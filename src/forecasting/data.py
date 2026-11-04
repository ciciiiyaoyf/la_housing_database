"""Week 1 monthly inputs from the project's active, revised DuckDB facts."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.geography.comparable_counties import PRIMARY_COUNTY_FIPS

LA = f"COUNTY_{PRIMARY_COUNTY_FIPS}"
PRICE_GEOGRAPHIES = (LA, "COUNTY_06059", "COUNTY_06065", "COUNTY_06073", "STATE_06")
COUNTY_SOURCES = {
    "zhvi": "zillow_county_zhvi",
    "zori_smoothed_sa": "zillow_county_zori_smoothed_sa",
    "active_inventory": "redfin_county_monthly",
    "permits_total": "census_bps_county",
}
FORECAST_TRANSFORMS = {
    "zhvi": "Level in USD and 100 times the exact one-month natural-log difference.",
    "zori_smoothed_sa": "100 times the exact one-month natural-log difference, restored to USD/month for scoring.",
    "active_inventory": "Monthly county inventory level and (when positive) exact one-month log growth.",
    "permits_total": "Monthly permitted units and the arithmetic mean of months t, t-1 and t-2.",
}
FACT_TABLE = "analytics.fact_housing_observation"
INPUT_QUERY = f"""
    SELECT f.geo_id, f.state_fips, f.county_fips, f.variable_id, f.observation_date,
           f.value, f.unit, f.provider, f.native_frequency, f.final_frequency, f.source_id,
           f.source_vintage, f.retrieved_at, f.source_filename, f.checksum_sha256,
           f.transformation_id, f.is_imputed, f.is_interpolated, f.is_seasonally_adjusted,
           f.quality_flag, m.original_filename, m.relative_path AS archive_path, m.download_url,
           m.geography_levels AS source_geographies
    FROM {FACT_TABLE} AS f
    LEFT JOIN raw.file_manifest AS m
      ON (f.source_id, f.source_vintage, f.retrieved_at, f.source_filename, f.checksum_sha256)
       = (m.source_id, m.data_vintage, m.retrieved_at, m.source_filename, m.checksum_sha256)
    WHERE (f.geo_id = ? AND f.variable_id IN (SELECT unnest(?)))
       OR (f.variable_id = 'zhvi' AND f.geo_id IN (SELECT unnest(?)))
    ORDER BY f.geo_id, f.variable_id, f.observation_date
