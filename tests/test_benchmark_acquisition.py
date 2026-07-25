from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.benchmark.acquisition import (
    archive_benchmark_file,
    benchmark_asset_for_data_vintage,
    latest_benchmark_asset,
)


def _archive(root: Path, source: Path, retrieved_at: datetime) -> dict[str, str]:
    return archive_benchmark_file(
        root,
        "official_test",
        source,
        provider="Official agency",
        dataset_name="Published table",
        source_page_url="https://example.gov/table",
        native_frequency="annual",
        source_geography="county",
        data_vintage="2023",
        retrieved_at=retrieved_at,
    )


def test_archive_is_checksummed_immutable_and_reused(tmp_path: Path) -> None:
    source = tmp_path / "official.csv"
    source.write_text("county,value\n06037,1\n", encoding="utf-8")
    first = _archive(
        tmp_path, source, datetime(2024, 1, 2, tzinfo=timezone.utc)
    )
    second = _archive(
        tmp_path, source, datetime(2024, 2, 2, tzinfo=timezone.utc)
    )

    assert first == second
    archived = tmp_path / first["relative_path"]
    assert archived.read_bytes() == source.read_bytes()
    manifest = pd.read_csv(
        tmp_path / "metadata/version_a_raw_file_manifest.csv", dtype=str
    )
    assert len(manifest) == 1
    assert manifest.loc[0, "checksum_sha256"] == first["checksum_sha256"]


def test_latest_asset_respects_retrieval_cutoff(tmp_path: Path) -> None:
    source = tmp_path / "official.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    old = _archive(
        tmp_path, source, datetime(2024, 1, 2, tzinfo=timezone.utc)
    )
    source.write_text("value\n2\n", encoding="utf-8")
    new = _archive(
        tmp_path, source, datetime(2024, 2, 2, tzinfo=timezone.utc)
    )

    latest_path, latest = latest_benchmark_asset(
        tmp_path, "official_test"
    )
    cutoff_path, cutoff = latest_benchmark_asset(
        tmp_path, "official_test", vintage="2024-01-31"
    )

    assert latest["checksum_sha256"] == new["checksum_sha256"]
    assert latest_path.read_text(encoding="utf-8").endswith("2\n")
    assert cutoff["checksum_sha256"] == old["checksum_sha256"]
    assert cutoff_path.read_text(encoding="utf-8").endswith("1\n")
    vintage_path, vintage = benchmark_asset_for_data_vintage(
        tmp_path, "official_test", "2023"
    )
    assert vintage["checksum_sha256"] == new["checksum_sha256"]
    assert vintage_path == latest_path
