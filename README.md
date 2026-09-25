# Los Angeles Housing Public-Data Database

This repository builds a reproducible, source-vintaged public-data database for Los Angeles housing research. It preserves immutable raw files, normalizes source-specific observations to a shared contract, builds DuckDB/Parquet analytical tables, and writes QA, coverage, catalog, and CSV outputs.

**Choose and download data without SQL:** double-click [Open_Data_Browser.cmd](Open_Data_Browser.cmd), choose a **Dataset**, select a **Variable** or keep All variables, then click **Download CSV**. The [step-by-step browser guide](docs/query_and_csv_guide.md) covers geography, place, frequency, Domain, Layer, Proxy, Constructed, simplified units, and temporal types, with an example downloading `zori_smoothed_sa` for postal ZIP 90024. Download matching variable definitions alongside the data. The local browser reads the existing database and shares the command-line query/export implementation; it uses Python's standard-library web server and adds no dependencies. To launch manually, run `python run_data_browser.py --open-browser` in the project's configured Python environment. See the [command reference](docs/query_command_reference.md) for reproducible queries, option discovery, CSV, and Parquet exports.

**On a new computer:** a GitHub clone contains code, documentation, and metadata; it does not include `database/housing.duckdb` or the archived source data. Set up `.venv` using the instructions below, then build the database through the documented pipeline or provide a compatible public database snapshot. The browser launcher requires that environment and database; it does not install or build them.

All **558 variables** now expose `unit_simplified`, `temporal_type`, and explanatory metadata while retaining their original `unit` and numerical values. Use `python run_query.py catalog` to find identifiers, or `python run_query.py observations --dataset main --temporal-type flow --geo-id COUNTY_06037 --output output/exports/la_flows.csv` to download matching observations. The complete downloadable dictionary is `output/exports/variable_dictionary.csv`. See [unit and temporal-type queries](docs/variable_units_temporal_types.md) for CSV/Parquet filters, SQL views, workbook provenance, and the five documented classification corrections. Refresh labels with `python run_pipeline.py --stage variable-metadata`.

The database is a static snapshot. DuckDB is not continuously connected to Census, BLS, Zillow, Redfin, or any other provider. New observations enter the database only when the pipeline is run.

For a nontechnical orientation, start with the [database system overview](docs/database_system_overview.md) and the filterable [outsider inventory workbook](outputs/database_system_overview_20260824/LA_Housing_Database_Outsider_Overview.xlsx). The live machine-readable inventories are `metadata/system_source_inventory.csv`, `metadata/analytical_dataset_inventory.csv`, `metadata/system_variable_inventory.csv`, `metadata/estimate_inventory.csv`, and `metadata/system_storage_inventory.csv`.

## Current generated outputs

The [September 22 implementation and exceptions report](docs/september_22_2026_implementation_report.md) is the current status for the two latest requirements documents. All eleven macro groups are materialized at their required frequency: ten monthly groups and **quarterly real GDP**, as explicitly confirmed by the user. GDP is not a missing monthly requirement. County formulas were independently audited; regime-flow shares now use the document's current-classified-ZIP denominator and remain null when prior-quarter classifications are incomplete. A **Redfin provider-release review hold** prevents strict refresh from treating its May 2026 legacy file as the newest release. New Zillow/LAUS observations extend through August 2026; frozen Version A is unchanged.

The consolidated configuration contract contains **558 unique variables**: 375 general variables in `config/variables.yaml`, 67 Version A variables in `config/version_a_variables.yaml`, 58 Version B variables in `config/version_b_variables.yaml`, and 58 Version C variables in `config/version_c_variables.yaml`. It contains **69 unique provider source IDs**: 61 general source IDs plus nine Version A source IDs, with `freddie_mac_pmms` present in both configurations and counted once; Versions B and C reuse those archived inputs and add no provider source. All 61 general sources are enabled. The ten workbook-approved general additions are California annual CPI-U and constant-2023-dollar homeowners premium; Central and Slow apartment-completion and under-construction proxies; Census PEP county population and housing-unit denominators; IRS net exemptions per 1,000 residents; and final annual one-unit permits per 1,000 housing units. The 64-variable ACS extension adds 52 direct B25032/B25127 estimates and margins of error, eight annual renter-structure, recent-construction, and noncontrolled-exposure structural bounds, and four B05002/B07001 foreign-born and moved-from-abroad estimate/MOE fields. The exact policy-adjusted `noncontrolled_exposure_share` remains absent. The contract also includes two annual exposure-adjusted noncontrolled-rent-growth structural sensitivities, 37 official 2023 California AHS Table 14A estimates/diagnostics and one reported-case debt proxy among them, two separately labeled 2024 California SIPP debt/equity sensitivities, 39 LA/comparable-county analytical and diagnostic variables, and 37 September macro/rates variables. The exact policy-adjusted growth target and the canonical calibrated finance targets remain unfilled.

Physical availability is not the same as source authorization. The variable-by-variable audit applies `LA_CA_Housing_Additional_Variables_Methodology.docx` together with the later workbook decisions and classifies 120 configured IDs as explicitly assigned to an approved source/use, 20 as only partially or contextually related, and 226 as outside those requirements and retained from earlier requirements or repository-only configuration. The ten new general IDs are in the approved category because the decision workbook fixes their sources and methods. The two structural rent-growth sensitivities remain contextual rather than the exact legally adjusted target. The 37 AHS IDs and two SIPP IDs are authorized by the separate `Four_Variable_Data_Status_Report.docx`, not by the Additional Variables document or decision workbook; that scope boundary is preserved in the source audit. HMDA/Modified LAR is likewise absent from those two sources but is explicitly authorized by the separate Four Variable Data Status Report for its annual homebuyer down-payment variable. A provider named for one construction is never treated as blanket authorization for another.

The **September 22 build** summarized below contains 2,671,375 long-form fact observations. Version A is built separately into `data/final/housing_finance_benchmark.parquet` with 3,886 rows: 2,262 ACS/PUMS/PMMS/CPI finance rows plus 1,624 official 2025 HMDA county/loan-type rows. The active official HUD/USPS rebuild contributes 1,768,936 native-quarterly observations across 3,029 geographies; its detailed licensed tables remain under restricted storage and outside public CSV exports.

| Output | Rows in September 22 build | Date coverage in that build |
|---|---:|---|
| `analytics.fact_housing_observation` / `data/final/fact_housing_observation.parquet` | 2,671,375 | 1914-12-31 to 2026-09-21 |
| `analytics.macro_financial_monthly` / `data/final/macro_financial_monthly.parquet` | 1,793 | 1954-07-31 to 2026-08-31; US, California, LA division kept separate |
| `analytics.county_monthly_panel` / `data/final/county_monthly_panel.parquet` | 440 | 1990-01-31 to 2026-08-31 |
| `analytics.comparable_county_monthly_analytics` / `data/final/comparable_county_monthly_analytics.parquet` | 3,520 | 1990-01-31 to 2026-08-31 |
| `analytics.zip_monthly_panel` / `data/final/zip_monthly_panel.parquet` | 87,680 | 2000-01-31 to 2026-08-31 |
| `analytics.zip_market_regime_quarterly` / `data/final/zip_market_regime_quarterly.parquet` | 7,124 | 2020-03-31 to 2026-06-30 |
| `analytics.la_zip_market_regime_summary_quarterly` / `data/final/la_zip_market_regime_summary_quarterly.parquet` | 104 | 2020-03-31 to 2026-06-30 |
| `analytics.la_zip_market_regime_transitions_quarterly` / `data/final/la_zip_market_regime_transitions_quarterly.parquet` | 416 | 2020-03-31 to 2026-06-30 |
| `analytics.state_quarterly_panel` / `data/final/state_quarterly_panel.parquet` | 313 | 1948-03-31 to 2026-03-31 |
| `analytics.metro_monthly_controls` / `data/final/metro_monthly_controls.parquet` | 1,095 | 1914-12-31 to 2026-05-31 |
| `analytics.metro_quarterly_controls` / `data/final/metro_quarterly_controls.parquet` | 226 | 1991-03-31 to 2026-03-31 |
| `analytics.metro_quarterly_panel` / `data/final/metro_quarterly_panel.parquet` | 141 | 1991-03-31 to 2026-03-31 |
| `analytics.county_annual_controls` / `data/final/county_annual_controls.parquet` | 57 | 1969-12-31 to 2025-12-31 |
| `analytics.zcta_annual_controls` / `data/final/zcta_annual_controls.parquet` | 3,836 | 2011-12-31 to 2024-12-31 |
| `analytics.county_quarterly_controls` / `data/final/county_quarterly_controls.parquet` | 72 | 2007-03-31 to 2024-12-31 |
| `analytics.state_annual_controls` / `data/final/state_annual_controls.parquet` | 71 | 1955-12-31 to 2025-12-31 |
| `analytics.state_monthly_controls` / `data/final/state_monthly_controls.parquet` | 192 | 2008-01-31 to 2023-12-31 |
| `analytics.coc_annual_controls` / `data/final/coc_annual_controls.parquet` | 822 | 2007-12-31 to 2025-12-31 |
| `analytics.housing_finance_benchmark` / `data/final/housing_finance_benchmark.parquet` | 3,886 | 2024 ACS finance benchmark plus 2025 HMDA benchmark |
| `analytics.housing_finance_monthly_hist` / `data/final/housing_finance_monthly_hist.parquet` | 225,388 | 2013-03-31 to 2026-03-31 |
| `analytics.housing_finance_monthly_rt` / `data/final/housing_finance_monthly_rt/information_cutoff_date=2026-08-17/part.parquet` | 188,384 | 2022-01-31 to 2026-08-31; as known at the 2026-08-17 cutoff |
| `analytics.homebuying_cost_scenarios` / `data/final/homebuying_cost_scenarios.parquet` | 346,785 | 2012-01-31 to 2026-05-31 |

