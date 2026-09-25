import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.cleaning.acs import ACS_TABLE_MAP, clean_acs_table
from src.common.config import SourceConfig
from src.ingestion.download import ACS_HISTORICAL_TABLES, _acs_request_contract


def _record(source_id: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "data_vintage": "2026-08-07",
        "retrieved_at": "2026-08-07T00:00:00+00:00",
        "source_filename": f"{source_id}_test.json",
        "checksum_sha256": "abc123",
        "provider_last_updated": "",
    }


def _write_zcta_reference(root, zctas=("90001",)) -> None:
    path = root / "config" / "la_zcta_reference.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "reference_id": "test_zctas",
                "version": "test",
                "geography_type": "zcta",
                "selection_basis": "test fixture",
                "validated_source_id": "census_acs_5year_b25032",
                "validated_acs_vintage": 2024,
                "postal_zip_equivalence": False,
                "zctas": list(zctas),
            }
        ),
        encoding="utf-8",
    )


def _geography_path(root) -> object:
    path = root / "data" / "clean" / "geography.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"geo_type": ["zcta"], "zcta": ["90001"]}).to_parquet(path)
    return path


def _source(table_id: str) -> SourceConfig:
    return SourceConfig(
        source_id=f"census_acs_5year_{table_id.lower()}",
        provider="U.S. Census Bureau",
        dataset_name=f"ACS 5-Year Detailed Table {table_id}",
        source_page_url=(
            "https://www.census.gov/data/developers/data-sets/acs-5year.html"
        ),
        download_url="https://api.census.gov/data/",
        native_frequency="annual_5_year_estimate",
        geography_levels=("county", "zcta"),
        file_format="json",
        raw_subdir="census_acs",
        access_method="official_census_api",
        selected_start_date="2010-01-01",
        selected_end_date="2024-12-31",
    )


def _api_columns(table_id: str, start: int, end: int) -> list[str]:
    return [
        f"{table_id}_{sequence:03d}{estimate_or_moe}"
        for sequence in range(start, end + 1)
        for estimate_or_moe in ("E", "M")
    ]


def _response(
    table_id: str,
    start: int,
    end: int,
    geography: str,
    *,
    sentinel_column: str | None = None,
) -> list[list[str]]:
    columns = _api_columns(table_id, start, end)
    values = []
    for column in columns:
        sequence = int(column.split("_")[1][0:3])
        value = sequence if column.endswith("E") else sequence + 1000
        values.append("-666666666" if column == sentinel_column else str(value))
    if geography == "county":
        return [
            ["NAME", *columns, "state", "county"],
            ["Los Angeles County, California", *values, "06", "037"],
        ]
    return [
        ["NAME", *columns, "zip code tabulation area"],
        ["ZCTA5 90001", *values, "90001"],
    ]


