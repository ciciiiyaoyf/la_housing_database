import numpy as np
import pandas as pd
import pytest

from src.forecasting.models import fit_forecast


@pytest.fixture
def prices():
    months = pd.period_range("2010-01", periods=125, freq="M")
    changes = np.random.default_rng(33).normal(0.3, 0.2, size=len(months))
    return pd.Series(500_000 * np.exp(np.cumsum(changes) / 100), index=months)


def test_common_fit_and_recursive_growth_recovery(prices):
    start, origin = prices.index[3], prices.index[-20]
    fits = [fit_forecast(prices, origin, start, f"growth_ar{lag}", hold_back=6)
            for lag in (1, 3, 6)]
    assert all((fit.train_first, fit.train_last, fit.nobs) ==
               (start + 7, origin, origin.ordinal - start.ordinal - 6) for fit in fits)
    assert fits[0].r_squared <= fits[1].r_squared <= fits[2].r_squared
    assert fits[0].train_rmse >= fits[1].train_rmse >= fits[2].train_rmse
    last_growth = 100 * np.diff(np.log(prices.loc[:origin].to_numpy()[-2:]))[0]
    params = fits[0].coefficients
    level = prices[origin]
    for forecast in fits[0].path:
        last_growth = params["intercept"] + params["log_growth_pct_lag_1"] * last_growth
        level *= np.exp(last_growth / 100)
        assert forecast == pytest.approx(level)


@pytest.mark.parametrize("model", [
    "level_ar1", "level_ar3", "growth_ar1", "growth_ar3", "growth_ar6",
    "arima011", "naive", "seasonal_naive", "mean3_ar1", "raw_ar1_mean3",
    "naive_mean3", "seasonal_naive_mean3",
])
def test_fit_is_unchanged_by_all_outside_window_values(prices, model):
    start, origin = prices.index[7], prices.index[-15]
    contaminated = prices.copy()
    contaminated[(prices.index < start) | (prices.index > origin)] = np.nan
    clean = fit_forecast(prices, origin, start, model)
    alternate = fit_forecast(contaminated, origin, start, model)
    np.testing.assert_array_equal(alternate.path, clean.path)
    assert alternate.coefficients == clean.coefficients


def test_permit_models_compare_same_trailing_target(prices):
    start, origin = prices.index[0], prices.index[100]
    raw = fit_forecast(prices, origin, start, "level_ar1").path
    expected = pd.Series([*prices.loc[origin - 1:origin], *raw]).rolling(3).mean().dropna()
    converted = fit_forecast(prices, origin, start, "raw_ar1_mean3")
    np.testing.assert_allclose(converted.path, expected)

    direct = fit_forecast(prices, origin, start, "mean3_ar1")
    expected_direct = fit_forecast(prices.rolling(3).mean(), origin, start + 2, "level_ar1")
    np.testing.assert_allclose(direct.path, expected_direct.path)

    last_mean = fit_forecast(prices, str(origin), str(start), "naive_mean3")
    np.testing.assert_allclose(last_mean.path, prices.loc[origin - 2:origin].mean())

    seasonal = fit_forecast(prices, origin, start, "seasonal_naive_mean3", steps=14)
    means = prices.rolling(3).mean()
    for h, forecast in enumerate(seasonal.path):
        assert forecast == pytest.approx(means[origin - 11 + (h % 12)])


def test_naive_benchmarks_use_the_correct_calendar_month(prices):
    start, origin = prices.index[5], prices.index[90]
    naive = fit_forecast(prices, origin, start, "naive")
    assert naive.nobs == 0 and naive.train_rmse is None
    np.testing.assert_allclose(naive.path, prices[origin])
    s_naive = fit_forecast(prices, origin, start, "seasonal_naive", steps=24)
    np.testing.assert_array_equal(s_naive.path, np.tile(prices.loc[origin - 11:origin], 2))


def test_arima011_conditional_innovations_and_forecast():
    months = pd.period_range("2000-01", periods=301, freq="M")
    shocks = np.random.default_rng(91).normal(0, 0.3, len(months) - 1)
    changes = shocks + 0.6 * np.r_[0, shocks[:-1]]
    levels = pd.Series(100 * np.exp(np.r_[0, np.cumsum(changes)] / 100), index=months)
    fit = fit_forecast(levels, months[-1], months[0], "arima011")
    theta = fit.coefficients["innovation_lag_1"]
    assert theta == pytest.approx(0.6, abs=0.15)
    innovations = np.zeros(len(months))
    for i, change in enumerate(changes, 1):
        innovations[i] = change - theta * innovations[i - 1]
    assert fit.train_rmse == pytest.approx(np.sqrt(np.mean(innovations[1:] ** 2)))
    np.testing.assert_allclose(fit.path, levels.iloc[-1] * np.exp(theta * innovations[-1] / 100))
    assert fit.nobs == len(changes)


def test_missing_duplicate_or_nonpositive_history_stops_fit(prices):
    start, origin = prices.index[0], prices.index[50]
    for broken in [prices.drop(prices.index[4]), pd.concat([prices, prices.iloc[[5]]])]:
        with pytest.raises(ValueError, match="every training month"):
            fit_forecast(broken, origin, start, "growth_ar3")
    prices.iloc[6] = np.nan
    with pytest.raises(ValueError, match="nonfinite training"):
        fit_forecast(prices, origin, start, "growth_ar3")
    prices.iloc[6] = 0
    with pytest.raises(ValueError, match="positive observed"):
        fit_forecast(prices, origin, start, "growth_ar3")
    with pytest.raises(ValueError, match="Insufficient training"):
        fit_forecast(prices, start + 3, start, "level_ar3")