CSV exports are written under `output/exports/`:

- `la_county_monthly_panel.csv`
- `la_zip_monthly_panel.csv`
- `la_state_quarterly_panel.csv`
- `la_metro_monthly_controls.csv`
- `la_metro_quarterly_panel.csv`
- `la_metro_quarterly_controls.csv`
- `la_county_annual_controls.csv`
- `la_zcta_annual_controls.csv`
- `la_county_quarterly_controls.csv`
- `la_state_annual_controls.csv`
- `la_state_monthly_controls.csv`
- `ca_coc_annual_controls.csv`
- `la_homebuying_cost_scenarios.csv`
- `la_version_a_benchmark.csv` when the Version A benchmark has been built

## Consolidated variable and source contract

The regenerated inventory confirms **514 of 558 IDs have at least one non-null value**: all 375 general IDs, 55 Version A IDs, and 42 IDs in each monthly finance version. The remaining 44 are explicit-null finance targets. Current analytical inventory: 22 base tables, 11 views, 26 final Parquet files, 277 constructed/estimate IDs, and 75 catalog products (69 provider sources plus six internal B/C products).

The general pipeline and three housing-finance layers are complementary parts of one repository contract. Versions B and C are derived monthly products and introduce no new provider source IDs:

| Contract | General | Version A | Version B | Version C | Consolidated unique total |
|---|---:|---:|---:|---:|---:|
| Variables | 375 | 67 | 58 | 58 | 558 |
| Provider source IDs | 61 | 9 | 0 new | 0 new | 69 |

Housing-finance Versions B and C are isolated 58-variable monthly layers derived from the frozen Version A benchmark: Version B uses `_monthly_hist` IDs for retrospective modeled history, while Version C uses append-only cutoff-specific `_monthly_rt` IDs and forbids future-endpoint interpolation. The current completed products contain 225,388 Version B observations and 188,384 Version C observations at the 2026-08-17 information cutoff, with all 58 counties and zero version-specific QA failures. Canonical unavailable debt, equity, and financed-purchase-flow targets remain explicit nulls; Redfin proxies and PMMS sensitivities have separate IDs. The global metadata registrar adds all 116 monthly IDs and the observation/lineage/completion product families without altering Version A or raw data. See the [Version B/C methodology, runners, outputs, lineage, QA, and source limitations](docs/housing_finance_versions_b_c.md).

All 67 Version A variable IDs end in `_benchmark`. The benchmark now publishes explicit raw PMMS 20/25/30-year debt sensitivities and matching mortgaged-owner/all-owner equity scenarios for all 58 California counties. These are uncalibrated sensitivity scenarios, not preferred calibrated estimates. The calibrated method still uses direct ACS B25089/B25081/B25082/B25120 levels, weighted PUMS parameters, and exact-date, universe-compatible NMDB controls when available. The official FHFA NMDB state ZIP file has been archived, but its `TOT_UPB` field covers all active mortgages rather than the required owner-occupied first-lien universe, so it is not used to calibrate debt/equity. This is a universe/method compatibility blocker, not a credential blocker. The earlier B25106 midpoint/80%-debt-service/25-year mortgage construction remains available only as a **legacy modeled scenario** for reproducibility and must not be presented as the primary debt or equity estimate.

The official 2025 SIPP public state-table products add two California-only 2024 sensitivities without altering those exact calibrated targets. `sipp_ca_aggregate_primary_residence_debt_sensitivity` is $1,627,458,504,000 and `sipp_ca_aggregate_positive_home_equity_sensitivity` is $4,913,341,020,000. Both multiply published rounded household counts, holding shares, and conditional means. The debt product is broader than owner-occupied closed-end first-lien debt; the equity product is conditional on positive home equity. The conditional means exclude the top one percent under the provider's debt or net-worth rule, and no covariance-based standard error is invented. These statewide table-product sensitivities are never allocated to counties or used to populate the twelve null calibrated Version A target IDs.

The official 2023 California AHS Table 14A export adds a reported-case primary-mortgage outstanding-principal proxy of $1,413,679,720,000: 3,381,200 households in the fourteen reported balance bins times the published $418,100 conditional mean. The table reports 1,203,800 of 4,585,200 regular-mortgage households without a balance, or 26.2540 percent. Those households are excluded; the repository does not assign them the reported-case mean. The result is a 2023 statewide public calibration proxy authorized by the Four Variable Data Status Report, not an exact owner-occupied closed-end first-lien control for the current Version A universe and 2022-07-01 reference date. AHS primary mortgage may differ from legal first lien, and no aggregate margin of error or confidence interval is constructed without the required covariance.

The eight `usps_*` variables and `hud_usps_ncwm_quarterly` are active from 75 verified official artifacts: 73 contiguous quarterly NCWM responses for 2008 Q1 through 2026 Q1, the official 2026 Q1 tract-to-postal-ZIP crosswalk, and the official 58-row 2020 Census California county-code reference. `usps_residential_vacancy_rate = (STV + LTV residential addresses) / total residential addresses` is a quarterly **all-residential administrative vacancy proxy**. It is not renter-only, not a count of available rental listings, and not true rental-listing inventory. No-stat addresses remain a separate diagnostic. Source components are direct at tract geography, California county values are constructed from complete tract components, and modeled ZIP rows represent Los Angeles County portions of postal ZIPs—not whole postal ZIPs or Census ZCTAs.

See the [HUD/USPS methodology and active provenance record](docs/hud_usps_residential_vacancy_proxy.md), the [official source manifest](metadata/hud_usps_source_manifest.csv), and the [superseded quarantine manifest](metadata/hud_usps_quarantine_manifest.csv). The former 2026-08-02 derived package file has been deleted; only its historical manifest remains. Active values are rebuilt from official artifacts archived with source URLs/queries, filenames, exact UTC retrieval timestamps, provider vintages or release dates when supplied, geography, file sizes, and SHA-256 checksums. Licensed raw and detailed derived data remain under Git-ignored `data/restricted/hud_usps/` and outside public CSV exports.

Rental supply is represented by two separate proxies, not one interchangeable inventory series. Matched ACS five-year B25004 and B25003 estimates construct annual Los Angeles County rental availability as `for-rent vacant / (renter-occupied + for-rent vacant)` for 2010-2024, retain both component margins of error, and suppress unreliable county ratios. Direct component counts and margins of error are retained for 274 configured Census ZCTAs for 2011-2024; the official 2010 ACS API does not publish ZCTA geography, and ZCTAs are not postal ZIPs. The HUD/USPS measure remains a native-quarterly all-residential address-vacancy proxy. Neither measure is interpolated or relabeled as active rental listings; `rental_listing_inventory_monthly` remains unavailable because the approved methodology does not identify a documented historical rental-listing source that can be integrated. No RentCast source is configured or used. See the [ACS rental-availability methodology](docs/acs_rental_availability.md).

Noncontrolled rental exposure is partially implemented from the methodology-listed official ACS B25032 and B25127 tables. The immutable official archives produce 84,722 B25032 clean observations and 115,530 B25127 clean observations, covering Los Angeles County for 2010-2024 and each of 274 configured Census ZCTAs for 2011-2024. Annual county and Census-ZCTA outputs retain 52 direct estimates/MOEs and construct four B25032 structure shares, two B25127 recent-construction bounds, and two 2020-and-later structural exposure bounds. Each final proxy bound has five non-null county values and 1,370 non-null Census-ZCTA values for 2020-2024. Two separately labeled annual sensitivities now multiply the exact December Los Angeles County ZORI year-over-year change by the same-ending-year structural lower and upper bounds for 2020-2024; they are not observed rent growth within legally noncontrolled units and are never expanded to monthly frequency. The exact point share and exact exposure-adjusted growth remain blocked on a complete dated property-level policy/exemption crosswalk. See the [noncontrolled rental exposure methodology](docs/noncontrolled_rental_exposure.md) and the official-source-only scaffold in `config/rent_control_policy_crosswalk.csv`.

