"""Download official UI 539 bulk CSV, retaining the legacy XML acquisition path."""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from src.common.config import SourceConfig

SOURCE_ID = "dol_ca_ui_claims"
CLAIMS_ENDPOINT = "https://oui.doleta.gov/unemploy/wkclaims/report.asp"
CLAIMS_CSV_ENDPOINT = "https://oui.doleta.gov/unemploy/csv/ar539.csv"
CLAIMS_DATA_MAP = "https://oui.doleta.gov/dmstree/handbooks/402/402_4/4024c6/4024c6.pdf"
CLAIMS_CSV_COLUMNS = {"st", "rptdate", "c2", "c3", "c7", "c8", "c12"}


def read_claims_csv(payload: bytes) -> pd.DataFrame:
    """Select California and reconstruct the provider's published UI aggregates.

    The official ar539 field map (printed page 41) defines rptdate as the
    report-week end and c2 as the reflected-week end. Regular initial claims
    c3 plus STC/workshare equivalents c7 reproduces InitialClaims in the XML.
    Continued claims c8 plus STC/workshare equivalents c12 reproduces
    ContinuedClaims. Do not substitute the non-equivalent workshare counts.
    Missing components propagate; they are never assumed to be zero.
    """
    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, UnicodeError) as exc:
        raise ValueError("DOL bulk export is not a valid CSV") from exc
    missing = CLAIMS_CSV_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"DOL bulk CSV is missing required fields: {sorted(missing)}")
    frame = frame.loc[frame["st"].str.strip().eq("CA")].copy()
    if frame.empty:
        raise ValueError("DOL bulk CSV has no California observations")
    for field in ("rptdate", "c2"):
        frame[field] = pd.to_datetime(frame[field], format="%Y-%m-%d", errors="raise")
        if frame[field].isna().any() or frame[field].duplicated().any():
            raise ValueError(f"DOL bulk CSV has missing or duplicate California {field}")
        if not frame[field].dt.dayofweek.eq(5).all():
            raise ValueError(f"DOL bulk CSV {field} must contain Saturdays")
    if not (frame["rptdate"] - frame["c2"]).eq(pd.Timedelta(days=7)).all():
        raise ValueError("DOL reflected week must precede its report week by seven days")
    for field in ("c3", "c7", "c8", "c12"):
        text = frame[field].str.strip().str.replace(",", "", regex=False)
        is_missing = text.isin({"", "-", "NA", "N/A", "(NA)"})
        invalid = ~is_missing & ~text.str.fullmatch(r"\d+")
        if invalid.any():
            raise ValueError(f"DOL bulk CSV {field} contains invalid claim counts")
        frame[field] = pd.to_numeric(text.where(~is_missing), errors="raise")
    frame["InitialClaims"] = frame["c3"] + frame["c7"]
    frame["ContinuedClaims"] = frame["c8"] + frame["c12"]
    return frame.sort_values("rptdate").reset_index(drop=True)


def _download_bulk_claims(source: SourceConfig, temp_path: Path) -> dict[str, str | int]:
    response = requests.get(
        source.download_url,
        timeout=60,
        headers={"User-Agent": "UCLA housing research data pipeline yifan@g.ucla.edu"},
    )
    response.raise_for_status()
    frame = read_claims_csv(response.content)
    initial_dates, continued_dates = frame["rptdate"], frame["c2"]
    metadata: dict[str, str | int] = {
        "request_method": "GET",
        "request_query": source.download_url,
        "request_series_ids": "c3+c7|c8+c12",
        "request_scope": "provider_full_history_all_states_archive;California_clean_filter",
        "response_observation_count": len(frame) * 2,
        "response_start_date": min(initial_dates.min(), continued_dates.min()).date().isoformat(),
        "response_end_date": max(initial_dates.max(), continued_dates.max()).date().isoformat(),
        "initial_claims_last_week": initial_dates.max().date().isoformat(),
        "continued_claims_last_week": continued_dates.max().date().isoformat(),
        "original_filename": "ar539.csv",
        "retrieval_method": "official_public_bulk_csv",
        "source_metadata_url": CLAIMS_DATA_MAP,
    }
    last_modified = response.headers.get("Last-Modified")
    if last_modified:
        modified = parsedate_to_datetime(last_modified).isoformat()
        metadata.update({
            "provider_last_updated": modified,
            "source_last_updated": modified,
            "provider_last_updated_status": "HTTP_Last_Modified_file_timestamp_not_release_date",
        })
    with temp_path.open("xb") as stream:
        stream.write(response.content)
    return metadata


