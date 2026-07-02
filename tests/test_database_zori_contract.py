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


def test_database_contains_only_active_sa_zori_contract():
    with _connect() as connection:
        clean_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'clean' AND table_name LIKE '%zori%'
                """
            ).fetchall()
        }
        assert clean_tables == {
            "zillow_county_zori_smoothed_sa",
            "zillow_zip_zori_smoothed_sa",
        }

        fact_variables = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT variable_id
                FROM analytics.fact_housing_observation
                WHERE variable_id LIKE 'zori%'
                """
            ).fetchall()
        }
        assert fact_variables == {"zori_smoothed_sa"}

        source_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_id FROM metadata.source_registry"
            ).fetchall()
        }
        assert {
            "zillow_county_zori_smoothed_sa",
            "zillow_zip_zori_smoothed_sa",
        }.issubset(source_ids)
        assert {
            "zillow_county_zori",
            "zillow_zip_zori",
            "zillow_county_zori_smoothed_nsa",
            "zillow_zip_zori_smoothed_nsa",
        }.isdisjoint(source_ids)

        dictionary_variables = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT variable_id, is_observed
                FROM metadata.variable_dictionary
                WHERE variable_id LIKE 'zori%'
                """
            ).fetchall()
        }
        assert dictionary_variables == {
            "zori_smoothed_sa": True,
            "zori_growth_mom": False,
            "zori_growth_yoy": False,
        }


def test_panels_expose_only_semantic_zori_and_match_sa_clean_tables():
    with _connect() as connection:
        for table in ("county_monthly_panel", "zip_monthly_panel"):
            columns = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info('analytics.{table}')"
                ).fetchall()
            }
            assert "zori" in columns
            assert "zori_smoothed_sa" not in columns
            assert "zori_smoothed_nsa" not in columns

        county_mismatches = connection.execute(
            """
            SELECT count(*)
            FROM clean.zillow_county_zori_smoothed_sa AS clean
            LEFT JOIN analytics.county_monthly_panel AS panel
              ON panel.date = clean.observation_date
            WHERE panel.date IS NULL
               OR panel.zori IS DISTINCT FROM clean.value
            """
        ).fetchone()[0]
        zip_mismatches = connection.execute(
            """
            SELECT count(*)
            FROM clean.zillow_zip_zori_smoothed_sa AS clean
            LEFT JOIN analytics.zip_monthly_panel AS panel
              ON panel.date = clean.observation_date
             AND panel.postal_zip = replace(clean.geo_id, 'ZIP_', '')
            WHERE panel.date IS NULL
               OR panel.zori IS DISTINCT FROM clean.value
            """
        ).fetchone()[0]
        assert county_mismatches == 0
        assert zip_mismatches == 0

        clean_county_count = connection.execute(
            "SELECT count(*) FROM clean.zillow_county_zori_smoothed_sa"
        ).fetchone()[0]
        panel_county_count = connection.execute(
            "SELECT count(*) FROM analytics.county_monthly_panel WHERE zori IS NOT NULL"
        ).fetchone()[0]
        clean_zip_count = connection.execute(
            "SELECT count(*) FROM clean.zillow_zip_zori_smoothed_sa"
        ).fetchone()[0]
        panel_zip_count = connection.execute(
            "SELECT count(*) FROM analytics.zip_monthly_panel WHERE zori IS NOT NULL"
        ).fetchone()[0]
        assert clean_county_count == panel_county_count
        assert clean_zip_count == panel_zip_count


def test_zori_keys_coverage_and_derived_features_are_valid():
    with _connect() as connection:
        duplicate_count = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT observation_date, geo_id, variable_id
                FROM analytics.fact_housing_observation
                WHERE variable_id LIKE 'zori%'
                GROUP BY ALL
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]
        assert duplicate_count == 0

        county_coverage = connection.execute(
            """
            SELECT count(*), min(observation_date), max(observation_date)
            FROM clean.zillow_county_zori_smoothed_sa
            """
        ).fetchone()
        zip_coverage = connection.execute(
            """
            SELECT count(*), count(DISTINCT geo_id),
                   min(observation_date), max(observation_date)
            FROM clean.zillow_zip_zori_smoothed_sa
            """
        ).fetchone()
        assert county_coverage[0] > 12
        assert county_coverage[1] == zip_coverage[2]
        assert county_coverage[2] == zip_coverage[3]
        assert zip_coverage[0] > county_coverage[0]
        assert zip_coverage[1] > 1

        panel = connection.execute(
            """
            SELECT date, zori, zhvi, zori_growth_mom, zori_growth_yoy,
                   price_to_rent_ratio,
                   mortgage_payment_10pct_down,
                   mortgage_payment_to_rent_10pct_down,
                   mortgage_payment_20pct_down,
                   mortgage_payment_to_rent_20pct_down
            FROM analytics.county_monthly_panel
            ORDER BY date
            """
        ).fetch_df()

    expected = {
        "zori_growth_mom": panel["zori"].pct_change(1, fill_method=None),
        "zori_growth_yoy": panel["zori"].pct_change(12, fill_method=None),
        "price_to_rent_ratio": panel["zhvi"] / (12.0 * panel["zori"]),
        "mortgage_payment_to_rent_10pct_down": (
            panel["mortgage_payment_10pct_down"] / panel["zori"]
        ),
        "mortgage_payment_to_rent_20pct_down": (
            panel["mortgage_payment_20pct_down"] / panel["zori"]
        ),
    }
    for column, values in expected.items():
        pd.testing.assert_series_equal(
            panel[column],
            values,
            check_names=False,
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )
