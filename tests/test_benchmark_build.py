from __future__ import annotations

import math

import pandas as pd
import pytest

from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.benchmark.build import build_version_a_benchmark


def _acs_rows() -> pd.DataFrame:
    values = {
        "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark": (
            1_000_000.0,
            900_000.0,
            1_100_000.0,
        ),
        "acs_mortgaged_owner_units_benchmark": (1_000.0, 900.0, 1_100.0),
        "acs_aggregate_household_income_mortgaged_owner_benchmark": (
            60_000_000.0,
            55_000_000.0,
            65_000_000.0,
        ),
        "acs_aggregate_owner_value_mortgaged_benchmark": (
            1_000_000_000.0,
            950_000_000.0,
            1_050_000_000.0,
        ),
        "acs_aggregate_owner_value_no_mortgage_benchmark": (
            400_000_000.0,
            380_000_000.0,
            420_000_000.0,
        ),
        "acs_owner_occupied_units_benchmark": (
            1_500.0,
            1_400.0,
            1_600.0,
        ),
        "acs_total_population_benchmark": (
            10_000.0,
            9_500.0,
            10_500.0,
        ),
        "acs_aggregate_household_income_owner_benchmark": (
            90_000_000.0,
            85_000_000.0,
            95_000_000.0,
        ),
    }
    rows = []
    for county_fips in CALIFORNIA_COUNTY_FIPS:
        multiplier = 2.0 if county_fips == "06037" else 1.0
        for variable, (value, low, high) in values.items():
            rows.append(
                {
                    "county_fips": county_fips,
                    "state_fips": "06",
                    "month": None,
                    "value": value * multiplier,
                    "variable_name": variable,
                    "database_version": "A. Benchmark",
                    "source_frequency": "5-year",
                    "source_period_start": "2020-01-01",
                    "source_period_end": "2024-12-31",
                    "reference_date": "2022-07-01",
                    "release_date": "2025-01-30",
                    "source_geography": "county",
                    "parameter_geography": "county",
                    "monthly_method": "not_applicable_source_period",
                    "is_modeled": False,
                    "is_nowcast": False,
                    "benchmark_vintage": "2024_ACS5",
                    "sample_n": None,
                    "coverage_rate": None,
                    "low_estimate": low * multiplier,
                    "central_estimate": value * multiplier,
                    "high_estimate": high * multiplier,
                    "universe_definition": "Owner occupied",
                    "notes": "Published ACS estimate",
                    "unit": "USD",
                    "source_filename": "acs.json",
                    "source_vintage": "2024",
                    "retrieved_at": "2026-01-30T00:00:00+00:00",
                    "checksum_sha256": "abc",
                    "quality_flag": "reported",
                }
            )
    return pd.DataFrame(rows)


