# Los Angeles Housing Public-Data Database

This repository builds a reproducible, source-vintaged housing database for Los Angeles County research. It downloads or imports public source files, preserves immutable raw vintages, normalizes observations to Parquet, builds county and postal-ZIP analytical panels, materializes DuckDB tables, and publishes QA, coverage, and CSV exports.

The database is a static snapshot. It is not continuously connected to any provider. New source data enter the analytical tables only when the relevant pipeline stages are run.

## What the project builds

Primary outputs:

- `database/housing.duckdb`
- `data/final/fact_housing_observation.parquet`
- `data/final/county_monthly_panel.parquet`
- `data/final/zip_monthly_panel.parquet`
- `data/final/state_quarterly_panel.parquet`
- `output/exports/la_county_monthly_panel.csv`
- `output/exports/la_zip_monthly_panel.csv`
- `output/qa/qa_summary.csv`
- `output/qa/qa_failures.csv`
- coverage reports under `output/coverage/`
- source, variable, transformation, geography, and raw-vintage metadata under `metadata/`

`analytics.state_quarterly_panel` is currently a typed empty Phase 2 table. The repository does not fabricate unimplemented quarterly observations.

## Implemented data sources

| Source ID | Provider and product | Geography | Source frequency | Acquisition |
|---|---|---|---|---|
| `zillow_county_zhvi` | Zillow middle-tier all-homes ZHVI, smoothed and seasonally adjusted | County | Monthly | Official Zillow Research CSV |
| `zillow_zip_zhvi` | Zillow middle-tier all-homes ZHVI, smoothed and seasonally adjusted | Postal ZIP | Monthly | Official Zillow Research CSV |
| `zillow_county_zori_smoothed_sa` | Zillow ZORI, all homes plus multifamily, smoothed and seasonally adjusted | County | Monthly | Official Zillow Research CSV |
| `zillow_zip_zori_smoothed_sa` | Zillow ZORI, all homes plus multifamily, smoothed and seasonally adjusted | Postal ZIP | Monthly | Official Zillow Research CSV |
| `redfin_county_monthly` | Redfin Housing Market Tracker | County | Monthly | Official Redfin data object |
| `redfin_zip_monthly` | Redfin Housing Market Tracker Key Metrics | Postal ZIP | Rolling three months, dated monthly | Manual official Data Center CSV import |
| `bls_laus_la_county` | BLS Local Area Unemployment Statistics | Los Angeles County | Monthly | Official BLS API |
| `freddie_mac_pmms` | Freddie Mac Primary Mortgage Market Survey, 30-year fixed rate | United States | Weekly | Official Freddie Mac CSV |
| `census_bps_county` | Census Building Permits Survey archive | Los Angeles County | Monthly | Official Census ASCII archive |
| `census_bps_state` | Census Building Permits Survey archive | California | Monthly | Official Census ASCII archive |

Exact source pages, endpoints, formats, enabled status, methodology notes, and revision notes are defined in `config/sources.yaml`. Variable definitions and units are in `config/variables.yaml`.

Important product semantics:

- Analytical `zori` means Zillow ZORI (Smoothed, Seasonally Adjusted), All Homes Plus Multifamily. No non-seasonally-adjusted ZORI is active.
- BLS LAUS county values are not seasonally adjusted.
- Redfin ZIP Key Metrics are overlapping rolling-three-month windows, not independent single-month observations.
- Redfin ZIP `ACTIVE LISTINGS` remains `active_listings`; it is not relabeled as inventory.
- Building permits are authorizations, not housing starts or completions.
- Freddie Mac mortgage rates are national, not Los Angeles-specific.
- Provider postal ZIPs are not Census ZCTAs.

## Installation

Python 3.11 or newer is required.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Commands below use `python` for readability and assume the virtual environment is activated. On Windows systems where PowerShell script activation is disabled, replace `python` with:

```powershell
.\.venv\Scripts\python.exe
```

Inspect the installed CLI:

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

BLS currently publishes some unavailable periods as a nonnumeric value such as `"-"` with an explanatory footnote. The downloader preserves that response in raw JSON; the cleaner does not turn it into a numeric observation.

