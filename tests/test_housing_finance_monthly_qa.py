from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.housing_finance_monthly.qa import (
    monthly_finance_qa_results,
    run_monthly_finance_qa,
)
from src.housing_finance_monthly.schema import (
    VERSION_B_DATABASE_NAME,
    VERSION_C_DATABASE_NAME,
    normalize_monthly_finance,
    normalize_monthly_lineage,
)


CHECKSUM = "b" * 64


def _row(
    *,
    version: str = "B",
    county_fips: str = "06037",
    month: str = "2025-01-31",
    base_variable: str = "acs_implied_first_mortgage_balance_cal",
    benchmark_variable: str = (
        "acs_implied_first_mortgage_balance_cal_benchmark"
    ),
    value: float | None = 100.0,
    lineage_id: str | None = None,
    transformation_id: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    realtime = version == "C"
    suffix = "_monthly_rt" if realtime else "_monthly_hist"
    database_version = (
        VERSION_C_DATABASE_NAME if realtime else VERSION_B_DATABASE_NAME
    )
    transformation = transformation_id or (
        "version_c_test_v1" if realtime else "version_b_test_v1"
    )
    identifier = base_variable + suffix
    lineage_value = lineage_id or f"lineage-{version}-{county_fips}-{month}-{identifier}"
    row: dict[str, object] = {
        "county_fips": county_fips,
        "state_fips": "06",
        "geo_id": f"COUNTY_{county_fips}",
        "month": month,
        "value": value,
        "variable_name": identifier,
        "variable_id": identifier,
        "unit": "USD",
        "estimate_variant": "central",
        "reported_value": None,
        "margin_of_error": None,
        "database_version": database_version,
        "source_id": "version_a_anchor|monthly_indicator",
        "source_frequency": "mixed",
        "source_period_start": "2025-01-01",
        "source_period_end": "2025-12-31",
        "reference_date": "2025-07-01",
        "release_date": "2026-01-15" if not realtime else "2026-02-01",
        "source_release_dates": (
            "version_a_anchor=2026-02-01"
            if realtime
            else "version_a_anchor=2026-01-15"
        ),
        "source_geography": "California county",
        "parameter_geography": "California county",
        "monthly_method": "documented_source_specific_monthly_method",
        "construction_method": "test construction",
        "payment_frequency": "not applicable",
        "is_modeled": True,
        "is_nowcast": realtime,
        "is_imputed": False,
        "is_interpolated": False,
        "is_suppressed": False,
        "benchmark_vintage": "2025_TEST",
        "sample_n": 100,
        "coverage_rate": 1.0,
        "low_estimate": value,
        "central_estimate": value,
        "high_estimate": value,
        "universe_definition": "test matched universe",
        "notes": "QA fixture",
        "source_vintage": "2025_TEST",
        "retrieved_at": (
            "2026-02-02T00:00:00Z"
            if realtime
            else "2026-01-16T00:00:00Z"
        ),
        "source_filename": "lineage_companion",
        "checksum_sha256": CHECKSUM,
        "transformation_id": transformation,
        "lineage_id": lineage_value,
        "quality_flag": "nowcast" if realtime else "modeled_history",
        "benchmark_variable_id": benchmark_variable,
        "information_cutoff_date": "2026-02-15" if realtime else None,
        "method_version": transformation,
    }
    row.update(overrides)
    return row


def _lineage_for(row: dict[str, object], **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "lineage_id": row["lineage_id"],
        "database_version": row["database_version"],
        "information_cutoff_date": row["information_cutoff_date"],
        "county_fips": row["county_fips"],
        "month": row["month"],
        "variable_id": row["variable_id"],
        "estimate_variant": row["estimate_variant"],
        "input_role": "benchmark_anchor",
        "source_id": "test_official_source",
        "source_vintage": row["benchmark_vintage"],
        "source_frequency": "annual",
        "source_period_start": row["source_period_start"],
        "source_period_end": row["source_period_end"],
        "reference_date": row["reference_date"],
        "release_date": row["release_date"],
        "source_geography": "California county",
        "parameter_geography": "California county",
        "retrieved_at": row["retrieved_at"],
        "source_filename": "official.csv",
        "checksum_sha256": CHECKSUM,
    }
    result.update(overrides)
    return result


def _frames(
    rows: list[dict[str, object]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    observations = normalize_monthly_finance(pd.DataFrame(rows))
    lineage = normalize_monthly_lineage(
        pd.DataFrame([_lineage_for(row) for row in rows])
    )
    return observations, lineage


def _dictionary(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "variable_id": row["variable_id"],
                "unit": row["unit"],
                "transformation_id": row["transformation_id"],
                "benchmark_variable_id": row["benchmark_variable_id"],
            }
            for row in {
                str(record["variable_id"]): record for record in rows
            }.values()
        ]
    ).sort_values("variable_id").reset_index(drop=True)


def _catalog(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transformation_id": sorted(
                {str(row["transformation_id"]) for row in rows}
            ),
        }
    )


