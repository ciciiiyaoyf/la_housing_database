"""Catalog, DOCX variable audit, and raw repository completeness outputs."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

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
    "Eviction trends (high-frequency, consistent)": _impl("Not implemented", "", "", "", "", "deferred", "The sheet specifies Eviction Lab ETS where available or annual filings; no eviction source is loaded yet."),
    "New Apartments Completed (county time series)": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_5_plus_units", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_5_plus_units", "proxy input", "The sheet workaround is lagged multifamily permits with an explicit completion-lag structure; permits are loaded, but no completion proxy is calculated."),
    "Multifamily Under Construction": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_5_plus_units", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_5_plus_units", "proxy input", "The sheet workaround is cumulative MF permits minus assumed completions over a rolling window; permit inputs are loaded, but the stock pipeline calculation is not implemented."),
    "Single-Family Rental Construction": _impl("Proxy input only", "census_bps_county|census_bps_state", "permits_1_unit", "analytics.fact_housing_observation|analytics.county_monthly_panel", "permits_1_unit", "proxy input", "The sheet workaround needs SFR permits combined with investor/rental-growth tract logic; only 1-unit permits are loaded."),
    "Net Change in Occupied Apartments (quarterly)": _impl("Partially implemented", "census_acs_5year_b25003", "acs_renter_occupied_units", "analytics.county_annual_controls|analytics.fact_housing_observation", "acs_renter_occupied_units", "observed", "ACS annual 5-year renter-occupied units are available; quarterly net change is not created."),
    "Price-to-Income Ratio (quarterly/local)": _impl("Partially implemented", "census_acs_5year_b19013|census_acs_5year_b25077", "county_annual_price_to_income_acs", "analytics.county_annual_controls", "county_annual_price_to_income_acs", "constructed", "Annual ACS ratio is implemented; quarterly/local alternatives are deferred."),
    "Natural Population Change (quarterly)": _impl("Not implemented", "", "", "", "", "deferred", "The sheet workaround specifies annual CDC WONDER births/deaths; CDC WONDER is not loaded."),
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
    ]
    return pd.DataFrame(rows)


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
    with pd.ExcelWriter(metadata_dir / "LA_Housing_Source_Tracker.xlsx", engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Data Catalog", index=False)
        raw_completeness.to_excel(writer, sheet_name="Raw Completeness", index=False)
        gap.to_excel(writer, sheet_name="Missing Deferred", index=False)
        assumptions_frame.to_excel(
            writer, sheet_name="Proxy Assumptions", index=False
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
