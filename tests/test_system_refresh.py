from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import duckdb

from src.common.config import ProjectConfig
from src.system_refresh import _check_database_access, _existing_c_cutoff, _source_actions, main


def _config(root: Path) -> ProjectConfig:
    (root / "database").mkdir(exist_ok=True)
    with duckdb.connect(str(root / "database/housing.duckdb")):
        pass
    (root / "config").mkdir(exist_ok=True)
    (root / "config/version_c_monthly_rt.yaml").write_text(
        "output_root: data/final/housing_finance_monthly_rt\n"
        "lineage_output_root: data/final/housing_finance_monthly_rt_lineage\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-08-24",
        sources=(),
        pipeline={"database_path": "database/housing.duckdb"},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def test_system_refresh_runs_general_then_b_c_and_integrated_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("src.system_refresh.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "src.system_refresh.verify_version_a_freeze",
        lambda root: calls.append(("freeze", root)),
    )
    monkeypatch.setattr(
        "src.system_refresh.general_pipeline_main",
        lambda argv: calls.append(("general", tuple(argv))) or 0,
    )
    monkeypatch.setattr(
        "src.system_refresh.monthly_finance_main",
        lambda version, argv: calls.append((version, tuple(argv))) or 0,
    )
    monkeypatch.setattr(
        "src.system_refresh.build_database",
        lambda config: calls.append("database"),
    )
    monkeypatch.setattr(
        "src.system_refresh.run_qa", lambda config: calls.append("qa")
    )
    monkeypatch.setattr(
        "src.system_refresh.build_catalog_outputs",
        lambda config: calls.append("catalog"),
    )
    monkeypatch.setattr(
        "src.system_refresh.export_outputs",
        lambda config: calls.append("export"),
    )
    monkeypatch.setattr(
        "src.system_refresh.refresh_variable_metadata",
        lambda config: calls.append("metadata"),
    )

    result = main(["--information-cutoff", "2026-08-24"])

    assert result == 0
    assert calls[1] == ("general", ("--stage", "refresh", "--log-level", "INFO"))
    assert calls[2] == ("B", ("--stage", "all"))
    assert calls[3] == (
        "C",
        ("--stage", "all", "--information-cutoff", "2026-08-24"),
    )
    assert calls[4:9] == ["database", "catalog", "metadata", "qa", "export"]
    assert calls[-1][0] == "freeze"
    log = pd.read_csv(tmp_path / "metadata" / "system_refresh_run_log.csv")
    assert log.loc[0, "status"] == "succeeded"
    assert log.loc[0, "version_a_action"] == "verified_frozen_not_rebuilt"
    assert bool(log.loc[0, "integrated_completed"])
    assert log.loc[0, "version_c_action"] == "built_new_cutoff"


def test_system_refresh_stops_before_finance_when_general_refresh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finance_calls: list[object] = []
    monkeypatch.setattr("src.system_refresh.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("src.system_refresh.verify_version_a_freeze", lambda root: None)
    monkeypatch.setattr("src.system_refresh.general_pipeline_main", lambda argv: 1)
    monkeypatch.setattr(
        "src.system_refresh.monthly_finance_main",
        lambda *args: finance_calls.append(args) or 0,
    )

    result = main(["--information-cutoff", "2026-08-24"])

    assert result == 1
    assert finance_calls == []
    log = pd.read_csv(tmp_path / "metadata" / "system_refresh_run_log.csv")
    assert log.loc[0, "status"] == "failed"
    assert "General source refresh" in log.loc[0, "error"]
    assert not bool(log.loc[0, "general_completed"])
    assert not bool(log.loc[0, "version_b_rebuilt"])
    assert not bool(log.loc[0, "version_c_rebuilt"])
    assert log.loc[0, "version_c_action"] == "not_started"


def test_system_refresh_rejects_timestamp_cutoff() -> None:
    with pytest.raises(SystemExit):
        main(["--information-cutoff", "2026-08-24T12:00:00Z"])


@pytest.fixture
def successful_refresh(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.system_refresh.load_config", lambda: config)
    monkeypatch.setattr("src.system_refresh.verify_version_a_freeze", lambda root: None)
    monkeypatch.setattr("src.system_refresh.general_pipeline_main", lambda argv: 0)
    for name in ("build_database", "build_catalog_outputs", "refresh_variable_metadata", "run_qa", "export_outputs"):
        monkeypatch.setattr(f"src.system_refresh.{name}", lambda config: None)
    monkeypatch.setattr("src.system_refresh.monthly_finance_main", lambda *args: 0)
    return config


def test_same_cutoff_is_validated_and_reused_without_overwriting(successful_refresh, monkeypatch):
    config = successful_refresh
    cutoff = "2026-08-24"
    partition = config.root / "data/final/housing_finance_monthly_rt" / f"information_cutoff_date={cutoff}"
    partition.mkdir(parents=True)
    marker = partition / "_COMPLETE.json"
    marker.write_text("existing immutable fixture", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "src.system_refresh.monthly_finance_main",
        lambda version, argv: calls.append((version, argv)) or 0,
    )
    assert main(["--information-cutoff", cutoff]) == 0
    assert calls[-1] == ("C", ["--stage", "validate", "--information-cutoff", cutoff])
    assert marker.read_text(encoding="utf-8") == "existing immutable fixture"
    log = pd.read_csv(config.root / "metadata/system_refresh_run_log.csv")
    assert not bool(log.loc[0, "version_c_rebuilt"])
    assert log.loc[0, "version_c_action"] == "validated_existing_cutoff"


def test_manual_release_actions_have_nonzero_exit_and_truthful_completion(successful_refresh, monkeypatch):
    config = successful_refresh
    monkeypatch.setattr("src.system_refresh._source_actions", lambda *args: ["manual_source: check newest official release"])
    assert main([]) == 2
    log = pd.read_csv(config.root / "metadata/system_refresh_run_log.csv")
    assert log.loc[0, "status"] == "completed_with_action_required"
    assert bool(log.loc[0, "integrated_completed"])
    assert log.loc[0, "action_required_count"] == 1


def test_failed_existing_c_validation_stops_integrated_publication(successful_refresh, monkeypatch):
    config = successful_refresh
    published = []
    monkeypatch.setattr("src.system_refresh._existing_c_cutoff", lambda *args: True)
    monkeypatch.setattr("src.system_refresh.monthly_finance_main", lambda version, argv: 1 if version == "C" else 0)
    monkeypatch.setattr("src.system_refresh.build_database", lambda config: published.append(True))
    assert main([]) == 1
    assert published == []
    log = pd.read_csv(config.root / "metadata/system_refresh_run_log.csv")
    assert bool(log.loc[0, "version_b_rebuilt"])
    assert not bool(log.loc[0, "version_c_rebuilt"])
    assert not bool(log.loc[0, "integrated_completed"])


def test_check_does_not_download_or_rebuild(successful_refresh, monkeypatch):
    calls = []
    monkeypatch.setattr("src.system_refresh.general_pipeline_main", lambda argv: calls.append(argv) or 0)
    assert main(["--check"]) == 0
    assert calls == []
    assert not (successful_refresh.root / "metadata/system_refresh_run_log.csv").exists()


def test_missing_database_is_not_created_by_preflight(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "database/housing.duckdb"
    path.unlink()
    with pytest.raises(FileNotFoundError, match="existing database"):
        _check_database_access(config)
    assert not path.exists()


def test_read_only_connection_blocks_refresh_before_network(successful_refresh, monkeypatch):
    config = successful_refresh
    calls = []
    monkeypatch.setattr("src.system_refresh.general_pipeline_main", lambda argv: calls.append(argv) or 0)
    with duckdb.connect(str(config.root / "database/housing.duckdb"), read_only=True):
        assert main([]) == 1
    assert calls == []
    log = pd.read_csv(config.root / "metadata/system_refresh_run_log.csv")
    assert ".quit" in log.loc[0, "error"]