def download_claims(
    source: SourceConfig,
    temp_path: Path,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, str | int]:
    """Use the current bulk file or legacy export; archiving belongs to the caller.

    Bulk acquisition always retrieves the unchanged national full-history file,
    including revisions; requested dates never truncate that raw payload. The
    cleaner selects California. Legacy XML snapshots remain readable and its
    old POST endpoint is retained for explicit reproduction, not silent fallback.
    """
    if source.source_id != SOURCE_ID:
        raise ValueError(f"Unsupported claims source: {source.source_id}")
    if source.download_url == CLAIMS_CSV_ENDPOINT:
        return _download_bulk_claims(source, temp_path)
    if source.download_url != CLAIMS_ENDPOINT:
        raise ValueError("DOL claims endpoint does not match a verified public download")
    first_year = pd.Timestamp(start_date or source.selected_start_date or "1967-01-01").year
    last_year = pd.Timestamp(end_date or datetime.now(timezone.utc).date()).year
    if first_year > last_year:
        raise ValueError("DOL claims start year exceeds end year")
    payload = {
        "level": "state",
        "strtdate": str(first_year),
        "enddate": str(last_year),
        "filetype": "xml",
        "states[]": "CA",
    }
    response = requests.post(
        source.download_url,
        data=payload,
        timeout=60,
        headers={"User-Agent": "UCLA housing research data pipeline yifan@g.ucla.edu"},
    )
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError("DOL export did not return valid XML") from exc
    weeks = root.findall("week")
    if root.tag != "r539cyState" or not weeks:
        raise ValueError("DOL export did not return a nonempty state UI 539 report")
    if any(week.findtext("stateName", "").strip() != "California" for week in weeks):
        raise ValueError("DOL export contains a geography other than California")
    initial_dates = pd.to_datetime(
        [week.findtext("weekEnded") for week in weeks], format="%m/%d/%Y", errors="raise"
    )
    continued_dates = pd.to_datetime(
        [week.findtext("ReflectingWeekEnded") for week in weeks],
        format="%m/%d/%Y", errors="raise",
    )
    if initial_dates.hasnans or continued_dates.hasnans:
        raise ValueError("DOL export is missing a required week date")
    if initial_dates.duplicated().any() or continued_dates.duplicated().any():
        raise ValueError("DOL export contains duplicate California week dates")
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^";]+)', disposition, flags=re.I)
    metadata: dict[str, str | int] = {
        "request_method": "POST",
        "request_query": json.dumps(payload, sort_keys=True),
        "request_start_year": str(first_year),
        "request_end_year": str(last_year),
        "request_series_ids": "InitialClaims|ContinuedClaims",
        "response_observation_count": len(weeks) * 2,
        "response_start_date": min(initial_dates.min(), continued_dates.min()).date().isoformat(),
        "response_end_date": max(initial_dates.max(), continued_dates.max()).date().isoformat(),
        "initial_claims_last_week": initial_dates.max().date().isoformat(),
        "continued_claims_last_week": continued_dates.max().date().isoformat(),
        "provider_report_run_date": root.attrib.get("rundate", ""),
        "retrieval_method": "official_public_form_xml_export",
    }
    if match:
        metadata["original_filename"] = match.group(1).strip()
    # The parent downloader uses a unique scratch file and archives these exact
    # bytes by checksum. Refuse even a scratch-file overwrite here.
    with temp_path.open("xb") as stream:
        stream.write(response.content)
    return metadata
