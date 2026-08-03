"""CDC WONDER vital-statistics cleaners."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields

LA_COUNTY_FIPS = "06037"
LA_COUNTY_NAME = "Los Angeles County"


def _read_wonder_csv(path: Path) -> pd.DataFrame:
    """Read the data portion of a CDC WONDER CSV export.

    WONDER appends citation/query/caveat text after a row beginning with
    "---".  Keep the raw file immutable and strip only that footer for the
    normalized clean table.
    """

    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row and str(row[0]).strip() == "---":
                break
            if row:
                rows.append(row)
    if not rows:
        raise ValueError(f"CDC WONDER CSV has no data rows: {path.name}")
    header, *data = rows
    return pd.DataFrame(data, columns=header)


def _extract_la_county(frame: pd.DataFrame, path: Path) -> pd.DataFrame:
    result = frame.copy()
    if "County Code" in result.columns:
        result["County Code"] = result["County Code"].astype(str).str.zfill(5)
        result = result.loc[result["County Code"].eq(LA_COUNTY_FIPS)].copy()
    elif "County" in result.columns:
        result = result.loc[
            result["County"].astype(str).str.contains(LA_COUNTY_NAME, na=False)
        ].copy()
    # LA-only WONDER exports can omit County/County Code columns; those are OK.
    if result.empty:
        raise ValueError(f"No Los Angeles County rows found in {path.name}")
    return result


def clean_cdc_wonder(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    source = _extract_la_county(_read_wonder_csv(path), path)
    if "Year" not in source.columns:
        raise ValueError(f"CDC WONDER CSV missing Year column: {path.name}")

    if source_id == "cdc_wonder_natality_county":
        value_column = "Births"
        variable_id = "cdc_wonder_live_births_annual"
        unit = "live_births"
        transformation_id = "cdc_wonder_natality_year_county_v1"
        provider = "CDC WONDER / National Center for Health Statistics"
        quality_flag = "annual_county_births"
    elif source_id in {
        "cdc_wonder_ucd_1999_2020_la_county",
        "cdc_wonder_ucd_2018_2024_county",
    }:
        value_column = "Deaths"
        variable_id = "cdc_wonder_deaths_annual"
        unit = "deaths"
        transformation_id = "cdc_wonder_ucd_year_county_v1"
        provider = "CDC WONDER / National Center for Health Statistics"
        quality_flag = "annual_county_deaths"
    else:
        raise ValueError(f"Unsupported CDC WONDER source: {source_id}")

    if value_column not in source.columns:
        raise ValueError(
            f"CDC WONDER CSV missing {value_column} column: {path.name}"
        )
    source["year"] = pd.to_numeric(source["Year"], errors="coerce").astype("Int64")
    source["value"] = pd.to_numeric(
        source[value_column].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    source = source.dropna(subset=["year", "value"]).copy()
    if source.empty:
        raise ValueError(f"CDC WONDER CSV has no numeric observations: {path.name}")
    source["year"] = source["year"].astype(int)
    if source_id == "cdc_wonder_ucd_1999_2020_la_county":
        source = source.loc[source["year"].between(2007, 2017)].copy()
    elif source_id == "cdc_wonder_ucd_2018_2024_county":
        source = source.loc[source["year"].between(2018, 2024)].copy()
    elif source_id == "cdc_wonder_natality_county":
        source = source.loc[source["year"].between(2007, 2024)].copy()
    if source.empty:
        raise ValueError(f"CDC WONDER source has no rows after range filter: {path.name}")

    long_rows: list[dict[str, object]] = []
    base_metadata = metadata_fields(record)
    for _, row in source.iterrows():
        year = int(row["year"])
        observation_date = pd.Timestamp(year=year, month=12, day=31)
        long_rows.append(
            {
                "observation_date": observation_date,
                "source_observation_date": observation_date,
                "period_start": pd.Timestamp(year=year, month=1, day=1),
                "period_end": observation_date,
                "geo_id": "COUNTY_06037",
                "state_fips": "06",
                "county_fips": LA_COUNTY_FIPS,
                "variable_id": variable_id,
                "value": row["value"],
                "reported_value": row["value"],
                "unit": unit,
                "provider": provider,
                "native_frequency": "annual",
                "final_frequency": "annual",
                "transformation_id": transformation_id,
                "is_imputed": False,
                "is_interpolated": False,
                "is_seasonally_adjusted": False,
                "quality_flag": quality_flag,
                **base_metadata,
            }
        )
    return finalize(pd.DataFrame(long_rows), output_path)