The ACS migration supplements are implemented as two separate observed concepts, each with its published 90% margin of error. `acs_foreign_born_population` maps to B05002_013E (MOE B05002_013M) and is a population stock of residents who were not U.S. citizens at birth. `acs_moved_from_abroad_past_year` maps to B07001_081E (MOE B07001_081M) and is a gross five-year survey estimate of current residents age one and over whose residence one year earlier was abroad. Census "abroad" includes foreign countries and U.S. territories and can include returning U.S. residents, so the B07001 measure is not net international immigration. Neither ACS supplement replaces the annual IRS SOI county net-migration series. Each estimate and MOE has 3,851 observations: 15 Los Angeles County vintages for 2010-2024 and 3,836 configured Census-ZCTA vintages for 2011-2024. The 2010 ZCTA gap is structural because that API vintage does not expose ZCTA geography. The ZCTA series also spans two boundary regimes (2010 Census ZCTAs for 2011-2020 vintages and 2020 Census ZCTAs for 2021-2024 vintages), and adjacent ACS five-year vintages overlap; users should also treat pandemic-period and weighting-method changes as a comparability caution rather than an annual-flow signal.

California homeowners premium is now available in nominal and constant-2023-dollar annual form. `ca_cpi_u_all_items_annual` retains only the official California Department of Industrial Relations table rows labeled `Annual`; the active vintage covers 1955-2025 and is never reconstructed from subannual values. `ca_homeowners_avg_written_premium_real = ca_homeowners_avg_written_premium * CPI_2023 / CPI_year` for exact matched calendar years. The 2023 real value equals the 2023 nominal value by construction. Missing CPI or premium inputs remain null, and the canonical real series stays annual rather than being copied into observed months. See the [California homeowners premium methodology](docs/ca_doi_homeowners_premium.md).

The apartment construction workflow has four explicitly modeled monthly fields. Central completions assign 20% of each five-plus-unit permit cohort equally to lags 12-17, 55% equally to lags 18-23, and 25% equally to lags 24-30. Slow completions assign 20% equally to lags 18-23, 50% equally to lags 24-29, and 30% equally to lags 30-36. The matching under-construction stocks apply prior stock plus current permits minus modeled completions and initialize from all still-active pre-sample cohorts—never from an arbitrary zero. Missing required permit months propagate, only Central and Slow are maintained, and every output remains labeled a scenario rather than an observed completion or construction count. The four values and their proxy-specific quality flags are materialized in `analytics.county_monthly_panel` and exposed in `analytics.renter_market_monthly`; they are intentionally not native rows in `analytics.fact_housing_observation`. See the [multifamily construction proxy methodology](docs/multifamily_construction_proxies.md).

The annual normalized rates use official Census PEP denominators, not ACS or fixed-year substitutions. `net_exemption_rate` matches the IRS filing-year-pair endpoint to the same-ending-year PEP resident population and calculates `net migration exemptions / population * 1,000`. `permits_1_unit_per_1000_housing_units` matches final annual BPS one-unit authorizations to the same-year PEP July 1 housing-unit estimate and multiplies by 1,000. Nonoverlapping Vintage 2019 and Vintage 2025 segments provide contiguous 2010-2025 denominator coverage, with the 2020 decennial-vintage boundary kept explicit. All source variables and both rates remain annual.

## September 2026 macro and rates additions

The two September requirements documents are tracked in the [current implementation report](docs/september_22_2026_implementation_report.md), which supersedes the [September 15 status report](docs/september_2026_requirements_status.md). The macro contract adds **37 general variable IDs from nine provider source IDs**:

| Family | New IDs | Geography and frequency |
|---|---:|---|
| CES industry employment and hourly earnings | 12 | Five industry employment levels and total-private hourly earnings, each with exact-calendar arithmetic YoY growth; monthly NSA observations for California and Los Angeles-Long Beach-Glendale Metropolitan Division. The same IDs are used at both geographies. |
| Unemployment insurance claims | 6 | California initial and continued claims, retained weekly with separate closed-month means and valid-week counts. Incomplete months have null means; wholly absent months within the native span retain zero counts without fabricating weekly observations. |
| Real GDP | 3 | California quarterly real GDP, compounded annualized QoQ growth, and four-quarter arithmetic YoY growth. No monthly interpolation. |
| Financial rates | 16 | National monthly federal funds rate plus five daily series (2-year/10-year Treasury yields, Baa-minus-10-year spread, prime rate, SOFR), each with separate closed-month arithmetic means and valid-day counts. |

DOL claims use the official public bulk CSV, not the XML response as the canonical input. No login is required for these public source contracts. US financial rates remain national rows; they are not silently broadcast into California or local observations. The monthly-only output is `analytics.macro_financial_monthly` / `data/final/macro_financial_monthly.parquet`; native daily, weekly, and quarterly records remain in the fact layer.

The September 22 build materializes **1,793 rows and 32 columns** in the monthly macro panel, spanning 1954-07-31 through 2026-08-31: 866 US rows, 487 California rows, and 440 LA-division rows. Its 27 variable columns comprise 20 substantive fields plus seven coverage diagnostics, representing 39 geography-specific series. Native daily/weekly inputs remain audit records; the three GDP fields remain required quarterly outputs, not missing monthly measures. These are geography-month rows, not broadcast copies. See `metadata/macro_required_frequency_status.csv` for the eleven requirement groups.

To refresh only these nine sources, run acquisition and strict cleaning first. This helper does not publish the final database:

```powershell
& ".\.venv\Scripts\python.exe" scripts/refresh_macro_request.py
```

Only after acquisition/cleaning succeeds, run each stage in order and stop on any failure. For newly introduced source contracts, register the catalog before validation; validation must succeed before export:

```powershell
& ".\.venv\Scripts\python.exe" run_pipeline.py --stage harmonize
& ".\.venv\Scripts\python.exe" run_pipeline.py --stage build-database
& ".\.venv\Scripts\python.exe" run_pipeline.py --stage catalog
& ".\.venv\Scripts\python.exe" run_pipeline.py --stage validate
& ".\.venv\Scripts\python.exe" run_pipeline.py --stage export
```

The comprehensive `run_system_refresh.py` workflow already includes all nine enabled sources while preserving frozen Version A. Neither runner installs a schedule or makes DuckDB a live connection.


## LA and comparable-county analytics

The repository implements the nine requested LA/comparable-county constructions for Los Angeles, Orange, Riverside, San Bernardino, San Diego, Sacramento, Santa Clara, and Alameda counties: home-price momentum, rent momentum, three-component market tightness, Zillow price-to-rent level and change, LA ZIP relative valuation pressure, LAUS employment momentum, LA-minus-seven-peer performance, frozen-model LA ZIP market regimes, and county housing heat. The [full methodology and output guide](docs/la_comparable_county_analytics.md) records the exact formulas, variable IDs, missingness rules, and interpretation.

The main outputs are `data/final/comparable_county_monthly_analytics.parquet`, `data/final/zip_market_regime_quarterly.parquet`, the two quarterly regime summary/transition Parquets, and their matching `analytics.*` DuckDB tables. Focused views are `analytics.county_housing_market_heat_monthly` and `analytics.la_relative_market_performance_monthly`. The active frozen artifacts are `metadata/comparable_county_reference_parameters_v1.json` and `metadata/zip_market_regime_model_v2.json`; the old regime v1 artifact is superseded.

Ordinary online refreshes request the newest eligible public Zillow, Redfin county, and BLS LAUS vintages and rebuild these estimates without refitting the frozen reference parameters or regime model. As of September 22, the Redfin county contract has a strict-refresh provider-release review hold: its configured legacy file still ends in May 2026, while newer publications exist and the successor download has not been verified. Historical offline builds remain possible; they do not certify freshness. `scripts/refresh_county_analytics.py` refreshes and strictly cleans the six inputs without publishing analytical outputs, and stops short of a successful run while that hold remains. Two choices remain explicitly tagged pending mentor confirmation: the February 2012–December 2019 pooled eight-county population-SD normalization window and the one-/twelve-month price-to-rent log-change horizons.

## Source groups

General source pages, download URLs/API endpoints, file formats, source IDs, key variables, and methodology notes live in `config/sources.yaml`; Version A sources live in `config/version_a_benchmark.yaml`. Variable definitions live in `config/variables.yaml` and `config/version_a_variables.yaml`.

