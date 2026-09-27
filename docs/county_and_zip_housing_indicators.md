# County and ZIP housing indicators

## Scope and status

This is the production contract for the nine constructions requested in `LA & comparable county analytics.docx`. The implementation is active at two geographies:

- monthly county analytics for Los Angeles County and seven fixed California peers; and
- monthly and quarterly analytics for verified Los Angeles County **postal ZIP codes**. These ZIPs are not Census ZCTAs.

Status verified September 22, 2026: newly refreshed Zillow and BLS LAUS inputs
extend through August 2026. The latest completed ZIP-regime quarter is 2026 Q2;
the quarterly classification is not converted into monthly observations. Complete
Redfin county tightness inputs, and therefore tightness and housing heat, still
end in May 2026 in the verified legacy download. Later Redfin publications exist,
and the user has supplied successor county CSVs, but their compatibility has not
yet been verified or accepted into the active series.
See the [dated coverage and limitations](data_inventory_and_limitations.md)
for the current completed-versus-unavailable assessment.

The county comparison set is fixed and identified by five-character FIPS codes:

| Role | County | FIPS |
|---|---|---|
| Focal | Los Angeles County | `06037` |
| Peer | Orange County | `06059` |
| Peer | Riverside County | `06065` |
| Peer | San Bernardino County | `06071` |
| Peer | San Diego County | `06073` |
| Peer | Sacramento County | `06067` |
| Peer | Santa Clara County | `06085` |
| Peer | Alameda County | `06001` |

## Source inputs

| Input | Active source IDs | Frequency and geography | Role |
|---|---|---|---|
| Zillow Home Value Index (ZHVI), all homes/middle tier, smoothed and seasonally adjusted | `zillow_county_zhvi`, `zillow_zip_zhvi` | Monthly county and postal ZIP | Price momentum, price-to-rent, valuation pressure, heat, and regimes |
| Zillow Observed Rent Index (ZORI), all homes plus multifamily, smoothed and seasonally adjusted | `zillow_county_zori_smoothed_sa`, `zillow_zip_zori_smoothed_sa` | Monthly county and postal ZIP | Rent momentum, price-to-rent, valuation pressure, and regimes |
| Redfin Housing Market Tracker | `redfin_county_monthly` | Monthly county | Months of supply, median days on market, and price-drop share for tightness |
| BLS Local Area Unemployment Statistics (LAUS) | `bls_laus_la_county` | Monthly county | Resident employment momentum and contextual unemployment rate |

The LAUS source ID is retained for backward compatibility even though it now contains all eight counties. The six source records above are configured for automatic public download and normally require no login or credential. The separately acquired Redfin ZIP measures are not an input to these nine constructions.

### Current Redfin source limitation

