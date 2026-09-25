"""Testable feature formulas used by analytical panels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


APARTMENT_COMPLETION_LAG_WEIGHTS: dict[str, dict[int, float]] = {
    "central": {
        **{lag: 0.20 / 6.0 for lag in range(12, 18)},
        **{lag: 0.55 / 6.0 for lag in range(18, 24)},
        **{lag: 0.25 / 7.0 for lag in range(24, 31)},
    },
    "slow": {
        **{lag: 0.20 / 6.0 for lag in range(18, 24)},
        **{lag: 0.50 / 6.0 for lag in range(24, 30)},
        **{lag: 0.30 / 7.0 for lag in range(30, 37)},
    },
}

APARTMENT_COMPLETION_PROXY_COLUMNS = {
    "central": "new_apartments_completed_proxy_central",
    "slow": "new_apartments_completed_proxy_slow",
}

MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS = {
    "central": "multifamily_under_construction_proxy_central",
    "slow": "multifamily_under_construction_proxy_slow",
}


def _weighted_lag_sum(
    permits: pd.Series, lag_weights: Mapping[int, float]
) -> pd.Series:
    """Return a distributed lag only where every weighted input is present."""
    terms = [permits.shift(lag) * weight for lag, weight in lag_weights.items()]
    return pd.concat(terms, axis=1).sum(axis=1, min_count=len(terms))


def _cohort_survival_stock(
    permits: pd.Series, completion_weights: Mapping[int, float]
) -> pd.Series:
    """Stock remaining after the current month's modeled completions.

    A permit enters the pipeline in full in its authorization month. Its
    surviving share at each subsequent age is one minus cumulative completion
    weight through that age. This is algebraically equivalent to
    ``S_t = S_{t-1} + P_t - C_t`` but can initialize (and reinitialize after a
    source gap) from sufficient permit cohorts instead of assuming an opening
    stock of zero.
    """
    maximum_lag = max(completion_weights)
    cumulative_completion = 0.0
    terms: list[pd.Series] = []
    for age in range(maximum_lag):
        cumulative_completion += completion_weights.get(age, 0.0)
        surviving_share = max(0.0, 1.0 - cumulative_completion)
        if surviving_share > 0.0:
            terms.append(permits.shift(age) * surviving_share)
    return pd.concat(terms, axis=1).sum(axis=1, min_count=len(terms))


def _normalise_group_columns(group_columns: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(group_columns, str):
        return (group_columns,)
    return tuple(group_columns)


def _construction_proxy_inputs(
    frame: pd.DataFrame,
    *,
    date_column: str,
    permit_column: str,
    group_columns: str | Sequence[str],
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Validate and normalize keys without altering the caller's frame."""
    groups = _normalise_group_columns(group_columns)
    required = {date_column, permit_column, *groups}
    missing_columns = required - set(frame.columns)
    if missing_columns:
        raise ValueError(
            "Construction proxy inputs are missing columns: "
            f"{sorted(missing_columns)}"
        )
    if groups and frame.loc[:, list(groups)].isna().any(axis=None):
        raise ValueError("Construction proxy group keys must not be missing")

    work = frame.loc[:, [*groups, date_column, permit_column]].copy()
    dates = pd.to_datetime(work[date_column], errors="coerce")
    if dates.isna().any():
        raise ValueError("Construction proxy dates must be valid and nonmissing")
    work["_month"] = dates.dt.to_period("M")

    permits = pd.to_numeric(work[permit_column], errors="coerce")
    invalid_numeric = work[permit_column].notna() & permits.isna()
    if invalid_numeric.any():
        raise ValueError("Construction proxy permits must be numeric when present")
    finite = permits.isna() | np.isfinite(permits)
    if not finite.all():
        raise ValueError("Construction proxy permits must be finite when present")
    if permits.lt(0).any():
        raise ValueError("Construction proxy permits must be nonnegative")
    work["_permits"] = permits.astype(float)
    work["_row_position"] = np.arange(len(work))

    duplicate_keys = [*groups, "_month"]
    if work.duplicated(duplicate_keys).any():
        raise ValueError("Construction proxy inputs require one row per group-month")
    return work, groups


