"""Catalog, DOCX variable audit, and raw repository completeness outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import duckdb
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from src.common.config import ProjectConfig, SourceConfig


def _req(
    category: str,
    variable_name: str,
    *,
    sheet_source: str = "",
    sheet_frequency: str = "",
    sheet_la_availability: str = "",
    sheet_notes: str = "",
    why_hard: str = "",
    best_public_workaround: str = "",
    sheet_variable_name: str = "",
) -> dict[str, str]:
    return {
        "docx_category": category,
        "variable_name": variable_name,
        "sheet_variable_name": sheet_variable_name or variable_name,
        "sheet_source": sheet_source,
        "sheet_frequency": sheet_frequency,
        "sheet_la_availability": sheet_la_availability,
        "sheet_notes": sheet_notes,
        "why_hard": why_hard,
        "best_public_workaround": best_public_workaround,
    }


DOCX_VARIABLES = [
    _req(
        "Difficult Variables",
        "Aggregate Home Equity",
        why_hard="No public state/county balance-sheet series; Fed Flow of Funds is national only",
        best_public_workaround="Construct proxy: owner-occupied housing value minus aggregate mortgage debt proxy. Housing value from ACS owner units times median value or Zillow; debt proxy below.",
    ),
    _req(
        "Difficult Variables",
        "Aggregate Mortgage Debt (outstanding balances)",
        why_hard="ACS reports payments, not balances; no public CA/LA balance series",
        best_public_workaround="Approximate via owner-occupied-with-mortgage counts times implied balance from payment distributions (ACS B25106) plus interest rate assumptions.",
    ),
    _req(
        "Difficult Variables",
        "Monthly Mortgage Payment",
        why_hard="Public data gives rates, not realized payments by geography",
        best_public_workaround="Construct using local price series (Redfin/Zillow), prevailing mortgage rates, assumed LTV/term; optionally validate against ACS owner costs.",
    ),
    _req(
        "Difficult Variables",
        "Homebuying Costs - Downpayment",
        sheet_variable_name="Homebuying Costs – Downpayment",
        why_hard="Not reported publicly by geography",
        best_public_workaround="Assume percent of median sale price (scenario-based: 10%, 20%, FHA-style); treat as policy-sensitive parameter.",
    ),
    _req(
        "Difficult Variables",
        "Homebuying Costs - Closing Costs",
        sheet_variable_name="Homebuying Costs – Closing Costs",
        why_hard="No public geographic series",
        best_public_workaround="Apply standardized percent of sale price, state-specific if available.",
    ),
    _req(
        "Difficult Variables",
        "Homebuying Costs - Property Insurance",
        sheet_variable_name="Homebuying Costs – Property Insurance",
        why_hard="NAIC only publishes state-level averages",
        best_public_workaround="Use CA state series.",
    ),
    _req(
        "Difficult Variables",
        "Rental price increase in non-rent-controlled locations",
        sheet_variable_name="Rental price increase in non–rent-controlled locations",
        why_hard="Public rent indices do not flag rent-control status",
        best_public_workaround="Use Zillow ZORI and exclude geographies by construction-year share (post-1995 / post-2005 proxy) or interact with policy exposure.",
    ),
    _req(
        "Difficult Variables",
        "Eviction trends (high-frequency, consistent)",
        why_hard="County coverage uneven; time consistency varies",
        best_public_workaround="Eviction Lab ETS where available; otherwise annual filings with interpolation.",
    ),
    _req(
        "Difficult Variables",
        "New Apartments Completed (county time series)",
        why_hard="Clean completions data not consistently public at county level",
        best_public_workaround="Proxy with lagged multifamily permits (5+) using completion lag structure; lags uncertain.",
    ),
    _req(
        "Difficult Variables",
        "Multifamily Under Construction",
        why_hard="No public stock measure at county/state level",
        best_public_workaround="Build pipeline stock: cumulative MF permits minus assumed completions over rolling window.",
    ),
    _req(
        "Difficult Variables",
        "Single-Family Rental Construction",
        why_hard='No public "build-for-rent" flag in Census data',
        best_public_workaround="Proxy using SFR permits in high-investor / rental-growth tracts or small MF permits.",
    ),
    _req(
        "Difficult Variables",
        "Net Change in Occupied Apartments (quarterly)",
        why_hard="ACS is annual; no quarterly public alternative",
        best_public_workaround="Annual ACS change in occupied renter units; optionally interpolate or use CPS/HVS for state-level timing.",
    ),
    _req(
        "Difficult Variables",
        "Price-to-Income Ratio (quarterly/local)",
        why_hard="Income is annual; prices are monthly",
        best_public_workaround="Construct ratio using annual ACS income with interpolated or stepwise series.",
    ),
    _req(
        "Difficult Variables",
        "Natural Population Change (quarterly)",
        why_hard="Birth/death data is annual at county level",
        best_public_workaround="Annual CDC WONDER series; interpolate if quarterly timing is needed.",
    ),
    _req(
        "Difficult Variables",
        "Net Immigration / Migration (county)",
        why_hard="IRS data is annual and lagged",
        best_public_workaround="Annual IRS inflow minus outflow; treat as slow-moving VAR state variable.",
    ),
    _req(
        "Difficult Variables",
        "Sheltered vs Unsheltered Homelessness (high frequency)",
        why_hard="PIT count is annual (January only)",
        best_public_workaround="Annual HUD PIT; no credible quarterly public alternative.",
    ),
    _req("AVAILABLE VARIABLES - Homeowners", "Home Price Index", sheet_source="FHFA HPI", sheet_frequency="Quarterly (also monthly)", sheet_notes="Clean state & large-county/MSA coverage"),
    _req("AVAILABLE VARIABLES - Homeowners", "Median Sale Price (SF & all homes)", sheet_source="Redfin Data Center", sheet_frequency="Monthly (weekly raw)", sheet_notes="Level prices ideal for affordability & payment calculations"),
    _req("AVAILABLE VARIABLES - Homeowners", "Existing Homes for Sale (Inventory)", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Direct stock measure"),
    _req("AVAILABLE VARIABLES - Homeowners", "Months of Supply", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Internally consistent with inventory & sales"),
    _req("AVAILABLE VARIABLES - Homeowners", "New Listings", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Useful leading indicator"),
    _req("AVAILABLE VARIABLES - Homeowners", "Homes Sold / Sales Volume", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Transaction-side activity"),
    _req("AVAILABLE VARIABLES - Homeowners", "Mortgage Rates (30Y FRM)", sheet_source="Freddie Mac PMMS", sheet_frequency="Weekly / Monthly", sheet_notes="Commonly used national rate in regional VARs"),
    _req("AVAILABLE VARIABLES - Homeowners", "Single-Family Housing Starts (permits)", sheet_source="Census BPS (1-unit permits)", sheet_frequency="Monthly (state)", sheet_notes="Monthly at CA level; LA county is annual only"),
    _req("AVAILABLE VARIABLES - Renters", "Market Rent Index (ZORI)", sheet_source="Zillow Research", sheet_frequency="Monthly", sheet_la_availability="Yes (county)", sheet_notes="Best public rent index"),
    _req("AVAILABLE VARIABLES - Renters", "Rent Growth (YoY / MoM)", sheet_source="Zillow ZORI", sheet_frequency="Monthly", sheet_la_availability="Yes", sheet_notes="Computed directly from ZORI"),
    _req("AVAILABLE VARIABLES - Renters", "Rental Inventory", sheet_source="Zillow Observed Rent Index / Listings", sheet_frequency="Monthly", sheet_la_availability="Yes", sheet_notes="Stock-side rental measure"),
    _req("AVAILABLE VARIABLES - Renters", "Rental Vacancy (HVS / CPS)", sheet_source="Census Housing Vacancy Survey", sheet_frequency="Quarterly", sheet_la_availability="No", sheet_notes="Quarterly vacancy rate (state only)"),
    _req("AVAILABLE VARIABLES - Renters", "Multifamily Permits (5+ units)", sheet_source="Census BPS", sheet_frequency="Monthly (state)", sheet_la_availability="Annual county", sheet_notes="Best public proxy for MF starts"),
    _req("AVAILABLE VARIABLES - Overall Housing Market", "Price-to-Rent Ratio", sheet_source="Constructed from FHFA / ZORI", sheet_frequency="Monthly / Quarterly", sheet_notes="Very clean once inputs chosen"),
    _req("AVAILABLE VARIABLES - Overall Housing Market", "Housing Turnover", sheet_source="Redfin (sales / inventory)", sheet_frequency="Monthly", sheet_notes="Useful liquidity metric"),
    _req("AVAILABLE VARIABLES - Overall Housing Market", "Market Time (Days on Market)", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Strong cyclical indicator"),
    _req("AVAILABLE VARIABLES - Overall Housing Market", "Share of Homes with Price Drops", sheet_source="Redfin Data Center", sheet_frequency="Monthly", sheet_notes="Forward-looking price pressure"),
    _req("Population, Labor, Macro Controls", "Payroll Employment", sheet_source="BLS CES", sheet_frequency="Monthly", sheet_la_availability="Yes (MSA)", sheet_notes="LA metro rather than county"),
    _req("Population, Labor, Macro Controls", "Unemployment Rate", sheet_source="BLS LAUS", sheet_frequency="Monthly", sheet_la_availability="Yes(county)", sheet_notes="Clean county coverage"),
    _req("Population, Labor, Macro Controls", "Labor Force", sheet_source="BLS LAUS", sheet_frequency="Monthly", sheet_la_availability="Yes"),
    _req("Population, Labor, Macro Controls", "Personal Income (proxy)", sheet_source="BEA", sheet_frequency="Quarterly", sheet_la_availability="county quarterly limited", sheet_notes="State is clean; county is noisier"),
    _req("Population, Labor, Macro Controls", "CPI / Inflation", sheet_source="BLS CPI-U", sheet_frequency="Monthly", sheet_la_availability="LA metro", sheet_notes="LA CPI-U is metro-level"),
]


def _impl(
    status: str,
    source_id: str,
    variable_id: str,
    table: str,
    column: str,
    kind: str,
    limitation: str,
) -> dict[str, str]:
    return {
        "current_status": status,
        "implemented_source_id": source_id,
        "implemented_variable_id": variable_id,
        "implemented_table": table,
        "implemented_column": column,
        "observed_constructed_proxy_or_scenario": kind,
        "limitation": limitation,
    }


IMPLEMENTATION = {
    "Aggregate Home Equity": _impl("Proxy implemented", "census_acs_5year_b25003|census_acs_5year_b25077|census_acs_5year_b25081|census_acs_5year_b25106|freddie_mac_pmms", "acs_implied_aggregate_home_equity_proxy", "analytics.county_annual_controls", "acs_implied_aggregate_home_equity_proxy", "proxy", "Implements the sheet workaround as owner-occupied units times ACS median home value minus the implied aggregate mortgage-debt proxy. This is not an observed balance-sheet series."),
    "Aggregate Mortgage Debt (outstanding balances)": _impl("Proxy implemented", "census_acs_5year_b25081|census_acs_5year_b25106|freddie_mac_pmms", "acs_implied_aggregate_mortgage_debt_proxy", "analytics.county_annual_controls", "acs_implied_aggregate_mortgage_debt_proxy", "proxy", "Implied from ACS B25106 owner income/cost-burden bins, ACS B25081 owner-with-mortgage counts, and Freddie Mac rate assumptions. ACS does not directly publish balances."),
    "Monthly Mortgage Payment": _impl("Scenario implemented", "", "mortgage_payment_3_5pct_down|mortgage_payment_10pct_down|mortgage_payment_20pct_down", "analytics.county_monthly_panel", "mortgage_payment_3_5pct_down|mortgage_payment_10pct_down|mortgage_payment_20pct_down", "scenario assumption", "Principal-and-interest scenarios only; excludes taxes, insurance, HOA, maintenance, and PMI."),
    "Homebuying Costs - Downpayment": _impl("Scenario implemented", "", "down_payment_3_5pct|down_payment_10pct|down_payment_20pct", "analytics.county_monthly_panel", "down_payment_3_5pct|down_payment_10pct|down_payment_20pct", "scenario assumption", "Scenario percentages of Redfin county median sale price."),
    "Homebuying Costs - Closing Costs": _impl("Scenario implemented", "", "closing_cost_2pct|closing_cost_3pct|closing_cost_5pct", "analytics.county_monthly_panel", "closing_cost_2pct|closing_cost_3pct|closing_cost_5pct", "scenario assumption", "Scenario percentages of Redfin county median sale price; not observed closing-cost data."),
    "Homebuying Costs - Property Insurance": _impl("Not implemented", "", "", "", "", "deferred", "The sheet specifies a CA state NAIC series; NAIC/state homeowners-insurance data is not loaded yet."),
    "Rental price increase in non-rent-controlled locations": _impl("Not implemented", "zillow_county_zori_smoothed_sa|zillow_zip_zori_smoothed_sa", "zori_smoothed_sa", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "zori", "proxy input", "ZORI is loaded, but the sheet workaround also requires construction-year/rent-control exposure filters that are not implemented."),
    "Eviction trends (high-frequency, consistent)": _impl("Implemented", "eviction_lab_county_estimates", "eviction_lab_filings_estimate_annual|eviction_lab_filings_rate_estimate_annual|eviction_lab_households_threatened_estimate_annual|eviction_lab_households_threatened_rate_estimate_annual", "analytics.fact_housing_observation|analytics.county_annual_controls", "eviction_lab_filings_rate_estimate_annual|eviction_lab_households_threatened_rate_estimate_annual", "modeled", "Eviction Lab ETS does not cover Los Angeles County, and the official observed/validated county analysis files do not contain LA County rows. The implemented source is the official Eviction Lab annual county modeled estimates for 2000-2018, with source observation flags and 95% credible interval bounds preserved. Do not treat as monthly/high-frequency ETS or direct local court filings."),
    "New Apartments Completed (county time series)": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_5_plus_units", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_5_plus_units", "proxy input", "The sheet workaround is lagged multifamily permits with an explicit completion-lag structure; permits are loaded, but no completion proxy is calculated."),
    "Multifamily Under Construction": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_5_plus_units", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_5_plus_units", "proxy input", "The sheet workaround is cumulative MF permits minus assumed completions over a rolling window; permit inputs are loaded, but the stock pipeline calculation is not implemented."),
    "Single-Family Rental Construction": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_1_unit", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_1_unit", "proxy input", "The sheet workaround needs SFR permits combined with investor/rental-growth tract logic; only 1-unit permits are loaded."),
    "Net Change in Occupied Apartments (quarterly)": _impl("Partially implemented", "census_acs_5year_b25003", "acs_renter_occupied_units", "analytics.county_annual_controls|analytics.fact_housing_observation", "acs_renter_occupied_units", "observed", "ACS annual 5-year renter-occupied units are available; quarterly net change is not created."),
    "Price-to-Income Ratio (quarterly/local)": _impl("Partially implemented", "census_acs_5year_b19013|census_acs_5year_b25077", "county_annual_price_to_income_acs", "analytics.county_annual_controls", "county_annual_price_to_income_acs", "constructed", "Annual ACS ratio is implemented; quarterly/local alternatives are deferred."),
    "Natural Population Change (quarterly)": _impl("Implemented with quarterly allocation", "cdc_wonder_natality_county|cdc_wonder_ucd_1999_2020_la_county|cdc_wonder_ucd_2018_2024_county", "cdc_wonder_live_births_annual|cdc_wonder_deaths_annual|natural_population_change_annual|natural_population_change_quarterly_allocated", "analytics.fact_housing_observation|analytics.county_annual_controls|analytics.county_quarterly_controls", "natural_population_change_quarterly_allocated", "proxy", "Annual CDC WONDER births and deaths are loaded as observed annual county facts; annual natural population change is births minus deaths. The quarterly series evenly allocates the annual total to four quarters and is flagged as imputed/interpolated timing, not observed quarterly vital events."),
    "Net Immigration / Migration (county)": _impl("Implemented", "irs_soi_county_migration", "irs_soi_net_migration_exemptions|irs_soi_net_migration_returns|irs_soi_net_migration_agi", "analytics.county_annual_controls|analytics.fact_housing_observation", "irs_soi_net_migration_exemptions", "constructed", "Annual IRS SOI in-migration minus out-migration is implemented. Exemptions approximate people and are the preferred public proxy; returns approximate tax filing units; AGI captures income composition. This is migration based on tax return address changes, not complete immigration."),
    "Sheltered vs Unsheltered Homelessness (high frequency)": _impl("Not implemented", "", "", "", "", "deferred", "The sheet workaround specifies annual HUD PIT; HUD PIT/CoC data is not loaded."),
    "Home Price Index": _impl("Implemented", "fhfa_hpi_state_purchase_only|fhfa_hpi_metro_purchase_only|fhfa_hpi_county_annual", "fhfa_hpi_ca_purchase_only_nsa|fhfa_hpi_ca_purchase_only_sa|fhfa_hpi_la_metropolitan_division_purchase_only_nsa|fhfa_hpi_la_metropolitan_division_purchase_only_sa|fhfa_hpi_la_county_all_transactions_nsa_annual", "analytics.state_quarterly_panel|analytics.metro_quarterly_controls|analytics.county_annual_controls|analytics.fact_housing_observation", "fhfa_hpi_ca_purchase_only_nsa|fhfa_hpi_la_county_all_transactions_nsa_annual", "observed", "Aligned with the sheet's FHFA HPI source. Zillow ZHVI remains available separately but is not counted here as the sheet HPI source."),
    "Median Sale Price (SF & all homes)": _impl("Implemented", "redfin_county_monthly|redfin_zip_monthly", "median_sale_price|redfin_zip_median_sale_price_nsa_rolling_3m", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "median_sale_price", "observed", "ZIP Redfin values are overlapping rolling-three-month NSA Key Metrics."),
    "Existing Homes for Sale (Inventory)": _impl("Implemented", "redfin_county_monthly", "active_inventory", "analytics.county_monthly_panel", "active_inventory", "observed", "Aligned with the sheet's Redfin Data Center stock measure at county level. Redfin ZIP active listings are retained separately but not used to satisfy this inventory row."),
    "Months of Supply": _impl("Implemented", "redfin_county_monthly", "months_of_supply", "analytics.county_monthly_panel", "months_of_supply", "observed", "Aligned with the sheet's Redfin Data Center monthly source at county level."),
    "New Listings": _impl("Implemented", "redfin_county_monthly|redfin_zip_monthly", "new_listings|redfin_zip_new_listings_rolling_3m", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "new_listings", "observed", "ZIP methodology is overlapping rolling-three-month."),
    "Homes Sold / Sales Volume": _impl("Implemented", "redfin_county_monthly|redfin_zip_monthly", "homes_sold|redfin_zip_homes_sold_rolling_3m", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "homes_sold", "observed", "ZIP methodology is overlapping rolling-three-month."),
    "Mortgage Rates (30Y FRM)": _impl("Implemented", "freddie_mac_pmms", "mortgage_rate_30y_weekly|mortgage_rate_30y_monthly_average|mortgage_rate_30y_monthly_median|mortgage_rate_30y_month_end|mortgage_rate_30y_observation_count", "analytics.fact_housing_observation|analytics.county_monthly_panel", "mortgage_rate_30y_monthly_average", "observed|constructed", "National PMMS, not LA-specific."),
    "Single-Family Housing Starts (permits)": _impl("Proxy implemented", "census_bps_county|census_bps_state", "permits_1_unit", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_1_unit", "proxy", "Census BPS permits are authorizations, not starts or completions."),
    "Market Rent Index (ZORI)": _impl("Implemented", "zillow_county_zori_smoothed_sa|zillow_zip_zori_smoothed_sa", "zori_smoothed_sa", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "zori", "observed", "Active ZORI is smoothed, seasonally adjusted, all homes plus multifamily."),
    "Rent Growth (YoY / MoM)": _impl("Implemented", "", "zori_growth_mom|zori_growth_yoy", "analytics.county_monthly_panel", "zori_growth_mom|zori_growth_yoy", "constructed", "Calculated from seasonally adjusted ZORI with initial missing months preserved."),
    "Rental Inventory": _impl("Not implemented - sheet source endpoint not verified", "", "", "", "", "unavailable", "The sheet specifies Zillow Observed Rent Index / Listings. ZORI is loaded, but ZORI is not inventory; it is a rent index, not a rental-listing stock count. Redfin for-sale active listings are not a valid substitute. A verified official Zillow rental-listings/inventory endpoint is still needed."),
    "Rental Vacancy (HVS / CPS)": _impl("Implemented", "census_hvs_state_rental_vacancy|census_hvs_metro_rental_vacancy_2005_2014|census_hvs_metro_rental_vacancy_2015_2025|census_hvs_metro_rental_vacancy_2026", "hvs_rental_vacancy_rate_ca|hvs_rental_vacancy_rate_la_metro", "analytics.state_quarterly_panel|analytics.metro_quarterly_controls", "hvs_rental_vacancy_rate_ca|hvs_rental_vacancy_rate_la_metro", "observed", "State/metro quarterly only; not LA County."),
    "Multifamily Permits (5+ units)": _impl("Implemented", "census_bps_county|census_bps_state", "permits_5_plus_units", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_5_plus_units", "observed", "Supply-pipeline authorization measure; not starts or completions."),
    "Price-to-Rent Ratio": _impl("Partially implemented - alternate source", "zillow_county_zhvi|zillow_county_zori_smoothed_sa", "price_to_rent_ratio", "analytics.county_monthly_panel", "price_to_rent_ratio", "constructed", "The active monthly panel ratio uses ZHVI/ZORI. The sheet specifies FHFA/ZORI; that version needs an explicit FHFA index-to-rent construction choice and is not yet implemented."),
    "Housing Turnover": _impl("Partially implemented", "redfin_county_monthly", "sales_to_inventory_ratio|inventory_to_sales_ratio", "analytics.county_monthly_panel", "sales_to_inventory_ratio|inventory_to_sales_ratio", "constructed", "Sales/inventory liquidity is implemented; sales/housing-stock turnover is deferred."),
    "Market Time (Days on Market)": _impl("Implemented", "redfin_county_monthly|redfin_zip_monthly", "days_on_market|redfin_zip_median_days_on_market_rolling_3m", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "days_on_market|median_days_on_market", "observed", "ZIP methodology is overlapping rolling-three-month."),
    "Share of Homes with Price Drops": _impl("Implemented", "redfin_county_monthly", "price_drop_share", "analytics.county_monthly_panel", "price_drop_share", "observed", "Aligned with the sheet's Redfin Data Center monthly source at county level."),
    "Payroll Employment": _impl("Implemented", "bls_ces_la_metro", "ces_total_nonfarm_employment_sa", "analytics.metro_monthly_controls", "ces_total_nonfarm_employment_sa", "observed", "LA metro series, not county."),
    "Unemployment Rate": _impl("Implemented", "bls_laus_la_county", "unemployment_rate", "analytics.county_monthly_panel", "unemployment_rate", "observed", "LA County LAUS NSA."),
    "Labor Force": _impl("Implemented", "bls_laus_la_county", "labor_force|employment|unemployment", "analytics.county_monthly_panel", "labor_force", "observed", "LA County LAUS NSA."),
    "Personal Income (proxy)": _impl("Partially implemented", "bea_state_personal_income|bea_county_personal_income", "bea_ca_personal_income_q_sa|bea_la_county_personal_income_annual", "analytics.state_quarterly_panel|analytics.county_annual_controls", "bea_ca_personal_income_q_sa|bea_la_county_personal_income_annual", "observed", "Aligned with BEA as the sheet source. State quarterly is loaded; county is annual because county quarterly personal income is limited/noisier as noted in the sheet."),
    "CPI / Inflation": _impl("Implemented", "bls_cpi_la_metro", "cpi_u_all_items_la_nsa", "analytics.metro_monthly_controls", "cpi_u_all_items_la_nsa", "observed", "LA metro CPI-U, not county."),
}


PANEL_PATHS = {
    "analytics.fact_housing_observation": "fact_housing_observation.parquet",
    "analytics.county_monthly_panel": "county_monthly_panel.parquet",
    "analytics.zip_monthly_panel": "zip_monthly_panel.parquet",
    "analytics.state_quarterly_panel": "state_quarterly_panel.parquet",
    "analytics.metro_monthly_controls": "metro_monthly_controls.parquet",
    "analytics.metro_quarterly_controls": "metro_quarterly_controls.parquet",
    "analytics.county_annual_controls": "county_annual_controls.parquet",
    "analytics.county_quarterly_controls": "county_quarterly_controls.parquet",
}


def _read_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _load_fact(config: ProjectConfig) -> pd.DataFrame:
    return _read_parquet(config.root / "data" / "final" / "fact_housing_observation.parquet")


def _fact_stats(fact: pd.DataFrame, variable_ids: str) -> dict[str, object]:
    ids = [item for item in str(variable_ids).split("|") if item]
    if fact.empty or not ids:
        return {"start_date": None, "end_date": None, "row_count": 0, "missingness_summary": ""}
    work = fact.loc[fact["variable_id"].isin(ids)].copy()
    if work.empty:
        return {"start_date": None, "end_date": None, "row_count": 0, "missingness_summary": ""}
    missing = int(work["value"].isna().sum())
    return {
        "start_date": work["observation_date"].min(),
        "end_date": work["observation_date"].max(),
        "row_count": len(work),
        "missingness_summary": f"{missing} missing of {len(work)} fact rows",
    }


def _panel_stats(config: ProjectConfig, tables: str, columns: str) -> dict[str, object]:
    table_names = [item for item in str(tables).split("|") if item in PANEL_PATHS]
    column_names = [item for item in str(columns).split("|") if item]
    parts: list[pd.DataFrame] = []
    for table in table_names:
        panel = _read_parquet(config.root / "data" / "final" / PANEL_PATHS[table])
        if panel.empty:
            continue
        present = [column for column in column_names if column in panel.columns]
        if not present:
            continue
        date_col = "observation_date" if "observation_date" in panel.columns else None
        if date_col is None:
            continue
        melted = panel[[date_col, *present]].melt(
            id_vars=[date_col],
            value_vars=present,
            var_name="column",
            value_name="value",
        )
        parts.append(melted.rename(columns={date_col: "observation_date"}))
    if not parts:
        return {"start_date": None, "end_date": None, "row_count": 0, "missingness_summary": ""}
    work = pd.concat(parts, ignore_index=True)
    missing = int(work["value"].isna().sum())
    return {
        "start_date": work["observation_date"].min(),
        "end_date": work["observation_date"].max(),
        "row_count": len(work),
        "missingness_summary": f"{missing} missing of {len(work)} panel cells",
    }


def _best_stats(config: ProjectConfig, fact: pd.DataFrame, implementation: dict[str, str]) -> dict[str, object]:
    stats = _fact_stats(fact, implementation["implemented_variable_id"])
    if stats["row_count"]:
        return stats
    return _panel_stats(
        config,
        implementation["implemented_table"],
        implementation["implemented_column"],
    )


def _raw_manifest(config: ProjectConfig) -> pd.DataFrame:
    path = config.root / "metadata" / "raw_file_manifest.csv"
    return pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()


def _latest_manifest_rows(config: ProjectConfig) -> pd.DataFrame:
    manifest = _raw_manifest(config)
    if manifest.empty:
        return pd.DataFrame()
    return (
        manifest.sort_values(["source_id", "retrieved_at"])
        .drop_duplicates("source_id", keep="last")
        .set_index("source_id", drop=False)
    )


def _raw_path(config: ProjectConfig, manifest_row: dict[str, object]) -> Path | None:
    relative_path = str(manifest_row.get("relative_path") or "")
    if not relative_path or relative_path.lower() == "nan":
        return None
    return config.root / relative_path


def _clean_table_names(config: ProjectConfig) -> set[str]:
    clean_dir = config.root / "data" / "clean"
    return {path.stem for path in clean_dir.glob("*.parquet")} if clean_dir.exists() else set()


def _source_clean_name(source: SourceConfig) -> str:
    return source.destination_clean_table or source.source_id


def _source_observation_path(config: ProjectConfig, source: SourceConfig) -> Path:
    if source.source_id == "redfin_zip_monthly":
        return (
            config.root
            / "data"
            / "interim"
            / "redfin_zip_monthly_observations.parquet"
        )
    return config.root / "data" / "clean" / f"{source.source_id}.parquet"


def _source_stats(config: ProjectConfig, source: SourceConfig) -> dict[str, object]:
    frame = _read_parquet(_source_observation_path(config, source))
    if frame.empty or "observation_date" not in frame.columns:
        return {
            "start_date": None,
            "end_date": None,
            "row_count": 0,
            "missingness_summary": "",
        }
    if "variable_id" in frame.columns and source.key_variables:
        frame = frame.loc[frame["variable_id"].isin(source.key_variables)].copy()
    if frame.empty:
        return {
            "start_date": None,
            "end_date": None,
            "row_count": 0,
            "missingness_summary": "",
        }
    missing = int(frame["value"].isna().sum()) if "value" in frame.columns else 0
    return {
        "start_date": frame["observation_date"].min(),
        "end_date": frame["observation_date"].max(),
        "row_count": len(frame),
        "missingness_summary": f"{missing} missing of {len(frame)} clean rows",
    }


def _docx_audit(config: ProjectConfig, fact: pd.DataFrame) -> pd.DataFrame:
    manifest = _latest_manifest_rows(config)
    clean_tables = _clean_table_names(config)
    source_by_id = {source.source_id: source for source in config.sources}
    rows = []
    for requirement in DOCX_VARIABLES:
        name = requirement["variable_name"]
        category = requirement["docx_category"]
        implementation = IMPLEMENTATION[name]
        stats = _best_stats(config, fact, implementation)
        source_ids = [
            item
            for item in implementation["implemented_source_id"].split("|")
            if item
        ]
        clean_names = [
            _source_clean_name(source_by_id[item])
            for item in source_ids
            if item in source_by_id
        ]
        rows.append(
            {
                "docx_category": category,
                "variable_name": name,
                "sheet_variable_name": requirement["sheet_variable_name"],
                "expected_source_from_docx": requirement["sheet_source"],
                "expected_frequency_from_docx": requirement["sheet_frequency"],
                "expected_la_availability_from_docx": requirement[
                    "sheet_la_availability"
                ],
                "expected_notes_from_docx": requirement["sheet_notes"],
                "why_hard_from_docx": requirement["why_hard"],
                "best_public_workaround_from_docx": requirement[
                    "best_public_workaround"
                ],
                **implementation,
                "native_frequency": "",
                "analytical_frequency": "",
                "geography_level": "",
                "geography_name": "",
                "raw_source_present": bool(source_ids)
                and all(item in manifest.index for item in source_ids),
                "clean_table_present": bool(clean_names)
                and any(item in clean_tables for item in clean_names),
                "fact_table_present": bool(implementation["implemented_variable_id"])
                and _fact_stats(fact, implementation["implemented_variable_id"])["row_count"] > 0,
                "panel_or_view_present": bool(implementation["implemented_table"]),
                "start_date": stats["start_date"],
                "end_date": stats["end_date"],
                "row_count": stats["row_count"],
                "missingness_summary": stats["missingness_summary"],
                "recommended_next_action": (
                    "Ready for data-collection use"
                    if implementation["current_status"]
                    in {"Implemented", "Scenario implemented", "Proxy implemented"}
                    else "Review documented limitation before modeling"
                ),
            }
        )
    return pd.DataFrame(rows)


def _data_catalog(config: ProjectConfig, fact: pd.DataFrame) -> pd.DataFrame:
    manifest = _latest_manifest_rows(config)
    rows = []
    for source in config.sources:
        manifest_row = (
            manifest.loc[source.source_id].to_dict()
            if source.source_id in manifest.index
            else {}
        )
        variables = list(source.key_variables)
        stats = _source_stats(config, source)
        raw_path = _raw_path(config, manifest_row)
        rows.append(
            {
                "Source": source.dataset_name,
                "Frequency": source.native_frequency,
                "Geography": "|".join(source.geography_levels),
                "Key variables": "|".join(variables),
                "source_id": source.source_id,
                "provider": source.provider,
                "product": source.dataset_name,
                "official_source_page": source.source_page_url,
                "exact_download_url_or_api": source.download_url,
                "access_method": source.access_method,
                "active_status": "Active" if source.enabled else "Inactive",
                "observed_constructed_proxy_or_scenario": "observed",
                "manual_download_required": not source.auto_download,
                "manual_reason": source.manual_reason,
                "raw_file_format": source.file_format,
                "raw_file_path": manifest_row.get("relative_path"),
                "original_filename": manifest_row.get("original_filename"),
                "retrieval_date": manifest_row.get("retrieved_at"),
                "provider_vintage": manifest_row.get("provider_last_updated")
                or manifest_row.get("source_last_updated")
                or manifest_row.get("data_vintage"),
                "checksum_sha256": manifest_row.get("checksum_sha256"),
                "native_frequency": source.native_frequency,
                "analytical_frequency": source.native_frequency,
                "geography_level": "|".join(source.geography_levels),
                "geography_name": "|".join(source.geography_levels),
                "geography_id": "",
                "start_date": stats["start_date"],
                "end_date": stats["end_date"],
                "row_count": stats["row_count"],
                "variable_ids": "|".join(variables),
                "seasonal_adjustment": "",
                "smoothing": "",
                "units": "",
                "destination_clean_table": _source_clean_name(source),
                "destination_analytical_table": source.destination_analytical_table
                or "analytics.fact_housing_observation",
                "update_command": f"python run_pipeline.py --stage download --source {source.source_id} --force-download",
                "missing_value_policy": "preserve null",
                "revision_policy": source.revision_notes,
                "known_limitations": source.methodology_notes,
                "license_or_use_notes": source.license_or_use_notes,
                "last_verified": config.verified_at,
                "notes": "Raw file exists" if raw_path and raw_path.exists() else "No local raw file",
            }
        )
    first_columns = ["Source", "Frequency", "Geography", "Key variables"]
    catalog = pd.DataFrame(rows)
    return catalog[first_columns + [col for col in catalog.columns if col not in first_columns]]


def _raw_repository_completeness(config: ProjectConfig) -> pd.DataFrame:
    manifest = _latest_manifest_rows(config)
    registry_ids = {source.source_id for source in config.sources}
    rows = []
    for source in config.sources:
        manifest_row = (
            manifest.loc[source.source_id].to_dict()
            if source.source_id in manifest.index
            else {}
        )
        raw_path = _raw_path(config, manifest_row)
        checks = {
            "raw_snapshot_present": bool(raw_path and raw_path.exists()),
            "manifest_present": source.source_id in manifest.index,
            "registry_present": source.source_id in registry_ids,
            "checksum_present": bool(manifest_row.get("checksum_sha256")),
            "retrieval_timestamp_present": bool(manifest_row.get("retrieved_at")),
            "source_url_present": bool(source.download_url or source.source_page_url),
            "frequency_present": bool(source.native_frequency),
            "geography_present": bool(source.geography_levels),
            "key_variables_present": bool(source.key_variables),
        }
        issues = [name for name, ok in checks.items() if not ok]
        rows.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "dataset_name": source.dataset_name,
                "active_status": "Active" if source.enabled else "Inactive",
                **checks,
                "completeness_status": "pass" if not issues else "fail",
                "issue": "|".join(issues),
            }
        )
    return pd.DataFrame(rows)


def _proxy_methodology_assumptions() -> pd.DataFrame:
    rows = [
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "ACS B25106 table role",
            "value": "B25106 owner income x housing-cost-burden bins are used to infer monthly owner housing costs; B25106 is not treated as observed mortgage balance.",
            "rationale": "The Housing Variables sheet requests an implied balance workaround, not a direct balance observation.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Income bin midpoints",
            "value": "10000; 27500; 42500; 62500; 112500",
            "rationale": "Closed bins use midpoint values. The open-ended $75k+ bin uses $112,500, or 1.5 times the lower bound, as a transparent conservative top-bin assumption.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Housing-cost-burden midpoints",
            "value": "0.10 for <20%; 0.25 for 20-29%; 0.40 for 30%+",
            "rationale": "Closed or semi-closed categories use practical midpoints; 30%+ uses 40% to avoid implying unlimited burden while retaining high-burden signal.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Debt-service share of ACS owner housing cost",
            "value": "0.80",
            "rationale": "ACS owner housing costs include non-debt items such as taxes, insurance, utilities, and fees; 80% assigns most but not all inferred owner cost to mortgage debt service.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Mortgage-rate assumption",
            "value": "Mean Freddie Mac PMMS monthly-average 30-year fixed rate over the ACS five-year estimate window.",
            "rationale": "The ACS 5-year estimate pools observations over five years; using the same window avoids using only one endpoint-year rate.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Remaining amortization term",
            "value": "25 years",
            "rationale": "Outstanding balances are on existing mortgages, not all new 30-year loans; 25 years is a transparent middle-ground remaining-term assumption.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Allocation of B25081 mortgaged units",
            "value": "B25081 owner-with-mortgage count is allocated across positive-income B25106 bins proportional to B25106 owner unit counts.",
            "rationale": "B25106 bins are not mortgage-status-specific. This forces aggregate mortgaged units to match the directly observed B25081 count while preserving the B25106 income/burden distribution.",
        },
        {
            "methodology_id": "acs_implied_mortgage_debt_proxy_v1",
            "variable_id": "acs_implied_aggregate_mortgage_debt_proxy",
            "assumption": "Zero/negative income handling",
            "value": "B25106 zero/negative-income owner units are preserved as an input but excluded from payment-to-balance inference.",
            "rationale": "A positive income midpoint is required to infer monthly owner housing cost from cost-burden percentages.",
        },
        {
            "methodology_id": "eviction_lab_county_estimates_v1",
            "variable_id": "eviction_lab_filings_estimate_annual",
            "assumption": "LA eviction-source choice",
            "value": "Use official Eviction Lab annual county modeled estimates for Los Angeles County because ETS and the observed/validated county analysis files do not provide LA County rows.",
            "rationale": "This preserves the expected Eviction Lab source family without silently substituting another geography, and keeps the limitation explicit for modeling.",
        },
        {
            "methodology_id": "eviction_lab_county_rate_v1",
            "variable_id": "eviction_lab_filings_rate_estimate_annual",
            "assumption": "Annual rate denominator",
            "value": "filings_estimate divided by Eviction Lab renting_hh, multiplied by 100.",
            "rationale": "A rate normalizes the trend for renter-household base changes while using only fields published in the same official Eviction Lab file.",
        },
        {
            "methodology_id": "eviction_lab_county_rate_v1",
            "variable_id": "eviction_lab_households_threatened_rate_estimate_annual",
            "assumption": "Annual rate denominator",
            "value": "hh_threat_estimate divided by Eviction Lab renting_hh, multiplied by 100.",
            "rationale": "A rate normalizes households-threatened estimates for renter-household base changes while using only fields published in the same official Eviction Lab file.",
        },
    ]
    return pd.DataFrame(rows)


def _source_tracker_summary(catalog: pd.DataFrame) -> pd.DataFrame:
    """Compact, human-readable source tracker sheet.

    The full data catalog intentionally keeps every reproducibility field. This
    sheet is the first place a mentor/reviewer should look when skimming what
    data exists, where it came from, and how current it is.
    """
    columns = [
        "Source",
        "provider",
        "active_status",
        "Frequency",
        "Geography",
        "Key variables",
        "start_date",
        "end_date",
        "row_count",
        "observed_constructed_proxy_or_scenario",
        "retrieval_date",
        "raw_file_path",
        "update_command",
        "known_limitations",
    ]
    labels = {
        "provider": "Provider",
        "active_status": "Status",
        "start_date": "Start date",
        "end_date": "End date",
        "row_count": "Rows",
        "observed_constructed_proxy_or_scenario": "Observed / proxy / scenario",
        "retrieval_date": "Retrieved at",
        "raw_file_path": "Raw file",
        "update_command": "Refresh command",
        "known_limitations": "Limitations",
    }
    available = [column for column in columns if column in catalog.columns]
    return catalog.loc[:, available].rename(columns=labels)


def _source_tracker_overview(
    catalog: pd.DataFrame,
    audit: pd.DataFrame,
    dictionary: pd.DataFrame,
    coverage: pd.DataFrame,
    manifest: pd.DataFrame,
    raw_completeness: pd.DataFrame,
    gap: pd.DataFrame,
    assumptions_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Workbook landing page explaining how to read the source tracker."""
    active_sources = int(catalog["active_status"].eq("Active").sum())
    completeness_pass = int(raw_completeness["completeness_status"].eq("pass").sum())
    completeness_fail = int(raw_completeness["completeness_status"].eq("fail").sum())
    earliest = catalog["start_date"].dropna().min()
    latest = catalog["end_date"].dropna().max()
    return pd.DataFrame(
        [
            {
                "Item": "Purpose",
                "Value": (
                    "Human-readable tracker for current Los Angeles housing "
                    "database requested variables, active sources, coverage, "
                    "raw-file completeness, gaps, and proxy assumptions."
                ),
            },
            {
                "Item": "Generated by",
                "Value": "python run_pipeline.py --stage catalog",
            },
            {
                "Item": "Do not hand edit",
                "Value": (
                    "This workbook is regenerated from config, source manifests, "
                    "DuckDB coverage, and pipeline metadata. Change the pipeline "
                    "inputs/code, then regenerate."
                ),
            },
            {
                "Item": "How to skim",
                "Value": (
                    "Start with Source Summary for provider/source status, "
                    "DOCX Variable Audit for requested-variable status, "
                    "Variable Dictionary for active variable definitions, "
                    "Coverage Summary for date/geography coverage, Data Catalog "
                    "for full source metadata, Raw Completeness for archival "
                    "checks, Missing Deferred for gaps, and Proxy Assumptions "
                    "for constructed variables."
                ),
            },
            {
                "Item": "Tracker scope",
                "Value": (
                    "This workbook tracks metadata, source coverage, variable "
                    "definitions, and implementation status. It does not duplicate "
                    "every observation row; the observations live in DuckDB, "
                    "Parquet, and CSV exports."
                ),
            },
            {
                "Item": "Eviction Trends location",
                "Value": (
                    "Requested variable row: DOCX Variable Audit. Source row: "
                    "Data Catalog source_id eviction_lab_county_estimates. "
                    "Implemented variable coverage: Coverage Summary rows whose "
                    "variable_id starts with eviction_lab_."
                ),
            },
            {"Item": "Catalog source rows", "Value": len(catalog)},
            {"Item": "DOCX variable audit rows", "Value": len(audit)},
            {"Item": "Active variable dictionary rows", "Value": len(dictionary)},
            {"Item": "Coverage summary rows", "Value": len(coverage)},
            {"Item": "Raw manifest rows", "Value": len(manifest)},
            {"Item": "Active sources", "Value": active_sources},
            {"Item": "Raw completeness pass", "Value": completeness_pass},
            {"Item": "Raw completeness fail", "Value": completeness_fail},
            {"Item": "Deferred / missing rows", "Value": len(gap)},
            {"Item": "Proxy assumption rows", "Value": len(assumptions_frame)},
            {"Item": "Earliest catalog date", "Value": earliest},
            {"Item": "Latest catalog date", "Value": latest},
        ]
    )


