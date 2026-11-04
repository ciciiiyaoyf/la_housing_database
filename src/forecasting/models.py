"""Univariate monthly models for the retrospective Week 1 backtests."""

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ForecastFit:
    path: np.ndarray  # Months 1 through steps, in reported target units.
    lhs: str
    rhs: tuple[str, ...]
    coefficients: dict[str, float]
    nobs: int
    train_first: pd.Period | None
    train_last: pd.Period | None
    train_rmse: float | None
    r_squared: float | None


def _training_sample(series, origin, start):
    origin, start = pd.Period(origin, freq="M"), pd.Period(start, freq="M")
    if start > origin:
        raise ValueError("Training start must precede or equal the forecast origin.")
    if isinstance(series.index, pd.PeriodIndex) and series.index.freqstr == "M":
        months = series.index
    elif isinstance(series.index, pd.DatetimeIndex):
        months = series.index.to_period("M")
    else:
        raise ValueError("Expected a monthly PeriodIndex or a DatetimeIndex.")
    # Slice before reading values or transforming them. Later vintages and
    # publication lags are a separate limitation of this retrospective exercise.
    selected = (months >= start) & (months <= origin)
    history = pd.Series(series.iloc[selected].to_numpy(), index=months[selected]).sort_index()
    expected = pd.period_range(start, origin, freq="M")
    if not history.index.equals(expected):
        raise ValueError(f"Expected one row for every training month from {start} to {origin}.")
    values = history.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"Missing or nonfinite training value between {start} and {origin}.")
    return expected, values


def _ols_ar(values, dates, lhs, lags, hold_back, steps):
    first = lags if hold_back is None else hold_back
    if not isinstance(first, (int, np.integer)) or first < lags:
        raise ValueError("hold_back must be a whole number at least as large as the lag order.")
    if len(values) - first <= lags + 1:
        raise ValueError("Insufficient training outcomes for this AR regression.")
    y = values[first:]
    terms = tuple(f"{lhs}_lag_{lag}" for lag in range(1, lags + 1))
    x = np.column_stack([np.ones(len(y))] + [
        values[first - lag : len(values) - lag] for lag in range(1, lags + 1)
    ])
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1]:
        raise ValueError(f"The {lhs} AR({lags}) training design is rank-deficient.")
    residuals = y - x @ beta
    sse = float(residuals @ residuals)
    centered = y - y.mean()
    sst = float(centered @ centered)
    past = list(values[-lags:])
    for _ in range(steps):
        past.append(float(beta[0] + beta[1:] @ past[-lags:][::-1]))
    return ForecastFit(
        np.array(past[lags:]), lhs, terms,
        dict(zip(("intercept", *terms), beta.tolist(), strict=True)),
        len(y), dates[first], dates[-1], np.sqrt(sse / len(y)),
        1 - sse / sst if sst > 0 else None,
    )


def _ma1_fit(changes):
    """Conditional sum of squares for x[t] = e[t] + theta * e[t-1], e[-1] = 0."""
    def innovations(theta):
        previous, sse = 0.0, 0.0
        for value in changes:
            previous = value - theta * previous
            sse += previous * previous
        return sse, previous

    # The invertible MA(1) has |theta| < 1. Search a grid first, then
    # refine each local minimum with golden section search.
    grid = np.linspace(-0.999, 0.999, 41)
    costs = [innovations(theta)[0] for theta in grid]
    candidates = [grid[0], grid[-1]]
    ratio = (np.sqrt(5) - 1) / 2
    for i, cost in enumerate(costs):
        lo_index, hi_index = max(0, i - 1), min(len(grid) - 1, i + 1)
        if cost <= costs[lo_index] and cost <= costs[hi_index]:
            left, right = grid[lo_index], grid[hi_index]
            low, high = right - ratio * (right - left), left + ratio * (right - left)
            lo_cost, hi_cost = innovations(low)[0], innovations(high)[0]
            while right - left > 1e-7:
                if lo_cost <= hi_cost:
                    right, high, hi_cost = high, low, lo_cost
                    low = right - ratio * (right - left)
                    lo_cost = innovations(low)[0]
                else:
                    left, low, lo_cost = low, high, hi_cost
                    high = left + ratio * (right - left)
                    hi_cost = innovations(high)[0]
            candidates.append((left + right) / 2)
    theta = float(min(candidates, key=lambda candidate: innovations(candidate)[0]))
    sse, last_error = innovations(theta)
    return theta, sse, last_error


