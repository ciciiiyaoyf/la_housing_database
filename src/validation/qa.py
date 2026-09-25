"""Automated QA reports; structural failures raise, outliers warn."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.common.config import ProjectConfig
from src.common.variable_metadata import validate_variable_metadata
from src.features.derived import (
    APARTMENT_COMPLETION_PROXY_COLUMNS,
    MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS,
    apartment_completion_proxies,
    market_tightness_index,
    multifamily_under_construction_proxies,
)
from src.features.comparable_counties import (
    MARKET_TIGHTNESS_COMPONENTS,
    county_housing_market_heat_index,
    exact_calendar_log_momentum,
    la_relative_market_performance,
    laus_employment_momentum_yoy,
    market_tightness_index as market_tightness_index_3c,
    positive_price_to_rent_ratio,
)
from src.features.market_regimes import (
    APPLICATION_START as REGIME_APPLICATION_START,
    FEATURE_COLUMNS as REGIME_FEATURE_COLUMNS,
    REGIME_LABELS,
    build_quarterly_regime_inputs,
    classify_quarterly_regime_inputs,
    load_market_regime_model,
)
from src.features.macro_financial import MONTHLY_IDENTITY_COLUMNS
from src.geography.build import CALIFORNIA_COUNTY_FIPS
from src.geography.comparable_counties import (
    COMPARABLE_COUNTY_FIPS,
    PEER_COUNTY_FIPS,
    PRIMARY_COUNTY_FIPS,
)
from src.geography.zcta_reference import load_zcta_reference
from src.validation.macro_financial import (
    CES_BASE_IDS,
    macro_financial_failures,
    macro_monthly_panel_failures,
)


GEO_ID_PATTERN = re.compile(
    r"^(US|STATE_\d{2}|COUNTY_\d{5}|ZIP_\d{5}|ZCTA_\d{5}|"
    r"TRACT_\d{11}|ZIPCOUNTYPART_\d{5}_\d{5}|"
    r"METRO_\d{5}|METRODIV_\d{5}|COC_[A-Z]{2}-\d{3}[A-Z]?|"
    r"COC_COMPOSITE_[A-Z0-9_-]+)$"
)

ACS_B25032_STRUCTURE_GROUPS = {
    "acs_renter_occupied_1_unit_share": (
        "acs_b25032_renter_1_detached",
        "acs_b25032_renter_1_attached",
    ),
    "acs_renter_occupied_2_to_4_units_share": (
        "acs_b25032_renter_2_units",
        "acs_b25032_renter_3_to_4_units",
    ),
    "acs_renter_occupied_5_plus_units_share": (
        "acs_b25032_renter_5_to_9_units",
        "acs_b25032_renter_10_to_19_units",
        "acs_b25032_renter_20_to_49_units",
        "acs_b25032_renter_50_plus_units",
    ),
    "acs_renter_occupied_mobile_other_share": (
        "acs_b25032_renter_mobile_home",
        "acs_b25032_renter_boat_rv_other",
    ),
}

ACS_B25127_STRUCTURE_SUFFIXES = (
    "1_unit",
    "2_to_4_units",
    "5_to_19_units",
    "20_to_49_units",
    "50_plus_units",
    "mobile_other",
)

ACS_RECENT_CONSTRUCTION_SHARE_COLUMNS = (
    "acs_renter_recent_construction_share_lower_bound",
    "acs_renter_recent_construction_share_upper_bound",
)

ACS_NONCONTROLLED_EXPOSURE_COLUMNS = (
    "noncontrolled_exposure_share_structural_proxy_lower_bound",
    "noncontrolled_exposure_share_structural_proxy_upper_bound",
)

NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS = (
    "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound",
    "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound",
)

ACS_NONCONTROLLED_DERIVED_COLUMNS = (
    *ACS_B25032_STRUCTURE_GROUPS,
    *ACS_RECENT_CONSTRUCTION_SHARE_COLUMNS,
    *ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
)

ACS_NONCONTROLLED_STRUCTURAL_PROXY_START_YEAR = 2020
ACS_COMPONENT_RECONCILIATION_TOLERANCE = 1e-9

CONSTRUCTION_PERMIT_COLUMN = "permits_5_plus_units"
CONSTRUCTION_PROXY_COLUMNS = (
    *APARTMENT_COMPLETION_PROXY_COLUMNS.values(),
    *MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values(),
)

PEP_RATE_SPECS = {
    "net_exemption_rate": (
        "irs_soi_net_migration_exemptions",
        "census_pep_county_population_annual",
    ),
    "permits_1_unit_per_1000_housing_units": (
        "permits_1_unit_annual_final",
        "census_pep_county_housing_units_annual",
    ),
}

NEW_ANNUAL_OBSERVED_FACT_IDS = frozenset(
    {
        "census_pep_county_population_annual",
        "census_pep_county_housing_units_annual",
        "ca_cpi_u_all_items_annual",
    }
)
NEW_ANNUAL_DERIVED_IDS = frozenset(
    {
        *PEP_RATE_SPECS,
        "ca_homeowners_avg_written_premium_real",
    }
)
NEW_ANNUAL_PANEL_LOCATIONS = {
    "census_pep_county_population_annual": "county_annual",
    "census_pep_county_housing_units_annual": "county_annual",
    "net_exemption_rate": "county_annual",
    "permits_1_unit_per_1000_housing_units": "county_annual",
    "ca_cpi_u_all_items_annual": "state_annual",
    "ca_homeowners_avg_written_premium_real": "state_annual",
}

ACS_ISSUE_13_14_PANEL_ONLY_IDS = frozenset(
    {
        "net_change_occupied_apartments_annual",
        "zcta_renter_occupied_units_vintage_change_annual",
        "county_annual_price_to_income_acs",
        "zcta_annual_price_to_income_acs",
    }
)
HUD_PIT_ANNUAL_IDS = frozenset(
    {
        "hud_pit_overall_homeless_annual",
        "hud_pit_sheltered_homeless_annual",
        "hud_pit_unsheltered_homeless_annual",
    }
)
ISSUE_13_15_APPROVED_PANEL_LOCATIONS = {
    "net_change_occupied_apartments_annual": "county_annual",
    "zcta_renter_occupied_units_vintage_change_annual": "zcta_annual",
    "county_annual_price_to_income_acs": "county_annual",
    "zcta_annual_price_to_income_acs": "zcta_annual",
    **{variable_id: "coc_annual" for variable_id in HUD_PIT_ANNUAL_IDS},
}

FORMULA_ABSOLUTE_TOLERANCE = 1e-9
FORMULA_RELATIVE_TOLERANCE = 1e-12


@dataclass
class CheckResult:
    check_id: str
    severity: str
    status: str
    failure_count: int
    detail: str


def _result(
    check_id: str, severity: str, failures: pd.DataFrame, detail: str
) -> CheckResult:
    count = len(failures)
    return CheckResult(
        check_id,
        severity,
        "fail" if count and severity == "error" else "warn" if count else "pass",
        count,
        detail,
    )


def _failure_rows(
    check_id: str, severity: str, frame: pd.DataFrame, limit: int = 1000
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result = frame.head(limit).copy()
    result.insert(0, "severity", severity)
    result.insert(0, "check_id", check_id)
    return result


def _metro_geography_failures(fact: pd.DataFrame) -> pd.DataFrame:
    """Preserve legacy metro scope and allow only the twelve new CES IDs.

    A source-level or ``ces_`` prefix exemption would also permit incorrectly
    labeled legacy variables.  The new industry's level/growth IDs have an
    explicit two-geography contract instead: California and LA metro division.
    """

    variables = fact["variable_id"].astype("string")
    geographies = fact["geo_id"].astype("string")
    new_ces_ids = frozenset(CES_BASE_IDS) | {
        f"{variable}_yoy_pct" for variable in CES_BASE_IDS
    }
    new_ces = variables.isin(new_ces_ids)
    metro_only = variables.str.startswith(
        ("ces_", "cpi_", "hvs_rental_vacancy_rate_la_", "fhfa_hpi_la_metropolitan_"),
        na=False,
    ) & ~new_ces
    valid_legacy_geo = geographies.str.startswith(("METRO_", "METRODIV_"), na=False)
    valid_new_ces_geo = geographies.isin({"STATE_06", "METRODIV_31084"})
    return fact.loc[
        (metro_only & ~valid_legacy_geo) | (new_ces & ~valid_new_ces_geo)
    ].copy()


def _classify_geo_type(geo_id: object) -> str:
    value = str(geo_id)
    if value == "US":
        return "national"
    prefix_types = (
        ("STATE_", "state"),
        ("COUNTY_", "county"),
        ("TRACT_", "tract_2020"),
        ("ZIPCOUNTYPART_", "postal_zip_county_part"),
        ("ZIP_", "postal_zip"),
        ("ZCTA_", "zcta"),
        ("METRODIV_", "metro_division"),
        ("METRO_", "metro"),
        ("COC_COMPOSITE_", "explicit_coc_composite"),
        ("COC_", "continuum_of_care"),
    )
    for prefix, geo_type in prefix_types:
        if value.startswith(prefix):
            return geo_type
    return "unknown"


def _hud_usps_geography_identity_failures(fact: pd.DataFrame) -> pd.DataFrame:
    """Return HUD-USPS tract/ZIP-part IDs inconsistent with their FIPS fields."""

    source = fact.get("source_id", pd.Series(index=fact.index, dtype="string"))
    hud_columns = [
        column
        for column in (
            "observation_date",
            "geo_id",
            "variable_id",
            "state_fips",
            "county_fips",
        )
        if column in fact
    ]
    hud = fact.loc[
        source.astype("string").eq("hud_usps_ncwm_quarterly"),
        hud_columns,
    ]
    if hud.empty:
        return pd.DataFrame(
            columns=["observation_date", "geo_id", "state_fips", "county_fips"]
        )

    geo_id = hud["geo_id"].astype("string")
    state_fips = hud["state_fips"].astype("string")
    county_fips = hud["county_fips"].astype("string")
    tract_county = geo_id.str.extract(
        r"^TRACT_(\d{5})\d{6}$", expand=False
    )
    zip_part_county = geo_id.str.extract(
        r"^ZIPCOUNTYPART_(\d{5})_\d{5}$", expand=False
    )
    tract = geo_id.str.startswith("TRACT_", na=False)
    zip_part = geo_id.str.startswith("ZIPCOUNTYPART_", na=False)
    expected_county = tract_county.where(tract, zip_part_county)
    relevant = tract | zip_part
    mismatch = relevant & (
        expected_county.isna()
        | county_fips.ne(expected_county).fillna(True)
        | state_fips.ne(expected_county.str[:2]).fillna(True)
    )
    columns = [
        column
        for column in (
            "observation_date",
            "geo_id",
            "variable_id",
            "state_fips",
            "county_fips",
        )
        if column in hud
    ]
    return hud.loc[mismatch, columns]


def _hud_pit_composite_failures(fact: pd.DataFrame) -> pd.DataFrame:
    """Validate the explicit four-CoC composite without treating it as county."""

    source = fact.get("source_id", pd.Series(index=fact.index, dtype="string"))
    hud = fact.loc[source.astype("string").eq("hud_pit_coc_2007_2025")].copy()
    if hud.empty:
        return pd.DataFrame()
    member_ids = {"COC_CA-600", "COC_CA-606", "COC_CA-607", "COC_CA-612"}
    composite_id = "COC_COMPOSITE_LA_FOUR_COC_COMPOSITE"
    members = hud.loc[hud["geo_id"].isin(member_ids)]
    composite = hud.loc[hud["geo_id"].eq(composite_id)]
    key = ["observation_date", "variable_id"]
    member_summary = (
        members.groupby(key, as_index=False)
        .agg(
            member_count=("geo_id", "nunique"),
            nonnull_count=("value", "count"),
            member_sum=("value", "sum"),
        )
    )
    member_summary["expected"] = member_summary["member_sum"].where(
        member_summary["member_count"].eq(4)
        & member_summary["nonnull_count"].eq(4)
    )
    comparison = composite.merge(
        member_summary[key + ["expected"]], on=key, how="outer", indicator=True
    )
    actual = pd.to_numeric(comparison.get("value"), errors="coerce")
    expected = pd.to_numeric(comparison.get("expected"), errors="coerce")
    mismatch = (
        comparison["_merge"].ne("both")
        | comparison.get("county_fips", pd.Series(index=comparison.index)).notna()
        | (expected.isna() & actual.notna())
        | (expected.notna() & (actual.isna() | ~actual.sub(expected).abs().le(1e-9)))
    )
    return comparison.loc[mismatch]


def _construction_proxy_failures(county: pd.DataFrame) -> pd.DataFrame:
    """Validate the four workbook-approved monthly construction proxies.

    Expectations are rebuilt with the same feature helpers and scenario
    constants used by panel construction.  Those helpers use calendar-month
    lags and require every weighted permit input, so the comparison also
    enforces warm-up and missing-source propagation without duplicating the
    lag buckets in QA.
    """

    frames: list[pd.DataFrame] = []
    key_columns = [
        column for column in ("date", "county_fips") if column in county
    ]

    def add(
        source: pd.DataFrame,
        mask: pd.Series,
        reason: str,
        variable: str,
        *,
        actual: pd.Series | None = None,
        expected: pd.Series | None = None,
    ) -> None:
        selected = pd.Series(mask, index=source.index).fillna(False)
        if not selected.any():
            return
        columns = [column for column in key_columns if column in source]
        rows = source.loc[selected, columns].copy()
        rows["variable_id"] = variable
        rows["failure_reason"] = reason
        if actual is not None:
            rows["actual"] = actual.loc[selected].to_numpy()
        if expected is not None:
            rows["expected"] = expected.loc[selected].to_numpy()
        frames.append(rows)

    permit_source_present = (
        CONSTRUCTION_PERMIT_COLUMN in county
        and county[CONSTRUCTION_PERMIT_COLUMN].notna().any()
    )
    if not permit_source_present:
        for column in CONSTRUCTION_PROXY_COLUMNS:
            if column in county:
                add(
                    county,
                    county[column].notna(),
                    "proxy_value_without_permit_source",
                    column,
                    actual=pd.to_numeric(county[column], errors="coerce"),
                )
        return (
            pd.concat(frames, ignore_index=True, sort=False)
            if frames
            else pd.DataFrame()
        )

    missing_outputs = [
        column for column in CONSTRUCTION_PROXY_COLUMNS if column not in county
    ]
    if missing_outputs:
        frames.append(
            pd.DataFrame(
                {
                    "variable_id": missing_outputs,
                    "failure_reason": "missing_proxy_column_with_permits_present",
                }
            )
        )

    required_inputs = {"date", "county_fips", CONSTRUCTION_PERMIT_COLUMN}
    missing_inputs = sorted(required_inputs - set(county.columns))
    if missing_inputs:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "failure_reason": "missing_construction_source_key",
                        "columns": "|".join(missing_inputs),
                    }
                ]
            )
        )
        return pd.concat(frames, ignore_index=True, sort=False)

    try:
        expected = apartment_completion_proxies(county)
        expected = multifamily_under_construction_proxies(expected)
    except ValueError as exc:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "failure_reason": "invalid_construction_source_grid",
                        "detail": str(exc),
                    }
                ]
            )
        )
        return pd.concat(frames, ignore_index=True, sort=False)

    completion_columns = set(APARTMENT_COMPLETION_PROXY_COLUMNS.values())
    stock_columns = set(MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values())
    for column in CONSTRUCTION_PROXY_COLUMNS:
        if column not in county:
            continue
        raw_actual = county[column]
        actual = pd.to_numeric(raw_actual, errors="coerce")
        expected_values = pd.to_numeric(expected[column], errors="coerce")
        add(
            county,
            raw_actual.notna() & actual.isna(),
            "proxy_value_nonnumeric",
            column,
            actual=actual,
            expected=expected_values,
        )
        add(
            county,
            actual.lt(0),
            "negative_proxy_value",
            column,
            actual=actual,
            expected=expected_values,
        )
        add(
            county,
            actual.notna() & expected_values.isna(),
            "proxy_nonnull_without_complete_required_lags",
            column,
            actual=actual,
            expected=expected_values,
        )
        add(
            county,
            actual.isna() & expected_values.notna(),
            "proxy_missing_despite_complete_required_lags",
            column,
            actual=actual,
            expected=expected_values,
        )
        tolerance = (
            FORMULA_ABSOLUTE_TOLERANCE
            + FORMULA_RELATIVE_TOLERANCE * expected_values.abs()
        )
        mismatch = (
            actual.notna()
            & expected_values.notna()
            & actual.sub(expected_values).abs().gt(tolerance)
        )
        add(
            county,
            mismatch,
            (
                "distributed_lag_formula_mismatch"
                if column in completion_columns
                else "cohort_stock_formula_mismatch"
            ),
            column,
            actual=actual,
            expected=expected_values,
        )

    # Independently enforce S_t = S_(t-1) + P_t - C_t for consecutive
    # calendar months where all four terms are defined.  Cohort recomputation
    # above also checks initialization and reinitialization after source gaps.
    for scenario, stock_column in MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.items():
        completion_column = APARTMENT_COMPLETION_PROXY_COLUMNS[scenario]
        if stock_column not in county or completion_column not in county:
            continue
        ordered = county[
            ["date", "county_fips", CONSTRUCTION_PERMIT_COLUMN,
             completion_column, stock_column]
        ].copy()
        ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
        ordered = ordered.sort_values(["county_fips", "date"])
        stock = pd.to_numeric(ordered[stock_column], errors="coerce")
        completion = pd.to_numeric(
            ordered[completion_column], errors="coerce"
        )
        permits = pd.to_numeric(
            ordered[CONSTRUCTION_PERMIT_COLUMN], errors="coerce"
        )
        group = ordered["county_fips"].astype("string")
        previous_stock = stock.groupby(group, sort=False).shift(1)
        previous_date = ordered["date"].groupby(group, sort=False).shift(1)
        current_period = ordered["date"].dt.to_period("M")
        previous_period = previous_date.dt.to_period("M")
        consecutive = current_period.eq(previous_period + 1)
        recurrence = previous_stock + permits - completion
        tolerance = (
            FORMULA_ABSOLUTE_TOLERANCE
            + FORMULA_RELATIVE_TOLERANCE * recurrence.abs()
        )
        mismatch = (
            consecutive
            & stock.notna()
            & previous_stock.notna()
            & permits.notna()
            & completion.notna()
            & stock.sub(recurrence).abs().gt(tolerance)
        )
        add(
            ordered,
            mismatch,
            "stock_flow_recurrence_mismatch",
            stock_column,
            actual=stock,
            expected=recurrence,
        )

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _market_tightness_failures(
    county: pd.DataFrame, minimum_components: int
) -> pd.DataFrame:
    """Recompute the published index and its component-count companion."""

    required = {
        "date",
        "months_of_supply",
        "days_on_market",
        "inventory_growth_yoy",
        "price_drop_share",
        "market_tightness_index",
        "market_tightness_components",
    }
    missing = sorted(required.difference(county.columns))
    if missing:
        return pd.DataFrame(
            [{"failure": "missing_required_columns", "columns": "|".join(missing)}]
        )
    ordered = county.sort_values("date").copy()
    expected_index, expected_components = market_tightness_index(
        ordered, minimum_components
    )
    actual_index = pd.to_numeric(
        ordered["market_tightness_index"], errors="coerce"
    )
    actual_components = pd.to_numeric(
        ordered["market_tightness_components"], errors="coerce"
    ).astype("Int64")
    value_match = (actual_index.isna() & expected_index.isna()) | (
        actual_index.sub(expected_index).abs().le(FORMULA_ABSOLUTE_TOLERANCE)
    )
    component_match = actual_components.eq(expected_components).fillna(False)
    mismatch = ~(value_match & component_match)
    if not mismatch.any():
        return pd.DataFrame()
    failures = ordered.loc[
        mismatch,
        ["date", "market_tightness_index", "market_tightness_components"],
    ].copy()
    failures["expected_market_tightness_index"] = expected_index.loc[mismatch]
    failures["expected_market_tightness_components"] = expected_components.loc[
        mismatch
    ]
    failures["failure"] = "formula_or_component_count_mismatch"
    return failures


def _fact_variable_rows(
    fact: pd.DataFrame, variable_id: str, geo_id: str
) -> pd.DataFrame:
    required = {"variable_id", "geo_id", "observation_date", "value"}
    if fact.empty or not required.issubset(fact.columns):
        return pd.DataFrame(columns=["observation_date", "value"])
    return fact.loc[
        fact["variable_id"].astype("string").eq(variable_id)
        & fact["geo_id"].astype("string").eq(geo_id),
        ["observation_date", "value"],
    ].copy()


def _annual_input_retention_failures(
    fact: pd.DataFrame,
    annual_panel: pd.DataFrame,
    *,
    variable_id: str,
    geo_id: str,
) -> tuple[pd.DataFrame, bool]:
    """Compare an observed annual fact input with its annual panel column."""

    source = _fact_variable_rows(fact, variable_id, geo_id)
    if source.empty:
        if variable_id in annual_panel and annual_panel[variable_id].notna().any():
            return (
                pd.DataFrame(
                    [
                        {
                            "variable_id": variable_id,
                            "failure_reason": "annual_panel_value_without_fact_source",
                        }
                    ]
                ),
                False,
            )
        return pd.DataFrame(), False
    if "date" not in annual_panel or variable_id not in annual_panel:
        missing = [
            column
            for column in ("date", variable_id)
            if column not in annual_panel
        ]
        return (
            pd.DataFrame(
                [
                    {
                        "variable_id": variable_id,
                        "failure_reason": "observed_annual_input_missing_from_panel",
                        "columns": "|".join(missing),
                    }
                ]
            ),
            True,
        )

    source["date"] = pd.to_datetime(
        source.pop("observation_date"), errors="coerce"
    )
    source["expected"] = pd.to_numeric(source.pop("value"), errors="coerce")
    panel_values = annual_panel.loc[
        annual_panel[variable_id].notna(), ["date", variable_id]
    ].copy()
    panel_values["date"] = pd.to_datetime(panel_values["date"], errors="coerce")
    panel_values["actual"] = pd.to_numeric(
        panel_values.pop(variable_id), errors="coerce"
    )
    comparison = source.merge(panel_values, on="date", how="outer", indicator=True)
    tolerance = (
        FORMULA_ABSOLUTE_TOLERANCE
        + FORMULA_RELATIVE_TOLERANCE * comparison["expected"].abs()
    )
    mismatch = (
        comparison["_merge"].ne("both")
        | comparison["expected"].isna()
        | comparison["actual"].isna()
        | comparison["actual"].sub(comparison["expected"]).abs().gt(tolerance)
    )
    failures = comparison.loc[mismatch].copy()
    if not failures.empty:
        failures["variable_id"] = variable_id
        failures["failure_reason"] = "annual_panel_not_equal_to_observed_fact"
    return failures, True


def _pep_annual_rate_failures(
    fact: pd.DataFrame, county_annual: pd.DataFrame
) -> pd.DataFrame:
    """Validate PEP denominator retention and the two approved annual rates."""

    frames: list[pd.DataFrame] = []
    denominator_available: dict[str, bool] = {}
    denominator_year_ranges: dict[str, tuple[int, int] | None] = {}
    for denominator in {
        denominator for _, denominator in PEP_RATE_SPECS.values()
    }:
        denominator_source = _fact_variable_rows(
            fact, denominator, "COUNTY_06037"
        )
        denominator_dates = pd.to_datetime(
            denominator_source.get(
                "observation_date",
                pd.Series(pd.NaT, index=denominator_source.index),
            ),
            errors="coerce",
        )
        denominator_years = denominator_dates.dt.year.dropna().astype(int)
        denominator_year_ranges[denominator] = (
            (int(denominator_years.min()), int(denominator_years.max()))
            if not denominator_years.empty
            else None
        )
        retained, available = _annual_input_retention_failures(
            fact,
            county_annual,
            variable_id=denominator,
            geo_id="COUNTY_06037",
        )
        denominator_available[denominator] = available
        if not retained.empty:
            frames.append(retained)

    for rate, (numerator, denominator) in PEP_RATE_SPECS.items():
        numerator_available = not _fact_variable_rows(
            fact, numerator, "COUNTY_06037"
        ).empty
        sources_available = (
            numerator_available and denominator_available[denominator]
        )
        rate_has_values = rate in county_annual and county_annual[rate].notna().any()
        if not sources_available:
            if rate_has_values:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "variable_id": rate,
                                "failure_reason": "rate_value_without_both_fact_sources",
                            }
                        ]
                    )
                )
            continue

        required = {"date", numerator, denominator}
        missing = sorted(required - set(county_annual.columns))
        if missing:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "variable_id": rate,
                            "failure_reason": "annual_rate_input_missing_from_panel",
                            "columns": "|".join(missing),
                        }
                    ]
                )
            )
            continue

        columns = ["date", numerator, denominator]
        if rate in county_annual:
            columns.append(rate)
        work = county_annual.loc[:, columns].copy()
        work_dates = pd.to_datetime(work["date"], errors="coerce")
        numerator_values = pd.to_numeric(work[numerator], errors="coerce")
        denominator_values = pd.to_numeric(work[denominator], errors="coerce")
        nonpositive = denominator_values.notna() & denominator_values.le(0)
        if nonpositive.any():
            rows = work.loc[nonpositive, ["date", denominator]].copy()
            rows["variable_id"] = rate
            rows["failure_reason"] = "nonpositive_pep_denominator"
            frames.append(rows)
        year_range = denominator_year_ranges[denominator]
        in_denominator_span = (
            work_dates.dt.year.between(*year_range)
            if year_range is not None
            else pd.Series(False, index=work.index)
        )
        missing_denominator = (
            numerator_values.notna()
            & in_denominator_span
            & denominator_values.isna()
        )
        if missing_denominator.any():
            rows = work.loc[missing_denominator, ["date", numerator]].copy()
            rows["variable_id"] = rate
            rows["failure_reason"] = "missing_pep_denominator_for_numerator"
            frames.append(rows)

        expected = numerator_values.div(denominator_values).mul(1000.0).where(
            numerator_values.notna() & denominator_values.gt(0)
        )
        if rate not in county_annual:
            if expected.notna().any():
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "variable_id": rate,
                                "failure_reason": "missing_annual_rate_column",
                            }
                        ]
                    )
                )
            continue
        raw_actual = work[rate]
        actual = pd.to_numeric(raw_actual, errors="coerce")
        tolerance = (
            FORMULA_ABSOLUTE_TOLERANCE
            + FORMULA_RELATIVE_TOLERANCE * expected.abs()
        )
        mismatch = (
            (raw_actual.notna() & actual.isna())
            | (expected.isna() & actual.notna())
            | (expected.notna() & actual.isna())
            | (
                expected.notna()
                & actual.notna()
                & actual.sub(expected).abs().gt(tolerance)
            )
        )
        if mismatch.any():
            rows = work.loc[mismatch, ["date"]].copy()
            rows["variable_id"] = rate
            rows["actual"] = actual.loc[mismatch].to_numpy()
            rows["expected"] = expected.loc[mismatch].to_numpy()
            rows["failure_reason"] = "annual_rate_formula_mismatch"
            frames.append(rows)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _ca_real_premium_failures(
    fact: pd.DataFrame, state_annual: pd.DataFrame
) -> pd.DataFrame:
    """Validate the California CPI deflation and constant-2023 identity."""

    cpi_id = "ca_cpi_u_all_items_annual"
    nominal_id = "ca_homeowners_avg_written_premium"
    real_id = "ca_homeowners_avg_written_premium_real"
    frames: list[pd.DataFrame] = []

    for variable_id in (cpi_id, nominal_id):
        retained, _ = _annual_input_retention_failures(
            fact,
            state_annual,
            variable_id=variable_id,
            geo_id="STATE_06",
        )
        if not retained.empty:
            frames.append(retained)

    cpi_available = not _fact_variable_rows(fact, cpi_id, "STATE_06").empty
    nominal_available = not _fact_variable_rows(
        fact, nominal_id, "STATE_06"
    ).empty
    real_has_values = real_id in state_annual and state_annual[real_id].notna().any()
    if not (cpi_available and nominal_available):
        if real_has_values:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "variable_id": real_id,
                            "failure_reason": "real_premium_without_both_fact_sources",
                        }
                    ]
                )
            )
        return (
            pd.concat(frames, ignore_index=True, sort=False)
            if frames
            else pd.DataFrame()
        )

    required = {"date", cpi_id, nominal_id}
    missing = sorted(required - set(state_annual.columns))
    if missing:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "variable_id": real_id,
                        "failure_reason": "real_premium_input_missing_from_panel",
                        "columns": "|".join(missing),
                    }
                ]
            )
        )
        return pd.concat(frames, ignore_index=True, sort=False)

    columns = ["date", nominal_id, cpi_id]
    if real_id in state_annual:
        columns.append(real_id)
    work = state_annual.loc[:, columns].copy()
    dates = pd.to_datetime(work["date"], errors="coerce")
    nominal = pd.to_numeric(work[nominal_id], errors="coerce")
    cpi = pd.to_numeric(work[cpi_id], errors="coerce")
    base_values = cpi.loc[dates.dt.year.eq(2023) & cpi.gt(0)].drop_duplicates()
    if len(base_values) != 1:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "variable_id": real_id,
                        "failure_reason": "missing_or_ambiguous_positive_2023_cpi_base",
                        "base_value_count": len(base_values),
                    }
                ]
            )
        )
        return pd.concat(frames, ignore_index=True, sort=False)
    base = float(base_values.iloc[0])

    invalid_cpi = cpi.notna() & cpi.le(0)
    if invalid_cpi.any():
        rows = work.loc[invalid_cpi, ["date", cpi_id]].copy()
        rows["variable_id"] = real_id
        rows["failure_reason"] = "nonpositive_cpi_denominator"
        frames.append(rows)
    expected = nominal.mul(base).div(cpi).where(nominal.notna() & cpi.gt(0))
    if real_id not in state_annual:
        if expected.notna().any():
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "variable_id": real_id,
                            "failure_reason": "missing_real_premium_column",
                        }
                    ]
                )
            )
        return pd.concat(frames, ignore_index=True, sort=False)

    raw_actual = work[real_id]
    actual = pd.to_numeric(raw_actual, errors="coerce")
    tolerance = (
        FORMULA_ABSOLUTE_TOLERANCE
        + FORMULA_RELATIVE_TOLERANCE * expected.abs()
    )
    mismatch = (
        (raw_actual.notna() & actual.isna())
        | (expected.isna() & actual.notna())
        | (expected.notna() & actual.isna())
        | (
            expected.notna()
            & actual.notna()
            & actual.sub(expected).abs().gt(tolerance)
        )
    )
    if mismatch.any():
        rows = work.loc[mismatch, ["date"]].copy()
        rows["variable_id"] = real_id
        rows["actual"] = actual.loc[mismatch].to_numpy()
        rows["expected"] = expected.loc[mismatch].to_numpy()
        rows["failure_reason"] = "real_premium_formula_mismatch"
        frames.append(rows)

    base_year = dates.dt.year.eq(2023) & nominal.notna()
    identity_mismatch = base_year & (
        actual.isna()
        | actual.sub(nominal).abs().gt(
            FORMULA_ABSOLUTE_TOLERANCE
            + FORMULA_RELATIVE_TOLERANCE * nominal.abs()
        )
    )
    if identity_mismatch.any():
        rows = work.loc[identity_mismatch, ["date"]].copy()
        rows["variable_id"] = real_id
        rows["actual"] = actual.loc[identity_mismatch].to_numpy()
        rows["expected"] = nominal.loc[identity_mismatch].to_numpy()
        rows["failure_reason"] = "real_premium_2023_base_identity_mismatch"
        frames.append(rows)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _new_annual_variable_frequency_failures(
    fact: pd.DataFrame, panels: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Keep new observed/derived annual fields out of nonannual storage."""

    frames: list[pd.DataFrame] = []
    if "variable_id" in fact:
        observed = fact.loc[
            fact["variable_id"].isin(NEW_ANNUAL_OBSERVED_FACT_IDS)
        ].copy()
        if not observed.empty:
            native = observed.get(
                "native_frequency", pd.Series(pd.NA, index=observed.index)
            ).astype("string")
            final = observed.get(
                "final_frequency", pd.Series(pd.NA, index=observed.index)
            ).astype("string")
            dates = pd.to_datetime(
                observed.get(
                    "observation_date", pd.Series(pd.NaT, index=observed.index)
                ),
                errors="coerce",
            )
            interpolated = observed.get(
                "is_interpolated", pd.Series(pd.NA, index=observed.index)
            ).astype("boolean")
            imputed = observed.get(
                "is_imputed", pd.Series(pd.NA, index=observed.index)
            ).astype("boolean")
            invalid = (
                native.ne("annual").fillna(True)
                | final.ne("annual").fillna(True)
                | dates.isna()
                | dates.dt.strftime("%m-%d").ne("12-31")
                | interpolated.fillna(True)
                | imputed.fillna(True)
            )
            if invalid.any():
                rows = observed.loc[
                    invalid,
                    [
                        column
                        for column in (
                            "observation_date",
                            "geo_id",
                            "variable_id",
                            "native_frequency",
                            "final_frequency",
                            "is_interpolated",
                            "is_imputed",
                        )
                        if column in observed
                    ],
                ].copy()
                rows["failure_reason"] = "observed_annual_fact_frequency_changed"
                frames.append(rows)

        derived = fact.loc[fact["variable_id"].isin(NEW_ANNUAL_DERIVED_IDS)].copy()
        if not derived.empty:
            rows = derived.loc[
                :,
                [
                    column
                    for column in (
                        "observation_date",
                        "geo_id",
                        "variable_id",
                        "native_frequency",
                        "final_frequency",
                    )
                    if column in derived
                ],
            ].copy()
            rows["failure_reason"] = "derived_annual_variable_must_not_be_in_fact"
            frames.append(rows)

    for table_name, panel in panels.items():
        for variable_id, allowed_table in NEW_ANNUAL_PANEL_LOCATIONS.items():
            if variable_id not in panel:
                continue
            if table_name != allowed_table:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "failure_reason": "annual_variable_exposed_outside_approved_panel",
                            }
                        ]
                    )
                )
                continue
            values = panel[variable_id]
            if not values.notna().any():
                continue
            date_column = "date" if "date" in panel else None
            if date_column is None:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "failure_reason": "annual_panel_missing_date",
                            }
                        ]
                    )
                )
                continue
            dates = pd.to_datetime(panel[date_column], errors="coerce")
            invalid_date = values.notna() & (
                dates.isna() | dates.dt.strftime("%m-%d").ne("12-31")
            )
            if invalid_date.any():
                rows = panel.loc[invalid_date, [date_column]].copy()
                rows["table_name"] = table_name
                rows["variable_id"] = variable_id
                rows["failure_reason"] = "annual_panel_value_on_nonannual_date"
                frames.append(rows)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _issue_13_15_annual_placement_failures(
    fact: pd.DataFrame, panels: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Keep Issues 13--15 annual fields in their approved native panels.

    The ACS difference and price-to-income fields are constructed panel-only
    values.  HUD PIT is an observed annual fact that may additionally appear
    only in the native CoC annual panel.  Column presence outside the approved
    destination is itself a structural failure, even when every value happens
    to be null, because such a schema invites silent frequency/geography carry.
    """

    frames: list[pd.DataFrame] = []
    if "variable_id" in fact:
        derived = fact.loc[
            fact["variable_id"].isin(ACS_ISSUE_13_14_PANEL_ONLY_IDS)
        ].copy()
        if not derived.empty:
            rows = derived[
                [
                    column
                    for column in (
                        "observation_date",
                        "geo_id",
                        "variable_id",
                        "native_frequency",
                        "final_frequency",
                    )
                    if column in derived
                ]
            ].copy()
            rows["failure_reason"] = "acs_derived_annual_field_present_in_fact"
            frames.append(rows)

        hud = fact.loc[fact["variable_id"].isin(HUD_PIT_ANNUAL_IDS)].copy()
        if not hud.empty:
            dates = pd.to_datetime(
                hud.get(
                    "observation_date", pd.Series(pd.NaT, index=hud.index)
                ),
                errors="coerce",
            )
            geo_ids = hud.get(
                "geo_id", pd.Series(pd.NA, index=hud.index)
            ).astype("string")
            native = hud.get(
                "native_frequency", pd.Series(pd.NA, index=hud.index)
            ).astype("string")
            final = hud.get(
                "final_frequency", pd.Series(pd.NA, index=hud.index)
            ).astype("string")
            county_fips = hud.get(
                "county_fips", pd.Series(pd.NA, index=hud.index)
            )
            interpolated = hud.get(
                "is_interpolated", pd.Series(pd.NA, index=hud.index)
            ).astype("boolean")
            imputed = hud.get(
                "is_imputed", pd.Series(pd.NA, index=hud.index)
            ).astype("boolean")
            invalid = (
                ~native.str.startswith("annual", na=False)
                | final.ne("annual").fillna(True)
                | dates.isna()
                | dates.dt.strftime("%m-%d").ne("12-31")
                | ~geo_ids.str.fullmatch(
                    r"COC_(?:[A-Z]{2}-\d{3}[A-Z]?|COMPOSITE_[A-Z0-9_-]+)",
                    na=False,
                )
                | county_fips.notna()
                | interpolated.fillna(True)
                | imputed.fillna(True)
            )
            if invalid.any():
                rows = hud.loc[
                    invalid,
                    [
                        column
                        for column in (
                            "observation_date",
                            "geo_id",
                            "county_fips",
                            "variable_id",
                            "native_frequency",
                            "final_frequency",
                            "is_interpolated",
                            "is_imputed",
                        )
                        if column in hud
                    ],
                ].copy()
                rows["failure_reason"] = (
                    "hud_pit_fact_frequency_or_geography_mismatch"
                )
                frames.append(rows)

    expected_geo_patterns = {
        "county_annual": r"COUNTY_06037",
        "zcta_annual": r"ZCTA_\d{5}",
        "coc_annual": (
            r"COC_(?:[A-Z]{2}-\d{3}[A-Z]?|COMPOSITE_[A-Z0-9_-]+)"
        ),
    }
    for table_name, panel in panels.items():
        panel_columns = {str(column) for column in panel.columns}
        for variable_id, approved_table in (
            ISSUE_13_15_APPROVED_PANEL_LOCATIONS.items()
        ):
            associated_columns = sorted(
                column
                for column in panel_columns
                if column == variable_id or column.startswith(f"{variable_id}_")
            )
            if associated_columns and table_name != approved_table:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "column": column,
                                "failure_reason": (
                                    "issue_13_15_field_outside_approved_panel"
                                ),
                            }
                            for column in associated_columns
                        ]
                    )
                )
                continue
            if table_name != approved_table:
                continue
            if variable_id not in panel_columns:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "failure_reason": (
                                    "issue_13_15_field_missing_from_approved_panel"
                                ),
                            }
                        ]
                    )
                )
                continue
            if "date" not in panel:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "failure_reason": "annual_panel_missing_date",
                            }
                        ]
                    )
                )
            else:
                dates = pd.to_datetime(panel["date"], errors="coerce")
                invalid_date = dates.isna() | dates.dt.strftime("%m-%d").ne(
                    "12-31"
                )
                if invalid_date.any():
                    rows = panel.loc[invalid_date, ["date"]].copy()
                    rows["table_name"] = table_name
                    rows["variable_id"] = variable_id
                    rows["failure_reason"] = (
                        "issue_13_15_field_on_nonannual_date"
                    )
                    frames.append(rows)
            if "geo_id" not in panel:
                frames.append(
                    pd.DataFrame(
                        [
                            {
                                "table_name": table_name,
                                "variable_id": variable_id,
                                "failure_reason": "annual_panel_missing_geo_id",
                            }
                        ]
                    )
                )
            else:
                invalid_geo = ~panel["geo_id"].astype("string").str.fullmatch(
                    expected_geo_patterns[approved_table], na=False
                )
                if invalid_geo.any():
                    rows = panel.loc[invalid_geo, ["geo_id"]].copy()
                    rows["table_name"] = table_name
                    rows["variable_id"] = variable_id
                    rows["failure_reason"] = (
                        "issue_13_15_field_in_wrong_geography_panel"
                    )
                    frames.append(rows)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _hud_pit_annual_panel_failures(
    fact: pd.DataFrame, coc_annual: pd.DataFrame
) -> pd.DataFrame:
    """Reconcile every California HUD PIT geography-year into the CoC panel.

    Expected keys come from fact rows rather than non-null values.  This makes
    an explicit all-null provider/composite year a required row instead of an
    accidental casualty of wide-table pivot semantics.
    """

    required_fact = {
        "observation_date",
        "geo_id",
        "state_fips",
        "variable_id",
        "value",
    }
    required_panel = {"date", "geo_id", *HUD_PIT_ANNUAL_IDS}
    missing_fact = sorted(required_fact - set(fact.columns))
    missing_panel = sorted(required_panel - set(coc_annual.columns))
    if missing_fact or missing_panel:
        return pd.DataFrame(
            [
                {
                    "failure_reason": "missing_columns",
                    "fact_columns": "|".join(missing_fact),
                    "panel_columns": "|".join(missing_panel),
                }
            ]
        )

    hud = fact.loc[
        fact["variable_id"].isin(HUD_PIT_ANNUAL_IDS)
        & fact["state_fips"].astype("string").eq("06")
    ].copy()
    if hud.empty:
        return pd.DataFrame(
            [{"failure_reason": "no_california_hud_pit_fact_rows"}]
        )
    hud["date"] = pd.to_datetime(hud["observation_date"], errors="coerce")
    panel = coc_annual.copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    expected_keys = hud[["date", "geo_id"]].drop_duplicates()
    actual_keys = panel[["date", "geo_id"]].drop_duplicates()
    key_comparison = expected_keys.merge(
        actual_keys,
        on=["date", "geo_id"],
        how="left",
        indicator=True,
    )
    frames: list[pd.DataFrame] = []
    missing_keys = key_comparison.loc[key_comparison["_merge"].ne("both")].copy()
    if not missing_keys.empty:
        missing_keys["failure_reason"] = "hud_pit_geography_year_missing_from_panel"
        frames.append(missing_keys)

    for variable_id in sorted(HUD_PIT_ANNUAL_IDS):
        expected = hud.loc[
            hud["variable_id"].eq(variable_id), ["date", "geo_id", "value"]
        ].rename(columns={"value": "expected_value"})
        actual = panel[["date", "geo_id", variable_id]].rename(
            columns={variable_id: "actual_value"}
        )
        comparison = expected.merge(
            actual,
            on=["date", "geo_id"],
            how="left",
            indicator=True,
        )
        expected_value = pd.to_numeric(
            comparison["expected_value"], errors="coerce"
        )
        actual_value = pd.to_numeric(
            comparison["actual_value"], errors="coerce"
        )
        mismatch = comparison["_merge"].ne("both") | (
            expected_value.isna() ^ actual_value.isna()
        ) | (
            expected_value.notna()
            & actual_value.notna()
            & expected_value.sub(actual_value).abs().gt(1e-12)
        )
        if mismatch.any():
            rows = comparison.loc[mismatch].copy()
            rows["variable_id"] = variable_id
            rows["failure_reason"] = "hud_pit_panel_value_mismatch"
            frames.append(rows)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _ca_doi_yoy_failures(state_annual: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date",
        "geo_id",
        "ca_homeowners_avg_written_premium",
        "ca_homeowners_avg_written_premium_yoy",
    }
    if state_annual.empty or not required.issubset(state_annual.columns):
        return pd.DataFrame()
    work = state_annual.sort_values(["geo_id", "date"]).copy()
    premium = pd.to_numeric(
        work["ca_homeowners_avg_written_premium"], errors="coerce"
    )
    prior = premium.groupby(work["geo_id"], sort=False).shift(1)
    prior_date = pd.to_datetime(work["date"]).groupby(
        work["geo_id"], sort=False
    ).shift(1)
    consecutive = (
        pd.to_datetime(work["date"]).dt.year - prior_date.dt.year
    ).eq(1)
    expected = premium.div(prior).sub(1).where(
        consecutive & premium.gt(0) & prior.gt(0)
    )
    actual = pd.to_numeric(
        work["ca_homeowners_avg_written_premium_yoy"], errors="coerce"
    )
    mismatch = (expected.isna() & actual.notna()) | (
        expected.notna()
        & (actual.isna() | ~actual.sub(expected).abs().le(1e-12))
    )
    return work.loc[mismatch]


def _ca_doi_monthly_control_failures(
    fact: pd.DataFrame, state_monthly: pd.DataFrame
) -> pd.DataFrame:
    """Validate the explicitly flagged calendar-year premium step control."""

    annual_id = "ca_homeowners_avg_written_premium"
    monthly_id = "ca_homeowners_avg_written_premium_monthly_control"
    annual = fact.loc[
        fact["geo_id"].eq("STATE_06")
        & fact["variable_id"].eq(annual_id)
        & fact["final_frequency"].eq("annual")
    ].copy()
    monthly = fact.loc[fact["variable_id"].eq(monthly_id)].copy()
    if annual.empty:
        return pd.DataFrame([{"failure_scope": "missing_annual_source"}])

    annual = (
        annual.sort_values(["source_vintage", "retrieved_at"])
        .drop_duplicates(["observation_date", "geo_id"], keep="last")
        .copy()
    )
    expected_parts: list[pd.DataFrame] = []
    for month in range(1, 13):
        expected = annual[
            ["observation_date", "geo_id", "value", "source_vintage"]
        ].rename(
            columns={
                "observation_date": "expected_source_annual_date",
                "value": "expected_value",
            }
        )
        expected["observation_date"] = pd.to_datetime(
            {
                "year": pd.to_datetime(
                    expected["expected_source_annual_date"]
                ).dt.year,
                "month": month,
                "day": 1,
            }
        ) + pd.offsets.MonthEnd(0)
        expected_parts.append(expected)
    expected = pd.concat(expected_parts, ignore_index=True)

    fact_comparison = expected.merge(
        monthly,
        on=["observation_date", "geo_id", "source_vintage"],
        how="outer",
        indicator=True,
    )
    actual_value = pd.to_numeric(fact_comparison.get("value"), errors="coerce")
    expected_value = pd.to_numeric(
        fact_comparison.get("expected_value"), errors="coerce"
    )
    actual_source_date = pd.to_datetime(
        fact_comparison.get("source_observation_date"), errors="coerce"
    )
    expected_source_date = pd.to_datetime(
        fact_comparison.get("expected_source_annual_date"), errors="coerce"
    )
    fact_mismatch = (
        fact_comparison["_merge"].ne("both")
        | actual_value.isna()
        | ~actual_value.sub(expected_value).abs().le(1e-12)
        | ~fact_comparison.get("geo_id", pd.Series(dtype="string")).eq(
            "STATE_06"
        )
        | ~fact_comparison.get(
            "native_frequency", pd.Series(dtype="string")
        ).eq("annual")
        | ~fact_comparison.get(
            "final_frequency", pd.Series(dtype="string")
        ).eq("monthly")
        | fact_comparison.get(
            "is_interpolated", pd.Series(dtype="boolean")
        ).fillna(True)
        | fact_comparison.get(
            "is_imputed", pd.Series(dtype="boolean")
        ).fillna(True)
        | ~fact_comparison.get(
            "quality_flag", pd.Series(dtype="string")
        ).astype("string").str.contains(
            "carried_annual_control", regex=False, na=False
        )
        | actual_source_date.ne(expected_source_date)
    )
    fact_failures = fact_comparison.loc[fact_mismatch].copy()
    fact_failures["failure_scope"] = "fact"

    panel_expected = expected.rename(columns={"observation_date": "date"})
    panel_comparison = panel_expected.merge(
        state_monthly,
        on=["date", "geo_id", "source_vintage"],
        how="outer",
        indicator=True,
    )
    panel_value = pd.to_numeric(
        panel_comparison.get(monthly_id), errors="coerce"
    )
    panel_expected_value = pd.to_numeric(
        panel_comparison.get("expected_value"), errors="coerce"
    )
    panel_source_date = pd.to_datetime(
        panel_comparison.get("source_annual_date"), errors="coerce"
    )
    panel_expected_source_date = pd.to_datetime(
        panel_comparison.get("expected_source_annual_date"), errors="coerce"
    )
    panel_mismatch = (
        panel_comparison["_merge"].ne("both")
        | panel_value.isna()
        | ~panel_value.sub(panel_expected_value).abs().le(1e-12)
        | ~panel_comparison.get(
            "native_frequency", pd.Series(dtype="string")
        ).eq("annual")
        | ~panel_comparison.get(
            "final_frequency", pd.Series(dtype="string")
        ).eq("monthly")
        | panel_comparison.get(
            "is_interpolated", pd.Series(dtype="boolean")
        ).fillna(True)
        | panel_comparison.get(
            "is_observed", pd.Series(dtype="boolean")
        ).fillna(True)
        | ~panel_comparison.get(
            "is_constructed", pd.Series(dtype="boolean")
        ).fillna(False)
        | ~panel_comparison.get(
            "is_mixed_frequency_control", pd.Series(dtype="boolean")
        ).fillna(False)
        | ~panel_comparison.get(
            "is_carried_annual_control", pd.Series(dtype="boolean")
        ).fillna(False)
        | panel_source_date.ne(panel_expected_source_date)
        | panel_comparison.get(
            "source_url", pd.Series(dtype="string")
        ).isna()
        | panel_comparison.get(
            "vintage_date", pd.Series(dtype="string")
        ).isna()
    )
    panel_failures = panel_comparison.loc[panel_mismatch].copy()
    panel_failures["failure_scope"] = "state_monthly_controls"
    return pd.concat([fact_failures, panel_failures], ignore_index=True)


def _hud_usps_county_coverage_failures(fact: pd.DataFrame) -> pd.DataFrame:
    """Require the exact 58-county California set for each HUD-USPS series."""

    source = fact.get("source_id", pd.Series(index=fact.index, dtype="string"))
    hud_columns = [
        column
        for column in (
            "observation_date",
            "variable_id",
            "geo_id",
            "county_fips",
        )
        if column in fact
    ]
    hud = fact.loc[
        source.astype("string").eq("hud_usps_ncwm_quarterly"),
        hud_columns,
    ]
    columns = [
        "observation_date",
        "variable_id",
        "county_count",
        "missing_counties",
        "unexpected_counties",
    ]
    if hud.empty:
        return pd.DataFrame(columns=columns)

    expected = set(CALIFORNIA_COUNTY_FIPS)
    county = hud.loc[
        hud["geo_id"].astype("string").str.startswith("COUNTY_", na=False)
    ]
    county_sets = {
        key: set(values.dropna().astype(str))
        for key, values in county.groupby(
            ["observation_date", "variable_id"],
            dropna=False,
            sort=False,
        )["county_fips"]
    }
    rows: list[dict[str, object]] = []
    for (date, variable_id), _ in hud.groupby(
        ["observation_date", "variable_id"], dropna=False, sort=True
    ):
        # Match the prior boolean-filter behavior for null grouping keys: an
        # equality comparison against a null key selects no county rows.
        group_counties = (
            set()
            if pd.isna(date) or pd.isna(variable_id)
            else county_sets.get((date, variable_id), set())
        )
        if group_counties != expected:
            rows.append(
                {
                    "observation_date": date,
                    "variable_id": variable_id,
                    "county_count": len(group_counties),
                    "missing_counties": "|".join(sorted(expected - group_counties)),
                    "unexpected_counties": "|".join(
                        sorted(group_counties - expected)
                    ),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _acs_rental_availability_failures(
    county_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Return structural or formula failures for the annual ACS proxy."""

    required = {
        "date",
        "acs_renter_occupied_units",
        "acs_renter_occupied_units_moe",
        "acs_for_rent_vacant_units",
        "acs_for_rent_vacant_units_moe",
        "acs_rental_availability_rate",
        "acs_rental_availability_quality_flag",
    }
    missing = sorted(required - set(county_annual.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )

    work = county_annual[list(sorted(required))].copy()
    numeric_columns = [
        "acs_renter_occupied_units",
        "acs_renter_occupied_units_moe",
        "acs_for_rent_vacant_units",
        "acs_for_rent_vacant_units_moe",
        "acs_rental_availability_rate",
    ]
    for column in numeric_columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    rate = work["acs_rental_availability_rate"]
    renter = work["acs_renter_occupied_units"]
    renter_moe = work["acs_renter_occupied_units_moe"]
    for_rent = work["acs_for_rent_vacant_units"]
    for_rent_moe = work["acs_for_rent_vacant_units_moe"]
    denominator = renter + for_rent
    expected = for_rent / denominator
    high_relative_moe = (
        ((renter.eq(0) & renter_moe.gt(0)) | (renter.gt(0) & renter_moe.ge(renter)))
        | (
            (for_rent.eq(0) & for_rent_moe.gt(0))
            | (for_rent.gt(0) & for_rent_moe.ge(for_rent))
        )
    )
    quality = work["acs_rental_availability_quality_flag"].astype("string")
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False)].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    add("rate_outside_unit_interval", rate.notna() & ~rate.between(0, 1))
    add(
        "formula_mismatch",
        rate.notna() & expected.notna() & rate.sub(expected).abs().gt(1e-12),
    )
    add("unsuppressed_high_relative_moe", rate.notna() & high_relative_moe)
    add("nonnull_rate_without_ok_flag", rate.notna() & quality.ne("ok"))
    add("ok_flag_without_rate", quality.eq("ok") & rate.isna())
    if not rate.notna().any():
        frames.append(
            pd.DataFrame(
                [{"failure_reason": "no_constructed_rate_observation"}]
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _acs_county_price_to_income_failures(
    county_annual: pd.DataFrame,
    expected_years: set[int] | None = None,
) -> pd.DataFrame:
    """Validate the matched-vintage county ACS price-to-income construction."""

    value_column = "county_annual_price_to_income_acs"
    quality_column = f"{value_column}_quality_flag"
    required = {
        "date",
        "geo_id",
        "acs_median_home_value",
        "acs_median_household_income",
        value_column,
        quality_column,
    }
    missing = sorted(required - set(county_annual.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )

    work = county_annual[list(sorted(required))].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    home_value = pd.to_numeric(work["acs_median_home_value"], errors="coerce")
    income = pd.to_numeric(
        work["acs_median_household_income"], errors="coerce"
    )
    reported = pd.to_numeric(work[value_column], errors="coerce")
    valid = home_value.notna() & home_value.ge(0) & income.notna() & income.gt(0)
    expected = (home_value / income).where(valid)
    quality = work[quality_column].astype("string")
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False)].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    add(
        "county_price_to_income_wrong_geography",
        ~work["geo_id"].astype("string").eq("COUNTY_06037"),
    )
    add(
        "county_price_to_income_nonannual_date",
        work["date"].isna()
        | work["date"].dt.month.ne(12)
        | work["date"].dt.day.ne(31),
    )
    add("missing_eligible_county_price_to_income", valid & reported.isna())
    add(
        "county_price_to_income_without_valid_inputs",
        reported.notna() & ~valid,
    )
    add(
        "county_price_to_income_formula_mismatch",
        reported.notna()
        & expected.notna()
        & reported.sub(expected).abs().gt(1e-12),
    )
    matched_flag = "matched_acs_5year_vintage_ratio"
    add(
        "nonnull_county_price_to_income_without_matched_vintage_flag",
        reported.notna() & quality.ne(matched_flag),
    )
    add(
        "matched_vintage_flag_without_valid_county_ratio",
        quality.eq(matched_flag) & (~valid | reported.isna()),
    )
    if expected_years is not None:
        target = work.loc[
            work["date"].dt.year.isin(expected_years)
            & work["geo_id"].astype("string").eq("COUNTY_06037")
        ]
        actual_years = set(target["date"].dt.year.dropna().astype(int))
        if actual_years != expected_years:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": "unexpected_county_price_to_income_year_grid",
                            "expected_years": "|".join(
                                str(year) for year in sorted(expected_years)
                            ),
                            "actual_years": "|".join(
                                str(year) for year in sorted(actual_years)
                            ),
                        }
                    ]
                )
            )
        unexpected_output = reported.notna() & ~work["date"].dt.year.isin(
            expected_years
        )
        add("county_price_to_income_outside_expected_year_grid", unexpected_output)

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def _acs_occupied_change_failures(
    county_annual: pd.DataFrame,
    county_quarterly: pd.DataFrame | None = None,
    expected_input_years: set[int] | None = None,
) -> pd.DataFrame:
    """Validate the consecutive-vintage ACS renter-occupied difference."""

    required = {
        "date",
        "geo_id",
        "acs_renter_occupied_units",
        "net_change_occupied_apartments_annual",
        "net_change_occupied_apartments_annual_quality_flag",
    }
    missing = sorted(required - set(county_annual.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )
    work = county_annual[list(sorted(required))].sort_values(["geo_id", "date"])
    current = pd.to_numeric(work["acs_renter_occupied_units"], errors="coerce")
    reported = pd.to_numeric(
        work["net_change_occupied_apartments_annual"], errors="coerce"
    )
    dates = pd.to_datetime(work["date"], errors="coerce")
    prior = current.groupby(work["geo_id"], sort=False).shift(1)
    prior_dates = dates.groupby(work["geo_id"], sort=False).shift(1)
    consecutive = dates.dt.year.sub(prior_dates.dt.year).eq(1)
    expected = current - prior
    eligible = consecutive & current.notna() & prior.notna()
    quality = work[
        "net_change_occupied_apartments_annual_quality_flag"
    ].astype("string")
    valid_flag = "overlapping_5year_vintage_difference_not_one_year_flow"
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False)].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    add("missing_eligible_change", eligible & reported.isna())
    add("change_without_consecutive_vintages", reported.notna() & ~eligible)
    add(
        "formula_mismatch",
        reported.notna() & expected.notna() & reported.sub(expected).abs().gt(1e-12),
    )
    add("nonnull_change_without_overlap_flag", reported.notna() & quality.ne(valid_flag))
    if not reported.notna().any():
        frames.append(pd.DataFrame([{"failure_reason": "no_constructed_change"}]))
    if expected_input_years is not None:
        actual_input_years = set(dates.loc[current.notna()].dt.year.astype(int))
        if actual_input_years != expected_input_years:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": "unexpected_input_year_grid",
                            "expected_years": "|".join(
                                str(year) for year in sorted(expected_input_years)
                            ),
                            "actual_years": "|".join(
                                str(year) for year in sorted(actual_input_years)
                            ),
                        }
                    ]
                )
            )
        expected_change_years = expected_input_years - {min(expected_input_years)}
        actual_change_years = set(dates.loc[reported.notna()].dt.year.astype(int))
        if actual_change_years != expected_change_years:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": "unexpected_change_year_grid",
                            "expected_years": "|".join(
                                str(year) for year in sorted(expected_change_years)
                            ),
                            "actual_years": "|".join(
                                str(year) for year in sorted(actual_change_years)
                            ),
                        }
                    ]
                )
            )
    if (
        county_quarterly is not None
        and "net_change_occupied_apartments_annual" in county_quarterly
    ):
        quarterly_values = pd.to_numeric(
            county_quarterly["net_change_occupied_apartments_annual"],
            errors="coerce",
        )
        if quarterly_values.notna().any():
            quarterly_rows = county_quarterly.loc[
                quarterly_values.notna()
            ].copy()
            quarterly_rows["failure_reason"] = "annual_change_expanded_to_quarterly"
            frames.append(quarterly_rows)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _acs_noncontrolled_exposure_failures(
    annual_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Validate ACS structural shares and noncontrolled-exposure bounds."""

    frames: list[pd.DataFrame] = []
    if "noncontrolled_exposure_share" in annual_panel.columns:
        frames.append(
            pd.DataFrame(
                [{"failure_reason": "exact_noncontrolled_point_column_present"}]
            )
        )

    b25032_components = tuple(
        component
        for components in ACS_B25032_STRUCTURE_GROUPS.values()
        for component in components
    )
    b25127_band_columns = {
        band: tuple(
            f"acs_b25127_renter_{band}_{suffix}"
            for suffix in ("total", *ACS_B25127_STRUCTURE_SUFFIXES)
        )
        for band in ("recent_lower_bound", "cutoff_mixed")
    }
    b25127_columns = (
        "acs_b25127_renter_total",
        *b25127_band_columns["recent_lower_bound"],
        *b25127_band_columns["cutoff_mixed"],
    )
    required = {
        "date",
        "geo_id",
        "acs_b25032_renter_total",
        *b25032_components,
        *b25127_columns,
        *ACS_NONCONTROLLED_DERIVED_COLUMNS,
    }
    missing = sorted(required - set(annual_panel.columns))
    if missing:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "failure_reason": "missing_columns",
                        "columns": "|".join(missing),
                    }
                ]
            )
        )
        return pd.concat(frames, ignore_index=True, sort=False)

    work = annual_panel[list(sorted(required))].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    numeric_columns = required - {"date", "geo_id"}
    for column in numeric_columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    def add(reason: str, mask: pd.Series) -> None:
        selected = mask.fillna(False)
        if selected.any():
            rows = work.loc[selected].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    tolerance = ACS_COMPONENT_RECONCILIATION_TOLERANCE
    b25032_input_columns = ("acs_b25032_renter_total", *b25032_components)
    b25032 = work[list(b25032_input_columns)]
    b25032_any = b25032.notna().any(axis=1)
    b25032_complete = b25032.notna().all(axis=1)
    b25032_nonnegative = b25032.ge(0).all(axis=1)
    b25032_total = work["acs_b25032_renter_total"]
    b25032_component_sum = work[list(b25032_components)].sum(
        axis=1, min_count=len(b25032_components)
    )
    b25032_reconciled = b25032_component_sum.sub(b25032_total).abs().le(
        tolerance
    )
    add("incomplete_b25032_structure_inputs", b25032_any & ~b25032_complete)
    add(
        "negative_b25032_structure_input",
        b25032_complete & ~b25032_nonnegative,
    )
    add(
        "b25032_structure_components_do_not_reconcile",
        b25032_complete & b25032_nonnegative & ~b25032_reconciled,
    )
    structure_valid = (
        b25032_complete
        & b25032_nonnegative
        & b25032_total.gt(0)
        & b25032_reconciled
    )
    for output_column, input_columns in ACS_B25032_STRUCTURE_GROUPS.items():
        reported = work[output_column]
        numerator = work[list(input_columns)].sum(
            axis=1, min_count=len(input_columns)
        )
        expected = (numerator / b25032_total).where(structure_valid)
        add(f"missing_eligible_{output_column}", structure_valid & reported.isna())
        add(
            f"{output_column}_without_valid_inputs",
            reported.notna() & ~structure_valid,
        )
        add(
            f"{output_column}_formula_mismatch",
            reported.notna()
            & expected.notna()
            & reported.sub(expected).abs().gt(tolerance),
        )
        add(
            f"{output_column}_outside_unit_interval",
            reported.notna() & ~reported.between(0, 1),
        )
    structure_shares = work[list(ACS_B25032_STRUCTURE_GROUPS)]
    structure_share_any = structure_shares.notna().any(axis=1)
    structure_share_complete = structure_shares.notna().all(axis=1)
    add(
        "incomplete_b25032_structure_share_set",
        structure_share_any & ~structure_share_complete,
    )
    add(
        "b25032_structure_shares_do_not_sum_to_one",
        structure_share_complete
        & structure_shares.sum(axis=1).sub(1.0).abs().gt(tolerance),
    )

    b25127 = work[list(b25127_columns)]
    b25127_any = b25127.notna().any(axis=1)
    b25127_complete = b25127.notna().all(axis=1)
    b25127_nonnegative = b25127.ge(0).all(axis=1)
    b25127_total = work["acs_b25127_renter_total"]
    recent_total = work["acs_b25127_renter_recent_lower_bound_total"]
    mixed_total = work["acs_b25127_renter_cutoff_mixed_total"]
    recent_detail = [
        f"acs_b25127_renter_recent_lower_bound_{suffix}"
        for suffix in ACS_B25127_STRUCTURE_SUFFIXES
    ]
    mixed_detail = [
        f"acs_b25127_renter_cutoff_mixed_{suffix}"
        for suffix in ACS_B25127_STRUCTURE_SUFFIXES
    ]
    recent_detail_sum = work[recent_detail].sum(
        axis=1, min_count=len(recent_detail)
    )
    mixed_detail_sum = work[mixed_detail].sum(
        axis=1, min_count=len(mixed_detail)
    )
    recent_reconciled = recent_detail_sum.sub(recent_total).abs().le(tolerance)
    mixed_reconciled = mixed_detail_sum.sub(mixed_total).abs().le(tolerance)
    age_bands_within_total = recent_total.add(mixed_total).le(
        b25127_total + tolerance
    )
    add("incomplete_b25127_structure_inputs", b25127_any & ~b25127_complete)
    add(
        "negative_b25127_structure_input",
        b25127_complete & ~b25127_nonnegative,
    )
    add(
        "b25127_recent_components_do_not_reconcile",
        b25127_complete & b25127_nonnegative & ~recent_reconciled,
    )
    add(
        "b25127_mixed_components_do_not_reconcile",
        b25127_complete & b25127_nonnegative & ~mixed_reconciled,
    )
    add(
        "b25127_recent_and_mixed_exceed_renter_total",
        b25127_complete & b25127_nonnegative & ~age_bands_within_total,
    )
    recent_valid = (
        b25127_complete
        & b25127_nonnegative
        & b25127_total.gt(0)
        & recent_reconciled
        & mixed_reconciled
        & age_bands_within_total
    )
    expected_recent_lower = (recent_total / b25127_total).where(recent_valid)
    expected_recent_upper = (
        recent_total.add(mixed_total) / b25127_total
    ).where(recent_valid)
    for output_column, expected in zip(
        ACS_RECENT_CONSTRUCTION_SHARE_COLUMNS,
        (expected_recent_lower, expected_recent_upper),
    ):
        reported = work[output_column]
        add(f"missing_eligible_{output_column}", recent_valid & reported.isna())
        add(
            f"{output_column}_without_valid_inputs",
            reported.notna() & ~recent_valid,
        )
        add(
            f"{output_column}_formula_mismatch",
            reported.notna()
            & expected.notna()
            & reported.sub(expected).abs().gt(tolerance),
        )
        add(
            f"{output_column}_outside_unit_interval",
            reported.notna() & ~reported.between(0, 1),
        )
    reported_recent_lower = work[ACS_RECENT_CONSTRUCTION_SHARE_COLUMNS[0]]
    reported_recent_upper = work[ACS_RECENT_CONSTRUCTION_SHARE_COLUMNS[1]]
    add(
        "recent_construction_bounds_reversed",
        reported_recent_lower.notna()
        & reported_recent_upper.notna()
        & reported_recent_lower.gt(reported_recent_upper),
    )

    cross_source_ready = structure_valid & recent_valid
    source_totals_match = b25032_total.sub(b25127_total).abs().le(tolerance)
    add(
        "b25032_b25127_renter_totals_do_not_match",
        cross_source_ready & ~source_totals_match,
    )
    b25032_one_unit = work[
        list(
            ACS_B25032_STRUCTURE_GROUPS[
                "acs_renter_occupied_1_unit_share"
            ]
        )
    ].sum(axis=1, min_count=2)
    recent_one_unit = work[
        "acs_b25127_renter_recent_lower_bound_1_unit"
    ]
    mixed_one_unit = work["acs_b25127_renter_cutoff_mixed_1_unit"]
    age_band_one_unit = recent_one_unit.add(mixed_one_unit)
    one_unit_overlap_valid = age_band_one_unit.le(b25032_one_unit + tolerance)
    add(
        "b25127_age_band_one_unit_exceeds_b25032_one_unit_total",
        cross_source_ready & ~one_unit_overlap_valid,
    )

    dates = work["date"]
    proxy_source_valid = (
        cross_source_ready & source_totals_match & one_unit_overlap_valid
    )
    proxy_eligible = (
        proxy_source_valid
        & dates.dt.year.ge(ACS_NONCONTROLLED_STRUCTURAL_PROXY_START_YEAR)
    )
    union_numerator = (
        recent_total
        .add(mixed_total)
        .add(b25032_one_unit)
        .sub(recent_one_unit)
        .sub(mixed_one_unit)
    )
    expected_proxy_lower = (recent_total / b25127_total).where(proxy_eligible)
    expected_proxy_upper = (union_numerator / b25127_total).clip(upper=1.0).where(
        proxy_eligible
    )
    reported_proxy_lower = work[ACS_NONCONTROLLED_EXPOSURE_COLUMNS[0]]
    reported_proxy_upper = work[ACS_NONCONTROLLED_EXPOSURE_COLUMNS[1]]
    for output_column, reported, expected in (
        (
            ACS_NONCONTROLLED_EXPOSURE_COLUMNS[0],
            reported_proxy_lower,
            expected_proxy_lower,
        ),
        (
            ACS_NONCONTROLLED_EXPOSURE_COLUMNS[1],
            reported_proxy_upper,
            expected_proxy_upper,
        ),
    ):
        add(f"missing_eligible_{output_column}", proxy_eligible & reported.isna())
        add(
            f"{output_column}_without_valid_inputs",
            reported.notna() & ~proxy_eligible,
        )
        add(
            f"{output_column}_formula_mismatch",
            reported.notna()
            & expected.notna()
            & reported.sub(expected).abs().gt(tolerance),
        )
        add(
            f"{output_column}_outside_unit_interval",
            reported.notna() & ~reported.between(0, 1),
        )
    proxy_any = reported_proxy_lower.notna() | reported_proxy_upper.notna()
    add(
        "noncontrolled_structural_proxy_before_2020",
        proxy_any
        & dates.dt.year.lt(ACS_NONCONTROLLED_STRUCTURAL_PROXY_START_YEAR),
    )
    add(
        "noncontrolled_structural_proxy_bounds_reversed",
        reported_proxy_lower.notna()
        & reported_proxy_upper.notna()
        & reported_proxy_lower.gt(reported_proxy_upper),
    )
    add(
        "noncontrolled_structural_proxy_on_nonannual_date",
        proxy_any
        & (
            dates.isna()
            | dates.dt.month.ne(12)
            | dates.dt.day.ne(31)
        ),
    )

    geo_ids = annual_panel["geo_id"].astype("string")
    if geo_ids.str.startswith("ZCTA_", na=False).any():
        if {"zcta", "geography_type"}.issubset(annual_panel.columns):
            zctas = annual_panel["zcta"].astype("string")
            add(
                "noncontrolled_proxy_zcta_identity_mismatch",
                zctas.ne(geo_ids.str.removeprefix("ZCTA_")),
            )
            add(
                "noncontrolled_proxy_zcta_geography_type_mismatch",
                annual_panel["geography_type"].astype("string").ne("zcta"),
            )
        else:
            missing_identity = sorted(
                {"zcta", "geography_type"} - set(annual_panel.columns)
            )
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": "missing_zcta_identity_columns",
                            "columns": "|".join(missing_identity),
                        }
                    ]
                )
            )
        if "postal_zip" in annual_panel.columns:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": (
                                "noncontrolled_proxy_zcta_has_postal_zip_column"
                            )
                        }
                    ]
                )
            )

    if (
        not reported_proxy_lower.notna().any()
        or not reported_proxy_upper.notna().any()
    ):
        frames.append(
            pd.DataFrame(
                [{"failure_reason": "no_constructed_noncontrolled_proxy_bounds"}]
            )
        )
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _acs_noncontrolled_schema_failures(
    panels: dict[str, pd.DataFrame],
    annual_panel_names: set[str] | None = None,
) -> pd.DataFrame:
    """Reject an exact point or expansion of annual ACS structural outputs."""

    annual_names = annual_panel_names or {"county_annual", "zcta_annual"}
    rows: list[dict[str, object]] = []
    exact_point = "noncontrolled_exposure_share"
    derived = set(ACS_NONCONTROLLED_DERIVED_COLUMNS)
    for panel_name, panel in panels.items():
        if exact_point in panel.columns:
            rows.append(
                {
                    "panel": panel_name,
                    "failure_reason": "exact_noncontrolled_point_column_present",
                    "column": exact_point,
                }
            )
        if panel_name not in annual_names:
            for column in sorted(derived & set(panel.columns)):
                rows.append(
                    {
                        "panel": panel_name,
                        "failure_reason": (
                            "structural_column_outside_county_or_zcta_annual_panel"
                        ),
                        "column": column,
                    }
                )
        if "variable_id" not in panel.columns:
            continue
        variable_ids = panel["variable_id"].astype("string")
        exact_rows = variable_ids.eq(exact_point)
        if exact_rows.any():
            rows.append(
                {
                    "panel": panel_name,
                    "failure_reason": "exact_noncontrolled_point_variable_present",
                    "column": exact_point,
                    "row_count": int(exact_rows.sum()),
                }
            )
        derived_rows = variable_ids.isin(derived)
        if "final_frequency" in panel.columns:
            wrong_frequency = derived_rows & ~panel["final_frequency"].eq("annual")
            if wrong_frequency.any():
                rows.append(
                    {
                        "panel": panel_name,
                        "failure_reason": "structural_variable_has_nonannual_frequency",
                        "row_count": int(wrong_frequency.sum()),
                    }
                )
    return pd.DataFrame(rows)


def _noncontrolled_rent_growth_sensitivity_failures(
    annual_panel: pd.DataFrame,
    monthly_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Validate exact-December ZORI x ACS structural-bound sensitivities."""

    required_annual = {
        "date",
        *ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
        *NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS,
    }
    missing_annual = sorted(required_annual - set(annual_panel.columns))
    missing_monthly = sorted({"date", "zori_growth_yoy"} - set(monthly_panel.columns))
    if missing_annual or missing_monthly:
        return pd.DataFrame(
            [
                {
                    "failure_reason": "missing_columns",
                    "annual_columns": "|".join(missing_annual),
                    "monthly_columns": "|".join(missing_monthly),
                }
            ]
        )

    annual = annual_panel[list(sorted(required_annual))].copy()
    annual["date"] = pd.to_datetime(annual["date"], errors="coerce")
    monthly = monthly_panel[["date", "zori_growth_yoy"]].copy()
    monthly["date"] = pd.to_datetime(monthly["date"], errors="coerce")
    monthly = monthly.loc[
        monthly["date"].dt.month.eq(12)
        & monthly["date"].dt.day.eq(31)
    ].copy()
    frames: list[pd.DataFrame] = []
    if monthly["date"].duplicated().any():
        duplicate = monthly.loc[monthly["date"].duplicated(False)].copy()
        duplicate["failure_reason"] = "duplicate_december_zori_growth"
        frames.append(duplicate)
    monthly = monthly.drop_duplicates("date", keep="last")
    annual = annual.merge(monthly, on="date", how="left")
    for column in (
        *ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
        *NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS,
        "zori_growth_yoy",
    ):
        annual[column] = pd.to_numeric(annual[column], errors="coerce")

    def add(reason: str, mask: pd.Series) -> None:
        selected = mask.fillna(False)
        if selected.any():
            rows = annual.loc[selected].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    tolerance = 1e-12
    for exposure_column, output_column in zip(
        ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
        NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS,
    ):
        exposure = annual[exposure_column]
        reported = annual[output_column]
        eligible = exposure.between(0, 1) & annual["zori_growth_yoy"].notna()
        expected = exposure * annual["zori_growth_yoy"]
        add(f"missing_eligible_{output_column}", eligible & reported.isna())
        add(f"{output_column}_without_valid_inputs", reported.notna() & ~eligible)
        add(
            f"{output_column}_formula_mismatch",
            eligible
            & reported.notna()
            & reported.sub(expected).abs().gt(tolerance),
        )
        add(
            f"{output_column}_on_nonannual_date",
            reported.notna()
            & (
                annual["date"].isna()
                | annual["date"].dt.month.ne(12)
                | annual["date"].dt.day.ne(31)
            ),
        )
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _noncontrolled_rent_growth_sensitivity_schema_failures(
    panels: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Keep sensitivity bounds county-annual and the exact target absent."""

    rows: list[dict[str, object]] = []
    exact_target = "exposure_adjusted_noncontrolled_rent_growth"
    sensitivity_columns = set(
        NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS
    )
    for panel_name, panel in panels.items():
        if exact_target in panel.columns:
            rows.append(
                {
                    "panel": panel_name,
                    "failure_reason": "exact_noncontrolled_rent_growth_column_present",
                    "column": exact_target,
                }
            )
        if panel_name != "county_annual":
            for column in sorted(sensitivity_columns & set(panel.columns)):
                rows.append(
                    {
                        "panel": panel_name,
                        "failure_reason": "rent_growth_sensitivity_outside_county_annual",
                        "column": column,
                    }
                )
        if "variable_id" not in panel:
            continue
        variable_ids = panel["variable_id"].astype("string")
        if variable_ids.eq(exact_target).any():
            rows.append(
                {
                    "panel": panel_name,
                    "failure_reason": "exact_noncontrolled_rent_growth_variable_present",
                    "column": exact_target,
                }
            )
        sensitivity_rows = variable_ids.isin(sensitivity_columns)
        if "final_frequency" in panel:
            wrong_frequency = sensitivity_rows & ~panel["final_frequency"].eq(
                "annual"
            )
            if wrong_frequency.any():
                rows.append(
                    {
                        "panel": panel_name,
                        "failure_reason": "rent_growth_sensitivity_nonannual_frequency",
                        "row_count": int(wrong_frequency.sum()),
                    }
                )
    return pd.DataFrame(rows)


def _acs_zcta_annual_failures(
    zcta_annual: pd.DataFrame,
    expected_zctas: set[str] | None = None,
    expected_years: set[int] | None = None,
) -> pd.DataFrame:
    """Validate exact-ZCTA annual ACS ratios and vintage differences."""

    price_column = "zcta_annual_price_to_income_acs"
    price_quality_column = "zcta_annual_price_to_income_acs_quality_flag"
    change_column = "zcta_renter_occupied_units_vintage_change_annual"
    change_quality_column = (
        "zcta_renter_occupied_units_vintage_change_annual_quality_flag"
    )
    required = {
        "date",
        "geo_id",
        "zcta",
        "geography_type",
        "acs_median_home_value",
        "acs_median_household_income",
        "acs_renter_occupied_units",
        price_column,
        price_quality_column,
        change_column,
        change_quality_column,
    }
    missing = sorted(required - set(zcta_annual.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )

    work = zcta_annual[list(sorted(required))].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.sort_values(["geo_id", "date"])
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False)].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    geo_ids = work["geo_id"].astype("string")
    zctas = work["zcta"].astype("string")
    add("invalid_zcta_geo_id", ~geo_ids.str.fullmatch(r"ZCTA_\d{5}", na=False))
    add("invalid_zcta_code", ~zctas.str.fullmatch(r"\d{5}", na=False))
    add("zcta_identity_mismatch", zctas.ne(geo_ids.str.removeprefix("ZCTA_")))
    add(
        "zcta_geography_type_mismatch",
        work["geography_type"].astype("string").ne("zcta"),
    )
    add(
        "non_annual_zcta_date",
        work["date"].isna()
        | work["date"].dt.month.ne(12)
        | work["date"].dt.day.ne(31),
    )
    duplicate = work.duplicated(["date", "geo_id"], keep=False)
    add("duplicate_zcta_year", duplicate)
    if "postal_zip" in zcta_annual and zcta_annual["postal_zip"].notna().any():
        frames.append(
            pd.DataFrame(
                [{"failure_reason": "zcta_mislabeled_as_postal_zip"}]
            )
        )

    if expected_zctas is not None and expected_years is not None:
        expected_grid = {
            (f"ZCTA_{zcta}", year)
            for zcta in expected_zctas
            for year in expected_years
        }
        actual_grid = set(
            zip(
                geo_ids.astype(str),
                work["date"].dt.year.fillna(-1).astype(int),
            )
        )
        missing_grid = expected_grid - actual_grid
        unexpected_grid = actual_grid - expected_grid
        if missing_grid or unexpected_grid:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "failure_reason": "unexpected_zcta_year_grid",
                            "expected_rows": len(expected_grid),
                            "actual_rows": len(actual_grid),
                            "missing_rows": len(missing_grid),
                            "unexpected_rows": len(unexpected_grid),
                        }
                    ]
                )
            )

    home_value = pd.to_numeric(work["acs_median_home_value"], errors="coerce")
    income = pd.to_numeric(work["acs_median_household_income"], errors="coerce")
    reported_price = pd.to_numeric(work[price_column], errors="coerce")
    valid_price = home_value.notna() & home_value.ge(0) & income.notna() & income.gt(0)
    expected_price = (home_value / income).where(valid_price)
    add("missing_eligible_zcta_price_to_income", valid_price & reported_price.isna())
    add("zcta_price_to_income_without_valid_inputs", reported_price.notna() & ~valid_price)
    add(
        "zcta_price_to_income_formula_mismatch",
        reported_price.notna()
        & expected_price.notna()
        & reported_price.sub(expected_price).abs().gt(1e-12),
    )
    add(
        "nonnull_zcta_price_to_income_without_matched_vintage_flag",
        reported_price.notna()
        & work[price_quality_column]
        .astype("string")
        .ne("matched_acs_5year_vintage_ratio"),
    )

    renter = pd.to_numeric(work["acs_renter_occupied_units"], errors="coerce")
    reported_change = pd.to_numeric(work[change_column], errors="coerce")
    prior_renter = renter.groupby(work["geo_id"], sort=False).shift(1)
    prior_date = work["date"].groupby(work["geo_id"], sort=False).shift(1)
    consecutive = work["date"].dt.year.sub(prior_date.dt.year).eq(1)
    valid_change = consecutive & renter.notna() & prior_renter.notna()
    expected_change = (renter - prior_renter).where(valid_change)
    add("missing_eligible_zcta_renter_change", valid_change & reported_change.isna())
    add("zcta_renter_change_without_consecutive_vintages", reported_change.notna() & ~valid_change)
    add(
        "zcta_renter_change_formula_mismatch",
        reported_change.notna()
        & expected_change.notna()
        & reported_change.sub(expected_change).abs().gt(1e-12),
    )
    add(
        "nonnull_zcta_renter_change_without_overlap_flag",
        reported_change.notna()
        & work[change_quality_column]
        .astype("string")
        .ne("overlapping_5year_vintage_difference_not_one_year_flow"),
    )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _zillow_valuation_failures(
    panel: pd.DataFrame, key_columns: list[str]
) -> pd.DataFrame:
    required = {
        *key_columns,
        "zhvi",
        "zori",
        "price_to_rent_ratio",
        "price_to_rent_ratio_zillow",
        "implied_gross_rent_yield",
    }
    missing = sorted(required - set(panel.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )
    work = panel[list(required)].copy()
    for column in (
        "zhvi",
        "zori",
        "price_to_rent_ratio",
        "price_to_rent_ratio_zillow",
        "implied_gross_rent_yield",
    ):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    valid = work["zhvi"].gt(0) & work["zori"].gt(0)
    expected_ratio = (work["zhvi"] / (12.0 * work["zori"])).where(valid)
    expected_yield = (12.0 * work["zori"] / work["zhvi"]).where(valid)
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False)].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    add(
        "legacy_alias_mismatch",
        ~(
            work["price_to_rent_ratio"].eq(
                work["price_to_rent_ratio_zillow"]
            )
            | (
                work["price_to_rent_ratio"].isna()
                & work["price_to_rent_ratio_zillow"].isna()
            )
        ),
    )
    add(
        "canonical_ratio_mismatch",
        ~(
            (
                work["price_to_rent_ratio_zillow"].isna()
                & expected_ratio.isna()
            )
            | work["price_to_rent_ratio_zillow"]
            .sub(expected_ratio)
            .abs()
            .le(1e-10)
        ),
    )
    add(
        "gross_yield_mismatch",
        ~(
            (
                work["implied_gross_rent_yield"].isna()
                & expected_yield.isna()
            )
            | work["implied_gross_rent_yield"]
            .sub(expected_yield)
            .abs()
            .le(1e-10)
        ),
    )
    add(
        "nonpositive_component_not_suppressed",
        ~valid
        & (
            work["price_to_rent_ratio_zillow"].notna()
            | work["implied_gross_rent_yield"].notna()
        ),
    )
    add(
        "inverse_identity_mismatch",
        valid
        & (
            work["price_to_rent_ratio_zillow"]
            * work["implied_gross_rent_yield"]
            - 1.0
        ).abs().gt(1e-10),
    )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _homebuying_scenario_failures(
    scenarios: pd.DataFrame, mortgage_config: dict
) -> pd.DataFrame:
    required = {
        "date",
        "geo_id",
        "geography_level",
        "median_sale_price",
        "down_payment_variable_id",
        "down_payment_share",
        "down_payment_amount",
        "estimated_mortgage_amount_scenario",
        "closing_cost_variable_id",
        "closing_cost_basis",
        "closing_cost_share",
        "closing_cost_amount",
        "total_cash_to_close_scenario",
        "scenario_name",
        "is_observed",
        "is_constructed",
        "is_interpolated",
        "prepaid_escrow_included",
    }
    missing = sorted(required - set(scenarios.columns))
    if missing:
        return pd.DataFrame(
            [{"failure_reason": "missing_columns", "columns": "|".join(missing)}]
        )
    if scenarios.empty:
        return pd.DataFrame([{"failure_reason": "no_scenario_rows"}])
    work = scenarios.copy()
    numeric = (
        "median_sale_price",
        "down_payment_share",
        "down_payment_amount",
        "estimated_mortgage_amount_scenario",
        "closing_cost_share",
        "closing_cost_amount",
        "total_cash_to_close_scenario",
    )
    for column in numeric:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    expected_down = work["median_sale_price"] * work["down_payment_share"]
    expected_loan = work["median_sale_price"] - expected_down
    expected_cost_base = work["median_sale_price"].where(
        work["closing_cost_basis"].eq("sale_price"),
        work["estimated_mortgage_amount_scenario"],
    )
    expected_cost = expected_cost_base * work["closing_cost_share"]
    expected_total = work["down_payment_amount"] + work["closing_cost_amount"]
    key = [
        "date",
        "geo_id",
        "down_payment_variable_id",
        "closing_cost_variable_id",
    ]
    frames: list[pd.DataFrame] = []

    def add(reason: str, mask: pd.Series) -> None:
        if mask.fillna(False).any():
            rows = work.loc[mask.fillna(False), key].copy()
            rows["failure_reason"] = reason
            frames.append(rows)

    add("duplicate_scenario_key", work.duplicated(key, keep=False))
    add(
        "down_payment_formula_mismatch",
        work["down_payment_amount"].sub(expected_down).abs().gt(1e-8),
    )
    add(
        "estimated_loan_formula_mismatch",
        work["estimated_mortgage_amount_scenario"]
        .sub(expected_loan)
        .abs()
        .gt(1e-8),
    )
    add(
        "closing_cost_formula_mismatch",
        work["closing_cost_amount"].sub(expected_cost).abs().gt(1e-8),
    )
    add(
        "cash_to_close_formula_mismatch",
        work["total_cash_to_close_scenario"].sub(expected_total).abs().gt(1e-8),
    )
    allowed_down = {
        float(value)
        for value in mortgage_config.get("down_payment_scenarios", [])
    }
    allowed_sale = {
        float(value)
        for value in mortgage_config.get("closing_cost_scenarios", [])
    }
    allowed_loan = {
        float(value)
        for value in mortgage_config.get("closing_cost_loan_scenarios", [])
    }
    add("unexpected_down_payment_share", ~work["down_payment_share"].isin(allowed_down))
    add(
        "unexpected_sale_price_cost_share",
        work["closing_cost_basis"].eq("sale_price")
        & ~work["closing_cost_share"].isin(allowed_sale),
    )
    add(
        "unexpected_loan_cost_share",
        work["closing_cost_basis"].eq("loan_amount")
        & ~work["closing_cost_share"].isin(allowed_loan),
    )
    add(
        "invalid_scenario_flags",
        work["is_observed"].ne(False)
        | work["is_constructed"].ne(True)
        | work["is_interpolated"].ne(False)
        | work["prepaid_escrow_included"].ne(False),
    )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _coverage_reports(
    config: ProjectConfig,
    fact: pd.DataFrame,
    county: pd.DataFrame,
    zip_panel: pd.DataFrame,
) -> None:
    coverage_dir = config.root / "output" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    source_work = fact.loc[
        :,
        [
            "provider",
            "variable_id",
            "geo_id",
            "observation_date",
            "value",
            "source_vintage",
        ],
    ].copy()
    source_work["geo_type"] = source_work["geo_id"].map(_classify_geo_type)
    source_coverage = (
        source_work.groupby(
            ["provider", "variable_id", "geo_type"], dropna=False
        )
        .agg(
            first_observation=("observation_date", "min"),
            latest_observation=("observation_date", "max"),
            observation_count=("value", "size"),
            nonnull_count=("value", "count"),
            geography_count=("geo_id", "nunique"),
            latest_source_vintage=("source_vintage", "max"),
        )
        .reset_index()
    )
    source_coverage.to_csv(coverage_dir / "source_coverage.csv", index=False)
    source_coverage.to_csv(config.root / "metadata" / "source_coverage.csv", index=False)
    del source_work
    if zip_panel.empty:
        zip_coverage = pd.DataFrame(
            columns=[
                "postal_zip",
                "first_observation",
                "latest_observation",
                "months",
                "zhvi_months",
                "zori_months",
                "redfin_months",
            ]
        )
    else:
        zip_coverage = (
            zip_panel.groupby("postal_zip")
            .agg(
                first_observation=("date", "min"),
                latest_observation=("date", "max"),
                months=("date", "size"),
                zhvi_months=("zhvi_available", "sum"),
                zori_months=("zori_available", "sum"),
                redfin_months=("redfin_available", "sum"),
            )
            .reset_index()
        )
    zip_coverage.to_csv(coverage_dir / "zip_coverage.csv", index=False)
    monthly = fact.loc[
        fact["final_frequency"].eq("monthly"),
        ["geo_id", "variable_id", "observation_date"],
    ].copy()
    monthly["period"] = monthly["observation_date"].dt.to_period("M")
    time_coverage = (
        monthly.groupby(["geo_id", "variable_id"])
        .agg(
            first_observation=("observation_date", "min"),
            latest_observation=("observation_date", "max"),
            observations=("observation_date", "nunique"),
            unique_months=("period", "nunique"),
        )
        .reset_index()
    )
    span = (
        (time_coverage["latest_observation"].dt.year - time_coverage["first_observation"].dt.year)
        * 12
        + time_coverage["latest_observation"].dt.month
        - time_coverage["first_observation"].dt.month
        + 1
    )
    time_coverage["missing_months_within_span"] = span - time_coverage["unique_months"]
    time_coverage.to_csv(coverage_dir / "time_coverage.csv", index=False)
    _write_missingness_report(coverage_dir, county, zip_panel)
    _write_redfin_active_vintage_report(coverage_dir, fact)
    _write_redfin_vintage_comparison(config, coverage_dir)


def _panel_missingness(table_name: str, frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    result: list[dict] = []
    dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame else None
    for column in frame.columns:
        series = frame[column]
        nonmissing = series.notna()
        first_date = None
        latest_date = None
        if dates is not None and nonmissing.any():
            observed_dates = dates.loc[nonmissing & dates.notna()]
            if not observed_dates.empty:
                first_date = observed_dates.min().date().isoformat()
                latest_date = observed_dates.max().date().isoformat()
        result.append(
            {
                "table_name": table_name,
                "column_name": column,
                "row_count": len(frame),
                "nonmissing_count": int(nonmissing.sum()),
                "missing_count": int((~nonmissing).sum()),
                "missing_share": float((~nonmissing).mean()),
                "first_nonmissing_date": first_date,
                "last_nonmissing_date": latest_date,
                "note": (
                    "Missing values intentionally preserved; no complete-case "
                    "filtering, interpolation, backfill, forward fill, or zero "
                    "fill is applied by default."
                ),
            }
        )
    return result


def _write_missingness_report(
    coverage_dir: Path, county: pd.DataFrame, zip_panel: pd.DataFrame
) -> None:
    rows = [
        *_panel_missingness("analytics.county_monthly_panel", county),
        *_panel_missingness("analytics.zip_monthly_panel", zip_panel),
    ]
    pd.DataFrame(rows).to_csv(
        coverage_dir / "missingness_summary.csv", index=False
    )


def _write_redfin_active_vintage_report(
    coverage_dir: Path, fact: pd.DataFrame
) -> None:
    redfin = fact.loc[fact["provider"].eq("Redfin Data Center")].copy()
    columns = [
        "source_id",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "checksum_sha256",
        "provider_last_updated",
        "first_observation",
        "latest_observation",
        "observation_count",
        "nonnull_count",
        "geography_count",
        "variable_count",
    ]
    if redfin.empty:
        pd.DataFrame(columns=columns).to_csv(
            coverage_dir / "redfin_active_vintage.csv", index=False
        )
        return
    redfin["source_id"] = redfin["variable_id"].map(
        lambda item: "redfin_zip_monthly"
        if str(item).startswith("redfin_zip_")
        else "redfin_county_monthly"
    )
    for column in ("checksum_sha256", "provider_last_updated"):
        if column not in redfin:
            redfin[column] = None
    active = (
        redfin.groupby(
            [
                "source_id",
                "source_filename",
                "source_vintage",
                "retrieved_at",
                "checksum_sha256",
                "provider_last_updated",
            ],
            dropna=False,
        )
        .agg(
            first_observation=("observation_date", "min"),
            latest_observation=("observation_date", "max"),
            observation_count=("value", "size"),
            nonnull_count=("value", "count"),
            geography_count=("geo_id", "nunique"),
            variable_count=("variable_id", "nunique"),
        )
        .reset_index()
        .reindex(columns=columns)
    )
    active.to_csv(coverage_dir / "redfin_active_vintage.csv", index=False)


def _redfin_archive_summary(config: ProjectConfig, record: pd.Series) -> dict:
    path = config.root / str(record.get("relative_path", ""))
    if not path.exists():
        return {"note": f"Archived file not found: {path}"}
    read_options: dict[str, object] = {"dtype": str}
    if str(record.get("source_id")) == "redfin_county_monthly":
        read_options.update({"sep": "\t", "compression": "gzip"})
    try:
        header = pd.read_csv(path, nrows=0, **read_options)
        normalized = {
            re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", column.lower())).strip("_"): column
            for column in header.columns
        }
        begin_column = normalized.get("period_begin")
        end_column = normalized.get("period_end")
        last_updated_column = normalized.get("last_updated")
        use_columns = [
            column for column in (begin_column, end_column, last_updated_column) if column
        ]
        frame = pd.read_csv(
            path,
            usecols=use_columns if use_columns else None,
            **read_options,
        )
    except Exception as exc:
        return {"note": f"{type(exc).__name__}: {exc}"}
    result: dict[str, object] = {
        "row_count": len(frame),
        "note": None,
    }
    for label, column in (
        ("first_period_begin", begin_column),
        ("latest_period_begin", begin_column),
        ("first_period_end", end_column),
        ("latest_period_end", end_column),
        ("provider_last_updated", last_updated_column),
    ):
        if column is None:
            result[label] = None
            continue
        dates = pd.to_datetime(frame[column], errors="coerce")
        if label.startswith("first"):
            value = dates.min()
        else:
            value = dates.max()
        result[label] = value.date().isoformat() if pd.notna(value) else None
    return result


def _write_redfin_vintage_comparison(
    config: ProjectConfig, coverage_dir: Path
) -> None:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    columns = [
        "source_id",
        "old_source_vintage",
        "new_source_vintage",
        "old_retrieved_at",
        "new_retrieved_at",
        "old_source_filename",
        "new_source_filename",
        "old_checksum_sha256",
        "new_checksum_sha256",
        "old_row_count",
        "new_row_count",
        "row_count_difference",
        "old_first_period_begin",
        "new_first_period_begin",
        "old_latest_period_end",
        "new_latest_period_end",
        "old_provider_last_updated",
        "new_provider_last_updated",
        "note",
    ]
    if not manifest_path.exists():
        pd.DataFrame(columns=columns).to_csv(
            coverage_dir / "redfin_vintage_comparison.csv", index=False
        )
        return
    manifest = pd.read_csv(manifest_path, dtype=str)
    redfin = manifest.loc[
        manifest["source_id"].isin({"redfin_county_monthly", "redfin_zip_monthly"})
    ].copy()
    rows: list[dict] = []
    for source_id, group in redfin.groupby("source_id", dropna=False):
        group = group.sort_values(["retrieved_at", "source_filename"])
        if len(group) < 2:
            record = group.iloc[-1].to_dict() if not group.empty else {}
            rows.append(
                {
                    "source_id": source_id,
                    "new_source_vintage": record.get("data_vintage"),
                    "new_retrieved_at": record.get("retrieved_at"),
                    "new_source_filename": record.get("source_filename"),
                    "new_checksum_sha256": record.get("checksum_sha256"),
                    "note": (
                        "Fewer than two raw vintages archived; no Redfin "
                        "revision comparison is possible yet."
                    ),
                }
            )
            continue
        old = group.iloc[-2]
        new = group.iloc[-1]
        old_summary = _redfin_archive_summary(config, old)
        new_summary = _redfin_archive_summary(config, new)
        old_rows = old_summary.get("row_count")
        new_rows = new_summary.get("row_count")
        note_parts = [
            str(value)
            for value in (old_summary.get("note"), new_summary.get("note"))
            if value
        ]
        rows.append(
            {
                "source_id": source_id,
                "old_source_vintage": old.get("data_vintage"),
                "new_source_vintage": new.get("data_vintage"),
                "old_retrieved_at": old.get("retrieved_at"),
                "new_retrieved_at": new.get("retrieved_at"),
                "old_source_filename": old.get("source_filename"),
                "new_source_filename": new.get("source_filename"),
                "old_checksum_sha256": old.get("checksum_sha256"),
                "new_checksum_sha256": new.get("checksum_sha256"),
                "old_row_count": old_rows,
                "new_row_count": new_rows,
                "row_count_difference": (
                    int(new_rows) - int(old_rows)
                    if old_rows is not None and new_rows is not None
                    else None
                ),
                "old_first_period_begin": old_summary.get("first_period_begin"),
                "new_first_period_begin": new_summary.get("first_period_begin"),
                "old_latest_period_end": old_summary.get("latest_period_end"),
                "new_latest_period_end": new_summary.get("latest_period_end"),
                "old_provider_last_updated": old_summary.get(
                    "provider_last_updated"
                ),
                "new_provider_last_updated": new_summary.get(
                    "provider_last_updated"
                ),
                "note": "; ".join(note_parts) if note_parts else None,
            }
        )
    pd.DataFrame(rows).reindex(columns=columns).to_csv(
        coverage_dir / "redfin_vintage_comparison.csv", index=False
    )


def _formula_mismatch_rows(
    frame: pd.DataFrame,
    actual_column: str,
    expected: pd.Series,
    *,
    key_columns: list[str],
) -> pd.DataFrame:
    if actual_column not in frame:
        return pd.DataFrame(
            [{"variable": actual_column, "failure": "missing_output_column"}]
        )
    actual = pd.to_numeric(frame[actual_column], errors="coerce")
    expected_numeric = pd.to_numeric(expected, errors="coerce")
    match = (actual.isna() & expected_numeric.isna()) | pd.Series(
        np.isclose(
            actual.to_numpy(dtype="float64", na_value=np.nan),
            expected_numeric.to_numpy(dtype="float64", na_value=np.nan),
            rtol=FORMULA_RELATIVE_TOLERANCE,
            atol=FORMULA_ABSOLUTE_TOLERANCE,
            equal_nan=True,
        ),
        index=frame.index,
    )
    mismatch = ~match.fillna(False)
    if not mismatch.any():
        return pd.DataFrame()
    keys = [column for column in key_columns if column in frame]
    rows = frame.loc[mismatch, keys].copy()
    rows["variable"] = actual_column
    rows["actual"] = actual.loc[mismatch]
    rows["expected"] = expected_numeric.loc[mismatch]
    return rows


def _comparable_county_analytics_failures(
    panel: pd.DataFrame, config: ProjectConfig
) -> pd.DataFrame:
    """Recompute every document-specified eight-county formula."""

    methodology = config.pipeline["comparable_county_analytics"]
    formula_columns = {
        "date",
        "county_fips",
        "zhvi",
        "zori_smoothed_sa",
        "months_of_supply",
        "days_on_market",
        "price_drop_share",
        "employment",
        "zhvi_momentum_yoy_log_pct",
        "zhvi_momentum_3m_annualized_log_pct",
        "zori_momentum_yoy_log_pct",
        "zori_momentum_3m_annualized_log_pct",
        "price_to_rent_ratio_zillow",
        "price_to_rent_ratio_log_change_mom_pct",
        "price_to_rent_ratio_log_change_yoy_pct",
        "laus_employment_momentum_yoy_log_pct",
        "market_tightness_index_3c_fixed",
        "market_tightness_components_3c",
        "county_housing_market_heat_index",
        "county_housing_market_heat_rank",
    }
    relative_outputs = {
        "zhvi_momentum_yoy_log_pct": "la_relative_zhvi_momentum_yoy_log_pct",
        "zhvi_momentum_3m_annualized_log_pct": (
            "la_relative_zhvi_momentum_3m_annualized_log_pct"
        ),
        "zori_momentum_yoy_log_pct": "la_relative_zori_momentum_yoy_log_pct",
        "zori_momentum_3m_annualized_log_pct": (
            "la_relative_zori_momentum_3m_annualized_log_pct"
        ),
        "market_tightness_index_3c_fixed": (
            "la_relative_market_tightness_index_3c_fixed"
        ),
        "price_to_rent_ratio_zillow": "la_relative_price_to_rent_ratio_zillow",
    }
    missing_columns = sorted(
        formula_columns.union(relative_outputs.values()).difference(panel.columns)
    )
    if missing_columns:
        return pd.DataFrame(
            [
                {"failure": "missing_output_column", "variable": column}
                for column in missing_columns
            ]
        )

    failures: list[pd.DataFrame] = []
    work = panel.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    duplicate = work.loc[
        work.duplicated(["date", "county_fips"], keep=False),
        ["date", "county_fips"],
    ]
    if not duplicate.empty:
        duplicate["failure"] = "duplicate_county_month"
        failures.append(duplicate)
    observed_counties = set(work["county_fips"].dropna().astype(str))
    expected_counties = {str(value) for value in methodology["target_county_fips"]}
    if observed_counties != expected_counties:
        failures.append(
            pd.DataFrame(
                [
                    {
                        "failure": "county_contract_mismatch",
                        "actual": "|".join(sorted(observed_counties)),
                        "expected": "|".join(sorted(expected_counties)),
                    }
                ]
            )
        )

    expected_formulas = {
        "zhvi_momentum_yoy_log_pct": exact_calendar_log_momentum(
            work, "zhvi", lag_months=12, scale=100.0
        ),
        "zhvi_momentum_3m_annualized_log_pct": exact_calendar_log_momentum(
            work, "zhvi", lag_months=3, scale=400.0
        ),
        "zori_momentum_yoy_log_pct": exact_calendar_log_momentum(
            work, "zori_smoothed_sa", lag_months=12, scale=100.0
        ),
        "zori_momentum_3m_annualized_log_pct": exact_calendar_log_momentum(
            work, "zori_smoothed_sa", lag_months=3, scale=400.0
        ),
        "price_to_rent_ratio_zillow": positive_price_to_rent_ratio(
            work["zhvi"], work["zori_smoothed_sa"]
        ),
        "laus_employment_momentum_yoy_log_pct": laus_employment_momentum_yoy(
            work
        ),
    }
    ratio_frame = work.copy()
    ratio_frame["_expected_ratio"] = expected_formulas[
        "price_to_rent_ratio_zillow"
    ]
    expected_formulas["price_to_rent_ratio_log_change_mom_pct"] = (
        exact_calendar_log_momentum(
            ratio_frame, "_expected_ratio", lag_months=1, scale=100.0
        )
    )
    expected_formulas["price_to_rent_ratio_log_change_yoy_pct"] = (
        exact_calendar_log_momentum(
            ratio_frame, "_expected_ratio", lag_months=12, scale=100.0
        )
    )
    for variable, expected in expected_formulas.items():
        mismatch = _formula_mismatch_rows(
            work,
            variable,
            expected,
            key_columns=["date", "county_fips"],
        )
        if not mismatch.empty:
            failures.append(mismatch)

    parameters_path = config.root / str(methodology["frozen_parameters_path"])
    try:
        artifact = json.loads(parameters_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(
            pd.DataFrame(
                [
                    {
                        "failure": "invalid_frozen_parameter_artifact",
                        "detail": str(exc),
                    }
                ]
            )
        )
        return pd.concat(failures, ignore_index=True, sort=False)
    expected_spec = {
        "methodology_id": str(methodology["methodology_id"]),
        "target_county_fips": [
            str(value) for value in methodology["target_county_fips"]
        ],
        "tightness_components": list(MARKET_TIGHTNESS_COMPONENTS),
        "tightness_reference_start": str(methodology["tightness_reference_start"]),
        "tightness_reference_end": str(methodology["tightness_reference_end"]),
        "heat_reference_start": str(methodology["heat_reference_start"]),
        "heat_reference_end": str(methodology["heat_reference_end"]),
        "normalization_scope": str(methodology["normalization_scope"]),
        "standard_deviation_ddof": int(methodology["standard_deviation_ddof"]),
    }
    if artifact.get("specification") != expected_spec:
        failures.append(
            pd.DataFrame([{"failure": "frozen_parameter_specification_mismatch"}])
        )
        return pd.concat(failures, ignore_index=True, sort=False)
    if not artifact.get("source_lineage"):
        failures.append(pd.DataFrame([{"failure": "missing_parameter_source_lineage"}]))

    expected_tightness, expected_coverage = market_tightness_index_3c(
        work, artifact["tightness"]
    )
    for variable, expected in (
        ("market_tightness_index_3c_fixed", expected_tightness),
        ("market_tightness_components_3c", expected_coverage),
    ):
        mismatch = _formula_mismatch_rows(
            work,
            variable,
            expected,
            key_columns=["date", "county_fips"],
        )
        if not mismatch.empty:
            failures.append(mismatch)
    heat_input = work.assign(
        home_price_momentum_3m_annualized=work[
            "zhvi_momentum_3m_annualized_log_pct"
        ],
        market_tightness_index=work["market_tightness_index_3c_fixed"],
    )
    expected_heat = county_housing_market_heat_index(
        heat_input, artifact["heat"]
    )
    mismatch = _formula_mismatch_rows(
        work,
        "county_housing_market_heat_index",
        expected_heat,
        key_columns=["date", "county_fips"],
    )
    if not mismatch.empty:
        failures.append(mismatch)
    expected_rank = (
        expected_heat.groupby(work["date"], sort=False)
        .rank(method="min", ascending=False)
    )
    mismatch = _formula_mismatch_rows(
        work,
        "county_housing_market_heat_rank",
        expected_rank,
        key_columns=["date", "county_fips"],
    )
    if not mismatch.empty:
        failures.append(mismatch)

    relative = la_relative_market_performance(
        work,
        tuple(relative_outputs),
        la_county_fips=str(methodology["la_county_fips"]),
        peer_county_fips=tuple(str(value) for value in methodology["peer_county_fips"]),
    ).rename(
        columns={
            f"{source}_la_relative_to_seven_peers": output
            for source, output in relative_outputs.items()
        }
    )
    la_rows = work.loc[
        work["county_fips"].astype(str).eq(str(methodology["la_county_fips"]))
    ].merge(relative, on="date", how="left", suffixes=("", "_expected"))
    for output in relative_outputs.values():
        mismatch = _formula_mismatch_rows(
            la_rows,
            output,
            la_rows[f"{output}_expected"],
            key_columns=["date", "county_fips"],
        )
        if not mismatch.empty:
            failures.append(mismatch)
    peer_nonnull = work.loc[
        ~work["county_fips"].astype(str).eq(str(methodology["la_county_fips"]))
        & work[list(relative_outputs.values())].notna().any(axis=1),
        ["date", "county_fips"],
    ]
    if not peer_nonnull.empty:
        peer_nonnull["failure"] = "la_relative_value_on_peer_row"
        failures.append(peer_nonnull)
    return (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame()
    )


def _zip_document_analytics_failures(
    zip_panel: pd.DataFrame, comparable_counties: pd.DataFrame
) -> pd.DataFrame:
    required = {
        "date",
        "postal_zip",
        "zhvi",
        "zori",
        "zhvi_momentum_yoy_log_pct",
        "zhvi_momentum_3m_annualized_log_pct",
        "zori_momentum_yoy_log_pct",
        "zori_momentum_3m_annualized_log_pct",
        "price_to_rent_ratio_zillow",
        "price_to_rent_ratio_log_change_mom_pct",
        "price_to_rent_ratio_log_change_yoy_pct",
        "la_county_price_to_rent_ratio_zillow",
        "relative_valuation_pressure_la_zip",
    }
    missing = sorted(required.difference(zip_panel.columns))
    if missing:
        return pd.DataFrame(
            [{"failure": "missing_output_column", "variable": item} for item in missing]
        )
    work = zip_panel.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    group_columns = ("postal_zip",)
    expected_ratio = positive_price_to_rent_ratio(work["zhvi"], work["zori"])
    expected = {
        "zhvi_momentum_yoy_log_pct": exact_calendar_log_momentum(
            work,
            "zhvi",
            lag_months=12,
            scale=100.0,
            group_columns=group_columns,
        ),
        "zhvi_momentum_3m_annualized_log_pct": exact_calendar_log_momentum(
            work,
            "zhvi",
            lag_months=3,
            scale=400.0,
            group_columns=group_columns,
        ),
        "zori_momentum_yoy_log_pct": exact_calendar_log_momentum(
            work,
            "zori",
            lag_months=12,
            scale=100.0,
            group_columns=group_columns,
        ),
        "zori_momentum_3m_annualized_log_pct": exact_calendar_log_momentum(
            work,
            "zori",
            lag_months=3,
            scale=400.0,
            group_columns=group_columns,
        ),
        "price_to_rent_ratio_zillow": expected_ratio,
    }
    ratio_frame = work.assign(_expected_ratio=expected_ratio)
    expected["price_to_rent_ratio_log_change_mom_pct"] = (
        exact_calendar_log_momentum(
            ratio_frame,
            "_expected_ratio",
            lag_months=1,
            scale=100.0,
            group_columns=group_columns,
        )
    )
    expected["price_to_rent_ratio_log_change_yoy_pct"] = (
        exact_calendar_log_momentum(
            ratio_frame,
            "_expected_ratio",
            lag_months=12,
            scale=100.0,
            group_columns=group_columns,
        )
    )
    failures: list[pd.DataFrame] = []
    for variable, values in expected.items():
        mismatch = _formula_mismatch_rows(
            work,
            variable,
            values,
            key_columns=["date", "postal_zip"],
        )
        if not mismatch.empty:
            failures.append(mismatch)

    la_ratio = comparable_counties.loc[
        comparable_counties["county_fips"].astype(str).eq(PRIMARY_COUNTY_FIPS),
        ["date", "price_to_rent_ratio_zillow"],
    ].rename(columns={"price_to_rent_ratio_zillow": "_expected_la_ratio"})
    aligned = work.merge(la_ratio, on="date", how="left", validate="many_to_one")
    mismatch = _formula_mismatch_rows(
        aligned,
        "la_county_price_to_rent_ratio_zillow",
        aligned["_expected_la_ratio"],
        key_columns=["date", "postal_zip"],
    )
    if not mismatch.empty:
        failures.append(mismatch)
    zip_ratio = pd.to_numeric(aligned["price_to_rent_ratio_zillow"], errors="coerce")
    county_ratio = pd.to_numeric(aligned["_expected_la_ratio"], errors="coerce")
    valid = zip_ratio.gt(0) & county_ratio.gt(0)
    relative = pd.Series(np.nan, index=aligned.index, dtype="float64")
    relative.loc[valid] = np.log(zip_ratio.loc[valid]) - np.log(county_ratio.loc[valid])
    mismatch = _formula_mismatch_rows(
        aligned,
        "relative_valuation_pressure_la_zip",
        relative,
        key_columns=["date", "postal_zip"],
    )
    if not mismatch.empty:
        failures.append(mismatch)
    return (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame()
    )


def _regime_assignment_count_failures(
    classified_context: pd.DataFrame,
    summary: pd.DataFrame,
    transitions: pd.DataFrame,
) -> pd.DataFrame:
    """Reconstruct stock and exact-quarter flow counts independently of reports.

    The context includes 2019 Q4 solely to compare the first application quarter.
    A ZIP without a prior-quarter classification is not an unchanged ZIP.
    """
    context = classified_context.copy()
    context["quarter_end"] = pd.to_datetime(context["quarter_end"])
    expected_summary: list[dict] = []
    expected_transitions: list[dict] = []
    application = context.loc[context["quarter_end"].ge(REGIME_APPLICATION_START)]
    for quarter_end, quarter in application.groupby("quarter_end", sort=True):
        previous_date = (quarter_end.to_period("Q") - 1).to_timestamp(how="end").normalize()
        current = quarter.loc[quarter["complete_regime_inputs"], ["postal_zip", "zip_market_regime_id"]]
        previous = context.loc[
            context["quarter_end"].eq(previous_date) & context["complete_regime_inputs"],
            ["postal_zip", "zip_market_regime_id"],
        ]
        paired = current.merge(previous, on="postal_zip", how="inner", suffixes=("_current", "_previous"), validate="one_to_one")
        classified, comparable = len(current), len(paired)
        missing_previous = classified - comparable
        status = ("no_current_classifications" if classified == 0 else
                  "complete" if missing_previous == 0 else "incomplete_previous_quarter")
        changed = paired["zip_market_regime_id_current"].ne(paired["zip_market_regime_id_previous"])
        common = {
            "quarter_end": quarter_end,
            "classified_zip_count": classified,
            "comparable_zip_count": comparable,
            "missing_previous_classification_count": missing_previous,
            "transition_denominator_status": status,
            "changed_regime_zip_count": int(changed.sum()),
        }
        for regime_id in REGIME_LABELS:
            expected_summary.append({
                **common, "regime_id": regime_id,
                "universe_zip_count": quarter["postal_zip"].nunique(),
                "regime_zip_count": int(current["zip_market_regime_id"].eq(regime_id).sum()),
                "entered_regime_zip_count": int((changed & paired["zip_market_regime_id_current"].eq(regime_id)).sum()),
                "exited_regime_zip_count": int((changed & paired["zip_market_regime_id_previous"].eq(regime_id)).sum()),
            })
            for destination in REGIME_LABELS:
                expected_transitions.append({
                    **common, "from_regime_id": regime_id, "to_regime_id": destination,
                    "previous_quarter_end": previous_date,
                    "transition_zip_count": int((paired["zip_market_regime_id_previous"].eq(regime_id) &
                                                 paired["zip_market_regime_id_current"].eq(destination)).sum()),
                })
    failures: list[pd.DataFrame] = []
    for label, actual, records, keys in (
        ("summary", summary, expected_summary, ["quarter_end", "regime_id"]),
        ("transition", transitions, expected_transitions, ["quarter_end", "from_regime_id", "to_regime_id"]),
    ):
        expected = pd.DataFrame(records)
        if expected.empty:
            if not actual.empty:
                failures.append(pd.DataFrame([{"failure": f"unexpected_regime_{label}_rows"}]))
            continue
        missing = sorted(set(expected.columns).difference(actual.columns))
        if missing:
            failures.append(pd.DataFrame([{"failure": f"missing_{label}_column", "variable": item} for item in missing]))
            continue
        work = actual.copy()
        work["quarter_end"] = pd.to_datetime(work["quarter_end"], errors="coerce")
        if work.duplicated(keys).any():
            duplicate = work.loc[work.duplicated(keys, keep=False), keys].copy()
            duplicate["failure"] = f"duplicate_regime_{label}_key"
            failures.append(duplicate)
            continue
        aligned = work.merge(expected, on=keys, how="outer", suffixes=("", "_expected"), indicator=True, validate="one_to_one")
        missing_keys = aligned.loc[aligned["_merge"].ne("both"), [*keys, "_merge"]].copy()
        if not missing_keys.empty:
            missing_keys["failure"] = f"regime_{label}_assignment_key_mismatch"
            failures.append(missing_keys)
        aligned = aligned.loc[aligned["_merge"].eq("both")]
        for column in set(expected.columns).difference(keys):
            if column in {"transition_denominator_status", "previous_quarter_end"}:
                actual_values = aligned[column]
                expected_values = aligned[f"{column}_expected"]
                if column == "previous_quarter_end":
                    actual_values = pd.to_datetime(actual_values, errors="coerce")
                bad = ~actual_values.eq(expected_values).fillna(False)
                if bad.any():
                    detail = aligned.loc[bad, keys].copy()
                    detail["failure"] = f"regime_{label}_{column}_mismatch"
                    failures.append(detail)
            else:
                mismatch = _formula_mismatch_rows(aligned, column, aligned[f"{column}_expected"], key_columns=keys)
                if not mismatch.empty:
                    failures.append(mismatch)
    return pd.concat(failures, ignore_index=True, sort=False) if failures else pd.DataFrame()


def _zip_market_regime_failures(
    zip_panel: pd.DataFrame,
    assignments: pd.DataFrame,
    summary: pd.DataFrame,
    transitions: pd.DataFrame,
    model_path: Path,
) -> pd.DataFrame:
    required_assignment = {
        "quarter_end",
        "postal_zip",
        *REGIME_FEATURE_COLUMNS,
        "complete_regime_inputs",
        "classification_status",
        "zip_market_regime_id",
        "zip_market_regime_label",
        "zip_market_regime_distance",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
    }
    missing = sorted(required_assignment.difference(assignments.columns))
    if missing:
        return pd.DataFrame(
            [{"failure": "missing_assignment_column", "variable": item} for item in missing]
        )
    failures: list[pd.DataFrame] = []
    try:
        model = load_market_regime_model(model_path)
    except (OSError, ValueError) as exc:
        return pd.DataFrame(
            [{"failure": "invalid_frozen_regime_model", "detail": str(exc)}]
        )
    work = assignments.copy()
    work["quarter_end"] = pd.to_datetime(work["quarter_end"], errors="coerce")
    invalid_key = work.loc[
        work["quarter_end"].isna()
        | work["quarter_end"].lt(REGIME_APPLICATION_START)
        | ~work["quarter_end"].dt.month.isin([3, 6, 9, 12])
        | ~work["postal_zip"].astype("string").str.fullmatch(r"\d{5}").fillna(False)
        | work.duplicated(["quarter_end", "postal_zip"], keep=False),
        ["quarter_end", "postal_zip"],
    ]
    if not invalid_key.empty:
        invalid_key["failure"] = "invalid_assignment_key"
        failures.append(invalid_key)
    missing_lineage = work.loc[
        work[["source_vintage", "retrieved_at", "source_filename", "checksum_sha256"]]
        .isna()
        .any(axis=1),
        ["quarter_end", "postal_zip"],
    ]
    if not missing_lineage.empty:
        missing_lineage["failure"] = "missing_assignment_source_lineage"
        failures.append(missing_lineage)

    expected_inputs = build_quarterly_regime_inputs(zip_panel)
    context_start = REGIME_APPLICATION_START - pd.DateOffset(months=3)
    expected_context = classify_quarterly_regime_inputs(
        expected_inputs.loc[pd.to_datetime(expected_inputs["quarter_end"]).ge(context_start)],
        model,
    )
    count_failures = _regime_assignment_count_failures(expected_context, summary, transitions)
    if not count_failures.empty:
        failures.append(count_failures)
    expected_inputs = expected_inputs.loc[
        pd.to_datetime(expected_inputs["quarter_end"], errors="coerce").ge(
            REGIME_APPLICATION_START
        )
    ].copy()
    expected_inputs["quarter_end"] = pd.to_datetime(
        expected_inputs["quarter_end"], errors="coerce"
    )
    expected_inputs = expected_inputs.rename(
        columns={variable: f"_expected_{variable}" for variable in REGIME_FEATURE_COLUMNS}
    )
    aligned_inputs = work.merge(
        expected_inputs,
        on=["quarter_end", "postal_zip"],
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    key_mismatch = aligned_inputs.loc[
        aligned_inputs["_merge"].ne("both"),
        ["quarter_end", "postal_zip", "_merge"],
    ].copy()
    if not key_mismatch.empty:
        key_mismatch["failure"] = "regime_quarter_input_key_mismatch"
        failures.append(key_mismatch)
    aligned_inputs = aligned_inputs.loc[aligned_inputs["_merge"].eq("both")]
    for variable in REGIME_FEATURE_COLUMNS:
        mismatch = _formula_mismatch_rows(
            aligned_inputs,
            variable,
            aligned_inputs[f"_expected_{variable}"],
            key_columns=["quarter_end", "postal_zip"],
        )
        if not mismatch.empty:
            failures.append(mismatch)

    recalculated = classify_quarterly_regime_inputs(
        work[["quarter_end", "postal_zip", *REGIME_FEATURE_COLUMNS]], model
    )
    actual_id = work["zip_market_regime_id"].astype("Int64")
    expected_id = recalculated["zip_market_regime_id"].astype("Int64")
    id_match = actual_id.eq(expected_id) | (actual_id.isna() & expected_id.isna())
    if (~id_match.fillna(False)).any():
        rows = work.loc[
            ~id_match.fillna(False), ["quarter_end", "postal_zip"]
        ].copy()
        rows["failure"] = "nearest_center_assignment_mismatch"
        failures.append(rows)
    for field in ("complete_regime_inputs", "classification_status", "zip_market_regime_label"):
        actual_values, expected_values = work[field], recalculated[field]
        matches = actual_values.eq(expected_values) | (actual_values.isna() & expected_values.isna())
        if (~matches.fillna(False)).any():
            rows = work.loc[~matches.fillna(False), ["quarter_end", "postal_zip"]].copy()
            rows["failure"] = f"regime_{field}_mismatch"
            failures.append(rows)
    mismatch = _formula_mismatch_rows(
        work,
        "zip_market_regime_distance",
        recalculated["zip_market_regime_distance"],
        key_columns=["quarter_end", "postal_zip"],
    )
    if not mismatch.empty:
        failures.append(mismatch)
    invalid_labels = work.loc[
        work["complete_regime_inputs"]
        & (
            ~work["zip_market_regime_id"].isin(REGIME_LABELS)
            | ~work["zip_market_regime_label"].isin(REGIME_LABELS.values())
        ),
        ["quarter_end", "postal_zip"],
    ]
    if not invalid_labels.empty:
        invalid_labels["failure"] = "invalid_regime_id_or_label"
        failures.append(invalid_labels)

    required_summary = {
        "quarter_end",
        "regime_id",
        "regime_zip_count",
        "classified_zip_count",
        "universe_zip_count",
        "comparable_zip_count",
        "changed_regime_zip_count",
        "entered_regime_zip_count",
        "exited_regime_zip_count",
        "regime_share_of_classified",
        "classification_coverage_share_of_universe",
        "changed_regime_share_of_comparable",
        "entered_regime_share_of_comparable",
        "exited_regime_share_of_comparable",
        "changed_regime_share_of_classified",
        "entered_regime_share_of_classified",
        "exited_regime_share_of_classified",
        "missing_previous_classification_count",
        "transition_denominator_status",
        "zip_market_regime_share",
        "zip_market_regime_changed_share",
        "zip_market_regime_entry_share",
        "zip_market_regime_classified_coverage",
    }
    if not required_summary.issubset(summary.columns):
        failures.append(
            pd.DataFrame(
                [
                    {
                        "failure": "missing_summary_column",
                        "variable": item,
                    }
                    for item in sorted(required_summary.difference(summary.columns))
                ]
            )
        )
    else:
        for quarter_end, group in summary.groupby("quarter_end", sort=False):
            classified = int(group["classified_zip_count"].iloc[0])
            universe = int(group["universe_zip_count"].iloc[0])
            share_sum = pd.to_numeric(group["zip_market_regime_share"], errors="coerce").sum()
            bad = (
                len(group) != 4
                or set(group["regime_id"].astype(int)) != set(REGIME_LABELS)
                or (classified > 0 and not np.isclose(share_sum, 100.0))
                or classified > universe
            )
            if bad:
                failures.append(
                    pd.DataFrame(
                        [{"failure": "invalid_regime_summary", "quarter_end": quarter_end}]
                    )
                )
        classified = pd.to_numeric(summary["classified_zip_count"], errors="coerce")
        universe = pd.to_numeric(summary["universe_zip_count"], errors="coerce")
        comparable = pd.to_numeric(summary["comparable_zip_count"], errors="coerce")
        full_denominator = classified.where(classified.gt(0) & comparable.eq(classified))
        expected_summary_shares = {
            "regime_share_of_classified": 100.0
            * pd.to_numeric(summary["regime_zip_count"], errors="coerce")
            / classified.where(classified.gt(0)),
            "zip_market_regime_share": 100.0
            * pd.to_numeric(summary["regime_zip_count"], errors="coerce")
            / classified.where(classified.gt(0)),
            "classification_coverage_share_of_universe": 100.0
            * classified
            / universe.where(universe.gt(0)),
            "zip_market_regime_classified_coverage": 100.0
            * classified
            / universe.where(universe.gt(0)),
            "changed_regime_share_of_comparable": 100.0
            * pd.to_numeric(summary["changed_regime_zip_count"], errors="coerce")
            / comparable.where(comparable.gt(0)),
            "zip_market_regime_changed_share": 100.0
            * pd.to_numeric(summary["changed_regime_zip_count"], errors="coerce")
            / full_denominator,
            "changed_regime_share_of_classified": 100.0
            * pd.to_numeric(summary["changed_regime_zip_count"], errors="coerce")
            / full_denominator,
            "entered_regime_share_of_comparable": 100.0
            * pd.to_numeric(summary["entered_regime_zip_count"], errors="coerce")
            / comparable.where(comparable.gt(0)),
            "zip_market_regime_entry_share": 100.0
            * pd.to_numeric(summary["entered_regime_zip_count"], errors="coerce")
            / full_denominator,
            "entered_regime_share_of_classified": 100.0
            * pd.to_numeric(summary["entered_regime_zip_count"], errors="coerce")
            / full_denominator,
            "exited_regime_share_of_comparable": 100.0
            * pd.to_numeric(summary["exited_regime_zip_count"], errors="coerce")
            / comparable.where(comparable.gt(0)),
            "exited_regime_share_of_classified": 100.0
            * pd.to_numeric(summary["exited_regime_zip_count"], errors="coerce")
            / full_denominator,
        }
        for variable, expected_values in expected_summary_shares.items():
            mismatch = _formula_mismatch_rows(
                summary,
                variable,
                expected_values,
                key_columns=["quarter_end", "regime_id"],
            )
            if not mismatch.empty:
                failures.append(mismatch)
    required_transition = {
        "quarter_end",
        "previous_quarter_end",
        "from_regime_id",
        "to_regime_id",
        "transition_zip_count",
        "comparable_zip_count",
        "classified_zip_count",
        "missing_previous_classification_count",
        "transition_denominator_status",
        "transition_share_of_comparable",
        "transition_share_of_classified",
        "changed_regime_zip_count",
        "changed_regime_share_of_comparable",
        "changed_regime_share_of_classified",
        "zip_market_regime_transition_share",
    }
    if not required_transition.issubset(transitions.columns):
        failures.append(
            pd.DataFrame(
                [
                    {
                        "failure": "missing_transition_column",
                        "variable": item,
                    }
                    for item in sorted(required_transition.difference(transitions.columns))
                ]
            )
        )
    else:
        transition_work = transitions.copy()
        transition_work["quarter_end"] = pd.to_datetime(
            transition_work["quarter_end"], errors="coerce"
        )
        transition_work["previous_quarter_end"] = pd.to_datetime(
            transition_work["previous_quarter_end"], errors="coerce"
        )
        for quarter_end, group in transition_work.groupby("quarter_end", sort=False):
            expected_previous = (
                (pd.Timestamp(quarter_end).to_period("Q") - 1)
                .to_timestamp(how="end")
                .normalize()
            )
            denominator = int(group["comparable_zip_count"].iloc[0])
            classified_count = int(group["classified_zip_count"].iloc[0])
            complete = classified_count > 0 and denominator == classified_count
            shares = pd.to_numeric(
                group["zip_market_regime_transition_share"], errors="coerce"
            )
            bad = (
                len(group) != 16
                or not group["previous_quarter_end"].eq(expected_previous).all()
                or int(group["transition_zip_count"].sum()) != denominator
                or (complete and not np.isclose(shares.sum(), 100.0))
                or (not complete and shares.notna().any())
            )
            if bad:
                failures.append(
                    pd.DataFrame(
                        [{"failure": "invalid_regime_transition", "quarter_end": quarter_end}]
                    )
                )
        denominator = pd.to_numeric(
            transitions["comparable_zip_count"], errors="coerce"
        )
        expected_transition_share = 100.0 * pd.to_numeric(
            transitions["transition_zip_count"], errors="coerce"
        ) / denominator.where(denominator.gt(0))
        classified = pd.to_numeric(transitions["classified_zip_count"], errors="coerce")
        full_denominator = classified.where(classified.gt(0) & denominator.eq(classified))
        full_transition_share = 100.0 * pd.to_numeric(
            transitions["transition_zip_count"], errors="coerce"
        ) / full_denominator
        changed = pd.to_numeric(transitions["changed_regime_zip_count"], errors="coerce")
        expected_transition_shares = {
            "transition_share_of_comparable": expected_transition_share,
            "transition_share_of_classified": full_transition_share,
            "zip_market_regime_transition_share": full_transition_share,
            "changed_regime_share_of_comparable": 100.0 * changed / denominator.where(denominator.gt(0)),
            "changed_regime_share_of_classified": 100.0 * changed / full_denominator,
        }
        for variable, expected_values in expected_transition_shares.items():
            mismatch = _formula_mismatch_rows(
                transitions,
                variable,
                expected_values,
                key_columns=["quarter_end", "from_regime_id", "to_regime_id"],
            )
            if not mismatch.empty:
                failures.append(mismatch)
    return (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame()
    )


def _variable_metadata_failures(config: ProjectConfig) -> pd.DataFrame:
    """Missing, stale, or contradictory labels are structural metadata errors."""
    registry = config.root / "config" / "variable_metadata.csv"
    if not registry.exists() and not config.pipeline.get("require_variable_metadata", False):
        return pd.DataFrame(columns=["location", "reason"])
    failures: list[dict[str, str]] = []

    def inspect(frame: pd.DataFrame, location: str, require_all: bool = False) -> None:
        try:
            issues = validate_variable_metadata(frame, config.root, require_all=require_all)
        except (ValueError, FileNotFoundError) as exc:
            issues = [str(exc)]
        for issue in issues:
            failures.append({"location": location, "reason": issue})
        for column in ("unit_simplified", "temporal_type", "temporal_type_note", "monetary_basis"):
            if column not in frame or frame[column].fillna("").eq("").any():
                failures.append({"location": location, "reason": f"Missing labels: {column}"})

    inspect(pd.DataFrame(config.variables), "configuration")
    path = config.root / "metadata" / "data_dictionary.csv"
    if path.exists():
        inspect(pd.read_csv(path, dtype=str, keep_default_na=False), "data_dictionary.csv", True)
    else:
        failures.append({"location": str(path), "reason": "Dictionary missing"})
    database_path = config.root / config.pipeline["database_path"]
    if database_path.exists():
        try:
            with duckdb.connect(str(database_path), read_only=True) as connection:
                frame = connection.execute("SELECT * FROM metadata.variable_dictionary").fetchdf()
            inspect(frame, "metadata.variable_dictionary", True)
        except duckdb.Error as exc:
            failures.append({"location": "metadata.variable_dictionary", "reason": str(exc)})
    return pd.DataFrame(failures, columns=["location", "reason"])


def run_qa(config: ProjectConfig, raise_on_error: bool = True) -> pd.DataFrame:
    logger = logging.getLogger("la_housing.qa")
    final = config.root / "data" / "final"
    fact = pd.read_parquet(final / "fact_housing_observation.parquet")
    macro_monthly_path = final / "macro_financial_monthly.parquet"
    macro_monthly = (
        pd.read_parquet(macro_monthly_path)
        if macro_monthly_path.exists()
        else pd.DataFrame(columns=MONTHLY_IDENTITY_COLUMNS)
    )
    county = pd.read_parquet(final / "county_monthly_panel.parquet")
    zip_panel = pd.read_parquet(final / "zip_monthly_panel.parquet")
    comparable_counties = pd.read_parquet(
        final / "comparable_county_monthly_analytics.parquet"
    )
    zip_market_regimes = pd.read_parquet(
        final / "zip_market_regime_quarterly.parquet"
    )
    zip_market_regime_summary = pd.read_parquet(
        final / "la_zip_market_regime_summary_quarterly.parquet"
    )
    zip_market_regime_transitions = pd.read_parquet(
        final / "la_zip_market_regime_transitions_quarterly.parquet"
    )
    homebuying_scenarios = pd.read_parquet(
        final / "homebuying_cost_scenarios.parquet"
    )
    metro_monthly = pd.read_parquet(final / "metro_monthly_controls.parquet")
    state_quarterly = pd.read_parquet(final / "state_quarterly_panel.parquet")
    state_monthly = pd.read_parquet(final / "state_monthly_controls.parquet")
    state_annual = pd.read_parquet(final / "state_annual_controls.parquet")
    metro_quarterly = pd.read_parquet(final / "metro_quarterly_panel.parquet")
    metro_quarterly_controls = pd.read_parquet(
        final / "metro_quarterly_controls.parquet"
    )
    county_annual = pd.read_parquet(final / "county_annual_controls.parquet")
    zcta_annual = pd.read_parquet(final / "zcta_annual_controls.parquet")
    county_quarterly = pd.read_parquet(
        final / "county_quarterly_controls.parquet"
    )
    coc_annual = pd.read_parquet(final / "coc_annual_controls.parquet")
    fact["observation_date"] = pd.to_datetime(fact["observation_date"])
    county["date"] = pd.to_datetime(county["date"])
    if not zip_panel.empty:
        zip_panel["date"] = pd.to_datetime(zip_panel["date"])
    comparable_counties["date"] = pd.to_datetime(comparable_counties["date"])
    zip_market_regimes["quarter_end"] = pd.to_datetime(
        zip_market_regimes["quarter_end"]
    )
    results: list[CheckResult] = []
    failures: list[pd.DataFrame] = []

    def check(check_id: str, severity: str, frame: pd.DataFrame, detail: str) -> None:
        results.append(_result(check_id, severity, frame, detail))
        failure = _failure_rows(check_id, severity, frame)
        if not failure.empty:
            failures.append(failure)

    primary_key = ["observation_date", "geo_id", "variable_id", "source_vintage"]
    duplicate_fact = fact.loc[fact.duplicated(primary_key, keep=False), primary_key]
    check("duplicate_fact_primary_key", "error", duplicate_fact, "Fact PK must be unique.")
    observation_key = ["observation_date", "geo_id", "variable_id"]
    duplicate_observation = fact.loc[
        fact.duplicated(observation_key, keep=False), observation_key
    ]
    check(
        "duplicate_fact_observation_key",
        "error",
        duplicate_observation,
        "Selected fact data must have one geography-date-variable observation.",
    )
    dictionary_ids = {
        item["variable_id"] for item in config.variables
    }
    unmapped_variables = fact.loc[
        ~fact["variable_id"].isin(dictionary_ids), ["variable_id"]
    ].drop_duplicates()
    check(
        "fact_variable_dictionary_mapping",
        "error",
        unmapped_variables,
        "Every fact variable must map to the variable dictionary.",
    )
    check(
        "variable_unit_temporal_metadata_contract",
        "error",
        _variable_metadata_failures(config),
        "Every configured variable requires a unique explicit temporal type and compatible simplified unit; CSV and SQL dictionaries must agree with the registry.",
    )
    active_zori = fact.loc[
        fact["variable_id"].astype(str).str.startswith("zori")
    ]
    zori_adjustment_error = active_zori.loc[
        ~active_zori["variable_id"].eq("zori_smoothed_sa")
        | ~active_zori["is_seasonally_adjusted"].fillna(False)
    ]
    check(
        "zori_adjustment_identity",
        "error",
        zori_adjustment_error,
        "The only active ZORI fact variable must be seasonally adjusted zori_smoothed_sa.",
    )
    bps_source_ids = {
        "census_bps_county",
        "census_bps_county_annual_final",
        "census_bps_state",
    }
    bps_key = ["observation_date", "geo_id", "variable_id"]
    bps = fact.loc[
        fact["source_id"].isin(bps_source_ids),
        [*bps_key, "state_fips", "county_fips"],
    ]
    duplicate_bps = bps.loc[bps.duplicated(bps_key, keep=False), bps_key]
    check(
        "duplicate_bps_geography_month_variable",
        "error",
        duplicate_bps,
        "BPS must contain one selected archive record per geography-month-variable.",
    )
    invalid_bps_fips = bps.loc[
        (
            bps["geo_id"].eq("COUNTY_06037")
            & (
                ~bps["state_fips"].astype(str).eq("06")
                | ~bps["county_fips"].astype(str).eq("06037")
            )
        )
        | (
            bps["geo_id"].eq("STATE_06")
            & ~bps["state_fips"].astype(str).eq("06")
        ),
        [*bps_key, "state_fips", "county_fips"],
    ]
    check(
        "bps_fips_preserved",
        "error",
        invalid_bps_fips,
        "BPS state and county FIPS must remain fixed-width strings.",
    )
    missing_key = fact.loc[fact[primary_key[:3]].isna().any(axis=1), primary_key]
    check("missing_fact_key", "error", missing_key, "Dates and geography/variable IDs are required.")
    invalid_geo = fact.loc[
        ~fact["geo_id"]
        .astype(str)
        .str.match(GEO_ID_PATTERN),
        primary_key,
    ]
    check(
        "geography_format",
        "error",
        invalid_geo,
        "FIPS, tract, postal ZIP, and ZIP-county-part IDs must retain fixed widths.",
    )
    check(
        "hud_usps_geography_identity",
        "error",
        _hud_usps_geography_identity_failures(fact),
        "HUD-USPS tract and ZIP-county-part IDs must agree with state and county FIPS fields.",
    )
    check(
        "hud_usps_california_county_coverage",
        "error",
        _hud_usps_county_coverage_failures(fact),
        "Every active HUD-USPS quarter-variable series must contain the exact 58 California counties.",
    )
    check(
        "hud_pit_explicit_four_coc_composite",
        "error",
        _hud_pit_composite_failures(fact),
        "HUD PIT composite values must equal the exact four complete native CoC values and must never carry county FIPS.",
    )
    invalid_zip = zip_panel.loc[
        ~zip_panel.get("postal_zip", pd.Series(dtype=str)).astype(str).str.match(r"^\d{5}$")
    ]
    check("zip_format", "error", invalid_zip, "Postal ZIP must be five digits.")
    nonnegative = fact.loc[
        fact["variable_id"].isin(
            {
                "zhvi",
                "zori_smoothed_sa",
                "median_sale_price",
                "active_inventory",
                "new_listings",
                "homes_sold",
                "months_of_supply",
                "days_on_market",
                "unemployment_rate",
                "employment",
                "unemployment",
                "labor_force",
                "mortgage_rate_30y_weekly",
                "mortgage_rate_30y_monthly_average",
                "mortgage_rate_30y_monthly_median",
                "mortgage_rate_30y_month_end",
                "mortgage_rate_30y_observation_count",
                "permits_total",
                "permits_1_unit",
                "permits_1_unit_annual_final",
                "permits_2_to_4_units",
                "permits_5_plus_units",
                "acs_for_rent_vacant_units",
                "acs_for_rent_vacant_units_moe",
                "ca_homeowners_written_premium",
                "ca_homeowners_written_exposures",
                "ca_homeowners_avg_written_premium",
                "ca_homeowners_avg_written_premium_monthly_control",
                "hud_pit_overall_homeless_annual",
                "hud_pit_sheltered_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        )
        & fact["value"].lt(0)
    ]
    check("invalid_negative_value", "error", nonnegative, "Configured level variables cannot be negative.")
    check(
        "duplicate_county_month",
        "error",
        county.loc[county.duplicated("date", keep=False)],
        "County panel must contain one row per month.",
    )
    check(
        "duplicate_zip_month",
        "error",
        zip_panel.loc[zip_panel.duplicated(["date", "postal_zip"], keep=False)]
        if not zip_panel.empty
        else pd.DataFrame(),
        "ZIP panel must contain one row per postal ZIP-month.",
    )
    check(
        "duplicate_comparable_county_month",
        "error",
        comparable_counties.loc[
            comparable_counties.duplicated(
                ["date", "county_fips"], keep=False
            )
        ],
        "Comparable-county analytics must contain one row per county-month.",
    )
    check(
        "comparable_county_document_formulas",
        "error",
        _comparable_county_analytics_failures(comparable_counties, config),
        "All eight-county momentum, valuation, LAUS, fixed three-component tightness, exact seven-peer, and heat calculations must equal the attached-method formulas and preserve missing inputs.",
    )
    check(
        "zip_document_momentum_and_relative_valuation_formulas",
        "error",
        _zip_document_analytics_failures(zip_panel, comparable_counties),
        "LA postal-ZIP momentum, price-to-rent changes, and matched-month relative valuation pressure must use exact calendar log formulas and preserve gaps.",
    )
    check(
        "zip_market_regime_frozen_model_and_outputs",
        "error",
        _zip_market_regime_failures(
            zip_panel,
            zip_market_regimes,
            zip_market_regime_summary,
            zip_market_regime_transitions,
            config.root
            / str(config.pipeline["zip_market_regimes"]["model_path"]),
        ),
        "ZIP regimes must use the fingerprinted frozen 2015Q2-2019Q4 k=4 model, complete four-input nearest-center assignments, explicit coverage denominators, and exact consecutive-quarter transition matrices.",
    )
    check(
        "construction_proxy_structure_and_formulas",
        "error",
        _construction_proxy_failures(county),
        "When five-plus-unit permits are present, all four Central/Slow construction proxies must exist, remain nonnegative, preserve warm-up and source gaps, equal the approved distributed-lag/cohort formulas, and obey the stock-flow recurrence.",
    )
    check(
        "market_tightness_formula_and_coverage",
        "error",
        _market_tightness_failures(
            county,
            int(config.pipeline["market_tightness_min_components"]),
        ),
        "Market tightness must equal the mean of the four negatively oriented full-history z-scores when the configured minimum number of components is available, and its component-count field must reconcile.",
    )
    check(
        "homebuying_cost_scenario_formulas",
        "error",
        _homebuying_scenario_failures(
            homebuying_scenarios, config.pipeline["mortgage"]
        ),
        "Homebuying-cost rows must retain unique scenario keys, approved assumptions, exact formulas, and non-observed/non-interpolated flags.",
    )
    check(
        "county_zillow_valuation_formulas",
        "error",
        _zillow_valuation_failures(county, ["date"]),
        "County Zillow ratio and gross-yield fields must use positive matched-month ZHVI/ZORI inputs and remain exact inverses.",
    )
    check(
        "zip_zillow_valuation_formulas",
        "error",
        _zillow_valuation_failures(zip_panel, ["date", "postal_zip"]),
        "ZIP Zillow ratio and gross-yield fields must use positive matched-month ZHVI/ZORI inputs and preserve gaps.",
    )
    panel_key_checks = [
        (
            "duplicate_metro_month",
            metro_monthly,
            ["date"],
        ),
        (
            "duplicate_state_month",
            state_monthly,
            ["date", "state_fips"],
        ),
        (
            "duplicate_state_quarter",
            state_quarterly,
            ["quarter_end", "state_fips"],
        ),
        (
            "duplicate_state_year",
            state_annual,
            ["date", "state_fips"],
        ),
        (
            "duplicate_metro_quarter",
            metro_quarterly,
            ["quarter_end"],
        ),
        (
            "duplicate_metro_quarter_control",
            metro_quarterly_controls,
            ["date", "geo_id"],
        ),
        (
            "duplicate_county_year",
            county_annual,
            ["date", "county_fips"],
        ),
        (
            "duplicate_coc_year",
            coc_annual,
            ["date", "geo_id"],
        ),
    ]
    for check_id, panel, key in panel_key_checks:
        check(
            check_id,
            "error",
            panel.loc[panel.duplicated(key, keep=False), key],
            f"Panel key must be unique: {key}.",
        )
    annual_panels = {
        "county_monthly": county,
        "zip_monthly": zip_panel,
        "county_quarterly": county_quarterly,
        "county_annual": county_annual,
        "state_monthly": state_monthly,
        "state_quarterly": state_quarterly,
        "state_annual": state_annual,
        "metro_monthly": metro_monthly,
        "metro_quarterly": metro_quarterly,
        "metro_quarterly_controls": metro_quarterly_controls,
        "zcta_annual": zcta_annual,
        "coc_annual": coc_annual,
        "homebuying_scenarios": homebuying_scenarios,
    }
    check(
        "pep_annual_denominators_and_rates",
        "error",
        _pep_annual_rate_failures(fact, county_annual),
        "Observed PEP population and housing-unit denominators must be retained exactly in county annual controls, remain positive where used, and produce the exact matched-year per-1,000 migration and one-unit-permit rates.",
    )
    check(
        "ca_real_homeowners_premium_formula",
        "error",
        _ca_real_premium_failures(fact, state_annual),
        "California real homeowners premium must equal nominal premium times CPI_2023 divided by same-year annual California CPI-U; the 2023 real and nominal values must be identical.",
    )
    check(
        "new_annual_variable_frequency_and_placement",
        "error",
        _new_annual_variable_frequency_failures(fact, annual_panels),
        "Observed PEP/CPI inputs must remain native annual facts, derived rates and real premium must remain panel-only, and none of the new annual fields may appear in monthly, quarterly, or the wrong-geography panels.",
    )
    check(
        "issues_13_15_annual_frequency_geography_and_placement",
        "error",
        _issue_13_15_annual_placement_failures(fact, annual_panels),
        "Issue 13-14 ACS constructions must remain panel-only at their exact county or Census-ZCTA annual geography, while Issue 15 HUD PIT facts may additionally appear only in the native CoC annual panel; none may leak into monthly, quarterly, or wrong-geography schemas.",
    )
    check(
        "hud_pit_annual_panel_fact_reconciliation",
        "error",
        _hud_pit_annual_panel_failures(fact, coc_annual),
        "Every California HUD PIT native-CoC or explicit-composite geography-year in fact must remain in CoC annual controls even when all analytical measures for that row are explicitly null.",
    )
    check(
        "ca_doi_average_premium_yoy_formula",
        "error",
        _ca_doi_yoy_failures(state_annual),
        "California DOI annual average-premium YoY must use the exact prior calendar year with no gap filling.",
    )
    check(
        "ca_doi_monthly_step_control",
        "error",
        _ca_doi_monthly_control_failures(fact, state_monthly),
        "California DOI monthly controls must repeat the same exposure-year annual value across 12 month ends, retain annual provenance, and remain explicitly constructed, carried-annual, mixed-frequency, non-interpolated state controls.",
    )
    check(
        "acs_rental_availability_formula_and_suppression",
        "error",
        _acs_rental_availability_failures(county_annual),
        "ACS rental availability must use matched B25003/B25004 annual inputs, remain in [0,1], and suppress unreliable component MOEs.",
    )
    check(
        "acs_county_price_to_income_matched_vintage_formula",
        "error",
        _acs_county_price_to_income_failures(
            county_annual, set(range(2010, 2025))
        ),
        "County annual ACS price-to-income must equal same-county, same-ending-vintage B25077 median home value divided by positive B19013 median household income for the complete 2010-2024 grid, preserving source nulls and annual timing.",
    )
    check(
        "acs_occupied_apartment_vintage_change",
        "error",
        _acs_occupied_change_failures(
            county_annual,
            county_quarterly,
            set(range(2010, 2025)),
        ),
        "ACS renter-occupied vintage change must use the complete 2010-2024 B25003 county grid, equal consecutive differences, preserve gaps, and remain absent from quarterly controls.",
    )
    configured_zctas, _ = load_zcta_reference(config.root)
    check(
        "acs_zcta_annual_derived_controls",
        "error",
        _acs_zcta_annual_failures(
            zcta_annual,
            set(configured_zctas),
            set(range(2011, 2025)),
        ),
        "Annual ACS ZCTA controls must preserve the exact configured Census-ZCTA/year grid, source nulls, matched-vintage price-to-income formula, and consecutive overlapping-five-year renter-unit differences without postal-ZIP relabeling or quarterly timing.",
    )
    check(
        "acs_noncontrolled_exposure_county_structural_bounds",
        "error",
        _acs_noncontrolled_exposure_failures(county_annual),
        "County annual ACS B25032 structure shares and B25127 recent-construction bounds must reconcile to their source totals. From 2020 onward, noncontrolled exposure must remain lower/upper structural proxy bounds: lower equals the recent-age share and upper unions recent/mixed age bands with one-unit rentals after subtracting their overlap and caps only the reconciled result at one.",
    )
    check(
        "acs_noncontrolled_exposure_zcta_structural_bounds",
        "error",
        _acs_noncontrolled_exposure_failures(zcta_annual),
        "Census-ZCTA annual ACS B25032/B25127 structural bounds must use the same reconciled formulas as the county output while retaining ZCTA identity and never becoming postal-ZIP observations.",
    )
    check(
        "acs_noncontrolled_exposure_schema_and_frequency",
        "error",
        _acs_noncontrolled_schema_failures(
            {
                "fact": fact,
                "county_monthly": county,
                "zip_monthly": zip_panel,
                "homebuying_scenarios": homebuying_scenarios,
                "state_monthly": state_monthly,
                "state_quarterly": state_quarterly,
                "state_annual": state_annual,
                "metro_monthly": metro_monthly,
                "metro_quarterly": metro_quarterly,
                "county_annual": county_annual,
                "zcta_annual": zcta_annual,
                "county_quarterly": county_quarterly,
                "coc_annual": coc_annual,
            }
        ),
        "The ACS structural bounds may exist only in county/ZCTA annual controls; no exact noncontrolled-exposure point or monthly/quarterly expansion is permitted.",
    )
    check(
        "noncontrolled_rent_growth_structural_sensitivity_formula",
        "error",
        _noncontrolled_rent_growth_sensitivity_failures(
            county_annual, county
        ),
        "Annual noncontrolled-rent-growth structural sensitivities must equal the exact-December county ZORI YoY change times the same-year ACS lower/upper structural exposure bounds, with no carry-forward.",
    )
    check(
        "noncontrolled_rent_growth_structural_sensitivity_schema",
        "error",
        _noncontrolled_rent_growth_sensitivity_schema_failures(
            {
                "fact": fact,
                "county_monthly": county,
                "zip_monthly": zip_panel,
                "homebuying_scenarios": homebuying_scenarios,
                "state_monthly": state_monthly,
                "state_quarterly": state_quarterly,
                "state_annual": state_annual,
                "metro_monthly": metro_monthly,
                "metro_quarterly": metro_quarterly,
                "county_annual": county_annual,
                "zcta_annual": zcta_annual,
                "county_quarterly": county_quarterly,
                "coc_annual": coc_annual,
            }
        ),
        "The structural-sensitivity bounds may exist only in county annual controls; the exact noncontrolled-rent-growth target must remain absent.",
    )
    check(
        "metro_geography_not_county",
        "error",
        _metro_geography_failures(fact),
        "Legacy metro-only variables require metro/metropolitan-division geography; only the twelve specified CES industry/earnings level and growth IDs permit California STATE_06 or LA METRODIV_31084, never county or other geographies.",
    )
    annual_ids = {
        item["variable_id"]
        for item in config.variables
        if item.get("final_frequency") == "annual"
    }
    annual_expanded = fact.loc[
        fact["variable_id"].isin(annual_ids)
        & ~fact["final_frequency"].eq("annual")
    ]
    check(
        "annual_not_expanded_to_monthly",
        "error",
        annual_expanded,
        "Annual variables must remain annual.",
    )
    quarterly_ids = {
        item["variable_id"]
        for item in config.variables
        if item.get("final_frequency") == "quarterly"
    }
    quarterly_expanded = fact.loc[
        fact["variable_id"].isin(quarterly_ids)
        & ~fact["final_frequency"].eq("quarterly")
    ]
    check(
        "quarterly_not_expanded_to_monthly",
        "error",
        quarterly_expanded,
        "Quarterly variables must remain quarterly.",
    )
    rental_inventory_mislabel = fact.loc[
        fact["variable_id"].str.contains("rental_inventory", na=False)
        | (
            fact["variable_id"].eq("zori_smoothed_sa")
            & fact["unit"].str.contains("inventory", case=False, na=False)
        )
    ]
    check(
        "zori_not_rental_inventory",
        "error",
        rental_inventory_mislabel,
        "ZORI must never be labeled as rental inventory.",
    )
    catalog_path = config.root / "metadata" / "data_catalog.csv"
    if catalog_path.exists():
        catalog = pd.read_csv(catalog_path, dtype=str)
        expected_first = [
            "Source",
            "Frequency",
            "Geography",
            "Key variables",
        ]
        invalid_catalog_columns = (
            pd.DataFrame()
            if catalog.columns[:4].tolist() == expected_first
            else pd.DataFrame(
                [{"actual": "|".join(catalog.columns[:4])}]
            )
        )
        check(
            "catalog_first_four_columns",
            "error",
            invalid_catalog_columns,
            "Catalog first four columns must match the assignment contract.",
        )
        missing_catalog_sources = pd.DataFrame(
            {
                "source_id": sorted(
                    {
                        source.source_id
                        for source in config.sources
                        if source.enabled
                    }
                    - set(catalog["source_id"])
                )
            }
        )
        check(
            "active_source_catalog_mapping",
            "error",
            missing_catalog_sources,
            "Every active configured source must have a catalog row.",
        )
    raw_completeness_path = (
        config.root / "output" / "qa" / "raw_repository_completeness.csv"
    )
    if raw_completeness_path.exists():
        raw_completeness = pd.read_csv(
            raw_completeness_path, dtype=str
        )
        active_rows = (
            raw_completeness["active_status"].eq("Active")
            if "active_status" in raw_completeness
            else pd.Series(True, index=raw_completeness.index)
        )
        check(
            "raw_repository_completeness",
            "error",
            raw_completeness.loc[
                active_rows
                & ~raw_completeness["completeness_status"].eq("PASS")
            ],
            "Every active implemented source must pass raw completeness.",
        )
    redfin_zip_path = (
        config.root
        / "data"
        / "clean"
        / "redfin_zip_monthly_la_county.parquet"
    )
    if redfin_zip_path.exists():
        redfin_zip = pd.read_parquet(redfin_zip_path)
        redfin_zip["period_begin"] = pd.to_datetime(
            redfin_zip["period_begin"]
        )
        redfin_zip["period_end"] = pd.to_datetime(
            redfin_zip["period_end"]
        )
        redfin_key = ["postal_zip", "period_end", "source_vintage"]
        check(
            "redfin_zip_duplicate_key",
            "error",
            redfin_zip.loc[
                redfin_zip.duplicated(redfin_key, keep=False), redfin_key
            ],
            "Redfin ZIP clean rows must be unique by ZIP, period end, and source vintage.",
        )
        check(
            "redfin_zip_identifier_format",
            "error",
            redfin_zip.loc[
                ~redfin_zip["postal_zip"]
                .astype(str)
                .str.fullmatch(r"\d{5}")
            ],
            "Redfin postal ZIP identifiers must be five-character strings.",
        )
        invalid_method = redfin_zip.loc[
            ~redfin_zip["region_type"].eq("Zip")
            | ~redfin_zip["frequency"].eq("Rolling 3 Months")
            | ~redfin_zip["rolling_window_months"].eq(3)
        ]
        check(
            "redfin_zip_source_method",
            "error",
            invalid_method,
            "Loaded Redfin rows must be ZIP regions with Rolling 3 Months frequency.",
        )
        window_months = (
            (
                redfin_zip["period_end"].dt.year
                - redfin_zip["period_begin"].dt.year
            )
            * 12
            + redfin_zip["period_end"].dt.month
            - redfin_zip["period_begin"].dt.month
            + 1
        )
        invalid_window = redfin_zip.loc[
            redfin_zip["period_begin"].gt(redfin_zip["period_end"])
            | window_months.ne(3)
            | redfin_zip["period_begin"].dt.day.ne(1)
            | redfin_zip["period_end"].ne(
                redfin_zip["period_end"] + pd.offsets.MonthEnd(0)
            )
        ]
        check(
            "redfin_zip_window_dates",
            "error",
            invalid_window,
            "Redfin windows must span three calendar months from month start to month end.",
        )
        redfin_levels = [
            "homes_sold",
            "median_sale_price_nsa",
            "median_days_on_market",
            "new_listings",
            "active_listings",
        ]
        invalid_redfin_levels = redfin_zip.loc[
            redfin_zip[redfin_levels].lt(0).any(axis=1)
        ]
        check(
            "redfin_zip_nonnegative_levels",
            "error",
            invalid_redfin_levels,
            "Redfin ZIP Key Metrics level values cannot be negative.",
        )
        percentage_columns = [
            column
            for column in redfin_zip.columns
            if column.endswith("_yoy_pct_provider")
        ]
        extreme_percentages = redfin_zip.loc[
            redfin_zip[percentage_columns].abs().gt(1000).any(axis=1)
        ]
        check(
            "redfin_zip_provider_percentage_review",
            "warning",
            extreme_percentages,
            "Provider YoY values above 1,000 percentage points require review but are retained.",
        )
        geography = pd.read_parquet(
            config.root / "data" / "clean" / "geography.parquet"
        )
        eligible_zips = set(
            geography.loc[
                geography["county_fips"].astype(str).eq("06037"),
                "postal_zip",
            ]
            .dropna()
            .astype(str)
        )
        invalid_county = redfin_zip.loc[
            ~redfin_zip["postal_zip"].astype(str).isin(eligible_zips)
            | ~redfin_zip["primary_county_fips"].astype(str).eq("06037")
        ]
        check(
            "redfin_zip_verified_la_county",
            "error",
            invalid_county,
            "Only ZIPs in the versioned LA County geography reference may enter the clean subset.",
        )
        expected_spine = fact.loc[
            fact["geo_id"].astype(str).str.startswith("ZIP_")
            & fact["variable_id"].isin({"zhvi", "zori_smoothed_sa"}),
            ["observation_date", "geo_id"],
        ].drop_duplicates()
        expected_spine["postal_zip"] = expected_spine.pop(
            "geo_id"
        ).str.removeprefix("ZIP_")
        expected_spine = expected_spine.rename(
            columns={"observation_date": "date"}
        )
        actual_spine = zip_panel[["date", "postal_zip"]].drop_duplicates()
        spine_difference = expected_spine.merge(
            actual_spine,
            on=["date", "postal_zip"],
            how="outer",
            indicator=True,
        )
        check(
            "redfin_zip_left_join_preserves_zillow_spine",
            "error",
            spine_difference.loc[spine_difference["_merge"].ne("both")],
            "Redfin must be left-joined without adding or removing Zillow ZIP-month keys.",
        )
        redfin_panel = redfin_zip.merge(
            zip_panel,
            left_on=["period_end", "postal_zip"],
            right_on=["date", "postal_zip"],
            how="left",
            suffixes=("_clean", "_panel"),
        )
        panel_mismatch_rows: list[pd.DataFrame] = []
        panel_pairs = {
            "homes_sold": "homes_sold",
            "median_sale_price_nsa": "median_sale_price_nsa",
            "median_days_on_market": "median_days_on_market",
            "new_listings": "new_listings",
            "active_listings": "active_listings",
        }
        for clean_column, panel_column in panel_pairs.items():
            clean_values = redfin_panel[f"{clean_column}_clean"]
            panel_values = redfin_panel[f"{panel_column}_panel"]
            mismatch = ~(
                clean_values.eq(panel_values)
                | (clean_values.isna() & panel_values.isna())
            )
            if mismatch.any():
                rows = redfin_panel.loc[
                    mismatch, ["postal_zip", "period_end"]
                ].copy()
                rows["metric"] = clean_column
                panel_mismatch_rows.append(rows)
        check(
            "redfin_zip_panel_matches_clean",
            "error",
            pd.concat(panel_mismatch_rows, ignore_index=True)
            if panel_mismatch_rows
            else pd.DataFrame(),
            "ZIP panel Redfin levels must exactly match clean Key Metrics values.",
        )
    sa_fact = (
        fact.loc[fact["variable_id"].eq("zori_smoothed_sa")]
        .sort_values(["source_vintage", "retrieved_at"])
        .drop_duplicates(["observation_date", "geo_id"], keep="last")
    )
    county_sa = sa_fact.loc[
        sa_fact["geo_id"].eq("COUNTY_06037"),
        ["observation_date", "value"],
    ].rename(columns={"observation_date": "date", "value": "clean_sa_zori"})
    county_zori_comparison = county[["date", "zori"]].merge(
        county_sa, on="date", how="left"
    )
    county_primary_mismatch = county_zori_comparison.loc[
        ~(
            county_zori_comparison["zori"].eq(
                county_zori_comparison["clean_sa_zori"]
            )
            | (
                county_zori_comparison["zori"].isna()
                & county_zori_comparison["clean_sa_zori"].isna()
            )
        )
    ]
    check(
        "county_primary_zori_is_sa",
        "error",
        county_primary_mismatch,
        "County primary zori must equal the active county SA clean series exactly.",
    )
    if zip_panel.empty:
        zip_primary_mismatch = pd.DataFrame()
    else:
        zip_sa = sa_fact.loc[
            sa_fact["geo_id"].astype(str).str.startswith("ZIP_"),
            ["observation_date", "geo_id", "value"],
        ].copy()
        zip_sa["postal_zip"] = zip_sa.pop("geo_id").str.removeprefix("ZIP_")
        zip_sa = zip_sa.rename(
            columns={"observation_date": "date", "value": "clean_sa_zori"}
        )
        zip_zori_comparison = zip_panel[["date", "postal_zip", "zori"]].merge(
            zip_sa, on=["date", "postal_zip"], how="left"
        )
        zip_primary_mismatch = zip_zori_comparison.loc[
            ~(
                zip_zori_comparison["zori"].eq(
                    zip_zori_comparison["clean_sa_zori"]
                )
                | (
                    zip_zori_comparison["zori"].isna()
                    & zip_zori_comparison["clean_sa_zori"].isna()
                )
            )
        ]
    check(
        "zip_primary_zori_is_sa",
        "error",
        zip_primary_mismatch,
        "ZIP primary zori must equal the active ZIP SA clean series exactly.",
    )
    ordered_county = county.sort_values("date").copy()
    expected_features = {
        "zori_growth_mom": ordered_county["zori"].pct_change(
            1, fill_method=None
        ),
        "zori_growth_yoy": ordered_county["zori"].pct_change(
            12, fill_method=None
        ),
        "price_to_rent_ratio": ordered_county["zhvi"]
        / (12.0 * ordered_county["zori"]),
        "price_to_rent_ratio_zillow": ordered_county["zhvi"]
        / (12.0 * ordered_county["zori"]),
        "implied_gross_rent_yield": (
            12.0 * ordered_county["zori"] / ordered_county["zhvi"]
        ),
    }
    for down_payment in ("10pct", "20pct"):
        payment = f"mortgage_payment_{down_payment}_down"
        ratio = f"mortgage_payment_to_rent_{down_payment}_down"
        if payment in ordered_county:
            expected_features[ratio] = (
                ordered_county[payment]
                / ordered_county["zori"]
            )
    derived_failures: list[pd.DataFrame] = []
    for column, expected in expected_features.items():
        actual = ordered_county[column]
        mismatch = ~(
            (actual.isna() & expected.isna())
            | (actual.sub(expected).abs() <= 1e-10)
        )
        if mismatch.any():
            rows = ordered_county.loc[mismatch, ["date"]].copy()
            rows["variable"] = column
            rows["actual"] = actual.loc[mismatch]
            rows["expected_from_sa"] = expected.loc[mismatch]
            derived_failures.append(rows)
    check(
        "zori_derived_features_use_sa",
        "error",
        pd.concat(derived_failures, ignore_index=True)
        if derived_failures
        else pd.DataFrame(),
        "ZORI growth, price-to-rent, and payment-to-rent must use SA ZORI.",
    )
    monthly = fact.loc[
        fact["final_frequency"].eq("monthly"),
        ["provider", "variable_id", "geo_id", "observation_date"],
    ].copy()
    gap_rows: list[dict] = []
    for (geo_id, variable_id), group in monthly.groupby(["geo_id", "variable_id"]):
        periods = group["observation_date"].dt.to_period("M").drop_duplicates().sort_values()
        if len(periods) > 1:
            gaps = periods.astype(int).diff().dropna()
            if gaps.gt(1).any():
                gap_rows.append(
                    {
                        "geo_id": geo_id,
                        "variable_id": variable_id,
                        "largest_gap_months": int(gaps.max()),
                    }
                )
    check(
        "non_monthly_gaps",
        "warning",
        pd.DataFrame(gap_rows),
        "Gaps are retained, never silently interpolated.",
    )
    stale = (
        monthly.groupby(["provider", "variable_id"])["observation_date"]
        .max()
        .reset_index()
    )
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(
        days=int(config.pipeline["stale_after_days"])
    )
    stale = stale.loc[stale["observation_date"] < cutoff]
    check("stale_latest_observation", "warning", stale, f"Latest date precedes {cutoff.date()}.")
    extreme_columns = [
        column
        for column in county
        if column.endswith("_growth_mom") or column.endswith("_growth_yoy")
    ]
    outliers: list[pd.DataFrame] = []
    for column in extreme_columns:
        threshold = (
            float(config.pipeline["extreme_mom_threshold"])
            if column.endswith("_mom")
            else float(config.pipeline["extreme_yoy_threshold"])
        )
        flagged = county.loc[county[column].abs() > threshold, ["date", column]].copy()
        if not flagged.empty:
            flagged["variable"] = column
            flagged["threshold"] = threshold
            flagged["value"] = flagged.pop(column)
            outliers.append(flagged)
    check(
        "extreme_economic_movement",
        "warning",
        pd.concat(outliers, ignore_index=True) if outliers else pd.DataFrame(),
        "Plausible outliers are warnings and remain in the data.",
    )
    zip_outliers: list[pd.DataFrame] = []
    for column in (
        "zhvi_growth_mom",
        "zhvi_growth_yoy",
        "zori_growth_mom",
        "zori_growth_yoy",
    ):
        if column not in zip_panel:
            continue
        threshold = (
            float(config.pipeline["extreme_mom_threshold"])
            if column.endswith("_mom")
            else float(config.pipeline["extreme_yoy_threshold"])
        )
        flagged = zip_panel.loc[
            pd.to_numeric(zip_panel[column], errors="coerce").abs() > threshold,
            ["date", "postal_zip", column],
        ].copy()
        if not flagged.empty:
            flagged["variable"] = column
            flagged["threshold"] = threshold
            flagged["value"] = flagged.pop(column)
            zip_outliers.append(flagged)
    check(
        "extreme_zip_zillow_movement",
        "warning",
        pd.concat(zip_outliers, ignore_index=True)
        if zip_outliers
        else pd.DataFrame(),
        "Plausible postal-ZIP Zillow growth outliers are warnings and remain in the data.",
    )
    if not zip_panel.empty and "zhvi" in zip_panel and "zhvi" in county:
        bounds = (
            zip_panel.groupby("date")["zhvi"].agg(zip_min="min", zip_max="max").reset_index()
        )
        comparison = county[["date", "zhvi"]].merge(bounds, on="date", how="inner")
        inconsistent = comparison.loc[
            comparison["zhvi"].notna()
            & (
                (comparison["zhvi"] < comparison["zip_min"] * 0.5)
                | (comparison["zhvi"] > comparison["zip_max"] * 1.5)
            )
        ]
    else:
        inconsistent = pd.DataFrame()
    check(
        "county_zip_consistency",
        "warning",
        inconsistent,
        "County ZHVI is checked against a broad ZIP range; levels are not aggregated as equivalent.",
    )
    weekly = fact.loc[
        fact["variable_id"].eq("mortgage_rate_30y_weekly"),
        ["observation_date", "value"],
    ].copy()
    if not weekly.empty:
        weekly = weekly.sort_values("observation_date")
        weekly_monthly = weekly.assign(
            month=weekly["observation_date"].dt.to_period("M")
        )
        calculated_mean = (
            weekly_monthly.groupby("month")["value"]
            .mean()
            .rename("calculated")
            .reset_index()
        )
        calculated_median = (
            weekly_monthly.groupby("month")["value"]
            .median()
            .rename("calculated")
            .reset_index()
        )
        calculated_count = (
            weekly_monthly.groupby("month")["value"]
            .count()
            .rename("calculated")
            .reset_index()
        )
        calculated_month_end = (
            weekly_monthly.groupby("month", as_index=False)
            .tail(1)[["month", "value"]]
            .rename(columns={"value": "calculated"})
        )
        expected_monthly = {
            "mortgage_rate_30y_monthly_average": calculated_mean,
            "mortgage_rate_30y_monthly_median": calculated_median,
            "mortgage_rate_30y_month_end": calculated_month_end,
            "mortgage_rate_30y_observation_count": calculated_count,
        }
    else:
        expected_monthly = {}
    for variable_id, calculated in expected_monthly.items():
        calculated = calculated.copy()
        calculated["observation_date"] = calculated["month"].dt.to_timestamp("M")
        reported = fact.loc[
            fact["variable_id"].eq(variable_id),
            ["observation_date", "value"],
        ].rename(columns={"value": "reported"})
        if reported.empty:
            aggregation_error = pd.DataFrame(
                [{"variable_id": variable_id, "detail": "missing monthly series"}]
            )
        else:
            comparison = reported.merge(
                calculated[["observation_date", "calculated"]],
                on="observation_date",
                how="outer",
            )
            aggregation_error = comparison.loc[
                ~(
                    comparison["reported"].notna()
                    & comparison["calculated"].notna()
                    & (
                        comparison["reported"] - comparison["calculated"]
                    ).abs().le(1e-10)
                )
            ].copy()
            aggregation_error["variable_id"] = variable_id
        check(
            f"{variable_id}_aggregation",
            "error",
            aggregation_error,
            f"{variable_id} must match the documented weekly-to-monthly PMMS aggregation.",
        )

    check(
        "macro_financial_source_frequency_formula_contract", "error",
        macro_financial_failures(fact),
        "New macro controls retain published geography/native frequency; exact CES/GDP growth and completed-month rates/claims means reconcile to source observations.",
    )
    check(
        "macro_financial_monthly_delivery_contract", "error",
        macro_monthly_panel_failures(
            fact,
            macro_monthly,
            require_complete=bool(config.pipeline.get("macro_monthly_delivery", {}).get("require_complete_contract", False)),
        ),
        "Monthly macro delivery must reconcile to monthly facts for all authorized series; daily/weekly observations and the explicitly retained quarterly GDP are excluded.",
    )
    _coverage_reports(config, fact, county, zip_panel)
    summary = pd.DataFrame([result.__dict__ for result in results])
    failure_output = (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame(columns=["check_id", "severity"])
    )
    qa_dir = config.root / "output" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(qa_dir / "qa_summary.csv", index=False)
    failure_output.to_csv(qa_dir / "qa_failures.csv", index=False)
    database_path = config.root / config.pipeline["database_path"]
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("qa_summary_frame", summary)
        connection.register("qa_failures_frame", failure_output)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.qa_summary AS SELECT * FROM qa_summary_frame"
        )
        connection.execute(
            "CREATE OR REPLACE TABLE quality.qa_failures AS SELECT * FROM qa_failures_frame"
        )
        connection.close()
    errors = summary.loc[
        summary["severity"].eq("error") & summary["failure_count"].gt(0)
    ]
    warnings = summary.loc[
        summary["severity"].eq("warning") & summary["failure_count"].gt(0)
    ]
    logger.info("QA completed: %s errors, %s warning checks", len(errors), len(warnings))
    if raise_on_error and not errors.empty:
        names = ", ".join(errors["check_id"])
        raise RuntimeError(f"Structural QA failed: {names}")
    return summary