def _excel_table_name(sheet_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", sheet_name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or not re.match(r"[A-Za-z_]", name):
        name = f"Table_{name}"
    return f"{name[:240]}Table"


def _format_tracker_sheet(
    worksheet,
    *,
    tab_color: str,
    table_style: str = "TableStyleMedium2",
) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_properties.tabColor = tab_color
    worksheet.freeze_panes = "A2"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    header_alignment = Alignment(
        horizontal="center",
        vertical="center",
        wrap_text=True,
    )
    thin_border = Border(
        bottom=Side(style="thin", color="B7C9D6"),
    )

    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        cell.border = thin_border
    worksheet.row_dimensions[1].height = 34

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    if worksheet.max_row > 1 and worksheet.max_column > 1:
        ref = (
            f"A1:{get_column_letter(worksheet.max_column)}"
            f"{worksheet.max_row}"
        )
        table = Table(displayName=_excel_table_name(worksheet.title), ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name=table_style,
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        worksheet.add_table(table)
        worksheet.auto_filter.ref = ref

    long_text_markers = {
        "url",
        "command",
        "limitation",
        "notes",
        "rationale",
        "value",
        "variables",
        "path",
        "filename",
        "issue",
        "detail",
        "workaround",
        "source",
    }
    for column_index in range(1, worksheet.max_column + 1):
        letter = get_column_letter(column_index)
        header = str(worksheet.cell(row=1, column=column_index).value or "")
        sample_values = [
            worksheet.cell(row=row, column=column_index).value
            for row in range(1, min(worksheet.max_row, 80) + 1)
        ]
        max_length = max(
            [len(str(value)) for value in sample_values if value is not None]
            or [len(header)]
        )
        cap = 62 if any(marker in header.lower() for marker in long_text_markers) else 28
        worksheet.column_dimensions[letter].width = max(12, min(cap, max_length + 2))


def _format_source_tracker_workbook(path: Path) -> None:
    workbook = load_workbook(path)
    tab_colors = {
        "Overview": "17365D",
        "Source Summary": "2F75B5",
        "DOCX Variable Audit": "4472C4",
        "Variable Dictionary": "9E480E",
        "Coverage Summary": "00B0F0",
        "Data Catalog": "5B9BD5",
        "Raw File Inventory": "548235",
        "Raw Completeness": "70AD47",
        "Missing Deferred": "FFC000",
        "Proxy Assumptions": "A64D79",
        "Update Commands": "8064A2",
    }
    for worksheet in workbook.worksheets:
        _format_tracker_sheet(
            worksheet,
            tab_color=tab_colors.get(worksheet.title, "808080"),
        )
    workbook.active = 0
    workbook.save(path)


def _write_source_tracker_workbook(
    path: Path,
    catalog: pd.DataFrame,
    audit: pd.DataFrame,
    dictionary: pd.DataFrame,
    manifest: pd.DataFrame,
    coverage: pd.DataFrame,
    raw_completeness: pd.DataFrame,
    gap: pd.DataFrame,
    assumptions_frame: pd.DataFrame,
) -> None:
    overview = _source_tracker_overview(
        catalog,
        audit,
        dictionary,
        coverage,
        manifest,
        raw_completeness,
        gap,
        assumptions_frame,
    )
    source_summary = _source_tracker_summary(catalog)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        overview.to_excel(writer, sheet_name="Overview", index=False)
        source_summary.to_excel(writer, sheet_name="Source Summary", index=False)
        audit.to_excel(writer, sheet_name="DOCX Variable Audit", index=False)
        dictionary.to_excel(writer, sheet_name="Variable Dictionary", index=False)
        coverage.to_excel(writer, sheet_name="Coverage Summary", index=False)
        catalog.to_excel(writer, sheet_name="Data Catalog", index=False)
        manifest.to_excel(writer, sheet_name="Raw File Inventory", index=False)
        raw_completeness.to_excel(writer, sheet_name="Raw Completeness", index=False)
        gap.to_excel(writer, sheet_name="Missing Deferred", index=False)
        assumptions_frame.to_excel(
            writer,
            sheet_name="Proxy Assumptions",
            index=False,
        )
        catalog[["source_id", "update_command"]].to_excel(
            writer,
            sheet_name="Update Commands",
            index=False,
        )
    _format_source_tracker_workbook(path)


def _write_workbooks(
    config: ProjectConfig,
    catalog: pd.DataFrame,
    audit: pd.DataFrame,
    gap: pd.DataFrame,
    raw_completeness: pd.DataFrame,
    assumptions_frame: pd.DataFrame,
) -> None:
    metadata_dir = config.root / "metadata"
    manifest = _raw_manifest(config)
    dictionary = pd.DataFrame(config.variables)
    requirements = pd.DataFrame(DOCX_VARIABLES)
    geography_matrix = (
        catalog.groupby(["Frequency", "Geography"])
        .size()
        .rename("source_count")
        .reset_index()
    )
    coverage_path = metadata_dir / "source_coverage.csv"
    coverage = pd.read_csv(coverage_path) if coverage_path.exists() else pd.DataFrame()
    assumptions = audit.loc[
        audit["observed_constructed_proxy_or_scenario"].isin(
            {"proxy", "scenario assumption", "deferred", "unavailable"}
        ),
        [
            "variable_name",
            "observed_constructed_proxy_or_scenario",
            "limitation",
            "recommended_next_action",
        ],
    ]
    with pd.ExcelWriter(metadata_dir / "data_catalog.xlsx", engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Data Catalog", index=False)
        dictionary.to_excel(writer, sheet_name="Variable Dictionary", index=False)
        requirements.to_excel(writer, sheet_name="DOCX Requirements", index=False)
        audit.to_excel(writer, sheet_name="DOCX Variable Audit", index=False)
        assumptions_frame.to_excel(
            writer, sheet_name="Proxy Assumptions", index=False
        )
        manifest.to_excel(writer, sheet_name="Raw File Inventory", index=False)
        geography_matrix.to_excel(writer, sheet_name="Geography-Frequency Matrix", index=False)
        coverage.to_excel(writer, sheet_name="Coverage Summary", index=False)
        audit.loc[audit["docx_category"].eq("Difficult Variables")].to_excel(
            writer,
            sheet_name="Difficult Variables",
            index=False,
        )
        gap.to_excel(writer, sheet_name="Missing Deferred", index=False)
        catalog[["source_id", "update_command"]].to_excel(
            writer,
            sheet_name="Update Commands",
            index=False,
        )
        assumptions.to_excel(writer, sheet_name="Assumptions Questions", index=False)
    with pd.ExcelWriter(metadata_dir / "docx_variable_audit.xlsx", engine="openpyxl") as writer:
        requirements.to_excel(writer, sheet_name="DOCX Requirements", index=False)
        audit.to_excel(writer, sheet_name="DOCX Variable Audit", index=False)
        assumptions_frame.to_excel(
            writer, sheet_name="Proxy Assumptions", index=False
        )
    _write_source_tracker_workbook(
        metadata_dir / "LA_Housing_Source_Tracker.xlsx",
        catalog,
        audit,
        dictionary,
        manifest,
        coverage,
        raw_completeness,
        gap,
        assumptions_frame,
    )


def _sync_raw_completeness_to_duckdb(
    config: ProjectConfig,
    raw_completeness: pd.DataFrame,
) -> None:
    database_path = config.root / config.pipeline["database_path"]
    if not database_path.exists():
        return
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS quality")
        connection.register("raw_completeness_frame", raw_completeness)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.raw_repository_completeness AS "
            "SELECT * FROM raw_completeness_frame"
        )


def build_catalog_outputs(config: ProjectConfig) -> dict[str, int]:
    """Write audit/catalog outputs for the Word checklist and active repository."""
    metadata_dir = config.root / "metadata"
    qa_dir = config.root / "output" / "qa"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)
    fact = _load_fact(config)
    audit = _docx_audit(config, fact)
    catalog = _data_catalog(config, fact)
    raw_completeness = _raw_repository_completeness(config)
    assumptions_frame = _proxy_methodology_assumptions()
    gap = audit.loc[
        ~audit["current_status"].isin(
            {"Implemented", "Scenario implemented", "Proxy implemented"}
        )
    ].copy()
    requirements = pd.DataFrame(DOCX_VARIABLES)
    requirements.to_csv(metadata_dir / "docx_variable_requirements.csv", index=False)
    assumptions_frame.to_csv(
        metadata_dir / "proxy_methodology_assumptions.csv", index=False
    )
    audit.to_csv(metadata_dir / "docx_variable_audit.csv", index=False)
    catalog.to_csv(metadata_dir / "data_catalog.csv", index=False)
    gap.to_csv(metadata_dir / "data_gap_analysis.csv", index=False)
    catalog.to_csv(metadata_dir / "source_discovery.csv", index=False)
    raw_completeness.to_csv(qa_dir / "raw_repository_completeness.csv", index=False)
    _write_workbooks(config, catalog, audit, gap, raw_completeness, assumptions_frame)
    _sync_raw_completeness_to_duckdb(config, raw_completeness)
    return {
        "catalog_rows": len(catalog),
        "audit_rows": len(audit),
        "gap_rows": len(gap),
        "raw_completeness_rows": len(raw_completeness),
    }


# Compatibility catalog retained for the structural-variable pipeline.
CATALOG_COLUMNS = [
    "Source",
    "Frequency",
    "Geography",
    "Key variables",
    "source_id",
    "provider",
    "product",
    "official_source_page",
    "exact_download_url_or_api",
    "access_method",
    "active_status",
    "manual_download_required",
    "raw_file_format",
    "raw_file_path",
    "original_filename",
    "retrieval_date",
    "provider_vintage",
    "checksum_sha256",
    "native_frequency",
    "analytical_frequency",
    "geography_level",
    "geography_name",
    "geography_id",
    "start_date",
    "end_date",
    "row_count",
    "variable_ids",
    "seasonal_adjustment",
    "smoothing",
    "units",
    "destination_clean_table",
    "destination_analytical_table",
    "update_command",
    "missing_value_policy",
    "revision_policy",
    "known_limitations",
    "license_or_use_notes",
    "last_verified",
    "notes",
]

UNAVAILABLE_ROWS = [
    {
        "Source": "Rental listing inventory",
        "Frequency": "Unknown",
        "Geography": "Los Angeles County or ZIP",
        "Key variables": "rental_inventory",
        "source_id": "unavailable_rental_inventory",
        "provider": "No verified official public source",
        "product": "Rental listing inventory",
        "active_status": "Missing — no verified public source",
        "known_limitations": (
            "ZORI is a rent index, not inventory. A documented public listing "
            "universe, stable geography, history, and revision policy are required."
        ),
    },
    {
        "Source": "Income-based affordability ratios",
        "Frequency": "Annual or mixed",
        "Geography": "Los Angeles County or ZIP",
        "Key variables": "price_to_income|rent_to_income",
        "source_id": "deferred_income_affordability_ratios",
        "provider": "Requires explicit income denominator decision",
        "product": "Price-to-income and rent-to-income ratios",
        "active_status": "Deferred",
        "known_limitations": (
            "The current active stage uses rent/payment affordability metrics. "
            "Income-denominator ratios are later-stage work and are not exposed "
            "as active variables."
        ),
    },
    {
        "Source": "Treasury rate and mortgage spread",
        "Frequency": "Daily or weekly",
        "Geography": "United States",
        "Key variables": "treasury_rate|mortgage_spread",
        "source_id": "deferred_treasury_mortgage_spread",
        "provider": "U.S. Treasury or Federal Reserve",
        "product": "Treasury benchmark and constructed mortgage spread",
        "active_status": "Deferred",
        "known_limitations": "Optional control outside the current requested core.",
    },
]


def _manifest(config: ProjectConfig) -> pd.DataFrame:
    path = config.root / "metadata" / "raw_file_manifest.csv"
    frame = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()
    if not frame.empty:
        active_ids = {source.source_id for source in config.sources}
        frame = frame.loc[frame["source_id"].isin(active_ids)].copy()
    if not frame.empty:
        frame["_retrieved"] = pd.to_datetime(
            frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
        )
    return frame


def build_raw_repository_completeness(
    config: ProjectConfig,
) -> pd.DataFrame:
    manifest = _manifest(config)
    database_path = config.root / config.pipeline["database_path"]
    registry_ids: set[str] = set()
    if database_path.exists():
        connection = duckdb.connect(str(database_path), read_only=True)
        registry_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_id FROM metadata.source_registry"
            ).fetchall()
        }
        connection.close()
    rows: list[dict] = []
    for source in config.sources:
        matches = (
            manifest.loc[manifest["source_id"].eq(source.source_id)]
            if not manifest.empty
            else pd.DataFrame()
        )
        paths = (
            [
                config.root / value
                for value in matches.get(
                    "relative_path", pd.Series(dtype=str)
                ).dropna()
            ]
            if not matches.empty
            else []
        )
        checks = {
            "raw_snapshot_present": bool(paths)
            and any(path.is_file() for path in paths),
            "manifest_present": not matches.empty,
            "registry_present": source.source_id in registry_ids,
            "checksum_present": not matches.empty
            and matches["checksum_sha256"].fillna("").str.len().eq(64).any(),
            "retrieval_timestamp_present": not matches.empty
            and pd.to_datetime(
                matches["retrieved_at"],
                utc=True,
                errors="coerce",
                format="mixed",
            )
            .notna()
            .any(),
            "source_url_present": bool(
                source.source_page_url and source.download_url
            ),
            "frequency_present": bool(source.native_frequency),
            "geography_present": bool(source.geography_levels),
            "key_variables_present": bool(source.key_variables),
        }
        issues = [name for name, passed in checks.items() if not passed]
        rows.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "active_status": (
                    "ACTIVE_MANUAL" if not source.auto_download else "ACTIVE"
                ),
                **checks,
                "completeness_status": "PASS" if not issues else "FAIL",
                "issue": "|".join(issues),
            }
        )
    result = pd.DataFrame(rows)
    output = config.root / "output" / "qa" / "raw_repository_completeness.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("raw_completeness_frame", result)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.raw_repository_completeness AS "
            "SELECT * FROM raw_completeness_frame"
        )
        connection.close()
    return result


