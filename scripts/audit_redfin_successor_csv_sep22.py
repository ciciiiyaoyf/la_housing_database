"""Read-only independent audit of supplied September 22 Redfin CSV exports.

Run with the project Python environment, including its Parquet reader. The
active interpreter also decodes the old Parquet into an in-memory JSON
comparison input. Source files and production outputs are never modified;
only the audit JSON is written.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = Path.home() / "Downloads"
COUNTIES = {
    "Los Angeles County, CA": "06037",
    "Orange County, CA": "06059",
    "Riverside County, CA": "06065",
    "San Bernardino County, CA": "06071",
    "San Diego County, CA": "06073",
    "Sacramento County, CA": "06067",
    "Santa Clara County, CA": "06085",
    "Alameda County, CA": "06001",
}
FILES = {
    "tracker": "redfin_housing_market_monthly_all_counties_2012_Jan_to_2026_Aug.csv",
    "price_drops": "redfin_price_drops_monthly_all_counties_2012_Jan_to_2026_Aug.csv",
}
METRICS = {
    "months_of_supply": ("tracker", "MONTHS OF SUPPLY", 1.0, 0.050000001),
    "days_on_market": ("tracker", "MEDIAN DAYS ON MARKET (DAYS)", 1.0, 0.500000001),
    "price_drop_share": ("price_drops", "PERCENT ACTIVE WITH PRICE DROPS (%)", 0.01, 0.000050001),
}


def value_summary(series):
    numeric = pd.to_numeric(series, errors="coerce")
    return {
        "nonmissing": int(numeric.notna().sum()),
        "missing": int(numeric.isna().sum()),
        "minimum": float(numeric.min()) if numeric.notna().any() else None,
        "maximum": float(numeric.max()) if numeric.notna().any() else None,
    }


def main():
    source_frames, reports = {}, {}
    for product, filename in FILES.items():
        path = DOWNLOADS / filename
        frame = pd.read_csv(path, dtype={"REGION ID": str})
        for name in ["PERIOD BEGIN", "PERIOD END"]:
            frame[name] = pd.to_datetime(frame[name], errors="raise")
        start, end = frame["PERIOD BEGIN"], frame["PERIOD END"]
        invalid = ~(
            start.dt.is_month_start
            & end.dt.is_month_end
            & start.dt.to_period("M").eq(end.dt.to_period("M"))
        )
        selected = frame.loc[frame["REGION NAME"].isin(COUNTIES)].copy()
        selected["county_fips"] = selected["REGION NAME"].map(COUNTIES)
        source_frames[product] = selected
        by_county = []
        for name, fips in COUNTIES.items():
            county = selected.loc[selected.county_fips.eq(fips)]
            by_county.append({
                "county": name, "county_fips": fips, "rows": len(county),
                "first_period": str(county["PERIOD BEGIN"].min().date()),
                "last_period": str(county["PERIOD END"].max().date()),
                "metrics": {header: value_summary(county[header]) for p, header, _, _ in METRICS.values() if p == product},
            })
        reports[product] = {
            "filename": filename,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "headers": frame.columns.tolist(),
            "total_rows": len(frame),
            "unique_region_names": frame["REGION NAME"].nunique(),
            "frequency_values": frame.FREQUENCY.unique().tolist(),
            "region_type_values": frame["REGION TYPE"].unique().tolist(),
            "provider_last_updated_values": frame["LAST UPDATED"].unique().tolist(),
            "invalid_calendar_month_rows": int(invalid.sum()),
            "duplicate_region_period_end_rows": int(frame.duplicated(["REGION NAME", "PERIOD END"], keep=False).sum()),
            "seasonal_adjustment_columns": [c for c in frame if "SEASON" in c.upper()],
            "property_type_columns": [c for c in frame if "PROPERTY" in c.upper()],
            "selected_county_rows": len(selected),
            "county_coverage": by_county,
        }
    old_json = subprocess.run(
        [sys.executable, "-c",
         "import pandas as pd; f=pd.read_parquet('data/clean/redfin_county_monthly.parquet'); "
         "f=f[f.variable_id.isin(['months_of_supply','days_on_market','price_drop_share'])]; "
         "print(f[['observation_date','county_fips','variable_id','value','is_seasonally_adjusted']].to_json(orient='records',date_format='iso'))"],
        cwd=ROOT, capture_output=True, check=True, text=True,
    ).stdout
    old = pd.DataFrame(json.loads(old_json))
    old["observation_date"] = pd.to_datetime(old.observation_date)
    comparisons = {}
    newest = []
    for variable, (product, header, multiplier, rounding_tolerance) in METRICS.items():
        new = source_frames[product][["PERIOD END", "county_fips", header]].rename(columns={"PERIOD END": "observation_date", header: "new_value"})
        new["new_value"] = pd.to_numeric(new.new_value, errors="raise") * multiplier
        previous = old.loc[old.variable_id.eq(variable)].rename(columns={"value": "old_value"})
        joined = previous.merge(new, on=["county_fips", "observation_date"], how="outer", validate="one_to_one", indicator=True)
        overlap = joined.loc[joined._merge.eq("both")].copy()
        comparison = {}
        for period, sub in {
            "all_overlap": overlap,
            "frozen_reference_window": overlap.loc[overlap.observation_date.between("2012-02-29", "2019-12-31")],
            "2020_onward": overlap.loc[overlap.observation_date.ge("2020-01-01")],
        }.items():
            finite = sub.loc[sub.old_value.notna() & sub.new_value.notna()]
            difference = finite.new_value - finite.old_value
            comparison[period] = {
                "paired_rows": len(sub), "paired_nonmissing": len(finite),
                "old_missing_new_present": int((sub.old_value.isna() & sub.new_value.notna()).sum()),
                "new_missing_old_present": int((sub.new_value.isna() & sub.old_value.notna()).sum()),
                "equal_at_1e_9": int(np.isclose(finite.new_value, finite.old_value, rtol=0, atol=1e-9).sum()),
                "equal_within_successor_display_rounding": int(np.isclose(finite.new_value, finite.old_value, rtol=0, atol=rounding_tolerance).sum()),
                "rounding_tolerance_in_stored_units": rounding_tolerance,
                "mean_new_minus_old": float(difference.mean()),
                "median_absolute_difference": float(difference.abs().median()),
                "p95_absolute_difference": float(difference.abs().quantile(.95)),
                "max_absolute_difference": float(difference.abs().max()),
                "correlation": float(finite.new_value.corr(finite.old_value)),
            }
        largest = overlap.assign(abs_difference=(overlap.new_value - overlap.old_value).abs()).nlargest(5, "abs_difference")
        comparison["largest_overlap_differences"] = json.loads(largest[["observation_date", "county_fips", "old_value", "new_value", "abs_difference"]].to_json(orient="records", date_format="iso"))
        comparison["new_only_rows"] = int(joined._merge.eq("right_only").sum())
        comparison["old_only_rows"] = int(joined._merge.eq("left_only").sum())
        comparisons[variable] = comparison
        fresh = new.loc[new.observation_date.eq("2026-08-31")].copy()
        fresh["variable_id"] = variable
        newest.extend(json.loads(fresh.to_json(orient="records", date_format="iso")))
    keys = ["county_fips", "PERIOD BEGIN", "PERIOD END"]
    merged = source_frames["tracker"].merge(source_frames["price_drops"], on=keys, how="outer", validate="one_to_one", indicator=True, suffixes=("_tracker", "_drops"))
    ratio = 100 * merged["PRICE DROPS"] / merged["ACTIVE LISTINGS"]
    share_difference = ratio - merged["PERCENT ACTIVE WITH PRICE DROPS (%)"]
    result = {
        "files": reports,
        "join": {
            "matched_rows": int(merged._merge.eq("both").sum()),
            "unmatched_rows": int(merged._merge.ne("both").sum()),
            "price_drop_share_vs_count_divided_by_active_listings_max_absolute_percentage_point_difference": float(share_difference.abs().max()),
            "price_drop_share_vs_count_divided_by_active_listings_median_absolute_percentage_point_difference": float(share_difference.abs().median()),
        },
        "old_clean_seasonal_flags": old.is_seasonally_adjusted.unique().tolist(),
        "overlap_comparisons": comparisons,
        "august_2026_values": newest,
        "limitations": [
            "Successor CSVs have no explicit seasonal-adjustment or property-type columns; source-page methodology/selection records are required to establish those attributes.",
            "Overlap differences describe observed revisions and/or methodological differences; the files alone do not identify their cause.",
            "Monthly frequency and August source observations do not establish a working automated provider download endpoint.",
        ],
    }
    target = ROOT / "metadata/redfin_successor_sep22_readonly_audit.json"
    target.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    brief = {
        "files": {name: {k: v for k, v in report.items() if k not in ["headers", "county_coverage"]} for name, report in reports.items()},
        "join": result["join"],
        "overlap_comparisons": comparisons,
        "august_2026_values": newest,
        "limitations": result["limitations"],
    }
    print(json.dumps(brief, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
