import math

import numpy as np
import pandas as pd
import pytest

from src.benchmark.finance import (
    BenchmarkMetadataError,
    adjust_pums_housing_dollars,
    annuity_factor,
    calculate_aggregate_mortgage_payment,
    calculate_debt_normalizations,
    calculate_hmda_downpayment_metrics,
    calculate_matched_universe_equity,
    calculate_pums_first_mortgage_pi,
    calculate_pums_junior_lien_payment,
    calculate_pums_pi_share,
    calibrate_implied_debt_to_california,
    implied_debt_from_distribution,
    implied_debt_scenarios,
    validate_benchmark_metadata,
)


def test_annuity_factor_handles_zero_rate_missingness_and_invalid_terms():
    assert annuity_factor(0.0, 300) == 300.0
    assert annuity_factor(0.06, 360) == pytest.approx(166.7916144)
    assert math.isnan(annuity_factor(np.nan, 360))
    assert math.isnan(annuity_factor(0.06, None))
    with pytest.raises(ValueError, match="positive"):
        annuity_factor(0.06, 0)
    with pytest.raises(ValueError, match="negative"):
        annuity_factor(-0.01, 360)


def test_pums_adjustment_and_pi_respect_escrow_flags_and_unknowns():
    frame = pd.DataFrame(
        {
            "WGTP": [2, 1, 1],
            "ADJHSG": [1_000_000, 1_000_000, 1_000_000],
            "MRGP": [2_000.0, 1_000.0, 1_100.0],
            "TAXAMT": [12_000.0, np.nan, 1_200.0],
            "INSP": [1_200.0, np.nan, 600.0],
            "SMOCP": [2_500.0, 1_500.0, 1_600.0],
            "MRGT": [1, 2, None],
            "MRGI": [1, 2, 2],
        }
    )

    assert adjust_pums_housing_dollars(1000, 1_100_000) == 1100
    result = calculate_pums_first_mortgage_pi(frame)
    assert result.loc[0, "pi_first"] == 900.0
    # Missing expenses do not poison P&I when flags explicitly say excluded.
    assert result.loc[1, "pi_first"] == 1_000.0
    # An unknown tax-inclusion flag is not guessed.
    assert math.isnan(result.loc[2, "pi_first"])

    share = calculate_pums_pi_share(frame)
    assert share["sample_n"] == 2
    assert share["coverage_rate"] == pytest.approx(2 / 3)
    assert share["pi_share"] == pytest.approx(2_800 / 6_500)


def test_pums_junior_lien_payment_is_adjusted_weighted_and_separate():
    frame = pd.DataFrame(
        {
            "WGTP": [2, 1, 7, 0],
            "ADJHSG": [1_100_000, 1_000_000, 1_000_000, 1_000_000],
            "SMP": [100, 0, None, 9_999],
        }
    )
    result = calculate_pums_junior_lien_payment(frame)

    # Blank SMP is the codebook-defined structural zero for no junior lien;
    # reported zero means a lien with no regular payment. Both stay in the
    # eligible mortgaged-unit denominator.
    assert result["junior_lien_monthly_payment"] == pytest.approx(220 / 10)
    assert result["sample_n"] == 3
    assert result["input_n"] == 4
    assert result["coverage_rate"] == pytest.approx(0.75)
    assert result["weighted_coverage_rate"] == pytest.approx(1.0)
    assert result["positive_payment_n"] == 1
    assert result["positive_payment_weighted_share"] == pytest.approx(2 / 10)
    assert result["structural_no_junior_lien_n"] == 1
    assert result["structural_no_junior_lien_weight"] == 7


