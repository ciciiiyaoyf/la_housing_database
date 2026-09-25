"""Geography and frequency contracts for the sparse macro control panel."""

import numpy as np
import pandas as pd
import pytest

from src.features.macro_financial import (
    MACRO_QUARTERLY_GDP_IDS, MONTHLY_VARIABLE_CONTRACTS, MONTHLY_VARIABLE_IDS,
    macro_delivery_role, macro_monthly_panel,
)


def observation(source, variable, value, *, geo="US", frequency="monthly", date="2026-01-31"):
    return {
        "source_id": source, "variable_id": variable, "value": value,
        "geo_id": geo, "final_frequency": frequency,
        "observation_date": pd.Timestamp(date),
    }


def test_macro_panel_does_not_broadcast_national_rates_or_state_controls():
    data = pd.DataFrame([
        observation("fred_sofr", "us_sofr_monthly_average", 4.5),
        observation("dol_ca_ui_claims", "ca_initial_claims_monthly_average", 20, geo="STATE_06"),
        observation("bls_ces_ca_la_industry_earnings", "ces_construction_employment_nsa", 100, geo="METRODIV_31084"),
    ])
    result = macro_monthly_panel(data).set_index("geo_id")
    assert set(result.index) == {"US", "STATE_06", "METRODIV_31084"}
    assert result.loc["US", "us_sofr_monthly_average"] == 4.5
    assert result.loc[["STATE_06", "METRODIV_31084"], "us_sofr_monthly_average"].isna().all()
    assert result.loc[["US", "METRODIV_31084"], "ca_initial_claims_monthly_average"].isna().all()
    assert result.loc[["US", "STATE_06"], "ces_construction_employment_nsa"].isna().all()
    assert result.loc["STATE_06", "geography_type"] == "state"
    assert result.loc["METRODIV_31084", "geography_type"] == "metropolitan_division"
    assert result.loc["METRODIV_31084", "state_fips"] == "06"
    assert pd.isna(result.loc["US", "state_fips"])


def test_macro_panel_contains_monthly_outputs_only_and_no_gdp_expansion():
    data = pd.DataFrame([
        observation("fred_sofr", "us_sofr_monthly_average", 4.5),
        observation("fred_sofr", "us_sofr_daily", 4.6, frequency="daily", date="2026-01-02"),
        observation("dol_ca_ui_claims", "ca_initial_claims_weekly", 20, geo="STATE_06", frequency="weekly", date="2026-01-03"),
        observation("bea_ca_real_gdp", "bea_ca_real_gdp_quarterly", 100, geo="STATE_06", frequency="quarterly", date="2026-03-31"),
        observation("zillow_county_zhvi", "zhvi", 100, geo="STATE_06"),
    ])
    result = macro_monthly_panel(data)
    assert len(result) == 1
    assert result.geo_id.tolist() == ["US"]
    assert set(result.columns) == {
        "date", "geo_id", "geo_name", "geography_type", "state_fips", "us_sofr_monthly_average",
    }


