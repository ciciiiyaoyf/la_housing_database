from pathlib import Path

import duckdb
from openpyxl import load_workbook
import pandas as pd

from src.common.config import load_config
from src.reporting.catalog import (
    ACS_RENTAL_AVAILABILITY_SOURCE_IDS,
    ACS_RENTAL_AVAILABILITY_VARIABLE_IDS,
    DOCX_VARIABLES,
    HUD_USPS_RESTRICTED_TABLES,
    HUD_USPS_VARIABLE_IDS,
    RENTAL_INVENTORY_STATUS,
)


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
    assert rental_inventory["current_status"] == RENTAL_INVENTORY_STATUS
    assert set(rental_inventory["implemented_source_id"].split("|")) == {
        *ACS_RENTAL_AVAILABILITY_SOURCE_IDS,
        "hud_usps_ncwm_quarterly",
    }
    assert set(rental_inventory["implemented_variable_id"].split("|")) == set(
        (*ACS_RENTAL_AVAILABILITY_VARIABLE_IDS, *HUD_USPS_VARIABLE_IDS)
    )
    assert "analytics.fact_housing_observation" in rental_inventory[
        "implemented_table"
    ]
    assert "analytics.county_annual_controls" in rental_inventory[
        "implemented_table"
    ]
    assert all(
        path in rental_inventory["implemented_table"]
        for path in HUD_USPS_RESTRICTED_TABLES
    )
    assert bool(rental_inventory["panel_or_view_present"])
    assert "not true rental-listing inventory" in rental_inventory["limitation"]
    assert "native-quarterly only" in rental_inventory["limitation"]
    assert "no-stat remains a separate diagnostic" in rental_inventory[
        "limitation"
    ].lower()
    assert "postal-ZIP county-parts are modeled" in rental_inventory[
        "limitation"
    ]
    assert "access-controlled" in rental_inventory["limitation"]
    assert "ZORI is not inventory" in rental_inventory["limitation"]
    assert "Redfin for-sale active listings are not a valid substitute" in (
        rental_inventory["limitation"]
    )
    assert "monthly rental-listing series remains missing" in rental_inventory[
        "limitation"
    ].lower()

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
    ].eq("PASS").all()

    hud_usps = catalog.loc[
        catalog["source_id"].eq("hud_usps_ncwm_quarterly")
    ].iloc[0]
    assert hud_usps["active_status"] == "Active"
    assert hud_usps["provenance_status"] == "official_raw_archive_rebuilt"
    assert str(hud_usps["update_command"]).startswith(
        "python run_pipeline.py --stage download --source "
        "hud_usps_ncwm_quarterly"
    )
    assert hud_usps["source_manifest_path"] == (
        "metadata/hud_usps_source_manifest.csv"
    )
    assert "data/restricted/hud_usps/raw/" in hud_usps["raw_file_path"]
    assert "quarantine" not in hud_usps["raw_file_path"]
    assert "historical, superseded record" in hud_usps["notes"]
    assert "clean.hud_usps_ncwm_quarterly" in hud_usps[
        "destination_clean_table"
    ]
    assert "analytics.fact_housing_observation" in hud_usps[
        "destination_analytical_table"
    ]
    assert all(
        path in hud_usps["destination_analytical_table"]
        for path in HUD_USPS_RESTRICTED_TABLES[:3]
    )


def test_hud_usps_v2_methodology_and_gap_workbook() -> None:
    assumptions = pd.read_csv(
        ROOT / "metadata" / "proxy_methodology_assumptions.csv"
    )
    hud = assumptions.loc[
        assumptions["methodology_id"].eq(
            "hud_usps_residential_vacancy_proxy_v2"
        )
    ]
    assert len(hud) == 4
    assert hud["methodology_status"].eq("active").all()
    combined = " ".join(hud["value"].astype(str))
    assert "metadata/hud_usps_source_manifest.csv" in combined
    assert "No-stat / total remains a separate" in combined
    assert "postal-ZIP county-part values are modeled" in combined
    assert "native-quarterly" in combined
    assert "data/restricted/hud_usps/" in " ".join(
        hud["rationale"].astype(str)
    )

    acs = assumptions.loc[
        assumptions["methodology_id"].eq(
            "acs_rental_availability_proxy_v1"
        )
    ]
    assert len(acs) == 1
    assert acs.iloc[0]["variable_id"] == "acs_rental_availability_rate"
    assert "B25004" in acs.iloc[0]["value"]
    assert "not an active rental-listing stock" in acs.iloc[0]["rationale"]

    workbook = load_workbook(
        ROOT / "metadata" / "data_gap_analysis.xlsx", read_only=True
    )
    assert workbook.sheetnames == ["Gap Analysis"]


