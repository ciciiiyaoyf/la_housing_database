"""Validated reuse for archived clean observation-contract files.

These sources were previously parsed from official raw files into the shared
observation contract. The fallback is intentionally narrow and used only for
legacy HVS workbooks whose raw layout is irregular; the catalog marks this
limitation so it is not hidden.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


def clean_prepared_observation_contract(
    path: Path,
    record: dict[str, str],
    existing_clean_path: Path,
    output_path: Path,
) -> pd.DataFrame:
    if not existing_clean_path.exists():
        raise FileNotFoundError(
            f"Prepared clean contract not found: {existing_clean_path}"
        )
    frame = pd.read_parquet(existing_clean_path)
    if "source_filename" in frame and not frame["source_filename"].astype(str).eq(
        record["source_filename"]
    ).any():
        raise ValueError(
            f"Prepared clean file {existing_clean_path.name} is not tied to "
            f"raw file {record['source_filename']}"
        )
    for key, value in metadata_fields(record).items():
        frame[key] = value
    frame["quality_flag"] = frame.get("quality_flag", pd.Series(index=frame.index))
    frame["quality_flag"] = frame["quality_flag"].astype(str).replace("nan", "")
    frame["quality_flag"] = (
        frame["quality_flag"].where(frame["quality_flag"].ne(""), "prepared_clean_contract")
        + ";prepared_clean_contract"
    )
    return finalize(frame, output_path)
