# Implications for the full forecasting project

The Week 1 exercises establish empirical baselines for a monthly county forecasting system. They support a small, outcome-specific set of starting models and identify the comparisons required before extending the system. The specifications below are proposed for the next development stage. The existing test period has informed these choices and therefore serves as development evidence for subsequent model selection.

## Lessons from the exercises

For LA prices with the common 2015 start, moving from AR(1) to AR(3) reduced six-month RMSE by 26.4%; moving from AR(3) to AR(6) provided a further 1.7% reduction. This supports AR(3) as a parsimonious starting point and AR(6) as a challenger. Rent forecasts favored AR(1), and inventory forecasts favored level AR(1). The appropriate lag structure therefore depends on the outcome and transformation.

Training fit did not reliably identify the strongest forecasts. The recent price window had smaller training residuals but worse test errors, and the models selected on earlier validation did not consistently retain their advantage in the later period. More parameters, more recent data, and smoother targets each require evaluation on matched future outcomes. The permit exercise also demonstrates that an apparent improvement can come from changing the target rather than improving the forecasting method.

## Starting specifications and growth measures

| Outcome | Starting model | Outcome definition | Comparisons to retain |
| --- | --- | --- | --- |
| County home values | Growth AR(3) | Monthly ZHVI log change | AR(1), AR(6), no change, log ARIMA(0,1,1) |
| County rents | Growth AR(1) | Monthly active smoothed-SA ZORI log change | AR(3), AR(6), no change |
| County inventory | Level AR(1) | Monthly county inventory stock, in homes | Level AR(3), positive log-growth models, no change, seasonal naive |
| Monthly permits | Level AR(1) | Raw monthly permitted housing units | No change and seasonal naive |
| Smoothed permit activity | Mean AR(1) | Trailing three-month mean of permits | Raw forecasts averaged to the identical target, no change, seasonal naive |

For a positive price or rent index P_t, the proposed primary growth measure remains g_t = 100 × [ln(P_t) − ln(P_(t−1))]. It is a monthly log change, approximately the ordinary monthly percentage change. Its exact ordinary percentage equivalent is 100 × [exp(g_t / 100) − 1]. Future growth is estimated by the model, and the level forecast is recovered as P_t × exp[(g_hat_(t+1) + ... + g_hat_(t+h)) / 100]. The origin level must therefore remain available even when the regression outcome is growth.

Year-over-year growth and annualized three-month growth are alternative definitions for separate sensitivity tests. They should not be substituted for monthly log growth without changing the model specification. Year-over-year changes overlap over eleven months, while annualization changes the scale. In the current database, `zhvi_growth_mom` and `inventory_growth_mom` are fractional changes, and `zhvi_momentum_3m_annualized_log_pct` is annualized three-month growth; none is numerically identical to Week 1 monthly log growth.

Mortgage and unemployment rates are already expressed in percent. Their month-to-month differences are percentage-point changes. County employment, when used instead of unemployment, can be expressed as monthly log growth after checking positivity, gaps and seasonality. Inventory and permits retain the outcome-specific definitions in the table; transforming all series with a single rule would discard their stock/flow and seasonal differences.

## A small initial input set

The first multivariable price specification should add a few inputs to the own-growth baseline. Their economic roles motivate testing, but the univariate exercises have not established their incremental predictive value or causal effects.

| Input | Geography and proposed representation | Initial role |
| --- | --- | --- |
| ZHVI history | Same county; latest three observed monthly log changes and the latest level | Core own-history baseline and level reconstruction |
| 30-year mortgage rate | US `mortgage_rate_30y_monthly_average`; test rate level and percentage-point change as separate candidates | Financing-cost control; retain its national identity |
| Unemployment rate | Same-county `unemployment_rate`; test percentage-point change against a rate-level specification | Local labor-market control; consider employment growth as an alternative |
| Active inventory | Same-county `active_inventory`; test log change as a price-model input while retaining its separate level forecasting baseline | Market-availability control |

Begin with the price baseline, then add financing, labor-market and inventory inputs in successive comparisons. For additional inputs, start with one available monthly observation and compare a three-lag block if validation supports the extra parameters. An AR(3) result for prices does not imply that every explanatory variable needs three lags, and the univariate AR(6) result does not justify six lags throughout a larger system.

