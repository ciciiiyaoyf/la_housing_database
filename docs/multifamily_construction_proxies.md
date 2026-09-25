# Multifamily completion and under-construction proxies

## Scope and labels

These four Los Angeles County monthly fields are modeled from Census Building
Permits Survey `permits_5_plus_units` observations:

- `new_apartments_completed_proxy_central`
- `new_apartments_completed_proxy_slow`
- `multifamily_under_construction_proxy_central`
- `multifamily_under_construction_proxy_slow`

They are scenarios, not observed completions, starts, occupied apartments, or
projects under construction. Only the Central and Slow scenarios are active;
there is no unsuffixed "best estimate." Calculations remain county-specific and
never pool permit cohorts across geographies.

The four scenario values and their four proxy-specific quality flags are
materialized in `analytics.county_monthly_panel` and exposed through
`analytics.renter_market_monthly`. They are deliberately not added as rows to
`analytics.fact_housing_observation`: the long fact table retains the native
monthly `permits_5_plus_units` input, while these cross-month scenario
calculations belong to the derived analytical panel layer.

## Completion timing

For scenario \(s\), modeled completions in month \(t\) are

`Completed[s,t] = sum(weight[s,lag] * permits_5_plus_units[t-lag])`.

Weights are equal within each approved bucket:

| Scenario | Permit-cohort lags | Bucket share | Weight at each lag |
|---|---:|---:|---:|
| Central | 12-17 months | 20% | 20% / 6 |
| Central | 18-23 months | 55% | 55% / 6 |
| Central | 24-30 months | 25% | 25% / 7 |
| Slow | 18-23 months | 20% | 20% / 6 |
| Slow | 24-29 months | 50% | 50% / 6 |
| Slow | 30-36 months | 30% | 30% / 7 |

Each scenario's weights sum to one. Lags outside the listed ranges have zero
weight. Thus one permitted unit maps to one modeled completion across the full
scenario horizon, subject to source revisions and missingness.

Central output requires the complete 12-30-month lag set, including 30 months
of pre-output permit history. Slow output requires the complete 18-36-month lag
set, including 36 months of pre-output history. A truly missing required permit
month makes the corresponding proxy month null; missing permits are never
treated as zero or silently interpolated.

## Under-construction stock

For each scenario, the modeled month-end pipeline follows the stock-flow
identity

`Stock[s,t] = Stock[s,t-1] + permits_5_plus_units[t] - Completed[s,t]`.

The opening value is not set arbitrarily to zero. It is initialized from every
permit cohort that still has positive uncompleted weight at the first eligible
month. This requires the current cohort plus ages 1-29 for Central and ages
1-35 for Slow, corresponding to complete 30- and 36-month active-cohort
windows. The cohort-survival initialization is algebraically equivalent to the
recurrence.

If any permit observation in the active cohort window is missing, the stock is
null. It is reinitialized only after a complete active window is available
again. Nonnegative permit inputs and completion weights must yield nonnegative
completion and stock proxies.

Each proxy has a companion `<variable_id>_quality_flag`. A populated value is
flagged with its scenario, complete required history, BPS authorization input,
provider-imputation status, and the applicable pre-2022 sample-tally and/or
2022-plus full-universe regime. Warm-up periods, absent calendar months, and
provider-null permit observations are flagged
`insufficient_required_lag_history_or_source_null` and remain null.

## Provenance and interpretation

All four fields retain the BPS source vintage and county identity. They use
monthly inputs and remain monthly modeled outputs; no frequency fabrication is
involved. BPS total estimates may contain provider imputation, and the January
2022 BPS universe change remains a comparability flag. Rebuilding a selected
permit vintage rebuilds all four scenario fields under the versioned
methodologies `bps_5_plus_distributed_completion_lag_v1` and
`bps_5_plus_stock_flow_pipeline_v1`.
