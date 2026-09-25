"""Formula-specific transformation logging without changing legacy sources."""

import pandas as pd
import pytest

from src.cleaning.runner import MACRO_CLEANING_MODULES, _macro_transformation_details


def _definitions():
    return (
        {
            "variable_id": "ces_construction_employment_nsa_yoy_pct",
            "transformation": "100 * (employment_t / employment_exact_t_minus_12_months - 1).",
            "methodological_notes": "Exact calendar lag; no interpolation; retain NSA geography.",
        },
        {
            "variable_id": "ces_information_employment_nsa_yoy_pct",
            "transformation": "100 * (employment_t / employment_exact_t_minus_12_months - 1).",
            "methodological_notes": "Missing current or lagged levels remain missing.",
        },
    )


def _record():
    return {
        "source_filename": "official_api_response.json",
        "checksum_sha256": "a" * 64,
        "retrieved_at": "2026-09-15T12:00:00Z",
        "provider_last_updated": "2026-08-21",
    }


def test_macro_group_lists_each_variable_formula_and_method_once():
    definitions = _definitions()
    group = pd.DataFrame({
        "variable_id": [definitions[1]["variable_id"], definitions[0]["variable_id"], definitions[0]["variable_id"]],
        **{key: [value] * 3 for key, value in _record().items()},
    })
    details = _macro_transformation_details(
        "bls_ces_ca_la_industry_earnings", group, definitions, _record()
    )
    assert details["code_module"] == "src.cleaning.macro_labor"
    for definition in definitions:
        assert details["description"].count(definition["variable_id"]) == 1
        assert definition["transformation"] in details["description"]
        assert definition["methodological_notes"] in details["description"]
    assert details["description"].startswith(definitions[0]["variable_id"])
    assert {key: details[key] for key in _record()} == _record()


@pytest.mark.parametrize("source_id, module", list(MACRO_CLEANING_MODULES.items()))
def test_all_nine_macro_sources_log_actual_importable_cleaner_module(source_id, module):
    definition = {"variable_id": "fixture_variable", "transformation": "Retain provider level."}
    group = pd.DataFrame({"variable_id": ["fixture_variable"]})
    details = _macro_transformation_details(source_id, group, (definition,), _record())
    assert len(MACRO_CLEANING_MODULES) == 9
    assert details["code_module"] == module
    assert module in {
        "src.cleaning.macro_rates", "src.cleaning.macro_labor",
        "src.cleaning.macro_bea_claims",
    }
    assert details["source_filename"] == _record()["source_filename"]
    assert details["checksum_sha256"] == _record()["checksum_sha256"]
    assert details["retrieved_at"] == _record()["retrieved_at"]


def test_cleaned_bea_release_date_takes_precedence_over_manifest_fallback():
    group = pd.DataFrame({
        "variable_id": ["gdp"],
        "provider_last_updated": ["2026-06-25"],
    })
    details = _macro_transformation_details(
        "bea_ca_real_gdp", group,
        ({"variable_id": "gdp", "transformation": "SQGDP9 line 1 California."},),
        _record(),
    )
    assert details["provider_last_updated"] == "2026-06-25"


def test_missing_lineage_stays_empty_and_source_updated_fallback_supported():
    group = pd.DataFrame({"variable_id": ["claims"], "provider_last_updated": [pd.NA]})
    definitions = ({"variable_id": "claims", "transformation": "Mean of all expected weekly observations."},)
    details = _macro_transformation_details(
        "dol_ca_ui_claims", group, definitions, {"source_last_updated": "2026-09-10"}
    )
    assert details["provider_last_updated"] == "2026-09-10"
    assert details["checksum_sha256"] == ""
    assert details["retrieved_at"] == ""
    assert details["source_filename"] == ""


@pytest.mark.parametrize("definitions, message", [
    ((), "no dictionary entry"),
    (({"variable_id": "missing_formula"},), "no explicit transformation"),
])
def test_missing_macro_dictionary_formula_fails(definitions, message):
    with pytest.raises(ValueError, match=message):
        _macro_transformation_details(
            "fred_fedfunds", pd.DataFrame({"variable_id": ["missing_formula"]}),
            definitions, _record(),
        )


def test_legacy_source_receives_no_overrides_or_additional_validation():
    assert _macro_transformation_details("zillow_county_zhvi", pd.DataFrame(), (), {}) == {}
