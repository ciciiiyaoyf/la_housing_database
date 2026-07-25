from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import src.benchmark.pipeline as benchmark_pipeline
from src.benchmark.pipeline import (
    ACS_SOURCE,
    CPI_SOURCE,
    HMDA_SOURCE,
    NMDB_DISTRIBUTION_SOURCE,
    NMDB_UPB_SOURCE,
    PUMS_SOURCE,
    _archived_asset_for_release,
    _clean_acs,
    _clean_nmdb,
    _release_date_mapping_json,
    _year_range,
    clean_version_a,
    download_version_a,
)


ROOT = Path(__file__).resolve().parents[1]


def test_source_year_ranges_keep_acs_history_and_guard_pums() -> None:
    assert list(
        _year_range(
            ROOT,
            2009,
            2024,
            source_id=ACS_SOURCE,
        )
    ) == list(range(2009, 2025))
    assert list(
        _year_range(
            ROOT,
            2009,
            2024,
            source_id=PUMS_SOURCE,
            clamp_to_source=True,
        )
    ) == list(range(2016, 2025))
    with pytest.raises(ValueError, match="2016-2024"):
        _year_range(
            ROOT,
            2015,
            2015,
            source_id=PUMS_SOURCE,
        )


def test_generic_download_and_clean_require_an_explicit_source() -> None:
    with pytest.raises(ValueError, match="download requires --source.*ACS/PUMS/CPI"):
        download_version_a(
            ROOT,
            start_year=2024,
            end_year=2024,
            offline=True,
        )
    with pytest.raises(ValueError, match="clean requires --source.*ACS/PUMS/CPI"):
        clean_version_a(
            ROOT,
            start_year=2024,
            end_year=2024,
        )


@pytest.mark.parametrize(
    "source_id",
    [
        ACS_SOURCE,
        PUMS_SOURCE,
        CPI_SOURCE,
        HMDA_SOURCE,
        NMDB_DISTRIBUTION_SOURCE,
        NMDB_UPB_SOURCE,
    ],
)
def test_acquisition_requires_explicit_release_date(source_id: str) -> None:
    with pytest.raises(ValueError, match="--release-date is required"):
        download_version_a(
            ROOT,
            source_id=source_id,
            start_year=2024,
            end_year=2024,
            offline=True,
        )


def test_scalar_release_date_rejects_multi_vintage_operations() -> None:
    for source_id in (ACS_SOURCE, PUMS_SOURCE):
        with pytest.raises(ValueError, match="exactly one vintage"):
            download_version_a(
                ROOT,
                source_id=source_id,
                start_year=2023,
                end_year=2024,
                release_date="2026-03-05",
                offline=True,
            )

    for source_id in (
        HMDA_SOURCE,
        NMDB_DISTRIBUTION_SOURCE,
        NMDB_UPB_SOURCE,
    ):
        with pytest.raises(ValueError, match="exactly one vintage"):
            download_version_a(
                ROOT,
                source_id=source_id,
                start_year=2023,
                end_year=2024,
                local_file=__file__,
                release_date="2026-03-05",
            )

    for source_id in (ACS_SOURCE, PUMS_SOURCE, HMDA_SOURCE):
        with pytest.raises(ValueError, match="exactly one vintage"):
            clean_version_a(
                ROOT,
                source_id=source_id,
                start_year=2023,
                end_year=2024,
                release_date="2026-03-05",
            )

    with pytest.raises(ValueError, match="exactly one vintage"):
        clean_version_a(
            ROOT,
            source_id=NMDB_DISTRIBUTION_SOURCE,
            start_year=2023,
            end_year=2024,
            column_map_json='{"weight":"Share"}',
            source_metadata_json='{"release_date":"2026-03-05"}',
        )


