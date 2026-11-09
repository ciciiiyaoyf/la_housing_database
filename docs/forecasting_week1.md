# Week 1 housing forecasts

Read the complete answers in [Word](../output/forecasting/week1/week1_exercises.docx),
[PDF](../output/forecasting/week1/week1_exercises.pdf), or
[Markdown](../output/forecasting/week1/week1_exercises.md).
Each task and question within Exercises 1 through 7 has a separate numbered
response, followed by the three final interpretation answers. The submission
includes the regression coefficients, sample dates, accuracy tables and charts.
The separate two-page memo covers only the three final questions.

The detailed final answers distinguish forecast horizon from predictive accuracy,
quantify sensitivity to lags and training dates, and compare each outcome and county.
Their benchmark-relative RMSE reductions use `100 * (1 - model_rmse / reference_rmse)`
on matched targets. Positive means lower error, not the percentage of future behavior
explained. Descriptive test winners are reported separately from the models chosen
using earlier validation data; no universal maximum forecast horizon is established.

The submission concludes with [implications for the full forecasting project](forecasting_model_design.md):
outcome-specific starting lags and growth measures, a small candidate input set,
sample and publication-timing requirements, and the next evaluation design.
These are proposed specifications for development, separate from the fitted Week 1 models.
The report generator embeds this maintained design note in the complete submission.

The held-out results favor a simple benchmark for LA home values. At every horizon, carrying the last observed value forward had lower test RMSE than the model chosen using earlier validation dates. At six months, the benchmark missed by $27,766 on RMSE, compared with $30,160 for the selected level AR(1). Several alternatives did better on those later dates. The two-page [results memo](../output/forecasting/week1/week1_memo.pdf) separates those retrospective comparisons from the models that could have been selected at the time.

## Read the submission

| Question | File |
|---|---|
| Which sources, geographies and periods qualified? | [Data inventory](../output/forecasting/week1/inventory.csv) |
| What outcome, predictors, coefficients, training months and sample sizes did each fit use? | [Regression tables](../output/forecasting/week1/regressions.csv) |
| What did each model predict, and what happened? | [Origin-by-origin forecasts and signed errors](../output/forecasting/week1/forecasts.csv) |
| How accurate was each specification at 3, 6 and 12 months? | [Validation and test accuracy](../output/forecasting/week1/accuracy.csv) |
| How did the choices made on validation perform on the later test? | [Selected models and held-out scores](../output/forecasting/week1/selections.csv) |
| How were the estimates constructed? | [Forecast data dictionary](../metadata/week1_forecast_dictionary.csv) and [run transformation log](../output/forecasting/week1/transformation_log.csv) |

The figures compare observed and predicted levels at the same target dates. Each panel holds the horizon fixed while the origin moves forward by one month. [Home values](../output/forecasting/week1/zhvi_forecasts.svg) · [Rents](../output/forecasting/week1/zori_smoothed_sa_forecasts.svg) · [Permits](../output/forecasting/week1/permits_total_forecasts.svg) · [Inventory](../output/forecasting/week1/active_inventory_forecasts.svg)

## Reproduce the run

Start with a provisioned working project containing `database/housing.duckdb`, its active facts and raw manifest. Git carries the code, configuration and reviewed submission. The ignored database, source archives, and local build products require separate provisioning and a local rebuild or refresh. The forecast command reads the database and writes analysis files under `output/forecasting/week1/`. [Database build and refresh guide](database_build_and_refresh.md)

From the project root, activate the project environment and run:

```text
python -m pip install -r requirements.txt
python run_forecasting.py
python -m pytest
python run_pipeline.py --stage validate
```

The command also generates a local `observations.csv` with one row per series/month, source vintage, retrieval timestamp, filenames, checksum, archive location and provider flags. Its geography IDs remain strings. A repeat using the same database and configuration reproduces the model estimates. Refreshing the database can revise inputs and results. Each execution records its own timestamp in the run log.

`run_forecasting.py` generates the complete Markdown answers. To rebuild the Word
and PDF editions, run `scripts/report_authoring/build_week1_answers.py` with a
Python environment containing `python-docx`, plus Node and Sharp for the SVG charts:

```text
python scripts/report_authoring/build_week1_answers.py --node <node-executable> --node-modules <node_modules-directory> --render-script <documents-skill>/render_docx.py --renderer-bin <renderer-directory>
```

