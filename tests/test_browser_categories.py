"""Picker headings preserve source metadata, available IDs, and dataset meaning."""

from __future__ import annotations

from collections import Counter

import duckdb
import pandas as pd
import pytest
import yaml

from src.common.config import ROOT, load_config
from src.reporting.browser_categories import DOMAIN_LABELS, variable_category
from src.reporting.data_browser import variable_options


def test_every_configured_domain_has_an_explicit_readable_label():
    domains = {row["data_domain"] for row in load_config().variables}
    assert domains == set(DOMAIN_LABELS)
    assert len(domains) == 18
    for domain in domains:
        category = variable_category("county-annual", {"variable_id": "example", "data_domain": domain})
        assert category.category_id == domain
        assert category.category_label and "_" not in category.category_label


@pytest.mark.parametrize("domain", [None, "", "unrecognized_domain"])
def test_missing_or_unknown_domain_remains_available_as_uncategorized(domain):
    category = variable_category("county-monthly", {"variable_id": "example", "data_domain": domain})
    assert category.category_id == "uncategorized"
    assert category.category_label == "Uncategorized"


@pytest.mark.parametrize("version,expected", [
    ("a", {"finance_payments": 7, "finance_downpayment_rates": 24, "finance_downpayment_flows": 4,
           "finance_debt": 9, "finance_equity": 13, "finance_benchmark_inputs": 10}),
    ("b", {"finance_payments": 4, "finance_downpayment_rates": 24, "finance_downpayment_flows": 8,
           "finance_debt": 9, "finance_equity": 13}),
    ("c", {"finance_payments": 4, "finance_downpayment_rates": 24, "finance_downpayment_flows": 8,
           "finance_debt": 9, "finance_equity": 13}),
])
def test_current_finance_contracts_are_fully_grouped_without_renaming_variables(version, expected):
    rows = yaml.safe_load((ROOT / "config" / f"version_{version}_variables.yaml").read_text(encoding="utf-8"))["variables"]
    original_ids = [row["variable_id"] for row in rows]
    groups = Counter(variable_category(f"version-{version}", row).category_id for row in rows)
    assert groups == expected
    assert [row["variable_id"] for row in rows] == original_ids


def test_special_groups_are_dataset_scoped_and_do_not_change_metadata():
    metadata = {
        "variable_id": "acs_annual_first_mortgage_debt_service_benchmark",
        "data_domain": "structural", "definition": "Annualized payment unit",
    }
    original = metadata.copy()
    assert variable_category("version-a", metadata).category_id == "finance_payments"
    assert variable_category("main", metadata).category_id == "structural"
    assert metadata == original
    assert variable_category("main", metadata).category_label == "Households and housing characteristics"


@pytest.mark.parametrize("variable_id,row_key,expected", [
    ("median_sale_price", "price_source_variable_id", "Sale-price inputs"),
    ("redfin_zip_median_sale_price_nsa_rolling_3m", "price_source_variable_id", "Sale-price inputs"),
    ("down_payment_10pct", "down_payment_variable_id", "Down-payment scenarios"),
    ("closing_cost_loan_3pct", "closing_cost_variable_id", "Closing-cost scenarios"),
    ("estimated_mortgage_amount_scenario", "", "Mortgage amount and cash to close"),
    ("total_cash_to_close_scenario", "", "Mortgage amount and cash to close"),
])
def test_scenario_categories_follow_existing_row_identity(variable_id, row_key, expected):
    metadata = {"variable_id": variable_id, "row_identifier_column": row_key, "data_domain": "for_sale_market"}
    assert variable_category("homebuying-scenarios", metadata).category_label == expected
    assert variable_category("county-monthly", metadata).category_id == "for_sale_market"


@pytest.mark.parametrize("dataset,variable_id,expected", [
    ("zip-regimes-quarterly", "missing_regime_input_count", "Regime classification and diagnostics"),
    ("zip-regimes-quarterly", "price_to_rent_ratio_zillow", "Model inputs"),
    ("zip-regimes-quarterly", "price_to_rent_ratio_zillow_z_fixed", "Standardized model inputs"),
    ("zip-regime-summary-quarterly", "zip_market_regime_share", "Regime shares and changes"),
    ("zip-regime-summary-quarterly", "zip_market_regime_classified_coverage", "Classification coverage"),
    ("zip-regime-transitions-quarterly", "zip_market_regime_transition_share", "Regime transitions"),
])
def test_regime_categories_describe_existing_measure_roles(dataset, variable_id, expected):
    assert variable_category(dataset, {"variable_id": variable_id}).category_label == expected


@pytest.mark.parametrize("dataset,suffix,table", [
    ("version-a", "_benchmark", "housing_finance_benchmark"),
    ("version-b", "_monthly_hist", "housing_finance_monthly_hist"),
    ("version-c", "_monthly_rt", "housing_finance_monthly_rt"),
])
def test_finance_options_include_each_native_variable_once_and_respect_geography(dataset, suffix, table):
    ids = ["acs_annual_first_mortgage_debt_service", "hmda_median_downpayment_rate",
           "acs_implied_first_mortgage_balance_cal", "acs_equity_rate_mortgaged"]
    dictionary = pd.DataFrame([
        {"variable_id": variable + suffix, "display_name": variable,
         "data_domain": "finance", "unit_simplified": "USD", "temporal_type": "period_statistic",
         "temporal_type_note": "Test metadata", "monetary_basis": "not_applicable"}
        for variable in ids
    ])
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA analytics")
        connection.register("dictionary_input", dictionary)
        connection.execute("CREATE TABLE metadata.variable_dictionary AS SELECT * FROM dictionary_input")
        connection.execute(
            f"CREATE TABLE analytics.{table} AS "
            "SELECT variable_id, 'COUNTY_06037' geo_id, NULL::DOUBLE AS \"value\" FROM dictionary_input "
            "UNION ALL SELECT variable_id, 'COUNTY_06059', NULL::DOUBLE FROM dictionary_input"
        )
        options = variable_options(connection, "observations", dataset, geographies=["county"])
        assert [row["value"] for row in options] == [variable + suffix for variable in ids]
        assert [row["category_id"] for row in options] == [
            "finance_payments", "finance_downpayment_rates", "finance_debt", "finance_equity"
        ]
        assert len(options) == len({row["value"] for row in options}) == 4
        assert variable_options(connection, "observations", dataset, geographies=["postal_zip"]) == []
