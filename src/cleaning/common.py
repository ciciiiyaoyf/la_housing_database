"""Shared clean-table contract."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

OBSERVATION_COLUMNS = [
    "observation_date",
    "source_observation_date",
    "period_start",
    "period_end",
    "geo_id",
    "state_fips",
    "county_fips",
    "variable_id",
    "value",
    "reported_value",
    "unit",
    "provider",
    "native_frequency",
    "final_frequency",
    "source_vintage",
    "retrieved_at",
    "source_filename",
    "checksum_sha256",
    "provider_last_updated",
    "transformation_id",
    "is_imputed",
    "is_interpolated",
    "is_seasonally_adjusted",
    "quality_flag",
]


def month_end(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    return parsed.dt.to_period("M").dt.to_timestamp("M")


def empty_observations() -> pd.DataFrame:
    return pd.DataFrame(columns=OBSERVATION_COLUMNS)


def finalize(frame: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    for column in OBSERVATION_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame = frame[OBSERVATION_COLUMNS].copy()
    for column in ("observation_date", "source_observation_date", "period_start", "period_end"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["reported_value"] = pd.to_numeric(frame["reported_value"], errors="coerce")
    frame["is_imputed"] = frame["is_imputed"].astype("boolean")
    frame["is_interpolated"] = frame["is_interpolated"].astype("boolean")
    frame["is_seasonally_adjusted"] = frame["is_seasonally_adjusted"].astype(
        "boolean"
    )
    frame["checksum_sha256"] = frame["checksum_sha256"].astype("string")
    frame["provider_last_updated"] = frame["provider_last_updated"].astype("string")
    frame = frame.dropna(subset=["observation_date", "geo_id", "variable_id"])
    frame = frame.sort_values(["geo_id", "variable_id", "observation_date"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False)
    return frame


def metadata_fields(record: dict[str, str]) -> dict[str, str]:
    return {
        "source_vintage": record["data_vintage"],
        "retrieved_at": record["retrieved_at"],
        "source_filename": record["source_filename"],
        "checksum_sha256": record.get("checksum_sha256"),
        "provider_last_updated": (
            record.get("provider_last_updated")
            or record.get("source_last_updated")
        ),
    }