def test_hmda_cltv_metrics_include_prefilter_coverage_and_no_silent_repair():
    frame = pd.DataFrame(
        {
            "property_value": [100.0, 200.0, 300.0, 400.0, 500.0],
            "combined_loan_to_value_ratio": [80.0, 95.0, 101.0, None, 160.0],
        }
    )
    result = calculate_hmda_downpayment_metrics(frame, minimum_sample=3)

    assert result["input_n"] == 5
    assert result["cltv_numeric_n"] == 4
    assert result["cltv_valid_n"] == 3
    assert result["sample_n"] == 3
    assert result["coverage_rate"] == pytest.approx(0.6)
    assert result["negative_downpayment_n"] == 1
    assert result["hmda_median_downpayment_rate"] == pytest.approx(0.05)
    assert result[
        "hmda_property_value_weighted_downpayment_rate"
    ] == pytest.approx(27 / 600)
    assert result["hmda_aggregate_downpayment_flow"] == pytest.approx(27)
    assert result["share_downpayment_below_3pct"] == pytest.approx(1 / 3)
    # The invalid 160 CLTV record is diagnosed, not replaced by another ratio.
    assert result["cltv_invalid_bounds_n"] == 1


def test_hmda_metrics_suppress_small_samples_but_keep_diagnostics():
    frame = pd.DataFrame(
        {
            "property_value": [500_000.0],
            "combined_loan_to_value_ratio": [80.0],
        }
    )
    result = calculate_hmda_downpayment_metrics(frame, minimum_sample=50)
    assert result["sample_n"] == 1
    assert result["meets_minimum_sample"] is False
    assert math.isnan(result["hmda_median_downpayment_rate"])


def test_implied_debt_scenarios_and_joint_distribution():
    scenarios = implied_debt_scenarios(
        1_000.0,
        0.0,
        remaining_term_years=(20, 25, 30),
    )
    assert scenarios[20] == 240_000.0
    assert scenarios[25] == 300_000.0
    assert scenarios[30] == 360_000.0

    distribution = pd.DataFrame(
        {
            "annual_rate": [0.04, 0.06],
            "remaining_months": [240, 360],
            "weight": [0.25, 0.75],
        }
    )
    expected = 1_000 * (
        0.25 * annuity_factor(0.04, 240)
        + 0.75 * annuity_factor(0.06, 360)
    )
    assert implied_debt_from_distribution(
        1_000, distribution
    ) == pytest.approx(expected)

    bad_weights = distribution.assign(weight=[1.0, 1.0])
    with pytest.raises(ValueError, match="sum to one"):
        implied_debt_from_distribution(1_000, bad_weights)
    assert implied_debt_from_distribution(
        1_000, bad_weights, normalize_weights=True
    ) == pytest.approx(
        1_000
        * (
            0.5 * annuity_factor(0.04, 240)
            + 0.5 * annuity_factor(0.06, 360)
        )
    )


def test_california_calibration_is_optional_but_requires_complete_coverage():
    raw = pd.Series(
        [100.0, 200.0, np.nan],
        index=["06001", "06037", "06075"],
    )
    uncalibrated = calibrate_implied_debt_to_california(raw)
    assert uncalibrated["was_calibrated"] is False
    pd.testing.assert_series_equal(uncalibrated["calibrated_debt"], raw)

    with pytest.raises(ValueError, match="nonmissing raw debt"):
        calibrate_implied_debt_to_california(raw, 600.0)

    complete = raw.fillna(300.0)
    calibrated = calibrate_implied_debt_to_california(
        complete,
        1_200.0,
        expected_counties=["06001", "06037", "06075"],
    )
    assert calibrated["was_calibrated"] is True
    assert calibrated["calibration_factor"] == 2.0
    assert calibrated["calibrated_debt"].loc["06001"] == 200.0
    assert calibrated["calibrated_debt"].loc["06037"] == 400.0
    assert calibrated["calibrated_debt"].loc["06075"] == 600.0

    with pytest.raises(ValueError, match="exact expected county set"):
        calibrate_implied_debt_to_california(
            complete.iloc[:2],
            600.0,
            expected_counties=["06001", "06037", "06075"],
        )


