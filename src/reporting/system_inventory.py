"""Generate outsider-facing inventories from live configuration and DuckDB state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from src.common.config import ProjectConfig
from src.common.variable_metadata import ENRICHMENT_COLUMNS


INTERNAL_PRODUCT_PREFIX = "housing_finance_version_"


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str) if path.is_file() else pd.DataFrame()


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)


def _atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _version_a_source_ids(root: Path) -> set[str]:
    path = root / "config" / "version_a_benchmark.yaml"
    if not path.is_file():
        return set()
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return set((document.get("sources") or {}).keys())


def _truthy(value: object) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def _source_layer(
    source_id: str, general_ids: set[str], version_a_ids: set[str]
) -> str:
    if source_id in general_ids and source_id in version_a_ids:
        return "General + Version A shared input"
    if source_id in general_ids:
        return "General pipeline"
    if source_id in version_a_ids:
        return "Version A input"
    return "Other configured source"


def _source_inventory(
    config: ProjectConfig, catalog: pd.DataFrame
) -> pd.DataFrame:
    general = {source.source_id: source for source in config.sources}
    version_a = _version_a_source_ids(config.root)
    if catalog.empty:
        return pd.DataFrame()
    provider_rows = catalog.loc[
        ~catalog["source_id"].astype(str).str.startswith(INTERNAL_PRODUCT_PREFIX)
    ].copy()
    provider_rows = provider_rows.drop_duplicates("source_id", keep="last")
    records: list[dict[str, Any]] = []
    for row in provider_rows.to_dict("records"):
        source_id = str(row.get("source_id", ""))
        configured = general.get(source_id)
        manual = _truthy(row.get("manual_download_required", False))
        if configured is not None:
            manual = not configured.auto_download
        layer = _source_layer(source_id, set(general), version_a)
        if source_id.startswith("census_acs_5year_"):
            credential = "CENSUS_API_KEY for future historical API refresh"
        elif source_id == "hud_usps_ncwm_quarterly":
            credential = "Authorized HUD User session/token for a future quarter"
        elif manual:
            credential = "Manual official provider export or approved restricted access"
        else:
            credential = "None for the configured public endpoint"
        if layer == "Version A input":
            refresh_behavior = (
                "Not refreshed by the system refresh: Version A is frozen. Use the "
                "separate approved Version A acquisition/build workflow."
            )
        elif manual:
            refresh_behavior = (
                "Existing archive may be reused, but every system refresh reports a "
                "manual provider-release check."
            )
        else:
            refresh_behavior = (
                "Attempted automatically by run_system_refresh.py; an acquisition "
                "failure blocks downstream publication."
            )
        records.append(
            {
                "source_id": source_id,
                "system_layer": layer,
                "provider": row.get("provider", ""),
                "dataset_name": row.get("product", row.get("Source", "")),
                "native_frequency": row.get("native_frequency", row.get("Frequency", "")),
                "analytical_frequency": row.get("analytical_frequency", ""),
                "geography": row.get("geography_level", row.get("Geography", "")),
                "geography_name": row.get("geography_name", ""),
                "key_variable_ids": row.get("variable_ids", row.get("Key variables", "")),
                "active_status": row.get("active_status", ""),
                "auto_refreshable": not manual and layer != "Version A input",
                "manual_intervention": manual or layer == "Version A input",
                "access_method": row.get("access_method", ""),
                "credential_or_access_requirement": credential,
                "refresh_behavior": refresh_behavior,
                "current_start_date": row.get("start_date", ""),
                "current_end_date": row.get("end_date", ""),
                "current_row_count": row.get("row_count", ""),
                "latest_retrieval": row.get("retrieval_date", ""),
                "provider_vintage": row.get("provider_vintage", ""),
                "raw_file_path": row.get("raw_file_path", ""),
                "clean_table": row.get("destination_clean_table", ""),
                "analytical_destination": row.get(
                    "destination_analytical_table", ""
                ),
                "official_source_page": row.get("official_source_page", ""),
                "download_url_or_api": row.get("exact_download_url_or_api", ""),
                "update_command": row.get("update_command", ""),
                "revision_policy": row.get("revision_policy", ""),
                "missing_value_policy": row.get("missing_value_policy", ""),
                "known_limitations": row.get("known_limitations", ""),
                "license_or_use_notes": row.get("license_or_use_notes", ""),
            }
        )
    return pd.DataFrame(records).sort_values(
        ["system_layer", "provider", "source_id"]
    ).reset_index(drop=True)


def _relation_frequency(name: str) -> str:
    if name == "fact_housing_observation":
        return "mixed native/analytical frequencies"
    if "monthly" in name or "homebuying" in name:
        return "monthly"
    if "quarterly" in name:
        return "quarterly"
    if "annual" in name:
        return "annual"
    if "benchmark" in name:
        return "native benchmark (ACS 5-year + annual)"
    return "mixed or convenience view"


def _relation_geography(name: str) -> str:
    if name == "macro_financial_monthly":
        return "United States / California / LA metropolitan division (distinct rows)"
    if name == "comparable_county_monthly_analytics":
        return "Los Angeles County and seven fixed California peer counties"
    if name == "fact_housing_observation":
        return "mixed"
    if name.startswith("v_la_"):
        return "Los Angeles County"
    if name.startswith("zip_"):
        return "postal ZIP"
    if name.startswith("zcta_"):
        return "Census ZCTA"
    if name.startswith("coc_"):
        return "Continuum of Care"
    if name.startswith("state_"):
        return "state"
    if name.startswith("metro_") or "macro_controls" in name:
        return "metro / mixed controls"
    if "housing_finance" in name or "version_" in name:
        return "California county"
    if "county" in name or "market_monthly" in name:
        return "Los Angeles County"
    return "mixed"


def _relation_storage_path(root: Path, name: str) -> str:
    special = {
        "housing_finance_monthly_rt": (
            "data/final/housing_finance_monthly_rt/"
            "information_cutoff_date=*/part.parquet"
        ),
        "housing_finance_monthly_hist": (
            "data/final/housing_finance_monthly_hist.parquet"
        ),
        "housing_finance_benchmark": (
            "data/final/housing_finance_benchmark.parquet"
        ),
    }
    candidate = special.get(name, f"data/final/{name}.parquet")
    if "*" in candidate or (root / candidate).exists():
        return candidate
    return "DuckDB view or integrated-only relation"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _analytical_inventory(
    root: Path, connection: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    relations = connection.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'analytics'
        ORDER BY table_type, table_name
        """
    ).fetchdf()
    columns = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'analytics'
        ORDER BY table_name, ordinal_position
        """
    ).fetchdf()
    column_map = columns.groupby("table_name")["column_name"].apply(list).to_dict()
    rows: list[dict[str, Any]] = []
    for relation in relations.itertuples(index=False):
        name = str(relation.table_name)
        names = [str(value) for value in column_map.get(name, [])]
        qualified = f"analytics.{_quote(name)}"
        row_count = connection.execute(
            f"SELECT count(*) FROM {qualified}"
        ).fetchone()[0]
        variable_count = ""
        if "variable_id" in names:
            variable_count = connection.execute(
                f"SELECT count(DISTINCT variable_id) FROM {qualified}"
            ).fetchone()[0]
        date_column = ""
        first_date = ""
        latest_date = ""
        for candidate in (
            "observation_date",
            "date",
            "month",
            "quarter_end",
            "reference_date",
        ):
            if candidate not in names:
                continue
            minimum, maximum = connection.execute(
                f"SELECT min({_quote(candidate)}), max({_quote(candidate)}) "
                f"FROM {qualified}"
            ).fetchone()
            if minimum is not None or maximum is not None:
                date_column = candidate
                first_date = str(minimum or "")[:10]
                latest_date = str(maximum or "")[:10]
                break
        geography_column = ""
        geography_count: Any = ""
        for candidate in (
            "geo_id",
            "county_fips",
            "postal_zip",
            "zcta",
            "coc_id",
            "fhfa_geography_id",
            "hvs_geography_id",
        ):
            if candidate in names:
                geography_column = candidate
                geography_count = connection.execute(
                    f"SELECT count(DISTINCT {_quote(candidate)}) FROM {qualified}"
                ).fetchone()[0]
                break
        rows.append(
            {
                "duckdb_object": f"analytics.{name}",
                "object_type": "table" if relation.table_type == "BASE TABLE" else "view",
                "frequency": _relation_frequency(name),
                "geography": _relation_geography(name),
                "row_count": int(row_count),
                "column_count": len(names),
                "variable_id_count": variable_count,
                "geography_key": geography_column,
                "geography_count": geography_count,
                "date_key": date_column,
                "first_date": first_date,
                "latest_date": latest_date,
                "canonical_storage": _relation_storage_path(root, name),
                "database_path": "database/housing.duckdb",
                "example_query": f"SELECT * FROM analytics.{name} LIMIT 100;",
            }
        )
    return pd.DataFrame(rows)


def _version_layer(variable_id: str) -> str:
    if variable_id.endswith("_benchmark"):
        return "Version A"
    if variable_id.endswith("_monthly_hist"):
        return "Version B"
    if variable_id.endswith("_monthly_rt"):
        return "Version C"
    return "General"


def _long_variable_stats(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, list[dict[str, Any]]]:
    tables = (
        ("analytics.fact_housing_observation", "observation_date", "geo_id"),
        ("analytics.housing_finance_benchmark", "reference_date", "county_fips"),
        ("analytics.housing_finance_monthly_hist", "month", "county_fips"),
        ("analytics.housing_finance_monthly_rt", "month", "county_fips"),
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for table, date_column, geography_column in tables:
        frame = connection.execute(
            f"""
            SELECT variable_id,
                   count(*) AS row_count,
                   count(value) AS nonnull_count,
                   min({_quote(date_column)}) AS first_date,
                   max({_quote(date_column)}) AS latest_date,
                   count(DISTINCT {_quote(geography_column)}) AS geography_count
            FROM {table}
            GROUP BY variable_id
            """
        ).fetchdf()
        for row in frame.to_dict("records"):
            result.setdefault(str(row["variable_id"]), []).append(
                {"location": table, **row}
            )
    return result


def _wide_variable_locations(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, list[str]]:
    columns = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'analytics'
          AND table_name NOT IN (
            'fact_housing_observation',
            'housing_finance_benchmark',
            'housing_finance_monthly_hist',
            'housing_finance_monthly_rt'
          )
        """
    ).fetchdf()
    return (
        columns.groupby("column_name")["table_name"]
        .apply(lambda values: sorted(set(str(value) for value in values)))
        .to_dict()
    )