def _pums() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "benchmark_vintage": "2024_ACS5",
                "reference_date": "2022-07-01",
                "state_fips": "06",
                "county_fips": None,
                "parameter_geography": "state:06",
                "parameter_geography_level": "state",
                "value": 0.6,
                "sample_n": 100,
                "coverage_rate": 0.8,
                "county_all_pumas_wholly_contained": True,
                "source_filename": "pums.zip",
                "source_vintage": "2024",
                "retrieved_at": "2026-03-06T00:00:00+00:00",
                "checksum_sha256": "pums-checksum",
                "relationship_vintage": 2020,
                "relationship_source_filename": "tract_puma_2020.txt",
                "relationship_retrieved_at": "2026-03-06T00:00:00+00:00",
                "relationship_checksum_sha256": "relationship-checksum",
                "release_date": "2026-03-05",
            },
            {
                "benchmark_vintage": "2024_ACS5",
                "reference_date": "2022-07-01",
                "state_fips": "06",
                "county_fips": "06001",
                "parameter_geography": "county:06001",
                "parameter_geography_level": "county",
                "value": 0.5,
                "sample_n": 50,
                "coverage_rate": 0.7,
                "county_all_pumas_wholly_contained": True,
                "source_filename": "pums.zip",
                "source_vintage": "2024",
                "retrieved_at": "2026-03-06T00:00:00+00:00",
                "checksum_sha256": "pums-checksum",
                "relationship_vintage": 2020,
                "relationship_source_filename": "tract_puma_2020.txt",
                "relationship_retrieved_at": "2026-03-06T00:00:00+00:00",
                "relationship_checksum_sha256": "relationship-checksum",
                "release_date": "2026-03-05",
            },
            {
                "benchmark_vintage": "2024_ACS5",
                "reference_date": "2022-07-01",
                "state_fips": "06",
                "county_fips": "06037",
                "parameter_geography": "county:06037",
                "parameter_geography_level": "county",
                "value": 0.55,
                "sample_n": 60,
                "coverage_rate": 0.75,
                "county_all_pumas_wholly_contained": False,
                "source_filename": "pums.zip",
                "source_vintage": "2024",
                "retrieved_at": "2026-03-06T00:00:00+00:00",
                "checksum_sha256": "pums-checksum",
                "relationship_vintage": 2020,
                "relationship_source_filename": "tract_puma_2020.txt",
                "relationship_retrieved_at": "2026-03-06T00:00:00+00:00",
                "relationship_checksum_sha256": "relationship-checksum",
                "release_date": "2026-03-05",
            },
        ]
    )


def _rates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "benchmark_vintage": "2024_ACS5",
                "reference_date": "2022-07-01",
                "value": 0.0,
                "parameter_geography": "US",
                "source_id": "freddie_mac_pmms",
                "source_vintage": "2026-03-01",
                "retrieved_at": "2026-03-02T00:00:00+00:00",
                "source_filename": "PMMS_history.csv",
                "checksum_sha256": "pmms-checksum",
                "release_date": "unavailable",
            }
        ]
    )


def _california_upb(
    *,
    benchmark_vintage: str = "2024_ACS5",
    reference_date: str = "2022-07-01",
    value: float = 900_000_000.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "benchmark_vintage": benchmark_vintage,
                "reference_date": reference_date,
                "value": value,
                "parameter_geography": "California",
                "source_period_start": reference_date,
                "source_period_end": reference_date,
                "release_date": "2025-03-28",
                "source_release_dates": (
                    "fhfa_nmdb_ca_upb=2025-03-28"
                ),
                "source_frequency": "published dashboard period",
                "source_id": "fhfa_nmdb_ca_upb",
                "source_vintage": "NMDB_CA_UPB_2025-03-28",
                "retrieved_at": "2025-04-02T17:00:00Z",
                "source_filename": "nmdb_ca_upb.csv",
                "checksum_sha256": "b" * 64,
                "source_page_url": (
                    "https://www.fhfa.gov/data/dashboard/"
                    "nmdb-outstanding-residential-mortgage-statistics"
                ),
                "transformation_id": "nmdb_california_upb_manual_v1",
                "universe_definition": (
                    "Outstanding California first-lien residential "
                    "mortgages compatible with county debt"
                ),
                "variable_name": (
                    "california_unpaid_principal_balance_benchmark"
                ),
                "unit": "USD",
                "control_type": "unpaid_principal_balance",
                "compatible_with_county_debt": True,
            }
        ]
    )


