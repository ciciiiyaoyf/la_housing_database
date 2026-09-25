from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.ca_cpi import SOURCE_ID as CA_CPI_SOURCE_ID
from src.cleaning.census_pep import SOURCE_IDS as CENSUS_PEP_SOURCE_IDS
from src.cleaning.runner import CA_DOI_SOURCE_ID, clean_sources
from src.common.config import ProjectConfig, SourceConfig


REQUIRED_SOURCE_CLEANERS = (
    (CA_DOI_SOURCE_ID, "clean_ca_doi_homeowners"),
    (CA_CPI_SOURCE_ID, "clean_ca_cpi"),
    *(
        (source_id, "clean_census_pep")
        for source_id in sorted(CENSUS_PEP_SOURCE_IDS)
    ),
)


def _config(root: Path, source_id: str) -> ProjectConfig:
    source = SourceConfig(
        source_id=source_id,
        provider="Official provider",
        dataset_name="Required paused-variable input",
        source_page_url="https://example.gov/source",
        download_url="https://example.gov/source.csv",
        native_frequency="annual",
        geography_levels=("county",),
        file_format="csv",
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-08-17",
        sources=(source,),
        pipeline={},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def _record(source_id: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "data_vintage": "test",
        "retrieved_at": "2026-08-17T00:00:00+00:00",
        "source_filename": "raw-source",
        "checksum_sha256": "a" * 64,
    }


@pytest.mark.parametrize(("source_id", "cleaner_name"), REQUIRED_SOURCE_CLEANERS)
@pytest.mark.parametrize("source_filter_mode", ("unfiltered", "filtered"))
def test_required_paused_variable_clean_failures_always_propagate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_id: str,
    cleaner_name: str,
    source_filter_mode: str,
) -> None:
    raw_path = tmp_path / "raw-source"
    raw_path.write_bytes(b"invalid-provider-file")
    monkeypatch.setattr(
        "src.cleaning.runner.raw_path_for_vintage",
        lambda *_args, **_kwargs: (raw_path, _record(source_id)),
    )

    def fail_clean(*_args, **_kwargs):
        raise ValueError(f"intentional {source_id} schema failure")

    monkeypatch.setattr(f"src.cleaning.runner.{cleaner_name}", fail_clean)
    source_filter = source_id if source_filter_mode == "filtered" else None

    with pytest.raises(ValueError, match=f"intentional {source_id} schema failure"):
        clean_sources(_config(tmp_path, source_id), source_filter=source_filter)


@pytest.mark.parametrize("source_filter_mode", ("unfiltered", "filtered"))
def test_optional_hud_pit_failure_only_propagates_when_explicitly_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_filter_mode: str,
) -> None:
    source_id = "hud_pit_coc_2007_2025"
    raw_path = tmp_path / "hud-pit.xlsx"
    raw_path.write_bytes(b"invalid-provider-file")
    monkeypatch.setattr(
        "src.cleaning.runner.raw_path_for_vintage",
        lambda *_args, **_kwargs: (raw_path, _record(source_id)),
    )

    def fail_clean(*_args, **_kwargs):
        raise ValueError("intentional optional HUD PIT schema failure")

    monkeypatch.setattr("src.cleaning.runner.clean_hud_pit", fail_clean)
    source_filter = source_id if source_filter_mode == "filtered" else None

    if source_filter is None:
        assert clean_sources(_config(tmp_path, source_id)) == ([], [])
    else:
        with pytest.raises(ValueError, match="optional HUD PIT schema failure"):
            clean_sources(
                _config(tmp_path, source_id), source_filter=source_filter
            )


def test_cleaning_log_refresh_preserves_monthly_finance_execution_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = CA_CPI_SOURCE_ID
    raw_path = tmp_path / "raw-source"
    raw_path.write_bytes(b"official-provider-file-fixture")
    monkeypatch.setattr(
        "src.cleaning.runner.raw_path_for_vintage",
        lambda *_args, **_kwargs: (raw_path, _record(source_id)),
    )
    monkeypatch.setattr(
        "src.cleaning.runner.clean_ca_cpi",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"transformation_id": ["ca_cpi_annual_clean_v1"]}
        ),
    )
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    pd.DataFrame(
        [
            {
                "transformation_id": "version_b_reference_year_payment_repeat_v1",
                "source_id": "version_a_housing_finance_benchmark",
                "source_vintage": "database_version=B_retrospective_history",
            },
            {
                "transformation_id": "version_c_latest_payment_carry_v1",
                "source_id": "version_a_housing_finance_benchmark",
                "source_vintage": "information_cutoff_date=2026-08-17",
            },
            {
                "transformation_id": "obsolete_transform_v1",
                "source_id": "obsolete_source",
                "source_vintage": "old",
            },
        ]
    ).to_csv(metadata / "transformation_log.csv", index=False)

    clean_sources(_config(tmp_path, source_id), source_filter=source_id)

    result = pd.read_csv(metadata / "transformation_log.csv", dtype=str)
    assert {
        "version_b_reference_year_payment_repeat_v1",
        "version_c_latest_payment_carry_v1",
        "ca_cpi_annual_clean_v1",
    } <= set(result["transformation_id"])
    assert "obsolete_transform_v1" not in set(result["transformation_id"])
