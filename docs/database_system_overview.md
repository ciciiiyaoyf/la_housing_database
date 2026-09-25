# Los Angeles Housing Database: Outsider Overview

Inventory update: **2026-09-15**. Current contract, fact, and analytical-product counts include the macro additions. Explicitly labeled September 5 pre-macro source breakdowns and storage statistics are preserved historical snapshots, not current totals.

This repository is a source-vintaged research database for Los Angeles housing,
affordability, rental conditions, construction, housing finance, population,
income, employment, and related controls. It combines official or
provider-published inputs, preserves immutable raw vintages, and materializes
clean, analytical, quality-assurance, and export layers. It is a **static local
snapshot**, not a live connection to its providers; new observations enter only
when the refresh workflow runs successfully.

## One-minute orientation

- There is **one active physical analytical database**:
  `database/housing.duckdb`.
- The maintained provider inventory contains **69 unique source IDs**. These
  are source contracts, not 69 separate database files.
- The current catalog contains **75 products**: 69 provider
  sources plus six internal Version B/C observation, lineage, and completion products.
- The variable contract contains **558 versioned variable IDs**: 375 general,
  67 Version A, 58 Version B, and 58 Version C.
- The current database exposes **109 relations**: 66 `clean` tables, 22 `analytics`
  tables, 11 `analytics` views, five `metadata` tables, three `quality` tables,
  and two `raw` tables.
- The September 15 fact table contains **2,670,497 rows**, spanning
  1914-12-31 through 2026-09-14. Specialized panels, scenarios, and finance layers
  sit alongside it rather than being forced into one universal grain.
- The current inventory identifies **277 constructed or estimate IDs**. This
  includes scenarios and diagnostics, not 277 independent statistical models.
  Concepts represented in the long homebuying table count as materialized even
  when they are not standalone wide-table columns.
- Current analytical products comprise **22 base tables, 11 views, and 26 final Parquet files**. Across the full contract, **514 of 558 IDs have at least one non-null value** and 44 remain explicit-null finance targets.

The most useful machine-readable orientation files are the
[inventory summary](../metadata/system_inventory_summary.json),
[source inventory](../metadata/system_source_inventory.csv),
[variable inventory](../metadata/system_variable_inventory.csv),
[analytical dataset inventory](../metadata/analytical_dataset_inventory.csv),
[estimate inventory](../metadata/estimate_inventory.csv), and
[storage inventory](../metadata/system_storage_inventory.csv).
For a filterable, outsider-facing presentation of the earlier 2026-08-24
snapshot, use the
[database overview workbook](../outputs/database_system_overview_20260824/LA_Housing_Database_Outsider_Overview.xlsx);
the generated CSV/JSON inventories linked above are authoritative for the
current counts.

## What each count means

| Count | Value and snapshot | Definition |
|---|---:|---|
| Active physical database | 1 | The maintained DuckDB file. The `housing_before_redfin_zip.duckdb` file is a legacy snapshot and is not an active database. |
| Provider source IDs | 69 | Unique configured acquisition/source contracts after deduplicating the Freddie Mac PMMS source shared by the general and Version A layers. |
| Catalog products | 75 | Sixty-nine provider sources plus six internal B/C observation, lineage, and completion products. |
| Configured variable IDs | 558 | Versioned analytical identifiers, not source columns or database tables. |
| Analytics base tables | 22 | Canonical analytical materializations in DuckDB, including the geography-preserving macro monthly panel. |
| Analytics views | 11 | Convenience views over the base tables, including market-segment, LA/comparable-county, and latest-vintage finance views. |
| Raw archive files | 1,414 (September 5 pre-macro) | Immutable provider/API payload vintages; multiple files can belong to one source ID. |
| Final Parquet files | 26 | Canonical analytical files under `data/final/`, including partitioned B/C files; two additional JSON completion markers protect paired B/C observations and lineage. |
| Public/modeling CSV exports | 27 | Files under `output/exports/`; restricted HUD/USPS detail is excluded. |

These numbers answer different questions. A source ID is not a raw file, a raw
file is not a variable, and a variable is not necessarily a standalone database
column. Row counts include explicit-null rows when the schema intentionally
preserves an unavailable target.

## Where the data live

The database, raw-file manifest, and observation CSV exports are generated
locally and are not included in a Git checkout. The published variable
dictionary and linked reference workbooks remain available in the repository.

