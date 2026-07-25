from __future__ import annotations

import math

import pandas as pd
import pytest

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
    }
    rows = []
    for county_fips, multiplier in (("06001", 1.0), ("06037", 2.0)):
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
            }
        ]
    )


def test_build_applies_county_share_then_explicit_state_fallback() -> None:
    result = build_version_a_benchmark(
        _acs_rows(),
        _pums(),
        rate_parameters=_rates(),
        california_upb={"2024_ACS5": 900_000_000.0},
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