def _status(summary: pd.DataFrame, check_id: str) -> str:
    return str(summary.set_index("check_id").loc[check_id, "status"])


def _benchmark(
    variable_id: str,
    value: float | None,
    *,
    reference_date: str = "2025-07-01",
    release_date: str = "2026-01-15",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "county_fips": "06037",
                "variable_id": variable_id,
                "benchmark_vintage": "2025_TEST",
                "value": value,
                "estimate_variant": "central",
                "source_period_start": "2025-01-01",
                "source_period_end": "2025-12-31",
                "reference_date": reference_date,
                "release_date": release_date,
            }
        ]
    )


@pytest.mark.parametrize("version", ["B", "C"])
def test_raw_debt_path_meets_complete_version_a_reference_envelope(
    version: str,
) -> None:
    row = _row(
        version=version,
        month="2025-07-31",
        base_variable="acs_implied_first_mortgage_balance_raw",
        benchmark_variable="acs_implied_first_mortgage_balance_raw_benchmark",
        value=100.0,
        low_estimate=90.0,
        central_estimate=100.0,
        high_estimate=110.0,
        reference_date="2025-07-01",
    )
    observations, lineage = _frames([row])
    benchmark = _benchmark(
        "acs_implied_first_mortgage_balance_raw_benchmark",
        100.0,
        reference_date="2025-07-01",
    )
    benchmark["low_estimate"] = 90.0
    benchmark["central_estimate"] = 100.0
    benchmark["high_estimate"] = 110.0

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version=version,
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        benchmark=benchmark,
        require_all_california_counties=False,
    )
    assert _status(passing, "raw_debt_reference_anchor_identity") == "pass"

    broken = observations.copy()
    broken.loc[:, "high_estimate"] = 111.0
    failing, failures = monthly_finance_qa_results(
        broken,
        lineage,
        version=version,
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        benchmark=benchmark,
        require_all_california_counties=False,
    )
    assert _status(failing, "raw_debt_reference_anchor_identity") == "fail"
    assert "raw_debt_reference_anchor_mismatch" in set(failures["reason"])


@pytest.mark.parametrize("version", ["B", "C"])
def test_valid_structural_contract_returns_summary_and_no_failures(
    version: str,
) -> None:
    month = "2025-01-31" if version == "B" else "2026-02-28"
    rows = [_row(version=version, month=month)]
    observations, lineage = _frames(rows)

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version=version,
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        require_all_california_counties=False,
    )

    assert _status(summary, "monthly_finance_contract") == "pass"
    assert _status(summary, "monthly_lineage_contract") == "pass"
    assert _status(summary, "version_dictionary_exact_coverage") == "pass"
    assert _status(summary, "transformation_catalog_exact_coverage") == "pass"
    assert _status(summary, "economic_plausibility_review") == "pass"
    assert not failures["severity"].eq("error").any()
    assert _status(summary, "benchmark_anchor_coverage") == "skip"


