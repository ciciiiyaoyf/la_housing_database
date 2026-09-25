from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.benchmark.schema import BENCHMARK_COLUMNS
from src.housing_finance_monthly.schema import (
    MONTHLY_FINANCE_COLUMNS,
    MONTHLY_LINEAGE_COLUMNS,
    VERSION_B_DATABASE_NAME,
    VERSION_C_DATABASE_NAME,
    empty_monthly_finance,
    empty_monthly_lineage,
    finalize_monthly_finance,
    monthly_finance_contract_errors,
    monthly_lineage_contract_errors,
    normalize_monthly_finance,
    normalize_monthly_lineage,
)


CHECKSUM = "a" * 64


def _observation(version: str = "B", **overrides: object) -> dict[str, object]:
    is_realtime = version == "C"
    suffix = "_monthly_rt" if is_realtime else "_monthly_hist"
    database_version = (
        VERSION_C_DATABASE_NAME if is_realtime else VERSION_B_DATABASE_NAME
    )
    row: dict[str, object] = {
        "county_fips": "06037",
        "state_fips": "06",
        "geo_id": "COUNTY_06037",
        "month": "2026-03-31" if is_realtime else "2025-01-31",
        "value": 100.0,
        "variable_name": (
            "acs_implied_first_mortgage_balance_cal" + suffix
        ),
        "variable_id": "acs_implied_first_mortgage_balance_cal" + suffix,
        "unit": "USD",
        "estimate_variant": "central",
        "reported_value": None,
        "margin_of_error": None,
        "database_version": database_version,
        "source_id": "census_acs5_finance|zillow_county_zhvi",
        "source_frequency": "mixed",
        "source_period_start": "2020-01-01",
        "source_period_end": "2024-12-31",
        "reference_date": "2022-07-01",
        "release_date": "2026-03-01" if is_realtime else "2026-01-29",
        "source_release_dates": (
            "census_acs5_finance=2026-01-29|"
            "zillow_county_zhvi=2026-03-01"
        ),
        "source_geography": "California county",
        "parameter_geography": "California county",
        "monthly_method": (
            "latest_released_anchor_with_indicator_update"
            if is_realtime
            else "retrospective_indicator_benchmarking"
        ),
        "construction_method": "documented monthly stock path",
        "payment_frequency": "not applicable",
        "is_modeled": True,
        "is_nowcast": is_realtime,
        "is_imputed": False,
        "is_interpolated": False,
        "is_suppressed": False,
        "benchmark_vintage": "2024_ACS5",
        "sample_n": None,
        "coverage_rate": 1.0,
        "low_estimate": 90.0,
        "central_estimate": 100.0,
        "high_estimate": 110.0,
        "universe_definition": "owner-occupied first mortgages",
        "notes": "test fixture",
        "source_vintage": "2024_ACS5|2026-03",
        "retrieved_at": "2026-03-02T12:00:00Z",
        "source_filename": "normalized_lineage_companion",
        "checksum_sha256": CHECKSUM,
        "transformation_id": (
            "version_c_monthly_debt_v1"
            if is_realtime
            else "version_b_monthly_debt_v1"
        ),
        "lineage_id": "lineage-c-1" if is_realtime else "lineage-b-1",
        "quality_flag": "nowcast" if is_realtime else "modeled_history",
        "benchmark_variable_id": (
            "acs_implied_first_mortgage_balance_cal_benchmark"
        ),
        "information_cutoff_date": "2026-03-15" if is_realtime else None,
        "method_version": (
            "version_c_monthly_debt_v1"
            if is_realtime
            else "version_b_monthly_debt_v1"
        ),
    }
    row.update(overrides)
    return row


def _observations(version: str = "B", **overrides: object) -> pd.DataFrame:
    return normalize_monthly_finance(
        pd.DataFrame([_observation(version, **overrides)])
    )