| Group | Source IDs / status | Main variables |
|---|---|---|
| Zillow Research | `zillow_county_zhvi`, `zillow_zip_zhvi`, `zillow_county_zori_smoothed_sa`, `zillow_zip_zori_smoothed_sa` | ZHVI, active SA ZORI |
| Redfin Data Center | `redfin_county_monthly`, `redfin_zip_monthly` | Sale price, sales volume, listings, days on market, inventory/listing indicators |
| BLS | `bls_laus_la_county`, `bls_ces_la_metro`, `bls_cpi_la_metro` | LAUS labor force/unemployment, CES payroll employment, LA metro CPI-U |
| Freddie Mac | `freddie_mac_pmms` | Weekly and monthly 30-year fixed mortgage-rate measures |
| Census BPS | `census_bps_county`, `census_bps_state`, `census_bps_county_annual_final` | Monthly total, 1-unit, 2-to-4-unit, and 5-plus-unit permits configured through June 2026; separate final annual LA County 1-unit permits for 1990-2025 |
| Census HVS | `census_hvs_state_rental_vacancy`, `census_hvs_metro_rental_vacancy_2005_2014`, `census_hvs_metro_rental_vacancy_2015_2025`, `census_hvs_metro_rental_vacancy_2026` | Rental vacancy rates at state/metro geographies |
| Census ACS 5-year detailed tables | `census_acs_5year_b05002`, `b07001`, `b11001`, `b19013`, `b25001`, `b25002`, `b25003`, `b25004`, `b25032`, `b25064`, `b25077`, `b25081`, `b25106`, `b25127` | Foreign-born population stock, moved-from-abroad current-resident mobility, households, income, housing units, vacancy, tenure, for-rent vacant units, annual rental availability, renter units by structure type and year built, noncontrolled structural bounds, gross rent, home value, mortgage status, owner housing-cost-burden bins, matched-vintage Census-ZCTA price-to-income, and Census-ZCTA renter-occupied vintage change |
| Census SIPP published state tables | `census_sipp_2025_state_debt_2024`, `census_sipp_2025_state_wealth_2024` | California-only 2024 aggregate Home Debt and positive-home-equity sensitivities from rounded published inputs; not exact county FHFA/NMDB calibrated targets |
| Census/HUD AHS Table Creator | `census_ahs_2023_ca_table14a` | Official 2023 California Table 14A balance-bin estimates and MOEs plus a reported-case primary-mortgage debt proxy; excludes unreported balances and is not an exact first-lien control |
| Census PEP county estimates | `census_pep_county_population_v2019`, `census_pep_county_population_v2025`, `census_pep_county_housing_units_v2019`, `census_pep_county_housing_units_v2025` | Official July 1 LA County population and housing-unit denominators, retained annually in nonoverlapping 2010-2019 and 2020-2025 vintage segments |
| FHFA HPI | `fhfa_hpi_state_purchase_only`, `fhfa_hpi_metro_purchase_only`, `fhfa_hpi_county_annual` | State, metro, and county HPI benchmarks |
| California Department of Insurance | `ca_doi_residential_property_part_i` | Annual statewide Homeowners Multi-Peril written premium, written exposures, nominal and constant-2023-dollar average written premium, exact-calendar annual YoY growth, and an explicitly flagged carried-annual calendar-year monthly control, 2008-2023 |
| California Department of Industrial Relations | `ca_dir_california_cpi` | Official California CPI-U All Items published annual averages, 1955-2025; deflator for constant-2023-dollar homeowners premium |
| BEA | `bea_state_personal_income`, `bea_county_personal_income` | Personal income and per-capita personal income |
| IRS SOI | `irs_soi_county_migration` | LA County annual in-migration, out-migration, and net migration from IRS county migration returns, exemptions, and AGI; 2022-2023 is flagged as the enhanced-matching/new-series method break |
| CDC WONDER / NCHS | `cdc_wonder_natality_county`, `cdc_wonder_ucd_1999_2020_la_county`, `cdc_wonder_ucd_2018_2024_county` | Annual LA County live births, deaths, annual natural population change, and quarterly allocated natural population change |
| Eviction Lab | `eviction_lab_county_estimates` | Annual LA County eviction filing estimates, households-threatened estimates, filing/threatened rates, credible interval bounds, and source-observation flags |
| HUD PIT | `hud_pit_coc_2007_2025` | Official annual 2007-2025 sheltered, unsheltered, and overall PIT counts at native CoCs plus an explicit CA-600/606/607/612 project composite; never relabeled LA County. Explicit geography-year rows remain in `analytics.coc_annual_controls` even when all three analytical values are null. |
| HUD/USPS (active, licensed/restricted) | `hud_usps_ncwm_quarterly` | Eight quarterly county/tract/modeled-ZIP-part all-residential address variables and diagnostics; not rental-listing inventory. Current coverage is complete through 2026 Q1; a future refresh requires an authorized HUD User session/token. |
| Version A finance (repository-only relative to the latest Additional Variables document) | Nine configured source IDs in `config/version_a_benchmark.yaml`, including `cfpb_hmda_modified_lar` and the shared `freddie_mac_pmms` scenario input | Official 2025 HMDA down-payment benchmarks, source-derived finance fields, uncalibrated PMMS debt/equity term scenarios, and separately retained all-null calibrated debt/equity targets. These sources are not assigned to the Version A variables by the latest document. |

ACS appears as multiple source IDs because ACS Detailed Tables are official table-level files. Keeping B11001, B19013, B25001, etc. separate preserves the exact source file, checksum, table methodology, and variable/MOE mapping. Modeling users should usually consume the unified fact table, `analytics.county_annual_controls`, `analytics.zcta_annual_controls`, or the catalog rather than treating each ACS table as a separate modeling dataset.

Important semantics:

