import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.redfin_zip import OFFICIAL_COLUMNS, REQUIRED_COLUMNS
from src.common.config import load_config
from src.common.io import sha256_file
from src.ingestion.redfin_download import (
    PUBLIC_BASE, PUBLIC_INDEX, RETRIEVAL_METHOD, _official_download,
    _published_zip_url, download_redfin_zip, validate_zip_download,
)

INDEX_BYTES = json.dumps({
    "housing_market": {"monthly": {"zip": {"all": "housing_market/monthly/all_zips.csv"}}}
}).encode()


def _payload(path: Path, *, columns=REQUIRED_COLUMNS, **changes) -> bytes:
    row = dict.fromkeys(columns, "1")
    row.update({
        "LAST UPDATED": "2026-09-03", "FREQUENCY": "Rolling 3 Months",
        "PERIOD BEGIN": "2026-06-01", "PERIOD END": "2026-08-31",
        "REGION ID": "123", "REGION TYPE": "Zip", "REGION NAME": "00501",
        "METRO": "Test Metro",
    })
    row.update(changes)
    pd.DataFrame([row], columns=columns).to_csv(path, index=False)
    return path.read_bytes()


def _details():
    return {
        "original_filename": "all_zips.csv",
        "download_url": PUBLIC_BASE + "housing_market/monthly/all_zips.csv",
        "source_index_url": PUBLIC_INDEX,
        "_index_bytes": INDEX_BYTES,
    }


def _download(path, start, end, timeout):
    _payload(path, columns=OFFICIAL_COLUMNS)
    return _details()


@pytest.fixture
def project(tmp_path):
    config = replace(load_config(), root=tmp_path)
    source = next(s for s in config.sources if s.source_id == "redfin_zip_monthly")
    return config, source


def test_national_payload_and_index_archived_unchanged(project):
    config, source = project
    received, payloads = [], []

    def download(path, start, end, timeout):
        received.append((start, end, timeout))
        payloads.append(_payload(path, columns=OFFICIAL_COLUMNS))
        return _details()

    record = download_redfin_zip(config, source, downloader=download)
    archived = config.root / record["relative_path"]
    index = config.root / record["source_index_relative_path"]
    assert archived.read_bytes() == payloads[0]
    assert index.read_bytes() == INDEX_BYTES
    assert record["checksum_sha256"] == sha256_file(archived)
    assert record["source_index_checksum_sha256"] == sha256_file(index)
    assert record["provider_last_updated"] == record["data_vintage"] == "2026-09-03"
    assert record["original_filename"] == "all_zips.csv"
    assert record["retrieval_method"] == RETRIEVAL_METHOD
    assert record["source_schema_version"] == "official_all_metrics_v3"
    assert record["selected_end_date"] == "2026-08-31"
    assert received == [("2019-01-01", None, 900)]
    again = download_redfin_zip(config, source, downloader=download)
    assert again["download_status"] == "reused_today"
    assert len(received) == 1
    forced = download_redfin_zip(config, source, force=True, downloader=download)
    assert forced["relative_path"] != record["relative_path"]
    assert archived.read_bytes() == payloads[0]
    assert index.read_bytes() == INDEX_BYTES
    assert pd.read_csv(config.root / "metadata/raw_file_manifest.csv").shape[0] == 2


def test_requested_subset_does_not_change_national_raw(project):
    config, source = project
    payloads = []

    def download(path, *args):
        _payload(path, columns=OFFICIAL_COLUMNS)
        frame = pd.read_csv(path, dtype=str)
        prior = frame.assign(**{"PERIOD BEGIN": "2026-05-01", "PERIOD END": "2026-07-31"})
        pd.concat([prior, frame]).to_csv(path, index=False)
        payloads.append(path.read_bytes())
        return _details()

    record = download_redfin_zip(config, source, end_date="2026-07-31", downloader=download)
    assert record["selected_end_date"] == "2026-07-31"
    assert record["response_latest_observation"] == "2026-08-31"
    assert (config.root / record["relative_path"]).read_bytes() == payloads[0]


@pytest.mark.parametrize("changes", [
    {"FREQUENCY": "Monthly"}, {"REGION TYPE": "County"}, {"REGION NAME": "501"},
    {"PERIOD BEGIN": "2026-08-01"}, {"PERIOD END": "2026-08-30"},
    {"LAST UPDATED": "not-a-date"}, {"LAST UPDATED": "2026-08-01"},
])
def test_wrong_product_or_window_is_rejected(tmp_path, changes):
    path = tmp_path / "download.csv"
    _payload(path, **changes)
    with pytest.raises(ValueError, match="failed validation"):
        validate_zip_download(path)


def test_unavailable_requested_end_does_not_register_or_replace_raw(project):
    config, source = project
    existing = config.root / "data/raw/redfin/prior.csv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"immutable prior vintage")
    with pytest.raises(ValueError, match="requested date range"):
        download_redfin_zip(config, source, end_date="2026-09-30", downloader=_download)
    assert existing.read_bytes() == b"immutable prior vintage"
    assert not (config.root / "metadata/raw_file_manifest.csv").exists()
    assert list(existing.parent.iterdir()) == [existing]


