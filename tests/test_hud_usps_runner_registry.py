from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.cleaning.runner import HUD_USPS_SOURCE_ID, clean_sources
from src.common.config import ProjectConfig, SourceConfig
from src.database.build import (
    _hud_usps_restricted_clean_tables,
    _register_clean_parquet_tables,
    _source_registry,
)


def _config(root: Path) -> ProjectConfig:
    source = SourceConfig(
        source_id=HUD_USPS_SOURCE_ID,
        provider="HUD-USPS",
        dataset_name="HUD-USPS vacancy data",
        source_page_url="https://www.huduser.gov/portal/datasets/usps.html",
        download_url="https://www.huduser.gov/hudapi/public/uspsncwm",
        native_frequency="quarterly",
        geography_levels=("county", "tract_2020", "postal_zip_county_part"),
        file_format="json",
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-08-03",
        sources=(source,),
        pipeline={},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def _archive_records() -> list[tuple[Path, dict[str, str]]]:
    records: list[tuple[Path, dict[str, str]]] = []
    for period in pd.period_range("2008Q1", "2026Q1", freq="Q"):
        label = f"{period.year}-Q{period.quarter}"
        filename = f"ncwm_{period.year}Q{period.quarter}.json"
        records.append(
            (
                Path(filename),
                {
                    "source_id": HUD_USPS_SOURCE_ID,
                    "artifact_role": "ncwm_standardized_2020",
                    "archive_period": label,
                    "provider_vintage": f"{period.year}Q{period.quarter}",
                    "data_vintage": "2026-08-03",
                    "source_filename": filename,
                    "relative_path": f"data/restricted/hud_usps/raw/{filename}",
                },
            )
        )
    records.extend(
        [
            (
                Path("crosswalk.json"),
                {
                    "source_id": HUD_USPS_SOURCE_ID,
                    "artifact_role": "tract_zip_crosswalk",
                    "archive_period": "2026-Q1-crosswalk",
                    "provider_vintage": "2026Q1",
                    "data_vintage": "2026-08-03",
                    "source_filename": "crosswalk.json",
                    "relative_path": (
                        "data/restricted/hud_usps/raw/crosswalk.json"
                    ),
                },
            ),
            (
                Path("county_reference.txt"),
                {
                    "source_id": HUD_USPS_SOURCE_ID,
                    "artifact_role": "county_fips_reference_2020",
                    "archive_period": "2020-county-fips-reference",
                    "provider_vintage": "2020 Census",
                    "data_vintage": "2026-08-03",
                    "source_filename": "county_reference.txt",
                    "relative_path": (
                        "data/restricted/hud_usps/raw/county_reference.txt"
                    ),
                },
            ),
        ]
    )
    assert len(records) == 75
    return records


def test_explicit_hud_clean_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.cleaning.runner.source_archive_paths",
        lambda *_args, **_kwargs: _archive_records(),
    )

    def fail_clean(*_args, **_kwargs):
        raise ValueError("incomplete official archive")

    monkeypatch.setattr(
        "src.cleaning.runner.clean_hud_usps_archive", fail_clean
    )

    with pytest.raises(ValueError, match="incomplete official archive"):
        clean_sources(_config(tmp_path), source_filter=HUD_USPS_SOURCE_ID)


def test_explicit_hud_clean_without_archives_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.cleaning.runner.source_archive_paths",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(FileNotFoundError, match="No raw archive"):
        clean_sources(_config(tmp_path), source_filter=HUD_USPS_SOURCE_ID)


