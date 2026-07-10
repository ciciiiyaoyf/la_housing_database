import os
from pathlib import Path

import duckdb
import pandas as pd

from src.common.config import load_config


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(
    os.environ.get("HOUSING_DATABASE_PATH", ROOT / "database" / "housing.duckdb")
)


def _connect():
    assert DATABASE_PATH.exists(), f"Database does not exist: {DATABASE_PATH}"
    return duckdb.connect(str(DATABASE_PATH), read_only=True)


def test_county_panel_exposes_all_pmms_monthly_aggregates():
    with _connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('analytics.county_monthly_panel')"
            ).fetchall()
        }
        assert {
            "mortgage_rate_30y_monthly_average",
            "mortgage_rate_30y_monthly_median",
            "mortgage_rate_30y_month_end",
            "mortgage_rate_30y_observation_count",
        }.issubset(columns)
        summary = connection.execute(
            """
            SELECT count(*) FILTER (
                       WHERE mortgage_rate_30y_monthly_average IS NOT NULL
                         AND mortgage_rate_30y_monthly_median IS NOT NULL
                         AND mortgage_rate_30y_month_end IS NOT NULL
                         AND mortgage_rate_30y_observation_count IS NOT NULL
                   ) AS complete_pmms_months,
                   min(mortgage_rate_30y_observation_count),
                   max(mortgage_rate_30y_observation_count)
            FROM analytics.county_monthly_panel
            """
        ).fetchone()

    assert summary[0] > 12
    assert summary[1] >= 1
    assert summary[2] <= 5


def test_duckdb_clean_schema_contains_only_active_configured_sources():
    config = load_config(ROOT)
    expected = {
        source.destination_clean_table or source.source_id
        for source in config.sources
        if source.enabled
    }
    with _connect() as connection:
        actual = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'clean'
                """
            ).fetchall()
        }

    assert actual == expected


def test_missing_values_are_preserved_in_panels_and_reports():
    with _connect() as connection:
        county_missing = connection.execute(
            """
            SELECT count(*)
            FROM analytics.county_monthly_panel
            WHERE zori IS NULL
              AND price_to_rent_ratio IS NULL
            """
        ).fetchone()[0]
        zip_redfin_missing = connection.execute(
            """
            SELECT count(*)
            FROM analytics.zip_monthly_panel
            WHERE redfin_available = false
            """
        ).fetchone()[0]
        county_bps_missing = connection.execute(
            """
            SELECT count(*)
            FROM analytics.county_monthly_panel
            WHERE permits_total IS NULL
            """
        ).fetchone()[0]

    assert county_missing > 0
    assert zip_redfin_missing > 0
    assert county_bps_missing > 0

    missingness = pd.read_csv(ROOT / "output" / "coverage" / "missingness_summary.csv")
    assert {
        "analytics.county_monthly_panel",
        "analytics.zip_monthly_panel",
    }.issubset(set(missingness["table_name"]))
    assert (missingness["missing_count"] > 0).any()


def test_redfin_vintage_reports_are_written():
    active = pd.read_csv(ROOT / "output" / "coverage" / "redfin_active_vintage.csv")
    comparison = pd.read_csv(
        ROOT / "output" / "coverage" / "redfin_vintage_comparison.csv"
    )

    assert {
        "source_id",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "checksum_sha256",
        "provider_last_updated",
    }.issubset(active.columns)
    assert {
        "source_id",
        "old_source_vintage",
        "new_source_vintage",
        "old_checksum_sha256",
        "new_checksum_sha256",
        "note",
    }.issubset(comparison.columns)
