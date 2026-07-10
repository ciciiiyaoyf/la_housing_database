import json
from types import SimpleNamespace

import pandas as pd

from src.cleaning.acs import clean_acs_table
from src.ingestion.download import _download_census_acs_5year_api
from src.common.config import SourceConfig


def _record() -> dict[str, str]:
    return {
        "data_vintage": "2026-07-10",
        "retrieved_at": "2026-07-10T00:00:00+00:00",
        "source_filename": "census_acs_5year_b25081_test.json",
        "checksum_sha256": "abc123",
        "provider_last_updated": "",
    }


def test_acs_api_json_cleaner_preserves_history_fips_and_zcta_filter(tmp_path):
    geography_path = tmp_path / "geography.parquet"
    pd.DataFrame({"postal_zip": ["90001"]}).to_parquet(geography_path)
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
                        "year": 2010,
                        "geography": "zcta_chunk_01",
                        "response": [
                            ["NAME", "B25081_002E", "B25081_002M", "ucgid"],
                            ["ZCTA5 90001", "10", "2", "860Z200US90001"],
                            ["ZCTA5 99999", "99", "3", "860Z200US99999"],
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
    )

    assert set(frame["observation_date"].dt.year) == {2010, 2011}
    assert set(frame["geo_id"]) == {"COUNTY_06037", "ZCTA_90001"}
    county = frame.loc[frame["geo_id"].eq("COUNTY_06037")]
    assert set(county["state_fips"]) == {"06"}
    assert set(county["county_fips"]) == {"06037"}
    assert "ZCTA_99999" not in set(frame["geo_id"])
    assert not frame.duplicated(["geo_id", "observation_date", "variable_id"]).any()


def test_acs_api_download_surfaces_missing_key_without_archiving(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    (tmp_path / "data" / "clean").mkdir(parents=True)
    pd.DataFrame({"postal_zip": ["90001"]}).to_parquet(
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