- The sole active rent index is Zillow ZORI smoothed, seasonally adjusted, all homes plus multifamily. Deprecated NSA ZORI files are not active in ingestion, clean tables, facts, or panels.
- Provider ZIPs are postal ZIPs, not Census ZCTAs. ACS ZCTA variables are labeled separately.
- Redfin ZIP Key Metrics are overlapping rolling-three-month observations. Adjacent ZIP Redfin rows should not be summed as single-month flows.
- Redfin ZIP `ACTIVE LISTINGS` remains `active_listings`; it is not relabeled as inventory.
- The listed housing-turnover requirement is implemented as the Redfin liquidity proxies `sales_to_inventory_ratio` and `inventory_to_sales_ratio`. These are available, but they are not true sales divided by total housing stock.
- Census BPS permits are authorizations, not starts or completions. The separately named Central and Slow apartment-completion and under-construction fields are modeled scenarios derived from 5+ unit permits; they are never relabeled as observed Census measures.
- Final annual BPS 1-unit permits are kept as `permits_1_unit_annual_final`; they are not silently mixed with the monthly BPS series.
- Freddie Mac mortgage rates are national, not Los Angeles-specific.
- Personal income is available at each BEA source's native geography and frequency: California quarterly and Los Angeles County annual. No county annual value is relabeled as observed quarterly data.
- Version A source-derived finance fields and explicitly labeled PMMS term scenarios are the current finance contract; the 12 calibrated debt/equity target IDs remain all-null pending a compatible California UPB universe. The older B25106 construction is a legacy modeled scenario only.
- The two SIPP California controls are separately labeled published-table sensitivities. They preserve statewide geography, rounding and 99th-percentile-truncation caveats and are never substituted into county calibrated debt/equity targets.
- The AHS California debt proxy uses only reported balance-bin households, retains all published bin and summary MOEs separately, and never constructs an all-household extrapolation or aggregate uncertainty measure.
- HMDA/Modified LAR is absent from the complete latest Additional Variables document. The 28 current HMDA fields are retained only as a clearly labeled repository Version A addition and are excluded from latest-document-compliant analyses unless explicit methodology authorization is added.
- ACS rental availability is an annual five-year-estimate proxy constructed from matched B25003 renter-occupied and B25004 for-rent vacant units; component margins of error are retained, unreliable county ratios are suppressed, and values are not interpolated.
- Historical B05002, B07001, B11001, B19013, B25001, B25002, B25003, B25004, B25032, B25064, B25077, B25081, B25106, and B25127 backfills use only the official Census ACS API. Immutable 2010-2024 archives are active. County observations cover 2010-2024; configured-ZCTA observations cover 2011-2024 because the 2010 API has no ZCTA geography. `CENSUS_API_KEY` is stored outside the repository, and personalized URLs or credentials are never written to configuration, raw provenance, logs, or tracked files.
- B05002 foreign-born population is a stock, while B07001 moved-from-abroad is gross current-resident mobility and not net international migration. Census "abroad" includes U.S. territories and the estimate can include returning U.S. residents. Both are overlapping ACS five-year estimates and remain analytically separate from IRS SOI net migration. The governing methodology names the ACS concepts/source family but not the exact table codes; B05002/B07001 were mapped after explicit user approval, and ZCTA output is a separately labeled repository extension.
- For historical ACS ZCTA comparisons, 2011-2020 vintages use 2010 Census ZCTA definitions and 2021-2024 vintages use 2020 Census ZCTA definitions. Boundary changes, overlapping five-year windows, and pandemic-period/weighting changes require comparability caution.
- B25032 structure shares and B25127 age-band bounds remain annual ACS five-year estimates. The noncontrolled structural bounds are null before 2020 and are not legal eligibility determinations. The exact `noncontrolled_exposure_share` is absent until the dated policy/exemption crosswalk is complete and usable at the target geography.
- `net_change_occupied_apartments_annual` is the difference between consecutive ACS B25003 renter-occupied five-year estimates. Fourteen LA County values are available for 2011-2024; adjacent vintages overlap for four survey years, so this is not an observed one-year or quarterly flow and it is never expanded to quarterly frequency.
- `county_annual_price_to_income_acs` matches B25077 median home value to B19013 median household income on Los Angeles County and the exact 2010-2024 ACS ending year. The 15 panel-only ratios are structurally recomputed during validation and are never written as fact observations or expanded to quarterly/monthly timing.
- `zcta_annual_price_to_income_acs` matches B25077 median home value to B19013 median household income on the exact Census ZCTA and ACS ending year. It contains 3,830 non-null ratios over 2011-2024; the six source-null ZCTA-vintages remain null.
- `zcta_renter_occupied_units_vintage_change_annual` contains 3,562 within-ZCTA consecutive-vintage B25003 differences for 2012-2024. Adjacent five-year vintages overlap for four survey years, so the values are not one-year or quarterly flows. Neither ZCTA variable is relabeled as a USPS postal-ZIP observation.
- CA DOI homeowners controls are statewide annual Homeowners Multi-Peril aggregates. `ca_homeowners_avg_written_premium_real` uses the approved California DIR annual CPI-U and a 2023 reference year; its 2023 value is identical to the nominal premium by construction. The separately authorized `ca_homeowners_avg_written_premium_monthly_control` carries each nominal annual value to the 12 month ends of its calendar year, is flagged `carried_annual_control` and `mixed_frequency_control`, retains `is_interpolated=false`, and is never presented as observed monthly or LA County data. The CPI and real-premium series themselves remain annual.
- HUD PIT observations are annual point-in-time counts. The four-CoC Los Angeles project composite is explicit and retains its component definition; it is not a county observation, and it does not satisfy a requested high-frequency homelessness series.
- HUD/USPS is active from the verified official archive. Its vacancy rate is an all-residential administrative proxy, no-stat remains separate, modeled ZIP values are postal-ZIP county-parts rather than ZCTAs, the native frequency remains quarterly, and neither measure is rental-listing inventory. Licensed detailed outputs remain restricted.
- BLS LAUS county data are not seasonally adjusted.
- CDC WONDER live births and deaths are annual county observations. `natural_population_change_annual` is births minus deaths; `natural_population_change_quarterly_allocated` evenly allocates the annual total to four quarters and is flagged as imputed/interpolated timing, not observed quarterly vital events.
- Eviction Lab LA County eviction trends are retained as annual modeled county estimates for 2000-2018. The official ETS Version 3.0 product registry was archived and audited on 2026-08-17: all 58 listed products expose weekly and monthly downloads, but zero rows name Los Angeles or California. The machine-readable result is in `metadata/eviction_lab_ets_source_check.csv`, so the retained annual series is not interpolated or expanded to monthly observations. It is not a direct local court filing feed; the pipeline preserves Eviction Lab's court-issued/observed source flags and 95% credible interval bounds.
- Native annual inputs remain annual in canonical facts and annual controls unless a separately named construction explicitly says otherwise. Annual CPI, real premium, PEP denominators, normalized rates, ACS measures, and Eviction Lab estimates are not copied into higher-frequency observations.
- Closed decisions remain closed: the twelve calibrated finance targets stay schema-only until a compatible universe is authorized; the exact policy-adjusted noncontrolled targets remain unavailable; single-family rental construction is not pursued without a qualifying public source; and true rental-listing inventory remains unavailable. Existing supporting inputs and sensitivities do not silently fill those targets.
- Missing values remain null. The pipeline does not interpolate, backfill, zero-fill, substitute county values into ZIPs, or silently drop incomplete source periods.

## Housing Variables.docx audit and source catalog

The repository now writes explicit audit/catalog outputs for the variables requested in `Housing Variables.docx`.

| File/table | Purpose |
|---|---|
| `metadata/docx_variable_requirements.csv` | Extracted requirement rows from `Housing Variables.docx`, preserving the sheet source, frequency, LA availability, notes, and best-public-workaround fields |
| `metadata/docx_variable_audit.csv` and `.xlsx` | One row per requested DOCX variable, with status, implemented source/variable IDs, tables, coverage, missingness, limitations, and next action |
| `metadata/proxy_methodology_assumptions.csv` | Explicit assumptions used in constructed/proxy variables, including the Central/Slow multifamily completion and stock scenarios, PEP-normalized annual rates, California CPI deflation, ACS noncontrolled structural bounds, the legacy ACS B25106 implied mortgage-debt proxy, and Eviction Lab annual-rate construction |
| `metadata/eviction_lab_ets_source_check.csv` and `metadata/eviction_lab_ets_registry_snapshot.csv` | Reproducible official ETS Version 3.0 source-update check, archived page provenance, complete product registry, and Los Angeles/California match result |
| `metadata/data_catalog.csv` and `.xlsx` | Active source catalog; the first four columns are `Source`, `Frequency`, `Geography`, `Key variables` |
| `metadata/data_gap_analysis.csv` | Variables that are deferred, partially implemented, unavailable, or need mentor decisions |
| `metadata/source_discovery.csv` | Source catalog copy for discovery/tracking workflows |
| `metadata/LA_Housing_Source_Tracker.xlsx` | Human-readable workbook that combines the source summary, DOCX variable audit, active variable dictionary, source coverage summary, raw file inventory/completeness, missing/deferred variables, proxy assumptions, and update commands. It tracks what exists and where it lives; observation rows remain in DuckDB, Parquet, and CSV exports. |
| `output/qa/raw_repository_completeness.csv` | Raw-file, manifest, checksum, URL, frequency, geography, and key-variable completeness checks |
| `quality.raw_repository_completeness` | DuckDB copy of the raw completeness report |

Current DOCX audit status:

These categories total the **38 prior DOCX requirements**. They are a legacy-requirement audit, distinct from the current canonical inventory of 54 methodology groups (**44 Have**, **7 Partial**, and **3 Do not have**) and the 482 configured variable IDs across the general and three housing-finance layers.

- Implemented: 22
- Implemented with quarterly allocation: 1
- Scenario implemented: 3
- Proxy implemented: 3
- Partially implemented: 7
- Proxy input only: 0
- Not implemented: 2

The audit is source-aligned to the sheet and applies the newest methodology when requirements conflict. True monthly rental-listing inventory remains unavailable because the governing methodology does not name an approved, integrable historical listing source; this is a methodology/source-identification blocker, not a credential or coding blocker. Redfin for-sale active listings do not satisfy it, and ZORI is a rent index rather than an inventory stock. The 2010-2024 ACS annual county rental-availability proxy and the HUD/USPS quarterly all-residential address-vacancy proxy are active as separate measures with different universes and frequencies; neither is expanded into monthly rental inventory. HUD/USPS licensed detail also remains outside public exports. Difficult-variable rows preserve the sheet's best-public-workaround text and report whether the current database implements the workaround, only has inputs, or has no source yet.

The original aggregate mortgage-debt workaround remains implemented for reproducibility as a legacy annual ACS modeled scenario, not an observed balance or the current benchmark. It allocates ACS B25081 owner-with-mortgage counts across ACS B25106 positive-income owner cost-burden bins, infers monthly debt service from income and burden midpoints, and converts payment to an implied balance using a fixed-rate amortization formula and Freddie Mac PMMS rates averaged over the ACS five-year window. Its assumptions remain recorded in `metadata/proxy_methodology_assumptions.csv`; analytical work should use the Version A source-derived fields and explicitly labeled raw PMMS scenarios while treating the calibrated debt/equity targets as unavailable until their universe blocker is resolved.

