# Current Variable Status

Status date: 2026-08-03

This inventory applies the newest requirements in `LA_CA_Housing_Additional_Variables_Methodology.docx` and then reconciles the earlier methodology/tracker requests. "Available" means that the active repository contains at least one non-null observation in a fact, panel, or benchmark table. A configured column with only null values is labeled schema-only, not available.

The ACS rental-availability source, facts, annual panel, DuckDB database, catalogs, coverage reports, and status workbooks have been rebuilt. Pipeline validation completed with zero structural errors and two warning-class review checks.

## Rental inventory answer

| Concept | Status | Active variables and coverage | Interpretation |
|---|---|---|---|
| True monthly active rental listings | **Not available; intentionally left missing** | No `rental_listing_inventory_monthly` variable | The current methodology requires a documented historical active-rental-listing source with stable coverage and acceptable licensing. ZORI, Redfin for-sale listings, ACS, HVS, and USPS cannot be relabeled as this measure. The workflow is stuck at source verification/licensing, not at a formula. |
| ACS annual rental availability | **Available for 2024** | `acs_renter_occupied_units`, `acs_renter_occupied_units_moe`, `acs_for_rent_vacant_units`, `acs_for_rent_vacant_units_moe` in `analytics.fact_housing_observation`; LA County plus 274 LA ZCTAs. `acs_rental_availability_rate` in `analytics.county_annual_controls`; LA County 2024 = 0.0402385507, quality flag `ok`. | Annual ACS five-year survey proxy: for-rent vacant units / (renter-occupied units + for-rent vacant units). It is not monthly listing inventory. Historical matched B25003/B25004 vintages are not yet archived. |
| HUD-USPS quarterly residential vacancy | **Available** | Eight `usps_*` variables, 2008 Q1-2026 Q1; 58 California counties, 2,494 LA tracts, and 477 modeled LA postal-ZIP county-parts. | Quarterly all-residential administrative address proxy. It is not renter-only. No-stat remains a separate diagnostic. |

## Newest Additional Variables requirements

