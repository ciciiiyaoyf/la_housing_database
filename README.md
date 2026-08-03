# Los Angeles Housing Public-Data Database

This repository builds a reproducible, source-vintaged public-data database for Los Angeles housing research. It preserves immutable raw files, normalizes source-specific observations to a shared contract, builds DuckDB/Parquet analytical tables, and writes QA, coverage, catalog, and CSV outputs.

The database is a static snapshot. DuckDB is not continuously connected to Census, BLS, Zillow, Redfin, or any other provider. New observations enter the database only when the pipeline is run.

## Current generated outputs

The consolidated configuration contract contains **221 unique variables**: 163 general variables in `config/variables.yaml` (including the ACS rental-availability variables and eight active HUD/USPS variables) plus 58 Version A variables in `config/version_a_variables.yaml`. It contains **45 unique source IDs**: 37 general source IDs plus nine Version A source IDs, with `freddie_mac_pmms` present in both configurations and counted once. All 37 general sources are enabled; `hud_usps_ncwm_quarterly` is active from a verified official raw archive.

The fully materialized general-pipeline snapshot summarized below contains 2,190,619 long-form fact observations. Version A is built separately into `data/final/housing_finance_benchmark.parquet`. The active official HUD/USPS rebuild contributes 1,768,936 native-quarterly observations across 3,029 geographies; its detailed licensed tables remain under restricted storage and outside public CSV exports.

| Output | Rows | Date coverage in current build |
|---|---:|---|
| `analytics.fact_housing_observation` / `data/final/fact_housing_observation.parquet` | 2,190,619 | 1914-12-31 to 2026-06-30 |
| `analytics.county_monthly_panel` / `data/final/county_monthly_panel.parquet` | 437 | 1990-01-31 to 2026-05-31 |
| `analytics.zip_monthly_panel` / `data/final/zip_monthly_panel.parquet` | 86,858 | 2000-01-31 to 2026-05-31 |
| `analytics.state_quarterly_panel` / `data/final/state_quarterly_panel.parquet` | 313 | 1948-03-31 to 2026-03-31 |
| `analytics.metro_monthly_controls` / `data/final/metro_monthly_controls.parquet` | 1,095 | 1914-12-31 to 2026-05-31 |
| `analytics.metro_quarterly_controls` / `data/final/metro_quarterly_controls.parquet` | 226 | 1991-03-31 to 2026-03-31 |
| `analytics.county_annual_controls` / `data/final/county_annual_controls.parquet` | 57 | 1969-12-31 to 2025-12-31 |
| `analytics.county_quarterly_controls` / `data/final/county_quarterly_controls.parquet` | 72 | 2007-03-31 to 2024-12-31 |

CSV exports are written under `output/exports/`:

- `la_county_monthly_panel.csv`
- `la_zip_monthly_panel.csv`
- `la_state_quarterly_panel.csv`
- `la_metro_monthly_controls.csv`
- `la_metro_quarterly_panel.csv`
- `la_metro_quarterly_controls.csv`
- `la_county_annual_controls.csv`
- `la_county_quarterly_controls.csv`
- `la_version_a_benchmark.csv` when the Version A benchmark has been built

## Consolidated variable and source contract

The general pipeline and Version A benchmark are complementary parts of one repository contract:

| Contract | General | Version A | Consolidated unique total |
|---|---:|---:|---:|
| Variables | 163 | 58 | 221 |
| Source IDs | 37 | 9 | 45 |

All 58 Version A variable IDs end in `_benchmark`. For mortgage debt and home equity, these Version A variables are the authoritative current standard. In particular, the benchmark uses direct ACS B25089/B25081/B25082/B25120 levels, weighted PUMS parameters, and exact-date, universe-compatible NMDB controls when available; PMMS is an explicitly labeled sensitivity fallback. The earlier B25106 midpoint/80%-debt-service/25-year mortgage construction remains available only as a **legacy modeled scenario** for reproducibility and must not be presented as the primary debt or equity estimate.

