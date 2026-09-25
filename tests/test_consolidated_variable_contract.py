from pathlib import Path

import pandas as pd
import yaml

from src.common.config import load_config


ROOT = Path(__file__).resolve().parents[1]

HUD_USPS_VARIABLE_IDS = {
    "usps_total_residential_addresses",
    "usps_short_term_vacant_residential_addresses",
    "usps_long_term_vacant_residential_addresses",
    "usps_vacant_residential_addresses",
    "usps_no_stat_residential_addresses",
    "usps_active_residential_addresses",
    "usps_residential_vacancy_rate",
    "usps_residential_no_stat_rate",
}

REQUIRED_REFRESH_SOURCE_IDS = (
    "bls_ces_la_metro",
    "bls_cpi_la_metro",
    "fhfa_hpi_state_purchase_only",
    "fhfa_hpi_metro_purchase_only",
    "fhfa_hpi_county_annual",
    "bea_state_personal_income",
    "bea_county_personal_income",
    "census_hvs_state_rental_vacancy",
)


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def _by_id(rows: list[dict], key: str) -> dict[str, dict]:
    return {str(row[key]): row for row in rows}


def test_consolidated_variable_and_source_counts_are_exact() -> None:
    general_variables = _yaml("config/variables.yaml")["variables"]
    version_a_variables = _yaml("config/version_a_variables.yaml")["variables"]
    version_b_variables = _yaml("config/version_b_variables.yaml")["variables"]
    version_c_variables = _yaml("config/version_c_variables.yaml")["variables"]
    general_variable_ids = {
        str(variable["variable_id"]) for variable in general_variables
    }
    version_a_variable_ids = {
        str(variable["variable_id"]) for variable in version_a_variables
    }
    version_b_variable_ids = {
        str(variable["variable_id"]) for variable in version_b_variables
    }
    version_c_variable_ids = {
        str(variable["variable_id"]) for variable in version_c_variables
    }

    assert len(general_variables) == len(general_variable_ids) == 375
    assert len(version_a_variables) == len(version_a_variable_ids) == 67
    assert len(version_b_variables) == len(version_b_variable_ids) == 58
    assert len(version_c_variables) == len(version_c_variable_ids) == 58
    all_contract_ids = (
        general_variable_ids
        | version_a_variable_ids
        | version_b_variable_ids
        | version_c_variable_ids
    )
    assert len(all_contract_ids) == 558
    assert all(variable_id.endswith("_benchmark") for variable_id in version_a_variable_ids)
    assert all(variable_id.endswith("_monthly_hist") for variable_id in version_b_variable_ids)
    assert all(variable_id.endswith("_monthly_rt") for variable_id in version_c_variable_ids)

    loaded_variable_ids = [
        str(variable["variable_id"]) for variable in load_config(ROOT).variables
    ]
    assert len(loaded_variable_ids) == len(set(loaded_variable_ids)) == 442
    assert set(loaded_variable_ids) == general_variable_ids | version_a_variable_ids

    dictionary = pd.read_csv(ROOT / "metadata/data_dictionary.csv", dtype=str)
    assert len(dictionary) == dictionary["variable_id"].nunique() == 558
    assert set(dictionary["variable_id"]) == all_contract_ids

    general_sources = _yaml("config/sources.yaml")["sources"]
    general_source_ids = {str(source["source_id"]) for source in general_sources}
    version_a_source_ids = set(_yaml("config/version_a_benchmark.yaml")["sources"])

    assert len(general_sources) == len(general_source_ids) == 61
    assert len(version_a_source_ids) == 9
    assert general_source_ids & version_a_source_ids == {"freddie_mac_pmms"}
    assert len(general_source_ids | version_a_source_ids) == 69