def _coverage(config: ProjectConfig) -> pd.DataFrame:
    database_path = config.root / config.pipeline["database_path"]
    if not database_path.exists():
        return pd.DataFrame()
    connection = duckdb.connect(str(database_path), read_only=True)
    frame = connection.execute(
        """
        SELECT
            source_filename,
            min(observation_date) AS start_date,
            max(observation_date) AS end_date,
            count(*) AS row_count,
            string_agg(DISTINCT variable_id, '|' ORDER BY variable_id)
                AS variable_ids,
            string_agg(DISTINCT final_frequency, '|' ORDER BY final_frequency)
                AS analytical_frequency,
            string_agg(DISTINCT unit, '|' ORDER BY unit) AS units
        FROM analytics.fact_housing_observation
        GROUP BY source_filename
        """
    ).df()
    connection.close()
    return frame


def _latest_manifest_by_source(manifest: pd.DataFrame) -> pd.DataFrame:
    if manifest.empty:
        return manifest
    return (
        manifest.sort_values("_retrieved")
        .groupby("source_id", as_index=False)
        .tail(1)
        .set_index("source_id")
    )


def _variable_text(config: ProjectConfig, variable_ids: tuple[str, ...]) -> dict:
    variables = {
        str(item["variable_id"]): item for item in config.variables
    }
    selected = [
        variables[variable_id]
        for variable_id in variable_ids
        if variable_id in variables
    ]
    return {
        "seasonal_adjustment": "|".join(
            sorted(
                {
                    str(item.get("seasonal_adjustment", ""))
                    for item in selected
                    if item.get("seasonal_adjustment")
                }
            )
        ),
        "smoothing": "|".join(
            sorted(
                {
                    str(item.get("smoothing", ""))
                    for item in selected
                    if item.get("smoothing")
                }
            )
        ),
        "units": "|".join(
            sorted(
                {
                    str(item.get("unit", ""))
                    for item in selected
                    if item.get("unit")
                }
            )
        ),
        "missing_value_policy": "|".join(
            sorted(
                {
                    str(item.get("missing_value_policy", ""))
                    for item in selected
                    if item.get("missing_value_policy")
                }
            )
        ),
    }


