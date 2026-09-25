from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.common.config import ProjectConfig
from src.system_refresh import main


def _config(root: Path) -> ProjectConfig:
    return ProjectConfig(
        root=root,
        verified_at="2026-08-24",
        sources=(),
        pipeline={},
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

    result = main(["--information-cutoff", "2026-08-24"])

    assert result == 0
    assert calls[1] == ("general", ("--stage", "refresh", "--log-level", "INFO"))
    assert calls[2] == ("B", ("--stage", "all"))
    assert calls[3] == (
        "C",
        ("--stage", "all", "--information-cutoff", "2026-08-24"),
    )
    assert calls[4:8] == ["database", "qa", "catalog", "export"]
    assert calls[-1][0] == "freeze"
    log = pd.read_csv(tmp_path / "metadata" / "system_refresh_run_log.csv")
    assert log.loc[0, "status"] == "succeeded"
    assert log.loc[0, "version_a_action"] == "verified_frozen_not_rebuilt"


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


def test_system_refresh_rejects_timestamp_cutoff() -> None:
    with pytest.raises(SystemExit):
        main(["--information-cutoff", "2026-08-24T12:00:00Z"])
