# September 2026 requirement-by-requirement implementation status

> **Historical September 15 snapshot — superseded September 22, 2026.**
> This report is preserved as the record of the earlier run; its dates, test
> counts, coverage, and completion statements below are not current status.
> Use the [September 22 implementation report](september_22_2026_implementation_report.md)
> and the [current county methodology](la_comparable_county_analytics.md).
>
> The September 22 audit corrected a substantive regime denominator mismatch:
> canonical changed, entry, and transition shares now use current classified
> `N_t`, not the smaller comparable-pair denominator. They remain null when any
> current ZIP lacks a previous-quarter classification; separately named
> comparable-pair percentages are conditional diagnostics, not replacements.
> In the refreshed 2026 Q2 snapshot, `N_t = 240` out of 274 ZIPs, 228 pairs are
> comparable, 34 changes are observed, and 12 prior classifications are missing.
> Only 2020 Q4 has complete canonical transition coverage among the 26 quarters.
>
> Newly refreshed Zillow and LAUS inputs now reach August 2026. The verified
> legacy Redfin county file still ends in May, although newer provider
> publications exist. Successor download access encountered HTTP 429 and a
> browser timeout; a compatible three-input county export is not yet verified,
> and the user is obtaining an official export. The former statement that all
> nine county constructions were implemented must be read subject to these
> corrections, source gaps, and the methodological choices still awaiting
> confirmation.

Requirements: `LA & comparable county analytics (1).docx` and
`California_Housing_Macro_Rates_Data_Table.docx`, supplied by the user.
Implementation/retrieval date: September 15, 2026 Pacific (September 16 UTC).

## Outcome and scope

All **11 macro/rate groups** have materialized observations and the requested
core transformations. They add **37 distinct variable IDs**: 12 CES level/growth
fields, six claims fields, three GDP fields, and 16 national-rate fields.
Because CES is retained for both California and the LA division, these are
**49 geography-specific series**, not 37 different economic concepts.

The county attachment is byte-for-byte identical to the previously implemented
county specification. Its **nine constructions (39 IDs)** were independently
checked against their source inputs and formulas; no duplicate versions were
created. Two unspecified methodological choices remain provisional, as detailed
below. Consequently this is **not an unqualified claim that every methodological
decision in both documents is finalized**.

## New macro/rate requirements

| Document requirement | Implemented construction and geography | Latest usable period in this retrieval |
|---|---|---|
| Industry employment growth | Construction, information, financial activities, professional/business services, leisure/hospitality; published NSA CES levels and exact-calendar `100*(x_t/x_(t-12)-1)` for California and LA metropolitan division | July 2026; latest CES observations preliminary |
| Total-private average hourly earnings | Nominal dollars/hour and the same arithmetic YoY formula, both geographies | July 2026 |
| Initial unemployment claims | California native weekly counts; monthly arithmetic mean of weeks ending in the month | Weekly September 5; monthly August 2026 |
| Continued unemployment claims | California native weekly counts using the separate reflected-week date; monthly arithmetic mean | Weekly August 29; monthly August 2026 |
| Real GDP growth | BEA California all-industry real GDP, quarterly SAAR; QoQ annualized `100*((GDP_t/GDP_(t-1))^4-1)` and YoY `100*(GDP_t/GDP_(t-4)-1)` | 2026 Q1 |
| Effective federal funds rate | Published national monthly FEDFUNDS; no artificial California counterpart | August 2026 |
| 2-year Treasury yield | National DGS2 daily observations and closed-month mean | Daily September 14; monthly August 2026 |
| 10-year Treasury yield | National DGS10 daily observations and closed-month mean | Daily September 14; monthly August 2026 |
| Baa–10-year Treasury spread | Published national BAA10Y, in percentage points; daily and closed-month mean | Daily September 14; monthly August 2026 |
| Bank prime loan rate | National DPRIME daily and closed-month mean; retained despite secondary status in document | Daily September 9; monthly August 2026 |
| SOFR | National SOFR daily and closed-month mean; retained as the document's secondary robustness control | Daily September 14; monthly August 2026 |