The natural-population-change workaround is implemented from official CDC WONDER/NCHS exports. Annual LA County live births and deaths are loaded as observed annual facts for 2007-2024. Annual natural population change is births minus deaths. The quarterly series is a modeling timing allocation equal to annual natural population change divided by four in each quarter, and it is explicitly flagged as imputed/interpolated.

The eviction-trends workaround is implemented from the official Eviction Lab county annual estimates file for 2000-2018. The official ETS Version 3.0 product registry was archived and audited on 2026-08-17; its 58 product rows contain neither Los Angeles nor California, and the official observed/validated county analysis files also do not contain LA County rows. The database therefore retains Eviction Lab's annual modeled county estimates for LA County without interpolating them. The active variables include annual filing estimates, households-threatened estimates, constructed rates per 100 renter households, 95% credible interval bounds, and Eviction Lab source-observation flags. This is a low-frequency eviction-pressure control, not monthly ETS or direct local court filing data. The source check is reproducible with `python -m src.ingestion.eviction_lab_ets_audit --root .`.

Historical refresh note: the fourteen configured sources `census_acs_5year_b05002`, `census_acs_5year_b07001`, `census_acs_5year_b11001`, `census_acs_5year_b19013`, `census_acs_5year_b25001`, `census_acs_5year_b25002`, `census_acs_5year_b25003`, `census_acs_5year_b25004`, `census_acs_5year_b25032`, `census_acs_5year_b25064`, `census_acs_5year_b25077`, `census_acs_5year_b25081`, `census_acs_5year_b25106`, and `census_acs_5year_b25127` use immutable official Census ACS API archives for the 2010 through 2024 five-year vintages. The downloader requests Los Angeles County for every year, California ZCTAs for 2011-2019, and nationwide ZCTAs for 2020-2024 before filtering to the versioned 274-ZCTA project reference. It records the unavailable 2010 ZCTA geography as an explicit provider gap. The ZCTA output uses 2010 Census ZCTA definitions for 2011-2020 ACS vintages and 2020 Census ZCTA definitions for 2021-2024 vintages; boundary changes, overlapping five-year windows, and pandemic-period/weighting changes require comparability caution. B25003 and B25004 must always be refreshed together before extending rental-availability history. `CENSUS_API_KEY` is stored in the Windows user environment outside the repository; if it is missing or invalid during a future refresh, the downloader exits without replacing an existing immutable archive or archiving a partial file. Personalized API URLs and keys are never stored.

```powershell
$env:CENSUS_API_KEY = "YOUR_CENSUS_API_KEY"
python run_pipeline.py --stage download --source census_acs_5year_b05002 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b07001 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b11001 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b19013 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25001 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25002 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25003 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25004 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25032 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25064 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25077 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25081 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25106 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25127 --force-download
python run_pipeline.py --stage all --offline
```

The table-based 2024 ACS raw files remain immutable archive vintages. The historical API responses are separate JSON raw vintages with request years, table variables, redacted Census request URLs, retrieval timestamps, source vintage, file size, geography contract, and SHA-256 checksum recorded in `metadata/raw_file_manifest.csv`. API credentials are never written to raw archives or tracked metadata.

## Installation

Python 3.11 or newer is required.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Commands below use `python` for readability and assume the environment is activated. If activation is disabled in PowerShell, use the full interpreter path:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --help
```

## Refreshing BLS LAUS data

The pipeline uses the official BLS Public Data API:

`https://api.bls.gov/publicAPI/v2/timeseries/data/`

An API retrieval is still a data download. DuckDB is not continuously connected to BLS. Opening `database/housing.duckdb` does not update the data. The database contains a static source-vintaged snapshot, and BLS may revise historical LAUS observations.

The configured Los Angeles County LAUS source ID is:

```text
bls_laus_la_county
```

The configured series IDs are:

- `LAUCN060370000000003` - unemployment rate
- `LAUCN060370000000004` - unemployment
- `LAUCN060370000000005` - employment
- `LAUCN060370000000006` - labor force

Request a fresh official API response and archive it as a new raw JSON vintage:

```powershell
python run_pipeline.py --stage download --source bls_laus_la_county --force-download
```

This command posts the configured LA County series IDs to the official endpoint, validates the response, saves a new immutable JSON file under `data/raw/bls/`, preserves earlier BLS raw files, and records source ID, endpoint, request series/year details, retrieval timestamp, source vintage, raw filename, byte size, SHA-256 checksum, latest returned observation, and observation count in `metadata/raw_file_manifest.csv`. If `database/housing.duckdb` already exists, it also refreshes `metadata.source_registry` and `raw.file_manifest`.

In the current local archive, the latest BLS LAUS raw manifest row was retrieved on `2026-07-02T06:08:52.347537+00:00`, requested years 1990-2026, recorded 1,744 numeric observations across the four configured series, and reported latest observation `2026-05-31`. After any future refresh, verify the new row in `metadata/raw_file_manifest.csv` or `raw.file_manifest`.

`--force-download` requests a new API response even if a same-day raw file exists. `--offline` makes no API request and reuses local raw files.

The download step only archives the API response. To propagate the newest archived BLS vintage through clean Parquet, analytical tables, DuckDB, QA, catalog, and exports, run:

```powershell
python run_pipeline.py --stage all --offline
```

The downloader exits nonzero if BLS returns an HTTP/API error, invalid JSON, missing configured series, or no numeric observations for the requested window.

## CLI stages

The current CLI is:

```powershell
python run_pipeline.py --help
```

Available stages:

| Stage | Behavior |
|---|---|
| `discover` | Performs source discovery/endpoint checks unless `--offline`; writes preliminary source discovery metadata |
| `download` | Acquires immutable raw vintages and appends raw manifest rows |
| `clean` | Runs source-specific cleaners against selected archived raw vintages |
| `harmonize` | Builds the long fact table, analytical panels, native-frequency controls, and derived features |
| `build-database` | Materializes DuckDB metadata, raw manifest, clean, analytics, and initial quality tables |
| `validate` | Runs structural QA and warning checks; writes QA and coverage reports |
| `catalog` | Writes DOCX audit, data catalog, source tracker workbook, gap analysis, and raw completeness QA |
| `export` | Writes CSV exports from final Parquet |
| `all` | Runs `discover`, `download`, `clean`, `harmonize`, `build-database`, `validate`, `catalog`, and `export` in that order |
| `refresh` | Checks every configured general source for the newest eligible release, blocks on automatic acquisition or cleaning failures, rebuilds all general estimates, and writes `metadata/source_refresh_status.csv` |

Common commands:

```powershell
python run_pipeline.py --stage all --offline
python run_pipeline.py --stage refresh
python run_pipeline.py --stage validate
python run_pipeline.py --stage catalog
python run_pipeline.py --stage export
python -m pytest -q
```

Exact single-source refresh examples used by the regression contract:

```powershell
python run_pipeline.py --stage download --source bls_ces_la_metro --force-download
python run_pipeline.py --stage download --source bls_cpi_la_metro --force-download
python run_pipeline.py --stage download --source fhfa_hpi_state_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_metro_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_county_annual --force-download
python run_pipeline.py --stage download --source bea_state_personal_income --force-download
python run_pipeline.py --stage download --source bea_county_personal_income --force-download
python run_pipeline.py --stage download --source census_hvs_state_rental_vacancy --force-download
```

Supported options:

- `--source SOURCE_ID`: filters source-aware stages such as `discover`, `download`, and `clean`.
- `--start-date` / `--end-date`: constrain supported downloads or harmonized output dates.
- `--vintage YYYY-MM-DD`: selects local raw vintages by retrieval-date logic where implemented.
- `--force-download`: bypasses same-day raw reuse and requests new source files/API responses.
- `--local-file PATH`: imports a manually downloaded official file for sources such as `redfin_zip_monthly`.
- `--offline`: disables network downloads/checks and reuses local raw archives.
- `--log-level`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

Pipeline run status is appended to `metadata/pipeline_run_log.csv`.

## Rebuild workflows

Rebuild everything from already archived raw files:

```powershell
python run_pipeline.py --stage all --offline
```

Refresh automatically downloadable sources, then rebuild:

```powershell
python run_pipeline.py --stage refresh
```

Refresh the general database, rebuild Version B from the newest archived retrospective inputs, create an append-only Version C as-of vintage, and then re-register and validate the integrated database:

```powershell
python run_system_refresh.py --information-cutoff YYYY-MM-DD
```

This command deliberately verifies but does not rebuild frozen Version A. Automatically downloadable source failures and strict cleaning failures stop publication instead of silently falling back to an older clean file. Manual or licensed sources remain explicit intervention items in `metadata/source_refresh_status.csv`; existing checksummed archives can be retained only with that warning. The command is schedulable but the repository does not install a Windows Task Scheduler, cron, or CI schedule automatically.

