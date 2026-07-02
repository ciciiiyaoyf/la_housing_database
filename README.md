# Los Angeles Housing Public-Data Database

This repository builds a reproducible Los Angeles housing research database from public, source-vintaged data. The active scope is intentionally narrow: it keeps the variables marked as currently available in the project variable sheet, plus the macro controls required for the weekly-task forecasting workflow.

The database is a static snapshot. It is not continuously connected to Zillow, Redfin, BLS, Census, FHFA, BEA, Freddie Mac, or any other provider. New observations enter the database only after the pipeline downloads or imports a raw source vintage and rebuilds the clean, final, DuckDB, QA, catalog, and export layers.

## Active outputs

Primary generated files:

- `database/housing.duckdb`
- `data/final/fact_housing_observation.parquet`
- `data/final/county_monthly_panel.parquet`
- `data/final/zip_monthly_panel.parquet`
- `data/final/metro_monthly_controls.parquet`
- `data/final/state_quarterly_panel.parquet`
- `data/final/metro_quarterly_panel.parquet`
- `data/final/county_annual_controls.parquet`
- `output/exports/la_county_monthly_panel.csv`
- `output/exports/la_zip_monthly_panel.csv`
- `metadata/data_catalog.csv`
- `metadata/data_catalog.xlsx`
- `metadata/data_gap_analysis.csv`
- `metadata/data_gap_analysis.xlsx`
- `output/qa/qa_summary.csv`
- `output/qa/raw_repository_completeness.csv`

The main modeling tables are:

- `analytics.county_monthly_panel`
- `analytics.zip_monthly_panel`
- `analytics.fact_housing_observation`
- `analytics.homeowner_market_monthly`
- `analytics.renter_market_monthly`
- `analytics.overall_market_monthly`
- `analytics.macro_controls_monthly`

## Active variable scope

The active database preserves the currently needed available variables:

- Home values and prices: Zillow ZHVI, FHFA HPI controls, Redfin median sale price.
- For-sale supply and activity: active inventory, active listings, months of supply, new listings, homes sold.
- Market condition indicators: days on market, price-drop share, inventory-to-sales, sales-to-inventory, market tightness.
- Rent: Zillow ZORI smoothed seasonally adjusted, rent growth, price-to-rent, payment-to-rent.
- Supply proxies: Census BPS 1-unit permit authorizations and 5-plus-unit permit authorizations.
- Mortgage affordability: Freddie Mac 30-year mortgage rate and principal-and-interest payment scenarios.
- Macro controls: BLS LAUS, BLS CES, BLS CPI, BEA personal income, HVS rental vacancy.

Not active at this stage:

- ACS table-level variables and ZCTA structural panels.
- Redfin pending sales and sale-to-list ratio.
- BPS total permits and 2-to-4-unit permits in the analytical product.
- Income-denominator affordability ratios, such as price-to-income and rent-to-income.
- Rental listing inventory, because no verified official public source is implemented.

Raw files from earlier experiments may still exist locally, but the active DuckDB, catalog, dictionary, fact table, and final panels are filtered to the active scope above.

## Active data sources

