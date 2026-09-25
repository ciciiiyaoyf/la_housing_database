from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.derived import (
    APARTMENT_COMPLETION_PROXY_COLUMNS,
    MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS,
    apartment_completion_proxies,
    multifamily_under_construction_proxies,
)
from src.validation.qa import (
    CONSTRUCTION_PROXY_COLUMNS,
    _ca_real_premium_failures,
    _construction_proxy_failures,
    _new_annual_variable_frequency_failures,
    _pep_annual_rate_failures,
)


def _construction_frame(periods: int = 80, permits: float = 10.0) -> pd.DataFrame:
    source = pd.DataFrame(
        {
            "date": pd.date_range("2010-01-31", periods=periods, freq="ME"),
            "county_fips": "06037",
            "permits_5_plus_units": permits,
        }
    )
    result = apartment_completion_proxies(source)
    return multifamily_under_construction_proxies(result)


def test_construction_qa_accepts_exact_helpers_and_genuine_source_absence() -> None:
    assert _construction_proxy_failures(_construction_frame()).empty
    assert _construction_proxy_failures(
        pd.DataFrame({"date": pd.to_datetime(["2020-01-31"])})
    ).empty
    assert _construction_proxy_failures(
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2020-01-31"]),
                "county_fips": ["06037"],
                "permits_5_plus_units": [np.nan],
            }
        )
    ).empty


def test_construction_qa_requires_all_four_outputs_when_permits_exist() -> None:
    source = _construction_frame().drop(columns=list(CONSTRUCTION_PROXY_COLUMNS))
    failures = _construction_proxy_failures(source)

    missing = failures.loc[
        failures["failure_reason"].eq("missing_proxy_column_with_permits_present"),
        "variable_id",
    ]
    assert set(missing) == set(CONSTRUCTION_PROXY_COLUMNS)


def test_construction_qa_rejects_warmup_gap_formula_and_negative_failures() -> None:
    result = _construction_frame()
    central_completion = APARTMENT_COMPLETION_PROXY_COLUMNS["central"]
    central_stock = MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS["central"]
    result.loc[0, central_completion] = 1.0
    result.loc[45, central_completion] += 1.0
    result.loc[45, central_stock] = -1.0

    failures = _construction_proxy_failures(result)
    reasons = set(failures["failure_reason"])

    assert "proxy_nonnull_without_complete_required_lags" in reasons
    assert "distributed_lag_formula_mismatch" in reasons
    assert "negative_proxy_value" in reasons
    assert "cohort_stock_formula_mismatch" in reasons
    assert "stock_flow_recurrence_mismatch" in reasons


def test_construction_qa_rejects_value_when_a_required_permit_lag_is_missing() -> None:
    result = _construction_frame()
    result.loc[40, "permits_5_plus_units"] = np.nan
    result = apartment_completion_proxies(
        result.drop(columns=list(CONSTRUCTION_PROXY_COLUMNS))
    )
    result = multifamily_under_construction_proxies(result)
    central_completion = APARTMENT_COMPLETION_PROXY_COLUMNS["central"]
    assert pd.isna(result.loc[52, central_completion])
    result.loc[52, central_completion] = 0.0

    failures = _construction_proxy_failures(result)
    row = failures.loc[
        failures["variable_id"].eq(central_completion)
        & failures["date"].eq(result.loc[52, "date"])
    ]
    assert "proxy_nonnull_without_complete_required_lags" in set(
        row["failure_reason"]
    )


def _fact_row(
    date: str,
    variable_id: str,
    value: float,
    *,
    geo_id: str = "COUNTY_06037",
    native_frequency: str = "annual",
    final_frequency: str = "annual",
) -> dict[str, object]:
    return {
        "observation_date": pd.Timestamp(date),
        "geo_id": geo_id,
        "variable_id": variable_id,
        "value": value,
        "native_frequency": native_frequency,
        "final_frequency": final_frequency,
        "is_imputed": False,
        "is_interpolated": False,
    }


def _pep_fact_and_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    fact = pd.DataFrame(
        [
            _fact_row("2022-12-31", "census_pep_county_population_annual", 10_000),
            _fact_row("2023-12-31", "census_pep_county_population_annual", 11_000),
            _fact_row("2022-12-31", "census_pep_county_housing_units_annual", 4_000),
            _fact_row("2023-12-31", "census_pep_county_housing_units_annual", 4_100),
            _fact_row("2022-12-31", "irs_soi_net_migration_exemptions", -100),
            _fact_row("2023-12-31", "irs_soi_net_migration_exemptions", 220),
            _fact_row("2022-12-31", "permits_1_unit_annual_final", 40),
            _fact_row("2023-12-31", "permits_1_unit_annual_final", 41),
        ]
    )
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-31", "2023-12-31"]),
            "county_fips": "06037",
            "census_pep_county_population_annual": [10_000.0, 11_000.0],
            "census_pep_county_housing_units_annual": [4_000.0, 4_100.0],
            "irs_soi_net_migration_exemptions": [-100.0, 220.0],
            "permits_1_unit_annual_final": [40.0, 41.0],
            "net_exemption_rate": [-10.0, 20.0],
            "permits_1_unit_per_1000_housing_units": [10.0, 10.0],
        }
    )
    return fact, panel


