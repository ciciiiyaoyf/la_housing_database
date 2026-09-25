"""Independent, read-only production-data audit of document requirement 8.

Does not import the production feature builder or refit/replace the frozen model.
Only the audit report under metadata is written.
"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "zhvi_momentum_3m_annualized_log_pct",
    "zori_momentum_3m_annualized_log_pct",
    "price_to_rent_ratio_zillow",
    "zhvi_monthly_log_growth_volatility_24m",
]


def reconstruct_quarters(panel):
    reconstructed = []
    for zipcode, group in panel.groupby("postal_zip", sort=True):
        source = group.set_index("date").sort_index()
        calendar = pd.date_range(source.index.min(), source.index.max(), freq="ME")
        source = source.reindex(calendar)
        price = pd.to_numeric(source.zhvi, errors="raise").astype(float).where(source.zhvi.gt(0))
        rent = pd.to_numeric(source.zori, errors="raise").astype(float).where(source.zori.gt(0))
        log_price, log_rent = np.log(price), np.log(rent)
        out = pd.DataFrame({
            "quarter_end": calendar,
            "postal_zip": zipcode,
            FEATURES[0]: (400 * (log_price - log_price.shift(3))).to_numpy(),
            FEATURES[1]: (400 * (log_rent - log_rent.shift(3))).to_numpy(),
            FEATURES[2]: (price / (12 * rent)).to_numpy(),
            FEATURES[3]: log_price.diff().rolling(24, min_periods=24).std(ddof=0).to_numpy(),
        })
        reconstructed.append(out.loc[out.quarter_end.dt.month.isin([3, 6, 9, 12])])
    return pd.concat(reconstructed).sort_values(["quarter_end", "postal_zip"]).reset_index(drop=True)


def frozen_training_quarters(model):
    """Read the model's immutable source vintages, not revised live history."""
    frames = []
    for item in model["source_lineage"]:
        path = ROOT / "data/raw/zillow" / item["source_filename"]
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["checksum_sha256"]:
            raise ValueError(f"Frozen source archive checksum mismatch: {path.name}")
        columns = list(pd.read_csv(path, nrows=0).columns)
        dates = [c for c in columns if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c) and "2013-01-01" <= c <= "2019-12-31"]
        source = pd.read_csv(path, usecols=["RegionName", "CountyName", "StateName", *dates], dtype={"RegionName": str})
        source = source.loc[source.CountyName.eq("Los Angeles County") & source.StateName.isin(["CA", "California"])]
        value_name = "zhvi" if item["source_id"] == "zillow_zip_zhvi" else "zori"
        long = source.melt(id_vars=["RegionName"], value_vars=dates, var_name="date", value_name=value_name)
        long["postal_zip"] = long.RegionName.str.zfill(5)
        long["date"] = pd.to_datetime(long.date).dt.to_period("M").dt.to_timestamp("M")
        frames.append(long[["date", "postal_zip", value_name]])
    if len(frames) != 2:
        raise ValueError("Expected exactly two immutable Zillow model input archives")
    panel = frames[0].merge(frames[1], on=["date", "postal_zip"], how="outer", validate="one_to_one")
    result = reconstruct_quarters(panel)
    return result.loc[result.quarter_end.between("2015-06-30", "2019-12-31")].dropna(subset=FEATURES)


