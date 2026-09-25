from __future__ import annotations

import hashlib
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.common.config import ProjectConfig, SourceConfig
from src.ingestion.download import download_sources
from src.ingestion.refresh import (
    SourceRefreshBlocked,
    refresh_latest_sources,
    verify_refreshed_clean_outputs,
)
from src.pipeline import _run_stage


def _source(source_id: str, *, auto_download: bool = True) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        provider="Official Provider",
        dataset_name=f"Dataset {source_id}",
        source_page_url="https://example.gov/source",
        download_url="https://example.gov/data.csv",
        native_frequency="monthly",
        geography_levels=("county",),
        file_format="csv",
        raw_subdir="test",
        auto_download=auto_download,
        manual_reason=("Export from the official provider UI." if not auto_download else ""),
    )


def _config(root: Path, *sources: SourceConfig) -> ProjectConfig:
    return ProjectConfig(
        root=root,
        verified_at="2026-08-24",
        sources=tuple(sources),
        pipeline={},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def _archive(root: Path, source_id: str, *, retrieved_at: str) -> dict[str, str]:
    raw_dir = root / "data" / "raw" / "test"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{source_id}_{retrieved_at[:10]}.csv"
    payload = f"source_id,value\n{source_id},1\n".encode()
    path.write_bytes(payload)
    record = {
        "source_id": source_id,
        "retrieved_at": retrieved_at,
        "data_vintage": retrieved_at[:10],
        "relative_path": path.relative_to(root).as_posix(),
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "response_latest_observation": "2026-07-31",
    }
    manifest = root / "metadata" / "raw_file_manifest.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([record]).to_csv(manifest, index=False)
    return record


def _clean(root: Path, source_id: str, dates: list[str]) -> Path:
    path = root / "data" / "clean" / f"{source_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "observation_date": pd.to_datetime(dates),
            "source_id": source_id,
            "value": range(len(dates)),
        }
    ).to_parquet(path, index=False)
    return path


def test_refresh_blocks_automatic_download_failure_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)

    def failed_download(*args, **kwargs):
        return [], [{"source_id": source.source_id, "reason": "HTTP 503"}]

    monkeypatch.setattr("src.ingestion.refresh.download_sources", failed_download)

    with pytest.raises(SourceRefreshBlocked, match="automatic_source"):
        refresh_latest_sources(
            config,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "refresh_status"] == "automatic_refresh_failed"
    assert bool(report.loc[0, "is_blocking"])
    assert report.loc[0, "detail"] == "HTTP 503"


def test_refresh_keeps_manual_source_visible_when_valid_archive_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("manual_source", auto_download=False)
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )

    def manual_download(*args, **kwargs):
        return [], [{"source_id": source.source_id, "reason": "manual export"}]

    monkeypatch.setattr("src.ingestion.refresh.download_sources", manual_download)

    _, issues, report = refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert len(issues) == 1
    assert report.loc[0, "refresh_status"] == (
        "manual_check_required_existing_archive_reused"
    )
    assert not bool(report.loc[0, "is_blocking"])
    assert bool(report.loc[0, "archive_verified"])
    assert "Check the provider" in report.loc[0, "action_required"]


def test_refresh_reports_a_new_checksum_verified_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )

    def successful_download(*args, **kwargs):
        record = _archive(
            tmp_path,
            source.source_id,
            retrieved_at="2026-08-24T12:00:00+00:00",
        )
        return [record], []

    monkeypatch.setattr("src.ingestion.refresh.download_sources", successful_download)

    _, _, report = refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
    )

    assert report.loc[0, "refresh_status"] == "new_archive_acquired"
    assert not bool(report.loc[0, "is_blocking"])
    assert bool(report.loc[0, "archive_verified"])
    assert report.loc[0, "latest_observation_or_archive_period"] == "2026-07-31"


def test_strict_clean_verification_blocks_endpoint_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30"])

    monkeypatch.setattr(
        "src.ingestion.refresh.download_sources",
        lambda *args, **kwargs: ([], []),
    )
    refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    _clean(tmp_path, source.source_id, ["2026-05-31"])

    with pytest.raises(SourceRefreshBlocked, match="automatic_source"):
        verify_refreshed_clean_outputs(config)

    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "clean_refresh_status"] == (
        "endpoint_regression_or_truncation_blocked"
    )
    assert bool(report.loc[0, "is_blocking"])
    assert "row count regressed from 2 to 1" in report.loc[
        0, "clean_refresh_detail"
    ]
    assert "latest observation regressed" in report.loc[
        0, "clean_refresh_detail"
    ]


