"""Export modeling-friendly CSVs and refresh coverage metadata."""

from __future__ import annotations

import logging

import pandas as pd

from src.common.config import ProjectConfig


def export_outputs(config: ProjectConfig) -> dict[str, int]:
    logger = logging.getLogger("la_housing.export")
    final = config.root / "data" / "final"
    export_dir = config.root / "output" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    county = pd.read_parquet(final / "county_monthly_panel.parquet")
    zip_panel = pd.read_parquet(final / "zip_monthly_panel.parquet")
    county.to_csv(export_dir / "la_county_monthly_panel.csv", index=False)
    zip_panel.to_csv(export_dir / "la_zip_monthly_panel.csv", index=False)
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
    logger.info("Exported county=%s and ZIP=%s rows", len(county), len(zip_panel))
    return {
        "county_rows": len(county),
        "zip_rows": len(zip_panel),
        "version_a_la_rows": benchmark_rows,
    }
