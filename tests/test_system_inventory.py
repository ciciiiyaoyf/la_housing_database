from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.common.config import ProjectConfig, SourceConfig
from src.common.variable_metadata import ENRICHMENT_COLUMNS
from src.reporting.system_inventory import (
    _estimate_inventory,
    _storage_inventory,
    _variable_inventory,
    build_system_inventory,
)


def _project_config(root: Path) -> ProjectConfig:
    source = SourceConfig(
        source_id="general_source",
        provider="Official Provider",
        dataset_name="General source",
        source_page_url="https://example.gov/general",
        download_url="https://example.gov/general.csv",
        native_frequency="monthly",
        geography_levels=("county",),
        file_format="csv",
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-08-24",
        sources=(source,),
        pipeline={"database_path": "database/housing.duckdb"},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def test_storage_inventory_excludes_folder_docs_but_counts_other_files(tmp_path: Path) -> None:
    for layer in ("data/raw", "data/restricted"):
        directory = tmp_path / layer / "provider"
        directory.mkdir(parents=True)
        (directory / "source_payload.bin").write_bytes(b"official payload")
        (directory / "provider_notes.md").write_bytes(b"source documentation")

    before = _storage_inventory(tmp_path).set_index("path")
    for layer in ("data/raw/", "data/restricted/"):
        assert before.loc[layer, "file_count"] == 2
        assert before.loc[layer, "bytes"] == len(b"official payloadsource documentation")
        directory = tmp_path / layer
        (directory / "README.md").write_text("Folder guide", encoding="utf-8")
        (directory / "provider" / "readme.md").write_text("Nested folder guide", encoding="utf-8")
        (directory / "provider" / ".gitkeep").write_bytes(b"placeholder")

    after = _storage_inventory(tmp_path).set_index("path")
    pd.testing.assert_frame_equal(after, before)


def _write_inventory_inputs(root: Path) -> None:
    metadata = root / "metadata"
    config = root / "config"
    metadata.mkdir(parents=True)
    config.mkdir(parents=True)
    (config / "version_a_benchmark.yaml").write_text(
        "sources:\n  version_a_source:\n    provider: Benchmark Provider\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "source_id": "general_source",
                "provider": "Official Provider",
                "product": "General source",
                "manual_download_required": False,
                "native_frequency": "monthly",
                "geography_level": "county",
            },
            {
                "source_id": "version_a_source",
                "provider": "Benchmark Provider",
                "product": "Benchmark source",
                "manual_download_required": False,
                "native_frequency": "annual",
                "geography_level": "california_counties",
            },
            {
                "source_id": "housing_finance_version_b_observations",
                "provider": "LA Housing Database",
                "product": "Internal derived product",
                "manual_download_required": False,
            },
        ]
    ).to_csv(metadata / "data_catalog.csv", index=False)
    common = {
        "definition": "Test variable",
        "unit": "count",
        "final_frequency": "monthly",
        "geography": "county",
        "transformation": "test",
        "is_proxy": False,
        "active_status": "Active",
    }
    pd.DataFrame(
        [
            {
                **common,
                "variable_id": "closing_cost_loan_3pct",
                "display_name": "Loan closing cost",
                "provider": "Constructed",
                "is_constructed": True,
            },
            {
                **common,
                "variable_id": "finance_test_benchmark",
                "display_name": "Version A test",
                "provider": "Benchmark Provider",
                "is_constructed": False,
            },
            {
                **common,
                "variable_id": "finance_test_monthly_hist",
                "display_name": "Version B test",
                "provider": "Constructed",
                "is_constructed": True,
            },
            {
                **common,
                "variable_id": "finance_test_monthly_rt",
                "display_name": "Version C test",
                "provider": "Constructed",
                "is_constructed": True,
            },
        ]
    ).to_csv(metadata / "data_dictionary.csv", index=False)


