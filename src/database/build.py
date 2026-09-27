"""Create an integrated DuckDB database from Parquet and metadata."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from src.benchmark.acquisition import MANIFEST_COLUMNS
from src.common.config import ProjectConfig
from src.common.variable_metadata import enrich_variable_metadata
from src.database.variable_metadata import register_variable_metadata_views
from src.database.nine_constructions import register_nine_constructions_dictionary
from src.harmonization.nine_constructions import build_nine_constructions
from src.database.housing_finance import (
    VERSION_B_LINEAGE,
    VERSION_B_OBSERVATIONS,
    VERSION_C_LINEAGE,
    VERSION_C_OBSERVATIONS,
    register_housing_finance_versions,
)


UNION_CLEAN_TABLES = {
    "clean.fhfa_hpi",
    "clean.census_hvs_rental_vacancy",
}

HUD_USPS_SOURCE_ID = "hud_usps_ncwm_quarterly"
HUD_USPS_RESTRICTED_CLEAN_TABLES = {
    "hud_usps_ca_county_quarterly": "ca_county_quarterly.parquet",
    "hud_usps_la_tract_2020_quarterly": (
        "la_tract_2020_quarterly.parquet"
    ),
    "hud_usps_la_postal_zip_county_part_quarterly": (
        "la_postal_zip_county_part_quarterly.parquet"
    ),
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
                    "request_query": None,
                    "artifact_role": None,
                    "provider_vintage": None,
                    "provider_release_date": None,
                    "provider_release_date_status": None,
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
        "request_query",
        "artifact_role",
        "provider_vintage",
        "provider_release_date",
        "provider_release_date_status",
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
        "new_apartments_completed_proxy_central",
        "new_apartments_completed_proxy_slow",
        "multifamily_under_construction_proxy_central",
        "multifamily_under_construction_proxy_slow",
        "new_apartments_completed_proxy_central_quality_flag",
        "new_apartments_completed_proxy_slow_quality_flag",
        "multifamily_under_construction_proxy_central_quality_flag",
        "multifamily_under_construction_proxy_slow_quality_flag",
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


def _hud_usps_restricted_clean_tables(
    config: ProjectConfig,
) -> list[tuple[str, Path]]:
    """Return native-quarterly restricted HUD products as separate tables."""

    source_is_active = any(
        source.enabled and source.source_id == HUD_USPS_SOURCE_ID
        for source in config.sources
    )
    if not source_is_active:
        return []
    restricted_dir = (
        config.root / "data" / "restricted" / "hud_usps" / "active"
    )
    tables = [
        (table_name, restricted_dir / filename)
        for table_name, filename in HUD_USPS_RESTRICTED_CLEAN_TABLES.items()
    ]
    existing = [(name, path) for name, path in tables if path.exists()]
    if not existing:
        return []
    if len(existing) != len(tables):
        missing = [path.name for _, path in tables if not path.exists()]
        raise FileNotFoundError(
            "HUD-USPS restricted clean products are incomplete: "
            + ", ".join(missing)
        )
    return tables


def _register_clean_parquet_tables(
    connection: duckdb.DuckDBPyConnection,
    tables: list[tuple[str, Path]],
) -> None:
    for name, path in tables:
        clean_path = path.as_posix().replace("'", "''")
        safe_name = name.replace('"', '""')
        connection.execute(
            f'CREATE OR REPLACE TABLE clean."{safe_name}" AS '
            f"SELECT * FROM read_parquet('{clean_path}')"
        )


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
    if (config.root / "config" / "nine_constructions.yaml").is_file():
        build_nine_constructions(config)
    for schema in ("metadata", "raw", "clean", "analytics", "quality"):
        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    connection.execute((config.root / "sql" / "create_schema.sql").read_text())
    global_dictionary_path = config.root / "metadata" / "data_dictionary.csv"
    variable_dictionary_frame = (
        pd.read_csv(global_dictionary_path)
        if global_dictionary_path.is_file()
        else pd.DataFrame(config.variables)
    )
    if (config.root / "config" / "variable_metadata.csv").exists():
        variable_dictionary_frame = enrich_variable_metadata(variable_dictionary_frame, config.root)
    frames = {
        "source_registry_frame": _source_registry(config),
        "version_a_manifest_frame": _version_a_manifest(config),
        "variable_dictionary_frame": variable_dictionary_frame,
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
        "analytics.homebuying_cost_scenarios": "homebuying_cost_scenarios.parquet",
        "analytics.metro_monthly_controls": "metro_monthly_controls.parquet",
        "analytics.state_monthly_controls": "state_monthly_controls.parquet",
        "analytics.state_quarterly_panel": "state_quarterly_panel.parquet",
        "analytics.state_annual_controls": "state_annual_controls.parquet",
        "analytics.metro_quarterly_panel": "metro_quarterly_panel.parquet",
        "analytics.metro_quarterly_controls": "metro_quarterly_controls.parquet",
        "analytics.county_annual_controls": "county_annual_controls.parquet",
        "analytics.zcta_annual_controls": "zcta_annual_controls.parquet",
        "analytics.coc_annual_controls": "coc_annual_controls.parquet",
        "analytics.county_quarterly_controls": "county_quarterly_controls.parquet",
    }
    optional_parquet_tables = {
        "analytics.nine_constructions": "nine_constructions.parquet",
        "analytics.macro_financial_monthly": "macro_financial_monthly.parquet",
        "analytics.comparable_county_monthly_analytics": (
            "comparable_county_monthly_analytics.parquet"
        ),
        "analytics.zip_market_regime_quarterly": (
            "zip_market_regime_quarterly.parquet"
        ),
        "analytics.la_zip_market_regime_summary_quarterly": (
            "la_zip_market_regime_summary_quarterly.parquet"
        ),
        "analytics.la_zip_market_regime_transitions_quarterly": (
            "la_zip_market_regime_transitions_quarterly.parquet"
        ),
    }
    final_dir = config.root / "data" / "final"
    parquet_tables.update(
        {
            table: filename
            for table, filename in optional_parquet_tables.items()
            if (final_dir / filename).exists()
        }
    )
    comparable_views = {
        "county_housing_market_heat_monthly",
        "la_relative_market_performance_monthly",
    }
    expected_analytics_names = {
        table.split(".", 1)[1] for table in parquet_tables
    } | {
        "homeowner_market_monthly",
        "renter_market_monthly",
        "overall_market_monthly",
        "macro_controls_monthly",
    } | (
        comparable_views
        if "analytics.comparable_county_monthly_analytics" in parquet_tables
        else set()
    )
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
    _register_clean_parquet_tables(
        connection,
        [
            *_active_clean_tables(config),
            *_hud_usps_restricted_clean_tables(config),
        ],
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
    if "analytics.nine_constructions" in parquet_tables:
        register_nine_constructions_dictionary(connection, config.root)
    if "analytics.comparable_county_monthly_analytics" in parquet_tables:
        connection.execute(
            """
            CREATE OR REPLACE VIEW analytics.county_housing_market_heat_monthly AS
            SELECT
                date,
                county_fips,
                county_name,
                zhvi_momentum_3m_annualized_log_pct,
                market_tightness_index_3c_fixed,
                county_housing_market_heat_index,
                county_housing_market_heat_rank,
                county_housing_market_heat_count,
                latest_source_vintage,
                normalization_assumption_status
            FROM analytics.comparable_county_monthly_analytics
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW analytics.la_relative_market_performance_monthly AS
            SELECT
                date,
                county_fips,
                la_relative_zhvi_momentum_yoy_log_pct,
                la_relative_zhvi_momentum_3m_annualized_log_pct,
                la_relative_zori_momentum_yoy_log_pct,
                la_relative_zori_momentum_3m_annualized_log_pct,
                la_relative_market_tightness_index_3c_fixed,
                la_relative_price_to_rent_ratio_zillow,
                la_relative_required_peer_count,
                latest_source_vintage
            FROM analytics.comparable_county_monthly_analytics
            WHERE county_fips = '06037'
            """
        )
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
    monthly_finance_files = register_housing_finance_versions(
        connection,
        config.root,
    )
    connection.execute(
        "CREATE OR REPLACE TABLE quality.qa_summary "
        "(check_id VARCHAR, severity VARCHAR, status VARCHAR, failure_count BIGINT, detail VARCHAR)"
    )
    raw_completeness_path = (
        config.root / "output" / "qa" / "raw_repository_completeness.csv"
    )
    if raw_completeness_path.exists():
        raw_completeness = pd.read_csv(raw_completeness_path, dtype=str)
        connection.register("raw_repository_completeness_frame", raw_completeness)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.raw_repository_completeness AS "
            "SELECT * FROM raw_repository_completeness_frame"
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
            price_to_rent_ratio_zillow,
            implied_gross_rent_yield,
            mortgage_payment_to_rent_10pct_down,
            mortgage_payment_to_rent_20pct_down,
            permits_5_plus_units,
            new_apartments_completed_proxy_central,
            new_apartments_completed_proxy_slow,
            multifamily_under_construction_proxy_central,
            multifamily_under_construction_proxy_slow,
            new_apartments_completed_proxy_central_quality_flag,
            new_apartments_completed_proxy_slow_quality_flag,
            multifamily_under_construction_proxy_central_quality_flag,
            multifamily_under_construction_proxy_slow_quality_flag
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
            price_to_rent_ratio_zillow,
            implied_gross_rent_yield,
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
    monthly_finance_tables = {
        VERSION_B_OBSERVATIONS: "version_b_observation_files",
        VERSION_B_LINEAGE: "version_b_lineage_files",
        VERSION_C_OBSERVATIONS: "version_c_observation_files",
        VERSION_C_LINEAGE: "version_c_lineage_files",
    }
    counts.update(
        {
            table: connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table, file_count_key in monthly_finance_tables.items()
            if monthly_finance_files[file_count_key]
        }
    )
    counts["raw.version_a_file_manifest"] = connection.execute(
        "SELECT count(*) FROM raw.version_a_file_manifest"
    ).fetchone()[0]
    register_variable_metadata_views(connection)
    connection.close()
    logger.info("Built %s with %s", database_path, counts)
    return counts
