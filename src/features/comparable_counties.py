"""Pure feature construction for the eight-county comparison panel.

The functions in this module do not read files or mutate configuration.  Reference
period z-score parameters are returned as JSON-serializable dictionaries so a
production pipeline can persist them and apply the same frozen parameters later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


MARKET_TIGHTNESS_COMPONENTS: tuple[str, ...] = (
    "months_of_supply",
    "days_on_market",
    "price_drop_share",
)

DEFAULT_LA_COUNTY_FIPS = "06037"
DEFAULT_PEER_COUNTY_FIPS: tuple[str, ...] = (
    "06001",  # Alameda
    "06059",  # Orange
    "06065",  # Riverside
    "06067",  # Sacramento
    "06071",  # San Bernardino
    "06073",  # San Diego
    "06085",  # Santa Clara
)


def _column_names(columns: Sequence[str]) -> tuple[str, ...]:
    result = tuple(str(column) for column in columns)
    if not result:
        raise ValueError("At least one column is required")
    if len(result) != len(set(result)):
        raise ValueError("Column names must be unique")
    return result


def exact_calendar_log_momentum(
    frame: pd.DataFrame,
    value_column: str,
    *,
    lag_months: int,
    scale: float,
    date_column: str = "date",
    group_columns: Sequence[str] = ("county_fips",),
) -> pd.Series:
    """Return a scaled log change matched to the exact prior calendar month.

    A value is null unless both the current and exact lag-month observations are
    positive.  Missing calendar months are never bridged with a row-based shift.
    """

    if lag_months <= 0:
        raise ValueError("lag_months must be positive")
    groups = tuple(str(column) for column in group_columns)
    required = {date_column, value_column, *groups}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    work = frame[[*groups, date_column, value_column]].copy()
    work["_row_position"] = np.arange(len(work), dtype=np.int64)
    parsed_dates = pd.to_datetime(work[date_column], errors="coerce")
    work["_calendar_month"] = parsed_dates.dt.to_period("M")
    valid_dates = work["_calendar_month"].notna()
    key_columns = [*groups, "_calendar_month"]
    duplicates = work.loc[valid_dates].duplicated(key_columns, keep=False)
    if duplicates.any():
        raise ValueError(
            "Exact-calendar momentum requires one observation per "
            f"{', '.join(key_columns)}"
        )

    lag_lookup = work.loc[
        valid_dates, [*groups, "_calendar_month", value_column]
    ].copy()
    lag_lookup["_calendar_month"] = (
        lag_lookup["_calendar_month"] + int(lag_months)
    )
    lag_lookup = lag_lookup.rename(columns={value_column: "_lagged_value"})
    merged = work.merge(
        lag_lookup,
        on=key_columns,
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values("_row_position")

    current = pd.to_numeric(merged[value_column], errors="coerce")
    lagged = pd.to_numeric(merged["_lagged_value"], errors="coerce")
    valid = current.gt(0) & lagged.gt(0)
    values = pd.Series(np.nan, index=merged.index, dtype="float64")
    values.loc[valid] = float(scale) * (
        np.log(current.loc[valid]) - np.log(lagged.loc[valid])
    )
    return pd.Series(values.to_numpy(), index=frame.index, dtype="float64")


def home_price_momentum(
    frame: pd.DataFrame,
    *,
    value_column: str = "zhvi",
    date_column: str = "date",
    group_columns: Sequence[str] = ("county_fips",),
) -> pd.DataFrame:
    """Construct the required YoY and annualized three-month ZHVI log changes."""

    return pd.DataFrame(
        {
            "home_price_momentum_yoy": exact_calendar_log_momentum(
                frame,
                value_column,
                lag_months=12,
                scale=100.0,
                date_column=date_column,
                group_columns=group_columns,
            ),
            "home_price_momentum_3m_annualized": exact_calendar_log_momentum(
                frame,
                value_column,
                lag_months=3,
                scale=400.0,
                date_column=date_column,
                group_columns=group_columns,
            ),
        },
        index=frame.index,
    )


def laus_employment_momentum_yoy(
    frame: pd.DataFrame,
    *,
    value_column: str = "employment",
    date_column: str = "date",
    group_columns: Sequence[str] = ("county_fips",),
) -> pd.Series:
    """Return ``100 * [ln(Employment_t) - ln(Employment_t-12)]``."""

    return exact_calendar_log_momentum(
        frame,
        value_column,
        lag_months=12,
        scale=100.0,
        date_column=date_column,
        group_columns=group_columns,
    )


def positive_price_to_rent_ratio(
    zhvi: pd.Series, zori: pd.Series
) -> pd.Series:
    """Return ZHVI divided by twelve times ZORI when both inputs are positive."""

    home_value = pd.to_numeric(zhvi, errors="coerce")
    monthly_rent = pd.to_numeric(zori, errors="coerce")
    valid = home_value.gt(0) & monthly_rent.gt(0)
    return home_value.div(12.0 * monthly_rent).where(valid)


def fit_pooled_population_zscore_parameters(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    reference_start: str | pd.Timestamp,
    reference_end: str | pd.Timestamp,
    date_column: str = "date",
) -> dict[str, Any]:
    """Fit pooled population-SD z-score parameters in a fixed date window."""

    selected = _column_names(columns)
    required = {date_column, *selected}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise KeyError(f"Missing required columns: {missing}")
    start = pd.Timestamp(reference_start)
    end = pd.Timestamp(reference_end)
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError("reference_start and reference_end must define a valid window")

    dates = pd.to_datetime(frame[date_column], errors="coerce")
    reference = dates.between(start, end, inclusive="both")
    statistics: dict[str, dict[str, float | int]] = {}
    for column in selected:
        values = pd.to_numeric(frame.loc[reference, column], errors="coerce")
        values = values.replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            raise ValueError(f"No reference-period observations for {column}")
        population_sd = float(values.std(ddof=0))
        if not np.isfinite(population_sd) or population_sd <= 0:
            raise ValueError(
                f"Reference-period population SD must be positive for {column}"
            )
        statistics[column] = {
            "mean": float(values.mean()),
            "population_sd": population_sd,
            "observation_count": int(values.size),
        }
    return {
        "method": "fixed_reference_pooled_population_zscore_v1",
        "scope": "pooled",
        "ddof": 0,
        "reference_start": start.isoformat(),
        "reference_end": end.isoformat(),
        "statistics": statistics,
    }


def apply_pooled_zscores(
    frame: pd.DataFrame,
    parameters: Mapping[str, Any],
    *,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Apply a previously fitted parameter mapping without refitting it."""

    if parameters.get("scope") != "pooled" or parameters.get("ddof") != 0:
        raise ValueError("Parameters must describe pooled population-SD z-scores")
    statistics = parameters.get("statistics")
    if not isinstance(statistics, Mapping):
        raise ValueError("Parameters must contain a statistics mapping")
    selected = (
        _column_names(columns)
        if columns is not None
        else _column_names(tuple(str(column) for column in statistics))
    )
    missing = sorted(set(selected).difference(frame.columns))
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    result = pd.DataFrame(index=frame.index)
    for column in selected:
        statistic = statistics.get(column)
        if not isinstance(statistic, Mapping):
            raise ValueError(f"Missing frozen statistics for {column}")
        mean = float(statistic["mean"])
        population_sd = float(statistic["population_sd"])
        if not np.isfinite(mean) or not np.isfinite(population_sd) or population_sd <= 0:
            raise ValueError(f"Invalid frozen statistics for {column}")
        values = pd.to_numeric(frame[column], errors="coerce")
        result[column] = (values - mean) / population_sd
    return result


