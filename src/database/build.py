"""Create an integrated DuckDB database from Parquet and metadata."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from src.benchmark.acquisition import MANIFEST_COLUMNS
from src.common.config import ProjectConfig


UNION_CLEAN_TABLES = {
    "clean.fhfa_hpi",
    "clean.census_hvs_rental_vacancy",
}


def _clean_table_name(source_id: str, configured: str) -> str:
    if configured and configured not in UNION_CLEAN_TABLES:
        return configured
    return f"clean.{re.sub(r'[^a-z0-9_]', '_', source_id.lower())}"


def _clean_parquet_sources(config: ProjectConfig) -> dict[str, Path]:
    clean_dir = config.root / "data" / "clean"
    tables: dict[str, Path] = {}
    for source in config.selected_sources():
        if source.source_id == "redfin_zip_monthly":
            path = clean_dir / "redfin_zip_monthly_la_county.parquet"
        elif source.source_id.startswith("census_acs_5year_"):
            continue
        else:
            path = clean_dir / f"{source.source_id}.parquet"
        if path.exists():
            tables[_clean_table_name(source.source_id, source.destination_clean_table)] = path
    return tables


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
                    "provider_last_updated": None,
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
                    "revision_policy": source.revision_notes,
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
        "provider_last_updated",
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
        "migration_direction",
        "archive_format_version",
        "license_or_use_notes",
        "revision_notes",
        "revision_policy",
        "ingestion_status",
    ]
    result = pd.DataFrame(rows).reindex(columns=columns)
    result["provider_last_updated"] = result["provider_last_updated"].fillna(
        result["source_last_updated"]
    )
    result["revision_policy"] = result["revision_policy"].fillna(
        result["revision_notes"]
    )
    return result


def _select_existing_columns(
    connection: duckdb.DuckDBPyConnection, table_name: str, columns: list[str]
) -> str:
    existing = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }
    selected = [column for column in columns if column in existing]
    return ", ".join(f'"{column}"' for column in selected)


def _create_analytical_views(connection: duckdb.DuckDBPyConnection) -> None:
    homeowner_columns = [
        "date",
        "county_fips",
        "county_name",
        "state_fips",
        "zhvi",
        "median_sale_price",
        "active_inventory",
        "homes_sold",
        "new_listings",
        "pending_sales",
        "months_of_supply",
        "days_on_market",
        "price_drop_share",
        "sale_to_list_ratio",
        "mortgage_rate_30y_monthly_average",
        "mortgage_rate_30y_monthly_median",
        "mortgage_rate_30y_month_end",
        "mortgage_rate_30y_observation_count",
        "price_to_rent_ratio",
        "mortgage_payment_10pct_down",
        "mortgage_payment_to_rent_10pct_down",
        "mortgage_payment_20pct_down",
        "mortgage_payment_to_rent_20pct_down",
        "market_tightness_index",
        "market_tightness_components",
        "source_coverage_count",
        "panel_quality_flag",
    ]
    macro_columns = [
        "date",
        "county_fips",
        "state_fips",
        "unemployment_rate",
        "unemployment",
        "employment",
        "labor_force",
        "permits_total",
        "permits_1_unit",
        "permits_2_to_4_units",
        "permits_5_plus_units",
        "mortgage_rate_30y_monthly_average",
        "mortgage_rate_30y_monthly_median",
        "mortgage_rate_30y_month_end",
        "mortgage_rate_30y_observation_count",
        "source_coverage_count",
        "panel_quality_flag",
    ]
    homeowner_select = _select_existing_columns(
        connection, "analytics.county_monthly_panel", homeowner_columns
    )
    macro_select = _select_existing_columns(
        connection, "analytics.county_monthly_panel", macro_columns
    )
    connection.execute(
        "CREATE OR REPLACE VIEW analytics.homeowner_market_monthly AS "
        f"SELECT {homeowner_select} FROM analytics.county_monthly_panel"
    )
    connection.execute(
        "CREATE OR REPLACE VIEW analytics.macro_controls_monthly AS "
        f"SELECT {macro_select} FROM analytics.county_monthly_panel"
    )
    renter_columns = [
        "date",
        "county_fips",
        "county_name",
        "state_fips",
        "zori",
        "zori_growth_mom",
        "zori_growth_yoy",
        "permits_5_plus_units",
        "price_to_rent_ratio",
        "source_coverage_count",
        "panel_quality_flag",
    ]
    overall_columns = [
        "date",
        "county_fips",
        "county_name",
        "state_fips",
        "zhvi",
        "zori",
        "price_to_rent_ratio",
        "inventory_to_sales_ratio",
        "days_on_market",
        "price_drop_share",
        "market_tightness_index",
        "market_tightness_components",
        "source_coverage_count",
        "panel_quality_flag",
    ]
    renter_select = _select_existing_columns(
        connection, "analytics.county_monthly_panel", renter_columns
    )
    overall_select = _select_existing_columns(
        connection, "analytics.county_monthly_panel", overall_columns
    )
    connection.execute(
        "CREATE OR REPLACE VIEW analytics.renter_market_monthly AS "
        f"SELECT {renter_select} FROM analytics.county_monthly_panel"
    )
    connection.execute(
        "CREATE OR REPLACE VIEW analytics.overall_market_monthly AS "
        f"SELECT {overall_select} FROM analytics.county_monthly_panel"
    )


def _active_clean_tables(config: ProjectConfig) -> list[tuple[str, Path]]:
    tables: list[tuple[str, Path]] = []
    for source in config.sources:
        if not source.enabled:
            continue
        if source.source_id == "redfin_zip_monthly":
            path = config.root / "data" / "clean" / "redfin_zip_monthly_la_county.parquet"
            table_name = "redfin_zip_monthly"
        else:
            path = config.root / "data" / "clean" / f"{source.source_id}.parquet"
            table_name = re.sub(r"[^a-z0-9_]", "_", source.source_id.lower())
        if path.exists():
            tables.append((table_name, path))
    return tables


def _version_a_manifest(config: ProjectConfig) -> pd.DataFrame:
    path = config.root / "metadata" / "version_a_raw_file_manifest.csv"
    if not path.exists():
        return pd.DataFrame(
            {
                column: pd.Series(dtype="string")
                for column in MANIFEST_COLUMNS
            }
        )
    return (
        pd.read_csv(path, dtype="string")
        .reindex(columns=MANIFEST_COLUMNS)
        .astype("string")
    )


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
    for schema in ("metadata", "raw", "clean", "analytics", "quality"):
        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    connection.execute((config.root / "sql" / "create_schema.sql").read_text())
    frames = {
        "source_registry_frame": _source_registry(config),
        "version_a_manifest_frame": _version_a_manifest(config),
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
        "analytics.metro_monthly_controls": "metro_monthly_controls.parquet",
        "analytics.state_quarterly_panel": "state_quarterly_panel.parquet",
        "analytics.state_annual_controls": "state_annual_controls.parquet",
        "analytics.metro_quarterly_panel": "metro_quarterly_panel.parquet",
        "analytics.metro_quarterly_controls": "metro_quarterly_controls.parquet",
        "analytics.county_annual_controls": "county_annual_controls.parquet",
        "analytics.county_quarterly_controls": "county_quarterly_controls.parquet",
    }
    expected_analytics_names = {
        table.split(".", 1)[1] for table in parquet_tables
    } | {
        "homeowner_market_monthly",
        "renter_market_monthly",
        "overall_market_monthly",
        "macro_controls_monthly",
    }
    stale_analytics_objects = connection.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'analytics'
        """
    ).fetchall()
    for table_name, table_type in stale_analytics_objects:
        if table_name in expected_analytics_names:
            continue
        safe_name = str(table_name).replace('"', '""')
        if table_type == "VIEW":
            connection.execute(f'DROP VIEW IF EXISTS analytics."{safe_name}"')
        else:
            connection.execute(f'DROP TABLE IF EXISTS analytics."{safe_name}"')
    final_dir = config.root / "data" / "final"
    for table, filename in parquet_tables.items():
        path = (final_dir / filename).as_posix().replace("'", "''")
        connection.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{path}')"
        )
    stale_clean_tables = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'clean'
        """
    ).fetchall()
    for (table_name,) in stale_clean_tables:
        safe_name = str(table_name).replace('"', '""')
        connection.execute(f'DROP TABLE IF EXISTS clean."{safe_name}"')
    for name, path in _active_clean_tables(config):
        clean_path = path.as_posix().replace("'", "''")
        safe_name = name.replace('"', '""')
        connection.execute(
            f'CREATE OR REPLACE TABLE clean."{safe_name}" AS '
            f"SELECT * FROM read_parquet('{clean_path}')"
        )
    union_tables = {
        "clean.fhfa_hpi": [
            "clean.fhfa_hpi_state_purchase_only",
            "clean.fhfa_hpi_metro_purchase_only",
            "clean.fhfa_hpi_county_annual",
        ],
        "clean.census_hvs_rental_vacancy": [
            "clean.census_hvs_state_rental_vacancy",
            "clean.census_hvs_metro_rental_vacancy_2005_2014",
            "clean.census_hvs_metro_rental_vacancy_2015_2025",
            "clean.census_hvs_metro_rental_vacancy_2026",
        ],
    }
    for destination, source_tables in union_tables.items():
        existing_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_schema || '.' || table_name
                FROM information_schema.tables
                WHERE table_schema = 'clean'
                """
            ).fetchall()
        }
        source_tables = [table for table in source_tables if table in existing_tables]
        if not source_tables:
            continue
        union_sql = " UNION ALL BY NAME ".join(
            f"SELECT * FROM {table}" for table in source_tables
        )
        connection.execute(
            f"CREATE OR REPLACE TABLE {destination} AS {union_sql}"
        )
    connection.execute(
        "CREATE OR REPLACE TABLE raw.file_manifest AS "
        "SELECT * FROM metadata.source_registry WHERE source_filename IS NOT NULL"
    )
    _create_analytical_views(connection)
    connection.execute(
        "CREATE OR REPLACE TABLE raw.version_a_file_manifest AS "
        "SELECT * FROM version_a_manifest_frame"
    )
    benchmark_path = final_dir / "housing_finance_benchmark.parquet"
    if benchmark_path.exists():
        escaped_benchmark = benchmark_path.as_posix().replace("'", "''")
        connection.execute(
            "CREATE OR REPLACE TABLE analytics.housing_finance_benchmark AS "
            f"SELECT * FROM read_parquet('{escaped_benchmark}')"
        )
        connection.execute(
            "CREATE OR REPLACE VIEW analytics.v_la_version_a_benchmark AS "
            "SELECT * FROM analytics.housing_finance_benchmark "
            "WHERE county_fips = '06037'"
        )
        parquet_tables[
            "analytics.housing_finance_benchmark"
        ] = "housing_finance_benchmark.parquet"
    connection.execute(
        "CREATE OR REPLACE TABLE quality.qa_summary "
        "(check_id VARCHAR, severity VARCHAR, status VARCHAR, failure_count BIGINT, detail VARCHAR)"
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW analytics.homeowner_market_monthly AS
        SELECT
            date,
            county_fips,
            zhvi,
            zhvi_growth_mom,
            zhvi_growth_yoy,
            median_sale_price,
            active_inventory,
            months_of_supply,
            new_listings,
            homes_sold,
            days_on_market,
            price_drop_share,
            mortgage_rate_30y_monthly_average AS us_mortgage_rate_30y_monthly_average,
            permits_1_unit,
            mortgage_payment_10pct_down,
            mortgage_payment_20pct_down
        FROM analytics.county_monthly_panel
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW analytics.renter_market_monthly AS
        SELECT
            date,
            county_fips,
            zori,
            zori_growth_mom,
            zori_growth_yoy,
            price_to_rent_ratio,
            mortgage_payment_to_rent_10pct_down,
            mortgage_payment_to_rent_20pct_down,
            permits_5_plus_units
        FROM analytics.county_monthly_panel
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW analytics.overall_market_monthly AS
        SELECT
            date,
            county_fips,
            price_to_rent_ratio,
            inventory_to_sales_ratio,
            sales_to_inventory_ratio,
            market_tightness_index,
            market_tightness_components,
            days_on_market,
            price_drop_share,
            months_of_supply,
            mortgage_payment_to_rent_10pct_down,
            mortgage_payment_to_rent_20pct_down
        FROM analytics.county_monthly_panel
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW analytics.macro_controls_monthly AS
        SELECT
            c.date,
            c.unemployment_rate AS la_county_unemployment_rate_nsa,
            c.employment AS la_county_laus_employment_nsa,
            c.unemployment AS la_county_unemployment_nsa,
            c.labor_force AS la_county_labor_force_nsa,
            m.ces_total_nonfarm_employment_sa AS la_metro_total_nonfarm_employment_sa,
            m.ces_total_nonfarm_employment_growth_yoy AS la_metro_payroll_employment_growth_yoy,
            m.cpi_u_all_items_la_nsa AS la_area_cpi_u_all_items_nsa,
            m.cpi_inflation_yoy AS la_area_cpi_inflation_yoy,
            c.mortgage_rate_30y_monthly_average AS us_mortgage_rate_30y_monthly_average
        FROM analytics.county_monthly_panel c
        LEFT JOIN analytics.metro_monthly_controls m USING (date)
        """
    )
    counts = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in parquet_tables
    }
    counts["raw.version_a_file_manifest"] = connection.execute(
        "SELECT count(*) FROM raw.version_a_file_manifest"
    ).fetchone()[0]
    connection.close()
    logger.info("Built %s with %s", database_path, counts)
    return counts
