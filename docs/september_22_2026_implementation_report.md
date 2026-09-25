# September 22, 2026 — implementation and remaining exceptions

## Bottom line

The two latest requirements documents have been checked against the code and rebuilt data: `LA & comparable county analytics (1).docx` and `California_Housing_Macro_Rates_Data_Table.docx`. This report supersedes the September 15 status report for current coverage and regime-transition formulas.

All eleven macro requirement groups are implemented at the required frequency: **ten monthly groups and quarterly real GDP**. The user explicitly confirmed the GDP exception on September 22. GDP is therefore **completed quarterly**, not a missing monthly measure. It is not repeated or interpolated into months.

All nine county/ZIP construction families have implementations. That does not mean every date and ZIP has a valid value, or that every method choice was specified by the documents. The important correction in this review is the regime-flow denominator. Remaining limitations are source gaps, an unresolved Redfin successor download, and explicitly provisional choices where the document does not supply a parameter. No missing observations have been fabricated.

## Macro requirements

| Requirement | Implemented construction and geography | Required frequency | Latest completed observation |
|---|---|---|---|
| Industry employment growth | Construction, information, financial activities, professional/business services, leisure/hospitality; levels and `100 × (x[t]/x[t−12] − 1)`; California and LA–Long Beach–Glendale Metropolitan Division | Monthly | August 2026 |
| Average hourly earnings | Total-private nominal level and arithmetic YoY growth; California and LA division | Monthly | August 2026 |
| Initial unemployment claims | California weekly observations retained; arithmetic mean of valid weekly observations in each completed month, with valid-week count | Monthly | August 2026 |
| Continued unemployment claims | Same explicit monthly-mean treatment; California | Monthly | August 2026 |
| Real GDP growth | California quarterly real GDP; `100 × ((GDP[q]/GDP[q−1])^4 − 1)` and `100 × (GDP[q]/GDP[q−4] − 1)` | **Quarterly, as required** | 2026 Q1 |
| Effective federal funds rate | Official national monthly series | Monthly | August 2026 |
| Two-year Treasury yield | National daily observations converted to closed-month arithmetic mean; valid-day count retained | Monthly | August 2026 |
| Ten-year Treasury yield | Same explicit daily-to-monthly treatment | Monthly | August 2026 |
| Baa-minus-ten-year Treasury spread | Same explicit daily-to-monthly treatment | Monthly | August 2026 |
| Bank prime loan rate | Same explicit daily-to-monthly treatment | Monthly | August 2026 |
| SOFR | Same explicit daily-to-monthly treatment | Monthly | August 2026 |

Real earnings and labor-force-scaled claims were optional alternatives, not required missing variables. Monthly means are the selected permitted treatment; they are not weekly sums or month-end snapshots. Incomplete month means stay null. CES series retain their NSA identity. National rates are not relabeled as California or county observations, and LA division payroll/earnings are not relabeled LA County resident employment.

The macro contract has 37 variable IDs: 27 monthly (20 substantive fields plus seven coverage counts), three quarterly GDP fields, five native daily audit inputs, and two native weekly audit inputs. Reuse of employment/earnings IDs at two geographies gives **39 monthly geography-specific series**. The monthly table contains 1,793 geography-month rows and 32 columns; not all columns apply to every geography.

Strict QA now verifies the explicit variable/source/geography/frequency contract, full required coverage, month-end keys, uniqueness, values, and null masks against the fact layer. It rejects daily, weekly, or GDP fields masquerading as monthly outputs. See [frequency completion status](../metadata/macro_required_frequency_status.csv) and [per-variable coverage and provenance](../metadata/september_2026_macro_variable_coverage.csv).

## LA and comparable-county requirements

The fixed county set is Los Angeles, Orange, Riverside, San Bernardino, San Diego, Sacramento, Santa Clara, and Alameda. ZIP outputs use 274 referenced LA County **postal ZIPs**, not Census ZCTAs.