def run_audit() -> dict:
    panel = pd.read_parquet(ROOT / "data/final/zip_monthly_panel.parquet")
    assignments = pd.read_parquet(ROOT / "data/final/zip_market_regime_quarterly.parquet")
    summary = pd.read_parquet(ROOT / "data/final/la_zip_market_regime_summary_quarterly.parquet")
    transitions = pd.read_parquet(ROOT / "data/final/la_zip_market_regime_transitions_quarterly.parquet")
    model = json.loads((ROOT / "metadata/zip_market_regime_model_v2.json").read_text())
    failures = []
    checks = {}

    def compare(name, actual, expected):
        a = np.asarray(actual, dtype=float)
        e = np.asarray(expected, dtype=float)
        if a.shape != e.shape:
            failures.append({"check": name, "shape_actual": list(a.shape), "shape_expected": list(e.shape)})
            return
        bad = ~np.isclose(a, e, rtol=1e-10, atol=1e-10, equal_nan=True)
        finite = np.isfinite(a) & np.isfinite(e)
        checks[name] = {
            "observations": int(a.size),
            "mismatches": int(bad.sum()),
            "max_absolute_error": float(np.max(np.abs(a[finite] - e[finite]))) if finite.any() else None,
        }
        if bad.any():
            failures.append({"check": name, "mismatches": int(bad.sum())})

    if panel.duplicated(["postal_zip", "date"]).any():
        raise ValueError("Duplicate ZIP-month input keys")
    expected = reconstruct_quarters(panel)
    mean = np.array([model["scaler_mean"][f] for f in FEATURES])
    scale = np.array([model["scaler_scale"][f] for f in FEATURES])
    centers = np.array([[r["center_standardized"][f] for f in FEATURES] for r in model["regimes"]])
    expected["complete"] = expected[FEATURES].notna().all(axis=1)
    values = (expected.loc[expected.complete, FEATURES].to_numpy() - mean) / scale
    dist = ((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    expected["regime"] = np.nan
    expected.loc[expected.complete, "regime"] = np.argmin(dist, axis=1) + 1
    expected["distance"] = np.nan
    expected.loc[expected.complete, "distance"] = np.sqrt(np.min(dist, axis=1))
    applicable = expected.loc[expected.quarter_end.ge("2020-03-31")]
    merged = assignments.merge(applicable, on=["quarter_end", "postal_zip"], how="outer", suffixes=("_actual", "_expected"), validate="one_to_one", indicator=True)
    if not merged._merge.eq("both").all():
        failures.append({"check": "assignment_keys", "unmatched": int(merged._merge.ne("both").sum())})
    for feature in FEATURES:
        compare(feature, merged[f"{feature}_actual"], merged[f"{feature}_expected"])
    compare("complete_inputs", merged.complete_regime_inputs, merged.complete)
    compare("frozen_nearest_center_assignments", merged.zip_market_regime_id.astype(float), merged.regime)
    compare("frozen_nearest_center_distances", merged.zip_market_regime_distance, merged.distance)
    for i, feature in enumerate(FEATURES):
        compare(f"{feature}_fixed_z", merged[f"{feature}_z_fixed"], ((merged[f"{feature}_expected"] - mean[i]) / scale[i]).where(merged.complete))

    training = frozen_training_quarters(model)
    training_values = (training[FEATURES].to_numpy() - mean) / scale
    training["regime"] = np.argmin(((training_values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1) + 1
    compare("training_scaler_mean_against_frozen_source_vintage", mean, training[FEATURES].mean().to_numpy())
    compare("training_scaler_population_sd_against_frozen_source_vintage", scale, training[FEATURES].std(ddof=0).to_numpy())
    compare("training_count", [model["training_observation_count"]], [len(training)])
    compare("training_quarter_count", [model["training_quarter_count"]], [training.quarter_end.nunique()])
    for r in model["regimes"]:
        members = training.loc[training.regime.eq(r["regime_id"])]
        compare(f"cluster_{r['regime_id']}_training_count", [r["training_observation_count"]], [len(members)])
        compare(f"cluster_{r['regime_id']}_raw_mean", [r["training_raw_mean"][f] for f in FEATURES], members[FEATURES].mean().to_numpy())
        compare(f"cluster_{r['regime_id']}_centroid", [r["center_standardized"][f] for f in FEATURES], ((members[FEATURES] - mean) / scale).mean().to_numpy())

    denominator_review = []
    for quarter, current in applicable.groupby("quarter_end", sort=True):
        classified = current.loc[current.complete]
        prev_quarter = (quarter.to_period("Q") - 1).to_timestamp(how="end").normalize()
        previous = expected.loc[expected.quarter_end.eq(prev_quarter) & expected.complete]
        pairs = previous[["postal_zip", "regime"]].merge(classified[["postal_zip", "regime"]], on="postal_zip", validate="one_to_one", suffixes=("_from", "_to"))
        n_t, n_pairs = len(classified), len(pairs)
        changed = int(pairs.regime_from.ne(pairs.regime_to).sum())
        quarter_summary = summary.loc[summary.quarter_end.eq(quarter)].sort_values("regime_id")
        quarter_transitions = transitions.loc[transitions.quarter_end.eq(quarter)].sort_values(["from_regime_id", "to_regime_id"])
        expected_counts = [int(classified.regime.eq(r).sum()) for r in range(1, 5)]
        compare(f"{quarter.date()}_regime_counts", quarter_summary.regime_zip_count, expected_counts)
        compare(f"{quarter.date()}_regime_shares", quarter_summary.regime_share_of_classified, 100 * np.array(expected_counts) / n_t if n_t else np.full(4, np.nan))
        compare(f"{quarter.date()}_changed_count", quarter_summary.changed_regime_zip_count, [changed] * 4)
        compare(f"{quarter.date()}_pair_denominator", quarter_summary.comparable_zip_count, [n_pairs] * 4)
        compare(f"{quarter.date()}_current_denominator", quarter_summary.classified_zip_count, [n_t] * 4)
        expected_transition_counts = [int((pairs.regime_from.eq(a) & pairs.regime_to.eq(b)).sum()) for a in range(1, 5) for b in range(1, 5)]
        compare(f"{quarter.date()}_all16transition_counts", quarter_transitions.transition_zip_count, expected_transition_counts)
        compare(f"{quarter.date()}_conditional_transition_shares", quarter_transitions.transition_share_of_comparable, 100 * np.array(expected_transition_counts) / n_pairs if n_pairs else np.full(16, np.nan))
        expected_entries = [int((pairs.regime_to.eq(r) & pairs.regime_from.ne(r)).sum()) for r in range(1, 5)]
        compare(f"{quarter.date()}_entries", quarter_summary.entered_regime_zip_count, expected_entries)
        compare(f"{quarter.date()}_conditional_entry_shares", quarter_summary.entered_regime_share_of_comparable, 100 * np.array(expected_entries) / n_pairs if n_pairs else np.full(4, np.nan))
        complete_comparison = n_t > 0 and n_pairs == n_t
        compare(f"{quarter.date()}_document_changed_share", quarter_summary.zip_market_regime_changed_share,
                [100 * changed / n_t] * 4 if complete_comparison else np.full(4, np.nan))
        compare(f"{quarter.date()}_document_entry_shares", quarter_summary.zip_market_regime_entry_share,
                100 * np.array(expected_entries) / n_t if complete_comparison else np.full(4, np.nan))
        compare(f"{quarter.date()}_document_all16transition_shares", quarter_transitions.zip_market_regime_transition_share,
                100 * np.array(expected_transition_counts) / n_t if complete_comparison else np.full(16, np.nan))
        denominator_review.append({
            "quarter_end": str(quarter.date()),
            "classified_current_n_t": n_t,
            "comparable_pair_denominator": n_pairs,
            "changed_count": changed,
            "existing_changed_percent_of_comparable": 100 * changed / n_pairs if n_pairs else None,
            "known_changes_percent_of_current_n_t_lower_bound_when_incomplete": 100 * changed / n_t if n_t else None,
            "exact_document_changed_percent_of_current_n_t": 100 * changed / n_t if complete_comparison else None,
            "current_classified_without_previous_classification": n_t - n_pairs,
            "counts_entering_regimes_1_to_4": expected_entries,
        })

    latest = applicable.loc[applicable.quarter_end.eq(applicable.quarter_end.max())]
    return {
        "audit": "September 22 independent document requirement 8 audit",
        "status": "PASS" if not failures else "FAIL",
        "production_formula_or_data_failures": failures,
        "checks": checks,
        "frozen_model": {k: model[k] for k in ["model_version", "model_fingerprint_sha256", "training_start", "training_end", "training_observation_count", "training_postal_zip_count", "training_quarter_count"]},
        "rows": {"assignments": len(assignments), "summary": len(summary), "transitions": len(transitions)},
        "latest_quarter": str(latest.quarter_end.max().date()),
        "latest_universe": len(latest),
        "latest_complete": int(latest.complete.sum()),
        "latest_missing_any_input": int((~latest.complete).sum()),
        "latest_missing_by_input": latest[FEATURES].isna().sum().to_dict(),
        "denominator_conclusion": "The document writes transitionShare /N_t. Canonical changed/transition/entry shares must use current classified N_t, with null output when any current classification lacks its exact previous-quarter classification. Explicit conditional comparable-pair diagnostics can remain available separately. Known changes divided by current N_t is only a lower bound when prior classifications are missing, not the exact document statistic.",
        "denominator_review": denominator_review,
        "implementation_choice_not_specified_by_document": "Population standard deviation (ddof=0) for the 24-month volatility and fixed training scaler. The document says SD but does not state sample/population convention.",
    }


if __name__ == "__main__":
    report = run_audit()
    target = ROOT / "metadata/regimes_exact_sep22_audit.json"
    target.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ["checks", "denominator_review", "production_formula_or_data_failures"]}, indent=2))
    print(json.dumps({"failed_check_count": len(report["production_formula_or_data_failures"]), "first_failures": report["production_formula_or_data_failures"][:5]}, indent=2))
    print(json.dumps({"latest_denominator": report["denominator_review"][-1]}, indent=2))
    raise SystemExit(bool(report["production_formula_or_data_failures"]))
