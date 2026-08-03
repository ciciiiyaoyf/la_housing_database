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
    general_variable_ids = {
        str(variable["variable_id"]) for variable in general_variables
    }
    version_a_variable_ids = {
        str(variable["variable_id"]) for variable in version_a_variables
    }

    assert len(general_variables) == len(general_variable_ids) == 166
    assert len(version_a_variables) == len(version_a_variable_ids) == 58
    assert general_variable_ids.isdisjoint(version_a_variable_ids)
    assert len(general_variable_ids | version_a_variable_ids) == 224
    assert all(variable_id.endswith("_benchmark") for variable_id in version_a_variable_ids)

    loaded_variable_ids = [
        str(variable["variable_id"]) for variable in load_config(ROOT).variables
    ]
    assert len(loaded_variable_ids) == len(set(loaded_variable_ids)) == 224
    assert set(loaded_variable_ids) == general_variable_ids | version_a_variable_ids

    dictionary = pd.read_csv(ROOT / "metadata/data_dictionary.csv", dtype=str)
    assert len(dictionary) == dictionary["variable_id"].nunique() == 224
    assert set(dictionary["variable_id"]) == set(loaded_variable_ids)

    general_sources = _yaml("config/sources.yaml")["sources"]
    general_source_ids = {str(source["source_id"]) for source in general_sources}
    version_a_source_ids = set(_yaml("config/version_a_benchmark.yaml")["sources"])

    assert len(general_sources) == len(general_source_ids) == 38
    assert len(version_a_source_ids) == 9
    assert general_source_ids & version_a_source_ids == {"freddie_mac_pmms"}
    assert len(general_source_ids | version_a_source_ids) == 46


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


def test_hud_usps_is_active_restricted_and_is_not_rental_inventory() -> None:
    sources = _by_id(_yaml("config/sources.yaml")["sources"], "source_id")
    source = sources["hud_usps_ncwm_quarterly"]
    assert source["enabled"] is True
    assert source["auto_download"] is False
    assert source["provenance_status"] == "official_authorized_api"
    assert set(source["key_variables"]) == HUD_USPS_VARIABLE_IDS
    source_notes = str(source["methodology_notes"])
    assert "Do not" in source_notes
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
    assert (ROOT / "metadata/hud_usps_quarantine_manifest.csv").is_file()
    assert (ROOT / "metadata/hud_usps_official_rebuild_summary.csv").is_file()


def test_readme_records_the_consolidated_contract_and_refresh_examples() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "224 unique variables",
        "166 general variables",
        "58 Version A variables",
        "46 unique source IDs",
        "legacy modeled scenario",
        "all-residential administrative vacancy proxy",
        "not true rental-listing inventory",
        "(docs/hud_usps_residential_vacancy_proxy.md)",
        "(metadata/hud_usps_quarantine_manifest.csv)",
    ):
        assert phrase in readme

    for source_id in REQUIRED_REFRESH_SOURCE_IDS:
        assert (
            "python run_pipeline.py --stage download --source "
            f"{source_id} --force-download"
        ) in readme
