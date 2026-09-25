# Housing finance versions

This document records the repository contract for the monthly housing-finance layers derived from the source-faithful Version A benchmark. It follows `LA_CA_Housing_Finance_Variables_Methodology.docx`; the authoritative machine-readable definitions are [the Version B configuration](../config/version_b_monthly_hist.yaml), [Version B variable dictionary](../config/version_b_variables.yaml), [Version B transformation catalog](../metadata/version_b_transformation_catalog.csv), [the Version C configuration](../config/version_c_monthly_rt.yaml), [Version C variable dictionary](../config/version_c_variables.yaml), and [Version C transformation catalog](../metadata/version_c_transformation_catalog.csv).

These layers do not replace or rewrite Version A. Version A remains the audit and rebenchmarking layer at each source's actual frequency, with variable IDs ending in `_benchmark`. Version B and Version C use separate IDs, output files, lineage files, manifests, and runners.

## Version contract

| Layer | Purpose | Suffix | Information rule | Write rule |
|---|---|---|---|---|
| Version A | Source-faithful benchmark and validation | `_benchmark` | Directly published or directly aggregated inputs at native frequency | Unchanged and checksum-frozen |
| Version B | Retrospective monthly history for charts, historical decomposition, and model development | `_monthly_hist` | May use the full historical series and later benchmark endpoints | Rebuilt as one atomic flat history; every generated row is modeled and no row is a nowcast |
| Version C | Real-time monthly vintage for forecast evaluation and production forecasting | `_monthly_rt` | Only inputs released or retrieved by the stated information cutoff; no future interpolation endpoint | One append-only partition per information cutoff |

All monthly dates are calendar month ends and county FIPS is the permanent geography key. The output contract retains all 58 California counties, including explicit null rows, with Los Angeles County identified as `06037`. Version B has no `information_cutoff_date`; Version C requires one. A Version C partition may be rerun idempotently only when its contents are identical. A different result for an existing cutoff is rejected rather than overwriting the vintage.

The Version A freeze is enforced through [the checksum manifest](../metadata/version_a_freeze_manifest.csv). Version B/C preflight, build, and validation verify the frozen runner, configuration, catalogs, benchmark code, and `data/final/housing_finance_benchmark.parquet` before proceeding; build and validation verify the freeze again on completion.

## Variable surface and methods

The dictionaries are closed contracts: emitted IDs must match their version dictionary exactly. Version B configures 58 IDs: 28 canonical modeled repeats, 16 canonical explicit-null targets, four Redfin flow proxies, and ten debt/equity sensitivities or scenarios. Version C also configures 58 IDs: 28 canonical modeled carries, 16 canonical explicit-null targets, four Redfin flow proxies, and ten debt/equity sensitivities or scenarios.

The A/B/C crosswalk contains 71 concepts: 54 mapped across all three versions,
four separately named B/C Redfin flow proxies linked to the corresponding A
HMDA anchors, and 13 A-only support/audit output concepts. The latter comprise
ten ACS population, owner-unit, value, income, and cost inputs plus three PUMS
payment-share and junior-lien diagnostics. They remain available as benchmark
inputs even though B/C do not emit them as separate monthly output concepts.
Read `benchmark_variable_id` and `construction_anchor_variable_ids` in the B/C
dictionaries when tracing a monthly ID; replacing the suffix alone misses the
separate proxy-to-anchor relationships.

For comparisons, match county, month, and the declared concept mapping, and
select one Version C information cutoff. Inspect non-null values separately
from row and flag counts: explicit-null contracts are materialized rows too.
The `canonical`, `proxy`, and `scenario` flags are separate attributes; the four
Version C Redfin proxies currently also have `scenario=true`, while the four
Version B proxies have `scenario=false`. Their `output_status` remains `proxy`
in both dictionaries.

The implemented methods follow the methodology's frequency and accounting rules:

