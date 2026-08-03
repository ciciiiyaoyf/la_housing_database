from __future__ import annotations

import pandas as pd
import pytest

from src.benchmark.qa import (
    benchmark_coverage_reports,
    benchmark_qa_results,
    run_benchmark_qa,
)
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
        source_release_dates="test_source=2025-01-30",
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
        source_vintage="2024",
        retrieved_at="2025-01-31T00:00:00+00:00",
        source_filename="test.csv",
        checksum_sha256="abc123",
        transformation_id="test_v1",
        lineage_id=f"test-{variable_name}",
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


def test_qa_rejects_direct_acs_only_as_incomplete_build() -> None:
    direct = _row("acs_mortgaged_owner_units_benchmark", 1_000.0)
    direct.update(
        source_id="census_acs5_finance",
        source_release_dates="census_acs5_finance=2025-01-30",
    )

    summary, failures = benchmark_qa_results(
        pd.DataFrame([direct]),
        require_complete_acs_counties=False,
    )

    assert summary.set_index("check_id").loc[
        "version_a_required_output_inventory", "status"
    ] == "fail"
    assert summary.set_index("check_id").loc[
        "version_a_required_direct_acs_inventory", "status"
    ] == "fail"
    required = set(
        failures.loc[
            failures["check_id"].eq("version_a_required_output_inventory"),
            "required_variable",
        ]
    )
    assert "acs_implied_first_mortgage_balance_cal_benchmark" in required
    assert (
        "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark"
        in required
    )


def test_qa_checks_published_acs_owner_partitions() -> None:
    frame = pd.DataFrame(
        [
            _row("acs_aggregate_owner_value_all_benchmark", 240.0),
            _row("acs_aggregate_owner_value_mortgaged_benchmark", 100.0),
            _row("acs_aggregate_owner_value_no_mortgage_benchmark", 30.0),
            _row("acs_aggregate_household_income_owner_benchmark", 90.0),
            _row(
                "acs_aggregate_household_income_mortgaged_owner_benchmark",
                60.0,
            ),
            _row(
                "acs_aggregate_household_income_no_mortgage_owner_benchmark",
                30.0,
            ),
        ]
    )

    summary, failures = benchmark_qa_results(
        frame,
        require_complete_acs_counties=False,
    )

    assert summary.set_index("check_id").loc[
        "aggregate_owner_value_partition_identity", "status"
    ] == "fail"
    assert failures["check_id"].eq(
        "aggregate_owner_value_partition_identity"
    ).any()
    assert summary.set_index("check_id").loc[
        "aggregate_owner_income_partition_identity", "status"
    ] == "pass"


def test_qa_allows_published_acs_hundred_dollar_partition_rounding() -> None:
    frame = pd.DataFrame(
        [
            _row("acs_aggregate_owner_value_all_benchmark", 1_000_000.0),
            _row(
                "acs_aggregate_owner_value_mortgaged_benchmark",
                600_000.0,
            ),
            _row(
                "acs_aggregate_owner_value_no_mortgage_benchmark",
                400_100.0,
            ),
            _row(
                "acs_aggregate_household_income_owner_benchmark",
                900_000.0,
            ),
            _row(
                "acs_aggregate_household_income_mortgaged_owner_benchmark",
                500_000.0,
            ),
            _row(
                "acs_aggregate_household_income_no_mortgage_owner_benchmark",
                399_900.0,
            ),
        ]
    )

    summary, failures = benchmark_qa_results(
        frame,
        require_complete_acs_counties=False,
    )
    indexed = summary.set_index("check_id")

    assert indexed.loc[
        "aggregate_owner_value_partition_identity", "status"
    ] == "pass"
    assert indexed.loc[
        "aggregate_owner_income_partition_identity", "status"
    ] == "pass"
    assert not failures["check_id"].isin(
        {
            "aggregate_owner_value_partition_identity",
            "aggregate_owner_income_partition_identity",
        }
    ).any()


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
    assert (
        tmp_path / "output/coverage/version_a_county_coverage.csv"
    ).exists()
    assert (
        tmp_path / "output/coverage/version_a_source_coverage.csv"
    ).exists()


def test_coverage_reports_distinguish_missing_values_from_missing_counties() -> None:
    frame = _payment_frame()
    missing = _row("test_missing_benchmark", float("nan"))
    missing["quality_flag"] = "source_value_unavailable"
    frame = pd.concat([frame, pd.DataFrame([missing])], ignore_index=True)

    county, source = benchmark_coverage_reports(frame)

    missing_county = county.loc[
        county["variable_name"].eq("test_missing_benchmark")
    ].iloc[0]
    assert missing_county["county_row_present"]
    assert missing_county["value_missing"]
    summary = source.loc[
        source["variable_name"].eq("test_missing_benchmark")
    ].iloc[0]
    assert summary["county_count"] == 1
    assert summary["county_presence_rate"] == pytest.approx(1 / 58)
    assert summary["value_available_count"] == 0
    assert summary["value_missing_count"] == 1