def _apply_monthly_proxy_by_group(
    frame: pd.DataFrame,
    calculator,
    *,
    date_column: str,
    permit_column: str,
    group_columns: str | Sequence[str],
) -> dict[str, np.ndarray]:
    work, groups = _construction_proxy_inputs(
        frame,
        date_column=date_column,
        permit_column=permit_column,
        group_columns=group_columns,
    )
    outputs = {
        scenario: np.full(len(frame), np.nan, dtype=float)
        for scenario in APARTMENT_COMPLETION_LAG_WEIGHTS
    }
    if work.empty:
        return outputs
    grouped = (
        work.groupby(groups[0] if len(groups) == 1 else list(groups), sort=False, dropna=False)
        if groups
        else [(None, work)]
    )
    for _, group in grouped:
        ordered = group.sort_values("_month")
        calendar = pd.period_range(
            ordered["_month"].iloc[0], ordered["_month"].iloc[-1], freq="M"
        )
        permits = ordered.set_index("_month")["_permits"].reindex(calendar)
        for scenario, weights in APARTMENT_COMPLETION_LAG_WEIGHTS.items():
            values = calculator(permits, weights)
            outputs[scenario][ordered["_row_position"].to_numpy()] = (
                values.reindex(ordered["_month"]).to_numpy(dtype=float)
            )
    return outputs


def apartment_completion_proxies(
    frame: pd.DataFrame,
    *,
    date_column: str = "date",
    permit_column: str = "permits_5_plus_units",
    group_columns: str | Sequence[str] = ("county_fips",),
) -> pd.DataFrame:
    """Add workbook-approved Central and Slow apartment-completion proxies.

    Calculations use calendar-month lags separately within each group. A value
    remains null until every permit observation carrying positive scenario
    weight is available; missing months and provider-null values are never
    treated as zero.
    """
    result = frame.copy()
    outputs = _apply_monthly_proxy_by_group(
        frame,
        _weighted_lag_sum,
        date_column=date_column,
        permit_column=permit_column,
        group_columns=group_columns,
    )
    for scenario, column in APARTMENT_COMPLETION_PROXY_COLUMNS.items():
        result[column] = outputs[scenario]
    return result


def multifamily_under_construction_proxies(
    frame: pd.DataFrame,
    *,
    date_column: str = "date",
    permit_column: str = "permits_5_plus_units",
    group_columns: str | Sequence[str] = ("county_fips",),
) -> pd.DataFrame:
    """Add Central and Slow multifamily construction-pipeline stock proxies.

    The cohort-survival calculation is the closed-form version of the required
    stock-flow recurrence. Central needs the current permit cohort plus 29
    preceding monthly cohorts; Slow needs the current plus 35. Consequently,
    early observations and periods affected by a true source gap remain null,
    and each scenario resumes only after all active cohorts are observed.
    """
    result = frame.copy()
    outputs = _apply_monthly_proxy_by_group(
        frame,
        _cohort_survival_stock,
        date_column=date_column,
        permit_column=permit_column,
        group_columns=group_columns,
    )
    for scenario, column in MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.items():
        result[column] = outputs[scenario]
    return result


def growth(series: pd.Series, periods: int) -> pd.Series:
    """Fractional growth relative to `periods` observations earlier."""
    return series.pct_change(periods=periods, fill_method=None)


def price_to_rent_ratio(zhvi: pd.Series, zori: pd.Series) -> pd.Series:
    """Positive home value divided by positive annualized asking rent."""
    valid = zhvi.gt(0) & zori.gt(0)
    return (zhvi / (12.0 * zori)).where(valid)


def implied_gross_rent_yield(
    zhvi: pd.Series, zori: pd.Series
) -> pd.Series:
    """Positive annualized asking rent divided by positive home value."""
    valid = zhvi.gt(0) & zori.gt(0)
    return (12.0 * zori / zhvi).where(valid)


def estimated_mortgage_amount(
    purchase_price: pd.Series, down_payment_share: float
) -> pd.Series:
    """Scenario loan principal before amortization or financed loan costs."""
    return purchase_price * (1.0 - down_payment_share)


def inventory_to_sales_ratio(
    inventory: pd.Series, homes_sold: pd.Series
) -> pd.Series:
    return inventory / homes_sold.replace(0, np.nan)


def housing_turnover(
    homes_sold: pd.Series, housing_stock: pd.Series
) -> pd.Series:
    """Monthly closed sales divided by housing stock; requires a stock input."""
    return homes_sold / housing_stock.replace(0, np.nan)