def test_version_a_finance_is_authoritative_and_b25106_is_legacy() -> None:
    general = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    version_a = _by_id(
        _yaml("config/version_a_variables.yaml")["variables"], "variable_id"
    )

    authoritative_ids = {
        "acs_implied_first_mortgage_balance_cal_benchmark",
        "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
        "acs_implied_owner_occupied_home_equity_all_benchmark",
    }
    assert authoritative_ids <= set(version_a)
    assert all(variable_id.endswith("_benchmark") for variable_id in authoritative_ids)

    legacy_ids = {
        "acs_implied_aggregate_mortgage_debt_proxy",
        "acs_implied_average_mortgage_balance_proxy",
        "acs_implied_annual_mortgage_debt_service_proxy",
        "acs_mortgage_debt_proxy_rate_assumption",
        "acs_mortgage_debt_proxy_term_years_assumption",
        "acs_mortgage_debt_proxy_debt_service_share_assumption",
        "acs_implied_aggregate_home_equity_proxy",
    }
    for variable_id in legacy_ids:
        assert general[variable_id]["active_status"] == "Legacy modeled scenario"
    for variable_id in {
        "acs_implied_aggregate_mortgage_debt_proxy",
        "acs_implied_aggregate_home_equity_proxy",
    }:
        notes = str(general[variable_id]["methodological_notes"])
        assert "Legacy Version-0 modeled scenario" in notes
        assert "Version A" in notes


def test_hud_usps_is_active_proxy_and_not_rental_inventory() -> None:
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    source = sources["hud_usps_ncwm_quarterly"]
    assert source["enabled"] is True
    assert source["auto_download"] is True
    assert source["provenance_status"] == "official_raw_archive_rebuilt"
    assert set(source["key_variables"]) == HUD_USPS_VARIABLE_IDS
    source_notes = str(source["methodology_notes"])
    assert "native quarterly frequency" in source_notes
    assert "No-stat remains separate" in source_notes
    assert "postal-ZIP-part values are modeled" in source_notes
    assert "true rental-listing inventory" in source_notes

    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    usps_variables = {
        variable_id for variable_id in variables if variable_id.startswith("usps_")
    }
    assert usps_variables == HUD_USPS_VARIABLE_IDS
    assert all(
        variables[variable_id]["active_status"] == "Active"
        for variable_id in usps_variables
    )
    assert all(
        variables[variable_id]["final_frequency"] == "quarterly"
        for variable_id in usps_variables
    )

    rate = variables["usps_residential_vacancy_rate"]
    assert rate["unit"] == "ratio"
    assert rate["is_proxy"] is True
    assert "not true rental-listing inventory" in rate["methodological_notes"]
    assert "NO_STAT" not in rate["original_field"]

    configured = load_config(ROOT)
    assert "hud_usps_ncwm_quarterly" in {
        source_config.source_id for source_config in configured.selected_sources()
    }
    assert (ROOT / "docs/hud_usps_residential_vacancy_proxy.md").is_file()
    source_manifest = pd.read_csv(
        ROOT / "metadata/hud_usps_source_manifest.csv", dtype=str
    )
    assert len(source_manifest) == 75
    assert source_manifest["artifact_role"].value_counts().to_dict() == {
        "ncwm_standardized_2020": 73,
        "tract_zip_crosswalk": 1,
        "county_fips_reference_2020": 1,
    }
    assert (ROOT / "metadata/hud_usps_quarantine_manifest.csv").is_file()

    configured_source_text = (ROOT / "config/sources.yaml").read_text(
        encoding="utf-8"
    ).lower()
    assert "rentcast" not in configured_source_text
    assert "rental_listing_inventory_monthly" not in variables


def test_historical_acs_sources_use_only_the_official_api_and_require_a_key() -> None:
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    for source_id, table_id in {
        "census_acs_5year_b05002": "B05002",
        "census_acs_5year_b07001": "B07001",
        "census_acs_5year_b11001": "B11001",
        "census_acs_5year_b19013": "B19013",
        "census_acs_5year_b25001": "B25001",
        "census_acs_5year_b25002": "B25002",
        "census_acs_5year_b25003": "B25003",
        "census_acs_5year_b25004": "B25004",
        "census_acs_5year_b25032": "B25032",
        "census_acs_5year_b25064": "B25064",
        "census_acs_5year_b25077": "B25077",
        "census_acs_5year_b25081": "B25081",
        "census_acs_5year_b25106": "B25106",
        "census_acs_5year_b25127": "B25127",
    }.items():
        source = sources[source_id]
        assert source["provider"] == "U.S. Census Bureau"
        assert source["enabled"] is True
        assert source["auto_download"] is True
        assert source["file_format"] == "json"
        assert source["access_method"] == "official_census_api"
        assert source["source_page_url"] == (
            "https://www.census.gov/data/developers/data-sets/acs-5year.html"
        )
        assert source["download_url"] == "https://api.census.gov/data/"
        assert source["selected_start_date"] == "2010-01-01"
        assert source["selected_end_date"] == "2024-12-31"
        assert table_id in source["dataset_name"]
        assert "2010-2024" in source["dataset_name"]
        assert "CENSUS_API_KEY" in source["revision_notes"]
        assert source["geography_levels"] == ["county", "zcta"]