def test_duplicate_zip_periods_rejected(tmp_path):
    path = tmp_path / "download.csv"
    _payload(path)
    frame = pd.read_csv(path, dtype=str)
    pd.concat([frame, frame]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate_zip_periods=1"):
        validate_zip_download(path)


def test_unreviewed_schema_rejected(tmp_path):
    path = tmp_path / "successor.csv"
    _payload(path, columns=OFFICIAL_COLUMNS)
    frame = pd.read_csv(path, dtype=str).rename(columns={
        "MEDIAN DAYS ON MARKET YOY (DAYS)": "MEDIAN DAYS ON MARKET YOY (WEEKS)"
    })
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="schema changed"):
        validate_zip_download(path)


class Response:
    def __init__(self, body, *, status=200, headers=None):
        self.body, self.status_code = body, status
        self.headers = {
            "Content-Length": str(len(body)), "Last-Modified": "Mon, 21 Sep 2026 18:23:22 GMT",
            "ETag": '"provider-etag"', "Content-Type": "binary/octet-stream", **(headers or {}),
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.body


def _session(monkeypatch, responses):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

    monkeypatch.setattr("src.ingestion.redfin_download.requests.Session", Session)
    return calls


def test_direct_download_uses_published_index_and_preserves_http_metadata(monkeypatch, tmp_path):
    payload = _payload(tmp_path / "fixture.csv", columns=OFFICIAL_COLUMNS)
    calls = _session(monkeypatch, [Response(INDEX_BYTES), Response(payload)])
    path = tmp_path / "download.csv"
    details = _official_download(path, "2019-01-01", None, 30)
    assert path.read_bytes() == payload
    assert details.pop("_index_bytes") == INDEX_BYTES
    assert details["http_content_length"] == len(payload)
    assert details["http_etag"] == details["index_http_etag"] == '"provider-etag"'
    assert [url for url, kwargs in calls] == [
        PUBLIC_INDEX, PUBLIC_BASE + "housing_market/monthly/all_zips.csv"
    ]
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)
    assert all(kwargs["headers"] == {"Accept-Encoding": "identity"} for _, kwargs in calls)


@pytest.mark.parametrize("path", [
    "https://unrelated.example/data.csv", "//unrelated.example/data.csv",
    "housing_market/monthly/../../other.csv", "housing_market/monthly/%2e%2e/data.csv",
    "housing_market/monthly/data.csv?redirect=elsewhere", "other/monthly/all_zips.csv",
])
def test_index_cannot_redirect_acquisition_outside_official_scope(path):
    index = {"housing_market": {"monthly": {"zip": {"all": path}}}}
    with pytest.raises(ValueError, match="outside the approved"):
        _published_zip_url(index)


@pytest.mark.parametrize("response", [
    Response(b"denied", status=429), Response(b"redirect", status=302),
    Response(b"short", headers={"Content-Length": "100"}),
    Response(b"body", headers={"Content-Encoding": "gzip"}),
])
def test_http_failure_leaves_existing_archives_and_manifest_untouched(project, monkeypatch, response):
    config, source = project
    raw = config.root / "data/raw/redfin"
    raw.mkdir(parents=True)
    prior = raw / "prior.csv"
    prior.write_bytes(b"immutable")
    calls = _session(monkeypatch, [Response(INDEX_BYTES), response])
    with pytest.raises((RuntimeError, ValueError)):
        download_redfin_zip(config, source)
    assert len(calls) == 2
    assert prior.read_bytes() == b"immutable"
    assert list(raw.iterdir()) == [prior]
    assert not (config.root / "metadata/raw_file_manifest.csv").exists()


def test_cache_requires_unchanged_index_as_well_as_csv(project):
    config, source = project
    record = download_redfin_zip(config, source, downloader=_download)
    index_path = config.root / record["source_index_relative_path"]
    # Simulate external corruption; downloader must refuse to reuse the broken cache.
    index_path.write_bytes(b"corrupted")
    fresh = download_redfin_zip(config, source, downloader=_download)
    assert fresh["relative_path"] != record["relative_path"]
    assert index_path.read_bytes() == b"corrupted"


def test_filename_collision_never_replaces_an_archive(project, monkeypatch):
    config, source = project

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 25, tzinfo=timezone.utc)

    monkeypatch.setattr("src.ingestion.redfin_download.datetime", Clock)
    record = download_redfin_zip(config, source, downloader=_download)
    original = config.root / record["relative_path"]
    before = original.read_bytes()
    with pytest.raises(FileExistsError, match="replace an existing"):
        download_redfin_zip(config, source, force=True, downloader=_download)
    assert original.read_bytes() == before
    assert pd.read_csv(config.root / "metadata/raw_file_manifest.csv").shape[0] == 1


@pytest.mark.parametrize("value", ["unparseable", "NaN", "inf", "-inf"])
def test_malformed_numeric_observations_are_not_silently_missing(tmp_path, value):
    path = tmp_path / "malformed.csv"
    _payload(path, columns=OFFICIAL_COLUMNS, **{"HOMES SOLD": value})
    with pytest.raises(ValueError, match="invalid_numeric_rows=1"):
        validate_zip_download(path)


@pytest.mark.parametrize("value", ["NA", "", "   "])
def test_provider_missing_numeric_observations_are_allowed(tmp_path, value):
    path = tmp_path / "missing.csv"
    _payload(path, columns=OFFICIAL_COLUMNS, **{"HOMES SOLD": value})
    assert validate_zip_download(path)["response_observation_count"] == 1