def _nmdb_distribution(
    *,
    benchmark_vintage: str = "2024_ACS5",
    reference_date: str = "2022-07-01",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "benchmark_vintage": benchmark_vintage,
                "reference_date": reference_date,
                "annual_rate": 0.0,
                "remaining_months": 300.0,
                "weight": 1.0,
                "parameter_geography": "US",
                "source_period_start": reference_date,
                "source_period_end": reference_date,
                "release_date": "2025-03-28",
                "source_release_dates": (
                    "fhfa_nmdb_joint_distribution=2025-03-28"
                ),
                "source_frequency": "published dashboard period",
                "source_id": "fhfa_nmdb_joint_distribution",
                "source_vintage": "NMDB_JOINT_2025-03-28",
                "retrieved_at": "2025-04-02T17:00:00Z",
                "source_filename": "nmdb_joint.csv",
                "checksum_sha256": "c" * 64,
                "source_page_url": (
                    "https://www.fhfa.gov/data/dashboard/"
                    "nmdb-outstanding-residential-mortgage-statistics"
                ),
                "transformation_id": (
                    "nmdb_joint_rate_remaining_term_manual_v1"
                ),
                "universe_definition": (
                    "Outstanding closed-end residential mortgages"
                ),
                "distribution_kind": "joint_rate_remaining_term",
                "mortgage_population": "outstanding_mortgages",
                "joint_distribution": True,
                "rates_are_percent": False,
            }
        ]
    )


def _cpi_rows(*years: int) -> pd.DataFrame:
    values = {2020: 258.811, 2024: 313.689}
    return pd.DataFrame(
        [
            {
                "year": year,
                "series_id": "CUUR0000SA0",
                "period": "M13",
                "value": values[year],
                "source_frequency": "annual_average",
                "source_period_start": f"{year}-01-01",
                "source_period_end": f"{year}-12-31",
                "release_date": "2025-02-12",
                "source_release_dates": (
                    "bls_cpi_u_us_city_average=2025-02-12"
                ),
                "source_id": "bls_cpi_u_us_city_average",
                "source_vintage": "2025-02-12",
                "retrieved_at": "2025-02-13T00:00:00+00:00",
                "source_filename": "cu.data.1.AllItems",
                "checksum_sha256": "cpi-checksum",
                "source_page_url": "https://www.bls.gov/cpi/data.htm",
            }
            for year in years
        ]
    )


def test_build_applies_county_share_then_explicit_state_fallback() -> None:
    result = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    payment = result.loc[
        result["variable_name"].eq(
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
        )
    ].set_index("county_fips")
    assert payment.loc["06001", "value"] == 500_000.0
    assert payment.loc["06001", "parameter_geography"] == "county:06001"
    assert payment.loc["06037", "value"] == 1_200_000.0
    assert payment.loc["06037", "parameter_geography"] == "state:06"
    assert payment.loc["06037", "quality_flag"] == "california_pums_fallback"

    calibrated = result.loc[
        result["variable_name"].eq(
            "acs_implied_first_mortgage_balance_cal_benchmark"
        )
    ]
    assert calibrated["value"].sum() == pytest.approx(900_000_000.0)
    assert not calibrated["is_modeled"].any()
    assert not calibrated["is_interpolated"].any()
    assert calibrated["source_id"].str.contains(
        "fhfa_nmdb_ca_upb", regex=False
    ).all()
    assert calibrated["source_release_dates"].str.contains(
        "fhfa_nmdb_ca_upb=2025-03-28", regex=False
    ).all()


def test_build_rejects_bare_or_provenance_poor_california_upb() -> None:
    with pytest.raises(TypeError, match="bare benchmark-to-value mappings"):
        build_version_a_benchmark(
            _acs_rows(),
            _pums(),
            rate_parameters=_rates(),
            california_upb={"2024_ACS5": 900_000_000.0},  # type: ignore[arg-type]
        )

    missing_compatibility = _california_upb().drop(
        columns="compatible_with_county_debt"
    )
    with pytest.raises(ValueError, match="provenance-rich"):
        build_version_a_benchmark(
            _acs_rows(),
            _pums(),
            rate_parameters=_rates(),
            california_upb=missing_compatibility,
        )


