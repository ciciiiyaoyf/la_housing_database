"""Combine clean source observations and build county/ZIP panels."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.cleaning.common import OBSERVATION_COLUMNS, empty_observations
from src.common.config import ProjectConfig
from src.features.derived import add_panel_features
from src.geography.build import build_geography

COUNTY_VARIABLES = [
    "zhvi",
    "zori_smoothed_sa",
    "median_sale_price",
    "active_inventory",
    "new_listings",
    "homes_sold",
    "pending_sales",
    "months_of_supply",
    "days_on_market",
    "price_drop_share",
    "sale_to_list_ratio",
    "unemployment_rate",
    "employment",
    "unemployment",
    "labor_force",
    "permits_total",
    "permits_1_unit",
    "permits_2_to_4_units",
    "permits_5_plus_units",
]

MORTGAGE_VARIABLES = [
    "mortgage_rate_30y_monthly_average",
    "mortgage_rate_30y_month_end",
]

ZIP_ZILLOW_VARIABLES = [
    "zhvi",
    "zori_smoothed_sa",
]

ZIP_REDFIN_LEVEL_COLUMNS = [
    "homes_sold",
    "median_sale_price_nsa",
    "median_days_on_market",
    "new_listings",
    "active_listings",
    "pending_sales",
]

ZIP_LEGACY_UNSUPPORTED_COLUMNS = [
    "active_inventory",
    "months_of_supply",
    "days_on_market",
    "price_drop_share",
]


def _read_clean_files(root: Path) -> pd.DataFrame:
    files = sorted(
        path
        for path in (root / "data" / "clean").glob("*.parquet")
        if path.name
        not in {
            "geography.parquet",
            "geography_crosswalk.parquet",
            "redfin_zip_monthly_la_county.parquet",
        }
    )
    redfin_zip_observations = (
        root
        / "data"
        / "interim"
        / "redfin_zip_monthly_observations.parquet"
    )
    if redfin_zip_observations.exists():
        files.append(redfin_zip_observations)
    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        for column in OBSERVATION_COLUMNS:
            if column not in frame:
                frame[column] = None
        frames.append(frame[OBSERVATION_COLUMNS])
    if not frames:
        return empty_observations()
    return pd.concat(frames, ignore_index=True)[OBSERVATION_COLUMNS]


def _date_filter(
    frame: pd.DataFrame, start_date: str | None, end_date: str | None
) -> pd.DataFrame:
    result = frame.copy()
    result["observation_date"] = pd.to_datetime(result["observation_date"])
    if start_date:
        result = result.loc[result["observation_date"] >= pd.Timestamp(start_date)]
    if end_date:
        result = result.loc[result["observation_date"] <= pd.Timestamp(end_date)]
    return result


def _latest_values(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(["source_vintage", "retrieved_at"])
        .drop_duplicates(["observation_date", "geo_id", "variable_id"], keep="last")
        .copy()
    )


def _county_panel(fact: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    county = _latest_values(
        fact.loc[
            fact["geo_id"].eq("COUNTY_06037")
            & fact["variable_id"].isin(COUNTY_VARIABLES)
            & fact["final_frequency"].eq("monthly")
        ]
    )
    if county.empty:
        panel = pd.DataFrame(columns=["date", *COUNTY_VARIABLES])
    else:
        panel = (
            county.pivot(
                index="observation_date", columns="variable_id", values="value"
            )
            .reset_index()
            .rename(columns={"observation_date": "date"})
        )
    for variable in COUNTY_VARIABLES:
        if variable not in panel:
            panel[variable] = pd.NA
    # Clean/fact naming is methodological; panel naming is semantic.
    panel["zori"] = panel["zori_smoothed_sa"]
    mortgage = _latest_values(
        fact.loc[
            fact["geo_id"].eq("US")
            & fact["variable_id"].isin(MORTGAGE_VARIABLES)
            & fact["final_frequency"].eq("monthly")
        ]
    )
    if not mortgage.empty:
        mortgage_panel = (
            mortgage.pivot(
                index="observation_date", columns="variable_id", values="value"
            )
            .reset_index()
            .rename(columns={"observation_date": "date"})
        )
        panel = panel.merge(mortgage_panel, on="date", how="left")
    for variable in MORTGAGE_VARIABLES:
        if variable not in panel:
            panel[variable] = pd.NA
    panel.insert(1, "county_fips", "06037")
    panel.insert(2, "county_name", "Los Angeles County")
    panel.insert(3, "state_fips", "06")
    observed = [*COUNTY_VARIABLES, *MORTGAGE_VARIABLES]
    panel["source_coverage_count"] = panel[observed].notna().sum(axis=1)
    panel["panel_quality_flag"] = panel["source_coverage_count"].map(
        lambda count: "ok" if count >= 5 else "partial"
    )
    vintages = fact.loc[
        fact["geo_id"].isin({"COUNTY_06037", "US"})
        & fact["final_frequency"].eq("monthly"),
        ["observation_date", "source_vintage"],
    ]
    if not vintages.empty:
        latest = (
            vintages.groupby("observation_date")["source_vintage"]
            .max()
            .rename("latest_source_vintage")
        )
        panel = panel.merge(latest, left_on="date", right_index=True, how="left")
    else:
        panel["latest_source_vintage"] = None
    panel = panel.drop(columns=["zori_smoothed_sa"])
    panel = add_panel_features(
        panel,
        config.pipeline["mortgage"],
        int(config.pipeline["market_tightness_min_components"]),
    )
    return panel.sort_values("date")


def _calculated_zip_yoy(
    panel: pd.DataFrame, level_column: str
) -> pd.Series:
    prior = panel[["date", "postal_zip", level_column]].copy()
    prior["date"] = prior["date"] + pd.DateOffset(years=1)
    prior = prior.rename(columns={level_column: "_prior"})
    aligned = panel[["date", "postal_zip", level_column]].merge(
        prior, on=["date", "postal_zip"], how="left", sort=False
    )
    return (
        aligned[level_column]
        / aligned["_prior"].replace(0, pd.NA)
        - 1.0
    ) * 100.0


def _zip_panel(fact: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    zip_fact = _latest_values(
        fact.loc[
            fact["geo_id"].astype(str).str.startswith("ZIP_")
            & fact["variable_id"].isin(ZIP_ZILLOW_VARIABLES)
            & fact["final_frequency"].eq("monthly")
        ]
    )
    if zip_fact.empty:
        panel = pd.DataFrame(
            columns=["date", "postal_zip", *ZIP_ZILLOW_VARIABLES]
        )
    else:
        panel = (
            zip_fact.pivot(
                index=["observation_date", "geo_id"],
                columns="variable_id",
                values="value",
            )
            .reset_index()
            .rename(columns={"observation_date": "date"})
        )
        panel["postal_zip"] = panel.pop("geo_id").str.removeprefix("ZIP_")
    for variable in ZIP_ZILLOW_VARIABLES:
        if variable not in panel:
            panel[variable] = pd.NA
    # Clean/fact naming is methodological; panel naming is semantic.
    panel["zori"] = panel["zori_smoothed_sa"]
    panel.insert(2, "primary_county_fips", "06037")
    redfin_path = (
        config.root
        / "data"
        / "clean"
        / "redfin_zip_monthly_la_county.parquet"
    )
    if redfin_path.exists():
        redfin = pd.read_parquet(redfin_path)
        redfin["period_end"] = pd.to_datetime(redfin["period_end"])
        redfin = (
            redfin.sort_values(["source_vintage", "retrieved_at"])
            .drop_duplicates(["postal_zip", "period_end"], keep="last")
            .rename(
                columns={
                    "period_end": "date",
                    "frequency": "redfin_frequency",
                    "rolling_window_months": "redfin_window_months",
                    "last_updated": "redfin_last_updated",
                }
            )
        )
        redfin_columns = [
            "date",
            "postal_zip",
            *ZIP_REDFIN_LEVEL_COLUMNS,
            "homes_sold_yoy_pct_provider",
            "median_sale_price_nsa_yoy_pct_provider",
            "median_days_on_market_yoy_pct_provider",
            "new_listings_yoy_pct_provider",
            "active_listings_yoy_pct_provider",
            "pending_sales_yoy_pct_provider",
            "redfin_frequency",
            "redfin_window_months",
            "redfin_last_updated",
        ]
        panel = panel.merge(
            redfin[redfin_columns],
            on=["date", "postal_zip"],
            how="left",
            validate="one_to_one",
        )
    else:
        for column in [
            *ZIP_REDFIN_LEVEL_COLUMNS,
            "homes_sold_yoy_pct_provider",
            "median_sale_price_nsa_yoy_pct_provider",
            "median_days_on_market_yoy_pct_provider",
            "new_listings_yoy_pct_provider",
            "active_listings_yoy_pct_provider",
            "pending_sales_yoy_pct_provider",
            "redfin_frequency",
            "redfin_window_months",
            "redfin_last_updated",
        ]:
            panel[column] = pd.NA
    panel["median_sale_price"] = panel["median_sale_price_nsa"]
    calculated_columns = {
        "homes_sold": "homes_sold_yoy_calculated",
        "median_sale_price_nsa": "median_sale_price_yoy_calculated",
        "median_days_on_market": (
            "median_days_on_market_yoy_calculated"
        ),
        "new_listings": "new_listings_yoy_calculated",
        "active_listings": "active_listings_yoy_calculated",
        "pending_sales": "pending_sales_yoy_calculated",
    }
    panel = panel.sort_values(["postal_zip", "date"]).reset_index(drop=True)
    for level_column, yoy_column in calculated_columns.items():
        panel[yoy_column] = _calculated_zip_yoy(panel, level_column)
    for column in ZIP_LEGACY_UNSUPPORTED_COLUMNS:
        panel[column] = pd.NA
    panel["zhvi_available"] = panel["zhvi"].notna()
    panel["zori_available"] = panel["zori"].notna()
    panel["redfin_available"] = panel[
        ZIP_REDFIN_LEVEL_COLUMNS
    ].notna().any(axis=1)
    panel["source_coverage_count"] = panel[
        [*ZIP_ZILLOW_VARIABLES, *ZIP_REDFIN_LEVEL_COLUMNS]
    ].notna().sum(axis=1)
    panel["data_quality_flag"] = panel["source_coverage_count"].map(
        lambda count: "ok" if count >= 2 else "partial"
    )
    panel = panel.drop(columns=["zori_smoothed_sa"])
    return panel.sort_values(["postal_zip", "date"])


def _redfin_yoy_report(config: ProjectConfig, panel: pd.DataFrame) -> None:
    pairs = {
        "homes_sold": (
            "homes_sold_yoy_pct_provider",
            "homes_sold_yoy_calculated",
        ),
        "median_sale_price_nsa": (
            "median_sale_price_nsa_yoy_pct_provider",
            "median_sale_price_yoy_calculated",
        ),
        "median_days_on_market": (
            "median_days_on_market_yoy_pct_provider",
            "median_days_on_market_yoy_calculated",
        ),
        "new_listings": (
            "new_listings_yoy_pct_provider",
            "new_listings_yoy_calculated",
        ),
        "active_listings": (
            "active_listings_yoy_pct_provider",
            "active_listings_yoy_calculated",
        ),
        "pending_sales": (
            "pending_sales_yoy_pct_provider",
            "pending_sales_yoy_calculated",
        ),
    }
    rows = []
    for metric, (provider_column, calculated_column) in pairs.items():
        comparable = panel[[provider_column, calculated_column]].dropna()
        difference = (
            comparable[provider_column] - comparable[calculated_column]
        ).abs()
        rows.append(
            {
                "metric": metric,
                "comparable_observations": len(comparable),
                "mean_absolute_difference_percentage_points": (
                    difference.mean() if not difference.empty else None
                ),
                "max_absolute_difference_percentage_points": (
                    difference.max() if not difference.empty else None
                ),
                "material_discrepancies_over_1pp": int(
                    difference.gt(1.0).sum()
                ),
            }
        )
    coverage_dir = config.root / "output" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        coverage_dir / "redfin_zip_yoy_comparison.csv", index=False
    )


def build_analytical_outputs(
    config: ProjectConfig,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    logger = logging.getLogger("la_housing.harmonize")
    fact = _date_filter(_read_clean_files(config.root), start_date, end_date)
    fact = fact.sort_values(
        ["observation_date", "geo_id", "variable_id", "source_vintage"]
    )
    county = _county_panel(fact, config)
    zip_panel = _zip_panel(fact, config)
    _redfin_yoy_report(config, zip_panel)
    state_quarterly = pd.DataFrame(
        {
            "quarter_end": pd.Series(dtype="datetime64[ns]"),
            "state_fips": pd.Series(dtype="string"),
            "fhfa_hpi": pd.Series(dtype="float64"),
            "permits": pd.Series(dtype="float64"),
            "rental_vacancy": pd.Series(dtype="float64"),
            "personal_income": pd.Series(dtype="float64"),
            "unemployment": pd.Series(dtype="float64"),
            "mortgage_rate": pd.Series(dtype="float64"),
            "treasury_rate": pd.Series(dtype="float64"),
            "mortgage_spread": pd.Series(dtype="float64"),
        }
    )
    final_dir = config.root / "data" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    fact.to_parquet(final_dir / "fact_housing_observation.parquet", index=False)
    county.to_parquet(final_dir / "county_monthly_panel.parquet", index=False)
    zip_panel.to_parquet(final_dir / "zip_monthly_panel.parquet", index=False)
    state_quarterly.to_parquet(
        final_dir / "state_quarterly_panel.parquet", index=False
    )
    geography, crosswalk = build_geography(config, fact)
    pd.DataFrame(config.variables).to_csv(
        config.root / "metadata" / "data_dictionary.csv", index=False
    )
    transformation_path = config.root / "metadata" / "transformation_log.csv"
    prior = (
        pd.read_csv(transformation_path, dtype=str)
        if transformation_path.exists()
        else pd.DataFrame()
    )
    if not prior.empty:
        active_sources = {
            source.source_id for source in config.sources
        } | {"multiple"}
        prior = prior.loc[prior["source_id"].isin(active_sources)]
    executed_at = pd.Timestamp.now(tz="UTC").isoformat()
    panel_transformations = pd.DataFrame(
        [
            {
                "transformation_id": "fact_union_latest_clean_v1",
                "source_id": "multiple",
                "input_file": "data/clean/*.parquet",
                "output_file": "data/final/fact_housing_observation.parquet",
                "input_rows_or_shape": len(fact),
                "output_rows": len(fact),
                "description": "Union the latest selected clean source vintages without interpolation.",
                "code_module": "src.harmonization.build",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "county_monthly_panel_and_features_v1",
                "source_id": "multiple",
                "input_file": "data/final/fact_housing_observation.parquet",
                "output_file": "data/final/county_monthly_panel.parquet",
                "input_rows_or_shape": len(fact),
                "output_rows": len(county),
                "description": (
                    "Pivot LA County variables, join national PMMS by month, and calculate "
                    "documented growth, affordability, and tightness features."
                ),
                "code_module": "src.harmonization.build|src.features.derived",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "zip_monthly_partial_coverage_v1",
                "source_id": "multiple",
                "input_file": "data/final/fact_housing_observation.parquet",
                "output_file": "data/final/zip_monthly_panel.parquet",
                "input_rows_or_shape": len(fact),
                "output_rows": len(zip_panel),
                "description": "Pivot postal-ZIP observations while retaining partial-coverage rows.",
                "code_module": "src.harmonization.build",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
        ]
    )
    transformations = pd.concat([prior, panel_transformations], ignore_index=True)
    transformations = transformations.drop_duplicates(
        ["transformation_id", "source_vintage"], keep="last"
    )
    transformations.to_csv(transformation_path, index=False)
    logger.info(
        "Built fact=%s, county=%s, ZIP=%s observations/rows",
        len(fact),
        len(county),
        len(zip_panel),
    )
    return {
        "fact": fact,
        "county": county,
        "zip": zip_panel,
        "state_quarterly": state_quarterly,
        "geography": geography,
        "crosswalk": crosswalk,
    }