| Requirement | Exact repository IDs | Availability and coverage | Exact unfinished step |
|---|---|---|---|
| Sale-price closing costs, 2%/3%/5% | Existing legacy IDs: `closing_cost_2pct`, `closing_cost_3pct`, `closing_cost_5pct` | **Partial.** County monthly panel only; 173 non-null LA County months, 2012-01 through 2026-05. | Add or rename to the current canonical IDs `closing_cost_saleprice_2pct`, `closing_cost_saleprice_3pct`, and `closing_cost_saleprice_5pct`; add current scenario metadata and ZIP scenarios if ZIP coverage is required. |
| Loan-based closing costs, 3%/6% | None | **Missing.** | Define which down-payment scenario supplies the estimated mortgage amount, then implement `closing_cost_loan_3pct` and `closing_cost_loan_6pct` in the scenario layer, dictionary, tests, and panel. |
| Total cash to close | Inputs: `down_payment_3_5pct`, `down_payment_10pct`, `down_payment_20pct` plus the legacy closing-cost fields | **Inputs available; target missing.** Inputs each cover 173 LA County months, 2012-01 through 2026-05. | Define the down-payment/closing-cost scenario pairing or cross-product and construct `total_cash_to_close_scenario`. Prepaid escrow remains excluded unless separately modeled. |
| One-unit permits/authorizations | `permits_1_unit` | **Available.** Fact and county panel; 317 LA County and 317 California monthly observations, 2000-01 through 2026-05. | No blocker for the base variable. Keep the concept labeled permits/authorizations, never starts or completions. A separate final-annual county benchmark is not loaded. |
| One-unit permits per 1,000 housing units | None | **Missing.** | Acquire a compatible historical lagged housing-stock denominator and then construct `permits_1_unit_per_1000_housing_units`. The active `acs_total_housing_units` currently has only 2024. |
| IRS net migration exemptions | `irs_soi_net_migration_exemptions` | **Available.** 13 LA County annual observations, 2011-2023, in fact and annual controls. | None. Preserve the filing-year interval and annual frequency. |
| IRS net migration returns | `irs_soi_net_migration_returns` | **Available.** Same coverage. | None. |
| IRS net migration AGI | `irs_soi_net_migration_agi` | **Available.** Same coverage. | None. |
| IRS net-exemption rate | None | **Missing.** | Acquire a compatible prior-year population series for the migration intervals, then calculate the annual rate per 1,000. Do not divide annual flows into observed months. |
| Zillow price-to-rent ratio | Existing equivalent: `price_to_rent_ratio` | **Partial.** County monthly panel; 137 non-null LA County months, 2015-01 through 2026-05. The source and formula already match the latest Zillow/Zillow requirement. | Add/alias canonical `price_to_rent_ratio_zillow`, construct it in the ZIP panel, enforce positive ZHVI and ZORI, and add ZIP coverage/outlier QA. |
| Implied gross rent yield | None | **Missing.** | Add `implied_gross_rent_yield = 12 * ZORI / ZHVI` to the feature code, dictionary, panels, and tests. Keep it explicitly gross. |
| ACS for-rent vacant units | `acs_for_rent_vacant_units`, `acs_for_rent_vacant_units_moe` | **Available.** 275 non-null 2024 facts per variable: LA County plus 274 LA ZCTAs. | Historical ACS vintages remain an enhancement; preserve ZCTA identity and do not relabel as postal ZIP. |
| ACS renter-occupied units | `acs_renter_occupied_units`, `acs_renter_occupied_units_moe` | **Available.** Same 2024 geography coverage. | Same historical-vintage limitation. |
| ACS rental-availability rate | `acs_rental_availability_rate` | **Available for LA County 2024.** Annual-controls value 0.0402385507; MOE reliability flag `ok`. | Historical matched B25003/B25004 acquisition is the remaining coverage step. This field must remain separate from USPS and true listing inventory. |
| True monthly rental-listing inventory | No configured ID; reserved concept `rental_listing_inventory_monthly` | **Missing by design.** | Verify and license a stable historical active-rental-listing source. Until then, leave the value missing rather than substituting ZORI, Redfin, HVS, ACS, or USPS. |
| HUD-USPS residential proxy | `usps_total_residential_addresses`, `usps_short_term_vacant_residential_addresses`, `usps_long_term_vacant_residential_addresses`, `usps_vacant_residential_addresses`, `usps_no_stat_residential_addresses`, `usps_active_residential_addresses`, `usps_residential_vacancy_rate`, `usps_residential_no_stat_rate` | **Available.** Each ID has 221,117 rows across 2008 Q1-2026 Q1. Latest LA County vacancy rate is 1.21698%. | No implementation blocker. Licensed tract and modeled ZIP-part detail remains restricted. Never label this all-residential proxy as rental inventory. |
| California average written homeowners premium | None | **Missing.** | Acquire and archive the California Department of Insurance Residential Property Insurance Report, select a consistent policy form (for example HO-3) or document an aggregate weighting rule, then calculate `ca_homeowners_avg_written_premium = written_premium / written_exposures`. |
| Real premium, annual YoY, and monthly carry-forward control | None | **Missing; dependent on the base premium.** | After the base annual state series exists, apply an explicit deflator, construct annual YoY growth, and create only a flagged annual step-function carry-forward for monthly models. Do not present it as observed LA County monthly insurance. |
| ZORI YoY outcome | `zori_growth_yoy` | **Available at LA County.** 125 monthly values, 2016-01 through 2026-05. | ZIP growth is not constructed. This outcome alone does not identify controlled or noncontrolled units. |
| Noncontrolled exposure share | None | **Missing.** | Build `noncontrolled_exposure_share` from parcel/structure characteristics and a dated policy crosswalk covering AB 1482, local ordinances, property types, construction-date rules, and exemptions. |
| Exposure-adjusted noncontrolled rent growth | None | **Missing and correctly blocked.** | Only after the exposure layer exists, construct `exposure_adjusted_noncontrolled_rent_growth`. Do not call unweighted ZORI observed noncontrolled rent growth. |

