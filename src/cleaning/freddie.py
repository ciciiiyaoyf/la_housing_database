"""Freddie Mac PMMS weekly and monthly transformations."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


def _normalize(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def clean_freddie(
    path: Path, record: dict[str, str], output_path: Path
) -> pd.DataFrame:
    source = pd.read_csv(path)
    source.columns = [_normalize(column) for column in source.columns]
    date_column = next(
        (column for column in source if column in {"date", "week", "week_ending"}),
        None,
    )
    rate_column = next(
        (
            column
            for column in source
            if column in {"pmms30", "pmms_30", "30yr_frm", "30_year_frm"}
            or ("30" in column and ("frm" in column or "rate" in column))
        ),
        None,
    )
    if date_column is None or rate_column is None:
        raise ValueError(
            f"Freddie Mac schema change: columns are {list(source.columns)}"
        )
    weekly = pd.DataFrame(
        {
            "source_observation_date": pd.to_datetime(
                source[date_column], errors="coerce"
            ),
            "value": pd.to_numeric(
                source[rate_column].astype(str).str.rstrip("%"), errors="coerce"
            ),
        }
    ).dropna()
    weekly["observation_date"] = weekly["source_observation_date"]
    weekly["period_start"] = weekly["source_observation_date"]
    weekly["period_end"] = weekly["source_observation_date"]
    weekly["variable_id"] = "mortgage_rate_30y_weekly"
    weekly["unit"] = "percent"
    monthly = weekly.assign(
        month=weekly["source_observation_date"].dt.to_period("M")
    )
    average = (
        monthly.groupby("month", as_index=False)["value"]
        .mean()
        .assign(variable_id="mortgage_rate_30y_monthly_average")
    )
    month_end = (
        monthly.sort_values("source_observation_date")
        .groupby("month", as_index=False)
        .tail(1)[["month", "value"]]
        .assign(variable_id="mortgage_rate_30y_month_end")
    )
    monthly_rows = pd.concat([average, month_end], ignore_index=True)
    monthly_rows["observation_date"] = monthly_rows["month"].dt.to_timestamp("M")
    monthly_rows["source_observation_date"] = monthly_rows["observation_date"]
    monthly_rows["period_start"] = monthly_rows["month"].dt.start_time
    monthly_rows["period_end"] = monthly_rows["observation_date"]
    monthly_rows["unit"] = "percent"
    combined = pd.concat([weekly, monthly_rows], ignore_index=True, sort=False)
    combined["geo_id"] = "US"
    combined["provider"] = "Freddie Mac"
    combined["native_frequency"] = "weekly"
    combined["final_frequency"] = combined["variable_id"].map(
        lambda item: "weekly" if item.endswith("_weekly") else "monthly"
    )
    for key, value in metadata_fields(record).items():
        combined[key] = value
    combined["transformation_id"] = combined["variable_id"].map(
        {
            "mortgage_rate_30y_weekly": "freddie_pmms_weekly_raw_v1",
            "mortgage_rate_30y_monthly_average": "freddie_pmms_monthly_mean_v1",
            "mortgage_rate_30y_month_end": "freddie_pmms_monthly_last_v1",
        }
    )
    combined["is_imputed"] = False
    combined["is_interpolated"] = False
    combined["is_seasonally_adjusted"] = False
    combined["quality_flag"] = None
    return finalize(combined, output_path)
