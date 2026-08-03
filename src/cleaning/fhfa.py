"""FHFA HPI source cleaning."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


def _quarter_dates(year: pd.Series, quarter: pd.Series) -> pd.Series:
    periods = year.astype(str) + "Q" + quarter.astype(str)
    return pd.PeriodIndex(periods, freq="Q").to_timestamp("Q")


def _emit(
    source: pd.DataFrame,
    value_column: str,
    variable_id: str,
    geo_id: str,
    unit: str,
    record: dict[str, str],
    transformation_id: str,
    seasonal: bool,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "observation_date": _quarter_dates(source["yr"], source["qtr"]),
            "source_observation_date": _quarter_dates(source["yr"], source["qtr"]),
            "value": pd.to_numeric(source[value_column], errors="coerce"),
            "reported_value": pd.to_numeric(source[value_column], errors="coerce"),
            "geo_id": geo_id,
            "variable_id": variable_id,
            "unit": unit,
            "provider": "Federal Housing Finance Agency",
            "native_frequency": "quarterly",
            "final_frequency": "quarterly",
            "transformation_id": transformation_id,
            "is_imputed": False,
            "is_interpolated": False,
            "is_seasonally_adjusted": seasonal,
            "quality_flag": "purchase_only;repeat_sales;single_family",
        }
    )
    result["period_start"] = result["observation_date"].dt.to_period("Q").dt.start_time
    result["period_end"] = result["observation_date"]
    result["state_fips"] = "06" if geo_id == "STATE_06" else None
    result["county_fips"] = None
    for key, value in metadata_fields(record).items():
        result[key] = value
    return result


def clean_fhfa(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    if source_id == "fhfa_hpi_state_purchase_only":
        source = pd.read_csv(path, sep="\t", dtype=str)
        source = source.loc[source["state"].eq("CA")].copy()
        rows = [
            _emit(
                source,
                "index_nsa",
                "fhfa_hpi_ca_purchase_only_nsa",
                "STATE_06",
                "index",
                record,
                "fhfa_purchase_only_quarterly_v1",
                False,
            ),
            _emit(
                source,
                "index_sa",
                "fhfa_hpi_ca_purchase_only_sa",
                "STATE_06",
                "index",
                record,
                "fhfa_purchase_only_quarterly_v1",
                True,
            ),
        ]
        return finalize(pd.concat(rows, ignore_index=True), output_path)
    if source_id == "fhfa_hpi_metro_purchase_only":
        source = pd.read_csv(path, sep="\t", dtype=str)
        source = source.loc[source["cbsa"].astype(str).eq("31084")].copy()
        rows = [
            _emit(
                source,
                "index_nsa",
                "fhfa_hpi_la_metropolitan_division_purchase_only_nsa",
                "METRODIV_31084",
                "index",
                record,
                "fhfa_purchase_only_quarterly_v1",
                False,
            ),
            _emit(
                source,
                "index_sa",
                "fhfa_hpi_la_metropolitan_division_purchase_only_sa",
                "METRODIV_31084",
                "index",
                record,
                "fhfa_purchase_only_quarterly_v1",
                True,
            ),
        ]
        return finalize(pd.concat(rows, ignore_index=True), output_path)
    if source_id == "fhfa_hpi_county_annual":
        source = pd.read_excel(path, sheet_name=0, header=5, dtype=str)
        source = source.loc[source["FIPS code"].astype(str).str.zfill(5).eq("06037")]
        value = pd.to_numeric(source["HPI with 2000 base"], errors="coerce")
        year = pd.to_numeric(source["Year"], errors="coerce").astype("Int64")
        result = pd.DataFrame(
            {
                "observation_date": pd.to_datetime(
                    year.astype(str) + "-12-31", errors="coerce"
                ),
                "source_observation_date": pd.to_datetime(
                    year.astype(str) + "-12-31", errors="coerce"
                ),
                "period_start": pd.to_datetime(
                    year.astype(str) + "-01-01", errors="coerce"
                ),
                "period_end": pd.to_datetime(
                    year.astype(str) + "-12-31", errors="coerce"
                ),
                "geo_id": "COUNTY_06037",
                "state_fips": "06",
                "county_fips": "06037",
                "variable_id": "fhfa_hpi_la_county_all_transactions_nsa_annual",
                "value": value,
                "reported_value": value,
                "unit": "index_2000_100",
                "provider": "Federal Housing Finance Agency",
                "native_frequency": "annual",
                "final_frequency": "annual",
                "transformation_id": "fhfa_county_developmental_annual_v1",
                "is_imputed": False,
                "is_interpolated": False,
                "is_seasonally_adjusted": False,
                "quality_flag": "developmental;all_transactions;annual;nsa",
            }
        )
        for key, item in metadata_fields(record).items():
            result[key] = item
        return finalize(result, output_path)
    raise ValueError(f"Unsupported FHFA source: {source_id}")
