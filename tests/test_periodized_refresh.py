from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import sha256_file
from src.ingestion import bps_annual, bps_archive, hud_usps, irs_migration


class Response:
    def __init__(self, text: str = "", payload: dict | None = None):
        self.text = text
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def source(source_id: str) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        provider="U.S. Census Bureau",
        dataset_name="Test published source",
        source_page_url="https://www2.census.gov/econ/bps/County/",
        download_url="https://www2.census.gov/econ/bps/County/co2601c.txt",
        native_frequency="monthly",
        geography_levels=("county",),
        file_format="csv",
        selected_start_date="2026-01-01",
        selected_end_date="2026-03-31",
    )


def config(root: Path, item: SourceConfig) -> ProjectConfig:
    return ProjectConfig(
        root=root, verified_at="2026-09-25", sources=(item,),
        pipeline={"census_bps": {"archive_start_date": "2026-01-01", "download_workers": 1}},
        variables=(), geographies=(), crosswalk_version="test",
    )


def test_irs_discovers_only_provider_linked_new_years(monkeypatch):
    index = '<a href="/statistics/soi-tax-stats-migration-data-2023-2024">release</a>'
    release = ''.join(
        f'<a href="https://www.irs.gov/pub/irs-soi/county{direction}2324.csv">CSV</a>'
        for direction in ("inflow", "outflow")
    )
    monkeypatch.setattr(
        irs_migration.requests, "get",
        lambda url, **kwargs: Response(index if url == irs_migration.MIGRATION_PAGE else release),
    )
    targets = irs_migration._published_targets("2024-01-01", None)
    assert {row["archive_period"] for row in targets} == {"2023-2024"}
    assert {row["migration_direction"] for row in targets} == {"inflow", "outflow"}
    assert all(row["download_url"].endswith("2324.csv") for row in targets)


def test_irs_discovery_rejects_missing_direction_in_published_release(monkeypatch):
    monkeypatch.setattr(
        irs_migration.requests, "get",
        lambda url, **kwargs: Response(
            '<a href="/statistics/soi-tax-stats-migration-data-2023-2024">release</a>'
            if url == irs_migration.MIGRATION_PAGE else
            '<a href="https://www.irs.gov/pub/irs-soi/countyinflow2324.csv">CSV</a>'
        ),
    )
    with pytest.raises(RuntimeError, match="outflow CSV link"):
        irs_migration._published_targets("2024-01-01", None)