def fit_market_tightness_parameters(
    frame: pd.DataFrame,
    *,
    reference_start: str | pd.Timestamp,
    reference_end: str | pd.Timestamp,
    date_column: str = "date",
) -> dict[str, Any]:
    """Fit frozen pooled statistics for the exact three tightness components."""

    parameters = fit_pooled_population_zscore_parameters(
        frame,
        MARKET_TIGHTNESS_COMPONENTS,
        reference_start=reference_start,
        reference_end=reference_end,
        date_column=date_column,
    )
    return {
        **parameters,
        "feature": "market_tightness_index",
        "components": list(MARKET_TIGHTNESS_COMPONENTS),
        "orientation": "negative",
    }


def market_tightness_index(
    frame: pd.DataFrame, parameters: Mapping[str, Any]
) -> tuple[pd.Series, pd.Series]:
    """Apply the exact three-component seller-favored market-tightness formula."""

    configured = tuple(parameters.get("components", MARKET_TIGHTNESS_COMPONENTS))
    if configured != MARKET_TIGHTNESS_COMPONENTS:
        raise ValueError(
            "Market tightness parameters must use months of supply, days on market, "
            "and price-drop share in that order"
        )
    zscores = apply_pooled_zscores(
        frame, parameters, columns=MARKET_TIGHTNESS_COMPONENTS
    )
    coverage = zscores.notna().sum(axis=1).astype("Int64")
    index = (-zscores.sum(axis=1, min_count=3) / 3.0).where(coverage.eq(3))
    return index, coverage


