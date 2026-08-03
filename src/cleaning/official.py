"""Clean the added official FHFA, BLS, BEA, HVS, and ACS products."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


def _quarter_dates(year: pd.Series, quarter: pd.Series) -> tuple[pd.Series, pd.Series]:
    periods = pd.PeriodIndex(
        year.astype(int).astype(str) + "Q" + quarter.astype(int).astype(str),
        freq="Q",
    )
    return pd.Series(periods.start_time), pd.Series(periods.end_time.normalize())


def _apply_common(
    frame: pd.DataFrame,
    record: dict[str, str],
    *,
    provider: str,
    native_frequency: str,
    final_frequency: str,
    transformation_id: str,
    seasonally_adjusted: bool,
) -> pd.DataFrame:
    frame["provider"] = provider
    frame["native_frequency"] = native_frequency
    frame["final_frequency"] = final_frequency
    for key, value in metadata_fields(record).items():
        frame[key] = value
    frame["transformation_id"] = transformation_id
    frame["is_imputed"] = False
    frame["is_interpolated"] = False
    frame["is_seasonally_adjusted"] = seasonally_adjusted
    return frame


def clean_fhfa(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    """Preserve each FHFA product's native geography, frequency, and method."""
    rows: list[dict] = []
    if source_id == "fhfa_hpi_state_purchase_only":
        source = pd.read_csv(path, sep="\t", dtype={"state": str})
        source = source.loc[source["state"].eq("CA")].copy()
        geo_id = "STATE_06"
        variables = {
            "index_nsa": ("fhfa_hpi_ca_purchase_only_nsa", False),
            "index_sa": ("fhfa_hpi_ca_purchase_only_sa", True),
        }
    elif source_id == "fhfa_hpi_metro_purchase_only":
        source = pd.read_csv(path, sep="\t", dtype={"cbsa": str})
        source["cbsa"] = source["cbsa"].str.zfill(5)
        source = source.loc[source["cbsa"].eq("31084")].copy()
        geo_id = "METRODIV_31084"
        variables = {
            "index_nsa": (
                "fhfa_hpi_la_metropolitan_division_purchase_only_nsa",
                False,
            ),
            "index_sa": (
                "fhfa_hpi_la_metropolitan_division_purchase_only_sa",
                True,
            ),
        }
    elif source_id == "fhfa_hpi_county_annual":
        source = pd.read_excel(path, header=5, dtype={"FIPS code": str})
        source["FIPS code"] = (
            source["FIPS code"].str.replace(r"\.0$", "", regex=True).str.zfill(5)
        )
        source = source.loc[source["FIPS code"].eq("06037")].copy()
        source_dates = pd.to_datetime(source["Year"].astype(int).astype(str) + "-12-31")
        frame = pd.DataFrame(
            {
                "observation_date": source_dates,
                "source_observation_date": source_dates,
                "period_start": pd.to_datetime(
                    source["Year"].astype(int).astype(str) + "-01-01"
                ),
                "period_end": source_dates,
                "geo_id": "COUNTY_06037",
                "state_fips": "06",
                "county_fips": "06037",
                "variable_id": "fhfa_hpi_la_county_all_transactions_nsa_annual",
                "value": pd.to_numeric(
                    source["HPI with 2000 base"], errors="coerce"
                ),
                "reported_value": source["HPI with 2000 base"].astype(str),
                "unit": "index_2000_100",
                "quality_flag": (
                    "developmental;all_transactions;annual;nsa;"
                    + source["Annual Change (%)"].map(
                        lambda value: f"annual_change_pct={value}"
                    )
                ),
            }
        )
        frame = _apply_common(
            frame,
            record,
            provider="Federal Housing Finance Agency",
            native_frequency="annual",
            final_frequency="annual",
            transformation_id="fhfa_county_developmental_annual_v1",
            seasonally_adjusted=False,
        )
        return finalize(frame, output_path)
    else:
        raise ValueError(f"Unsupported FHFA source: {source_id}")

    if source.empty:
        raise ValueError(f"{source_id} contains no configured geography")
    starts, ends = _quarter_dates(source["yr"], source["qtr"])
    for source_column, (variable_id, is_sa) in variables.items():
        for position, (_, item) in enumerate(source.iterrows()):
            value = pd.to_numeric(item[source_column], errors="coerce")
            if pd.isna(value):
                continue
            rows.append(
                {
                    "observation_date": ends.iloc[position],
                    "source_observation_date": ends.iloc[position],
                    "period_start": starts.iloc[position],
                    "period_end": ends.iloc[position],
                    "geo_id": geo_id,
                    "state_fips": "06" if geo_id == "STATE_06" else None,
                    "county_fips": None,
                    "variable_id": variable_id,
                    "value": value,
                    "reported_value": str(item[source_column]),
                    "unit": "index",
                    "provider": "Federal Housing Finance Agency",
                    "native_frequency": "quarterly",
                    "final_frequency": "quarterly",
                    **metadata_fields(record),
                    "transformation_id": "fhfa_purchase_only_quarterly_v1",
                    "is_imputed": False,
                    "is_interpolated": False,
                    "is_seasonally_adjusted": is_sa,
                    "quality_flag": (
                        str(item.get("Warning"))
                        if pd.notna(item.get("Warning"))
                        else "purchase_only;repeat_sales;single_family"
                    ),
                }
            )
    return finalize(pd.DataFrame(rows), output_path)


