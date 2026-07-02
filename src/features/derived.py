"""Testable feature formulas used by analytical panels."""

from __future__ import annotations

import numpy as np
import pandas as pd


def growth(series: pd.Series, periods: int) -> pd.Series:
    """Fractional growth relative to `periods` observations earlier."""
    return series.pct_change(periods=periods, fill_method=None)


def price_to_rent_ratio(zhvi: pd.Series, zori: pd.Series) -> pd.Series:
    """Home value divided by annualized rent."""
    return zhvi / (12.0 * zori.replace(0, np.nan))


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
    principal = purchase_price * (1.0 - down_payment_share)
    monthly_rate = annual_rate_percent / 100.0 / 12.0
    periods = loan_term_years * 12
    denominator = 1.0 - (1.0 + monthly_rate).pow(-periods)
    payment = principal * monthly_rate / denominator
    zero_rate = monthly_rate.eq(0)
    return payment.mask(zero_rate, principal / periods)


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
        result["price_to_rent_ratio"] = price_to_rent_ratio(
            result["zhvi"], result["zori"]
        )
    if {"active_inventory", "homes_sold"}.issubset(result):
        result["inventory_to_sales_ratio"] = inventory_to_sales_ratio(
            result["active_inventory"], result["homes_sold"]
        )
        result["sales_to_inventory_ratio"] = (
            result["homes_sold"]
            / result["active_inventory"].replace(0, np.nan)
        )
    if {
        "median_sale_price",
        "mortgage_rate_30y_monthly_average",
    }.issubset(result):
        for down_payment in mortgage_config["down_payment_scenarios"]:
            label = f"{int(round(down_payment * 100))}pct"
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
