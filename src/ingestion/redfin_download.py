"""Download the public files used by Redfin's official Data Center download hub.

Resolve the ZIP file through Redfin's published index, archive both files unchanged,
and validate the national CSV before recording a successful acquisition.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any, BinaryIO, Callable
from urllib.parse import urlsplit

import duckdb
import pandas as pd
import requests

from src.cleaning.redfin_zip import redfin_zip_schema_version
from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file

LOGGER = logging.getLogger("la_housing.download")
SOURCE_ID = "redfin_zip_monthly"
DOWNLOAD_HUB = "https://www.redfin.com/news/data-center/downloads/"
PUBLIC_BASE = "https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_data_center/"
PUBLIC_INDEX = PUBLIC_BASE + "index.json"
RETRIEVAL_METHOD = "official_public_download"
MANUAL_IMPORT_GUIDANCE = (
    "An official manual CSV can also be supplied with --local-file; its schema and "
    "rolling windows must pass the same validation. See docs/database_build_and_refresh.md, "
    "Redfin downloads."
)


def _published_zip_url(index: dict[str, Any]) -> str:
    try:
        relative = index["housing_market"]["monthly"]["zip"]["all"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Redfin's public index has no monthly all-ZIP download") from exc
    if not isinstance(relative, str):
        raise ValueError("Redfin's ZIP download path must be a string")
    parsed = urlsplit(relative)
    segments = relative.split("/")
    if (
        parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
        or not relative.startswith("housing_market/monthly/")
        or not relative.endswith(".csv")
        or any(part in {"", ".", ".."} for part in segments)
        or any(character in relative for character in ("%", "\\", ":"))
        or any(character.isspace() for character in relative)
    ):
        raise ValueError("Redfin's index ZIP path is outside the approved public source")
    return PUBLIC_BASE + relative


def _fetch_file(
    session: requests.Session, url: str, target: BinaryIO, timeout: int,
    *, max_bytes: int | None = None,
) -> dict[str, str | int]:
    """Require a complete unencoded public object, with no redirects or retries."""
    deadline = monotonic() + timeout
    with session.get(
        url, stream=True, timeout=(30, timeout), allow_redirects=False,
        headers={"Accept-Encoding": "identity"},
    ) as response:
        if response.status_code != 200:
            raise RuntimeError(
                f"Redfin public file request failed (HTTP {response.status_code}). "
                "No new data was imported. Do not repeatedly retry access-limit errors. "
                + MANUAL_IMPORT_GUIDANCE
            )
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Redfin returned an encoded object; cannot verify original bytes")
        try:
            expected = int(response.headers["Content-Length"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Redfin public file has no valid Content-Length") from exc
        if expected <= 0 or (max_bytes is not None and expected > max_bytes):
            raise ValueError("Redfin public file has an unexpected byte size")
        received = 0
        report_at = 128 * 1024 * 1024
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if monotonic() > deadline:
                raise TimeoutError("Redfin public download exceeded its time limit")
            received += len(chunk)
            if received > expected:
                raise ValueError("Redfin public file exceeds its Content-Length")
            target.write(chunk)
            if received >= report_at:
                LOGGER.info("Redfin download: %.0f / %.0f MiB", received / 2**20, expected / 2**20)
                report_at += 128 * 1024 * 1024
        if received != expected:
            raise ValueError(
                f"Incomplete Redfin download: received {received} of {expected} bytes"
            )
        return {
            "http_status": response.status_code,
            "http_content_length": expected,
            "http_content_type": response.headers.get("Content-Type", ""),
            "http_last_modified": response.headers.get("Last-Modified", ""),
            "http_etag": response.headers.get("ETag", ""),
        }


def _official_download(
    destination: Path, start_date: str, end_date: str | None, timeout_seconds: int
) -> dict[str, Any]:
    """Acquire the full national object; date/geography selection belongs to cleaning."""
    with requests.Session() as session:
        index_buffer = io.BytesIO()
        index_http = _fetch_file(
            session, PUBLIC_INDEX, index_buffer, timeout_seconds, max_bytes=2_000_000
        )
        index_bytes = index_buffer.getvalue()
        url = _published_zip_url(json.loads(index_bytes))
        with destination.open("xb") as target:
            csv_http = _fetch_file(session, url, target, timeout_seconds)
    return {
        "original_filename": PurePosixPath(urlsplit(url).path).name,
        "download_url": url,
        "source_index_url": PUBLIC_INDEX,
        "request_query": "housing_market.monthly.zip.all",
        "raw_scope": "unmodified national all-ZIP CSV; all published dates and metrics",
        **csv_http,
        **{f"index_{key}": value for key, value in index_http.items()},
        "_index_bytes": index_bytes,
    }


def validate_zip_download(path: Path) -> dict[str, str | int]:
    """Reject wrong products, truncated dates, and malformed windows before archive."""
    header = pd.read_csv(path, nrows=0)
    schema_version = redfin_zip_schema_version(tuple(header.columns))
    numeric_invalid = " OR ".join(
        f'''(trim("{column}") NOT IN ('', 'NA') AND '''
        f'(try_cast("{column}" AS DOUBLE) IS NULL '
        f'OR NOT isfinite(try_cast("{column}" AS DOUBLE))))'
        for column in header.columns[8:]
    )
    with duckdb.connect() as connection:
        connection.read_csv(
            str(path), header=True, all_varchar=True
        ).create_view("source")
        result = connection.execute(
            f'''SELECT count(*),
                min(try_cast("PERIOD BEGIN" AS DATE)),
                max(try_cast("PERIOD END" AS DATE)),
                min(try_cast("LAST UPDATED" AS DATE)),
                max(try_cast("LAST UPDATED" AS DATE)),
                count(*) FILTER (WHERE "FREQUENCY" IS DISTINCT FROM 'Rolling 3 Months'
                    OR "REGION TYPE" IS DISTINCT FROM 'Zip'
                    OR NOT regexp_full_match(coalesce("REGION NAME", ''), '[0-9]{{5}}')
                    OR try_cast("PERIOD BEGIN" AS DATE) IS NULL
                    OR try_cast("PERIOD END" AS DATE) IS NULL
                    OR try_cast("LAST UPDATED" AS DATE) IS NULL
                    OR try_cast("LAST UPDATED" AS DATE) < try_cast("PERIOD END" AS DATE)
                    OR try_cast("PERIOD BEGIN" AS DATE) !=
                        date_trunc('month', try_cast("PERIOD END" AS DATE)) - INTERVAL 2 MONTH
                    OR try_cast("PERIOD END" AS DATE) != last_day(try_cast("PERIOD END" AS DATE))),
                count(*) - count(DISTINCT ("REGION NAME", "PERIOD END")),
                count(*) FILTER (WHERE {numeric_invalid})
            FROM source'''
        ).fetchone()
    rows, first, last, first_update, last_update, invalid, duplicates, invalid_numeric = result
    if not rows or invalid or duplicates or invalid_numeric or first_update != last_update:
        raise ValueError(
            "Redfin ZIP download failed validation: "
            f"rows={rows}, invalid_windows_or_identifiers={invalid}, "
            f"duplicate_zip_periods={duplicates}, invalid_numeric_rows={invalid_numeric}, "
            f"provider_updates={first_update}/{last_update}"
        )
    return {
        "response_observation_count": rows,
        "response_first_observation": first.isoformat(),
        "response_latest_observation": last.isoformat(),
        "provider_last_updated": last_update.isoformat(),
        "source_frequency": "Rolling 3 Months",
        "source_schema_version": schema_version,
        "latest_observation_evidence": "maximum PERIOD END in validated CSV",
    }


def download_redfin_zip(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    downloader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Archive the national CSV and its discovery index without rewriting raw bytes."""
    if source.source_id != SOURCE_ID:
        raise ValueError("The Redfin public adapter only supports the ZIP source")
    start = pd.Timestamp(start_date or source.selected_start_date or "2019-01-01")
    requested_end = (pd.Timestamp(end_date) + pd.offsets.MonthEnd(0)) if end_date else None
    if requested_end is not None and start > requested_end:
        raise ValueError("Redfin start date exceeds the requested end month")
    start_text = start.date().isoformat()
    now = datetime.now(timezone.utc)
    manifest_path = config.root / "metadata/raw_file_manifest.csv"
    if manifest_path.exists() and not force:
        manifest = pd.read_csv(manifest_path, dtype=str).fillna("")
        for record in reversed(manifest.to_dict("records")):
            if (
                record["source_id"] == SOURCE_ID
                and record.get("retrieval_method") == RETRIEVAL_METHOD
                and record.get("retrieved_at", "").startswith(now.date().isoformat())
                and record.get("selected_start_date") == start_text
                and record.get("requested_end_date", "") == (end_date or "latest_published")
            ):
                path = config.root / record["relative_path"]
                index_path = config.root / record.get("source_index_relative_path", "")
                if (
                    path.is_file() and sha256_file(path) == record["checksum_sha256"]
                    and index_path.is_file()
                    and sha256_file(index_path) == record.get("source_index_checksum_sha256")
                ):
                    return {**record, "download_status": "reused_today"}
    raw_dir = config.root / "data/raw/redfin"
    raw_dir.mkdir(parents=True, exist_ok=True)
    token = now.strftime("%Y%m%dT%H%M%S%fZ")
    partial = raw_dir / f".{SOURCE_ID}_{token}.part"
    index_partial = raw_dir / f".{SOURCE_ID}_index_{token}.part"
    if partial.exists() or index_partial.exists():
        raise FileExistsError("Redfin acquisition filename collision; existing files retained")
    try:
        details = (downloader or _official_download)(
            partial, start_text, end_date,
            int(config.pipeline.get("redfin_download_timeout_seconds", 900)),
        )
        index_bytes = details.pop("_index_bytes")
        with index_partial.open("xb") as target:
            target.write(index_bytes)
        LOGGER.info("Validating Redfin national ZIP CSV before archival")
        validation = validate_zip_download(partial)
        latest = pd.Timestamp(validation["response_latest_observation"])
        selected_end = requested_end if requested_end is not None else latest
        first_end = pd.Timestamp(validation["response_first_observation"]) + pd.DateOffset(months=2) + pd.offsets.MonthEnd(0)
        if start > selected_end or selected_end > latest or selected_end < first_end:
            raise ValueError("Redfin CSV does not cover the requested date range")
        checksum = sha256_file(partial)
        index_checksum = sha256_file(index_partial)
        destination = raw_dir / f"{SOURCE_ID}_{token}_{checksum[:12]}.csv"
        index_destination = raw_dir / f"{SOURCE_ID}_index_{token}_{index_checksum[:12]}.json"
        if destination.exists() or index_destination.exists():
            raise FileExistsError("Refusing to replace an existing Redfin raw archive")
        partial.rename(destination)
        index_partial.rename(index_destination)
    finally:
        partial.unlink(missing_ok=True)
        index_partial.unlink(missing_ok=True)
    record = {
        "source_id": SOURCE_ID,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": DOWNLOAD_HUB,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": now.isoformat(),
        "data_vintage": validation["provider_last_updated"],
        "source_filename": destination.name,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "source_index_relative_path": index_destination.relative_to(config.root).as_posix(),
        "source_index_checksum_sha256": index_checksum,
        "source_index_byte_size": index_destination.stat().st_size,
        "source_index_original_filename": "index.json",
        "retrieval_method": RETRIEVAL_METHOD,
        "selected_start_date": start_text,
        "selected_end_date": selected_end.date().isoformat(),
        "requested_end_date": end_date or "latest_published",
        "rolling_window_months": 3,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "revision_policy": source.revision_notes,
        "ingestion_status": "downloaded",
        **details,
        **validation,
        "source_last_updated": validation["provider_last_updated"],
    }
    append_csv(manifest_path, [record])
    return record
