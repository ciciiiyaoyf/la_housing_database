"""Strict California UI-claims and real-GDP constructions for the macro request."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.cleaning.common import finalize, metadata_fields
from src.ingestion.macro_claims import read_claims_csv

CLAIMS_SOURCE_ID = "dol_ca_ui_claims"
GDP_SOURCE_ID = "bea_ca_real_gdp"
CLAIMS_SERIES = {
    "initial": ("InitialClaims", "weekEnded"),
    "continued": ("ContinuedClaims", "ReflectingWeekEnded"),
}


def _claims_number(text: str | None) -> float:
    cleaned = (text or "").strip().replace(",", "")
    if cleaned in {"", "-", "NA", "N/A", "(NA)"}:
        return np.nan
    if not re.fullmatch(r"\d+", cleaned):
        raise ValueError(f"DOL claims must be nonnegative integer counts: {text!r}")
    return float(cleaned)


def _claims(path: Path, record: dict[str, str]) -> pd.DataFrame:
    payload = path.read_bytes()
    is_csv = not payload.lstrip().startswith(b"<")
    if is_csv:
        source = read_claims_csv(payload)
        native_series = {
            "initial": pd.DataFrame({"observation_date": source["rptdate"], "value": source["InitialClaims"]}),
            "continued": pd.DataFrame({"observation_date": source["c2"], "value": source["ContinuedClaims"]}),
        }
        native_transformation = "dol_ca_ui_claims_native_week_csv_v2"
        source_flag = "official_dol_ui539_nsa;includes_workshare_equivalents"
    else:
        # Archived provider XML remains readable; CSV and XML histories are
        # never stitched together within a single clean vintage.
        root = ET.fromstring(payload)
        weeks = root.findall("week")
        if root.tag != "r539cyState" or not weeks:
            raise ValueError("Expected a nonempty state UI 539 XML report")
        if any(week.findtext("stateName", "").strip() != "California" for week in weeks):
            raise ValueError("UI claims report includes a non-California geography")
        native_series = {
            name: pd.DataFrame({
                "observation_date": pd.to_datetime(
                    [week.findtext(date_tag) for week in weeks], format="%m/%d/%Y", errors="raise",
                ),
                "value": [_claims_number(week.findtext(value_tag)) for week in weeks],
            })
            for name, (value_tag, date_tag) in CLAIMS_SERIES.items()
        }
        native_transformation = "dol_ca_ui_claims_native_week_v1"
        source_flag = "official_dol_ui539_nsa"
    retrieved = pd.to_datetime(record["retrieved_at"], utc=True, errors="raise")
    if pd.isna(retrieved):
        raise ValueError("A retrieval date is required to identify closed claims months")
    as_of = retrieved.tz_convert(None).normalize()
    rows: list[dict] = []
    for name, native in native_series.items():
        native = native.sort_values("observation_date")
        dates = native["observation_date"]
        if dates.isna().any() or dates.duplicated().any():
            raise ValueError(f"Invalid or duplicate {name} claims week date")
        if not dates.dt.dayofweek.eq(5).all():
            raise ValueError(f"{name} claims dates must be Saturdays")
        if dates.gt(as_of).any():
            raise ValueError(f"{name} claims includes a future observation date")
        for item in native.itertuples(index=False):
            rows.append({
                "observation_date": item.observation_date,
                "source_observation_date": item.observation_date,
                "period_start": item.observation_date - pd.Timedelta(days=6),
                "period_end": item.observation_date,
                "variable_id": f"ca_{name}_claims_weekly",
                "value": item.value,
                "reported_value": item.value,
                "unit": "claims",
                "final_frequency": "weekly",
                "transformation_id": native_transformation,
                "quality_flag": (
                    source_flag if pd.notna(item.value) else f"{source_flag};source_missing"
                ),
            })
        periods = pd.period_range(dates.min().to_period("M"), dates.max().to_period("M"), freq="M")
        native_periods = dates.dt.to_period("M")
        for period in periods:
            if period >= as_of.to_period("M"):
                # Keep current-week source observations, but do not publish a
                # current-month row as a completed monthly control.
                continue
            part = native.loc[native_periods.eq(period)]
            valid = part.loc[part["value"].notna()]
            start, end = period.start_time, period.end_time.normalize()
            expected = pd.date_range(start, end, freq="W-SAT")
            complete = set(valid["observation_date"]) == set(expected)
            closed = end < as_of
            count = int(len(valid))
            flag = (
                "complete_closed_month" if complete and closed
                else "open_month" if not closed else "incomplete_week_coverage"
            )
            base = {
                "observation_date": end,
                "source_observation_date": part["observation_date"].max(),
                "period_start": start,
                "period_end": end,
                "reported_value": np.nan,
                "final_frequency": "monthly",
                "transformation_id": "dol_ca_ui_claims_complete_month_mean_v1",
                "quality_flag": f"{flag};weekly_observation_count={count};expected_saturdays={len(expected)};{source_flag}",
            }
            rows.append({
                **base,
                "variable_id": f"ca_{name}_claims_monthly_average",
                "value": float(valid["value"].mean()) if complete and closed else np.nan,
                "unit": "claims",
            })
            rows.append({
                **base,
                "variable_id": f"ca_{name}_claims_monthly_observation_count",
                "value": count,
                "unit": "weeks",
            })
    frame = pd.DataFrame(rows)
    frame["provider"] = "U.S. Department of Labor"
    frame["native_frequency"] = "weekly"
    frame["is_seasonally_adjusted"] = False
    return frame


def _gdp(path: Path, record: dict[str, str]) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if re.fullmatch(r"SQGDP9__ALL_AREAS_\d{4}_\d{4}\.csv", name)]
        if len(members) != 1:
            raise ValueError("BEA archive must contain exactly one SQGDP9 all-areas CSV")
        source_text = archive.read(members[0]).decode("latin1")
    source = pd.read_csv(io.StringIO(source_text), dtype=str)
    required = {"GeoFIPS", "GeoName", "TableName", "LineCode", "Description", "Unit"}
    if not required.issubset(source.columns):
        raise ValueError(f"BEA GDP missing columns: {sorted(required - set(source.columns))}")
    geographies = source["GeoFIPS"].str.strip().str.strip('"').str.strip()
    source = source.loc[geographies.eq("06000") & source["LineCode"].str.strip().eq("1")]
    if len(source) != 1:
        raise ValueError("BEA GDP requires exactly one California all-industry row")
    row = source.iloc[0]
    if row["GeoName"].strip() != "California" or row["TableName"].strip() != "SQGDP9":
        raise ValueError("BEA GDP row does not match California real GDP table SQGDP9")
    if row["Description"].strip() != "All industry total":
        raise ValueError("BEA GDP line 1 is not labeled All industry total")
    if row["Unit"].strip() != "Millions of chained 2017 dollars":
        raise ValueError("BEA GDP unit/base year changed; update the dictionary before ingestion")
    columns = [col for col in source.columns if re.fullmatch(r"\d{4}:Q[1-4]", col)]
    if not columns:
        raise ValueError("BEA GDP contains no quarterly periods")
    values = pd.Series(
        [pd.to_numeric(row[col], errors="coerce") for col in columns],
        index=pd.PeriodIndex([col.replace(":", "") for col in columns], freq="Q"),
        dtype=float,
    ).sort_index()
    if values.index.duplicated().any() or values.dropna().le(0).any() or values.dropna().empty:
        raise ValueError("BEA GDP contains duplicate periods, nonpositive levels, or no numeric levels")
    invalid = [col for col in columns if pd.isna(pd.to_numeric(row[col], errors="coerce")) and str(row[col]).strip() not in {"(NA)", "(D)", "", "nan"}]
    if invalid:
        raise ValueError(f"BEA GDP contains unexplained nonnumeric cells: {invalid}")
    rows = []
    for period, value in values.items():
        previous = values.get(period - 1, np.nan)
        year_ago = values.get(period - 4, np.nan)
        common = {
            "observation_date": period.end_time.normalize(),
            "source_observation_date": period.end_time.normalize(),
            "period_start": period.start_time,
            "period_end": period.end_time.normalize(),
        }
        rows.append({
            **common,
            "variable_id": "bea_ca_real_gdp_quarterly",
            "value": value,
            "reported_value": value,
            "unit": "millions_of_chained_2017_dollars_saar",
            "transformation_id": "bea_ca_sqgdp9_line1_native_v1",
            "quality_flag": "official_bea_sqgdp9_line1" if pd.notna(value) else "source_missing",
        })
        for variable, prior, exponent in (
            ("bea_ca_real_gdp_qoq_annualized_pct", previous, 4),
            ("bea_ca_real_gdp_yoy_pct", year_ago, 1),
        ):
            available = pd.notna(value) and pd.notna(prior)
            rows.append({
                **common,
                "variable_id": variable,
                "value": 100 * ((value / prior) ** exponent - 1) if available else np.nan,
                "reported_value": np.nan,
                "unit": "percent",
                "transformation_id": "bea_ca_real_gdp_exact_quarter_growth_v1",
                "quality_flag": "exact_calendar_lag_growth" if available else "missing_current_or_exact_lag",
            })
    frame = pd.DataFrame(rows)
    frame["provider"] = "U.S. Bureau of Economic Analysis"
    frame["native_frequency"] = "quarterly"
    frame["final_frequency"] = "quarterly"
    frame["is_seasonally_adjusted"] = True
    update = re.search(r"Last updated:\s*([A-Za-z]+ \d{1,2}, \d{4})", source_text)
    if update:
        frame["provider_last_updated"] = pd.Timestamp(update.group(1)).date().isoformat()
    return frame


def clean_macro_bea_claims(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    """Return the canonical long-format observations without filling missing data."""
    if source_id == CLAIMS_SOURCE_ID:
        frame = _claims(path, record)
    elif source_id == GDP_SOURCE_ID:
        frame = _gdp(path, record)
    else:
        raise ValueError(f"Unsupported macro BEA/claims source: {source_id}")
    frame["geo_id"] = "STATE_06"
    frame["state_fips"] = "06"
    frame["county_fips"] = None
    frame["is_imputed"] = False
    frame["is_interpolated"] = False
    for key, value in metadata_fields(record).items():
        if key == "provider_last_updated" and key in frame:
            continue
        frame[key] = value
    frame["source_id"] = source_id
    return finalize(frame, output_path)