| Layer | Location | September 5 pre-macro inventory | Intended use |
|---|---|---:|---|
| SQL database | `database/housing.duckdb` | 1 active file, about 409 MiB | Local primary query surface. |
| Immutable source archive | [`data/raw/`](../data/raw/) | 1,414 files, about 3.45 GB | Provenance, reproducibility, and source-vintage audit. Normally query clean/final data instead. |
| Normalized source tables | [`data/clean/`](../data/clean/) | 96 Parquet files on disk | Source-level analysis. Active membership is defined by configuration and DuckDB, because superseded files can remain on disk. |
| Canonical analytical files | [`data/final/`](../data/final/) | 25 Parquet files plus two completion JSON files | Direct columnar access and database rebuilds. |
| Public/modeling exports | [`output/exports/`](../output/exports/) | 26 CSV files | Locally generated observation CSVs; the variable dictionary is published here. |
| Restricted licensed layer | `data/restricted/` | 85 files, about 1.43 GB | Access-controlled HUD/USPS raw and detailed derivatives; not in public exports or Git. |
| Human-readable source workbook | [`metadata/LA_Housing_Source_Tracker.xlsx`](../metadata/LA_Housing_Source_Tracker.xlsx) | 1 workbook | Source, coverage, gaps, provenance, and update-command overview. |

The exact checksums, retrieval timestamps, filenames, request metadata, and raw
paths are in the local raw-file manifest, `metadata/raw_file_manifest.csv`. The
[active catalog workbook](../metadata/data_catalog.xlsx) and
[source-discovery inventory](../metadata/source_discovery.csv) contain the full
official page and download/API URL for every maintained provider source.

## Provider and source inventory

The 69 source IDs comprise 61 enabled general sources (56 automatic and five
manual) and eight additional Version A-only inputs; Freddie Mac PMMS is shared
and counted once. Versions B and C are derived products and add **no new provider
source IDs**.

The provider, source-frequency, and geography-bundle tables below preserve the **September 5 pre-macro 60-source snapshot**. The new nine-source block comprises one CES source, one DOL claims source, one BEA GDP source, and six national financial-rate sources; the current contract is 69 sources.

| Provider grouping | Source IDs in September 5 pre-macro snapshot |
|---|---:|
| U.S. Census Bureau | 31 |
| Federal Housing Finance Agency | 5 |
| U.S. Bureau of Labor Statistics | 4 |
| Zillow Research | 4 |
| CDC WONDER / National Center for Health Statistics | 3 |
| Redfin Data Center | 2 |
| U.S. Bureau of Economic Analysis | 2 |
| Nine other provider labels, one source each | 9 |
| **Total** | **60** |

The nine one-source provider labels are California DIR, California DOI,
CFPB/FFIEC, Eviction Lab, Freddie Mac, HUD-USPS, IRS, Census/HUD AHS, and HUD PIT.

### Sources by native frequency

This table counts each source ID once using its exact configured native-frequency
label. It does not count analytical transformations to another frequency.

| Native-frequency contract | Source IDs |
|---|---:|
| Annual | 15 |
| Annual ACS five-year estimate | 14 |
| Monthly | 10 |
| Quarterly | 8 |
| Annual published-table product | 2 |
| ACS five-year benchmark input | 2 |
| Decennial crosswalk | 2 |
| Quarterly or published aggregate | 2 |
| Rolling three months | 1 |
| Weekly | 1 |
| Biennial survey year | 1 |
| Annual point-in-time | 1 |
| Monthly with a published annual average | 1 |
| **Total** | **60** |

### Sources by configured geography bundle

Multi-geography sources are counted under their exact bundle so the table sums to
60. Flattening the bundles into individual geography memberships would produce a
larger, non-additive count.

| Geography bundle | Source IDs |
|---|---:|
| County | 17 |
| County and Census ZCTA | 14 |
| State | 9 |
| Metro | 6 |
| Postal ZIP | 3 |
| Tract-to-PUMA crosswalk | 2 |
| Nine unique bundles, one source each | 9 |
| **Total** | **60** |

The nine unique bundles are national; Continuum of Care plus an explicit project
composite; county/2020 tract/postal-ZIP-county-part; California counties;
PUMA/California; loan record/county; United States or published-parameter
geography; California; and U.S.-city-average.

Important geography rule: a provider postal ZIP is not a Census ZCTA. Leading
zeros are preserved, and county, metro, state, CoC, tract, PUMA, postal-ZIP,
postal-ZIP-county-part, and ZCTA observations are never silently substituted for
one another.

## Variable inventory

| System layer | Variable IDs | Required suffix or role |
|---|---:|---|
| General | 375 | Observed variables, transformations, controls, diagnostics, and scenarios. |
| Version A | 67 | Every ID ends in `_benchmark`. |
| Version B | 58 | Every ID ends in `_monthly_hist`. |
| Version C | 58 | Every ID ends in `_monthly_rt`. |
| **Total** | **558** | Unique versioned IDs. |

