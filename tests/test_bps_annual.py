from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.bps_annual import clean_bps_annual, parse_bps_annual_county
from src.cleaning.census_bps import BPSFormatError, COUNTY_COLUMNS
from src.common.io import is_bps_ascii_payload
from src.ingestion.bps_annual import (
    SOURCE_ID,
    annual_county_targets,
    annual_county_url,
    download_annual_county_file,
)


def _row(year_token: str, state: str, county: str, total: int, reported: int) -> list:
    return [
        year_token,
        state,
        county,
        "4",
        "9",
        "Fixture County",
        10,
        total,
        1_000_000,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        9,
        reported,
        900_000,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]


def _payload(rows: list[list]) -> bytes:
    header_one = ",".join(
        ["Survey", "FIPS", "FIPS", "Region", "Division", "County", *([""] * 24)]
    )
    header_two = ",".join(
        ["Date", "State", "County", "Code", "Code", "Name", *([""] * 24)]
    )
    text = "\n".join(
        [header_one, header_two, "", *[",".join(map(str, row)) for row in rows]]
    )
    return (text + "\n").encode("utf-8")


def _record(path: Path, year: int) -> dict[str, str]:
    return {
        "source_id": SOURCE_ID,
        "survey_year": str(year),
        "archive_period": str(year),
        "annual_release_status": "final",
        "data_vintage": "2026-08-07",
        "retrieved_at": "2026-08-07T12:00:00+00:00",
        "source_filename": path.name,
        "original_filename": f"co{year}a.txt",
        "download_url": annual_county_url(year),
        "checksum_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class _Response:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


class _Session:
    def __init__(self, content: bytes):
        self.content = content
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.content)


def test_annual_targets_are_final_and_use_only_official_urls():
    targets = annual_county_targets(1990, 2025)
    assert len(targets) == 36
    assert targets[0]["download_url"].endswith("/co1990a.txt")
    assert targets[-1]["download_url"].endswith("/co2025a.txt")
    assert {target["annual_release_status"] for target in targets} == {"final"}
    with pytest.raises(ValueError, match="1990-2025"):
        annual_county_url(2026)


def test_download_archives_payload_and_complete_provenance(tmp_path):
    body = _payload([_row("2025", "06", "037", 120, 100)])
    session = _Session(body)
    retrieved = datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc)
    record = download_annual_county_file(
        tmp_path,
        2025,
        session=session,
        retrieved_at=retrieved,
    )

    archived = tmp_path / record["relative_path"]
    assert archived.read_bytes() == body
    assert record["checksum_sha256"] == hashlib.sha256(body).hexdigest()
    assert record["survey_year"] == "2025"
    assert record["annual_release_status"] == "final"
    assert record["original_filename"] == "co2025a.txt"
    assert record["native_frequency"] == "annual"
    assert session.calls[0][0] == annual_county_url(2025)
    manifest = pd.read_csv(tmp_path / "metadata" / "raw_file_manifest.csv", dtype=str)
    assert manifest.loc[0, "checksum_sha256"] == record["checksum_sha256"]
    assert manifest.loc[0, "relative_path"] == record["relative_path"]


def test_historical_spaced_header_and_extra_trailing_delimiter_are_valid(tmp_path):
    lines = _payload([_row("9099", "06", "037", 120, 100)]).decode().splitlines()
    lines[0] = lines[0].replace("Survey", "Survey ", 1) + ","
    lines[1] = lines[1].replace("Date", "Date ", 1)
    body = ("\n".join(lines) + "\n").encode()
    record = download_annual_county_file(
        tmp_path,
        1990,
        session=_Session(body),
        retrieved_at=datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
    )

    assert is_bps_ascii_payload(tmp_path / record["relative_path"])


def test_parser_preserves_la_identifiers_total_reported_and_final_status(tmp_path):
    path = tmp_path / "co2025a.txt"
    path.write_bytes(
        _payload(
            [
                _row("2025", "06", "037", 120, 100),
                _row("2025", "06", "001", 25, 25),
                _row("2025", "36", "001", 999, 999),
            ]
        )
    )
    result = clean_bps_annual(
        path,
        _record(path, 2025),
        tmp_path / "annual.parquet",
    )
    row = result.iloc[0]
    assert len(result) == 1
    assert row["geo_id"] == "COUNTY_06037"
    assert row["state_fips"] == "06"
    assert row["county_fips"] == "06037"
    assert row["variable_id"] == "permits_1_unit_annual_final"
    assert row["value"] == 120
    assert row["reported_value"] == 100
    assert bool(row["is_imputed"])
    assert row["native_frequency"] == row["final_frequency"] == "annual"
    assert row["observation_date"] == pd.Timestamp("2025-12-31")
    assert "bps_annual_final" in row["quality_flag"]
    assert row["checksum_sha256"] == _record(path, 2025)["checksum_sha256"]


def test_parser_can_retain_all_california_counties_without_state_aggregation(tmp_path):
    path = tmp_path / "co2024a.txt"
    path.write_bytes(
        _payload(
            [
                _row("2024", "06", "037", 100, 100),
                _row("2024", "06", "001", 25, 20),
                _row("2024", "36", "001", 999, 999),
            ]
        )
    )
    result = parse_bps_annual_county(
        path,
        _record(path, 2024),
        state_fips="06",
        county_fips=None,
    )
    assert set(result["geo_id"]) == {"COUNTY_06037", "COUNTY_06001"}
    assert not result["geo_id"].str.startswith("STATE_").any()


def test_legacy_yy99_annual_token_is_supported(tmp_path):
    path = tmp_path / "co1990a.txt"
    path.write_bytes(_payload([_row("9099", "06", "037", 80, 75)]))
    result = parse_bps_annual_county(path, _record(path, 1990))
    assert result.iloc[0]["observation_date"] == pd.Timestamp("1990-12-31")
    assert "bps_pre_2022_sample_tally" in result.iloc[0]["quality_flag"]


@pytest.mark.parametrize("survey_token", ["202501", "2024"])
def test_monthly_or_wrong_year_survey_tokens_are_rejected(tmp_path, survey_token):
    path = tmp_path / "co2025a.txt"
    path.write_bytes(_payload([_row(survey_token, "06", "037", 80, 75)]))
    with pytest.raises(BPSFormatError, match="final annual survey year 2025"):
        parse_bps_annual_county(path, _record(path, 2025))


def test_unknown_annual_column_width_is_rejected(tmp_path):
    path = tmp_path / "co2025a.txt"
    row = _row("2025", "06", "037", 80, 75)[:-1]
    path.write_bytes(_payload([row]))
    with pytest.raises(BPSFormatError, match="requires 30"):
        parse_bps_annual_county(path, _record(path, 2025))


def test_fixture_width_matches_documented_annual_schema():
    assert len(COUNTY_COLUMNS) == 30
    assert len(_row("2025", "06", "037", 1, 1)) == len(COUNTY_COLUMNS)