def test_pep_rate_qa_accepts_exact_matched_year_formulas_and_absent_sources() -> None:
    fact, panel = _pep_fact_and_panel()
    assert _pep_annual_rate_failures(fact, panel).empty

    # Final annual BPS begins before the configured PEP segment. A numerator
    # outside the observed denominator span must remain null, not be treated as
    # a missing same-year denominator inside 2010-2025.
    fact = pd.concat(
        [
            fact,
            pd.DataFrame(
                [_fact_row("2009-12-31", "permits_1_unit_annual_final", 25)]
            ),
        ],
        ignore_index=True,
    )
    panel = pd.concat(
        [
            panel,
            pd.DataFrame(
                [
                    {
                        "date": pd.Timestamp("2009-12-31"),
                        "county_fips": "06037",
                        "census_pep_county_population_annual": np.nan,
                        "census_pep_county_housing_units_annual": np.nan,
                        "irs_soi_net_migration_exemptions": np.nan,
                        "permits_1_unit_annual_final": 25.0,
                        "net_exemption_rate": np.nan,
                        "permits_1_unit_per_1000_housing_units": np.nan,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    assert _pep_annual_rate_failures(fact, panel).empty

    empty_fact = fact.iloc[0:0]
    empty_panel = pd.DataFrame({"date": pd.to_datetime(["2023-12-31"])})
    assert _pep_annual_rate_failures(empty_fact, empty_panel).empty


def test_pep_rate_qa_rejects_missing_nonpositive_and_mismatched_denominators() -> None:
    fact, panel = _pep_fact_and_panel()
    panel.loc[0, "census_pep_county_population_annual"] = np.nan
    panel.loc[1, "census_pep_county_housing_units_annual"] = 0.0
    panel.loc[1, "net_exemption_rate"] = 999.0

    failures = _pep_annual_rate_failures(fact, panel)
    reasons = set(failures["failure_reason"])

    assert "annual_panel_not_equal_to_observed_fact" in reasons
    assert "missing_pep_denominator_for_numerator" in reasons
    assert "nonpositive_pep_denominator" in reasons
    assert "annual_rate_formula_mismatch" in reasons


def _cpi_fact_and_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    fact = pd.DataFrame(
        [
            _fact_row(
                "2022-12-31",
                "ca_cpi_u_all_items_annual",
                200.0,
                geo_id="STATE_06",
            ),
            _fact_row(
                "2023-12-31",
                "ca_cpi_u_all_items_annual",
                220.0,
                geo_id="STATE_06",
            ),
            _fact_row(
                "2022-12-31",
                "ca_homeowners_avg_written_premium",
                1_000.0,
                geo_id="STATE_06",
            ),
            _fact_row(
                "2023-12-31",
                "ca_homeowners_avg_written_premium",
                1_200.0,
                geo_id="STATE_06",
            ),
        ]
    )
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-31", "2023-12-31"]),
            "state_fips": "06",
            "ca_cpi_u_all_items_annual": [200.0, 220.0],
            "ca_homeowners_avg_written_premium": [1_000.0, 1_200.0],
            "ca_homeowners_avg_written_premium_real": [1_100.0, 1_200.0],
        }
    )
    return fact, panel


def test_real_premium_qa_accepts_formula_and_2023_identity() -> None:
    fact, panel = _cpi_fact_and_panel()
    assert _ca_real_premium_failures(fact, panel).empty
    assert _ca_real_premium_failures(
        fact.iloc[0:0], pd.DataFrame({"date": pd.to_datetime([])})
    ).empty


def test_real_premium_qa_rejects_formula_and_2023_identity_mismatch() -> None:
    fact, panel = _cpi_fact_and_panel()
    panel.loc[0, "ca_homeowners_avg_written_premium_real"] = 1_099.0
    panel.loc[1, "ca_homeowners_avg_written_premium_real"] = 1_199.0

    failures = _ca_real_premium_failures(fact, panel)
    reasons = set(failures["failure_reason"])

    assert "real_premium_formula_mismatch" in reasons
    assert "real_premium_2023_base_identity_mismatch" in reasons


def test_new_annual_frequency_qa_enforces_fact_and_panel_placement() -> None:
    observed = pd.DataFrame(
        [
            _fact_row("2023-12-31", "census_pep_county_population_annual", 10),
            _fact_row(
                "2023-12-31",
                "ca_cpi_u_all_items_annual",
                300,
                geo_id="STATE_06",
            ),
        ]
    )
    allowed = {
        "county_annual": pd.DataFrame(
            {
                "date": pd.to_datetime(["2023-12-31"]),
                "census_pep_county_population_annual": [10.0],
                "net_exemption_rate": [1.0],
            }
        ),
        "state_annual": pd.DataFrame(
            {
                "date": pd.to_datetime(["2023-12-31"]),
                "ca_cpi_u_all_items_annual": [300.0],
                "ca_homeowners_avg_written_premium_real": [1_000.0],
            }
        ),
        "county_monthly": pd.DataFrame(),
        "county_quarterly": pd.DataFrame(),
        "state_monthly": pd.DataFrame(),
        "state_quarterly": pd.DataFrame(),
    }
    assert _new_annual_variable_frequency_failures(observed, allowed).empty

    invalid_fact = pd.concat(
        [
            observed.assign(final_frequency="monthly"),
            pd.DataFrame(
                [
                    _fact_row(
                        "2023-12-31",
                        "net_exemption_rate",
                        1.0,
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    invalid_panels = allowed | {
        "county_quarterly": pd.DataFrame(
            {"permits_1_unit_per_1000_housing_units": [np.nan]}
        )
    }
    failures = _new_annual_variable_frequency_failures(
        invalid_fact, invalid_panels
    )
    reasons = set(failures["failure_reason"])

    assert "observed_annual_fact_frequency_changed" in reasons
    assert "derived_annual_variable_must_not_be_in_fact" in reasons
    assert "annual_variable_exposed_outside_approved_panel" in reasons
