"""Source-specific acquisition with immutable, checksummed raw archives."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file
from src.cleaning.acs import ACS_TABLE_MAP
from src.ingestion.bps_archive import download_bps_archive
from src.ingestion.irs_migration import download_irs_soi_county_migration

RAW_SUBDIR = {
    "Zillow Research": "zillow",
    "Redfin Data Center": "redfin",
    "U.S. Bureau of Labor Statistics": "bls",
    "Freddie Mac": "freddie_mac",
    "U.S. Census Bureau": "census_bps",
    "Federal Housing Finance Agency": "fhfa",
    "U.S. Bureau of Economic Analysis": "bea",
    "Internal Revenue Service": "irs_soi",
}

BLS_SERIES = (
    "LAUCN060370000000003",
    "LAUCN060370000000004",
    "LAUCN060370000000005",
    "LAUCN060370000000006",
)
USER_AGENT = "UCLA housing research data pipeline yifan@g.ucla.edu"

ACS_HISTORICAL_TABLES = {
    "census_acs_5year_b25003",
    "census_acs_5year_b25077",
    "census_acs_5year_b25081",
    "census_acs_5year_b25106",
}

ACS_API_LATEST_5YEAR = 2024
ACS_API_MIN_5YEAR = 2010
ACS_UCGID_ZCTA_CHUNK_SIZE = 40


def _raw_subdir(source: SourceConfig) -> str:
    return source.raw_subdir or RAW_SUBDIR[source.provider]


def _today_manifest_record(
    config: ProjectConfig, source_id: str
) -> tuple[Path, dict] | None:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return None
    frame = pd.read_csv(manifest_path, dtype=str)
    rows = frame.loc[frame["source_id"] == source_id].copy()
    if rows.empty:
        return None
    retrieved = pd.to_datetime(
        rows["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    rows = rows.loc[retrieved.dt.date == datetime.now(timezone.utc).date()]
    if rows.empty:
        return None
    record = rows.iloc[-1].to_dict()
    path = config.root / str(record["relative_path"])
    return (path, record) if path.exists() else None


def _checksum_manifest_record(
    config: ProjectConfig, source_id: str, checksum: str
) -> tuple[Path, dict] | None:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return None
    frame = pd.read_csv(manifest_path, dtype=str)
    rows = frame.loc[
        frame["source_id"].eq(source_id)
        & frame["checksum_sha256"].eq(checksum)
    ]
    if rows.empty:
        return None
    record = rows.iloc[-1].to_dict()
    path = config.root / str(record["relative_path"])
    return (path, record) if path.exists() else None


def _mark_manual_resolved(
    config: ProjectConfig, source_id: str, resolved_at: str
) -> None:
    path = config.root / "metadata" / "manual_downloads.csv"
    if not path.exists():
        return
    frame = pd.read_csv(path, dtype=str)
    if "status" not in frame:
        frame["status"] = "open"
    if "resolved_at" not in frame:
        frame["resolved_at"] = None
    matches = frame["source_id"].eq(source_id)
    frame.loc[matches, "status"] = "resolved"
    frame.loc[matches, "resolved_at"] = resolved_at
    frame.to_csv(path, index=False)


def _write_response(response: requests.Response, temp_path: Path, chunk_bytes: int) -> None:
    response.raise_for_status()
    with temp_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=chunk_bytes):
            if chunk:
                handle.write(chunk)


def _latest_bps_url(source: SourceConfig) -> str:
    """Resolve the newest published current-month ASCII file, checking four lags."""
    match = re.match(
        r"^(https://www2\.census\.gov/econ/bps/(?:County|State)/)(?:co|st)\d{4}c\.txt$",
        source.download_url,
    )
    if not match:
        return source.download_url
    prefix = "co" if source.source_id.endswith("county") else "st"
    anchor = pd.Timestamp.now().to_period("M")
    for lag in range(4):
        period = anchor - lag
        candidate = f"{match.group(1)}{prefix}{period.strftime('%y%m')}c.txt"
        try:
            response = requests.head(
                candidate,
                timeout=30,
                allow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            if response.ok:
                return candidate
        except requests.RequestException:
            continue
    return source.download_url


def _download_bls(
    source: SourceConfig,
    temp_path: Path,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, str | int]:
    start_year = pd.Timestamp(start_date or "1990-01-01").year
    end_year = pd.Timestamp(end_date or date.today()).year
    series_ids = source.series_ids or BLS_SERIES
    if not series_ids:
        raise ValueError("No BLS series IDs are configured")
    payloads: list[dict] = []
    requests_made: list[dict] = []
    observation_dates: list[pd.Timestamp] = []
    observation_count = 0
    session = requests.Session()
    for chunk_start in range(start_year, end_year + 1, 10):
        chunk_end = min(chunk_start + 9, end_year)
        request_payload = {
            "seriesid": list(series_ids),
            "startyear": str(chunk_start),
            "endyear": str(chunk_end),
        }
        response = session.post(
            source.download_url,
            json=request_payload,
            timeout=120,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"BLS returned invalid JSON for {chunk_start}-{chunk_end}"
            ) from exc
        if not isinstance(body, dict):
            raise RuntimeError(
                f"BLS returned a non-object JSON payload for "
                f"{chunk_start}-{chunk_end}"
            )
        if body.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS API error for {chunk_start}-{chunk_end}: {body}")
        results = body.get("Results")
        response_series = (
            results.get("series")
            if isinstance(results, dict)
            else None
        )
        if not isinstance(response_series, list):
            raise RuntimeError(
                f"BLS response has no Results.series list for "
                f"{chunk_start}-{chunk_end}"
            )
        by_id = {
            item.get("seriesID"): item
            for item in response_series
            if isinstance(item, dict)
        }
        missing_series = sorted(set(series_ids) - set(by_id))
        if missing_series:
            raise RuntimeError(
                f"BLS response missing configured series for "
                f"{chunk_start}-{chunk_end}: {missing_series}"
            )
        for series_id in series_ids:
            data = by_id[series_id].get("data")
            if not isinstance(data, list):
                raise RuntimeError(
                    f"BLS series {series_id} has no data list for "
                    f"{chunk_start}-{chunk_end}"
                )
            monthly_items = [
                item
                for item in data
                if isinstance(item, dict)
                and str(item.get("period", "")).startswith("M")
                and str(item.get("period")) != "M13"
            ]
            if not monthly_items:
                raise RuntimeError(
                    f"BLS series {series_id} returned no monthly "
                    f"observations for {chunk_start}-{chunk_end}"
                )
            numeric_observations = 0
            for item in monthly_items:
                try:
                    year = int(item["year"])
                    month = int(str(item["period"])[1:])
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"BLS series {series_id} contains an invalid "
                        f"monthly observation: {item}"
                    ) from exc
                try:
                    float(item["value"])
                except (KeyError, TypeError, ValueError):
                    # BLS publishes unavailable months as nonnumeric values
                    # (for example "-" with explanatory footnote X).
                    continue
                observation_dates.append(
                    pd.Timestamp(year=year, month=month, day=1)
                    + pd.offsets.MonthEnd(0)
                )
                numeric_observations += 1
            if numeric_observations == 0:
                raise RuntimeError(
                    f"BLS series {series_id} returned no numeric monthly "
                    f"observations for {chunk_start}-{chunk_end}"
                )
            observation_count += numeric_observations
        requests_made.append(request_payload)
        payloads.append(body)
    if not observation_dates:
        raise RuntimeError("BLS returned no monthly observations")
    temp_path.write_text(
        json.dumps(
            {
                "source_url": source.download_url,
                "series_ids": series_ids,
                "requests": requests_made,
                "responses": payloads,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "request_series_ids": "|".join(series_ids),
        "request_start_year": str(start_year),
        "request_end_year": str(end_year),
        "response_latest_observation": max(observation_dates)
        .date()
        .isoformat(),
        "response_observation_count": str(observation_count),
    }


def _acs_table_id(source_id: str) -> str:
    return source_id.rsplit("_", 1)[-1].lower()


def _acs_api_variable(summary_file_variable: str) -> str:
    """Convert table-based ACS names like B25106_E004 to API B25106_004E."""
    match = re.fullmatch(r"([A-Z0-9]+)_([EM])(\d{3})", summary_file_variable)
    if not match:
        raise ValueError(f"Unsupported ACS variable name: {summary_file_variable}")
    table, estimate_or_moe, sequence = match.groups()
    return f"{table}_{sequence}{estimate_or_moe}"


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _la_zcta_ucgids(config: ProjectConfig) -> list[str]:
    geography_path = config.root / "data" / "clean" / "geography.parquet"
    if not geography_path.exists():
        raise RuntimeError(
            "Historical ACS ZCTA API downloads require the local LA ZIP/ZCTA "
            "reference at data/clean/geography.parquet. Build geography once "
            "or provide that versioned file before downloading ACS ZCTA data."
        )
    geography = pd.read_parquet(geography_path)
    if "postal_zip" not in geography:
        raise RuntimeError(
            "data/clean/geography.parquet does not contain a postal_zip column"
        )
    zips = sorted(
        {
            str(value).zfill(5)
            for value in geography["postal_zip"].dropna()
            if re.fullmatch(r"\d{5}", str(value).zfill(5))
        }
    )
    if not zips:
        raise RuntimeError(
            "No LA ZIP/ZCTA identifiers were found in data/clean/geography.parquet"
        )
    return [f"860Z200US{postal_zip}" for postal_zip in zips]


def _download_census_acs_5year_api(
    config: ProjectConfig,
    source: SourceConfig,
    temp_path: Path,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, str]:
    """Download selected ACS 5-year detailed table cells through the Census API."""
    table_id = _acs_table_id(source.source_id)
    variable_map = ACS_TABLE_MAP.get(table_id)
    if variable_map is None:
        raise ValueError(f"No ACS variable map is configured for {source.source_id}")
    api_key = os.environ.get("CENSUS_API_KEY")
    start_year = max(
        ACS_API_MIN_5YEAR,
        pd.Timestamp(start_date or source.selected_start_date or "2010-01-01").year,
    )
    end_year = min(
        ACS_API_LATEST_5YEAR,
        pd.Timestamp(end_date or source.selected_end_date or "2024-12-31").year,
    )
    if start_year > end_year:
        raise ValueError(
            f"Invalid ACS 5-year request range for {source.source_id}: "
            f"{start_year}>{end_year}"
        )
    variables = [_acs_api_variable(column) for column in variable_map]
    group_clause = f"group({table_id.upper()})"
    zcta_ucgids = _la_zcta_ucgids(config)
    zcta_ucgid_chunks = _chunks(zcta_ucgids, ACS_UCGID_ZCTA_CHUNK_SIZE)
    requests_made: list[dict] = []
    observation_count = 0
    session = requests.Session()
    for year in range(start_year, end_year + 1):
        base_url = f"https://api.census.gov/data/{year}/acs/acs5"
        geography_requests = [
            (
                "county",
                {
                    "get": group_clause,
                    "ucgid": "050C0US06037",
                },
            )
        ]
        geography_requests.extend(
            (
                f"zcta_chunk_{chunk_number:02d}",
                {
                    "get": group_clause,
                    "ucgid": ",".join(chunk),
                },
            )
            for chunk_number, chunk in enumerate(zcta_ucgid_chunks, start=1)
        )
        for geography, params in geography_requests:
            request_params = dict(params)
            if api_key:
                request_params["key"] = api_key
            response = session.get(
                base_url,
                params=request_params,
                timeout=120,
                headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
            )
            attempted_url = (
                response.history[0].request.url
                if getattr(response, "history", None)
                else getattr(getattr(response, "request", None), "url", response.url)
            )
            request_url = (
                attempted_url.replace(api_key, "CENSUS_API_KEY")
                if api_key
                else attempted_url
            )
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                text = response.text[:500].replace("\n", " ").strip()
                if "Missing Key" in text or "valid" in text and "key" in text:
                    raise RuntimeError(
                        "The Census Data API link pattern is available for this "
                        "historical ACS table, but the API rejected this request "
                        "without a valid key. Set CENSUS_API_KEY and retry. No "
                        f"partial raw file was archived. Request URL: {request_url}"
                    ) from exc
                raise RuntimeError(
                    f"Census ACS API returned invalid JSON for "
                    f"{source.source_id} {year} {geography}: {text}"
                ) from exc
            if not isinstance(body, list) or len(body) < 2:
                raise RuntimeError(
                    f"Census ACS API returned no observations for "
                    f"{source.source_id} {year} {geography}: {body}"
                )
            header = [str(item) for item in body[0]]
            missing = sorted(set(["NAME", *variables]) - set(header))
            if missing:
                raise RuntimeError(
                    f"Census ACS API response missing columns for "
                    f"{source.source_id} {year} {geography}: {missing}"
                )
            requests_made.append(
                {
                    "year": year,
                    "geography": geography,
                    "url": request_url,
                    "params_without_key": {
                        key: value
                        for key, value in request_params.items()
                        if key != "key"
                    },
                    "variables": variables,
                    "api_get": group_clause,
                    "response": body,
                }
            )
            observation_count += len(body) - 1
    temp_path.write_text(
        json.dumps(
            {
                "source_url": source.download_url,
                "source_page_url": source.source_page_url,
                "api": "Census ACS 5-year API",
                "dataset": "acs/acs5",
                "table_id": table_id,
                "start_year": start_year,
                "end_year": end_year,
                "requests": requests_made,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "request_api_base": "https://api.census.gov/data/{year}/acs/acs5",
        "request_table_id": table_id.upper(),
        "request_variables": group_clause,
        "request_selected_variables": "|".join(variables),
        "request_geographies": "ucgid:050C0US06037|ucgid:860Z200US<LA ZIP chunks>",
        "request_start_year": str(start_year),
        "request_end_year": str(end_year),
        "request_zcta_count": str(len(zcta_ucgids)),
        "response_latest_observation": f"{end_year}-12-31",
        "response_observation_count": str(observation_count),
    }


def _normalize_metadata_column(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _provider_last_updated(path: Path, source: SourceConfig) -> str | None:
    if source.provider != "Redfin Data Center":
        return None
    read_options: dict[str, object] = {"nrows": 1, "dtype": str}
    if source.file_format == "tsv.gz":
        read_options.update({"sep": "\t", "compression": "gzip"})
    try:
        header = pd.read_csv(path, **read_options)
    except Exception:
        return None
    if header.empty:
        return None
    by_normalized = {
        _normalize_metadata_column(column): column for column in header.columns
    }
    column = by_normalized.get("last_updated")
    if column is None:
        return None
    value = str(header.iloc[0].get(column, "")).strip()
    return value or None


def _source_filename_from_url(download_url: str) -> str | None:
    filename = Path(urlparse(download_url).path).name
    return filename or None


def _extension(source: SourceConfig) -> str:
    return {
        "csv": ".csv",
        "dat": ".dat",
        "json": ".json",
        "txt": ".txt",
        "tsv.gz": ".tsv.gz",
        "xlsx": ".xlsx",
        "zip": ".zip",
    }[source.file_format]


def _import_local_file(
    config: ProjectConfig,
    source: SourceConfig,
    local_file: str,
    force: bool,
) -> dict:
    logger = logging.getLogger("la_housing.download")
    path = Path(local_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Local source file does not exist: {path}")
    header = pd.read_csv(path, nrows=1, dtype=str)
    actual_columns = tuple(header.columns)
    if source.expected_columns and actual_columns != source.expected_columns:
        missing = sorted(set(source.expected_columns) - set(actual_columns))
        extra = sorted(set(actual_columns) - set(source.expected_columns))
        raise ValueError(
            "Redfin ZIP Key Metrics schema changed: "
            f"missing={missing}, extra={extra}, "
            f"expected_order={list(source.expected_columns)}, "
            f"actual_order={list(actual_columns)}"
        )
    checksum = sha256_file(path)
    if not force:
        existing = _checksum_manifest_record(
            config, source.source_id, checksum
        )
        if existing:
            _mark_manual_resolved(
                config,
                source.source_id,
                datetime.now(timezone.utc).isoformat(),
            )
            logger.info(
                "Reusing identical archived local file for %s",
                source.source_id,
            )
            return existing[1]
    retrieved_at = datetime.now(timezone.utc)
    range_label = (
        f"{source.selected_start_date[:7]}_to_{source.selected_end_date[:7]}"
        if source.selected_start_date and source.selected_end_date
        else "selected_range"
    )
    filename = (
        f"{source.source_id}_{range_label}_"
        f"{retrieved_at:%Y%m%dT%H%M%SZ}_{checksum[:12]}"
        f"{_extension(source)}"
    )
    raw_dir = config.root / "data" / "raw" / _raw_subdir(source)
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / filename
    temp_path = raw_dir / f".{filename}.part"
    try:
        shutil.copyfile(path, temp_path)
        copied_checksum = sha256_file(temp_path)
        if copied_checksum != checksum:
            raise IOError(
                f"Checksum changed while importing {path.name}: "
                f"{checksum} != {copied_checksum}"
            )
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    first = header.iloc[0]
    source_last_updated = str(first.get("LAST UPDATED", "")).strip()
    source_frequency = str(first.get("FREQUENCY", "")).strip()
    record = {
        "source_id": source.source_id,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": source.source_page_url,
        "download_url": source.download_url,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": retrieved_at.isoformat(),
        "data_vintage": retrieved_at.date().isoformat(),
        "source_filename": destination.name,
        "original_filename": path.name,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": "manual_official_download_import",
        "source_last_updated": source_last_updated,
        "provider_last_updated": source_last_updated,
        "revision_policy": source.revision_notes,
        "selected_start_date": source.selected_start_date,
        "selected_end_date": source.selected_end_date,
        "source_frequency": source_frequency,
        "rolling_window_months": source.rolling_window_months,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "ingestion_status": "manually_imported",
    }
    append_csv(config.root / "metadata" / "raw_file_manifest.csv", [record])
    _mark_manual_resolved(
        config, source.source_id, retrieved_at.isoformat()
    )
    logger.info(
        "Imported %s unchanged (%s bytes, sha256=%s)",
        source.source_id,
        record["byte_size"],
        checksum[:12],
    )
    return record


def download_source(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool,
    explicitly_selected: bool,
    start_date: str | None,
    end_date: str | None,
    local_file: str | None = None,
) -> dict:
    logger = logging.getLogger("la_housing.download")
    if local_file:
        if source.source_id != "redfin_zip_monthly":
            raise ValueError(
                "--local-file is currently supported only for "
                "redfin_zip_monthly"
            )
        return _import_local_file(config, source, local_file, force)
    if not source.auto_download:
        raise PermissionError(source.manual_reason or "Explicit source selection required")
    if source.source_id.startswith("census_bps_"):
        return download_bps_archive(
            config,
            source,
            force=force,
            start_date=start_date,
            end_date=end_date,
        )
    if source.source_id == "irs_soi_county_migration":
        return download_irs_soi_county_migration(
            config,
            source,
            force=force,
            start_date=start_date,
            end_date=end_date,
        )
    if not force:
        existing = _today_manifest_record(config, source.source_id)
        if existing:
            logger.info("Reusing today's raw archive for %s", source.source_id)
            return existing[1]

    raw_dir = config.root / "data" / "raw" / _raw_subdir(source)
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc)
    temp_path = raw_dir / f".{source.source_id}.{retrieved_at:%Y%m%dT%H%M%SZ}.part"
    resolved_download_url = source.download_url
    request_metadata: dict[str, str | int] = {}
    original_filename = (
        "api_response.json"
        if source.file_format == "json"
        and source.provider == "U.S. Bureau of Labor Statistics"
        else unquote(Path(urlparse(resolved_download_url).path).name)
    )
    try:
        if source.file_format == "json" and source.source_id.startswith("bls_"):
            request_metadata = _download_bls(
                source, temp_path, start_date, end_date
            )
        elif source.source_id in ACS_HISTORICAL_TABLES:
            request_metadata = _download_census_acs_5year_api(
                config, source, temp_path, start_date, end_date
            )
        else:
            with requests.get(
                resolved_download_url,
                stream=True,
                timeout=int(config.pipeline["request_timeout_seconds"]),
                headers={"User-Agent": USER_AGENT},
            ) as response:
                _write_response(
                    response,
                    temp_path,
                    int(config.pipeline["download_chunk_bytes"]),
                )
                disposition = response.headers.get("Content-Disposition", "")
                match = re.search(r'filename="?([^";]+)', disposition)
                if match:
                    original_filename = match.group(1).strip()
        if source.file_format in {"zip", "xlsx"}:
            if temp_path.read_bytes()[:2] != b"PK":
                raise ValueError(
                    f"{source.source_id} did not return a valid ZIP/XLSX payload"
                )
        elif source.file_format in {"dat", "txt"}:
            prefix = temp_path.read_bytes()[:200].lstrip().lower()
            if prefix.startswith((b"<!doctype html", b"<html")):
                raise ValueError(
                    f"{source.source_id} returned HTML instead of source data"
                )
        checksum = sha256_file(temp_path)
        filename = (
            f"{source.source_id}_{retrieved_at:%Y%m%dT%H%M%SZ}_"
            f"{checksum[:12]}{_extension(source)}"
        )
        destination = raw_dir / filename
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    provider_last_updated = _provider_last_updated(destination, source)

    record = {
        "source_id": source.source_id,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": source.source_page_url,
        "download_url": resolved_download_url,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": retrieved_at.isoformat(),
        "data_vintage": retrieved_at.date().isoformat(),
        "source_filename": destination.name,
        "original_filename": original_filename,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": (
            "official_api"
            if source.file_format == "json"
            and source.provider == "U.S. Bureau of Labor Statistics"
            else source.access_method
        ),
        "source_last_updated": provider_last_updated,
        "provider_last_updated": provider_last_updated,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "revision_policy": source.revision_notes,
        "ingestion_status": "downloaded",
        **request_metadata,
    }
    append_csv(config.root / "metadata" / "raw_file_manifest.csv", [record])
    logger.info(
        "Archived %s (%s bytes, sha256=%s)",
        source.source_id,
        record["byte_size"],
        checksum[:12],
    )
    return record


def download_sources(
    config: ProjectConfig,
    source_filter: str | None,
    force: bool,
    offline: bool,
    start_date: str | None,
    end_date: str | None,
    local_file: str | None = None,
) -> tuple[list[dict], list[dict]]:
    logger = logging.getLogger("la_housing.download")
    if local_file and source_filter != "redfin_zip_monthly":
        raise ValueError(
            "--local-file requires --source redfin_zip_monthly"
        )
    if offline:
        logger.info("Offline mode: download stage skipped")
        return [], []
    completed: list[dict] = []
    manual: list[dict] = []
    for source in config.selected_sources(source_filter):
        try:
            completed.append(
                download_source(
                    config,
                    source,
                    force=force,
                    explicitly_selected=source_filter is not None,
                    start_date=start_date,
                    end_date=end_date,
                    local_file=local_file,
                )
            )
        except Exception as exc:  # keep independent sources moving
            reason = f"{type(exc).__name__}: {exc}"
            logger.error("Could not acquire %s: %s", source.source_id, reason)
            if source_filter is not None:
                raise RuntimeError(
                    f"Could not acquire explicitly selected source "
                    f"{source.source_id}: {reason}"
                ) from exc
            manual.append(
                {
                    "source_id": source.source_id,
                    "provider": source.provider,
                    "source_page_url": source.source_page_url,
                    "download_url": source.download_url,
                    "expected_directory": f"data/raw/{_raw_subdir(source)}/",
                    "reason": reason,
                    "instruction": (
                        "Download the official file without modifying it, then run "
                        "python run_pipeline.py --stage download --source "
                        f"{source.source_id} --local-file PATH_TO_FILE. "
                        "The import command creates the immutable raw archive and provenance record."
                    ),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    if manual:
        pd.DataFrame(manual).to_csv(
            config.root / "metadata" / "manual_downloads.csv", index=False
        )
    return completed, manual