def test_schema_and_lineage_errors_are_structural_failures() -> None:
    row = _row(
        is_modeled=False,
        is_nowcast=True,
        month="2025-01-30",
    )
    observations, lineage = _frames([row])

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        require_all_california_counties=False,
    )

    assert _status(summary, "monthly_finance_contract") == "fail"
    assert _status(summary, "monthly_lineage_contract") == "fail"
    assert _status(summary, "version_b_modeled_no_nowcast") == "fail"
    assert failures["severity"].eq("error").any()


def test_every_group_must_retain_exactly_58_california_counties() -> None:
    rows = [
        _row(county_fips=county, lineage_id=f"lineage-{county}")
        for county in CALIFORNIA_COUNTY_FIPS
    ]
    observations, lineage = _frames(rows)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
    )

    reduced_rows = rows[:-1]
    reduced_observations, reduced_lineage = _frames(reduced_rows)
    failing, failures = monthly_finance_qa_results(
        reduced_observations,
        reduced_lineage,
        version="B",
        dictionary=_dictionary(reduced_rows),
        transformation_catalog=_catalog(reduced_rows),
    )

    assert _status(passing, "california_58_county_retention") == "pass"
    assert _status(failing, "california_58_county_retention") == "fail"
    retention = failures.loc[
        failures["check_id"].eq("california_58_county_retention")
    ].iloc[0]
    assert retention["unique_county_count"] == 57
    assert retention["missing_counties"]


def test_dictionary_and_catalog_must_cover_outputs_exactly_once() -> None:
    rows = [_row()]
    observations, lineage = _frames(rows)
    wrong_dictionary = pd.DataFrame(
        {
            "variable_id": [
                "unused_monthly_hist",
                "unused_monthly_hist",
            ]
        }
    )
    wrong_catalog = pd.DataFrame(
        {
            "transformation_id": ["unused_v1", "unused_v1"],
        }
    )

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=wrong_dictionary,
        transformation_catalog=wrong_catalog,
        require_all_california_counties=False,
    )

    assert _status(summary, "version_dictionary_exact_coverage") == "fail"
    assert _status(summary, "transformation_catalog_exact_coverage") == "fail"
    reasons = set(failures["reason"].dropna())
    assert "duplicate_dictionary_variable_id" in reasons
    assert "missing_dictionary_variable_id" in reasons
    assert "duplicate_catalog_transformation_id" in reasons
    assert "missing_catalog_transformation_id" in reasons


def test_dictionary_enforces_unit_anchor_and_transformation_contract() -> None:
    row = _row()
    observations, lineage = _frames([row])
    dictionary = _dictionary([row])
    dictionary.loc[0, "unit"] = "ratio"

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=dictionary,
        transformation_catalog=_catalog([row]),
        require_all_california_counties=False,
    )

    assert _status(summary, "version_dictionary_row_contract") == "fail"
    assert "dictionary_unit_mismatch" in set(failures["reason"])


def test_version_c_rejects_cutoff_interpolation_and_lineage_lookahead() -> None:
    row = _row(
        version="C",
        month="2026-02-28",
        is_interpolated=True,
    )
    observations = normalize_monthly_finance(pd.DataFrame([row]))
    lineage = normalize_monthly_lineage(
        pd.DataFrame(
            [
                _lineage_for(
                    row,
                    release_date="2026-02-16",
                    retrieved_at="2026-02-17T00:00:00Z",
                )
            ]
        )
    )

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="C",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        require_all_california_counties=False,
    )

    assert _status(summary, "version_c_cutoff_no_interpolation_lookahead") == (
        "fail"
    )
    reasons = set(
        failures.loc[
            failures["check_id"].eq(
                "version_c_cutoff_no_interpolation_lookahead"
            ),
            "reason",
        ]
    )
    assert "future_endpoint_interpolation" in reasons
    assert "lineage_release_after_cutoff" in reasons
    assert "lineage_retrieval_after_cutoff" in reasons


