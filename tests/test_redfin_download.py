from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.cleaning.redfin_zip import REQUIRED_COLUMNS
from src.common.config import load_config
from src.common.io import sha256_file
from src.ingestion.redfin_download import (
    _browser_download, download_redfin_zip, validate_zip_download,
)


def _payload(path: Path, **changes) -> bytes:
    row = dict.fromkeys(REQUIRED_COLUMNS, "1")
    row.update({
        "LAST UPDATED": "2026-09-03", "FREQUENCY": "Rolling 3 Months",
        "PERIOD BEGIN": "2026-06-01", "PERIOD END": "2026-08-31",
        "REGION ID": "123", "REGION TYPE": "Zip", "REGION NAME": "00501",
        "METRO": "Test Metro",
    })
    row.update(changes)
    pd.DataFrame([row]).to_csv(path, index=False)
    return path.read_bytes()


@pytest.fixture
def project(tmp_path):
    config = replace(load_config(), root=tmp_path)
    source = next(s for s in config.sources if s.source_id == "redfin_zip_monthly")
    return config, source


def test_browser_payload_archived_unchanged_and_latest_requested(project):
    config, source = project
    received = []
    payloads = []

    def download(path, start, end, timeout):
        received.append((start, end, timeout))
        payloads.append(_payload(path))
        return {
            "original_filename": "official_redfin.csv",
            "selected_start_date": start,
            "selected_end_date": "2026-08-31",
            "request_query": "public download form",
        }

    record = download_redfin_zip(config, source, downloader=download)
    archived = config.root / record["relative_path"]
    assert archived.read_bytes() == payloads[0]
    assert record["checksum_sha256"] == sha256_file(archived)
    assert record["provider_last_updated"] == "2026-09-03"
    assert record["data_vintage"] == "2026-09-03"
    assert record["original_filename"] == "official_redfin.csv"
    assert record["retrieval_method"] == "official_browser_download"
    assert received == [("2019-01-01", None, 900)]
    again = download_redfin_zip(config, source, downloader=download)
    assert again["download_status"] == "reused_today"
    assert len(received) == 1
    forced = download_redfin_zip(config, source, force=True, downloader=download)
    assert forced["relative_path"] != record["relative_path"]
    assert archived.read_bytes() == payloads[0]
    assert pd.read_csv(config.root / "metadata/raw_file_manifest.csv").shape[0] == 2


@pytest.mark.parametrize("changes", [
    {"FREQUENCY": "Monthly"},
    {"REGION TYPE": "County"},
    {"REGION NAME": "501"},
    {"PERIOD BEGIN": "2026-08-01"},
    {"PERIOD END": "2026-08-30"},
    {"LAST UPDATED": "not-a-date"},
])
def test_wrong_product_or_window_is_rejected(tmp_path, changes):
    path = tmp_path / "download.csv"
    _payload(path, **changes)
    with pytest.raises(ValueError, match="failed validation"):
        validate_zip_download(path)


def test_failed_download_does_not_register_or_replace_raw(project):
    config, source = project
    existing = config.root / "data/raw/redfin/prior.csv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"immutable prior vintage")

    def download(path, start, end, timeout):
        _payload(path, **{"PERIOD END": "2026-07-31", "PERIOD BEGIN": "2026-05-01"})
        return {"selected_end_date": "2026-08-31"}

    with pytest.raises(ValueError, match="selected published end month"):
        download_redfin_zip(config, source, downloader=download)
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


def test_successor_days_change_never_enters_percent_variable(tmp_path):
    path = tmp_path / "successor.csv"
    _payload(path)
    frame = pd.read_csv(path, dtype=str).rename(columns={
        "MEDIAN DAYS ON MARKET YOY (%)": "MEDIAN DAYS ON MARKET YOY (DAYS)"
    })
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="from percent to days"):
        validate_zip_download(path)


def test_browser_denial_stops_without_retry_or_output(monkeypatch, tmp_path):
    events = []

    class Page:
        url = "https://ratelimited.redfin.com/"

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            events.append("request")
            return SimpleNamespace(status=429)

    class Browser:
        def new_page(self, **kwargs):
            return Page()

        def close(self):
            events.append("closed")

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: Browser()))

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", Playwright)
    path = tmp_path / "download.csv"
    with pytest.raises(RuntimeError, match="HTTP 429"):
        _browser_download(path, "2019-01-01", None, 30)
    assert events == ["request", "closed"]
    assert not path.exists()
