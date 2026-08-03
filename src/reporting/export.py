"""Export modeling-friendly CSVs and refresh coverage metadata."""

from __future__ import annotations

import logging

import pandas as pd

from src.common.config import ProjectConfig


RESTRICTED_PUBLIC_COLUMN_PREFIXES = ("usps_",)


def _assert_public_export_safe(frame: pd.DataFrame, export_name: str) -> None:
    """Stop licensed HUD-USPS fields from entering ordinary CSV exports."""

    restricted = sorted(
        str(column)
        for column in frame.columns
        if str(column).startswith(RESTRICTED_PUBLIC_COLUMN_PREFIXES)
    )
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
    exports = {
        "county_monthly_panel": (
            final / "county_monthly_panel.parquet",
            export_dir / "la_county_monthly_panel.csv",
        ),
        "zip_monthly_panel": (
            final / "zip_monthly_panel.parquet",
            export_dir / "la_zip_monthly_panel.csv",
        ),
        "state_quarterly_panel": (
            final / "state_quarterly_panel.parquet",
            export_dir / "la_state_quarterly_panel.csv",
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
        la_benchmark.to_csv(
            export_dir / "la_version_a_benchmark.csv", index=False
        )
        benchmark_rows = len(la_benchmark)
    counts["version_a_la_rows"] = benchmark_rows
    logger.info("Exported CSV outputs: %s", counts)
    return counts
