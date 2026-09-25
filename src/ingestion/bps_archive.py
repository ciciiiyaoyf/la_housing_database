"""Immutable acquisition of official monthly Census BPS ASCII archives."""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, is_bps_ascii_payload, sha256_file

USER_AGENT = "UCLA housing research data pipeline/0.2"
_THREAD_LOCAL = threading.local()


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _THREAD_LOCAL.session = session
    return session


def _archive_coordinates(source: SourceConfig) -> tuple[str, str, str]:
    match = re.match(
        r"^(https://www2\.census\.gov/econ/bps/(County|State)/)(?:co|st)\d{4}c\.txt$",
        source.download_url,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            f"BPS archive URL does not match the documented Census pattern: "
            f"{source.download_url}"
        )
    geography = match.group(2).lower()
    prefix = "co" if geography == "county" else "st"
    return match.group(1), prefix, geography


def _targets(
    source: SourceConfig, start_date: str, end_date: str
) -> list[dict[str, str]]:
    base_url, prefix, geography = _archive_coordinates(source)
    periods = pd.period_range(
        pd.Timestamp(start_date).to_period("M"),
        pd.Timestamp(end_date).to_period("M"),
        freq="M",
    )
    return [
        {
            "source_id": source.source_id,
            "geography": geography,
            "archive_period": str(period),
            "download_url": (
                f"{base_url}{prefix}{period.strftime('%y%m')}c.txt"
            ),
        }
        for period in periods
    ]