def test_annual_downpayment_flow_months_sum_to_benchmark() -> None:
    benchmark_id = "hmda_aggregate_downpayment_flow_benchmark"
    rows = [
        _row(
            month=str(pd.Timestamp(2025, month, 1) + pd.offsets.MonthEnd(0)).split()[0],
            base_variable="hmda_aggregate_downpayment_flow_proxy",
            benchmark_variable=benchmark_id,
            value=10.0,
            lineage_id=f"flow-{month}",
            transformation_id=(
                "version_b_redfin_purchase_value_proxy_distribution_v1"
            ),
        )
        for month in range(1, 13)
    ]
    observations, lineage = _frames(rows)
    benchmark = _benchmark(benchmark_id, 120.0)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    broken = observations.copy()
    broken.loc[0, "value"] = 11.0
    broken.loc[0, "low_estimate"] = 11.0
    broken.loc[0, "central_estimate"] = 11.0
    broken.loc[0, "high_estimate"] = 11.0
    failing, failures = monthly_finance_qa_results(
        broken,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(passing, "annual_downpayment_flow_sum_identity") == "pass"
    assert _status(failing, "annual_downpayment_flow_sum_identity") == "fail"
    identity = failures.loc[
        failures["check_id"].eq("annual_downpayment_flow_sum_identity")
    ].iloc[0]
    assert identity["actual_monthly_sum"] == 121.0
    assert identity["benchmark_value"] == 120.0


def test_canonical_unavailable_flow_is_not_treated_as_proxy_distribution() -> None:
    benchmark_id = "hmda_aggregate_downpayment_flow_benchmark"
    rows = [
        _row(
            month=str(
                pd.Timestamp(2025, month, 1) + pd.offsets.MonthEnd(0)
            ).split()[0],
            base_variable="hmda_aggregate_downpayment_flow",
            benchmark_variable=benchmark_id,
            value=None,
            lineage_id=f"unavailable-flow-{month}",
            transformation_id="version_b_hmda_flow_unavailable_v1",
            quality_flag="unavailable_exact_financed_purchase_value",
        )
        for month in range(1, 13)
    ]
    observations, lineage = _frames(rows)

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=_benchmark(benchmark_id, None),
        require_all_california_counties=False,
    )

    assert _status(summary, "annual_downpayment_flow_sum_identity") == "pass"
    assert not failures["check_id"].eq(
        "annual_downpayment_flow_sum_identity"
    ).any()


def test_proxy_flow_accepts_twelve_null_months_for_null_benchmark() -> None:
    benchmark_id = "hmda_aggregate_downpayment_flow_benchmark"
    rows = [
        _row(
            month=str(
                pd.Timestamp(2025, month, 1) + pd.offsets.MonthEnd(0)
            ).split()[0],
            base_variable="hmda_aggregate_downpayment_flow_proxy",
            benchmark_variable=benchmark_id,
            value=None,
            lineage_id=f"null-proxy-flow-{month}",
            transformation_id=(
                "version_b_redfin_purchase_value_proxy_distribution_v1"
            ),
            is_suppressed=True,
            quality_flag="suppressed_annual_anchor",
        )
        for month in range(1, 13)
    ]
    observations, lineage = _frames(rows)

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=_benchmark(benchmark_id, None),
        require_all_california_counties=False,
    )

    assert _status(summary, "annual_downpayment_flow_sum_identity") == "pass"
    assert not failures["check_id"].eq(
        "annual_downpayment_flow_sum_identity"
    ).any()


def test_proxy_flow_accepts_explicit_null_when_redfin_year_is_incomplete() -> None:
    benchmark_id = "hmda_aggregate_downpayment_flow_benchmark"
    rows = [
        _row(
            month=str(
                pd.Timestamp(2025, month, 1) + pd.offsets.MonthEnd(0)
            ).split()[0],
            base_variable="hmda_aggregate_downpayment_flow_proxy",
            benchmark_variable=benchmark_id,
            value=None,
            lineage_id=f"incomplete-weight-proxy-{month}",
            transformation_id=(
                "version_b_redfin_purchase_value_proxy_distribution_v1"
            ),
            quality_flag=(
                "redfin_annual_weight_coverage_or_hmda_anchor_unavailable"
            ),
        )
        for month in range(1, 13)
    ]
    observations, lineage = _frames(rows)
    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=_benchmark(benchmark_id, 120.0),
        require_all_california_counties=False,
    )

    assert _status(summary, "annual_downpayment_flow_sum_identity") == "pass"
    assert not failures["check_id"].eq(
        "annual_downpayment_flow_sum_identity"
    ).any()


