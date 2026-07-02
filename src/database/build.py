"""Create an integrated DuckDB database from Parquet and metadata."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from src.common.config import ProjectConfig


def _source_registry(config: ProjectConfig) -> pd.DataFrame:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if manifest_path.exists():
        frame = pd.read_csv(manifest_path, dtype=str)
    else:
        frame = pd.DataFrame()
    rows: list[dict] = []
    for source in config.sources:
        matches = (
            frame.loc[frame["source_id"] == source.source_id]
            if not frame.empty
            else pd.DataFrame()
        )
        if matches.empty:
            rows.append(
                {
                    "source_id": source.source_id,
                    "provider": source.provider,
                    "dataset_name": source.dataset_name,
                    "source_page_url": source.source_page_url,
                    "download_url": source.download_url,
                    "native_frequency": source.native_frequency,
                    "geography_levels": "|".join(source.geography_levels),
                    "retrieved_at": None,
                    "data_vintage": None,
                    "source_filename": None,
                    "original_filename": None,
                    "stored_filename": None,
                    "checksum_sha256": None,
                    "retrieval_method": None,
                    "source_last_updated": None,
                    "selected_start_date": source.selected_start_date or None,
                    "selected_end_date": source.selected_end_date or None,
                    "source_frequency": source.native_frequency,
                    "rolling_window_months": source.rolling_window_months,
                    "request_series_ids": "|".join(source.series_ids) or None,
                    "request_start_year": None,
                    "request_end_year": None,
                    "response_latest_observation": None,
                    "response_observation_count": None,
                    "license_or_use_notes": source.license_or_use_notes,
                    "revision_notes": source.revision_notes,
                    "ingestion_status": "manual_required"
                    if not source.auto_download
                    else "not_downloaded",
                }
            )
        else:
            rows.extend(matches.to_dict("records"))
    columns = [
        "source_id",
        "provider",
        "dataset_name",
        "source_page_url",
        "download_url",
        "native_frequency",
        "geography_levels",
        "retrieved_at",
        "data_vintage",
        "source_filename",
        "original_filename",
        "stored_filename",
        "relative_path",
        "byte_size",
        "checksum_sha256",
        "retrieval_method",
        "source_last_updated",
        "selected_start_date",
        "selected_end_date",
        "source_frequency",
        "rolling_window_months",
        "request_series_ids",
        "request_start_year",
        "request_end_year",
        "response_latest_observation",
        "response_observation_count",
        "archive_period",
        "archive_format_version",
        "license_or_use_notes",
        "revision_notes",
        "ingestion_status",
    ]
    return pd.DataFrame(rows).reindex(columns=columns)


def sync_source_metadata(config: ProjectConfig) -> dict[str, int]:
    """Refresh source registry tables after a download without rebuilding analytics."""
    database_path = config.root / config.pipeline["database_path"]
    if not database_path.exists():
        return {}
    registry = _source_registry(config)
    connection = duckdb.connect(str(database_path))
    connection.execute((config.root / "sql" / "create_schema.sql").read_text())
    connection.register("source_registry_frame", registry)
    connection.execute(
        "CREATE OR REPLACE TABLE metadata.source_registry AS "
        "SELECT * FROM source_registry_frame"
    )
    connection.execute(
        "CREATE OR REPLACE TABLE raw.file_manifest AS "
        "SELECT * FROM metadata.source_registry WHERE source_filename IS NOT NULL"
    )
    counts = {
        "metadata.source_registry": connection.execute(
            "SELECT count(*) FROM metadata.source_registry"
        ).fetchone()[0],
        "raw.file_manifest": connection.execute(
            "SELECT count(*) FROM raw.file_manifest"
        ).fetchone()[0],
    }
    connection.close()
    return counts


def build_database(
    config: ProjectConfig, database_path_override: Path | None = None
) -> dict[str, int]:
    logger = logging.getLogger("la_housing.database")
    database_path = (
        database_path_override
        if database_path_override is not None
        else config.root / config.pipeline["database_path"]
    )
    if not database_path.is_absolute():
        database_path = config.root / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(database_path))
    connection.execute((config.root / "sql" / "create_schema.sql").read_text())
    frames = {
        "source_registry_frame": _source_registry(config),
        "variable_dictionary_frame": pd.DataFrame(config.variables),
        "geography_frame": pd.read_parquet(
            config.root / "data" / "clean" / "geography.parquet"
        ),
    }
    for name, frame in frames.items():
        connection.register(name, frame)
    connection.execute(
        "CREATE OR REPLACE TABLE metadata.source_registry AS "
        "SELECT * FROM source_registry_frame"
    )
    connection.execute(
        "CREATE OR REPLACE TABLE metadata.variable_dictionary AS "
        "SELECT * FROM variable_dictionary_frame"
    )
    connection.execute(
        "CREATE OR REPLACE TABLE metadata.geography AS SELECT * FROM geography_frame"
    )
    parquet_tables = {
        "analytics.fact_housing_observation": "fact_housing_observation.parquet",
        "analytics.county_monthly_panel": "county_monthly_panel.parquet",
        "analytics.zip_monthly_panel": "zip_monthly_panel.parquet",
        "analytics.state_quarterly_panel": "state_quarterly_panel.parquet",
    }
    final_dir = config.root / "data" / "final"
    for table, filename in parquet_tables.items():
        path = (final_dir / filename).as_posix().replace("'", "''")
        connection.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{path}')"
        )
    for path in (config.root / "data" / "clean").glob("*.parquet"):
        if path.name in {"geography.parquet", "geography_crosswalk.parquet"}:
            continue
        name = (
            "redfin_zip_monthly"
            if path.name == "redfin_zip_monthly_la_county.parquet"
            else re.sub(r"[^a-z0-9_]", "_", path.stem.lower())
        )
        clean_path = path.as_posix().replace("'", "''")
        connection.execute(
            f"CREATE OR REPLACE TABLE clean.{name} AS "
            f"SELECT * FROM read_parquet('{clean_path}')"
        )
    connection.execute(
        "CREATE OR REPLACE TABLE raw.file_manifest AS "
        "SELECT * FROM metadata.source_registry WHERE source_filename IS NOT NULL"
    )
    connection.execute(
        "CREATE OR REPLACE TABLE quality.qa_summary "
        "(check_id VARCHAR, severity VARCHAR, status VARCHAR, failure_count BIGINT, detail VARCHAR)"
    )
    counts = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in parquet_tables
    }
    connection.close()
    logger.info("Built %s with %s", database_path, counts)
    return counts