def _lineage(
    version: str = "B",
    **overrides: object,
) -> pd.DataFrame:
    observation = _observation(version)
    row: dict[str, object] = {
        "lineage_id": observation["lineage_id"],
        "database_version": observation["database_version"],
        "information_cutoff_date": observation["information_cutoff_date"],
        "county_fips": observation["county_fips"],
        "month": observation["month"],
        "variable_id": observation["variable_id"],
        "estimate_variant": observation["estimate_variant"],
        "input_role": "benchmark_anchor",
        "source_id": "census_acs5_finance",
        "source_vintage": "2024_ACS5",
        "source_frequency": "ACS 5-year",
        "source_period_start": "2020-01-01",
        "source_period_end": "2024-12-31",
        "reference_date": "2022-07-01",
        "release_date": "2026-01-29",
        "source_geography": "California county",
        "parameter_geography": "California county",
        "retrieved_at": "2026-02-01T12:00:00Z",
        "source_filename": "acs_finance_summary_files_2024.zip",
        "checksum_sha256": CHECKSUM,
    }
    row.update(overrides)
    return normalize_monthly_lineage(pd.DataFrame([row]))


def test_monthly_schema_is_a_superset_without_mutating_version_a_columns() -> None:
    assert MONTHLY_FINANCE_COLUMNS[: len(BENCHMARK_COLUMNS)] == BENCHMARK_COLUMNS
    assert MONTHLY_FINANCE_COLUMNS[-5:] == [
        "benchmark_variable_id",
        "information_cutoff_date",
        "method_version",
        "matched_owner_value_stock",
        "accounting_debt_variable_id",
    ]
    assert list(empty_monthly_finance().columns) == MONTHLY_FINANCE_COLUMNS
    assert list(empty_monthly_lineage().columns) == MONTHLY_LINEAGE_COLUMNS


@pytest.mark.parametrize("version", ["B", "C"])
def test_valid_version_contracts(version: str) -> None:
    frame = _observations(version)

    assert monthly_finance_contract_errors(frame, version=version) == []


@pytest.mark.parametrize(
    ("version", "wrong_suffix"),
    [("B", "_monthly_rt"), ("C", "_monthly_hist")],
)
def test_suffix_and_database_version_are_hard_contracts(
    version: str,
    wrong_suffix: str,
) -> None:
    wrong_name = "acs_implied_first_mortgage_balance_cal" + wrong_suffix
    frame = _observations(
        version,
        variable_id=wrong_name,
        variable_name=wrong_name,
        database_version="A. Benchmark",
    )

    errors = monthly_finance_contract_errors(frame, version=version)

    assert any("must end" in error for error in errors)
    assert any("database_version" in error for error in errors)


@pytest.mark.parametrize("version", ["B", "C"])
def test_month_must_be_calendar_month_end(version: str) -> None:
    frame = _observations(version, month="2025-01-30")

    errors = monthly_finance_contract_errors(frame, version=version)

    assert any("calendar month-end" in error for error in errors)


@pytest.mark.parametrize("version", ["B", "C"])
def test_version_specific_primary_key_must_be_unique(version: str) -> None:
    frame = _observations(version)
    duplicated = pd.concat([frame, frame], ignore_index=True)
    duplicated.loc[1, "lineage_id"] = "different-lineage"

    errors = monthly_finance_contract_errors(duplicated, version=version)

    assert any("duplicate monthly finance key" in error for error in errors)


def test_version_b_requires_modeled_non_nowcast_rows_without_cutoff() -> None:
    frame = _observations(
        "B",
        is_modeled=False,
        is_nowcast=True,
        information_cutoff_date="2026-01-31",
    )

    errors = monthly_finance_contract_errors(frame, version="B")

    assert any("must be modeled" in error for error in errors)
    assert any("cannot be marked as nowcasts" in error for error in errors)
    assert any("information_cutoff_date must be null" in error for error in errors)


def test_version_c_rejects_missing_cutoff_interpolation_and_future_release() -> None:
    missing_cutoff = _observations(
        "C",
        information_cutoff_date=None,
        is_interpolated=True,
    )
    future_release = _observations(
        "C",
        release_date="2026-03-16",
    )
    future_month = _observations(
        "C",
        month="2026-04-30",
    )

    missing_errors = monthly_finance_contract_errors(
        missing_cutoff,
        version="C",
    )
    release_errors = monthly_finance_contract_errors(
        future_release,
        version="C",
    )
    month_errors = monthly_finance_contract_errors(future_month, version="C")

    assert any("cutoff_date must be populated" in error for error in missing_errors)
    assert any("cannot use future-endpoint" in error for error in missing_errors)
    assert any("release_date cannot exceed" in error for error in release_errors)
    assert any("month after its cutoff month" in error for error in month_errors)