def test_strict_clean_verification_blocks_left_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )
    _clean(
        tmp_path,
        source.source_id,
        ["2026-04-30", "2026-05-31", "2026-06-30"],
    )
    monkeypatch.setattr(
        "src.ingestion.refresh.download_sources",
        lambda *args, **kwargs: ([], []),
    )
    refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30"])

    with pytest.raises(SourceRefreshBlocked, match="automatic_source"):
        verify_refreshed_clean_outputs(config)

    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert "earliest observation advanced" in report.loc[
        0, "clean_refresh_detail"
    ]


def test_strict_clean_verification_accepts_an_appended_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30"])
    monkeypatch.setattr(
        "src.ingestion.refresh.download_sources",
        lambda *args, **kwargs: ([], []),
    )
    refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    _clean(
        tmp_path,
        source.source_id,
        ["2026-05-31", "2026-06-30", "2026-07-31"],
    )

    report = verify_refreshed_clean_outputs(config)

    assert report.loc[0, "clean_refresh_status"] == (
        "verified_no_endpoint_regression"
    )
    assert report.loc[0, "refreshed_clean_row_count"] == "3"
    assert report.loc[0, "refreshed_clean_latest_observation"].startswith(
        "2026-07-31"
    )
    baseline = pd.read_csv(
        tmp_path / "metadata" / "source_clean_coverage_baseline.csv"
    )
    assert baseline.loc[0, "row_count"] == 3
    assert str(baseline.loc[0, "latest_observation"]).startswith("2026-07-31")


