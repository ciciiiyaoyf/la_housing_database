import os
from pathlib import Path

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(
    os.environ.get("HOUSING_DATABASE_PATH", ROOT / "database" / "housing.duckdb")
)


def _connect():
    assert DATABASE_PATH.exists(), f"Database does not exist: {DATABASE_PATH}"
    return duckdb.connect(str(DATABASE_PATH), read_only=True)


def test_redfin_zip_clean_and_fact_contract():
    with _connect() as connection:
        summary = connection.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT postal_zip) AS zips,
                   min(period_end) AS first_period,
                   max(period_end) AS latest_period,
                   count(*) - count(DISTINCT
                       (postal_zip, period_end, source_vintage)) AS duplicates,
                   count(*) FILTER (
                       WHERE length(postal_zip) != 5
                          OR postal_zip !~ '^[0-9]{5}$'
                          OR primary_county_fips != '06037'
                          OR region_type != 'Zip'
                          OR frequency != 'Rolling 3 Months'
                          OR rolling_window_months != 3
                   ) AS invalid_rows
            FROM clean.redfin_zip_monthly
            """
        ).fetchone()
        assert summary[0] > 0
        assert summary[1] > 0
        assert summary[2] <= summary[3]
        assert summary[4] == 0
        assert summary[5] == 0

        variables = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT variable_id
                FROM analytics.fact_housing_observation
                WHERE variable_id LIKE 'redfin_zip_%'
                """
            ).fetchall()
        }
        assert variables == {
            "redfin_zip_homes_sold_rolling_3m",
            "redfin_zip_median_sale_price_nsa_rolling_3m",
            "redfin_zip_median_days_on_market_rolling_3m",
            "redfin_zip_new_listings_rolling_3m",
            "redfin_zip_active_listings_rolling_3m",
            "redfin_zip_pending_sales_rolling_3m",
            "redfin_zip_homes_sold_yoy_pct_provider_rolling_3m",
            "redfin_zip_median_sale_price_nsa_yoy_pct_provider_rolling_3m",
            "redfin_zip_median_days_on_market_yoy_pct_provider_rolling_3m",
            "redfin_zip_new_listings_yoy_pct_provider_rolling_3m",
            "redfin_zip_active_listings_yoy_pct_provider_rolling_3m",
            "redfin_zip_pending_sales_yoy_pct_provider_rolling_3m",
        }
        invalid_fact_geographies = connection.execute(
            """
            SELECT count(*)
            FROM analytics.fact_housing_observation
            WHERE variable_id LIKE 'redfin_zip_%'
              AND geo_id NOT LIKE 'ZIP_%'
            """
        ).fetchone()[0]
        assert invalid_fact_geographies == 0


