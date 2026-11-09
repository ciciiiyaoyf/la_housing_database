"""Build the seven numbered exercise responses from the verified result tables."""

import json
from pathlib import Path

import pandas as pd

from src.forecasting.report import HORIZONS, LABELS, LA, _growth, _main_start, _point, _seasonality


GROWTH_MODELS = ("growth_ar1", "growth_ar3", "growth_ar6")


def _table(headers, rows):
    def line(values):
        return "| " + " | ".join(str(value).replace("|", "/") for value in values) + " |"

    return "\n".join([line(headers), line(["---"] * len(headers)), *map(line, rows)]) + "\n"


def _series(frame, variable="zhvi", geo=LA, target="level"):
    return frame[(frame.geo_id == geo) & (frame.variable_id == variable) & (frame.target == target)]


def _accuracy_table(scores, specs):
    rows = []
    for model, start, label in specs:
        row = [label]
        for horizon in HORIZONS:
            score = _point(scores, model, horizon, start)
            row.extend([f"{score.mae:,.2f}", f"{score.rmse:,.2f}"])
        rows.append(row)
    return _table(["Specification", "3m MAE", "3m RMSE", "6m MAE", "6m RMSE", "12m MAE", "12m RMSE"], rows)


def _coefficients(fits):
    rows = []
    for row in fits.itertuples():
        for term, coefficient in json.loads(row.coefficients).items():
            rows.append([LABELS[row.model], row.train_start, term, f"{coefficient:.6f}"])
    return _table(["Model", "Raw start", "Term", "Coefficient"], rows)


def _fit_table(fits):
    return _table(
        ["Model", "Raw start", "First fitted month", "Last fitted month", "N", "Training RMSE", "R squared"],
        [[LABELS[r.model], r.train_start, r.train_first, r.train_last, r.nobs,
          f"{r.train_rmse:.4f}", f"{r.r_squared:.4f}" if pd.notna(r.r_squared) else "N/A"] for r in fits.itertuples()],
    )


def _winner_labels(scores, models):
    return [
        LABELS[min((_point(scores, model, h) for model in models), key=lambda row: row.rmse).model]
        for h in HORIZONS
    ]


def _winner_text(winners):
    if len(set(winners)) == 1:
        return f"{winners[0]} at all three horizons"
    return f"{', '.join(winners)} at 3, 6 and 12 months, respectively"


def _rmse_reduction(model_rmse, reference_rmse):
    """Report relative error reduction; it is not a share of explained variation."""
    return 100 * (1 - model_rmse / reference_rmse) if reference_rmse > 0 else float("nan")


