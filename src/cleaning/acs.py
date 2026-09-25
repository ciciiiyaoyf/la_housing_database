"""Census ACS 5-year table-based summary file cleaning."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields
from src.geography.zcta_reference import load_zcta_reference


ACS_TABLE_MAP = {
    "b05002": {
        "B05002_E013": ("acs_foreign_born_population", "persons"),
        "B05002_M013": ("acs_foreign_born_population_moe", "persons"),
    },
    "b07001": {
        "B07001_E081": ("acs_moved_from_abroad_past_year", "persons"),
        "B07001_M081": (
            "acs_moved_from_abroad_past_year_moe",
            "persons",
        ),
    },
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
    "b25004": {
        "B25004_E002": ("acs_for_rent_vacant_units", "housing_units"),
        "B25004_M002": ("acs_for_rent_vacant_units_moe", "housing_units"),
    },
    "b25032": {
        "B25032_E013": ("acs_b25032_renter_total", "housing_units"),
        "B25032_M013": ("acs_b25032_renter_total_moe", "housing_units"),
        "B25032_E014": ("acs_b25032_renter_1_detached", "housing_units"),
        "B25032_M014": (
            "acs_b25032_renter_1_detached_moe",
            "housing_units",
        ),
        "B25032_E015": ("acs_b25032_renter_1_attached", "housing_units"),
        "B25032_M015": (
            "acs_b25032_renter_1_attached_moe",
            "housing_units",
        ),
        "B25032_E016": ("acs_b25032_renter_2_units", "housing_units"),
        "B25032_M016": ("acs_b25032_renter_2_units_moe", "housing_units"),
        "B25032_E017": ("acs_b25032_renter_3_to_4_units", "housing_units"),
        "B25032_M017": (
            "acs_b25032_renter_3_to_4_units_moe",
            "housing_units",
        ),
        "B25032_E018": ("acs_b25032_renter_5_to_9_units", "housing_units"),
        "B25032_M018": (
            "acs_b25032_renter_5_to_9_units_moe",
            "housing_units",
        ),
        "B25032_E019": ("acs_b25032_renter_10_to_19_units", "housing_units"),
        "B25032_M019": (
            "acs_b25032_renter_10_to_19_units_moe",
            "housing_units",
        ),
        "B25032_E020": ("acs_b25032_renter_20_to_49_units", "housing_units"),
        "B25032_M020": (
            "acs_b25032_renter_20_to_49_units_moe",
            "housing_units",
        ),
        "B25032_E021": ("acs_b25032_renter_50_plus_units", "housing_units"),
        "B25032_M021": (
            "acs_b25032_renter_50_plus_units_moe",
            "housing_units",
        ),
        "B25032_E022": ("acs_b25032_renter_mobile_home", "housing_units"),
        "B25032_M022": (
            "acs_b25032_renter_mobile_home_moe",
            "housing_units",
        ),
        "B25032_E023": ("acs_b25032_renter_boat_rv_other", "housing_units"),
        "B25032_M023": (
            "acs_b25032_renter_boat_rv_other_moe",
            "housing_units",
        ),
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


B25127_STRUCTURE_SUFFIXES = (
    "total",
    "1_unit",
    "2_to_4_units",
    "5_to_19_units",
    "20_to_49_units",
    "50_plus_units",
    "mobile_other",
)

# B25127 changes its year-built bins and cell offsets across ACS vintages.  The
# downloader requests this union, which remains below the Census API's 50-field
# limit, and the cleaner assigns cells using the vintage regimes below.
ACS_API_REQUEST_COLUMN_OVERRIDES = {
    "b25127": tuple(
        f"B25127_{estimate_or_moe}{sequence:03d}"
        for sequence in range(38, 60)
        for estimate_or_moe in ("E", "M")
    ),
}

B25127_OUTPUT_MAP = {
    "B25127_OUTPUT_E_TOTAL": ("acs_b25127_renter_total", "housing_units"),
    "B25127_OUTPUT_M_TOTAL": ("acs_b25127_renter_total_moe", "housing_units"),
    **{
        f"B25127_OUTPUT_{estimate_or_moe}_{band.upper()}_{suffix.upper()}": (
            "acs_b25127_renter_"
            f"{band}_{suffix}"
            f"{'_moe' if estimate_or_moe == 'M' else ''}",
            "housing_units",
        )
        for band in ("recent_lower_bound", "cutoff_mixed")
        for suffix in B25127_STRUCTURE_SUFFIXES
        for estimate_or_moe in ("E", "M")
    },
}

B25127_VINTAGE_REGIMES = (
    {
        "start_year": 2010,
        "end_year": 2014,
        "renter_total_sequence": 38,
        "recent_start_sequence": 39,
        "mixed_start_sequence": 46,
        "quality_flag": (
            "b25127_recent_built_2000_plus_mixed_built_1980_to_1999"
        ),
    },
    {
        "start_year": 2015,
        "end_year": 2020,
        "renter_total_sequence": 45,
        "recent_start_sequence": 46,
        "mixed_start_sequence": 53,
        "quality_flag": (
            "b25127_recent_built_2010_plus_mixed_built_2000_to_2009"
        ),
    },
    {
        "start_year": 2021,
        "end_year": 2024,
        "renter_total_sequence": 45,
        "recent_start_sequence": 46,
        "mixed_start_sequence": 53,
        "quality_flag": (
            "b25127_recent_built_2020_plus_mixed_built_2000_to_2019"
        ),
    },
)

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

ACS_MIN_ZCTA_YEAR = 2011
ACS_MIGRATION_SUPPLEMENT_SOURCE_IDS = {
    "census_acs_5year_b05002",
    "census_acs_5year_b07001",
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


def _b25127_vintage_regime(year: int) -> dict[str, int | str]:
    for regime in B25127_VINTAGE_REGIMES:
        if int(regime["start_year"]) <= year <= int(regime["end_year"]):
            return regime
    raise ValueError(f"No ACS B25127 cell regime is configured for vintage {year}")


def _b25127_rows(source: pd.DataFrame, year: int) -> list[pd.DataFrame]:
    """Normalize vintage-specific B25127 cells to stable analytical inputs."""

    regime = _b25127_vintage_regime(year)
    geography_columns = ["geo_id", "state_fips", "county_fips"]
    rows: list[pd.DataFrame] = []

    def append_cell(sequence: int, variable_id: str, estimate_or_moe: str) -> None:
        api_column = f"B25127_{sequence:03d}{estimate_or_moe}"
        if api_column not in source:
            raise ValueError(
                f"ACS B25127 schema change for {year}: missing {api_column}"
            )
        part = source[[*geography_columns, api_column]].copy()
        part["value"] = _numeric_acs(part[api_column])
        part["reported_value"] = part["value"]
        part["variable_id"] = variable_id
        part["unit"] = "housing_units"
        part["_normalization_quality_flag"] = str(regime["quality_flag"])
        rows.append(part)

    for estimate_or_moe in ("E", "M"):
        moe_suffix = "_moe" if estimate_or_moe == "M" else ""
        append_cell(
            int(regime["renter_total_sequence"]),
            f"acs_b25127_renter_total{moe_suffix}",
            estimate_or_moe,
        )
        for band, start_field in (
            ("recent_lower_bound", "recent_start_sequence"),
            ("cutoff_mixed", "mixed_start_sequence"),
        ):
            start_sequence = int(regime[start_field])
            for offset, suffix in enumerate(B25127_STRUCTURE_SUFFIXES):
                append_cell(
                    start_sequence + offset,
                    f"acs_b25127_renter_{band}_{suffix}{moe_suffix}",
                    estimate_or_moe,
                )
    return rows


def _zcta_reference_root(geography_path: Path) -> Path:
    """Resolve the project root without using geography rows as the ZCTA source."""

    candidates: list[Path] = []
    if (
        geography_path.name == "geography.parquet"
        and geography_path.parent.name == "clean"
        and geography_path.parent.parent.name == "data"
    ):
        candidates.append(geography_path.parents[2])
    candidates.append(geography_path.parent)
    for candidate in candidates:
        if (candidate / "config" / "la_zcta_reference.json").is_file():
            return candidate
    # Let the canonical loader raise its precise missing-reference error.
    return candidates[0]


def _eligible_zctas(geography_path: Path) -> set[str]:
    """Load the canonical ZCTA scope; never infer it from postal/geography data."""

    values, _ = load_zcta_reference(_zcta_reference_root(geography_path))
    return set(values)


def _year_from_config_date(value: str | None) -> int | None:
    if not value:
        return None
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError(f"Invalid configured ACS date: {value!r}")
    return int(parsed.year)


def _validate_year_range(
    *,
    source_id: str,
    declared_start_year: int,
    declared_end_year: int,
    expected_start_date: str | None,
    expected_end_date: str | None,
) -> tuple[int, ...]:
    if declared_start_year > declared_end_year:
        raise ValueError(
            f"ACS archive has an invalid year range for {source_id}: "
            f"{declared_start_year}>{declared_end_year}"
        )
    expected_start_year = _year_from_config_date(expected_start_date)
    expected_end_year = _year_from_config_date(expected_end_date)
    if expected_start_year is not None and declared_start_year != expected_start_year:
        raise ValueError(
            f"ACS archive start year mismatch for {source_id}: "
            f"expected {expected_start_year}, found {declared_start_year}"
        )
    if expected_end_year is not None and declared_end_year != expected_end_year:
        raise ValueError(
            f"ACS archive end year mismatch for {source_id}: "
            f"expected {expected_end_year}, found {declared_end_year}"
        )
    return tuple(range(declared_start_year, declared_end_year + 1))


def _validate_api_request_grid(
    requests: list[dict],
    *,
    source_id: str,
    expected_years: tuple[int, ...],
) -> None:
    """Require one county request and the correct ZCTA request regime per year."""

    expected_year_set = set(expected_years)
    request_keys: list[tuple[int, str]] = []
    geography_classes: dict[int, list[str]] = {year: [] for year in expected_years}
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError(f"Invalid ACS API request record for {source_id}")
        try:
            year = int(request["year"])
            geography = str(request["geography"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"ACS API request is missing a valid year/geography for {source_id}"
            ) from exc
        if year not in expected_year_set:
            raise ValueError(
                f"ACS API request year outside declared range for {source_id}: {year}"
            )
        if geography == "county":
            geography_class = "county"
        elif geography == "zcta" or geography.startswith("zcta_chunk_"):
            geography_class = "zcta"
        else:
            raise ValueError(f"Unsupported ACS API geography: {geography}")
        request_keys.append((year, geography))
        geography_classes[year].append(geography_class)

    duplicates = sorted(
        key for key, count in Counter(request_keys).items() if count > 1
    )
    if duplicates:
        raise ValueError(
            f"Duplicate ACS API year/geography requests for {source_id}: "
            f"{duplicates[:5]}"
        )

    for year in expected_years:
        classes = geography_classes[year]
        county_count = classes.count("county")
        zcta_count = classes.count("zcta")
        if county_count != 1:
            raise ValueError(
                f"ACS API request grid requires exactly one county request for "
                f"{source_id} {year}; found {county_count}"
            )
        if year < ACS_MIN_ZCTA_YEAR and zcta_count:
            raise ValueError(
                f"ACS API request grid must not contain ZCTA geography for "
                f"{source_id} {year}"
            )
        if year >= ACS_MIN_ZCTA_YEAR and zcta_count < 1:
            raise ValueError(
                f"ACS API request grid requires ZCTA geography for "
                f"{source_id} {year}"
            )


def _validate_clean_grid(
    long: pd.DataFrame,
    *,
    source_id: str,
    expected_years: tuple[int, ...],
    eligible_zctas: set[str],
    variable_map: dict[str, tuple[str, str]],
) -> None:
    """Require the exact configured county/ZCTA observation grid and uniqueness."""

    key_columns = ["observation_date", "geo_id", "variable_id"]
    duplicate_rows = long.loc[long.duplicated(key_columns, keep=False), key_columns]
    if not duplicate_rows.empty:
        sample = duplicate_rows.head(5).astype(str).to_dict("records")
        raise ValueError(
            f"Duplicate ACS clean observations for {source_id}: {sample}"
        )

    expected_variables = {variable_id for variable_id, _ in variable_map.values()}
    actual_variables = set(long["variable_id"].astype(str))
    if actual_variables != expected_variables:
        raise ValueError(
            f"ACS clean variable grid mismatch for {source_id}: "
            f"missing={sorted(expected_variables - actual_variables)}, "
            f"extra={sorted(actual_variables - expected_variables)}"
        )

    actual_grid = {
        (int(pd.Timestamp(date).year), str(geo_id))
        for date, geo_id in long[["observation_date", "geo_id"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    }
    expected_grid: set[tuple[int, str]] = set()
    for year in expected_years:
        expected_grid.add((year, "COUNTY_06037"))
        if year >= ACS_MIN_ZCTA_YEAR:
            expected_grid.update((year, f"ZCTA_{zcta}") for zcta in eligible_zctas)
    missing = sorted(expected_grid - actual_grid)
    extra = sorted(actual_grid - expected_grid)
    if missing or extra:
        raise ValueError(
            f"ACS clean geography grid mismatch for {source_id}: "
            f"missing_count={len(missing)}, missing_sample={missing[:5]}, "
            f"extra_count={len(extra)}, extra_sample={extra[:5]}"
        )

    expected_rows = len(expected_grid) * len(expected_variables)
    if len(long) != expected_rows:
        raise ValueError(
            f"ACS clean row-count mismatch for {source_id}: "
            f"expected {expected_rows}, found {len(long)}"
        )
    if source_id in ACS_MIGRATION_SUPPLEMENT_SOURCE_IDS:
        numeric = pd.to_numeric(long["value"], errors="coerce")
        negative = long.loc[numeric.lt(0), [*key_columns, "value"]]
        if not negative.empty:
            sample = negative.head(5).astype(str).to_dict("records")
            raise ValueError(
                f"ACS migration supplement contains negative counts for "
                f"{source_id}: {sample}"
            )


def _clean_acs_api_json(
    path: Path,
    record: dict[str, str],
    source_id: str,
    variable_map: dict[str, tuple[str, str]] | None,
    eligible_zips: set[str],
    output_path: Path,
    expected_start_date: str | None,
    expected_end_date: str | None,
) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"ACS API archive must be a JSON object: {path.name}")
    expected_table_id = source_id.rsplit("_", 1)[-1].lower()
    if str(payload.get("table_id", "")).lower() != expected_table_id:
        raise ValueError(
            f"ACS API archive table mismatch for {source_id}: "
            f"found {payload.get('table_id')!r}"
        )
    try:
        declared_start_year = int(payload["start_year"])
        declared_end_year = int(payload["end_year"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"ACS API archive has no valid declared year range: {path.name}"
        ) from exc
    expected_years = _validate_year_range(
        source_id=source_id,
        declared_start_year=declared_start_year,
        declared_end_year=declared_end_year,
        expected_start_date=expected_start_date,
        expected_end_date=expected_end_date,
    )
    requests = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(requests, list) or not requests:
        raise ValueError(f"ACS API raw file has no requests list: {path.name}")
    _validate_api_request_grid(
        requests, source_id=source_id, expected_years=expected_years
    )
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
        if expected_table_id == "b25127":
            rows = _b25127_rows(source, year)
        else:
            if variable_map is None:
                raise ValueError(f"No ACS variable map is configured for {source_id}")
            rows = []
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
        base_quality_flag = f"{year - 4}-{year}_5year_estimate"
        if "_normalization_quality_flag" in long:
            long["quality_flag"] = (
                base_quality_flag
                + "|"
                + long["_normalization_quality_flag"].astype(str)
            )
        else:
            long["quality_flag"] = base_quality_flag
        pieces.append(long)
    long = pd.concat(pieces, ignore_index=True)
    long["provider"] = "U.S. Census Bureau"
    long["native_frequency"] = "annual_5_year_estimate"
    long["final_frequency"] = "annual"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = (
        "acs_b25127_vintage_normalization_v1"
        if expected_table_id == "b25127"
        else "acs_5year_api_summary_v1"
    )
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = False
    _validate_clean_grid(
        long,
        source_id=source_id,
        expected_years=expected_years,
        eligible_zctas=eligible_zips,
        variable_map=(
            B25127_OUTPUT_MAP if expected_table_id == "b25127" else variable_map
        ),
    )
    return finalize(long, output_path)


def clean_acs_table(
    path: Path,
    record: dict[str, str],
    source_id: str,
    geography_path: Path,
    output_path: Path,
    *,
    expected_start_date: str | None = None,
    expected_end_date: str | None = None,
) -> pd.DataFrame:
    table_id = source_id.rsplit("_", 1)[-1].lower()
    variable_map = ACS_TABLE_MAP.get(table_id)
    if variable_map is None and table_id != "b25127":
        raise ValueError(f"No ACS variable map is configured for {source_id}")
    eligible_zips = _eligible_zctas(geography_path)
    if path.suffix.lower() == ".json":
        return _clean_acs_api_json(
            path,
            record,
            source_id,
            variable_map,
            eligible_zips,
            output_path,
            expected_start_date,
            expected_end_date,
        )
    if table_id == "b25127":
        raise ValueError(
            "ACS B25127 requires a historical API JSON archive because its "
            "cell meanings vary by vintage"
        )
    if variable_map is None:
        raise ValueError(f"No ACS variable map is configured for {source_id}")
    source = pd.read_csv(path, sep="|", dtype=str)
    source["geo_id"] = source["GEO_ID"].map(lambda item: _geo_id(item, eligible_zips))
    source = source.loc[source["geo_id"].notna()].copy()
    if source.empty:
        raise ValueError(f"No LA County/ZCTA ACS rows found in {path.name}")
    year = _acs_year_from_filename(path)
    expected_years = _validate_year_range(
        source_id=source_id,
        declared_start_year=year,
        declared_end_year=year,
        expected_start_date=expected_start_date,
        expected_end_date=expected_end_date,
    )
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
    _validate_clean_grid(
        long,
        source_id=source_id,
        expected_years=expected_years,
        eligible_zctas=eligible_zips,
        variable_map=variable_map,
    )
    return finalize(long, output_path)