def _scenario_variable_stat(
    connection: duckdb.DuckDBPyConnection, variable_id: str
) -> dict[str, Any] | None:
    """Locate dictionary IDs stored as scenario-key values instead of columns."""

    exists = connection.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_schema = 'analytics'
          AND table_name = 'homebuying_cost_scenarios'
        """
    ).fetchone()[0]
    if not exists:
        return None
    for id_column, value_column in (
        ("closing_cost_variable_id", "closing_cost_amount"),
        ("down_payment_variable_id", "down_payment_amount"),
    ):
        present = connection.execute(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'analytics'
              AND table_name = 'homebuying_cost_scenarios'
              AND column_name IN (?, ?)
            """,
            [id_column, value_column],
        ).fetchone()[0]
        if present != 2:
            continue
        values = connection.execute(
            f"""
            SELECT count(*) AS row_count,
                   count({_quote(value_column)}) AS nonnull_count,
                   min(date) AS first_date,
                   max(date) AS latest_date,
                   count(DISTINCT geo_id) AS geography_count
            FROM analytics.homebuying_cost_scenarios
            WHERE {_quote(id_column)} = ?
            """,
            [variable_id],
        ).fetchone()
        if values[0]:
            return {
                "location": "analytics.homebuying_cost_scenarios",
                "variable_id": variable_id,
                "row_count": values[0],
                "nonnull_count": values[1],
                "first_date": values[2],
                "latest_date": values[3],
                "geography_count": values[4],
            }
    return None


