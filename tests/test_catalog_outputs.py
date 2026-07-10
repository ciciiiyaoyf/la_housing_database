from pathlib import Path

import duckdb
import pandas as pd

from src.common.config import load_config
from src.reporting.catalog import DOCX_VARIABLES


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database" / "housing.duckdb"


def test_docx_variable_audit_covers_attached_variable_sheet():
    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    requirements = pd.read_csv(ROOT / "metadata" / "docx_variable_requirements.csv")
    expected_names = {item["variable_name"] for item in DOCX_VARIABLES}

    assert len(audit) == len(DOCX_VARIABLES)
    assert expected_names == set(audit["variable_name"])
    assert len(requirements) == len(DOCX_VARIABLES)
    assert {
        "sheet_source",
        "sheet_frequency",
        "sheet_la_availability",
        "sheet_notes",
        "best_public_workaround",
    }.issubset(requirements.columns)

    rental_inventory = audit.loc[
        audit["variable_name"].eq("Rental Inventory")
    ].iloc[0]
    assert rental_inventory["expected_source_from_docx"] == (
        "Zillow Observed Rent Index / Listings"
    )
    assert rental_inventory["current_status"] == (
        "Not implemented - sheet source endpoint not verified"
    )
    assert "ZORI is not inventory" in rental_inventory["limitation"]
    assert "Redfin for-sale active listings are not a valid substitute" in (
        rental_inventory["limitation"]
    )

    permits = audit.loc[
        audit["variable_name"].eq("Single-Family Housing Starts (permits)")
    ].iloc[0]
    assert permits["expected_source_from_docx"] == "Census BPS (1-unit permits)"
    assert "authorizations, not starts or completions" in permits["limitation"]


def test_data_catalog_shape_and_active_sources_are_complete():
    config = load_config(ROOT)
    active_source_ids = {source.source_id for source in config.sources if source.enabled}
    catalog = pd.read_csv(ROOT / "metadata" / "data_catalog.csv")
    raw_completeness = pd.read_csv(
        ROOT / "output" / "qa" / "raw_repository_completeness.csv"
    )

    assert catalog.columns[:4].tolist() == [
        "Source",
        "Frequency",
        "Geography",
        "Key variables",
    ]
    assert active_source_ids.issubset(set(catalog["source_id"]))
    assert active_source_ids.issubset(set(raw_completeness["source_id"]))
    assert raw_completeness.loc[
        raw_completeness["source_id"].isin(active_source_ids),
        "completeness_status",
    ].eq("pass").all()


def test_raw_repository_completeness_is_available_in_duckdb():
    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        row_count = connection.execute(
            "SELECT count(*) FROM quality.raw_repository_completeness"
        ).fetchone()[0]
        duplicate_count = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT geo_id, observation_date, variable_id
                FROM analytics.fact_housing_observation
                GROUP BY ALL
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]

    assert row_count >= len(load_config(ROOT).sources)
    assert duplicate_count == 0


def test_acs_b25106_mortgage_debt_proxy_is_documented_and_available():
    assumptions = pd.read_csv(ROOT / "metadata" / "proxy_methodology_assumptions.csv")
    assert "acs_implied_aggregate_mortgage_debt_proxy" in set(
        assumptions["variable_id"]
    )
    assert (
        assumptions["methodology_id"].eq("acs_implied_mortgage_debt_proxy_v1").any()
    )

    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    mortgage_debt = audit.loc[
        audit["variable_name"].eq("Aggregate Mortgage Debt (outstanding balances)")
    ].iloc[0]
    assert mortgage_debt["current_status"] == "Proxy implemented"
    assert "census_acs_5year_b25106" in mortgage_debt["implemented_source_id"]

    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        clean_rows = connection.execute(
            "SELECT count(*) FROM clean.census_acs_5year_b25106"
        ).fetchone()[0]
        proxy = connection.execute(
            """
            SELECT acs_implied_aggregate_mortgage_debt_proxy,
                   acs_implied_average_mortgage_balance_proxy,
                   acs_mortgage_debt_proxy_rate_assumption,
                   acs_mortgage_debt_proxy_term_years_assumption,
                   acs_mortgage_debt_proxy_debt_service_share_assumption
            FROM analytics.county_annual_controls
            WHERE date = DATE '2024-12-31'
            """
        ).fetchone()

    assert clean_rows > 0
    assert proxy[0] > 0
    assert proxy[1] > 0
    assert proxy[2] > 0
    assert proxy[3] == 25.0
    assert proxy[4] == 0.80


def test_irs_soi_net_migration_is_documented_and_loaded():
    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    migration = audit.loc[
        audit["variable_name"].eq("Net Immigration / Migration (county)")
    ].iloc[0]
    assert migration["current_status"] == "Implemented"
    assert migration["implemented_source_id"] == "irs_soi_county_migration"
    assert "irs_soi_net_migration_exemptions" in migration["implemented_variable_id"]

    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        clean_coverage = connection.execute(
            """
            SELECT min(observation_date), max(observation_date),
                   count(DISTINCT observation_date), count(*)
            FROM clean.irs_soi_county_migration
            """
        ).fetchone()
        county_coverage = connection.execute(
            """
            SELECT min(date), max(date), count(*)
            FROM analytics.county_annual_controls
            WHERE irs_soi_net_migration_exemptions IS NOT NULL
            """
        ).fetchone()
        duplicate_count = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT geo_id, observation_date, variable_id
                FROM clean.irs_soi_county_migration
                GROUP BY ALL
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]

    assert clean_coverage[0].year == 2011
    assert clean_coverage[1].year == 2023
    assert clean_coverage[2] == 13
    assert clean_coverage[3] == 117
    assert county_coverage[0].year == 2011
    assert county_coverage[1].year == 2023
    assert county_coverage[2] == 13
    assert duplicate_count == 0