BLS_ADDED_SERIES = {
    "bls_ces_la_metro": {
        "SMS06310800000000001": (
            "ces_total_nonfarm_employment_sa",
            "thousands_of_jobs",
            True,
        )
    },
    "bls_cpi_la_metro": {
        "CUURS49ASA0": ("cpi_u_all_items_la_nsa", "index_1982_84_100", False)
    },
}


def clean_bls_added(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    mapping = BLS_ADDED_SERIES[source_id]
    rows: list[dict] = []
    found: set[str] = set()
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_series = []
        for response in payload.get("responses", []):
            source_series.extend(response.get("Results", {}).get("series", []))
    else:
        flat = pd.read_csv(path, sep="\t", dtype=str)
        flat.columns = flat.columns.str.strip()
        for column in flat.columns:
            flat[column] = flat[column].str.strip()
        source_series = [
            {
                "seriesID": series_id,
                "data": group.rename(
                    columns={
                        "period": "period",
                        "year": "year",
                        "value": "value",
                        "footnote_codes": "footnote_codes",
                    }
                ).to_dict("records"),
            }
            for series_id, group in flat.groupby("series_id")
            if series_id in mapping
        ]
    for series in source_series:
        series_id = series.get("seriesID")
        if series_id not in mapping:
            continue
        found.add(series_id)
        variable_id, unit, is_sa = mapping[series_id]
        for item in series.get("data", []):
                period = str(item.get("period", "")).strip()
                if not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
                    continue
                value = pd.to_numeric(item.get("value"), errors="coerce")
                if pd.isna(value):
                    continue
                start = pd.Timestamp(
                    year=int(item["year"]), month=int(period[1:]), day=1
                )
                end = start + pd.offsets.MonthEnd(0)
                if "footnotes" in item:
                    footnotes = "|".join(
                        note.get("code", "")
                        for note in item.get("footnotes", [])
                        if note.get("code")
                    )
                else:
                    footnotes = str(item.get("footnote_codes") or "").strip()
                rows.append(
                    {
                        "observation_date": end,
                        "source_observation_date": start,
                        "period_start": start,
                        "period_end": end,
                        "geo_id": "METRO_31080",
                        "state_fips": None,
                        "county_fips": None,
                        "variable_id": variable_id,
                        "value": value,
                        "reported_value": str(item.get("value")),
                        "unit": unit,
                        "provider": "U.S. Bureau of Labor Statistics",
                        "native_frequency": "monthly",
                        "final_frequency": "monthly",
                        **metadata_fields(record),
                        "transformation_id": f"{source_id}_api_month_end_v1",
                        "is_imputed": False,
                        "is_interpolated": False,
                        "is_seasonally_adjusted": is_sa,
                        "quality_flag": footnotes or None,
                    }
                )
    missing = sorted(set(mapping) - found)
    if missing or not rows:
        raise ValueError(f"{source_id} missing series/data: {missing}")
    return finalize(pd.DataFrame(rows), output_path)


def _bea_member(archive: zipfile.ZipFile, prefix: str) -> str:
    matches = [
        name
        for name in archive.namelist()
        if name.startswith(prefix) and name.lower().endswith(".csv")
    ]
    all_areas = [name for name in matches if "__ALL_AREAS_" in name]
    if not all_areas:
        raise ValueError(f"BEA archive is missing {prefix} all-areas CSV")
    return sorted(all_areas)[-1]


def clean_bea(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        prefix = (
            "SQINC1__" if source_id == "bea_state_personal_income" else "CAINC1__"
        )
        member = _bea_member(archive, prefix)
        source = pd.read_csv(
            archive.open(member), dtype=str, encoding="latin-1", low_memory=False
        )
    source["GeoFIPS_clean"] = source["GeoFIPS"].str.replace(r"\D", "", regex=True)
    target_fips = "06000" if source_id == "bea_state_personal_income" else "06037"
    source = source.loc[
        source["GeoFIPS_clean"].eq(target_fips)
        & source["LineCode"].isin(["1", "3"])
    ].copy()
    if source.empty:
        raise ValueError(f"{source_id} contains no configured geography/line codes")
    period_pattern = r"^\d{4}:Q[1-4]$" if source_id.endswith("state_personal_income") else r"^\d{4}$"
    period_columns = [
        column for column in source.columns if re.fullmatch(period_pattern, column)
    ]
    long = source.melt(
        id_vars=["LineCode", "TableName", "Description", "Unit"],
        value_vars=period_columns,
        var_name="period",
        value_name="reported_value",
    )
    long["value"] = pd.to_numeric(
        long["reported_value"].str.replace(",", "", regex=False), errors="coerce"
    )
    long = long.loc[long["value"].notna()].reset_index(drop=True)
    if source_id == "bea_state_personal_income":
        pieces = long["period"].str.extract(r"(?P<year>\d{4}):Q(?P<quarter>[1-4])")
        starts, ends = _quarter_dates(pieces["year"], pieces["quarter"])
        long["period_start"] = starts
        long["period_end"] = ends
        long["observation_date"] = ends
        long["source_observation_date"] = ends
        long["geo_id"] = "STATE_06"
        long["state_fips"] = "06"
        long["county_fips"] = None
        long["variable_id"] = long["LineCode"].map(
            {
                "1": "bea_ca_personal_income_q_sa",
                "3": "bea_ca_per_capita_personal_income_q_sa",
            }
        )
        long["unit"] = long["LineCode"].map(
            {
                "1": "millions_of_current_dollars_saar",
                "3": "current_dollars_per_person_saar",
            }
        )
        frequency = "quarterly"
        seasonally_adjusted = True
    else:
        long["period_start"] = pd.to_datetime(long["period"] + "-01-01")
        long["period_end"] = pd.to_datetime(long["period"] + "-12-31")
        long["observation_date"] = long["period_end"]
        long["source_observation_date"] = long["period_end"]
        long["geo_id"] = "COUNTY_06037"
        long["state_fips"] = "06"
        long["county_fips"] = "06037"
        long["variable_id"] = long["LineCode"].map(
            {
                "1": "bea_la_county_personal_income_annual",
                "3": "bea_la_county_per_capita_personal_income_annual",
            }
        )
        long["unit"] = long["LineCode"].map(
            {
                "1": "thousands_of_current_dollars",
                "3": "current_dollars_per_person",
            }
        )
        frequency = "annual"
        seasonally_adjusted = False
    long["quality_flag"] = (
        "table=" + long["TableName"] + ";line_code=" + long["LineCode"]
    )
    long = _apply_common(
        long,
        record,
        provider="U.S. Bureau of Economic Analysis",
        native_frequency=frequency,
        final_frequency=frequency,
        transformation_id=f"{source_id}_official_bulk_v1",
        seasonally_adjusted=seasonally_adjusted,
    )
    return finalize(long, output_path)


def clean_hvs(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    source = pd.read_excel(path, header=None)
    is_state = source_id == "census_hvs_state_rental_vacancy"
    target = "California" if is_state else "Los Angeles"
    geo_id = "STATE_06" if is_state else "METRO_31080"
    variable = (
        "hvs_rental_vacancy_rate_ca"
        if is_state
        else "hvs_rental_vacancy_rate_la_metro"
    )
    rows: list[dict] = []
    for row_index, target_row in source.iterrows():
        target_columns = [
            index
            for index, value in enumerate(target_row)
            if pd.notna(value) and target in str(value)
        ]
        if not target_columns:
            continue
        target_column = target_columns[0]
        header_window = source.iloc[max(0, row_index - 50) : row_index + 1]
        header_text = " ".join(
            str(value)
            for value in header_window.to_numpy().ravel()
            if pd.notna(value)
        )
        years = re.findall(r"\b20\d{2}\b", header_text)
        if not years:
            continue
        year = int(years[-1])
        values = [
            pd.to_numeric(value, errors="coerce")
            for value in target_row.iloc[target_column + 1 :]
        ]
        values += [float("nan")] * max(0, 8 - len(values))
        for quarter in range(1, 5):
            rate = values[(quarter - 1) * 2]
            moe = values[(quarter - 1) * 2 + 1]
            if pd.isna(rate):
                continue
            starts, ends = _quarter_dates(
                pd.Series([year]), pd.Series([quarter])
            )
            base = {
                "observation_date": ends.iloc[0],
                "source_observation_date": ends.iloc[0],
                "period_start": starts.iloc[0],
                "period_end": ends.iloc[0],
                "geo_id": geo_id,
                "state_fips": "06" if is_state else None,
                "county_fips": None,
                "provider": "U.S. Census Bureau",
                "native_frequency": "quarterly",
                "final_frequency": "quarterly",
                **metadata_fields(record),
                "transformation_id": "census_hvs_quarterly_rate_moe_v1",
                "is_imputed": False,
                "is_interpolated": False,
                "is_seasonally_adjusted": False,
            }
            rows.append(
                {
                    **base,
                    "variable_id": variable,
                    "value": rate,
                    "reported_value": str(rate),
                    "unit": "percent",
                    "quality_flag": "survey_estimate",
                }
            )
            if pd.notna(moe):
                rows.append(
                    {
                        **base,
                        "variable_id": f"{variable}_moe",
                        "value": moe,
                        "reported_value": str(moe),
                        "unit": "percentage_points",
                        "quality_flag": "published_margin_of_error",
                    }
                )
    if not rows:
        raise ValueError(f"No {target} HVS observations found in {path.name}")
    return finalize(pd.DataFrame(rows), output_path)


ACS_TABLES = {
    "b11001": {
        "B11001_E001": ("acs_households", "households"),
        "B11001_M001": ("acs_households_moe", "households"),
    },
    "b19013": {
        "B19013_E001": ("acs_median_household_income", "current_dollars"),
        "B19013_M001": ("acs_median_household_income_moe", "current_dollars"),
    },
    "b25001": {
        "B25001_E001": ("acs_total_housing_units", "housing_units"),
        "B25001_M001": ("acs_total_housing_units_moe", "housing_units"),
    },
    "b25002": {
        "B25002_E003": ("acs_vacant_housing_units", "housing_units"),
        "B25002_M003": ("acs_vacant_housing_units_moe", "housing_units"),
    },
    "b25003": {
        "B25003_E002": ("acs_owner_occupied_units", "housing_units"),
        "B25003_M002": ("acs_owner_occupied_units_moe", "housing_units"),
        "B25003_E003": ("acs_renter_occupied_units", "housing_units"),
        "B25003_M003": ("acs_renter_occupied_units_moe", "housing_units"),
    },
    "b25064": {
        "B25064_E001": ("acs_median_gross_rent", "current_dollars_per_month"),
        "B25064_M001": ("acs_median_gross_rent_moe", "current_dollars_per_month"),
    },
    "b25077": {
        "B25077_E001": ("acs_median_home_value", "current_dollars"),
        "B25077_M001": ("acs_median_home_value_moe", "current_dollars"),
    },
    "b25081": {
        "B25081_E002": ("acs_owner_units_with_mortgage", "housing_units"),
        "B25081_M002": ("acs_owner_units_with_mortgage_moe", "housing_units"),
    },
}


def clean_acs_table(
    path: Path,
    record: dict[str, str],
    source_id: str,
    geography_path: Path,
    county_output: Path,
    zcta_output: Path,
) -> pd.DataFrame:
    table_id = source_id.rsplit("_", 1)[-1]
    mapping = ACS_TABLES[table_id]
    source = pd.read_csv(
        path,
        sep="|",
        dtype=str,
        usecols=["GEO_ID", *mapping],
        low_memory=False,
    )
    postal_codes = set(
        pd.read_parquet(geography_path)["postal_zip"].dropna().astype(str).str.zfill(5)
    )
    county_geoid = "0500000US06037"
    zcta_prefix = "860Z200US"
    source = source.loc[
        source["GEO_ID"].eq(county_geoid)
        | (
            source["GEO_ID"].str.startswith(zcta_prefix)
            & source["GEO_ID"].str.removeprefix(zcta_prefix).isin(postal_codes)
        )
    ].copy()
    if source.empty or not source["GEO_ID"].eq(county_geoid).any():
        raise ValueError(f"{source_id} missing Los Angeles County ACS row")
    rows: list[dict] = []
    for _, item in source.iterrows():
        is_county = item["GEO_ID"] == county_geoid
        code = (
            "06037"
            if is_county
            else str(item["GEO_ID"]).removeprefix(zcta_prefix)
        )
        for source_column, (variable_id, unit) in mapping.items():
            value = pd.to_numeric(item[source_column], errors="coerce")
            if pd.isna(value) or value < 0:
                continue
            rows.append(
                {
                    "observation_date": pd.Timestamp("2024-12-31"),
                    "source_observation_date": pd.Timestamp("2024-12-31"),
                    "period_start": pd.Timestamp("2020-01-01"),
                    "period_end": pd.Timestamp("2024-12-31"),
                    "geo_id": f"COUNTY_{code}" if is_county else f"ZCTA_{code}",
                    "state_fips": "06" if is_county else None,
                    "county_fips": "06037" if is_county else None,
                    "variable_id": variable_id,
                    "value": value,
                    "reported_value": str(item[source_column]),
                    "unit": unit,
                    "provider": "U.S. Census Bureau",
                    "native_frequency": "annual_5_year_estimate",
                    "final_frequency": "annual",
                    **metadata_fields(record),
                    "transformation_id": "acs_2024_5year_table_summary_v1",
                    "is_imputed": False,
                    "is_interpolated": False,
                    "is_seasonally_adjusted": False,
                    "quality_flag": (
                        "2020-2024_5year_estimate"
                        if is_county
                        else "2020-2024_5year_estimate;"
                        "zcta_code_selected_from_la_postal_reference;"
                        "zcta_not_postal_zip"
                    ),
                }
            )
    frame = pd.DataFrame(rows)
    county = finalize(
        frame.loc[frame["geo_id"].eq("COUNTY_06037")].copy(), county_output
    )
    zcta = finalize(
        frame.loc[frame["geo_id"].str.startswith("ZCTA_")].copy(), zcta_output
    )
    return pd.concat([county, zcta], ignore_index=True)