In the **September 5 pre-macro snapshot**, the final/model-frequency contract contained 265
annual or native-benchmark IDs (226 annual plus 39 ACS five-year), 219 monthly
IDs, 35 quarterly IDs, one weekly ID, and one mixed quarterly/annual ID. This is
different from the native-frequency source inventory because a construction can
have a different, explicitly documented output frequency.

The full definitions, units, transformations, missing-value policies,
geographies, source frequencies, and observed/constructed/proxy flags are in the
[data dictionary](../metadata/data_dictionary.csv).

## Canonical analytical datasets

The following 22 base tables describe the **September 15 build** of the `analytics` DuckDB schema.
The added `analytics.macro_financial_monthly` table preserves US, California, and LA metropolitan-division rows separately. Existing non-macro panels retain their previous row counts and horizons; a new macro retrieval does not imply that every housing source has also advanced. See the [September requirements report](september_2026_requirements_status.md).

| Analytical table | Frequency / geography | September 15 rows | Geographies | Current coverage |
|---|---|---:|---:|---|
| `fact_housing_observation` | Mixed / mixed | 2,670,497 | See inventory | 1914-12-31 to 2026-09-14 |
| `macro_financial_monthly` | Monthly / US, California, LA division | 1,792 | 3 | 1954-07-31 to 2026-08-31 |
| `county_monthly_panel` | Monthly / Los Angeles County | 439 | 1 | 1990-01-31 to 2026-07-31 |
| `comparable_county_monthly_analytics` | Monthly / eight California counties | 3,512 | 8 | 1990-01-31 to 2026-07-31 |
| `zip_monthly_panel` | Monthly / postal ZIP | 87,406 | 274 | 2000-01-31 to 2026-07-31 |
| `zip_market_regime_quarterly` | Quarterly / Los Angeles County postal ZIP | 7,124 | 274 | 2020-03-31 to 2026-06-30 |
| `la_zip_market_regime_summary_quarterly` | Quarterly / LA ZIP aggregate by regime | 104 | 1 | 2020-03-31 to 2026-06-30 |
| `la_zip_market_regime_transitions_quarterly` | Quarterly / LA ZIP transition matrix | 416 | 1 | 2020-03-31 to 2026-06-30 |
| `metro_monthly_controls` | Monthly / metro and mixed controls | 1,095 | 1 | 1914-12-31 to 2026-05-31 |
| `state_monthly_controls` | Monthly / state | 192 | 1 | 2008-01-31 to 2023-12-31 |
| `homebuying_cost_scenarios` | Monthly / county and postal ZIP | 346,785 | 275 | 2012-01-31 to 2026-05-31 |
| `housing_finance_monthly_hist` | Monthly / 58 California counties | 225,388 | 58 | 2013-03-31 to 2026-03-31 |
| `housing_finance_monthly_rt` | Monthly real-time vintages / 58 California counties | 188,384 | 58 | Month labels 2022-01-31 to 2026-08-31 |
| `county_quarterly_controls` | Quarterly / Los Angeles County | 72 | 1 | 2007-03-31 to 2024-12-31 |
| `state_quarterly_panel` | Quarterly / state | 313 | 1 | 1948-03-31 to 2026-03-31 |
| `metro_quarterly_controls` | Quarterly / metro and mixed controls | 226 | 2 | 1991-03-31 to 2026-03-31 |
| `metro_quarterly_panel` | Quarterly / LA metro | 141 | 1 | 1991-03-31 to 2026-03-31 |
| `county_annual_controls` | Annual / Los Angeles County | 57 | 1 | 1969-12-31 to 2025-12-31 |
| `zcta_annual_controls` | Annual ACS / Census ZCTA | 3,836 | 274 | 2011-12-31 to 2024-12-31 |
| `state_annual_controls` | Annual / state | 71 | 1 | 1955-12-31 to 2025-12-31 |
| `coc_annual_controls` | Annual / Continuum of Care | 822 | 46 | 2007-12-31 to 2025-12-31 |
| `housing_finance_benchmark` | Native benchmark / 58 California counties | 3,886 | 58 | Reference dates 2022-07-01 to 2025-07-01 |

Eleven convenience views provide homeowner, renter, overall-market, and macro
monthly subsets; Version A, B, all-C, latest-C, and Los Angeles-specific finance
views; and the new `county_housing_market_heat_monthly` and
`la_relative_market_performance_monthly` comparison views. Consult the
[analytical dataset inventory](../metadata/analytical_dataset_inventory.csv) for
canonical file paths and example SQL for every table and view.

