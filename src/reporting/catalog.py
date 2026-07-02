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
        "Source": "Income-based affordability ratios",
        "Frequency": "Annual or mixed",
        "Geography": "Los Angeles County or ZIP",
        "Key variables": "price_to_income|rent_to_income",
        "source_id": "deferred_income_affordability_ratios",
        "provider": "Requires explicit income denominator decision",
        "product": "Price-to-income and rent-to-income ratios",
        "active_status": "Deferred",
        "known_limitations": (
            "The current active stage uses rent/payment affordability metrics. "
            "Income-denominator ratios are later-stage work and are not exposed "
            "as active variables."
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
        active_ids = {source.source_id for source in config.sources}
        frame = frame.loc[frame["source_id"].isin(active_ids)].copy()
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
        if end_date:
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


def _gap_row(
    variable_name: str,
    market_segment: str,
    variable_role: str,
    requested_source: str,
    official_source_found: str,
    implementation_status: str,
    current_database_table: str,
    current_database_column: str,
    native_frequency: str,
    geography: str,
    available_start_date: str = "",
    available_end_date: str = "",
    limitation: str = "",
    recommended_action: str = "",
) -> dict[str, str]:
    return {
        "variable_name": variable_name,
        "market_segment": market_segment,
        "variable_role": variable_role,
        "requested_source": requested_source,
        "official_source_found": official_source_found,
        "implementation_status": implementation_status,
        "current_database_table": current_database_table,
        "current_database_column": current_database_column,
        "native_frequency": native_frequency,
        "geography": geography,
        "available_start_date": available_start_date,
        "available_end_date": available_end_date,
        "limitation": limitation,
        "recommended_action": recommended_action,
    }


def _refresh_gap_analysis(config: ProjectConfig) -> pd.DataFrame:
    rows = [
        _gap_row("ZHVI", "Homeowner", "Outcome", "Zillow Research", "Yes", "Implemented", "analytics.county_monthly_panel", "zhvi", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Provider modeled index; histories can be revised.", "Keep as the local home-value index."),
        _gap_row("FHFA HPI", "Homeowner", "Benchmark", "FHFA", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.metro_quarterly_panel|analytics.county_annual_controls", "FHFA HPI columns", "quarterly and annual", "California; Los Angeles metro; Los Angeles County", "1975-12-31", "2026-03-31", "Products differ by geography, frequency, method, and seasonal adjustment.", "Retain at native frequency."),
        _gap_row("median sale price", "Homeowner", "Outcome", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "median_sale_price", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Transaction mix can change the median.", "Retain as a transaction-price indicator."),
        _gap_row("inventory", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "active_inventory", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "For-sale inventory only; not rental inventory.", "Keep the label explicit."),
        _gap_row("active listings", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.zip_monthly_panel", "active_listings", "rolling 3 months", "Los Angeles County postal ZIPs", "2019-06-30", "2026-05-31", "ZIP product uses overlapping rolling-three-month windows.", "Keep separate from county inventory."),
        _gap_row("months of supply", "Homeowner", "Supply", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "months_of_supply", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Provider ratio; not additive.", "Retain provider value."),
        _gap_row("new listings", "Homeowner", "Leading indicator", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "new_listings", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "ZIP observations are rolling windows.", "Use county monthly for time-series models."),
        _gap_row("homes sold", "Homeowner", "Demand", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "homes_sold", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "Closed-sales flow; ZIP observations are rolling windows.", "Use with inventory ratios."),
        _gap_row("days on market", "Overall housing market", "Market time", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel|analytics.zip_monthly_panel", "days_on_market|median_days_on_market", "monthly and rolling 3 months", "Los Angeles County and postal ZIPs", "2012-01-31", "2026-05-31", "Median; not additive.", "Expose as market-time indicator."),
        _gap_row("share with price drops", "Overall housing market", "Leading indicator", "Redfin Data Center", "Yes", "Implemented", "analytics.county_monthly_panel", "price_drop_share", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Share, not count.", "Retain nulls and document coverage."),
        _gap_row("30-year mortgage rate", "Homeowner", "Control", "Freddie Mac PMMS", "Yes", "Implemented", "analytics.county_monthly_panel", "mortgage_rate_30y_monthly_average", "weekly aggregated to monthly", "United States", "1971-04-02", "2026-06-25", "National rate; not local.", "Keep U.S. geography explicit."),
        _gap_row("single-family permits", "Homeowner", "Supply", "Census BPS", "Yes", "Implemented", "analytics.county_monthly_panel", "permits_1_unit", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Permits are authorizations, not starts or completions.", "Retain as the single-family supply proxy."),
        _gap_row("monthly mortgage payment", "Homeowner", "Constructed indicator", "Redfin and Freddie Mac", "Yes", "Constructed variable", "analytics.county_monthly_panel", "mortgage_payment_10pct_down|mortgage_payment_20pct_down", "monthly", "Los Angeles County with U.S. rate", "2012-01-31", "2026-05-31", "Principal-and-interest scenarios exclude taxes, insurance, HOA, maintenance, and mortgage insurance.", "Keep scenario assumptions visible."),
        _gap_row("ZORI", "Renter", "Outcome", "Zillow Research", "Yes", "Implemented", "analytics.county_monthly_panel", "zori", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Smoothed seasonally adjusted rent index; not inventory.", "Use as primary rent index."),
        _gap_row("rent growth", "Renter", "Constructed indicator", "Zillow ZORI", "Yes", "Constructed variable", "analytics.county_monthly_panel", "zori_growth_mom|zori_growth_yoy", "monthly", "Los Angeles County", "2015-02-28", "2026-05-31", "Depends on the active SA ZORI vintage.", "Rebuild when ZORI refreshes."),
        _gap_row("rental inventory", "Renter", "Supply", "No verified official public source", "No", "Missing — no verified public source", "", "", "unknown", "Los Angeles County or ZIPs", "", "", "ZORI measures rent levels and cannot serve as rental inventory.", "Leave unavailable until a documented public source is verified."),
        _gap_row("rental vacancy", "Renter", "Supply", "Census HVS", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.metro_quarterly_panel", "hvs_rental_vacancy_rate_ca|hvs_rental_vacancy_rate_la_metro", "quarterly", "California and published metro geographies", "2005-03-31", "2026-03-31", "Los Angeles County is not an HVS published geography; sampling uncertainty is retained.", "Retain native geography/frequency."),
        _gap_row("multifamily permits", "Renter", "Supply", "Census BPS", "Yes", "Implemented", "analytics.county_monthly_panel", "permits_5_plus_units", "monthly", "Los Angeles County", "2000-01-31", "2026-05-31", "Permits are authorizations, not completed or occupied rental units.", "Retain as multifamily supply proxy."),
        _gap_row("price-to-rent", "Overall housing market", "Constructed indicator", "Zillow ZHVI and ZORI", "Yes", "Constructed variable", "analytics.county_monthly_panel", "price_to_rent_ratio", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Index-based valuation ratio.", "Keep explicit formula."),
        _gap_row("inventory-to-sales", "Overall housing market", "Constructed indicator", "Redfin Data Center", "Yes", "Constructed variable", "analytics.county_monthly_panel", "inventory_to_sales_ratio|sales_to_inventory_ratio", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Uses for-sale inventory and sales, not total housing stock.", "Keep both orientations clearly named."),
        _gap_row("market tightness", "Overall housing market", "Constructed indicator", "Redfin Data Center", "Yes", "Constructed variable", "analytics.county_monthly_panel", "market_tightness_index|market_tightness_components", "monthly", "Los Angeles County", "2012-01-31", "2026-05-31", "Composite, not official provider statistic.", "Retain component-count metadata."),
        _gap_row("affordability metrics", "Overall housing market", "Affordability", "Zillow, Redfin, Freddie Mac", "Yes", "Implemented", "analytics.county_monthly_panel", "price_to_rent_ratio|mortgage_payment_to_rent_10pct_down|mortgage_payment_to_rent_20pct_down", "monthly", "Los Angeles County", "2015-01-31", "2026-05-31", "Income-based ratios are deferred at this active stage.", "Do not expose ACS-derived affordability columns until requested."),
        _gap_row("LAUS unemployment rate", "Macro", "Control", "BLS LAUS", "Yes", "Implemented", "analytics.county_monthly_panel", "unemployment_rate", "monthly", "Los Angeles County", "1990-01-31", "2026-05-31", "County series is not seasonally adjusted and may be revised.", "Archive API responses."),
        _gap_row("LAUS labor force", "Macro", "Control", "BLS LAUS", "Yes", "Implemented", "analytics.county_monthly_panel", "labor_force", "monthly", "Los Angeles County", "1990-01-31", "2026-05-31", "County series is not seasonally adjusted and may be revised.", "Archive API responses."),
        _gap_row("CES payroll employment", "Macro", "Control", "BLS CES", "Yes", "Implemented", "analytics.metro_monthly_controls", "ces_total_nonfarm_employment_sa", "monthly", "Los Angeles metro", "2005-01-31", "2026-05-31", "Metro establishment series; not county LAUS employment.", "Retain geography label."),
        _gap_row("personal income", "Macro", "Control", "BEA", "Yes", "Implemented", "analytics.state_quarterly_panel|analytics.county_annual_controls", "bea_ca_personal_income_q_sa|bea_la_county_personal_income_annual", "quarterly and annual", "California and Los Angeles County", "1948-03-31", "2026-03-31", "State quarterly and county annual products differ.", "Retain native frequency."),
        _gap_row("CPI / inflation", "Macro", "Control", "BLS CPI", "Yes", "Implemented", "analytics.metro_monthly_controls", "cpi_u_all_items_la_nsa|cpi_inflation_yoy", "monthly with historical gaps", "Los Angeles published area", "1914-12-31", "2026-05-31", "Local CPI is not seasonally adjusted and has historical publication gaps.", "Preserve gaps."),
        _gap_row("price-to-income", "Homeowner", "Affordability", "ACS or BEA", "Yes", "Deferred", "", "", "annual or mixed", "Los Angeles County or ZIP", "", "", "Not part of the current active available-variable set.", "Add later only with an explicit denominator choice."),
    ]
    gap = pd.DataFrame(rows)
    path = config.root / "metadata" / "data_gap_analysis.csv"
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
