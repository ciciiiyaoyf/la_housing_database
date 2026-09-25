"""Integration contracts for metadata-only refreshes and labeled SQL views."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest

from src.common.config import ROOT, load_config
from src.common.io import sha256_file
from src.common.variable_metadata import (
    ENRICHMENT_COLUMNS,
    load_variable_metadata,
    validate_variable_metadata,
)
from src.database.variable_metadata import (
    LABELED_TABLES,
    register_variable_metadata_views,
)
from src.reporting.variable_metadata import refresh_variable_metadata


def _observations(variable_id: str, unit: str) -> pd.DataFrame:
    """Synthetic values intentionally include a null and a negative growth rate."""
    return pd.DataFrame({
        "observation_date": pd.to_datetime(["2025-01-31", "2025-02-28", "2025-03-31"]),
        "geo_id": ["ZIP_00123"] * 3,
        "county_fips": ["06037"] * 3,
        "postal_zip": ["00123"] * 3,
        "variable_id": [variable_id] * 3,
        "value": [-0.25, 1.50, None],
        "unit": [unit] * 3,
        "source_vintage": ["fixture-vintage"] * 3,
        "retrieved_at": ["2026-01-01T00:00:00+00:00"] * 3,
        "source_filename": ["fixture.csv"] * 3,
        "checksum_sha256": ["a" * 64] * 3,
    })


def _store_table(connection: duckdb.DuckDBPyConnection, name: str, frame: pd.DataFrame) -> None:
    connection.register("fixture_frame", frame)
    connection.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM fixture_frame")
    connection.unregister("fixture_frame")


@pytest.mark.parametrize("table_name", LABELED_TABLES)
def test_labeled_views_preserve_values_provenance_and_identifiers(table_name: str) -> None:
    original = _observations("zhvi_growth_mom", "fraction")
    dictionary = pd.DataFrame([{
        "variable_id": "zhvi_growth_mom",
        "unit_simplified": "share",
        "temporal_type": "growth_change",
        "temporal_type_note": "Growth may be negative or exceed one.",
        "monetary_basis": "not_applicable",
    }])
    with duckdb.connect() as connection:
        connection.execute("CREATE SCHEMA analytics")
        connection.execute("CREATE SCHEMA metadata")
        _store_table(connection, f"analytics.{table_name}", original)
        _store_table(connection, "metadata.variable_dictionary", dictionary)
        expected = connection.execute(f"SELECT * FROM analytics.{table_name} ORDER BY observation_date").df()

        register_variable_metadata_views(connection)
        register_variable_metadata_views(connection)

        labeled = connection.execute(f"SELECT * FROM analytics.{table_name}_labeled ORDER BY observation_date").df()
        pd.testing.assert_frame_equal(labeled[expected.columns], expected)
        assert len(labeled) == 3
        assert labeled["unit_simplified"].eq("share").all()
        assert labeled["temporal_type"].eq("growth_change").all()
        assert labeled["county_fips"].eq("06037").all()
        assert labeled["postal_zip"].eq("00123").all()
        assert labeled["value"].iloc[:2].tolist() == [-0.25, 1.5]


def test_labeled_views_reject_dictionary_duplicates_before_fanout() -> None:
    dictionary = pd.DataFrame({
        "variable_id": ["zhvi", "zhvi"],
        "unit_simplified": ["USD", "USD"],
        "temporal_type": ["period_statistic", "period_statistic"],
        "temporal_type_note": ["Nonadditive level."] * 2,
        "monetary_basis": ["current_dollars"] * 2,
    })
    with duckdb.connect() as connection:
        connection.execute("CREATE SCHEMA metadata")
        _store_table(connection, "metadata.variable_dictionary", dictionary)
        with pytest.raises(ValueError, match="unique variable IDs"):
            register_variable_metadata_views(connection)


@pytest.fixture
def metadata_project(tmp_path: Path):
    for relative in (
        "config/sources.yaml", "config/pipeline.yaml", "config/geographies.yaml",
        "config/variables.yaml", "config/version_a_variables.yaml",
        "config/version_b_variables.yaml", "config/version_c_variables.yaml",
        "config/variable_metadata.csv", "config/variable_metadata_provenance.json",
        "metadata/version_b_transformation_catalog.csv",
        "metadata/version_c_transformation_catalog.csv",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    # Start with a pre-feature dictionary and omit monthly registrations so the
    # refresh must rebuild both complete B/C contracts from their A anchors.
    dictionary = pd.read_csv(ROOT / "metadata/data_dictionary.csv", dtype=str)
    dictionary = dictionary.loc[~dictionary["variable_id"].str.endswith(("_monthly_hist", "_monthly_rt"))]
    dictionary = dictionary.drop(columns=list(ENRICHMENT_COLUMNS), errors="ignore")
    dictionary["researcher_annotation"] = "Preserve this existing extension."
    dictionary.to_csv(tmp_path / "metadata/data_dictionary.csv", index=False)
    pd.DataFrame([{"transformation_id": "existing_fixture_transform", "description": "Keep this audit row."}]).to_csv(
        tmp_path / "metadata/transformation_log.csv", index=False,
    )
    config = load_config(tmp_path)
    config = replace(config, pipeline={**config.pipeline, "database_path": "database/fixture.duckdb"})
    database = tmp_path / config.pipeline["database_path"]
    database.parent.mkdir(parents=True, exist_ok=True)
    product_ids = {
        "fact_housing_observation": ("zhvi_growth_mom", "fraction"),
        "housing_finance_benchmark": ("acs_aggregate_owner_value_all_benchmark", "USD"),
        "housing_finance_monthly_hist": ("acs_implied_first_mortgage_balance_cal_monthly_hist", "USD"),
        "housing_finance_monthly_rt": ("acs_implied_first_mortgage_balance_cal_monthly_rt", "USD"),
    }
    final = tmp_path / "data/final"
    final.mkdir(parents=True)
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA analytics")
        for table, (variable_id, unit) in product_ids.items():
            frame = _observations(variable_id, unit)
            frame.to_parquet(final / f"{table}.parquet", index=False)
            _store_table(connection, f"analytics.{table}", frame)
    # Existing archive and completion markers are immutable refresh inputs.
    archive = tmp_path / "data/raw/fixture/source.csv"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"synthetic test archive; do not rewrite")
    (final / "housing_finance_monthly_hist.complete.json").write_text("{}", encoding="utf-8")
    partition = final / "housing_finance_monthly_rt/information_cutoff_date=2026-01-01"
    partition.mkdir(parents=True)
    (partition / "_COMPLETE.json").write_text("{}", encoding="utf-8")
    return config


def test_metadata_refresh_covers_all_products_without_touching_observations(metadata_project) -> None:
    config = metadata_project
    protected = {path: sha256_file(path) for path in (config.root / "data").rglob("*") if path.is_file()}
    database = config.root / config.pipeline["database_path"]
    with duckdb.connect(str(database), read_only=True) as connection:
        before = {table: connection.execute(f"SELECT * FROM analytics.{table} ORDER BY observation_date").df() for table in LABELED_TABLES}

    result = refresh_variable_metadata(config)

    registry = load_variable_metadata(config.root)
    dictionary = pd.read_csv(config.root / "metadata/data_dictionary.csv", dtype=str, keep_default_na=False)
    assert result["variable_dictionary_rows"] == len(registry)
    assert dictionary["variable_id"].is_unique
    assert set(dictionary["variable_id"]) == set(registry["variable_id"])
    assert not validate_variable_metadata(dictionary, config.root, require_all=True)
    assert dictionary["variable_id"].str.endswith("_monthly_hist").sum() == 58
    assert dictionary["variable_id"].str.endswith("_monthly_rt").sum() == 58
    assert dictionary.loc[dictionary["variable_id"].eq("zhvi"), "researcher_annotation"].item() == "Preserve this existing extension."
    exported = pd.read_csv(config.root / "output/exports/variable_dictionary.csv", dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(exported, dictionary)
    assert {path: sha256_file(path) for path in protected} == protected
    with duckdb.connect(str(database), read_only=True) as connection:
        for table, expected in before.items():
            actual = connection.execute(f"SELECT * FROM analytics.{table} ORDER BY observation_date").df()
            labeled = connection.execute(f"SELECT * FROM analytics.{table}_labeled ORDER BY observation_date").df()
            pd.testing.assert_frame_equal(actual, expected)
            pd.testing.assert_frame_equal(labeled[expected.columns], expected)
            assert labeled["temporal_type"].notna().all()
        assert connection.execute("SELECT count(*) FROM metadata.variable_dictionary").fetchone()[0] == len(registry)
    log = pd.read_csv(config.root / "metadata/transformation_log.csv", dtype=str)
    assert log["transformation_id"].eq("existing_fixture_transform").sum() == 1
    entry = log.loc[log["transformation_id"].eq("variable_units_temporal_metadata_v1")].iloc[0]
    assert entry["checksum_sha256"] == sha256_file(config.root / "config/variable_metadata.csv")
    assert "no rescaling or frequency conversion" in entry["description"]
    assert pd.to_datetime(entry["executed_at"], utc=True) is not pd.NaT


def test_metadata_refresh_can_initialize_a_missing_dictionary(metadata_project) -> None:
    config = metadata_project
    dictionary_path = config.root / "metadata/data_dictionary.csv"
    dictionary_path.unlink()
    refresh_variable_metadata(config)
    dictionary = pd.read_csv(dictionary_path, dtype=str, keep_default_na=False)
    assert dictionary["variable_id"].is_unique
    assert not validate_variable_metadata(dictionary, config.root, require_all=True)


@pytest.mark.parametrize("damage", ["missing_column", "mismatched_classification", "duplicate_identifier"])
def test_metadata_structural_qa_rejects_missing_or_stale_classifications(metadata_project, damage: str) -> None:
    from src.validation.qa import _variable_metadata_failures

    config = metadata_project
    refresh_variable_metadata(config)
    assert _variable_metadata_failures(config).empty
    dictionary_path = config.root / "metadata/data_dictionary.csv"
    dictionary = pd.read_csv(dictionary_path, dtype=str, keep_default_na=False)
    if damage == "missing_column":
        dictionary = dictionary.drop(columns="temporal_type")
    elif damage == "mismatched_classification":
        dictionary.loc[dictionary["variable_id"].eq("zhvi"), "temporal_type"] = "flow"
    else:
        dictionary = pd.concat([dictionary, dictionary.iloc[[0]]], ignore_index=True)
    dictionary.to_csv(dictionary_path, index=False)
    assert not _variable_metadata_failures(config).empty