The eight `usps_*` variables and `hud_usps_ncwm_quarterly` are active from 75 verified official artifacts: 73 contiguous quarterly NCWM responses for 2008 Q1 through 2026 Q1, the official 2026 Q1 tract-to-postal-ZIP crosswalk, and the official 58-row 2020 Census California county-code reference. `usps_residential_vacancy_rate = (STV + LTV residential addresses) / total residential addresses` is a quarterly **all-residential administrative vacancy proxy**. It is not renter-only, not a count of available rental listings, and not true rental-listing inventory. No-stat addresses remain a separate diagnostic. Source components are direct at tract geography, California county values are constructed from complete tract components, and modeled ZIP rows represent Los Angeles County portions of postal ZIPs—not whole postal ZIPs or Census ZCTAs.

See the [HUD/USPS methodology and active provenance record](docs/hud_usps_residential_vacancy_proxy.md), the [official source manifest](metadata/hud_usps_source_manifest.csv), and the [superseded quarantine manifest](metadata/hud_usps_quarantine_manifest.csv). The former 2026-08-02 derived package file has been deleted; only its historical manifest remains. Active values are rebuilt from official artifacts archived with source URLs/queries, filenames, exact UTC retrieval timestamps, provider vintages or release dates when supplied, geography, file sizes, and SHA-256 checksums. Licensed raw and detailed derived data remain under Git-ignored `data/restricted/hud_usps/` and outside public CSV exports.

Rental supply is represented by two separate proxies, not one interchangeable inventory series. The 2024 ACS five-year measure constructs annual Los Angeles County rental availability as `for-rent vacant / (renter-occupied + for-rent vacant)` from matched B25004 and B25003 estimates, retains both component margins of error, and suppresses unreliable county ratios. Direct component counts and margins of error are also retained for the configured Census ZCTAs, which are not postal ZIPs. The HUD/USPS measure remains a native-quarterly all-residential address-vacancy proxy. Neither measure is interpolated or relabeled as active rental listings; `rental_listing_inventory_monthly` remains unavailable pending acquisition of a documented historical listing universe and usable license. See the [ACS rental-availability methodology](docs/acs_rental_availability.md).

## Source groups

General source pages, download URLs/API endpoints, file formats, source IDs, key variables, and methodology notes live in `config/sources.yaml`; Version A sources live in `config/version_a_benchmark.yaml`. Variable definitions live in `config/variables.yaml` and `config/version_a_variables.yaml`.

| Group | Source IDs / status | Main variables |
|---|---|---|
| Zillow Research | `zillow_county_zhvi`, `zillow_zip_zhvi`, `zillow_county_zori_smoothed_sa`, `zillow_zip_zori_smoothed_sa` | ZHVI, active SA ZORI |
| Redfin Data Center | `redfin_county_monthly`, `redfin_zip_monthly` | Sale price, sales volume, listings, days on market, inventory/listing indicators |
| BLS | `bls_laus_la_county`, `bls_ces_la_metro`, `bls_cpi_la_metro` | LAUS labor force/unemployment, CES payroll employment, LA metro CPI-U |
| Freddie Mac | `freddie_mac_pmms` | Weekly and monthly 30-year fixed mortgage-rate measures |
| Census BPS | `census_bps_county`, `census_bps_state` | Total, 1-unit, 2-to-4-unit, and 5-plus-unit permits |
| Census HVS | `census_hvs_state_rental_vacancy`, `census_hvs_metro_rental_vacancy_2005_2014`, `census_hvs_metro_rental_vacancy_2015_2025`, `census_hvs_metro_rental_vacancy_2026` | Rental vacancy rates at state/metro geographies |
| Census ACS 5-year detailed tables | `census_acs_5year_b11001`, `b19013`, `b25001`, `b25002`, `b25003`, `b25004`, `b25064`, `b25077`, `b25081`, `b25106` | Households, income, housing units, vacancy, tenure, for-rent vacant units, annual rental availability, gross rent, home value, mortgage status, owner housing-cost-burden bins |
| FHFA HPI | `fhfa_hpi_state_purchase_only`, `fhfa_hpi_metro_purchase_only`, `fhfa_hpi_county_annual` | State, metro, and county HPI benchmarks |
| BEA | `bea_state_personal_income`, `bea_county_personal_income` | Personal income and per-capita personal income |
| IRS SOI | `irs_soi_county_migration` | LA County annual in-migration, out-migration, and net migration from IRS county migration returns, exemptions, and AGI |
| CDC WONDER / NCHS | `cdc_wonder_natality_county`, `cdc_wonder_ucd_1999_2020_la_county`, `cdc_wonder_ucd_2018_2024_county` | Annual LA County live births, deaths, annual natural population change, and quarterly allocated natural population change |
| Eviction Lab | `eviction_lab_county_estimates` | Annual LA County eviction filing estimates, households-threatened estimates, filing/threatened rates, credible interval bounds, and source-observation flags |
| HUD/USPS (active, licensed/restricted) | `hud_usps_ncwm_quarterly` | Eight quarterly county/tract/modeled-ZIP-part all-residential address variables and diagnostics; not rental-listing inventory |
| Version A finance | Nine configured source IDs in `config/version_a_benchmark.yaml`, including the shared `freddie_mac_pmms` fallback | Authoritative `_benchmark` mortgage debt, home equity, debt-service, down-payment, CPI, and supporting variables |