def _write_api_archive(
    root,
    table_id: str,
    year: int,
    start_sequence: int,
    end_sequence: int,
    *,
    sentinel_column: str | None = None,
):
    requests = [
        {
            "year": year,
            "geography": "county",
            "response": _response(
                table_id,
                start_sequence,
                end_sequence,
                "county",
                sentinel_column=sentinel_column,
            ),
        }
    ]
    if year >= 2011:
        requests.append(
            {
                "year": year,
                "geography": "zcta",
                "response": _response(
                    table_id,
                    start_sequence,
                    end_sequence,
                    "zcta",
                    sentinel_column=sentinel_column,
                ),
            }
        )
    path = root / f"{table_id.lower()}_{year}.json"
    path.write_text(
        json.dumps(
            {
                "api": "Census ACS 5-year API",
                "table_id": table_id.lower(),
                "start_year": year,
                "end_year": year,
                "requests": requests,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_b25032_static_renter_cells_and_request_contract(tmp_path) -> None:
    expected_ids = {
        "acs_b25032_renter_total",
        "acs_b25032_renter_1_detached",
        "acs_b25032_renter_1_attached",
        "acs_b25032_renter_2_units",
        "acs_b25032_renter_3_to_4_units",
        "acs_b25032_renter_5_to_9_units",
        "acs_b25032_renter_10_to_19_units",
        "acs_b25032_renter_20_to_49_units",
        "acs_b25032_renter_50_plus_units",
        "acs_b25032_renter_mobile_home",
        "acs_b25032_renter_boat_rv_other",
    }
    mapped_ids = {variable_id for variable_id, _ in ACS_TABLE_MAP["b25032"].values()}
    assert expected_ids <= mapped_ids
    assert {f"{variable_id}_moe" for variable_id in expected_ids} <= mapped_ids

    _write_zcta_reference(tmp_path)
    contract = _acs_request_contract(
        SimpleNamespace(root=tmp_path),
        _source("B25032"),
        "2024-01-01",
        "2024-12-31",
    )
    selected = contract["request_selected_variables"].split("|")
    assert selected == _api_columns("B25032", 13, 23)
    assert len(contract["request_variables"].split(",")) == 23


def test_b25032_cleaner_preserves_direct_cells_moes_and_sentinel(tmp_path) -> None:
    _write_zcta_reference(tmp_path)
    geography_path = _geography_path(tmp_path)
    raw_path = _write_api_archive(
        tmp_path,
        "B25032",
        2024,
        13,
        23,
        sentinel_column="B25032_023M",
    )

    result = clean_acs_table(
        raw_path,
        _record("census_acs_5year_b25032"),
        "census_acs_5year_b25032",
        geography_path,
        tmp_path / "clean.parquet",
        expected_start_date="2024-01-01",
        expected_end_date="2024-12-31",
    )

    assert len(result) == 2 * 22
    county = result.loc[result["geo_id"].eq("COUNTY_06037")].set_index("variable_id")
    assert county.loc["acs_b25032_renter_total", "value"] == 13
    assert county.loc["acs_b25032_renter_total_moe", "value"] == 1013
    assert county.loc["acs_b25032_renter_boat_rv_other", "value"] == 23
    assert pd.isna(county.loc["acs_b25032_renter_boat_rv_other_moe", "value"])
    assert result["transformation_id"].eq("acs_5year_api_summary_v1").all()


@pytest.mark.parametrize(
    (
        "year",
        "total_sequence",
        "recent_start",
        "mixed_start",
        "regime_flag",
    ),
    (
        (
            2010,
            38,
            39,
            46,
            "b25127_recent_built_2000_plus_mixed_built_1980_to_1999",
        ),
        (
            2015,
            45,
            46,
            53,
            "b25127_recent_built_2010_plus_mixed_built_2000_to_2009",
        ),
        (
            2021,
            45,
            46,
            53,
            "b25127_recent_built_2020_plus_mixed_built_2000_to_2019",
        ),
    ),
)
def test_b25127_cleaner_normalizes_each_vintage_regime(
    tmp_path,
    year,
    total_sequence,
    recent_start,
    mixed_start,
    regime_flag,
) -> None:
    _write_zcta_reference(tmp_path)
    geography_path = _geography_path(tmp_path)
    raw_path = _write_api_archive(tmp_path, "B25127", year, 38, 59)

    result = clean_acs_table(
        raw_path,
        _record("census_acs_5year_b25127"),
        "census_acs_5year_b25127",
        geography_path,
        tmp_path / "clean.parquet",
        expected_start_date=f"{year}-01-01",
        expected_end_date=f"{year}-12-31",
    )

    expected_geographies = 1 if year == 2010 else 2
    assert len(result) == expected_geographies * 30
    county = result.loc[result["geo_id"].eq("COUNTY_06037")].set_index("variable_id")
    assert county.loc["acs_b25127_renter_total", "value"] == total_sequence
    assert county.loc["acs_b25127_renter_total_moe", "value"] == (
        total_sequence + 1000
    )
    assert county.loc[
        "acs_b25127_renter_recent_lower_bound_total", "value"
    ] == recent_start
    assert county.loc[
        "acs_b25127_renter_recent_lower_bound_mobile_other", "value"
    ] == (recent_start + 6)
    assert county.loc[
        "acs_b25127_renter_cutoff_mixed_total", "value"
    ] == mixed_start
    assert county.loc[
        "acs_b25127_renter_cutoff_mixed_mobile_other_moe", "value"
    ] == (mixed_start + 6 + 1000)
    assert result["quality_flag"].str.contains(regime_flag, regex=False).all()
    assert result["transformation_id"].eq(
        "acs_b25127_vintage_normalization_v1"
    ).all()


def test_b25127_request_contract_uses_union_below_api_limit(tmp_path) -> None:
    _write_zcta_reference(tmp_path)
    contract = _acs_request_contract(
        SimpleNamespace(root=tmp_path),
        _source("B25127"),
        None,
        None,
    )

    selected = contract["request_selected_variables"].split("|")
    assert selected == _api_columns("B25127", 38, 59)
    assert len(selected) == 44
    assert len(contract["request_variables"].split(",")) == 45
    assert "census_acs_5year_b25032" in ACS_HISTORICAL_TABLES
    assert "census_acs_5year_b25127" in ACS_HISTORICAL_TABLES


def test_b25127_cleaner_rejects_unconfigured_vintage(tmp_path) -> None:
    _write_zcta_reference(tmp_path)
    geography_path = _geography_path(tmp_path)
    raw_path = _write_api_archive(tmp_path, "B25127", 2025, 38, 59)

    with pytest.raises(ValueError, match="No ACS B25127 cell regime"):
        clean_acs_table(
            raw_path,
            _record("census_acs_5year_b25127"),
            "census_acs_5year_b25127",
            geography_path,
            tmp_path / "clean.parquet",
            expected_start_date="2025-01-01",
            expected_end_date="2025-12-31",
        )
