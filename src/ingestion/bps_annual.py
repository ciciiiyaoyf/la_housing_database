"""Immutable acquisition of official final-annual Census BPS county files.

This module is deliberately separate from :mod:`src.ingestion.bps_archive`,
which acquires monthly current/YTD files.  Annual files use the provider's
``coYYYYa.txt`` names and must never be treated as monthly observations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests

from src.common.io import append_csv, sha256_file

SOURCE_ID = "census_bps_county_annual_final"
PROVIDER = "U.S. Census Bureau"
DATASET_NAME = "Building Permits Survey final annual county file"
SOURCE_PAGE_URL = "https://www2.census.gov/econ/bps/County/"
ANNUAL_URL_TEMPLATE = SOURCE_PAGE_URL + "co{year:04d}a.txt"
FIRST_SUPPORTED_YEAR = 1990
LAST_SUPPORTED_YEAR = 2025
USER_AGENT = "UCLA housing research data pipeline/0.2"


class BPSAnnualDownloadError(RuntimeError):
    """Raised when an official annual BPS file cannot be safely archived."""


def annual_county_url(year: int) -> str:
    """Return the documented official URL for a supported survey year."""
    year = int(year)
    latest_calendar_year = datetime.now(timezone.utc).year - 1
    if not FIRST_SUPPORTED_YEAR <= year <= latest_calendar_year:
        raise ValueError(
            f"BPS annual county year must be {FIRST_SUPPORTED_YEAR}-"
            f"{latest_calendar_year}; got {year}"
        )
    return ANNUAL_URL_TEMPLATE.format(year=year)


def _published_annual_years(session: Any | None = None) -> set[int]:
    """Use the provider's directory to discover published final annual files."""
    client = session or requests.Session()
    response = client.get(
        SOURCE_PAGE_URL, timeout=60, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    years: set[int] = set()
    for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', response.text, re.I):
        match = re.fullmatch(
            r"https://www2\.census\.gov/econ/bps/County/co(\d{4})a\.txt",
            urljoin(SOURCE_PAGE_URL, href),
        )
        if match and FIRST_SUPPORTED_YEAR <= int(match[1]) < datetime.now(timezone.utc).year:
            years.add(int(match[1]))
    if not years:
        raise BPSAnnualDownloadError("Official BPS directory contains no recognized final annual links")
    return years


def annual_county_targets(
    start_year: int = FIRST_SUPPORTED_YEAR,
    end_year: int = LAST_SUPPORTED_YEAR,
) -> list[dict[str, str | int]]:
    """Build the complete inclusive set of official final-annual targets."""
    start_year = int(start_year)
    end_year = int(end_year)
    annual_county_url(start_year)
    annual_county_url(end_year)
    if start_year > end_year:
        raise ValueError("BPS annual start_year must not exceed end_year")
    return [
        {
            "source_id": SOURCE_ID,
            "survey_year": year,
            "annual_release_status": "final",
            "source_filename": f"co{year:04d}a.txt",
            "download_url": annual_county_url(year),
        }
        for year in range(start_year, end_year + 1)
    ]


def _validate_annual_payload(payload: bytes, year: int) -> None:
    if not payload:
        raise BPSAnnualDownloadError(f"co{year}a.txt returned an empty response")
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise BPSAnnualDownloadError(
            f"co{year}a.txt is not a decodable Census ASCII file"
        ) from exc
    nonempty = [line for line in lines if line.strip()]
    if len(nonempty) < 3:
        raise BPSAnnualDownloadError(
            f"co{year}a.txt does not contain two headers and data"
        )
    first = [cell.strip() for cell in nonempty[0].split(",")]
    second = [cell.strip() for cell in nonempty[1].split(",")]
    if len(first) == 31 and first[-1] == "":
        # Historical provider headers include one harmless extra trailing
        # delimiter; the second header and every data row retain 30 fields.
        first = first[:-1]
    if not first or first[0] != "Survey" or not second or second[0] != "Date":
        raise BPSAnnualDownloadError(
            f"co{year}a.txt does not have the documented BPS ASCII header"
        )
    sample_widths = {len(line.split(",")) for line in nonempty[2:12]}
    if len(second) != 30 or sample_widths != {30}:
        raise BPSAnnualDownloadError(
            f"co{year}a.txt second header/data width changed from the "
            "documented 30 columns"
        )


def _latest_reusable_record(root: Path, year: int) -> dict[str, str] | None:
    manifest_path = root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return None
    manifest = pd.read_csv(manifest_path, dtype=str)
    required = {"source_id", "relative_path", "checksum_sha256", "retrieved_at"}
    if not required.issubset(manifest.columns):
        return None
    survey_year = manifest.get(
        "survey_year", manifest.get("archive_period", pd.Series(index=manifest.index))
    )
    matches = manifest.loc[
        manifest["source_id"].eq(SOURCE_ID) & survey_year.astype(str).eq(str(year))
    ].copy()
    if matches.empty:
        return None
    matches["_retrieved"] = pd.to_datetime(
        matches["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    for _, row in matches.sort_values("_retrieved", ascending=False).iterrows():
        record = {
            key: str(value)
            for key, value in row.drop(labels="_retrieved").to_dict().items()
            if pd.notna(value)
        }
        path = root / record["relative_path"]
        if not path.is_file():
            continue
        if sha256_file(path) != record["checksum_sha256"]:
            continue
        try:
            _validate_annual_payload(path.read_bytes(), year)
        except BPSAnnualDownloadError:
            continue
        return record
    return None


def download_annual_county_file(
    root: Path,
    year: int,
    *,
    force: bool = False,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Download one official annual file into an immutable raw archive.

    ``session`` and ``retrieved_at`` are injectable to make acquisition tests
    deterministic.  No credential is required by the Census file server.
    """
    root = Path(root).resolve()
    year = int(year)
    url = annual_county_url(year)
    if not force:
        prior = _latest_reusable_record(root, year)
        if prior is not None:
            return {**prior, "download_status": "reused"}

    client = session or requests.Session()
    response = client.get(
        url,
        timeout=timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    )
    status_code = int(getattr(response, "status_code", 0))
    if status_code != 200:
        raise BPSAnnualDownloadError(
            f"Official BPS annual URL returned HTTP {status_code}: {url}"
        )
    payload = bytes(response.content)
    _validate_annual_payload(payload, year)

    retrieved = retrieved_at or datetime.now(timezone.utc)
    if retrieved.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    retrieved = retrieved.astimezone(timezone.utc)
    raw_dir = root / "data" / "raw" / "census_bps_annual"
    raw_dir.mkdir(parents=True, exist_ok=True)
    temp_path = raw_dir / (
        f".{SOURCE_ID}.{year}.{retrieved:%Y%m%dT%H%M%S%fZ}.part"
    )
    try:
        temp_path.write_bytes(payload)
        checksum = sha256_file(temp_path)
        stored_filename = (
            f"{SOURCE_ID}_{year}_{retrieved:%Y%m%dT%H%M%S%fZ}_"
            f"{checksum[:12]}.txt"
        )
        destination = raw_dir / stored_filename
        if destination.exists():
            raise FileExistsError(
                f"Refusing to replace existing immutable archive: {destination}"
            )
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    record: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "provider": PROVIDER,
        "dataset_name": DATASET_NAME,
        "source_page_url": SOURCE_PAGE_URL,
        "download_url": url,
        "native_frequency": "annual",
        "geography_levels": "county",
        "retrieved_at": retrieved.isoformat(),
        "data_vintage": retrieved.date().isoformat(),
        "survey_year": str(year),
        "archive_period": str(year),
        "annual_release_status": "final",
        "original_filename": f"co{year:04d}a.txt",
        "source_filename": destination.name,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "archive_format_version": "county_annual_ascii_30col",
        "retrieval_method": "official_public_download",
        "revision_notes": (
            "Final annual provider file; later provider corrections remain separate "
            "immutable retrievals selected by retrieval timestamp."
        ),
        "ingestion_status": "downloaded",
        "download_status": "downloaded",
    }
    append_csv(root / "metadata" / "raw_file_manifest.csv", [record])
    return record


def download_bps_annual_county(
    root: Path,
    *,
    start_year: int = FIRST_SUPPORTED_YEAR,
    end_year: int | None = LAST_SUPPORTED_YEAR,
    force: bool = False,
    session: Any | None = None,
    refresh_latest: bool = False,
) -> list[dict[str, Any]]:
    """Acquire an inclusive range of final-annual county files."""
    if refresh_latest:
        published = _published_annual_years(session)
        end_year = min(int(end_year), max(published)) if end_year is not None else max(published)
        missing = set(range(int(start_year), end_year + 1)).difference(published)
        if missing:
            raise BPSAnnualDownloadError(
                f"Official BPS annual directory is missing required years: {sorted(missing)}"
            )
    elif end_year is None:
        end_year = LAST_SUPPORTED_YEAR
    return [
        download_annual_county_file(
            root,
            int(target["survey_year"]),
            force=force or (refresh_latest and int(target["survey_year"]) == end_year),
            session=session,
        )
        for target in annual_county_targets(start_year, end_year)
    ]
