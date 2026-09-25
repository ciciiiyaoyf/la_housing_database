"""Export modeling-friendly CSVs and refresh coverage metadata."""

from __future__ import annotations

import logging

import pandas as pd

from src.common.config import ProjectConfig
from src.common.variable_metadata import enrich_variable_metadata


RESTRICTED_PUBLIC_COLUMN_PREFIXES = ("usps_",)


def _assert_public_export_safe(frame: pd.DataFrame, export_name: str) -> None:
    """Stop licensed HUD-USPS fields from entering ordinary CSV exports."""

    restricted = sorted(
        str(column)
        for column in frame.columns
        if str(column).startswith(RESTRICTED_PUBLIC_COLUMN_PREFIXES)
    )
    if "variable_id" in frame:
        restricted.extend(sorted(set(frame.loc[
            frame["variable_id"].astype("string").str.startswith(RESTRICTED_PUBLIC_COLUMN_PREFIXES, na=False),
            "variable_id",
        ].astype(str))))
    if "source_id" in frame and frame["source_id"].eq("hud_usps_ncwm_quarterly").any():
        restricted.append("hud_usps_ncwm_quarterly")
    if restricted:
        raise ValueError(
            f"Refusing public export {export_name!r}: restricted HUD-USPS "
            f"columns are present: {restricted}"
        )


def export_outputs(config: ProjectConfig) -> dict[str, int]:
    logger = logging.getLogger("la_housing.export")
    final = config.root / "data" / "final"
    export_dir = config.root / "output" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    dictionary_path = config.root / "metadata" / "data_dictionary.csv"
    if (config.root / "config" / "variable_metadata.csv").exists() and dictionary_path.exists():
        dictionary = enrich_variable_metadata(pd.read_csv(dictionary_path), config.root)
        dictionary.to_csv(export_dir / "variable_dictionary.csv", index=False)
    exports = {
        "macro_financial_monthly": (
            final / "macro_financial_monthly.parquet",
            export_dir / "macro_financial_monthly.csv",
        ),
        "county_monthly_panel": (
            final / "county_monthly_panel.parquet",
            export_dir / "la_county_monthly_panel.csv",
        ),
        "zip_monthly_panel": (
            final / "zip_monthly_panel.parquet",
            export_dir / "la_zip_monthly_panel.csv",
        ),
        "comparable_county_monthly_analytics": (
            final / "comparable_county_monthly_analytics.parquet",
            export_dir / "la_comparable_county_monthly_analytics.csv",
        ),
        "zip_market_regime_quarterly": (
            final / "zip_market_regime_quarterly.parquet",
            export_dir / "la_zip_market_regime_quarterly.csv",
        ),
        "la_zip_market_regime_summary_quarterly": (
            final / "la_zip_market_regime_summary_quarterly.parquet",
            export_dir / "la_zip_market_regime_summary_quarterly.csv",
        ),
        "la_zip_market_regime_transitions_quarterly": (
            final / "la_zip_market_regime_transitions_quarterly.parquet",
            export_dir / "la_zip_market_regime_transitions_quarterly.csv",
        ),
        "homebuying_cost_scenarios": (
            final / "homebuying_cost_scenarios.parquet",
            export_dir / "la_homebuying_cost_scenarios.csv",
        ),
        "state_monthly_controls": (
            final / "state_monthly_controls.parquet",
            export_dir / "la_state_monthly_controls.csv",
        ),
        "state_quarterly_panel": (
            final / "state_quarterly_panel.parquet",
            export_dir / "la_state_quarterly_panel.csv",
        ),
        "state_annual_controls": (
            final / "state_annual_controls.parquet",
            export_dir / "la_state_annual_controls.csv",
        ),
        "metro_monthly_controls": (
            final / "metro_monthly_controls.parquet",
            export_dir / "la_metro_monthly_controls.csv",
        ),
        "metro_quarterly_panel": (
            final / "metro_quarterly_panel.parquet",
            export_dir / "la_metro_quarterly_panel.csv",
        ),
        "metro_quarterly_controls": (
            final / "metro_quarterly_controls.parquet",
            export_dir / "la_metro_quarterly_controls.csv",
        ),
        "county_annual_controls": (
            final / "county_annual_controls.parquet",
            export_dir / "la_county_annual_controls.csv",
        ),
        "zcta_annual_controls": (
            final / "zcta_annual_controls.parquet",
            export_dir / "la_zcta_annual_controls.csv",
        ),
        "coc_annual_controls": (
            final / "coc_annual_controls.parquet",
            export_dir / "ca_coc_annual_controls.csv",
        ),
        "county_quarterly_controls": (
            final / "county_quarterly_controls.parquet",
            export_dir / "la_county_quarterly_controls.csv",
        ),
    }
    counts: dict[str, int] = {}
    for name, (parquet_path, csv_path) in exports.items():
        if not parquet_path.exists():
            continue
        frame = pd.read_parquet(parquet_path)
        _assert_public_export_safe(frame, name)
        frame.to_csv(csv_path, index=False)
        counts[f"{name}_rows"] = len(frame)
    benchmark_path = final / "housing_finance_benchmark.parquet"
    benchmark_rows = 0
    if benchmark_path.exists():
        benchmark = pd.read_parquet(benchmark_path)
        la_benchmark = benchmark.loc[
            benchmark["county_fips"].astype("string").eq("06037")
        ]
        if (config.root / "config" / "variable_metadata.csv").exists():
            la_benchmark = enrich_variable_metadata(la_benchmark, config.root)
        la_benchmark.to_csv(
            export_dir / "la_version_a_benchmark.csv", index=False
        )
        benchmark_rows = len(la_benchmark)
    counts["version_a_la_rows"] = benchmark_rows
    logger.info("Exported CSV outputs: %s", counts)
    return counts
