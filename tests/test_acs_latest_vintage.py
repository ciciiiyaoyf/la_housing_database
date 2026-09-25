import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.common.config import SourceConfig
from src.ingestion.download import (
    _acs_request_contract,
    _probe_latest_acs_5year_vintage,
    _resolve_acs_5year_end_year,
    download_source,
)


def _source() -> SourceConfig:
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


def _config(root):
    return SimpleNamespace(
        root=root,
        pipeline={"request_timeout_seconds": 120, "download_chunk_bytes": 8192},
    )


def _write_zcta_reference(root) -> None:
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
                "zctas": ["90001"],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_acs_probe_cache():
    _probe_latest_acs_5year_vintage.cache_clear()
    yield
    _probe_latest_acs_5year_vintage.cache_clear()


def test_latest_vintage_probe_uses_official_group_endpoint_and_process_cache(
    monkeypatch,
):
    calls = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {"variables": {"B19013_001E": {"label": "Estimate"}}}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(200 if "/2025/" in url else 404)

    monkeypatch.setenv("CENSUS_API_KEY", "must-never-enter-discovery-request")
    monkeypatch.setattr("src.ingestion.download.requests.get", fake_get)

    assert _probe_latest_acs_5year_vintage("b19013", 2026) == 2025
    assert _probe_latest_acs_5year_vintage("b19013", 2026) == 2025

    assert [url for url, _ in calls] == [
        "https://api.census.gov/data/2026/acs/acs5/groups/B19013.json",
        "https://api.census.gov/data/2025/acs/acs5/groups/B19013.json",
    ]
    assert all("params" not in kwargs for _, kwargs in calls)
    assert all("key" not in str(kwargs).casefold() for _, kwargs in calls)


def test_normal_download_ignores_static_2024_cap_when_2025_is_published(
    tmp_path,
    monkeypatch,
):
    _write_zcta_reference(tmp_path)
    source = _source()
    config = _config(tmp_path)
    probe_calls = []
    captured_contracts = []

    def fake_probe(table_id, max_year):
        probe_calls.append((table_id, max_year))
        return 2025

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
        captured_contracts.append(dict(request_contract))
        temp_path.write_text(
            json.dumps({"request_contract": request_contract, "requests": []}),
            encoding="utf-8",
        )
        return {
            **request_contract,
            "response_latest_observation": "2025-12-31",
            "response_observation_count": "0",
        }

    monkeypatch.setattr(
        "src.ingestion.download._probe_latest_acs_5year_vintage", fake_probe
    )
    monkeypatch.setattr(
        "src.ingestion.download._download_census_acs_5year_api", fake_download
    )

    record = download_source(
        config,
        source,
        force=True,
        explicitly_selected=True,
        start_date=None,
        end_date=None,
    )

    assert probe_calls == [("B19013", date.today().year)]
    assert captured_contracts[0]["request_end_year"] == "2025"
    assert captured_contracts[0]["request_latest_available_year"] == "2025"
    assert captured_contracts[0]["request_end_year_resolution"] == (
        "official_census_group_endpoint"
    )
    assert "2020-2025 nationwide ZCTA:*" in (
        captured_contracts[0]["request_geographies"]
    )
    assert record["response_latest_observation"] == "2025-12-31"


def test_explicit_end_date_is_a_cap_without_unneeded_probe(tmp_path, monkeypatch):
    _write_zcta_reference(tmp_path)

    def fail_probe(*args, **kwargs):
        raise AssertionError("an established explicit vintage must not be probed")

    monkeypatch.setattr(
        "src.ingestion.download._probe_latest_acs_5year_vintage", fail_probe
    )
    end_year, resolution = _resolve_acs_5year_end_year(
        _source(), "2023-12-31"
    )
    contract = _acs_request_contract(
        _config(tmp_path),
        _source(),
        None,
        "2023-12-31",
        latest_available_year=end_year,
        end_year_resolution=resolution,
    )

    assert contract["request_end_year"] == "2023"
    assert contract["request_latest_available_year"] == "2023"
    assert contract["request_end_year_resolution"] == "explicit_user_end_date"


def test_discovery_failure_logs_safe_fallback_without_api_key(
    monkeypatch,
    caplog,
):
    secret = "unitTestCensusKeyMustNotLeak"
    monkeypatch.setenv("CENSUS_API_KEY", secret)

    def fail_probe(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(f"prepared URL unexpectedly echoed {secret}")

    monkeypatch.setattr(
        "src.ingestion.download._probe_latest_acs_5year_vintage", fail_probe
    )
    with caplog.at_level("WARNING", logger="la_housing.download"):
        end_year, resolution = _resolve_acs_5year_end_year(_source(), None)

    assert end_year == 2024
    assert resolution == "configured_fallback_after_discovery_failure"
    assert "official Census group endpoint" in caplog.text
    assert "safe fallback year 2024" in caplog.text
    assert secret not in caplog.text
