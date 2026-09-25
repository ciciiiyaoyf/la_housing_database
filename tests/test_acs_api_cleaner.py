import json
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote_plus

import pandas as pd
import pytest
import requests

from src.cleaning.acs import clean_acs_table
from src.cleaning.runner import clean_sources
from src.ingestion.download import (
    ACS_HISTORICAL_TABLES,
    _acs_request_contract,
    _download_census_acs_5year_api,
    download_source,
)
from src.common.config import SourceConfig


def _record() -> dict[str, str]:
    return {
        "data_vintage": "2026-07-10",
        "retrieved_at": "2026-07-10T00:00:00+00:00",
        "source_filename": "census_acs_5year_b25081_test.json",
        "checksum_sha256": "abc123",
        "provider_last_updated": "",
    }


def _write_zcta_reference(root, zctas=("90001",)):
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
                "validated_source_id": "census_acs_5year_b19013",
                "validated_acs_vintage": 2024,
                "postal_zip_equivalence": False,
                "zctas": list(zctas),
            }
        ),
        encoding="utf-8",
    )


def _write_geography(root, zctas=("90001",)):
    path = root / "data" / "clean" / "geography.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"geo_type": ["zcta"] * len(zctas), "zcta": list(zctas)}
    ).to_parquet(path)
    return path