def _geography_description(source_id: str, levels: tuple[str, ...]) -> tuple[str, str]:
    if "zillow_zip" in source_id or source_id == "redfin_zip_monthly":
        return "Los Angeles County postal ZIPs", "POSTAL_ZIP"
    if "county" in levels:
        return "Los Angeles County", "06037"
    if "state" in levels:
        return "California", "06"
    if "metro" in levels:
        if "fhfa" in source_id:
            return "Los Angeles-Long Beach-Glendale Metropolitan Division", "31084"
        return "Los Angeles published metro/area", "31080|S49A"
    if "national" in levels:
        return "United States", "US"
    return "|".join(levels), ""


def _catalog_frame(config: ProjectConfig) -> pd.DataFrame:
    manifest = _manifest(config)
    latest = _latest_manifest_by_source(manifest)
    coverage = _coverage(config)
    rows: list[dict] = []
    for source in config.sources:
        raw = (
            latest.loc[source.source_id].to_dict()
            if not latest.empty and source.source_id in latest.index
            else {}
        )
        source_files = (
            set(
                manifest.loc[
                    manifest["source_id"].eq(source.source_id),
                    "source_filename",
                ].dropna()
            )
            if not manifest.empty
            else set()
        )
        source_coverage = (
            coverage.loc[coverage["source_filename"].isin(source_files)]
            if not coverage.empty
            else pd.DataFrame()
        )
        variable_ids = tuple(source.key_variables)
        variable_text = _variable_text(config, variable_ids)
        geography_name, geography_id = _geography_description(
            source.source_id, source.geography_levels
        )
        if source_coverage.empty:
            start_date = end_date = analytical_frequency = ""
            row_count = 0
            coverage_units = ""
        else:
            start_date = source_coverage["start_date"].min()
            end_date = source_coverage["end_date"].max()
            row_count = int(source_coverage["row_count"].sum())
            analytical_frequency = "|".join(
                sorted(
                    {
                        value
                        for text in source_coverage[
                            "analytical_frequency"
                        ].dropna()
                        for value in str(text).split("|")
                    }
                )
            )
            coverage_units = "|".join(
                sorted(
                    {
                        value
                        for text in source_coverage["units"].dropna()
                        for value in str(text).split("|")
                    }
                )
            )
        command = (
            "python run_pipeline.py --stage download --source "
            f"{source.source_id} --force-download"
            if source.auto_download
            else "python run_pipeline.py --stage download --source "
            f"{source.source_id} --local-file PATH_TO_OFFICIAL_FILE"
        )
        if end_date:
            provider_vintage = f"data through {str(end_date)[:10]}"
        else:
            provider_vintage = raw.get("data_vintage", "")
        rows.append(
            {
                "Source": source.dataset_name,
                "Frequency": source.native_frequency,
                "Geography": geography_name,
                "Key variables": "|".join(variable_ids),
                "source_id": source.source_id,
                "provider": source.provider,
                "product": source.dataset_name,
                "official_source_page": source.source_page_url,
                "exact_download_url_or_api": source.download_url,
                "access_method": source.access_method,
                "active_status": "Active",
                "manual_download_required": not source.auto_download,
                "raw_file_format": source.file_format,
                "raw_file_path": raw.get("relative_path", ""),
                "original_filename": raw.get("original_filename", ""),
                "retrieval_date": raw.get("retrieved_at", ""),
                "provider_vintage": provider_vintage,
                "checksum_sha256": raw.get("checksum_sha256", ""),
                "native_frequency": source.native_frequency,
                "analytical_frequency": (
                    analytical_frequency or source.native_frequency
                ),
                "geography_level": "|".join(source.geography_levels),
                "geography_name": geography_name,
                "geography_id": geography_id,
                "start_date": start_date,
                "end_date": end_date,
                "row_count": row_count,
                "variable_ids": "|".join(variable_ids),
                "seasonal_adjustment": variable_text["seasonal_adjustment"],
                "smoothing": variable_text["smoothing"],
                "units": coverage_units or variable_text["units"],
                "destination_clean_table": source.destination_clean_table
                or f"clean.{source.source_id}",
                "destination_analytical_table": (
                    source.destination_analytical_table
                ),
                "update_command": command,
                "missing_value_policy": variable_text[
                    "missing_value_policy"
                ],
                "revision_policy": source.revision_notes,
                "known_limitations": (
                    source.methodology_notes or source.revision_notes
                ),
                "license_or_use_notes": source.license_or_use_notes,
                "last_verified": config.verified_at,
                "notes": (
                    "Identifiers remain strings; raw vintages are immutable."
                ),
            }
        )
    for unavailable in UNAVAILABLE_ROWS:
        row = {column: "" for column in CATALOG_COLUMNS}
        row.update(unavailable)
        row["last_verified"] = config.verified_at
        row["notes"] = "No placeholder observations are created."
        rows.append(row)
    return pd.DataFrame(rows).reindex(columns=CATALOG_COLUMNS)