## Earlier methodology and tracker variables

### Available with non-null data

| Variable group | Exact repository IDs | Coverage and qualification |
|---|---|---|
| Monthly mortgage-payment scenarios | `mortgage_payment_3_5pct_down`, `mortgage_payment_10pct_down`, `mortgage_payment_20pct_down` | County monthly panel; 173 values each, 2012-01 through 2026-05. Principal and interest only. |
| Down-payment scenarios | `down_payment_3_5pct`, `down_payment_10pct`, `down_payment_20pct` | County monthly panel; 173 values each, 2012-01 through 2026-05. |
| Eviction trends | `eviction_lab_filings_estimate_annual`, `eviction_lab_filings_rate_estimate_annual`, `eviction_lab_households_threatened_estimate_annual`, `eviction_lab_households_threatened_rate_estimate_annual` | Fact and annual controls; 19 annual modeled values each, 2000-2018. These are not high-frequency LA court filings or LA ETS observations. |
| Natural population change | `cdc_wonder_live_births_annual`, `cdc_wonder_deaths_annual`, `natural_population_change_annual`, `natural_population_change_quarterly_allocated` | 18 annual observations, 2007-2024; 72 quarterly allocations. Quarterly timing is explicitly imputed. |
| Home-price indexes | `fhfa_hpi_ca_purchase_only_nsa`, `fhfa_hpi_ca_purchase_only_sa`, `fhfa_hpi_la_metropolitan_division_purchase_only_nsa`, `fhfa_hpi_la_metropolitan_division_purchase_only_sa`, `fhfa_hpi_la_county_all_transactions_nsa_annual` | California/LA metro quarterly 1991 Q1-2026 Q1; LA County annual 1975-2025. |
| Median sale price | `median_sale_price`, `redfin_zip_median_sale_price_nsa_rolling_3m` | LA County monthly 2012-01 through 2026-05; ZIP rolling-three-month 2019-06 through 2026-05. |
| Existing homes for sale | `active_inventory`; separate ZIP field `redfin_zip_active_listings_rolling_3m` | County: 173 months, 2012-01 through 2026-05. ZIP: 23,008 non-null rolling observations, 2019-06 through 2026-05. Both are for-sale, not rental, inventory. |
| Months of supply | `months_of_supply` | LA County monthly; 173 values, 2012-01 through 2026-05. |
| New listings | `new_listings`, `redfin_zip_new_listings_rolling_3m` | County 2012-01 through 2026-05; ZIP rolling 2019-06 through 2026-05. |
| Homes sold | `homes_sold`, `redfin_zip_homes_sold_rolling_3m` | Same respective coverage. |
| Mortgage rates | `mortgage_rate_30y_weekly`, `mortgage_rate_30y_monthly_average`, `mortgage_rate_30y_monthly_median`, `mortgage_rate_30y_month_end`, `mortgage_rate_30y_observation_count` | Weekly 1971-04-02 through 2026-06-25; monthly 1971-04 through 2026-06. National PMMS, not LA-specific. |
| Market rent index | `zori_smoothed_sa` | County and postal ZIP, 2015-01 through 2026-05; active product is smoothed, seasonally adjusted, all homes plus multifamily. |
| Rent growth | `zori_growth_mom`, `zori_growth_yoy` | LA County monthly; YoY begins 2016-01. ZIP growth is not yet constructed. |
| Rental vacancy | `hvs_rental_vacancy_rate_ca`, `hvs_rental_vacancy_rate_la_metro` | 85 quarters each, 2005 Q1-2026 Q1. State/metro vacancy rates, not listing inventory. |
| Multifamily permits | `permits_5_plus_units` | Fact and county panel; 317 LA County plus 317 California monthly observations, 2000-01 through 2026-05. Authorizations, not starts or completions. |
| Market time | `days_on_market`, `redfin_zip_median_days_on_market_rolling_3m` | County 2012-01 through 2026-05; ZIP rolling 2019-06 through 2026-05. |
| Price-drop share | `price_drop_share` | LA County monthly; 172 non-null of 173 rows, 2012-01 through 2026-05. |
| Payroll employment | `ces_total_nonfarm_employment_sa` | LA metro monthly; 257 values, 2005-01 through 2026-05. |
| Unemployment rate | `unemployment_rate` | LA County monthly; 436 non-null of 437 rows, 1990-01 through 2026-05. |
| Labor-force components | `labor_force`, `employment`, `unemployment` | LA County monthly; 436 non-null values each, 1990-01 through 2026-05. |
| CPI/inflation | `cpi_u_all_items_la_nsa` | LA metro; 1,094 non-null observations, 1914-12 through 2026-05. |