def _interpretation_answers(test, snapshot, selections, observations):
    price = _series(test)
    rent = _series(test, "zori_smoothed_sa")
    stock = _series(test, "active_inventory")
    permits = _series(test, "permits_total")
    means = _series(test, "permits_total", target="mean3")
    windows = _series(snapshot).query("model == 'growth_ar3'").sort_values("train_start")
    chosen = _series(selections).set_index("horizon")

    def reduction(frame, model, horizon, benchmark="naive"):
        return _rmse_reduction(_point(frame, model, horizon).rmse, _point(frame, benchmark, horizon).rmse)

    def rmse_row(frame, model, label):
        return [label, *[f"{_point(frame, model, h).rmse:,.2f}" for h in HORIZONS]]

    parts = [
        "## Final interpretation questions\n",
        "### 1. How much of future housing-market behavior can be predicted from the variable’s own past?\n",
        "The exercises identify useful predictive information in housing-market histories, but "
        "do not establish a maximum useful forecasting horizon. LA price-growth dynamics provide "
        "their strongest benchmark improvement at short horizons, while some rent and inventory "
        "specifications retain an advantage at one year. Forecasts can be calculated beyond one "
        "year, but their usefulness requires further evaluation across longer horizons and a "
        "specified tolerance for forecast error. The evidence below describes the range tested "
        "and the stability of the observed forecasting gains.\n",
        "**Measurement of predictive value.** Forecast errors on later observations are compared with the "
        "no-change forecast, which carries the last observed level forward. RMSE is in the outcome's "
        "units and gives more weight to large misses. In the following tables, RMSE reduction = "
        "100 × (1 − model RMSE / benchmark RMSE). Positive values indicate improvement; negative "
        "values indicate larger errors. A 36% RMSE reduction means a 36% smaller error measure, "
        "not that 36% of future market behavior has been explained. Training R squared likewise "
        "describes fit to known outcomes, not a percentage of future behavior that we can predict. "
        "This distinction follows the treatment of held-out forecast accuracy in "
        "[Forecasting: Principles and Practice, Section 5.8](https://otexts.com/fpp3/toolbox.html#evaluating-point-forecast-accuracy).\n",
        "**LA home values: useful short-horizon dynamics, weaker evidence at one year.** "
        "For the main 2015-start comparison, the following are test results for growth AR(6), "
        "the strongest of the three tested growth-lag models in this run.\n",
        _table(["Horizon", "AR(6) RMSE, USD", "No-change RMSE, USD", "RMSE reduction"],
               [[h, f"{_point(price, 'growth_ar6', h).rmse:,.0f}",
                 f"{_point(price, 'naive', h).rmse:,.0f}", f"{reduction(price, 'growth_ar6', h):+.1f}%"]
                for h in HORIZONS]),
        f"At three months, the reduction is {reduction(price, 'growth_ar6', 3):.1f}%; at six months "
        f"it is {reduction(price, 'growth_ar6', 6):.1f}%. At twelve months, the growth-model error is "
        f"{-reduction(price, 'growth_ar6', 12):.1f}% larger than no change. Within this comparison, "
        "estimating recent growth dynamics helps most at three months and provides a smaller gain "
        "at six months. Extending the same growth dynamics to one year does not improve the forecast. "
        f"The log ARIMA benchmark has twelve-month RMSE ${_point(price, 'arima011', 12).rmse:,.0f}, "
        f"only {reduction(price, 'arima011', 12):.1f}% below no change. Thus the data do not show "
        "a substantial, broadly shared one-year improvement for the fitted LA price models.\n",
        "**The distinction between an observed winner and a usable selection rule matters.** "
        "The AR(6) comparison above is descriptive: its relative success is observed on the test "
        "period. The specifications chosen using only earlier validation results perform as follows.\n",
        _table(["Horizon", "Validation-selected model", "Selected test RMSE, USD", "RMSE reduction vs no change"],
               [[h, LABELS[chosen.loc[h, "model"]], f"{chosen.loc[h, 'test_rmse']:,.0f}",
                 f"{_rmse_reduction(chosen.loc[h, 'test_rmse'], _point(price, 'naive', h).rmse):+.1f}%"]
                for h in HORIZONS]),
        "In this run, the validation-selected LA price models lose to no change at all three "
        "horizons. This means the process did not reliably identify in advance the model that would "
        "perform best in the later market period. These results do not establish a reliably useful "
        "six-month forecasting range. No change itself uses the variable's "
        "past, so failing to beat it does not mean historical information is useless.\n",
        "**The useful horizon also depends on the outcome.** The fixed-model test comparisons "
        "give the following reductions relative to each outcome's own no-change forecast. These "
        "are descriptive comparisons, not the performance of the validation-selected procedure.\n",
        _table(["Outcome and model", "3m RMSE reduction", "6m RMSE reduction", "12m RMSE reduction"],
               [[label, *[f"{reduction(frame, model, h):+.1f}%" for h in HORIZONS]]
                for frame, model, label in [(rent, "growth_ar1", "Rent: growth AR(1)"),
                                            (stock, "level_ar1", "Inventory: level AR(1)")]]),
        "These rent and inventory models improve on no change even at twelve months, so six months "
        "is not a universal cutoff across housing variables. Our evidence is limited to the tested "
        "3-, 6- and 12-month horizons. We have not established accuracy beyond twelve months, an "
        "exact cutoff between the tested horizons, or a single percentage of predictable behavior. "
        "Whether an error is tolerable also depends on the purpose of the forecast.\n",
        "### 2. How sensitive are the results to lag length, training period, and prediction horizon?\n",
        "**Lag length: most of the LA price gain comes from moving from one to three lags.** "
        "Hold the raw start at January 2015 and use identical response months and forecast origins. "
        "The table reports RMSE in dollars for recovered home-value levels.\n",
        _table(["Growth specification", "3m RMSE", "6m RMSE", "12m RMSE"],
               [rmse_row(price, model, LABELS[model]) for model in GROWTH_MODELS]),
        f"At six months, moving from AR(1) to AR(3) lowers RMSE by "
        f"{_rmse_reduction(_point(price, 'growth_ar3').rmse, _point(price, 'growth_ar1').rmse):.1f}%. "
        f"Moving from AR(3) to AR(6) provides a further "
        f"{_rmse_reduction(_point(price, 'growth_ar6').rmse, _point(price, 'growth_ar3').rmse):.1f}% reduction. "
        f"The additional AR(3)-to-AR(6) reduction is "
        f"{_rmse_reduction(_point(price, 'growth_ar6', 3).rmse, _point(price, 'growth_ar3', 3).rmse):.1f}% "
        f"at three months and {_rmse_reduction(_point(price, 'growth_ar6', 12).rmse, _point(price, 'growth_ar3', 12).rmse):.1f}% "
        "at twelve months. More lags help within this family, but the incremental gains after three "
        "lags are small, and none of these main-window growth models beats no change at twelve months. "
        "A six-lag model uses six past monthly growth rates; it is not limited to forecasting six months ahead.\n",
        "**Training period: more recent data do not automatically yield better forecasts.** "
        "Keep the AR(3) specification and evaluation dates fixed. The sample sizes and training "
        "RMSE below are the December 2022 fit snapshot; training RMSE is in percentage points of "
        "monthly log growth, while test RMSE is in dollars.\n",
        _table(["Raw start", "Fitted N", "Training RMSE, pp", "6m test RMSE, USD", "12m test RMSE, USD"],
               [[r.train_start, r.nobs, f"{r.train_rmse:.3f}",
                 f"{_point(price, 'growth_ar3', 6, r.train_start).rmse:,.0f}",
                 f"{_point(price, 'growth_ar3', 12, r.train_start).rmse:,.0f}"] for r in windows.itertuples()]),
        f"Extending the start from 2015 to 2010 lowers six-month RMSE by "
        f"{_rmse_reduction(_point(price, 'growth_ar3', 6, '2010-01').rmse, _point(price, 'growth_ar3', 6, '2015-01').rmse):.1f}%; "
        f"shortening it from 2015 to 2019 raises that error by "
        f"{-_rmse_reduction(_point(price, 'growth_ar3', 6, '2019-01').rmse, _point(price, 'growth_ar3', 6, '2015-01').rmse):.1f}%. "
        "The recent window has the smallest training residuals but larger forecast errors. "
        "Because these windows contain different response months, their training errors describe "
        "different samples; the matched test errors provide the fair forecast comparison.\n",
        "The coefficients also change. At the common December 2022 origin, the first-lag "
        f"coefficients for starts {', '.join(windows.train_start)} are "
        f"{', '.join(format(json.loads(r.coefficients)['log_growth_pct_lag_1'], '.3f') for r in windows.itertuples())}, "
        "and the second-lag coefficients are "
        f"{', '.join(format(json.loads(r.coefficients)['log_growth_pct_lag_2'], '.3f') for r in windows.itertuples())}. "
        "The recent fit estimates a stronger immediate response and a larger subsequent reversal. "
        "It contains fewer observations and puts more weight on pandemic-era and rate-transition "
        "conditions. Those differences can change fitted persistence, but these regressions do not "
        "identify which economic shock caused the coefficient changes.\n",
        "**Prediction horizon: the dollar errors grow substantially and benchmark rankings change.** "
        f"For the main AR(3), RMSE rises from ${_point(price, 'growth_ar3', 3).rmse:,.0f} at three months "
        f"to ${_point(price, 'growth_ar3', 6).rmse:,.0f} at six months and "
        f"${_point(price, 'growth_ar3', 12).rmse:,.0f} at twelve months. The twelve-month error is "
        f"{_point(price, 'growth_ar3', 12).rmse / _point(price, 'growth_ar3', 3).rmse:.2f} times the "
        "three-month error. Recursive forecasts use predicted growth as future lag inputs, so "
        "errors can accumulate; changes in market dynamics also matter. Models share target dates "
        "within each horizon. Across horizons, the target calendar shifts, so the observed increase "
        "reflects both distance ahead and the market months being forecast.\n",
        "The same model is therefore not uniformly preferable across horizons or estimation "
        "periods. Earlier validation selected level AR(1) at three and six months and growth "
        "AR(1) at twelve months, but those selections underperformed no change on the later test. "
        "The main practical finding is sensitivity of the model choice itself, not merely a gradual "
        "increase in error as forecasts extend farther ahead.\n",
        "### 3. Do the same simple models work across home prices, rents, permits, inventory, and California counties?\n",
        "**The modeling framework transfers; the best specification and its advantage do not.** "
        "Each outcome can be studied with univariate lag models and common backtesting rules, "
        "but the suitable transformation, lag length, benchmark and target definition differ. "
        "Dollar errors for home values, monthly rent errors, permit flows and inventory counts "
        "must be interpreted in their own units.\n",
        f"**Prices versus rents.** Among the growth models with the common January 2015 start, LA price accuracy favors "
        f"{_winner_text(_winner_labels(price, GROWTH_MODELS))}, while rent accuracy favors "
        f"{_winner_text(_winner_labels(rent, GROWTH_MODELS))}. "
        f"At six months, rent AR(1) has RMSE ${_point(rent, 'growth_ar1').rmse:.2f} per month, "
        f"compared with ${_point(rent, 'growth_ar3').rmse:.2f} for AR(3), "
        f"${_point(rent, 'growth_ar6').rmse:.2f} for AR(6), and ${_point(rent, 'naive').rmse:.2f} "
        "for no change. Training fit and test accuracy answer different questions; the winning "
        "rent lag length is stated above. Using the same AR(3) specification and dates, six-month MAPE is "
        f"{_point(rent, 'growth_ar3').mape_pct:.2f}% for rents and {_point(price, 'growth_ar3').mape_pct:.2f}% "
        "for home values. Rent levels are more predictable on this proportional-error metric; "
        "this is not a comparison of explained monthly-growth variance. Both indexes are "
        "provider-smoothed and seasonally adjusted.\n",
        "**Permits: a volatile monthly flow and a target-definition issue.** At six months, raw "
        f"permit AR(1) has RMSE {_point(permits, 'level_ar1').rmse:.1f} units, compared with "
        f"{_point(permits, 'naive').rmse:.1f} for no change and {_point(permits, 'seasonal_naive').rmse:.1f} "
        "for seasonal naive. On the common trailing-three-month-mean target, the direct mean "
        f"AR(1) has RMSE {_point(means, 'mean3_ar1').rmse:.1f}, forecasting raw flows then averaging "
        f"has {_point(means, 'raw_ar1_mean3').rmse:.1f}, and seasonal naive has "
        f"{_point(means, 'seasonal_naive_mean3').rmse:.1f}. The direct mean model does better at "
        "three months, whereas the raw-then-average method does slightly better at six and twelve. "
        "The lower error of an averaged series cannot itself establish a better method: adjacent "
        "means share two months, creating persistence and reducing volatility. Provider imputation "
        "and the 2022 coverage change also qualify the economic interpretation.\n",
        "**Inventory: a level model works better than these growth models.** Level AR(1) has "
        f"six-month RMSE {_point(stock, 'level_ar1').rmse:,.0f} homes, compared with "
        f"{_point(stock, 'level_ar3').rmse:,.0f} for level AR(3), "
        f"{_point(stock, 'growth_ar1').rmse:,.0f} for growth AR(1), "
        f"{_point(stock, 'growth_ar3').rmse:,.0f} for growth AR(3), and "
        f"{_point(stock, 'seasonal_naive').rmse:,.0f} for seasonal naive. "
        "The level AR(1) also has the lowest fitted-model test RMSE at three and twelve months. "
        "Inventory is an unadjusted stock with pronounced seasonal movement, but repeating last "
        "year's calendar month still performs poorly when the overall level changes. The presence "
        "of seasonality does not guarantee that seasonal naive is the strongest benchmark.\n",
        "**Counties: short-horizon transfer is stronger than one-year transfer.** The following "
        "table compares the same 2015-start growth AR(3) with each county's no-change forecast. "
        "Relative RMSE reductions put counties with different price levels on a comparable "
        "benchmark-relative scale.\n",
    ]
    county_rows = []
    for geo, group in test[(test.variable_id == "zhvi") & test.geo_id.str.startswith("COUNTY_")].groupby("geo_id"):
        name = observations.loc[observations.geo_id == geo, "geo_name"].iloc[0].replace(" County", "")
        county_rows.append([name, *[f"{reduction(group, 'growth_ar3', h):+.1f}%" for h in HORIZONS]])
    parts += [
        _table(["County", "3m RMSE reduction", "6m RMSE reduction", "12m RMSE reduction"], county_rows),
        "AR(3) beats AR(1) in all four counties at three and six months. At twelve months, "
        "Riverside favors AR(1), while the other three still favor AR(3). However, being the "
        "better AR model does not guarantee beating the benchmark: only Orange County's AR(3) "
        "improves on no change at twelve months. The same lag structure therefore has more "
        "consistent benchmark gains at short horizons than at one year. LA's high volatility "
        "and Riverside's low volatility align with their six-month proportional errors, but "
        "Orange and San Diego reverse that ranking. Four counties provide descriptive evidence "
        "rather than a general volatility-error law. Statewide ZHVI is unavailable in this run; "
        "an unweighted average of these counties is not a California index.\n",
        "**Can the observed winning specification be selected in advance?** The following "
        "summarizes the actual validation-selection procedure. It guards against treating the "
        "best model observed after the test as a model we would necessarily have chosen beforehand.\n",
    ]
    selection_rows = []
    for variable, frame, label in [("zhvi", price, "Prices"), ("zori_smoothed_sa", rent, "Rents"),
                                   ("active_inventory", stock, "Inventory")]:
        selected = _series(selections, variable).set_index("horizon")
        for h in HORIZONS:
            row = selected.loc[h]
            selection_rows.append([label, h, LABELS[row.model],
                                   f"{_rmse_reduction(row.test_rmse, _point(frame, 'naive', h).rmse):+.1f}%"])
    parts += [
        _table(["Outcome", "Horizon", "Validation-selected model", "Test RMSE reduction vs no change"], selection_rows),
        "For rents, validation selections improve slightly at three and six months but lose at "
        "twelve; inventory selections lose at three and six months and tie the benchmark at "
        "twelve because no change was selected. Useful fixed-model relationships therefore "
        "coexist with instability in model selection. The evidence supports outcome- and "
        "horizon-specific evaluation, keeping simple benchmarks in the candidate set.\n",
        "**Scope of the conclusions.** These are point-forecast comparisons from 30 test origins "
        "per horizon. Adjacent multi-month errors overlap. We have not established statistical "
        "significance of small RMSE differences or calibrated prediction intervals. Revised "
        "provider histories and smoothing make the exercise retrospective. The results describe "
        "how the tested methods performed in this sample; they do not guarantee future accuracy "
        "or identify causal economic mechanisms.\n",
        "Full numerical evidence is retained in [accuracy.csv](accuracy.csv), [regressions.csv](regressions.csv), "
        "[forecasts.csv](forecasts.csv), [selections.csv](selections.csv) and [transformation_log.csv](transformation_log.csv).\n",
    ]
    return parts


