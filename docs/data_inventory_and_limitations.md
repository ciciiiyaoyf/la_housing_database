# Data inventory, coverage, and limitations

This repository combines housing, rental, construction, finance, population,
income, and labor-market data for Los Angeles and related California
geographies. The working database is a **local snapshot**, not a live provider
connection. A successful query reads the accepted local data; it does not prove
that every source is current or that every requested concept is available.

Start with the [README](../README.md) to set up the project and download a table.
Use the [query and export commands](query_and_export_commands.md) for reproducible filters,
units, temporal types, and exports, and [database build and refresh](database_build_and_refresh.md) for builds,
source refreshes, storage, and validation.

## Definitions, coverage, and evidence

The configured contract has **558 unique variables**: 375 general variables,
67 Version A variables, 58 Version B variables, and 58 Version C variables.
There are **69 unique provider source IDs**; B/C reuse archived inputs rather
than adding provider sources. These are configured counts, not assurances that
every variable is populated in a supplied database.

Use these generated files for the latest recorded build rather than copying
counts from a dated report. They describe the database from which they were
generated; a later provider release does not update them automatically.

| Question | Authoritative project reference |
|---|---|
| What variables exist, and what do they mean? | [Variable dictionary](../metadata/data_dictionary.csv): exact IDs, definitions, original and simplified units, temporal types, geography, source frequency, transformations, and flags. |
| Which variables have values, and where? | [Variable inventory](../metadata/system_variable_inventory.csv): materialization and coverage by system layer. |
| What tables can I query? | [Analytical dataset inventory](../metadata/analytical_dataset_inventory.csv): DuckDB objects, storage paths, date/geography keys, coverage, and example SQL. |
| Which source contracts are configured? | [Source inventory](../metadata/system_source_inventory.csv) and [source catalog](../metadata/data_catalog.csv): providers, official URLs, access methods, and refresh instructions. |
| Which outputs are constructed, modeled, or scenarios? | [Estimate inventory](../metadata/estimate_inventory.csv): formulas, limitations, dependencies, and output locations. |
| What are the current recorded totals? | [Inventory summary](../metadata/system_inventory_summary.json) and [storage inventory](../metadata/system_storage_inventory.csv). |
| What period does each source cover? | [Source coverage](../metadata/source_coverage.csv); inspect a variable's non-null coverage as well as its table's date range. |
| What happened during the latest refresh? | [Source refresh status](../metadata/source_refresh_status.csv), [macro refresh status](../metadata/macro_request_refresh_status.csv), and [county refresh status](../metadata/county_analytics_refresh_status.csv). |
| Which requirements remain partial or intentionally missing? | [Gap analysis](../metadata/data_gap_analysis.csv) and [decision register](../metadata/paused_variables_decision_register.csv). |
| How were the data transformed? | [Transformation log](../metadata/transformation_log.csv), variable definitions, and the source-specific code. |

A provider source ID is an acquisition contract, not a database file. One
source can have many archived vintages and variables. Catalog products also
include internal finance outputs that introduce no new provider source.
Configured IDs, populated IDs, table rows, and files are different counts:
some configured targets have explicit null rows, and some constructed measures
exist only in a panel or scenario table rather than the main fact table.

The earlier Excel inventories and finance comparison are dated presentations
of these records. They are not build inputs or current catalogs; use the
dictionary and generated inventories above for current definitions and recorded
coverage, and the [finance guide](housing_finance_versions.md) for the A/B/C
crosswalk and methods. The [document variable audit](../metadata/docx_variable_audit.csv)
preserves an earlier requirements scope; it is not a complete current inventory.

A methodology concept can map to several configured IDs. Assess whether its
target has non-null observations at the required geography and frequency,
separately from whether inputs, alternate measures, or proxies exist. A
populated scenario does not establish observed-target completeness, and an
all-null schema does not establish availability. Source authorization is a
separate question from physical availability, as described below.

## Choose the right analytical product

All table names below belong to DuckDB's `analytics` schema. The dataset
inventory gives their exact keys, storage paths, and recorded coverage.

