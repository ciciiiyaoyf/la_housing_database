import numpy as np
import pandas as pd
import pytest

from src.features.derived import (
    APARTMENT_COMPLETION_LAG_WEIGHTS,
    APARTMENT_COMPLETION_PROXY_COLUMNS,
    MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS,
    apartment_completion_proxies,
    multifamily_under_construction_proxies,
)


def _monthly_frame(
    periods: int = 100,
    *,
    counties: tuple[str, ...] = ("06037",),
    permits: float = 0.0,
) -> pd.DataFrame:
    dates = pd.date_range("2010-01-01", periods=periods, freq="MS")
    return pd.DataFrame(
        [
            {
                "date": date,
                "county_fips": county,
                "permits_5_plus_units": permits,
            }
            for date in dates
            for county in counties
        ]
    )


def test_completion_weights_exactly_match_approved_buckets():
    central = APARTMENT_COMPLETION_LAG_WEIGHTS["central"]
    slow = APARTMENT_COMPLETION_LAG_WEIGHTS["slow"]

    assert set(central) == set(range(12, 31))
    assert [central[lag] for lag in range(12, 18)] == pytest.approx([0.20 / 6] * 6)
    assert [central[lag] for lag in range(18, 24)] == pytest.approx([0.55 / 6] * 6)
    assert [central[lag] for lag in range(24, 31)] == pytest.approx([0.25 / 7] * 7)
    assert sum(central.values()) == pytest.approx(1.0)

    assert set(slow) == set(range(18, 37))
    assert [slow[lag] for lag in range(18, 24)] == pytest.approx([0.20 / 6] * 6)
    assert [slow[lag] for lag in range(24, 30)] == pytest.approx([0.50 / 6] * 6)
    assert [slow[lag] for lag in range(30, 37)] == pytest.approx([0.30 / 7] * 7)
    assert sum(slow.values()) == pytest.approx(1.0)


def test_permit_impulse_completes_only_at_approved_scenario_lags():
    frame = _monthly_frame()
    frame.loc[40, "permits_5_plus_units"] = 420.0
    result = apartment_completion_proxies(frame)

    for scenario, weights in APARTMENT_COMPLETION_LAG_WEIGHTS.items():
        column = APARTMENT_COMPLETION_PROXY_COLUMNS[scenario]
        expected_nonzero = {40 + lag for lag in weights}
        actual_nonzero = set(result.index[result[column].fillna(0.0).ne(0.0)])
        assert actual_nonzero == expected_nonzero
        for lag, weight in weights.items():
            assert result.loc[40 + lag, column] == pytest.approx(420.0 * weight)


def test_outputs_require_complete_scenario_history_before_warmup_ends():
    frame = _monthly_frame(periods=50, permits=1.0)
    completions = apartment_completion_proxies(frame)
    stocks = multifamily_under_construction_proxies(frame)

    assert completions["new_apartments_completed_proxy_central"].first_valid_index() == 30
    assert completions["new_apartments_completed_proxy_slow"].first_valid_index() == 36
    assert completions.loc[30, "new_apartments_completed_proxy_central"] == pytest.approx(1.0)
    assert completions.loc[36, "new_apartments_completed_proxy_slow"] == pytest.approx(1.0)

    # The stock needs every cohort that can still be under construction: the
    # current cohort plus ages 1-29 (Central) or 1-35 (Slow).
    assert stocks["multifamily_under_construction_proxy_central"].first_valid_index() == 29
    assert stocks["multifamily_under_construction_proxy_slow"].first_valid_index() == 35


def test_true_missing_permit_propagates_and_stocks_reinitialize_after_it_ages_out():
    frame = _monthly_frame(periods=90, permits=10.0)
    frame.loc[40, "permits_5_plus_units"] = np.nan
    completions = apartment_completion_proxies(frame)
    stocks = multifamily_under_construction_proxies(frame)

    central_completion = completions["new_apartments_completed_proxy_central"]
    slow_completion = completions["new_apartments_completed_proxy_slow"]
    assert central_completion.loc[52:70].isna().all()
    assert slow_completion.loc[58:76].isna().all()
    assert central_completion.loc[51] == pytest.approx(10.0)
    assert central_completion.loc[71] == pytest.approx(10.0)

    central_stock = stocks["multifamily_under_construction_proxy_central"]
    slow_stock = stocks["multifamily_under_construction_proxy_slow"]
    assert central_stock.loc[40:69].isna().all()
    assert slow_stock.loc[40:75].isna().all()
    assert pd.notna(central_stock.loc[70])
    assert pd.notna(slow_stock.loc[76])


def test_absent_calendar_month_is_not_treated_as_zero_or_a_positional_lag():
    frame = _monthly_frame(periods=80, permits=10.0).drop(index=40).reset_index(drop=True)
    missing_month = pd.Timestamp("2013-05-01")
    result = apartment_completion_proxies(frame)

    affected = result["date"].eq(missing_month + pd.DateOffset(months=12))
    assert affected.sum() == 1
    assert result.loc[affected, "new_apartments_completed_proxy_central"].isna().all()


def test_construction_stock_is_nonnegative_and_obeys_stock_flow_recurrence():
    frame = _monthly_frame(periods=100)
    frame["permits_5_plus_units"] = [float((index * 7) % 23) for index in frame.index]
    result = apartment_completion_proxies(frame)
    result = multifamily_under_construction_proxies(result)

    for scenario in ("central", "slow"):
        completion = result[APARTMENT_COMPLETION_PROXY_COLUMNS[scenario]]
        stock = result[MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS[scenario]]
        assert stock.dropna().ge(0.0).all()
        comparable = stock.notna() & stock.shift(1).notna() & completion.notna()
        expected = stock.shift(1) + result["permits_5_plus_units"] - completion
        assert stock.loc[comparable].to_numpy() == pytest.approx(
            expected.loc[comparable].to_numpy(), abs=1e-10
        )


def test_counties_are_isolated_even_when_rows_are_interleaved():
    frame = _monthly_frame(counties=("06037", "06059"))
    impulse = frame["county_fips"].eq("06037") & frame["date"].eq(
        pd.Timestamp("2013-05-01")
    )
    frame.loc[impulse, "permits_5_plus_units"] = 100.0
    result = apartment_completion_proxies(frame)
    result = multifamily_under_construction_proxies(result)

    other_county = result["county_fips"].eq("06059")
    for column in (
        *APARTMENT_COMPLETION_PROXY_COLUMNS.values(),
        *MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values(),
    ):
        assert result.loc[other_county, column].dropna().eq(0.0).all()

    la_county = result["county_fips"].eq("06037")
    assert (
        result.loc[la_county, "new_apartments_completed_proxy_central"]
        .fillna(0.0)
        .gt(0.0)
        .any()
    )


def test_negative_permits_are_rejected_instead_of_creating_negative_proxies():
    frame = _monthly_frame(periods=40)
    frame.loc[20, "permits_5_plus_units"] = -1.0

    with pytest.raises(ValueError, match="nonnegative"):
        apartment_completion_proxies(frame)
    with pytest.raises(ValueError, match="nonnegative"):
        multifamily_under_construction_proxies(frame)


def test_only_central_and_slow_proxy_scenarios_are_created():
    frame = _monthly_frame(periods=40)
    result = apartment_completion_proxies(frame)
    result = multifamily_under_construction_proxies(result)

    added = set(result.columns) - set(frame.columns)
    assert added == {
        *APARTMENT_COMPLETION_PROXY_COLUMNS.values(),
        *MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values(),
    }
    assert "new_apartments_completed_proxy" not in result
    assert "multifamily_under_construction_proxy" not in result
