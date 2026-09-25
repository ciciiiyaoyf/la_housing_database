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
    assert list(
        _year_range(
            ROOT,
            2018,
            2025,
            source_id=HMDA_SOURCE,
        )
    ) == list(range(2018, 2026))
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


def test_download_acs_without_key_uses_summary_file_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setattr(
        benchmark_pipeline,
        "benchmark_asset_for_data_vintage",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        benchmark_pipeline,
        "fetch_acs_finance_payload",
        lambda *_, **__: pytest.fail("API path must not run without a key"),
    )
    summary_calls: list[tuple[int, str]] = []

    def fake_summary_archive(vintage, output_path, *, release_date):
        summary_calls.append((vintage, release_date))
        output_path.write_bytes(b"summary-file-zip")
        return {"release_date": release_date}

    monkeypatch.setattr(
        benchmark_pipeline,
        "archive_acs_finance_summary_files",
        fake_summary_archive,
    )
    archived: dict[str, object] = {}

    def fake_archive(root, source_id, source_file, **metadata):
        archived.update(metadata)
        archived["source_id"] = source_id
        archived["suffix"] = source_file.suffix
        archived["bytes"] = source_file.read_bytes()
        return {key: str(value) for key, value in archived.items()}

    monkeypatch.setattr(
        benchmark_pipeline,
        "archive_benchmark_file",
        fake_archive,
    )

    record = benchmark_pipeline._download_acs(
        tmp_path,
        2024,
        force=False,
        release_date="2026-01-29",
    )

    assert summary_calls == [(2024, "2026-01-29")]
    assert archived["source_id"] == ACS_SOURCE
    assert archived["suffix"] == ".zip"
    assert archived["bytes"] == b"summary-file-zip"
    assert archived["retrieval_method"] == (
        "official_table_based_summary_file_zip"
    )
    assert record["retrieval_method"] == (
        "official_table_based_summary_file_zip"
    )


def test_download_acs_with_key_uses_existing_api_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "unit-test-placeholder")
    monkeypatch.setattr(
        benchmark_pipeline,
        "benchmark_asset_for_data_vintage",
        lambda *_, **__: None,
    )
    api_calls: list[tuple[int, str]] = []

    def fake_api_payload(vintage, *, release_date):
        api_calls.append((vintage, release_date))
        return {"release_date": release_date, "responses": {}}

    monkeypatch.setattr(
        benchmark_pipeline,
        "fetch_acs_finance_payload",
        fake_api_payload,
    )
    monkeypatch.setattr(
        benchmark_pipeline,
        "archive_acs_finance_summary_files",
        lambda *_, **__: pytest.fail(
            "Summary File path must not run when a key is set"
        ),
    )
    archived: dict[str, object] = {}

    def fake_archive(root, source_id, source_file, **metadata):
        archived.update(metadata)
        archived["source_id"] = source_id
        archived["suffix"] = source_file.suffix
        archived["payload"] = source_file.read_text(encoding="utf-8")
        return {key: str(value) for key, value in archived.items()}

    monkeypatch.setattr(
        benchmark_pipeline,
        "archive_benchmark_file",
        fake_archive,
    )

    record = benchmark_pipeline._download_acs(
        tmp_path,
        2024,
        force=False,
        release_date="2026-01-29",
    )

    assert api_calls == [(2024, "2026-01-29")]
    assert archived["source_id"] == ACS_SOURCE
    assert archived["suffix"] == ".json"
    assert "unit-test-placeholder" not in str(archived["payload"])
    assert archived["retrieval_method"] == "official_api_composite_json"
    assert record["retrieval_method"] == "official_api_composite_json"


def test_download_acs_before_2022_without_key_fails_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setattr(
        benchmark_pipeline,
        "benchmark_asset_for_data_vintage",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        benchmark_pipeline,
        "fetch_acs_finance_payload",
        lambda *_, **__: pytest.fail("API path must not run without a key"),
    )
    monkeypatch.setattr(
        benchmark_pipeline,
        "archive_acs_finance_summary_files",
        lambda *_, **__: pytest.fail(
            "Unverified Summary File fallback must not run before 2022"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=r"CENSUS_API_KEY is required.*before 2022",
    ):
        benchmark_pipeline._download_acs(
            tmp_path,
            2021,
            force=False,
            release_date="2022-03-17",
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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        benchmark_pipeline,
        "acs_finance_archive_metadata",
        lambda _: {"release_date": "2026-01-30"},
    )
    monkeypatch.setattr(
        benchmark_pipeline,
        "clean_acs_finance_vintage",
        lambda *_, **__: pytest.fail(
            "Cleaning must not run after a release-date mismatch"
        ),
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