def test_annual_downpayment_rate_is_repeated_not_divided() -> None:
    benchmark_id = "hmda_median_downpayment_rate_benchmark"
    rows = [
        _row(
            base_variable="hmda_median_downpayment_rate",
            benchmark_variable=benchmark_id,
            value=0.2,
        )
    ]
    observations, lineage = _frames(rows)
    benchmark = _benchmark(benchmark_id, 0.2)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    divided = observations.copy()
    divided.loc[0, "value"] = 0.2 / 12.0
    divided.loc[0, "low_estimate"] = 0.2 / 12.0
    divided.loc[0, "central_estimate"] = 0.2 / 12.0
    divided.loc[0, "high_estimate"] = 0.2 / 12.0
    failing, _ = monthly_finance_qa_results(
        divided,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(passing, "repeated_downpayment_rate_identity") == "pass"
    assert _status(failing, "repeated_downpayment_rate_identity") == "fail"


def test_annual_downpayment_threshold_share_is_repeated_not_divided() -> None:
    benchmark_id = "hmda_share_downpayment_below_10pct_benchmark"
    rows = [
        _row(
            base_variable="hmda_share_downpayment_below_10pct",
            benchmark_variable=benchmark_id,
            value=0.3,
            unit="share",
        )
    ]
    observations, lineage = _frames(rows)
    benchmark = _benchmark(benchmark_id, 0.3)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )
    divided = observations.copy()
    divided.loc[0, [
        "value",
        "low_estimate",
        "central_estimate",
        "high_estimate",
    ]] = 0.3 / 12.0
    failing, _ = monthly_finance_qa_results(
        divided,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(passing, "repeated_downpayment_rate_identity") == "pass"
    assert _status(failing, "repeated_downpayment_rate_identity") == "fail"


def test_repeated_rate_accepts_explicitly_suppressed_null_anchor() -> None:
    benchmark_id = "hmda_median_downpayment_rate_benchmark"
    rows = [
        _row(
            base_variable="hmda_median_downpayment_rate",
            benchmark_variable=benchmark_id,
            value=None,
            transformation_id="version_b_hmda_annual_rate_repeat_v1",
            is_suppressed=True,
            quality_flag="suppressed_annual_rate",
        )
    ]
    observations, lineage = _frames(rows)

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=_benchmark(benchmark_id, None),
        require_all_california_counties=False,
    )

    assert _status(summary, "repeated_downpayment_rate_identity") == "pass"
    assert not failures["check_id"].eq(
        "repeated_downpayment_rate_identity"
    ).any()


def test_realtime_rate_accepts_explicit_null_before_anchor_period() -> None:
    benchmark_id = "hmda_median_downpayment_rate_benchmark"
    rows = [
        _row(
            version="C",
            month="2024-12-31",
            base_variable="hmda_median_downpayment_rate",
            benchmark_variable=benchmark_id,
            value=None,
            transformation_id="version_c_hmda_latest_released_rate_carry_v1",
            quality_flag="before_anchor_source_period",
            is_nowcast=False,
        )
    ]
    observations, lineage = _frames(rows)

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="C",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=_benchmark(benchmark_id, 0.2),
        require_all_california_counties=False,
    )

    assert _status(summary, "repeated_downpayment_rate_identity") == "pass"
    assert not failures["check_id"].eq(
        "repeated_downpayment_rate_identity"
    ).any()


