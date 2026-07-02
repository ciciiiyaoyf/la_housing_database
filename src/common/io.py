"""Filesystem and metadata helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


DIRECTORIES = (
    "config",
    "data/raw/zillow",
    "data/raw/redfin",
    "data/raw/bls",
    "data/raw/bls_ces",
    "data/raw/bls_cpi",
    "data/raw/freddie_mac",
    "data/raw/census_bps",
    "data/raw/census_hvs",
    "data/raw/census_acs",
    "data/raw/fhfa",
    "data/raw/bea",
    "data/raw/geography",
    "data/interim",
    "data/clean",
    "data/final",
    "database",
    "metadata",
    "output/qa",
    "output/exports",
    "output/coverage",
    "src/common",
    "src/ingestion",
    "src/cleaning",
    "src/geography",
    "src/harmonization",
    "src/features",
    "src/validation",
    "src/database",
    "src/reporting",
    "sql",
    "tests",
)


def ensure_directories(root: Path) -> None:
    for relative in DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_bps_ascii_payload(path: Path) -> bool:
    """Recognize the documented two-row BPS ASCII header, not HTTP error HTML."""
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            first = handle.readline()
            second = handle.readline()
        return first.startswith("Survey,") and second.startswith("Date,")
    except (OSError, UnicodeError):
        return False


def append_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = pd.DataFrame(rows)
    if path.exists():
        existing = pd.read_csv(path, dtype=str)
        frame = pd.concat([existing, incoming], ignore_index=True, sort=False)
    else:
        frame = incoming
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)


def source_archive_paths(
    root: Path, source_id: str, vintage: str | None = None
) -> list[tuple[Path, dict[str, str]]]:
    """Return one latest immutable raw file per BPS archive month."""
    manifest_path = root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return []
    frame = pd.read_csv(manifest_path, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return []
    frame["archive_period"] = frame.get(
        "archive_period", pd.Series(index=frame.index, dtype=str)
    )
    inferred = frame["download_url"].str.extract(
        r"/(?:co|st)(\d{2})(\d{2})c\.txt$", expand=True
    )
    inferred_period = inferred[0].map(
        lambda year: f"{2000 + int(year):04d}" if pd.notna(year) else None
    ) + "-" + inferred[1]
    frame["archive_period"] = frame["archive_period"].fillna(inferred_period)
    frame = frame.loc[frame["archive_period"].notna()]
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    if vintage:
        cutoff = pd.Timestamp(vintage, tz="UTC") + pd.Timedelta(days=1)
        frame = frame.loc[frame["_retrieved"] < cutoff]
    frame = (
        frame.sort_values("_retrieved")
        .groupby("archive_period", as_index=False)
        .tail(1)
        .sort_values("archive_period")
    )
    selected: list[tuple[Path, dict[str, str]]] = []
    for _, row in frame.iterrows():
        record = {
            key: str(value)
            for key, value in row.drop(labels="_retrieved").to_dict().items()
            if pd.notna(value)
        }
        path = root / record["relative_path"]
        if path.exists() and (
            not source_id.startswith("census_bps_")
            or is_bps_ascii_payload(path)
        ):
            selected.append((path, record))
    return selected


def atomic_json(path: Path, payload: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def latest_manifest_rows(root: Path) -> pd.DataFrame:
    path = root / "metadata" / "raw_file_manifest.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype=str).drop_duplicates(
        ["source_id", "checksum_sha256"], keep="last"
    )
    frame["retrieved_at_sort"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, format="mixed"
    )
    return (
        frame.sort_values("retrieved_at_sort")
        .groupby("source_id", as_index=False)
        .tail(1)
        .drop(columns="retrieved_at_sort")
    )


def latest_raw_path(root: Path, source_id: str) -> tuple[Path, dict[str, str]] | None:
    rows = latest_manifest_rows(root)
    if rows.empty:
        return None
    match = rows.loc[rows["source_id"] == source_id]
    if match.empty:
        return None
    record = {key: str(value) for key, value in match.iloc[-1].to_dict().items()}
    return root / record["relative_path"], record


def raw_path_for_vintage(
    root: Path, source_id: str, vintage: str | None = None
) -> tuple[Path, dict[str, str]] | None:
    if vintage is None:
        return latest_raw_path(root, source_id)
    path = root / "metadata" / "raw_file_manifest.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype=str)
    match = frame.loc[
        frame["source_id"].eq(source_id) & frame["data_vintage"].eq(vintage)
    ]
    if match.empty:
        return None
    match = match.assign(
        _retrieved=pd.to_datetime(
            match["retrieved_at"], utc=True, errors="coerce", format="mixed"
        )
    ).sort_values("_retrieved")
    record = {
        key: str(value)
        for key, value in match.iloc[-1].drop(labels="_retrieved").to_dict().items()
    }
    raw_path = root / record["relative_path"]
    return (raw_path, record) if raw_path.exists() else None