def test_redfin_left_join_preserves_zillow_spine_and_values():
    with _connect() as connection:
        panel_rows = connection.execute(
            "SELECT count(*) FROM analytics.zip_monthly_panel"
        ).fetchone()[0]
        zillow_spine_rows = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT DISTINCT observation_date, geo_id
                FROM analytics.fact_housing_observation
                WHERE variable_id IN ('zhvi', 'zori_smoothed_sa')
                  AND geo_id LIKE 'ZIP_%'
            )
            """
        ).fetchone()[0]
        assert panel_rows == zillow_spine_rows

        redfin_mismatches = connection.execute(
            """
            SELECT count(*)
            FROM clean.redfin_zip_monthly AS clean
            LEFT JOIN analytics.zip_monthly_panel AS panel
              ON panel.postal_zip = clean.postal_zip
             AND panel.date = clean.period_end
            WHERE panel.date IS NULL
               OR panel.homes_sold IS DISTINCT FROM clean.homes_sold
               OR panel.median_sale_price_nsa
                    IS DISTINCT FROM clean.median_sale_price_nsa
               OR panel.median_days_on_market
                    IS DISTINCT FROM clean.median_days_on_market
               OR panel.new_listings IS DISTINCT FROM clean.new_listings
               OR panel.active_listings IS DISTINCT FROM clean.active_listings
            """
        ).fetchone()[0]
        assert redfin_mismatches == 0

        zillow_mismatches = connection.execute(
            """
            WITH latest AS (
                SELECT observation_date, geo_id, variable_id, value,
                       row_number() OVER (
                           PARTITION BY observation_date, geo_id, variable_id
                           ORDER BY source_vintage DESC, retrieved_at DESC
                       ) AS rank
                FROM analytics.fact_housing_observation
                WHERE variable_id IN ('zhvi', 'zori_smoothed_sa')
                  AND geo_id LIKE 'ZIP_%'
            ),
            wide AS (
                SELECT observation_date,
                       replace(geo_id, 'ZIP_', '') AS postal_zip,
                       max(value) FILTER (WHERE variable_id = 'zhvi') AS zhvi,
                       max(value) FILTER (
                           WHERE variable_id = 'zori_smoothed_sa'
                       ) AS zori
                FROM latest
                WHERE rank = 1
                GROUP BY ALL
            )
            SELECT count(*)
            FROM wide
            JOIN analytics.zip_monthly_panel AS panel
              ON panel.date = wide.observation_date
             AND panel.postal_zip = wide.postal_zip
            WHERE panel.zhvi IS DISTINCT FROM wide.zhvi
               OR panel.zori IS DISTINCT FROM wide.zori
            """
        ).fetchone()[0]
        assert zillow_mismatches == 0


def test_redfin_zip_database_parquet_and_csv_exports_agree():
    parquet = pd.read_parquet(
        ROOT / "data" / "final" / "zip_monthly_panel.parquet"
    )
    csv = pd.read_csv(
        ROOT / "output" / "exports" / "la_zip_monthly_panel.csv",
        dtype={"postal_zip": str},
        parse_dates=["date"],
    )
    with _connect() as connection:
        database = connection.execute(
            """
            SELECT date, postal_zip
            FROM analytics.zip_monthly_panel
            ORDER BY postal_zip, date
            """
        ).fetch_df()
    parquet_keys = parquet[["date", "postal_zip"]].sort_values(
        ["postal_zip", "date"]
    ).reset_index(drop=True)
    csv_keys = csv[["date", "postal_zip"]].sort_values(
        ["postal_zip", "date"]
    ).reset_index(drop=True)
    database["date"] = pd.to_datetime(database["date"])
    database["postal_zip"] = database["postal_zip"].astype(str)
    pd.testing.assert_frame_equal(parquet_keys, csv_keys)
    pd.testing.assert_frame_equal(parquet_keys, database)


def test_zip_zillow_derived_fields_preserve_exact_calendar_gaps():
    panel = pd.read_parquet(
        ROOT / "data" / "final" / "zip_monthly_panel.parquet"
    ).sort_values(["postal_zip", "date"])
    valid = panel.loc[panel["zhvi"].gt(0) & panel["zori"].gt(0)].copy()
    assert not valid.empty
    pd.testing.assert_series_equal(
        valid["price_to_rent_ratio_zillow"],
        valid["zhvi"] / (12.0 * valid["zori"]),
        check_names=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )
    pd.testing.assert_series_equal(
        valid["implied_gross_rent_yield"],
        12.0 * valid["zori"] / valid["zhvi"],
        check_names=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )
    assert (
        valid["price_to_rent_ratio_zillow"]
        * valid["implied_gross_rent_yield"]
    ).sub(1.0).abs().max() < 1e-12

    prior = panel[["date", "postal_zip", "zori"]].copy()
    prior["date"] = prior["date"] + pd.DateOffset(months=12)
    prior = prior.rename(columns={"zori": "prior_zori"})
    aligned = panel.merge(prior, on=["date", "postal_zip"], how="left")
    expected = aligned["zori"] / aligned["prior_zori"] - 1.0
    pd.testing.assert_series_equal(
        aligned["zori_growth_yoy"],
        expected,
        check_names=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def test_homebuying_cost_scenario_table_is_complete_and_keyed():
    scenarios = pd.read_parquet(
        ROOT / "data" / "final" / "homebuying_cost_scenarios.parquet"
    )
    key = [
        "date",
        "geo_id",
        "down_payment_variable_id",
        "closing_cost_variable_id",
    ]
    assert not scenarios.empty
    assert not scenarios.duplicated(key).any()
    assert set(scenarios["closing_cost_variable_id"].unique()) == {
        "closing_cost_saleprice_2pct",
        "closing_cost_saleprice_3pct",
        "closing_cost_saleprice_5pct",
        "closing_cost_loan_3pct",
        "closing_cost_loan_6pct",
    }
    assert scenarios["is_constructed"].all()
    assert not scenarios["is_observed"].any()
    assert not scenarios["prepaid_escrow_included"].any()
    assert (
        scenarios["down_payment_amount"]
        + scenarios["closing_cost_amount"]
        - scenarios["total_cash_to_close_scenario"]
    ).abs().max() < 1e-8

    zip_rows = scenarios.loc[scenarios["geography_level"].eq("postal_zip")]
    assert not zip_rows.empty
    assert zip_rows["price_rolling_window_months"].eq(3).all()
    with _connect() as connection:
        database_count = connection.execute(
            "SELECT count(*) FROM analytics.homebuying_cost_scenarios"
        ).fetchone()[0]
    assert database_count == len(scenarios)