def _gap_row(
    variable_name: str,
    market_segment: str,
    variable_role: str,
    requested_source: str,
    official_source_found: str,
    implementation_status: str,
    current_database_table: str,
    current_database_column: str,
    native_frequency: str,
    geography: str,
    available_start_date: str = "",
    available_end_date: str = "",
    limitation: str = "",
    recommended_action: str = "",
) -> dict[str, str]:
    return {
        "variable_name": variable_name,
        "market_segment": market_segment,
        "variable_role": variable_role,
        "requested_source": requested_source,
        "official_source_found": official_source_found,
        "implementation_status": implementation_status,
        "current_database_table": current_database_table,
        "current_database_column": current_database_column,
        "native_frequency": native_frequency,
        "geography": geography,
        "available_start_date": available_start_date,
        "available_end_date": available_end_date,
        "limitation": limitation,
        "recommended_action": recommended_action,
    }


def _refresh_gap_analysis(config: ProjectConfig) -> pd.DataFrame:
    rows = [
        _gap_row("ZHVI", "Homeowner", "Outcome", "Zillow Research", "Yes", "Implemented", "analytics.county_monthly_panel", "zhvi", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Provider modeled index; histories can be revised.", "Keep as the local home-value index."),
        _gap_row("FHFA HPI", "Homeowner", "Benchmark", "FHFA", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.metro_quarterly_panel|analytics.county_annual_controls", "FHFA HPI columns", "quarterly and annual", "California; Los Angeles metro; Los Angeles County", "1975-12-31", "2026-03-31", "Products differ by geography, frequency, method, and seasonal adjustment.", "Retain at native frequency."),
        _gap_row("median sale price", "Homeowner", "Outcome", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "median_sale_price", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Transaction mix can change the median.", "Retain as a transaction-price indicator."),
        _gap_row("inventory", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "active_inventory", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "For-sale inventory only; not rental inventory.", "Keep the label explicit."),
        _gap_row("active listings", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.zip_monthly_panel", "active_listings", "rolling 3 months", "Los Angeles County postal ZIPs", "2019-06-30", "2026-05-31", "ZIP product uses overlapping rolling-three-month windows.", "Keep separate from county inventory."),
        _gap_row("months of supply", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "months_of_supply", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Provider ratio; not additive.", "Retain provider value."),
        _gap_row("new listings", "Homeowner", "Leading indicator", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "new_listings", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "ZIP observations are rolling windows.", "Use county monthly for time-series models."),
        _gap_row("homes sold", "Homeowner", "Demand", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "homes_sold", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "Closed-sales flow; ZIP observations are rolling windows.", "Use with inventory ratios."),
        _gap_row("days on market", "Overall housing market", "Market time", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "days_on_market|median_days_on_market", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "Median; not additive.", "Expose as market-time indicator."),
        _gap_row("share with price drops", "Overall housing market", "Leading indicator", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "price_drop_share", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Share, not count.", "Retain nulls and document coverage."),
        _gap_row("30-year mortgage rate", "Homeowner", "Control", "Freddie Mac PMMS", "Yes", "Implemented", "analytics.county_monthly_panel", "mortgage_rate_30y_monthly_average", "weekly aggregated to monthly", "United States", "1971-04-02", "2026-06-25", "National rate; not local.", "Keep U.S. geography explicit."),
        _gap_row("single-family permits", "Homeowner", "Supply", "Census BPS", "Yes", "Implemented", "analytics.county_monthly_panel", "permits_1_unit", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Permits are authorizations, not starts or completions.", "Retain as the single-family supply proxy."),
        _gap_row("monthly mortgage payment", "Homeowner", "Constructed indicator", "Redfin and Freddie Mac", "Yes", "Constructed variable", "analytics.county_monthly_panel", "mortgage_payment_10pct_down|mortgage_payment_20pct_down", "monthly", "Los Angeles County with U.S. rate", "2012-01-31", "2026-05-31", "Principal-and-interest scenarios exclude taxes, insurance, HOA, maintenance, and mortgage insurance.", "Keep scenario assumptions visible."),
        _gap_row("ZORI", "Renter", "Outcome", "Zillow Research", "Yes", "Implemented", "analytics.county_monthly_panel", "zori", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Smoothed seasonally adjusted rent index; not inventory.", "Use as primary rent index."),
        _gap_row("rent growth", "Renter", "Constructed indicator", "Zillow ZORI", "Yes", "Constructed variable", "analytics.county_monthly_panel", "zori_growth_mom|zori_growth_yoy", "monthly", "Los Angeles County", "2015-02-28", "2026-05-31", "Depends on the active SA ZORI vintage.", "Rebuild when ZORI refreshes."),
        _gap_row("rental inventory", "Renter", "Supply", "No verified official public source", "No", "Missing — no verified public source", "", "", "unknown", "Los Angeles County or ZIPs", "", "", "ZORI measures rent levels and cannot serve as rental inventory.", "Leave unavailable until a documented public source is verified."),
        _gap_row("rental vacancy", "Renter", "Supply", "Census HVS", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.metro_quarterly_panel", "hvs_rental_vacancy_rate_ca|hvs_rental_vacancy_rate_la_metro", "quarterly", "California and published metro geographies", "2005-03-31", "2026-03-31", "Los Angeles County is not an HVS published geography; sampling uncertainty is retained.", "Retain native geography/frequency."),
        _gap_row("multifamily permits", "Renter", "Supply", "Census BPS", "Yes", "Implemented", "analytics.county_monthly_panel", "permits_5_plus_units", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Permits are authorizations, not completed or occupied rental units.", "Retain as multifamily supply proxy."),
        _gap_row("price-to-rent", "Overall housing market", "Constructed indicator", "Zillow ZHVI and ZORI", "Yes", "Constructed variable", "analytics.county_monthly_panel", "price_to_rent_ratio", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Index-based valuation ratio.", "Keep explicit formula."),
        _gap_row("inventory-to-sales", "Overall housing market", "Constructed indicator", "Redfin Data Center", "Yes", "Constructed variable", "analytics.county_monthly_panel", "inventory_to_sales_ratio|sales_to_inventory_ratio", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Uses for-sale inventory and sales, not total housing stock.", "Keep both orientations clearly named."),
        _gap_row("market tightness", "Overall housing market", "Constructed indicator", "Redfin Data Center", "Yes", "Constructed variable", "analytics.county_monthly_panel", "market_tightness_index|market_tightness_components", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Composite, not official provider statistic.", "Retain component-count metadata."),
        _gap_row("affordability metrics", "Overall housing market", "Affordability", "Zillow, Redfin, Freddie Mac", "Yes", "Implemented", "analytics.county_monthly_panel", "price_to_rent_ratio|mortgage_payment_to_rent_10pct_down|mortgage_payment_to_rent_20pct_down", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Income-based ratios are deferred at this active stage.", "Do not expose ACS-derived affordability columns until requested."),
        _gap_row("LAUS unemployment rate", "Macro", "Control", "BLS LAUS", "Yes", "Implemented", "analytics.county_monthly_panel", "unemployment_rate", "monthly", "Los Angeles County", "1990-01-31", "2026-05-31", "County series is not seasonally adjusted and may be revised.", "Archive API responses."),
        _gap_row("LAUS labor force", "Macro", "Control", "BLS LAUS", "Yes", "Implemented", "analytics.county_monthly_panel", "labor_force", "monthly", "Los Angeles County", "1990-01-31", "2026-05-31", "County series is not seasonally adjusted and may be revised.", "Archive API responses."),
        _gap_row("CES payroll employment", "Macro", "Control", "BLS CES", "Yes", "Implemented", "analytics.metro_monthly_controls", "ces_total_nonfarm_employment_sa", "monthly", "Los Angeles metro", "2005-01-31", "2026-05-31", "Metro establishment series; not county LAUS employment.", "Retain geography label."),
        _gap_row("personal income", "Macro", "Control", "BEA", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.county_annual_controls", "bea_ca_personal_income_q_sa|bea_la_county_personal_income_annual", "quarterly and annual", "California and Los Angeles County", "1948-03-31", "2026-03-31", "State quarterly and county annual products differ.", "Retain native frequency."),
        _gap_row("CPI / inflation", "Macro", "Control", "BLS CPI", "Yes", "Implemented", "analytics.metro_monthly_controls", "cpi_u_all_items_la_nsa|cpi_inflation_yoy", "monthly with historical gaps", "Los Angeles published area", "1914-12-31", "2026-05-31", "Local CPI is not seasonally adjusted and has historical publication gaps.", "Preserve gaps."),
        _gap_row("price-to-income", "Homeowner", "Affordability", "ACS or BEA", "Yes", "Deferred", "", "", "annual or mixed", "Los Angeles County or ZIP", "", "", "Not part of the current active available-variable set.", "Add later only with an explicit denominator choice."),
    ]
    gap = pd.DataFrame(rows)
    path = config.root / "metadata" / "data_gap_analysis.csv"
    gap.to_csv(path, index=False)
    return gap


def _format_workbook(path: Path, build_version: str) -> None:
    workbook = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    status_colors = {
        "Active": "C6E0B4",
        "Implemented": "C6E0B4",
        "Constructed variable": "D9EAF7",
        "Partially implemented": "FFF2CC",
        "Missing — no verified public source": "F4CCCC",
        "Deferred": "E2F0D9",
        "Proxy only": "E4DFEC",
        "PASS": "C6E0B4",
        "FAIL": "F4CCCC",
    }
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        headers = {
            str(cell.value): cell.column
            for cell in sheet[1]
            if cell.value is not None
        }
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, str) and cell.value in status_colors:
                    cell.fill = PatternFill(
                        "solid", fgColor=status_colors[cell.value]
                    )
            for header, column in headers.items():
                if any(
                    token in header.lower()
                    for token in ("id", "fips", "zip", "checksum")
                ):
                    row[column - 1].number_format = "@"
        for column_index in range(1, sheet.max_column + 1):
            maximum = max(
                len(str(sheet.cell(row, column_index).value or ""))
                for row in range(1, min(sheet.max_row, 250) + 1)
            )
            sheet.column_dimensions[get_column_letter(column_index)].width = min(
                max(maximum + 2, 12), 42
            )
    workbook.properties.creator = "LA housing database pipeline"
    workbook.properties.title = "LA Housing Database Data Catalog"
    workbook.properties.description = (
        "Generated from configuration, raw manifest, DuckDB metadata, coverage, "
        f"and gap analysis. Database build version: {build_version}."
    )
    workbook.properties.keywords = f"database_build_version={build_version}"
    workbook.save(path)


def build_data_catalog(config: ProjectConfig) -> dict[str, int]:
    generated_at = datetime.now(timezone.utc).isoformat()
    database_path = config.root / config.pipeline["database_path"]
    if database_path.exists():
        connection = duckdb.connect(str(database_path), read_only=True)
        fact_rows, latest_vintage = connection.execute(
            """
            SELECT count(*), max(source_vintage)
            FROM analytics.fact_housing_observation
            """
        ).fetchone()
        connection.close()
        build_version = f"{latest_vintage}-{fact_rows}"
    else:
        build_version = "database-not-built"
    completeness = build_raw_repository_completeness(config)
    catalog = _catalog_frame(config)
    gap = _refresh_gap_analysis(config)
    catalog["notes"] = catalog["notes"].map(
        lambda value: (
            f"{value} Catalog generated {generated_at}; "
            f"database build version {build_version}."
        )
    )
    csv_path = config.root / "metadata" / "data_catalog.csv"
    xlsx_path = config.root / "metadata" / "data_catalog.xlsx"
    gap_xlsx = config.root / "metadata" / "data_gap_analysis.xlsx"
    catalog.to_csv(csv_path, index=False)
    manifest = _manifest(config).drop(columns="_retrieved", errors="ignore")
    variables = pd.DataFrame(config.variables)
    matrix = catalog[
        [
            "source_id",
            "active_status",
            "native_frequency",
            "analytical_frequency",
            "geography_level",
            "geography_name",
        ]
    ].copy()
    coverage = catalog[
        [
            "source_id",
            "active_status",
            "start_date",
            "end_date",
            "row_count",
            "variable_ids",
        ]
    ].copy()
    unavailable = catalog.loc[
        ~catalog["active_status"].eq("Active")
    ].copy()
    commands = catalog.loc[
        catalog["active_status"].eq("Active"),
        ["source_id", "provider", "update_command"],
    ].copy()
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Data Catalog", index=False)
        variables.to_excel(
            writer, sheet_name="Variable Dictionary", index=False
        )
        manifest.to_excel(
            writer, sheet_name="Raw File Inventory", index=False
        )
        matrix.to_excel(
            writer, sheet_name="Geography-Frequency Matrix", index=False
        )
        coverage.to_excel(
            writer, sheet_name="Coverage Summary", index=False
        )
        unavailable.to_excel(
            writer, sheet_name="Unavailable and Deferred", index=False
        )
        commands.to_excel(
            writer, sheet_name="Update Commands", index=False
        )
        gap.to_excel(writer, sheet_name="Gap Analysis", index=False)
    _format_workbook(xlsx_path, build_version)
    with pd.ExcelWriter(gap_xlsx, engine="openpyxl") as writer:
        gap.to_excel(writer, sheet_name="Gap Analysis", index=False)
    _format_workbook(gap_xlsx, build_version)
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("data_catalog_frame", catalog)
        connection.register("gap_analysis_frame", gap)
        connection.execute(
            "CREATE OR REPLACE TABLE metadata.data_catalog AS "
            "SELECT * FROM data_catalog_frame"
        )
        connection.execute(
            "CREATE OR REPLACE TABLE metadata.data_gap_analysis AS "
            "SELECT * FROM gap_analysis_frame"
        )
        connection.close()
    failures = int(completeness["completeness_status"].ne("PASS").sum())
    if failures:
        failed = ", ".join(
            completeness.loc[
                completeness["completeness_status"].ne("PASS"), "source_id"
            ]
        )
        raise RuntimeError(
            f"Raw repository completeness failed for {failures} sources: {failed}"
        )
    return {
        "catalog_rows": len(catalog),
        "variable_rows": len(variables),
        "raw_inventory_rows": len(manifest),
        "gap_rows": len(gap),
    }
