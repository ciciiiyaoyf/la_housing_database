"""Document-authorized FRED national rates; preserve native data and monthly means."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.cleaning.common import finalize, metadata_fields


RATE_SERIES = {
    "fred_fedfunds": ("FEDFUNDS", "us_federal_funds_rate_monthly", "monthly", "percent"),
    "fred_dgs2": ("DGS2", "us_treasury_2y_yield_daily", "daily", "percent"),
    "fred_dgs10": ("DGS10", "us_treasury_10y_yield_daily", "daily", "percent"),
    "fred_baa10y": ("BAA10Y", "us_baa_10y_spread_daily", "daily", "percentage_points"),
    "fred_dprime": ("DPRIME", "us_bank_prime_rate_daily", "daily", "percent"),
    "fred_sofr": ("SOFR", "us_sofr_daily", "daily", "percent"),
}


def clean_macro_rates(path: Path, record: dict, source_id: str, output_path: Path) -> pd.DataFrame:
    """Never sum rates, forward-fill holidays, or call a partial month complete."""
    series, variable, frequency, unit = RATE_SERIES[source_id]
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(raw.columns) != ["observation_date", series]:
        raise ValueError(f"{source_id}: expected official FRED date and {series} columns")
    dates = pd.to_datetime(raw["observation_date"], errors="raise")
    if dates.isna().any() or dates.duplicated().any():
        raise ValueError(f"{source_id}: missing or duplicate dates")
    tokens = raw[series].str.strip()
    missing = tokens.isin(["", ".", "NA", "NaN"])
    values = pd.to_numeric(tokens.mask(missing), errors="raise")
    if not np.isfinite(values.dropna()).all():
        raise ValueError(f"{source_id}: nonfinite source value")
    if not values.notna().any():
        raise ValueError(f"{source_id}: no numeric source observations")
    retrieved = pd.Timestamp(record["retrieved_at"])
    retrieved = retrieved.tz_convert("UTC").tz_localize(None) if retrieved.tzinfo else retrieved
    if dates.gt(retrieved.normalize()).any():
        raise ValueError(f"{source_id}: observation after retrieval date")
    frame = pd.DataFrame({"source_observation_date": dates, "value": values})
    frame["observation_date"] = dates if frequency == "daily" else dates.dt.to_period("M").dt.to_timestamp("M")
    frame["period_start"] = dates
    frame["period_end"] = frame["observation_date"]
    if frequency == "monthly" and not dates.dt.day.eq(1).all():
        raise ValueError(f"{source_id}: FEDFUNDS must use provider month-start labels")
    frame["variable_id"] = variable
    frame["reported_value"] = values
    frame["unit"] = unit
    frame["geo_id"] = "US"
    frame["state_fips"] = None
    frame["county_fips"] = None
    frame["provider"] = "Federal Reserve Bank of St. Louis / FRED"
    frame["native_frequency"] = frequency
    frame["final_frequency"] = frequency
    frame["is_imputed"] = False
    frame["is_interpolated"] = False
    frame["is_seasonally_adjusted"] = False
    frame["quality_flag"] = missing.map({True: "provider_missing_or_nontrading_day", False: "provider_published"})
    frame["transformation_id"] = "fred_national_rate_native_v1"
    for key, value in metadata_fields(record).items():
        frame[key] = value
    if frequency == "monthly":
        # A provider monthly series should never be presented as a completed
        # month when its month-end falls in the retrieval month.
        frame = frame.loc[frame["observation_date"].lt(retrieved.to_period("M").start_time)].copy()
        if frame.empty:
            raise ValueError(f"{source_id}: no completed provider month")
        return finalize(frame, output_path)
    outputs = [frame]
    for period, group in frame.groupby(frame["observation_date"].dt.to_period("M")):
        if period >= retrieved.to_period("M"):
            continue
        template = group.iloc[-1].to_dict()
        count = int(group["value"].notna().sum())
        template.update(
            observation_date=period.to_timestamp("M"),
            source_observation_date=group["source_observation_date"].max(),
            period_start=period.start_time,
            period_end=period.to_timestamp("M"),
            final_frequency="monthly",
            reported_value=None,
            quality_flag="closed_calendar_month_mean_of_available_observations_no_fill",
            transformation_id="fred_daily_rate_monthly_arithmetic_mean_v1",
        )
        mean = {**template, "variable_id": variable.removesuffix("_daily") + "_monthly_average", "value": group["value"].mean()}
        diagnostic = {**template, "variable_id": variable.removesuffix("_daily") + "_monthly_observation_count", "value": count, "unit": "count", "transformation_id": "fred_daily_rate_monthly_count_v1"}
        outputs.append(pd.DataFrame([mean, diagnostic]))
    return finalize(pd.concat(outputs, ignore_index=True), output_path)
