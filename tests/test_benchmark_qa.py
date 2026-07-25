from __future__ import annotations

import pandas as pd
import pytest

from src.benchmark.qa import benchmark_qa_results, run_benchmark_qa
from src.benchmark.schema import benchmark_row


def _row(variable_name: str, value: float) -> dict:
    return benchmark_row(
        county_fips="06037",
        state_fips="06",
        geo_id="COUNTY_06037",
        month=None,
        value=value,
        variable_name=variable_name,
        variable_id=variable_name,
        unit="USD",
        estimate_variant="published",
        database_version="A. Benchmark",
        source_id="test_source",
        source_frequency="5-year",
        source_period_start="2020-01-01",
        source_period_end="2024-12-31",
        reference_date="2022-07-01",
        release_date="2025-01-30",
        source_geography="county",
        parameter_geography="county",
        monthly_method="not_applicable_source_period",
        construction_method="test",
        is_modeled=False,
        is_nowcast=False,
        is_imputed=False,
        is_interpolated=False,
        is_suppressed=False,
        benchmark_vintage="2024_ACS5",
        sample_n=None,
        coverage_rate=None,
        low_estimate=None,
        central_estimate=value,
        high_estimate=None,
        universe_definition="Owner occupied",
        notes="test",
        transformation_id="test_v1",
        quality_flag="reported",
    )


def _payment_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _row("acs_mortgaged_owner_units_benchmark", 1_000.0),
            _row(
                "acs_aggregate_household_income_mortgaged_owner_benchmark",
                60_000_000.0,
            ),
            _row(
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
                600_000.0,
            ),
            _row(
                "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
                600.0,
            ),
            _row(
                "acs_annual_first_mortgage_debt_service_benchmark",
                7_200_000.0,
            ),
            _row("acs_mortgage_debt_service_to_income_benchmark", 0.12),
        ]
    )


def test_qa_accepts_payment_identities() -> None:
    summary, failures = benchmark_qa_results(
        _payment_frame(), require_complete_acs_counties=False
    )
    assert failures.empty
    assert not summary["status"].eq("fail").any()


def test_qa_stops_on_broken_annual_payment_identity(tmp_path) -> None:
    frame = _payment_frame()
    frame.loc[
        frame["variable_name"].eq(
            "acs_annual_first_mortgage_debt_service_benchmark"
        ),
        "value",
    ] = 1.0
    summary, failures = benchmark_qa_results(
        frame, require_complete_acs_counties=False
    )
    assert summary.set_index("check_id").loc[
        "annual_payment_identity", "status"
    ] == "fail"
    assert failures["check_id"].eq("annual_payment_identity").any()
    with pytest.raises(RuntimeError, match="annual_payment_identity"):
        run_benchmark_qa(
            frame,
            root=tmp_path,
            require_complete_acs_counties=False,
        )
    assert (tmp_path / "output/qa/version_a_qa_summary.csv").exists()
