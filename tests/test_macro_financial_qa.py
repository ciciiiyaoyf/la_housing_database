"""Independent corruption tests for the macro/rates QA contract."""

import numpy as np
import pandas as pd
import pytest

from src.validation.macro_financial import (
    CES_BASE_IDS, CLAIMS_BASE_IDS, DAILY_BASE_IDS, macro_financial_failures,
)


def rows(variable, dates, values, *, geo="US", frequency="monthly", retrieved="2026-03-01T00:00:00Z"):
    return pd.DataFrame({
        "variable_id": variable, "geo_id": geo,
        "observation_date": pd.to_datetime(dates), "value": values,
        "final_frequency": frequency, "retrieved_at": retrieved,
        "source_vintage": "2026-03-01",
    })


def reasons(frame):
    return set(macro_financial_failures(frame)["reason"])


def test_unrelated_legacy_facts_are_ignored():
    assert macro_financial_failures(pd.DataFrame()).empty
    assert macro_financial_failures(pd.DataFrame({"variable_id": ["zhvi"]})).empty


@pytest.mark.parametrize("base", CES_BASE_IDS)
@pytest.mark.parametrize("geo", ["STATE_06", "METRODIV_31084"])
def test_ces_growth_uses_exact_calendar_arithmetic_yoy(base, geo):
    dates = ["2024-01-31", "2024-12-31", "2025-01-31", "2025-02-28"]
    frame = pd.concat([
        rows(base, dates, [100, 105, 110, 121], geo=geo),
        rows(base + "_yoy_pct", dates, [np.nan, np.nan, 10, np.nan], geo=geo),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty
    frame.loc[frame.variable_id.eq(base + "_yoy_pct") & frame.observation_date.eq("2025-01-31"), "value"] = 100 * np.log(1.1)
    assert "formula_or_missingness_mismatch" in reasons(frame)


def test_missing_growth_outputs_and_orphan_outputs_fail():
    base = CES_BASE_IDS[0]
    frame = rows(base, ["2025-01-31"], [100], geo="STATE_06")
    assert "missing_derived_observation" in reasons(frame)
    orphan = rows(base + "_yoy_pct", ["2025-01-31"], [10], geo="STATE_06")
    assert "unexpected_derived_observation" in reasons(orphan)


def test_gdp_compounds_quarterly_growth_and_preserves_missing_quarters():
    dates = ["2024-03-31", "2024-12-31", "2025-03-31", "2025-09-30"]
    frame = pd.concat([
        rows("bea_ca_real_gdp_quarterly", dates, [100, 110, 121, 130], geo="STATE_06", frequency="quarterly"),
        rows("bea_ca_real_gdp_qoq_annualized_pct", dates, [np.nan, np.nan, 100 * (1.1**4 - 1), np.nan], geo="STATE_06", frequency="quarterly"),
        rows("bea_ca_real_gdp_yoy_pct", dates, [np.nan, np.nan, 21, np.nan], geo="STATE_06", frequency="quarterly"),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty
    frame.loc[frame.variable_id.eq("bea_ca_real_gdp_qoq_annualized_pct") & frame.observation_date.eq("2025-03-31"), "value"] = 40
    assert "formula_or_missingness_mismatch" in reasons(frame)


@pytest.mark.parametrize("base", DAILY_BASE_IDS)
def test_daily_monthly_mean_ignores_null_holidays_and_current_month(base):
    stem = base.removesuffix("_daily")
    frame = pd.concat([
        rows(base, ["2026-01-01", "2026-01-02", "2026-01-05", "2026-02-02"], [np.nan, 4, 6, 99], frequency="daily", retrieved="2026-02-10T00:00:00Z"),
        rows(stem + "_monthly_average", ["2026-01-31"], [5]),
        rows(stem + "_monthly_observation_count", ["2026-01-31"], [2]),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty
    wrong = pd.concat([frame, rows(stem + "_monthly_average", ["2026-02-28"], [99])], ignore_index=True)
    assert "unexpected_derived_observation" in reasons(wrong)
    frame.loc[frame.variable_id.eq(stem + "_monthly_average"), "value"] = 10 / 3
    assert "formula_or_missingness_mismatch" in reasons(frame)


@pytest.mark.parametrize("base", CLAIMS_BASE_IDS)
def test_claims_average_requires_every_saturday_and_counts_nonnull(base):
    stem = base.removesuffix("_weekly")
    dates = ["2026-01-03", "2026-01-10", "2026-01-17", "2026-01-24", "2026-01-31", "2026-02-07", "2026-02-14", "2026-02-21"]
    frame = pd.concat([
        rows(base, dates, [10, 20, 30, 40, 50, 60, 70, 80], geo="STATE_06", frequency="weekly"),
        rows(stem + "_monthly_average", ["2026-01-31", "2026-02-28"], [30, np.nan], geo="STATE_06"),
        rows(stem + "_monthly_observation_count", ["2026-01-31", "2026-02-28"], [5, 3], geo="STATE_06"),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty
    frame.loc[frame.variable_id.eq(stem + "_monthly_average") & frame.observation_date.eq("2026-02-28"), "value"] = 70
    assert "formula_or_missingness_mismatch" in reasons(frame)


def test_current_month_cutoff_uses_utc_calendar():
    base = "us_sofr_daily"
    frame = pd.concat([
        rows(base, ["2026-01-30"], [4], frequency="daily", retrieved="2026-01-31T20:00:00-05:00"),
        rows("us_sofr_monthly_average", ["2026-01-31"], [4]),
        rows("us_sofr_monthly_observation_count", ["2026-01-31"], [1]),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty


@pytest.mark.parametrize("base", CLAIMS_BASE_IDS)
def test_wholly_absent_claims_month_has_null_mean_zero_count_without_native_fill(base):
    stem = base.removesuffix("_weekly")
    # A real-world failure involved the entirely absent July 1987 calendar
    # month: preserve its diagnostic output without adding any weekly rows.
    frame = pd.concat([
        rows(base, ["1987-06-06", "1987-08-01"], [10, 20], geo="STATE_06", frequency="weekly", retrieved="1987-09-01T00:00:00Z"),
        rows(stem + "_monthly_average", ["1987-06-30", "1987-07-31", "1987-08-31"], [np.nan, np.nan, np.nan], geo="STATE_06"),
        rows(stem + "_monthly_observation_count", ["1987-06-30", "1987-07-31", "1987-08-31"], [1, 0, 1], geo="STATE_06"),
    ], ignore_index=True)
    unchanged = frame.copy(deep=True)
    assert macro_financial_failures(frame).empty
    pd.testing.assert_frame_equal(frame, unchanged)
    assert len(frame.loc[frame.variable_id.eq(base)]) == 2
    absent_month = frame.observation_date.eq("1987-07-31")
    frame.loc[absent_month & frame.variable_id.eq(stem + "_monthly_average"), "value"] = 0
    assert "formula_or_missingness_mismatch" in reasons(frame)
    frame.loc[absent_month & frame.variable_id.eq(stem + "_monthly_average"), "value"] = np.nan
    frame.loc[absent_month & frame.variable_id.eq(stem + "_monthly_observation_count"), "value"] = 1
    assert "formula_or_missingness_mismatch" in reasons(frame)


def test_empty_claims_month_diagnostics_are_required_only_within_closed_native_span():
    base = "ca_initial_claims_weekly"
    stem = base.removesuffix("_weekly")
    frame = pd.concat([
        rows(base, ["1987-06-06", "1987-08-01"], [10, 20], geo="STATE_06", frequency="weekly", retrieved="1987-08-15T00:00:00Z"),
        rows(stem + "_monthly_average", ["1987-06-30", "1987-07-31"], [np.nan, np.nan], geo="STATE_06"),
        rows(stem + "_monthly_observation_count", ["1987-06-30", "1987-07-31"], [1, 0], geo="STATE_06"),
    ], ignore_index=True)
    assert macro_financial_failures(frame).empty
    without_july = frame.loc[~frame.observation_date.eq("1987-07-31")]
    assert "missing_derived_observation" in reasons(without_july)
    # Neither an open August nor a date before the native span is an allowed
    # monthly result, even when a caller supplies only a zero-count diagnostic.
    for date in ("1987-05-31", "1987-08-31"):
        extra = rows(stem + "_monthly_observation_count", [date], [0], geo="STATE_06")
        assert "unexpected_derived_observation" in reasons(pd.concat([frame, extra]))


def test_claims_current_month_only_does_not_require_monthly_outputs():
    frame = rows("ca_initial_claims_weekly", ["2026-02-07"], [10], geo="STATE_06", frequency="weekly", retrieved="2026-02-10T00:00:00Z")
    assert macro_financial_failures(frame).empty


@pytest.mark.parametrize("column,value,reason", [
    ("geo_id", "COUNTY_06037", "invalid_geography"),
    ("final_frequency", "daily", "invalid_frequency"),
    ("final_frequency", pd.NA, "invalid_frequency"),
    ("observation_date", pd.Timestamp("2025-01-01"), "monthly_date_not_month_end"),
    ("value", np.inf, "nonfinite_value"),
    ("value", "not a number", "nonnumeric_value"),
])
def test_structural_corruptions_are_reported(column, value, reason):
    frame = rows("us_federal_funds_rate_monthly", ["2025-01-31"], [4.5])
    frame[column] = value
    assert reason in reasons(frame)


def test_duplicates_and_missing_source_retrieval_are_reported():
    frame = rows("us_federal_funds_rate_monthly", ["2025-01-31"], [4.5])
    assert "duplicate_observation_key" in reasons(pd.concat([frame, frame]))
    daily = rows("us_sofr_daily", ["2025-01-31"], [4], frequency="daily", retrieved=None)
    assert "missing_or_invalid_retrieval_timestamp" in reasons(daily)


def test_claims_saturday_and_required_schema_are_checked():
    frame = rows("ca_initial_claims_weekly", ["2026-01-02"], [10], geo="STATE_06", frequency="weekly")
    assert "claims_date_not_saturday" in reasons(frame)
    assert "missing_required_columns" in reasons(frame.drop(columns="value"))
