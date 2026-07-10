from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize
from src.features.derived import price_to_rent_ratio


def test_finalize_preserves_missing_observation_values(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "observation_date": ["2026-01-31", "2026-02-28"],
            "source_observation_date": ["2026-01-31", "2026-02-28"],
            "period_start": ["2026-01-01", "2026-02-01"],
            "period_end": ["2026-01-31", "2026-02-28"],
            "geo_id": ["COUNTY_06037", "COUNTY_06037"],
            "variable_id": ["test_variable", "test_variable"],
            "value": [1.0, None],
        }
    )

    result = finalize(frame, tmp_path / "observations.parquet")

    assert len(result) == 2
    assert result["value"].isna().sum() == 1


def test_affordability_ratio_stays_missing_when_rent_missing():
    result = price_to_rent_ratio(
        pd.Series([600_000.0, 600_000.0]),
        pd.Series([2_500.0, None]),
    )

    assert result.iloc[0] == 20.0
    assert pd.isna(result.iloc[1])


def test_source_code_does_not_fill_housing_values_with_zero_or_interpolation():
    root = Path(__file__).resolve().parents[1] / "src"
    forbidden = ("fillna(0", ".interpolate(", ".bfill(", ".ffill(")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append((path.relative_to(root.parent).as_posix(), token))

    assert offenders == []