- Mortgage payment: Version B repeats the Version A ACS/PUMS structural payment and related debt-service measures within the anchor's reference calendar year. Version C carries the latest eligible aggregate P&I benchmark forward. Payment is not regenerated from modeled debt, which avoids circularity.
- Down-payment rates: annual HMDA county rates are repeated as monthly levels. Rates are never divided by 12. Version B repeats the activity-year rate; Version C carries the latest eligible released rate.
- Down-payment flow proxy: Version B distributes each populated annual HMDA flow by county-month Redfin `homes_sold * median_sale_price` proxy weights and assigns the floating residual to the last eligible month so the twelve proxy values sum exactly to the annual benchmark. Version C uses only an eligible archived Redfin snapshot, scales a complete finalized year's proxy weights to the eligible annual HMDA flow, and uses that frozen scale only for later eligible target months.
- Debt sensitivities: Version B scales each Version A 20/25/30-year PMMS sensitivity anchor using California NMDB all-mortgage growth and complete county Redfin sales-share movements. Its separately labeled raw composite debt sensitivity uses the Version A raw-debt anchor with the same growth indicators. NMDB is only a growth indicator in these constructions, never the calibration level. Version C capitalizes carried aggregate P&I using the latest eligible Freddie Mac PMMS rate and fixed 20/25/30-year annuity factors; its raw composite sensitivity combines the documented payment bounds and fixed-term endpoints. PMMS is an origination-rate sensitivity, not the preferred outstanding-stock coupon.
- Equity scenarios: the matched ACS mortgaged-owner or all-owner value anchor is updated with eligible county Zillow ZHVI movements, then the matching PMMS debt sensitivity is subtracted. Debt plus scenario equity must equal the matched modeled owner-value stock.

The methodology distinguishes annual flows, rates, monthly payment levels, and stocks. Therefore annual flows must reconcile to annual totals, repeated rates stay unchanged, the complete anchor-year average of a modeled payment path must reconcile to its benchmark, and debt/equity scenarios must satisfy their stock identity.

## Canonical nulls, proxies, and sensitivities

Missing method-compliant inputs are represented as explicit null canonical rows; they are not silently filled with a nearby proxy. In both monthly versions the following canonical concepts remain explicit nulls:

- aggregate down-payment flow for all eligible, conventional, FHA, and VA loans;
- California-calibrated owner-occupied first-mortgage debt and its per-mortgaged-owner-unit, per-owner-unit, per-capita, and owner-income-normalized measures; and
- mortgaged-owner and all-owner equity, equity per owner unit, real-2024-dollar equity, aggregate LTV, and the mortgaged-owner equity rate that depend on the calibrated debt stock.

The four Redfin sales-value flow IDs are separately suffixed and marked as proxies. They approximate monthly purchase-value weights from total-market homes sold and median sale price; they do not measure financed purchase-dollar volume, cash/finance mix, or borrower-level down payments.

The 20/25/30-year debt IDs and their mortgaged-owner/all-owner equity counterparts are separately labeled sensitivities or scenarios. They never populate a canonical calibrated debt or equity ID. A build requested with `--strict-primary-concepts` remains blocked while any canonical calibrated debt/equity target is null.

## Runners and outputs

The runners expose `preflight`, `build`, `validate`, and `all` stages. Both accept optional inclusive `--start-month YYYY-MM` and `--end-month YYYY-MM` bounds. Version B rejects an information cutoff; Version C requires one for `build` and `all`.

```powershell
python run_version_b.py --stage preflight
python run_version_b.py --stage all

python run_version_c.py --stage preflight --information-cutoff 2026-08-17
python run_version_c.py --stage all --information-cutoff 2026-08-17
```

The date in the Version C example is the current completed repository cutoff. Future runs must use the actual forecast information cutoff and only archived snapshots eligible by that date.

Version B writes:

- observations: `data/final/housing_finance_monthly_hist.parquet`;
- normalized lineage: `data/final/housing_finance_monthly_hist_lineage.parquet`;
- Los Angeles County export: `output/exports/la_version_b_monthly_hist.csv`;
- preflight and QA: `output/qa/version_b_preflight.csv`, `output/qa/version_b_qa_summary.csv`, and `output/qa/version_b_qa_failures.csv`; and
- reproducibility records: `metadata/version_b_build_manifest.csv` and `metadata/version_b_pipeline_run_log.csv`.

Version C writes one cutoff-specific observation and lineage pair:

