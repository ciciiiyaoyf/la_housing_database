"""The new two-geography CES contract must not relax legacy metro scope."""

import pandas as pd
import pytest

from src.validation.macro_financial import CES_BASE_IDS
from src.validation.qa import _metro_geography_failures


NEW_CES_IDS = (*CES_BASE_IDS, *(f"{base}_yoy_pct" for base in CES_BASE_IDS))


@pytest.mark.parametrize("variable", NEW_CES_IDS)
def test_all_twelve_new_ces_ids_accept_only_california_and_la_division(variable):
    fact = pd.DataFrame({
        "variable_id": variable,
        "geo_id": ["STATE_06", "METRODIV_31084"],
    })
    assert len(NEW_CES_IDS) == 12
    assert _metro_geography_failures(fact).empty


@pytest.mark.parametrize("variable", NEW_CES_IDS)
@pytest.mark.parametrize("geo", ["COUNTY_06037", "STATE_12", "METRO_31080", "METRODIV_36084", "US", pd.NA])
def test_new_ces_rejects_county_and_every_other_geography(variable, geo):
    fact = pd.DataFrame({"variable_id": [variable], "geo_id": [geo]})
    assert len(_metro_geography_failures(fact)) == 1


@pytest.mark.parametrize("variable", [
    "ces_total_nonfarm_employment_sa", "ces_unconfigured_industry",
    "cpi_all_items", "hvs_rental_vacancy_rate_la_metro",
    "fhfa_hpi_la_metropolitan_division",
])
def test_legacy_metro_contract_still_rejects_state_and_county(variable):
    # Even a new macro source label must not exempt an old/unconfigured ID.
    fact = pd.DataFrame({
        "variable_id": variable,
        "source_id": "bls_ces_ca_la_industry_earnings",
        "geo_id": ["STATE_06", "COUNTY_06037", "METRO_31080", "METRODIV_31084"],
    })
    bad = _metro_geography_failures(fact)
    assert bad.geo_id.tolist() == ["STATE_06", "COUNTY_06037"]


def test_unrelated_state_and_national_fields_are_not_reclassified_as_metro():
    fact = pd.DataFrame({
        "variable_id": ["bea_ca_real_gdp_quarterly", "us_sofr_daily", "zhvi"],
        "geo_id": ["STATE_06", "US", "COUNTY_06037"],
    })
    unchanged = fact.copy(deep=True)
    assert _metro_geography_failures(fact).empty
    pd.testing.assert_frame_equal(fact, unchanged)