def test_aggregate_payment_and_matched_equity_identities():
    payment = calculate_aggregate_mortgage_payment(
        1_000_000.0,
        0.6,
        mortgaged_units=1_000,
        aggregate_income_mortgaged_owners=60_000_000,
    )
    assert payment["aggregate_monthly_pi"] == 600_000.0
    assert payment["pi_per_mortgaged_unit"] == 600.0
    assert payment["annual_debt_service"] == 7_200_000.0
    assert payment["mortgage_debt_service_to_income"] == pytest.approx(0.12)

    equity = calculate_matched_universe_equity(
        1_000_000_000.0,
        600_000_000.0,
        mortgage_free_owner_value=400_000_000.0,
        owner_units=2_000,
    )
    assert equity["equity_mortgaged_owner"] == 400_000_000.0
    assert equity["equity_all_owner"] == 800_000_000.0
    assert equity["aggregate_ltv_mortgaged"] == pytest.approx(0.6)
    assert equity["equity_rate_mortgaged"] == pytest.approx(0.4)
    assert equity["equity_per_owner_unit"] == 400_000.0
    assert equity["mortgaged_identity_residual"] == 0.0
    assert equity["all_owner_identity_residual"] == 0.0

    missing_all_owner = calculate_matched_universe_equity(
        1_000.0, 600.0
    )
    assert math.isnan(missing_all_owner["equity_all_owner"])


def test_debt_normalizations_require_explicit_nonzero_denominators():
    normalized = calculate_debt_normalizations(
        600_000_000,
        mortgaged_units=1_000,
        owner_units=1_500,
        population=3_000,
        aggregate_owner_income=1_200_000_000,
    )
    assert normalized["debt_per_mortgaged_unit"] == 600_000
    assert normalized["debt_per_owner_unit"] == 400_000
    assert normalized["debt_per_capita"] == 200_000
    assert normalized["debt_to_owner_income"] == pytest.approx(0.5)

    unavailable = calculate_debt_normalizations(
        600_000_000,
        mortgaged_units=0,
        owner_units=None,
        population=float("nan"),
        aggregate_owner_income=0,
    )
    assert all(math.isnan(value) for value in unavailable.values())


def _valid_metadata() -> dict:
    return {
        "county_fips": "06037",
        "month": None,
        "value": 0.20,
        "variable_name": "hmda_median_downpayment_rate_benchmark",
        "database_version": "A. Benchmark",
        "source_frequency": "annual",
        "source_period_start": "2023-01-01",
        "source_period_end": "2023-12-31",
        "reference_date": "2023-07-01",
        "release_date": "2024-03-26",
        "source_release_dates": "hmda_modified_lar=2024-03-26",
        "source_geography": "county",
        "parameter_geography": "county",
        "monthly_method": "not_applicable_source_period",
        "is_modeled": False,
        "is_nowcast": False,
        "benchmark_vintage": "2023_HMDA",
        "sample_n": 1_000,
        "coverage_rate": 0.92,
        "low_estimate": 0.18,
        "central_estimate": 0.20,
        "high_estimate": 0.22,
        "universe_definition": (
            "Originated first-lien home-purchase loans on principal residences"
        ),
        "notes": None,
    }


def test_benchmark_metadata_contract_accepts_source_faithful_record():
    assert validate_benchmark_metadata(_valid_metadata()) == []


def test_benchmark_metadata_contract_rejects_modeled_or_ambiguous_records():
    bad = _valid_metadata()
    bad.update(
        {
            "county_fips": 6037,
            "variable_name": "hmda_median_downpayment_rate",
            "monthly_method": "log_linear_interpolation",
            "is_modeled": True,
            "coverage_rate": "unknown",
        }
    )
    errors = validate_benchmark_metadata(bad, raise_on_error=False)
    assert any("five-character string" in error for error in errors)
    assert any("end with _benchmark" in error for error in errors)
    assert any("not source-faithful" in error for error in errors)
    assert any("is_modeled" in error for error in errors)
    assert any("coverage_rate must be numeric" in error for error in errors)
    with pytest.raises(BenchmarkMetadataError):
        validate_benchmark_metadata(bad)