| Source ID | Product | Geography | Frequency |
|---|---|---|---|
| `zillow_county_zhvi` | Zillow ZHVI, all homes, smoothed SA | Los Angeles County | Monthly |
| `zillow_zip_zhvi` | Zillow ZHVI, all homes, smoothed SA | Postal ZIP | Monthly |
| `zillow_county_zori_smoothed_sa` | Zillow ZORI, all homes plus multifamily, smoothed SA | Los Angeles County | Monthly |
| `zillow_zip_zori_smoothed_sa` | Zillow ZORI, all homes plus multifamily, smoothed SA | Postal ZIP | Monthly |
| `redfin_county_monthly` | Redfin county for-sale market metrics | Los Angeles County | Monthly |
| `redfin_zip_monthly` | Redfin ZIP Key Metrics | Postal ZIP | Rolling 3 months |
| `bls_laus_la_county` | BLS LAUS labor force controls | Los Angeles County | Monthly |
| `bls_ces_la_metro` | BLS CES total nonfarm payroll employment | LA metro | Monthly |
| `bls_cpi_la_metro` | BLS CPI-U All Items | LA published CPI area | Monthly |
| `freddie_mac_pmms` | Freddie Mac 30-year fixed mortgage rate | United States | Weekly/monthly |
| `census_bps_county` | Census BPS permit authorizations | Los Angeles County | Monthly |
| `census_bps_state` | Census BPS permit authorizations | California | Monthly |
| `fhfa_hpi_state_purchase_only` | FHFA purchase-only HPI | California | Quarterly |
| `fhfa_hpi_metro_purchase_only` | FHFA purchase-only HPI | LA metropolitan division | Quarterly |
| `fhfa_hpi_county_annual` | FHFA all-transactions county HPI | Los Angeles County | Annual |
| `bea_state_personal_income` | BEA personal income | California | Quarterly |
| `bea_county_personal_income` | BEA personal income | Los Angeles County | Annual |
| `census_hvs_state_rental_vacancy` | Census HVS rental vacancy | California | Quarterly |
| `census_hvs_metro_rental_vacancy_*` | Census HVS rental vacancy | Published LA metro | Quarterly |

Exact URLs, official source pages, formats, key variables, table destinations, and revision notes are in `config/sources.yaml`. Variable definitions and units are in `config/variables.yaml`.

## Installation

Python 3.11 or newer is required.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Commands below use `python` for readability. If the virtual environment is not activated, use:

```powershell
.\.venv\Scripts\python.exe
```

Inspect the CLI:

```powershell
python run_pipeline.py --help
```

## Refreshing BLS LAUS data

The pipeline retrieves LAUS data from the official BLS Public Data API at:

`https://api.bls.gov/publicAPI/v2/timeseries/data/`

An API retrieval is still a data download. DuckDB is not continuously connected to BLS, and opening `database/housing.duckdb` does not contact BLS or update any observations. The database contains a static, source-vintaged snapshot. BLS can revise historical LAUS observations, so each API response is archived as a separate raw vintage.

The configured Los Angeles County series are:

- `LAUCN060370000000003` - unemployment rate
- `LAUCN060370000000004` - unemployment
- `LAUCN060370000000005` - employment
- `LAUCN060370000000006` - labor force

Close open writable DuckDB sessions before refreshing source metadata, then request a fresh API response with:

```powershell
python run_pipeline.py --stage download --source bls_laus_la_county --force-download
```

This command was live-tested against the official API on July 2, 2026. It:

1. posts the four configured series IDs to the official endpoint;
2. requests 1990 through the current year in ten-year chunks;
3. validates the API status, JSON structure, returned series IDs, and numeric monthly observations;
4. preserves published nonnumeric missing values and their footnotes in the raw response;
5. writes a new immutable JSON file under `data/raw/bls/`;
6. leaves all earlier BLS raw files untouched;
7. appends the endpoint, request series, request years, retrieval timestamp, source vintage, filename, byte size, SHA-256 checksum, latest numeric observation, and observation count to `metadata/raw_file_manifest.csv`; and
8. refreshes `metadata.source_registry` and `raw.file_manifest` in an existing DuckDB database.

The verified run created:

- raw file: `bls_laus_la_county_20260702T044223Z_be71938705a3.json`
- byte size: `476532`
- SHA-256: `be71938705a3a87723eac1fe5d54654f5a9cb8f39b7ffc6e04fd5b34bd6dbbdd`
- request range: 1990-2026
- latest numeric observation returned: May 2026
- numeric observations returned across all four series: 1,744

`--force-download` requests and archives a new API response even when a same-day BLS raw file already exists. `--offline` makes no API request and reuses existing local raw files.

