"""Reconcile the combined delivery table with its unchanged native inputs."""

from __future__ import annotations

import duckdb
import pandas as pd

from src.common.config import ProjectConfig
from src.harmonization.nine_constructions import build_nine_constructions


def nine_constructions_failures(
    config: ProjectConfig, *, connection: duckdb.DuckDBPyConnection | None = None,
) -> pd.DataFrame:
    failures = []
    if not (config.root / "config" / "nine_constructions.yaml").is_file():
        return pd.DataFrame(columns=["location", "reason"])
    try:
        expected = build_nine_constructions(config, write_outputs=False)
    except (ValueError, KeyError, OSError) as exc:
        return pd.DataFrame([{"location": "nine_constructions_inputs", "reason": str(exc)}])

    def compare(actual: pd.DataFrame, location: str) -> None:
        try:
            if set(actual.columns) != set(expected.columns):
                raise ValueError("Combined table columns do not match the configured source projections.")
            keys = ["row_type", "source_row_key"]
            pd.testing.assert_frame_equal(
                actual.loc[:, expected.columns].sort_values(keys).reset_index(drop=True),
                expected.sort_values(keys).reset_index(drop=True),
                check_dtype=False, check_exact=True,
            )
        except (AssertionError, ValueError, KeyError) as exc:
            failures.append({"location": location, "reason": str(exc)[:1500]})

    path = config.root / "data" / "final" / "nine_constructions.parquet"
    try:
        compare(pd.read_parquet(path), path.relative_to(config.root).as_posix())
    except (OSError, ValueError) as exc:
        failures.append({"location": "nine_constructions.parquet", "reason": str(exc)})
    own_connection = connection is None
    try:
        if own_connection:
            connection = duckdb.connect(str(config.root / config.pipeline["database_path"]), read_only=True)
        compare(connection.execute("SELECT * FROM analytics.nine_constructions").fetchdf(), "analytics.nine_constructions")
        dictionary_path = config.root / "metadata" / "nine_constructions_dictionary.csv"
        published = connection.execute("SELECT * FROM metadata.nine_constructions_dictionary").fetchdf().fillna("").astype(str)
        dictionary = pd.read_csv(dictionary_path, dtype=str, keep_default_na=False)
        pd.testing.assert_frame_equal(published, dictionary, check_dtype=False)
    except (duckdb.Error, OSError, AssertionError, ValueError) as exc:
        failures.append({"location": "DuckDB nine-construction delivery", "reason": str(exc)[:1500]})
    finally:
        if own_connection and connection is not None:
            connection.close()
    return pd.DataFrame(failures, columns=["location", "reason"])