County homes sold and rent growth are subsequent candidate inputs. Monthly permits are a candidate for LA, where the current facts contain the required series; comparable county coverage must be verified before extending that input to other counties. These groups should be added individually or in small, prespecified combinations. Annual income and population, and overlapping ACS five-year estimates, should remain outside the first monthly model until their frequency and publication-availability treatment is specified.

The recommendations are supported at county geography. A later ZIP or ZCTA extension requires separate coverage and forecast validation; county observations cannot substitute for missing ZIP outcomes. Redfin ZIP Key Metrics are overlapping rolling-three-month observations and require a distinct frequency and target specification.

## Model form, estimation sample and information timing

A small autoregressive distributed-lag model is the proposed first multivariable extension: regress monthly price growth on its own lags and a limited set of lagged controls. Compare it with the unchanged own-history and naive benchmarks. Check residual dependence and the stability of the transformed series. If residual autocorrelation remains, a dynamic regression with ARMA errors is a candidate extension. A small VAR with one versus three lags is a separate comparison when jointly forecasting the outcomes is useful. Five variables in VAR(3) require 16 coefficients per equation including the intercept; VAR(6) requires 31. This parameter cost motivates a small initial system, with shrinkage considered if the input set expands.

Use January 2015 as the common starting sample for the first combined monthly system, subject to each input's actual coverage. Price-only models should retain 2010 and 2015 starts as comparison candidates: the 2010-start AR(3) performed better in the exercises. County Redfin inventory begins in 2012, and the active rent index begins in 2015, so a longer sample cannot be imposed on every input set. Whenever adding an input shortens the usable sample, refit the benchmark on those same months. Otherwise a performance difference could reflect the dates rather than the added information.

All predictors must be available at the forecast issuance date. Observation month, retrieval timestamp and historical publication date are different concepts. Source histories in the present database are revised, and most series do not have observation-specific historical release dates. Consequently, additional backtests remain retrospective until an availability and vintage calendar is constructed. A one-month lag alone does not prove that an observation was available at the forecast origin.

For a model that requires future mortgage rates, labor conditions or inventory, those paths must be forecast or supplied as explicit scenarios. Realized future controls cannot enter historical forecasts. A useful comparison is a direct horizon-specific model with target G_(t,h) = 100 × [ln(P_(t+h)) − ln(P_t)] and only origin-available predictors. At each estimation cutoff, its training rows must have their entire future target already observed. This direct approach and the existing recursive forecasts should be compared on identical targets. Guidance on dynamic regression and unknown future predictors is provided in [Forecasting: Principles and Practice, Chapter 10](https://otexts.com/fpp3/dynamic-regression.html).

## Data readiness and the next evaluation

The proposed mortgage, county inventory and county labor inputs cover the main Week 1 origin period through May 2025. Later data require additional checks: county labor observations are missing in October 2025; the archived county inventory and sales series end in May 2026; and permit estimates contain substantial provider imputation and a 2022 coverage change. Missing observations and source breaks must remain explicit. The mortgage cleaner correctly calculates the named monthly mean and median, but their dictionary smoothing labels are reversed and should be reconciled before the multivariable input contract is finalized.

The next evaluation should freeze a small candidate set and compare models by outcome and horizon using time-ordered validation. Newly added inputs, lag counts, transformations, regularization and training windows must be chosen within the development process. Use an untouched later period where feasible, or freeze the specifications for prospective evaluation; the already-inspected Week 1 test must not be presented as new independent confirmation of subsequent choices.

To estimate the useful forecasting range, extend the horizon grid, initially to monthly horizons from 1 through 36 where the data support adequate evaluation. Report origin counts and target calendars, use common origins for horizon comparisons where feasible, and avoid comparing different samples as if only the horizon had changed. Assess benchmark-relative gains, percentage errors and calibrated prediction intervals. The maximum useful horizon should be defined by the project's acceptable error and evidence of stable forecasting performance, rather than by the longest horizon the software can produce.

The recommended first build is therefore a county price-growth AR(3) baseline with separately tested financing, labor-market and inventory inputs, accompanied by the outcome-specific rent, inventory and permit baselines above. Retain simple benchmarks and treat larger systems, longer lag structures and long-horizon forecasts as candidates requiring further evidence.