def _variable_inventory(
    root: Path, connection: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    dictionary = _read_csv(root / "metadata" / "data_dictionary.csv")
    long_stats = _long_variable_stats(connection)
    wide_locations = _wide_variable_locations(connection)
    rows: list[dict[str, Any]] = []
    for item in dictionary.to_dict("records"):
        variable_id = str(item["variable_id"])
        stats = long_stats.get(variable_id, [])
        locations = [str(row["location"]) for row in stats]
        locations.extend(
            f"analytics.{name}" for name in wide_locations.get(variable_id, [])
        )
        locations = sorted(set(locations))
        primary = max(
            stats,
            key=lambda row: (int(row.get("nonnull_count", 0)), int(row.get("row_count", 0))),
            default=None,
        )
        if primary is None and not wide_locations.get(variable_id):
            primary = _scenario_variable_stat(connection, variable_id)
            if primary is not None:
                locations.append(str(primary["location"]))
                locations = sorted(set(locations))
        if primary is None and wide_locations.get(variable_id):
            table_name = wide_locations[variable_id][0]
            relation = f"analytics.{_quote(table_name)}"
            date_column = next(
                (
                    name
                    for name in ("date", "observation_date", "month", "reference_date")
                    if connection.execute(
                        """
                        SELECT count(*) FROM information_schema.columns
                        WHERE table_schema='analytics' AND table_name=? AND column_name=?
                        """,
                        [table_name, name],
                    ).fetchone()[0]
                ),
                None,
            )
            date_sql = (
                f", min({_quote(date_column)}), max({_quote(date_column)})"
                if date_column
                else ", NULL, NULL"
            )
            values = connection.execute(
                f"SELECT count(*), count({_quote(variable_id)}){date_sql} FROM {relation}"
            ).fetchone()
            primary = {
                "row_count": values[0],
                "nonnull_count": values[1],
                "first_date": values[2],
                "latest_date": values[3],
                "geography_count": "",
            }
        row_count = int(primary["row_count"]) if primary is not None else 0
        nonnull_count = int(primary["nonnull_count"]) if primary is not None else 0
        if nonnull_count:
            materialization_status = "Available with non-null data"
        elif row_count:
            materialization_status = "Explicit rows present but currently all null"
        else:
            materialization_status = "Configured but not materialized as a current column/row"
        rows.append(
            {
                **item,
                "system_layer": _version_layer(variable_id),
                "materialization_status": materialization_status,
                "primary_row_count": row_count,
                "primary_nonnull_count": nonnull_count,
                "first_materialized_date": (
                    str(primary.get("first_date", ""))[:10] if primary else ""
                ),
                "latest_materialized_date": (
                    str(primary.get("latest_date", ""))[:10] if primary else ""
                ),
                "materialized_locations": "|".join(locations),
            }
        )
    return pd.DataFrame(rows)


def _estimate_family(variable_id: str) -> str:
    patterns = (
        ("market_regime", "ZIP market regimes"),
        ("market_heat", "Market heat and ranking"),
        ("market_tightness", "Market tightness"),
        ("momentum", "Growth and change estimates"),
        ("relative_valuation", "Valuation and rent yield"),
        ("la_relative_", "Peer-relative performance"),
        ("new_apartments_completed", "Multifamily construction scenarios"),
        ("under_construction", "Multifamily construction scenarios"),
        ("mortgage_payment", "Homebuying affordability scenarios"),
        ("down_payment", "Homebuying affordability scenarios"),
        ("closing_cost", "Homebuying affordability scenarios"),
        ("price_to_rent", "Valuation and rent yield"),
        ("rent_yield", "Valuation and rent yield"),
        ("growth_", "Growth and change estimates"),
        ("_growth", "Growth and change estimates"),
        ("noncontrolled", "Rent-regulation structural sensitivities"),
        ("rental_availability", "Rental availability"),
        ("price_to_income", "Income affordability"),
        ("net_exemption_rate", "Population migration normalization"),
        ("per_1000", "Population/housing normalization"),
        ("mortgage", "Housing finance"),
        ("equity", "Housing finance"),
        ("downpayment", "Housing finance"),
        ("ltv", "Housing finance"),
    )
    for token, family in patterns:
        if token in variable_id:
            return family
    return "Other constructed indicator or diagnostic"


def _estimate_inventory(variables: pd.DataFrame) -> pd.DataFrame:
    if variables.empty:
        return variables
    constructed = variables.loc[
        variables.get("is_constructed", "False").map(_truthy)
        | variables.get("is_proxy", "False").map(_truthy)
        | variables["system_layer"].isin(["Version B", "Version C"])
        | variables.get("provider", "").astype(str).eq("Constructed")
    ].copy()
    records: list[dict[str, Any]] = []
    for row in constructed.to_dict("records"):
        variable_id = str(row["variable_id"])
        layer = str(row["system_layer"])
        if layer == "Version A":
            update_behavior = "Frozen benchmark; separate approved Version A workflow only"
        elif layer == "Version B":
            update_behavior = (
                "Rebuilt retrospectively from frozen A and newest eligible local "
                "indicator archives by run_system_refresh.py"
            )
        elif layer == "Version C":
            update_behavior = (
                "New append-only cutoff vintage from inputs available by the cutoff; "
                "never future-interpolated"
            )
        else:
            update_behavior = (
                "Recomputed during harmonize after strict newest-source refresh"
            )
        special_limit = ""
        if variable_id == "market_tightness_index":
            special_limit = (
                "Full-history z-score: adding or revising months can revise the entire "
                "historical index; not a real-time vintage measure."
            )
        elif variable_id == "market_tightness_index_3c_fixed":
            special_limit = (
                "Uses frozen pooled reference parameters and exactly months of supply, "
                "days on market, and price-drop share; the initial reference-window "
                "choice remains pending mentor confirmation."
            )
        elif variable_id.startswith("zip_market_regime_"):
            special_limit = (
                "Uses a fingerprinted frozen 2015Q2-2019Q4 model; classification and "
                "transition denominators change with complete Zillow ZIP coverage."
            )
        records.append(
            {
                "variable_id": variable_id,
                "estimate_family": _estimate_family(variable_id),
                "system_layer": layer,
                "display_name": row.get("display_name", ""),
                "definition": row.get("definition", ""),
                "final_frequency": row.get("final_frequency", ""),
                "geography": row.get("geography", ""),
                "unit": row.get("unit", ""),
                "transformation": row.get("transformation", ""),
                "is_proxy": row.get("is_proxy", ""),
                "materialization_status": row.get("materialization_status", ""),
                "primary_nonnull_count": row.get("primary_nonnull_count", 0),
                "latest_materialized_date": row.get("latest_materialized_date", ""),
                "automatic_update_behavior": update_behavior,
                "important_limitation": (
                    special_limit or row.get("methodological_notes", "")
                ),
                "materialized_locations": row.get("materialized_locations", ""),
                **{
                    column: row[column]
                    for column in ("unit_original", *ENRICHMENT_COLUMNS)
                    if column in row
                },
            }
        )
    return pd.DataFrame(records).sort_values(
        ["estimate_family", "system_layer", "variable_id"]
    ).reset_index(drop=True)


def _storage_inventory(root: Path) -> pd.DataFrame:
    def files_under(relative: str, suffix: str | None = None) -> list[Path]:
        base = root / relative
        if not base.exists():
            return []
        return [
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.name.casefold() not in {"readme.md", ".gitkeep"}
            and (suffix is None or path.suffix.lower() == suffix)
        ]

    raw = files_under("data/raw")
    clean = files_under("data/clean", ".parquet")
    final_parquet = files_under("data/final", ".parquet")
    final_markers = files_under("data/final", ".json")
    exports = files_under("output/exports", ".csv")
    restricted = files_under("data/restricted")
    active_db = root / "database" / "housing.duckdb"
    legacy_db = root / "database" / "housing_before_redfin_zip.duckdb"
    rows = [
        {
            "storage_layer": "Active analytical database",
            "path": "database/housing.duckdb",
            "active": True,
            "file_count": 1 if active_db.is_file() else 0,
            "bytes": active_db.stat().st_size if active_db.is_file() else 0,
            "contents": "Five DuckDB schemas: metadata, raw, clean, analytics, quality",
            "outsider_use": "Primary SQL query surface",
        },
        {
            "storage_layer": "Legacy database snapshot",
            "path": "database/housing_before_redfin_zip.duckdb",
            "active": False,
            "file_count": 1 if legacy_db.is_file() else 0,
            "bytes": legacy_db.stat().st_size if legacy_db.is_file() else 0,
            "contents": "Historical pre-Redfin-ZIP snapshot; not configured or maintained",
            "outsider_use": "Do not use for current analysis",
        },
        {
            "storage_layer": "Immutable raw archives",
            "path": "data/raw/",
            "active": True,
            "file_count": len(raw),
            "bytes": sum(path.stat().st_size for path in raw),
            "contents": "Original official provider/API payloads with checksums and retrieval vintages",
            "outsider_use": "Audit/reproducibility; normally query clean/final instead",
        },
        {
            "storage_layer": "Normalized clean Parquet",
            "path": "data/clean/",
            "active": True,
            "file_count": len(clean),
            "bytes": sum(path.stat().st_size for path in clean),
            "contents": "Source-specific normalized tables; filesystem includes superseded files",
            "outsider_use": "Source-level analysis; define active tables from DuckDB/config",
        },
        {
            "storage_layer": "Final analytical Parquet",
            "path": "data/final/",
            "active": True,
            "file_count": len(final_parquet),
            "bytes": sum(path.stat().st_size for path in final_parquet),
            "contents": "Canonical analytical tables, finance outputs, and lineage",
            "outsider_use": "Direct columnar access",
        },
        {
            "storage_layer": "Completion markers",
            "path": "data/final/",
            "active": True,
            "file_count": len(final_markers),
            "bytes": sum(path.stat().st_size for path in final_markers),
            "contents": "Checksum gates for paired Version B/C observations and lineage",
            "outsider_use": "Verify completeness before consuming finance files",
        },
        {
            "storage_layer": "Restricted licensed layer",
            "path": "data/restricted/",
            "active": True,
            "file_count": len(restricted),
            "bytes": sum(path.stat().st_size for path in restricted),
            "contents": "Licensed HUD/USPS detailed raw and rebuilt data",
            "outsider_use": "Access-controlled; not included in public CSV exports",
        },
        {
            "storage_layer": "CSV exports",
            "path": "output/exports/",
            "active": True,
            "file_count": len(exports),
            "bytes": sum(path.stat().st_size for path in exports),
            "contents": "Modeling-friendly public exports",
            "outsider_use": "Simplest non-SQL entry point",
        },
    ]
    return pd.DataFrame(rows)


def build_system_inventory(config: ProjectConfig) -> dict[str, int]:
    """Write current source, dataset, variable, estimate, and storage inventories."""

    metadata = config.root / "metadata"
    catalog = _read_csv(metadata / "data_catalog.csv")
    database_path = config.root / config.pipeline["database_path"]
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        sources = _source_inventory(config, catalog)
        analytical = _analytical_inventory(config.root, connection)
        variables = _variable_inventory(config.root, connection)
    finally:
        connection.close()
    estimates = _estimate_inventory(variables)
    storage = _storage_inventory(config.root)
    gaps = _read_csv(metadata / "data_gap_analysis.csv")
    refresh = _read_csv(metadata / "source_refresh_status.csv")

    outputs = {
        "system_source_inventory.csv": sources,
        "analytical_dataset_inventory.csv": analytical,
        "system_variable_inventory.csv": variables,
        "estimate_inventory.csv": estimates,
        "system_storage_inventory.csv": storage,
    }
    for name, frame in outputs.items():
        _atomic_csv(frame, metadata / name)

    summary = {
        "active_physical_analytical_databases": int(
            storage.loc[
                storage["storage_layer"].eq("Active analytical database"),
                "file_count",
            ].sum()
        ),
        "provider_source_datasets": int(len(sources)),
        "configured_versioned_variables": int(len(variables)),
        "general_variables": int(variables["system_layer"].eq("General").sum()),
        "version_a_variables": int(variables["system_layer"].eq("Version A").sum()),
        "version_b_variables": int(variables["system_layer"].eq("Version B").sum()),
        "version_c_variables": int(variables["system_layer"].eq("Version C").sum()),
        "analytics_base_tables": int(analytical["object_type"].eq("table").sum()),
        "analytics_views": int(analytical["object_type"].eq("view").sum()),
        "final_parquet_files": int(
            storage.loc[
                storage["storage_layer"].eq("Final analytical Parquet"),
                "file_count",
            ].sum()
        ),
        "estimate_or_constructed_variable_rows": int(len(estimates)),
        "gap_inventory_rows": int(len(gaps)),
        "latest_refresh_report_rows": int(len(refresh)),
        "generated_from": {
            "database": "database/housing.duckdb",
            "catalog": "metadata/data_catalog.csv",
            "dictionary": "metadata/data_dictionary.csv",
        },
    }
    _atomic_json(summary, metadata / "system_inventory_summary.json")
    return {key: int(value) for key, value in summary.items() if isinstance(value, int)}


__all__ = ["build_system_inventory"]
