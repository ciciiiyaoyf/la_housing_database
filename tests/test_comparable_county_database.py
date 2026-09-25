from pathlib import Path

import duckdb

from src.geography.comparable_counties import COMPARABLE_COUNTY_FIPS


ROOT = Path(__file__).resolve().parents[1]


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(ROOT / "database" / "housing.duckdb"), read_only=True)


def test_comparable_county_outputs_are_registered_with_exact_geographies():
    with _connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'analytics'
                """
            ).fetchall()
        }
        assert {
            "comparable_county_monthly_analytics",
            "zip_market_regime_quarterly",
            "la_zip_market_regime_summary_quarterly",
            "la_zip_market_regime_transitions_quarterly",
            "county_housing_market_heat_monthly",
            "la_relative_market_performance_monthly",
        }.issubset(tables)
        counties = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT county_fips
                FROM analytics.comparable_county_monthly_analytics
                """
            ).fetchall()
        }
        assert counties == set(COMPARABLE_COUNTY_FIPS)
        duplicate_count = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT date, county_fips
                FROM analytics.comparable_county_monthly_analytics
                GROUP BY ALL
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]
        assert duplicate_count == 0


def test_new_county_and_zip_variables_are_materialized():
    with _connect() as connection:
        county_counts = connection.execute(
            """
            SELECT
                count(zhvi_momentum_yoy_log_pct),
                count(zhvi_momentum_3m_annualized_log_pct),
                count(zori_momentum_yoy_log_pct),
                count(market_tightness_index_3c_fixed),
                count(laus_employment_momentum_yoy_log_pct),
                count(county_housing_market_heat_index),
                count(la_relative_price_to_rent_ratio_zillow)
            FROM analytics.comparable_county_monthly_analytics
            """
        ).fetchone()
        assert all(value > 0 for value in county_counts)
        zip_counts = connection.execute(
            """
            SELECT
                count(zhvi_momentum_yoy_log_pct),
                count(zori_momentum_3m_annualized_log_pct),
                count(relative_valuation_pressure_la_zip)
            FROM analytics.zip_monthly_panel
            """
        ).fetchone()
        assert all(value > 0 for value in zip_counts)


def test_frozen_regime_outputs_publish_complete_denominators():
    with _connect() as connection:
        regime_ids = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT zip_market_regime_id
                FROM analytics.zip_market_regime_quarterly
                WHERE zip_market_regime_id IS NOT NULL
                """
            ).fetchall()
        }
        assert regime_ids == {1, 2, 3, 4}
        summary_failures = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT quarter_end,
                       count(*) AS rows,
                       sum(zip_market_regime_share) AS share_sum,
                       min(classified_zip_count) AS classified_min,
                       max(classified_zip_count) AS classified_max
                FROM analytics.la_zip_market_regime_summary_quarterly
                GROUP BY quarter_end
            )
            WHERE rows <> 4
               OR classified_min <> classified_max
               OR abs(share_sum - 100.0) > 1e-10
            """
        ).fetchone()[0]
        transition_failures = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT quarter_end,
                       count(*) AS rows,
                       sum(transition_zip_count) AS transitions,
                       min(comparable_zip_count) AS denominator_min,
                       max(comparable_zip_count) AS denominator_max
                FROM analytics.la_zip_market_regime_transitions_quarterly
                GROUP BY quarter_end
            )
            WHERE rows <> 16
               OR denominator_min <> denominator_max
               OR transitions <> denominator_min
            """
        ).fetchone()[0]
        assert summary_failures == 0
        assert transition_failures == 0