`--force-download` is what requests and archives a new API response even if a BLS file was already retrieved that day. Without it, the downloader may reuse the current UTC day's archived response. `--offline` makes no API request and reuses local raw files.

The download command refreshes provenance immediately, but it does not clean the new response or rebuild analytical tables. To propagate the newest archived BLS vintage through Parquet, DuckDB, QA, and exports, run:

```powershell
python run_pipeline.py --stage all --offline
```

To limit the API request years, use inclusive dates:

```powershell
python run_pipeline.py --stage download --source bls_laus_la_county --start-date 2015-01-01 --end-date 2026-12-31 --force-download
```

An explicitly selected BLS download exits nonzero if HTTP transport fails, JSON is invalid, BLS reports an API error, a configured series is missing, or a requested window contains no numeric monthly observations.

## CLI and pipeline stages

Available stages:

| Stage | Behavior |
|---|---|
| `discover` | Writes `metadata/data_catalog.csv`; performs endpoint HEAD checks unless `--offline` is supplied. |
| `download` | Acquires immutable raw vintages and updates the raw manifest. Successful downloads also refresh DuckDB source-registry tables when the database exists. |
| `clean` | Selects a local raw vintage and runs the source-specific cleaner. |
| `harmonize` | Unions clean observations, selects latest values, builds the fact table and panels, calculates features, and writes final Parquet. |
| `build-database` | Materializes metadata, raw-manifest, clean, analytics, and empty quality tables in DuckDB. |
| `validate` | Runs structural and warning-level QA, writes reports, and updates DuckDB quality tables. |
| `export` | Writes county and ZIP modeling CSVs from final Parquet. |
| `all` | Runs `discover`, `download`, `clean`, `harmonize`, `build-database`, `validate`, and `export` in that order. |

Examples:

```powershell
python run_pipeline.py --stage discover --offline
python run_pipeline.py --stage clean --source bls_laus_la_county
python run_pipeline.py --stage harmonize
python run_pipeline.py --stage build-database
python run_pipeline.py --stage validate
python run_pipeline.py --stage export
```

Supported options:

- `--source SOURCE_ID`: filters `discover`, `download`, and `clean`. With `--stage all`, only those source-specific stages are filtered; downstream harmonization still rebuilds from all local clean files.
- `--start-date` / `--end-date`: constrain BLS and BPS acquisition and can filter harmonized analytical dates.
- `--vintage YYYY-MM-DD`: selects a clean input by retrieval-date vintage. BPS selects the latest archived file at or before the vintage for each archive month.
- `--force-download`: bypasses same-day raw reuse. For BPS this can redownload the requested archive range, so use it deliberately.
- `--local-file PATH`: supported for the manual `redfin_zip_monthly` import and requires `--source redfin_zip_monthly`.
- `--offline`: disables discovery network checks and skips downloads.
- `--log-level`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

Pipeline run status and errors are appended to `metadata/pipeline_run_log.csv`.

## Rebuild workflows

Rebuild everything from already archived raw data without network access:

```powershell
python run_pipeline.py --stage all --offline
```

Refresh automatically downloadable sources, then rebuild:

```powershell
python run_pipeline.py --stage all --force-download
```

The large Redfin ZIP Key Metrics product remains manual and opt-in. A full online run logs that manual source while continuing with other enabled sources and then reuses the latest locally imported Redfin ZIP file.

Import a new official Redfin ZIP Key Metrics file:

```powershell
python run_pipeline.py --stage download --source redfin_zip_monthly --local-file "PATH_TO_OFFICIAL_REDFIN_CSV"
python run_pipeline.py --stage all --offline
```

The import copies the national CSV unchanged to immutable raw storage. Cleaning uses DuckDB projection and filtering, joins against the versioned LA County ZIP reference, saves only the LA subset to `data/clean/redfin_zip_monthly_la_county.parquet`, and caches that result by raw checksum.

## Storage and provenance