def test_irs_explicit_historical_end_is_respected(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return Response('<a href="/statistics/soi-tax-stats-migration-data-2022-2023">release</a>')
    monkeypatch.setattr(irs_migration.requests, "get", get)
    targets = irs_migration._published_targets("2021-01-01", "2022-12-31")
    assert {row["archive_period"] for row in targets} == {"2020-2021", "2021-2022"}
    assert calls == [irs_migration.MIGRATION_PAGE]


def test_irs_does_not_reuse_archive_after_bytes_changed(tmp_path):
    item = source("irs_soi_county_migration")
    raw = tmp_path / "archive.csv"
    raw.write_text("original", encoding="utf-8")
    record = {
        "source_id": item.source_id, "relative_path": raw.name,
        "retrieved_at": "2026-09-25T00:00:00Z", "archive_period": "2022-2023",
        "migration_direction": "inflow", "checksum_sha256": sha256_file(raw),
    }
    (tmp_path / "metadata").mkdir()
    pd.DataFrame([record]).to_csv(tmp_path / "metadata/raw_file_manifest.csv", index=False)
    raw.write_text("changed", encoding="utf-8")
    assert irs_migration._existing_files(config(tmp_path, item), item.source_id).empty


def test_irs_partial_year_failure_blocks_even_with_other_success(tmp_path, monkeypatch):
    item = replace(source("irs_soi_county_migration"), selected_start_date="2023-01-01", selected_end_date="2023-12-31")
    monkeypatch.setattr(irs_migration, "append_csv", lambda *args: None)
    def download(_config, _source, target, _raw_dir):
        ok = target["migration_direction"] == "inflow"
        return ({"source_id": item.source_id} if ok else None), {
            **target, "download_status": "downloaded" if ok else "failed",
            "detail": "HTTP 503" if not ok else "",
        }
    monkeypatch.setattr(irs_migration, "_download_one", download)
    with pytest.raises(RuntimeError, match="archive is incomplete"):
        irs_migration.download_irs_soi_county_migration(
            config(tmp_path, item), item, force=False, start_date=None, end_date=None,
        )
    report = pd.read_csv(tmp_path / "output/coverage/irs_soi_migration_download.csv")
    assert set(report.download_status) == {"downloaded", "failed"}


def test_annual_bps_discovers_latest_and_rechecks_latest_file(tmp_path, monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            assert url == bps_annual.SOURCE_PAGE_URL
            return Response('<a href="co2024a.txt">2024</a><a href="co2025a.txt">2025</a><a href="co2501c.txt">monthly</a>')
    calls = []
    def download(root, year, **kwargs):
        calls.append((year, kwargs["force"]))
        return {"survey_year": str(year)}
    monkeypatch.setattr(bps_annual, "download_annual_county_file", download)
    result = bps_annual.download_bps_annual_county(
        tmp_path, start_year=2024, end_year=None, session=Session(), refresh_latest=True,
    )
    assert calls == [(2024, False), (2025, True)]
    assert [row["survey_year"] for row in result] == ["2024", "2025"]


def test_annual_bps_discovery_rejects_missing_historical_link(tmp_path, monkeypatch):
    monkeypatch.setattr(bps_annual, "_published_annual_years", lambda session: {2023, 2025})
    with pytest.raises(bps_annual.BPSAnnualDownloadError, match="2024"):
        bps_annual.download_bps_annual_county(
            tmp_path, start_year=2023, end_year=None, refresh_latest=True,
        )


def test_hud_latest_query_uses_documented_default_year_and_month(monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            assert url == hud_usps.NCWM_API
            assert kwargs["params"] == {"type": 1, "stateid": "06"}
            return Response(payload={"year": "2026", "month": 3, "results": [{"fixture": True}]})
    monkeypatch.setattr(hud_usps, "_session", lambda token: Session())
    assert hud_usps._latest_published_quarter("test-token") == pd.Period("2026Q1")


def test_hud_new_release_blocks_without_changing_crosswalk_or_archive(tmp_path, monkeypatch):
    item = source("hud_usps_ncwm_quarterly")
    monkeypatch.setenv("HUDUSER_API_TOKEN", "test-token")
    monkeypatch.setattr(hud_usps, "_latest_published_quarter", lambda token: pd.Period("2026Q2"))
    with pytest.raises(RuntimeError, match="fixed ZIP crosswalk"):
        hud_usps.download_hud_usps_archive(
            config(tmp_path, item), item, force=False, start_date=None, end_date=None, refresh_latest=True,
        )
    assert not (tmp_path / "data").exists()


def test_hud_requires_token_for_freshness_check_even_with_cached_archive(tmp_path, monkeypatch):
    item = source("hud_usps_ncwm_quarterly")
    monkeypatch.delenv("HUDUSER_API_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="cached archive alone"):
        hud_usps.download_hud_usps_archive(
            config(tmp_path, item), item, force=False, start_date=None, end_date=None, refresh_latest=True,
        )


def test_monthly_bps_discovers_current_files_only(monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            return Response('<a href="co2601c.txt">Jan</a><a href="co2602c.txt">Feb</a><a href="co2603y.txt">YTD</a><a href="co2025a.txt">annual</a>')
    monkeypatch.setattr(bps_archive, "_session", lambda: Session())
    assert bps_archive._latest_published_period(source("census_bps_county")) == pd.Period("2026-02", freq="M")


@pytest.mark.parametrize("failed_status", ["failed", "not_published", "rejected_response"])
def test_monthly_bps_partial_acquisition_never_reports_refresh_success(tmp_path, monkeypatch, failed_status):
    item = source("census_bps_county")
    monkeypatch.setattr(bps_archive, "_latest_published_period", lambda source: pd.Period("2026-02", freq="M"))
    monkeypatch.setattr(bps_archive, "_recover_unmanifested_files", lambda *args: 0)
    monkeypatch.setattr(bps_archive, "append_csv", lambda *args: None)
    def download(_config, _source, target, _raw_dir):
        ok = target["archive_period"] == "2026-01"
        return ({"source_id": item.source_id} if ok else None), {
            **target, "download_status": "downloaded" if ok else failed_status,
        }
    monkeypatch.setattr(bps_archive, "_download_one", download)
    (tmp_path / "output/coverage").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Required published BPS periods"):
        bps_archive.download_bps_archive(
            config(tmp_path, item), item, force=False, start_date=None, end_date=None, refresh_latest=True,
        )
    report = pd.read_csv(tmp_path / "output/coverage/bps_archive_download.csv")
    assert set(report.download_status) == {"downloaded", failed_status}