def mortgage_payment(
    purchase_price: pd.Series,
    annual_rate_percent: pd.Series,
    down_payment_share: float,
    loan_term_years: int = 30,
) -> pd.Series:
    """Monthly principal and interest; excludes all non-loan housing costs."""
    principal = estimated_mortgage_amount(purchase_price, down_payment_share)
    monthly_rate = annual_rate_percent / 100.0 / 12.0
    periods = loan_term_years * 12
    denominator = 1.0 - (1.0 + monthly_rate).pow(-periods)
    payment = principal * monthly_rate / denominator
    zero_rate = monthly_rate.eq(0)
    return payment.mask(zero_rate, principal / periods)


def scenario_label(share: float) -> str:
    text = f"{share * 100:g}".replace(".", "_")
    return f"{text}pct"


def homebuying_cost_scenarios(
    observations: pd.DataFrame,
    mortgage_config: dict,
) -> pd.DataFrame:
    """Build a long, explicitly keyed homebuying-cost scenario layer.

    Each source price observation is crossed with every configured down-payment
    scenario and both the sale-price and loan-amount closing-cost assumptions.
    This avoids silently selecting one down payment for the loan-based 3%/6%
    sensitivities. Prepaid escrow items are excluded unless a future method
    adds them as a separate component.
    """

    columns = [
        "date",
        "geo_id",
        "geography_level",
        "county_fips",
        "postal_zip",
        "median_sale_price",
        "price_source_variable_id",
        "price_native_frequency",
        "price_rolling_window_months",
        "down_payment_variable_id",
        "down_payment_share",
        "down_payment_amount",
        "estimated_mortgage_amount_scenario",
        "closing_cost_variable_id",
        "closing_cost_basis",
        "closing_cost_share",
        "closing_cost_amount",
        "total_cash_to_close_scenario",
        "scenario_name",
        "is_observed",
        "is_constructed",
        "is_proxy",
        "is_interpolated",
        "prepaid_escrow_included",
    ]
    if observations.empty:
        return pd.DataFrame(columns=columns)

    required = {
        "date",
        "geo_id",
        "geography_level",
        "median_sale_price",
        "price_source_variable_id",
        "price_native_frequency",
    }
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(
            "Homebuying scenario observations are missing columns: "
            f"{sorted(missing)}"
        )

    source = observations.loc[
        pd.to_numeric(observations["median_sale_price"], errors="coerce").gt(0)
    ].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)
    for optional in (
        "county_fips",
        "postal_zip",
        "price_rolling_window_months",
    ):
        if optional not in source:
            source[optional] = pd.NA

    down_payments = [
        float(value)
        for value in mortgage_config.get("down_payment_scenarios", [])
    ]
    sale_price_costs = [
        float(value)
        for value in mortgage_config.get("closing_cost_scenarios", [])
    ]
    loan_costs = [
        float(value)
        for value in mortgage_config.get("closing_cost_loan_scenarios", [])
    ]
    rows: list[pd.DataFrame] = []
    for down_share in down_payments:
        down_label = scenario_label(down_share)
        base = source.copy()
        base["down_payment_variable_id"] = f"down_payment_{down_label}"
        base["down_payment_share"] = down_share
        base["down_payment_amount"] = base["median_sale_price"] * down_share
        base["estimated_mortgage_amount_scenario"] = estimated_mortgage_amount(
            base["median_sale_price"], down_share
        )
        for basis, closing_shares in (
            ("sale_price", sale_price_costs),
            ("loan_amount", loan_costs),
        ):
            for closing_share in closing_shares:
                closing_label = scenario_label(closing_share)
                part = base.copy()
                if basis == "sale_price":
                    part["closing_cost_variable_id"] = (
                        f"closing_cost_saleprice_{closing_label}"
                    )
                    cost_base = part["median_sale_price"]
                else:
                    part["closing_cost_variable_id"] = (
                        f"closing_cost_loan_{closing_label}"
                    )
                    cost_base = part["estimated_mortgage_amount_scenario"]
                part["closing_cost_basis"] = basis
                part["closing_cost_share"] = closing_share
                part["closing_cost_amount"] = cost_base * closing_share
                part["total_cash_to_close_scenario"] = (
                    part["down_payment_amount"] + part["closing_cost_amount"]
                )
                part["scenario_name"] = (
                    f"{down_label}_down__{basis}_{closing_label}_closing"
                )
                rows.append(part)

    result = pd.concat(rows, ignore_index=True) if rows else source.iloc[0:0].copy()
    result["is_observed"] = False
    result["is_constructed"] = True
    result["is_proxy"] = False
    result["is_interpolated"] = False
    result["prepaid_escrow_included"] = False
    return result.reindex(columns=columns).sort_values(
        ["geography_level", "geo_id", "date", "scenario_name"]
    ).reset_index(drop=True)