def test_macro_panel_preserves_explicit_nulls_and_does_not_fill_dates():
    data = pd.DataFrame([
        observation("fred_sofr", "us_sofr_monthly_average", 4.5),
        observation("fred_sofr", "us_sofr_monthly_average", np.nan, date="2026-03-31"),
    ])
    unchanged = data.copy(deep=True)
    result = macro_monthly_panel(data)
    assert result.date.tolist() == [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-03-31")]
    assert pd.isna(result.iloc[1].us_sofr_monthly_average)
    pd.testing.assert_frame_equal(data, unchanged)


def test_macro_panel_rejects_unapproved_geography():
    data = pd.DataFrame([
        observation("bls_ces_ca_la_industry_earnings", "ces_construction_employment_nsa", 100, geo="COUNTY_06037"),
    ])
    with pytest.raises(ValueError, match="Unapproved macro geography"):
        macro_monthly_panel(data)


def test_macro_panel_rejects_duplicate_keys_instead_of_averaging():
    row = observation("fred_sofr", "us_sofr_monthly_average", 4.5)
    data = pd.DataFrame([row, {**row, "value": 5.0}])
    with pytest.raises(ValueError, match="one selected observation"):
        macro_monthly_panel(data)


def test_macro_panel_has_stable_empty_schema_when_no_monthly_sources_exist():
    data = pd.DataFrame([
        observation("bea_ca_real_gdp", "bea_ca_real_gdp_quarterly", 100, geo="STATE_06", frequency="quarterly", date="2026-03-31"),
    ])
    result = macro_monthly_panel(data)
    assert result.empty
    assert result.columns.tolist() == ["date", "geo_id", "geo_name", "geography_type", "state_fips"]


def test_explicit_contract_distinguishes_fields_series_and_required_quarterly_gdp():
    assert len(MONTHLY_VARIABLE_IDS) == 27
    assert sum(item.role == "monthly_substantive" for item in MONTHLY_VARIABLE_CONTRACTS.values()) == 20
    assert sum(item.role == "monthly_coverage_diagnostic" for item in MONTHLY_VARIABLE_CONTRACTS.values()) == 7
    assert sum(len(item.geo_ids) for item in MONTHLY_VARIABLE_CONTRACTS.values()) == 39
    assert all(macro_delivery_role(variable) == "quarterly_gdp_required" for variable in MACRO_QUARTERLY_GDP_IDS)
    assert macro_delivery_role("us_sofr_daily") == "native_daily_audit"
    assert macro_delivery_role("ca_initial_claims_weekly") == "native_weekly_audit"
    assert macro_delivery_role("zhvi") == "not_in_macro_request"


@pytest.mark.parametrize("source,variable,geo", [
    ("fred_sofr", "us_sofr_daily", "US"),
    ("dol_ca_ui_claims", "ca_initial_claims_weekly", "STATE_06"),
    ("bea_ca_real_gdp", "bea_ca_real_gdp_quarterly", "STATE_06"),
    ("fred_sofr", "invented_monthly_control", "US"),
])
def test_native_gdp_or_unknown_fields_cannot_enter_by_monthly_mislabel(source, variable, geo):
    data = pd.DataFrame([observation(source, variable, 4, geo=geo)])
    with pytest.raises(ValueError, match="Unapproved monthly macro variable"):
        macro_monthly_panel(data)


@pytest.mark.parametrize("source", ["fred_dgs2", "unrelated_source"])
def test_known_monthly_variable_cannot_come_from_wrong_source(source):
    data = pd.DataFrame([observation(source, "us_sofr_monthly_average", 4)])
    with pytest.raises(ValueError, match="Unapproved macro source"):
        macro_monthly_panel(data)


@pytest.mark.parametrize("geo", ["STATE_06", "METRODIV_31084"])
def test_national_rates_cannot_be_relabelled_as_state_or_local(geo):
    data = pd.DataFrame([observation("fred_sofr", "us_sofr_monthly_average", 4, geo=geo)])
    with pytest.raises(ValueError, match="Unapproved macro geography"):
        macro_monthly_panel(data)


@pytest.mark.parametrize("date", ["2026-01-01", "2026-01-31 12:00:00", "not-a-date", None])
def test_monthly_macro_dates_must_be_month_end(date):
    row = observation("fred_sofr", "us_sofr_monthly_average", 4)
    row["observation_date"] = date
    with pytest.raises(ValueError, match="valid month ends"):
        macro_monthly_panel(pd.DataFrame([row]))


def test_monthly_variable_cannot_silently_disappear_with_wrong_frequency():
    data = pd.DataFrame([observation("fred_sofr", "us_sofr_monthly_average", 4, frequency="daily")])
    with pytest.raises(ValueError, match="nonmonthly frequency"):
        macro_monthly_panel(data)


def test_empty_input_has_identity_schema():
    assert macro_monthly_panel(pd.DataFrame()).columns.tolist() == [
        "date", "geo_id", "geo_name", "geography_type", "state_fips",
    ]
