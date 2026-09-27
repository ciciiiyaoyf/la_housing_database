"""Publish the nine-construction delivery table without refreshing providers."""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile

import duckdb
import pandas as pd

from src.common.config import ProjectConfig
from src.harmonization.nine_constructions import build_nine_constructions
from src.reporting.query import export_table


def register_nine_constructions_dictionary(
    connection: duckdb.DuckDBPyConnection, root: Path,
) -> None:
    """Register the delivery mapping without adding or redefining variable IDs."""
    path = root / "metadata" / "nine_constructions_dictionary.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Nine-construction dictionary is missing: {path}")
    frame = pd.read_csv(path, dtype=str)
    connection.register("nine_constructions_dictionary_frame", frame)
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE metadata.nine_constructions_dictionary AS "
            "SELECT * FROM nine_constructions_dictionary_frame"
        )
    finally:
        connection.unregister("nine_constructions_dictionary_frame")


def export_nine_constructions(
    connection: duckdb.DuckDBPyConnection, root: Path,
) -> int:
    """Replace generated exports only after both data and definitions are ready."""
    destination = root / "output" / "exports"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nine-constructions-", dir=destination) as temporary:
        path = Path(temporary) / "la_nine_constructions.csv"
        rows, _, omitted = export_table(connection, path, dataset="nine-constructions")
        if omitted:
            raise ValueError(f"Nine-construction export would omit configured fields: {omitted}")
        for source in (path, path.with_name("la_nine_constructions.variables.csv")):
            source.replace(destination / source.name)
    return rows


def publish_nine_constructions(config: ProjectConfig) -> dict[str, int]:
    """Build/register/check/export from the five accepted local analytical inputs."""
    from src.validation.nine_constructions import nine_constructions_failures
    from src.reporting.system_inventory import build_system_inventory

    database = config.root / config.pipeline["database_path"]
    if not database.is_file():
        raise FileNotFoundError("Build the local housing database before publishing nine constructions.")
    # Acquire write access before changing generated files; an open reader must
    # not leave a new Parquet paired with an old, locked database.
    with duckdb.connect(str(database)) as connection:
        frame = build_nine_constructions(config)
        connection.register("nine_constructions_frame", frame)
        try:
            connection.execute("BEGIN TRANSACTION")
            connection.execute(
                "CREATE OR REPLACE TABLE analytics.nine_constructions AS "
                "SELECT * FROM nine_constructions_frame"
            )
            register_nine_constructions_dictionary(connection, config.root)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.unregister("nine_constructions_frame")
        failures = nine_constructions_failures(config, connection=connection)
        if not failures.empty:
            raise ValueError("Nine-construction reconciliation failed: " + failures.to_json(orient="records"))
        rows = export_nine_constructions(connection, config.root)
    build_system_inventory(config)
    logging.getLogger("la_housing.nine_constructions").info(
        "Published analytics.nine_constructions: %s rows; CSV and definitions in output/exports", rows
    )
    return {"nine_constructions_rows": rows}
