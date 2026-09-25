from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.common.config import load_config
from src.database.build import build_database
from src.database.housing_finance import (
    VERSION_B_LA_VIEW,
    VERSION_B_LINEAGE,
    VERSION_B_OBSERVATIONS,
    VERSION_C_LATEST_LA_VIEW,
    VERSION_C_LATEST_VIEW,
    VERSION_C_LA_VIEW,
    VERSION_C_LINEAGE,
    VERSION_C_OBSERVATIONS,
    register_housing_finance_versions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_FINAL_FILES = (
    "fact_housing_observation.parquet",
    "county_monthly_panel.parquet",
    "zip_monthly_panel.parquet",
    "homebuying_cost_scenarios.parquet",
    "metro_monthly_controls.parquet",
    "state_monthly_controls.parquet",
    "state_quarterly_panel.parquet",
    "state_annual_controls.parquet",
    "metro_quarterly_panel.parquet",
    "metro_quarterly_controls.parquet",
    "county_annual_controls.parquet",
    "zcta_annual_controls.parquet",
    "coc_annual_controls.parquet",
    "county_quarterly_controls.parquet",
)


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _write_empty_parquet_with_source_schema(
    source: Path,
    destination: Path,
) -> None:
    schema = pq.ParquetFile(source).schema_arrow
    empty = pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in schema],
        schema=schema,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(empty, destination)


