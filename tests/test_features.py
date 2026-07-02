import pandas as pd
import pytest

from src.features.derived import (
    housing_turnover,
    market_tightness_index,
    mortgage_payment,
    price_to_rent_ratio,
)


def test_price_to_rent_formula():
    result = price_to_rent_ratio(pd.Series([600_000.0]), pd.Series([2_500.0]))
    assert result.iloc[0] == 20.0


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