Import a new official Redfin ZIP Key Metrics CSV:

```powershell
python run_pipeline.py --stage download --source redfin_zip_monthly --local-file "PATH_TO_OFFICIAL_REDFIN_CSV"
python run_pipeline.py --stage all --offline
```

The Redfin ZIP import preserves the national CSV as immutable raw data. Cleaning filters to Los Angeles County postal ZIPs using the versioned ZIP reference and writes the LA subset plus long-form ZIP observations.

## Storage and provenance

| Location | Contents |
|---|---|
| `data/raw/<provider>/` | Immutable provider payloads; filenames include source ID, retrieval timestamp, and checksum prefix |
| `data/archive/deprecated_zori_nsa/` | Deprecated NSA ZORI archive; excluded from active ingestion |
| `data/restricted/hud_usps/raw/` | Licensed immutable HUD/USPS official archive: 73 NCWM responses, one tract-to-ZIP crosswalk, and one Census county-code reference |
| `data/restricted/hud_usps/staging/` and `active/` | Licensed rebuilt county, tract, modeled postal-ZIP-part, common-fact, and extended-lineage outputs |
| `data/interim/` | Reusable intermediate outputs, including Redfin ZIP long observations |
| `data/clean/` | Source-specific normalized Parquet files |
| `data/final/` | Long fact table, monthly panels, and native-frequency control panels |
| `data/benchmark/` | Version A immutable raw archives, manifests, and benchmark clean intermediates |
| `database/housing.duckdb` | Integrated query database |
| `metadata/raw_file_manifest.csv` | Raw URL/API endpoint, retrieval timestamp, filename, size, checksum, source vintage, and request metadata |
| `metadata/source_coverage.csv` | Variable/source date coverage and non-null counts |
| `metadata/system_source_inventory.csv` | Consolidated 69-source inventory with frequency, geography, access, refresh behavior, and official/local locations |
| `metadata/analytical_dataset_inventory.csv` | Live inventory of analytical tables and views in DuckDB, including row/date/geography counts |
| `metadata/system_variable_inventory.csv` | Consolidated 482-ID variable inventory with Version A/B/C suffixes and materialization coverage |
| `metadata/estimate_inventory.csv` | Constructed/proxy/scenario estimates, methods, output locations, and refresh dependencies |
| `metadata/system_storage_inventory.csv` | Physical storage-layer inventory distinguishing the active database, raw, clean, final, restricted, and export files |
| `metadata/source_refresh_status.csv` | Latest strict refresh attempt, archive checksum verification, blocking status, and manual intervention text |
| `metadata/data_dictionary.csv` | Generated consolidated variable dictionary from `config/variables.yaml` and `config/version_a_variables.yaml` |
| `metadata/hud_usps_source_manifest.csv` | Active inventory of the 75 verified HUD/USPS and supporting Census official artifacts |
| `metadata/hud_usps_quarantine_manifest.csv` | Historical record for the superseded 2026-08-02 derived package; not an active raw manifest |
| `metadata/transformation_log.csv` | Transformation IDs, inputs, outputs, and execution metadata |
| `output/coverage/` | Coverage, missingness, BPS, Redfin, and source-vintage reports |
| `output/qa/` | QA summary/failure reports and raw repository completeness |
| `output/exports/` | Modeling-friendly CSV exports |

Raw payloads are never edited, replaced, or deleted by the pipeline. Clean and analytical outputs are rebuildable products from selected local raw vintages.

## DuckDB schemas

`database/housing.duckdb` uses five schemas:

- `metadata`
- `raw`
- `clean`
- `analytics`
- `quality`

`metadata.source_registry` and `raw.file_manifest` are provenance tables. They contain source-vintage/raw-manifest rows, not copies of provider payloads. The general configuration has 61 enabled source IDs. Version A defines nine source IDs and shares `freddie_mac_pmms` with the general configuration, producing 69 unique source IDs across the repository. Census ACS, Census BPS, Census PEP, California DIR CPI, HUD/USPS, SIPP table packages, and the AHS Table Creator export retain immutable source vintages with retrieval and checksum provenance.

The `clean` schema is rebuilt from active configured sources only. It contains one table per active source ID, except `redfin_zip_monthly`, whose raw national file is filtered into `clean.redfin_zip_monthly`. It also exposes the union compatibility views `clean.fhfa_hpi` and `clean.census_hvs_rental_vacancy`; these views do not create new observations or change source-specific tables.

The `analytics` schema contains:

- `analytics.fact_housing_observation`
- `analytics.county_monthly_panel`
- `analytics.zip_monthly_panel`
- `analytics.comparable_county_monthly_analytics`
- `analytics.zip_market_regime_quarterly`
- `analytics.la_zip_market_regime_summary_quarterly`
- `analytics.la_zip_market_regime_transitions_quarterly`
- `analytics.county_housing_market_heat_monthly`
- `analytics.la_relative_market_performance_monthly`
- `analytics.state_quarterly_panel`
- `analytics.metro_monthly_controls`
- `analytics.metro_quarterly_controls`
- `analytics.county_annual_controls`
- `analytics.zcta_annual_controls`
- `analytics.county_quarterly_controls`
- `analytics.state_annual_controls`
- `analytics.state_monthly_controls`
- `analytics.coc_annual_controls`
- `analytics.homebuying_cost_scenarios`
- `analytics.homeowner_market_monthly`
- `analytics.renter_market_monthly`
- `analytics.overall_market_monthly`
- `analytics.macro_controls_monthly`

The `quality` schema contains:

- `quality.qa_summary`
- `quality.qa_failures`
- `quality.raw_repository_completeness`

## Derived features

County and postal-ZIP derived fields include:

- one-month and exact-calendar twelve-month ZHVI/ZORI growth, including ZIP ZORI growth where the exact prior observation exists;
- canonical `price_to_rent_ratio_zillow = ZHVI / (12 * ZORI)` and its legacy alias;
- `implied_gross_rent_yield = 12 * ZORI / ZHVI`;
- inventory-to-sales and sales-to-inventory ratios;
- down-payment scenarios at 3.5%, 10%, and 20%;
- canonical sale-price closing-cost scenarios at 2%, 3%, and 5%;
- loan-based closing costs at 3% and 6% for each down-payment scenario;
- estimated mortgage amount and total cash-to-close scenarios;
- principal-and-interest mortgage-payment scenarios using Freddie Mac monthly-average 30-year rates;
- mortgage-payment-to-rent ratios; and
- market-tightness index components.

The long-form `analytics.homebuying_cost_scenarios` table contains the full three-down-payment by five-closing-cost cross-product: 15 scenarios for every positive county or ZIP sale-price observation. It contains 2,595 county rows and 344,190 postal-ZIP rows. Prepaid escrow is excluded. Mortgage-payment and cash-to-close values are affordability scenarios, not observed borrower-level transactions; mortgage payments also exclude taxes, insurance, HOA fees, maintenance, PMI, and closing-cost financing.

## QA and coverage

Run validation:

```powershell
python run_pipeline.py --stage validate
```

The current validation run completed with 0 structural errors and 3 warning checks. Structural QA failures stop validation. Plausible economic outliers, source freshness, and expected source gaps are warnings for review.

QA and coverage include duplicate-key checks, geography ID/FIPS validation, ZORI product identity, Redfin ZIP rolling-window semantics, BPS archive coverage, missingness summaries, clean-to-panel equality checks, and raw repository completeness. Source coverage explicitly distinguishes `postal_zip`, `zcta`, `county`, `state`, `metro`, `metro_division`, and `national`; unsupported geography identifiers remain `unknown` and fail structural geography validation instead of being relabeled as national.

Core reports:

- `output/qa/qa_summary.csv`
- `output/qa/qa_failures.csv`
- `output/qa/raw_repository_completeness.csv`
- `output/coverage/source_coverage.csv`
- `output/coverage/time_coverage.csv`
- `output/coverage/zip_coverage.csv`
- `output/coverage/missingness_summary.csv`
- `output/coverage/bps_archive_coverage.csv`
- `output/coverage/bps_archive_period_report.csv`
- `output/coverage/redfin_active_vintage.csv`
- `output/coverage/redfin_vintage_comparison.csv`
- `output/coverage/redfin_zip_source_audit.csv`
- `output/coverage/redfin_zip_coverage.csv`
- `output/coverage/redfin_zip_yoy_comparison.csv`

Run tests:

```powershell
python -m pytest -q
```

## Current coverage notes

