from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.benchmark.acquisition import (
    MANIFEST_COLUMNS,
    archive_benchmark_file,
    benchmark_asset_for_data_vintage,
    benchmark_manifest_path,
    download_benchmark_file,
    latest_benchmark_asset,
)


def _archive(
    root: Path,
    source: Path,
    retrieved_at: datetime,
    *,
    release_date: str = "2024-01-01",
) -> dict[str, str]:
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
        release_date=release_date,
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
    assert manifest.loc[0, "release_date"] == "2024-01-01"


def test_release_date_is_part_of_dedupe_and_cache_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "official.csv"
    source.write_text("county,value\n06037,1\n", encoding="utf-8")
    first = _archive(
        tmp_path,
        source,
        datetime(2024, 1, 16, tzinfo=timezone.utc),
        release_date="2024-01-15",
    )
    second = _archive(
        tmp_path,
        source,
        datetime(2024, 2, 16, tzinfo=timezone.utc),
        release_date="2024-02-15T12:30:00Z",
    )

    assert first["checksum_sha256"] == second["checksum_sha256"]
    assert first["relative_path"] != second["relative_path"]
    assert second["release_date"] == "2024-02-15"
    _, selected_first = benchmark_asset_for_data_vintage(
        tmp_path,
        "official_test",
        "2023",
        release_date="2024-01-15",
    )
    _, selected_second = benchmark_asset_for_data_vintage(
        tmp_path,
        "official_test",
        "2023",
        release_date="2024-02-15",
    )
    assert selected_first["relative_path"] == first["relative_path"]
    assert selected_second["relative_path"] == second["relative_path"]


def test_legacy_manifest_read_and_next_write_migrate_release_header(
    tmp_path: Path,
) -> None:
    source = tmp_path / "official.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    _archive(
        tmp_path,
        source,
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    manifest_path = (
        tmp_path / "metadata/version_a_raw_file_manifest.csv"
    )
    legacy = pd.read_csv(manifest_path, dtype=str).drop(
        columns="release_date"
    )
    legacy.to_csv(manifest_path, index=False)

    _, legacy_record = benchmark_asset_for_data_vintage(
        tmp_path,
        "official_test",
        "2023",
    )
    assert legacy_record["release_date"] == ""

    source.write_text("value\n2\n", encoding="utf-8")
    _archive(
        tmp_path,
        source,
        datetime(2024, 2, 16, tzinfo=timezone.utc),
        release_date="2024-02-15",
    )
    migrated = pd.read_csv(manifest_path, dtype=str)
    assert tuple(migrated.columns) == MANIFEST_COLUMNS
    assert pd.isna(migrated.loc[0, "release_date"])
    assert migrated.loc[1, "release_date"] == "2024-02-15"


def test_archive_rejects_release_after_retrieval(tmp_path: Path) -> None:
    source = tmp_path / "official.csv"
    source.write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be later than retrieved_at"):
        _archive(
            tmp_path,
            source,
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            release_date="2024-01-03",
        )

    assert not benchmark_manifest_path(tmp_path).exists()


def test_selector_rejects_impossible_legacy_release_timing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "official.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    _archive(
        tmp_path,
        source,
        datetime(2024, 1, 2, tzinfo=timezone.utc),
        release_date="2024-01-01",
    )
    manifest_path = benchmark_manifest_path(tmp_path)
    manifest = pd.read_csv(manifest_path, dtype=str)
    manifest.loc[0, "release_date"] = "2024-01-03"
    manifest.to_csv(manifest_path, index=False)

    with pytest.raises(ValueError, match="cannot be later than retrieved_at"):
        benchmark_asset_for_data_vintage(
            tmp_path,
            "official_test",
            "2023",
            release_date="2024-01-03",
        )


def test_identical_bytes_keep_distinct_vintage_and_forced_retrieval_records(
    tmp_path: Path,
) -> None:
    source = tmp_path / "official.csv"
    source.write_text("county,value\n06037,1\n", encoding="utf-8")
    first = archive_benchmark_file(
        tmp_path,
        "official_test",
        source,
        provider="Official agency",
        dataset_name="Published table",
        source_page_url="https://example.gov/table",
        native_frequency="annual",
        source_geography="county",
        data_vintage="2023",
        retrieved_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    new_vintage = archive_benchmark_file(
        tmp_path,
        "official_test",
        source,
        provider="Official agency",
        dataset_name="Published table",
        source_page_url="https://example.gov/table",
        native_frequency="annual",
        source_geography="county",
        data_vintage="2024",
        retrieved_at=datetime(2024, 2, 2, tzinfo=timezone.utc),
    )
    forced = archive_benchmark_file(
        tmp_path,
        "official_test",
        source,
        provider="Official agency",
        dataset_name="Published table",
        source_page_url="https://example.gov/table",
        native_frequency="annual",
        source_geography="county",
        data_vintage="2024",
        retrieved_at=datetime(2024, 3, 2, tzinfo=timezone.utc),
        force_duplicate=True,
    )

    assert first["data_vintage"] == "2023"
    assert new_vintage["data_vintage"] == forced["data_vintage"] == "2024"
    assert len(
        {
            first["relative_path"],
            new_vintage["relative_path"],
            forced["relative_path"],
        }
    ) == 3
    manifest = pd.read_csv(
        tmp_path / "metadata/version_a_raw_file_manifest.csv", dtype=str
    )
    assert len(manifest) == 3


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


def test_streamed_download_preserves_provider_file_extension(
    tmp_path: Path,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size > 0
            yield b"official zip payload"

        def close(self) -> None:
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    record = download_benchmark_file(
        tmp_path,
        "official_zip",
        "https://example.gov/data/official.zip",
        provider="Official agency",
        dataset_name="Published microdata",
        source_page_url="https://example.gov/data",
        native_frequency="annual",
        source_geography="state",
        data_vintage="2024",
        release_date="2025-03-01T12:00:00Z",
        session=Session(),
    )

    assert record["source_filename"].endswith(".zip")
    assert not record["source_filename"].endswith(".part")
    assert (tmp_path / record["relative_path"]).suffix == ".zip"
    assert record["release_date"] == "2025-03-01"
