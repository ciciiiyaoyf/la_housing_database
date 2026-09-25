"""Independent formula audit; never imports production feature functions.

Write JSON evidence under metadata and print it to stdout. All analytical inputs
and archived vintages are read-only; this script does not refresh sources or
alter analytical outputs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
COUNTIES = {"06037", "06059", "06065", "06071", "06073", "06067", "06085", "06001"}
PEERS = sorted(COUNTIES - {"06037"})
CHECKS: list[dict] = []


def floats(values):
    return np.asarray(pd.to_numeric(values, errors="raise"), dtype=float)


def check(name, actual, expected):
    actual, expected = floats(actual), floats(expected)
    same_null = np.isnan(actual) == np.isnan(expected)
    finite = np.isfinite(actual) & np.isfinite(expected)
    differences = np.abs(actual[finite] - expected[finite])
    mismatch = ~np.isclose(actual, expected, rtol=1e-12, atol=1e-12, equal_nan=True)
    CHECKS.append({
        "check": name, "rows": len(actual), "nonnull": int(np.isfinite(actual).sum()),
        "null_mask_mismatches": int((~same_null).sum()),
        "value_mismatches": int(mismatch.sum()),
        "max_abs_difference": float(differences.max()) if len(differences) else 0.0,
    })


def log_change(frame, group, column, lag, multiplier):
    dates = pd.to_datetime(frame["date"])
    months = dates.dt.year * 12 + dates.dt.month
    values = floats(frame[column])
    lookup = dict(zip(zip(frame[group].astype(str), months), values))
    if len(lookup) != len(frame):
        raise AssertionError("Duplicate geography/month key")
    previous = np.array([lookup.get((str(geo), month - lag), np.nan)
                         for geo, month in zip(frame[group], months)])
    valid = (values > 0) & (previous > 0)
    output = np.full(len(frame), np.nan)
    output[valid] = multiplier * (np.log(values[valid]) - np.log(previous[valid]))
    return output


def coverage(frame, columns):
    result = []
    for column in columns:
        values = frame[column].notna()
        result.append({"variable": column, "nonnull": int(values.sum()),
                       "first": str(frame.loc[values, "date"].min()),
                       "latest": str(frame.loc[values, "date"].max())})
    return result


def main():
    county = pd.read_parquet(ROOT / "data/final/comparable_county_monthly_analytics.parquet")
    zipcode = pd.read_parquet(ROOT / "data/final/zip_monthly_panel.parquet")
    fact = pd.read_parquet(ROOT / "data/final/fact_housing_observation.parquet")
    params = json.loads((ROOT / "metadata/comparable_county_reference_parameters_v1.json").read_text())
    assert set(county["county_fips"]) == COUNTIES
    assert set(zipcode["primary_county_fips"]) == {"06037"}
    assert zipcode["postal_zip"].str.fullmatch(r"\d{5}").all()
    assert county["date"].dt.is_month_end.all() and zipcode["date"].dt.is_month_end.all()

    # Verify panel inputs independently against latest fact records, including nulls.
    inputs = ["zhvi", "zori_smoothed_sa", "months_of_supply", "days_on_market",
              "price_drop_share", "employment", "unemployment_rate"]
    latest = fact[fact["final_frequency"].eq("monthly")].sort_values(
        ["source_vintage", "retrieved_at"]
    ).drop_duplicates(["observation_date", "geo_id", "variable_id"], keep="last")
    for label, frame, pairs in [
        ("county", county, [(v, v) for v in inputs]),
        ("zip", zipcode.assign(geo_id="ZIP_" + zipcode["postal_zip"]),
         [("zhvi", "zhvi"), ("zori", "zori_smoothed_sa")]),
    ]:
        keys = pd.MultiIndex.from_arrays([frame["date"], frame["geo_id"]])
        for output, variable in pairs:
            lookup = latest[latest["variable_id"].eq(variable)].set_index(
                ["observation_date", "geo_id"]
            )["value"]
            check(f"{label}:source:{output}", frame[output], lookup.reindex(keys))

    core = []
    for label, frame, group, rent in [
        ("county", county, "county_fips", "zori_smoothed_sa"),
        ("zip", zipcode, "postal_zip", "zori"),
    ]:
        for source, prefix in [("zhvi", "zhvi"), (rent, "zori")]:
            for lag, scale, suffix in [(12, 100, "yoy"), (3, 400, "3m_annualized")]:
                output = f"{prefix}_momentum_{suffix}_log_pct"
                check(f"{label}:{output}", frame[output], log_change(frame, group, source, lag, scale))
                if label == "county": core.append(output)
        price, rents = floats(frame["zhvi"]), floats(frame[rent])
        ratio = np.where((price > 0) & (rents > 0), price / (12 * rents), np.nan)
        check(f"{label}:price_to_rent_ratio_zillow", frame["price_to_rent_ratio_zillow"], ratio)
        work = frame.assign(independent_ratio=ratio)
        for lag, suffix in [(1, "mom"), (12, "yoy")]:
            check(f"{label}:price_to_rent_ratio_log_change_{suffix}_pct",
                  frame[f"price_to_rent_ratio_log_change_{suffix}_pct"],
                  log_change(work, group, "independent_ratio", lag, 100))

    check("county:laus_employment_momentum_yoy_log_pct", county["laus_employment_momentum_yoy_log_pct"],
          log_change(county, "county_fips", "employment", 12, 100))
    components = ["months_of_supply", "days_on_market", "price_drop_share"]
    zvalues = []
    for component in components:
        stats = params["tightness"]["statistics"][component]
        z = (floats(county[component]) - stats["mean"]) / stats["population_sd"]
        check(f"county:{component}_z_fixed", county[f"{component}_z_fixed"], z)
        zvalues.append(z)
        reference = county[county["date"].between("2012-02-29", "2019-12-31")][component].dropna()
        check(f"frozen_tightness:{component}", [stats["mean"], stats["population_sd"], stats["observation_count"]],
              [reference.mean(), reference.std(ddof=0), len(reference)])
    tightness = -np.mean(zvalues, axis=0)
    check("county:market_tightness_index_3c_fixed", county["market_tightness_index_3c_fixed"], tightness)
    check("county:market_tightness_components_3c", county["market_tightness_components_3c"],
          np.isfinite(zvalues).sum(axis=0))
    heat_parts = []
    for name, output, values in [
        ("home_price_momentum_3m_annualized", "zhvi_momentum_3m_z_fixed_for_heat",
         log_change(county, "county_fips", "zhvi", 3, 400)),
        ("market_tightness_index", "market_tightness_z_fixed_for_heat", tightness),
    ]:
        stats = params["heat"]["statistics"][name]
        z = (values - stats["mean"]) / stats["population_sd"]
        heat_parts.append(z)
        check(f"county:{output}", county[output], z)
    check("county:county_housing_market_heat_index", county["county_housing_market_heat_index"],
          np.mean(heat_parts, axis=0))

    relatives = {v: "la_relative_" + v for v in core + ["market_tightness_index_3c_fixed", "price_to_rent_ratio_zillow"]}
    for source, output in relatives.items():
        wide = county.pivot(index="date", columns="county_fips", values=source)
        expected = wide["06037"] - wide[PEERS].sum(axis=1, min_count=7) / 7
        expected = county["date"].map(expected).where(county["county_fips"].eq("06037"))
        check(f"county:{output}", county[output], expected)
    la = county[county["county_fips"].eq("06037")].set_index("date")["price_to_rent_ratio_zillow"]
    la_ratio = zipcode["date"].map(la)
    check("zip:la_county_price_to_rent_ratio_zillow", zipcode["la_county_price_to_rent_ratio_zillow"], la_ratio)
    check("zip:relative_valuation_pressure_la_zip", zipcode["relative_valuation_pressure_la_zip"],
          np.log(floats(zipcode["price_to_rent_ratio_zillow"])) - np.log(floats(la_ratio)))

    # Reconstruct frozen heat price statistics from the archived training vintage,
    # not from revised current Zillow history. Verify every frozen raw checksum.
    lineage = []
    for item in params["source_lineage"]:
        matches = list((ROOT / "data/raw").rglob(item["source_filename"]))
        assert len(matches) == 1, item["source_filename"]
        digest = hashlib.sha256(matches[0].read_bytes()).hexdigest()
        assert digest == item["checksum_sha256"], item["source_filename"]
        lineage.append({"source_id": item["source_id"], "checksum_matches": True})
        if item["source_id"] == "zillow_county_zhvi":
            raw = pd.read_csv(matches[0], dtype={"StateCodeFIPS": str, "MunicipalCodeFIPS": str})
            raw["county_fips"] = raw["StateCodeFIPS"].str.zfill(2) + raw["MunicipalCodeFIPS"].str.zfill(3)
            raw = raw[raw["county_fips"].isin(COUNTIES)]
            dates = [column for column in raw if len(column) == 10 and column[4] == "-"]
            raw = raw.melt(id_vars="county_fips", value_vars=dates, var_name="date", value_name="zhvi")
            raw["date"] = pd.to_datetime(raw["date"])
            raw["momentum"] = log_change(raw, "county_fips", "zhvi", 3, 400)
            values = raw.loc[raw["date"].between("2012-02-29", "2019-12-31"), "momentum"].dropna()
            stats = params["heat"]["statistics"]["home_price_momentum_3m_annualized"]
            check("frozen_heat:archived_price_momentum", [stats["mean"], stats["population_sd"], stats["observation_count"]],
                  [values.mean(), values.std(ddof=0), len(values)])

    sources = latest[latest["variable_id"].isin(inputs) & latest["geo_id"].isin(county["geo_id"])]
    source_coverage = sources.groupby(["source_id", "source_vintage", "retrieved_at"]).agg(
        first=("observation_date", "min"), latest=("observation_date", "max"), nonnull=("value", "count")
    ).reset_index().to_dict(orient="records")
    output = {"county_rows": len(county), "zip_rows": len(zipcode), "zip_count": zipcode["postal_zip"].nunique(),
              "checks": CHECKS, "failure_count": sum(c["value_mismatches"] for c in CHECKS),
              "frozen_raw_lineage": lineage, "source_coverage": source_coverage,
              "county_coverage": coverage(county, inputs + core + ["market_tightness_index_3c_fixed", "price_to_rent_ratio_zillow", "county_housing_market_heat_index"]),
              "zip_coverage": coverage(zipcode, ["zhvi", "zori", *core, "relative_valuation_pressure_la_zip"]),
              "provisional_assumption_status": params["assumption_status"]}
    evidence = json.dumps(output, indent=2, default=str)
    (ROOT / "metadata/county_exact_sep22_audit.json").write_text(evidence + "\n", encoding="utf-8")
    print(evidence)
    return bool(output["failure_count"])


if __name__ == "__main__":
    raise SystemExit(main())
