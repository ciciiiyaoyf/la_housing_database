"""BLS public flat-file cleaners for CES and CPI controls."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


SERIES = {
    "bls_ces_la_metro": {
        "series_id": "SMS06310800000000001",
        "variable_id": "ces_total_nonfarm_employment_sa",
        "geo_id": "METRO_31080",
        "unit": "thousands_of_jobs",
        "seasonally_adjusted": True,
        "transformation_id": "bls_ces_la_metro_flatfile_month_end_v1",
    },
    "bls_cpi_la_metro": {
        "series_id": "CUURS49ASA0",
        "variable_id": "cpi_u_all_items_la_nsa",
        "geo_id": "METRO_31080",
        "unit": "index_1982_84_100",
        "seasonally_adjusted": False,
        "transformation_id": "bls_cpi_la_metro_flatfile_month_end_v1",
    },
}


def clean_bls_flat(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    spec = SERIES[source_id]
    source = pd.read_csv(path, sep="\t", dtype=str)
    source.columns = [column.strip() for column in source.columns]
    for column in source.columns:
        source[column] = source[column].astype(str).str.strip()
    source = source.loc[
        source["series_id"].eq(spec["series_id"])
        & source["period"].astype(str).str.startswith("M")
        & ~source["period"].eq("M13")
    ].copy()
    month = pd.to_numeric(source["period"].str.removeprefix("M"), errors="coerce")
    year = pd.to_numeric(source["year"], errors="coerce")
    source["source_observation_date"] = pd.to_datetime(
        {
            "year": year,
            "month": month,
            "day": 1,
        },
        errors="coerce",
    )
    source["observation_date"] = (
        source["source_observation_date"].dt.to_period("M").dt.to_timestamp("M")
    )
    source["period_start"] = source["source_observation_date"]
    source["period_end"] = source["observation_date"]
    source["geo_id"] = spec["geo_id"]
    source["state_fips"] = None
    source["county_fips"] = None
    source["variable_id"] = spec["variable_id"]
    source["value"] = pd.to_numeric(source["value"], errors="coerce")
    source["reported_value"] = source["value"]
    source["unit"] = spec["unit"]
    source["provider"] = "U.S. Bureau of Labor Statistics"
    source["native_frequency"] = "monthly"
    source["final_frequency"] = "monthly"
    for key, value in metadata_fields(record).items():
        source[key] = value
    source["transformation_id"] = spec["transformation_id"]
    source["is_imputed"] = False
    source["is_interpolated"] = False
    source["is_seasonally_adjusted"] = spec["seasonally_adjusted"]
    source["quality_flag"] = None
    return finalize(source, output_path)
