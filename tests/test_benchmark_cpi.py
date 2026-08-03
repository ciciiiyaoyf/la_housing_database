from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.benchmark.cpi import (
    CPIFormatError,
    CPI_U_SERIES_ID,
    clean_bls_cpi_u_annual,
    cpi_u_annual_index,
)
from src.benchmark.pipeline import (
    CPI_SOURCE,
    clean_version_a,
    download_version_a,
)


def _write_bls_snapshot(path: Path) -> None:
    path.write_text(
        "series_id\tyear\tperiod\tvalue\tfootnote_codes\n"
        "CUUR0000SA0\t2020\tM01\t257.971\t\n"
        "CUUR0000SA0\t2020\tM13\t258.811\t\n"
        "CUUR0000SA0\t2024\tM13\t313.689\t\n"
        "CUSR0000SA0\t2024\tM13\t313.689\t\n",
        encoding="utf-8",
    )


def _archive_metadata() -> dict[str, str]:
    return {
        "source_id": CPI_SOURCE,
        "data_vintage": "2025-01-15",
        "retrieved_at": "2025-01-16T12:00:00+00:00",
        "source_filename": "cu.data.1.AllItems",
        "checksum_sha256": "abc123",
        "source_page_url": "https://www.bls.gov/cpi/data.htm",
    }


def test_clean_cpi_selects_exact_published_m13_annual_average(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cu.data.1.AllItems"
    _write_bls_snapshot(source)

    result = clean_bls_cpi_u_annual(
        source,
        _archive_metadata(),
        release_date="2025-01-15",
    )

    assert result["year"].tolist() == [2020, 2024]
    assert result["value"].tolist() == [258.811, 313.689]
    assert result["series_id"].eq(CPI_U_SERIES_ID).all()
    assert result["period"].eq("M13").all()
    assert result["source_period_start"].tolist() == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2024-01-01"),
    ]
    assert result["source_release_dates"].eq(
        "bls_cpi_u_us_city_average=2025-01-15"
    ).all()
    assert result["checksum_sha256"].eq("abc123").all()


def test_clean_cpi_rejects_missing_annual_average(tmp_path: Path) -> None:
    source = tmp_path / "monthly_only.txt"
    source.write_text(
        "series_id\tyear\tperiod\tvalue\n"
        "CUUR0000SA0\t2024\tM12\t315.605\n",
        encoding="utf-8",
    )
    with pytest.raises(CPIFormatError, match="M13"):
        clean_bls_cpi_u_annual(
            source,
            _archive_metadata(),
            release_date="2025-01-15",
        )


def test_clean_cpi_rejects_release_before_latest_retained_year(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cu.data.1.AllItems"
    _write_bls_snapshot(source)

    with pytest.raises(CPIFormatError, match="latest retained CPI"):
        clean_bls_cpi_u_annual(
            source,
            _archive_metadata(),
            release_date="2024-12-30",
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("period", "M12", "period must be exactly M13"),
        ("source_id", "not_bls", "source_id must be exactly"),
        ("source_frequency", "monthly", "source_frequency must be exactly"),
    ],
)
def test_cpi_index_rejects_nonannual_or_wrong_source_contract(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    source = tmp_path / "cu.data.1.AllItems"
    _write_bls_snapshot(source)
    cleaned = clean_bls_cpi_u_annual(
        source,
        _archive_metadata(),
        release_date="2025-01-15",
    )
    cleaned.loc[cleaned.index[0], column] = value

    with pytest.raises(CPIFormatError, match=message):
        cpi_u_annual_index(cleaned)


def test_cpi_index_requires_complete_release_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cu.data.1.AllItems"
    _write_bls_snapshot(source)
    cleaned = clean_bls_cpi_u_annual(
        source,
        _archive_metadata(),
        release_date="2025-01-15",
    )

    with pytest.raises(KeyError, match="source_release_dates"):
        cpi_u_annual_index(cleaned.drop(columns="source_release_dates"))

    cleaned.loc[cleaned.index[0], "checksum_sha256"] = ""
    with pytest.raises(CPIFormatError, match="checksum_sha256"):
        cpi_u_annual_index(cleaned)


def test_cpi_local_orchestration_archives_then_cleans_release_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cu.data.1.AllItems"
    _write_bls_snapshot(source)

    records = download_version_a(
        tmp_path,
        source_id=CPI_SOURCE,
        local_file=str(source),
        release_date="2025-01-15",
    )
    outputs = clean_version_a(
        tmp_path,
        source_id=CPI_SOURCE,
        release_date="2025-01-15",
    )

    assert records[0]["data_vintage"] == "2025-01-15"
    assert records[0]["release_date"] == "2025-01-15"
    assert records[0]["retrieval_method"] == (
        "manual_official_bls_flat_file_import"
    )
    assert outputs == [
        tmp_path
        / "data/benchmark/clean/bls_cpi_u_annual_2025-01-15.parquet"
    ]
    cleaned = pd.read_parquet(outputs[0])
    assert cleaned[["year", "value"]].to_records(index=False).tolist() == [
        (2020, 258.811),
        (2024, 313.689),
    ]