- observations: `data/final/housing_finance_monthly_rt/information_cutoff_date=YYYY-MM-DD/part.parquet`;
- normalized lineage: `data/final/housing_finance_monthly_rt_lineage/information_cutoff_date=YYYY-MM-DD/part.parquet`;
- Los Angeles County export: `output/exports/la_version_c_monthly_rt_YYYY-MM-DD.csv`;
- preflight and QA: `output/qa/version_c_preflight.csv`, `output/qa/version_c_qa_summary.csv`, and `output/qa/version_c_qa_failures.csv`; and
- append-aware reproducibility records: `metadata/version_c_build_manifest.csv` and `metadata/version_c_pipeline_run_log.csv`.

## Current materialization

The completed 2026-08-17 build has the following checksum-gated products:

- Version B: 225,388 observations and 1,231,722 normalized lineage rows, 58 variable IDs, all 58 California counties, and monthly coverage from 2013-03-31 through 2026-03-31.
- Version C: 188,384 observations and 482,312 normalized lineage rows, 58 variable IDs, all 58 California counties, and monthly coverage from 2022-01-31 through 2026-08-31 in the append-only `information_cutoff_date=2026-08-17` partition.
- Both version-specific QA summaries contain zero structural failures and zero warning failures. The Version C raw-debt low, central, and high levels match their Version A reference envelope exactly for all 58 counties.

The pipeline logs, QA summaries, build manifests, completion markers, and Parquet files remain the machine-readable evidence for these counts.

## Lineage and QA

Each observation carries its benchmark mapping, construction and monthly method, transformation ID, method version, modeled/nowcast/imputation/interpolation flags, source periods, reference and release dates, source and parameter geographies, benchmark vintage, universe definition, and a lineage ID. Normalized lineage records preserve each input's source ID, source vintage, frequency, source period, reference and release date, retrieval timestamp, original filename, and SHA-256 checksum. Build manifests hash the benchmark, version configuration, dictionary, transformation catalog, selected raw assets, observations, and lineage output.

The standalone global metadata registrar merges all 58 Version B and all 58 Version C dictionary rows into `metadata/data_dictionary.csv` using its existing schema, and registers observation, lineage, and completion-marker product families in `metadata/data_catalog.csv`. After a checksum-complete build, it writes one `metadata/transformation_log.csv` row per transformation actually emitted, using the completed output paths, source paths, row counts, completion timestamp, vintages, and checksums. Registration is idempotent, preserves unrelated rows, replaces Version B execution rows on a revised-history rebuild, and preserves separate Version C cutoff-vintage execution rows. This describes the registration contract and does not assert that a particular production build succeeded.

Structural QA stops the build or validation on failures. It checks the shared observation and lineage schemas; suffix/version/month-end/key/flag rules; the full 58-county set; exact dictionary and transformation-catalog coverage; Version B modeled/no-nowcast semantics; Version C cutoff and no-look-ahead rules; source-release timing; uncertainty-bound ordering; Version A anchor coverage; annual flow, repeated-rate, payment-average, and debt/equity identities. Broad economic outliers are retained and reported as warnings for review rather than deleted or converted into structural errors.

## Current source limitations

- Financed purchase-dollar volume: no licensed ATTOM/deed input or ATTOM credential is available to the current build. Redfin therefore supports only the separately labeled total-market sales-value proxy. The four canonical monthly HMDA flow targets remain null.
- Debt calibration universe: the archived NMDB California `TOT_UPB` series covers all active mortgages, not the required owner-occupied first-lien universe. It cannot calibrate canonical debt or equity. Version B may use its growth rate only inside labeled PMMS scenarios; Version C uses eligible PMMS rate sensitivities instead.
- Benchmark depth: the frozen Version A artifact currently contains one ACS 2024 five-year anchor (2020-2024 source period, 2022-07-01 reference date) and one HMDA 2025 activity-year anchor. This is insufficient for a multi-anchor historical rebenchmarking history; the builders preserve the limited anchor structure rather than inventing releases.
- Real-time archive depth: the current Redfin, Zillow, and PMMS raw manifests begin with July 2026 retrievals and do not contain the historical snapshots needed to reconstruct earlier information sets. Version C can enforce a cutoff for archived vintages, but the repository cannot claim genuine historical pseudo-out-of-sample vintages before the snapshot archive begins.

These are source or universe limitations, not permission to substitute a proxy into a canonical target. When a compatible public or licensed input is added, it must be archived immutably with its vintage, retrieval time, checksum, filename, release timing, frequency, and geography before the canonical method can change.