def _existing_archive_files(config: ProjectConfig, source_id: str) -> pd.DataFrame:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(manifest_path, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return frame
    if "archive_period" not in frame:
        frame["archive_period"] = None
    inferred = frame["download_url"].str.extract(
        r"/(?:co|st)(\d{2})(\d{2})c\.txt$", expand=True
    )
    inferred_period = inferred[0].map(
        lambda year: f"{2000 + int(year):04d}" if pd.notna(year) else None
    ) + "-" + inferred[1]
    frame["archive_period"] = frame["archive_period"].fillna(inferred_period)
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    latest = (
        frame.loc[frame["archive_period"].notna()]
        .sort_values("_retrieved")
        .groupby("archive_period", as_index=False)
        .tail(1)
    )
    valid = [
        is_bps_ascii_payload(config.root / str(row.relative_path))
        for row in latest.itertuples()
    ]
    return latest.loc[valid]


def _download_one(
    config: ProjectConfig,
    source: SourceConfig,
    target: dict[str, str],
    raw_dir: Path,
) -> tuple[dict | None, dict]:
    retrieved_at = datetime.now(timezone.utc)
    period_token = target["archive_period"].replace("-", "")
    temp = raw_dir / (
        f".{source.source_id}.{period_token}.{retrieved_at:%Y%m%dT%H%M%S%fZ}.part"
    )
    coverage = {
        **target,
        "retrieved_at": retrieved_at.isoformat(),
        "download_status": "failed",
        "http_status": None,
        "relative_path": None,
        "checksum_sha256": None,
        "detail": None,
    }
    try:
        response = None
        max_attempts = 1 if target["geography"] == "state" else 3
        for attempt in range(max_attempts):
            try:
                stream_response = target["geography"] == "county"
                response = _session().get(
                    target["download_url"],
                    stream=stream_response,
                    timeout=(10, 30 if stream_response else 5),
                )
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt == max_attempts - 1:
                    raise
                time.sleep(1 + attempt * 2)
        assert response is not None
        coverage["http_status"] = response.status_code
        if response.status_code == 404:
            coverage["download_status"] = "not_published"
            coverage["detail"] = "Official archive URL returned HTTP 404."
            return None, coverage
        response.raise_for_status()
        with temp.open("wb") as handle:
            if target["geography"] == "state":
                handle.write(response.content)
            else:
                for chunk in response.iter_content(
                    chunk_size=int(config.pipeline["download_chunk_bytes"])
                ):
                    if chunk:
                        handle.write(chunk)
        checksum = sha256_file(temp)
        filename = (
            f"{source.source_id}_{period_token}_{retrieved_at:%Y%m%dT%H%M%SZ}_"
            f"{checksum[:12]}.txt"
        )
        destination = raw_dir / filename
        temp.replace(destination)
        valid_payload = is_bps_ascii_payload(destination)
        format_version = (
            "county_ascii_2000_30col"
            if target["geography"] == "county"
            else "state_ascii_2000_29col"
        )
        record = {
            "source_id": source.source_id,
            "provider": source.provider,
            "dataset_name": source.dataset_name,
            "source_page_url": source.source_page_url,
            "download_url": target["download_url"],
            "native_frequency": source.native_frequency,
            "geography_levels": "|".join(source.geography_levels),
            "retrieved_at": retrieved_at.isoformat(),
            "data_vintage": retrieved_at.date().isoformat(),
            "source_filename": destination.name,
            "relative_path": destination.relative_to(config.root).as_posix(),
            "byte_size": destination.stat().st_size,
            "checksum_sha256": checksum,
            "archive_period": target["archive_period"],
            "archive_format_version": format_version,
            "license_or_use_notes": source.license_or_use_notes,
            "revision_notes": source.revision_notes,
            "ingestion_status": (
                "downloaded" if valid_payload else "rejected_response"
            ),
        }
        coverage.update(
            {
                "download_status": (
                    "downloaded" if valid_payload else "rejected_response"
                ),
                "relative_path": record["relative_path"],
                "checksum_sha256": checksum,
                "detail": (
                    None
                    if valid_payload
                    else "Response did not contain the documented two-row BPS ASCII header."
                ),
            }
        )
        return record, coverage
    except Exception as exc:
        coverage["detail"] = f"{type(exc).__name__}: {exc}"
        return None, coverage
    finally:
        if temp.exists():
            temp.unlink()


def _recover_unmanifested_files(
    config: ProjectConfig,
    source: SourceConfig,
    raw_dir: Path,
) -> int:
    """Register complete immutable files left by an interrupted batch."""
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    known_paths: set[str] = set()
    if manifest_path.exists():
        manifest = pd.read_csv(manifest_path, dtype=str)
        known_paths = set(manifest.get("relative_path", pd.Series(dtype=str)).dropna())
    _, prefix, geography = _archive_coordinates(source)
    pattern = re.compile(
        rf"^{re.escape(source.source_id)}_(\d{{6}})_(\d{{8}}T\d{{6}}Z)_"
        r"([0-9a-f]{12})\.txt$"
    )
    records: list[dict] = []
    for path in raw_dir.glob(f"{source.source_id}_*.txt"):
        relative = path.relative_to(config.root).as_posix()
        if relative in known_paths:
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        period_token, retrieved_token, checksum_prefix = match.groups()
        checksum = sha256_file(path)
        if not checksum.startswith(checksum_prefix):
            continue
        archive_period = f"{period_token[:4]}-{period_token[4:]}"
        retrieved_at = datetime.strptime(
            retrieved_token, "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
        base_url, _, _ = _archive_coordinates(source)
        records.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "dataset_name": source.dataset_name,
                "source_page_url": source.source_page_url,
                "download_url": (
                    f"{base_url}{prefix}"
                    f"{pd.Period(archive_period).strftime('%y%m')}c.txt"
                ),
                "native_frequency": source.native_frequency,
                "geography_levels": "|".join(source.geography_levels),
                "retrieved_at": retrieved_at.isoformat(),
                "data_vintage": retrieved_at.date().isoformat(),
                "source_filename": path.name,
                "relative_path": relative,
                "byte_size": path.stat().st_size,
                "checksum_sha256": checksum,
                "archive_period": archive_period,
                "archive_format_version": (
                    "county_ascii_2000_30col"
                    if geography == "county"
                    else "state_ascii_2000_29col"
                ),
                "license_or_use_notes": source.license_or_use_notes,
                "revision_notes": source.revision_notes,
                "ingestion_status": "recovered_after_interrupted_batch",
            }
        )
    if records:
        append_csv(manifest_path, records)
    return len(records)