## Major variable and estimate families

The general layer includes prices and rents; listings, sales, inventory, market
time, and price reductions; mortgage rates and homebuying costs; permits and
modeled construction pipelines; vacancy and rental-availability proxies;
employment, unemployment, income, CPI, population, migration, births/deaths,
eviction, homelessness, insurance, housing-finance controls, eight-county
momentum/tightness/valuation comparisons, and quarterly LA postal-ZIP regimes.

The current estimate inventory contains **277 constructed/estimate IDs**. For historical comparison, the **September 5 pre-macro inventory** grouped its 255 rows as follows; this detailed family table does not include the new macro calculations:

| Estimate family | Rows | Typical use |
|---|---:|---|
| Housing finance, including B/C | 130 | Benchmarks, modeled monthly layers, explicit null targets, sensitivities, and proxies. |
| Other constructed indicators or diagnostics | 41 | Quality flags, supporting ratios, allocations, and diagnostics. |
| Growth and change estimates | 30 | Price, rent, valuation, employment, income, and structural-growth calculations. |
| Homebuying affordability scenarios | 17 | Down payment, principal-and-interest payment, closing cost, and cash-to-close scenarios. |
| Valuation and rent yield | 8 | Price-to-rent levels and changes, relative valuation, and implied gross-rent-yield measures. |
| ZIP market regimes | 7 | Frozen-model regime assignments, shares, change rates, and transition diagnostics. |
| Market tightness | 6 | Legacy and fixed-reference composites plus component diagnostics. |
| Multifamily construction scenarios | 4 | Central/Slow completion and under-construction paths. |
| Market heat and ranking | 3 | Fixed-reference heat index, comparable-county rank, and coverage count. |
| Income affordability | 2 | Price/income-style affordability ratios. |
| Peer-relative performance | 2 | LA-minus-equal-weight-seven-peer constructions and peer-count diagnostics. |
| Rent-regulation structural sensitivities | 2 | Annual lower/upper exposure-adjusted sensitivities, not legal classifications. |
| Population migration normalization | 1 | Annual IRS migration flow normalized by matched population. |
| Population/housing normalization | 1 | Annual permits normalized by matched housing units. |
| Rental availability | 1 | Annual ACS county proxy. |

Every row's automation behavior and limitation is recorded in the
[estimate inventory](../metadata/estimate_inventory.csv).

## Housing-finance Versions A, B, and C

| Version | IDs / suffix | What it represents | Current materialization |
|---|---|---|---|
| A | 67, `_benchmark` | Frozen county benchmark layer at source/reference frequency. Each variable has one row for each of California's 58 counties, so 67 × 58 = 3,886 rows. | 55 IDs have at least one non-null value; 12 calibrated debt/equity target IDs remain schema-only. |
| B | 58, `_monthly_hist` | Retrospective monthly history rebuilt from frozen A and the newest eligible local indicator archives. It can use retrospective information and approved interpolation rules. | 225,388 rows through 2026-03-31. Sixteen canonical IDs are intentionally all null. |
| C | 58, `_monthly_rt` | Cutoff-controlled, append-only monthly real-time layer. It uses only information available by the stated cutoff and prohibits future-endpoint interpolation. | 188,384 rows in the current all-vintage table. Only one genuine cutoff, 2026-08-17, currently exists, so this is not yet a historical panel of multiple real-time vintages. |

A, B, and C are not name-only copies. Across the three layers there are 71
normalized concepts: 54 shared by all three, 13 A-only supporting/audit concepts,
and four B/C-only Redfin sales-value flow proxies. B and C cover the same 58 base
concepts, but 14 have different construction rules; ten debt/equity concepts
produce materially different overlapping values, while four Redfin proxy
concepts primarily differ in cutoff and extension behavior. The 16 canonical
null IDs in each monthly layer comprise 12 calibrated debt/equity targets and
four exact monthly financed-purchase-flow targets; separately labeled PMMS
sensitivities and Redfin proxies must not fill those canonical rows.

The detailed parallel comparison is in
[`Version_A_B_C_Variable_Parallel_Comparison.xlsx`](../outputs/abc_variable_comparison_20260824/Version_A_B_C_Variable_Parallel_Comparison.xlsx),
and the methods are documented in
[`housing_finance_versions_b_c.md`](housing_finance_versions_b_c.md).

## What the system is especially useful for

- **One query surface with traceable lineage.** Users can join housing-market,
  macroeconomic, demographic, construction, and finance controls without losing
  source ID, geography, frequency, vintage, or checksum provenance.