def test_source_coverage_preserves_geography_types():
    coverage = pd.read_csv(ROOT / "metadata" / "source_coverage.csv")

    zcta = coverage.loc[
        coverage["variable_id"].eq("acs_median_gross_rent")
        & coverage["geo_type"].eq("zcta")
    ].iloc[0]
    assert zcta["geography_count"] == 274
    assert set(
        coverage.loc[
            coverage["variable_id"].eq("acs_median_gross_rent"),
            "geo_type",
        ]
    ) == {"county", "zcta"}
    assert coverage.loc[
        coverage["variable_id"].eq(
            "fhfa_hpi_la_metropolitan_division_purchase_only_nsa"
        ),
        "geo_type",
    ].eq("metro_division").all()
    assert coverage.loc[
        coverage["variable_id"].eq("ces_total_nonfarm_employment_sa"),
        "geo_type",
    ].eq("metro").all()
    assert coverage.loc[
        coverage["variable_id"].eq("mortgage_rate_30y_weekly"),
        "geo_type",
    ].eq("national").all()
    assert not coverage["geo_type"].eq("unknown").any()


def test_source_tracker_workbook_is_human_readable():
    workbook_path = ROOT / "metadata" / "LA_Housing_Source_Tracker.xlsx"
    workbook = load_workbook(workbook_path)

    assert workbook.sheetnames[:2] == ["Overview", "Source Summary"]
    expected_sheets = {
        "Overview",
        "Source Summary",
        "DOCX Variable Audit",
        "Variable Dictionary",
        "Coverage Summary",
        "Data Catalog",
        "Raw File Inventory",
        "Raw Completeness",
        "Missing Deferred",
        "Proxy Assumptions",
        "Update Commands",
    }
    assert expected_sheets.issubset(set(workbook.sheetnames))

    for sheet_name in expected_sheets:
        worksheet = workbook[sheet_name]
        assert worksheet.sheet_view.showGridLines is False
        assert worksheet.tables

    summary_headers = [
        workbook["Source Summary"].cell(row=1, column=column).value
        for column in range(1, workbook["Source Summary"].max_column + 1)
    ]
    assert summary_headers[:6] == [
        "Source",
        "Provider",
        "Status",
        "Frequency",
        "Geography",
        "Key variables",
    ]

    def sheet_records(sheet_name: str) -> list[dict[str, object]]:
        worksheet = workbook[sheet_name]
        headers = [
            worksheet.cell(row=1, column=column).value
            for column in range(1, worksheet.max_column + 1)
        ]
        records = []
        for row in range(2, worksheet.max_row + 1):
            records.append(
                {
                    headers[column - 1]: worksheet.cell(row=row, column=column).value
                    for column in range(1, worksheet.max_column + 1)
                }
            )
        return records

    data_catalog = sheet_records("Data Catalog")
    assert any(
        row.get("source_id") == "eviction_lab_county_estimates"
        for row in data_catalog
    )

    variable_audit = sheet_records("DOCX Variable Audit")
    eviction_rows = [
        row
        for row in variable_audit
        if row.get("variable_name") == "Eviction trends (high-frequency, consistent)"
    ]
    assert eviction_rows
    assert eviction_rows[0]["current_status"] == "Implemented"
    assert eviction_rows[0]["implemented_source_id"] == "eviction_lab_county_estimates"

    variable_dictionary = sheet_records("Variable Dictionary")
    dictionary_ids = {row.get("variable_id") for row in variable_dictionary}
    assert "eviction_lab_filings_estimate_annual" in dictionary_ids
    assert "natural_population_change_quarterly_allocated" in dictionary_ids

    coverage = sheet_records("Coverage Summary")
    assert any(
        str(row.get("variable_id", "")).startswith("eviction_lab_")
        for row in coverage
    )
    assert any(
        row.get("variable_id") == "acs_median_gross_rent"
        and row.get("geo_type") == "zcta"
        and row.get("geography_count") == 274
        for row in coverage
    )
    assert any(
        row.get("variable_id")
        == "fhfa_hpi_la_metropolitan_division_purchase_only_nsa"
        and row.get("geo_type") == "metro_division"
        for row in coverage
    )


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