"""


def _check_series(rows: pd.DataFrame, geo: pd.Series, variable: pd.Series) -> None:
    """Fail on ambiguous observations, untraceable inputs or changes to source semantics."""
    months = rows["observation_date"]
    variable_id = variable.name
    geo_id = geo.name
    if (months.isna().any() or not months.dt.is_month_end.all()
            or not months.eq(months.dt.normalize()).all() or months.duplicated().any()):
        raise ValueError(f"{geo_id}/{variable_id}: expected one observation per calendar month-end")
    if rows["archive_path"].isna().any() or not rows["checksum_sha256"].str.fullmatch(
        r"[0-9a-fA-F]{64}", na=False
    ).all():
        raise ValueError(f"{geo_id}/{variable_id}: source lineage does not match the raw file manifest")

    level = geo["geo_type"]
    if not rows["source_geographies"].fillna("").str.contains(rf"\b{level}\b").all():
        raise ValueError(f"{geo_id}/{variable_id}: source file does not cover {level} geography")
    if level == "county":
        if not rows["source_id"].eq(COUNTY_SOURCES[variable_id]).fillna(False).all():
            raise ValueError(f"{geo_id}/{variable_id}: unexpected county source")
        if not rows["county_fips"].eq(geo["county_fips"]).fillna(False).all():
            raise ValueError(f"{geo_id}/{variable_id}: inconsistent county identifier")
    elif not rows["county_fips"].isna().all():
        raise ValueError(f"{geo_id}/{variable_id}: county values cannot represent a state series")
    if not rows["state_fips"].eq(geo["state_fips"]).fillna(False).all():
        raise ValueError(f"{geo_id}/{variable_id}: inconsistent state identifier")
    if not rows[["native_frequency", "final_frequency"]].eq("monthly").fillna(False).all().all():
        raise ValueError(f"{geo_id}/{variable_id}: expected native monthly observations")
    if rows["unit"].isna().any() or not rows["unit"].eq(variable["unit"]).all():
        raise ValueError(f"{geo_id}/{variable_id}: inconsistent units")
    if rows["is_interpolated"].isna().any() or rows["is_interpolated"].any():
        raise ValueError(f"{geo_id}/{variable_id}: interpolated or unclassified observation")
    observed = rows["value"].dropna()
    if not np.isfinite(observed).all() or observed.lt(0).any():
        raise ValueError(f"{geo_id}/{variable_id}: invalid level")
    if variable_id in {"zhvi", "zori_smoothed_sa"}:
        if observed.eq(0).any() or not rows["is_seasonally_adjusted"].eq(True).fillna(False).all():
            raise ValueError(f"{geo_id}/{variable_id}: expected positive, seasonally adjusted Zillow levels")


def _coverage(series: pd.Series) -> dict:
    valid = series.notna()
    # Number each uninterrupted run by the number of missing months before it.
    runs = valid.groupby((~valid).cumsum()).sum()
    return {
        "first_month": series.index.min(),
        "last_month": series.index.max(),
        "expected_months": len(series),
        "observed_months": int(valid.sum()),
        "missing_months": int((~valid).sum()),
        "missing_month_dates": ", ".join(series.index[~valid].strftime("%Y-%m")),
        "longest_contiguous_months": int(runs.max()) if len(series) else 0,
    }


def load_inputs(database: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return monthly levels with row lineage and one inventory row per requested series.

    Only the observed series span is regularized. An absent month has no value or
    source record. State ZHVI is checked directly, without aggregating counties.
    """
    with duckdb.connect(str(database), read_only=True) as connection:
        geographies = connection.execute(
            "SELECT geo_id, geo_name, geo_type, state_fips, county_fips FROM metadata.geography "
            "WHERE geo_id IN (SELECT unnest(?))", [list(PRICE_GEOGRAPHIES)]
        ).df().set_index("geo_id")
        variables = connection.execute(
            "SELECT variable_id, display_name, definition, unit, smoothing, seasonal_adjustment, "
            "methodological_notes, transformation FROM metadata.variable_dictionary "
            "WHERE variable_id IN (SELECT unnest(?))", [list(COUNTY_SOURCES)]
        ).df().set_index("variable_id")
        facts = connection.execute(INPUT_QUERY, [LA, list(COUNTY_SOURCES), list(PRICE_GEOGRAPHIES)]).df()

    if not geographies.index.is_unique or not variables.index.is_unique:
        raise ValueError("Week 1 geography and variable metadata require unique identifiers")
    requested = [(geo_id, "zhvi") for geo_id in PRICE_GEOGRAPHIES]
    requested += [(LA, var) for var in COUNTY_SOURCES if var != "zhvi"]
    inventory, observations = [], []
    for geo_id, variable_id in requested:
        geo, variable = geographies.loc[geo_id], variables.loc[variable_id]
        rows = facts.loc[facts["geo_id"].eq(geo_id) & facts["variable_id"].eq(variable_id)].copy()
        if not rows.empty:
            _check_series(rows, geo, variable)
        rows = rows.set_index("observation_date")
        full_index = pd.DatetimeIndex([], name="observation_date")
        if not rows.empty:
            full_index = pd.date_range(rows.index.min(), rows.index.max(), freq="ME", name="observation_date")
        calendar = rows.reindex(full_index)
        if not calendar.empty:
            calendar["geo_id"] = geo_id
            calendar["geo_name"] = geo["geo_name"]
            calendar["variable_id"] = variable_id
            calendar["source_row_present"] = calendar.index.isin(rows.index)
            observations.append(calendar.reset_index())
        sa_flags = rows["is_seasonally_adjusted"].dropna().unique()
        sa_label = ", ".join("SA" if flag else "NSA" for flag in sorted(sa_flags))
        inventory.append({
            "geo_id": geo_id, "geo_name": geo["geo_name"], "geo_type": geo["geo_type"],
            "state_fips": geo["state_fips"], "county_fips": geo["county_fips"],
            "variable_id": variable_id, "display_name": variable["display_name"],
            "definition": variable["definition"], "unit": variable["unit"],
            "frequency": "monthly", "seasonal_adjustment": sa_label or None,
            "smoothing": variable["smoothing"], "methodological_notes": variable["methodological_notes"],
            "source_transformation": variable["transformation"],
            "forecast_transformation": FORECAST_TRANSFORMS[variable_id],
            "fact_table": FACT_TABLE, "source_ids": ", ".join(sorted(rows["source_id"].unique())),
            "source_rows": len(rows), "distinct_source_files": rows["source_filename"].nunique(),
            "provider_imputed_months": int(rows["is_imputed"].sum()), **_coverage(calendar["value"]),
            "status": "available" if calendar["value"].notna().any() else "unavailable",
        })
    if not observations:
        raise ValueError("No Week 1 monthly observations in the active DuckDB")
    return pd.concat(observations, ignore_index=True), pd.DataFrame(inventory)