def test_nmdb_parameters_require_exact_reference_date_and_preserve_source_ids() -> None:
    exact = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        nmdb_distributions=_nmdb_distribution(),
        california_upb=_california_upb(),
    )
    raw = exact.loc[
        exact["variable_name"].eq(
            "acs_implied_first_mortgage_balance_raw_benchmark"
        )
    ]
    calibrated = exact.loc[
        exact["variable_name"].eq(
            "acs_implied_first_mortgage_balance_cal_benchmark"
        )
    ]
    assert raw["source_id"].str.contains(
        "fhfa_nmdb_joint_distribution", regex=False
    ).all()
    assert raw["source_release_dates"].str.contains(
        "fhfa_nmdb_joint_distribution=2025-03-28", regex=False
    ).all()
    assert calibrated["source_id"].str.contains(
        "fhfa_nmdb_ca_upb", regex=False
    ).all()

    wrong_reference = "2022-08-01"
    mismatched = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
        nmdb_distributions=_nmdb_distribution(
            reference_date=wrong_reference
        ),
        california_upb=_california_upb(
            reference_date=wrong_reference
        ),
    )
    mismatched_raw = mismatched.loc[
        mismatched["variable_name"].eq(
            "acs_implied_first_mortgage_balance_raw_benchmark"
        )
    ]
    mismatched_calibrated = mismatched.loc[
        mismatched["variable_name"].eq(
            "acs_implied_first_mortgage_balance_cal_benchmark"
        )
    ]
    assert mismatched_raw["quality_flag"].eq(
        "pmms_origination_rate_sensitivity"
    ).all()
    assert not mismatched_raw["source_id"].str.contains(
        "fhfa_nmdb_joint_distribution", regex=False
    ).any()
    assert mismatched_calibrated["value"].isna().all()
    assert mismatched_calibrated["quality_flag"].eq(
        "california_upb_calibration_unavailable"
    ).all()


def test_pums_and_rate_parameters_require_exact_reference_date() -> None:
    pums = _pums().copy()
    pums["reference_date"] = "2019-07-01"
    rates = _rates().copy()
    rates["reference_date"] = "2019-07-01"

    result = build_version_a_benchmark(
        _acs_rows(),
        pums,
        rate_parameters=rates,
    )

    applied = result.loc[
        result["variable_name"].eq(
            "pums_first_mortgage_pi_share_applied_benchmark"
        )
    ]
    assert applied["value"].isna().all()
    assert applied["quality_flag"].eq("pums_parameter_unavailable").all()
    raw = result.loc[
        result["variable_name"].eq(
            "acs_implied_first_mortgage_balance_raw_benchmark"
        )
    ]
    assert raw["value"].isna().all()
    assert raw["quality_flag"].str.contains(
        "aggregate_first_mortgage_pi_unavailable",
        regex=False,
    ).all()
    assert not raw["source_id"].str.contains(
        "freddie_mac_pmms",
        regex=False,
    ).any()


def test_null_derived_values_identify_the_missing_input() -> None:
    missing_cost = _acs_rows()
    cost_mask = (
        missing_cost["county_fips"].eq("06037")
        & missing_cost["variable_name"].eq(
            "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark"
        )
    )
    missing_cost.loc[cost_mask, "value"] = math.nan
    missing_cost.loc[cost_mask, "central_estimate"] = math.nan

    result = build_version_a_benchmark(
        missing_cost,
        _pums(),
        rate_parameters=_rates(),
    )
    la = result.loc[result["county_fips"].eq("06037")].set_index(
        "variable_name"
    )
    assert pd.isna(
        la.loc[
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            "value",
        ]
    )
    assert (
        la.loc[
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            "quality_flag",
        ]
        == "acs_selected_owner_costs_unavailable"
    )
    assert "aggregate_first_mortgage_pi_unavailable" in la.loc[
        "acs_implied_first_mortgage_balance_raw_benchmark",
        "quality_flag",
    ]

    missing_value = _acs_rows()
    value_mask = (
        missing_value["county_fips"].eq("06037")
        & missing_value["variable_name"].eq(
            "acs_aggregate_owner_value_mortgaged_benchmark"
        )
    )
    missing_value.loc[value_mask, "value"] = math.nan
    missing_value.loc[value_mask, "central_estimate"] = math.nan
    equity_result = build_version_a_benchmark(
        missing_value,
        _pums(),
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    equity = equity_result.loc[
        equity_result["county_fips"].eq("06037")
        & equity_result["variable_name"].eq(
            "acs_implied_owner_occupied_home_equity_mortgaged_benchmark"
        )
    ].iloc[0]
    assert pd.isna(equity["value"])
    assert (
        equity["quality_flag"]
        == "acs_mortgaged_owner_value_unavailable"
    )


def test_missing_california_upb_preserves_null_calibrated_debt_and_equity() -> None:
    result = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
    )
    unavailable = result.loc[
        result["variable_name"].isin(
            {
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
                "acs_implied_owner_occupied_home_equity_all_benchmark",
            }
        )
    ]
    assert unavailable["value"].isna().all()
    assert unavailable["quality_flag"].eq(
        "california_upb_calibration_unavailable"
    ).all()
    assert all(math.isnan(value) for value in unavailable["central_estimate"])