def _objects(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = ?
            """,
            [schema],
        ).fetchall()
    }


def _version_a_hash(connection: duckdb.DuckDBPyConnection) -> str:
    payload = (
        connection.execute(
            "SELECT * FROM analytics.housing_finance_benchmark ORDER BY row_id"
        ).fetchall(),
        connection.execute(
            "PRAGMA table_info('analytics.housing_finance_benchmark')"
        ).fetchall(),
        connection.execute(
            "SELECT * FROM analytics.v_la_version_a_benchmark ORDER BY row_id"
        ).fetchall(),
        connection.execute(
            "SELECT * FROM raw.version_a_file_manifest ORDER BY relative_path"
        ).fetchall(),
        connection.execute(
            """
            SELECT sql
            FROM duckdb_views()
            WHERE schema_name = 'analytics'
              AND view_name = 'v_la_version_a_benchmark'
            """
        ).fetchone(),
    )
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _create_version_a_objects(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    connection.execute("CREATE SCHEMA analytics")
    connection.execute("CREATE SCHEMA raw")
    connection.execute(
        """
        CREATE TABLE analytics.housing_finance_benchmark AS
        SELECT * FROM (VALUES
            (1, '06037', 'payment_benchmark', 123.45),
            (2, '06001', 'payment_benchmark', 67.89)
        ) AS source(row_id, county_fips, variable_id, value)
        """
    )
    connection.execute(
        """
        CREATE VIEW analytics.v_la_version_a_benchmark AS
        SELECT * FROM analytics.housing_finance_benchmark
        WHERE county_fips = '06037'
        """
    )
    connection.execute(
        """
        CREATE TABLE raw.version_a_file_manifest AS
        SELECT * FROM (VALUES
            ('data/final/housing_finance_benchmark.parquet', 'abc123')
        ) AS source(relative_path, sha256)
        """
    )


def test_absent_outputs_are_a_true_noop(tmp_path: Path) -> None:
    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result == {
            "version_b_observation_files": 0,
            "version_b_lineage_files": 0,
            "version_c_observation_files": 0,
            "version_c_lineage_files": 0,
        }
        assert _objects(connection, "analytics") == set()
        assert _objects(connection, "metadata") == set()


def test_registers_version_b_table_la_view_and_metadata_lineage(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    _write_parquet(
        final / "housing_finance_monthly_hist.parquet",
        [
            {
                "county_fips": "06037",
                "month": pd.Timestamp("2025-01-31"),
                "variable_id": "payment_monthly_hist",
                "value": 100.0,
                "lineage_id": "b-la",
            },
            {
                "county_fips": "06001",
                "month": pd.Timestamp("2025-01-31"),
                "variable_id": "payment_monthly_hist",
                "value": 80.0,
                "lineage_id": "b-alameda",
            },
        ],
    )
    _write_parquet(
        final / "housing_finance_monthly_hist_lineage.parquet",
        [
            {"lineage_id": "b-la", "input_role": "version_a_anchor"},
            {"lineage_id": "b-alameda", "input_role": "version_a_anchor"},
        ],
    )

    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_b_observation_files"] == 1
        assert result["version_b_lineage_files"] == 1
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_B_OBSERVATIONS}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT county_fips FROM {VERSION_B_LA_VIEW}"
        ).fetchall() == [("06037",)]
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_B_LINEAGE}"
        ).fetchone()[0] == 2


def _write_c_partition(
    final: Path,
    cutoff: str,
    *,
    value: float,
) -> tuple[Path, Path, Path]:
    observation = {
        "county_fips": "06037",
        "month": pd.Timestamp("2025-12-31"),
        "information_cutoff_date": pd.Timestamp(cutoff),
        "variable_id": "payment_monthly_rt",
        "value": value,
        "lineage_id": f"c-{cutoff}",
    }
    lineage = {
        "lineage_id": f"c-{cutoff}",
        "information_cutoff_date": pd.Timestamp(cutoff),
        "input_role": "version_a_anchor",
    }
    observation_path = (
        final
        / "housing_finance_monthly_rt"
        / f"information_cutoff_date={cutoff}"
        / "part.parquet"
    )
    lineage_path = (
        final
        / "housing_finance_monthly_rt_lineage"
        / f"information_cutoff_date={cutoff}"
        / "part.parquet"
    )
    _write_parquet(observation_path, [observation])
    _write_parquet(lineage_path, [lineage])
    marker_path = _write_c_completion_marker(
        final.parents[1],
        observation_path,
        lineage_path,
        cutoff=cutoff,
    )
    return observation_path, lineage_path, marker_path


def _write_c_completion_marker(
    project_root: Path,
    observation_path: Path,
    lineage_path: Path,
    *,
    cutoff: str | None,
) -> Path:
    marker_path = observation_path.parent / "_COMPLETE.json"
    payload = {
        "schema_version": "housing_finance_output_pair_v1",
        "database_version": "C",
        "information_cutoff_date": cutoff,
        "observation_path": observation_path.resolve()
        .relative_to(project_root.resolve())
        .as_posix(),
        "observation_sha256": hashlib.sha256(
            observation_path.read_bytes()
        ).hexdigest(),
        "observation_rows": pq.ParquetFile(
            observation_path
        ).metadata.num_rows,
        "lineage_path": lineage_path.resolve()
        .relative_to(project_root.resolve())
        .as_posix(),
        "lineage_sha256": hashlib.sha256(
            lineage_path.read_bytes()
        ).hexdigest(),
        "lineage_rows": pq.ParquetFile(lineage_path).metadata.num_rows,
        "completed_at": "2026-08-17T00:00:00+00:00",
    }
    marker_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return marker_path


def test_registers_all_c_partitions_and_latest_and_la_views(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    _write_c_partition(final, "2026-01-15", value=100.0)
    _write_c_partition(final, "2026-02-15", value=110.0)

    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_c_observation_files"] == 2
        assert result["version_c_lineage_files"] == 2
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_C_OBSERVATIONS}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_C_LINEAGE}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_C_LA_VIEW}"
        ).fetchone()[0] == 2
        latest = connection.execute(
            f"SELECT information_cutoff_date, value FROM {VERSION_C_LATEST_VIEW}"
        ).fetchone()
        assert latest == (pd.Timestamp("2026-02-15"), 110.0)
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_C_LATEST_LA_VIEW}"
        ).fetchone()[0] == 1


def test_reregistration_picks_up_new_append_only_c_partition(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    _write_c_partition(final, "2026-01-15", value=100.0)

    with duckdb.connect() as connection:
        register_housing_finance_versions(connection, tmp_path)
        _write_c_partition(final, "2026-02-15", value=110.0)
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_c_observation_files"] == 2
        assert connection.execute(
            f"SELECT count(*) FROM {VERSION_C_OBSERVATIONS}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT value FROM {VERSION_C_LATEST_VIEW}"
        ).fetchall() == [(110.0,)]


def test_unmarked_invalid_and_half_written_c_partitions_are_ignored(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    _write_c_partition(final, "2026-01-15", value=100.0)

    unmarked_observation = (
        final
        / "housing_finance_monthly_rt"
        / "information_cutoff_date=2026-02-15"
        / "part.parquet"
    )
    unmarked_lineage = (
        final
        / "housing_finance_monthly_rt_lineage"
        / "information_cutoff_date=2026-02-15"
        / "part.parquet"
    )
    _write_parquet(
        unmarked_observation,
        [
            {
                "county_fips": "06037",
                "information_cutoff_date": pd.Timestamp("2026-02-15"),
                "value": 200.0,
            }
        ],
    )
    _write_parquet(unmarked_lineage, [{"lineage_id": "unmarked"}])

    _, _, invalid_marker = _write_c_partition(
        final,
        "2026-03-15",
        value=300.0,
    )
    invalid_payload = json.loads(invalid_marker.read_text(encoding="utf-8"))
    invalid_payload["observation_sha256"] = "0" * 64
    invalid_marker.write_text(
        json.dumps(invalid_payload),
        encoding="utf-8",
    )

    _, missing_lineage, _ = _write_c_partition(
        final,
        "2026-04-15",
        value=400.0,
    )
    missing_lineage.unlink()

    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_c_observation_files"] == 1
        assert result["version_c_lineage_files"] == 1
        assert connection.execute(
            f"SELECT information_cutoff_date, value FROM {VERSION_C_OBSERVATIONS}"
        ).fetchall() == [(pd.Timestamp("2026-01-15"), 100.0)]


def test_c_flat_file_and_legacy_as_of_date_are_supported(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    observation_path = final / "housing_finance_monthly_rt.parquet"
    lineage_path = final / "housing_finance_monthly_rt_lineage.parquet"
    _write_parquet(
        observation_path,
        [
            {
                "county_fips": "06037",
                "month": pd.Timestamp("2025-12-31"),
                "as_of_date": pd.Timestamp("2026-01-15", tz="UTC"),
                "variable_id": "payment_monthly_rt",
                "value": 100.0,
            },
            {
                "county_fips": "06037",
                "month": pd.Timestamp("2025-12-31"),
                "as_of_date": pd.Timestamp("2026-02-15", tz="UTC"),
                "variable_id": "payment_monthly_rt",
                "value": 110.0,
            },
        ],
    )
    _write_parquet(
        lineage_path,
        [
            {"lineage_id": "legacy-1", "input_role": "version_a_anchor"},
            {"lineage_id": "legacy-2", "input_role": "version_a_anchor"},
        ],
    )
    _write_c_completion_marker(
        tmp_path,
        observation_path,
        lineage_path,
        cutoff=None,
    )

    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_c_observation_files"] == 1
        assert result["version_c_lineage_files"] == 1
        assert connection.execute(
            f"SELECT value FROM {VERSION_C_LATEST_VIEW}"
        ).fetchall() == [(110.0,)]


def test_unmarked_flat_legacy_pair_is_not_registered(tmp_path: Path) -> None:
    final = tmp_path / "data" / "final"
    _write_parquet(
        final / "housing_finance_monthly_rt.parquet",
        [
            {
                "county_fips": "06037",
                "as_of_date": pd.Timestamp("2026-01-15", tz="UTC"),
                "value": 100.0,
            }
        ],
    )
    _write_parquet(
        final / "housing_finance_monthly_rt_lineage.parquet",
        [{"lineage_id": "unmarked-legacy"}],
    )

    with duckdb.connect() as connection:
        result = register_housing_finance_versions(connection, tmp_path)

        assert result["version_c_observation_files"] == 0
        assert result["version_c_lineage_files"] == 0
        assert _objects(connection, "analytics") == set()
        assert _objects(connection, "metadata") == set()


def test_c_without_a_cutoff_column_fails_explicitly(tmp_path: Path) -> None:
    observation_path = (
        tmp_path / "data" / "final" / "housing_finance_monthly_rt.parquet"
    )
    lineage_path = (
        tmp_path
        / "data"
        / "final"
        / "housing_finance_monthly_rt_lineage.parquet"
    )
    _write_parquet(
        observation_path,
        [{"county_fips": "06037", "value": 1.0}],
    )
    _write_parquet(lineage_path, [{"lineage_id": "legacy-1"}])
    _write_c_completion_marker(
        tmp_path,
        observation_path,
        lineage_path,
        cutoff=None,
    )

    with duckdb.connect() as connection:
        with pytest.raises(ValueError, match="require information_cutoff_date"):
            register_housing_finance_versions(connection, tmp_path)


def test_registration_preserves_version_a_content_schema_and_view_definition(
    tmp_path: Path,
) -> None:
    final = tmp_path / "data" / "final"
    _write_parquet(
        final / "housing_finance_monthly_hist.parquet",
        [
            {
                "county_fips": "06037",
                "month": pd.Timestamp("2025-01-31"),
                "variable_id": "payment_monthly_hist",
                "value": 100.0,
            }
        ],
    )
    _write_c_partition(final, "2026-01-15", value=105.0)

    with duckdb.connect() as connection:
        _create_version_a_objects(connection)
        before = _version_a_hash(connection)

        register_housing_finance_versions(connection, tmp_path)

        assert _version_a_hash(connection) == before
        assert "housing_finance_benchmark" in _objects(connection, "analytics")
        assert "v_la_version_a_benchmark" in _objects(connection, "analytics")
        assert "version_a_file_manifest" in _objects(connection, "raw")


def test_general_database_build_registers_b_and_c_without_changing_a(
    tmp_path: Path,
) -> None:
    root = tmp_path / "minimal_project"
    final = root / "data" / "final"
    source_final = PROJECT_ROOT / "data" / "final"
    for filename in BASE_FINAL_FILES:
        _write_empty_parquet_with_source_schema(
            source_final / filename,
            final / filename,
        )
    _write_empty_parquet_with_source_schema(
        PROJECT_ROOT / "data" / "clean" / "geography.parquet",
        root / "data" / "clean" / "geography.parquet",
    )
    (root / "sql").mkdir(parents=True)
    (root / "sql" / "create_schema.sql").write_text(
        (PROJECT_ROOT / "sql" / "create_schema.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    metadata = root / "metadata"
    metadata.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "variable_id": "global_dictionary_contract_marker",
                "display_name": "Global dictionary contract marker",
            }
        ]
    ).to_csv(metadata / "data_dictionary.csv", index=False)

    _write_parquet(
        final / "housing_finance_benchmark.parquet",
        [
            {
                "row_id": 1,
                "county_fips": "06037",
                "variable_id": "payment_benchmark",
                "value": 123.45,
            }
        ],
    )
    _write_parquet(
        final / "housing_finance_monthly_hist.parquet",
        [
            {
                "county_fips": "06037",
                "month": pd.Timestamp("2025-01-31"),
                "variable_id": "payment_monthly_hist",
                "value": 100.0,
                "lineage_id": "b-la",
            }
        ],
    )
    _write_parquet(
        final / "housing_finance_monthly_hist_lineage.parquet",
        [{"lineage_id": "b-la", "input_role": "version_a_anchor"}],
    )
    _write_c_partition(final, "2026-01-15", value=105.0)

    config = replace(
        load_config(PROJECT_ROOT),
        root=root,
        sources=(),
        pipeline={"database_path": "database/minimal.duckdb"},
    )
    database = root / "database" / "minimal.duckdb"
    counts = build_database(config, database_path_override=database)

    assert counts[VERSION_B_OBSERVATIONS] == 1
    assert counts[VERSION_B_LINEAGE] == 1
    assert counts[VERSION_C_OBSERVATIONS] == 1
    assert counts[VERSION_C_LINEAGE] == 1
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute(
            "SELECT * FROM analytics.housing_finance_benchmark"
        ).fetchall() == [(1, "06037", "payment_benchmark", 123.45)]
        assert connection.execute(
            "SELECT * FROM analytics.v_la_version_a_benchmark"
        ).fetchall() == [(1, "06037", "payment_benchmark", 123.45)]
        assert connection.execute(
            f"SELECT value FROM {VERSION_B_LA_VIEW}"
        ).fetchall() == [(100.0,)]
        assert connection.execute(
            f"SELECT value FROM {VERSION_C_LATEST_LA_VIEW}"
        ).fetchall() == [(105.0,)]
        assert connection.execute(
            "SELECT variable_id FROM metadata.variable_dictionary"
        ).fetchall() == [("global_dictionary_contract_marker",)]