- **Revision-aware research.** Immutable raw vintages make it possible to audit
  provider revisions instead of overwriting history.
- **Cross-frequency analysis without hidden frequency conversion.** Native
  annual and quarterly series remain native unless a separately named,
  documented control or scenario performs the conversion.
- **Local and statewide comparison.** The system supports Los Angeles County,
  metro, postal ZIP, Census ZCTA, state, CoC, tract/PUMA support layers, and all
  58 California counties where the source permits it.
- **Affordability and scenario analysis.** The long homebuying-cost table exposes
  assumptions instead of silently selecting one down payment, closing cost, or
  mortgage scenario.
- **Supply-pipeline sensitivity analysis.** Central and Slow multifamily paths
  show how results change under alternative permit-to-completion lags.
- **Retrospective versus real-time comparison.** Versions B and C allow users to
  distinguish a best-current-history estimate from a cutoff-safe estimate once
  additional C vintages accumulate.
- **Honest missingness.** Unsupported targets remain null or absent, while
  proxies and scenarios have distinct IDs and flags.

## Missing and partial areas

The older general-plus-A methodology-group audit contains **44 Have, seven
Partial, and three Do not have** groups. That 54-group audit predates Versions B
and C. Its associated 366-ID per-variable audit covered the then-current 299
general plus 67 Version A identifiers; it is a preserved methodology audit, not
the current **558-ID consolidated system inventory**.

| Area | Current status | What would be needed |
|---|---|---|
| True monthly rental-listing inventory | Missing by design. ACS vacancy, HUD/USPS address vacancy, ZORI, and Redfin for-sale listings are not substitutes. | An approved, integrable historical rental-listing source and method. This is not solved by an existing credential. |
| Single-family rental construction | Closed/not pursued. One-unit permits do not identify rental tenure or build-for-rent intent. | A qualifying official public source and an explicitly approved method. |
| Exact legally adjusted noncontrolled exposure and rent growth | Partial. Annual ACS structure/age bounds and 2020-2024 exposure-scaled ZORI sensitivities exist, but they are not legal eligibility measures or observed noncontrolled-unit rent growth. | A complete, dated, legally reviewed property/policy/exemption crosswalk. |
| Calibrated aggregate first-mortgage debt and home equity | Partial. PMMS term sensitivities, AHS, SIPP, and broader NMDB controls exist, but the exact target universe remains unmatched. | An exact-date, universe-compatible California owner-occupied closed-end first-lien UPB control, or an explicitly approved compatible calibration. This is a method/universe blocker, not an ordinary login problem. |
| Exact monthly financed-purchase down-payment flow | Canonical B/C rows are explicit nulls; Redfin sales-value proxies are separate. | Exact monthly financed purchase-dollar volume compatible with the HMDA benchmark universe. |
| High-frequency eviction | Partial. Annual modeled Eviction Lab county estimates cover 2000-2018; the audited ETS registry has no California/Los Angeles product. | A newly approved high-frequency local source. Annual values are not interpolated. |
| Quarterly occupied-apartment change | Partial. County and ZCTA consecutive ACS-vintage differences exist annually. | A defensible quarterly source/timing method. Adjacent ACS five-year vintages overlap and are not one-year flows. |
| Quarterly/local price-to-income | Partial. Annual county and ZCTA matched-vintage ACS ratios exist. | Approval to retain annual timing or an explicit mixed-frequency method; annual values are not relabeled as observed quarters. |
| High-frequency homelessness | Partial. HUD PIT provides annual CoC point-in-time counts and an explicit four-CoC project composite. | A separately approved high-frequency source. The composite is not Los Angeles County. |

The complete row-level gap inventory is in
[`metadata/data_gap_analysis.xlsx`](../metadata/data_gap_analysis.xlsx), and the
current narrative status is in
[`docs/current_variable_status.md`](current_variable_status.md).

## Estimate interpretation and caveats

### Market tightness

`market_tightness_index` is a constructed monthly Los Angeles County index, not
an official provider statistic. It is the mean of available, negatively oriented
full-history z-scores for:

1. months of supply;
2. days on market;
3. year-over-year for-sale inventory growth; and
4. price-drop share.

Higher values mean tighter conditions. At least three components are required,
and `market_tightness_components` records coverage. The current index has 172
non-null months through 2026-05-31; the component-count field extends through
2026-06-30. Because means and standard deviations use the full available history,
a provider revision or newly appended month can change earlier index values.
Treat it as a retrospective standardized indicator unless a separately vintaged
real-time version is created.