def test_calibration_rejects_incomplete_california_county_coverage() -> None:
    incomplete = _acs_rows().loc[
        lambda frame: ~frame["county_fips"].eq("06115")
    ]
    result = build_version_a_benchmark(
        incomplete,
        _pums(),
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    calibrated = result.loc[
        result["variable_name"].eq(
            "acs_implied_first_mortgage_balance_cal_benchmark"
        )
    ]
    assert len(calibrated) == 57
    assert calibrated["value"].isna().all()
    assert calibrated["quality_flag"].eq(
        "california_county_coverage_incomplete"
    ).all()


def test_debt_and_equity_normalizations_use_direct_acs_denominators() -> None:
    result = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    la = result.loc[result["county_fips"].eq("06037")].set_index(
        "variable_name"
    )
    debt = la.loc[
        "acs_implied_first_mortgage_balance_cal_benchmark", "value"
    ]
    assert la.loc[
        "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
        "value",
    ] == pytest.approx(debt / 2_000.0)
    assert la.loc[
        "acs_first_mortgage_debt_per_owner_unit_benchmark", "value"
    ] == pytest.approx(debt / 3_000.0)
    assert la.loc[
        "acs_first_mortgage_debt_per_capita_benchmark", "value"
    ] == pytest.approx(debt / 20_000.0)
    assert la.loc[
        "acs_first_mortgage_debt_to_owner_income_benchmark", "value"
    ] == pytest.approx(debt / 180_000_000.0)
    equity_all = la.loc[
        "acs_implied_owner_occupied_home_equity_all_benchmark", "value"
    ]
    assert la.loc[
        "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
        "value",
    ] == pytest.approx(equity_all / 3_000.0)


def test_debt_bounds_preserve_acs_payment_uncertainty() -> None:
    result = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    raw = result.loc[
        result["county_fips"].eq("06037")
        & result["variable_name"].eq(
            "acs_implied_first_mortgage_balance_raw_benchmark"
        )
    ].iloc[0]
    assert raw["low_estimate"] == pytest.approx(1_080_000.0 * 240.0)
    assert raw["central_estimate"] == pytest.approx(1_200_000.0 * 300.0)
    assert raw["high_estimate"] == pytest.approx(1_320_000.0 * 360.0)

    missing_lower = _acs_rows()
    mask = (
        missing_lower["county_fips"].eq("06037")
        & missing_lower["variable_name"].eq(
            "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark"
        )
    )
    missing_lower.loc[mask, "low_estimate"] = math.nan
    missing_result = build_version_a_benchmark(
        missing_lower,
        _pums(),
        rate_parameters=_rates(),
    )
    payment = missing_result.loc[
        missing_result["county_fips"].eq("06037")
        & missing_result["variable_name"].eq(
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
        )
    ].iloc[0]
    raw_missing = missing_result.loc[
        missing_result["county_fips"].eq("06037")
        & missing_result["variable_name"].eq(
            "acs_implied_first_mortgage_balance_raw_benchmark"
        )
    ].iloc[0]
    assert pd.isna(payment["low_estimate"])
    assert pd.isna(raw_missing["low_estimate"])


def test_pums_replicate_bounds_propagate_into_payment_and_debt() -> None:
    pums = _pums().copy()
    state = pums["county_fips"].isna()
    pums.loc[state, "low_estimate"] = 0.5
    pums.loc[state, "high_estimate"] = 0.7

    result = build_version_a_benchmark(
        _acs_rows(),
        pums,
        rate_parameters=_rates(),
    )
    la = result.loc[result["county_fips"].eq("06037")].set_index(
        "variable_name"
    )
    applied = la.loc[
        "pums_first_mortgage_pi_share_applied_benchmark"
    ]
    payment = la.loc[
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
    ]
    raw = la.loc[
        "acs_implied_first_mortgage_balance_raw_benchmark"
    ]

    assert applied["low_estimate"] == pytest.approx(0.5)
    assert applied["high_estimate"] == pytest.approx(0.7)
    assert payment["low_estimate"] == pytest.approx(900_000.0)
    assert payment["central_estimate"] == pytest.approx(1_200_000.0)
    assert payment["high_estimate"] == pytest.approx(1_540_000.0)
    assert raw["low_estimate"] == pytest.approx(900_000.0 * 240.0)
    assert raw["high_estimate"] == pytest.approx(1_540_000.0 * 360.0)


def test_build_keeps_pums_junior_lien_diagnostic_separate() -> None:
    pums = _pums().copy()
    pums["variable_name"] = "pums_first_mortgage_pi_share_benchmark"
    pums["variable_id"] = pums["variable_name"]
    pums["unit"] = "share"
    pums["source_id"] = "census_acs5_pums_housing_ca"
    pums["source_frequency"] = "5-year"
    pums["source_period_start"] = "2020-01-01"
    pums["source_period_end"] = "2024-12-31"
    pums["release_date"] = "2026-03-05"
    pums["source_geography"] = "PUMA"
    pums["lineage_id"] = "pums-and-crosswalk-lineage"
    pums["geography_tier"] = ["state", "county", "state_fallback"]
    pums.loc[pums["county_fips"].eq("06037"), "value"] = 0.6
    pums.loc[
        pums["county_fips"].eq("06037"), "parameter_geography"
    ] = "state:06"
    pums.loc[
        pums["county_fips"].eq("06037"), "parameter_geography_level"
    ] = "state"

    junior = pums.loc[pums["county_fips"].eq("06037")].copy()
    junior["variable_name"] = (
        "pums_junior_lien_payment_per_mortgaged_unit_benchmark"
    )
    junior["variable_id"] = junior["variable_name"]
    junior["unit"] = "USD/unit/month"
    junior["value"] = 125.0
    pums = pd.concat([pums, junior], ignore_index=True)

    result = build_version_a_benchmark(
        _acs_rows(),
        pums,
        rate_parameters=_rates(),
        california_upb=_california_upb(),
    )
    la = result.loc[result["county_fips"].eq("06037")]
    applied = la.loc[
        la["variable_name"].eq(
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
        ),
        "value",
    ].iloc[0]
    junior_value = la.loc[
        la["variable_name"].eq(
            "pums_junior_lien_payment_per_mortgaged_unit_benchmark"
        ),
        "value",
    ].iloc[0]
    assert applied == 1_200_000.0
    assert junior_value == 125.0
    direct = la.loc[
        la["variable_name"].isin(
            {
                "pums_first_mortgage_pi_share_benchmark",
                "pums_junior_lien_payment_per_mortgaged_unit_benchmark",
            }
        )
    ]
    assert direct["source_id"].str.contains(
        "census_tract_to_puma_2020",
        regex=False,
    ).all()
    assert direct["source_release_dates"].str.contains(
        "census_tract_to_puma_2020=unavailable",
        regex=False,
    ).all()
    assert direct["source_filename"].str.contains(
        "tract_puma_2020.txt",
        regex=False,
    ).all()
    assert direct["checksum_sha256"].str.contains(
        "relationship-checksum",
        regex=False,
    ).all()
    assert direct["lineage_id"].eq("pums-and-crosswalk-lineage").all()


def test_real_equity_uses_acs_ending_year_cpi_and_preserves_nominal() -> None:
    acs = _acs_rows().copy()
    acs["benchmark_vintage"] = "2020_ACS5"
    acs["source_period_start"] = "2016-01-01"
    acs["source_period_end"] = "2020-12-31"
    acs["reference_date"] = "2018-07-01"
    pums = _pums().copy()
    pums["benchmark_vintage"] = "2020_ACS5"
    pums["reference_date"] = "2018-07-01"
    rates = _rates().copy()
    rates["benchmark_vintage"] = "2020_ACS5"
    rates["reference_date"] = "2018-07-01"

    nominal_only = build_version_a_benchmark(
        acs,
        pums,
        rate_parameters=rates,
        california_upb=_california_upb(
            benchmark_vintage="2020_ACS5",
            reference_date="2018-07-01",
        ),
    )
    result = build_version_a_benchmark(
        acs,
        pums,
        rate_parameters=rates,
        california_upb=_california_upb(
            benchmark_vintage="2020_ACS5",
            reference_date="2018-07-01",
        ),
        cpi_rows=_cpi_rows(2020, 2024),
    )

    nominal_name = (
        "acs_implied_owner_occupied_home_equity_mortgaged_benchmark"
    )
    real_name = (
        "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark"
    )
    nominal_before = nominal_only.loc[
        nominal_only["variable_name"].eq(nominal_name)
        & nominal_only["county_fips"].eq("06037"),
        "value",
    ].iloc[0]
    nominal_after = result.loc[
        result["variable_name"].eq(nominal_name)
        & result["county_fips"].eq("06037"),
        "value",
    ].iloc[0]
    real = result.loc[
        result["variable_name"].eq(real_name)
        & result["county_fips"].eq("06037")
    ].iloc[0]

    assert nominal_after == nominal_before
    assert real["value"] == pytest.approx(
        nominal_after * 313.689 / 258.811
    )
    assert real["unit"] == "constant_2024_USD"
    # Row-level release_date is the latest release across every contributing
    # source; the source-specific audit retains the earlier CPI snapshot date.
    assert real["release_date"] == pd.Timestamp("2026-03-05")
    assert (
        "bls_cpi_u_us_city_average=2025-02-12"
        in real["source_release_dates"]
    )
    assert "bls_cpi_u_us_city_average" in real["source_id"]
    assert (
        "bls_cpi_u_us_city_average=2025-02-12"
        in real["source_release_dates"]
    )
    assert "2018 five-year midpoint" in real["notes"]
    assert "2020 vintage-ending-year" in real["notes"]


def test_real_equity_is_null_when_required_price_year_cpi_is_missing() -> None:
    acs = _acs_rows().copy()
    acs["benchmark_vintage"] = "2020_ACS5"
    acs["source_period_start"] = "2016-01-01"
    acs["source_period_end"] = "2020-12-31"
    acs["reference_date"] = "2018-07-01"
    pums = _pums().copy()
    pums["benchmark_vintage"] = "2020_ACS5"
    pums["reference_date"] = "2018-07-01"
    rates = _rates().copy()
    rates["benchmark_vintage"] = "2020_ACS5"
    rates["reference_date"] = "2018-07-01"

    result = build_version_a_benchmark(
        acs,
        pums,
        rate_parameters=rates,
        california_upb=_california_upb(
            benchmark_vintage="2020_ACS5",
            reference_date="2018-07-01",
        ),
        cpi_rows=_cpi_rows(2024),
    )
    real = result.loc[
        result["variable_name"].eq(
            "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark"
        )
    ]
    nominal = result.loc[
        result["variable_name"].eq(
            "acs_implied_owner_occupied_home_equity_all_benchmark"
        )
    ]

    assert nominal["value"].notna().all()
    assert real["value"].isna().all()
    assert real["quality_flag"].eq("cpi_u_2020_unavailable").all()
