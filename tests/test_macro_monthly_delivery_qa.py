"""Monthly-delivery corruption tests, independent of source formula QA."""

import numpy as np
import pandas as pd
import pytest

from src.features.macro_financial import (
    MONTHLY_VARIABLE_CONTRACTS, macro_monthly_panel,
)
from src.validation.macro_financial import macro_monthly_panel_failures


def complete_facts():
    return pd.DataFrame([
        {"source_id": contract.source_id, "variable_id": variable, "geo_id": geo,
         "observation_date": pd.Timestamp("2026-01-31"), "final_frequency": "monthly", "value": 1.0}
        for variable, contract in MONTHLY_VARIABLE_CONTRACTS.items()
        for geo in sorted(contract.geo_ids)
    ])


def reasons(fact, panel, **kwargs):
    return set(macro_monthly_panel_failures(fact, panel, **kwargs).reason)


def test_complete_contract_and_sparse_default_preserve_inputs():
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    unchanged_fact, unchanged_panel = fact.copy(deep=True), panel.copy(deep=True)
    assert macro_monthly_panel_failures(fact, panel, require_complete=True).empty
    pd.testing.assert_frame_equal(fact, unchanged_fact)
    pd.testing.assert_frame_equal(panel, unchanged_panel)
    sparse = fact.iloc[:1].copy()
    sparse_panel = macro_monthly_panel(sparse)
    assert macro_monthly_panel_failures(sparse, sparse_panel).empty
    assert "monthly_contract_missing_nonnull_fact_series" in reasons(sparse, sparse_panel, require_complete=True)
    assert "monthly_contract_missing_nonnull_panel_series" in reasons(sparse, sparse_panel, require_complete=True)


def test_empty_contract_is_allowed_only_when_not_required():
    fact = pd.DataFrame()
    panel = macro_monthly_panel(fact)
    assert macro_monthly_panel_failures(fact, panel).empty
    assert "monthly_panel_missing_column" in reasons(fact, panel, require_complete=True)
    assert "monthly_contract_missing_nonnull_fact_series" in reasons(fact, panel, require_complete=True)
    assert "monthly_panel_missing_identity_columns" in reasons(fact, pd.DataFrame())


@pytest.mark.parametrize("value", [0.0, 9.0, np.nan])
def test_changed_value_and_null_mask_fail(value):
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    panel.loc[panel.geo_id.eq("US"), "us_sofr_monthly_average"] = value
    assert "monthly_panel_value_or_null_mask_mismatch" in reasons(fact, panel)


def test_explicit_null_is_retained_and_may_not_be_filled():
    fact = complete_facts()
    fact.loc[fact.variable_id.eq("ca_initial_claims_monthly_average"), "value"] = np.nan
    panel = macro_monthly_panel(fact)
    assert macro_monthly_panel_failures(fact, panel).empty
    panel.loc[panel.geo_id.eq("STATE_06"), "ca_initial_claims_monthly_average"] = 0
    assert "monthly_panel_value_or_null_mask_mismatch" in reasons(fact, panel)


def test_national_rates_cannot_be_broadcast_to_other_geographies():
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    panel["us_sofr_monthly_average"] = 1.0
    found = reasons(fact, panel)
    assert "monthly_panel_unauthorized_value_geography" in found
    assert "monthly_panel_value_or_null_mask_mismatch" in found


@pytest.mark.parametrize("variable", ["bea_ca_real_gdp_quarterly", "us_sofr_daily", "ca_initial_claims_weekly", "invented"])
def test_no_native_or_gdp_or_unknown_output_columns(variable):
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    panel[variable] = np.nan
    assert "monthly_panel_unexpected_column" in reasons(fact, panel)


@pytest.mark.parametrize("column,value,reason", [
    ("source_id", "fred_dgs2", "monthly_fact_unauthorized_source"),
    ("geo_id", "STATE_06", "monthly_fact_unauthorized_geography"),
    ("final_frequency", "daily", "monthly_fact_invalid_frequency"),
    ("observation_date", pd.Timestamp("2026-01-01"), "monthly_fact_invalid_month_end"),
    ("value", np.inf, "monthly_fact_invalid_numeric_value"),
])
def test_invalid_fact_contract_cannot_be_laundered_through_valid_panel(column, value, reason):
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    fact.loc[fact.variable_id.eq("us_sofr_monthly_average"), column] = value
    assert reason in reasons(fact, panel)


def test_mislabelled_gdp_is_detected_even_when_not_in_output():
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    extra = fact.iloc[:1].copy()
    extra["source_id"], extra["variable_id"], extra["geo_id"] = "bea_ca_real_gdp", "bea_ca_real_gdp_quarterly", "STATE_06"
    assert "unauthorized_monthly_fact_variable" in reasons(pd.concat([fact, extra], ignore_index=True), panel)
    extra["final_frequency"] = "quarterly"
    assert macro_monthly_panel_failures(pd.concat([fact, extra], ignore_index=True), panel, require_complete=True).empty


@pytest.mark.parametrize("column,value,reason", [
    ("date", pd.Timestamp("2026-01-01"), "monthly_panel_invalid_month_end"),
    ("date", pd.Timestamp("2026-01-31 01:00"), "monthly_panel_invalid_month_end"),
    ("geo_id", "COUNTY_06037", "monthly_panel_unauthorized_geography"),
    ("geo_name", "Los Angeles County", "monthly_panel_geography_metadata_mismatch"),
    ("geography_type", "county", "monthly_panel_geography_metadata_mismatch"),
    ("state_fips", "6", "monthly_panel_geography_metadata_mismatch"),
])
def test_panel_identity_corruptions_fail(column, value, reason):
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    panel.loc[panel.geo_id.eq("STATE_06"), column] = value
    assert reason in reasons(fact, panel)


def test_missing_and_extra_rows_and_columns_fail():
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    assert "monthly_panel_missing_row" in reasons(fact, panel.iloc[1:])
    extra = panel.iloc[:1].copy()
    extra["date"] = pd.Timestamp("2026-02-28")
    assert "monthly_panel_unexpected_row" in reasons(fact, pd.concat([panel, extra], ignore_index=True))
    assert "monthly_panel_missing_column" in reasons(fact, panel.drop(columns="us_sofr_monthly_average"))
    assert "monthly_panel_missing_identity_columns" in reasons(fact, panel.drop(columns="geo_name"))


def test_duplicate_keys_columns_and_malformed_values_fail():
    fact = complete_facts()
    panel = macro_monthly_panel(fact)
    assert "monthly_fact_duplicate_key" in reasons(pd.concat([fact, fact.iloc[:1]]), panel)
    assert "monthly_panel_duplicate_key" in reasons(fact, pd.concat([panel, panel.iloc[:1]]))
    assert "monthly_panel_duplicate_columns" in reasons(fact, pd.concat([panel, panel[["geo_id"]]], axis=1))
    for value in [np.inf, "not numeric"]:
        corrupt = panel.copy()
        corrupt["us_sofr_monthly_average"] = value
        assert "monthly_panel_invalid_numeric_value" in reasons(fact, corrupt)


def test_entire_authorized_geographic_series_must_have_output_when_required():
    fact = complete_facts()
    fact.loc[fact.variable_id.eq("ces_construction_employment_nsa") & fact.geo_id.eq("METRODIV_31084"), "value"] = np.nan
    panel = macro_monthly_panel(fact)
    assert macro_monthly_panel_failures(fact, panel).empty
    found = reasons(fact, panel, require_complete=True)
    assert "monthly_contract_missing_nonnull_fact_series" in found
    assert "monthly_contract_missing_nonnull_panel_series" in found
