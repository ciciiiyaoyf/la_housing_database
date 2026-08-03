"""Census ACS 5-year table-based summary file cleaning."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


ACS_TABLE_MAP = {
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
        "B25064_E001": (
            "acs_median_gross_rent",
            "current_dollars_per_month",
        ),
        "B25064_M001": (
            "acs_median_gross_rent_moe",
            "current_dollars_per_month",
        ),
    },
    "b25077": {
        "B25077_E001": ("acs_median_home_value", "current_dollars"),
        "B25077_M001": ("acs_median_home_value_moe", "current_dollars"),
    },
    "b25081": {
        "B25081_E002": (
            "acs_owner_units_with_mortgage",
            "housing_units",
        ),
        "B25081_M002": (
            "acs_owner_units_with_mortgage_moe",
            "housing_units",
        ),
    },
    "b25106": {
        "B25106_E004": (
            "acs_b25106_owner_less_20k_cost_lt20pct",
            "housing_units",
        ),
        "B25106_E005": (
            "acs_b25106_owner_less_20k_cost_20_29pct",
            "housing_units",
        ),
        "B25106_E006": (
            "acs_b25106_owner_less_20k_cost_30pluspct",
            "housing_units",
        ),
        "B25106_E008": (
            "acs_b25106_owner_20k_34999_cost_lt20pct",
            "housing_units",
        ),
        "B25106_E009": (
            "acs_b25106_owner_20k_34999_cost_20_29pct",
            "housing_units",
        ),
        "B25106_E010": (
            "acs_b25106_owner_20k_34999_cost_30pluspct",
            "housing_units",
        ),
        "B25106_E012": (
            "acs_b25106_owner_35k_49999_cost_lt20pct",
            "housing_units",
        ),
        "B25106_E013": (
            "acs_b25106_owner_35k_49999_cost_20_29pct",
            "housing_units",
        ),
        "B25106_E014": (
            "acs_b25106_owner_35k_49999_cost_30pluspct",
            "housing_units",
        ),
        "B25106_E016": (
            "acs_b25106_owner_50k_74999_cost_lt20pct",
            "housing_units",
        ),
        "B25106_E017": (
            "acs_b25106_owner_50k_74999_cost_20_29pct",
            "housing_units",
        ),
        "B25106_E018": (
            "acs_b25106_owner_50k_74999_cost_30pluspct",
            "housing_units",
        ),
        "B25106_E020": (
            "acs_b25106_owner_75kplus_cost_lt20pct",
            "housing_units",
        ),
        "B25106_E021": (
            "acs_b25106_owner_75kplus_cost_20_29pct",
            "housing_units",
        ),
        "B25106_E022": (
            "acs_b25106_owner_75kplus_cost_30pluspct",
            "housing_units",
        ),
        "B25106_E023": (
            "acs_b25106_owner_zero_negative_income",
            "housing_units",
        ),
    },
}

ACS_MISSING_SENTINELS = {
    "-222222222",
    "-333333333",
    "-555555555",
    "-666666666",
    "-888888888",
    "-999999999",
    "**",
    "***",
    "*****",
    "-",
}


def _acs_year_from_filename(path: Path) -> int:
    for token in path.stem.replace("-", "_").split("_"):
        if token.isdigit() and len(token) == 4:
            return int(token)
    return 2024


def _geo_id(raw_geo: str, eligible_zips: set[str]) -> str | None:
    raw_geo = str(raw_geo)
    if raw_geo == "0500000US06037":
        return "COUNTY_06037"
    if raw_geo.startswith("860Z200US"):
        zcta = raw_geo[-5:]
        if zcta in eligible_zips:
            return f"ZCTA_{zcta}"
    return None


def _acs_api_variable(summary_file_variable: str) -> str:
    """Convert table-based ACS names like B25106_E004 to API B25106_004E."""
    match = re.fullmatch(r"([A-Z0-9]+)_([EM])(\d{3})", summary_file_variable)
    if not match:
        raise ValueError(f"Unsupported ACS variable name: {summary_file_variable}")
    table, estimate_or_moe, sequence = match.groups()
    return f"{table}_{sequence}{estimate_or_moe}"


def _numeric_acs(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.mask(cleaned.isin(ACS_MISSING_SENTINELS))
    return pd.to_numeric(cleaned, errors="coerce")


def _clean_acs_api_json(
    path: Path,
    record: dict[str, str],
    source_id: str,
    variable_map: dict[str, tuple[str, str]],
    eligible_zips: set[str],
    output_path: Path,
) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    requests = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(requests, list) or not requests:
        raise ValueError(f"ACS API raw file has no requests list: {path.name}")
    pieces: list[pd.DataFrame] = []
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError(f"Invalid ACS API request record in {path.name}")
        year = int(request["year"])
        geography = str(request["geography"])
        response = request.get("response")
        if not isinstance(response, list) or len(response) < 2:
            raise ValueError(
                f"ACS API request has no observations for {year} {geography}"
            )
        header = [str(item) for item in response[0]]
        source = pd.DataFrame(response[1:], columns=header, dtype=str)
        if geography == "county":
            if "ucgid" in source:
                county_fips = source["ucgid"].astype(str).str.extract(
                    r"^050C0US(\d{5})$", expand=False
                )
                source["county_fips"] = county_fips
                source["state_fips"] = county_fips.str[:2]
            else:
                required = {"state", "county"}
                missing = required - set(source.columns)
                if missing:
                    raise ValueError(
                        f"ACS county response missing geography columns: {sorted(missing)}"
                    )
                source["state_fips"] = source["state"].str.zfill(2)
                source["county_fips"] = source["county"].map(
                    lambda item: f"06{str(item).zfill(3)}"
                    if str(item).zfill(3) == "037"
                    else None
                )
            source["geo_id"] = source["county_fips"].map(
                lambda item: f"COUNTY_{item}" if item == "06037" else None
            )
        elif geography == "zcta" or geography.startswith("zcta_chunk_"):
            if "ucgid" in source:
                source["postal_zip"] = source["ucgid"].astype(str).str.extract(
                    r"^860Z200US(\d{5})$", expand=False
                )
            else:
                zcta_column = "zip code tabulation area"
                if zcta_column not in source:
                    raise ValueError("ACS ZCTA response missing ZCTA geography column")
                source["postal_zip"] = source[zcta_column].astype(str).str.zfill(5)
            source = source.loc[source["postal_zip"].isin(eligible_zips)].copy()
            source["geo_id"] = source["postal_zip"].map(lambda item: f"ZCTA_{item}")
            source["state_fips"] = None
            source["county_fips"] = None
        else:
            raise ValueError(f"Unsupported ACS API geography: {geography}")
        source = source.loc[source["geo_id"].notna()].copy()
        if source.empty:
            raise ValueError(f"No LA County/ZCTA ACS rows found for {year} {geography}")
        rows: list[pd.DataFrame] = []
        for source_column, (variable_id, unit) in variable_map.items():
            api_column = _acs_api_variable(source_column)
            if api_column not in source:
                raise ValueError(f"ACS API schema change: missing {api_column}")
            part = source[
                ["geo_id", "state_fips", "county_fips", api_column]
            ].copy()
            part["value"] = _numeric_acs(part[api_column])
            part["reported_value"] = part["value"]
            part["variable_id"] = variable_id
            part["unit"] = unit
            rows.append(part)
        long = pd.concat(rows, ignore_index=True)
        long["observation_date"] = pd.Timestamp(year=year, month=12, day=31)
        long["source_observation_date"] = long["observation_date"]
        long["period_start"] = pd.Timestamp(year=year - 4, month=1, day=1)
        long["period_end"] = long["observation_date"]
        long["quality_flag"] = f"{year - 4}-{year}_5year_estimate"
        pieces.append(long)
    long = pd.concat(pieces, ignore_index=True)
    long["provider"] = "U.S. Census Bureau"
    long["native_frequency"] = "annual_5_year_estimate"
    long["final_frequency"] = "annual"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = "acs_5year_api_summary_v1"
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = False
    return finalize(long, output_path)


def clean_acs_table(
    path: Path,
    record: dict[str, str],
    source_id: str,
    geography_path: Path,
    output_path: Path,
) -> pd.DataFrame:
    table_id = source_id.rsplit("_", 1)[-1].lower()
    variable_map = ACS_TABLE_MAP.get(table_id)
    if variable_map is None:
        raise ValueError(f"No ACS variable map is configured for {source_id}")
    geography = pd.read_parquet(geography_path)
    eligible_zips = set(
        geography.loc[geography["postal_zip"].notna(), "postal_zip"].astype(str)
    )
    if path.suffix.lower() == ".json":
        return _clean_acs_api_json(
            path, record, source_id, variable_map, eligible_zips, output_path
        )
    source = pd.read_csv(path, sep="|", dtype=str)
    source["geo_id"] = source["GEO_ID"].map(lambda item: _geo_id(item, eligible_zips))
    source = source.loc[source["geo_id"].notna()].copy()
    if source.empty:
        raise ValueError(f"No LA County/ZCTA ACS rows found in {path.name}")
    year = _acs_year_from_filename(path)
    rows: list[pd.DataFrame] = []
    for source_column, (variable_id, unit) in variable_map.items():
        if source_column not in source:
            raise ValueError(f"ACS schema change: missing {source_column}")
        part = source[["geo_id", source_column]].copy()
        part["value"] = _numeric_acs(part[source_column])
        part["reported_value"] = part["value"]
        part["variable_id"] = variable_id
        part["unit"] = unit
        rows.append(part)
    long = pd.concat(rows, ignore_index=True)
    long["observation_date"] = pd.Timestamp(year=year, month=12, day=31)
    long["source_observation_date"] = long["observation_date"]
    long["period_start"] = pd.Timestamp(year=year - 4, month=1, day=1)
    long["period_end"] = long["observation_date"]
    long["state_fips"] = long["geo_id"].map(
        lambda item: "06" if str(item).startswith("COUNTY_") else None
    )
    long["county_fips"] = long["geo_id"].map(
        lambda item: "06037" if str(item).startswith("COUNTY_") else None
    )
    long["provider"] = "U.S. Census Bureau"
    long["native_frequency"] = "annual_5_year_estimate"
    long["final_frequency"] = "annual"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = "acs_5year_table_summary_v1"
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = False
    long["quality_flag"] = f"{year - 4}-{year}_5year_estimate"
    return finalize(long, output_path)