def test_failed_regression_does_not_replace_last_accepted_clean_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    _archive(
        tmp_path,
        source.source_id,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "src.ingestion.refresh.download_sources",
        lambda *args, **kwargs: ([], []),
    )
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30"])
    refresh_latest_sources(
        config,
        now=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    verify_refreshed_clean_outputs(config)

    _clean(tmp_path, source.source_id, ["2026-05-31"])
    refresh_latest_sources(
        config,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    with pytest.raises(SourceRefreshBlocked):
        verify_refreshed_clean_outputs(config)

    baseline = pd.read_csv(
        tmp_path / "metadata" / "source_clean_coverage_baseline.csv"
    )
    assert baseline.loc[0, "row_count"] == 2
    assert str(baseline.loc[0, "latest_observation"]).startswith("2026-06-30")


def test_explicit_refresh_download_failure_is_returned_for_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("automatic_source")
    config = _config(tmp_path, source)
    (tmp_path / "metadata").mkdir()

    def fail_source(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("src.ingestion.download.download_source", fail_source)

    completed, issues = download_sources(
        config,
        source.source_id,
        False,
        False,
        None,
        None,
        refresh_latest=True,
    )

    assert completed == []
    assert [row["source_id"] for row in issues] == [source.source_id]
    assert "provider unavailable" in issues[0]["reason"]


def test_clean_endpoint_guard_is_refresh_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, _source("automatic_source"))
    calls: list[object] = []
    arguments = Namespace(source=None, vintage=None)
    monkeypatch.setattr("src.pipeline.load_config", lambda: config)
    monkeypatch.setattr(
        "src.pipeline.clean_sources",
        lambda *args, **kwargs: calls.append(("clean", kwargs.get("strict"))),
    )
    monkeypatch.setattr(
        "src.pipeline.verify_refreshed_clean_outputs",
        lambda *args, **kwargs: calls.append("verify"),
    )

    _run_stage("clean", arguments, refresh_latest=False)
    assert calls == [("clean", False)]

    calls.clear()
    _run_stage("clean", arguments, refresh_latest=True)
    assert calls == [("clean", True), "verify"]


def test_provider_review_hold_blocks_successful_download_and_keeps_archive_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("held_provider")
    config = _config(tmp_path, source)
    reason = "Review the provider's new release route; the legacy endpoint is stale."
    config.pipeline["source_refresh_review_holds"] = {source.source_id: reason}
    prior = _archive(tmp_path, source.source_id, retrieved_at="2026-08-01T00:00:00+00:00")
    downloaded: list[dict] = []

    def successful_download(*args, **kwargs):
        record = _archive(tmp_path, source.source_id, retrieved_at="2026-09-22T12:00:00+00:00")
        downloaded.append(record)
        return [record], []

    monkeypatch.setattr("src.ingestion.refresh.download_sources", successful_download)
    with pytest.raises(SourceRefreshBlocked, match="held_provider"):
        refresh_latest_sources(config)

    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "refresh_status"] == "provider_release_review_required"
    assert bool(report.loc[0, "is_blocking"])
    assert report.loc[0, "action_required"] == reason
    assert bool(report.loc[0, "archive_verified"])
    assert report.loc[0, "prior_retrieved_at"] == prior["retrieved_at"]
    assert report.loc[0, "latest_retrieved_at"] == downloaded[0]["retrieved_at"]
    assert report.loc[0, "latest_checksum_sha256"] == downloaded[0]["checksum_sha256"]
    assert report.loc[0, "latest_raw_path"] == downloaded[0]["relative_path"]
    assert report.loc[0, "latest_observation_or_archive_period"] == "2026-07-31"
    assert (tmp_path / prior["relative_path"]).is_file()
    assert (tmp_path / downloaded[0]["relative_path"]).is_file()


def test_redfin_group_keeps_county_review_hold_after_both_downloads_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    county = _source("redfin_county_monthly")
    zipcode = _source("redfin_zip_monthly")
    config = _config(tmp_path, county, zipcode, _source("unrelated_source"))
    config.pipeline.update({
        "source_groups": {"redfin": [county.source_id, zipcode.source_id]},
        "source_refresh_review_holds": {county.source_id: "County successor definition needs review."},
    })
    acquired = []

    def download_group(actual_config, source_filter, force, offline, *args, **kwargs):
        assert source_filter == "redfin"
        assert force is True and offline is False and kwargs["refresh_latest"] is True
        for source in actual_config.selected_sources(source_filter):
            acquired.append(_archive(
                tmp_path, source.source_id, retrieved_at="2026-09-25T12:00:00+00:00"
            ))
        pd.DataFrame(acquired).to_csv(tmp_path / "metadata/raw_file_manifest.csv", index=False)
        return acquired, []

    monkeypatch.setattr("src.ingestion.refresh.download_sources", download_group)
    with pytest.raises(SourceRefreshBlocked, match="redfin_county_monthly"):
        refresh_latest_sources(config, source_filter="redfin")
    assert [record["source_id"] for record in acquired] == [county.source_id, zipcode.source_id]
    report = pd.read_csv(tmp_path / "metadata/source_refresh_status.csv").set_index("source_id")
    assert set(report.index) == {county.source_id, zipcode.source_id}
    assert report.loc[county.source_id, "refresh_status"] == "provider_release_review_required"
    assert bool(report.loc[county.source_id, "is_blocking"])
    assert not bool(report.loc[zipcode.source_id, "is_blocking"])
    assert report["archive_verified"].all()


def test_group_refresh_runs_shared_pipeline_stages_once(tmp_path, monkeypatch):
    from src import pipeline

    config = _config(tmp_path, _source("redfin_county_monthly"), _source("redfin_zip_monthly"), _source("unrelated"))
    config.pipeline["source_groups"] = {"redfin": ["redfin_county_monthly", "redfin_zip_monthly"]}
    monkeypatch.setattr(pipeline, "load_config", lambda: config)
    calls = []

    def record_stage(stage, arguments, *, refresh_latest=False):
        assert refresh_latest is True
        assert arguments.source == "redfin"
        calls.append(stage)

    monkeypatch.setattr(pipeline, "_run_stage", record_stage)
    assert pipeline.main(["--stage", "refresh", "--source", "redfin"]) == 0
    assert calls == list(pipeline.PIPELINE_STAGES)
    assert calls.count("harmonize") == calls.count("build-database") == 1
    assert calls.count("validate") == calls.count("export") == 1


def test_clean_verification_cannot_clear_provider_hold_or_accept_coverage_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("held_provider")
    config = _config(tmp_path, source)
    _archive(tmp_path, source.source_id, retrieved_at="2026-08-01T00:00:00+00:00")
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30"])
    monkeypatch.setattr("src.ingestion.refresh.download_sources", lambda *args, **kwargs: ([], []))
    refresh_latest_sources(config)
    verify_refreshed_clean_outputs(config)
    baseline_path = tmp_path / "metadata" / "source_clean_coverage_baseline.csv"
    accepted_baseline = baseline_path.read_bytes()

    # A newly configured hold applies even to the earlier successful report.
    reason = "Verify which public download reflects the current provider release."
    config.pipeline["source_refresh_review_holds"] = {source.source_id: reason}
    _clean(tmp_path, source.source_id, ["2026-05-31", "2026-06-30", "2026-07-31"])
    with pytest.raises(SourceRefreshBlocked, match="held_provider"):
        verify_refreshed_clean_outputs(config)
    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "refresh_status"] == "provider_release_review_required"
    assert report.loc[0, "action_required"] == reason
    assert report.loc[0, "clean_refresh_status"] == "blocked_by_acquisition_or_provider_review"
    assert report.loc[0, "refreshed_clean_row_count"] == 3
    assert baseline_path.read_bytes() == accepted_baseline

    # Merely deleting the config hold cannot let clean-only verification certify
    # an acquisition whose report still explicitly requires provider review.
    config.pipeline["source_refresh_review_holds"] = {}
    with pytest.raises(SourceRefreshBlocked, match="held_provider"):
        verify_refreshed_clean_outputs(config)
    assert baseline_path.read_bytes() == accepted_baseline

    # Resolution is an explicit config change plus a successful refresh rerun.
    refresh_latest_sources(config)
    report = verify_refreshed_clean_outputs(config)
    assert str(report.loc[0, "is_blocking"]).lower() == "false"
    assert pd.read_csv(baseline_path).loc[0, "row_count"] == 3


