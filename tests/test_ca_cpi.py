from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.ca_cpi import (
    CACPIFormatError,
    SOURCE_ID,
    TRANSFORMATION_ID,
    UNIT,
    VARIABLE_ID,
    clean_ca_cpi,
    parse_ca_cpi_annual_text,
)
from src.cleaning.common import OBSERVATION_COLUMNS


def _text(*annual_rows: str, last_title_year: int = 1958) -> str:
    return "\n".join(
        [
            "State of California Office of the Director- Research Unit",
            "Department of Industrial Relations",
            f"CALIFORNIA CONSUMER PRICE INDEX (1955-{last_title_year})",
            "ALL ITEMS (1982 - 1984 = 100)",
            "Year Month All Urban Consumers Urban Wage Earners",
            "and Clerical Workers",
            "1957 December 28.3 28.4",
            *annual_rows,
            "1955 March 25.7 25.8",
        ]
    )


def _complete_text() -> str:
    return _text(
        "1957 Annual 27.1 27.3",
        "1956 Annual 26.2 26.3",
        "1955 Annual 25.7 25.9",
    )


def _record() -> dict[str, str]:
    return {
        "source_id": SOURCE_ID,
        "data_vintage": "2026-08-15",
        "retrieved_at": "2026-08-15T18:30:00+00:00",
        "source_filename": "EntireCCPI.PDF",
        "checksum_sha256": "a" * 64,
        "provider_last_updated": "2026-08-01",
    }


def test_text_parser_keeps_only_published_annual_cpi_u_levels() -> None:
    result = parse_ca_cpi_annual_text(_complete_text(), _record())

    assert list(result.columns) == OBSERVATION_COLUMNS
    assert list(result["observation_date"]) == list(
        pd.to_datetime(["1955-12-31", "1956-12-31", "1957-12-31"])
    )
    assert result["value"].tolist() == [25.7, 26.2, 27.1]
    assert result["reported_value"].tolist() == [25.7, 26.2, 27.1]
    assert result["variable_id"].eq(VARIABLE_ID).all()
    assert result["geo_id"].eq("STATE_06").all()
    assert result["state_fips"].eq("06").all()
    assert result["county_fips"].isna().all()
    assert result["unit"].eq(UNIT).all()
    assert result["native_frequency"].eq("annual").all()
    assert result["final_frequency"].eq("annual").all()
    assert result["source_id"].eq(SOURCE_ID).all()
    assert result["transformation_id"].eq(TRANSFORMATION_ID).all()
    assert result["is_imputed"].eq(False).all()
    assert result["is_interpolated"].eq(False).all()
    assert result["is_seasonally_adjusted"].eq(False).all()
    assert result["checksum_sha256"].eq("a" * 64).all()
    assert result["period_start"].dt.strftime("%m-%d").eq("01-01").all()
    assert result["period_end"].dt.strftime("%m-%d").eq("12-31").all()


def test_cleaner_uses_text_extraction_and_finalizes_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "EntireCCPI.PDF"
    source.write_bytes(b"synthetic-pdf-placeholder")
    output = tmp_path / "ca_cpi.parquet"
    monkeypatch.setattr(
        "src.cleaning.ca_cpi.extract_ca_cpi_pdf_text",
        lambda path: _complete_text(),
    )

    result = clean_ca_cpi(source, _record(), output)

    assert output.is_file()
    assert list(result.columns) == OBSERVATION_COLUMNS
    assert len(pd.read_parquet(output)) == 3
    assert result["value"].tolist() == [25.7, 26.2, 27.1]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            _complete_text().replace("ALL ITEMS", "FOOD AND BEVERAGES"),
            "1982-1984=100",
        ),
        (
            _complete_text().replace(
                "All Urban Consumers Urban Wage Earners",
                "Urban Wage Earners All Urban Consumers",
            ),
            "column order changed",
        ),
        (
            _complete_text().replace("1956 Annual 26.2 26.3\n", ""),
            r"missing=\[1956\]",
        ),
        (
            _complete_text().replace(
                "1956 Annual 26.2 26.3",
                "1956 Annual unavailable 26.3",
            ),
            "unrecognized.*Annual row",
        ),
    ],
)
def test_schema_drift_and_incomplete_annual_history_fail(
    text: str, message: str
) -> None:
    with pytest.raises(CACPIFormatError, match=message):
        parse_ca_cpi_annual_text(text, _record())


def test_duplicate_annual_year_fails() -> None:
    text = _complete_text() + "\n1956 Annual 999.9 999.8"
    with pytest.raises(CACPIFormatError, match=r"duplicate.*\[1956\]"):
        parse_ca_cpi_annual_text(text, _record())


def test_revised_marker_preserves_numeric_level_and_is_flagged() -> None:
    text = _complete_text().replace("1956 Annual 26.2", "1956 Annual R/26.2")
    result = parse_ca_cpi_annual_text(text, _record())
    row = result.loc[result["observation_date"].eq("1956-12-31")].iloc[0]
    assert row["value"] == 26.2
    assert "source_revised" in row["quality_flag"]


def test_unexpected_source_id_fails_before_observations_are_built() -> None:
    record = _record() | {"source_id": "not_the_dir_cpi_source"}
    with pytest.raises(ValueError, match="unexpected source_id"):
        parse_ca_cpi_annual_text(_complete_text(), record)
