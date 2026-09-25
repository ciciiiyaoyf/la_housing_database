"""Register housing-finance Versions B and C in DuckDB.

This module owns only the new monthly-version objects.  In particular, it
does not create, replace, drop, or otherwise reference Version A objects.
Version C discovery exposes only observation/lineage pairs committed by a
valid completion marker.  The legacy flat pair is accepted only when it has
the same checksum-gated marker and no valid append-only partitions exist.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from src.common.io import sha256_file


VERSION_B_OBSERVATIONS = "analytics.housing_finance_monthly_hist"
VERSION_B_LINEAGE = "metadata.housing_finance_monthly_hist_lineage"
VERSION_B_LA_VIEW = "analytics.v_la_version_b_monthly_hist"

VERSION_C_OBSERVATIONS = "analytics.housing_finance_monthly_rt"
VERSION_C_LINEAGE = "metadata.housing_finance_monthly_rt_lineage"
VERSION_C_LA_VIEW = "analytics.v_la_version_c_monthly_rt"
VERSION_C_LATEST_VIEW = "analytics.v_latest_version_c_monthly_rt"
VERSION_C_LATEST_LA_VIEW = "analytics.v_la_latest_version_c_monthly_rt"

_LA_COUNTY_FIPS = "06037"
_COMPLETION_SCHEMA_VERSION = "housing_finance_output_pair_v1"
_CUTOFF_PARTITION_PREFIX = "information_cutoff_date="
_C_PART_FILENAME = "part.parquet"
_REGISTRATION_KEYS = (
    "version_b_observation_files",
    "version_b_lineage_files",
    "version_c_observation_files",
    "version_c_lineage_files",
)


def _parquet_files(path: Path) -> list[Path]:
    """Return deterministic Parquet inputs without following an absent path."""

    if path.is_file():
        return [path] if path.suffix.casefold() == ".parquet" else []
    if not path.is_dir():
        return []
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.casefold() == ".parquet"
    )


def _relative_path(project_root: Path, path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def _valid_completion_marker(
    project_root: Path,
    *,
    marker_path: Path,
    observation_path: Path,
    lineage_path: Path,
    cutoff: str | None,
) -> bool:
    """Return whether a marker commits this exact Version C output pair."""

    if not (
        marker_path.is_file()
        and observation_path.is_file()
        and lineage_path.is_file()
    ):
        return False
    try:
        payload: Any = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    expected = {
        "schema_version": _COMPLETION_SCHEMA_VERSION,
        "database_version": "C",
        "information_cutoff_date": cutoff,
        "observation_path": _relative_path(project_root, observation_path),
        "lineage_path": _relative_path(project_root, lineage_path),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return False
    try:
        return (
            payload.get("observation_sha256")
            == sha256_file(observation_path)
            and payload.get("lineage_sha256") == sha256_file(lineage_path)
        )
    except OSError:
        return False


def _cutoff_from_partition_name(name: str) -> str | None:
    if not name.startswith(_CUTOFF_PARTITION_PREFIX):
        return None
    cutoff = name.removeprefix(_CUTOFF_PARTITION_PREFIX)
    try:
        parsed = date.fromisoformat(cutoff)
    except ValueError:
        return None
    return cutoff if parsed.isoformat() == cutoff else None


def _version_c_partition_pairs(
    project_root: Path,
) -> list[tuple[Path, Path]]:
    """Find checksum-valid append-only cutoff partitions as exact pairs."""

    final_root = project_root / "data" / "final"
    observation_root = final_root / "housing_finance_monthly_rt"
    lineage_root = final_root / "housing_finance_monthly_rt_lineage"
    pairs: list[tuple[Path, Path]] = []
    if not observation_root.is_dir():
        return pairs
    for observation_path in sorted(
        observation_root.glob(
            f"{_CUTOFF_PARTITION_PREFIX}*/{_C_PART_FILENAME}"
        )
    ):
        cutoff = _cutoff_from_partition_name(observation_path.parent.name)
        if cutoff is None:
            continue
        lineage_path = (
            lineage_root / observation_path.parent.name / _C_PART_FILENAME
        )
        marker_path = observation_path.parent / "_COMPLETE.json"
        if _valid_completion_marker(
            project_root,
            marker_path=marker_path,
            observation_path=observation_path,
            lineage_path=lineage_path,
            cutoff=cutoff,
        ):
            pairs.append((observation_path, lineage_path))
    return pairs


def _safe_legacy_version_c_pair(
    project_root: Path,
) -> tuple[Path, Path] | None:
    """Return a checksum-gated flat legacy pair, never an unmarked file."""

    final_root = project_root / "data" / "final"
    observation_path = final_root / "housing_finance_monthly_rt.parquet"
    lineage_path = final_root / "housing_finance_monthly_rt_lineage.parquet"
    marker_path = final_root / "_COMPLETE.json"
    if not _valid_completion_marker(
        project_root,
        marker_path=marker_path,
        observation_path=observation_path,
        lineage_path=lineage_path,
        cutoff=None,
    ):
        return None
    return observation_path, lineage_path


def _version_c_files(project_root: Path) -> tuple[list[Path], list[Path]]:
    """Prefer valid cutoff pairs, then a checksum-gated legacy flat pair."""

    pairs = _version_c_partition_pairs(project_root)
    if not pairs:
        legacy = _safe_legacy_version_c_pair(project_root)
        if legacy is not None:
            pairs = [legacy]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _sql_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("'", "''")
    return f"'{value}'"


def _replace_from_parquet(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    paths: list[Path],
) -> None:
    sources = ", ".join(_sql_path(path) for path in paths)
    connection.execute(
        f"CREATE OR REPLACE TABLE {table_name} AS "
        f"SELECT * FROM read_parquet(["
        f"{sources}], union_by_name = true, hive_partitioning = false)"
    )


def _table_columns(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            f"PRAGMA table_info('{table_name}')"
        ).fetchall()
    }


def _register_version_b_views(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    connection.execute(
        f"CREATE OR REPLACE VIEW {VERSION_B_LA_VIEW} AS "
        f"SELECT * FROM {VERSION_B_OBSERVATIONS} "
        f"WHERE county_fips = '{_LA_COUNTY_FIPS}'"
    )


def _register_version_c_views(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    columns = _table_columns(connection, VERSION_C_OBSERVATIONS)
    if "information_cutoff_date" in columns:
        cutoff_column = "information_cutoff_date"
    elif "as_of_date" in columns:
        # Compatibility with the first real-time generator.  New normalized
        # outputs use the shared information_cutoff_date schema contract.
        cutoff_column = "as_of_date"
    else:
        raise ValueError(
            "Version C observations require information_cutoff_date "
            "(or legacy as_of_date) to identify the latest cutoff"
        )

    connection.execute(
        f"CREATE OR REPLACE VIEW {VERSION_C_LA_VIEW} AS "
        f"SELECT * FROM {VERSION_C_OBSERVATIONS} "
        f"WHERE county_fips = '{_LA_COUNTY_FIPS}'"
    )
    connection.execute(
        f"CREATE OR REPLACE VIEW {VERSION_C_LATEST_VIEW} AS "
        f"SELECT * FROM {VERSION_C_OBSERVATIONS} "
        f"WHERE {cutoff_column} = ("
        f"SELECT max({cutoff_column}) FROM {VERSION_C_OBSERVATIONS})"
    )
    connection.execute(
        f"CREATE OR REPLACE VIEW {VERSION_C_LATEST_LA_VIEW} AS "
        f"SELECT * FROM {VERSION_C_LATEST_VIEW} "
        f"WHERE county_fips = '{_LA_COUNTY_FIPS}'"
    )


def register_housing_finance_versions(
    connection: duckdb.DuckDBPyConnection,
    root: str | Path,
) -> dict[str, int]:
    """Load present Version B/C outputs without mutating Version A objects.

    Version B is a single revised-history artifact.  Version C is loaded only
    from cutoff partitions whose sibling ``_COMPLETE.json`` commits the exact
    observation/lineage pair and validates both checksums.  Unmarked, invalid,
    or half-written partitions are ignored.  A legacy flat pair is accepted
    only with the same checksum-gated marker and only when no valid partitions
    exist.  Missing outputs are a supported no-op, which lets the general
    database build run before either optional monthly version is materialized.

    Returns file counts for the four independently optional registrations.
    """

    project_root = Path(root)
    final_root = project_root / "data" / "final"
    c_observations, c_lineage = _version_c_files(project_root)
    files = {
        "version_b_observation_files": _parquet_files(
            final_root / "housing_finance_monthly_hist.parquet"
        ),
        "version_b_lineage_files": _parquet_files(
            final_root / "housing_finance_monthly_hist_lineage.parquet"
        ),
        "version_c_observation_files": c_observations,
        "version_c_lineage_files": c_lineage,
    }
    result = {key: len(files[key]) for key in _REGISTRATION_KEYS}
    if not any(result.values()):
        return result

    if files["version_b_observation_files"] or files[
        "version_c_observation_files"
    ]:
        connection.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    if files["version_b_lineage_files"] or files["version_c_lineage_files"]:
        connection.execute("CREATE SCHEMA IF NOT EXISTS metadata")

    if files["version_b_observation_files"]:
        _replace_from_parquet(
            connection,
            VERSION_B_OBSERVATIONS,
            files["version_b_observation_files"],
        )
        _register_version_b_views(connection)
    if files["version_b_lineage_files"]:
        _replace_from_parquet(
            connection,
            VERSION_B_LINEAGE,
            files["version_b_lineage_files"],
        )
    if files["version_c_observation_files"]:
        _replace_from_parquet(
            connection,
            VERSION_C_OBSERVATIONS,
            files["version_c_observation_files"],
        )
        _register_version_c_views(connection)
    if files["version_c_lineage_files"]:
        _replace_from_parquet(
            connection,
            VERSION_C_LINEAGE,
            files["version_c_lineage_files"],
        )
    return result


__all__ = [
    "VERSION_B_LA_VIEW",
    "VERSION_B_LINEAGE",
    "VERSION_B_OBSERVATIONS",
    "VERSION_C_LATEST_LA_VIEW",
    "VERSION_C_LATEST_VIEW",
    "VERSION_C_LA_VIEW",
    "VERSION_C_LINEAGE",
    "VERSION_C_OBSERVATIONS",
    "register_housing_finance_versions",
]