def test_all_flags_are_explicit_and_null_values_are_explained() -> None:
    frame = _observations(
        "B",
        is_imputed=None,
        value=None,
        quality_flag=None,
    )

    errors = monthly_finance_contract_errors(frame, version="B")

    assert any("flags must be explicit" in error for error in errors)
    assert any("null monthly values" in error for error in errors)


def test_finalize_sorts_and_can_preserve_a_flagged_null() -> None:
    later = _observation("B", month="2025-02-28", lineage_id="lineage-b-2")
    missing = _observation(
        "B",
        month="2025-01-31",
        value=None,
        low_estimate=None,
        central_estimate=None,
        high_estimate=None,
        quality_flag="calibration_unavailable",
    )

    result = finalize_monthly_finance(
        pd.DataFrame([later, missing]),
        version="B",
    )

    assert result["month"].tolist() == [
        pd.Timestamp("2025-01-31"),
        pd.Timestamp("2025-02-28"),
    ]
    assert pd.isna(result.loc[0, "value"])


def test_finalize_rejects_an_invalid_contract(tmp_path: Path) -> None:
    output = tmp_path / "must_not_exist.parquet"

    with pytest.raises(ValueError, match="month must be"):
        finalize_monthly_finance(
            pd.DataFrame([_observation("B", month="2025-01-01")]),
            output,
            version="B",
        )

    assert not output.exists()


@pytest.mark.parametrize("version", ["B", "C"])
def test_valid_normalized_lineage_contract(version: str) -> None:
    observations = _observations(version)
    lineage = _lineage(version)

    assert monthly_lineage_contract_errors(
        lineage,
        observations,
        version=version,
    ) == []


def test_lineage_requires_complete_source_provenance() -> None:
    observations = _observations("B")
    lineage = _lineage(
        "B",
        source_filename=None,
        checksum_sha256="not-a-checksum",
        release_date=None,
    )

    errors = monthly_lineage_contract_errors(
        lineage,
        observations,
        version="B",
    )

    assert any("cannot be blank" in error for error in errors)
    assert any("dates must be populated" in error for error in errors)
    assert any("64-character hex" in error for error in errors)


def test_lineage_must_cover_only_matching_observations() -> None:
    observations = _observations("B")
    missing = _lineage("B", lineage_id="orphan-lineage")

    errors = monthly_lineage_contract_errors(
        missing,
        observations,
        version="B",
    )

    assert any("without input lineage" in error for error in errors)
    assert any("orphan monthly lineage" in error for error in errors)


def test_lineage_context_must_match_observation() -> None:
    observations = _observations("B")
    lineage = _lineage("B", month="2025-02-28")

    errors = monthly_lineage_contract_errors(
        lineage,
        observations,
        version="B",
    )

    assert any("context does not match" in error for error in errors)


def test_version_c_lineage_rejects_future_release_and_retrieval() -> None:
    observations = _observations("C")
    future = _lineage(
        "C",
        release_date="2026-03-16",
        retrieved_at="2026-03-17T12:00:00Z",
    )

    errors = monthly_lineage_contract_errors(
        future,
        observations,
        version="C",
    )

    assert any("release_date cannot exceed its cutoff" in error for error in errors)
    assert any("retrieved_at cannot exceed its cutoff" in error for error in errors)


def test_duplicate_lineage_input_is_rejected() -> None:
    observations = _observations("B")
    lineage = _lineage("B")
    duplicated = pd.concat([lineage, lineage], ignore_index=True)

    errors = monthly_lineage_contract_errors(
        duplicated,
        observations,
        version="B",
    )

    assert any("duplicate monthly lineage input key" in error for error in errors)


def test_unknown_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="Version B or C"):
        monthly_finance_contract_errors(_observations("B"), version="A")
