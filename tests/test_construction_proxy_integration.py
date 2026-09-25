from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.common.config import load_config
from src.database.build import build_database
from src.features.derived import (
    APARTMENT_COMPLETION_PROXY_COLUMNS,
    MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS,
    apartment_completion_proxies,
    multifamily_under_construction_proxies,
)
from src.harmonization.build import _add_construction_proxy_quality_flags
from src.reporting.catalog import IMPLEMENTATION, _proxy_methodology_assumptions


ROOT = Path(__file__).resolve().parents[1]
PROXY_COLUMNS = (
    *APARTMENT_COMPLETION_PROXY_COLUMNS.values(),
    *MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values(),
)
QUALITY_COLUMNS = tuple(f"{column}_quality_flag" for column in PROXY_COLUMNS)


def test_issue_4_and_5_configuration_is_exact_and_has_only_two_scenarios() -> None:
    construction = load_config(ROOT).pipeline["construction_proxies"]

    assert construction["input_variable_id"] == "permits_5_plus_units"
    assert construction["require_complete_lag_history"] is True
    assert construction["missing_value_policy"] == (
        "propagate_required_source_nulls"
    )
    assert set(construction["scenarios"]) == {"central", "slow"}

    expected = {
        "central": {
            "completion_variable_id": (
                "new_apartments_completed_proxy_central"
            ),
            "under_construction_variable_id": (
                "multifamily_under_construction_proxy_central"
            ),
            "lag_buckets": [(12, 17, 0.20), (18, 23, 0.55), (24, 30, 0.25)],
        },
        "slow": {
            "completion_variable_id": "new_apartments_completed_proxy_slow",
            "under_construction_variable_id": (
                "multifamily_under_construction_proxy_slow"
            ),
            "lag_buckets": [(18, 23, 0.20), (24, 29, 0.50), (30, 36, 0.30)],
        },
    }
    for scenario, expected_scenario in expected.items():
        actual = construction["scenarios"][scenario]
        assert actual["completion_variable_id"] == expected_scenario[
            "completion_variable_id"
        ]
        assert actual["under_construction_variable_id"] == expected_scenario[
            "under_construction_variable_id"
        ]
        buckets = [
            (
                bucket["start_month"],
                bucket["end_month"],
                bucket["completion_share"],
            )
            for bucket in actual["lag_buckets"]
        ]
        assert buckets == pytest.approx(expected_scenario["lag_buckets"])
        assert sum(bucket[2] for bucket in buckets) == pytest.approx(1.0)


def test_dictionary_and_methodology_lineage_cover_all_four_scenario_fields() -> None:
    config = load_config(ROOT)
    dictionary = {item["variable_id"]: item for item in config.variables}
    for variable_id in PROXY_COLUMNS:
        item = dictionary[variable_id]
        assert item["native_frequency"] == "monthly"
        assert item["final_frequency"] == "monthly"
        assert item["geography"] == "county"
        assert item["is_observed"] is False
        assert item["is_constructed"] is True
        assert item["is_proxy"] is True
        assert item["active_status"] == "Active"

    assumptions = _proxy_methodology_assumptions()
    construction = assumptions.loc[
        assumptions["methodology_id"].isin(
            {
                "bps_5_plus_distributed_completion_lag_v1",
                "bps_5_plus_stock_flow_pipeline_v1",
            }
        )
    ]
    assert set(construction["variable_id"]) == set(PROXY_COLUMNS)
    assert construction["methodology_status"].eq("active").all()

    transformations = pd.read_csv(
        ROOT / "metadata" / "transformation_log.csv", dtype=str
    )
    rows = transformations.loc[
        transformations["transformation_id"].isin(
            {
                "bps_5_plus_distributed_completion_lag_v1",
                "bps_5_plus_stock_flow_pipeline_v1",
            }
        )
    ]
    assert set(rows["transformation_id"]) == {
        "bps_5_plus_distributed_completion_lag_v1",
        "bps_5_plus_stock_flow_pipeline_v1",
    }
    assert rows["source_id"].eq("census_bps_county").all()
    assert rows["input_file"].eq(
        "data/clean/census_bps_county.parquet"
    ).all()
    assert rows["output_file"].eq(
        "data/final/county_monthly_panel.parquet"
    ).all()
    assert rows["checksum_sha256"].notna().all()
    assert rows["retrieved_at"].notna().all()


