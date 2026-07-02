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
    counts = {"county_rows": len(county), "zip_rows": len(zip_panel)}
    additional = {
        "metro_monthly_controls": "metro_monthly_controls.csv",
        "state_quarterly_panel": "state_quarterly_panel.csv",
        "metro_quarterly_panel": "metro_quarterly_panel.csv",
        "county_annual_structural": "county_annual_structural.csv",
        "zcta_annual_structural": "zcta_annual_structural.csv",
    }
    for stem, filename in additional.items():
        frame = pd.read_parquet(final / f"{stem}.parquet")
        frame.to_csv(export_dir / filename, index=False)
        counts[f"{stem}_rows"] = len(frame)
    logger.info("Exported analytical tables: %s", counts)
    return counts
