"""Write reproducible per-variable coverage for the September macro request."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.common.config import load_config
from src.features.macro_financial import MACRO_SOURCE_IDS, macro_delivery_role


def main() -> None:
    config = load_config(ROOT)
    fact = pd.read_parquet(ROOT / "data/final/fact_housing_observation.parquet")
    selected = fact.loc[fact.source_id.isin(MACRO_SOURCE_IDS)]
    definitions = {v["variable_id"]: v for v in config.variables}
    records = []
    for (source, variable, geo), group in selected.groupby(["source_id", "variable_id", "geo_id"]):
        valid = group.loc[group.value.notna()]
        definition = definitions[variable]
        records.append({
            "source_id": source, "variable_id": variable, "geo_id": geo,
            "display_name": definition["display_name"],
            "unit": definition["unit"], "frequency": definition["final_frequency"],
            "delivery_role": macro_delivery_role(variable),
            "included_in_monthly_panel": definition["final_frequency"] == "monthly",
            "rows": len(group), "nonnull_rows": len(valid),
            "null_rows": int(group.value.isna().sum()),
            "first_nonnull_date": valid.observation_date.min(),
            "latest_nonnull_date": valid.observation_date.max(),
            "latest_retrieval": group.retrieved_at.max(),
            "source_vintage": "|".join(sorted(group.source_vintage.dropna().astype(str).unique())),
            "source_filename": "|".join(sorted(group.source_filename.dropna().astype(str).unique())),
            "checksum_sha256": "|".join(sorted(group.checksum_sha256.dropna().astype(str).unique())),
            "construction": definition["transformation"],
            "method_notes": definition["methodological_notes"],
        })
    output = pd.DataFrame(records)
    if output.variable_id.nunique() != 37 or not output.nonnull_rows.gt(0).all():
        raise ValueError("The September macro contract is not fully materialized")
    target = ROOT / "metadata/september_2026_macro_variable_coverage.csv"
    output.to_csv(target, index=False)
    monthly = output.loc[output.frequency.eq("monthly")]
    if monthly.variable_id.nunique() != 27 or len(monthly) != 39:
        raise ValueError("Monthly macro delivery must contain 27 IDs / 39 geography-specific series")
    # GDP's quarterly frequency is explicitly confirmed by the user. It is a
    # completed quarterly requirement, not a failed or missing monthly measure.
    groups = {
        "Industry employment growth": output.variable_id.str.startswith("ces_") & ~output.variable_id.str.contains("hourly"),
        "Average hourly earnings": output.variable_id.str.contains("hourly_earnings"),
        "Initial unemployment claims": output.variable_id.str.startswith("ca_initial_claims_monthly"),
        "Continued unemployment claims": output.variable_id.str.startswith("ca_continued_claims_monthly"),
        "Real GDP growth": output.source_id.eq("bea_ca_real_gdp"),
        "Effective Federal Funds Rate": output.source_id.eq("fred_fedfunds"),
        "2-Year Treasury yield": output.source_id.eq("fred_dgs2") & output.frequency.eq("monthly"),
        "10-Year Treasury yield": output.source_id.eq("fred_dgs10") & output.frequency.eq("monthly"),
        "Baa corporate spread": output.source_id.eq("fred_baa10y") & output.frequency.eq("monthly"),
        "Bank prime loan rate": output.source_id.eq("fred_dprime") & output.frequency.eq("monthly"),
        "SOFR": output.source_id.eq("fred_sofr") & output.frequency.eq("monthly"),
    }
    statuses = []
    for name, mask in groups.items():
        group = output.loc[mask]
        if group.empty or not group.nonnull_rows.gt(0).all():
            raise ValueError(f"Required macro group is not materialized: {name}")
        statuses.append({
            "requirement": name, "status": "implemented",
            "required_frequency": "quarterly" if name == "Real GDP growth" else "monthly",
            "variable_ids": "|".join(sorted(group.variable_id.unique())),
            "geographies": "|".join(sorted(group.geo_id.unique())),
            "variable_count_including_levels_and_diagnostics": group.variable_id.nunique(),
            "geography_series_count": len(group),
            "common_latest_nonnull_date": group.latest_nonnull_date.min(),
            "latest_retrieval": group.latest_retrieval.max(),
            "note": "Required quarterly GDP; no monthly expansion" if name == "Real GDP growth" else "Monthly delivery; native audit inputs retained separately",
        })
    pd.DataFrame(statuses).to_csv(ROOT / "metadata/macro_required_frequency_status.csv", index=False)
    print(f"Monthly delivery: {len(monthly)} geography-specific series / {monthly.variable_id.nunique()} IDs; GDP remains quarterly as required")
    print(f"{len(output)} geography-specific series; {output.variable_id.nunique()} variable IDs; {len(selected):,} fact rows")
    print(target)


if __name__ == "__main__":
    main()