| Document item | Implemented calculation | Current limitation |
|---|---|---|
| 1. Price momentum | `100 × Δ12 ln(ZHVI)` and `400 × Δ3 ln(ZHVI)` at county and LA ZIP levels | August 2026 price levels and YoY growth available for all 274 ZIPs |
| 2. Rent momentum | Same exact-calendar log formulas using active smoothed, seasonally adjusted ZORI | August levels: 256 ZIPs; YoY growth: 220; three-month momentum: 243; missing source history is not filled |
| 3. Market tightness | `−(z(months of supply) + z(median days on market) + z(price-drop share))/3`; all three components required; fixed historical normalization | Compatible verified Redfin inputs stop at May 2026; no active-listings substitute |
| 4. Price-to-rent ratio | `ZHVI/(12 × ZORI)`, plus separately labeled one- and twelve-month log changes | August ratio available for 256 ZIPs; change horizons remain provisional because the document does not select them |
| 5. ZIP relative valuation pressure | `ln(ZIP PTR) − ln(LA County PTR)` in the same month | August available for 256 ZIPs; no county filling of missing ZIP rents |
| 6. Employment momentum | LAUS resident employment `100 × Δ12 ln(employment)` with unemployment context, for all eight counties | Latest August 2026; October 2025 source observations absent for all eight counties |
| 7. LA-relative performance | LA minus the equal-weight mean of exactly seven fixed peers, separately for price, rent, tightness, and PTR | All seven peers required; latest tightness comparison stops at May 2026 |
| 8. ZIP regimes and transitions | Quarter-end four-feature inputs; fixed scaler and four-cluster k-means trained only on 2015 Q2–2019 Q4, applied from 2020 onward; empirical labels; current-classified denominator for required shares | Incomplete ZIP rent history limits classifications and exact transition percentages; details below |
| 9. Housing market heat | `(z(three-month price momentum) + z(market tightness))/2` for all eight counties | May 2026 latest non-null because tightness inputs have not been verified beyond May |

Other known historical gaps include Santa Clara ZORI in June–August 2020 and Redfin price-drop shares in December 2012 for Los Angeles and Orange. Exact-calendar lags preserve these gaps rather than jumping over them.

The [independent county audit](../metadata/county_exact_sep22_audit.json) recomputed 44 numerical/null-mask/source checks for items 1–7 and 9 with no mismatches. The [independent regime audit](../metadata/regimes_exact_sep22_audit.json) passed feature, frozen-training, assignment, stock-share, transition-count, and canonical-denominator checks. Frozen models were checked against their original archived training vintages, not retroactively refit to the newly revised source history.

### Regime correction and what remains unavailable

The document defines `N[t]` as the number of currently classified ZIPs. The previous implementation exposed transition/changed shares using only ZIPs classified in both quarters. Those conditional percentages are useful diagnostics, but **they are not the required denominator**.

The canonical changed, entry, and transition percentages now use current-classified `N[t]`. If any currently classified ZIP lacks a previous exact-quarter classification, the complete numerator is unknown and the canonical percentage is null. The known-pair percentage remains explicitly labeled `*_of_comparable`; it is never substituted for the required percentage. Coverage counts and `transition_denominator_status` make the reason inspectable. For complete pairs, transition shares are `100 × n[i→j,t]/N[t]`, not row-normalized transition probabilities.

In 2026 Q2, 240 of 274 ZIPs are classified; 228 have both-quarter classifications, 34 of those changed regime, and 12 currently classified ZIPs have unknown prior classifications. Thus 34/228 = 14.91% is only a conditional diagnostic. The full required changed percentage cannot be identified from these inputs. Dividing 34 by 240 would be a known-change lower bound, not an exact estimate, so it is not published as the canonical percentage.

Only 2020 Q4 currently has complete previous-quarter coverage for every current classified ZIP. The canonical transition/changed percentages are therefore available for that quarter and null for the other 25 classification quarters. Regime stock shares remain available among each quarter's classified ZIPs, with coverage stated. This is a data-completeness limitation, not a request to change the document formula.

### Method choices still requiring confirmation

The documents require fixed historical normalization but do not specify the reference dates, pooling scope, or SD convention. The existing frozen choice is February 2012–December 2019, pooled across eight counties, population SD (`ddof=0`). PTR changes are retained at one- and twelve-month log horizons because a horizon was not selected. The regime volatility calculation uses population SD of the 24 monthly raw log price changes. These choices are disclosed; they must not be described as parameters explicitly mandated by the document or approved by the mentor. A changed choice requires a versioned model/reference update, not silent refitting.

## Freshness, access, and required intervention

On September 22, all nine macro source contracts and the six county/ZIP input contracts were reacquired from their configured official public endpoints and strictly cleaned with provenance retained. Download success is not the same as proof that an endpoint is the provider's newest product.

The configured legacy Redfin county file still ends in May 2026. Redfin's [methodology and release calendar](https://www.redfin.com/news/data-center/methodology/) and redesigned [download hub](https://www.redfin.com/news/data-center/downloads/) show newer publications. Automated access to the successor hub returned HTTP 429 and the browser attempt timed out. No credential prompt was encountered. A compatible successor county export has not yet been verified, so this build must not be described as fully current for Redfin-dependent estimates.

