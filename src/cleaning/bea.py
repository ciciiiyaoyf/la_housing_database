"""BEA regional personal income cleaners."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


def _read_zip_csv(path: Path, prefix: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        name = next(item for item in archive.namelist() if item.startswith(prefix))
        return pd.read_csv(archive.open(name), dtype=str, encoding="latin1")


def _melt_quarters(frame: pd.DataFrame, line_map: dict[str, tuple[str, str]]) -> pd.DataFrame:
    value_columns = [column for column in frame.columns if ":Q" in column]
    rows = []
    for line_code, (variable_id, unit) in line_map.items():
        part = frame.loc[frame["LineCode"].astype(str).eq(line_code)].copy()
        melted = part.melt(
            id_vars=["GeoFIPS", "GeoName", "LineCode"],
            value_vars=value_columns,
            var_name="period",
            value_name="value",
        )
        melted["variable_id"] = variable_id
        melted["unit"] = unit
        rows.append(melted)
    return pd.concat(rows, ignore_index=True)


def _melt_years(frame: pd.DataFrame, line_map: dict[str, tuple[str, str]]) -> pd.DataFrame:
    value_columns = [column for column in frame.columns if column.isdigit()]
    rows = []
    for line_code, (variable_id, unit) in line_map.items():
        part = frame.loc[frame["LineCode"].astype(str).eq(line_code)].copy()
        melted = part.melt(
            id_vars=["GeoFIPS", "GeoName", "LineCode"],
            value_vars=value_columns,
            var_name="period",
            value_name="value",
        )
        melted["variable_id"] = variable_id
        melted["unit"] = unit
        rows.append(melted)
    return pd.concat(rows, ignore_index=True)


def clean_bea(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    if source_id == "bea_state_personal_income":
        source = _read_zip_csv(path, "SQINC1__ALL_AREAS")
        source = source.loc[source["GeoFIPS"].astype(str).str.contains("06000")]
        long = _melt_quarters(
            source,
            {
                "1": ("bea_ca_personal_income_q_sa", "millions_of_current_dollars_saar"),
                "3": (
                    "bea_ca_per_capita_personal_income_q_sa",
                    "current_dollars_per_person_saar",
                ),
            },
        )
        periods = pd.PeriodIndex(long["period"].str.replace(":", ""), freq="Q")
        long["observation_date"] = periods.to_timestamp("Q")
        long["source_observation_date"] = long["observation_date"]
        long["period_start"] = periods.to_timestamp("Q").to_period("Q").start_time
        long["period_end"] = long["observation_date"]
        long["geo_id"] = "STATE_06"
        long["state_fips"] = "06"
        long["county_fips"] = None
        long["native_frequency"] = "quarterly"
        long["final_frequency"] = "quarterly"
        long["is_seasonally_adjusted"] = True
        transformation = "bea_state_personal_income_official_bulk_v1"
    elif source_id == "bea_county_personal_income":
        source = _read_zip_csv(path, "CAINC1__ALL_AREAS")
        source = source.loc[source["GeoFIPS"].astype(str).str.contains("06037")]
        long = _melt_years(
            source,
            {
                "1": (
                    "bea_la_county_personal_income_annual",
                    "thousands_of_current_dollars",
                ),
                "3": (
                    "bea_la_county_per_capita_personal_income_annual",
                    "current_dollars_per_person",
                ),
            },
        )
        year = pd.to_numeric(long["period"], errors="coerce").astype("Int64")
        long["observation_date"] = pd.to_datetime(
            year.astype(str) + "-12-31", errors="coerce"
        )
        long["source_observation_date"] = long["observation_date"]
        long["period_start"] = pd.to_datetime(
            year.astype(str) + "-01-01", errors="coerce"
        )
        long["period_end"] = long["observation_date"]
        long["geo_id"] = "COUNTY_06037"
        long["state_fips"] = "06"
        long["county_fips"] = "06037"
        long["native_frequency"] = "annual"
        long["final_frequency"] = "annual"
        long["is_seasonally_adjusted"] = False
        transformation = "bea_county_personal_income_official_bulk_v1"
    else:
        raise ValueError(f"Unsupported BEA source: {source_id}")
    long["value"] = pd.to_numeric(
        long["value"].replace({"(NA)": None, "(D)": None}), errors="coerce"
    )
    long["reported_value"] = long["value"]
    long["provider"] = "U.S. Bureau of Economic Analysis"
    long["transformation_id"] = transformation
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["quality_flag"] = "official_bulk_regional"
    for key, value in metadata_fields(record).items():
        long[key] = value
    return finalize(long, output_path)