def test_acs_migration_supplement_contract_uses_documented_direct_cells() -> None:
    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    expected = {
        "acs_foreign_born_population": (
            "census_acs_5year_b05002",
            "B05002_013E",
        ),
        "acs_foreign_born_population_moe": (
            "census_acs_5year_b05002",
            "B05002_013M",
        ),
        "acs_moved_from_abroad_past_year": (
            "census_acs_5year_b07001",
            "B07001_081E",
        ),
        "acs_moved_from_abroad_past_year_moe": (
            "census_acs_5year_b07001",
            "B07001_081M",
        ),
    }
    for variable_id, (source_id, original_field) in expected.items():
        variable = variables[variable_id]
        assert variable["original_field"] == original_field
        assert variable["unit"] == "persons"
        assert variable["native_frequency"] == "annual_5_year_estimate"
        assert variable["final_frequency"] == "annual"
        assert variable["geography"] == "county|zcta"
        assert variable["is_observed"] is True
        assert variable["is_constructed"] is False
        assert variable_id in sources[source_id]["key_variables"]

    moved_notes = variables["acs_moved_from_abroad_past_year"][
        "methodological_notes"
    ]
    assert "gross" in moved_notes
    assert "neither net international migration" in moved_notes
    foreign_notes = variables["acs_foreign_born_population"][
        "methodological_notes"
    ]
    assert "population-stock" in foreign_notes
    assert "not a migration flow" in foreign_notes


def test_noncontrolled_acs_contract_is_annual_bounded_and_policy_safe() -> None:
    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")

    b25032_estimates = {
        "acs_b25032_renter_total",
        "acs_b25032_renter_1_detached",
        "acs_b25032_renter_1_attached",
        "acs_b25032_renter_2_units",
        "acs_b25032_renter_3_to_4_units",
        "acs_b25032_renter_5_to_9_units",
        "acs_b25032_renter_10_to_19_units",
        "acs_b25032_renter_20_to_49_units",
        "acs_b25032_renter_50_plus_units",
        "acs_b25032_renter_mobile_home",
        "acs_b25032_renter_boat_rv_other",
    }
    structures = {
        "total",
        "1_unit",
        "2_to_4_units",
        "5_to_19_units",
        "20_to_49_units",
        "50_plus_units",
        "mobile_other",
    }
    b25127_estimates = {
        "acs_b25127_renter_total",
        *{
            f"acs_b25127_renter_{band}_{structure}"
            for band in ("recent_lower_bound", "cutoff_mixed")
            for structure in structures
        },
    }
    direct_ids = (
        b25032_estimates
        | {f"{variable_id}_moe" for variable_id in b25032_estimates}
        | b25127_estimates
        | {f"{variable_id}_moe" for variable_id in b25127_estimates}
    )
    derived_ids = {
        "acs_renter_occupied_1_unit_share",
        "acs_renter_occupied_2_to_4_units_share",
        "acs_renter_occupied_5_plus_units_share",
        "acs_renter_occupied_mobile_other_share",
        "acs_renter_recent_construction_share_lower_bound",
        "acs_renter_recent_construction_share_upper_bound",
        "noncontrolled_exposure_share_structural_proxy_lower_bound",
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    }
    assert len(direct_ids) == 52
    assert len(derived_ids) == 8
    assert direct_ids | derived_ids <= set(variables)
    assert "noncontrolled_exposure_share" not in variables

    for variable_id in direct_ids:
        variable = variables[variable_id]
        assert variable["final_frequency"] == "annual"
        assert variable["geography"] == "county|zcta"
        assert variable["is_observed"] is True
        assert variable["is_constructed"] is False
        assert variable["active_status"] == "Active"
    for variable_id in derived_ids:
        variable = variables[variable_id]
        assert variable["final_frequency"] == "annual"
        assert variable["geography"] == "county|zcta"
        assert variable["is_observed"] is False
        assert variable["is_constructed"] is True
        assert variable["active_status"] == "Active"

    upper = variables[
        "noncontrolled_exposure_share_structural_proxy_upper_bound"
    ]
    assert "min(1" in upper["original_field"]
    assert "acs_b25032_renter_1_detached" in upper["original_field"]
    assert "acs_b25127_renter_recent_lower_bound_1_unit" in upper[
        "original_field"
    ]
    assert "when observation year is 2020 or later" in upper["original_field"]
    assert "not proof" in upper["methodological_notes"]
    for variable_id in {
        "noncontrolled_exposure_share_structural_proxy_lower_bound",
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    }:
        variable = variables[variable_id]
        assert variable["market_segment"] == "Renter"
        assert variable["variable_role"] == "Outcome"
        assert variable["data_domain"] == "rental_market"

    assert direct_ids | {
        "acs_renter_occupied_1_unit_share",
        "acs_renter_occupied_2_to_4_units_share",
        "acs_renter_occupied_5_plus_units_share",
        "acs_renter_occupied_mobile_other_share",
        "acs_renter_recent_construction_share_lower_bound",
        "acs_renter_recent_construction_share_upper_bound",
        "noncontrolled_exposure_share_structural_proxy_lower_bound",
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    } <= (
        set(sources["census_acs_5year_b25032"]["key_variables"])
        | set(sources["census_acs_5year_b25127"]["key_variables"])
    )

    pipeline = _yaml("config/pipeline.yaml")["noncontrolled_rental_exposure"]
    assert pipeline["source_table_ids"] == ["B25032", "B25127"]
    assert pipeline["publication_floor"] == "2020-12-31"
    assert pipeline["exact_policy_adjustment_enabled"] is False
    assert pipeline["policy_crosswalk_status"] == (
        "scaffold_incomplete_not_applied"
    )

    crosswalk = pd.read_csv(
        ROOT / "config" / "rent_control_policy_crosswalk.csv", dtype=str
    ).fillna("")
    assert set(crosswalk["policy_id"]) == {
        "CA_AB1482_CIV_1947_12",
        "CITY_LA_RSO",
        "LA_COUNTY_RSTPO",
    }
    assert crosswalk["official_source_url"].str.startswith("https://").all()
    assert crosswalk["coding_status"].eq("scaffold_only_not_applied").all()
    assert not crosswalk.loc[
        crosswalk["policy_id"].eq("LA_COUNTY_RSTPO"), "repo_geo_id"
    ].any()
    assert (ROOT / "docs" / "noncontrolled_rental_exposure.md").is_file()