- Census BPS county and state monthly archive selection runs from January 2000 through the latest published configured month, June 2026. Permits remain authorizations, not starts, construction, or completions.
- The separate final annual Census BPS LA County 1-unit series has 36 observations from 1990 through 2025. Its same-year PEP-normalized rate has 16 observations for 2010-2025.
- Central 5+ permit completion and under-construction scenarios have 288 and 289 monthly non-null observations, respectively; the Slow scenarios have 282 and 283. Their full lag/cohort windows and null-propagation rules are documented in `docs/multifamily_construction_proxies.md`.
- Zillow ZHVI runs through May 2026 in the current build.
- Active Zillow ZORI SA runs from January 2015 through May 2026.
- Canonical Zillow price-to-rent ratio and implied gross rent yield are available in both county and postal-ZIP panels. Exact-calendar ZORI growth is also constructed at ZIP level; natural source gaps remain null.
- Redfin county monthly data runs from January 2012 through May 2026.
- Redfin ZIP rolling-three-month data runs from June 2019 through May 2026.
- BLS LAUS runs from January 1990 through May 2026.
- Freddie Mac PMMS runs from April 1971 through June 2026.
- Historical ACS B05002, B07001, B11001, B19013, B25001, B25002, B25003, B25004, B25032, B25064, B25077, B25081, B25106, and B25127 variables use official 2010-2024 API archives. County coverage begins in 2010; configured-ZCTA coverage begins in 2011 because the 2010 ACS API does not publish ZCTA geography. Preserved 2024 table-based files remain immutable raw vintages.
- B25032/B25127 direct cells and margins of error remain annual ACS five-year estimates. Their eight derived structure/age/exposure fields are annual county or Census-ZCTA constructions; the two noncontrolled structural proxies begin in 2020. Exact legal exposure and exposure-adjusted ZORI growth remain unavailable pending a complete dated policy/exemption crosswalk and approved geography/frequency method.
- B05002 foreign-born stock and B07001 moved-from-abroad gross mobility each have 3,851 estimate rows and 3,851 published-MOE rows: 15 Los Angeles County vintages for 2010-2024 and 3,836 configured-ZCTA vintages for 2011-2024. Neither is a replacement for IRS SOI net migration. ZCTA comparisons cross the 2010-definition/2020-definition boundary between the 2020 and 2021 ACS vintages, and overlapping five-year windows plus pandemic-period/weighting changes limit year-to-year comparability.
- The ACS county rental-availability rate has 15 matched annual values for 2010-2024. `net_change_occupied_apartments_annual` has 14 consecutive-vintage county differences for 2011-2024. The annual Census-ZCTA panel has 3,836 exact ZCTA-vintage rows for 2011-2024, 3,830 non-null matched-vintage price-to-income ratios, and 3,562 renter-occupied vintage differences for 2012-2024. These difference measures remain overlapping-five-year-estimate proxies, not quarterly flows.
- HVS rental-vacancy series are state/metro quarterly controls, not LA County observations.
- CA DOI Homeowners Multi-Peril controls contain 16 statewide annual observations from 2008 through 2023; annual average-premium YoY contains 15 non-null values from 2009 through 2023. California DIR CPI-U has 71 annual observations for 1955-2025, and the CPI-deflated premium has 16 annual constant-2023-dollar observations for 2008-2023. The authorized nominal calendar-year monthly control contains 192 explicitly constructed carried-annual month-end values over 2008-2023, flagged `carried_annual_control` and `mixed_frequency_control` with `is_interpolated=false`; it is not missing-value imputation or observed monthly insurance.
- HUD PIT uses the official annual 2007-2025 CoC workbook and an explicit four-CoC project composite for CA-600, CA-606, CA-607, and CA-612. The 2025 composite is 72,207 overall, 24,757 sheltered, and 47,450 unsheltered people. Count Types are canonicalized and applied row by row, including mixed 2021 full, partial-unsheltered, and sheltered-only records; incomplete analytical totals remain null while reported values are retained.
- IRS SOI migration preserves annual filing-year intervals. All nine 2022-2023 inflow, outflow, and net observations carry `provider_methodology_break_2022_2023_enhanced_matching_new_series`; values are not altered by this provider-method flag. `net_exemption_rate` has 13 annual values for 2011-2023 and uses the interval's ending-year PEP population, never a prior-year or ACS denominator.
- HUD/USPS runs quarterly from 2008 Q1 through 2026 Q1 for all 58 California counties, 2,494 Los Angeles County standardized 2020 tracts, and 477 modeled Los Angeles County postal-ZIP county-parts. Detailed values are licensed and restricted; the series is not true rental-listing inventory.
- Eviction Lab county eviction estimates run from 2000 through 2018 for Los Angeles County. This is annual modeled coverage. The official ETS Version 3.0 registry check dated 2026-08-17 found zero Los Angeles/California matches across 58 product rows, so no ETS/high-frequency series is integrated and no annual value is interpolated.

## Query examples

Open DuckDB read-only from Python:

```python
import duckdb

con = duckdb.connect("database/housing.duckdb", read_only=True)
county = con.sql("""
    SELECT date, zhvi, zori, unemployment_rate, permits_total
    FROM analytics.county_monthly_panel
    WHERE date >= DATE '2020-01-01'
    ORDER BY date
""").df()
```

Inspect the newest BLS raw vintage:

```sql
SELECT
    retrieved_at,
    source_filename,
    checksum_sha256,
    request_series_ids,
    request_start_year,
    request_end_year,
    response_latest_observation,
    response_observation_count
FROM raw.file_manifest
WHERE source_id = 'bls_laus_la_county'
ORDER BY retrieved_at DESC
LIMIT 1;
```

Inspect ZIP source availability:

```sql
SELECT
    postal_zip,
    min(date) AS first_month,
    max(date) AS latest_month,
    count(*) FILTER (WHERE zhvi_available) AS zhvi_months,
    count(*) FILTER (WHERE zori_available) AS zori_months,
    count(*) FILTER (WHERE redfin_available) AS redfin_periods
FROM analytics.zip_monthly_panel
GROUP BY postal_zip
ORDER BY postal_zip;
```

## Known limitations

- Provider histories can be revised. Raw vintages preserve retrieved snapshots, but active clean/analytical tables select the latest configured local vintage.
- HVS official raw workbooks are archived and parsed directly by the source-specific workbook cleaner. State and metro estimates remain at their published quarterly geographies; no LA County value is fabricated.
- Redfin ZIP Key Metrics has no verified stable direct endpoint for the selected CSV export and requires manual official download/import.
- True rental-listing inventory is not implemented because the approved methodology does not list an integrable historical rental-listing source; ZORI is not inventory. No RentCast data or source configuration is used.
- The active HUD/USPS all-residential vacancy proxy does not satisfy true rental-listing inventory. Its detailed observations are licensed/restricted, its modeled ZIP geography is a Los Angeles County portion of a postal ZIP rather than a whole ZIP or Census ZCTA, and it remains quarterly rather than monthly.
- The explicit Version A PMMS 20/25/30-year debt and equity variables are available only as uncalibrated scenarios. The B25106 mortgage debt/equity construction remains a separate legacy reproducibility scenario. The archived FHFA NMDB state ZIP `TOT_UPB` control covers all active mortgages and is not universe-compatible with an owner-occupied first-lien calibration, so calibrated debt/equity fields remain blocked on a documented universe/method decision rather than credentials.
- CA DOI real-dollar premium is implemented annually with California DIR CPI-U in constant 2023 dollars. The 2023 real/nominal identity is enforced; neither CPI nor real premium is fabricated at monthly frequency.
- HUD PIT satisfies the official annual source requirement only. It does not create high-frequency homelessness observations.
- Eviction trends are implemented only as annual Eviction Lab modeled county estimates for 2000-2018; they should not be interpreted as high-frequency ETS or direct LA court filing data.
- Permits are not starts, completions, or units under construction. The Central/Slow completion and pipeline-stock variables are explicit modeled scenarios with versioned lag weights, full-window missingness, and no unsuffixed point estimate.
- County values are not silently allocated to ZIPs, and ZIP values are not treated as ZCTAs. The explicit HUD/USPS exception models postal-ZIP county-parts from tract counts with the fixed, versioned residential-address crosswalk and preserves modeled lineage.
- Deferred variables and mentor-decision items are documented in `metadata/data_gap_analysis.csv`.

## Adding or changing a source

1. Use only an official public download, documented API, or provider-published file.
2. Add complete source metadata and key variables to `config/sources.yaml`.
3. Add or update variable definitions in `config/variables.yaml`.
4. Preserve immutable raw files, retrieval timestamps, filenames, and SHA-256 checksums.
5. Preserve leading zeros in FIPS, ZIP, and ZCTA identifiers.
6. Implement source-specific cleaning and frequency/geography rules.
7. Update transformations, variable dictionary entries, QA, coverage, tests, catalog outputs, and this README.
8. Run:

```powershell
python run_pipeline.py --stage validate
python -m pytest -q
```