def fit_county_heat_parameters(
    frame: pd.DataFrame,
    *,
    reference_start: str | pd.Timestamp,
    reference_end: str | pd.Timestamp,
    date_column: str = "date",
    price_momentum_column: str = "home_price_momentum_3m_annualized",
    tightness_column: str = "market_tightness_index",
) -> dict[str, Any]:
    """Fit frozen pooled statistics for the two county-heat components."""

    components = (price_momentum_column, tightness_column)
    parameters = fit_pooled_population_zscore_parameters(
        frame,
        components,
        reference_start=reference_start,
        reference_end=reference_end,
        date_column=date_column,
    )
    return {
        **parameters,
        "feature": "county_housing_market_heat_index",
        "components": list(components),
    }


def county_housing_market_heat_index(
    frame: pd.DataFrame, parameters: Mapping[str, Any]
) -> pd.Series:
    """Return the equal mean of standardized price momentum and tightness."""

    components = tuple(str(column) for column in parameters.get("components", ()))
    if len(components) != 2:
        raise ValueError("County heat parameters must contain exactly two components")
    zscores = apply_pooled_zscores(frame, parameters, columns=components)
    return zscores.sum(axis=1, min_count=2) / 2.0


def la_minus_seven_peer_mean(
    frame: pd.DataFrame,
    value_column: str,
    *,
    date_column: str = "date",
    county_column: str = "county_fips",
    la_county_fips: str = DEFAULT_LA_COUNTY_FIPS,
    peer_county_fips: Sequence[str] = DEFAULT_PEER_COUNTY_FIPS,
    output_column: str | None = None,
) -> pd.DataFrame:
    """Return LA minus the equal mean of exactly seven complete peer values."""

    peers = tuple(str(value) for value in peer_county_fips)
    if len(peers) != 7 or len(set(peers)) != 7:
        raise ValueError("Exactly seven unique peer counties are required")
    if str(la_county_fips) in peers:
        raise ValueError("Los Angeles County cannot also be a peer county")
    required = {date_column, county_column, value_column}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    selected_counties = {str(la_county_fips), *peers}
    work = frame.loc[
        frame[county_column].astype("string").isin(selected_counties),
        [date_column, county_column, value_column],
    ].copy()
    work[date_column] = pd.to_datetime(work[date_column], errors="coerce")
    work[county_column] = work[county_column].astype("string")
    valid_dates = work[date_column].notna()
    duplicate_keys = work.loc[valid_dates].duplicated(
        [date_column, county_column], keep=False
    )
    if duplicate_keys.any():
        raise ValueError("LA-relative measures require one row per county and date")

    dates = pd.DataFrame(
        {date_column: sorted(work.loc[valid_dates, date_column].unique())}
    )
    if dates.empty:
        name = output_column or f"{value_column}_la_relative_to_seven_peers"
        dates[name] = pd.Series(dtype="float64")
        return dates
    wide = work.loc[valid_dates].pivot(
        index=date_column, columns=county_column, values=value_column
    )
    required_counties = [str(la_county_fips), *peers]
    numeric = wide.reindex(columns=required_counties).apply(
        pd.to_numeric, errors="coerce"
    )
    complete = numeric.notna().all(axis=1)
    relative = numeric[str(la_county_fips)] - numeric[list(peers)].mean(axis=1)
    name = output_column or f"{value_column}_la_relative_to_seven_peers"
    result = relative.where(complete).rename(name).reset_index()
    return dates.merge(result, on=date_column, how="left", validate="one_to_one")


def la_relative_market_performance(
    frame: pd.DataFrame,
    value_columns: Sequence[str],
    *,
    date_column: str = "date",
    county_column: str = "county_fips",
    la_county_fips: str = DEFAULT_LA_COUNTY_FIPS,
    peer_county_fips: Sequence[str] = DEFAULT_PEER_COUNTY_FIPS,
) -> pd.DataFrame:
    """Construct LA-minus-seven-peer measures for several comparable variables."""

    selected = _column_names(value_columns)
    result: pd.DataFrame | None = None
    for column in selected:
        part = la_minus_seven_peer_mean(
            frame,
            column,
            date_column=date_column,
            county_column=county_column,
            la_county_fips=la_county_fips,
            peer_county_fips=peer_county_fips,
        )
        result = (
            part
            if result is None
            else result.merge(part, on=date_column, how="outer", validate="one_to_one")
        )
    assert result is not None
    return result.sort_values(date_column).reset_index(drop=True)