| Product | Appropriate use |
|---|---|
| `fact_housing_observation` | Long observations with variable IDs and source lineage; mixed native frequencies and geographies. |
| `county_monthly_panel`, `zip_monthly_panel` | Monthly housing analysis at LA County or postal-ZIP geography; individual measures can have different coverage and source windows. |
| `comparable_county_monthly_analytics` | LA and the seven fixed comparison counties, with the separate frozen-reference constructions. |
| `zip_market_regime_quarterly`, `la_zip_market_regime_summary_quarterly`, `la_zip_market_regime_transitions_quarterly` | Quarterly ZIP classifications, regime shares, transitions, and coverage diagnostics. |
| `county_annual_controls`, `zcta_annual_controls` | Annual county and Census-ZCTA measures, including matched-vintage ACS constructions. |
| `state_annual_controls`, `state_monthly_controls` | State controls at their documented output frequency; monthly insurance controls retain annual source meaning. |
| `state_quarterly_panel`, `county_quarterly_controls`, metro control panels | Native quarterly or explicitly constructed controls at their actual geography. |
| `macro_financial_monthly` | Monthly national, California, and LA metropolitan-division macro measures on separate geography rows. GDP is in the quarterly state panel. |
| `coc_annual_controls` | HUD point-in-time homelessness measures at Continuum-of-Care geography, including the separately identified project composite. |
| `homebuying_cost_scenarios` | Down-payment, sale-price- or loan-based closing-cost, payment, and cash-to-close scenarios with assumptions retained. |
| `housing_finance_benchmark` | Version A source-faithful benchmark; IDs end in `_benchmark`. |
| `housing_finance_monthly_hist` | Version B retrospective modeled history; IDs end in `_monthly_hist`. |
| `housing_finance_monthly_rt` | Version C cutoff-specific monthly estimates; IDs end in `_monthly_rt`. Keep `information_cutoff_date` when comparing vintages. |

Versions B and C reuse archived inputs and preserve the frozen Version A
benchmark. B may use later benchmark endpoints when reconstructing history;
C may use only information available by its cutoff and does not interpolate
toward a future endpoint. See [housing-finance versions](housing_finance_versions.md).

The database, raw archives, and observation exports are generated locally and
are not included in a Git clone. Published metadata and reference workbooks
describe the recorded project build; they do not provide the underlying data.
Licensed HUD-USPS detail is excluded from public downloads.

## Interpretation rules

- Keep postal ZIPs, Census ZCTAs, counties, states, metros, metro divisions,
  national series, and CoCs distinct. Preserve leading zeros in every ID.
  HUD-USPS modeled ZIP outputs represent **LA County portions** of postal ZIPs.
- The active rent index is Zillow ZORI smoothed and seasonally adjusted, all
  homes plus multifamily. Archived NSA vintages are excluded from active use.
- Redfin ZIP Key Metrics are overlapping rolling-three-month observations.
  A monthly output date does not make them single-month flows. The national
  source CSV is raw-only; the analytical ZIP product uses the versioned LA list.
- ACS observations are overlapping five-year estimates. County history begins
  in 2010; the official 2010 API has no ZCTA geography, so configured ZCTA
  history begins in 2011. ZCTA vintages also cross the 2010/2020 boundary regime
  between the 2020 and 2021 ACS vintages. Consecutive-vintage differences are
  neither independent survey estimates nor directly observed annual flows.
- Original units and scales remain authoritative. Simplified labels do not
  rescale values. Percent, percentage points, fractions, dollar multipliers,
  real-dollar bases, and annualized rates must retain their distinctions.
- Annual controls remain annual unless a separately named transformation
  explicitly carries or allocates them. The nominal insurance monthly control
  is a flagged calendar-year carry; quarterly natural-population change is
  allocated timing. Neither is observed at the higher frequency.
- Mortgage-payment and cash-to-close outputs are scenarios. Retain their
  down-payment and closing-cost assumptions; principal-and-interest payments
  exclude property tax, insurance, HOA fees, maintenance, and mortgage insurance.
  The Zillow implied gross rent yield excludes operating and ownership costs.

Detailed source formulas, uncertainty treatment, and modeled construction
assumptions are in [source definitions and methods](source_definitions_and_methods.md).

## Metro variables and finer geography alternatives