Monthly rate means use nonmissing published observations, with separate counts;
holidays are not forward-filled. Claims means require every expected Saturday
in a closed calendar month; incomplete months remain null and report the number
of observed weeks. A wholly absent historical month has a null mean and count
zero, not zero claims. September monthly values are not fabricated from a partial
month. GDP is never interpolated into months. Growth uses exact calendar lags,
not simply the previous available row.

California and the Los Angeles–Long Beach–Glendale Metropolitan Division
(`METRODIV_31084`) remain distinct source geographies. The LA division is not the
broader Los Angeles–Orange County MSA, and CES jobs are not LAUS resident employment.

## County specification: existing constructions checked

| Requirement | Verified implementation | Qualification |
|---|---|---|
| Home-price momentum | ZHVI `100*Δ12 ln(P)` and `400*Δ3 ln(P)`, county and LA postal ZIP | Implemented |
| Rent momentum | Corresponding ZORI log-growth measures | Implemented; active smoothed SA ZORI only |
| Market tightness | Negative equal-weight mean of standardized months of supply, days on market and price-drop share; all three required | Normalization settings provisional |
| Price-to-rent ratio | `ZHVI/(12*ZORI)`, plus changes | Level implemented; change horizons provisional |
| ZIP relative valuation pressure | `ln(ZIP price/rent)-ln(LA price/rent)` | Implemented; no county replacement for missing ZIP inputs |
| Employment momentum | LAUS resident employment `100*Δ12 ln(E)` with unemployment context | Implemented; distinct from new CES jobs |
| LA relative performance | LA minus equal-weight mean of all seven fixed peers, separately for price, rent, tightness and price/rent | Implemented; tightness inherits provisional normalization |
| ZIP market regimes | Four-cluster, four-feature model; frozen 2015 Q2–2019 Q4 fit; 2020 onward assignments, quarterly shares and transitions | Implemented; incomplete-feature ZIPs remain unclassified |
| County housing heat | Equal mean of standardized 3-month price momentum and tightness | Implemented; inherits provisional normalization |

The county/ZIP snapshot checked here reaches July 2026 for Zillow/LAUS,
May 2026 for complete tightness/heat inputs, and 2026 Q2 for ZIP regimes.
These existing housing sources were audited, **not newly downloaded in this
macro-only acquisition run**. The common eight-county universe is LA, Orange,
Riverside, San Bernardino, San Diego, Sacramento, Santa Clara and Alameda.

## What remains unresolved or was not selected

1. **Normalization specification requires mentor confirmation.** The document
   requires a fixed historical reference but does not fix dates, pooling scope
   or standard-deviation convention. Existing settings remain February
   2012–December 2019, pooled eight counties, population SD. Tightness, its
   relative-performance comparison and heat must retain this qualification.
2. **Price-to-rent change horizon requires mentor confirmation.** The document
   does not choose a horizon. Existing one- and twelve-month log changes remain
   explicitly provisional; the ratio level itself is fully specified.
3. **Optional alternatives are not missing core requirements.** Nominal wage
   growth is implemented; optional CPI-deflated wages are not added. Claims use
   the permitted monthly-average option, not an additional four-week moving
   average or optional labor-force scaling. Rates use monthly averages, not
   a second end-of-month variant. GDP supplies both permitted growth variants.
   County PCA tightness and population-weighted peer comparisons remain later
   robustness options; predictive testing of regime transitions is not claimed
   as completed by constructing the indicators.
4. **Publication gaps remain real gaps.** Unreleased periods, missing inputs,
   initial lag windows and unclassifiable ZIPs are not imputed.

No login/credential blocker prevented the nine new source acquisitions. BLS
public flat-file access was unavailable, but its official keyless API worked.
That API has quotas: exhaustion on a future run is reported as a failure, not
silently replaced by old data.

## Sources and provenance

