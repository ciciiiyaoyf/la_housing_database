"""Refresh queryable variable labels without rebuilding source observations."""

from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pandas as pd

from src.common.config import ProjectConfig
from src.common.io import append_csv, sha256_file
from src.common.variable_metadata import enrich_variable_metadata, validate_variable_metadata
from src.database.variable_metadata import register_variable_metadata_views
from src.housing_finance_monthly.metadata import _merge_dictionary


def refresh_variable_metadata(config: ProjectConfig) -> dict[str, int]:
    """Write the complete dictionary, SQL labels, downloadable CSV, and audit row."""
    root = config.root
    dictionary_path = root / "metadata" / "data_dictionary.csv"
    configured = pd.DataFrame(config.variables)
    existing = pd.read_csv(dictionary_path) if dictionary_path.exists() else configured
    # Preserve existing descriptions and source metadata, adding only definitions
    # not yet in the dictionary before registering the monthly finance contracts.
    missing = configured.loc[~configured["variable_id"].isin(existing["variable_id"])]
    dictionary, _ = _merge_dictionary(root, pd.concat([existing, missing], ignore_index=True))
    dictionary = enrich_variable_metadata(dictionary, root)
    issues = validate_variable_metadata(dictionary, root, require_all=True)
    if issues:
        raise ValueError("Variable metadata contract failed: " + "; ".join(issues))
    dictionary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = dictionary_path.with_suffix(".csv.tmp")
    dictionary.to_csv(temporary, index=False)
    temporary.replace(dictionary_path)
    database_path = root / config.pipeline["database_path"]
    if database_path.exists():
        with duckdb.connect(str(database_path)) as connection:
            connection.execute("BEGIN TRANSACTION")
            connection.register("variable_metadata_dictionary_frame", dictionary)
            connection.execute("CREATE SCHEMA IF NOT EXISTS metadata")
            connection.execute(
                "CREATE OR REPLACE TABLE metadata.variable_dictionary AS "
                "SELECT * FROM variable_metadata_dictionary_frame"
            )
            register_variable_metadata_views(connection)
            connection.execute("COMMIT")
    export_dir = root / "output" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    dictionary.to_csv(export_dir / "variable_dictionary.csv", index=False)
    refreshed_outputs = [
        "metadata/data_dictionary.csv", "output/exports/variable_dictionary.csv",
    ]
    if database_path.exists():
        refreshed_outputs.append("metadata.variable_dictionary")
    for name in ("system_variable_inventory.csv", "estimate_inventory.csv"):
        inventory_path = root / "metadata" / name
        if inventory_path.exists():
            inventory = enrich_variable_metadata(pd.read_csv(inventory_path, dtype=str), root)
            temporary_inventory = inventory_path.with_suffix(".csv.tmp")
            inventory.to_csv(temporary_inventory, index=False)
            temporary_inventory.replace(inventory_path)
            refreshed_outputs.append(f"metadata/{name}")
    registry_path = root / "config" / "variable_metadata.csv"
    append_csv(root / "metadata" / "transformation_log.csv", [{
        "transformation_id": "variable_units_temporal_metadata_v1",
        "source_id": "multiple",
        "input_file": "config/variable_metadata.csv|config/variables.yaml|config/version_a_variables.yaml|config/version_b_variables.yaml|config/version_c_variables.yaml",
        "output_file": "|".join(refreshed_outputs),
        "input_rows_or_shape": f"{len(dictionary)} variable definitions",
        "output_rows": len(dictionary),
        "description": "Attach explicit simplified units, temporal types, notes, and monetary basis. Preserve original units and every observation value; perform no rescaling or frequency conversion. Workbook classifications and documented corrections are retained in the registry.",
        "code_module": "src.common.variable_metadata|src.reporting.variable_metadata",
        "source_vintage": "variable_metadata_v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "source_filename": registry_path.name,
        "checksum_sha256": sha256_file(registry_path),
    }])
    return {"variable_dictionary_rows": len(dictionary)}