ACS appears as multiple source IDs because ACS Detailed Tables are official table-level files. Keeping B11001, B19013, B25001, etc. separate preserves the exact source file, checksum, table methodology, and variable/MOE mapping. Modeling users should usually consume the unified fact table, `analytics.county_annual_controls`, or the catalog rather than treating each ACS table as a separate modeling dataset.

Important semantics:

- The sole active rent index is Zillow ZORI smoothed, seasonally adjusted, all homes plus multifamily. Deprecated NSA ZORI files are not active in ingestion, clean tables, facts, or panels.
- Provider ZIPs are postal ZIPs, not Census ZCTAs. ACS ZCTA variables are labeled separately.
- Redfin ZIP Key Metrics are overlapping rolling-three-month observations. Adjacent ZIP Redfin rows should not be summed as single-month flows.
- Redfin ZIP `ACTIVE LISTINGS` remains `active_listings`; it is not relabeled as inventory.
- Census BPS permits are authorizations, not starts or completions.
- Freddie Mac mortgage rates are national, not Los Angeles-specific.
- Version A `_benchmark` mortgage debt and equity variables are authoritative; the older B25106 construction is a legacy modeled scenario only.
- ACS rental availability is an annual five-year-estimate proxy constructed from matched B25003 renter-occupied and B25004 for-rent vacant units; component margins of error are retained, unreliable county ratios are suppressed, and values are not interpolated.
- HUD/USPS is active from the verified official archive. Its vacancy rate is an all-residential administrative proxy, no-stat remains separate, modeled ZIP values are postal-ZIP county-parts rather than ZCTAs, the native frequency remains quarterly, and neither measure is rental-listing inventory. Licensed detailed outputs remain restricted.
- BLS LAUS county data are not seasonally adjusted.
- CDC WONDER live births and deaths are annual county observations. `natural_population_change_annual` is births minus deaths; `natural_population_change_quarterly_allocated` evenly allocates the annual total to four quarters and is flagged as imputed/interpolated timing, not observed quarterly vital events.
- Eviction Lab LA County eviction trends are annual modeled county estimates for 2000-2018. They are not monthly ETS observations and not a direct local court filing feed. The pipeline preserves Eviction Lab's court-issued/observed source flags and 95% credible interval bounds.
- Missing values remain null. The pipeline does not interpolate, backfill, zero-fill, substitute county values into ZIPs, or silently drop incomplete source periods.

## Housing Variables.docx audit and source catalog

The repository now writes explicit audit/catalog outputs for the variables requested in `Housing Variables.docx`.

| File/table | Purpose |
|---|---|
| `metadata/docx_variable_requirements.csv` | Extracted requirement rows from `Housing Variables.docx`, preserving the sheet source, frequency, LA availability, notes, and best-public-workaround fields |
| `metadata/docx_variable_audit.csv` and `.xlsx` | One row per requested DOCX variable, with status, implemented source/variable IDs, tables, coverage, missingness, limitations, and next action |
| `metadata/proxy_methodology_assumptions.csv` | Explicit assumptions used in constructed/proxy variables, including the ACS B25106 implied mortgage-debt proxy and Eviction Lab annual-rate construction |
| `metadata/data_catalog.csv` and `.xlsx` | Active source catalog; the first four columns are `Source`, `Frequency`, `Geography`, `Key variables` |
| `metadata/data_gap_analysis.csv` | Variables that are deferred, partially implemented, unavailable, or need mentor decisions |
| `metadata/source_discovery.csv` | Source catalog copy for discovery/tracking workflows |
| `metadata/LA_Housing_Source_Tracker.xlsx` | Human-readable workbook that combines the source summary, DOCX variable audit, active variable dictionary, source coverage summary, raw file inventory/completeness, missing/deferred variables, proxy assumptions, and update commands. It tracks what exists and where it lives; observation rows remain in DuckDB, Parquet, and CSV exports. |
| `output/qa/raw_repository_completeness.csv` | Raw-file, manifest, checksum, URL, frequency, geography, and key-variable completeness checks |
| `quality.raw_repository_completeness` | DuckDB copy of the raw completeness report |