def test_complete_anchor_year_payment_average_matches_benchmark() -> None:
    benchmark_id = (
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
    )
    rows = [
        _row(
            month=str(pd.Timestamp(2025, month, 1) + pd.offsets.MonthEnd(0)).split()[0],
            base_variable=(
                "acs_estimated_aggregate_monthly_first_mortgage_pi"
            ),
            benchmark_variable=benchmark_id,
            value=100.0,
            lineage_id=f"payment-{month}",
        )
        for month in range(1, 13)
    ]
    observations, lineage = _frames(rows)
    benchmark = _benchmark(benchmark_id, 100.0)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    broken = observations.copy()
    broken["value"] = 110.0
    broken["low_estimate"] = 110.0
    broken["central_estimate"] = 110.0
    broken["high_estimate"] = 110.0
    failing, _ = monthly_finance_qa_results(
        broken,
        lineage,
        version="B",
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(passing, "annual_average_payment_benchmark_identity") == (
        "pass"
    )
    assert _status(failing, "annual_average_payment_benchmark_identity") == (
        "fail"
    )


def test_version_c_benchmark_anchor_cannot_be_released_after_cutoff() -> None:
    row = _row(version="C", month="2026-02-28")
    observations, lineage = _frames([row])
    benchmark = _benchmark(
        str(row["benchmark_variable_id"]),
        100.0,
        release_date="2026-02-16",
    )

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="C",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(summary, "benchmark_anchor_coverage") == "fail"
    assert "benchmark_anchor_released_after_cutoff" in set(failures["reason"])


def test_scenario_variant_maps_to_central_version_a_anchor() -> None:
    benchmark_id = (
        "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity_benchmark"
    )
    row = _row(
        version="C",
        month="2026-02-28",
        base_variable=(
            "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity"
        ),
        benchmark_variable=benchmark_id,
        estimate_variant="pmms_20y_sensitivity",
        transformation_id="version_c_stepwise_pmms_rate_debt_sensitivity_v1",
    )
    observations, lineage = _frames([row])

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="C",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        benchmark=_benchmark(benchmark_id, 100.0),
        require_all_california_counties=False,
    )

    assert _status(summary, "benchmark_anchor_coverage") == "pass"
    assert not failures["check_id"].eq("benchmark_anchor_coverage").any()


def test_available_benchmark_uncertainty_bounds_must_be_propagated() -> None:
    benchmark_id = "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
    row = _row(
        base_variable="acs_estimated_aggregate_monthly_first_mortgage_pi",
        benchmark_variable=benchmark_id,
        value=100.0,
        low_estimate=None,
        central_estimate=100.0,
        high_estimate=None,
    )
    observations, lineage = _frames([row])
    benchmark = _benchmark(benchmark_id, 100.0)
    benchmark["low_estimate"] = 90.0
    benchmark["central_estimate"] = 100.0
    benchmark["high_estimate"] = 110.0

    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        benchmark=benchmark,
        require_all_california_counties=False,
    )

    assert _status(summary, "benchmark_uncertainty_bounds_presence") == "fail"
    assert "eligible_benchmark_bounds_not_propagated" in set(
        failures["reason"]
    )


@pytest.mark.parametrize("version", ["B", "C"])
def test_debt_plus_equity_must_equal_matched_owner_value_stock(
    version: str,
) -> None:
    suffix = "_monthly_rt" if version == "C" else "_monthly_hist"
    debt_base = "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity"
    rows = [
        _row(
            version=version,
            base_variable=debt_base,
            benchmark_variable=f"{debt_base}_benchmark",
            value=40.0,
            estimate_variant="pmms_20y_sensitivity",
            transformation_id=(
                "version_c_stepwise_pmms_rate_debt_sensitivity_v1"
                if version == "C"
                else "version_b_single_anchor_nmdb_redfin_pmms_debt_v1"
            ),
            lineage_id=f"{version}-debt-accounting",
        ),
        _row(
            version=version,
            base_variable=(
                "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_scenario"
            ),
            benchmark_variable=(
                "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_scenario_benchmark"
            ),
            value=60.0,
            estimate_variant="pmms_20y_scenario",
            transformation_id=f"version_{version.lower()}_hpi_updated_pmms_equity_v1",
            lineage_id=f"{version}-equity-accounting",
            matched_owner_value_stock=100.0,
            accounting_debt_variable_id=f"{debt_base}{suffix}",
        ),
    ]
    observations, lineage = _frames(rows)

    passing, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version=version,
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        require_all_california_counties=False,
    )

    broken = observations.copy()
    equity_mask = broken["variable_id"].str.contains("home_equity", na=False)
    broken.loc[equity_mask, [
        "value",
        "low_estimate",
        "central_estimate",
        "high_estimate",
    ]] = 59.0
    failing, _ = monthly_finance_qa_results(
        broken,
        lineage,
        version=version,
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        require_all_california_counties=False,
    )

    assert _status(passing, "debt_equity_owner_value_stock_identity") == "pass"
    assert _status(failing, "debt_equity_owner_value_stock_identity") == "fail"


