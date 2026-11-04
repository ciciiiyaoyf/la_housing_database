from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.forecasting.data import load_inputs
from src.forecasting.week1 import backtest, evaluate, evaluation_calendar, publish_results


ROOT = Path(__file__).resolve().parents[1]


def test_locked_report_rolls_back_all_published_tables(tmp_path, monkeypatch):
    stage, output = tmp_path / "stage", tmp_path / "week1"
    stage.mkdir()
    output.mkdir()
    for name in ("accuracy.csv", "selections.csv", "week1_memo.pdf"):
        (stage / name).write_bytes(b"new")
    for name in ("accuracy.csv", "week1_memo.pdf", "user-notes.txt"):
        (output / name).write_bytes(b"old")

    replace = Path.replace

    def replace_unlocked(path, destination):
        if path == stage / "week1_memo.pdf":
            raise PermissionError("Report is open in another program")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", replace_unlocked)
    with pytest.raises(PermissionError):
        publish_results(stage, output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == {
        "accuracy.csv": b"old", "week1_memo.pdf": b"old", "user-notes.txt": b"old",
    }
    assert not (tmp_path / ".week1.publish.lock").exists()


def _config():
    return yaml.safe_load((ROOT / "config/forecasting_week1.yaml").read_text(encoding="utf-8"))


def test_validation_targets_must_be_known_before_test_and_initial_window_is_long_enough():
    config = _config()
    months = evaluation_calendar(config)
    assert months["validation"][-1] + max(config["horizons"]) <= months["test"][0]
    config["test"][0] = str(months["validation"][-1] + max(config["horizons"]) - 1)
    with pytest.raises(ValueError, match="Validation"):
        evaluation_calendar(config)
    config = _config()
    config["train_start"] = str(months["validation"][0] - 58)
    with pytest.raises(ValueError, match="five years"):
        evaluation_calendar(config)


@pytest.fixture
def score_rows():
    """Two deliberately small error distributions with opposite validation and test rankings."""
    calendar = {"validation": pd.period_range("2020-01", periods=2, freq="M"),
                "test": pd.period_range("2021-01", periods=2, freq="M")}
    errors = {"validation": {"growth_ar3": (3, -4), "naive": (8, -9)},
              "test": {"growth_ar3": (8, -6), "naive": (1, -2)}}
    rows = []
    for split, origins in calendar.items():
        for model, model_errors in errors[split].items():
            for origin, error in zip(origins, model_errors, strict=True):
                for horizon in (3, 6):
                    rows.append(dict(split=split, geo_id="COUNTY_06037", variable_id="zhvi",
                                     target="level", model=model, train_start="2015-01",
                                     horizon=horizon, origin=str(origin), target_date=str(origin + horizon),
                                     origin_level=100.0, actual=100.0, forecast=100.0 + error, error=error))
    return pd.DataFrame(rows), calendar


def test_scores_and_selection_use_validation_alone(score_rows):
    rows, calendar = score_rows
    accuracy, selections = evaluate(rows, calendar, horizons=(3, 6))
    val = accuracy.query("split == 'validation' and model == 'growth_ar3' and horizon == 3").iloc[0]
    assert val.n == 2
    assert val.mae == pytest.approx((3 + 4) / 2)
    assert val.rmse == pytest.approx(np.sqrt((9 + 16) / 2))
    assert set(selections.model) == {"growth_ar3"}
    np.testing.assert_allclose(selections.test_rmse, np.sqrt((64 + 36) / 2))
    assert selections.test_mae.eq(7).all()
    assert accuracy.query("split == 'test' and model == 'naive'").rmse.max() < selections.test_rmse.min()


@pytest.mark.parametrize("corruption", [
    "duplicate_origin", "missing_origin", "conflicting_actual", "shifted_target", "stale_error",
    "missing_specification_split", "missing_specification_horizon", "entire_horizon_absent",
])
def test_invalid_common_samples_and_error_rows_stop_evaluation(score_rows, corruption):
    rows, calendar = score_rows
    if corruption == "duplicate_origin":
        rows = pd.concat([rows, rows.iloc[[0]]])
    elif corruption == "missing_origin":
        rows = rows.drop(index=0)
    elif corruption == "conflicting_actual":
        rows.loc[0, "actual"] += 1
        rows.loc[0, "error"] -= 1
    elif corruption == "shifted_target":
        rows.loc[0, "target_date"] = "2020-05"
    elif corruption == "stale_error":
        rows.loc[0, "error"] = -rows.loc[0, "error"]
    elif corruption == "missing_specification_split":
        rows = rows[~((rows["split"] == "test") & (rows["model"] == "naive"))]
    elif corruption == "missing_specification_horizon":
        rows = rows[~((rows["model"] == "naive") & (rows["horizon"] == 6))]
    else:
        rows = rows[rows["horizon"] == 3]
    with pytest.raises(ValueError):
        evaluate(rows, calendar, horizons=(3, 6))


def test_live_backtest_matches_independent_ols_and_ignores_later_test_values():
    database = ROOT / "database/housing.duckdb"
    if not database.is_file():
        pytest.skip("Provision the local housing DuckDB to run the live Week 1 reproduction")
    config = _config()
    # Shorten only the number of origins to keep this real-data integration check fast.
    config["validation"] = ["2020-12", "2021-01"]
    config["test"] = ["2022-12", "2023-01"]
    observations, inventory = load_inputs(database)
    original, fits, coverage = backtest(observations, inventory, config)
    calendar = evaluation_calendar(config)
    end = calendar["test"][-1]
    revised = observations.copy()
    price_rows = (revised.geo_id == "COUNTY_06037") & (revised.variable_id == "zhvi")
    revised.loc[price_rows & (revised.observation_date.dt.to_period("M") > end), "value"] *= 2
    rerun, revised_fits, _ = backtest(revised, inventory, config)
    pd.testing.assert_frame_equal(fits, revised_fits)
    pd.testing.assert_series_equal(original.forecast, rerun.forecast)
    pd.testing.assert_frame_equal(original.query("split == 'validation'"), rerun.query("split == 'validation'"))
    assert (original.query("split == 'test'").actual != rerun.query("split == 'test'").actual).any()

    prices = observations.loc[price_rows].set_index("observation_date").value
    prices.index = prices.index.to_period("M")
    start = config["train_start"]
    history = prices.loc[start:end].to_numpy()
    growth = 100 * np.diff(np.log(history))
    # Hold back six growth observations, even for shorter AR models, for matched fits.
    x = np.column_stack([np.ones(len(growth) - 6), growth[5:-1], growth[4:-2], growth[3:-3]])
    beta = np.linalg.lstsq(x, growth[6:], rcond=None)[0]
    past = list(growth[-3:])
    for _ in range(max(config["horizons"])):
        past.append(beta @ [1, past[-1], past[-2], past[-3]])
    level_path = history[-1] * np.exp(np.cumsum(past[3:]) / 100)
    check = original[(original.geo_id == "COUNTY_06037") & (original.variable_id == "zhvi")
                     & (original.model == "growth_ar3") & (original.train_start == start)
                     & (original.origin == str(end))].sort_values("horizon")
    assert check.horizon.tolist() == config["horizons"]
    np.testing.assert_allclose(check.forecast, level_path[np.array(config["horizons"]) - 1])
    np.testing.assert_allclose(check.actual, [prices.loc[end + h] for h in config["horizons"]])
    state = coverage.query("geo_id == 'STATE_06'").iloc[0]
    assert original.geo_id.eq("STATE_06").any() == (state.evaluation_status == "evaluated")
    assert original.loc[original.variable_id == "zhvi", "geo_id"].nunique() >= 3
