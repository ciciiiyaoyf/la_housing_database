"""Zillow wide CSV cleaning."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields, month_end

DATE_COLUMN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _la_rows(path: Path, geography: str) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, chunksize=2500, low_memory=False):
        if geography == "county":
            name = chunk.get("RegionName", pd.Series(index=chunk.index, dtype=str))
            state = chunk.get(
                "StateName",
                chunk.get("State", pd.Series(index=chunk.index, dtype=str)),
            )
            mask = name.astype(str).str.strip().eq("Los Angeles County")
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
            selected.append(chunk.loc[mask].copy())
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
        raise ValueError(f"No Los Angeles County rows found in {path.name}")
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
        long["geo_id"] = "COUNTY_06037"
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
    long["transformation_id"] = f"{source_id}_wide_to_long_v1"
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = variable_id in {
        "zhvi",
        "zori_smoothed_sa",
    }
    long["quality_flag"] = None
    return finalize(long, output_path)