def test_acs_rental_availability_is_annual_and_separate_from_listings() -> None:
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    source = sources["census_acs_5year_b25004"]
    assert source["destination_clean_table"] == "clean.census_acs_5year_b25004"
    assert source["destination_analytical_table"] == (
        "analytics.county_annual_controls"
    )
    assert "not historical monthly active rental listings" in source[
        "methodology_notes"
    ]

    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    for variable_id in {
        "acs_for_rent_vacant_units",
        "acs_for_rent_vacant_units_moe",
        "acs_rental_availability_rate",
    }:
        assert variables[variable_id]["active_status"] == "Active"
        assert variables[variable_id]["final_frequency"] == "annual"
    rate = variables["acs_rental_availability_rate"]
    assert rate["unit"] == "ratio"
    assert rate["geography"] == "county"
    assert rate["is_constructed"] is True
    assert rate["is_proxy"] is True
    assert "rental-listing inventory" in rate["methodological_notes"]


def test_acs_zcta_annual_extensions_are_source_safe_and_not_postal_zip() -> None:
    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")

    ratio = variables["zcta_annual_price_to_income_acs"]
    assert ratio["final_frequency"] == "annual"
    assert ratio["geography"] == "zcta"
    assert ratio["is_observed"] is False
    assert ratio["is_constructed"] is True
    assert ratio["is_proxy"] is False
    assert "B25077_001E / B19013_001E" == ratio["original_field"]
    assert "not a USPS postal-ZIP observation" in ratio["methodological_notes"]

    change = variables["zcta_renter_occupied_units_vintage_change_annual"]
    assert change["final_frequency"] == "annual"
    assert change["geography"] == "zcta"
    assert change["is_observed"] is False
    assert change["is_constructed"] is True
    assert change["is_proxy"] is True
    assert "not an observed one-year or quarterly flow" in change[
        "methodological_notes"
    ]
    assert "never relabeled as a postal ZIP" in change["methodological_notes"]

    assert "zcta_annual_price_to_income_acs" in sources[
        "census_acs_5year_b19013"
    ]["key_variables"]
    assert "zcta_annual_price_to_income_acs" in sources[
        "census_acs_5year_b25077"
    ]["key_variables"]
    assert "zcta_renter_occupied_units_vintage_change_annual" in sources[
        "census_acs_5year_b25003"
    ]["key_variables"]

    harmonization = (ROOT / "src/harmonization/build.py").read_text(
        encoding="utf-8"
    )
    database = (ROOT / "src/database/build.py").read_text(encoding="utf-8")
    exports = (ROOT / "src/reporting/export.py").read_text(encoding="utf-8")
    assert "zcta_annual_controls.parquet" in harmonization
    assert "zcta_annual_controls.parquet" in database
    assert "zcta_annual_controls.parquet" in exports
    assert "analytics.zcta_annual_controls" in database
    assert "la_zcta_annual_controls.csv" in exports


