"""Local, read-only CSV browser backed by the reproducible query functions."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit
import webbrowser

import duckdb

from src.common.config import ROOT, load_config
from src.reporting.query import (
    DATASETS, TABLE_DATASETS, _observation_query, _row_geography_filters, _table_query,
    export_catalog, export_observations, export_table,
    list_datasets, list_options, query_catalog,
)


DATASET_LABELS = {
    "main": "Source observations — General layer",
    "version-a": "Housing finance — Version A benchmark",
    "version-b": "Housing finance — Version B monthly history",
    "version-c": "Housing finance — Version C real-time vintages",
    "county-monthly": "LA County housing — Monthly",
    "comparable-county-monthly": "LA and comparable counties — Monthly",
    "zip-monthly": "Postal ZIP housing — Monthly",
    "county-annual": "County controls — Annual",
    "zcta-annual": "Census ZCTA controls — Annual",
    "county-quarterly": "County controls — Quarterly",
    "state-monthly": "State controls — Monthly",
    "state-quarterly": "State panel — Quarterly",
    "state-annual": "State controls — Annual",
    "metro-monthly": "Metro controls — Monthly",
    "metro-quarterly": "Metro panel — Quarterly",
    "metro-quarterly-controls": "Metro controls — Quarterly",
    "macro-monthly": "Macro and financial controls — Monthly",
    "coc-annual": "Continuum of Care controls — Annual",
    "zip-regimes-quarterly": "ZIP market classifications — Quarterly",
    "zip-regime-summary-quarterly": "ZIP market summary — Quarterly",
    "zip-regime-transitions-quarterly": "ZIP market transitions — Quarterly",
    "homebuying-scenarios": "Homebuying cost scenarios",
}
MULTI_FILTERS = {
    "domain": "domains", "layer": "layers", "geography": "geographies",
    "frequency": "frequencies", "native_frequency": "native_frequencies",
    "unit": "units", "temporal_type": "temporal_types", "geo_id": "geo_ids",
    "variable": "variable_ids",
}


def request_filters(params: dict[str, list[str]]) -> tuple[str, str, dict]:
    """Translate browser controls to the same typed filters used by the CLI."""
    kind = params.get("kind", ["table"])[0]
    dataset = params.get("dataset", ["county-monthly"])[0]
    allowed = TABLE_DATASETS if kind == "table" else DATASETS if kind == "observations" else {}
    if dataset not in allowed:
        raise ValueError("Choose a supported dataset.")
    filters = {
        argument: tuple(value for value in params.get(name, ()) if value)
        for name, argument in MULTI_FILTERS.items()
    }
    for name in ("proxy", "constructed"):
        value = params.get(name, [""])[0]
        if value not in ("", "true", "false"):
            raise ValueError(f"Choose Yes, No, or All for {name}.")
        filters[name] = None if not value else value == "true"
    for name in ("start_date", "end_date"):
        filters[name] = params.get(name, [""])[0] or None
    return kind, dataset, filters


def selected_query(connection, kind: str, dataset: str, filters: dict):
    if kind == "table":
        return _table_query(connection, dataset=dataset, **filters)
    sql, parameters = _observation_query(connection, dataset=dataset, **filters)
    ids = connection.execute(
        f"SELECT DISTINCT variable_id FROM ({sql}) selected", parameters
    ).fetchnumpy()["variable_id"].tolist()
    metadata = query_catalog(connection, variable_ids=ids) if ids else query_catalog(connection).iloc[:0]
    return sql, parameters, metadata


def variable_options(connection, kind: str, dataset: str, geographies=()) -> list[dict[str, str]]:
    """List only IDs represented by the selected dataset and actual geography."""
    if kind == "table":
        if dataset not in TABLE_DATASETS:
            raise ValueError("Choose a supported dataset.")
        sql, parameters, metadata = _table_query(connection, dataset=dataset, geographies=geographies)
        if "row_identifier_column" in metadata:
            # Scenario prices and assumptions share value columns, but their
            # variable IDs belong to particular rows and geographies.
            row_keys = sorted(set(metadata["row_identifier_column"]) - {""})
            quoted_keys = ['"' + key.replace('"', '""') + '"' for key in row_keys]
            projection = ', '.join(quoted_keys) if row_keys else "1"
            records = connection.execute(
                f"SELECT DISTINCT {projection} FROM ({sql}) selected", parameters
            ).fetchall()
            present = {key: {row[index] for row in records} for index, key in enumerate(row_keys)}
            metadata = metadata.loc[metadata.apply(
                lambda row: bool(records) and (
                    not row["row_identifier_column"]
                    or row["row_identifier_value"] in present[row["row_identifier_column"]]
                ), axis=1
            )]
        elif geographies and connection.execute(
            f"SELECT 1 FROM ({sql}) selected LIMIT 1", parameters
        ).fetchone() is None:
            metadata = metadata.iloc[:0]
    elif kind == "observations" and dataset in DATASETS:
        table = DATASETS[dataset].table
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
        clauses, parameters = _row_geography_filters(
            connection, columns, geographies=geographies, alias="q"
        )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        ids = {row[0] for row in connection.execute(
            f"SELECT DISTINCT variable_id FROM {table} q{where}", parameters
        ).fetchall()}
        metadata = query_catalog(connection)
        metadata = metadata.loc[metadata["variable_id"].isin(ids)]
    else:
        raise ValueError("Choose a supported dataset.")
    options = []
    for row in metadata.drop_duplicates("variable_id").fillna("").to_dict("records"):
        variable_id = str(row["variable_id"])
        display_name = str(row.get("display_name", "")).strip()
        label = f"{display_name} ({variable_id})" if display_name and display_name != variable_id else variable_id
        options.append({"value": variable_id, "label": label})
    return sorted(options, key=lambda option: (option["label"].casefold(), option["value"]))


def preview(connection, kind: str, dataset: str, filters: dict) -> dict:
    sql, parameters, metadata = selected_query(connection, kind, dataset, filters)
    frame = connection.execute(f"SELECT * FROM ({sql}) selected LIMIT 21", parameters).fetchdf()
    notes = []
    omitted = metadata.attrs.get("unclassified_columns", [])
    if omitted:
        notes.append("Unregistered measure columns omitted: " + ", ".join(omitted))
    if kind == "observations" and dataset == "version-c":
        notes.append("All information cutoffs are retained; check vintage fields before combining rows.")
    return {
        "columns": frame.columns.tolist(),
        "rows": json.loads(frame.head(20).to_json(orient="records", date_format="iso")),
        "metadata_count": len(metadata), "has_more": len(frame) > 20, "notes": notes,
    }


class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, database: Path, page: Path):
        self.database = database.resolve()
        self.page = page.resolve()
        super().__init__(address, BrowserHandler)


class BrowserHandler(BaseHTTPRequestHandler):
    server: BrowserServer

    def _headers(self, status: int, content_type: str, length: int, filename: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        port = self.server.server_port
        origin = self.headers.get("Origin")
        if host not in {f"localhost:{port}", f"127.0.0.1:{port}"} or (
            origin and origin not in {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
        ) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json({"error": "Open this browser from its local launcher."}, 403)
            return
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = self.server.page.read_bytes()
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            with duckdb.connect(str(self.server.database), read_only=True) as connection:
                connection.execute("SET threads=2")
                if parsed.path == "/api/datasets":
                    frame = list_datasets(connection)
                    rows = frame.to_dict("records")
                    for row in rows:
                        row["label"] = DATASET_LABELS.get(row["dataset"], row["dataset"])
                    self._json({"datasets": rows})
                    return
                if parsed.path == "/api/options":
                    kind, dataset, _ = request_filters(params)
                    field = params.get("field", [""])[0]
                    if field == "variable":
                        self._json({"options": variable_options(
                            connection, kind, dataset, geographies=params.get("geography", ())
                        )})
                        return
                    frame = list_options(
                        connection, field, kind=kind,
                        dataset=dataset, geographies=params.get("geography", ()),
                    )
                    self._json({"options": [
                        {"value": str(value), "label": str(value)} for value in frame["value"]
                    ]})
                    return
                if parsed.path not in {"/api/preview", "/api/download", "/api/definitions"}:
                    self._json({"error": "Page not found."}, 404)
                    return
                kind, dataset, filters = request_filters(params)
                if parsed.path == "/api/preview":
                    self._json(preview(connection, kind, dataset, filters))
                    return
                # Export and validate fully before sending HTTP success. This
                # uses the identical query/export functions as the command line.
                with tempfile.TemporaryDirectory(prefix="la-housing-csv-") as temporary:
                    output = Path(temporary) / f"{dataset}.csv"
                    if parsed.path == "/api/definitions":
                        _, _, metadata = selected_query(connection, kind, dataset, filters)
                        output = output.with_name(f"{dataset}.variables.csv")
                        export_catalog(metadata, output)
                    elif kind == "table":
                        export_table(connection, output, dataset=dataset, **filters)
                    else:
                        export_observations(connection, output, dataset=dataset, **filters)
                    self._headers(200, "text/csv; charset=utf-8", output.stat().st_size, output.name)
                    with output.open("rb") as handle:
                        shutil.copyfileobj(handle, self.wfile)
        except (ValueError, OSError, duckdb.Error) as exc:
            self._json({"error": str(exc)}, 400)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    config = load_config()
    database = args.database or config.root / config.pipeline["database_path"]
    if not database.is_file():
        parser.error(f"Database not found: {database}")
    page = ROOT / "scripts" / "data_browser.html"
    with BrowserServer(("127.0.0.1", args.port), database, page) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"LA Housing Data Browser: {url}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
        if args.open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
