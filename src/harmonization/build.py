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
    "mortgage_rate_30y_monthly_median",
    "mortgage_rate_30y_month_end",
    "mortgage_rate_30y_observation_count",
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

B25106_MORTGAGE_DEBT_BINS = {
    "acs_b25106_owner_less_20k_cost_lt20pct": (10_000.0, 0.10),
    "acs_b25106_owner_less_20k_cost_20_29pct": (10_000.0, 0.25),
    "acs_b25106_owner_less_20k_cost_30pluspct": (10_000.0, 0.40),
    "acs_b25106_owner_20k_34999_cost_lt20pct": (27_500.0, 0.10),
    "acs_b25106_owner_20k_34999_cost_20_29pct": (27_500.0, 0.25),
    "acs_b25106_owner_20k_34999_cost_30pluspct": (27_500.0, 0.40),
    "acs_b25106_owner_35k_49999_cost_lt20pct": (42_500.0, 0.10),
    "acs_b25106_owner_35k_49999_cost_20_29pct": (42_500.0, 0.25),
    "acs_b25106_owner_35k_49999_cost_30pluspct": (42_500.0, 0.40),
    "acs_b25106_owner_50k_74999_cost_lt20pct": (62_500.0, 0.10),
    "acs_b25106_owner_50k_74999_cost_20_29pct": (62_500.0, 0.25),
    "acs_b25106_owner_50k_74999_cost_30pluspct": (62_500.0, 0.40),
    "acs_b25106_owner_75kplus_cost_lt20pct": (112_500.0, 0.10),
    "acs_b25106_owner_75kplus_cost_20_29pct": (112_500.0, 0.25),
    "acs_b25106_owner_75kplus_cost_30pluspct": (112_500.0, 0.40),
}

MORTGAGE_DEBT_PROXY_DEBT_SERVICE_SHARE = 0.80
MORTGAGE_DEBT_PROXY_REMAINING_TERM_YEARS = 25.0


def _read_clean_files(config: ProjectConfig) -> pd.DataFrame:
    root = config.root
    files: list[Path] = []
    for source in config.sources:
        if not source.enabled:
            continue
        path = root / "data" / "clean" / f"{source.source_id}.parquet"
        if source.source_id == "redfin_zip_monthly":
            path = (
                root
                / "data"
                / "interim"
                / "redfin_zip_monthly_observations.parquet"
            )
        if path.exists():
            files.append(path)
    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        for column in OBSERVATION_COLUMNS:
            if column not in frame:
                frame[column] = None
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame["reported_value"] = pd.to_numeric(
            frame["reported_value"], errors="coerce"
        )
        frame["checksum_sha256"] = frame["checksum_sha256"].astype("string")
        frame["provider_last_updated"] = frame["provider_last_updated"].astype("string")
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


def _wide_control_panel(
    fact: pd.DataFrame,
    *,
    geo_prefixes: tuple[str, ...],
    frequency: str,
    id_columns: dict[str, str],
) -> pd.DataFrame:
    selected = _latest_values(
        fact.loc[
            fact["geo_id"].astype(str).str.startswith(geo_prefixes)
            & fact["final_frequency"].eq(frequency)
        ]
    )
    if selected.empty:
        return pd.DataFrame(columns=["date", *id_columns])
    panel = (
        selected.pivot_table(
            index=["observation_date", "geo_id"],
            columns="variable_id",
            values="value",
            aggfunc="last",
        )
        .reset_index()
        .rename(columns={"observation_date": "date"})
    )
    for column, value in id_columns.items():
        panel[column] = value
    vintages = (
        selected.groupby(["observation_date", "geo_id"])["source_vintage"]
        .max()
        .rename("latest_source_vintage")
        .reset_index()
        .rename(columns={"observation_date": "date"})
    )
    panel = panel.merge(vintages, on=["date", "geo_id"], how="left")
    variable_columns = [
        column
        for column in panel.columns
        if column not in {"date", "geo_id", *id_columns, "latest_source_vintage"}
    ]
    panel["source_coverage_count"] = panel[variable_columns].notna().sum(axis=1)
    return panel.sort_values(["geo_id", "date"])


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