def test_catalog_truthfully_reports_panel_and_renter_view_placement() -> None:
    for label in (
        "New Apartments Completed (county time series)",
        "Multifamily Under Construction",
    ):
        implementation = IMPLEMENTATION[label]
        assert set(implementation["implemented_table"].split("|")) == {
            "analytics.county_monthly_panel",
            "analytics.renter_market_monthly",
        }
        assert "analytics.fact_housing_observation" not in implementation[
            "implemented_table"
        ]
        assert "intentionally not materialized as native rows" in implementation[
            "limitation"
        ]


def test_provider_nulls_receive_missing_history_flags_not_complete_flags() -> None:
    dates = pd.date_range("2010-01-31", periods=80, freq="ME")
    panel = pd.DataFrame(
        {
            "date": dates,
            "county_fips": "06037",
            "permits_5_plus_units": 10.0,
        }
    )
    panel.loc[10, "permits_5_plus_units"] = pd.NA
    panel = apartment_completion_proxies(panel)
    panel = multifamily_under_construction_proxies(panel)

    fact = pd.DataFrame(
        {
            "observation_date": dates,
            "geo_id": "COUNTY_06037",
            "variable_id": "permits_5_plus_units",
            "value": panel["permits_5_plus_units"],
            "final_frequency": "monthly",
            "quality_flag": "bps_pre_2022_sample_tally;includes_imputation",
            "source_vintage": "test_vintage",
            "retrieved_at": pd.Timestamp("2026-08-17", tz="UTC"),
        }
    )
    result = _add_construction_proxy_quality_flags(panel, fact)

    for proxy, quality in zip(PROXY_COLUMNS, QUALITY_COLUMNS, strict=True):
        incomplete = result[quality].str.contains(
            "insufficient_required_lag_history_or_source_null", na=False
        )
        complete = result[quality].str.contains(
            "complete_required_lag_history", na=False
        )
        assert incomplete.eq(result[proxy].isna()).all()
        assert complete.eq(result[proxy].notna()).all()


def _empty_parquet_with_source_schema(source: Path, destination: Path) -> None:
    schema = pq.ParquetFile(source).schema_arrow
    empty = pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in schema], schema=schema
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(empty, destination)


def test_database_build_exposes_values_and_quality_flags_without_fact_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "minimal_project"
    final = root / "data" / "final"
    source_final = ROOT / "data" / "final"
    filenames = (
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
    for filename in filenames:
        _empty_parquet_with_source_schema(
            source_final / filename, final / filename
        )

    county_schema = pq.ParquetFile(
        source_final / "county_monthly_panel.parquet"
    ).schema_arrow
    county_row = pq.ParquetFile(
        source_final / "county_monthly_panel.parquet"
    ).read_row_group(0).slice(0, 1)
    pq.write_table(
        county_row.cast(county_schema), final / "county_monthly_panel.parquet"
    )
    _empty_parquet_with_source_schema(
        ROOT / "data" / "clean" / "geography.parquet",
        root / "data" / "clean" / "geography.parquet",
    )
    (root / "sql").mkdir(parents=True)
    (root / "sql" / "create_schema.sql").write_text(
        (ROOT / "sql" / "create_schema.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    source_config = load_config(ROOT)
    config = replace(
        source_config,
        root=root,
        sources=(),
        pipeline={"database_path": "database/minimal.duckdb"},
    )
    database = root / "database" / "minimal.duckdb"
    build_database(config, database_path_override=database)

    with duckdb.connect(str(database), read_only=True) as connection:
        fact_proxy_rows = connection.execute(
            """
            SELECT count(*)
            FROM analytics.fact_housing_observation
            WHERE variable_id LIKE 'new_apartments_completed_proxy%'
               OR variable_id LIKE 'multifamily_under_construction_proxy%'
            """
        ).fetchone()[0]
        panel_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('analytics.county_monthly_panel')"
            ).fetchall()
        }
        renter_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('analytics.renter_market_monthly')"
            ).fetchall()
        }

    assert fact_proxy_rows == 0
    assert set((*PROXY_COLUMNS, *QUALITY_COLUMNS)) <= panel_columns
    assert set((*PROXY_COLUMNS, *QUALITY_COLUMNS)) <= renter_columns
    assert {
        column
        for column in renter_columns
        if column.startswith("new_apartments_completed_proxy")
        or column.startswith("multifamily_under_construction_proxy")
    } == set((*PROXY_COLUMNS, *QUALITY_COLUMNS))