### Missing, schema-only, or input-only results

| Requested result | What the repository currently has | Exact stuck step |
|---|---|---|
| Aggregate home equity | Schema for `acs_implied_owner_occupied_home_equity_mortgaged_benchmark`, `acs_implied_owner_occupied_home_equity_all_benchmark`, `acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark`, and `acs_implied_owner_occupied_home_equity_all_real_2024_benchmark` | **Schema-only:** all 232 expected values are null. Acquire an exact-date, universe-compatible California NMDB UPB control before producing calibrated debt and equity. |
| Aggregate mortgage debt | `acs_implied_first_mortgage_balance_raw_benchmark` has 58 non-null 2024 California-county estimates. Schema also exists for `acs_implied_first_mortgage_balance_cal_benchmark`, `acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark`, `acs_first_mortgage_debt_per_owner_unit_benchmark`, `acs_first_mortgage_debt_per_capita_benchmark`, and `acs_first_mortgage_debt_to_owner_income_benchmark`. | The raw sensitivity is available, but every authoritative calibrated/ratio field is null. Same compatible California NMDB UPB control blocker. |
| New apartments completed | Input `permits_5_plus_units` | Specify and implement a versioned multifamily completion-lag model. No completion variable currently exists. |
| Multifamily under construction | Input `permits_5_plus_units` | Specify assumed completions and rolling window, then construct cumulative permits minus completions. |
| Single-family rental construction | Input `permits_1_unit` | Acquire or build the investor/rental-growth tract classifier required to identify a build-for-rent proxy. No public build-for-rent flag is loaded. |
| Quarterly net change in occupied apartments | Input `acs_renter_occupied_units`, 2024 only | Archive historical matched B25003 vintages, calculate annual differences, and only then define any explicitly modeled timing. One vintage cannot produce a change. |
| Quarterly/local price-to-income | `county_annual_price_to_income_acs` | One LA County annual value for 2024. Acquire historical annual inputs and document a mixed-frequency treatment; do not silently call repeated annual values observed quarterly. |
| Sheltered versus unsheltered homelessness | No configured variable | Load annual HUD PIT/CoC data. A credible public high-frequency series is unavailable, so the high-frequency requirement remains unsatisfied. |
| Housing turnover | `sales_to_inventory_ratio`, `inventory_to_sales_ratio`, 2012-01 through 2026-05 | These are liquidity ratios. True sales/housing-stock turnover is stuck on a compatible housing-stock denominator. |
| Personal income | `bea_ca_personal_income_q_sa`, 1948 Q1-2026 Q1; `bea_la_county_personal_income_annual`, 1969-2024 | California quarterly and LA County annual are available. LA County quarterly remains unsupported by the active source. |

## Status-artifact note

`metadata/docx_variable_audit.csv` and the related generated workbooks preserve the requirements extracted from the older source document, so their FHFA/ZORI, NAIC, and closing-cost rows remain an audit of that document. They have been regenerated to include the active ACS Tier A rental-availability implementation. This status document applies the latest Additional Variables methodology whenever the standards conflict.
