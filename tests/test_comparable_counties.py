from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from src.features.comparable_counties import (
    DEFAULT_PEER_COUNTY_FIPS,
    apply_pooled_zscores,
    county_housing_market_heat_index,
    exact_calendar_log_momentum,
    fit_county_heat_parameters,
    fit_market_tightness_parameters,
    fit_pooled_population_zscore_parameters,
    home_price_momentum,
    la_minus_seven_peer_mean,
    la_relative_market_performance,
    laus_employment_momentum_yoy,
    market_tightness_index,
    positive_price_to_rent_ratio,
)


def test_exact_calendar_home_price_momentum_does_not_bridge_missing_months():
    frame = pd.DataFrame(
        {
            "county_fips": ["06037"] * 4 + ["06059"] * 2,
            "date": pd.to_datetime(
                [
                    "2024-01-31",
                    "2024-04-30",
                    "2025-01-31",
                    "2025-04-30",
                    "2024-01-31",
                    "2024-05-31",
                ]
            ),
            "zhvi": [100.0, 121.0, 110.0, 133.1, 200.0, 220.0],
        }
    )

    result = home_price_momentum(frame)

    assert result.loc[1, "home_price_momentum_3m_annualized"] == pytest.approx(
        400.0 * np.log(121.0 / 100.0)
    )
    assert result.loc[2, "home_price_momentum_yoy"] == pytest.approx(
        100.0 * np.log(110.0 / 100.0)
    )
    assert result.loc[3, "home_price_momentum_3m_annualized"] == pytest.approx(
        400.0 * np.log(133.1 / 110.0)
    )
    assert pd.isna(result.loc[5, "home_price_momentum_3m_annualized"])


def test_exact_calendar_momentum_rejects_duplicate_county_months():
    frame = pd.DataFrame(
        {
            "county_fips": ["06037", "06037"],
            "date": pd.to_datetime(["2024-01-01", "2024-01-31"]),
            "value": [1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="one observation"):
        exact_calendar_log_momentum(
            frame, "value", lag_months=3, scale=400.0
        )


def test_price_to_rent_requires_positive_components():
    result = positive_price_to_rent_ratio(
        pd.Series([600_000.0, 0.0, 500_000.0, -1.0]),
        pd.Series([2_500.0, 2_000.0, 0.0, 2_000.0]),
    )
    assert result.iloc[0] == pytest.approx(20.0)
    assert result.iloc[1:].isna().all()


def test_laus_employment_momentum_uses_exact_twelve_month_log_change():
    frame = pd.DataFrame(
        {
            "county_fips": ["06037", "06037", "06037"],
            "date": pd.to_datetime(["2024-01-31", "2024-12-31", "2025-01-31"]),
            "employment": [100.0, 105.0, 110.0],
        }
    )
    result = laus_employment_momentum_yoy(frame)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(100.0 * np.log(1.1))


def test_frozen_pooled_population_parameters_ignore_post_reference_values():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2019-01-31", "2019-01-31", "2019-02-28", "2021-01-31"]
            ),
            "x": [1.0, 3.0, 5.0, 1_000.0],
        }
    )
    parameters = fit_pooled_population_zscore_parameters(
        frame,
        ["x"],
        reference_start="2019-01-01",
        reference_end="2019-12-31",
    )
    assert parameters["statistics"]["x"]["mean"] == pytest.approx(3.0)
    assert parameters["statistics"]["x"]["population_sd"] == pytest.approx(
        np.sqrt(8.0 / 3.0)
    )
    assert parameters["statistics"]["x"]["observation_count"] == 3

    frozen = MappingProxyType(parameters)
    first = apply_pooled_zscores(frame, frozen)["x"]
    appended = pd.concat(
        [frame, pd.DataFrame({"date": [pd.Timestamp("2022-01-31")], "x": [5_000.0]})],
        ignore_index=True,
    )
    second = apply_pooled_zscores(appended, frozen)["x"]
    pd.testing.assert_series_equal(first, second.iloc[: len(first)], check_names=False)


def test_market_tightness_uses_exact_three_components_and_ignores_inventory():
    reference = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2019-01-31", "2019-01-31", "2019-02-28", "2019-02-28"]
            ),
            "months_of_supply": [1.0, 2.0, 3.0, 4.0],
            "days_on_market": [10.0, 20.0, 30.0, 40.0],
            "price_drop_share": [0.1, 0.2, 0.3, 0.4],
            "inventory_growth_yoy": [-99.0, 99.0, -99.0, 99.0],
        }
    )
    parameters = fit_market_tightness_parameters(
        reference,
        reference_start="2019-01-01",
        reference_end="2019-12-31",
    )
    evaluation = pd.DataFrame(
        {
            "months_of_supply": [1.0, 1.0],
            "days_on_market": [10.0, 10.0],
            "price_drop_share": [0.1, np.nan],
            "inventory_growth_yoy": [-1_000_000.0, 1_000_000.0],
        }
    )
    index, coverage = market_tightness_index(evaluation, parameters)
    z = (1.0 - 2.5) / np.sqrt(1.25)
    assert index.iloc[0] == pytest.approx(-z)
    assert coverage.tolist() == [3, 2]
    assert pd.isna(index.iloc[1])
    assert "inventory_growth_yoy" not in parameters["statistics"]


def test_county_heat_requires_both_standardized_components():
    reference = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2019-01-31", "2019-02-28", "2019-03-31", "2019-04-30"]
            ),
            "home_price_momentum_3m_annualized": [-2.0, 0.0, 2.0, 4.0],
            "market_tightness_index": [-1.0, 0.0, 1.0, 2.0],
        }
    )
    parameters = fit_county_heat_parameters(
        reference,
        reference_start="2019-01-01",
        reference_end="2019-12-31",
    )
    evaluation = pd.DataFrame(
        {
            "home_price_momentum_3m_annualized": [4.0, 4.0],
            "market_tightness_index": [2.0, np.nan],
        }
    )
    result = county_housing_market_heat_index(evaluation, parameters)
    z_price = (4.0 - 1.0) / np.sqrt(5.0)
    z_tightness = (2.0 - 0.5) / np.sqrt(1.25)
    assert result.iloc[0] == pytest.approx((z_price + z_tightness) / 2.0)
    assert pd.isna(result.iloc[1])


def _relative_frame() -> pd.DataFrame:
    counties = ["06037", *DEFAULT_PEER_COUNTY_FIPS]
    first = pd.DataFrame(
        {
            "date": pd.Timestamp("2025-01-31"),
            "county_fips": counties,
            "price": [80.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "rent": [160.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0],
        }
    )
    second = first.iloc[:-1].copy()
    second["date"] = pd.Timestamp("2025-02-28")
    return pd.concat([first, second], ignore_index=True)


def test_la_relative_measure_requires_all_exactly_seven_peers():
    frame = _relative_frame()
    result = la_minus_seven_peer_mean(frame, "price")
    assert result.loc[0, "price_la_relative_to_seven_peers"] == pytest.approx(76.0)
    assert pd.isna(result.loc[1, "price_la_relative_to_seven_peers"])

    combined = la_relative_market_performance(frame, ["price", "rent"])
    assert combined.loc[0, "rent_la_relative_to_seven_peers"] == pytest.approx(
        152.0
    )
    with pytest.raises(ValueError, match="Exactly seven"):
        la_minus_seven_peer_mean(
            frame, "price", peer_county_fips=DEFAULT_PEER_COUNTY_FIPS[:-1]
        )


def test_la_relative_measure_rejects_duplicate_county_dates():
    frame = _relative_frame()
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="one row per county and date"):
        la_minus_seven_peer_mean(duplicate, "price")