The earlier metro review used an August 24 inventory and September 4, 2026
source review. Its variable counts and coverage dates are historical. The
configuration was rechecked for this consolidation on September 24; the
alternatives below remain proposals, not newly acquired observations.

The Los Angeles–Long Beach–Anaheim MSA covers Los Angeles and Orange counties.
Los Angeles–Long Beach–Glendale Metropolitan Division **31084** has the footprint
of Los Angeles County. Equal land coverage does not make provider methods or
geography labels interchangeable. Keep division records labeled as divisions,
including the distinct BLS and FHFA provider identifiers. See the
[California EDD metropolitan definitions](https://labormarketinfo.edd.ca.gov/definitions/metropolitan-areas.html).

| Existing measure family | Geography and interpretation |
|---|---|
| CES total nonfarm employment and MoM/YoY growth | `ces_total_nonfarm_employment_sa` uses MSA series `SMS06310800000000001`; it is a broader payroll-employment control. |
| CPI-U and inflation | `cpi_u_all_items_la_nsa` uses the published LA–Orange CPI area. The September 4 review identified no official county/ZIP/ZCTA/tract general CPI replacement; retain the regional control rather than allocate it. [BLS geographic sample](https://www.bls.gov/cpi/additional-resources/geographic-sample.htm). |
| HVS rental vacancy, MOE, and YoY percentage-point change | Published metro quarterly estimates remain broader controls. Local ACS availability and HUD-USPS address vacancy have different universes and are not interchangeable replacements. |
| FHFA purchase-only HPI and growth | Quarterly SA/NSA division products retain their official method. The existing annual county all-transactions HPI is a separate product. |
| September CES industry and earnings additions | NSA monthly industry employment and total-private hourly earnings already exist for California and Division 31084. These do not implement the proposed SA total-nonfarm division series below. |

County LAUS employment measures residents; CES measures payroll jobs at
establishments. Even with matching footprints, they answer different questions.
See [BLS LAUS geographic availability](https://www.bls.gov/lau/lauavail.htm).

| Possible extension | Current repository boundary and required treatment |
|---|---|
| SA total-nonfarm payrolls for Division 31084 | [BLS lists `SMS06310840000000001`](https://www.bls.gov/sae/additional-resources/list-of-published-state-and-metropolitan-area-series/california.htm), but it is not configured. Any adoption needs separate level/MoM/YoY IDs; preserve the existing MSA comparator. |
| QCEW county industry detail | [BLS QCEW](https://www.bls.gov/cew/overview.htm) offers employment, establishments, and wages. It is not configured here; retain its quarterly reporting and source adjustment status rather than replacing monthly CES. |
| FHFA ZIP5 and tract HPI | [FHFA developmental indexes](https://www.fhfa.gov/data/hpi/datasets) offer finer annual geography, but are not configured here. Preserve missing/suppressed values, verify ZIP5 definitions, and do not splice them into quarterly purchase-only HPI. |
| ACS tract rental availability | A proposed annual five-year-estimate extension to the existing county/ZCTA components. Preserve matched vintages and published MOEs; no tract product or quarterly interpolation is implemented. |

Matching five-digit postal ZIP and ZCTA codes do not establish spatial
equivalence. Any spatial allocation requires a dated crosswalk and explicit
weighting method. New source/geography/method combinations require their own
configuration, identifiers, provenance, and validation before becoming active;
public source discovery alone is not ingestion or approval of a new method.

## September 22, 2026 verification snapshot

This section records the September 22 review of
`California_Housing_Macro_Rates_Data_Table.docx` and
`LA & comparable county analytics (1).docx`. These are dated findings, not a
claim that provider releases after that date have been integrated. The review
supersedes the September 15 status for coverage and regime-transition formulas.

### Macro frequency and geography

All eleven requested macro groups were materialized at the required frequency:
ten monthly groups and **quarterly real GDP**, as explicitly confirmed on
September 22. GDP is complete at quarterly frequency and is not expanded to months.

| Requirement | Construction and geography | Latest completed period in that review |
|---|---|---|
| Five industry-employment groups | BLS NSA CES levels and arithmetic YoY `100*(x[t]/x[t-12]-1)` for construction, information, financial activities, professional/business services, and leisure/hospitality; California and LA division. | August 2026 |
| Total-private hourly earnings | Nominal dollars/hour and the same exact-calendar YoY formula; California and LA division. | August 2026 |
| Initial claims | California weekly regular plus workshare-equivalent counts; arithmetic mean across the expected weeks of a completed month, with coverage count. | August 2026 monthly |
| Continued claims | California weekly levels dated by the reflected week; separate completed-month arithmetic mean and coverage count. | August 2026 monthly |
| Real GDP growth | BEA California all-industry real GDP, quarterly SAAR; QoQ annualized `100*((GDP[q]/GDP[q-1])^4-1)` and YoY `100*(GDP[q]/GDP[q-4]-1)`. | 2026 Q1 |
| Effective federal funds rate | Published national monthly FEDFUNDS. | August 2026 |
| Two- and ten-year Treasury yields | National DGS2/DGS10 daily observations converted to closed-month means with valid-day counts. | August 2026 monthly |
| Baa–Treasury spread, prime rate, SOFR | National BAA10Y, DPRIME, and SOFR; closed-month means and valid-day counts. Spread units remain percentage points. | August 2026 monthly |

DOL's `ar539.csv` maps initial claims to `c3+c7` at `rptdate` and continued
claims to `c8+c12` at reflected-week date `c2`. Claims means require every
expected Saturday in the completed month; missing months have null means and
zero counts. Rate means use available published days without holiday filling.
Growth requires the exact prior calendar period. Real earnings and
labor-force-scaled claims were optional alternatives, not missing requirements.
National rates are never relabeled as California observations, and LA division
payroll measures are not LA County resident-employment measures.

See [frequency completion](../metadata/macro_required_frequency_status.csv)
and [variable coverage and provenance](../metadata/september_2026_macro_variable_coverage.csv).
Source definitions and URLs remain in the source inventory. BAA10Y has provider
redistribution restrictions; local ingestion does not establish unrestricted
public redistribution rights.

### County, ZIP, and regime coverage

The fixed comparison set is LA, Orange, Riverside, San Bernardino, San Diego,
Sacramento, Santa Clara, and Alameda. September 22 accepted Zillow and LAUS
inputs through August 2026. Missing ZIP rent history and missing exact-calendar
lags remain null, including the October 2025 LAUS gap across the county set.
The [county audit](../metadata/county_exact_sep22_audit.json) and
[regime audit](../metadata/regimes_exact_sep22_audit.json) record the independent
formula, source-value, null-mask, and frozen-model checks.

The older `market_tightness_index` uses full-history normalization and at least
three available components among months of supply, days on market, inventory
YoY growth, and price-drop share. It is distinct from
`market_tightness_index_3c_fixed`, which requires all three specified components
and uses a frozen reference. The old ID is not silently replaced. Tightness,
market heat, and dependent LA-relative comparisons remained limited by the
accepted Redfin inputs through May 2026.

Canonical regime changed/entry/transition shares use the **currently classified
ZIP count `N[t]`**, not only ZIPs classified in both quarters. If any currently
classified ZIP lacks its previous-quarter class, the complete numerator is
unknown and the canonical percentage is null. Separately named
`*_of_comparable` percentages are conditional diagnostics. In the reviewed
2026 Q2 data, 240 ZIPs were classified, 228 had comparable pairs, and 34 known
changes did not determine a complete changed percentage. Only 2020 Q4 had full
previous-quarter coverage in that build.

The frozen pooled normalization window, population-SD convention, and one- and
twelve-month price-to-rent change horizons are disclosed implementation choices;
they were not specified or approved by the requirements documents. Any change
requires a versioned reference/model decision. See the full
[county and ZIP housing indicators](county_and_zip_housing_indicators.md).

### Redfin successor review hold

The legacy county download still ended in May 2026 during the review despite
newer provider publications. Two supplied successor county exports passed
calendar-boundary, geography, uniqueness, and three-input completeness checks
through August; their provider `LAST UPDATED` date was September 3, distinct
from retrieval. They had **not** replaced the accepted active observations.

The [successor audit](../metadata/redfin_successor_sep22_readonly_audit.json)
found a substantive price-drop-share shift: an average 7.687 percentage points
below the legacy overlapping values and 7.525 points below within the frozen
reference window. The files did not establish whether definitions, seasonal
adjustment, or revisions caused it. A reviewed adapter and explicit versioned
normalization decision are required before combining these histories.

`source_refresh_review_holds.redfin_county_monthly` blocks strict refresh from
claiming current-release success merely because the legacy endpoint downloads.
Historical offline builds remain usable with the stated cutoff. An offline
rebuild does not acquire newer data; a source refresh must check for revisions.
No verified automatic successor endpoint or incremental-only revision policy
was established by this review.

## Partial and intentionally unavailable targets

| Target | Available evidence and remaining boundary |
|---|---|
| True monthly rental-listing inventory | Intentionally missing under the current method. ACS rental availability, HVS/USPS vacancy, ZORI, and Redfin for-sale listings do not satisfy it. A new source and method must be approved; an existing credential does not solve the gap. |
| Exact policy-adjusted noncontrolled exposure/growth | ACS structure/age bounds and annual exposure-scaled ZORI sensitivities exist. Exact legal exposure and observed rent growth inside noncontrolled units remain absent; the policy scaffold is not an applied parcel/exemption crosswalk. |
| Calibrated first-lien debt, equity, and dependent ratios | Canonical calibrated targets remain explicit nulls. PMMS sensitivities, AHS reported-case debt, SIPP statewide sensitivities, and broader NMDB inputs are separate products with different universes or reference dates. |
| Exact monthly financed-purchase down-payment flow | B/C canonical flows remain explicit nulls. Redfin sales-value weights are separately labeled proxies, not observed financed purchase-dollar volume. |
| Single-family rental construction | Closed/not pursued. Generic one-unit permits do not identify rental tenure or investor intent. |
| Quarterly occupied-apartment change and local price-to-income | Annual county/ZCTA ACS constructions exist. Renter-occupied units include more than apartments, and overlapping-vintage differences do not supply quarterly timing. |
| High-frequency eviction | Annual modeled Eviction Lab county estimates are retained. The documented ETS registry check found no qualifying LA/California product; no annual series is interpolated. |
| High-frequency homelessness | Annual HUD PIT CoC data and the explicit four-CoC composite exist. The composite is not LA County, and partial/sheltered-only count years remain analytically incomplete. |

AHS excludes households without reported balances from its aggregate proxy and
does not extrapolate their debt. SIPP debt is broader than first-lien debt;
its equity sensitivity is conditional on positive equity. SIPP products use
rounded counts/shares and conditional means with the provider's top-one-percent
exclusion. Neither source supplies a fabricated aggregate uncertainty interval
or automatically fills the unmatched Version A calibration target.

The earlier B25106 midpoint/80%-debt-service/25-year mortgage construction remains
a **legacy modeled scenario** for reproducibility, not the primary debt/equity
estimate. The active HUD-USPS measure is an **all-residential administrative
vacancy proxy**, not true rental-listing inventory. See the
[source methods](source_definitions_and_methods.md#hud-usps-vacancy),
[active official manifest](../metadata/hud_usps_source_manifest.csv), and
[historical quarantine manifest](../metadata/hud_usps_quarantine_manifest.csv).

## Requirements scope and source authorization

Physical availability does not establish authorization for every research use.
The Additional Variables methodology, later decision workbooks, finance
methodology, Four Variable Data Status Report, and county/macro specifications
have distinct scopes. A provider approved for one variable is not blanket
approval for another construction.

The Four Variable Data Status Report separately authorizes the AHS/SIPP finance
sensitivities and HMDA annual down-payment work; HMDA's absence from the narrower
Additional Variables source audit does not make it unauthorized overall.
B05002/B07001 were explicitly approved as the ACS migration-supplement mappings;
their extra ZCTA coverage is a repository extension. Foreign-born population is
a stock and moved-from-abroad is a gross mobility measure, neither a replacement
for IRS net migration.

Preserve the [decision register](../metadata/paused_variables_decision_register.csv)
and its authority references when interpreting closed targets. Provenance
includes original filenames, checksums, retrieval times, source vintages,
geography, and transformation history. It is not reconstructed from a report's
publication date. Public metadata does not grant access to licensed raw data;
HUD-USPS source and detailed derivatives remain restricted.