def test_hud_transformation_log_keeps_full_lineage_and_quarantine_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archives = _archive_records()
    monkeypatch.setattr(
        "src.cleaning.runner.source_archive_paths",
        lambda *_args, **_kwargs: archives,
    )
    monkeypatch.setattr(
        "src.cleaning.runner.clean_hud_usps_archive",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "transformation_id": [
                    "hud_usps_official_raw_to_quarterly_v2"
                ],
                "value": [1.0],
            }
        ),
    )
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    pd.DataFrame(
        [
            {
                "transformation_id": "hud_usps_quarantine_registration_v1",
                "source_id": HUD_USPS_SOURCE_ID,
                "input_file": "quarantined_package.zip",
                "output_file": "metadata/hud_usps_quarantine_manifest.csv",
                "input_rows_or_shape": "1 package",
                "output_rows": "9",
                "description": "Historical quarantine registration",
                "code_module": "src.cleaning.hud_usps",
                "source_vintage": "2026-08-02",
                "executed_at": "2026-08-03T00:00:00+00:00",
            }
        ]
    ).to_csv(metadata / "transformation_log.csv", index=False)

    clean_sources(_config(tmp_path), source_filter=HUD_USPS_SOURCE_ID)

    log = pd.read_csv(metadata / "transformation_log.csv", dtype=str)
    assert set(log["transformation_id"]) == {
        "hud_usps_quarantine_registration_v1",
        "hud_usps_official_raw_to_quarterly_v2",
    }
    active = log.loc[
        log["transformation_id"].eq(
            "hud_usps_official_raw_to_quarterly_v2"
        )
    ].iloc[0]
    input_paths = active["input_file"].split("|")
    assert len(input_paths) == 75
    assert set(input_paths) == {
        record["relative_path"] for _, record in archives
    }
    assert active["input_rows_or_shape"] == (
        "75-file manifest: 73 NCWM quarters 2008-Q1..2026-Q1; "
        "1 crosswalk 2026Q1; 1 county reference 2020 Census"
    )
    assert "crosswalk 2026Q1" in active["description"]
    assert "county validation reference 2020 Census" in active["description"]


def test_source_registry_retains_hud_artifact_provenance(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    expected = {
        "request_query": "type=1&year=2026&month=3&stateid=06",
        "artifact_role": "ncwm_standardized_2020",
        "provider_vintage": "2026Q1",
        "provider_release_date": "2026-06-15",
        "provider_release_date_status": "provider_published",
    }
    pd.DataFrame(
        [
            {
                "source_id": HUD_USPS_SOURCE_ID,
                "source_filename": "ncwm_2026Q1.json",
                "retrieved_at": "2026-08-03T08:52:20+00:00",
                "data_vintage": "2026-08-03",
                **expected,
            }
        ]
    ).to_csv(metadata / "raw_file_manifest.csv", index=False)

    registry = _source_registry(_config(tmp_path))

    assert len(registry) == 1
    for column, value in expected.items():
        assert registry.iloc[0][column] == value


def test_registers_three_restricted_native_quarterly_hud_tables(
    tmp_path: Path,
) -> None:
    restricted = (
        tmp_path / "data" / "restricted" / "hud_usps" / "active"
    )
    restricted.mkdir(parents=True)
    files = {
        "ca_county_quarterly.parquet": "county",
        "la_tract_2020_quarterly.parquet": "tract_2020",
        "la_postal_zip_county_part_quarterly.parquet": (
            "postal_zip_county_part"
        ),
    }
    for filename, geo_type in files.items():
        pd.DataFrame(
            {
                "quarter_end": [pd.Timestamp("2026-03-31")],
                "geo_type": [geo_type],
                "value": [1.0],
            }
        ).to_parquet(restricted / filename, index=False)

    tables = _hud_usps_restricted_clean_tables(_config(tmp_path))

    assert {name for name, _ in tables} == {
        "hud_usps_ca_county_quarterly",
        "hud_usps_la_tract_2020_quarterly",
        "hud_usps_la_postal_zip_county_part_quarterly",
    }
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE SCHEMA clean")
        _register_clean_parquet_tables(connection, tables)
        registered = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'clean'
                """
            ).fetchall()
        }
        analytics_tables = connection.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'analytics'
            """
        ).fetchone()[0]
    assert registered == {
        "hud_usps_ca_county_quarterly",
        "hud_usps_la_tract_2020_quarterly",
        "hud_usps_la_postal_zip_county_part_quarterly",
    }
    assert analytics_tables == 0


def test_rejects_partial_restricted_hud_table_set(tmp_path: Path) -> None:
    restricted = (
        tmp_path / "data" / "restricted" / "hud_usps" / "active"
    )
    restricted.mkdir(parents=True)
    pd.DataFrame({"value": [1]}).to_parquet(
        restricted / "ca_county_quarterly.parquet", index=False
    )

    with pytest.raises(FileNotFoundError, match="products are incomplete"):
        _hud_usps_restricted_clean_tables(_config(tmp_path))
