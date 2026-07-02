"""Generate the data catalog and raw-repository completeness deliverables."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.common.config import ProjectConfig

CATALOG_COLUMNS = [
    "Source",
    "Frequency",
    "Geography",
    "Key variables",
    "source_id",
    "provider",
    "product",
    "official_source_page",
    "exact_download_url_or_api",
    "access_method",
    "active_status",
    "manual_download_required",
    "raw_file_format",
    "raw_file_path",
    "original_filename",
    "retrieval_date",
    "provider_vintage",
    "checksum_sha256",
    "native_frequency",
    "analytical_frequency",
    "geography_level",
    "geography_name",
    "geography_id",
    "start_date",
    "end_date",
    "row_count",
    "variable_ids",
    "seasonal_adjustment",
    "smoothing",
    "units",
    "destination_clean_table",
    "destination_analytical_table",
    "update_command",
    "missing_value_policy",
    "revision_policy",
    "known_limitations",
    "license_or_use_notes",
    "last_verified",
    "notes",
]

UNAVAILABLE_ROWS = [
    {
        "Source": "Rental listing inventory",
        "Frequency": "Unknown",
        "Geography": "Los Angeles County or ZIP",
        "Key variables": "rental_inventory",
        "source_id": "unavailable_rental_inventory",
        "provider": "No verified official public source",
        "product": "Rental listing inventory",
        "active_status": "Missing — no verified public source",
        "known_limitations": (
            "ZORI is a rent index, not inventory. A documented public listing "
            "universe, stable geography, history, and revision policy are required."
        ),
    },
    {
        "Source": "Occupied-apartment growth",
        "Frequency": "Annual at best",
        "Geography": "Los Angeles County or metro",
        "Key variables": "occupied_apartment_growth",
        "source_id": "proxy_occupied_apartment_growth",
        "provider": "No verified exact public series",
        "product": "Occupied-apartment growth",
        "active_status": "Proxy only",
        "known_limitations": (
            "ACS renter-occupied units include non-apartment rentals and cannot "
            "be relabeled as occupied-apartment growth."
        ),
    },
    {
        "Source": "Treasury rate and mortgage spread",
        "Frequency": "Daily or weekly",
        "Geography": "United States",
        "Key variables": "treasury_rate|mortgage_spread",
        "source_id": "deferred_treasury_mortgage_spread",
        "provider": "U.S. Treasury or Federal Reserve",
        "product": "Treasury benchmark and constructed mortgage spread",
        "active_status": "Deferred",
        "known_limitations": "Optional control outside the current requested core.",
    },
]


def _manifest(config: ProjectConfig) -> pd.DataFrame:
    path = config.root / "metadata" / "raw_file_manifest.csv"
    frame = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()
    if not frame.empty:
        frame["_retrieved"] = pd.to_datetime(
            frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
        )
    return frame


def build_raw_repository_completeness(
    config: ProjectConfig,
) -> pd.DataFrame:
    manifest = _manifest(config)
    database_path = config.root / config.pipeline["database_path"]
    registry_ids: set[str] = set()
    if database_path.exists():
        connection = duckdb.connect(str(database_path), read_only=True)
        registry_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_id FROM metadata.source_registry"
            ).fetchall()
        }
        connection.close()
    rows: list[dict] = []
    for source in config.sources:
        matches = (
            manifest.loc[manifest["source_id"].eq(source.source_id)]
            if not manifest.empty
            else pd.DataFrame()
        )
        paths = (
            [
                config.root / value
                for value in matches.get(
                    "relative_path", pd.Series(dtype=str)
                ).dropna()
            ]
            if not matches.empty
            else []
        )
        checks = {
            "raw_snapshot_present": bool(paths)
            and any(path.is_file() for path in paths),
            "manifest_present": not matches.empty,
            "registry_present": source.source_id in registry_ids,
            "checksum_present": not matches.empty
            and matches["checksum_sha256"].fillna("").str.len().eq(64).any(),
            "retrieval_timestamp_present": not matches.empty
            and pd.to_datetime(
                matches["retrieved_at"],
                utc=True,
                errors="coerce",
                format="mixed",
            )
            .notna()
            .any(),
            "source_url_present": bool(
                source.source_page_url and source.download_url
            ),
            "frequency_present": bool(source.native_frequency),
            "geography_present": bool(source.geography_levels),
            "key_variables_present": bool(source.key_variables),
        }
        issues = [name for name, passed in checks.items() if not passed]
        rows.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "active_status": (
                    "ACTIVE_MANUAL" if not source.auto_download else "ACTIVE"
                ),
                **checks,
                "completeness_status": "PASS" if not issues else "FAIL",
                "issue": "|".join(issues),
            }
        )
    result = pd.DataFrame(rows)
    output = config.root / "output" / "qa" / "raw_repository_completeness.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("raw_completeness_frame", result)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.raw_repository_completeness AS "
            "SELECT * FROM raw_completeness_frame"
        )
        connection.close()
    return result


def _coverage(config: ProjectConfig) -> pd.DataFrame:
    database_path = config.root / config.pipeline["database_path"]
    if not database_path.exists():
        return pd.DataFrame()
    connection = duckdb.connect(str(database_path), read_only=True)
    frame = connection.execute(
        """
        SELECT
            source_filename,
            min(observation_date) AS start_date,
            max(observation_date) AS end_date,
            count(*) AS row_count,
            string_agg(DISTINCT variable_id, '|' ORDER BY variable_id)
                AS variable_ids,
            string_agg(DISTINCT final_frequency, '|' ORDER BY final_frequency)
                AS analytical_frequency,
            string_agg(DISTINCT unit, '|' ORDER BY unit) AS units
        FROM analytics.fact_housing_observation
        GROUP BY source_filename
        """
    ).df()
    connection.close()
    return frame


def _latest_manifest_by_source(manifest: pd.DataFrame) -> pd.DataFrame:
    if manifest.empty:
        return manifest
    return (
        manifest.sort_values("_retrieved")
        .groupby("source_id", as_index=False)
        .tail(1)
        .set_index("source_id")
    )


def _variable_text(config: ProjectConfig, variable_ids: tuple[str, ...]) -> dict:
    variables = {
        str(item["variable_id"]): item for item in config.variables
    }
    selected = [
        variables[variable_id]
        for variable_id in variable_ids
        if variable_id in variables
    ]
    return {
        "seasonal_adjustment": "|".join(
            sorted(
                {
                    str(item.get("seasonal_adjustment", ""))
                    for item in selected
                    if item.get("seasonal_adjustment")
                }
            )
        ),
        "smoothing": "|".join(
            sorted(
                {
                    str(item.get("smoothing", ""))
                    for item in selected
                    if item.get("smoothing")
                }
            )
        ),
        "units": "|".join(
            sorted(
                {
                    str(item.get("unit", ""))
                    for item in selected
                    if item.get("unit")
                }
            )
        ),
        "missing_value_policy": "|".join(
            sorted(
                {
                    str(item.get("missing_value_policy", ""))
                    for item in selected
                    if item.get("missing_value_policy")
                }
            )
        ),
    }


def _geography_description(source_id: str, levels: tuple[str, ...]) -> tuple[str, str]:
    if "zillow_zip" in source_id or source_id == "redfin_zip_monthly":
        return "Los Angeles County postal ZIPs", "POSTAL_ZIP"
    if source_id.startswith("census_acs_"):
        return "Los Angeles County and selected ZCTAs", "06037|ZCTA"
    if "county" in levels:
        return "Los Angeles County", "06037"
    if "state" in levels:
        return "California", "06"
    if "metro" in levels:
        if "fhfa" in source_id:
            return "Los Angeles-Long Beach-Glendale Metropolitan Division", "31084"
        return "Los Angeles published metro/area", "31080|S49A"
    if "national" in levels:
        return "United States", "US"
    return "|".join(levels), ""


def _catalog_frame(config: ProjectConfig) -> pd.DataFrame:
    manifest = _manifest(config)
    latest = _latest_manifest_by_source(manifest)
    coverage = _coverage(config)
    rows: list[dict] = []
    for source in config.sources:
        raw = (
            latest.loc[source.source_id].to_dict()
            if not latest.empty and source.source_id in latest.index
            else {}
        )
        source_files = (
            set(
                manifest.loc[
                    manifest["source_id"].eq(source.source_id),
                    "source_filename",
                ].dropna()
            )
            if not manifest.empty
            else set()
        )
        source_coverage = (
            coverage.loc[coverage["source_filename"].isin(source_files)]
            if not coverage.empty
            else pd.DataFrame()
        )
        variable_ids = tuple(source.key_variables)
        variable_text = _variable_text(config, variable_ids)
        geography_name, geography_id = _geography_description(
            source.source_id, source.geography_levels
        )
        if source_coverage.empty:
            start_date = end_date = analytical_frequency = ""
            row_count = 0
            coverage_units = ""
        else:
            start_date = source_coverage["start_date"].min()
            end_date = source_coverage["end_date"].max()
            row_count = int(source_coverage["row_count"].sum())
            analytical_frequency = "|".join(
                sorted(
                    {
                        value
                        for text in source_coverage[
                            "analytical_frequency"
                        ].dropna()
                        for value in str(text).split("|")
                    }
                )
            )
            coverage_units = "|".join(
                sorted(
                    {
                        value
                        for text in source_coverage["units"].dropna()
                        for value in str(text).split("|")
                    }
                )
            )
        command = (
            "python run_pipeline.py --stage download --source "
            f"{source.source_id} --force-download"
            if source.auto_download
            else "python run_pipeline.py --stage download --source "
            f"{source.source_id} --local-file PATH_TO_OFFICIAL_FILE"
        )
        if source.source_id.startswith("census_acs_5year_"):
            provider_vintage = "2024 ACS 5-Year (2020-2024)"
        elif end_date:
            provider_vintage = f"data through {str(end_date)[:10]}"
        else:
            provider_vintage = raw.get("data_vintage", "")
        rows.append(
            {
                "Source": source.dataset_name,
                "Frequency": source.native_frequency,
                "Geography": geography_name,
                "Key variables": "|".join(variable_ids),
                "source_id": source.source_id,
                "provider": source.provider,
                "product": source.dataset_name,
                "official_source_page": source.source_page_url,
                "exact_download_url_or_api": source.download_url,
                "access_method": source.access_method,
                "active_status": "Active",
                "manual_download_required": not source.auto_download,
                "raw_file_format": source.file_format,
                "raw_file_path": raw.get("relative_path", ""),
                "original_filename": raw.get("original_filename", ""),
                "retrieval_date": raw.get("retrieved_at", ""),
                "provider_vintage": provider_vintage,
                "checksum_sha256": raw.get("checksum_sha256", ""),
                "native_frequency": source.native_frequency,
                "analytical_frequency": (
                    analytical_frequency or source.native_frequency
                ),
                "geography_level": "|".join(source.geography_levels),
                "geography_name": geography_name,
                "geography_id": geography_id,
                "start_date": start_date,
                "end_date": end_date,
                "row_count": row_count,
                "variable_ids": "|".join(variable_ids),
                "seasonal_adjustment": variable_text["seasonal_adjustment"],
                "smoothing": variable_text["smoothing"],
                "units": coverage_units or variable_text["units"],
                "destination_clean_table": source.destination_clean_table
                or f"clean.{source.source_id}",
                "destination_analytical_table": (
                    source.destination_analytical_table
                ),
                "update_command": command,
                "missing_value_policy": variable_text[
                    "missing_value_policy"
                ],
                "revision_policy": source.revision_notes,
                "known_limitations": (
                    source.methodology_notes or source.revision_notes
                ),
                "license_or_use_notes": source.license_or_use_notes,
                "last_verified": config.verified_at,
                "notes": (
                    "Identifiers remain strings; raw vintages are immutable."
                ),
            }
        )
    for unavailable in UNAVAILABLE_ROWS:
        row = {column: "" for column in CATALOG_COLUMNS}
        row.update(unavailable)
        row["last_verified"] = config.verified_at
        row["notes"] = "No placeholder observations are created."
        rows.append(row)
    return pd.DataFrame(rows).reindex(columns=CATALOG_COLUMNS)


def _refresh_gap_analysis(config: ProjectConfig) -> pd.DataFrame:
    path = config.root / "metadata" / "data_gap_analysis.csv"
    gap = pd.read_csv(path, dtype=str).fillna("")
    updates = {
        "FHFA HPI": (
            "Implemented",
            "analytics.state_quarterly_panel|analytics.metro_quarterly_panel|analytics.county_annual_structural",
            "FHFA HPI columns",
            "1975-12-31",
            "2026-03-31",
        ),
        "price-to-income": (
            "Constructed variable",
            "analytics.county_annual_structural|analytics.zcta_annual_structural",
            "annual_price_to_income_ratio",
            "2024-12-31",
            "2024-12-31",
        ),
        "housing turnover": (
            "Constructed variable",
            "analytics.county_annual_structural",
            "sales_to_housing_stock_turnover",
            "2024-12-31",
            "2024-12-31",
        ),
        "rental vacancy": (
            "Implemented",
            "analytics.state_quarterly_panel|analytics.metro_quarterly_panel",
            "hvs_rental_vacancy_rate_ca|hvs_rental_vacancy_rate_la_metro",
            "2005-03-31",
            "2026-03-31",
        ),
        "renter household counts": (
            "Implemented",
            "analytics.county_annual_structural|analytics.zcta_annual_structural",
            "acs_households",
            "2024-12-31",
            "2024-12-31",
        ),
        "renter-occupied units": (
            "Implemented",
            "analytics.county_annual_structural|analytics.zcta_annual_structural",
            "acs_renter_occupied_units",
            "2024-12-31",
            "2024-12-31",
        ),
        "rent-to-income": (
            "Constructed variable",
            "analytics.county_annual_structural|analytics.zcta_annual_structural",
            "annual_rent_to_income_ratio",
            "2024-12-31",
            "2024-12-31",
        ),
        "CES payroll employment": (
            "Implemented",
            "analytics.metro_monthly_controls",
            "ces_total_nonfarm_employment_sa",
            "2005-01-31",
            "2026-05-31",
        ),
        "personal income": (
            "Implemented",
            "analytics.state_quarterly_panel|analytics.county_annual_structural",
            "bea_ca_personal_income_q_sa|bea_la_county_personal_income_annual",
            "1948-03-31",
            "2026-03-31",
        ),
        "CPI / inflation": (
            "Implemented",
            "analytics.metro_monthly_controls",
            "cpi_u_all_items_la_nsa|cpi_inflation_yoy",
            "1914-12-31",
            "2026-05-31",
        ),
    }
    for variable_name, values in updates.items():
        matches = gap["variable_name"].eq(variable_name)
        (
            status,
            table,
            column,
            start,
            end,
        ) = values
        gap.loc[matches, "implementation_status"] = status
        gap.loc[matches, "current_database_table"] = table
        gap.loc[matches, "current_database_column"] = column
        gap.loc[matches, "available_start_date"] = start
        gap.loc[matches, "available_end_date"] = end
        gap.loc[matches, "recommended_action"] = (
            "Retain at native frequency and rebuild through the pipeline."
        )
    affordability = gap["variable_name"].eq("affordability metrics")
    gap.loc[affordability, "implementation_status"] = "Implemented"
    gap.loc[affordability, "current_database_table"] = (
        "analytics.county_monthly_panel|analytics.county_annual_structural"
    )
    gap.loc[affordability, "current_database_column"] = (
        "price_to_rent_ratio|mortgage_payment_to_rent_10pct_down|"
        "mortgage_payment_to_rent_20pct_down|annual_price_to_income_ratio|"
        "annual_rent_to_income_ratio"
    )
    gap.to_csv(path, index=False)
    return gap


def _format_workbook(path: Path, build_version: str) -> None:
    workbook = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    status_colors = {
        "Active": "C6E0B4",
        "Implemented": "C6E0B4",
        "Constructed variable": "D9EAF7",
        "Partially implemented": "FFF2CC",
        "Missing — no verified public source": "F4CCCC",
        "Deferred": "E2F0D9",
        "Proxy only": "E4DFEC",
        "PASS": "C6E0B4",
        "FAIL": "F4CCCC",
    }
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        headers = {
            str(cell.value): cell.column
            for cell in sheet[1]
            if cell.value is not None
        }
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, str) and cell.value in status_colors:
                    cell.fill = PatternFill(
                        "solid", fgColor=status_colors[cell.value]
                    )
            for header, column in headers.items():
                if any(
                    token in header.lower()
                    for token in ("id", "fips", "zip", "checksum")
                ):
                    row[column - 1].number_format = "@"
        for column_index in range(1, sheet.max_column + 1):
            maximum = max(
                len(str(sheet.cell(row, column_index).value or ""))
                for row in range(1, min(sheet.max_row, 250) + 1)
            )
            sheet.column_dimensions[get_column_letter(column_index)].width = min(
                max(maximum + 2, 12), 42
            )
    workbook.properties.creator = "LA housing database pipeline"
    workbook.properties.title = "LA Housing Database Data Catalog"
    workbook.properties.description = (
        "Generated from configuration, raw manifest, DuckDB metadata, coverage, "
        f"and gap analysis. Database build version: {build_version}."
    )
    workbook.properties.keywords = f"database_build_version={build_version}"
    workbook.save(path)


def build_data_catalog(config: ProjectConfig) -> dict[str, int]:
    generated_at = datetime.now(timezone.utc).isoformat()
    database_path = config.root / config.pipeline["database_path"]
    if database_path.exists():
        connection = duckdb.connect(str(database_path), read_only=True)
        fact_rows, latest_vintage = connection.execute(
            """
            SELECT count(*), max(source_vintage)
            FROM analytics.fact_housing_observation
            """
        ).fetchone()
        connection.close()
        build_version = f"{latest_vintage}-{fact_rows}"
    else:
        build_version = "database-not-built"
    completeness = build_raw_repository_completeness(config)
    catalog = _catalog_frame(config)
    gap = _refresh_gap_analysis(config)
    catalog["notes"] = catalog["notes"].map(
        lambda value: (
            f"{value} Catalog generated {generated_at}; "
            f"database build version {build_version}."
        )
    )
    csv_path = config.root / "metadata" / "data_catalog.csv"
    xlsx_path = config.root / "metadata" / "data_catalog.xlsx"
    gap_xlsx = config.root / "metadata" / "data_gap_analysis.xlsx"
    catalog.to_csv(csv_path, index=False)
    manifest = _manifest(config).drop(columns="_retrieved", errors="ignore")
    variables = pd.DataFrame(config.variables)
    matrix = catalog[
        [
            "source_id",
            "active_status",
            "native_frequency",
            "analytical_frequency",
            "geography_level",
            "geography_name",
        ]
    ].copy()
    coverage = catalog[
        [
            "source_id",
            "active_status",
            "start_date",
            "end_date",
            "row_count",
            "variable_ids",
        ]
    ].copy()
    unavailable = catalog.loc[
        ~catalog["active_status"].eq("Active")
    ].copy()
    commands = catalog.loc[
        catalog["active_status"].eq("Active"),
        ["source_id", "provider", "update_command"],
    ].copy()
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Data Catalog", index=False)
        variables.to_excel(
            writer, sheet_name="Variable Dictionary", index=False
        )
        manifest.to_excel(
            writer, sheet_name="Raw File Inventory", index=False
        )
        matrix.to_excel(
            writer, sheet_name="Geography-Frequency Matrix", index=False
        )
        coverage.to_excel(
            writer, sheet_name="Coverage Summary", index=False
        )
        unavailable.to_excel(
            writer, sheet_name="Unavailable and Deferred", index=False
        )
        commands.to_excel(
            writer, sheet_name="Update Commands", index=False
        )
        gap.to_excel(writer, sheet_name="Gap Analysis", index=False)
    _format_workbook(xlsx_path, build_version)
    with pd.ExcelWriter(gap_xlsx, engine="openpyxl") as writer:
        gap.to_excel(writer, sheet_name="Gap Analysis", index=False)
    _format_workbook(gap_xlsx, build_version)
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("data_catalog_frame", catalog)
        connection.register("gap_analysis_frame", gap)
        connection.execute(
            "CREATE OR REPLACE TABLE metadata.data_catalog AS "
            "SELECT * FROM data_catalog_frame"
        )
        connection.execute(
            "CREATE OR REPLACE TABLE metadata.data_gap_analysis AS "
            "SELECT * FROM gap_analysis_frame"
        )
        connection.close()
    failures = int(completeness["completeness_status"].ne("PASS").sum())
    if failures:
        failed = ", ".join(
            completeness.loc[
                completeness["completeness_status"].ne("PASS"), "source_id"
            ]
        )
        raise RuntimeError(
            f"Raw repository completeness failed for {failures} sources: {failed}"
        )
    return {
        "catalog_rows": len(catalog),
        "variable_rows": len(variables),
        "raw_inventory_rows": len(manifest),
        "gap_rows": len(gap),
    }