The newer comparable-county specification is materialized separately as
`market_tightness_index_3c_fixed`. It uses exactly months of supply, days on
market, and price-drop share:
`-[Z(months of supply) + Z(days on market) + Z(price-drop share)] / 3`. Its
population means and standard deviations are frozen from the pooled eight-county
2012-02 through 2019-12 reference sample, and all three components are required.
`county_housing_market_heat_index` then averages the fixed-reference z-score of
annualized three-month ZHVI momentum with the fixed-reference z-score of that
tightness measure. The comparison outputs are available in
`analytics.comparable_county_monthly_analytics` and the
`analytics.county_housing_market_heat_monthly` view; the older four-component LA
index remains available under its original ID and is not silently overwritten.

### Prices, rents, yields, and homebuying scenarios

- The active rent series is Zillow ZORI smoothed and seasonally adjusted, all
  homes plus multifamily. Archived non-seasonally-adjusted ZORI vintages are not
  active inputs.
- `price_to_rent_ratio_zillow = ZHVI / (12 × ZORI)` and
  `implied_gross_rent_yield = 12 × ZORI / ZHVI`. They compare provider indexes;
  the yield is gross and excludes vacancies, operating costs, taxes, insurance,
  financing, and transaction costs.
- Mortgage-payment and cash-to-close outputs are scenarios, not borrower-level
  observations. They use stated down-payment/closing-cost assumptions and
  national Freddie Mac PMMS rates. Principal-and-interest scenarios exclude
  property tax, homeowners insurance, HOA fees, maintenance, mortgage insurance,
  and prepaid escrow unless a field explicitly says otherwise.
- The current homebuying scenario table runs through 2026-05-31 and is rebuilt
  when its price/rent/rate inputs refresh.

### Rental supply and vacancy

- ACS rental availability is annual:
  `for-rent vacant / (renter occupied + for-rent vacant)`, using matched ACS
  five-year vintages and retaining component margins of error.
- HUD/USPS is a native-quarterly **all-residential administrative address-vacancy
  proxy**, not renter-only vacancy or rental listings. Licensed tract and modeled
  postal-ZIP-part detail remains restricted.
- HVS supplies state/metro rental-vacancy rates at native quarterly frequency.
- None of these measures is relabeled as true monthly rental-listing inventory.

### Construction

Census BPS measures permits/authorizations, not starts or completions. The four
multifamily estimates are explicit scenarios:

- Central completions distribute a five-plus-unit permit cohort 20% across lags
  12-17 months, 55% across 18-23, and 25% across 24-30.
- Slow completions distribute 20% across lags 18-23, 50% across 24-29, and 30%
  across 30-36.
- The matching under-construction stocks roll prior modeled stock forward with
  current permits minus modeled completions and require complete active-cohort
  history.

The current construction proxies extend through 2026-06-30. Missing required
permit history propagates to null; there is no unsuffixed preferred estimate.

### Annual controls and mixed frequency

Annual ACS, PEP, IRS, Eviction Lab, California CPI, and real-insurance-premium
variables remain annual. A higher-frequency result exists only under a separately
named construction, such as the explicitly allocated quarterly natural-population
change or the carried-annual nominal insurance control. These are marked as
allocated/carried controls and are not observed high-frequency data.

### Geography and source-window cautions

- Redfin ZIP Key Metrics are overlapping rolling-three-month postal-ZIP
  observations. They are not single-month values and not Census ZCTAs.
- Historical ACS county values cover 2010 forward, while the official 2010 API
  has no ZCTA geography. Configured ZCTA history therefore begins in 2011.
- ACS ZCTA vintages cross 2010- and 2020-Census boundary regimes, and adjacent
  five-year estimates overlap for four survey years.
- Freddie Mac mortgage rates are national, not Los Angeles-specific.
- BEA California quarterly income and Los Angeles County annual income remain
  separate geographies/frequencies.
- Missing observations remain null. The system does not silently zero-fill,
  bridge gaps, substitute counties for ZIPs, or treat plausible economic outliers
  as structural failures.

## September 2026 macro and rates additions

The two September requirements documents are tracked in the [detailed requirements status report](september_2026_requirements_status.md). The macro contract adds **37 general variable IDs from nine provider source IDs**:

| Family | New IDs | Geography and frequency |
|---|---:|---|
| CES industry employment and hourly earnings | 12 | Five industry employment levels and total-private hourly earnings, each with exact-calendar arithmetic YoY growth; monthly NSA observations for California and Los Angeles-Long Beach-Glendale Metropolitan Division. The same IDs are used at both geographies. |
| Unemployment insurance claims | 6 | California initial and continued claims, retained weekly with separate closed-month means and valid-week counts. Incomplete months have null means; wholly absent months within the native span retain zero counts without fabricating weekly observations. |
| Real GDP | 3 | California quarterly real GDP, compounded annualized QoQ growth, and four-quarter arithmetic YoY growth. No monthly interpolation. |
| Financial rates | 16 | National monthly federal funds rate plus five daily series (2-year/10-year Treasury yields, Baa-minus-10-year spread, prime rate, SOFR), each with separate closed-month arithmetic means and valid-day counts. |