- CES: [BLS public API](https://www.bls.gov/developers/) and
  [EDD industry data](https://labormarketinfo.edd.ca.gov/data/industries.html).
- Claims: [DOL official bulk downloads](https://oui.doleta.gov/unemploy/DataDownloads.asp),
  using the full-history `ar539.csv`. Initial claims are `c3+c7` (regular plus
  workshare-equivalent) dated `rptdate`; continued claims are `c8+c12` dated
  reflected week `c2`, as defined by the
  [official ETA 539 field map](https://oui.doleta.gov/dmstree/handbooks/402/402_4/4024c6/4024c6.pdf).
  All 2,112 overlapping California weeks match the older official XML report
  exactly; the CSV adds the newest week. Original national CSV bytes are archived
  unchanged; only the clean table is California-filtered. Older XML is retained
  immutably, not silently used as a failed-download fallback.
- GDP: [BEA quarterly state GDP download](https://apps.bea.gov/regional/zip/SQGDP.zip),
  table SQGDP9, GeoFIPS `06000`, all-industry line 1, chained 2017 dollars.
- Rates: FRED's [FEDFUNDS](https://fred.stlouisfed.org/series/FEDFUNDS),
  [DGS2](https://fred.stlouisfed.org/series/DGS2),
  [DGS10](https://fred.stlouisfed.org/series/DGS10),
  [BAA10Y](https://fred.stlouisfed.org/series/BAA10Y),
  [DPRIME](https://fred.stlouisfed.org/series/DPRIME), and
  [SOFR](https://fred.stlouisfed.org/series/SOFR).
  BAA10Y carries provider redistribution restrictions; local research ingestion
  is not a statement that public redistribution is unrestricted.

Raw vintages, retrieval timestamps, filenames, checksums and geography are
preserved. Formulas are in code, the variable dictionary and transformation log.

## Verification

The rebuilt fact table contains 2,670,497 rows, including 84,030 new macro rows.
All 37 new IDs have non-null output. The monthly macro panel has 1,792 rows;
national, California and LA-division values remain on separate geography rows.
The consolidated system now has 558 configured IDs: 514 have non-null output
and 44 pre-existing finance targets remain explicit nulls.

The monthly macro Parquet, CSV export and DuckDB table reconcile in values,
geography and null masks. None of the new macro facts is imputed or interpolated.

`python run_pipeline.py --stage validate` passes with zero structural errors.
The final full `pytest -q` run passes **757 tests** (340.69 seconds).
Three warning categories remain visible for review: Redfin provider outliers,
historical gaps and the older California insurance monthly control. These are
not erased or converted into fabricated observations. The Version A checksum
freeze verification passes for all 18 protected files.

## Where to use the results and how refresh works

The database, final Parquet files, and observation CSV exports below are
generated locally and are not included in a Git checkout.

- Database: `database/housing.duckdb`.
- Monthly macro controls: `analytics.macro_financial_monthly`,
  `data/final/macro_financial_monthly.parquet`,
  and `output/exports/macro_financial_monthly.csv`.
- GDP: `analytics.state_quarterly_panel` and the corresponding state-quarterly export.
- Native daily/weekly observations and every macro transformation:
  `analytics.fact_housing_observation`.
- [Per-variable, per-geography coverage and lineage](../metadata/september_2026_macro_variable_coverage.csv).
- [Actual acquisition status](../metadata/macro_request_refresh_status.csv).
- [County methodology/output guide](la_comparable_county_analytics.md).

The standard online `run_system_refresh.py` includes all nine new enabled
sources and recalculates their estimates after successful acquisition. It pulls
current provider files, advances year limits dynamically and stops on acquisition
or structural failures. No periodic background schedule is created by this work.
An offline rebuild is not a fresh data pull. Version A remains frozen; Version
B/C data and cutoffs are not rebuilt by this macro-only implementation.

The focused `scripts/refresh_macro_request.py` downloads and cleans only these
nine sources; it does not publish analytical tables. Follow it with the normal
harmonize and build-database stages, regenerate the catalog when source contracts
have changed, then validate and export, stopping if any stage fails. Re-run
`scripts/report_macro_request.py` after successful publication
to regenerate the detailed coverage inventory.