def write_exercise_report(output_dir: Path):
    """Write a readable answer to every task using the published fit and score rows."""
    output_dir = Path(output_dir)
    scores = pd.read_csv(output_dir / "accuracy.csv")
    fits = pd.read_csv(output_dir / "regressions.csv")
    predictions = pd.read_csv(output_dir / "forecasts.csv")
    selections = pd.read_csv(output_dir / "selections.csv")
    observations = pd.read_csv(output_dir / "observations.csv")
    inventory = pd.read_csv(output_dir / "inventory.csv", dtype={"geo_id": str})
    test = scores[scores.split == "test"]
    price = _series(test)
    start = _main_start(price)
    test_origins = sorted(predictions.loc[predictions.split == "test", "origin"].unique())
    val_origins = sorted(predictions.loc[predictions.split == "validation", "origin"].unique())
    snapshot = fits[fits.origin == test_origins[0]]
    initial = fits[fits.origin == val_origins[0]]
    price_fits = _series(snapshot)
    lag_fits = price_fits[(price_fits.train_start == start) & price_fits.model.isin(GROWTH_MODELS)]
    specs = [(model, start, LABELS[model]) for model in GROWTH_MODELS]
    fit_winner = LABELS[lag_fits.loc[lag_fits.train_rmse.idxmin(), "model"]]
    lag_winners = _winner_labels(price, GROWTH_MODELS)
    parts = [
        "# Week 1 housing forecasting exercise answers\n",
        "This submission follows all seven numbered exercises in the supplied teaching module. "
        "Each exercise includes the requested model, numerical results and interpretation. "
        "The three final interpretation questions are answered after Exercise 7. "
        "All numbers come from the local housing database and the reproducible Week 1 run.\n",
        "## Data and evaluation dates\n",
        f"The primary raw training window begins in {start}. We use {len(val_origins)} monthly validation origins "
        f"from {val_origins[0]} through {val_origins[-1]} and {len(test_origins)} later test origins from "
        f"{test_origins[0]} through {test_origins[-1]}. Validation outcomes through the longest horizon are "
        "available by the first test origin. The 3-, 6- and 12-month targets are identical across "
        "candidate models and eligible counties. Tables below report test errors unless labeled validation.\n",
        _table(["Geography", "Variable", "Observed first month", "Observed last month", "Missing months", "Study status"],
               [[r.geo_name, r.variable_id, str(r.first_month)[:7] if pd.notna(r.first_month) else "Unavailable",
                 str(r.last_month)[:7] if pd.notna(r.last_month) else "Unavailable",
                 int(r.missing_months) if r.status == "available" else "N/A",
                 r.evaluation_status] for r in inventory.itertuples()]),
        "We forecast log growth and recover price/rent levels by compounding the predicted changes. "
        "Consequently, MAE and RMSE in the price and rent tables are in USD and USD per month, respectively. "
        "They measure errors in recovered levels. MAPE compares errors relative to each observed level. "
        "These retrospective backtests use revised, provider-smoothed histories. Publication dates and "
        "historical release vintages have not been reconstructed.\n",
        "The introductory level AR(1) estimates P_t = a + b P_(t−1) + e_t; a trending or nonstationary "
        "price level can give a high fitted R squared without good forecasts. The growth AR(1) instead "
        "models g_t = a + b g_(t−1) + e_t. Recover a horizon-h level as "
        "P_hat_(t+h) = P_t × exp[(g_hat_(t+1) + … + g_hat_(t+h)) / 100]. "
        "Lag length counts past predictors; horizon counts months ahead. For example, AR(3) can produce "
        "a 12-month forecast by recursion. More lags can capture dynamics but estimate more parameters "
        "from the available sample. These introductory models and comparisons are included below.\n",
        "## Exercise 1  Lag length comparison\n",
        "**1.1 Estimate AR(1), AR(3), and AR(6) for monthly LA County ZHVI log growth using a consistent sample.**\n",
        "Let P_t be LA County ZHVI and g_t = 100 × [ln(P_t) − ln(P_(t−1))]. "
        "For AR(p), the LHS is g_t and the RHS contains an intercept and each separate lag "
        "g_(t−1), …, g_(t−p), for p equal to 1, 3 and 6. All three models retain six growth "
        "observations for initialization so that their fitted response months coincide.\n",
        "**1.2 Record the LHS, every RHS lag, coefficients, number of observations, and training dates.**\n",
        f"The following regression snapshot uses information through {test_origins[0]}, the first test origin. "
        "Training RMSE is in percentage points of monthly log growth. The coefficients table includes every "
        "estimated RHS term. The complete origin-by-origin fits are in [regressions.csv](regressions.csv).\n",
        _fit_table(lag_fits),
        _coefficients(lag_fits),
        "**1.3 Forecast the same later observations and compare 3-, 6-, and 12-month accuracy.**\n",
        "Recovered home-value accuracy is measured in dollars on the same test dates.\n",
        _accuracy_table(price, specs),
        "**1.4a Which model fits training data best?**\n",
        f"{fit_winner} has the lowest training RMSE in this common-sample snapshot. "
        "The three rows above use identical fitted response months, so their training errors are comparable.\n",
        "**1.4b Does it forecast best?**\n",
        f"Among these three growth models, the lowest test RMSE comes from {_winner_text(lag_winners)}. "
        f"{'The best-fitting model therefore also forecasts best in this comparison.' if all(w == fit_winner for w in lag_winners) else 'The best-fitting model does not lead at every forecast horizon.'} "
        "This is a comparison using realized test outcomes. Exercise 3 separately evaluates the "
        "specifications that earlier validation data selected.\n",
        "**1.4c Does this change by horizon?**\n",
        f"{'The winning lag length stays the same at all three horizons.' if len(set(lag_winners)) == 1 else 'The winning lag length changes with the horizon.'} "
        "The benchmark comparison can still change with horizon. The following table states directly "
        "whether the best growth AR beats no change at each horizon.\n",
        _table(["Horizon", "Best growth AR", "Growth AR RMSE", "No-change RMSE", "Growth AR beats no change"],
               [[h, lag_winners[i],
                 f"{min(_point(price, m, h).rmse for m in GROWTH_MODELS):,.0f}",
                 f"{_point(price, 'naive', h).rmse:,.0f}",
                 "Yes" if min(_point(price, m, h).rmse for m in GROWTH_MODELS) < _point(price, "naive", h).rmse else "No"]
                for i, h in enumerate(HORIZONS)]),
        "## Exercise 2  Window sensitivity\n",
        "**2.1 Keep AR(3), the outcome, and evaluation dates unchanged. Change only the training start.**\n",
        "Hold the LHS, AR(3) lag terms, expanding-window ends and all target dates fixed. "
        "Change only the first raw observation to each of the three dates below. The same seven-month "
        "growth/lag initialization applies to every start.\n",
        "**2.2 How do the fitted coefficients and forecast errors compare across samples?**\n",
    ]
    windows = price_fits[price_fits.model == "growth_ar3"].sort_values("train_start")
    parts += [_fit_table(windows), _coefficients(windows),
              _accuracy_table(price, [("growth_ar3", r.train_start, f"Start {r.train_start}") for r in windows.itertuples()])]
    initial_recent = _series(initial).query("model == 'growth_ar3'").sort_values("train_start").iloc[-1]
    window_errors = [_point(price, "growth_ar3", 6, r.train_start).rmse for r in windows.itertuples()]
    parts += [
        f"Six-month RMSE is {', '.join('$' + format(v, ',.0f') for v in window_errors)} "
        "as the training start moves through the dates shown. "
        f"The first-lag coefficients are {', '.join(format(json.loads(r.coefficients)['log_growth_pct_lag_1'], '.3f') for r in windows.itertuples())}, "
        f"and the second-lag coefficients are {', '.join(format(json.loads(r.coefficients)['log_growth_pct_lag_2'], '.3f') for r in windows.itertuples())}. "
        "The recent window gives a stronger immediate response and a larger subsequent reversal in this run. "
        "Its smaller training error does not translate into the smallest test error.\n",
        "**2.3 Why can periods covering COVID-era dynamics or mortgage-rate changes produce different coefficients?**\n",
        f"The latest window starts in {initial_recent.train_start} "
        f"and has only {initial_recent.nobs} fitted responses at the first validation origin. "
        "Pandemic-era changes in demand, available supply and prices therefore receive greater weight. "
        "Mortgage rates affect borrowing costs and purchasing power, which can change how price growth "
        "persists or reverses. An AR model summarizes those relationships through its lag coefficients. "
        "Changing the period changes the mixture of market conditions being summarized. These univariate "
        "regressions do not identify the separate causal effect of COVID or mortgage rates.\n",
        "**2.4 What is the difference between an expanding and a rolling window?**\n",
        "An expanding window fixes the start and advances the end. "
        "A rolling window advances both boundaries to retain a fixed length.\n",
        "## Exercise 3  Expanding window backtest\n",
        "**3.1 Select an initial training period of at least five years.**\n",
        f"The initial raw window is {start} through {val_origins[0]}, "
        f"{pd.Period(val_origins[0], 'M').ordinal - pd.Period(start, 'M').ordinal + 1} consecutive monthly levels. "
        "It exceeds five years and has no missing months. The log difference and common lag initialization "
        "reduce the number of fitted responses, as recorded in the regression table.\n",
        "**3.2 Estimate AR(1) and forecast 3, 6, and 12 months ahead.**\n",
        f"At the initial {val_origins[0]} origin, retain the monthly levels from {start} through that origin. "
        "Construct only backward differences and lags. Estimate each AR model, predict the next month, "
        "then feed predicted growth into subsequent unavailable lags. Compound each path from the observed "
        "origin level and retain the 3-, 6- and 12-month predictions. "
        "For a concrete example, the first test origin gives the following AR(1) forecasts in dollars.\n",
        _table(["Origin", "Horizon", "Target month", "Forecast", "Actual", "Forecast minus actual"],
               [[r.origin, r.horizon, r.target_date, f"{r.forecast:,.0f}", f"{r.actual:,.0f}", f"{r.error:,.0f}"]
                for r in predictions[(predictions.geo_id == LA) & (predictions.variable_id == "zhvi") &
                                     (predictions.model == "growth_ar1") & (predictions.train_start == start) &
                                     (predictions.origin == test_origins[0])].sort_values("horizon").itertuples()]),
        "**3.3 Advance the origin by one month, re-estimate, and repeat until the test period ends.**\n",
        f"The test loop starts at {test_origins[0]}, advances one calendar month at a time and ends at "
        f"{test_origins[-1]}. It produces {len(test_origins)} forecasts for each model and horizon. "
        "Each new fit uses the expanded history through its own origin.\n",
        "**3.4 Apply the same procedure to AR(3), AR(6), and a naive benchmark.**\n",
        "AR(3) and AR(6) use the same response months and forecast origins as AR(1). "
        "The no-change benchmark predicts the last observed home value at every horizon. "
        "All four are scored on identical observed targets.\n",
        "**3.5 Plot predicted versus actual home values and calculate MAE and RMSE separately by horizon.**\n",
        "For each horizon, error = forecast − actual, MAE = mean absolute error, and "
        "RMSE = square root of mean squared error. The same procedure covers AR(1/3/6) and no change. "
        "The table below contains the requested horizon-specific scores, plus the introductory benchmarks.\n",
        _accuracy_table(price, [(model, start, LABELS[model]) for model in (*GROWTH_MODELS, "naive", "seasonal_naive", "level_ar1", "arima011")]),
        "The level AR(1) directly regresses P_t on an intercept and P_(t−1). The introductory "
        "ARIMA(0,1,1) uses log prices, no drift, and a moving-average term in the differenced series. "
        "The seasonal benchmark repeats the corresponding month one year earlier.\n",
        f"The benchmark fit snapshot below also uses the {test_origins[0]} origin. Level AR(1) training "
        "RMSE is in dollars. ARIMA innovation RMSE is in percentage points of monthly log growth, "
        "and OLS R squared does not apply to that conditional innovation fit.\n",
        _fit_table(price_fits[price_fits.model.isin(["level_ar1", "arima011"])]),
        _coefficients(price_fits[price_fits.model.isin(["level_ar1", "arima011"])]),
        "![Observed values and each lag model at three horizons](zhvi_lag_comparison_forecasts.svg)\n",
        "**3.6 Keep an early validation period for choosing specifications and reserve a later independent test period.**\n",
        f"Validation origins run from {val_origins[0]} through {val_origins[-1]}. Test origins run from "
        f"{test_origins[0]} through {test_origins[-1]}. "
        "For independent evaluation, choose a model and training start using the minimum validation "
        "RMSE for that horizon, then assess that choice on the later test origins.\n",
    ]
    chosen = _series(selections).set_index("horizon")
    parts.append(_table(
        ["Horizon", "Validation choice", "Start", "Validation RMSE", "Test MAE", "Test RMSE", "No-change test RMSE"],
        [[h, LABELS[chosen.loc[h, "model"]], chosen.loc[h, "train_start"],
          f'{chosen.loc[h, "validation_rmse"]:,.0f}', f'{chosen.loc[h, "test_mae"]:,.0f}',
          f'{chosen.loc[h, "test_rmse"]:,.0f}', f'{_point(price, "naive", h).rmse:,.0f}'] for h in HORIZONS],
    ))
    chosen_wins = [str(h) for h in HORIZONS if chosen.loc[h, "test_rmse"] < _point(price, "naive", h).rmse]
    selection_result = (
        f"The validation-selected specifications beat no change on test RMSE at {', '.join(chosen_wins)} months."
        if chosen_wins else
        "The validation-selected specifications have equal or larger test RMSE than no change at all three horizons."
    )
    parts.append(
        f"{selection_result} "
        "Good fitted values and even good validation performance do not guarantee performance in a later "
        "market period. All comparisons use matching dates, and adjacent multi-month errors overlap.\n"
    )
    parts += [
        "**3.7 How is leakage avoided, and what limitation remains?**\n",
        "At each origin, slice the observed history before creating differences or lags and estimating "
        "coefficients. Recursion uses predicted future growth whenever a lag extends beyond the origin. "
        "Actual future values enter only the subsequent error calculation. No full-sample standardization "
        "or additional smoothing is applied. Provider revisions and smoothing are already embedded in "
        "the published histories, so these results are explicitly retrospective rather than real-time "
        "reconstructions of past information sets.\n",
    ]
    rent = _series(test, "zori_smoothed_sa")
    rent_fits = _series(snapshot, "zori_smoothed_sa")
    rent_fits = rent_fits[rent_fits.model.isin(GROWTH_MODELS)]
    parts += [
        "## Exercise 4  Rent growth forecasts\n",
        "**4.1 Estimate AR(1) for rent growth and state the LHS and RHS.**\n",
        "Use the active all-homes-plus-multifamily ZORI, which Zillow smooths and seasonally adjusts. "
        "Let r_t = 100 × [ln(R_t) − ln(R_(t−1))]. The LHS is r_t and the RHS is an intercept plus "
        "r_(t−1). Recover forecast rent levels by compounding predicted growth from the origin rent.\n",
        "**4.2 Repeat with three and six lags.**\n",
        "AR(3) adds r_(t−2) and r_(t−3); AR(6) additionally includes r_(t−4), r_(t−5), and "
        "r_(t−6), each with its own coefficient. All three fits use the same response months. "
        "The next tables give the fitted equations and test MAE/RMSE in dollars per month.\n",
        _fit_table(rent_fits), _coefficients(rent_fits),
        _accuracy_table(rent, [(model, start, LABELS[model]) for model in (*GROWTH_MODELS, "naive")]),
        f"The best rent lag specification by test RMSE is "
        f"{_winner_text(_winner_labels(rent, GROWTH_MODELS))}.\n",
        "**4.3 Is rent growth more or less predictable than home-value growth over the same dates?**\n",
        "The following percentage errors compare rents and home values on identical dates.\n",
        _table(["Model", "Horizon", "Home-value MAPE", "Rent MAPE"],
               [[LABELS[model], h, f"{_point(price, model, h).mape_pct:.2f}%",
                 f"{_point(rent, model, h).mape_pct:.2f}%"] for model in GROWTH_MODELS for h in HORIZONS]),
        f"At six months, growth AR(3) has rent MAPE of {_point(rent, 'growth_ar3').mape_pct:.2f}% "
        f"and price MAPE of {_point(price, 'growth_ar3').mape_pct:.2f}%. "
        f"{'Rent has lower percentage forecast errors in every matched model/horizon comparison in the table, so it is more predictable on this recovered-level metric.' if all(_point(rent, m, h).mape_pct < _point(price, m, h).mape_pct for m in GROWTH_MODELS for h in HORIZONS) else 'Predictability varies with the model and horizon, as the matched percentage-error table shows.'} "
        "This is a comparison of growth-model forecasts recovered into published index levels; it does "
        "not equate low level MAPE with a high explained share of monthly growth variation.\n",
        "**4.4 Are the rent and price indexes seasonally adjusted or smoothed?**\n",
        "Both active ZORI and ZHVI are provider-smoothed and seasonally adjusted. No additional smoothing "
        "or seasonal adjustment is applied. The archived non-seasonally-adjusted ZORI vintage is excluded. "
        "Provider processing can affect apparent persistence and predictability, and revised histories "
        "limit real-time interpretations.\n",
        "![Rent forecasts and observed values](zori_smoothed_sa_forecasts.svg)\n",
        "## Exercise 5  Housing permit activity\n",
        "**5.1 Construct a trailing three-month moving average of permits.**\n",
        "Permits measure housing units authorized during each month. Construct M_t as "
        "(permits_t + permits_(t−1) + permits_(t−2)) / 3. The average uses the current and two prior "
        "months only; it never includes a future observation. The first two raw months initialize the average.\n",
        "**5.2 Regress the current moving average on its own first lag.**\n",
        "The fitted equation is M_t = a + b M_(t−1) + e_t. Its LHS is the current three-month mean; "
        "the RHS contains an intercept and the preceding month's three-month mean. The tables also show "
        "the raw-permit AR(1), with current monthly permits on the LHS and last month's permits on the RHS.\n",
    ]
    permits = _series(test, "permits_total")
    means = _series(test, "permits_total", target="mean3")
    permit_fits = snapshot[(snapshot.geo_id == LA) & (snapshot.variable_id == "permits_total") &
                          snapshot.model.isin(["level_ar1", "mean3_ar1"])]
    parts += [
        _fit_table(permit_fits), _coefficients(permit_fits),
        "**5.3 Compare forecasts with a model estimated directly on raw monthly permits.**\n",
        "**Raw monthly target.** MAE and RMSE are in permitted units per month.\n",
        _accuracy_table(permits, [(m, start, LABELS[m]) for m in ("level_ar1", "naive", "seasonal_naive")]),
        "**Common trailing-mean target.** Every row forecasts the same observed three-month mean. "
        "For the raw AR(1) comparison, average its predicted monthly levels, using observed components "
        "whenever those components fall on or before the origin.\n",
        _accuracy_table(means, [(m, start, LABELS[m]) for m in ("mean3_ar1", "raw_ar1_mean3", "naive_mean3", "seasonal_naive_mean3")]),
        f"At six months, direct mean AR(1) has RMSE {_point(means, 'mean3_ar1').rmse:,.1f}, "
        f"raw AR(1) followed by averaging has {_point(means, 'raw_ar1_mean3').rmse:,.1f}, "
        "all on the common mean target. "
        f"The direct mean model has lower RMSE at {', '.join(str(h) for h in HORIZONS if _point(means, 'mean3_ar1', h).rmse < _point(means, 'raw_ar1_mean3', h).rmse) or 'none of the'} months. "
        "Smoothing therefore does not automatically produce the better forecasting method once the "
        "target is held fixed.\n",
        "**5.4 Compare with a seasonal-naive forecast.**\n",
        "Seasonal naive repeats the value from twelve months before the target. Its mean-target variant "
        "averages the three corresponding year-earlier monthly values. "
        f"At six months its raw-target RMSE is {_point(permits, 'seasonal_naive').rmse:,.1f}, versus "
        f"{_point(permits, 'level_ar1').rmse:,.1f} for raw AR(1); its mean-target RMSE is "
        f"{_point(means, 'seasonal_naive_mean3').rmse:,.1f}, versus {_point(means, 'mean3_ar1').rmse:,.1f} "
        "for direct mean AR(1). The horizon-specific tables above retain the full comparison.\n",
        "**5.5 Why does overlapping smoothing create persistence, and how should forecast errors be compared?**\n",
        "Adjacent averages share two monthly observations, which creates persistence mechanically. "
        "For example, M_t and M_(t−1) both contain permits_(t−1) and permits_(t−2), even if raw monthly "
        "flows were independent. A smoother target also has less variation. Compare competing methods "
        "against the same observed target; a lower RMSE for means than for raw flows is not by itself "
        "evidence of improved forecasting. The raw and averaged targets therefore have separate tables. Census flags provider "
        "imputation in most monthly estimates, and monthly coverage expanded in 2022.\n",
        "![Permits forecasts on a common trailing-mean target](permits_total_forecasts.svg)\n",
        "## Exercise 6  Housing inventory\n",
        "**6.1 Verify whether a suitable inventory series is available.**\n",
        "Use Redfin county active inventory, an unadjusted monthly stock. The level models regress "
        "actual county inventory; no ZIP active-listings measure is relabeled as inventory. "
        "The data inventory at the start of this report records its observed coverage and confirms "
        "a complete monthly study interval.\n",
    ]
    stock = _series(test, "active_inventory")
    stock_fits = _series(snapshot, "active_inventory")
    stock_fits = stock_fits[stock_fits.model.isin(["level_ar1", "level_ar3", "growth_ar1", "growth_ar3"])]
    seasonal = _seasonality(observations, start, test_origins[0])
    positive = observations[(observations.geo_id == LA) & (observations.variable_id == "active_inventory")].value.gt(0).all()
    parts += [
        "**6.2 Estimate AR(1) and AR(3) in inventory levels.**\n",
        "AR(1) has inventory_t on the LHS and an intercept plus inventory_(t−1) on the RHS. "
        "AR(3) adds inventory_(t−2) and inventory_(t−3), each separately. Both use the same fitted "
        "response months within the level comparison.\n",
        "**6.3 Consider log changes if the inventory observations are positive.**\n",
        f"{'All available inventory observations are positive' if positive else 'Inventory includes nonpositive observations'}. Level-regression training "
        "RMSE is measured in homes, and growth-regression training RMSE in percentage points.\n",
        "Define q_t = 100 × [ln(inventory_t) − ln(inventory_(t−1))]. The growth specifications "
        "regress q_t on an intercept and its first lag or its first three lags. Forecast growth is "
        "compounded back into homes, so every row in the test table can be compared on the same level target.\n",
        _fit_table(stock_fits), _coefficients(stock_fits),
        _accuracy_table(stock, [(m, start, LABELS[m]) for m in ("level_ar1", "level_ar3", "growth_ar1", "growth_ar3", "naive", "seasonal_naive")
                                if m in set(stock.model)]),
        f"Between the two level models, the lower test RMSE comes from "
        f"{_winner_text(_winner_labels(stock, ('level_ar1', 'level_ar3')))}. "
        f"Across all four fitted inventory models, the best RMSE comes from "
        f"{_winner_text(_winner_labels(stock, ('level_ar1', 'level_ar3', 'growth_ar1', 'growth_ar3')))}.\n",
        "**6.4 Compare with a seasonal-naive benchmark.**\n",
        "The seasonal benchmark uses inventory in the target calendar month one year earlier. "
        f"At six months it has RMSE {_point(stock, 'seasonal_naive').rmse:,.0f} homes, versus "
        f"{_point(stock, 'level_ar1').rmse:,.0f} for level AR(1). "
        "The table reports its 3- and 12-month errors as well. Strong seasonality alone does not guarantee "
        "that a year-earlier observation forecasts well when the overall inventory level changes.\n",
        "**6.5 Does inventory have stronger seasonality than prices?**\n",
        f"Calendar-month means explain {seasonal.active_inventory:.1f}% of monthly inventory log-change "
        f"variation versus {seasonal.zhvi:.1f}% for prices over matching dates through the first test origin. "
        "The inventory series has a stronger seasonal pattern in this comparison, while ZHVI is already "
        "smoothed and seasonally adjusted by its provider.\n",
        "![Inventory forecasts and seasonal benchmark](active_inventory_forecasts.svg)\n",
        "## Exercise 7  California county comparison\n",
        "**7.1 Verify comparable data for at least two other counties and estimate AR(1) and AR(3).**\n",
        "Repeat growth AR(1) and AR(3) for Orange, Riverside and San Diego in addition to LA. "
        "All four have complete ZHVI histories for the study interval, as recorded in the inventory table. "
        "The county accuracy table below reports the results, and the full county coefficients and "
        "sample dates are retained in regressions.csv.\n",
        "**7.2 Keep units, transformations, lag definitions, horizons, and evaluation dates comparable.**\n",
        "All use the same ZHVI definition in dollars, log-growth transformation, raw training start, "
        "six-growth-month initialization, forecast horizons and test dates.\n",
    ]
    county_rows, comparison_rows = [], []
    county_mape, county_volatility, county_winners = {}, {}, {}
    county_scores = test[(test.variable_id == "zhvi") & test.geo_id.str.startswith("COUNTY_")]
    for geo, group in county_scores.groupby("geo_id"):
        name = observations.loc[observations.geo_id == geo, "geo_name"].iloc[0].replace(" County", "")
        for model in ("growth_ar1", "growth_ar3", "naive"):
            row = [name, LABELS[model]]
            for h in HORIZONS:
                point = _point(group, model, h)
                row.extend([f"{point.mae:,.0f}", f"{point.rmse:,.0f}"])
            county_rows.append(row)
        target_dates = sorted(predictions.loc[(predictions.split == "test") & (predictions.horizon == 6), "target_date"].unique())
        volatility = _growth(observations, geo, "zhvi").reindex(pd.PeriodIndex(target_dates, freq="M")).std()
        county_volatility[name] = volatility
        county_mape[name] = _point(group, "growth_ar3").mape_pct
        county_winners[name] = _winner_labels(group, ("growth_ar1", "growth_ar3"))
        comparison_rows.append([name, *_winner_labels(group, ("growth_ar1", "growth_ar3")),
                                f"{_point(group, 'growth_ar3').mape_pct:.2f}%", f"{volatility:.3f}"])
    most_volatile = max(county_volatility, key=county_volatility.get)
    least_volatile = min(county_volatility, key=county_volatility.get)
    largest_error = max(county_mape, key=county_mape.get)
    smallest_error = min(county_mape, key=county_mape.get)
    volatility_answer = (
        f"{most_volatile} has the highest growth standard deviation and {largest_error} the highest "
        f"six-month AR(3) MAPE. {least_volatile} has the lowest volatility and {smallest_error} the lowest MAPE."
    )
    if sorted(county_volatility, key=county_volatility.get) != sorted(county_mape, key=county_mape.get):
        volatility_answer += " The full ranking differs, so greater volatility does not consistently imply a larger error."
    state = inventory[inventory.geo_id == "STATE_06"].iloc[0]
    if state.evaluation_status == "evaluated":
        state_answer = "Statewide ZHVI also qualified, and its separate model estimates are in the result tables."
    elif state.status == "available":
        state_answer = "Statewide ZHVI was present but could not support the fixed training and evaluation dates."
    else:
        state_answer = "Statewide ZHVI has no observations in this database, so a statewide model could not be estimated."
    parts += [
        _table(["County", "Model", "3m MAE", "3m RMSE", "6m MAE", "6m RMSE", "12m MAE", "12m RMSE"], county_rows),
        "**7.3 Does the same lag length work best in each county?**\n",
        _table(["County", "Lower RMSE at 3m", "At 6m", "At 12m", "6m AR(3) MAPE", "Growth SD in pp"], comparison_rows),
        "The preferred lag depends on both county and horizon. "
        + " ".join(f"At {h} months: " + ", ".join(name + " favors " + winners[i] for name, winners in county_winners.items()) + "."
                   for i, h in enumerate(HORIZONS)) + "\n",
        "**7.4 Are forecast errors larger in more volatile markets?**\n",
        f"{volatility_answer} The six-month MAPE and growth standard deviation use matching target months. "
        "MAPE removes the immediate effect of different dollar price levels. The relationship is partly "
        "consistent at the extremes, but four counties do not establish a general statistical or causal "
        "relationship.\n",
        "**7.5 If statewide data are available, estimate a comparable statewide model.**\n",
        f"{state_answer} The optional state comparison is therefore governed by actual source availability "
        "and the same fixed-date eligibility checks as the county models.\n",
        "**7.6 Can an unweighted average of the available counties be treated as a statewide index?**\n",
        "No. These four counties do not cover California, and equal county weights ignore differences "
        "in housing stock and the provider's index construction. No county average is substituted for "
        "an official statewide series.\n",
    ]
    parts += _interpretation_answers(test, snapshot, selections, observations)
    design_path = Path(__file__).resolve().parents[2] / "docs/forecasting_model_design.md"
    design = design_path.read_text(encoding="utf-8")
    # Embed the maintained design note one heading level below its standalone form.
    parts.append("\n".join("#" + line if line.startswith("#") else line for line in design.splitlines()))
    (output_dir / "week1_exercises.md").write_text("\n".join(parts), encoding="utf-8")