The legacy county endpoint was downloaded again on September 22, but its usable
county observations still stop in May 2026. This is a verified limit of the
acquired file, **not** a claim that Redfin has published no newer information.
Redfin's [release calendar](https://www.redfin.com/news/data-center/methodology/)
lists August market-tracker data for September 9 and August price-drop data for
September 16. Its [May 12 pipeline announcement](https://www.redfin.com/news/new-redfin-data-center/)
also describes changed methodology and seasonal-adjustment defaults.

The successor [download hub](https://www.redfin.com/news/data-center/downloads/)
returned HTTP 429 during the September 22 investigation. Its official public-file
index was subsequently verified on September 25 and is used by the ZIP downloader;
that resolves ZIP acquisition but does not establish county-method compatibility.
The user supplied official tracker and price-drop CSVs on September 22.
The read-only audit confirms exact calendar months, unique keys, a one-to-one
join, and complete coverage of all three required inputs for eight counties
over 176 months (January 2012–August 2026). The correct share is `PERCENT ACTIVE
WITH PRICE DROPS (%) / 100`, not the `PRICE DROPS` count. However, the successor
share averages 7.687 percentage points below the legacy share in overlapping
observations, including a 7.525-point shift in the frozen reference window.
The files have no explicit seasonal-adjustment or property-type columns. The
exact cause (definition, adjustment, or provider revision) is not identified by
the files alone. This requires methodological compatibility review and an
explicit versioned normalization decision before active replacement, not an
unreviewed append. See `metadata/redfin_successor_sep22_readonly_audit.json`.
Rolling-three-month ZIP data, metro observations, and inventory are not
substitutes for the specified county-month inputs. Existing estimates retain
their verified cutoff pending that review.

## The nine constructions

All lags below are exact calendar lags. A missing month is not bridged by a row shift, and a log construction is null unless its current and lagged inputs are positive.

### 1. Home price momentum

For ZHVI:

- year over year: `100 × [ln(ZHVI_t) − ln(ZHVI_t−12)]`; and
- three-month annualized: `400 × [ln(ZHVI_t) − ln(ZHVI_t−3)]`.

The output IDs are `zhvi_momentum_yoy_log_pct` and `zhvi_momentum_3m_annualized_log_pct` at county and postal-ZIP geography. The three-month measure is used for turning points, county heat, and the ZIP regime model; the year-over-year measure is the longer-run reporting field.

### 2. Rent momentum

The same two formulas are applied to the sole active Zillow ZORI product:

- `zori_momentum_yoy_log_pct = 100 × [ln(ZORI_t) − ln(ZORI_t−12)]`; and
- `zori_momentum_3m_annualized_log_pct = 400 × [ln(ZORI_t) − ln(ZORI_t−3)]`.

Price and rent momentum remain separate variables. Archived nonsessionally-adjusted ZORI vintages are not substituted.

### 3. County market tightness

For each county-month, the three Redfin inputs are standardized with frozen reference means and population standard deviations:

`market_tightness_index_3c_fixed = −[Z(months_of_supply) + Z(days_on_market) + Z(price_drop_share)] / 3`

The negative sign makes a higher value mean a tighter, more seller-favored market. All three components are required. `market_tightness_components_3c` reports the available-component count; `months_of_supply_z_fixed`, `days_on_market_z_fixed`, and `price_drop_share_z_fixed` expose the standardized inputs. Active listings/inventory are deliberately excluded.

### 4. Zillow price-to-rent ratio

At county and postal-ZIP geography:

`price_to_rent_ratio_zillow = ZHVI / (12 × ZORI)`

This is a valuation ratio, not a cap rate. The output also retains two exact-calendar log changes:

- `price_to_rent_ratio_log_change_mom_pct = 100 × [ln(PTR_t) − ln(PTR_t−1)]`; and
- `price_to_rent_ratio_log_change_yoy_pct = 100 × [ln(PTR_t) − ln(PTR_t−12)]`.

The one- and twelve-month horizons are an explicit pending mentor assumption described below.

### 5. LA postal-ZIP relative valuation pressure

Each postal ZIP is compared with Los Angeles County in the same month:

`relative_valuation_pressure_la_zip = ln(PTR_ZIP) − ln(PTR_LA County)`

A positive value means the ZIP has a higher price-to-rent valuation than Los Angeles County overall. The matched county reference is exposed as `la_county_price_to_rent_ratio_zillow`.

### 6. Local economic momentum

For BLS LAUS resident employment:

`laus_employment_momentum_yoy_log_pct = 100 × [ln(Employment_t) − ln(Employment_t−12)]`

The source is not seasonally adjusted. `unemployment_rate` remains available as a contextual county indicator but is not a component of housing heat.

### 7. Los Angeles relative performance

For every eligible measure and month:

`RelativeLA_X = X_LA − (1/7) × Σ X_peer`

The peer mean always uses the exact seven named counties. The result is null unless Los Angeles and all seven peers are present; it never averages only the available peers. Separate LA-only output fields are maintained for:

- ZHVI momentum: `la_relative_zhvi_momentum_yoy_log_pct` and `la_relative_zhvi_momentum_3m_annualized_log_pct`;
- ZORI momentum: `la_relative_zori_momentum_yoy_log_pct` and `la_relative_zori_momentum_3m_annualized_log_pct`;
- three-component tightness: `la_relative_market_tightness_index_3c_fixed`; and
- Zillow price-to-rent ratio: `la_relative_price_to_rent_ratio_zillow`.

The fixed diagnostic `la_relative_required_peer_count` is seven. These measures are not collapsed into one composite, and the optional population-weighted robustness variant is not a production output.

### 8. LA postal-ZIP market regimes

Each quarter-end postal ZIP observation uses four inputs:

1. three-month annualized ZHVI log momentum;
2. three-month annualized ZORI log momentum;
3. Zillow price-to-rent ratio; and
4. `zhvi_monthly_log_growth_volatility_24m`, the population standard deviation of 24 consecutive monthly `ln(ZHVI_t) − ln(ZHVI_t−1)` changes.

The volatility field is in **raw log points and is not multiplied by 100**. All four inputs must be present. Monthly gaps break the relevant lag or rolling window; no source value is filled.

The model pools complete ZIP-quarters from 2015 Q2 through 2019 Q4, standardizes each feature with the training mean and population standard deviation, and fits deterministic k-means with `k = 4`. The fitted scaler, centers, semantic IDs, and labels are frozen in `metadata/zip_market_regime_model_v2.json`. Quarter-end observations from 2020 Q1 onward are assigned to the nearest frozen center without refitting.

The four empirical center-profile labels are:

- `stable/low-volatility`;
- `rent-led pressure`;
- `cooling/high-volatility`; and
- `high-growth/high-valuation`.

The quarterly assignment output includes `zip_market_regime_id`, label, distance,
standardized feature coordinates, classification status, and missing-input
diagnostics. The document's `N_t` is the number of currently classified ZIPs in
quarter `t`, not only the subset classified in both consecutive quarters:

- `zip_market_regime_share = 100 × regime_zip_count / N_t`;
- `zip_market_regime_changed_share = 100 × number changing regime / N_t`;
- `zip_market_regime_transition_share = 100 × number in a specified from–to cell / N_t`;
- `zip_market_regime_entry_share = 100 × number entering a specified regime from a different regime / N_t`.

Every comparison uses the exact previous quarter. If even one currently
classified ZIP lacks its previous-quarter classification, its transition is
unknown. The canonical changed, entry, and all 16 transition percentages are
therefore **null for that quarter**, rather than treating an unknown transition
as unchanged or silently replacing `N_t` with a smaller denominator. A zero
current classified count also produces null percentages. Current-quarter regime
stock shares remain available when `N_t` is positive.

The implementation exposes the literal full-`N_t` results as
`changed_regime_share_of_classified`, `entered_regime_share_of_classified`, and
`transition_share_of_classified`, with canonical IDs mapped to these fields.
`exited_regime_share_of_classified` uses the same completeness rule. The separate
`changed_regime_share_of_comparable`, `entered_regime_share_of_comparable`,
`exited_regime_share_of_comparable`, and `transition_share_of_comparable` fields
retain conditional diagnostics for ZIPs observed in both quarters. They are not
substitutes for the document's full-population statistics. All shares are in
percent, not fractions.

Summary and transition outputs include `classified_zip_count`,
`comparable_zip_count`, `missing_previous_classification_count`, and
`transition_denominator_status` (`complete`, `incomplete_previous_quarter`, or
`no_current_classifications`). The full 4-by-4 matrix retains observed pair
counts, including zero-count cells, even when full-population percentages cannot
be identified. No transition is inferred across a missing quarter.

In the September 22 refreshed snapshot, 2026 Q2 has **240 classified ZIPs out of
274**, with 228 comparable pairs, 34 observed regime changes, and 12 currently
classified ZIPs lacking a previous-quarter classification. Its canonical
transition percentages are consequently unavailable. Across the 26 application
quarters from 2020 Q1 through 2026 Q2, only **2020 Q4** has complete prior
classification coverage for the current `N_t`; the other 25 quarters retain
conditional diagnostics but not canonical full-`N_t` transition percentages.

Model v2 is the active production model. The older `metadata/zip_market_regime_model_v1.json` is a superseded draft and is not referenced by the active configuration.

### 9. County housing market heat

For every county-month:

`county_housing_market_heat_index = [Z(three-month ZHVI momentum) + Z(market tightness)] / 2`

The two components have equal weights and use their frozen reference distributions. Positive values mean hotter conditions relative to the reference. `county_housing_market_heat_rank` ranks observed counties from hottest to coolest within the month, and `county_housing_market_heat_count` reports the same-month coverage denominator.

## Frozen reference and model behavior

Two artifacts protect historical comparability:

| Artifact | Frozen content | Refresh behavior |
|---|---|---|
| `metadata/comparable_county_reference_parameters_v1.json` | Tightness and heat component means and population SDs, reference specification, and input-source lineage | Required at build time; ordinary refresh applies it and does not refit |
| `metadata/zip_market_regime_model_v2.json` | Training scaler, four centers, empirical labels, training-data fingerprint, model fingerprint, and source lineage | Required at build time; ordinary refresh classifies with it and does not refit |

Configuration/model mismatches, missing artifacts, changed fingerprints, missing required counties, or malformed geography are structural failures. They stop the build or validation rather than silently changing the method.

## Outputs and where they live

### One table for all nine constructions

`analytics.nine_constructions` combines the nine constructions into one wide,
downloadable table. It projects accepted values from the five products below
and stacks their records; it does not recalculate the methods above.

| `row_type` | Source product | Original row key |
|---|---|---|
| `county_monthly` | `comparable_county_monthly_analytics` | County and month. |
| `zip_monthly` | `zip_monthly_panel` | Postal ZIP and month. |
| `zip_regime_quarterly` | `zip_market_regime_quarterly` | Postal ZIP and quarter end. |
| `zip_regime_summary_quarterly` | `la_zip_market_regime_summary_quarterly` | Quarter end and regime. |
| `zip_regime_transition_quarterly` | `la_zip_market_regime_transitions_quarterly` | Quarter end, from-regime, and to-regime. |

Shared context includes `date`, `frequency`, `geo_id`, `geography_type`,
`geography_scope`, source-table/file references, and `applicable_variable_ids`.
The LA ZIP aggregate rows use county geography with an explicit postal-ZIP
aggregate scope; they do not become county housing measurements or individual
ZIP records. Original coverage, nulls, model/normalization diagnostics, and
transition denominators remain attached. The monthly ZIP projection retains
the explicitly named LA County price-to-rent reference used by relative
valuation; it introduces no additional county-to-ZIP copying.

Use `applicable_variable_ids` with
[the combined-table dictionary](../metadata/nine_constructions_dictionary.csv)
to distinguish an inapplicable cell from a missing applicable value. The mapping
uses existing canonical IDs and records each construction and row type;
[configuration](../config/nine_constructions.yaml) defines the projections.
A monthly variable appearing as an input to a quarterly classification retains
its input meaning; the containing record remains a quarterly classification.
No quarterly classification, share, or transition is repeated into months.
Counts repeated as context across four summary rows or sixteen transition rows
are not additive across those rows.

In the Data Browser, select **Nine housing constructions — All geographies and
periods** and leave all filters at **All** for the complete download. See
[the download walkthrough](query_and_export_commands.md#download-all-nine-housing-constructions-together).
The source products remain available separately. The combined product lives at
`data/final/nine_constructions.parquet`, with the standard CSV at
`output/exports/la_nine_constructions.csv`.

To inspect it at the DuckDB prompt:

```sql
SELECT *
FROM analytics.nine_constructions
ORDER BY row_type, date DESC, geo_id
LIMIT 10;
```

To retrieve monthly county observations only:

```sql
SELECT date, geo_id, county_housing_market_heat_index,
       market_tightness_index_3c_fixed, panel_quality_flag
FROM analytics.nine_constructions
WHERE row_type = 'county_monthly'
ORDER BY date, geo_id;
```

Ordinary harmonization/database builds include the combined product. To build,
register, and export it from already accepted local final products without
refreshing providers, close database readers and run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage nine-constructions
```

### Parquet and CSV

| Product | Parquet | CSV export |
|---|---|---|
| All nine constructions with explicit row types | `data/final/nine_constructions.parquet` | `output/exports/la_nine_constructions.csv` |
| Eight-county monthly analytics | `data/final/comparable_county_monthly_analytics.parquet` | `output/exports/la_comparable_county_monthly_analytics.csv` |
| Los Angeles County monthly panel, including LA copies of the comparable features | `data/final/county_monthly_panel.parquet` | `output/exports/la_county_monthly_panel.csv` |
| LA postal-ZIP monthly panel, including momentum, PTR, and relative valuation | `data/final/zip_monthly_panel.parquet` | `output/exports/la_zip_monthly_panel.csv` |
| ZIP-quarter regime assignments | `data/final/zip_market_regime_quarterly.parquet` | `output/exports/la_zip_market_regime_quarterly.csv` |
| Quarterly regime shares, entries/exits, and coverage | `data/final/la_zip_market_regime_summary_quarterly.parquet` | `output/exports/la_zip_market_regime_summary_quarterly.csv` |
| Full 4 × 4 quarterly transition matrix | `data/final/la_zip_market_regime_transitions_quarterly.parquet` | `output/exports/la_zip_market_regime_transitions_quarterly.csv` |

### DuckDB

The integrated database is `database/housing.duckdb`. The Parquet products are materialized as:

- `analytics.comparable_county_monthly_analytics`;
- `analytics.county_monthly_panel`;
- `analytics.zip_monthly_panel`;
- `analytics.zip_market_regime_quarterly`;
- `analytics.la_zip_market_regime_summary_quarterly`; and
- `analytics.la_zip_market_regime_transitions_quarterly`.

Two focused views are also published:

- `analytics.county_housing_market_heat_monthly`; and
- `analytics.la_relative_market_performance_monthly`.

Open the database read-only from Python:

```python
import duckdb

con = duckdb.connect("database/housing.duckdb", read_only=True)
print(con.sql("""
    SELECT date, county_name, county_housing_market_heat_index
    FROM analytics.county_housing_market_heat_monthly
    ORDER BY date DESC, county_housing_market_heat_rank
    LIMIT 24
""").df())
```

Variable definitions live in `config/variables.yaml`. Executed transformation metadata is written to `metadata/transformation_log.csv`, and the generated dictionary is `metadata/data_dictionary.csv`.

## Refreshing with the newest source vintages

DuckDB is a static snapshot, not a live connection. To check the configured public endpoints for the newest eligible release, archive any new payload immutably, clean strictly, reconstruct the estimates, rebuild DuckDB, validate, refresh metadata, and write CSV exports, run:

```powershell
python run_pipeline.py --stage refresh
```

To perform that general refresh and also rebuild housing-finance Versions B and C while verifying frozen Version A, run:

```powershell
python run_system_refresh.py --information-cutoff YYYY-MM-DD
```

Each run uses the newest successfully archived source vintage selected by the pipeline. Zillow and Redfin can revise their published histories, so refreshed observations may change while the fixed normalization parameters and frozen regime centers remain unchanged. Acquisition or strict-cleaning failures block publication; they do not silently reuse a stale clean file as if it were current. Review:

- `metadata/source_refresh_status.csv` for acquisition/manual-intervention status;
- `metadata/raw_file_manifest.csv` for retrieval time, source filename, vintage, and checksum;
- `metadata/pipeline_run_log.csv` for the overall result; and
- `output/qa/qa_summary.csv` and `output/qa/qa_failures.csv` for validation.

The repository does not install a scheduler. Automation means the refresh command performs these checks and rebuilds consistently whenever it is run or scheduled externally.

## Explicit assumptions pending mentor confirmation

The requested document fixes the construction logic but does not choose the following details. Both are implemented transparently as production defaults and tagged `pending_mentor_confirmation`:

1. **Fixed normalization reference:** February 2012 through December 2019, pooled over all eight counties, using population standard deviations (`ddof = 0`), for both the market-tightness inputs and the county-heat inputs.
2. **Price-to-rent change horizons:** exact-calendar one-month and twelve-month log changes, each multiplied by 100.

A mentor decision should not overwrite either convention silently. If either assumption changes, update the method ID, create a new parameter artifact where applicable, rebuild every dependent output, and preserve the prior artifact and source vintages for reproducibility.

## Interpretation and missingness cautions

- ZHVI, ZORI, Redfin, and LAUS histories can be revised by their providers. The pipeline preserves raw vintages and records the selected lineage.
- ZORI is a rent index, not rental inventory. Redfin active listings are for-sale listings and are not a tightness component in this specification.
- PTR and relative valuation are price/rent comparisons, not operating-income yields or cap rates.
- Postal ZIPs are not ZCTAs. County observations are never silently copied to ZIPs.
- Exact-calendar lags, complete tightness components, all seven peers, and all four regime inputs are required. Canonical full-`N_t` transition percentages additionally require previous-quarter classifications for every currently classified ZIP; merely having some consecutive-quarter pairs is insufficient. Missing source values remain missing.
- Changing ZIP ZORI coverage changes classification denominators. Use the published counts and percentage coverage with regime shares and transitions.
