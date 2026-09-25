# Querying units and temporal types

The variable dictionary covers all 558 configured variables. `unit_simplified`
and `temporal_type` are stable query fields; the existing `unit` remains the
original unit. Labels never rescale values or change observation dates,
frequency, geography, source vintage, or aggregation rules.

`config/variable_metadata.csv` is the explicit per-variable registry.
`config/variable_metadata_provenance.json` records the supplied workbook's
filename, SHA-256, import timestamp, definitions, additions, and corrections.
The workbook covers 482 IDs; 76 later additions are classified from the current
configuration and transformation definitions. The source workbook remains intact.

| Temporal type | Meaning |
|---|---|
| `stock` | Outstanding level; do not sum through time. |
| `flow` | Quantity during a period; additive only across compatible, nonoverlapping periods. |
| `period_average` | An explicitly defined average; inspect its averaging basis. |
| `period_statistic` | A non-additive index, median, modeled level, or cross-sectional statistic. |
| `derived_ratio` | Ratio, rate, share, or yield; recompute from components where appropriate. |
| `growth_change` | Change between observations; recompute for a different horizon. |
| `rolling_window` | Overlapping-window observation; do not sum adjacent observations. |
| `cumulative` | Cumulative-to-date amount; do not sum successive totals. Currently unused. |
| `metadata_or_assumption` | Uncertainty, flags, diagnostics, or assumptions with no general economic aggregation rule. |

These categories describe observations; they do not select frequency-conversion
algorithms. In particular, Redfin ZIP observations remain overlapping rolling
three-month windows, and repeated annual controls retain their annual source
meaning. Annualized GDP and income rates are not unadjusted quarterly flows.

Unit simplifications include `current_dollars` to `USD`, `housing_units` to
`units`, and `fraction`/`proportion` to `share`. `percent`,
`percentage_points`, dollar scaling, annualization, constant-dollar bases, and
index bases remain distinct. `monetary_basis` preserves inflation information
when the simplified label is less specific. A growth fraction labeled `share`
can be negative or exceed one; the label does not impose a zero-to-one bound.

Five reviewed classification corrections are recorded in `metadata_review_note`,
with the original supplied type retained in `workbook_temporal_type`: three
average-premium variables are derived ratios; annual total BEA personal income
is a flow; the cross-sectional implied average mortgage balance is a period
statistic. No numerical data were changed.

Refresh metadata and SQL views after updating the explicit registry:

```powershell
python run_pipeline.py --stage variable-metadata
```

This writes `metadata/data_dictionary.csv`, the DuckDB
`metadata.variable_dictionary` table, `output/exports/variable_dictionary.csv`,
and a transformation-log entry. It does not rebuild observations. A project
with `require_variable_metadata: true` fails when its registry is missing;
validation treats missing, duplicate, or contradictory labels as structural
errors. Normal configuration loading and B/C dictionary registration also
retain these fields.

Find variables, then download a selected slice:

```powershell
python run_query.py catalog --temporal-type stock --unit units --output output/exports/stock_variables.csv
python run_query.py observations --dataset main --temporal-type flow --geo-id COUNTY_06037 --start-date 2020-01-01 --output output/exports/la_flows.csv
python run_query.py observations --dataset version-b --temporal-type stock --unit USD --geo-id COUNTY_06037 --output output/exports/la_finance_stocks.parquet
python run_query.py table --dataset county-monthly --temporal-type flow --output output/exports/county_flow_table.csv
python run_query.py table --dataset zip-monthly --temporal-type rolling_window --output output/exports/zip_rolling_table.parquet
python run_query.py table --dataset homebuying-scenarios --variable closing_cost_loan_3pct --output output/exports/closing_cost_3pct.parquet
```

Repeat a filter to select several values within that field; different fields
are combined with AND. `--unit` is an alias for `--unit-simplified`.
`--variable` matches an exact variable ID. Output paths must be new `.csv` or
`.parquet` files. Parquet preserves typed identifiers; when opening a CSV in
Excel, import ZIP, ZCTA, and FIPS columns as text to retain leading zeros.

The four observation datasets are `main`, `version-a`, `version-b`, and
`version-c`. Inclusive date filters use `observation_date`, `reference_date`,
`month`, and `month`, respectively. Version A's reference date does not change
its source interval; Version C retains all information cutoffs. Original source
and vintage fields are exported alongside the labels. Catalog membership does
not imply observations are materialized; unavailable variables remain unavailable.
Existing HUD-USPS restrictions also apply to long-form downloads.

For SQL, the labeled views are:

```sql
SELECT observation_date, geo_id, variable_id, value, unit,
       unit_simplified, temporal_type, monetary_basis, source_vintage
FROM analytics.fact_housing_observation_labeled
WHERE temporal_type = 'flow' AND geo_id = 'COUNTY_06037';
```

The other views are `analytics.housing_finance_benchmark_labeled`,
`analytics.housing_finance_monthly_hist_labeled`, and
`analytics.housing_finance_monthly_rt_labeled`. The `table` command supports
18 named wide datasets, listed by `python run_query.py table --help`. It selects
measure columns using the same filters, retains geography/date/provenance keys,
and writes a companion `<output-stem>.variables.csv` with the exact
`table_column` to `variable_id` mapping and each column's unit and temporal type.
For example, the ZIP panel's display column `homes_sold` maps explicitly to the
Redfin rolling-window ID. Unclassified measure columns are reported and omitted.
No single temporal type is assigned to an entire mixed table.

Homebuying scenarios use the existing price, down-payment, and closing-cost row
identifiers. Their companion dictionary additionally supplies
`row_identifier_column` and `row_identifier_value`, so the meaning of a value
column remains explicit for every scenario. Multiple IDs within one scenario
role are alternatives; selections across roles intersect. For example,
`--variable down_payment_10pct --variable closing_cost_loan_3pct` keeps scenarios
with both choices. Direct mortgage-amount and cash-to-close selections retain
all scenarios unless row-role or date filters are also supplied. Scenario
assumptions and identifiers remain in the download.
