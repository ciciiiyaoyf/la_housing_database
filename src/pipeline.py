"""Stage orchestration and reproducible CLI."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from src.cleaning import clean_sources
from src.common.config import load_config
from src.common.io import append_csv, ensure_directories
from src.common.logging import configure_logging
from src.database import build_database, sync_source_metadata
from src.database.nine_constructions import publish_nine_constructions
from src.harmonization import build_analytical_outputs
from src.ingestion.discover import discover_sources
from src.ingestion.download import download_sources
from src.ingestion.refresh import (
    refresh_latest_sources,
    verify_refreshed_clean_outputs,
)
from src.reporting import build_catalog_outputs, export_outputs
from src.reporting.variable_metadata import refresh_variable_metadata
from src.validation import run_qa

PIPELINE_STAGES = (
    "discover",
    "download",
    "clean",
    "harmonize",
    "build-database",
    "validate",
    "catalog",
    "export",
)
STAGES = (*PIPELINE_STAGES, "variable-metadata", "nine-constructions", "all", "refresh")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--stage", choices=STAGES, required=True)
    command.add_argument(
        "--source", help="Configured source_id or source group; redfin selects county and ZIP sources"
    )
    command.add_argument("--start-date", help="Inclusive ISO date filter")
    command.add_argument("--end-date", help="Inclusive ISO date filter")
    command.add_argument("--vintage", help="Reserved source-vintage selector (ISO date)")
    command.add_argument("--force-download", action="store_true")
    command.add_argument(
        "--local-file",
        help="Import an official local file for a manually acquired source.",
    )
    command.add_argument("--offline", action="store_true")
    command.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return command


def _run_stage(
    stage: str,
    arguments: argparse.Namespace,
    *,
    refresh_latest: bool = False,
) -> None:
    config = load_config()
    if stage == "discover":
        discover_sources(config, arguments.source, arguments.offline)
    elif stage == "download":
        if refresh_latest:
            completed, _, _ = refresh_latest_sources(
                config,
                source_filter=arguments.source,
                start_date=arguments.start_date,
                end_date=arguments.end_date,
                local_file=arguments.local_file,
            )
        else:
            completed, _ = download_sources(
                config,
                arguments.source,
                arguments.force_download,
                arguments.offline,
                arguments.start_date,
                arguments.end_date,
                arguments.local_file,
            )
        if completed:
            sync_source_metadata(config)
    elif stage == "clean":
        clean_sources(
            config,
            arguments.source,
            arguments.vintage,
            strict=refresh_latest,
        )
        if refresh_latest:
            verify_refreshed_clean_outputs(
                config,
                source_filter=arguments.source,
            )
    elif stage == "harmonize":
        build_analytical_outputs(config, arguments.start_date, arguments.end_date)
    elif stage == "build-database":
        build_database(config)
    elif stage == "validate":
        run_qa(config)
    elif stage == "catalog":
        build_catalog_outputs(config)
    elif stage == "export":
        export_outputs(config)
    elif stage == "variable-metadata":
        refresh_variable_metadata(config)
    elif stage == "nine-constructions":
        publish_nine_constructions(config)
    else:
        raise ValueError(stage)


def main(argv: list[str] | None = None) -> int:
    command = parser()
    arguments = command.parse_args(argv)
    if arguments.stage == "refresh" and arguments.offline:
        command.error("--stage refresh requires online source verification")
    if arguments.stage == "refresh" and arguments.vintage:
        command.error("--stage refresh does not accept --vintage")
    config = load_config()
    ensure_directories(config.root)
    logger = configure_logging(config.root, arguments.log_level)
    stages = (
        PIPELINE_STAGES
        if arguments.stage in {"all", "refresh"}
        else (arguments.stage,)
    )
    refresh_latest = arguments.stage == "refresh"
    run_started = datetime.now(timezone.utc)
    status = "succeeded"
    error = ""
    try:
        for stage in stages:
            logger.info("Starting stage: %s", stage)
            _run_stage(stage, arguments, refresh_latest=refresh_latest)
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Pipeline failed")
    finally:
        append_csv(
            config.root / "metadata" / "pipeline_run_log.csv",
            [
                {
                    "started_at": run_started.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "requested_stage": arguments.stage,
                    "source_filter": arguments.source,
                    "start_date": arguments.start_date,
                    "end_date": arguments.end_date,
                    "vintage": arguments.vintage,
                    "offline": arguments.offline,
                    "force_download": arguments.force_download,
                    "refresh_latest": refresh_latest,
                    "status": status,
                    "error": error,
                }
            ],
        )
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
