"""Automated QA reports; structural failures raise, outliers warn."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import duckdb
import pandas as pd

from src.common.config import ProjectConfig


@dataclass
class CheckResult:
    check_id: str
    severity: str
    status: str
    failure_count: int
    detail: str


def _result(
    check_id: str, severity: str, failures: pd.DataFrame, detail: str
) -> CheckResult:
    count = len(failures)
    return CheckResult(
        check_id,
        severity,
        "fail" if count and severity == "error" else "warn" if count else "pass",
        count,
        detail,
    )


def _failure_rows(
    check_id: str, severity: str, frame: pd.DataFrame, limit: int = 1000
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result = frame.head(limit).copy()
    result.insert(0, "severity", severity)
    result.insert(0, "check_id", check_id)
    return result


def _coverage_reports(
    config: ProjectConfig, fact: pd.DataFrame, zip_panel: pd.DataFrame
) -> None:
    coverage_dir = config.root / "output" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    work = fact.copy()
    work["geo_type"] = work["geo_id"].map(
        lambda item: "postal_zip"
        if str(item).startswith("ZIP_")
        else "county"
        if str(item).startswith("COUNTY_")
        else "state"
        if str(item).startswith("STATE_")
        else "zcta"
        if str(item).startswith("ZCTA_")
        else "metropolitan_division"
        if str(item).startswith("METRODIV_")
        else "metro"
        if str(item).startswith("METRO_")
        else "national"
    )
    source_coverage = (
        work.groupby(["provider", "variable_id", "geo_type"], dropna=False)
        .agg(
            first_observation=("observation_date", "min"),
            latest_observation=("observation_date", "max"),
            observation_count=("value", "size"),
            nonnull_count=("value", "count"),
            geography_count=("geo_id", "nunique"),
            latest_source_vintage=("source_vintage", "max"),
        )
        .reset_index()
    )
    source_coverage.to_csv(coverage_dir / "source_coverage.csv", index=False)
    source_coverage.to_csv(config.root / "metadata" / "source_coverage.csv", index=False)
    if zip_panel.empty:
        zip_coverage = pd.DataFrame(
            columns=[
                "postal_zip",
                "first_observation",
                "latest_observation",
                "months",
                "zhvi_months",
                "zori_months",
                "redfin_months",
            ]
        )
    else:
        zip_coverage = (
            zip_panel.groupby("postal_zip")
            .agg(
                first_observation=("date", "min"),
                latest_observation=("date", "max"),
                months=("date", "size"),
                zhvi_months=("zhvi_available", "sum"),
                zori_months=("zori_available", "sum"),
                redfin_months=("redfin_available", "sum"),
            )
            .reset_index()
        )
    zip_coverage.to_csv(coverage_dir / "zip_coverage.csv", index=False)
    monthly = work.loc[work["final_frequency"].eq("monthly")].copy()
    monthly["period"] = monthly["observation_date"].dt.to_period("M")
    time_coverage = (
        monthly.groupby(["geo_id", "variable_id"])
        .agg(
            first_observation=("observation_date", "min"),
            latest_observation=("observation_date", "max"),
            observations=("observation_date", "nunique"),
            unique_months=("period", "nunique"),
        )
        .reset_index()
    )
    span = (
        (time_coverage["latest_observation"].dt.year - time_coverage["first_observation"].dt.year)
        * 12
        + time_coverage["latest_observation"].dt.month
        - time_coverage["first_observation"].dt.month
        + 1
    )
    time_coverage["missing_months_within_span"] = span - time_coverage["unique_months"]
    time_coverage.to_csv(coverage_dir / "time_coverage.csv", index=False)


def run_qa(config: ProjectConfig, raise_on_error: bool = True) -> pd.DataFrame:
    logger = logging.getLogger("la_housing.qa")
    final = config.root / "data" / "final"
    fact = pd.read_parquet(final / "fact_housing_observation.parquet")
    county = pd.read_parquet(final / "county_monthly_panel.parquet")
    zip_panel = pd.read_parquet(final / "zip_monthly_panel.parquet")
    metro_monthly = pd.read_parquet(final / "metro_monthly_controls.parquet")
    state_quarterly = pd.read_parquet(final / "state_quarterly_panel.parquet")
    metro_quarterly = pd.read_parquet(final / "metro_quarterly_panel.parquet")
    county_annual = pd.read_parquet(final / "county_annual_structural.parquet")
    zcta_annual = pd.read_parquet(final / "zcta_annual_structural.parquet")
    fact["observation_date"] = pd.to_datetime(fact["observation_date"])
    county["date"] = pd.to_datetime(county["date"])
    if not zip_panel.empty:
        zip_panel["date"] = pd.to_datetime(zip_panel["date"])
    results: list[CheckResult] = []
    failures: list[pd.DataFrame] = []

    def check(check_id: str, severity: str, frame: pd.DataFrame, detail: str) -> None:
        results.append(_result(check_id, severity, frame, detail))
        failure = _failure_rows(check_id, severity, frame)
        if not failure.empty:
            failures.append(failure)

    primary_key = ["observation_date", "geo_id", "variable_id", "source_vintage"]
    duplicate_fact = fact.loc[fact.duplicated(primary_key, keep=False), primary_key]
    check("duplicate_fact_primary_key", "error", duplicate_fact, "Fact PK must be unique.")
    observation_key = ["observation_date", "geo_id", "variable_id"]
    duplicate_observation = fact.loc[
        fact.duplicated(observation_key, keep=False), observation_key
    ]
    check(
        "duplicate_fact_observation_key",
        "error",
        duplicate_observation,
        "Selected fact data must have one geography-date-variable observation.",
    )
    dictionary_ids = {
        item["variable_id"] for item in config.variables
    }
    unmapped_variables = fact.loc[
        ~fact["variable_id"].isin(dictionary_ids), ["variable_id"]
    ].drop_duplicates()
    check(
        "fact_variable_dictionary_mapping",
        "error",
        unmapped_variables,
        "Every fact variable must map to the variable dictionary.",
    )
    active_zori = fact.loc[
        fact["variable_id"].astype(str).str.startswith("zori")
    ]
    zori_adjustment_error = active_zori.loc[
        ~active_zori["variable_id"].eq("zori_smoothed_sa")
        | ~active_zori["is_seasonally_adjusted"].fillna(False)
    ]
    check(
        "zori_adjustment_identity",
        "error",
        zori_adjustment_error,
        "The only active ZORI fact variable must be seasonally adjusted zori_smoothed_sa.",
    )
    bps = fact.loc[fact["provider"].eq("U.S. Census Bureau")].copy()
    bps_key = ["observation_date", "geo_id", "variable_id"]
    duplicate_bps = bps.loc[bps.duplicated(bps_key, keep=False), bps_key]
    check(
        "duplicate_bps_geography_month_variable",
        "error",
        duplicate_bps,
        "BPS must contain one selected archive record per geography-month-variable.",
    )
    invalid_bps_fips = bps.loc[
        (
            bps["geo_id"].eq("COUNTY_06037")
            & (
                ~bps["state_fips"].astype(str).eq("06")
                | ~bps["county_fips"].astype(str).eq("06037")
            )
        )
        | (
            bps["geo_id"].eq("STATE_06")
            & ~bps["state_fips"].astype(str).eq("06")
        ),
        [*bps_key, "state_fips", "county_fips"],
    ]
    check(
        "bps_fips_preserved",
        "error",
        invalid_bps_fips,
        "BPS state and county FIPS must remain fixed-width strings.",
    )
    missing_key = fact.loc[fact[primary_key[:3]].isna().any(axis=1), primary_key]
    check("missing_fact_key", "error", missing_key, "Dates and geography/variable IDs are required.")
    invalid_geo = fact.loc[
        ~fact["geo_id"].astype(str).str.match(
            r"^(US|STATE_\d{2}|COUNTY_\d{5}|ZIP_\d{5}|"
            r"ZCTA_\d{5}|METRO_\d{5}|METRODIV_\d{5})$"
        ),
        primary_key,
    ]
    check("geography_format", "error", invalid_geo, "FIPS and ZIP IDs must retain fixed widths.")
    invalid_zip = zip_panel.loc[
        ~zip_panel.get("postal_zip", pd.Series(dtype=str)).astype(str).str.match(r"^\d{5}$")
    ]
    check("zip_format", "error", invalid_zip, "Postal ZIP must be five digits.")
    nonnegative = fact.loc[
        fact["variable_id"].isin(
            {
                "zhvi",
                "zori_smoothed_sa",
                "median_sale_price",
                "active_inventory",
                "new_listings",
                "homes_sold",
                "pending_sales",
                "months_of_supply",
                "days_on_market",
                "unemployment_rate",
                "employment",
                "unemployment",
                "labor_force",
                "mortgage_rate_30y_weekly",
                "mortgage_rate_30y_monthly_average",
                "mortgage_rate_30y_month_end",
                "permits_total",
                "permits_1_unit",
                "permits_2_to_4_units",
                "permits_5_plus_units",
            }
        )
        & fact["value"].lt(0)
    ]
    check("invalid_negative_value", "error", nonnegative, "Configured level variables cannot be negative.")
    check(
        "duplicate_county_month",
        "error",
        county.loc[county.duplicated("date", keep=False)],
        "County panel must contain one row per month.",
    )
    check(
        "duplicate_zip_month",
        "error",
        zip_panel.loc[zip_panel.duplicated(["date", "postal_zip"], keep=False)]
        if not zip_panel.empty
        else pd.DataFrame(),
        "ZIP panel must contain one row per postal ZIP-month.",
    )
    panel_key_checks = [
        (
            "duplicate_metro_month",
            metro_monthly,
            ["date"],
        ),
        (
            "duplicate_state_quarter",
            state_quarterly,
            ["quarter_end", "state_fips"],
        ),
        (
            "duplicate_metro_quarter",
            metro_quarterly,
            ["quarter_end"],
        ),
        (
            "duplicate_county_year",
            county_annual,
            ["year_end", "county_fips"],
        ),
        (
            "duplicate_zcta_year",
            zcta_annual,
            ["year_end", "zcta"],
        ),
    ]
    for check_id, panel, key in panel_key_checks:
        check(
            check_id,
            "error",
            panel.loc[panel.duplicated(key, keep=False), key],
            f"Panel key must be unique: {key}.",
        )
    invalid_zcta = zcta_annual.loc[
        ~zcta_annual["zcta"].astype(str).str.fullmatch(r"\d{5}")
    ]
    check(
        "zcta_identifier_format",
        "error",
        invalid_zcta,
        "Census ZCTA identifiers must remain five-character strings.",
    )
    metro_variables = fact["variable_id"].str.startswith(
        ("ces_", "cpi_", "hvs_rental_vacancy_rate_la_", "fhfa_hpi_la_metropolitan_")
    )
    mislabeled_metro = fact.loc[
        metro_variables
        & ~fact["geo_id"].str.startswith(("METRO_", "METRODIV_"))
    ]
    check(
        "metro_geography_not_county",
        "error",
        mislabeled_metro,
        "Metro and metropolitan-division variables cannot be labeled as county.",
    )
    annual_ids = {
        item["variable_id"]
        for item in config.variables
        if item.get("final_frequency") == "annual"
    }
    annual_expanded = fact.loc[
        fact["variable_id"].isin(annual_ids)
        & ~fact["final_frequency"].eq("annual")
    ]
    check(
        "annual_not_expanded_to_monthly",
        "error",
        annual_expanded,
        "Annual variables must remain annual.",
    )
    quarterly_ids = {
        item["variable_id"]
        for item in config.variables
        if item.get("final_frequency") == "quarterly"
    }
    quarterly_expanded = fact.loc[
        fact["variable_id"].isin(quarterly_ids)
        & ~fact["final_frequency"].eq("quarterly")
    ]
    check(
        "quarterly_not_expanded_to_monthly",
        "error",
        quarterly_expanded,
        "Quarterly variables must remain quarterly.",
    )
    rental_inventory_mislabel = fact.loc[
        fact["variable_id"].str.contains("rental_inventory", na=False)
        | (
            fact["variable_id"].eq("zori_smoothed_sa")
            & fact["unit"].str.contains("inventory", case=False, na=False)
        )
    ]
    check(
        "zori_not_rental_inventory",
        "error",
        rental_inventory_mislabel,
        "ZORI must never be labeled as rental inventory.",
    )
    catalog_path = config.root / "metadata" / "data_catalog.csv"
    if catalog_path.exists():
        catalog = pd.read_csv(catalog_path, dtype=str)
        expected_first = [
            "Source",
            "Frequency",
            "Geography",
            "Key variables",
        ]
        invalid_catalog_columns = (
            pd.DataFrame()
            if catalog.columns[:4].tolist() == expected_first
            else pd.DataFrame(
                [{"actual": "|".join(catalog.columns[:4])}]
            )
        )
        check(
            "catalog_first_four_columns",
            "error",
            invalid_catalog_columns,
            "Catalog first four columns must match the assignment contract.",
        )
        missing_catalog_sources = pd.DataFrame(
            {
                "source_id": sorted(
                    {
                        source.source_id
                        for source in config.sources
                        if source.enabled
                    }
                    - set(catalog["source_id"])
                )
            }
        )
        check(
            "active_source_catalog_mapping",
            "error",
            missing_catalog_sources,
            "Every active configured source must have a catalog row.",
        )
    raw_completeness_path = (
        config.root / "output" / "qa" / "raw_repository_completeness.csv"
    )
    if raw_completeness_path.exists():
        raw_completeness = pd.read_csv(
            raw_completeness_path, dtype=str
        )
        check(
            "raw_repository_completeness",
            "error",
            raw_completeness.loc[
                ~raw_completeness["completeness_status"].eq("PASS")
            ],
            "Every active implemented source must pass raw completeness.",
        )
    redfin_zip_path = (
        config.root
        / "data"
        / "clean"
        / "redfin_zip_monthly_la_county.parquet"
    )
    if redfin_zip_path.exists():
        redfin_zip = pd.read_parquet(redfin_zip_path)
        redfin_zip["period_begin"] = pd.to_datetime(
            redfin_zip["period_begin"]
        )
        redfin_zip["period_end"] = pd.to_datetime(
            redfin_zip["period_end"]
        )
        redfin_key = ["postal_zip", "period_end", "source_vintage"]
        check(
            "redfin_zip_duplicate_key",
            "error",
            redfin_zip.loc[
                redfin_zip.duplicated(redfin_key, keep=False), redfin_key
            ],
            "Redfin ZIP clean rows must be unique by ZIP, period end, and source vintage.",
        )
        check(
            "redfin_zip_identifier_format",
            "error",
            redfin_zip.loc[
                ~redfin_zip["postal_zip"]
                .astype(str)
                .str.fullmatch(r"\d{5}")
            ],
            "Redfin postal ZIP identifiers must be five-character strings.",
        )
        invalid_method = redfin_zip.loc[
            ~redfin_zip["region_type"].eq("Zip")
            | ~redfin_zip["frequency"].eq("Rolling 3 Months")
            | ~redfin_zip["rolling_window_months"].eq(3)
        ]
        check(
            "redfin_zip_source_method",
            "error",
            invalid_method,
            "Loaded Redfin rows must be ZIP regions with Rolling 3 Months frequency.",
        )
        window_months = (
            (
                redfin_zip["period_end"].dt.year
                - redfin_zip["period_begin"].dt.year
            )
            * 12
            + redfin_zip["period_end"].dt.month
            - redfin_zip["period_begin"].dt.month
            + 1
        )
        invalid_window = redfin_zip.loc[
            redfin_zip["period_begin"].gt(redfin_zip["period_end"])
            | window_months.ne(3)
            | redfin_zip["period_begin"].dt.day.ne(1)
            | redfin_zip["period_end"].ne(
                redfin_zip["period_end"] + pd.offsets.MonthEnd(0)
            )
        ]
        check(
            "redfin_zip_window_dates",
            "error",
            invalid_window,
            "Redfin windows must span three calendar months from month start to month end.",
        )
        redfin_levels = [
            "homes_sold",
            "median_sale_price_nsa",
            "median_days_on_market",
            "new_listings",
            "active_listings",
            "pending_sales",
        ]
        invalid_redfin_levels = redfin_zip.loc[
            redfin_zip[redfin_levels].lt(0).any(axis=1)
        ]
        check(
            "redfin_zip_nonnegative_levels",
            "error",
            invalid_redfin_levels,
            "Redfin ZIP Key Metrics level values cannot be negative.",
        )
        percentage_columns = [
            column
            for column in redfin_zip.columns
            if column.endswith("_yoy_pct_provider")
        ]
        extreme_percentages = redfin_zip.loc[
            redfin_zip[percentage_columns].abs().gt(1000).any(axis=1)
        ]
        check(
            "redfin_zip_provider_percentage_review",
            "warning",
            extreme_percentages,
            "Provider YoY values above 1,000 percentage points require review but are retained.",
        )
        geography = pd.read_parquet(
            config.root / "data" / "clean" / "geography.parquet"
        )
        eligible_zips = set(
            geography.loc[
                geography["county_fips"].astype(str).eq("06037"),
                "postal_zip",
            ]
            .dropna()
            .astype(str)
        )
        invalid_county = redfin_zip.loc[
            ~redfin_zip["postal_zip"].astype(str).isin(eligible_zips)
            | ~redfin_zip["primary_county_fips"].astype(str).eq("06037")
        ]
        check(
            "redfin_zip_verified_la_county",
            "error",
            invalid_county,
            "Only ZIPs in the versioned LA County geography reference may enter the clean subset.",
        )
        expected_spine = fact.loc[
            fact["geo_id"].astype(str).str.startswith("ZIP_")
            & fact["variable_id"].isin({"zhvi", "zori_smoothed_sa"}),
            ["observation_date", "geo_id"],
        ].drop_duplicates()
        expected_spine["postal_zip"] = expected_spine.pop(
            "geo_id"
        ).str.removeprefix("ZIP_")
        expected_spine = expected_spine.rename(
            columns={"observation_date": "date"}
        )
        actual_spine = zip_panel[["date", "postal_zip"]].drop_duplicates()
        spine_difference = expected_spine.merge(
            actual_spine,
            on=["date", "postal_zip"],
            how="outer",
            indicator=True,
        )
        check(
            "redfin_zip_left_join_preserves_zillow_spine",
            "error",
            spine_difference.loc[spine_difference["_merge"].ne("both")],
            "Redfin must be left-joined without adding or removing Zillow ZIP-month keys.",
        )
        redfin_panel = redfin_zip.merge(
            zip_panel,
            left_on=["period_end", "postal_zip"],
            right_on=["date", "postal_zip"],
            how="left",
            suffixes=("_clean", "_panel"),
        )
        panel_mismatch_rows: list[pd.DataFrame] = []
        panel_pairs = {
            "homes_sold": "homes_sold",
            "median_sale_price_nsa": "median_sale_price_nsa",
            "median_days_on_market": "median_days_on_market",
            "new_listings": "new_listings",
            "active_listings": "active_listings",
            "pending_sales": "pending_sales",
        }
        for clean_column, panel_column in panel_pairs.items():
            clean_values = redfin_panel[f"{clean_column}_clean"]
            panel_values = redfin_panel[f"{panel_column}_panel"]
            mismatch = ~(
                clean_values.eq(panel_values)
                | (clean_values.isna() & panel_values.isna())
            )
            if mismatch.any():
                rows = redfin_panel.loc[
                    mismatch, ["postal_zip", "period_end"]
                ].copy()
                rows["metric"] = clean_column
                panel_mismatch_rows.append(rows)
        check(
            "redfin_zip_panel_matches_clean",
            "error",
            pd.concat(panel_mismatch_rows, ignore_index=True)
            if panel_mismatch_rows
            else pd.DataFrame(),
            "ZIP panel Redfin levels must exactly match clean Key Metrics values.",
        )
        yoy_pairs = {
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
        yoy_discrepancies = []
        for metric, (provider_column, calculated_column) in yoy_pairs.items():
            comparable = zip_panel.loc[
                zip_panel[provider_column].notna()
                & zip_panel[calculated_column].notna(),
                ["date", "postal_zip", provider_column, calculated_column],
            ].copy()
            comparable["absolute_difference_pp"] = (
                comparable[provider_column]
                - comparable[calculated_column]
            ).abs()
            material = comparable.loc[
                comparable["absolute_difference_pp"].gt(1.0)
            ].copy()
            if not material.empty:
                material["metric"] = metric
                yoy_discrepancies.append(material)
        check(
            "redfin_zip_provider_calculated_yoy",
            "warning",
            pd.concat(yoy_discrepancies, ignore_index=True)
            if yoy_discrepancies
            else pd.DataFrame(),
            "Provider and exact 12-month calculated YoY values differing by over one percentage point require review.",
        )
    sa_fact = (
        fact.loc[fact["variable_id"].eq("zori_smoothed_sa")]
        .sort_values(["source_vintage", "retrieved_at"])
        .drop_duplicates(["observation_date", "geo_id"], keep="last")
    )
    county_sa = sa_fact.loc[
        sa_fact["geo_id"].eq("COUNTY_06037"),
        ["observation_date", "value"],
    ].rename(columns={"observation_date": "date", "value": "clean_sa_zori"})
    county_zori_comparison = county[["date", "zori"]].merge(
        county_sa, on="date", how="left"
    )
    county_primary_mismatch = county_zori_comparison.loc[
        ~(
            county_zori_comparison["zori"].eq(
                county_zori_comparison["clean_sa_zori"]
            )
            | (
                county_zori_comparison["zori"].isna()
                & county_zori_comparison["clean_sa_zori"].isna()
            )
        )
    ]
    check(
        "county_primary_zori_is_sa",
        "error",
        county_primary_mismatch,
        "County primary zori must equal the active county SA clean series exactly.",
    )
    if zip_panel.empty:
        zip_primary_mismatch = pd.DataFrame()
    else:
        zip_sa = sa_fact.loc[
            sa_fact["geo_id"].astype(str).str.startswith("ZIP_"),
            ["observation_date", "geo_id", "value"],
        ].copy()
        zip_sa["postal_zip"] = zip_sa.pop("geo_id").str.removeprefix("ZIP_")
        zip_sa = zip_sa.rename(
            columns={"observation_date": "date", "value": "clean_sa_zori"}
        )
        zip_zori_comparison = zip_panel[["date", "postal_zip", "zori"]].merge(
            zip_sa, on=["date", "postal_zip"], how="left"
        )
        zip_primary_mismatch = zip_zori_comparison.loc[
            ~(
                zip_zori_comparison["zori"].eq(
                    zip_zori_comparison["clean_sa_zori"]
                )
                | (
                    zip_zori_comparison["zori"].isna()
                    & zip_zori_comparison["clean_sa_zori"].isna()
                )
            )
        ]
    check(
        "zip_primary_zori_is_sa",
        "error",
        zip_primary_mismatch,
        "ZIP primary zori must equal the active ZIP SA clean series exactly.",
    )
    ordered_county = county.sort_values("date").copy()
    expected_features = {
        "zori_growth_mom": ordered_county["zori"].pct_change(
            1, fill_method=None
        ),
        "zori_growth_yoy": ordered_county["zori"].pct_change(
            12, fill_method=None
        ),
        "price_to_rent_ratio": ordered_county["zhvi"]
        / (12.0 * ordered_county["zori"]),
    }
    for down_payment in ("10pct", "20pct"):
        payment = f"mortgage_payment_{down_payment}_down"
        ratio = f"mortgage_payment_to_rent_{down_payment}_down"
        if payment in ordered_county:
            expected_features[ratio] = (
                ordered_county[payment]
                / ordered_county["zori"]
            )
    derived_failures: list[pd.DataFrame] = []
    for column, expected in expected_features.items():
        actual = ordered_county[column]
        mismatch = ~(
            (actual.isna() & expected.isna())
            | (actual.sub(expected).abs() <= 1e-10)
        )
        if mismatch.any():
            rows = ordered_county.loc[mismatch, ["date"]].copy()
            rows["variable"] = column
            rows["actual"] = actual.loc[mismatch]
            rows["expected_from_sa"] = expected.loc[mismatch]
            derived_failures.append(rows)
    check(
        "zori_derived_features_use_sa",
        "error",
        pd.concat(derived_failures, ignore_index=True)
        if derived_failures
        else pd.DataFrame(),
        "ZORI growth, price-to-rent, and payment-to-rent must use SA ZORI.",
    )
    monthly = fact.loc[fact["final_frequency"].eq("monthly")].copy()
    gap_rows: list[dict] = []
    for (geo_id, variable_id), group in monthly.groupby(["geo_id", "variable_id"]):
        periods = group["observation_date"].dt.to_period("M").drop_duplicates().sort_values()
        if len(periods) > 1:
            gaps = periods.astype(int).diff().dropna()
            if gaps.gt(1).any():
                gap_rows.append(
                    {
                        "geo_id": geo_id,
                        "variable_id": variable_id,
                        "largest_gap_months": int(gaps.max()),
                    }
                )
    check(
        "non_monthly_gaps",
        "warning",
        pd.DataFrame(gap_rows),
        "Gaps are retained, never silently interpolated.",
    )
    stale = (
        monthly.groupby(["provider", "variable_id"])["observation_date"]
        .max()
        .reset_index()
    )
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(
        days=int(config.pipeline["stale_after_days"])
    )
    stale = stale.loc[stale["observation_date"] < cutoff]
    check("stale_latest_observation", "warning", stale, f"Latest date precedes {cutoff.date()}.")
    extreme_columns = [
        column
        for column in county
        if column.endswith("_growth_mom") or column.endswith("_growth_yoy")
    ]
    outliers: list[pd.DataFrame] = []
    for column in extreme_columns:
        threshold = (
            float(config.pipeline["extreme_mom_threshold"])
            if column.endswith("_mom")
            else float(config.pipeline["extreme_yoy_threshold"])
        )
        flagged = county.loc[county[column].abs() > threshold, ["date", column]].copy()
        if not flagged.empty:
            flagged["variable"] = column
            flagged["threshold"] = threshold
            flagged["value"] = flagged.pop(column)
            outliers.append(flagged)
    check(
        "extreme_economic_movement",
        "warning",
        pd.concat(outliers, ignore_index=True) if outliers else pd.DataFrame(),
        "Plausible outliers are warnings and remain in the data.",
    )
    if not zip_panel.empty and "zhvi" in zip_panel and "zhvi" in county:
        bounds = (
            zip_panel.groupby("date")["zhvi"].agg(zip_min="min", zip_max="max").reset_index()
        )
        comparison = county[["date", "zhvi"]].merge(bounds, on="date", how="inner")
        inconsistent = comparison.loc[
            comparison["zhvi"].notna()
            & (
                (comparison["zhvi"] < comparison["zip_min"] * 0.5)
                | (comparison["zhvi"] > comparison["zip_max"] * 1.5)
            )
        ]
    else:
        inconsistent = pd.DataFrame()
    check(
        "county_zip_consistency",
        "warning",
        inconsistent,
        "County ZHVI is checked against a broad ZIP range; levels are not aggregated as equivalent.",
    )
    weekly = fact.loc[fact["variable_id"].eq("mortgage_rate_30y_weekly")].copy()
    reported_monthly = fact.loc[
        fact["variable_id"].eq("mortgage_rate_30y_monthly_average"),
        ["observation_date", "value"],
    ].rename(columns={"value": "reported"})
    if not weekly.empty and not reported_monthly.empty:
        calculated = (
            weekly.assign(month=weekly["observation_date"].dt.to_period("M"))
            .groupby("month")["value"]
            .mean()
            .rename("calculated")
            .reset_index()
        )
        calculated["observation_date"] = calculated["month"].dt.to_timestamp("M")
        comparison = reported_monthly.merge(calculated, on="observation_date")
        aggregation_error = comparison.loc[
            (comparison["reported"] - comparison["calculated"]).abs() > 1e-10
        ]
    else:
        aggregation_error = pd.DataFrame()
    check(
        "mortgage_monthly_aggregation",
        "error",
        aggregation_error,
        "Monthly PMMS average must equal the arithmetic mean of weekly observations.",
    )

    _coverage_reports(config, fact, zip_panel)
    summary = pd.DataFrame([result.__dict__ for result in results])
    failure_output = (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame(columns=["check_id", "severity"])
    )
    qa_dir = config.root / "output" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(qa_dir / "qa_summary.csv", index=False)
    failure_output.to_csv(qa_dir / "qa_failures.csv", index=False)
    database_path = config.root / config.pipeline["database_path"]
    if database_path.exists():
        connection = duckdb.connect(str(database_path))
        connection.register("qa_summary_frame", summary)
        connection.register("qa_failures_frame", failure_output)
        connection.execute(
            "CREATE OR REPLACE TABLE quality.qa_summary AS SELECT * FROM qa_summary_frame"
        )
        connection.execute(
            "CREATE OR REPLACE TABLE quality.qa_failures AS SELECT * FROM qa_failures_frame"
        )
        connection.close()
    errors = summary.loc[
        summary["severity"].eq("error") & summary["failure_count"].gt(0)
    ]
    warnings = summary.loc[
        summary["severity"].eq("warning") & summary["failure_count"].gt(0)
    ]
    logger.info("QA completed: %s errors, %s warning checks", len(errors), len(warnings))
    if raise_on_error and not errors.empty:
        names = ", ".join(errors["check_id"])
        raise RuntimeError(f"Structural QA failed: {names}")
    return summary