Current DOCX audit status:

- Implemented: 19
- Implemented with quarterly allocation: 1
- Scenario implemented: 3
- Proxy implemented: 3
- Partially implemented: 4
- Partially implemented with alternate source: 1
- Proxy input only: 3
- Active all-residential proxy (not true rental inventory): 1
- Not implemented: 3

The audit is source-aligned to the sheet. True monthly rental-listing inventory remains unavailable at documented source and license acquisition: Redfin for-sale active listings do not satisfy it, and ZORI is a rent index rather than an inventory stock. The 2024 ACS annual rental-availability proxy and the HUD/USPS quarterly all-residential address-vacancy proxy are active as separate measures with different universes and frequencies; neither is expanded into monthly rental inventory. HUD/USPS licensed detail also remains outside public exports. Difficult-variable rows preserve the sheet's best-public-workaround text and report whether the current database implements the workaround, only has inputs, or has no source yet.

The original aggregate mortgage-debt workaround remains implemented for reproducibility as a legacy annual ACS modeled scenario, not an observed balance or the current benchmark. It allocates ACS B25081 owner-with-mortgage counts across ACS B25106 positive-income owner cost-burden bins, infers monthly debt service from income and burden midpoints, and converts payment to an implied balance using a fixed-rate amortization formula and Freddie Mac PMMS rates averaged over the ACS five-year window. Its assumptions remain recorded in `metadata/proxy_methodology_assumptions.csv`; analytical work should use the Version A `_benchmark` debt and equity variables as the authoritative current standard.

The natural-population-change workaround is implemented from official CDC WONDER/NCHS exports. Annual LA County live births and deaths are loaded as observed annual facts for 2007-2024. Annual natural population change is births minus deaths. The quarterly series is a modeling timing allocation equal to annual natural population change divided by four in each quarter, and it is explicitly flagged as imputed/interpolated.

The eviction-trends workaround is implemented from the official Eviction Lab county annual estimates file for 2000-2018. Eviction Lab ETS does not cover Los Angeles County, and the official observed/validated county analysis files do not contain LA County rows, so the database uses Eviction Lab's annual modeled county estimates for LA County instead. The active variables include annual filing estimates, households-threatened estimates, constructed rates per 100 renter households, 95% credible interval bounds, and Eviction Lab source-observation flags. This is a low-frequency eviction-pressure control, not monthly ETS or direct local court filing data.

Historical refresh note: `census_acs_5year_b25003` and `census_acs_5year_b25004` currently use matched official 2024 table-based summary files. They must be refreshed together before claiming additional rental-availability history. The other proxy-input tables `census_acs_5year_b25077`, `census_acs_5year_b25081`, and `census_acs_5year_b25106` are configured for the official Census ACS 5-year API from the 2010 through 2024 ACS five-year vintages. The downloader uses the same Census Data API pattern shown by data.census.gov's More Tools -> API modal, for example `get=group(B25106)&ucgid=050C0US06037`. It also queries the versioned local LA ZIP/ZCTA list as `860Z200US...` UCGIDs in chunks. If Census redirects the automated request to `missing_key.html`, set `CENSUS_API_KEY` in the shell before refreshing; if the key is missing or invalid, the downloader exits before archiving any partial raw file.

```powershell
$env:CENSUS_API_KEY = "YOUR_CENSUS_API_KEY"
python run_pipeline.py --stage download --source census_acs_5year_b25077 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25081 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25106 --force-download
python run_pipeline.py --stage all --offline
```

The table-based 2024 ACS raw files remain immutable archive vintages. New API refreshes for the configured historical-API tables are stored as separate JSON raw vintages with request years, table variables, Census URLs, retrieval timestamps, source vintage, file size, and SHA-256 checksum recorded in `metadata/raw_file_manifest.csv`.

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

Common commands:

```powershell
python run_pipeline.py --stage all --offline
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
python run_pipeline.py --stage all --force-download
```

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