def test_clean_asset_selection_rejects_manifest_release_mismatch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pums.zip"
    source.write_bytes(b"official PUMS bytes")
    benchmark_pipeline.archive_benchmark_file(
        tmp_path,
        PUMS_SOURCE,
        source,
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year PUMS California Housing Records",
        source_page_url="https://www.census.gov/programs-surveys/acs/microdata.html",
        native_frequency="ACS 5-year",
        source_geography="California PUMA",
        data_vintage="2024",
        release_date="2026-03-05",
    )

    _, matching = _archived_asset_for_release(
        tmp_path,
        PUMS_SOURCE,
        "2024",
        "2026-03-05",
    )
    assert matching["release_date"] == "2026-03-05"
    with pytest.raises(ValueError, match="does not match requested"):
        _archived_asset_for_release(
            tmp_path,
            PUMS_SOURCE,
            "2024",
            "2026-03-06",
        )


def test_acs_clean_checks_embedded_release_against_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "acs_2024.json"
    source.write_text(
        '{"release_date":"2026-01-30"}\n',
        encoding="utf-8",
    )
    benchmark_pipeline.archive_benchmark_file(
        tmp_path,
        ACS_SOURCE,
        source,
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year Detailed Tables: Housing Finance",
        source_page_url="https://www.census.gov/data/developers/",
        native_frequency="ACS 5-year",
        source_geography="California counties",
        data_vintage="2024",
        release_date="2026-01-29",
    )

    with pytest.raises(ValueError, match="embedded release_date"):
        _clean_acs(
            tmp_path,
            2024,
            release_date="2026-01-29",
        )


def test_release_date_json_is_keyed_by_source_and_vintage() -> None:
    parsed = _release_date_mapping_json(
        '{"census_acs5_finance":{"2024":"2026-01-29"},'
        '"census_acs5_pums_housing_ca":{"2024":"2026-03-05"},'
        '"bls_cpi_u_us_city_average":{"snapshot":"2025-01-15"}}'
    )
    assert parsed[ACS_SOURCE][2024] == "2026-01-29"
    assert parsed[PUMS_SOURCE][2024] == "2026-03-05"
    assert parsed[CPI_SOURCE]["snapshot"] == "2025-01-15"

    with pytest.raises(ValueError, match="vintage-to-date objects"):
        _release_date_mapping_json(
            '{"census_acs5_finance":"2026-01-29",'
            '"census_acs5_pums_housing_ca":{"2024":"2026-03-05"},'
            '"bls_cpi_u_us_city_average":{"snapshot":"2025-01-15"}}'
        )

    with pytest.raises(ValueError, match="bls_cpi_u_us_city_average"):
        _release_date_mapping_json(
            '{"census_acs5_finance":{"2024":"2026-01-29"},'
            '"census_acs5_pums_housing_ca":{"2024":"2026-03-05"}}'
        )


@pytest.mark.parametrize(
    ("source_id", "normalizer_name"),
    [
        (
            NMDB_DISTRIBUTION_SOURCE,
            "normalize_nmdb_joint_distribution",
        ),
        (NMDB_UPB_SOURCE, "normalize_nmdb_california_upb"),
    ],
)
def test_nmdb_clean_preserves_distinct_source_id(
    tmp_path,
    monkeypatch,
    source_id,
    normalizer_name,
) -> None:
    archived = tmp_path / f"{source_id}.csv"
    archived.write_text("value\n1\n", encoding="utf-8")
    manifest = {
        "data_vintage": "2024",
        "retrieved_at": "2025-04-02T17:00:00Z",
        "source_filename": archived.name,
        "checksum_sha256": "a" * 64,
        "release_date": "2025-03-28",
    }
    monkeypatch.setattr(
        benchmark_pipeline,
        "benchmark_asset_for_data_vintage",
        lambda *_, **__: (archived, manifest),
    )
    captured: dict[str, object] = {}

    def fake_normalizer(*_, **kwargs):
        captured.update(kwargs["source_metadata"])
        return pd.DataFrame({"value": [1.0]})

    monkeypatch.setattr(
        benchmark_pipeline,
        normalizer_name,
        fake_normalizer,
    )

    output = _clean_nmdb(
        tmp_path,
        source_id,
        2024,
        column_map_json='{"value":"value"}',
        source_metadata_json=(
            '{"benchmark_vintage":"2024_ACS5",'
            '"release_date":"2025-03-28"}'
        ),
    )
    assert output.exists()
    assert captured["source_id"] == source_id