After a fresh BLS download, rebuild the database from local raw files:

```powershell
python run_pipeline.py --stage all --offline
```

## Pipeline stages

| Stage | What it does |
|---|---|
| `discover` | Writes `metadata/source_discovery.csv`; skips endpoint checks with `--offline`. |
| `download` | Downloads or imports immutable raw source vintages and updates the raw manifest. |
| `clean` | Converts selected raw vintages into source-specific clean Parquet. |
| `harmonize` | Builds the filtered fact table, panels, and derived features. |
| `build-database` | Recreates generated DuckDB schemas and loads metadata, raw manifest, clean, analytics, and quality tables. |
| `catalog` | Builds the data catalog, gap analysis, and raw-repository completeness report. |
| `validate` | Runs structural QA and warning checks. |
| `export` | Writes modeling-friendly CSV exports. |
| `all` | Runs every stage in order. |

Common rebuild:

```powershell
python run_pipeline.py --stage all --offline
```

Run QA and tests:

```powershell
python run_pipeline.py --stage validate
python -m pytest -q
```

## Refresh commands for active sources

Each command creates a new immutable raw payload and manifest row.

```powershell
python run_pipeline.py --stage download --source zillow_county_zhvi --force-download
python run_pipeline.py --stage download --source zillow_zip_zhvi --force-download
python run_pipeline.py --stage download --source zillow_county_zori_smoothed_sa --force-download
python run_pipeline.py --stage download --source zillow_zip_zori_smoothed_sa --force-download
python run_pipeline.py --stage download --source redfin_county_monthly --force-download
python run_pipeline.py --stage download --source bls_laus_la_county --force-download
python run_pipeline.py --stage download --source bls_ces_la_metro --force-download
python run_pipeline.py --stage download --source bls_cpi_la_metro --force-download
python run_pipeline.py --stage download --source freddie_mac_pmms --force-download
python run_pipeline.py --stage download --source census_bps_county --force-download
python run_pipeline.py --stage download --source census_bps_state --force-download
python run_pipeline.py --stage download --source fhfa_hpi_state_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_metro_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_county_annual --force-download
python run_pipeline.py --stage download --source bea_state_personal_income --force-download
python run_pipeline.py --stage download --source bea_county_personal_income --force-download
python run_pipeline.py --stage download --source census_hvs_state_rental_vacancy --force-download
python run_pipeline.py --stage download --source census_hvs_metro_rental_vacancy_2005_2014 --force-download
python run_pipeline.py --stage download --source census_hvs_metro_rental_vacancy_2015_2025 --force-download
python run_pipeline.py --stage download --source census_hvs_metro_rental_vacancy_2026 --force-download
```

Redfin ZIP data currently require a manual official Data Center CSV import:

```powershell
python run_pipeline.py --stage download --source redfin_zip_monthly --local-file "PATH_TO_OFFICIAL_REDFIN_CSV"
python run_pipeline.py --stage all --offline
```

## Catalog and data dictionary

Regenerate catalog outputs:

```powershell
python run_pipeline.py --stage catalog
```

The catalog includes active sources plus clearly labeled unavailable/deferred rows. It should be the first place to check source pages, exact download URLs, retrieval dates, filenames, checksums, source vintages, key variables, and destination tables.

The Excel workbook contains:

- Data Catalog
- Variable Dictionary
- Raw File Inventory
- Geography-Frequency Matrix
- Coverage Summary
- Unavailable and Deferred
- Update Commands
- Gap Analysis

## DuckDB schemas

`database/housing.duckdb` uses:

- `metadata`: source registry, variable dictionary, geography, catalog, gap analysis.
- `raw`: raw file manifest metadata, not raw payload copies.
- `clean`: purpose-named clean source tables such as `clean.zillow_county_home_values`, `clean.redfin_county_for_sale_market`, `clean.redfin_zip_for_sale_market`, `clean.census_bps_county_permits`, and `clean.freddie_mac_mortgage_rates`.
- `analytics`: fact table, panels, and market views.
- `quality`: QA summary, QA failures, raw completeness.

