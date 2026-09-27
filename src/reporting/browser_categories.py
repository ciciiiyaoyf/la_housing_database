"""Display-only groups for the variable picker; source classifications stay intact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


DOMAIN_LABELS = {
    "construction": "Construction and permits",
    "eviction": "Evictions",
    "finance": "Interest rates and financing",
    "for_sale_market": "Home sales and market conditions",
    "home_values": "Home values",
    "homelessness": "Homelessness",
    "housing_finance": "Housing finance",
    "housing_stock": "Housing stock",
    "labor_market": "Employment and labor market",
    "macro": "Macroeconomic indicators",
    "migration": "Migration",
    "population": "Population",
    "prices": "Prices and inflation",
    "property_insurance": "Property insurance",
    "rental_market": "Rents and rental market",
    "residential_address_availability": "Residential address availability",
    "structural": "Households and housing characteristics",
    "uncertainty": "Uncertainty and reliability",
}


@dataclass(frozen=True)
class BrowserCategory:
    category_id: str
    category_label: str
    order: int = 100


FINANCE_CATEGORIES = {
    "payments": BrowserCategory("finance_payments", "Mortgage payments and payment parameters", 1),
    "downpayment_rates": BrowserCategory("finance_downpayment_rates", "Down-payment rates and borrower shares", 2),
    "downpayment_flows": BrowserCategory("finance_downpayment_flows", "Aggregate down-payment flows", 3),
    "debt": BrowserCategory("finance_debt", "Mortgage debt and sensitivities", 4),
    "equity": BrowserCategory("finance_equity", "Home equity and loan-to-value", 5),
    "benchmark_inputs": BrowserCategory("finance_benchmark_inputs", "Benchmark population, housing, values and income", 6),
}
PAYMENT_VARIABLES = {
    "acs_estimated_aggregate_monthly_first_mortgage_pi",
    "acs_first_mortgage_pi_per_mortgaged_unit",
    "acs_annual_first_mortgage_debt_service",
    "acs_mortgage_debt_service_to_income",
    "pums_first_mortgage_pi_share_applied", "pums_first_mortgage_pi_share",
    "pums_junior_lien_payment_per_mortgaged_unit",
}
BENCHMARK_INPUT_VARIABLES = {
    "acs_aggregate_household_income_mortgaged_owner", "acs_aggregate_household_income_no_mortgage_owner",
    "acs_aggregate_household_income_owner", "acs_aggregate_owner_value_all",
    "acs_aggregate_owner_value_mortgaged", "acs_aggregate_owner_value_no_mortgage",
    "acs_aggregate_selected_monthly_owner_costs_mortgaged", "acs_mortgaged_owner_units",
    "acs_owner_occupied_units", "acs_total_population",
}
SCENARIO_ROW_CATEGORIES = {
    "price_source_variable_id": BrowserCategory("scenario_prices", "Sale-price inputs", 1),
    "down_payment_variable_id": BrowserCategory("scenario_downpayment", "Down-payment scenarios", 2),
    "closing_cost_variable_id": BrowserCategory("scenario_closing_costs", "Closing-cost scenarios", 3),
}
REGIME_INPUT_VARIABLES = {
    "zhvi_momentum_3m_annualized_log_pct", "zori_momentum_3m_annualized_log_pct",
    "price_to_rent_ratio_zillow", "zhvi_monthly_log_growth_volatility_24m",
}


def _finance_category(variable_id: str) -> BrowserCategory | None:
    for suffix in ("_benchmark", "_monthly_hist", "_monthly_rt"):
        if variable_id.endswith(suffix):
            variable_id = variable_id.removesuffix(suffix)
            break
    # Payment/service measures must precede the broader mortgage-debt prefix.
    if variable_id in PAYMENT_VARIABLES:
        return FINANCE_CATEGORIES["payments"]
    if variable_id.startswith("hmda_aggregate_downpayment_flow"):
        return FINANCE_CATEGORIES["downpayment_flows"]
    if variable_id.startswith(("hmda_median_downpayment_rate", "hmda_property_value_weighted_downpayment_rate",
                               "hmda_share_downpayment_below_")):
        return FINANCE_CATEGORIES["downpayment_rates"]
    if variable_id.startswith(("acs_implied_first_mortgage_balance", "acs_first_mortgage_debt")):
        return FINANCE_CATEGORIES["debt"]
    if variable_id.startswith("acs_implied_owner_occupied_home_equity") or variable_id in {
        "acs_aggregate_ltv_mortgaged", "acs_equity_rate_mortgaged"
    }:
        return FINANCE_CATEGORIES["equity"]
    if variable_id in BENCHMARK_INPUT_VARIABLES:
        return FINANCE_CATEGORIES["benchmark_inputs"]
    return None


def variable_category(dataset: str, metadata: Mapping[str, object]) -> BrowserCategory:
    """Return one navigation group without changing variable metadata or values."""
    variable_id = str(metadata.get("variable_id", "")).strip()
    if dataset == "nine-constructions":
        number = str(metadata.get("construction_number", "")).strip().removesuffix(".0")
        name = str(metadata.get("construction_name", "")).strip()
        if number.isdigit() and name:
            family = int(number)
            return BrowserCategory(f"construction_{family}", f"{family}. {name}", family)
    if dataset in {"version-a", "version-b", "version-c"}:
        category = _finance_category(variable_id)
        if category is not None:
            return category
    if dataset == "homebuying-scenarios":
        row_key = str(metadata.get("row_identifier_column", ""))
        if row_key in SCENARIO_ROW_CATEGORIES:
            return SCENARIO_ROW_CATEGORIES[row_key]
        if variable_id in {"estimated_mortgage_amount_scenario", "total_cash_to_close_scenario"}:
            return BrowserCategory("scenario_amounts", "Mortgage amount and cash to close", 4)
    if dataset == "zip-regimes-quarterly":
        if variable_id in {"zip_market_regime_id", "zip_market_regime_distance", "missing_regime_input_count"}:
            return BrowserCategory("regime_classification", "Regime classification and diagnostics", 1)
        if variable_id in REGIME_INPUT_VARIABLES:
            return BrowserCategory("regime_inputs", "Model inputs", 2)
        if variable_id.removesuffix("_z_fixed") in REGIME_INPUT_VARIABLES and variable_id.endswith("_z_fixed"):
            return BrowserCategory("regime_standardized_inputs", "Standardized model inputs", 3)
    if dataset == "zip-regime-summary-quarterly":
        if variable_id in {"zip_market_regime_share", "zip_market_regime_changed_share", "zip_market_regime_entry_share"}:
            return BrowserCategory("regime_shares", "Regime shares and changes", 1)
        if variable_id == "zip_market_regime_classified_coverage":
            return BrowserCategory("regime_coverage", "Classification coverage", 2)
    if dataset == "zip-regime-transitions-quarterly" and variable_id == "zip_market_regime_transition_share":
        return BrowserCategory("regime_transitions", "Regime transitions", 1)
    domain = str(metadata.get("data_domain", "")).strip()
    if domain in DOMAIN_LABELS:
        return BrowserCategory(domain, DOMAIN_LABELS[domain])
    return BrowserCategory("uncategorized", "Uncategorized", 1000)
