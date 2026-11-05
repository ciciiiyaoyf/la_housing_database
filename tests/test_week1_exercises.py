"""Check that exercise answers follow the score tables when model rankings change."""

from pathlib import Path
import shutil

import pandas as pd
import pytest

from src.forecasting.exercises import write_exercise_report


def test_exercise_answers_update_when_the_rent_ranking_changes(tmp_path):
    source = Path(__file__).resolve().parents[1] / "output/forecasting/week1"
    if not (source / "observations.csv").exists():
        pytest.skip("Run the forecast command with provisioned observations first.")
    for name in ("accuracy", "regressions", "forecasts", "selections", "observations", "inventory"):
        shutil.copyfile(source / f"{name}.csv", tmp_path / f"{name}.csv")
    scores = pd.read_csv(tmp_path / "accuracy.csv")
    rent = (scores.split == "test") & (scores.variable_id == "zori_smoothed_sa")
    # This test scenario changes only the fixture, never the published results.
    winner = scores.loc[rent, "rmse"].min() / 10
    scores.loc[rent & (scores.model == "growth_ar3"), "rmse"] = winner
    scores.to_csv(tmp_path / "accuracy.csv", index=False)
    write_exercise_report(tmp_path)
    report = (tmp_path / "week1_exercises.md").read_text(encoding="utf-8")
    assert "rent accuracy favors Growth AR(3) at all three horizons" in report
    assert "rent accuracy favors Growth AR(1) at all three horizons" not in report
    assert "innovation_lag_1" in report
    assert report.count("## Exercise ") == 7
    assert "### 3. Do the same simple models work" in report
