"""Source-specific acquisition with immutable, checksummed raw archives."""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file
from src.ingestion.bps_archive import download_bps_archive

RAW_SUBDIR = {
    "Zillow Research": "zillow",
    "Redfin Data Center": "redfin",
    "U.S. Bureau of Labor Statistics": "bls",
    "Freddie Mac": "freddie_mac",
    "U.S. Census Bureau": "census_bps",
}

BLS_SERIES = (
    "LAUCN060370000000003",
    "LAUCN060370000000004",
    "LAUCN060370000000005",
    "LAUCN060370000000006",
)


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
                headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
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
            headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
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


def _extension(source: SourceConfig) -> str:
    return {
        "csv": ".csv",
        "json": ".json",
        "txt": ".txt",
        "tsv.gz": ".tsv.gz",
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
    raw_dir = config.root / "data" / "raw" / RAW_SUBDIR[source.provider]
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
    if not force:
        existing = _today_manifest_record(config, source.source_id)
        if existing:
            logger.info("Reusing today's raw archive for %s", source.source_id)
            return existing[1]

    raw_dir = config.root / "data" / "raw" / RAW_SUBDIR[source.provider]
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc)
    temp_path = raw_dir / f".{source.source_id}.{retrieved_at:%Y%m%dT%H%M%SZ}.part"
    resolved_download_url = source.download_url
    request_metadata: dict[str, str | int] = {}
    try:
        if source.source_id == "bls_laus_la_county":
            request_metadata = _download_bls(
                source, temp_path, start_date, end_date
            )
        else:
            with requests.get(
                resolved_download_url,
                stream=True,
                timeout=int(config.pipeline["request_timeout_seconds"]),
                headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
            ) as response:
                _write_response(
                    response,
                    temp_path,
                    int(config.pipeline["download_chunk_bytes"]),
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
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
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
                    "expected_directory": (
                        f"data/raw/{RAW_SUBDIR[source.provider]}/"
                    ),
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