Current analytics tables/views:

- `analytics.fact_housing_observation`
- `analytics.county_monthly_panel`
- `analytics.zip_monthly_panel`
- `analytics.metro_monthly_controls`
- `analytics.state_quarterly_panel`
- `analytics.metro_quarterly_panel`
- `analytics.county_annual_controls`
- `analytics.homeowner_market_monthly`
- `analytics.renter_market_monthly`
- `analytics.overall_market_monthly`
- `analytics.macro_controls_monthly`

## Important semantics

- Analytical `zori` is Zillow ZORI smoothed seasonally adjusted. Non-seasonally-adjusted ZORI is not active.
- Redfin ZIP observations are overlapping rolling-three-month windows. Do not sum adjacent ZIP rows as monthly flows.
- Redfin ZIP `ACTIVE LISTINGS` remains `active_listings`; it is not rental inventory.
- BPS variables are permit authorizations, not starts or completions.
- Freddie Mac mortgage rates are national controls, not Los Angeles rates.
- BLS CES is metro establishment employment; BLS LAUS is county residence-based labor data.
- HVS rental vacancy is state or published metro geography, not Los Angeles County.
- Quarterly and annual sources remain at native frequency and are not expanded into monthly observations.
- Missing values remain null; the pipeline does not interpolate housing or labor series.

## Query examples

County monthly panel:

```python
import duckdb

con = duckdb.connect("database/housing.duckdb", read_only=True)
county = con.sql("""
    SELECT date, zhvi, zori, unemployment_rate, permits_1_unit, permits_5_plus_units
    FROM analytics.county_monthly_panel
    WHERE date >= DATE '2020-01-01'
    ORDER BY date
""").df()
```

Check active tables:

```sql
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema IN ('clean', 'analytics')
ORDER BY table_schema, table_name;
```

Inspect the latest BLS raw vintage:

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

## QA and coverage

QA checks include duplicate fact keys, duplicate panel keys, fixed-width FIPS and ZIP strings, ZORI seasonal-adjustment identity, BPS FIPS preservation, source-to-panel equality, Redfin ZIP rolling-window semantics, metro/county geography separation, native annual/quarterly frequency preservation, catalog structure, raw repository completeness, and mortgage-rate monthly aggregation.

Core reports:

- `output/qa/qa_summary.csv`
- `output/qa/qa_failures.csv`
- `output/qa/raw_repository_completeness.csv`
- `output/coverage/source_coverage.csv`
- `output/coverage/time_coverage.csv`
- `output/coverage/zip_coverage.csv`
- `output/coverage/bps_archive_coverage.csv`
- `output/coverage/redfin_zip_coverage.csv`

## Known limitations

- Provider histories can be revised; raw vintages preserve retrieval snapshots, while active tables use the latest selected local vintage.
- Redfin ZIP Key Metrics has no verified stable direct endpoint for the selected export and requires a manual official download.
- Rental listing inventory is not implemented. ZORI is a rent index, not an inventory measure.
- Income-based affordability ratios are deferred until the denominator and frequency treatment are explicitly chosen.
- HVS estimates have sampling uncertainty and published margins of error.
- FHFA products differ from Zillow products in geography, method, and frequency.
- CPI uses the current LA-area published CPI series and preserves historical publication gaps.

## Adding or changing a source

1. Use only an official public download, documented API, or provider-published file.
2. Add source metadata to `config/sources.yaml`.
3. Add methodologically distinct variable IDs to `config/variables.yaml`.
4. Preserve immutable raw files and checksums.
5. Implement source-specific schema validation and cleaning.
6. Keep geography identifiers as strings.
7. Update harmonization, QA, catalog, tests, and this README.
8. Rebuild offline, run QA/tests, and inspect the catalog before committing.
