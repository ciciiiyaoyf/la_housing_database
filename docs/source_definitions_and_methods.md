# Source definitions and methods

This reference consolidates source contracts, formulas, and limitations without changing observations or methods.
Coverage figures identify recorded source vintages or
verification examples; use the [data inventory](data_inventory_and_limitations.md) for
current availability and [build and refresh guide](database_build_and_refresh.md) for
general setup and validation. Raw archives and detailed licensed data remain local.

- [ACS rental availability](#acs-rental-availability)
- [AHS mortgage debt](#ahs-mortgage-debt)
- [California insurance](#california-insurance)
- [HUD USPS vacancy](#hud-usps-vacancy)
- [Construction proxies](#construction-proxies)
- [Noncontrolled rental exposure](#noncontrolled-rental-exposure)
- [Eviction estimates](#eviction-estimates)

## ACS rental availability

### Definition and sources

```text
acs_rental_availability_rate
  = acs_for_rent_vacant_units
    / (acs_renter_occupied_units + acs_for_rent_vacant_units)
```

The Census ACS five-year Detailed Table B25004 supplies the for-rent vacant estimate
and its published 90% margin of error (`B25004_002E`, `B25004_002M`); B25003 supplies
the matched renter-occupied denominator component and margin of error
(`B25003_003E`, `B25003_003M`). The five variables are
`acs_for_rent_vacant_units`, `acs_for_rent_vacant_units_moe`,
`acs_renter_occupied_units`, `acs_renter_occupied_units_moe`, and
`acs_rental_availability_rate`.

Official references: [B25004 definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25004.html),
[B25003 definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25003.html),
[2024 B25004 table-based file](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25004.dat),
and [ACS API availability by vintage](https://www.census.gov/data/developers/data-sets/acs-5year.2010.html).

### Geography, reliability, and frequency

Direct component estimates and margins of error are retained for Los Angeles County
and the 274 configured Census ZCTAs. The documented archives cover county vintages
2010–2024 and ZCTA vintages 2011–2024. The official 2010 ACS API does not expose ZCTA
geography; that gap is not filled. Census ZCTAs remain distinct from postal ZIPs.
The constructed rate is exposed in annual county controls.

The county rate requires both component estimates and both margins of error to be
present and nonnegative, with a positive denominator. It is suppressed if either
margin of error is greater than or equal to its estimate; a zero estimate with a
positive margin of error is unreliable. Components and their margins of error remain
available when the rate is suppressed. These native annual five-year estimates are
never interpolated, forward-filled, or expanded to monthly or quarterly observations.

Each immutable API archive preserves requested years and variables, redacted source
URLs, retrieval timestamp, Census vintage, filename, geography contract, and checksum.
The API key stays outside the repository. Refresh B25003 and B25004 together for matched vintages.

### Interpretation and missing rental inventory

This is a rental-availability proxy, not active rental-listing inventory. The separate
[HUD USPS measure](#hud-usps-vacancy) covers all residential addresses at quarterly
frequency, with different geography and reliability information; the two rates are
not combined. `rental_listing_inventory_monthly` remains unavailable because the
governing methodology identifies no approved, integrable historical listing source.
The unresolved step is source/method identification, not credentials or API activation.
A future source needs a stable listing universe, documented geography and revisions,
and usable storage terms. ZORI is a rent index, Redfin active listings are for-sale
listings, ACS is annual, and HUD USPS is all-residential; none substitutes for that stock.

## AHS mortgage debt

### Authorized scope and source context

The Four Variable Data Status Report authorizes the 2023 American Housing Survey as a
public California owner-occupied mortgage-debt calibration proxy. AHS “primary mortgage”
may not mean a legal first lien. This construction remains separate from the exact,
still-null FHFA/NMDB calibrated Version A target. Its authorization comes from that
finance-status report, not `LA_CA_Housing_Additional_Variables_Methodology.docx`.

The Census Bureau/HUD source is 2023 AHS Table Creator, Table 14A, Mortgage
Characteristics: California (`areas=00006`), owner-occupied (`filtergroup1=01`), source
ID `census_ahs_2023_ca_table14a`, native frequency `biennial_survey_year`, geography
`STATE_06`. The CSV does not embed the selected year or geography. Configuration
preserves the official query URL; cleaning requires explicit 2023/California context.
The unchanged CSV, original filename, retrieval timestamp, provider vintage, byte size,
and SHA-256 remain in the immutable archive and manifest.

### Formula and cell validation

The cleaner validates complete Table 14A section order and its exact three-column
export. It retains published estimates and margins of error for regular-mortgage
households, fourteen outstanding-principal bins, balance not reported, median principal,
and mean principal. Displayed household estimates are in thousands and are normalized
to households. Reported bins must reconcile to regular-mortgage households minus
balance-not-reported households within 0.2 thousand households, allowing independent
rounding of published cells.

```text
ahs_ca_reported_case_primary_mortgage_outstanding_principal_proxy
  = sum(14 reported-bin household estimates in published thousands)
    * 1,000 * published conditional mean principal
```

When using already-normalized household counts, the 1,000 factor is not applied again.
The archived 2023 California check records 4,585,200 regular-mortgage households,
3,381,200 reported-bin households, 1,203,800 balance-not-reported households (26.2540%),
a $418,100 conditional mean, and a $1,413,679,720,000 reported-case proxy.

### Missingness, universe, and import

The proxy excludes households with unreported balances. Applying the reported-case
mean to them would introduce an unsupported missing-at-random assumption, so no
all-household extrapolation is emitted. Published margins of error stay attached to
their original cells. No aggregate margin of error, standard error, or confidence
interval is constructed: count/conditional-mean covariance is unavailable.

The result is a 2023 statewide sensitivity, not a 2024 exact-date control, county
observation, or exact owner-occupied closed-end first-lien total. It must not be
allocated to counties or substituted into the twelve null calibrated Version A IDs.
Obtain a new official Table Creator export with margins of error enabled and import
the unchanged file:

```powershell
python run_pipeline.py --stage download --source census_ahs_2023_ca_table14a --local-file PATH_TO_OFFICIAL_CSV
```

Cleaning stops on changed headers, reordered sections, missing/suppressed/nonnumeric
required cells, duplicate keys, missing provenance, or failed bin reconciliation.

## California insurance

### Sources and annual construction

The California Department of Insurance [Residential Property Insurance Report](https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/),
Part I Premium and Exposure Summary Data, supplies the official
`A-Part-I-Data-Summary.xlsx`. It is public and needs no login, API key, or subscription.
The documented coverage is exposure years 2008–2023 at California state geography.
These are neither Los Angeles County estimates nor property-specific premium quotes.

Rendered worksheets are pivot views. The cleaner reads OOXML pivot-cache definitions
and records, requires `EXP_YEAR`, `POLICY_TYPE`, `WP`, and `WE`, filters exactly to
`Homeowners Multi-Peril`, and sums all coverage ranges within each exposure year:

```text
ca_homeowners_written_premium = sum(WP)
ca_homeowners_written_exposures = sum(WE)
ca_homeowners_avg_written_premium = sum(WP) / sum(WE)
```

The annual premium amounts and average are nominal. Cleaning stops if expected
2008–2023 coverage changes, fields/values are missing, shared-item references are
invalid, record counts disagree, or annual exposures are nonpositive. Each raw
workbook remains unchanged with retrieval time, filename, byte size, and SHA-256.

The approved deflator is California DIR CPI-U All Items,
`ca_cpi_u_all_items_annual` (`1982-1984=100`). Its cleaner reads official
`EntireCCPI.PDF` and retains only rows labeled `Annual`; it never calculates annual
averages from subannual rows. The documented vintage has 71 values for 1955–2025.
This California index is distinct from Los Angeles metro CPI.

### Derived controls and frequency

- `ca_homeowners_avg_written_premium_yoy` is `premium_year / premium_previous_year - 1`
  for exact consecutive years. The documented series has 15 values for 2009–2023;
  first-year, nonconsecutive-year, missing, or nonpositive-input cases remain null.
- `ca_homeowners_avg_written_premium_monthly_control` repeats an annual exposure-year
  value at that calendar year's twelve month ends. The documented 192 rows remain
  `STATE_06`, preserve annual source observations and raw provenance, use
  `native_frequency=annual`, `final_frequency=monthly`,
  `quality_flag=carried_annual_control;mixed_frequency_control`, and
  `is_interpolated=false`. They occur in the long fact and
  `analytics.state_monthly_controls` as constructed carried-annual controls.
- `ca_homeowners_avg_written_premium_real` is
  `nominal_premium_year * California_CPI_U_2023 / California_CPI_U_year`.
  Positive premium/CPI values must match the exact year; the 2023 result equals its
  nominal value. Missing inputs stay null; no CPI interpolation or geography
  substitution is allowed. The canonical constant-2023-dollar series remains annual.

The within-year step function follows the governing document, is not release-date-aware,
and is not observed monthly data, linear interpolation, or LA County data. Any separate
analysis-layer carry of an annual value must retain an explicit carried/mixed-frequency
flag. Average premiums are non-additive ratios, not total premium flows.

### Source-specific retrieval

```powershell
python run_pipeline.py --stage download --source ca_doi_residential_property_part_i --force-download
python run_pipeline.py --stage clean --source ca_doi_residential_property_part_i
python run_pipeline.py --stage download --source ca_dir_california_cpi --force-download
python run_pipeline.py --stage clean --source ca_dir_california_cpi
```

Neither source needs credentials or a browser session. The generic XLSX downloader
handles CDI; the DIR downloader archives the official static PDF as a new immutable
vintage when its bytes change.

## HUD USPS vacancy

### Universe, variables, and licensing

Source `hud_usps_ncwm_quarterly` measures all-residential administrative addresses,
not rental advertisements, asking rents, renter tenure, or availability for rent.
It is not `rental_listing_inventory_monthly`.

```text
usps_vacant_residential_addresses
  = usps_short_term_vacant_residential_addresses
    + usps_long_term_vacant_residential_addresses
usps_residential_vacancy_rate
  = usps_vacant_residential_addresses / usps_total_residential_addresses
usps_residential_no_stat_rate
  = usps_no_stat_residential_addresses / usps_total_residential_addresses
```

The eighth variable, `usps_active_residential_addresses`, completes the identity
`total = active + vacant + no-stat`. No-stat addresses are a separate diagnostic
and never enter the vacancy numerator.

HUD USPS tract information and detailed derivatives are licensed. Raw files and
detailed county, tract, and modeled postal-ZIP-part outputs stay access-controlled
under `data/restricted/hud_usps/`, outside Git history and public CSV exports. The
required sublicense notice accompanies every copy. Configuration, methods, tests,
and nonconfidential metadata may be published.

### Official archive and geography

The verified official archive contains 75 artifacts: 73 contiguous standardized-2020
tract NCWM responses for 2008 Q1–2026 Q1 (`ncwm_standardized_2020`), one official
2026 Q1 residential tract-to-postal-ZIP crosswalk (`tract_zip_crosswalk`), and a
58-row 2020 Census California county-name/FIPS reference (`county_fips_reference_2020`).
The crosswalk vintage is fixed for all modeled ZIP histories; the county reference
resolves names without changing FIPS. The earlier derived package is superseded and
not an active input; its historical inventory/hashes remain in
`metadata/hud_usps_quarantine_manifest.csv`.

Every official artifact records role, URL/request query, filename, exact UTC retrieval
time, provider vintage/release date when supplied, geography, byte size, and SHA-256 in
`metadata/hud_usps_source_manifest.csv` and the local `metadata/raw_file_manifest.csv`.
Quarter ends are observation periods, not inferred release dates. Missing, duplicate,
malformed, or changed expected artifacts stop the rebuild.

The recorded rebuild spans 73 quarter ends and 3,029 geographies: 4,234 county-quarter
rows for all 58 California counties; 182,062 tract-quarter rows for 2,494 standardized
2020 LA County tracts; and 34,821 modeled ZIP-quarter rows for 477 LA County portions
of postal ZIPs. Across eight variables these are respectively 33,872, 1,456,496, and
278,568 observations, totaling 1,768,936.

Source tract component counts are direct provider values. County counts sum complete
constituent tract counts with null propagation; rates are computed after aggregation,
never by averaging tract percentages. `reported_value` is populated only for direct
tract components, not constructed county values, modeled ZIP values, or rates.

ZIP counts use the fixed 2026 Q1 residential-address allocation. They represent LA
County portions of postal ZIPs, not whole ZIPs or Census ZCTAs, and retain
`geography=postal_zip_county_part`, `is_modeled=true`. County sums are constructed but
not crosswalk-modeled. Row lineage retains source/crosswalk filenames, checksums,
vintages, and retrieval times.

### Frequency, validation, and source access

Native and analytical frequency stays quarterly; no monthly-panel expansion occurs.
A future monthly carry would require a separate modeled variable with
`is_interpolated=true`, never an observed monthly inventory label.

Structural failures include invalid response schemas/periods, required columns,
quarter ends, identifiers or duplicate keys; incorrect tract/county relationships,
modeled flags or ZIP scope; negative counts, invalid rate bounds or noninteger direct
counts; failed component/rate identities; and excessive crosswalk allocation loss.
Missing inputs remain missing, identities are checked only with complete inputs, and
zero-address rates stay null. In the recorded rebuild, 219 county-quarter rows are
null across dependent components/rates because constituent tracts are incomplete;
partial sums are not retained.

Plausible high rates, large changes, low denominators, and address-management
discontinuities are review warnings, not grounds for deletion, smoothing, or imputation.
Allocation residuals are reported separately against their structural threshold.
The historical 2026 Q1 LA County check was 48,211 vacant / 3,961,536 total addresses
= 0.0121697746 (1.21698%), an all-residential proxy.

`src.ingestion.hud_usps` obtains official authenticated responses and archives them
under `data/restricted/hud_usps/raw/`; `src.cleaning.hud_usps` rebuilds quarterly county,
tract, modeled ZIP-part, common-fact, and extended-lineage tables. Official references:
[dataset](https://www.huduser.gov/portal/datasets/usps.html),
[NCWM API](https://www.huduser.gov/portal/dataset/uspsncwm-api.html),
[ZIP crosswalk API](https://www.huduser.gov/portal/dataset/uspszip-api.html),
[tract sublicense](https://www.huduser.gov/portal/usps/sublicense_agreement.html), and
[California county codes](https://www2.census.gov/geo/docs/reference/codes2020/cou/st06_ca_cou2020.txt).

## Construction proxies

### Scope and completion timing

Four LA County monthly scenarios use Census Building Permits Survey
`permits_5_plus_units`: `new_apartments_completed_proxy_central`,
`new_apartments_completed_proxy_slow`,
`multifamily_under_construction_proxy_central`, and
`multifamily_under_construction_proxy_slow`. They are modeled scenarios, not observed
completions, starts, occupancy, or projects under construction. There is no unsuffixed
best estimate; cohorts remain county-specific and are never pooled across geographies.

```text
Completed[s,t] = sum(weight[s,lag] * permits_5_plus_units[t-lag])
```

| Scenario | Permit-cohort lags | Bucket share | Weight per lag |
|---|---:|---:|---:|
| Central | 12–17 months | 20% | 20% / 6 |
| Central | 18–23 months | 55% | 55% / 6 |
| Central | 24–30 months | 25% | 25% / 7 |
| Slow | 18–23 months | 20% | 20% / 6 |
| Slow | 24–29 months | 50% | 50% / 6 |
| Slow | 30–36 months | 30% | 30% / 7 |

Weights are equal within buckets, sum to one within a scenario, and are zero outside
listed lags. One permit unit therefore maps to one modeled completion over the full
horizon, subject to revisions/missingness. Central requires the complete 12–30-month
lag set and 30 months of pre-output history; Slow requires lags 18–36 and 36 months.
A missing required permit month makes that completion null, never zero or interpolated.

### Under-construction stock and lineage

```text
Stock[s,t] = Stock[s,t-1] + permits_5_plus_units[t] - Completed[s,t]
```

Opening stock is initialized from every cohort with positive uncompleted weight, not
zero. Central needs the current cohort plus ages 1–29; Slow needs current plus ages
1–35, giving complete 30-/36-month active windows. Cohort-survival initialization is
algebraically equivalent to the recurrence. A missing active-window observation makes
stock null; reinitialization occurs only after a complete window returns. Nonnegative
permits and weights must produce nonnegative completions and stocks.

Each variable has `<variable_id>_quality_flag`: populated values identify scenario,
complete history, BPS authorization input, provider imputation, and applicable pre-2022
sample-tally and/or 2022-plus full-universe regimes. Warm-up periods, absent months,
and provider-null permits carry `insufficient_required_lag_history_or_source_null`.

The four values/flags are in `analytics.county_monthly_panel` and
`analytics.renter_market_monthly`. They are not rows in
`analytics.fact_housing_observation`, which retains the native monthly permits input.
All retain BPS vintage and county identity. The January 2022 universe change remains
a comparability flag. Rebuilding a selected permit vintage rebuilds the scenarios
under `bps_5_plus_distributed_completion_lag_v1` and `bps_5_plus_stock_flow_pipeline_v1`.

## Noncontrolled rental exposure

### Source scope and geography

The public-data construction retains 52 ACS estimate/MOE variables from
[B25032, Tenure by Units in Structure](https://api.census.gov/data/2024/acs/acs5/groups/B25032.html),
and [B25127, Tenure by Year Structure Built by Units in Structure](https://api.census.gov/data/2024/acs/acs5/groups/B25127.html),
and constructs eight annual structure/age-share variables. Source IDs are
`census_acs_5year_b25032` and `census_acs_5year_b25127`; access follows the
[official five-year API](https://www.census.gov/data/developers/data-sets/acs-5year.html).
`CENSUS_API_KEY` is read from the environment only during requests; keys and personalized
URLs never enter configuration, provenance, logs, or committed files. No unlisted
provider, including RentCast, is used.

Native/final frequency is annual ACS five-year estimates: documented county vintages
2010–2024 and 274 project ZCTAs for 2011–2024, with the explicit 2010 API geography gap.
ZCTAs retain identifiers such as `ZCTA_90001` in `analytics.zcta_annual_controls`; they
are not postal ZIPs or monthly data. No interpolation, forward-fill, or monthly
expansion occurs. Archives retain vintage, retrieval time, filename, checksum, and
geography. Provider missing sentinels stay null, and estimate/90% MOE cells are separate.
The documented clean B25032/B25127 counts are 84,722/115,530 observations.

### Structure shares and vintage normalization

Let `T32 = acs_b25032_renter_total`. Same-vintage descriptive shares use B25032 renter
structure components (the numerator names below abbreviate that table's component IDs):

```text
acs_renter_occupied_1_unit_share
  = (renter_1_detached + renter_1_attached) / T32
acs_renter_occupied_2_to_4_units_share
  = (renter_2_units + renter_3_to_4_units) / T32
acs_renter_occupied_5_plus_units_share
  = (renter_5_to_9_units + renter_10_to_19_units
     + renter_20_to_49_units + renter_50_plus_units) / T32
acs_renter_occupied_mobile_other_share
  = (renter_mobile_home + renter_boat_rv_other) / T32
```

These are structure descriptions, not legal classifications. B25127 uses explicit vintage regimes and published labels:

| ACS ending vintage | Definitely-recent lower band | Cutoff-mixed band |
|---|---|---|
| 2010–2014 | Built 2000 or later | Built 1980–1999 |
| 2015–2020 | Built 2010 or later | Built 2000–2009 |
| 2021–2024 | Built 2020 or later | Built 2000–2019 |

Normalized estimates/MOEs cover total renter units and six mutually exclusive groups:
one unit, two-to-four, five-to-nineteen, twenty-to-forty-nine, fifty-plus, and mobile/other.
Let `T27 = acs_b25127_renter_total`,
`R = acs_b25127_renter_recent_lower_bound_total`, and
`M = acs_b25127_renter_cutoff_mixed_total`:

```text
acs_renter_recent_construction_share_lower_bound = R / T27
acs_renter_recent_construction_share_upper_bound = (R + M) / T27
```

The lower bound omits potentially qualifying mixed-bin units; the upper includes the whole mixed bin. Neither is an exact rolling fifteen-year share.

### Structural exposure and rent-growth sensitivities

Final exposure bounds are published only for ACS ending years 2020 onward, corresponding
to the AB 1482 effective period, not proof of legal exemption. Let `O` be total B25032
one-unit renter units, `R1` B25127 one-unit renters in the recent band, and `M1` one-unit
renters in the mixed band:

```text
noncontrolled_exposure_share_structural_proxy_lower_bound = R / T27
noncontrolled_exposure_share_structural_proxy_upper_bound
  = min(1, (R + M + O - R1 - M1) / T27)
```

Subtracting `R1 + M1` avoids double-counting. The upper proxy treats remaining one-unit
rentals as potentially exempt; ACS lacks ownership and tenant-notice conditions, so
this is a structural bound, not a legal determination. Both fields are null before
2020; required missing/invalid estimates or nonpositive denominators also yield null.

The 2024 LA County verification gives structure shares 26.9194%, 13.6184%, 58.4807%,
and 0.9815% respectively; recent construction spans 0.9888%–12.6978%; broader structural
exposure has a 37.5339% upper proxy. These diagnose formulas, not legal coverage.

Two LA County annual sensitivity fields use exact same-year December 31 matches:

```text
exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound
  = December ZORI YoY growth * structural exposure lower bound
exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound
  = December ZORI YoY growth * structural exposure upper bound
```

The documented 2020–2024 products are exposure-scaled aggregate sensitivities, not rent
growth within legally noncontrolled units; aggregate ZORI does not observe separate
controlled/noncontrolled segments. Negative ZORI growth reverses numerical product
order without changing the input bounds' meanings. ACS bounds are not carried to
other months, and ZCTA bounds are not joined to postal-ZIP ZORI.

### Exact-target limitations and source retrieval

`noncontrolled_exposure_share` and `exposure_adjusted_noncontrolled_rent_growth` remain
absent. A defensible point estimate needs dated state-law versions/exclusions; property
jurisdiction including incorporated/unincorporated status; every local ordinance and
amendment; construction/certificate-of-occupancy status; ownership and required notices;
affordable-housing, dormitory, mobilehome and other statutory categories; and a documented
covered-property/unit aggregation to county or ZCTA.

`config/rent_control_policy_crosswalk.csv` is an official-source-only scaffold covering
state law, City of Los Angeles RSO, and unincorporated LA County RSTPO. Every row is
`scaffold_only_not_applied`; it is incomplete across municipalities and cannot be joined
to current ACS aggregates. `COUNTY_06037` contains incorporated and unincorporated areas:
the County RSTPO must not be applied county-wide.

Scaffold references are [Civil Code 1947.12](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=1947.12.),
[LA City RSO](https://housing.lacity.gov/rso), and
[LA County RSTPO](https://dcba.lacounty.gov/portfolio/rent-increases/). They establish
provenance, not automated legal classifications; reverify rules by date before use.
With `CENSUS_API_KEY` available, retrieve paired inputs before the standard rebuild:

```powershell
python run_pipeline.py --stage download --source census_acs_5year_b25032 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25127 --force-download
```

Failed/partial API responses never replace archives; no missing observation is fabricated from another geography, table, or vintage.

## Eviction estimates

The retained `eviction_lab_county_estimates` product is annual for 2000–2018. A periodic
source check looks for a genuine high-frequency LA County product without interpolating
the annual estimates, expanding them to months, or substituting another geography.

The authoritative discovery source is the public [Eviction Tracking System registry](https://evictionlab.org/eviction-tracking/get-the-data/).
The audit archives that page unchanged, validates the complete `ets-get-data` table,
and records every official weekly/monthly download. Availability requires a registry
row naming Los Angeles or California with a weekly or monthly Eviction Lab download.
`All Cities` and `All States` are required sentinel rows, not implicit LA County products.

The dated 2026-08-17 check of ETS Version 3.0 (page updated August 8, 2026) found no LA
or California product. No login, credentials, or paywall prevented the check. This is
a dated source finding, not a claim that the registry can never change.

- `metadata/eviction_lab_ets_source_check.csv` records audit date, page checksum/archive
  path, target matches, decision, and unchanged annual-series contract.
- `metadata/eviction_lab_ets_registry_snapshot.csv` retains the complete registry,
  official download URLs, and target-match flags.
- `data/raw/eviction_lab/ets_coverage_registry/` holds immutable official HTML snapshots.

```powershell
python -m src.ingestion.eviction_lab_ets_audit --root .
```

An incomplete/unexpected registry schema fails the audit instead of producing a false
negative. A new LA/California row yields `available_review_before_integration`; it does
not trigger ingestion until geography, definitions, coverage, and compatibility are
reviewed.