def test_version_a_finance_is_authoritative_and_b25106_legacy_is_retained():
    assumptions = pd.read_csv(ROOT / "metadata" / "proxy_methodology_assumptions.csv")
    assert "acs_implied_aggregate_mortgage_debt_proxy" in set(
        assumptions["variable_id"]
    )
    legacy_assumptions = assumptions.loc[
        assumptions["methodology_id"].eq(
            "acs_implied_mortgage_debt_proxy_legacy_v0"
        )
    ]
    assert not legacy_assumptions.empty
    assert legacy_assumptions["methodology_status"].eq("legacy").all()

    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    mortgage_debt = audit.loc[
        audit["variable_name"].eq("Aggregate Mortgage Debt (outstanding balances)")
    ].iloc[0]
    assert mortgage_debt["current_status"] == "Proxy implemented"
    assert "census_acs5_finance" in mortgage_debt["implemented_source_id"]
    assert "acs_implied_first_mortgage_balance_cal_benchmark" in mortgage_debt[
        "implemented_variable_id"
    ]
    assert "older B25106" in mortgage_debt["limitation"]

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


def test_cdc_wonder_natural_population_change_is_loaded_and_allocated():
    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    natural = audit.loc[
        audit["variable_name"].eq("Natural Population Change (quarterly)")
    ].iloc[0]
    assert natural["current_status"] == "Implemented with quarterly allocation"
    assert "cdc_wonder_natality_county" in natural["implemented_source_id"]
    assert "natural_population_change_quarterly_allocated" in natural[
        "implemented_variable_id"
    ]

    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        annual = connection.execute(
            """
            SELECT min(date), max(date), count(*)
            FROM analytics.county_annual_controls
            WHERE natural_population_change_annual IS NOT NULL
              AND county_fips = '06037'
            """
        ).fetchone()
        quarterly = connection.execute(
            """
            SELECT min(date), max(date), count(*),
                   bool_and(is_imputed), bool_and(is_interpolated)
            FROM analytics.county_quarterly_controls
            WHERE natural_population_change_quarterly_allocated IS NOT NULL
              AND county_fips = '06037'
            """
        ).fetchone()

    assert annual[0].year == 2007
    assert annual[1].year == 2024
    assert annual[2] == 18
    assert quarterly[0] == pd.Timestamp("2007-03-31")
    assert quarterly[1] == pd.Timestamp("2024-12-31")
    assert quarterly[2] == 72
    assert quarterly[3] is True
    assert quarterly[4] is True


def test_eviction_lab_county_estimates_are_documented_and_loaded():
    audit = pd.read_csv(ROOT / "metadata" / "docx_variable_audit.csv")
    evictions = audit.loc[
        audit["variable_name"].eq("Eviction trends (high-frequency, consistent)")
    ].iloc[0]
    assert evictions["current_status"] == "Implemented"
    assert evictions["implemented_source_id"] == "eviction_lab_county_estimates"
    assert "eviction_lab_filings_rate_estimate_annual" in evictions[
        "implemented_variable_id"
    ]
    assert "not cover Los Angeles County" in evictions["limitation"]

    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        clean_coverage = connection.execute(
            """
            SELECT min(observation_date), max(observation_date),
                   count(DISTINCT observation_date), count(*),
                   count(DISTINCT county_fips)
            FROM clean.eviction_lab_county_estimates
            WHERE variable_id IN (
                'eviction_lab_filings_estimate_annual',
                'eviction_lab_households_threatened_estimate_annual'
            )
            """
        ).fetchone()
        county_coverage = connection.execute(
            """
            SELECT min(date), max(date), count(*)
            FROM analytics.county_annual_controls
            WHERE eviction_lab_filings_rate_estimate_annual IS NOT NULL
              AND county_fips = '06037'
            """
        ).fetchone()
        duplicate_count = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT geo_id, observation_date, variable_id
                FROM clean.eviction_lab_county_estimates
                GROUP BY ALL
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]

    assert clean_coverage[0].year == 2000
    assert clean_coverage[1].year == 2018
    assert clean_coverage[2] == 19
    assert clean_coverage[3] == 38
    assert clean_coverage[4] == 1
    assert county_coverage[0].year == 2000
    assert county_coverage[1].year == 2018
    assert county_coverage[2] == 19
    assert duplicate_count == 0
