from pathlib import Path

import pandas as pd

from src.benchmark.schema import (
    BENCHMARK_COLUMNS,
    benchmark_contract_errors,
    benchmark_row,
    finalize_benchmark,
)


def _row(**overrides):
    row = benchmark_row(
        county_fips="06037",
        state_fips="06",
        geo_id="COUNTY_06037",
        month=None,
        value=None,
        variable_name="acs_implied_first_mortgage_balance_cal_benchmark",
        variable_id="acs_implied_first_mortgage_balance_cal_benchmark",
        unit="USD",
        estimate_variant="central",
        database_version="A. Benchmark",
        source_id="census_acs5_finance",
        source_frequency="acs_5_year",
        source_period_start="2020-01-01",
        source_period_end="2024-12-31",
        reference_date="2022-07-01",
        release_date=None,
        source_geography="county",
        parameter_geography="CA",
        monthly_method="not_applicable_source_period",
        construction_method="ACS/PUMS/NMDB benchmark",
        payment_frequency="monthly",
        is_modeled=False,
        is_nowcast=False,
        is_imputed=False,
        is_interpolated=False,
        is_suppressed=False,
        benchmark_vintage="ACS5_2024",
        sample_n=None,
        coverage_rate=None,
        low_estimate=None,
        central_estimate=None,
        high_estimate=None,
        universe_definition="Owner-occupied housing with a first mortgage",
        notes="NMDB calibration unavailable",
        quality_flag="calibration_unavailable",
    )
    row.update(overrides)
    return row


def test_finalize_preserves_explicit_missing_benchmark(tmp_path: Path):
    output = tmp_path / "benchmark.parquet"
    result = finalize_benchmark(pd.DataFrame([_row()]), output)

    assert list(result.columns) == BENCHMARK_COLUMNS
    assert len(result) == 1
    assert pd.isna(result.loc[0, "value"])
    assert result.loc[0, "quality_flag"] == "calibration_unavailable"
    assert output.exists()


def test_contract_rejects_interpolation_and_unflagged_missing_value():
    bad = pd.DataFrame(
        [
            _row(
                monthly_method="log_linear_interpolation",
                quality_flag=None,
                notes=None,
            )
        ]
    )
    errors = benchmark_contract_errors(bad)
    assert any("monthly_method" in error for error in errors)
    assert any("null benchmark values" in error for error in errors)


def test_contract_allows_reported_suppression():
    suppressed = pd.DataFrame(
        [_row(is_suppressed=True, quality_flag="published_suppression")]
    )
    assert benchmark_contract_errors(suppressed) == []