def _write_b19013_api_archive(
    root,
    *,
    start_year,
    end_year,
    zctas_by_year=None,
    include_2010_zcta=False,
):
    zctas_by_year = zctas_by_year or {}
    requests_payload = []
    for year in range(start_year, end_year + 1):
        requests_payload.append(
            {
                "year": year,
                "geography": "county",
                "response": [
                    [
                        "NAME",
                        "B19013_001E",
                        "B19013_001M",
                        "state",
                        "county",
                    ],
                    [
                        "Los Angeles County, California",
                        str(50000 + year),
                        "500",
                        "06",
                        "037",
                    ],
                ],
            }
        )
        if year >= 2011 or include_2010_zcta:
            zcta_rows = [
                [f"ZCTA5 {zcta}", str(40000 + year), "700", zcta]
                for zcta in zctas_by_year.get(year, ())
            ]
            requests_payload.append(
                {
                    "year": year,
                    "geography": "zcta",
                    "response": [
                        [
                            "NAME",
                            "B19013_001E",
                            "B19013_001M",
                            "zip code tabulation area",
                        ],
                        *zcta_rows,
                    ],
                }
            )
    path = root / "acs_b19013_api.json"
    path.write_text(
        json.dumps(
            {
                "api": "Census ACS 5-year API",
                "table_id": "b19013",
                "start_year": start_year,
                "end_year": end_year,
                "requests": requests_payload,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_acs_supplement_api_archive(
    root,
    *,
    table_id,
    estimate_column,
    moe_column,
):
    requests_payload = []
    for year in (2010, 2011):
        requests_payload.append(
            {
                "year": year,
                "geography": "county",
                "response": [
                    [
                        "NAME",
                        estimate_column,
                        moe_column,
                        "state",
                        "county",
                    ],
                    [
                        "Los Angeles County, California",
                        str(1000 + year),
                        "100",
                        "06",
                        "037",
                    ],
                ],
            }
        )
        if year >= 2011:
            requests_payload.append(
                {
                    "year": year,
                    "geography": "zcta",
                    "response": [
                        [
                            "NAME",
                            estimate_column,
                            moe_column,
                            "zip code tabulation area",
                        ],
                        [
                            "ZCTA5 90001",
                            "25",
                            "-666666666",
                            "90001",
                        ],
                    ],
                }
            )
    path = root / f"acs_{table_id}_api.json"
    path.write_text(
        json.dumps(
            {
                "api": "Census ACS 5-year API",
                "table_id": table_id,
                "start_year": 2010,
                "end_year": 2011,
                "requests": requests_payload,
            }
        ),
        encoding="utf-8",
    )
    return path


def _acs_source() -> SourceConfig:
    return SourceConfig(
        source_id="census_acs_5year_b19013",
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year Detailed Table B19013 Median Household Income",
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


def _project_config(root):
    return SimpleNamespace(
        root=root,
        pipeline={"request_timeout_seconds": 120, "download_chunk_bytes": 8192},
    )


def _write_today_manifest_archive(root, source, contract):
    archive = root / "data" / "raw" / "census_acs" / "existing.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("existing immutable archive", encoding="utf-8")
    manifest = root / "metadata" / "raw_file_manifest.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "source_id": source.source_id,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "relative_path": archive.relative_to(root).as_posix(),
                "checksum_sha256": "0" * 64,
                **contract,
            }
        ]
    ).to_csv(manifest, index=False)
    return archive


def test_acs_api_json_cleaner_preserves_history_fips_and_zcta_filter(tmp_path):
    _write_zcta_reference(tmp_path, ("90001",))
    # Deliberately stale geography proves config is the canonical ZCTA source.
    geography_path = _write_geography(tmp_path, ("99999",))
    raw_path = tmp_path / "acs_api.json"
    raw_path.write_text(
        json.dumps(
            {
                "api": "Census ACS 5-year API",
                "table_id": "b25081",
                "start_year": 2010,
                "end_year": 2011,
                "requests": [
                    {
                        "year": 2010,
                        "geography": "county",
                        "response": [
                            ["NAME", "B25081_002E", "B25081_002M", "ucgid"],
                            [
                                "Los Angeles County, California",
                                "123",
                                "9",
                                "050C0US06037",
                            ],
                        ],
                    },
                    {
                        "year": 2011,
                        "geography": "county",
                        "response": [
                            ["NAME", "B25081_002E", "B25081_002M", "ucgid"],
                            [
                                "Los Angeles County, California",
                                "125",
                                "8",
                                "050C0US06037",
                            ],
                        ],
                    },
                    {
                        "year": 2011,
                        "geography": "zcta_chunk_01",
                        "response": [
                            ["NAME", "B25081_002E", "B25081_002M", "ucgid"],
                            ["ZCTA5 90001", "11", "1", "860Z200US90001"],
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    frame = clean_acs_table(
        raw_path,
        _record(),
        "census_acs_5year_b25081",
        geography_path,
        tmp_path / "clean.parquet",
        expected_start_date="2010-01-01",
        expected_end_date="2011-12-31",
    )

    assert set(frame["observation_date"].dt.year) == {2010, 2011}
    assert set(frame["geo_id"]) == {"COUNTY_06037", "ZCTA_90001"}
    county = frame.loc[frame["geo_id"].eq("COUNTY_06037")]
    assert set(county["state_fips"]) == {"06"}
    assert set(county["county_fips"]) == {"06037"}
    assert "ZCTA_99999" not in set(frame["geo_id"])
    assert not frame.duplicated(["geo_id", "observation_date", "variable_id"]).any()


def test_acs_b25004_static_cleaner_preserves_geography_and_missing_sentinel(
    tmp_path,
):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("99999",))
    raw_path = tmp_path / "acs_b25004_2024.dat"
    raw_path.write_text(
        "\n".join(
            [
                "GEO_ID|B25004_E002|B25004_M002",
                "0500000US06037|12345|321",
                "860Z200US90001|47|-666666666",
                "860Z200US99999|999|12",
            ]
        ),
        encoding="utf-8",
    )

    frame = clean_acs_table(
        raw_path,
        _record(),
        "census_acs_5year_b25004",
        geography_path,
        tmp_path / "clean.parquet",
    )

    assert set(frame["geo_id"]) == {"COUNTY_06037", "ZCTA_90001"}
    assert "ZCTA_99999" not in set(frame["geo_id"])
    assert set(frame["variable_id"]) == {
        "acs_for_rent_vacant_units",
        "acs_for_rent_vacant_units_moe",
    }
    assert set(frame["observation_date"]) == {pd.Timestamp("2024-12-31")}
    assert set(frame["unit"]) == {"housing_units"}
    assert len(frame) == 4

    county = frame.loc[frame["geo_id"].eq("COUNTY_06037")]
    assert set(county["state_fips"]) == {"06"}
    assert set(county["county_fips"]) == {"06037"}
    assert county.set_index("variable_id").loc[
        "acs_for_rent_vacant_units", "value"
    ] == 12345
    assert county.set_index("variable_id").loc[
        "acs_for_rent_vacant_units_moe", "value"
    ] == 321

    zcta = frame.loc[frame["geo_id"].eq("ZCTA_90001")]
    assert zcta["state_fips"].isna().all()
    assert zcta["county_fips"].isna().all()
    zcta_moe = zcta.loc[
        zcta["variable_id"].eq("acs_for_rent_vacant_units_moe")
    ].iloc[0]
    assert pd.isna(zcta_moe["value"])
    assert pd.isna(zcta_moe["reported_value"])


@pytest.mark.parametrize(
    ("source_id", "estimate_column", "moe_column", "estimate_id", "moe_id"),
    [
        (
            "census_acs_5year_b05002",
            "B05002_E013",
            "B05002_M013",
            "acs_foreign_born_population",
            "acs_foreign_born_population_moe",
        ),
        (
            "census_acs_5year_b07001",
            "B07001_E081",
            "B07001_M081",
            "acs_moved_from_abroad_past_year",
            "acs_moved_from_abroad_past_year_moe",
        ),
    ],
)
def test_acs_migration_supplement_cleaner_preserves_estimate_and_moe(
    tmp_path,
    source_id,
    estimate_column,
    moe_column,
    estimate_id,
    moe_id,
):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("90001",))
    raw_path = tmp_path / f"{source_id}_2024.dat"
    raw_path.write_text(
        "\n".join(
            [
                f"GEO_ID|{estimate_column}|{moe_column}",
                "0500000US06037|3456789|12345",
                "860Z200US90001|67890|2345",
            ]
        ),
        encoding="utf-8",
    )

    frame = clean_acs_table(
        raw_path,
        _record(),
        source_id,
        geography_path,
        tmp_path / "clean.parquet",
    )

    assert len(frame) == 4
    assert set(frame["variable_id"]) == {estimate_id, moe_id}
    assert set(frame["unit"]) == {"persons"}
    county = frame.loc[frame["geo_id"].eq("COUNTY_06037")].set_index(
        "variable_id"
    )
    assert county.loc[estimate_id, "value"] == 3456789
    assert county.loc[moe_id, "value"] == 12345
    zcta = frame.loc[frame["geo_id"].eq("ZCTA_90001")].set_index(
        "variable_id"
    )
    assert zcta.loc[estimate_id, "value"] == 67890
    assert zcta.loc[moe_id, "value"] == 2345
    assert source_id in ACS_HISTORICAL_TABLES


@pytest.mark.parametrize(
    ("source_id", "table_id", "estimate_column", "moe_column"),
    [
        (
            "census_acs_5year_b05002",
            "b05002",
            "B05002_013E",
            "B05002_013M",
        ),
        (
            "census_acs_5year_b07001",
            "b07001",
            "B07001_081E",
            "B07001_081M",
        ),
    ],
)
def test_acs_migration_supplement_api_archive_uses_direct_cells_and_grid(
    tmp_path,
    source_id,
    table_id,
    estimate_column,
    moe_column,
):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("90001",))
    raw_path = _write_acs_supplement_api_archive(
        tmp_path,
        table_id=table_id,
        estimate_column=estimate_column,
        moe_column=moe_column,
    )

    frame = clean_acs_table(
        raw_path,
        _record(),
        source_id,
        geography_path,
        tmp_path / "clean.parquet",
        expected_start_date="2010-01-01",
        expected_end_date="2011-12-31",
    )

    assert len(frame) == 6
    assert set(frame["observation_date"].dt.year) == {2010, 2011}
    assert set(frame["geo_id"]) == {"COUNTY_06037", "ZCTA_90001"}
    assert frame["transformation_id"].eq("acs_5year_api_summary_v1").all()
    assert not frame.duplicated(
        ["observation_date", "geo_id", "variable_id"]
    ).any()
    zcta_moe = frame.loc[
        frame["geo_id"].eq("ZCTA_90001")
        & frame["variable_id"].str.endswith("_moe"),
        "value",
    ]
    assert len(zcta_moe) == 1
    assert zcta_moe.isna().all()


def test_acs_api_cleaner_rejects_missing_configured_zcta(tmp_path):
    _write_zcta_reference(tmp_path, ("90001", "90002"))
    geography_path = _write_geography(tmp_path, ("90001", "90002"))
    raw_path = _write_b19013_api_archive(
        tmp_path,
        start_year=2011,
        end_year=2011,
        zctas_by_year={2011: ("90001",)},
    )
    output_path = tmp_path / "clean.parquet"

    with pytest.raises(ValueError, match="ACS clean geography grid mismatch"):
        clean_acs_table(
            raw_path,
            _record(),
            "census_acs_5year_b19013",
            geography_path,
            output_path,
            expected_start_date="2011-01-01",
            expected_end_date="2011-12-31",
        )

    assert not output_path.exists()


def test_acs_api_cleaner_rejects_duplicate_target_observation(tmp_path):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("90001",))
    raw_path = _write_b19013_api_archive(
        tmp_path,
        start_year=2011,
        end_year=2011,
        zctas_by_year={2011: ("90001", "90001")},
    )

    with pytest.raises(ValueError, match="Duplicate ACS clean observations"):
        clean_acs_table(
            raw_path,
            _record(),
            "census_acs_5year_b19013",
            geography_path,
            tmp_path / "clean.parquet",
            expected_start_date="2011-01-01",
            expected_end_date="2011-12-31",
        )


def test_acs_api_cleaner_rejects_2010_zcta_request(tmp_path):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("90001",))
    raw_path = _write_b19013_api_archive(
        tmp_path,
        start_year=2010,
        end_year=2010,
        zctas_by_year={2010: ("90001",)},
        include_2010_zcta=True,
    )

    with pytest.raises(ValueError, match="must not contain ZCTA geography"):
        clean_acs_table(
            raw_path,
            _record(),
            "census_acs_5year_b19013",
            geography_path,
            tmp_path / "clean.parquet",
            expected_start_date="2010-01-01",
            expected_end_date="2010-12-31",
        )


def test_acs_api_cleaner_rejects_partial_configured_year_range(tmp_path):
    _write_zcta_reference(tmp_path, ("90001",))
    geography_path = _write_geography(tmp_path, ("90001",))
    raw_path = _write_b19013_api_archive(
        tmp_path,
        start_year=2011,
        end_year=2011,
        zctas_by_year={2011: ("90001",)},
    )

    with pytest.raises(ValueError, match="archive start year mismatch"):
        clean_acs_table(
            raw_path,
            _record(),
            "census_acs_5year_b19013",
            geography_path,
            tmp_path / "clean.parquet",
            expected_start_date="2010-01-01",
            expected_end_date="2011-12-31",
        )


def test_explicit_acs_clean_failure_is_reraised(tmp_path, monkeypatch):
    source = SourceConfig(
        source_id="census_acs_5year_b19013",
        provider="U.S. Census Bureau",
        dataset_name="ACS B19013",
        source_page_url="https://www.census.gov/",
        download_url="https://api.census.gov/data/",
        native_frequency="annual_5_year_estimate",
        geography_levels=("county", "zcta"),
        file_format="json",
        selected_start_date="2010-01-01",
        selected_end_date="2024-12-31",
    )
    config = SimpleNamespace(
        root=tmp_path,
        selected_sources=lambda source_filter: (source,),
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.cleaning.runner.raw_path_for_vintage",
        lambda *args, **kwargs: (raw_path, _record()),
    )

    def fail_clean(*args, **kwargs):
        raise ValueError("intentional ACS grid failure")

    monkeypatch.setattr("src.cleaning.runner.clean_acs_table", fail_clean)

    with pytest.raises(ValueError, match="intentional ACS grid failure"):
        clean_sources(config, source_filter=source.source_id)

    with pytest.raises(ValueError, match="intentional ACS grid failure"):
        clean_sources(config, source_filter=None)


def test_acs_api_download_surfaces_missing_key_without_archiving(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    (tmp_path / "data" / "clean").mkdir(parents=True)
    _write_zcta_reference(tmp_path)
    pd.DataFrame({"geo_type": ["zcta"], "zcta": ["90001"]}).to_parquet(
        tmp_path / "data" / "clean" / "geography.parquet"
    )

    class MissingKeyResponse:
        url = (
            "https://api.census.gov/data/2024/acs/acs5?"
            "get=group(B25081)&ucgid=050C0US06037"
        )
        text = "<html><title>Missing Key</title>A valid key must be included</html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    class MissingKeySession:
        def get(self, *args, **kwargs):
            return MissingKeyResponse()

    monkeypatch.setattr(
        "src.ingestion.download.requests.Session", lambda: MissingKeySession()
    )
    source = SourceConfig(
        source_id="census_acs_5year_b25081",
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year Detailed Table B25081 Mortgage Status",
        source_page_url="https://www.census.gov/data/developers/data-sets/acs-5year.html",
        download_url="https://api.census.gov/data/",
        native_frequency="annual_5_year_estimate",
        geography_levels=("county", "zcta"),
        file_format="json",
        selected_start_date="2010-01-01",
        selected_end_date="2024-12-31",
    )

    try:
        _download_census_acs_5year_api(
            SimpleNamespace(root=tmp_path),
            source,
            tmp_path / "raw.json",
            "2024-01-01",
            "2024-12-31",
        )
    except RuntimeError as exc:
        assert "CENSUS_API_KEY" in str(exc)
    else:
        raise AssertionError("Historical ACS API missing-key rejection should fail")
    assert not (tmp_path / "raw.json").exists()


def test_acs_api_download_never_exposes_key_from_http_error(tmp_path, monkeypatch):
    secret = "unitTestCensusKey1234567890"
    monkeypatch.setenv("CENSUS_API_KEY", secret)
    (tmp_path / "data" / "clean").mkdir(parents=True)
    _write_zcta_reference(tmp_path)
    pd.DataFrame({"geo_type": ["zcta"], "zcta": ["90001"]}).to_parquet(
        tmp_path / "data" / "clean" / "geography.parquet"
    )

    class RejectedResponse:
        history = []
        status_code = 403
        text = "request rejected"
        url = (
            "https://api.census.gov/data/2024/acs/acs5?"
            f"get=group(B25003)&ucgid=050C0US06037&key={secret}"
        )
        request = SimpleNamespace(url=url)

        def raise_for_status(self):
            raise requests.HTTPError(
                f"403 Client Error for url: {self.url}", response=self
            )

    class RejectedSession:
        def get(self, *args, **kwargs):
            return RejectedResponse()

    monkeypatch.setattr(
        "src.ingestion.download.requests.Session", lambda: RejectedSession()
    )
    source = SourceConfig(
        source_id="census_acs_5year_b25003",
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year Detailed Table B25003 Tenure",
        source_page_url="https://www.census.gov/data/developers/data-sets/acs-5year.html",
        download_url="https://api.census.gov/data/",
        native_frequency="annual_5_year_estimate",
        geography_levels=("county", "zcta"),
        file_format="json",
        selected_start_date="2010-01-01",
        selected_end_date="2024-12-31",
    )

    with pytest.raises(RuntimeError) as exc_info:
        _download_census_acs_5year_api(
            SimpleNamespace(root=tmp_path),
            source,
            tmp_path / "raw.json",
            "2024-01-01",
            "2024-12-31",
        )

    assert secret not in str(exc_info.value)
    assert "CENSUS_API_KEY" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert not (tmp_path / "raw.json").exists()


def test_acs_api_download_uses_vintage_specific_geographies_and_redacts_key(
    tmp_path, monkeypatch
):
    secret = "unitTestCensusKey1234567890"
    monkeypatch.setenv("CENSUS_API_KEY", f"  {secret}\n")
    (tmp_path / "data" / "clean").mkdir(parents=True)
    _write_zcta_reference(tmp_path)
    pd.DataFrame({"geo_type": ["zcta"], "zcta": ["90001"]}).to_parquet(
        tmp_path / "data" / "clean" / "geography.parquet"
    )

    class SuccessfulResponse:
        history = []
        status_code = 200
        text = ""

        def __init__(self, params):
            self.request = SimpleNamespace(
                url=(
                    "https://api.census.gov/data/test/acs/acs5?"
                    f"get=group(B19013)&key={params['key']}"
                )
            )
            self.url = self.request.url
            if params["for"].startswith("zip code tabulation area"):
                geography_columns = (
                    ["state", "zip code tabulation area"]
                    if "in" in params
                    else ["zip code tabulation area"]
                )
                self._body = [
                    [
                        "NAME",
                        "B19013_001E",
                        "B19013_001M",
                        *geography_columns,
                    ],
                    [
                        "ZCTA5 90001",
                        "59000",
                        "900",
                        *(["06", "90001"] if "in" in params else ["90001"]),
                    ],
                ]
            else:
                self._body = [
                    [
                        "NAME",
                        "B19013_001E",
                        "B19013_001M",
                        "state",
                        "county",
                    ],
                    ["Los Angeles County, California", "58000", "800", "06", "037"],
                ]

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class SuccessfulSession:
        def __init__(self):
            self.calls = []

        def get(self, url, *, params, timeout, headers):
            del timeout, headers
            self.calls.append((url, dict(params)))
            return SuccessfulResponse(params)

    session = SuccessfulSession()
    monkeypatch.setattr("src.ingestion.download.requests.Session", lambda: session)
    source = SourceConfig(
        source_id="census_acs_5year_b19013",
        provider="U.S. Census Bureau",
        dataset_name="ACS 5-Year Detailed Table B19013 Median Household Income",
        source_page_url="https://www.census.gov/data/developers/data-sets/acs-5year.html",
        download_url="https://api.census.gov/data/",
        native_frequency="annual_5_year_estimate",
        geography_levels=("county", "zcta"),
        file_format="json",
        selected_start_date="2010-01-01",
        selected_end_date="2020-12-31",
    )
    raw_path = tmp_path / "raw.json"

    metadata = _download_census_acs_5year_api(
        SimpleNamespace(root=tmp_path),
        source,
        raw_path,
        "2010-01-01",
        "2020-12-31",
    )

    calls_by_year = {}
    for url, params in session.calls:
        year = int(url.split("/data/", 1)[1].split("/", 1)[0])
        calls_by_year.setdefault(year, []).append(params)
        assert params["key"] == secret
    assert len(calls_by_year[2010]) == 1
    assert calls_by_year[2010][0]["for"] == "county:037"
    assert "ucgid" not in calls_by_year[2010][0]
    assert len(calls_by_year[2011]) == 2
    assert any(
        params.get("for") == "zip code tabulation area:*"
        and params.get("in") == "state:06"
        for params in calls_by_year[2011]
    )
    assert any(
        params.get("for") == "zip code tabulation area:*" and "in" not in params
        for params in calls_by_year[2020]
    )
    assert secret not in raw_path.read_text(encoding="utf-8")
    assert metadata["request_zcta_unavailable_years"] == "2010"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["request_contract"] == {
        key: metadata[key]
        for key in payload["request_contract"]
    }
    assert len(metadata["request_zcta_reference_sha256"]) == 64


def test_acs_invalid_json_excerpt_and_traceback_do_not_expose_key(
    tmp_path, monkeypatch
):
    secret = "unit+Test/Census=Key"
    encoded_secret = quote_plus(secret, safe="")
    monkeypatch.setenv("CENSUS_API_KEY", secret)
    _write_zcta_reference(tmp_path)

    class InvalidJsonResponse:
        history = []
        status_code = 200
        text = f"invalid response key={secret}; encoded={encoded_secret}"
        url = (
            "https://api.census.gov/data/2024/acs/acs5?"
            f"get=B19013_001E&key={encoded_secret}"
        )
        request = SimpleNamespace(url=url)

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError(f"decoder echoed {secret}")

    class InvalidJsonSession:
        def get(self, *args, **kwargs):
            return InvalidJsonResponse()

    monkeypatch.setattr(
        "src.ingestion.download.requests.Session", lambda: InvalidJsonSession()
    )
    raw_path = tmp_path / "raw.json"

    with pytest.raises(RuntimeError) as exc_info:
        _download_census_acs_5year_api(
            _project_config(tmp_path),
            _acs_source(),
            raw_path,
            "2024-01-01",
            "2024-12-31",
        )

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in str(exc_info.value)
    assert encoded_secret not in str(exc_info.value)
    assert secret not in rendered
    assert encoded_secret not in rendered
    assert "CENSUS_API_KEY" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__
    assert not raw_path.exists()


def test_same_day_acs_reuse_rejects_mismatched_range_then_reuses_exact_contract(
    tmp_path, monkeypatch
):
    _write_zcta_reference(tmp_path)
    source = _acs_source()
    config = _project_config(tmp_path)
    original_contract = _acs_request_contract(config, source, None, None)
    original_archive = _write_today_manifest_archive(
        tmp_path, source, original_contract
    )
    calls = []

    def fake_download(
        config_arg,
        source_arg,
        temp_path,
        start_date,
        end_date,
        *,
        request_contract=None,
    ):
        del config_arg, source_arg, start_date, end_date
        calls.append(dict(request_contract))
        temp_path.write_text(
            json.dumps({"request_contract": request_contract, "requests": []}),
            encoding="utf-8",
        )
        return {
            **request_contract,
            "response_latest_observation": "2024-12-31",
            "response_observation_count": "0",
        }

    monkeypatch.setattr(
        "src.ingestion.download._download_census_acs_5year_api", fake_download
    )

    record = download_source(
        config,
        source,
        force=False,
        explicitly_selected=True,
        start_date="2020-01-01",
        end_date="2024-12-31",
    )

    assert len(calls) == 1
    assert calls[0]["request_start_year"] == "2020"
    assert record["request_start_year"] == "2020"
    assert record["relative_path"] != original_archive.relative_to(tmp_path).as_posix()
    assert original_archive.read_text(encoding="utf-8") == (
        "existing immutable archive"
    )
    manifest = pd.read_csv(
        tmp_path / "metadata" / "raw_file_manifest.csv", dtype=str
    )
    assert len(manifest) == 2
    assert set(manifest["request_start_year"]) == {"2010", "2020"}

    reused = download_source(
        config,
        source,
        force=False,
        explicitly_selected=True,
        start_date="2020-01-01",
        end_date="2024-12-31",
    )
    assert len(calls) == 1
    assert reused["relative_path"] == record["relative_path"]


def test_same_day_acs_reuse_rejects_semantically_changed_zcta_reference(
    tmp_path, monkeypatch
):
    _write_zcta_reference(tmp_path)
    source = _acs_source()
    config = _project_config(tmp_path)
    dynamic_contract_kwargs = {
        "latest_available_year": 2024,
        "end_year_resolution": "official_census_group_endpoint",
    }
    monkeypatch.setattr(
        "src.ingestion.download._probe_latest_acs_5year_vintage",
        lambda table_id, max_year: 2024,
    )
    original_contract = _acs_request_contract(
        config, source, None, None, **dynamic_contract_kwargs
    )
    original_archive = _write_today_manifest_archive(
        tmp_path, source, original_contract
    )
    reference_path = tmp_path / "config" / "la_zcta_reference.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))

    # Whitespace and object-key ordering do not alter the canonical hash.
    reference_path.write_text(
        json.dumps(reference, indent=4, sort_keys=True), encoding="utf-8"
    )
    reformatted_contract = _acs_request_contract(
        config, source, None, None, **dynamic_contract_kwargs
    )
    assert (
        reformatted_contract["request_zcta_reference_sha256"]
        == original_contract["request_zcta_reference_sha256"]
    )

    # Keep ID, version, count, and ZCTA values fixed; only the canonical hash
    # can detect this semantic reference change.
    reference["selection_basis"] = "changed test selection basis"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    changed_contract = _acs_request_contract(
        config, source, None, None, **dynamic_contract_kwargs
    )
    assert changed_contract["request_zcta_reference_id"] == (
        original_contract["request_zcta_reference_id"]
    )
    assert changed_contract["request_zcta_reference_version"] == (
        original_contract["request_zcta_reference_version"]
    )
    assert changed_contract["request_zcta_count"] == (
        original_contract["request_zcta_count"]
    )
    assert changed_contract["request_zcta_reference_sha256"] != (
        original_contract["request_zcta_reference_sha256"]
    )
    calls = []

    def fake_download(
        config_arg,
        source_arg,
        temp_path,
        start_date,
        end_date,
        *,
        request_contract=None,
    ):
        del config_arg, source_arg, start_date, end_date
        calls.append(dict(request_contract))
        temp_path.write_text(
            json.dumps({"request_contract": request_contract, "requests": []}),
            encoding="utf-8",
        )
        return {
            **request_contract,
            "response_latest_observation": "2024-12-31",
            "response_observation_count": "0",
        }

    monkeypatch.setattr(
        "src.ingestion.download._download_census_acs_5year_api", fake_download
    )
    record = download_source(
        config,
        source,
        force=False,
        explicitly_selected=True,
        start_date=None,
        end_date=None,
    )

    assert len(calls) == 1
    assert calls[0] == changed_contract
    assert record["request_zcta_reference_sha256"] == (
        changed_contract["request_zcta_reference_sha256"]
    )
    assert record["relative_path"] != original_archive.relative_to(tmp_path).as_posix()
    assert original_archive.read_text(encoding="utf-8") == (
        "existing immutable archive"
    )
    new_archive = tmp_path / record["relative_path"]
    archived_payload = json.loads(new_archive.read_text(encoding="utf-8"))
    assert archived_payload["request_contract"] == changed_contract
    manifest = pd.read_csv(
        tmp_path / "metadata" / "raw_file_manifest.csv", dtype=str
    )
    assert manifest.iloc[-1]["request_zcta_reference_sha256"] == (
        changed_contract["request_zcta_reference_sha256"]
    )