def _seasonal_path(values, steps):
    if len(values) < 12:
        raise ValueError("Seasonal naive requires all 12 preceding target months.")
    return np.resize(values[-12:], steps)


def fit_forecast(
    series: pd.Series, origin, start, model: str, steps: int = 12, *, hold_back: int | None = None,
) -> ForecastFit:
    """Fit one county and outcome from raw levels in [start, origin].

    start and origin accept monthly Periods or YYYY-MM strings. No lags,
    differences or trailing averages use a month before start. The AR
    response series loses its first `hold_back` months to lags (the
    default is its own lag order). For growth models, set this to six
    across AR(1/3/6) for identical response months. The first raw level
    supplies the first difference, so the first fitted response is
    start + 7. Three-month means lose two raw months to initialization.
    All growth forecasts are recovered in level units. Models ending in
    'mean3' and mean3_ar1 forecast a trailing three-month arithmetic mean.
    """
    if not isinstance(steps, (int, np.integer)) or steps < 1:
        raise ValueError("steps must be a positive integer.")
    dates, levels = _training_sample(series, origin, start)
    if model in {"growth_ar1", "growth_ar3", "growth_ar6", "arima011"}:
        if (levels <= 0).any():
            raise ValueError("Log growth requires positive observed levels.")
        changes = 100 * np.diff(np.log(levels))
        if model == "arima011":
            if len(changes) < 3 or np.all(changes == 0):
                raise ValueError("ARIMA(0,1,1) needs at least three identifiable log changes.")
            theta, sse, last_error = _ma1_fit(changes)
            # Only the first future innovation correction is predictable.
            change_path = np.zeros(steps)
            change_path[0] = theta * last_error
            fit = ForecastFit(
                change_path, "log_growth_pct", ("innovation_lag_1",),
                {"innovation_lag_1": theta}, len(changes), dates[1], dates[-1],
                np.sqrt(sse / len(changes)), None,
            )
        else:
            fit = _ols_ar(changes, dates[1:], "log_growth_pct", int(model[-1]), hold_back, steps)
        path = levels[-1] * np.exp(np.cumsum(fit.path) / 100)
    elif model in {"level_ar1", "level_ar3", "raw_ar1_mean3", "mean3_ar1"}:
        if model == "mean3_ar1":
            if len(levels) < 3:
                raise ValueError("Trailing permits means need three raw months.")
            means = np.convolve(levels, np.ones(3) / 3, mode="valid")
            fit = _ols_ar(means, dates[2:], "mean3", 1, hold_back, steps)
        else:
            lags = 3 if model == "level_ar3" else 1
            fit = _ols_ar(levels, dates, "level", lags, hold_back, steps)
        path = fit.path
        if model == "raw_ar1_mean3":
            # Short horizons include the relevant one or two observed months.
            path = np.convolve(np.r_[levels[-2:], path], np.ones(3) / 3, mode="valid")
    elif model in {"naive", "seasonal_naive", "naive_mean3", "seasonal_naive_mean3"}:
        if model.endswith("mean3"):
            if len(levels) < 3:
                raise ValueError("A trailing mean requires three raw months.")
            values = np.convolve(levels, np.ones(3) / 3, mode="valid")
        else:
            values = levels
        seasonal = model.startswith("seasonal")
        path = _seasonal_path(values, steps) if seasonal else np.full(steps, values[-1])
        fit = ForecastFit(
            path, "mean3" if model.endswith("mean3") else "level",
            ("same_month_last_year",) if seasonal else ("last_observation",),
            {}, 0, None, None, None, None,
        )
    else:
        raise ValueError(f"Unknown Week 1 forecasting model: {model}")
    if not np.isfinite(path).all():
        raise ValueError(f"{model} produced nonfinite forecasts.")
    return replace(fit, path=path)
