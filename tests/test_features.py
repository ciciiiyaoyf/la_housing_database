import pandas as pd
import pytest

from src.features.derived import (
    homebuying_cost_scenarios,
    housing_turnover,
    implied_gross_rent_yield,
    market_tightness_index,
    mortgage_payment,
    price_to_rent_ratio,
)


def test_price_to_rent_formula():
    result = price_to_rent_ratio(pd.Series([600_000.0]), pd.Series([2_500.0]))
    assert result.iloc[0] == 20.0


def test_zillow_ratio_and_gross_yield_require_positive_components():
    zhvi = pd.Series([600_000.0, 0.0, -1.0, 600_000.0])
    zori = pd.Series([2_500.0, 2_500.0, 2_500.0, 0.0])
    ratio = price_to_rent_ratio(zhvi, zori)
    gross_yield = implied_gross_rent_yield(zhvi, zori)
    assert ratio.iloc[0] == pytest.approx(20.0)
    assert gross_yield.iloc[0] == pytest.approx(0.05)
    assert ratio.iloc[0] * gross_yield.iloc[0] == pytest.approx(1.0)
    assert ratio.iloc[1:].isna().all()
    assert gross_yield.iloc[1:].isna().all()


def test_homebuying_cost_scenarios_keep_full_cross_product():
    source = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-12-31")],
            "geo_id": ["COUNTY_06037"],
            "geography_level": ["county"],
            "county_fips": ["06037"],
            "postal_zip": [pd.NA],
            "median_sale_price": [500_000.0],
            "price_source_variable_id": ["median_sale_price"],
            "price_native_frequency": ["monthly"],
            "price_rolling_window_months": [pd.NA],
        }
    )
    config = {
        "down_payment_scenarios": [0.035, 0.10, 0.20],
        "closing_cost_scenarios": [0.02, 0.03, 0.05],
        "closing_cost_loan_scenarios": [0.03, 0.06],
    }
    result = homebuying_cost_scenarios(source, config)
    assert len(result) == 15
    assert result["scenario_name"].nunique() == 15
    assert set(result["closing_cost_basis"]) == {"sale_price", "loan_amount"}
    assert not result["is_observed"].any()
    assert result["is_constructed"].all()
    assert not result["prepaid_escrow_included"].any()

    loan = result.loc[
        result["down_payment_share"].eq(0.20)
        & result["closing_cost_variable_id"].eq("closing_cost_loan_3pct")
    ].iloc[0]
    assert loan["estimated_mortgage_amount_scenario"] == pytest.approx(400_000.0)
    assert loan["closing_cost_amount"] == pytest.approx(12_000.0)
    assert loan["total_cash_to_close_scenario"] == pytest.approx(112_000.0)


def test_mortgage_payment_known_case_and_down_payment_order():
    price = pd.Series([500_000.0])
    rate = pd.Series([6.0])
    ten = mortgage_payment(price, rate, 0.10).iloc[0]
    twenty = mortgage_payment(price, rate, 0.20).iloc[0]
    assert ten == pytest.approx(2697.98, abs=0.02)
    assert twenty < ten


def test_turnover_requires_stock_denominator():
    value = housing_turnover(pd.Series([10.0]), pd.Series([1000.0]))
    assert value.iloc[0] == 0.01


def test_market_tightness_respects_component_minimum():
    frame = pd.DataFrame(
        {
            "months_of_supply": [1.0, 2.0, 3.0],
            "days_on_market": [10.0, 20.0, 30.0],
            "inventory_growth_yoy": [-0.1, 0.0, 0.1],
            "price_drop_share": [0.1, None, 0.3],
        }
    )
    index, coverage = market_tightness_index(frame, 3)
    assert coverage.tolist() == [4, 3, 4]
    assert index.notna().all()
    assert index.iloc[0] > index.iloc[2]