The authoring run used the bundled document runtime. On Windows, the renderer
directory was `scripts/report_authoring`, containing the local Microsoft Word
bridge compiled from `SofficeWordBridge.cs`; Word must be installed for that bridge.
The compiled bridge and bundled dependencies are local prerequisites and are not
transferred by Git. Omitting the render arguments creates the DOCX only. Rendered
page images remain under `tmp/pdfs/week1_answers/render` for visual inspection.

## Evaluation dates and model selection

- The **origin** is the last month of observations allowed into a fit. A **horizon** of six months gives a June 2023 target for a December 2022 origin. Fitted values refer to months used to estimate the model. The reported forecasts iterate past the origin, substituting predicted values for lags that have yet to be observed.
- The main training window begins in January 2015. The 13 validation origins run from December 2020 through December 2021. Their last 12-month outcome is December 2022. Test origins begin in December 2022 and end in May 2025, giving 30 test errors per horizon. The final 12-month target is May 2026, the last month available for LA inventory.
- Choose the lowest validation RMSE separately for each outcome, geography, target definition and horizon. Freeze that choice before evaluating it on later targets. Refit coefficients through each origin as observations accumulate. These are expanding windows. A fixed-length rolling window would also move its start forward.
- The available series have complete monthly histories across the study interval. The code checks month keys, frequency, geography, source lineage, fit completeness, signed errors, and matching dates across candidates. The inventory records the state-level search. The project has no monthly California ZHVI series, so the comparison uses LA, Orange, Riverside and San Diego counties.

## Model interpretation

For the level model, current price is the dependent variable and last month's price is its predictor. In the growth models, the dependent variable is 100 times the monthly change in log price or log rent. AR(3) uses each of the last three monthly changes with a separate coefficient. Each fit includes an intercept. A 12-month forecast uses 12 recursively predicted changes and compounds them from the last observed level. A trending price level can yield a high in-sample fit even when the model's out-of-sample dollar errors are large.

The lag comparison fits AR(1), AR(3) and AR(6) on identical response months after a six-lag warm-up. The window experiment keeps LA price growth AR(3), its forecast origins and its targets fixed, changing only the start to January 2010, 2015 or 2019. The shorter window gives pandemic-era changes a larger share of the estimation sample. Shifts in housing demand or mortgage rates could affect estimated persistence, but these univariate regressions cannot identify the cause. The additional benchmark is a driftless ARIMA(0,1,1) on log prices, fitted by conditional least squares with an invertible moving-average coefficient. Its future expected log change is zero after the first forecast month.

For example, at the December 2022 origin the main LA AR(3) fit used 89 monthly growth responses from August 2015 through December 2022. Its intercept was 0.162 percentage points, and the coefficients on the first, second and third lags were 1.486, −0.705 and −0.037. Starting from the December value of $837,651 and compounding six predicted monthly changes gave a June 2023 forecast of $874,510. The revised June value was $811,396, so the forecast overshot by $63,114. The accuracy table averages errors over 30 test origins to put that individual miss in context.

For permits, `level` measures a single month's published flow. For `mean3`, every candidate predicts the same trailing three-month mean, including the model that predicts raw monthly flows before averaging. Adjacent averages share two months, which mechanically raises persistence. BPS flags provider imputation in 317 of 318 LA months. Beginning in January 2022 monthly tabulation expanded from sampled offices to all permit offices. Census began updating the universe annually in January 2023. [Census Building Permits Survey methodology](https://www.census.gov/construction/bps/methodology.html)

Inventory uses Redfin's unadjusted **county inventory** stock. The seasonal benchmark uses the level in the target calendar month one year earlier. Zillow's county ZHVI and active all-homes-plus-multifamily ZORI series are already smoothed and seasonally adjusted. Calendar-month comparisons therefore reflect differences in both the series and provider processing.

Forecast error is forecast minus actual. MAE is the average absolute error, and RMSE puts extra weight on large misses. Both retain the target's units. MAPE permits price and rent errors to be compared across different dollar scales. Overlapping forecast horizons make successive errors dependent. The use of revised histories and provider smoothing makes this a **retrospective backtest**. Reconstructing each historical information set would require publication dates and as-released vintages. [Forecasting: Principles and Practice, Chapter 5](https://otexts.com/fpp3/toolbox.html)