The user supplied two original exports on September 22: `redfin_housing_market_monthly_all_counties_2012_Jan_to_2026_Aug.csv` (119,227,326 bytes) and `redfin_price_drops_monthly_all_counties_2012_Jan_to_2026_Aug.csv` (61,717,180 bytes). Each has 532,836 rows labeled Monthly/County. The tracker provides months of supply and median days on market; the second file explicitly separates the price-drop count from `PERCENT ACTIVE WITH PRICE DROPS (%)`. The originals remain unedited. Before active ingestion, the adapter must verify true calendar-month periods (not rolling three-month data), exact county geography, share versus count, seasonal adjustment, history, and consistency with the existing frozen normalization. An uploaded file alone does not prove that all three required inputs are compatible. At the time of the verified build reported above, these successor CSVs have not replaced active historical observations.

The [successor-file audit](../metadata/redfin_successor_sep22_readonly_audit.json) now verifies every row's calendar boundaries and unique county-period keys. Each file contains exactly 1,408 relevant county-month rows (eight counties × 176 months), joining one-to-one, with all three required inputs nonmissing through August 2026. Both report provider `LAST UPDATED = 2026-09-03`, which is distinct from the date the user downloaded or supplied them.

The remaining blocker is substantive compatibility. Across 1,380 nonmissing overlapping share observations, the successor price-drop share averages **7.687 percentage points lower**, with no pair matching within successor rounding precision. The frozen February 2012–December 2019 reference window also shifts by **7.525 percentage points on average**. MOS and DOM are much closer (median absolute differences 0.1 month and zero days). The files do not establish whether the share difference comes from definitions, seasonal adjustment, or other provider revisions. Therefore, the new share must not be silently appended to the old normalized series. A reviewed successor adapter and explicit versioned normalization decision remain necessary. The August LA inputs are available in the candidate files (MOS 4.5 months, DOM 49 days, share 17.01%) but are **not yet active estimates**.

A configured `source_refresh_review_holds.redfin_county_monthly` now prevents strict online refresh from claiming successful current-release acquisition solely because the legacy file downloads. Historical offline builds remain usable with the stated cutoff. The hold can be removed after the successor source, adapter, and normalization compatibility are reviewed and tested. Other source refreshes are unaffected. Raw vintages and frozen Version A remain immutable; ordinary refresh does not refit the analytical models.

### Does every run require another large download?

No download is needed to recalculate estimates from an already accepted local vintage. That is an offline rebuild, not a source refresh. A genuine source refresh must check for a newer release. The preferred workflow is to reuse unchanged provider files and automate retrieval of changed files when an official stable endpoint supports that operation. Such an automated successor endpoint has not yet been verified; this report does not claim that automation is already operational.

The two supplied nationwide histories total approximately 181 MB. Only the eight required counties need to enter analytical tables; the original national inputs can remain immutable archives. Where provider-side geography filtering is available, it can reduce transfer size. Downloading a shorter recent time window is smaller, but cannot guarantee the latest revised values for older dates: Redfin explicitly revises historical observations and re-estimates monthly seasonal factors. Given the user's latest-vintage requirement, full-history refresh on each changed release is the safer default unless a revision feed or complete change-detection mechanism is verified. A smaller incremental-only policy should not be adopted silently.

## Outputs and verification

The rebuilt database is `database/housing.duckdb`. This database and the observation CSV exports below are generated locally and are not included in a Git checkout. Main user-facing exports are:

- Monthly macro panel: `output/exports/macro_financial_monthly.csv`, with GDP deliberately excluded.
- Quarterly state panel: `output/exports/la_state_quarterly_panel.csv`, including California real GDP and its growth measures.
- Comparable-county monthly analytics: `output/exports/la_comparable_county_monthly_analytics.csv`.
- LA ZIP monthly panel: `output/exports/la_zip_monthly_panel.csv`.
- Quarterly regime summary: `output/exports/la_zip_market_regime_summary_quarterly.csv`; transition detail: `output/exports/la_zip_market_regime_transitions_quarterly.csv`.

The general fact layer has 2,671,375 observations; comparable-county monthly analytics has 3,520 rows; the ZIP monthly panel has 87,680 rows. The system remains at 558 configured IDs, 514 with at least one non-null value. The existing 44 all-null finance targets are separate, previously documented issues and were not resolved or relabeled by this county/macro work. Versions A, B, and C were not rebuilt in this run; their suffix conventions are unchanged.

Production validation completed with **zero errors and three warning checks**: unusually large provider ZIP YoY values, retained non-monthly gaps, and stale latest observations. Those warnings are not evidence that the unresolved source and method limitations above have disappeared. The full regression suite completed with **826 tests passed in 226.28 seconds**. All 18 files in the frozen Version A checksum manifest remain byte-identical.
