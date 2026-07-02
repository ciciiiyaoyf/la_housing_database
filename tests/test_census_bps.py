from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.census_bps import (
    BPSFormatError,
    COUNTY_COLUMNS,
    STATE_COLUMNS,
    clean_bps,
    clean_bps_archive,
)


def _write_bps_file(
    path: Path, period: str, geography: str, *, malformed: bool = False
) -> None:
    columns = COUNTY_COLUMNS if geography == "county" else STATE_COLUMNS
    header_one = "Survey,FIPS,FIPS,Region,Division,County," + "," * (
        len(columns) - 6
    )
    header_two = "Date,State,County,Code,Code,Name," + "," * (
        len(columns) - 6
    )
    if geography == "county":
        row = [
            period,
            "06",
            "037",
            "4",
            "9",
            "Los Angeles County",
            10,
            10,
            1_000_000,
            2,
            4,
            400_000,
            1,
            3,
            300_000,
            2,
            20,
            2_000_000,
            9,
            9,
            900_000,
            2,
            4,
            400_000,
            1,
            3,
            300_000,
            1,
            10,
            1_000_000,
        ]
    else:
        header_one = "Survey,FIPS,Region,Division,State," + "," * (
            len(columns) - 5
        )
        header_two = "Date,State,Code,Code,Name," + "," * (
            len(columns) - 5
        )
        row = [
            period,
            "06",
            "4",
            "9",
            "California",
            100,
            100,
            10_000_000,
            3,
            6,
            600_000,
            2,
            8,
            800_000,
            5,
            50,
            5_000_000,
            90,
            90,
            9_000_000,
            3,
            6,
            600_000,
            2,
            8,
            800_000,
            4,
            40,
            4_000_000,
        ]
    if malformed:
        row = row[:-1]
    path.write_text(
        f"{header_one}\n{header_two}\n{','.join(map(str, row))}\n",
        encoding="utf-8",
    )


def _record(path: Path, period: str) -> dict[str, str]:
    return {
        "archive_period": f"{period[:4]}-{period[4:]}",
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": path.name,
        "download_url": f"https://www2.census.gov/econ/bps/test/{path.name}",
        "checksum_sha256": "test",
    }


@pytest.mark.parametrize(
    ("source_id", "geography", "expected_geo", "expected_state", "expected_county"),
    [
        ("census_bps_county", "county", "COUNTY_06037", "06", "06037"),
        ("census_bps_state", "state", "STATE_06", "06", None),
    ],
)
def test_bps_archive_loads_multiple_months_and_preserves_fips(
    tmp_path,
    source_id,
    geography,
    expected_geo,
    expected_state,
    expected_county,
):
    files = []
    for period in ("201201", "201202"):
        path = tmp_path / f"{geography}_{period}.txt"
        _write_bps_file(path, period, geography)
        files.append((path, _record(path, period)))
    result = clean_bps_archive(
        files,
        source_id,
        tmp_path / f"{source_id}.parquet",
        tmp_path / "coverage.csv",
    )
    assert result["observation_date"].nunique() == 2
    assert len(result) == 8
    assert set(result["geo_id"]) == {expected_geo}
    assert set(result["state_fips"]) == {expected_state}
    if expected_county is None:
        assert result["county_fips"].isna().all()
    else:
        assert set(result["county_fips"]) == {expected_county}
    assert not result.duplicated(
        ["observation_date", "geo_id", "variable_id"]
    ).any()
    assert result["is_imputed"].any()
    assert result["reported_value"].notna().all()


def test_bps_unknown_column_width_is_rejected(tmp_path):
    path = tmp_path / "county_bad.txt"
    _write_bps_file(path, "201201", "county", malformed=True)
    with pytest.raises(BPSFormatError, match="requires 30"):
        clean_bps(
            path,
            _record(path, "201201"),
            "census_bps_county",
            tmp_path / "bad.parquet",
        )