def test_new_official_annual_sources_and_outputs_are_source_limited() -> None:
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    variables = _by_id(_yaml("config/variables.yaml")["variables"], "variable_id")

    bps = sources["census_bps_county_annual_final"]
    assert bps["provider"] == "U.S. Census Bureau"
    assert bps["source_page_url"] == "https://www2.census.gov/econ/bps/County/"
    assert bps["download_url"] == (
        "https://www2.census.gov/econ/bps/County/co2025a.txt"
    )
    assert bps["native_frequency"] == "annual"
    assert bps["geography_levels"] == ["county"]
    assert bps["file_format"] == "txt"
    assert bps["access_method"] == "official_public_download"
    assert bps["selected_start_date"] == "1990-01-01"
    assert bps["selected_end_date"] == "2025-12-31"
    assert bps["key_variables"] == ["permits_1_unit_annual_final"]
    assert bps["destination_clean_table"] == (
        "clean.census_bps_county_annual_final"
    )
    assert bps["destination_analytical_table"] == "analytics.county_annual_controls"
    assert "no credential is required" in bps["license_or_use_notes"]
    assert "Never relabel" in bps["revision_notes"]

    annual_permits = variables["permits_1_unit_annual_final"]
    assert annual_permits["provider"] == "U.S. Census Bureau"
    assert annual_permits["final_frequency"] == "annual"
    assert annual_permits["geography"] == "county"
    assert annual_permits["is_observed"] is True
    assert annual_permits["is_constructed"] is False
    assert annual_permits["active_status"] == "Active"
    assert "distinct from monthly" in annual_permits["methodological_notes"]

    doi = sources["ca_doi_residential_property_part_i"]
    assert doi["provider"] == "California Department of Insurance"
    assert doi["source_page_url"] == (
        "https://www.insurance.ca.gov/0400-news/0200-studies-reports/"
        "0250-homeowners-study/"
    )
    assert doi["download_url"] == (
        "https://www.insurance.ca.gov/0400-news/0200-studies-reports/"
        "0250-homeowners-study/upload/A-Part-I-Data-Summary.xlsx"
    )
    assert doi["native_frequency"] == "annual"
    assert doi["geography_levels"] == ["state"]
    assert doi["file_format"] == "xlsx"
    assert set(doi["key_variables"]) == {
        "ca_homeowners_written_premium",
        "ca_homeowners_written_exposures",
        "ca_homeowners_avg_written_premium",
    }
    assert "Homeowners Multi-Peril" in doi["methodology_notes"]
    assert "not Los Angeles County" in doi["methodology_notes"]
    assert "monthly insurance data" in doi["methodology_notes"]

    for variable_id in {
        "ca_homeowners_written_premium",
        "ca_homeowners_written_exposures",
        "ca_homeowners_avg_written_premium",
        "ca_homeowners_avg_written_premium_yoy",
    }:
        variable = variables[variable_id]
        assert variable["final_frequency"] == "annual"
        assert variable["geography"] == "state"
        assert variable["source_geography"] == "California"
        assert variable["active_status"] == "Active"

    monthly_insurance = variables[
        "ca_homeowners_avg_written_premium_monthly_control"
    ]
    assert monthly_insurance["native_frequency"] == "annual"
    assert monthly_insurance["final_frequency"] == "monthly"
    assert monthly_insurance["geography"] == "state"
    assert monthly_insurance["source_geography"] == "California"
    assert monthly_insurance["is_observed"] is False
    assert monthly_insurance["is_constructed"] is True
    assert monthly_insurance["active_status"] == "Active"

    bps_monthly = {
        "census_bps_county": (
            "https://www2.census.gov/econ/bps/County/co2606c.txt"
        ),
        "census_bps_state": (
            "https://www2.census.gov/econ/bps/State/st2606c.txt"
        ),
    }
    for source_id, download_url in bps_monthly.items():
        source = sources[source_id]
        assert source["download_url"] == download_url
        assert source["selected_end_date"] == "2026-06-30"
        assert "currently June 2026" in source["revision_notes"]

    irs = sources["irs_soi_county_migration"]
    assert "enhanced matching process" in irs["revision_notes"]
    assert "provider-methodology-break" in irs["revision_notes"]

    pit = sources["hud_pit_coc_2007_2025"]
    assert pit["provider"] == "U.S. Department of Housing and Urban Development"
    assert pit["source_page_url"] == (
        "https://www.huduser.gov/portal/datasets/ahar/"
        "2025-ahar-part-1-pit-estimates-of-homelessness-in-the-us.html"
    )
    assert pit["download_url"] == (
        "https://www.huduser.gov/portal/sites/default/files/xls/"
        "2007-2025-PIT-Counts-by-CoC.xlsb"
    )
    assert pit["native_frequency"] == "annual_point_in_time"
    assert pit["geography_levels"] == [
        "continuum_of_care",
        "explicit_coc_composite",
    ]
    assert pit["file_format"] == "xlsb"
    assert pit["access_method"] == "official_download"
    assert pit["selected_start_date"] == "2007-01-01"
    assert pit["selected_end_date"] == "2025-12-31"
    assert pit["destination_clean_table"] == "clean.hud_pit_coc_2007_2025"
    assert pit["destination_analytical_table"] == "analytics.coc_annual_controls"
    assert set(pit["key_variables"]) == {
        "hud_pit_overall_homeless_annual",
        "hud_pit_sheltered_homeless_annual",
        "hud_pit_unsheltered_homeless_annual",
    }
    assert "never Los Angeles County" in pit["methodology_notes"]
    assert "mixed 2021 row types" in pit["methodology_notes"]
    assert "covers 2007-2025" in pit["revision_notes"]

    for variable_id in pit["key_variables"]:
        variable = variables[variable_id]
        assert variable["final_frequency"] == "annual"
        assert variable["geography"] == (
            "continuum_of_care|explicit_coc_composite"
        )
        assert variable["source_geography"] == "Continuum of Care"
        assert variable["active_status"] == "Active"
        assert (
            "Neither a native CoC nor the explicit four-CoC project composite "
            "is relabeled Los Angeles County"
            in variable["methodological_notes"]
        )

    harmonization = (ROOT / "src/harmonization/build.py").read_text(
        encoding="utf-8"
    )
    database = (ROOT / "src/database/build.py").read_text(encoding="utf-8")
    exports = (ROOT / "src/reporting/export.py").read_text(encoding="utf-8")
    for parquet_name in (
        "state_annual_controls.parquet",
        "state_monthly_controls.parquet",
        "coc_annual_controls.parquet",
    ):
        assert parquet_name in harmonization
        assert parquet_name in database
        assert parquet_name in exports
    for table_name in (
        "analytics.state_annual_controls",
        "analytics.state_monthly_controls",
        "analytics.coc_annual_controls",
    ):
        assert table_name in database
    assert "la_state_annual_controls.csv" in exports
    assert "la_state_monthly_controls.csv" in exports
    assert "ca_coc_annual_controls.csv" in exports


def test_readme_records_the_consolidated_contract_and_refresh_examples() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "558 unique variables",
        "375 general variables",
        "67 Version A variables",
        "58 Version B variables",
        "58 Version C variables",
        "69 unique provider source IDs",
        "legacy modeled scenario",
        "all-residential administrative vacancy proxy",
        "not true rental-listing inventory",
        "(docs/hud_usps_residential_vacancy_proxy.md)",
        "(metadata/hud_usps_source_manifest.csv)",
        "(metadata/hud_usps_quarantine_manifest.csv)",
        "(docs/noncontrolled_rental_exposure.md)",
    ):
        assert phrase in readme

    for source_id in REQUIRED_REFRESH_SOURCE_IDS:
        assert (
            "python run_pipeline.py --stage download --source "
            f"{source_id} --force-download"
        ) in readme