| Location | Contents |
|---|---|
| `data/raw/<provider>/` | Immutable provider payloads. Filenames include source ID, UTC retrieval timestamp, and checksum prefix. |
| `data/archive/deprecated_zori_nsa/` | Deprecated NSA ZORI files and their archive manifest; excluded from active ingestion. |
| `data/interim/` | Reusable intermediate observations, currently the long-form Redfin ZIP extract. |
| `data/clean/` | Latest selected source-specific normalized Parquet and geography tables. |
| `data/final/` | Long fact table and analytical panels in Parquet. |
| `database/housing.duckdb` | Integrated query database. |
| `metadata/raw_file_manifest.csv` | Raw URL/API endpoint, retrieval, filename, byte size, checksum, source vintage, and source-specific request metadata. |
| `metadata/data_catalog.csv` | Configured source inventory and discovery results. |
| `metadata/data_dictionary.csv` | Variable definitions and methods. |
| `metadata/transformation_log.csv` | Executed transformations and output paths. |
| `metadata/pipeline_run_log.csv` | Stage requests, filters, status, and errors. |
| `output/coverage/` | Source, time, ZIP, BPS archive, and Redfin ZIP coverage reports. |
| `output/qa/` | QA summary and bounded failure samples. |
| `output/exports/` | Modeling-friendly county and ZIP CSVs. |

The operational `source_vintage` is the UTC retrieval date. Provider observation dates and period boundaries remain separate. Raw payloads are never overwritten. Clean and analytical tables use the latest selected local vintage, while raw manifests retain earlier acquisitions.

## DuckDB schemas

`database/housing.duckdb` uses five schemas:

### `metadata`

- `metadata.source_registry`
- `metadata.variable_dictionary`
- `metadata.geography`

### `raw`

- `raw.file_manifest`

This table stores provenance rows, not copies of provider payloads.

### `clean`

- `clean.zillow_county_zhvi`
- `clean.zillow_zip_zhvi`
- `clean.zillow_county_zori_smoothed_sa`
- `clean.zillow_zip_zori_smoothed_sa`
- `clean.redfin_county_monthly`
- `clean.redfin_zip_monthly`
- `clean.bls_laus_la_county`
- `clean.freddie_mac_pmms`
- `clean.census_bps_county`
- `clean.census_bps_state`

The national Redfin ZIP CSV is not loaded into DuckDB. `clean.redfin_zip_monthly` contains only the verified Los Angeles County subset.

### `analytics`

- `analytics.fact_housing_observation`
- `analytics.county_monthly_panel`
- `analytics.zip_monthly_panel`
- `analytics.state_quarterly_panel`

The fact observation contract retains observation/source dates, geography IDs, state/county FIPS, variable ID, value, reported value where available, units, provider, native/final frequency, source vintage, retrieval timestamp, source filename, transformation ID, adjustment/imputation/interpolation flags, and quality flags.

`analytics.county_monthly_panel` is one row per Los Angeles County month and includes source levels plus documented growth, affordability, mortgage-payment, and market-tightness features.

`analytics.zip_monthly_panel` keeps the Zillow ZIP-month spine, seasonally adjusted `zori`, ZHVI, Redfin rolling-three-month Key Metrics, provider and calculated YoY fields, methodology fields, and availability flags. Redfin is left-joined and cannot remove Zillow ZIP-months.

### `quality`

- `quality.qa_summary`
- `quality.qa_failures`

Structural error checks stop validation. Warning checks preserve plausible source gaps, revisions, and economic outliers for review.

## Dates, geography, and missing values

- Monthly analytical dates are calendar month ends.
- Source dates and source period boundaries are preserved.
- California FIPS is `"06"` and Los Angeles County FIPS is `"06037"`.
- Provider ZIPs use `geo_id="ZIP_#####"` and `postal_zip="#####"`.
- ZIPs are not silently treated as ZCTAs.
- Missing values remain null.
- The pipeline does not interpolate housing or labor data.
- ZIP values are not filled from county values.
- County values are not distributed to ZIPs.
- Extreme but structurally valid values are retained and reported as warnings.

