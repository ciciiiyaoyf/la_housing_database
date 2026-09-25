"""One-command source refresh and rebuild of published analytical estimates."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

from src.common.config import load_config
from src.common.io import append_csv, ensure_directories
from src.database import build_database
from src.housing_finance_monthly.pipeline import (
    main as monthly_finance_main,
    verify_version_a_freeze,
)
from src.pipeline import main as general_pipeline_main
from src.reporting import build_catalog_outputs, export_outputs
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
    command.add_argument("--source", help="Optionally refresh one general source_id")
    command.add_argument("--start-date", help="Optional supported-source start date")
    command.add_argument("--end-date", help="Optional supported-source end date")
    command.add_argument(
        "--local-file",
        help="Official local file for a supported manually imported source.",
    )
    command.add_argument("--skip-version-b", action="store_true")
    command.add_argument("--skip-version-c", action="store_true")
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


def main(argv: list[str] | None = None) -> int:
    command = parser()
    arguments = command.parse_args(argv)
    try:
        cutoff = _date_only(arguments.information_cutoff)
    except ValueError as exc:
        command.error(str(exc))
    config = load_config()
    ensure_directories(config.root)
    started = datetime.now(timezone.utc)
    status = "succeeded"
    error = ""
    try:
        verify_version_a_freeze(config.root)
        if general_pipeline_main(_general_arguments(arguments)) != 0:
            raise RuntimeError(
                "General source refresh or analytical rebuild failed; see "
                "metadata/source_refresh_status.csv and metadata/pipeline_run_log.csv"
            )
        if not arguments.skip_version_b:
            if monthly_finance_main("B", ["--stage", "all"]) != 0:
                raise RuntimeError("Version B rebuild failed")
        if not arguments.skip_version_c:
            if monthly_finance_main(
                "C",
                [
                    "--stage",
                    "all",
                    "--information-cutoff",
                    cutoff,
                ],
            ) != 0:
                raise RuntimeError("Version C cutoff build failed")

        # The general build runs before B/C. Re-register the completed monthly
        # products, then rerun structural QA/catalog/exports over the integrated
        # database so users never query a stale Version B or latest-C view.
        build_database(config)
        run_qa(config)
        build_catalog_outputs(config)
        export_outputs(config)
        verify_version_a_freeze(config.root)
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
                    "version_a_action": "verified_frozen_not_rebuilt",
                    "version_b_rebuilt": not arguments.skip_version_b,
                    "version_c_rebuilt": not arguments.skip_version_c,
                    "status": status,
                    "error": error,
                }
            ],
        )
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