def _write_inventory_database(root: Path) -> None:
    database = root / "database"
    database.mkdir(parents=True)
    connection = duckdb.connect(str(database / "housing.duckdb"))
    try:
        connection.execute("CREATE SCHEMA analytics")
        connection.execute(
            """
            CREATE TABLE analytics.fact_housing_observation (
                variable_id VARCHAR,
                value DOUBLE,
                observation_date DATE,
                geo_id VARCHAR
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE analytics.housing_finance_benchmark AS
            SELECT 'finance_test_benchmark'::VARCHAR AS variable_id,
                   1.0::DOUBLE AS value,
                   DATE '2024-07-01' AS reference_date,
                   '06037'::VARCHAR AS county_fips,
                   'COUNTY_06037'::VARCHAR AS geo_id
            """
        )
        connection.execute(
            """
            CREATE TABLE analytics.housing_finance_monthly_hist AS
            SELECT 'finance_test_monthly_hist'::VARCHAR AS variable_id,
                   2.0::DOUBLE AS value,
                   DATE '2024-07-31' AS month,
                   '06037'::VARCHAR AS county_fips,
                   'COUNTY_06037'::VARCHAR AS geo_id
            """
        )
        connection.execute(
            """
            CREATE TABLE analytics.housing_finance_monthly_rt AS
            SELECT 'finance_test_monthly_rt'::VARCHAR AS variable_id,
                   3.0::DOUBLE AS value,
                   DATE '2024-07-31' AS month,
                   '06037'::VARCHAR AS county_fips,
                   'COUNTY_06037'::VARCHAR AS geo_id
            """
        )
        connection.execute(
            """
            CREATE TABLE analytics.homebuying_cost_scenarios AS
            SELECT DATE '2024-07-31' AS date,
                   'COUNTY_06037'::VARCHAR AS geo_id,
                   'closing_cost_loan_3pct'::VARCHAR
                       AS closing_cost_variable_id,
                   100.0::DOUBLE AS closing_cost_amount,
                   'down_payment_20pct'::VARCHAR AS down_payment_variable_id,
                   500.0::DOUBLE AS down_payment_amount
            """
        )
        connection.execute(
            """
            CREATE TABLE analytics.metro_quarterly_panel AS
            SELECT DATE '2024-03-31' AS quarter_end,
                   'METRO_TEST'::VARCHAR AS fhfa_geography_id
            UNION ALL
            SELECT DATE '2024-06-30', 'METRO_TEST'
            """
        )
        connection.execute(
            """
            CREATE VIEW analytics.v_la_version_a_benchmark AS
            SELECT * FROM analytics.housing_finance_benchmark
            """
        )
    finally:
        connection.close()