def _annuity_principal(monthly_payment: float, annual_rate_pct: float) -> float:
    monthly_rate = annual_rate_pct / 100.0 / 12.0
    periods = int(MORTGAGE_DEBT_PROXY_REMAINING_TERM_YEARS * 12)
    if monthly_rate <= 0:
        return monthly_payment * periods
    return monthly_payment * (1.0 - (1.0 + monthly_rate) ** (-periods)) / monthly_rate


def _pmms_average_rate_for_acs_window(
    fact: pd.DataFrame, observation_date: pd.Timestamp
) -> float | None:
    end = pd.Timestamp(observation_date)
    start = pd.Timestamp(year=end.year - 4, month=1, day=1)
    rates = fact.loc[
        fact["geo_id"].eq("US")
        & fact["variable_id"].eq("mortgage_rate_30y_monthly_average")
        & fact["observation_date"].between(start, end),
        "value",
    ]
    rates = pd.to_numeric(rates, errors="coerce").dropna()
    if rates.empty:
        return None
    return float(rates.mean())


def _add_acs_mortgage_debt_proxy(
    county_annual: pd.DataFrame, fact: pd.DataFrame
) -> pd.DataFrame:
    required = {
        "acs_owner_units_with_mortgage",
        *B25106_MORTGAGE_DEBT_BINS.keys(),
    }
    if not required.issubset(county_annual.columns):
        for column in [
            "acs_implied_aggregate_mortgage_debt_proxy",
            "acs_implied_average_mortgage_balance_proxy",
            "acs_implied_annual_mortgage_debt_service_proxy",
            "acs_mortgage_debt_proxy_rate_assumption",
            "acs_mortgage_debt_proxy_term_years_assumption",
            "acs_mortgage_debt_proxy_debt_service_share_assumption",
        ]:
            county_annual[column] = pd.NA
        return county_annual
    result = county_annual.copy()
    for index, row in result.iterrows():
        owner_units_with_mortgage = pd.to_numeric(
            row.get("acs_owner_units_with_mortgage"), errors="coerce"
        )
        if pd.isna(owner_units_with_mortgage) or owner_units_with_mortgage <= 0:
            continue
        bin_counts = {
            column: pd.to_numeric(row.get(column), errors="coerce")
            for column in B25106_MORTGAGE_DEBT_BINS
        }
        positive_bin_total = sum(
            float(value) for value in bin_counts.values() if pd.notna(value)
        )
        if positive_bin_total <= 0:
            continue
        rate = _pmms_average_rate_for_acs_window(fact, row["date"])
        if rate is None:
            continue
        aggregate_debt = 0.0
        annual_debt_service = 0.0
        for column, count in bin_counts.items():
            if pd.isna(count) or count <= 0:
                continue
            income_midpoint, burden_midpoint = B25106_MORTGAGE_DEBT_BINS[column]
            allocated_mortgaged_units = (
                float(count) / positive_bin_total * float(owner_units_with_mortgage)
            )
            monthly_owner_cost = income_midpoint * burden_midpoint / 12.0
            monthly_debt_service = (
                monthly_owner_cost * MORTGAGE_DEBT_PROXY_DEBT_SERVICE_SHARE
            )
            aggregate_debt += allocated_mortgaged_units * _annuity_principal(
                monthly_debt_service, rate
            )
            annual_debt_service += allocated_mortgaged_units * monthly_debt_service * 12.0
        result.loc[index, "acs_implied_aggregate_mortgage_debt_proxy"] = aggregate_debt
        result.loc[index, "acs_implied_average_mortgage_balance_proxy"] = (
            aggregate_debt / float(owner_units_with_mortgage)
        )
        result.loc[index, "acs_implied_annual_mortgage_debt_service_proxy"] = (
            annual_debt_service
        )
        result.loc[index, "acs_mortgage_debt_proxy_rate_assumption"] = rate
        result.loc[index, "acs_mortgage_debt_proxy_term_years_assumption"] = (
            MORTGAGE_DEBT_PROXY_REMAINING_TERM_YEARS
        )
        result.loc[index, "acs_mortgage_debt_proxy_debt_service_share_assumption"] = (
            MORTGAGE_DEBT_PROXY_DEBT_SERVICE_SHARE
        )
    return result