def download_bps_archive(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    """Download or reuse every documented monthly current-period archive file."""
    logger = logging.getLogger("la_housing.download.bps")
    archive_config = config.pipeline["census_bps"]
    effective_start = start_date or str(archive_config["archive_start_date"])
    effective_end = (
        end_date
        or source.selected_end_date
        or pd.Timestamp.now().date().isoformat()
    )
    targets = _targets(source, effective_start, effective_end)
    existing = _existing_archive_files(config, source.source_id)
    existing_by_period = (
        {str(row["archive_period"]): row for _, row in existing.iterrows()}
        if not existing.empty
        else {}
    )
    raw_dir = config.root / "data" / "raw" / "census_bps"
    raw_dir.mkdir(parents=True, exist_ok=True)
    recovered = _recover_unmanifested_files(config, source, raw_dir)
    if recovered:
        logger.info(
            "Recovered %s complete immutable files from an interrupted %s batch",
            recovered,
            source.source_id,
        )
        existing = _existing_archive_files(config, source.source_id)
        existing_by_period = {
            str(row["archive_period"]): row for _, row in existing.iterrows()
        }
    records: list[dict] = []
    coverage: list[dict] = []
    pending: list[dict[str, str]] = []
    for target in targets:
        prior = existing_by_period.get(target["archive_period"])
        prior_path = (
            config.root / str(prior["relative_path"])
            if prior is not None
            else None
        )
        if not force and prior is not None and prior_path is not None and prior_path.exists():
            coverage.append(
                {
                    **target,
                    "retrieved_at": prior["retrieved_at"],
                    "download_status": "reused",
                    "http_status": None,
                    "relative_path": prior["relative_path"],
                    "checksum_sha256": prior["checksum_sha256"],
                    "detail": "Reused latest immutable local archive.",
                }
            )
        else:
            pending.append(target)
    workers = int(archive_config.get("download_workers", 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_download_one, config, source, target, raw_dir): target
            for target in pending
        }
        for future in as_completed(futures):
            record, result = future.result()
            coverage.append(result)
            if record is not None:
                records.append(record)
    if records:
        append_csv(config.root / "metadata" / "raw_file_manifest.csv", records)
    coverage_frame = pd.DataFrame(coverage).sort_values(
        ["source_id", "archive_period"]
    )
    coverage_path = config.root / "output" / "coverage" / "bps_archive_download.csv"
    if coverage_path.exists():
        prior_coverage = pd.read_csv(coverage_path, dtype=str)
        current_keys = set(
            zip(
                coverage_frame["source_id"],
                coverage_frame["archive_period"],
                strict=True,
            )
        )
        prior_coverage = prior_coverage.loc[
            [
                (row.source_id, row.archive_period) not in current_keys
                for row in prior_coverage.itertuples()
            ]
        ]
        coverage_frame = pd.concat(
            [prior_coverage, coverage_frame], ignore_index=True, sort=False
        )
    coverage_frame.to_csv(coverage_path, index=False)
    successful = coverage_frame.loc[
        coverage_frame["source_id"].eq(source.source_id)
        & coverage_frame["download_status"].isin({"downloaded", "reused"})
    ]
    failed = coverage_frame.loc[
        coverage_frame["source_id"].eq(source.source_id)
        & coverage_frame["download_status"].eq("failed")
    ]
    if successful.empty:
        raise RuntimeError(f"No BPS archive files available for {source.source_id}")
    if not failed.empty:
        logger.warning(
            "%s BPS archive downloads failed for %s",
            len(failed),
            source.source_id,
        )
    logger.info(
        "BPS archive %s: %s downloaded, %s reused, %s not published, %s failed",
        source.source_id,
        sum(item["download_status"] == "downloaded" for item in coverage),
        sum(item["download_status"] == "reused" for item in coverage),
        sum(item["download_status"] == "not_published" for item in coverage),
        sum(item["download_status"] == "failed" for item in coverage),
    )
    return {
        "source_id": source.source_id,
        "archive_start": successful["archive_period"].min(),
        "archive_end": successful["archive_period"].max(),
        "archive_files": len(successful),
        "new_files": len(records),
        "ingestion_status": "downloaded",
    }
