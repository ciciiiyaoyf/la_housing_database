"""Zillow wide CSV cleaning."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields, month_end
from src.geography.comparable_counties import (
    COMPARABLE_COUNTY_FIPS,
    comparable_county_fips,
)

DATE_COLUMN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _la_rows(path: Path, geography: str) -> pd.DataFrame:
    """Select eight comparison counties or LA postal ZIPs from a national file."""

    selected: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, chunksize=2500, low_memory=False):
        if geography == "county":
            name = chunk.get("RegionName", pd.Series(index=chunk.index, dtype=str))
            state = chunk.get(
                "StateName",
                chunk.get("State", pd.Series(index=chunk.index, dtype=str)),
            )
            county_fips = name.map(comparable_county_fips)
            mask = county_fips.notna()
            mask &= state.astype(str).str.strip().isin({"CA", "California"})
        else:
            county = chunk.get(
                "CountyName", pd.Series(index=chunk.index, dtype=str)
            )
            state = chunk.get(
                "StateName",
                chunk.get("State", pd.Series(index=chunk.index, dtype=str)),
            )
            mask = county.astype(str).str.strip().eq("Los Angeles County")
            mask &= state.astype(str).str.strip().isin({"CA", "California"})
        if mask.any():
            piece = chunk.loc[mask].copy()
            if geography == "county":
                piece["_county_fips"] = county_fips.loc[mask].astype("string")
            selected.append(piece)
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()


def clean_zillow(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    geography = "zip" if "_zip_" in source_id else "county"
    if "_zori_" in source_id:
        if not source_id.endswith("_zori_smoothed_sa"):
            raise ValueError(f"Unsupported inactive Zillow ZORI source: {source_id}")
        variable_id = "zori_smoothed_sa"
    else:
        variable_id = "zhvi"
    source = _la_rows(path, geography)
    if source.empty:
        scope = "configured comparison counties" if geography == "county" else "Los Angeles County ZIPs"
        raise ValueError(f"No {scope} found in {path.name}")
    if geography == "county":
        observed_counties = set(source["_county_fips"].dropna().astype(str))
        missing_counties = sorted(set(COMPARABLE_COUNTY_FIPS) - observed_counties)
        if missing_counties:
            raise ValueError(
                "Zillow source is missing configured comparison counties: "
                f"{missing_counties}"
            )
        if source["_county_fips"].duplicated().any():
            raise ValueError("Zillow source has duplicate comparison-county rows")
    date_columns = [column for column in source.columns if DATE_COLUMN.match(str(column))]
    if not date_columns:
        raise ValueError(f"Zillow schema change: no ISO date columns in {path.name}")
    id_columns = [column for column in source.columns if column not in date_columns]
    long = source.melt(
        id_vars=id_columns,
        value_vars=date_columns,
        var_name="source_observation_date",
        value_name="value",
    )
    long["source_observation_date"] = pd.to_datetime(long["source_observation_date"])
    long["observation_date"] = month_end(long["source_observation_date"])
    long["period_start"] = long["observation_date"].dt.to_period("M").dt.start_time
    long["period_end"] = long["observation_date"]
    if geography == "county":
        long["county_fips"] = long["_county_fips"].astype("string")
        long["state_fips"] = "06"
        long["geo_id"] = "COUNTY_" + long["county_fips"]
    else:
        postal_zip = (
            long["RegionName"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(5)
        )
        long["geo_id"] = "ZIP_" + postal_zip
    long["variable_id"] = variable_id
    long["unit"] = "USD" if variable_id == "zhvi" else "USD/month"
    long["provider"] = "Zillow Research"
    long["native_frequency"] = "monthly"
    long["final_frequency"] = "monthly"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = (
        f"{source_id}_comparable_county_wide_to_long_v1"
        if geography == "county"
        else f"{source_id}_wide_to_long_v1"
    )
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = variable_id in {
        "zhvi",
        "zori_smoothed_sa",
    }
    long["quality_flag"] = None
    return finalize(long, output_path)
