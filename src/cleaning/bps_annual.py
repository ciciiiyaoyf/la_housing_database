"""Parser for final-annual Census BPS county ASCII files.

Annual releases remain annual observations.  This module never converts them
to months and never combines them with monthly current/YTD BPS archives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.cleaning.census_bps import BPSFormatError, COUNTY_COLUMNS
from src.cleaning.common import finalize, metadata_fields
from src.ingestion.bps_annual import SOURCE_ID

ANNUAL_FORMAT_VERSION = "county_annual_ascii_30col"


def _record_year(record: dict[str, str], path: Path) -> int:
    candidates = [
        record.get("survey_year"),
        record.get("archive_period"),
        record.get("original_filename"),
        record.get("download_url"),
        path.name,
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if re.fullmatch(r"(?:19|20)\d{2}", text):
            return int(text)
        match = re.search(r"co((?:19|20)\d{2})a\.txt", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    raise BPSFormatError(f"{path.name}: annual survey year is absent from metadata")


def _survey_token_matches_year(token: str, year: int) -> bool:
    token = str(token).strip()
    return token == str(year) or token == f"{year % 100:02d}99"


def _read_annual_ascii(
    path: Path, record: dict[str, str]
) -> tuple[pd.DataFrame, int]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    nonempty = [line for line in lines if line.strip()]
    if len(nonempty) < 3:
        raise BPSFormatError(f"{path.name}: expected two header rows and data")
    first = [cell.strip() for cell in nonempty[0].split(",")]
    second = [cell.strip() for cell in nonempty[1].split(",")]
    if first[0] != "Survey" or second[0] != "Date":
        raise BPSFormatError(
            f"{path.name}: unrecognized two-row annual BPS header signature"
        )
    source = pd.read_csv(
        path,
        skiprows=2,
        header=None,
        dtype=str,
        skipinitialspace=True,
    )
    if source.shape[1] != len(COUNTY_COLUMNS):
        raise BPSFormatError(
            f"{path.name}: annual county format has {source.shape[1]} columns; "
            f"documented {ANNUAL_FORMAT_VERSION} requires {len(COUNTY_COLUMNS)}"
        )
    source.columns = COUNTY_COLUMNS
    year = _record_year(record, path)
    valid_year = source["survey_date"].map(
        lambda value: _survey_token_matches_year(value, year)
    )
    if not valid_year.all():
        invalid = sorted(source.loc[~valid_year, "survey_date"].astype(str).unique())
        raise BPSFormatError(
            f"{path.name}: survey-date values {invalid} do not identify final "
            f"annual survey year {year}"
        )
    return source, year


def parse_bps_annual_county(
    path: Path,
    record: dict[str, str],
    *,
    state_fips: str = "06",
    county_fips: str | None = "06037",
) -> pd.DataFrame:
    """Parse annual one-unit permits for LA County or all counties in a state.

    Set ``county_fips=None`` to retain every county in ``state_fips``.  The
    function never creates a state aggregate from county rows.
    """
    path = Path(path)
    source, survey_year = _read_annual_ascii(path, record)
    state_fips = str(state_fips).zfill(2)
    source["state_fips"] = source["state_fips"].astype(str).str.strip().str.zfill(2)
    source["county_fips"] = (
        source["county_fips"].astype(str).str.strip().str.zfill(3)
    )
    source = source.loc[source["state_fips"].eq(state_fips)].copy()
    if county_fips is not None:
        requested = str(county_fips).zfill(5)
        if requested[:2] != state_fips:
            raise ValueError(
                f"county_fips {requested} is outside requested state {state_fips}"
            )
        source = source.loc[source["county_fips"].eq(requested[-3:])].copy()
    if source.empty:
        target = state_fips if county_fips is None else str(county_fips).zfill(5)
        raise ValueError(f"{path.name}: requested geography {target} is absent")

    for column in ("one_units", "one_rep_units"):
        source[column] = pd.to_numeric(source[column], errors="raise")
        if source[column].lt(0).any():
            raise BPSFormatError(f"{path.name}: {column} contains negative units")
    source["county_fips"] = source["state_fips"] + source["county_fips"]
    source["geo_id"] = "COUNTY_" + source["county_fips"]

    annual_end = pd.Timestamp(year=survey_year, month=12, day=31)
    annual_start = pd.Timestamp(year=survey_year, month=1, day=1)
    result = source[["geo_id", "state_fips", "county_fips"]].copy()
    result["observation_date"] = annual_end
    result["source_observation_date"] = annual_end
    result["period_start"] = annual_start
    result["period_end"] = annual_end
    result["variable_id"] = "permits_1_unit_annual_final"
    result["value"] = source["one_units"].to_numpy()
    result["reported_value"] = source["one_rep_units"].to_numpy()
    result["unit"] = "units"
    result["provider"] = "U.S. Census Bureau"
    result["native_frequency"] = "annual"
    result["final_frequency"] = "annual"
    for key, value in metadata_fields(record).items():
        result[key] = value
    result["source_id"] = record.get("source_id", SOURCE_ID)
    if result["source_id"].ne(SOURCE_ID).any():
        raise ValueError(
            "Annual BPS cleaner requires the dedicated annual-final source_id"
        )
    result["transformation_id"] = (
        "census_bps_county_annual_final_30col_permits_1_unit_v1"
    )
    result["is_imputed"] = result["value"].ne(result["reported_value"])
    result["is_interpolated"] = False
    result["is_seasonally_adjusted"] = False
    universe_flag = (
        "bps_2022_plus_full_universe"
        if survey_year >= 2022
        else "bps_pre_2022_sample_tally"
    )
    result["quality_flag"] = result["is_imputed"].map(
        lambda imputed: (
            "bps_annual_final|"
            + ("includes_imputation" if imputed else "reported_equals_total")
            + f"|{universe_flag}"
        )
    )
    return result


def clean_bps_annual(
    path: Path,
    record: dict[str, str],
    output_path: Path,
    *,
    state_fips: str = "06",
    county_fips: str | None = "06037",
) -> pd.DataFrame:
    """Clean one final-annual county file to the shared observation contract."""
    frame = parse_bps_annual_county(
        path,
        record,
        state_fips=state_fips,
        county_fips=county_fips,
    )
    return finalize(frame, output_path)


def clean_bps_annual_archive(
    archive_files: list[tuple[Path, dict[str, str]]],
    output_path: Path,
    *,
    state_fips: str = "06",
    county_fips: str | None = "06037",
) -> pd.DataFrame:
    """Combine immutable annual files without changing their annual frequency."""

    if not archive_files:
        raise ValueError("BPS annual archive cannot be empty")
    frames = [
        parse_bps_annual_county(
            path,
            record,
            state_fips=state_fips,
            county_fips=county_fips,
        )
        for path, record in archive_files
    ]
    combined = pd.concat(frames, ignore_index=True)
    key = ["observation_date", "geo_id", "variable_id"]
    duplicates = combined.loc[combined.duplicated(key, keep=False), key]
    if not duplicates.empty:
        raise BPSFormatError(
            "BPS annual archive has more than one selected file for a "
            f"geography-year-variable key: {duplicates.head().to_dict('records')}"
        )
    return finalize(combined, output_path)
