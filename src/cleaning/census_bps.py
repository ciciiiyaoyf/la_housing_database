"""Explicit multi-file parsing for official Census BPS monthly archives."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields

COUNTY_COLUMNS = [
    "survey_date",
    "state_fips",
    "county_fips",
    "region_code",
    "division_code",
    "name",
    "one_bldgs",
    "one_units",
    "one_value",
    "two_bldgs",
    "two_units",
    "two_value",
    "three_four_bldgs",
    "three_four_units",
    "three_four_value",
    "five_plus_bldgs",
    "five_plus_units",
    "five_plus_value",
    "one_rep_bldgs",
    "one_rep_units",
    "one_rep_value",
    "two_rep_bldgs",
    "two_rep_units",
    "two_rep_value",
    "three_four_rep_bldgs",
    "three_four_rep_units",
    "three_four_rep_value",
    "five_plus_rep_bldgs",
    "five_plus_rep_units",
    "five_plus_rep_value",
]

STATE_COLUMNS = [
    "survey_date",
    "state_fips",
    "region_code",
    "division_code",
    "name",
    *COUNTY_COLUMNS[6:],
]

FORMAT_BY_GEOGRAPHY = {
    "county": (COUNTY_COLUMNS, "county_ascii_2000_30col"),
    "state": (STATE_COLUMNS, "state_ascii_2000_29col"),
}


class BPSFormatError(ValueError):
    """Raised when an archive file does not match a documented schema."""


def _read_ascii(path: Path, geography: str) -> tuple[pd.DataFrame, str]:
    if geography not in FORMAT_BY_GEOGRAPHY:
        raise ValueError(f"Unsupported BPS geography: {geography}")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 3:
        raise BPSFormatError(f"{path.name}: expected two header rows and data")
    if "Survey" not in lines[0] or "Date" not in lines[1]:
        raise BPSFormatError(
            f"{path.name}: unrecognized two-row BPS header signature"
        )
    columns, format_version = FORMAT_BY_GEOGRAPHY[geography]
    frame = pd.read_csv(path, skiprows=2, header=None, dtype=str)
    if frame.shape[1] != len(columns):
        raise BPSFormatError(
            f"{path.name}: {geography} format has {frame.shape[1]} columns; "
            f"documented {format_version} requires {len(columns)}"
        )
    frame.columns = columns
    if geography == "state":
        frame["county_fips"] = None
    survey_dates = frame["survey_date"].astype(str)
    if not survey_dates.str.fullmatch(r"\d{6}").all():
        raise BPSFormatError(f"{path.name}: invalid YYYYMM survey-date field")
    return frame, format_version


def _quality_flag(is_imputed: bool, observation_date: pd.Timestamp) -> str:
    flags = []
    if is_imputed:
        flags.append("includes_imputation")
    flags.append(
        "bps_2022_plus_full_universe"
        if observation_date >= pd.Timestamp("2022-01-01")
        else "bps_pre_2022_sample_tally"
    )
    return "|".join(flags)


def _transform_file(
    path: Path,
    record: dict[str, str],
    source_id: str,
) -> tuple[pd.DataFrame, str]:
    geography = "county" if source_id.endswith("county") else "state"
    source, format_version = _read_ascii(path, geography)
    source["state_fips"] = source["state_fips"].astype(str).str.zfill(2)
    if geography == "county":
        source["county_fips"] = source["county_fips"].astype(str).str.zfill(3)
        source = source.loc[
            source["state_fips"].eq("06") & source["county_fips"].eq("037")
        ].copy()
        source["geo_id"] = "COUNTY_06037"
        source["county_fips"] = "06037"
    else:
        source = source.loc[source["state_fips"].eq("06")].copy()
        source["geo_id"] = "STATE_06"
        source["county_fips"] = None
    if source.empty:
        raise ValueError(f"{path.name}: target California geography is absent")
    numeric = [
        "one_units",
        "two_units",
        "three_four_units",
        "five_plus_units",
        "one_rep_units",
        "two_rep_units",
        "three_four_rep_units",
        "five_plus_rep_units",
    ]
    for column in numeric:
        source[column] = pd.to_numeric(source[column], errors="raise")
    values = {
        "permits_1_unit": source["one_units"],
        "permits_2_to_4_units": source["two_units"]
        + source["three_four_units"],
        "permits_5_plus_units": source["five_plus_units"],
    }
    values["permits_total"] = (
        values["permits_1_unit"]
        + values["permits_2_to_4_units"]
        + values["permits_5_plus_units"]
    )
    reported = {
        "permits_1_unit": source["one_rep_units"],
        "permits_2_to_4_units": source["two_rep_units"]
        + source["three_four_rep_units"],
        "permits_5_plus_units": source["five_plus_rep_units"],
    }
    reported["permits_total"] = (
        reported["permits_1_unit"]
        + reported["permits_2_to_4_units"]
        + reported["permits_5_plus_units"]
    )
    frames: list[pd.DataFrame] = []
    for variable_id, series in values.items():
        part = source[
            ["survey_date", "geo_id", "state_fips", "county_fips"]
        ].copy()
        part["variable_id"] = variable_id
        part["value"] = series
        part["reported_value"] = reported[variable_id]
        part["is_imputed"] = part["value"].ne(part["reported_value"])
        frames.append(part)
    long = pd.concat(frames, ignore_index=True)
    source_date = pd.to_datetime(
        long["survey_date"].astype(str) + "01", format="%Y%m%d"
    )
    long["source_observation_date"] = source_date
    long["observation_date"] = source_date.dt.to_period("M").dt.to_timestamp("M")
    long["period_start"] = source_date
    long["period_end"] = long["observation_date"]
    long["unit"] = "units"
    long["provider"] = "U.S. Census Bureau"
    long["native_frequency"] = "monthly"
    long["final_frequency"] = "monthly"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = (
        f"{source_id}_{format_version}_structure_units_v2"
    )
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = False
    long["quality_flag"] = [
        _quality_flag(bool(imputed), date)
        for imputed, date in zip(
            long["is_imputed"], long["observation_date"], strict=True
        )
    ]
    return long, format_version


def clean_bps(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    """Clean one archive file; retained as a focused unit-test entry point."""
    frame, _ = _transform_file(path, record, source_id)
    return finalize(frame, output_path)


def clean_bps_archive(
    files: list[tuple[Path, dict[str, str]]],
    source_id: str,
    output_path: Path,
    coverage_path: Path,
) -> pd.DataFrame:
    """Clean all selected archive months and report every parse outcome."""
    cleaned: list[pd.DataFrame] = []
    parse_results: list[dict] = []
    for path, record in files:
        archive_period = record.get("archive_period")
        try:
            frame, format_version = _transform_file(path, record, source_id)
            cleaned.append(frame)
            parse_results.append(
                {
                    "source_id": source_id,
                    "archive_period": archive_period,
                    "download_url": record.get("download_url"),
                    "source_filename": record.get("source_filename"),
                    "checksum_sha256": record.get("checksum_sha256"),
                    "retrieved_at": record.get("retrieved_at"),
                    "format_version": format_version,
                    "parse_status": "loaded",
                    "observation_rows": len(frame),
                    "detail": None,
                }
            )
        except Exception as exc:
            parse_results.append(
                {
                    "source_id": source_id,
                    "archive_period": archive_period,
                    "download_url": record.get("download_url"),
                    "source_filename": record.get("source_filename"),
                    "checksum_sha256": record.get("checksum_sha256"),
                    "retrieved_at": record.get("retrieved_at"),
                    "format_version": record.get("archive_format_version"),
                    "parse_status": "parse_failed",
                    "observation_rows": 0,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
    report = pd.DataFrame(parse_results)
    if coverage_path.exists():
        prior = pd.read_csv(coverage_path, dtype=str)
        prior = prior.loc[~prior["source_id"].eq(source_id)]
        report = pd.concat([prior, report], ignore_index=True, sort=False)
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    report.sort_values(["source_id", "archive_period"]).to_csv(
        coverage_path, index=False
    )
    download_path = coverage_path.with_name("bps_archive_download.csv")
    if download_path.exists():
        downloads = pd.read_csv(download_path, dtype=str)
        parse_columns = [
            "source_id",
            "archive_period",
            "format_version",
            "parse_status",
            "observation_rows",
            "detail",
        ]
        parse_report = report.reindex(columns=parse_columns).rename(
            columns={"detail": "parse_detail"}
        )
        period_report = downloads.merge(
            parse_report,
            on=["source_id", "archive_period"],
            how="left",
        )
        period_report["load_status"] = period_report["parse_status"].fillna(
            period_report["download_status"].map(
                {
                    "not_published": "not_published",
                    "failed": "download_failed",
                    "rejected_response": "rejected_response",
                    "downloaded": "downloaded_not_parsed",
                    "reused": "reused_not_parsed",
                }
            )
        )
        period_report.sort_values(["source_id", "archive_period"]).to_csv(
            coverage_path.with_name("bps_archive_period_report.csv"),
            index=False,
        )
    if not cleaned:
        raise RuntimeError(f"No BPS archive periods parsed for {source_id}")
    combined = pd.concat(cleaned, ignore_index=True)
    duplicate_key = ["observation_date", "geo_id", "variable_id"]
    duplicates = combined.duplicated(duplicate_key, keep=False)
    if duplicates.any():
        periods = sorted(
            combined.loc[duplicates, "observation_date"]
            .dt.strftime("%Y-%m")
            .unique()
        )
        raise ValueError(
            f"Duplicate BPS geography-month-variable keys in periods: {periods}"
        )
    return finalize(combined, output_path)