DOL claims use the official public bulk CSV, not the XML response as the canonical input. No login is required for these public source contracts. US financial rates remain national rows; they are not silently broadcast into California or local observations. The monthly-only output is `analytics.macro_financial_monthly` / `data/final/macro_financial_monthly.parquet`; native daily, weekly, and quarterly records remain in the fact layer.

The September 15 build materializes **1,792 rows and 32 columns** in the monthly macro panel, spanning 1954-07-31 through 2026-08-31: 866 US rows, 487 California rows, and 439 LA-division rows. These are geography-month rows, not broadcast copies.

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


## Automated newest-source refresh

The comprehensive update command is:

```powershell
& ".\.venv\Scripts\python.exe" run_system_refresh.py --information-cutoff YYYY-MM-DD
```

If the cutoff is omitted, the command uses the current UTC date. A cutoff must be
a date only; it is the information-availability boundary for Version C, not proof
that every provider has published data through that date.

The command performs the following gated workflow:

1. Verify that frozen Version A has not changed.
2. Run the general pipeline's strict online `refresh` stage. Automatically
   downloadable full-history/point files are fetched anew; periodized sources
   reuse checksum-valid history and request newly eligible periods.
3. Clean sources strictly, rebuild harmonized panels and every general estimate,
   rebuild DuckDB, run structural QA, regenerate catalogs, and export CSVs.
4. Rebuild retrospective Version B from frozen A and the newest eligible local
   inputs.
5. Build or reuse the append-only Version C partition for the stated information
   cutoff, without future-endpoint interpolation.
6. Re-register B/C in DuckDB, rerun integrated QA/catalog/export, and verify
   Version A again.

This means market tightness, price/rent/yield measures, homebuying scenarios,
growth rates, multifamily completion/stock scenarios, normalized annual controls,
and other harmonized estimates are recalculated after eligible source updates.
Version A remains frozen; changing it requires the separate approved Version A
workflow. Version B is retrospective and can revise with newer local input
histories. Version C is cutoff-specific and append-only.

ACS detailed-table downloads dynamically probe the official Census group endpoint
for the newest published five-year vintage when no explicit end date is supplied.
An explicit `--end-date` is an upper cap. Discovery is cached within a process,
does not send the Census API key, and falls back to the configured established
vintage with a clear warning only if discovery fails. Newly published ACS cell
regimes still must pass strict cleaner/schema validation; the system will stop
rather than infer an unapproved mapping.

### Refresh evidence and failure behavior

Each run writes or updates:

- `metadata/source_refresh_status.csv`: one row per selected general source,
  newest period/vintage, archive path/checksum, refresh status, blocking flag, and
  required user action, plus pre/post-clean coverage signatures;
- `metadata/source_clean_coverage_baseline.csv`: the last accepted clean row-count
  and date-endpoint baseline used to detect regression or truncation;
- `metadata/pipeline_run_log.csv`: general pipeline stage outcome;
- `metadata/system_refresh_run_log.csv`: end-to-end A/B/C refresh outcome and
  cutoff; and
- the normal raw manifest, transformation log, QA outputs, catalog, coverage,
  inventory, DuckDB, Parquet, and CSV products.

Automatic acquisition failure, a missing or invalid checksum, strict cleaner or
schema failure, a source-period coverage regression/truncation, structural QA
failure, B/C completion mismatch, or Version A freeze violation blocks downstream
publication. A plausible economic outlier is retained as a review warning rather
than automatically deleted. Clean-endpoint verification compares the refreshed
table with the last accepted pre-clean baseline and blocks a falling row count,
left truncation, or a regressing latest date. A failed attempt does not replace
the last accepted baseline. These strict rules apply to `refresh`; ordinary
non-refresh download/clean behavior is unchanged.

At the **September 5 pre-macro snapshot**, the documented horizons before the next system refresh included
the comparable-county and ZIP Zillow panels through 2026-07, quarterly ZIP regime
outputs through 2026 Q2, homebuying scenarios through 2026-05, construction
proxies through 2026-06, Version B through 2026-03 because of its current NMDB
indicator horizon, and one Version C cutoff dated 2026-08-17 using the
cutoff-eligible local archives. C is cutoff-safe relative to what was archived by
that cutoff; complete real-time coverage depends on running scheduled
acquisitions before every cutoff and cannot be reconstructed retroactively from a
file first retrieved later.

