"""Source-faithful CES industry employment and earnings with exact-month YoY.

The contract is California and the published Los Angeles-Long Beach-Glendale
metropolitan division.  Both use NSA all-employee CES series.  The division is
never silently relabeled as a county, and provider missing values are retained.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


SOURCE_ID = "bls_ces_ca_la_industry_earnings"
LEVEL_TRANSFORMATION_ID = "bls_ces_ca_la_industry_earnings_month_end_v1"
GROWTH_TRANSFORMATION_ID = "bls_ces_ca_la_exact_12_month_arithmetic_yoy_v1"

INDUSTRIES = {
    "20000000": "construction",
    "50000000": "information",
    "55000000": "financial_activities",
    "60000000": "professional_business_services",
    "70000000": "leisure_hospitality",
}
GEOGRAPHIES = {"00000": "STATE_06", "31084": "METRODIV_31084"}

# series_id -> (variable_id, geo_id, unit); identifiers are provider-published.
SERIES = {
    f"SMU06{area}{industry}01": (
        f"ces_{name}_employment_nsa", geo_id, "thousands_jobs"
    )
    for area, geo_id in GEOGRAPHIES.items()
    for industry, name in INDUSTRIES.items()
}
SERIES.update({
    f"SMU06{area}0500000003": (
        "ces_average_hourly_earnings_total_private_nsa", geo_id, "USD/hour"
    )
    for area, geo_id in GEOGRAPHIES.items()
})
BLS_CES_LABOR_SERIES_IDS = tuple(SERIES)


def _number(value: object, series_id: str) -> float:
    """Retain documented-style publication nulls, reject corrupt numbers."""
    if value is None or str(value).strip() in {"", "-", "--", ".", "NA", "N/A", "(NA)"}:
        return float("nan")
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CES {series_id} has an invalid numeric value: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"CES {series_id} has a nonfinite numeric value: {value!r}")
    return result


def _footnotes(item: dict) -> tuple[str, ...]:
    notes = item.get("footnotes", [])
    if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
        raise ValueError("CES observation has invalid footnotes")
    return tuple(sorted({
        ": ".join(str(note.get(key) or "").strip() for key in ("code", "text")
                  if str(note.get(key) or "").strip())
        for note in notes
        if note.get("code") or note.get("text")
    }))


def clean_macro_labor(
    path: Path, record: dict[str, str], output_path: Path
) -> pd.DataFrame:
    """Clean the archived BLS wrapper and derive 100*(x_t/x_(t-12)-1).

    Identical overlapping response rows are deduplicated. Conflicting duplicates
    fail instead of choosing a revision without a vintage rule. Empty historical
    response chunks are allowed (AHE begins in 2007), but all 12 series must have
    monthly observations somewhere in the complete source archive.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid CES raw JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError("CES raw archive must be an object")
    declared = payload.get("series_ids")
    if not isinstance(declared, list) or set(declared) != set(SERIES):
        raise ValueError("CES archive must declare exactly all 12 configured series")
    if len(declared) != len(SERIES):
        raise ValueError("CES archive declares duplicate series IDs")
    responses = payload.get("responses")
    if not isinstance(responses, list) or not responses:
        raise ValueError("CES archive has no responses")
    if record.get("source_id") != SOURCE_ID:
        raise ValueError(f"CES cleaner requires source_id {SOURCE_ID}")

    observations: dict[tuple[str, pd.Period], tuple[float, tuple[str, ...]]] = {}
    populated_series: set[str] = set()
    for response in responses:
        if not isinstance(response, dict) or response.get("status") != "REQUEST_SUCCEEDED":
            raise ValueError("CES archive contains an unsuccessful API response")
        results = response.get("Results")
        series_list = results.get("series") if isinstance(results, dict) else None
        if not isinstance(series_list, list):
            raise ValueError("CES response has no Results.series list")
        for series in series_list:
            if not isinstance(series, dict) or series.get("seriesID") not in SERIES:
                raise ValueError("CES response contains an unsupported series")
            series_id = series["seriesID"]
            data = series.get("data")
            if not isinstance(data, list):
                raise ValueError(f"CES {series_id} has no data list")
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError(f"CES {series_id} has an invalid observation")
                period = str(item.get("period", ""))
                if period == "M13" or not period.startswith("M"):
                    continue
                if period not in {f"M{month:02d}" for month in range(1, 13)}:
                    raise ValueError(f"CES {series_id} has an invalid monthly period: {period}")
                try:
                    month = pd.Period(year=int(item["year"]), month=int(period[1:]), freq="M")
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"CES {series_id} has an invalid year") from exc
                if "value" not in item:
                    raise ValueError(f"CES {series_id} observation is missing its value field")
                value, notes = _number(item["value"], series_id), _footnotes(item)
                key = (series_id, month)
                if key in observations:
                    old_value, old_notes = observations[key]
                    equal_value = value == old_value or (math.isnan(value) and math.isnan(old_value))
                    if not equal_value or notes != old_notes:
                        raise ValueError(f"Conflicting duplicate CES observation: {series_id} {month}")
                else:
                    observations[key] = (value, notes)
                populated_series.add(series_id)
    missing = sorted(set(SERIES) - populated_series)
    if missing:
        raise ValueError(f"CES archive missing configured monthly series: {missing}")

    records = []
    source_metadata = metadata_fields(record)
    for (series_id, month), (value, notes) in observations.items():
        variable_id, geo_id, unit = SERIES[series_id]
        base = {
            "source_observation_date": month.start_time,
            "observation_date": month.to_timestamp("M"),
            "period_start": month.start_time,
            "period_end": month.to_timestamp("M"),
            "geo_id": geo_id,
            "state_fips": "06",
            "county_fips": None,
            "provider": "U.S. Bureau of Labor Statistics",
            "native_frequency": "monthly",
            "final_frequency": "monthly",
            "is_imputed": False,
            "is_interpolated": False,
            "is_seasonally_adjusted": False,
            **source_metadata,
        }
        level_notes = list(notes)
        if math.isnan(value):
            level_notes.append("provider_published_missing")
        records.append({
            **base, "variable_id": variable_id, "unit": unit,
            "value": value, "reported_value": value,
            "transformation_id": LEVEL_TRANSFORMATION_ID,
            "quality_flag": " | ".join(level_notes) or None,
        })
        lag = observations.get((series_id, month - 12))
        growth = float("nan")
        growth_notes = [f"current:{note}" for note in notes]
        if lag is not None:
            growth_notes.extend(f"lag12:{note}" for note in lag[1])
        if math.isnan(value):
            growth_notes.append("current_provider_published_missing")
        if lag is None:
            growth_notes.append("exact_12_month_lag_unavailable")
        elif math.isnan(lag[0]):
            growth_notes.append("lag12_provider_published_missing")
        elif lag[0] <= 0:
            growth_notes.append("lag12_nonpositive_denominator")
        elif not math.isnan(value):
            growth = 100.0 * (value / lag[0] - 1.0)
        records.append({
            **base, "variable_id": variable_id + "_yoy_pct", "unit": "percent",
            "value": growth, "reported_value": None,
            "transformation_id": GROWTH_TRANSFORMATION_ID,
            "quality_flag": " | ".join(growth_notes) or None,
        })
    return finalize(pd.DataFrame(records), output_path)