def _oriented_zscore(series: pd.Series, direction: float) -> pd.Series:
    standard_deviation = series.std(ddof=0)
    if pd.isna(standard_deviation) or standard_deviation == 0:
        return pd.Series(np.nan, index=series.index)
    return direction * (series - series.mean()) / standard_deviation


def market_tightness_index(
    frame: pd.DataFrame, minimum_components: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Mean oriented z-score; higher means tighter and coverage is explicit."""
    components: list[pd.Series] = []
    for column in (
        "months_of_supply",
        "days_on_market",
        "inventory_growth_yoy",
        "price_drop_share",
    ):
        if column in frame:
            components.append(_oriented_zscore(frame[column], -1.0).rename(column))
    if not components:
        empty = pd.Series(np.nan, index=frame.index)
        return empty, pd.Series(0, index=frame.index, dtype="Int64")
    matrix = pd.concat(components, axis=1)
    coverage = matrix.notna().sum(axis=1).astype("Int64")
    index = matrix.mean(axis=1, skipna=True).where(coverage >= minimum_components)
    return index, coverage


def add_panel_features(
    panel: pd.DataFrame, mortgage_config: dict, minimum_components: int = 3
) -> pd.DataFrame:
    result = panel.sort_values("date").copy()
    for source, prefix in (
        ("zhvi", "zhvi"),
        ("zori", "zori"),
        ("active_inventory", "inventory"),
    ):
        if source in result:
            result[f"{prefix}_growth_mom"] = growth(result[source], 1)
            result[f"{prefix}_growth_yoy"] = growth(result[source], 12)
    if {"zhvi", "zori"}.issubset(result):
        result["price_to_rent_ratio_zillow"] = price_to_rent_ratio(
            result["zhvi"], result["zori"]
        )
        result["price_to_rent_ratio"] = result["price_to_rent_ratio_zillow"]
        result["implied_gross_rent_yield"] = implied_gross_rent_yield(
            result["zhvi"], result["zori"]
        )
    if {"active_inventory", "homes_sold"}.issubset(result):
        result["inventory_to_sales_ratio"] = inventory_to_sales_ratio(
            result["active_inventory"], result["homes_sold"]
        )
        result["sales_to_inventory_ratio"] = (
            result["homes_sold"] / result["active_inventory"].replace(0, np.nan)
        )
    if "median_sale_price" in result:
        for down_payment in mortgage_config.get("down_payment_scenarios", []):
            label = scenario_label(float(down_payment))
            result[f"down_payment_{label}"] = (
                result["median_sale_price"] * float(down_payment)
            )
        for closing_cost in mortgage_config.get("closing_cost_scenarios", []):
            label = scenario_label(float(closing_cost))
            canonical = f"closing_cost_saleprice_{label}"
            result[canonical] = (
                result["median_sale_price"] * float(closing_cost)
            )
            # Backward-compatible county-panel aliases retained for users of
            # the pre-methodology schema.
            result[f"closing_cost_{label}"] = result[canonical]
    if {
        "median_sale_price",
        "mortgage_rate_30y_monthly_average",
    }.issubset(result):
        for down_payment in mortgage_config["down_payment_scenarios"]:
            label = scenario_label(float(down_payment))
            payment_column = f"mortgage_payment_{label}_down"
            result[payment_column] = mortgage_payment(
                result["median_sale_price"],
                result["mortgage_rate_30y_monthly_average"],
                down_payment,
                int(mortgage_config["loan_term_years"]),
            )
            if "zori" in result:
                result[f"mortgage_payment_to_rent_{label}_down"] = (
                    result[payment_column] / result["zori"].replace(0, np.nan)
                )
    result["market_tightness_index"], result["market_tightness_components"] = (
        market_tightness_index(result, minimum_components)
    )
    return result