Census BPS county methodology changes in January 2022. Earlier county values are flagged as sampled-jurisdiction tallies; January 2022 onward uses the full current universe with imputation flags. Reported units and estimated totals are preserved separately.

## Derived analytical variables

County features include:

- one- and twelve-month fractional growth for ZHVI, SA ZORI, and active inventory;
- price-to-rent: `ZHVI / (12 * ZORI)`;
- inventory-to-sales;
- principal-and-interest mortgage payments for configured 10% and 20% down-payment scenarios;
- mortgage-payment-to-rent ratios; and
- a market-tightness index based on available oriented z-score components.

Mortgage estimates use the national Freddie Mac monthly-average 30-year rate and Redfin county median sale price. They exclude property tax, insurance, HOA dues, maintenance, and mortgage insurance.

Redfin ZIP provider YoY values are retained in `_provider` columns using percentage-point units. Exact twelve-month changes calculated from rolling-window levels use `_yoy_calculated` columns. The project does not calculate Redfin ZIP MoM changes because adjacent source windows overlap.

## QA and coverage

Run validation:

```powershell
python run_pipeline.py --stage validate
```

QA covers duplicate keys, missing identifiers, geography formats, prohibited negative levels, BPS fixed-width FIPS, BLS/fact frequency gaps, ZORI product identity, clean-to-panel equality, Redfin ZIP rolling-window semantics and county eligibility, provider/calculated YoY differences, stale endpoints, economic movement warnings, county/ZIP plausibility, and Freddie Mac monthly aggregation.

Core reports:

- `output/qa/qa_summary.csv`
- `output/qa/qa_failures.csv`
- `output/coverage/source_coverage.csv`
- `output/coverage/time_coverage.csv`
- `output/coverage/zip_coverage.csv`
- `output/coverage/bps_archive_download.csv`
- `output/coverage/bps_archive_coverage.csv`
- `output/coverage/bps_archive_period_report.csv`
- `output/coverage/redfin_zip_source_audit.csv`
- `output/coverage/redfin_zip_source_null_counts.csv`
- `output/coverage/redfin_zip_coverage.csv`
- `output/coverage/redfin_zip_unmatched.csv`
- `output/coverage/redfin_zip_yoy_comparison.csv`

Run tests:

```powershell
python -m pytest -q
```

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

Inspect the newest BLS raw vintage and request metadata:

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

Inspect the latest BLS analytical observations:

```sql
SELECT observation_date, variable_id, value, source_vintage
FROM analytics.fact_housing_observation
WHERE geo_id = 'COUNTY_06037'
  AND variable_id IN (
      'unemployment_rate',
      'unemployment',
      'employment',
      'labor_force'
  )
ORDER BY observation_date DESC, variable_id
LIMIT 24;
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

- Provider histories can be revised. Raw vintages preserve what was retrieved, but the active clean and analytical tables select one latest local vintage.
- BLS may publish unavailable months as nonnumeric values with footnotes. These remain in raw JSON and do not become fabricated numeric observations.
- Redfin ZIP Key Metrics has no verified stable direct endpoint for the selected export and requires a manual official download.
- Redfin ZIP observations are overlapping three-month windows. They must not be summed across adjacent period ends or interpreted as single-month flows.
- Redfin's provider `MEDIAN DAYS ON MARKET YOY (%)` values can differ materially from percentage changes calculated from level values; both are retained and compared.
- The geography table records provider postal ZIP assignments but does not invent Census ZCTA equivalence or fractional county allocation.
- The California quarterly panel is intentionally empty pending implementation of official Phase 2 sources.
- Row-level revision reports across archived provider vintages are not yet generated.

## Adding or changing a source

1. Use only an official public download, documented API, or provider-published file.
2. Add complete source metadata to `config/sources.yaml`.
3. Add methodologically distinct variable IDs to `config/variables.yaml`.
4. Preserve immutable raw files and checksums.
5. Implement source-specific schema validation and transformation logic.
6. Preserve geography identifiers as strings.
7. Update harmonization, QA, coverage, tests, and this README.
8. Run the full offline rebuild, tests, and validation before publishing.
