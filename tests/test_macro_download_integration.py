"""No-network coverage of CES lifecycle acquisition and configured contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cleaning.macro_labor import (
    BLS_CES_LABOR_SERIES_IDS,
    SERIES,
    SOURCE_ID,
    clean_macro_labor,
)
from src.common.config import SourceConfig, load_config
from src.ingestion.download import _download_bls


ROOT = Path(__file__).resolve().parents[1]
EARNINGS_SERIES_IDS = {
    "SMU06000000500000003",
    "SMU06310840500000003",
}
EMPLOYMENT_SERIES_IDS = set(BLS_CES_LABOR_SERIES_IDS) - EARNINGS_SERIES_IDS
LEVEL_VARIABLE_IDS = {spec[0] for spec in SERIES.values()}
ALL_VARIABLE_IDS = LEVEL_VARIABLE_IDS | {
    variable_id + "_yoy_pct" for variable_id in LEVEL_VARIABLE_IDS
}


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class _Session:
    def __init__(self, missing_series=None, empty_series=None):
        self.calls = []
        self.missing_series = missing_series
        self.empty_series = empty_series

    def post(self, url, *, json, **kwargs):
        self.calls.append({"url": url, "payload": json, **kwargs})
        year = int(json["endyear"])
        series = []
        for series_id in json["seriesid"]:
            if series_id == self.missing_series:
                continue
            # This fixture emulates provider history rather than inventing AHE
            # observations before its published 2007 start.
            data = [] if (series_id in EARNINGS_SERIES_IDS and year < 2007) else [{
                "year": str(year), "period": "M07", "value": "100.0",
                "footnotes": [{"code": "P", "text": "Preliminary"}],
            }]
            if series_id == self.empty_series:
                data = []
            series.append({"seriesID": series_id, "data": data})
        return _Response({
            "status": "REQUEST_SUCCEEDED", "message": [],
            "Results": {"series": series},
        })


def _source():
    return SourceConfig(
        source_id=SOURCE_ID,
        provider="U.S. Bureau of Labor Statistics",
        dataset_name="California and LA division CES industry employment and earnings",
        source_page_url="https://www.bls.gov/sae/",
        download_url="https://api.bls.gov/publicAPI/v2/timeseries/data/",
        native_frequency="monthly",
        geography_levels=("state", "metropolitan_division"),
        file_format="json",
        series_ids=BLS_CES_LABOR_SERIES_IDS,
    )


def _install_session(monkeypatch, **kwargs):
    session = _Session(**kwargs)
    monkeypatch.setattr("src.ingestion.download.requests.Session", lambda: session)
    return session


def test_macro_download_batches_lifecycles_without_truncating_industry_history(
    tmp_path, monkeypatch
):
    session = _install_session(monkeypatch)
    output = tmp_path / "macro_bls.json"
    metadata = _download_bls(_source(), output, "1990-01-01", "2026-12-31")
    payload = json.loads(output.read_text(encoding="utf-8"))

    requests = [call["payload"] for call in session.calls]
    assert [(request["startyear"], request["endyear"]) for request in requests] == [
        ("1990", "1999"), ("2000", "2009"),
        ("2010", "2019"), ("2020", "2026"),
    ]
    assert len(requests[0]["seriesid"]) == 10
    assert set(requests[0]["seriesid"]) == EMPLOYMENT_SERIES_IDS
    for request in requests[1:]:
        assert len(request["seriesid"]) == 12
        assert set(request["seriesid"]) == set(BLS_CES_LABOR_SERIES_IDS)
    assert payload["requests"] == requests
    # Archive-level contract remains all 12, even though one request has 10.
    assert set(payload["series_ids"]) == set(BLS_CES_LABOR_SERIES_IDS)
    assert len(payload["series_ids"]) == 12
    assert set(metadata["request_series_ids"].split("|")) == set(BLS_CES_LABOR_SERIES_IDS)
    assert metadata["request_start_year"] == "1990"
    assert metadata["request_end_year"] == "2026"
    assert metadata["response_latest_observation"] == "2026-07-31"
    assert int(metadata["response_observation_count"]) == 46


@pytest.mark.parametrize("missing_series", [
    "SMU06000002000000001", "SMU06310845000000001",
])
def test_macro_lifecycle_exception_does_not_allow_missing_employment(
    tmp_path, monkeypatch, missing_series
):
    _install_session(monkeypatch, missing_series=missing_series)
    with pytest.raises(RuntimeError, match="missing configured series"):
        _download_bls(_source(), tmp_path / "missing.json", "1990-01-01", "2026-12-31")


def test_macro_requested_earnings_still_must_be_present_after_start(
    tmp_path, monkeypatch
):
    _install_session(monkeypatch, missing_series="SMU06310840500000003")
    with pytest.raises(RuntimeError, match="missing configured series"):
        _download_bls(_source(), tmp_path / "missing_ahe.json", "2007-01-01", "2016-12-31")


def test_macro_empty_employment_chunk_is_not_treated_as_lifecycle_exception(
    tmp_path, monkeypatch
):
    _install_session(monkeypatch, empty_series="SMU06000002000000001")
    with pytest.raises(RuntimeError, match="no monthly observations"):
        _download_bls(_source(), tmp_path / "empty.json", "1990-01-01", "1999-12-31")


def test_configured_macro_source_and_variables_match_published_contract():
    config = load_config(ROOT)
    matches = [source for source in config.sources if source.source_id == SOURCE_ID]
    assert len(matches) == 1
    source = matches[0]
    assert source.enabled and source.auto_download
    assert source.file_format == "json"
    assert source.native_frequency == "monthly"
    assert set(source.geography_levels) == {"state", "metropolitan_division"}
    assert set(source.series_ids) == set(BLS_CES_LABOR_SERIES_IDS)
    assert len(source.series_ids) == 12
    assert set(source.key_variables) == ALL_VARIABLE_IDS
    variables = {variable["variable_id"]: variable for variable in config.variables}
    assert ALL_VARIABLE_IDS <= set(variables)
    assert len(ALL_VARIABLE_IDS) == 12
    for variable_id in ALL_VARIABLE_IDS:
        variable = variables[variable_id]
        assert variable["native_frequency"] == "monthly"
        assert variable["final_frequency"] == "monthly"
        if variable_id.endswith("_yoy_pct"):
            assert variable["is_constructed"] is True
            assert variable["is_observed"] is False
            assert variable["unit"] == "percent"
        else:
            assert variable["is_observed"] is True
            assert variable["is_constructed"] is False
            assert variable["unit"] == (
                "USD/hour" if "hourly_earnings" in variable_id else "thousands_jobs"
            )
    geographies = {row["geo_id"]: row for row in config.geographies}
    assert geographies["METRODIV_31084"]["geo_type"] == "metropolitan_division"
    assert geographies["METRODIV_31084"]["state_fips"] == "06"
    assert geographies["METRODIV_31084"]["county_fips"] is None


def test_configured_source_download_to_cleaner_preserves_all_variables(
    tmp_path, monkeypatch
):
    source = load_config(ROOT).selected_sources(SOURCE_ID)[0]
    _install_session(monkeypatch)
    raw_path = tmp_path / "macro_source.json"
    _download_bls(source, raw_path, "1990-01-01", "2026-12-31")
    frame = clean_macro_labor(raw_path, {
        "source_id": SOURCE_ID,
        "data_vintage": "2026-09-15",
        "retrieved_at": "2026-09-15T12:00:00Z",
        "source_filename": raw_path.name,
        "checksum_sha256": "b" * 64,
        "provider_last_updated": "2026-08-21",
    }, tmp_path / "clean.parquet")
    assert set(frame.variable_id) == ALL_VARIABLE_IDS
    assert set(frame.geo_id) == {"STATE_06", "METRODIV_31084"}
    assert len(frame) == 46 * 2
    assert not frame.duplicated(["geo_id", "variable_id", "observation_date"]).any()
    assert frame.source_id.eq(SOURCE_ID).all()
    assert frame.checksum_sha256.eq("b" * 64).all()