def test_provider_hold_does_not_hide_download_failure_and_clean_cannot_accept_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("held_provider")
    config = _config(tmp_path, source)
    config.pipeline["source_refresh_review_holds"] = {source.source_id: "Review current releases."}
    archived = _archive(tmp_path, source.source_id, retrieved_at="2026-08-01T00:00:00+00:00")
    _clean(tmp_path, source.source_id, ["2026-07-31"])
    monkeypatch.setattr(
        "src.ingestion.refresh.download_sources",
        lambda *args, **kwargs: ([], [{"source_id": source.source_id, "reason": "HTTP 503"}]),
    )
    with pytest.raises(SourceRefreshBlocked):
        refresh_latest_sources(config)
    with pytest.raises(SourceRefreshBlocked):
        verify_refreshed_clean_outputs(config)

    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "refresh_status"] == "automatic_refresh_failed"
    assert report.loc[0, "action_required"] == "HTTP 503"
    assert report.loc[0, "detail"] == "HTTP 503"
    assert report.loc[0, "latest_raw_path"] == archived["relative_path"]
    assert not (tmp_path / "metadata" / "source_clean_coverage_baseline.csv").exists()


def test_provider_hold_does_not_hide_archive_checksum_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source("held_provider")
    config = _config(tmp_path, source)
    config.pipeline["source_refresh_review_holds"] = {source.source_id: "Review current releases."}
    record = _archive(tmp_path, source.source_id, retrieved_at="2026-08-01T00:00:00+00:00")
    record["checksum_sha256"] = "0" * 64
    pd.DataFrame([record]).to_csv(tmp_path / "metadata" / "raw_file_manifest.csv", index=False)
    monkeypatch.setattr("src.ingestion.refresh.download_sources", lambda *args, **kwargs: ([], []))
    with pytest.raises(SourceRefreshBlocked):
        refresh_latest_sources(config)
    report = pd.read_csv(tmp_path / "metadata" / "source_refresh_status.csv")
    assert report.loc[0, "refresh_status"] == "archive_missing_or_checksum_failed"
    assert not bool(report.loc[0, "archive_verified"])


def test_provider_hold_does_not_change_unrelated_source_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, unaffected = _source("held_provider"), _source("unaffected_provider")
    config = _config(tmp_path, held, unaffected)
    config.pipeline["source_refresh_review_holds"] = {held.source_id: "Review current releases."}

    def successful_download(*args, **kwargs):
        assert args[1] == unaffected.source_id
        record = _archive(tmp_path, unaffected.source_id, retrieved_at="2026-09-22T12:00:00+00:00")
        return [record], []

    monkeypatch.setattr("src.ingestion.refresh.download_sources", successful_download)
    _, _, report = refresh_latest_sources(config, source_filter=unaffected.source_id)
    assert report.source_id.tolist() == [unaffected.source_id]
    assert report.loc[0, "refresh_status"] == "new_archive_acquired"
    assert not bool(report.loc[0, "is_blocking"])


@pytest.mark.parametrize("invalid_hold", [[], {"held_provider": " "}, {"unknown_provider": "Review"}])
def test_provider_hold_configuration_fails_before_download_when_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_hold
) -> None:
    config = _config(tmp_path, _source("held_provider"))
    config.pipeline["source_refresh_review_holds"] = invalid_hold

    def unexpected_download(*args, **kwargs):
        pytest.fail("Malformed hold must be rejected before acquisition")

    monkeypatch.setattr("src.ingestion.refresh.download_sources", unexpected_download)
    with pytest.raises(ValueError):
        refresh_latest_sources(config)


@pytest.mark.parametrize("stage,function", [
    ("harmonize", "build_analytical_outputs"),
    ("build-database", "build_database"),
    ("validate", "run_qa"),
])
def test_provider_review_hold_does_not_disable_offline_build_or_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, function: str
) -> None:
    source = _source("held_provider")
    config = _config(tmp_path, source)
    config.pipeline["source_refresh_review_holds"] = {source.source_id: "Review current releases."}
    calls: list[str] = []
    monkeypatch.setattr("src.pipeline.load_config", lambda: config)
    monkeypatch.setattr(f"src.pipeline.{function}", lambda *args, **kwargs: calls.append(stage))
    monkeypatch.setattr(
        "src.pipeline.refresh_latest_sources",
        lambda *args, **kwargs: pytest.fail("Offline build/validation must not attempt source refresh"),
    )
    _run_stage(stage, Namespace(start_date=None, end_date=None), refresh_latest=False)
    assert calls == [stage]
