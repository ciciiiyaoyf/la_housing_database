from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.freddie import clean_freddie


def test_freddie_monthly_mean_median_and_last_are_distinct(tmp_path: Path):
    raw = tmp_path / "pmms.csv"
    pd.DataFrame(
        {
            "date": [
                "2026-01-01",
                "2026-01-08",
                "2026-01-15",
                "2026-01-29",
            ],
            "pmms30": [5.0, 6.0, 7.0, 10.0],
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-02-01",
        "retrieved_at": "2026-02-01T00:00:00+00:00",
        "source_filename": raw.name,
    }

    result = clean_freddie(raw, record, tmp_path / "clean.parquet")
    monthly = result.loc[result["final_frequency"].eq("monthly")].set_index(
        "variable_id"
    )

    assert monthly.loc[
        "mortgage_rate_30y_monthly_average", "value"
    ] == pytest.approx(7.0)
    assert monthly.loc[
        "mortgage_rate_30y_monthly_median", "value"
    ] == pytest.approx(6.5)
    assert monthly.loc[
        "mortgage_rate_30y_month_end", "value"
    ] == pytest.approx(10.0)
    assert monthly.loc[
        "mortgage_rate_30y_monthly_median", "transformation_id"
    ] == "freddie_pmms_monthly_median_v1"