`metadata.source_registry` and `raw.file_manifest` are provenance tables. They contain source-vintage/raw-manifest rows, not copies of provider payloads. The general configuration has 37 enabled source IDs. Version A defines nine source IDs and shares `freddie_mac_pmms` with the general configuration, producing 45 unique source IDs across the repository. Census BPS and HUD/USPS store multiple manifest rows per source because each archived response or file remains an immutable vintage.

The `clean` schema is rebuilt from active configured sources only. It contains one table per active source ID, except `redfin_zip_monthly`, whose raw national file is filtered into `clean.redfin_zip_monthly`. It also exposes the union compatibility views `clean.fhfa_hpi` and `clean.census_hvs_rental_vacancy`; these views do not create new observations or change source-specific tables.

The `analytics` schema contains:

- `analytics.fact_housing_observation`
- `analytics.county_monthly_panel`
- `analytics.zip_monthly_panel`
- `analytics.state_quarterly_panel`
- `analytics.metro_monthly_controls`
- `analytics.metro_quarterly_controls`
- `analytics.county_annual_controls`
- `analytics.county_quarterly_controls`
- `analytics.homeowner_market_monthly`
- `analytics.renter_market_monthly`
- `analytics.overall_market_monthly`
- `analytics.macro_controls_monthly`

The `quality` schema contains:

- `quality.qa_summary`
- `quality.qa_failures`
- `quality.raw_repository_completeness`

## Derived features

County monthly derived fields include:

- one-month and twelve-month ZHVI/ZORI growth;
- price-to-rent ratio using seasonally adjusted ZORI;
- inventory-to-sales and sales-to-inventory ratios;
- down-payment scenarios at 3.5%, 10%, and 20%;
- closing-cost scenarios at 2%, 3%, and 5%;
- principal-and-interest mortgage-payment scenarios using Freddie Mac monthly-average 30-year rates;
- mortgage-payment-to-rent ratios; and
- market-tightness index components.

Mortgage-payment scenarios exclude taxes, insurance, HOA fees, maintenance, PMI, and closing-cost financing. They are affordability scenarios, not observed borrower-level payments.

## QA and coverage

Run validation:

```powershell
python run_pipeline.py --stage validate
```

The current validation run completed with 0 structural errors and 3 warning checks. Structural QA failures stop validation. Plausible economic outliers and expected source gaps are warnings for review.

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

- Census BPS county and state monthly archives are loaded from January 2000 through May 2026. The period report records later attempted archive periods separately when they are not published or cannot be retrieved.
- Zillow ZHVI runs through May 2026 in the current build.
- Active Zillow ZORI SA runs from January 2015 through May 2026.
- Redfin county monthly data runs from January 2012 through May 2026.
- Redfin ZIP rolling-three-month data runs from June 2019 through May 2026.
- BLS LAUS runs from January 1990 through May 2026.
- Freddie Mac PMMS runs from April 1971 through June 2026.
- ACS table-based variables currently use the 2024 ACS 5-year detailed-table files. The rental-availability rate uses matched B25003/B25004 components and does not infer earlier vintages.
- HVS rental-vacancy series are state/metro quarterly controls, not LA County observations.
- HUD/USPS runs quarterly from 2008 Q1 through 2026 Q1 for all 58 California counties, 2,494 Los Angeles County standardized 2020 tracts, and 477 modeled Los Angeles County postal-ZIP county-parts. Detailed values are licensed and restricted; the series is not true rental-listing inventory.
- Eviction Lab county eviction estimates run from 2000 through 2018 for Los Angeles County. This is annual modeled coverage; ETS/high-frequency LA coverage is not available in the integrated source.

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
- Rental inventory is not implemented because no verified official rental-listing inventory source is integrated; ZORI is not inventory.
- The active HUD/USPS all-residential vacancy proxy does not satisfy true rental-listing inventory. Its detailed observations are licensed/restricted, its modeled ZIP geography is a Los Angeles County portion of a postal ZIP rather than a whole ZIP or Census ZCTA, and it remains quarterly rather than monthly.
- The B25106 mortgage debt/equity scenario remains only for reproducibility; use Version A `_benchmark` variables as the current authoritative finance standard.
- Eviction trends are implemented only as annual Eviction Lab modeled county estimates for 2000-2018; they should not be interpreted as high-frequency ETS or direct LA court filing data.
- Permits are not starts, completions, or units under construction.
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