def build_analytical_outputs(
    config: ProjectConfig,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    logger = logging.getLogger("la_housing.harmonize")
    fact = _date_filter(_read_clean_files(config), start_date, end_date)
    fact = fact.sort_values(
        ["observation_date", "geo_id", "variable_id", "source_vintage"]
    )
    county = _county_panel(fact, config)
    zip_panel = _zip_panel(fact, config)
    _redfin_yoy_report(config, zip_panel)
    state_quarterly = _wide_control_panel(
        fact,
        geo_prefixes=("STATE_",),
        frequency="quarterly",
        id_columns={"state_fips": "06", "state_name": "California"},
    )
    metro_monthly = _wide_control_panel(
        fact,
        geo_prefixes=("METRO_",),
        frequency="monthly",
        id_columns={"metro_name": "Los Angeles-Long Beach-Anaheim, CA"},
    )
    metro_quarterly = _wide_control_panel(
        fact,
        geo_prefixes=("METRO_", "METRODIV_"),
        frequency="quarterly",
        id_columns={"metro_name": "Los Angeles official metro/division geography"},
    )
    county_annual = _wide_control_panel(
        fact,
        geo_prefixes=("COUNTY_",),
        frequency="annual",
        id_columns={
            "county_fips": "06037",
            "county_name": "Los Angeles County",
            "state_fips": "06",
        },
    )
    if {
        "acs_median_home_value",
        "acs_median_household_income",
    }.issubset(county_annual.columns):
        county_annual["county_annual_price_to_income_acs"] = (
            county_annual["acs_median_home_value"]
            / county_annual["acs_median_household_income"].replace(0, pd.NA)
        )
    if {
        "acs_owner_occupied_units",
        "acs_median_home_value",
    }.issubset(county_annual.columns):
        county_annual["acs_owner_occupied_home_value_proxy"] = (
            county_annual["acs_owner_occupied_units"]
            * county_annual["acs_median_home_value"]
        )
    county_annual = _add_acs_mortgage_debt_proxy(county_annual, fact)
    if {
        "acs_owner_occupied_home_value_proxy",
        "acs_implied_aggregate_mortgage_debt_proxy",
    }.issubset(county_annual.columns):
        county_annual["acs_implied_aggregate_home_equity_proxy"] = (
            county_annual["acs_owner_occupied_home_value_proxy"]
            - county_annual["acs_implied_aggregate_mortgage_debt_proxy"]
        )
    final_dir = config.root / "data" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    fact.to_parquet(final_dir / "fact_housing_observation.parquet", index=False)
    county.to_parquet(final_dir / "county_monthly_panel.parquet", index=False)
    zip_panel.to_parquet(final_dir / "zip_monthly_panel.parquet", index=False)
    state_quarterly.to_parquet(
        final_dir / "state_quarterly_panel.parquet", index=False
    )
    metro_monthly.to_parquet(
        final_dir / "metro_monthly_controls.parquet", index=False
    )
    metro_quarterly.to_parquet(
        final_dir / "metro_quarterly_controls.parquet", index=False
    )
    county_annual.to_parquet(
        final_dir / "county_annual_controls.parquet", index=False
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
            {
                "transformation_id": "native_frequency_control_panels_v1",
                "source_id": "multiple",
                "input_file": "data/final/fact_housing_observation.parquet",
                "output_file": (
                    "data/final/state_quarterly_panel.parquet|"
                    "data/final/metro_monthly_controls.parquet|"
                    "data/final/metro_quarterly_controls.parquet|"
                    "data/final/county_annual_controls.parquet"
                ),
                "input_rows_or_shape": len(fact),
                "output_rows": (
                    len(state_quarterly)
                    + len(metro_monthly)
                    + len(metro_quarterly)
                    + len(county_annual)
                ),
                "description": (
                    "Pivot state, metro, and annual county controls at native "
                    "frequency without expanding them into monthly panels. "
                    "Also compute documented annual ACS proxies such as "
                    "owner-occupied housing value and implied aggregate mortgage "
                    "debt from B25106/B25081/Freddie Mac assumptions."
                ),
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
        "metro_monthly": metro_monthly,
        "metro_quarterly": metro_quarterly,
        "county_annual": county_annual,
        "geography": geography,
        "crosswalk": crosswalk,
    }
