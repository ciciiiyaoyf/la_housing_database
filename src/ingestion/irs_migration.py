"""Immutable acquisition of official IRS SOI county migration CSV archives."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file

USER_AGENT = "UCLA housing research data pipeline/0.2"
IRS_MIGRATION_DIRECTIONS = ("inflow", "outflow")


def _period_targets(start_date: str, end_date: str) -> list[dict[str, str]]:
    """Build IRS filing-year-pair CSV targets.

    IRS county migration files are named by prior/current filing year pairs, e.g.
    countyinflow2223.csv for the 2022-2023 migration interval. The observation
    date used downstream is the current/end filing year.
    """
    start_year = max(2011, pd.Timestamp(start_date).year)
    end_year = min(2023, pd.Timestamp(end_date).year)
    targets: list[dict[str, str]] = []
    for end_year_item in range(start_year, end_year + 1):
        prior_year = end_year_item - 1
        token = f"{prior_year % 100:02d}{end_year_item % 100:02d}"
        archive_period = f"{prior_year}-{end_year_item}"
        for direction in IRS_MIGRATION_DIRECTIONS:
            targets.append(
                {
                    "archive_period": archive_period,
                    "migration_direction": direction,
                    "download_url": (
                        "https://www.irs.gov/pub/irs-soi/"
                        f"county{direction}{token}.csv"
                    ),
                }
            )
    return targets


def _existing_files(config: ProjectConfig, source_id: str) -> pd.DataFrame:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(manifest_path, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return frame
    if "archive_period" not in frame:
        frame["archive_period"] = None
    if "migration_direction" not in frame:
        frame["migration_direction"] = None
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    latest = (
        frame.loc[
            frame["archive_period"].notna()
            & frame["migration_direction"].notna()
        ]
        .sort_values("_retrieved")
        .groupby(["archive_period", "migration_direction"], as_index=False)
        .tail(1)
    )
    exists = [
        (config.root / str(row.relative_path)).exists()
        for row in latest.itertuples()
    ]
    return latest.loc[exists].drop(columns="_retrieved")


def _header_version(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = handle.readline().strip().lower()
    if "y2_statefips" in header or "y1_statefips" in header:
        return "county_migration_csv_2011plus_schema"
    if "state_code_dest" in header or "state_code_origin" in header:
        return "county_migration_csv_2010_legacy_schema"
    return "county_migration_csv_unknown_schema"


def _download_one(
    config: ProjectConfig,
    source: SourceConfig,
    target: dict[str, str],
    raw_dir: Path,
) -> tuple[dict | None, dict]:
    retrieved_at = datetime.now(timezone.utc)
    period_token = target["archive_period"].replace("-", "")
    direction = target["migration_direction"]
    temp = raw_dir / (
        f".{source.source_id}.{period_token}.{direction}."
        f"{retrieved_at:%Y%m%dT%H%M%S%fZ}.part"
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
        response = requests.get(
            target["download_url"],
            timeout=120,
            headers={"User-Agent": USER_AGENT},
        )
        coverage["http_status"] = response.status_code
        if response.status_code == 404:
            coverage["download_status"] = "not_published"
            coverage["detail"] = "Official IRS CSV URL returned HTTP 404."
            return None, coverage
        response.raise_for_status()
        temp.write_bytes(response.content)
        archive_format_version = _header_version(temp)
        if archive_format_version == "county_migration_csv_unknown_schema":
            coverage["download_status"] = "rejected_response"
            coverage["detail"] = "Response did not contain a known IRS CSV header."
            return None, coverage
        checksum = sha256_file(temp)
        filename = (
            f"{source.source_id}_{period_token}_{direction}_"
            f"{retrieved_at:%Y%m%dT%H%M%SZ}_{checksum[:12]}.csv"
        )
        destination = raw_dir / filename
        temp.replace(destination)
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
            "original_filename": Path(urlparse(target["download_url"]).path).name,
            "stored_filename": destination.name,
            "relative_path": destination.relative_to(config.root).as_posix(),
            "byte_size": destination.stat().st_size,
            "checksum_sha256": checksum,
            "retrieval_method": "official_download",
            "source_last_updated": None,
            "provider_last_updated": None,
            "archive_period": target["archive_period"],
            "migration_direction": direction,
            "archive_format_version": archive_format_version,
            "response_latest_observation": f"{target['archive_period'][-4:]}-12-31",
            "license_or_use_notes": source.license_or_use_notes,
            "revision_notes": source.revision_notes,
            "revision_policy": source.revision_notes,
            "ingestion_status": "downloaded",
        }
        coverage.update(
            {
                "download_status": "downloaded",
                "relative_path": record["relative_path"],
                "checksum_sha256": checksum,
                "detail": None,
            }
        )
        return record, coverage
    except Exception as exc:
        coverage["detail"] = f"{type(exc).__name__}: {exc}"
        return None, coverage
    finally:
        if temp.exists():
            temp.unlink()


def download_irs_soi_county_migration(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    logger = logging.getLogger("la_housing.download.irs_soi")
    effective_start = start_date or source.selected_start_date or "2011-01-01"
    effective_end = end_date or source.selected_end_date or "2023-12-31"
    targets = _period_targets(effective_start, effective_end)
    existing = _existing_files(config, source.source_id)
    existing_keys = (
        {
            (str(row["archive_period"]), str(row["migration_direction"]))
            for _, row in existing.iterrows()
        }
        if not existing.empty
        else set()
    )
    raw_dir = config.root / "data" / "raw" / (source.raw_subdir or "irs_soi")
    raw_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    coverage: list[dict] = []
    for target in targets:
        key = (target["archive_period"], target["migration_direction"])
        if not force and key in existing_keys:
            prior = existing.loc[
                existing["archive_period"].eq(key[0])
                & existing["migration_direction"].eq(key[1])
            ].iloc[-1]
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
            continue
        record, result = _download_one(config, source, target, raw_dir)
        coverage.append(result)
        if record is not None:
            records.append(record)
    if records:
        append_csv(config.root / "metadata" / "raw_file_manifest.csv", records)
    coverage_frame = pd.DataFrame(coverage).sort_values(
        ["archive_period", "migration_direction"]
    )
    coverage_path = config.root / "output" / "coverage" / "irs_soi_migration_download.csv"
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_frame.to_csv(coverage_path, index=False)
    successful = coverage_frame.loc[
        coverage_frame["download_status"].isin({"downloaded", "reused"})
    ]
    if successful.empty:
        raise RuntimeError("No IRS SOI county migration files available")
    logger.info(
        "IRS SOI migration: %s downloaded, %s reused, %s not published, %s failed",
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