def test_build_system_inventory_tracks_live_relations_and_scenario_ids(
    tmp_path: Path,
) -> None:
    _write_inventory_inputs(tmp_path)
    _write_inventory_database(tmp_path)

    summary = build_system_inventory(_project_config(tmp_path))

    assert summary == {
        "active_physical_analytical_databases": 1,
        "provider_source_datasets": 2,
        "configured_versioned_variables": 4,
        "general_variables": 1,
        "version_a_variables": 1,
        "version_b_variables": 1,
        "version_c_variables": 1,
        "analytics_base_tables": 6,
        "analytics_views": 1,
        "final_parquet_files": 0,
        "estimate_or_constructed_variable_rows": 3,
        "gap_inventory_rows": 0,
        "latest_refresh_report_rows": 0,
    }
    generated = {
        "system_source_inventory.csv",
        "analytical_dataset_inventory.csv",
        "system_variable_inventory.csv",
        "estimate_inventory.csv",
        "system_storage_inventory.csv",
        "system_inventory_summary.json",
    }
    assert generated <= {path.name for path in (tmp_path / "metadata").iterdir()}

    sources = pd.read_csv(tmp_path / "metadata" / "system_source_inventory.csv")
    assert set(sources["source_id"]) == {"general_source", "version_a_source"}
    assert sources.set_index("source_id").loc[
        "version_a_source", "manual_intervention"
    ]

    variables = pd.read_csv(
        tmp_path / "metadata" / "system_variable_inventory.csv"
    ).set_index("variable_id")
    closing_cost = variables.loc["closing_cost_loan_3pct"]
    assert closing_cost["materialization_status"] == "Available with non-null data"
    assert closing_cost["primary_nonnull_count"] == 1
    assert (
        closing_cost["materialized_locations"]
        == "analytics.homebuying_cost_scenarios"
    )

    analytical = pd.read_csv(
        tmp_path / "metadata" / "analytical_dataset_inventory.csv"
    ).set_index("duckdb_object")
    metro = analytical.loc["analytics.metro_quarterly_panel"]
    assert metro["date_key"] == "quarter_end"
    assert metro["first_date"] == "2024-03-31"
    assert metro["latest_date"] == "2024-06-30"
    assert metro["geography_key"] == "fhfa_geography_id"
    assert metro["geography_count"] == 1
    assert (
        analytical.loc["analytics.v_la_version_a_benchmark", "geography"]
        == "Los Angeles County"
    )

    payload = json.loads(
        (tmp_path / "metadata" / "system_inventory_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["generated_from"]["database"] == "database/housing.duckdb"


def test_estimate_inventory_accepts_provider_constructed_rows() -> None:
    variables = pd.DataFrame(
        [
            {
                "variable_id": "market_tightness_index",
                "system_layer": "General",
                "provider": "Constructed",
                "is_constructed": False,
                "is_proxy": False,
                "display_name": "Market Tightness Index",
                "definition": "Mean oriented z-score",
                "final_frequency": "monthly",
                "geography": "county",
                "unit": "standard_deviation_index",
                "transformation": "oriented z-score mean",
                "materialization_status": "Available with non-null data",
                "primary_nonnull_count": 12,
                "latest_materialized_date": "2026-05-31",
                "materialized_locations": "analytics.county_monthly_panel",
            }
        ]
    )

    result = _estimate_inventory(variables)

    assert result["variable_id"].tolist() == ["market_tightness_index"]
    assert result.loc[0, "estimate_family"] == "Market tightness"
    assert "Full-history z-score" in result.loc[0, "important_limitation"]


@pytest.mark.parametrize("native_present", [True, False])
def test_nine_constructions_does_not_inflate_native_variable_coverage(
    tmp_path: Path, native_present: bool,
) -> None:
    _write_inventory_inputs(tmp_path)
    _write_inventory_database(tmp_path)
    dictionary_path = tmp_path / "metadata" / "data_dictionary.csv"
    dictionary = pd.read_csv(dictionary_path)
    regime = dictionary.iloc[0].to_dict()
    regime.update(variable_id="zip_market_regime_id", final_frequency="quarterly")
    pd.concat([dictionary, pd.DataFrame([regime])], ignore_index=True).to_csv(
        dictionary_path, index=False,
    )
    with duckdb.connect(str(tmp_path / "database" / "housing.duckdb")) as connection:
        connection.execute("""
            CREATE TABLE analytics.zip_market_regime_quarterly AS
            SELECT DATE '2026-03-31' AS quarter_end, 1::BIGINT AS zip_market_regime_id
            UNION ALL SELECT DATE '2026-06-30', NULL
        """)
        connection.execute("""
            CREATE TABLE analytics.nine_constructions AS
            SELECT quarter_end AS date, zip_market_regime_id,
                   'zip_market_regime_id'::VARCHAR AS applicable_variable_ids
            FROM analytics.zip_market_regime_quarterly
            UNION ALL SELECT DATE '1990-01-31', NULL, 'unemployment_rate'
            UNION ALL SELECT DATE '2026-08-31', NULL, 'zhvi_momentum_yoy_log_pct'
        """)
        if not native_present:
            connection.execute("DROP TABLE analytics.zip_market_regime_quarterly")
        result = _variable_inventory(tmp_path, connection).set_index("variable_id")
    row = result.loc["zip_market_regime_id"]
    assert row.primary_row_count == 2  # Includes the applicable missing classification.
    assert row.primary_nonnull_count == 1
    assert row.first_materialized_date == "2026-03-31"
    assert row.latest_materialized_date == "2026-06-30"
    locations = set(row.materialized_locations.split("|"))
    assert "analytics.nine_constructions" in locations
    assert ("analytics.zip_market_regime_quarterly" in locations) == native_present


def test_estimate_inventory_preserves_available_variable_metadata_and_order() -> None:
    base = pd.DataFrame([
        {
            "variable_id": "other_proxy", "system_layer": "General",
            "provider": "Provider", "is_constructed": False, "is_proxy": True,
            "unit": "current_dollars", "primary_nonnull_count": 8,
        },
        {
            "variable_id": "observed_only", "system_layer": "General",
            "provider": "Provider", "is_constructed": False, "is_proxy": False,
            "unit": "count", "primary_nonnull_count": 11,
        },
        {
            "variable_id": "mortgage_payment", "system_layer": "General",
            "provider": "Constructed", "is_constructed": True, "is_proxy": False,
            "unit": "USD/month", "primary_nonnull_count": 12,
        },
    ])
    minimal = _estimate_inventory(base)
    enriched = base.assign(
        unit_original=base["unit"],
        unit_simplified=["USD", "count", "USD/month"],
        temporal_type=["stock", "flow", "period_statistic"],
        temporal_type_note=["Outstanding balance", "Period count", "Modeled monthly payment"],
        monetary_basis=["nominal", "not_applicable", "not_specified_in_unit"],
        unit_simplification_note=["Label only", "Unchanged", "Unchanged"],
        metadata_source=["configuration", "workbook", "configuration"],
        workbook_temporal_type=["", "flow", ""],
        workbook_temporal_type_note=["", "Period count", ""],
        metadata_review_note=["Added proxy", "", "Added modeled payment"],
    )
    source_copy = enriched.copy(deep=True)
    result = _estimate_inventory(enriched)
    pd.testing.assert_frame_equal(result[minimal.columns], minimal)
    pd.testing.assert_frame_equal(enriched, source_copy)
    metadata_columns = ["unit_original", *ENRICHMENT_COLUMNS]
    expected = enriched.set_index("variable_id").loc[
        result["variable_id"], metadata_columns
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(result[metadata_columns], expected)
    assert "observed_only" not in result["variable_id"].tolist()

    # Small projects may provide only a subset of labels and need no registry file.
    partial = _estimate_inventory(enriched.drop(columns=list(ENRICHMENT_COLUMNS[1:])))
    assert "unit_simplified" in partial
    assert "temporal_type" not in partial
    pd.testing.assert_frame_equal(partial[minimal.columns], minimal)
