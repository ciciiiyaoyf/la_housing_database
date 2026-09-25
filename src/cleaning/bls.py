"""BLS LAUS API response cleaning."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields
from src.geography.comparable_counties import (
    COMPARABLE_LAUS_SERIES,
    PRIMARY_COUNTY_FIPS,
    PRIMARY_LAUS_SERIES_IDS,
)

SERIES = {
    series_id: (variable_id, unit)
    for series_id, (_, variable_id, unit) in COMPARABLE_LAUS_SERIES.items()
}
SERIES_COUNTY_FIPS = {
    series_id: county_fips
    for series_id, (county_fips, _, _) in COMPARABLE_LAUS_SERIES.items()
}


def clean_bls(
    path: Path, record: dict[str, str], output_path: Path
) -> pd.DataFrame:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid BLS raw JSON: {path.name}") from exc
    responses = payload.get("responses") if isinstance(payload, dict) else None
    if not isinstance(responses, list) or not responses:
        raise ValueError(
            f"BLS raw archive has no responses: {path.name}"
        )
    records: list[dict] = []
    found_series: set[str] = set()
    for response in responses:
        results = response.get("Results") if isinstance(response, dict) else None
        series_list = (
            results.get("series")
            if isinstance(results, dict)
            else None
        )
        if not isinstance(series_list, list):
            raise ValueError(
                f"BLS raw response has no Results.series list: {path.name}"
            )
        for series in series_list:
            if series["seriesID"] not in SERIES:
                continue
            series_id = series["seriesID"]
            found_series.add(series_id)
            variable_id, unit = SERIES[series_id]
            county_fips = SERIES_COUNTY_FIPS[series_id]
            for item in series["data"]:
                period = str(item["period"])
                if not period.startswith("M") or period == "M13":
                    continue
                records.append(
                    {
                        "year": int(item["year"]),
                        "month": int(period[1:]),
                        "variable_id": variable_id,
                        "unit": unit,
                        "county_fips": county_fips,
                        "value": item["value"],
                        "footnotes": "|".join(
                            note.get("code", "")
                            for note in item.get("footnotes", [])
                            if note.get("code")
                        ),
                    }
                )
    declared_series_raw = payload.get("series_ids") if isinstance(payload, dict) else None
    if isinstance(declared_series_raw, list) and declared_series_raw:
        expected_series = {str(series_id) for series_id in declared_series_raw}
        unknown_series = sorted(expected_series - set(SERIES))
        if unknown_series:
            raise ValueError(
                f"BLS raw archive declares unsupported series: {unknown_series}"
            )
    elif found_series - set(PRIMARY_LAUS_SERIES_IDS):
        expected_series = set(SERIES)
    else:
        # Preserve compatibility with archived LA-only responses created before
        # the comparison-county source expansion.
        expected_series = set(PRIMARY_LAUS_SERIES_IDS)
    missing_series = sorted(expected_series - found_series)
    if missing_series:
        raise ValueError(
            f"BLS raw archive missing configured series: {missing_series}"
        )
    if not records:
        raise ValueError(
            f"BLS raw archive contains no monthly observations: {path.name}"
        )
    frame = pd.DataFrame(records).drop_duplicates(
        ["year", "month", "county_fips", "variable_id"], keep="last"
    )
    source_dates = pd.to_datetime(
        dict(year=frame["year"], month=frame["month"], day=1)
    )
    frame["source_observation_date"] = source_dates
    frame["observation_date"] = source_dates.dt.to_period("M").dt.to_timestamp("M")
    frame["period_start"] = source_dates
    frame["period_end"] = frame["observation_date"]
    frame["state_fips"] = "06"
    frame["county_fips"] = frame["county_fips"].astype("string")
    frame["geo_id"] = "COUNTY_" + frame["county_fips"]
    frame["provider"] = "U.S. Bureau of Labor Statistics"
    frame["native_frequency"] = "monthly"
    frame["final_frequency"] = "monthly"
    for key, value in metadata_fields(record).items():
        frame[key] = value
    frame["transformation_id"] = (
        "bls_laus_api_comparable_county_month_end_v1"
        if frame["county_fips"].ne(PRIMARY_COUNTY_FIPS).any()
        else "bls_laus_api_month_end_v1"
    )
    frame["is_imputed"] = False
    frame["is_interpolated"] = False
    frame["is_seasonally_adjusted"] = False
    frame["quality_flag"] = frame["footnotes"].replace("", None)
    return finalize(frame, output_path)
