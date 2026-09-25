import json

import pytest

from src.common.config import SourceConfig
from src.geography.comparable_counties import (
    COMPARABLE_LAUS_SERIES_IDS,
    PRIMARY_LAUS_SERIES_IDS,
)
from src.ingestion.download import _download_bls


SERIES_IDS = PRIMARY_LAUS_SERIES_IDS


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class _Session:
    def __init__(self, body):
        self.body = body

    def post(self, *args, **kwargs):
        body = self.body(kwargs["json"]) if callable(self.body) else self.body
        return _Response(body)


def _source():
    return SourceConfig(
        source_id="bls_laus_la_county",
        provider="U.S. Bureau of Labor Statistics",
        dataset_name="LAUS",
        source_page_url="https://www.bls.gov/lau/",
        download_url="https://api.bls.gov/publicAPI/v2/timeseries/data/",
        native_frequency="monthly",
        geography_levels=("county",),
        file_format="json",
        series_ids=SERIES_IDS,
    )


def _series(series_ids=COMPARABLE_LAUS_SERIES_IDS):
    return [
        {
            "seriesID": series_id,
            "data": [
                {
                    "year": "2025",
                    "period": "M01",
                    "value": "5.2",
                    "footnotes": [{}],
                }
            ],
        }
        for series_id in series_ids
    ]


def test_bls_download_records_request_and_latest_observation(
    tmp_path, monkeypatch
):
    def body(request_payload):
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": _series(tuple(request_payload["seriesid"]))},
        }

    monkeypatch.setattr(
        "src.ingestion.download.requests.Session",
        lambda: _Session(body),
    )
    output = tmp_path / "bls.json"
    metadata = _download_bls(
        _source(), output, "2025-01-01", "2025-12-31"
    )
    payload = json.loads(output.read_text())

    assert payload["source_url"].startswith("https://api.bls.gov/")
    assert payload["series_ids"] == list(COMPARABLE_LAUS_SERIES_IDS)
    assert len(payload["requests"]) == 2
    assert max(len(request["seriesid"]) for request in payload["requests"]) == 25
    assert [
        series_id
        for request in payload["requests"]
        for series_id in request["seriesid"]
    ] == list(COMPARABLE_LAUS_SERIES_IDS)
    assert metadata["request_start_year"] == "2025"
    assert metadata["request_end_year"] == "2025"
    assert metadata["response_latest_observation"] == "2025-01-31"
    assert metadata["response_observation_count"] == "32"


def test_bls_download_rejects_missing_series(tmp_path, monkeypatch):
    body = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": _series(SERIES_IDS[:1])},
    }
    monkeypatch.setattr(
        "src.ingestion.download.requests.Session",
        lambda: _Session(body),
    )
    with pytest.raises(RuntimeError, match="missing configured series"):
        _download_bls(
            _source(),
            tmp_path / "bls.json",
            "2025-01-01",
            "2025-12-31",
        )


def test_bls_download_preserves_published_missing_values(
    tmp_path, monkeypatch
):
    def body(request_payload):
        series = _series(tuple(request_payload["seriesid"]))
        if request_payload["seriesid"][0] == COMPARABLE_LAUS_SERIES_IDS[0]:
            series[0]["data"].append(
                {
                    "year": "2025",
                    "period": "M10",
                    "value": "-",
                    "footnotes": [
                        {
                            "code": "X",
                            "text": "Data unavailable",
                        }
                    ],
                }
            )
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": series},
        }

    monkeypatch.setattr(
        "src.ingestion.download.requests.Session",
        lambda: _Session(body),
    )
    output = tmp_path / "bls.json"
    metadata = _download_bls(
        _source(), output, "2025-01-01", "2025-12-31"
    )
    payload = json.loads(output.read_text())

    assert payload["responses"][0]["Results"]["series"][0]["data"][1][
        "value"
    ] == "-"
    assert metadata["response_observation_count"] == "32"