@pytest.mark.parametrize("version", ["B", "C"])
def test_equity_explicit_null_is_valid_when_owner_value_path_is_unavailable(
    version: str,
) -> None:
    suffix = "_monthly_rt" if version == "C" else "_monthly_hist"
    debt_base = "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity"
    rows = [
        _row(
            version=version,
            base_variable=debt_base,
            benchmark_variable=f"{debt_base}_benchmark",
            value=40.0,
            estimate_variant="pmms_20y_sensitivity",
            transformation_id=(
                "version_c_stepwise_pmms_rate_debt_sensitivity_v1"
                if version == "C"
                else "version_b_single_anchor_nmdb_redfin_pmms_debt_v1"
            ),
            lineage_id=f"{version}-debt-unavailable-owner-value",
        ),
        _row(
            version=version,
            base_variable=(
                "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_scenario"
            ),
            benchmark_variable=(
                "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_scenario_benchmark"
            ),
            value=None,
            low_estimate=None,
            central_estimate=None,
            high_estimate=None,
            estimate_variant="pmms_20y_scenario",
            transformation_id=f"version_{version.lower()}_hpi_updated_pmms_equity_v1",
            lineage_id=f"{version}-equity-unavailable-owner-value",
            matched_owner_value_stock=None,
            accounting_debt_variable_id=f"{debt_base}{suffix}",
            quality_flag="hpi_anchor_or_matching_pmms_debt_unavailable",
        ),
    ]
    observations, lineage = _frames(rows)
    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version=version,
        dictionary=_dictionary(rows),
        transformation_catalog=_catalog(rows),
        require_all_california_counties=False,
    )

    assert _status(summary, "debt_equity_owner_value_stock_identity") == "pass"
    assert not failures["check_id"].eq(
        "debt_equity_owner_value_stock_identity"
    ).any()


def test_estimate_bound_order_is_a_structural_error() -> None:
    row = _row(low_estimate=110.0, central_estimate=100.0, high_estimate=90.0)
    observations, lineage = _frames([row])

    summary, _ = monthly_finance_qa_results(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        require_all_california_counties=False,
    )

    assert _status(summary, "monthly_finance_contract") == "fail"
    assert _status(summary, "estimate_bounds_order") == "fail"


def test_plausible_outlier_is_warning_and_does_not_raise() -> None:
    row = _row(value=-1.0, low_estimate=-1.0, central_estimate=-1.0, high_estimate=-1.0)
    observations, lineage = _frames([row])

    summary = run_monthly_finance_qa(
        observations,
        lineage,
        version="B",
        dictionary=_dictionary([row]),
        transformation_catalog=_catalog([row]),
        require_all_california_counties=False,
    )

    assert _status(summary, "economic_plausibility_review") == "warn"


def test_run_raises_on_errors_after_writing_summary_and_failures(
    tmp_path: Path,
) -> None:
    row = _row(is_nowcast=True)
    observations, lineage = _frames([row])

    with pytest.raises(RuntimeError, match="structural QA failed"):
        run_monthly_finance_qa(
            observations,
            lineage,
            version="B",
            dictionary=_dictionary([row]),
            transformation_catalog=_catalog([row]),
            root=tmp_path,
            require_all_california_counties=False,
        )

    summary_path = tmp_path / "output/qa/version_b_qa_summary.csv"
    failures_path = tmp_path / "output/qa/version_b_qa_failures.csv"
    assert summary_path.is_file()
    assert failures_path.is_file()
    written = pd.read_csv(summary_path)
    assert (
        written.loc[written["severity"].eq("error"), "status"].eq("fail").any()
    )