## Manual and credential intervention

Across the full 69-source contract, 56 source rows are auto-refreshable and 13 are
marked for manual intervention. Eight of those 13 are Version A-only inputs that
the system refresh deliberately does not touch because A is frozen. The five
manual general sources checked on each refresh are:

- Redfin ZIP Key Metrics;
- Census/HUD AHS Table Creator 2023 California Table 14A; and
- three CDC WONDER natality/mortality exports.

A valid existing checksum archive lets the general rebuild continue, but the
refresh report still asks a person to check the provider for a newer release. A
manual source with no valid archive is blocking. Future Version A HMDA/NMDB
acquisition also follows a separate manual/approved workflow.

| Access item | Snapshot status | When intervention is needed |
|---|---|---|
| `CENSUS_API_KEY` | Present in the user environment at the 2026-08-24 audit; its value is not stored or reported. | If a Census data request rejects anonymous access or the key is missing/invalid. Keys and personalized URLs must never enter config, logs, raw payloads, or tracked metadata. |
| `HUDUSER_API_TOKEN` | Not present. Current verified HUD/USPS coverage through 2026 Q1 remains usable. | Only when a newly published restricted HUD/USPS quarter beyond the archived coverage must be acquired. An authorized HUD User session/token is then required. |
| BLS registration key | Not present and not currently blocking. | LAUS supports anonymous API use for the configured request volume; current CES/CPI sources use public files. A registration key could improve quota resilience but is not an established current requirement. Intervene if BLS changes access or returns an API limit/error. |
| Manual provider files | Existing archives are present. | Check provider release pages; if newer, download the official unmodified file and use the source-specific import command shown in the source inventory. |
| Calibrated debt/equity universe | Unresolved method compatibility. | Coordinate or approve a universe-compatible control/method. Supplying an ordinary API key does not solve this blocker. |

The user should intervene whenever `source_refresh_status.csv` reports manual
release review, authentication failure, missing/invalid archive, acquisition
failure, provider schema change, or coverage regression. The pipeline should not
be bypassed by copying a newer value into clean/final tables.

## Representative official provider pages

The complete list is in the
[system source inventory](../metadata/system_source_inventory.csv). Common entry
points include:

- [Zillow Research data](https://www.zillow.com/research/data/)
- [Redfin Data Center downloads](https://www.redfin.com/news/data-center/downloads/)
- [Census ACS five-year API documentation](https://www.census.gov/data/developers/data-sets/acs-5year.html)
- [BLS LAUS data overview](https://www.bls.gov/lau/data-overview.htm)
- [Freddie Mac PMMS archives](https://www.freddiemac.com/pmms/pmms_archives)
- [FHFA House Price Index datasets](https://www.fhfa.gov/house-price-index?tab=HPI%20Datasets)
- [BEA personal income by county](https://www.bea.gov/data/income-saving/personal-income-by-county)
- [HUD-USPS vacancy data](https://www.huduser.gov/portal/datasets/usps.html)
- [HUD PIT/AHAR data](https://www.huduser.gov/portal/datasets/ahar/2025-ahar-part-1-pit-estimates-of-homelessness-in-the-us.html)
- [CFPB/FFIEC Modified LAR](https://ffiec.cfpb.gov/data-publication/modified-lar)

Provider terms and licensing still apply. In particular, detailed HUD/USPS data
remain restricted, and Zillow/Redfin outputs require appropriate attribution and
compliance with provider terms.

## Recommended starting points for a new analyst

1. Read this overview and the
   [current variable status](current_variable_status.md).
2. Use the [source inventory](../metadata/system_source_inventory.csv) to identify
   provenance, native frequency, geography, refresh behavior, and official links.
3. Use the [variable inventory](../metadata/system_variable_inventory.csv) or
   [data dictionary](../metadata/data_dictionary.csv) to select exact IDs and
   understand units and transformations.
4. Choose a canonical table from the
   [analytical dataset inventory](../metadata/analytical_dataset_inventory.csv).
5. Query the local `database/housing.duckdb` or use the locally generated observation CSVs in
   [`output/exports/`](../output/exports/).
6. Check the [gap inventory](../metadata/data_gap_analysis.xlsx), proxy flags,
   quality flags, and source vintage before interpreting an estimate.

The governing principle is simple: use the newest checksum-valid official input
available to the relevant run or cutoff, recompute documented estimates, preserve
vintages and geography, and leave unsupported targets null rather than silently
substituting a proxy.
