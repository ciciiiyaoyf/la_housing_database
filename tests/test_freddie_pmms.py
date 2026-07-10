from pathlib import Path

import pandas as pd

from src.cleaning.freddie import clean_freddie


def test_freddie_cleaner_emits_documented_monthly_aggregates(tmp_path: Path):
    raw = tmp_path / "pmms.csv"
    pd.DataFrame(
        {
            "date": [
                "2026-01-01",
                "2026-01-08",
                "2026-01-15",
                "2026-02-05",
            ],
            "pmms30": [6.0, 6.5, 7.0, 6.25],
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
        "checksum_sha256": "abc123",
    }

    result = clean_freddie(raw, record, tmp_path / "freddie.parquet")
    january = result.loc[
        result["observation_date"].eq(pd.Timestamp("2026-01-31"))
    ].set_index("variable_id")

    assert january.loc["mortgage_rate_30y_monthly_average", "value"] == 6.5
    assert january.loc["mortgage_rate_30y_monthly_median", "value"] == 6.5
    assert january.loc["mortgage_rate_30y_month_end", "value"] == 7.0
    assert january.loc["mortgage_rate_30y_observation_count", "value"] == 3
    assert (
        january.loc[
            "mortgage_rate_30y_observation_count", "unit"
        ]
        == "weekly_observations"
    )
    assert result["checksum_sha256"].eq("abc123").all()
