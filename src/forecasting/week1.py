"""Expanding-window backtests with a sealed later evaluation period."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import yaml

from src.forecasting.data import load_inputs
from src.forecasting.models import fit_forecast


SERIES = ["geo_id", "variable_id", "target"]
SPEC = [*SERIES, "model", "train_start"]
LA = "COUNTY_06037"


def evaluation_calendar(config):
    horizons = config["horizons"]
    if horizons != [3, 6, 12]:
        raise ValueError("The Week 1 exercises require horizons of 3, 6 and 12 months.")
    split_months = {s: pd.period_range(*config[s], freq="M") for s in ("validation", "test")}
    if any(len(months) == 0 for months in split_months.values()):
        raise ValueError("Both evaluation periods require at least one origin.")
    if split_months["validation"][-1] + max(horizons) > split_months["test"][0]:
        raise ValueError("Validation outcomes must be observable by the first test origin.")
    if split_months["validation"][0].ordinal - pd.Period(config["train_start"], "M").ordinal + 1 < 60:
        raise ValueError("The primary specification needs five years of initial history.")
    if config["selection_metric"] != "rmse":
        raise ValueError("The Week 1 selection criterion is validation RMSE.")
    starts = config["la_price_window_starts"]
    if len(set(starts)) < 2 or config["train_start"] in starts:
        raise ValueError("LA price window sensitivity requires at least two alternative training starts.")
    return split_months


def _run_specs(series, geo, variable, target, specs, calendar, horizons, forecasts, regressions):
    # The target at t is observed only when scoring. Every fit receives a prefix.
    target_values = series.rolling(3, min_periods=3).mean() if target == "mean3" else series
    for split, origins in calendar.items():
        for origin in origins:
            prefix = series.loc[:origin]
            for model, start in specs:
                hold_back = None
                if model.startswith("growth_ar"):
                    hold_back = 6
                elif model.startswith("level_ar") or model == "raw_ar1_mean3":
                    hold_back = 3
                fit = fit_forecast(prefix, origin, start, model, max(horizons), hold_back=hold_back)
                context = dict(split=split, geo_id=geo, variable_id=variable, target=target,
                               model=model, train_start=start, origin=str(origin))
                regressions.append({
                    **context, "lhs": fit.lhs, "rhs": json.dumps(fit.rhs),
                    "coefficients": json.dumps(fit.coefficients), "nobs": fit.nobs,
                    "train_first": str(fit.train_first) if fit.train_first is not None else None,
                    "train_last": str(fit.train_last) if fit.train_last is not None else None,
                    "train_rmse": fit.train_rmse, "r_squared": fit.r_squared,
                })
                for h in horizons:
                    actual = float(target_values.loc[origin + h])
                    prediction = float(fit.path[h - 1])
                    forecasts.append({
                        **context, "horizon": h, "target_date": str(origin + h),
                        "origin_level": float(target_values.loc[origin]), "forecast": prediction,
                        "actual": actual, "error": prediction - actual,
                    })


def backtest(observations, inventory, config):
    calendar = evaluation_calendar(config)
    horizons, start, models = config["horizons"], config["train_start"], config["model_sets"]
    fits, forecasts = [], []
    inventory = inventory.copy()
    inventory["evaluation_status"] = "unavailable"
    end = calendar["test"][-1] + max(horizons)
    compared = 0
    for idx, record in inventory.iterrows():
        geo, variable = record["geo_id"], record["variable_id"]
        series_rows = observations[(observations.geo_id == geo) & (observations.variable_id == variable)]
        series = series_rows.set_index("observation_date")["value"].sort_index()
        series.index = series.index.to_period("M")
        first = min(start, *config["la_price_window_starts"]) if (geo, variable) == (LA, "zhvi") else start
        expected = pd.period_range(first, end, freq="M")
        if record["status"] != "available" or series.reindex(expected).isna().any():
            if geo == LA:
                raise ValueError(f"Required {geo} {variable} is unavailable over {first} through {end}.")
            inventory.at[idx, "evaluation_status"] = "insufficient coverage for fixed calendar"
            continue
        if variable == "zhvi":
            group = "la_price" if geo == LA else "other_price"
            if geo == "STATE_06":
                group = "state_price"
            elif geo != LA:
                if geo not in config["county_comparisons"]:
                    inventory.at[idx, "evaluation_status"] = "outside configured county comparison"
                    continue
                compared += 1
        else:
            group = {"zori_smoothed_sa": "rent", "active_inventory": "inventory",
                     "permits_total": "permits"}[variable]
        specs = [(model, start) for model in models[group]]
        if group == "la_price":
            specs.extend(("growth_ar3", alternative) for alternative in config["la_price_window_starts"])
        if group == "inventory" and (series.loc[start:calendar["validation"][0]] > 0).all():
            specs.extend((model, start) for model in models["positive_inventory"])
        _run_specs(series, geo, variable, "level", specs, calendar, horizons, forecasts, fits)
        if group == "permits":
            specs = [(model, start) for model in models["permits_mean3"]]
            _run_specs(series, geo, variable, "mean3", specs, calendar, horizons, forecasts, fits)
        inventory.at[idx, "evaluation_status"] = "evaluated"
    if compared < 2:
        raise ValueError("The matched comparison requires at least two additional California counties.")
    return pd.DataFrame(forecasts), pd.DataFrame(fits), inventory


def evaluate(forecasts, calendar, horizons=(3, 6, 12)):
    keys = ["split", *SPEC, "horizon"]
    if forecasts.duplicated(keys + ["origin"]).any():
        raise ValueError("Duplicate specification, horizon and origin.")
    if not np.isfinite(forecasts[["actual", "forecast", "origin_level", "error"]]).all().all():
        raise ValueError("Missing or nonfinite forecast, error or observed target.")
    if set(forecasts.split) != set(calendar) or set(forecasts.horizon) != set(horizons):
        raise ValueError("Unexpected or missing evaluation split or horizon.")
    counts = forecasts[[*SPEC, "split", "horizon"]].drop_duplicates().groupby(SPEC).size()
    if not counts.eq(len(calendar) * len(horizons)).all():
        raise ValueError("Every specification requires both splits and all horizons.")
    targets = pd.PeriodIndex(forecasts.origin, freq="M").asi8 + forecasts.horizon.to_numpy()
    if not np.array_equal(targets, pd.PeriodIndex(forecasts.target_date, freq="M").asi8):
        raise ValueError("A target date does not equal its origin plus horizon.")
    if not np.allclose(forecasts.error, forecasts.forecast - forecasts.actual, rtol=1e-12, atol=1e-8):
        raise ValueError("Forecast error must equal forecast minus actual.")
    if forecasts.groupby([*SERIES, "target_date"]).actual.nunique().max() != 1:
        raise ValueError("Models must be scored against identical observed targets.")
    scores = []
    for key, rows in forecasts.groupby(keys, sort=False):
        if set(rows.origin) != set(map(str, calendar[key[0]])):
            raise ValueError(f"Unmatched evaluation origins in {key}.")
        errors = rows.error.to_numpy()
        scores.append({
            **dict(zip(keys, key, strict=True)), "n": len(rows),
            "mae": np.abs(errors).mean(), "rmse": np.sqrt(np.mean(errors ** 2)),
            "mape_pct": 100 * (rows.error / rows.actual).abs().mean() if rows.actual.ne(0).all() else None,
        })
    accuracy = pd.DataFrame(scores)
    # Freeze the winner using validation alone. Only then attach independent scores.
    group = [*SERIES, "horizon"]
    scores_key = [*group, "model", "train_start"]
    validation = accuracy[accuracy.split == "validation"].sort_values(["rmse", "model", "train_start"])
    winners = validation.drop_duplicates(group)[scores_key + ["rmse"]].rename(columns={"rmse": "validation_rmse"})
    test = accuracy[accuracy.split == "test"][scores_key + ["rmse", "mae", "mape_pct"]]
    selected = winners.merge(test, on=scores_key, validate="one_to_one", how="left", indicator=True)
    if not selected["_merge"].eq("both").all():
        raise ValueError("A selected specification is missing its held-out test scores.")
    return accuracy, selected.drop(columns="_merge").rename(columns={
        "rmse": "test_rmse", "mae": "test_mae", "mape_pct": "test_mape_pct",
    })


def run_week1(database: Path, config_path: Path, output: Path):
    from src.forecasting.exercises import write_exercise_report
    from src.forecasting.report import write_report

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    calendar = evaluation_calendar(config)
    observations, inventory = load_inputs(database)
    forecasts, fits, inventory = backtest(observations, inventory, config)
    accuracy, selections = evaluate(forecasts, calendar, config["horizons"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".week1-") as staging:
        stage = Path(staging)
        tables = dict(observations=observations, inventory=inventory, forecasts=forecasts,
                      regressions=fits, accuracy=accuracy, selections=selections)
        for name, frame in tables.items():
            frame.to_csv(stage / f"{name}.csv", index=False)
        write_report(stage)
        write_exercise_report(stage)
        write_transformation_log(stage, observations, forecasts, config)
        # The previous submission stays usable if analysis or rendering raises.
        publish_results(stage, output)


def publish_results(stage, output):
    """Publish only generated files. Restore the previous set if a file is locked."""
    output.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.publish.lock"
    sources = sorted(stage.iterdir())
    # Two concurrent runs may calculate independently, but only one may publish.
    with lock.open("x"):
        pass
    try:
        with TemporaryDirectory(dir=stage) as backup_dir:
            published = []
            try:
                for source in sources:
                    destination = output / source.name
                    backup = Path(backup_dir) / source.name
                    if destination.exists():
                        shutil.copy2(destination, backup)
                    source.replace(destination)
                    published.append((destination, backup))
            except OSError:
                for destination, backup in reversed(published):
                    if backup.exists():
                        backup.replace(destination)
                    else:
                        destination.replace(stage / destination.name)
                raise
    finally:
        lock.unlink()


def write_transformation_log(stage, observations, forecasts, config):
    """Record what ran and tie each estimated specification to its input lineage."""
    stamp = datetime.now(timezone.utc).isoformat()
    records = []
    for key, runs in forecasts.groupby(SPEC, sort=False):
        spec = dict(zip(SPEC, key, strict=True))
        inputs = observations[(observations.geo_id == spec["geo_id"]) &
                              (observations.variable_id == spec["variable_id"])]
        month = inputs.observation_date.dt.to_period("M")
        inputs = inputs[month.between(pd.Period(spec["train_start"], "M"),
                                     pd.Period(runs.target_date.max(), "M"))]
        diagnostics = []
        if spec["geo_id"].startswith("COUNTY_") and spec["variable_id"] == "zhvi":
            diagnostics.append("county_growth_sd")
        if spec["geo_id"] == LA and spec["variable_id"] in {"zhvi", "active_inventory"}:
            diagnostics.append("calendar_month_variance_share")
        records.append({
            **spec, "executed_at_utc": stamp,
            "source_ids": json.dumps(sorted(inputs.source_id.dropna().unique().tolist())),
            "source_vintages": json.dumps(sorted(inputs.source_vintage.dropna().astype(str).unique().tolist())),
            "lineage_file": "observations.csv", "code_module": "src.forecasting.data|src.forecasting.week1|src.forecasting.models|src.forecasting.report|src.forecasting.exercises",
            "dictionary": "metadata/week1_forecast_dictionary.csv",
            "operations": "extract monthly levels with lineage; fit/iterate model; score signed error, MAE, RMSE and MAPE; select on validation RMSE",
            "report_diagnostics": ", ".join(diagnostics),
            "validation_origins": "/".join(config["validation"]), "test_origins": "/".join(config["test"]),
            "horizons_months": json.dumps(config["horizons"]), "forecast_rows": len(runs),
        })
    pd.DataFrame(records).to_csv(stage / "transformation_log.csv", index=False)
