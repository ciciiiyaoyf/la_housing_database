"""Immutable acquisition helpers for Version A benchmark source files.

The benchmark layer has its own manifest because several inputs are large
annual microdata or manually exported dashboard products.  Keeping those
assets separate prevents the legacy monthly cleaner from accidentally
ingesting them while preserving the same checksum-and-vintage guarantees.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests

from src.common.io import append_csv, sha256_file


MANIFEST_COLUMNS = (
    "source_id",
    "provider",
    "dataset_name",
    "source_page_url",
    "download_url",
    "retrieved_at",
    "data_vintage",
    "source_filename",
    "original_filename",
    "relative_path",
    "byte_size",
    "checksum_sha256",
    "retrieval_method",
    "native_frequency",
    "source_geography",
    "ingestion_status",
    "notes",
)


def benchmark_manifest_path(root: Path) -> Path:
    """Return the dedicated Version A raw-file manifest path."""
    return root / "metadata" / "version_a_raw_file_manifest.csv"


def _manifest(root: Path) -> pd.DataFrame:
    path = benchmark_manifest_path(root)
    if not path.exists():
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    return pd.read_csv(path, dtype=str).reindex(columns=MANIFEST_COLUMNS)


def _record_for_checksum(
    root: Path, source_id: str, checksum: str
) -> tuple[Path, dict[str, str]] | None:
    frame = _manifest(root)
    match = frame.loc[
        frame["source_id"].eq(source_id)
        & frame["checksum_sha256"].eq(checksum)
    ]
    if match.empty:
        return None
    record = {
        key: "" if pd.isna(value) else str(value)
        for key, value in match.iloc[-1].to_dict().items()
    }
    path = root / record["relative_path"]
    return (path, record) if path.is_file() else None


def _safe_extension(path: Path, download_url: str = "") -> str:
    name = path.name or Path(urlparse(download_url).path).name
    suffixes = Path(name).suffixes
    extension = "".join(suffixes[-2:]) if suffixes[-2:] in (
        [".csv", ".gz"],
        [".tsv", ".gz"],
    ) else (suffixes[-1] if suffixes else ".bin")
    return extension.lower()


def archive_benchmark_file(
    root: Path,
    source_id: str,
    source_file: Path,
    *,
    provider: str,
    dataset_name: str,
    source_page_url: str,
    download_url: str = "",
    native_frequency: str,
    source_geography: str,
    data_vintage: str | None = None,
    retrieval_method: str = "manual_official_download_import",
    notes: str = "",
    force_duplicate: bool = False,
    retrieved_at: datetime | None = None,
) -> dict[str, str]:
    """Copy an official payload into immutable Version A raw storage.

    Identical payloads for the same source are reused by checksum unless
    ``force_duplicate`` is explicitly requested.  Existing archived files are
    never overwritten.
    """
    root = Path(root).resolve()
    source_file = Path(source_file).expanduser().resolve()
    if not source_file.is_file():
        raise FileNotFoundError(f"Benchmark source file does not exist: {source_file}")
    if not source_id or "/" in source_id or "\\" in source_id:
        raise ValueError("source_id must be a nonempty path-safe identifier")

    checksum = sha256_file(source_file)
    if not force_duplicate:
        existing = _record_for_checksum(root, source_id, checksum)
        if existing:
            return existing[1]

    retrieved = retrieved_at or datetime.now(timezone.utc)
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=timezone.utc)
    retrieved = retrieved.astimezone(timezone.utc)
    vintage = data_vintage or retrieved.date().isoformat()
    extension = _safe_extension(source_file, download_url)
    filename = (
        f"{source_id}_{vintage}_{retrieved:%Y%m%dT%H%M%SZ}_"
        f"{checksum[:12]}{extension}"
    )
    raw_dir = root / "data" / "raw" / "version_a" / source_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / filename
    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite immutable benchmark asset: {destination}"
        )

    temporary = destination.with_name(f".{destination.name}.part")
    try:
        shutil.copyfile(source_file, temporary)
        copied_checksum = sha256_file(temporary)
        if copied_checksum != checksum:
            raise IOError(
                "Checksum changed while archiving benchmark source: "
                f"{checksum} != {copied_checksum}"
            )
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    record: dict[str, Any] = {
        "source_id": source_id,
        "provider": provider,
        "dataset_name": dataset_name,
        "source_page_url": source_page_url,
        "download_url": download_url,
        "retrieved_at": retrieved.isoformat(),
        "data_vintage": vintage,
        "source_filename": destination.name,
        "original_filename": source_file.name,
        "relative_path": destination.relative_to(root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": retrieval_method,
        "native_frequency": native_frequency,
        "source_geography": source_geography,
        "ingestion_status": "archived",
        "notes": notes,
    }
    append_csv(benchmark_manifest_path(root), [record])
    return {key: str(value) for key, value in record.items()}


def download_benchmark_file(
    root: Path,
    source_id: str,
    download_url: str,
    *,
    provider: str,
    dataset_name: str,
    source_page_url: str,
    native_frequency: str,
    source_geography: str,
    data_vintage: str | None = None,
    notes: str = "",
    timeout: int = 300,
    session: requests.Session | None = None,
) -> dict[str, str]:
    """Download and archive one official Version A payload.

    The response is streamed to a temporary file and validated as nonempty
    before it enters immutable storage.
    """
    extension = _safe_extension(Path(urlparse(download_url).path), download_url)
    temporary_dir = root / "data" / "raw" / "version_a" / ".downloads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{source_id}{extension}.part"
    client = session or requests.Session()
    response = client.get(
        download_url,
        stream=True,
        timeout=timeout,
        headers={"User-Agent": "UCLA housing research data pipeline/0.2"},
    )
    try:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"Official source returned an empty file: {download_url}")
        return archive_benchmark_file(
            root,
            source_id,
            temporary,
            provider=provider,
            dataset_name=dataset_name,
            source_page_url=source_page_url,
            download_url=download_url,
            native_frequency=native_frequency,
            source_geography=source_geography,
            data_vintage=data_vintage,
            retrieval_method="official_url_download",
            notes=notes,
        )
    finally:
        response.close()
        if temporary.exists():
            temporary.unlink()


def latest_benchmark_asset(
    root: Path,
    source_id: str,
    *,
    vintage: str | None = None,
) -> tuple[Path, dict[str, str]] | None:
    """Return the latest available archived asset for a source.

    If ``vintage`` is supplied, select the latest retrieval on or before that
    UTC calendar date.  Missing files are skipped rather than silently
    returning stale manifest references.
    """
    frame = _manifest(root)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return None
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    if vintage:
        cutoff = pd.Timestamp(vintage)
        cutoff = (
            cutoff.tz_localize("UTC")
            if cutoff.tzinfo is None
            else cutoff.tz_convert("UTC")
        ) + pd.Timedelta(days=1)
        frame = frame.loc[frame["_retrieved"].lt(cutoff)]
    for _, row in frame.sort_values("_retrieved", ascending=False).iterrows():
        record = {
            key: "" if pd.isna(value) else str(value)
            for key, value in row.drop(labels="_retrieved").to_dict().items()
        }
        path = root / record["relative_path"]
        if path.is_file() and sha256_file(path) == record["checksum_sha256"]:
            return path, record
    return None


def benchmark_asset_for_data_vintage(
    root: Path,
    source_id: str,
    data_vintage: str,
) -> tuple[Path, dict[str, str]] | None:
    """Return the latest valid archive for an exact provider data vintage."""
    frame = _manifest(root)
    frame = frame.loc[
        frame["source_id"].eq(source_id)
        & frame["data_vintage"].eq(str(data_vintage))
    ].copy()
    if frame.empty:
        return None
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    for _, row in frame.sort_values("_retrieved", ascending=False).iterrows():
        record = {
            key: "" if pd.isna(value) else str(value)
            for key, value in row.drop(labels="_retrieved").to_dict().items()
        }
        path = root / record["relative_path"]
        if path.is_file() and sha256_file(path) == record["checksum_sha256"]:
            return path, record
    return None
