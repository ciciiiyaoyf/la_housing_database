"""One-command source refresh and rebuild of published analytical estimates."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from src.common.config import ProjectConfig, load_config
from src.common.io import append_csv, ensure_directories
from src.database import build_database
from src.housing_finance_monthly.pipeline import (
    main as monthly_finance_main,
    verify_version_a_freeze,
)
from src.pipeline import main as general_pipeline_main
from src.reporting import build_catalog_outputs, export_outputs
from src.reporting.variable_metadata import refresh_variable_metadata
from src.validation import run_qa


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--information-cutoff",
        help=(
            "Date-only Version C cutoff (YYYY-MM-DD). Defaults to today's UTC "
            "date and creates/reuses that append-only vintage."
        ),
    )
    command.add_argument(
        "--source", help="Optionally refresh a general source_id or group; redfin selects county and ZIP sources"
    )
    command.add_argument("--start-date", help="Optional supported-source start date")
    command.add_argument("--end-date", help="Optional supported-source end date")
    command.add_argument(
        "--local-file",
        help="Official local file for a supported manually imported source.",
    )
    command.add_argument("--skip-version-b", action="store_true")
    command.add_argument("--skip-version-c", action="store_true")
    command.add_argument(
        "--check", action="store_true",
        help="Check database access and frozen inputs, and list known source actions; no downloads or rebuilds.",
    )
    command.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return command


def _date_only(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--information-cutoff must be exactly YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("--information-cutoff must be exactly YYYY-MM-DD")
    return value


def _general_arguments(arguments: argparse.Namespace) -> list[str]:
    result = ["--stage", "refresh", "--log-level", arguments.log_level]
    for flag, value in (
        ("--source", arguments.source),
        ("--start-date", arguments.start_date),
        ("--end-date", arguments.end_date),
        ("--local-file", arguments.local_file),
    ):
        if value:
            result.extend([flag, value])
    return result


def _check_database_access(config: ProjectConfig) -> None:
    """Fail before acquisition if the existing research database is in use."""
    path = config.root / config.pipeline["database_path"]
    if not path.is_file():
        raise FileNotFoundError(
            f"Refresh requires an existing database: {path}. "
            "Follow docs/database_build_and_refresh.md to provision the workspace."
        )
    try:
        with duckdb.connect(str(path)) as connection:
            connection.execute("SELECT 1")
    except (duckdb.IOException, duckdb.ConnectionException) as exc:
        raise RuntimeError(
            "Cannot open the database for refresh. Exit DuckDB with .quit, stop "
            "the SQL UI and Data Browser, and close other database readers before retrying."
        ) from exc


def _existing_c_cutoff(root: Path, cutoff: str) -> bool:
    """Existing append-only partitions must be validated, never overwritten."""
    with (root / "config" / "version_c_monthly_rt.yaml").open(encoding="utf-8") as handle:
        settings = yaml.safe_load(handle)
    partition = f"information_cutoff_date={cutoff}"
    return any(
        (root / settings[name] / partition).exists()
        for name in ("output_root", "lineage_output_root")
    )


def _source_actions(config: ProjectConfig, source_filter: str | None) -> list[str]:
    """Surface manual release checks left unresolved by the general pipeline."""
    path = config.root / "metadata" / "source_refresh_status.csv"
    selected = {source.source_id for source in config.selected_sources(source_filter)}
    if not selected:
        return []
    if not path.is_file():
        return ["Source refresh report is missing; current releases are not verified."]
    frame = pd.read_csv(path, dtype=str).fillna("")
    actions = [
        f"{source_id}: missing from source refresh report"
        for source_id in sorted(selected - set(frame["source_id"]))
    ]
    for row in frame.loc[frame.source_id.isin(selected)].to_dict("records"):
        if row.get("action_required", "").strip():
            actions.append(f"{row['source_id']}: {row['action_required']}")
    return actions


def _preflight(config: ProjectConfig, arguments: argparse.Namespace) -> list[str]:
    _check_database_access(config)
    verify_version_a_freeze(config.root)
    selected = config.selected_sources(arguments.source)
    holds = config.pipeline.get("source_refresh_review_holds", {})
    actions = []
    for source in selected:
        if source.source_id in holds:
            actions.append(f"{source.source_id}: {holds[source.source_id]}")
        elif not source.auto_download and not arguments.local_file:
            actions.append(f"{source.source_id}: {source.manual_reason or 'Official manual release check required.'}")
    return actions


def main(argv: list[str] | None = None) -> int:
    command = parser()
    arguments = command.parse_args(argv)
    try:
        cutoff = _date_only(arguments.information_cutoff)
    except ValueError as exc:
        command.error(str(exc))
    config = load_config()
    if arguments.check:
        try:
            actions = _preflight(config, arguments)
        except Exception as exc:
            print(f"Refresh preflight failed: {exc}", file=sys.stderr)
            return 1
        print(f"Database access and frozen Version A checks passed. Planned C cutoff: {cutoff}.")
        for action in actions:
            print(f"Action required: {action}")
        print("No data downloaded or rebuilt. See docs/database_build_and_refresh.md for every dataset's update schedule.")
        return 2 if actions else 0
    ensure_directories(config.root)
    started = datetime.now(timezone.utc)
    status = "succeeded"
    error = ""
    general_completed = False
    version_b_rebuilt = False
    version_c_action = "skipped" if arguments.skip_version_c else "not_started"
    integrated_completed = False
    version_a_action = "not_verified"
    actions: list[str] = []
    try:
        _preflight(config, arguments)
        version_a_action = "verified_frozen_not_rebuilt"
        print(f"Refreshing configured datasets; Version C information cutoff: {cutoff}.", flush=True)
        if general_pipeline_main(_general_arguments(arguments)) != 0:
            raise RuntimeError(
                "General source refresh or analytical rebuild failed; see "
                "metadata/source_refresh_status.csv and metadata/pipeline_run_log.csv"
            )
        general_completed = True
        actions = _source_actions(config, arguments.source)
        if not arguments.skip_version_b:
            if monthly_finance_main("B", ["--stage", "all"]) != 0:
                raise RuntimeError("Version B rebuild failed")
            version_b_rebuilt = True
        if not arguments.skip_version_c:
            c_stage = "validate" if _existing_c_cutoff(config.root, cutoff) else "all"
            if monthly_finance_main(
                "C",
                [
                    "--stage",
                    c_stage,
                    "--information-cutoff",
                    cutoff,
                ],
            ) != 0:
                raise RuntimeError("Version C cutoff build failed")
            version_c_action = "validated_existing_cutoff" if c_stage == "validate" else "built_new_cutoff"

        # The general build runs before B/C. Re-register the completed monthly
        # products, then rerun structural QA/catalog/exports over the integrated
        # database so users never query a stale Version B or latest-C view.
        build_database(config)
        build_catalog_outputs(config)
        refresh_variable_metadata(config)
        run_qa(config)
        export_outputs(config)
        verify_version_a_freeze(config.root)
        integrated_completed = True
        if actions:
            status = "completed_with_action_required"
            for action in actions:
                print(f"Action required: {action}", file=sys.stderr)
        print(
            f"Refresh {status}. Frozen Version A is preserved; "
            f"Version C: {version_c_action}. See metadata/source_refresh_status.csv "
            "and metadata/system_refresh_run_log.csv. Fixed-edition and benchmark "
            "inputs remain at the documented vintages in the refresh schedule.",
            flush=True,
        )
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr)
    finally:
        append_csv(
            config.root / "metadata" / "system_refresh_run_log.csv",
            [
                {
                    "started_at": started.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "information_cutoff_date": cutoff,
                    "source_filter": arguments.source,
                    "start_date": arguments.start_date,
                    "end_date": arguments.end_date,
                    "version_a_action": version_a_action,
                    "general_completed": general_completed,
                    "version_b_rebuilt": version_b_rebuilt,
                    "version_c_rebuilt": version_c_action == "built_new_cutoff",
                    "version_c_action": version_c_action,
                    "integrated_completed": integrated_completed,
                    "action_required_count": len(actions),
                    "status": status,
                    "error": error,
                }
            ],
        )
    return {"succeeded": 0, "completed_with_action_required": 2, "failed": 1}[status]


if __name__ == "__main__":
    raise SystemExit(main())
