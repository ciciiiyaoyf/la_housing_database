from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.common.config import ProjectConfig, SourceConfig
from src.ingestion.discover import discover_sources


def _config(root: Path) -> ProjectConfig:
    (root / "metadata").mkdir()
    sources = tuple(
        SourceConfig(
            source_id=source_id,
            provider="Test provider",
            dataset_name=f"Dataset {source_id}",
            source_page_url="https://example.test/data",
            download_url="https://example.test/data.csv",
            native_frequency="monthly",
            geography_levels=("county",),
            file_format="csv",
        )
        for source_id in ("first", "second")
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-09-25",
        sources=sources,
        pipeline={"request_timeout_seconds": 1},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def test_filtered_discovery_preserves_unrelated_records_and_replaces_selected(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    path = tmp_path / "metadata" / "source_discovery.csv"
    previous = pd.DataFrame(
        [
            {"source_id": "first", "discovery_status": "old", "county_fips": "06037"},
            {"source_id": "second", "discovery_status": "verified", "county_fips": "06059"},
            {"source_id": "retired_source", "discovery_status": "historical", "county_fips": "06001"},
            {"source_id": "first", "discovery_status": "duplicate_old", "county_fips": "06037"},
        ]
    )
    previous.to_csv(path, index=False)

    returned = discover_sources(config, source_filter="first", offline=True)

    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert returned["source_id"].tolist() == ["first"]
    assert saved["source_id"].tolist() == ["second", "retired_source", "first"]
    pd.testing.assert_frame_equal(
        saved.iloc[:2].loc[:, previous.columns].reset_index(drop=True),
        previous.iloc[1:3].reset_index(drop=True),
    )
    assert saved.loc[saved["source_id"].eq("first"), "discovery_status"].item() == "configured"
    assert saved.loc[saved["source_id"].eq("first"), "checked_at"].item()


def test_full_discovery_replaces_report_with_current_configured_sources(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    path = tmp_path / "metadata" / "source_discovery.csv"
    path.write_text("source_id,obsolete_column\nretired_source,old\n", encoding="utf-8")

    returned = discover_sources(config, offline=True)

    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert returned["source_id"].tolist() == ["first", "second"]
    assert saved["source_id"].tolist() == ["first", "second"]
    assert "obsolete_column" not in saved.columns


def test_filtered_discovery_can_create_a_missing_report(tmp_path: Path) -> None:
    config = _config(tmp_path)

    returned = discover_sources(config, source_filter="second", offline=True)

    saved = pd.read_csv(tmp_path / "metadata" / "source_discovery.csv")
    assert returned["source_id"].tolist() == saved["source_id"].tolist() == ["second"]


def test_filtered_discovery_does_not_overwrite_an_unidentifiable_report(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    path = tmp_path / "metadata" / "source_discovery.csv"
    original = "dataset_name,discovery_status\nOld dataset,verified\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="missing source_id"):
        discover_sources(config, source_filter="first", offline=True)

    assert path.read_text(encoding="utf-8") == original
