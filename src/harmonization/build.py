"""Combine clean source observations and build county/ZIP panels."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.cleaning.common import OBSERVATION_COLUMNS, empty_observations
from src.common.config import ProjectConfig
from src.features.derived import (
    APARTMENT_COMPLETION_LAG_WEIGHTS,
    APARTMENT_COMPLETION_PROXY_COLUMNS,
    MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS,
    add_panel_features,
    apartment_completion_proxies,
    homebuying_cost_scenarios,
    implied_gross_rent_yield,
    multifamily_under_construction_proxies,
    price_to_rent_ratio,
)
from src.features.comparable_counties import (
    MARKET_TIGHTNESS_COMPONENTS,
    apply_pooled_zscores,
    county_housing_market_heat_index,
    exact_calendar_log_momentum,
    fit_county_heat_parameters,
    fit_market_tightness_parameters,
    home_price_momentum,
    la_relative_market_performance,
    laus_employment_momentum_yoy,
    market_tightness_index as market_tightness_index_3c,
    positive_price_to_rent_ratio,
)
from src.features.market_regimes import (
    APPLICATION_START as REGIME_APPLICATION_START,
    FEATURE_COLUMNS as REGIME_FEATURE_COLUMNS,
    MAX_ITERATIONS as REGIME_MAX_ITERATIONS,
    MODEL_VERSION as REGIME_MODEL_VERSION,
    N_CLUSTERS as REGIME_CLUSTER_COUNT,
    N_INIT as REGIME_N_INIT,
    RANDOM_SEED as REGIME_RANDOM_SEED,
    TRAINING_END as REGIME_TRAINING_END,
    TRAINING_START as REGIME_TRAINING_START,
    CONVERGENCE_TOLERANCE as REGIME_TOLERANCE,
    build_zip_market_regimes,
    load_market_regime_model,
)
from src.geography.build import build_geography
from src.features.macro_financial import macro_monthly_panel, MACRO_SOURCE_IDS
from src.housing_finance_monthly.metadata import register_monthly_finance_contracts
from src.harmonization.nine_constructions import build_nine_constructions

COUNTY_VARIABLES = [
    "zhvi",
    "zori_smoothed_sa",
    "median_sale_price",
    "active_inventory",
    "new_listings",
    "homes_sold",
    "months_of_supply",
    "days_on_market",
    "price_drop_share",
    "unemployment_rate",
    "employment",
    "unemployment",
    "labor_force",
    "permits_1_unit",
    "permits_5_plus_units",
]

COMPARABLE_COUNTY_VARIABLES = [
    "zhvi",
    "zori_smoothed_sa",
    "months_of_supply",
    "days_on_market",
    "price_drop_share",
    "employment",
    "unemployment_rate",
]

COMPARABLE_COUNTY_NAMES = {
    "06001": "Alameda County",
    "06037": "Los Angeles County",
    "06059": "Orange County",
    "06065": "Riverside County",
    "06067": "Sacramento County",
    "06071": "San Bernardino County",
    "06073": "San Diego County",
    "06085": "Santa Clara County",
}

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
]

STATE_QUARTERLY_VARIABLES = [
    "fhfa_hpi_ca_purchase_only_nsa",
    "fhfa_hpi_ca_purchase_only_sa",
    "bea_ca_personal_income_q_sa",
    "bea_ca_per_capita_personal_income_q_sa",
    "hvs_rental_vacancy_rate_ca",
    "hvs_rental_vacancy_rate_ca_moe",
]

METRO_QUARTERLY_VARIABLES = [
    "fhfa_hpi_la_metropolitan_division_purchase_only_nsa",
    "fhfa_hpi_la_metropolitan_division_purchase_only_sa",
    "hvs_rental_vacancy_rate_la_metro",
    "hvs_rental_vacancy_rate_la_metro_moe",
]

METRO_MONTHLY_VARIABLES = [
    "ces_total_nonfarm_employment_sa",
    "cpi_u_all_items_la_nsa",
]

COUNTY_ANNUAL_VARIABLES = [
    "fhfa_hpi_la_county_all_transactions_nsa_annual",
    "bea_la_county_personal_income_annual",
    "bea_la_county_per_capita_personal_income_annual",
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

ACS_B25032_STRUCTURE_GROUPS = {
    "acs_renter_occupied_1_unit_share": (
        "acs_b25032_renter_1_detached",
        "acs_b25032_renter_1_attached",
    ),
    "acs_renter_occupied_2_to_4_units_share": (
        "acs_b25032_renter_2_units",
        "acs_b25032_renter_3_to_4_units",
    ),
    "acs_renter_occupied_5_plus_units_share": (
        "acs_b25032_renter_5_to_9_units",
        "acs_b25032_renter_10_to_19_units",
        "acs_b25032_renter_20_to_49_units",
        "acs_b25032_renter_50_plus_units",
    ),
    "acs_renter_occupied_mobile_other_share": (
        "acs_b25032_renter_mobile_home",
        "acs_b25032_renter_boat_rv_other",
    ),
}

ACS_B25127_STRUCTURE_SUFFIXES = (
    "1_unit",
    "2_to_4_units",
    "5_to_19_units",
    "20_to_49_units",
    "50_plus_units",
    "mobile_other",
)

ACS_NONCONTROLLED_EXPOSURE_COLUMNS = (
    "noncontrolled_exposure_share_structural_proxy_lower_bound",
    "noncontrolled_exposure_share_structural_proxy_upper_bound",
)

NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS = (
    "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound",
    "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound",
)

ACS_NONCONTROLLED_STRUCTURAL_PROXY_START_YEAR = 2020
ACS_COMPONENT_RECONCILIATION_TOLERANCE = 1e-9


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
    result = pd.concat(frames, ignore_index=True)[OBSERVATION_COLUMNS]
    string_columns = [
        column
        for column in OBSERVATION_COLUMNS
        if column
        not in {
            "observation_date",
            "source_observation_date",
            "period_start",
            "period_end",
            "value",
            "is_imputed",
            "is_interpolated",
            "is_seasonally_adjusted",
        }
    ]
    for column in string_columns:
        result[column] = result[column].astype("string")
    return result


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


def _add_ca_doi_monthly_control(fact: pd.DataFrame) -> pd.DataFrame:
    """Repeat each annual California DOI premium across its calendar year.

    The governing methodology explicitly authorizes this step-function control
    for monthly or mixed-frequency models.  It remains a California annual
    source value: the derived rows retain the annual source date and provenance,
    use ``native_frequency=annual``, and are explicitly marked as carried annual
    controls.  This is not interpolation, an LA County estimate, or observed
    monthly data.
    """

    annual_id = "ca_homeowners_avg_written_premium"
    monthly_id = "ca_homeowners_avg_written_premium_monthly_control"
    annual = _latest_values(
        fact.loc[
            fact["geo_id"].eq("STATE_06")
            & fact["variable_id"].eq(annual_id)
            & fact["final_frequency"].eq("annual")
        ]
    )
    if annual.empty:
        return fact

    annual = annual.loc[pd.to_numeric(annual["value"], errors="coerce").notna()].copy()
    annual["observation_date"] = pd.to_datetime(annual["observation_date"])
    parts: list[pd.DataFrame] = []
    for month in range(1, 13):
        monthly = annual.copy()
        monthly["observation_date"] = pd.to_datetime(
            {
                "year": annual["observation_date"].dt.year,
                "month": month,
                "day": 1,
            }
        ) + pd.offsets.MonthEnd(0)
        monthly["variable_id"] = monthly_id
        monthly["reported_value"] = pd.NA
        monthly["native_frequency"] = "annual"
        monthly["final_frequency"] = "monthly"
        monthly["transformation_id"] = (
            "ca_doi_premium_calendar_year_monthly_control_v1"
        )
        monthly["is_imputed"] = False
        monthly["is_interpolated"] = False
        monthly["quality_flag"] = (
            monthly["quality_flag"].fillna("").astype(str).str.rstrip(";")
            + ";constructed_calendar_year_carry;carried_annual_control;"
            "mixed_frequency_control;not_observed_monthly;not_interpolated"
        ).str.lstrip(";")
        parts.append(monthly[OBSERVATION_COLUMNS])

    result = pd.concat([fact, *parts], ignore_index=True)[OBSERVATION_COLUMNS]
    return result.sort_values(
        ["observation_date", "geo_id", "variable_id", "source_vintage"]
    )


def _wide_by_date(
    fact: pd.DataFrame,
    variables: list[str],
    *,
    date_column: str,
) -> pd.DataFrame:
    selected = _latest_values(
        fact.loc[fact["variable_id"].isin(variables)].copy()
    )
    if selected.empty:
        return pd.DataFrame(columns=[date_column, *variables])
    panel = (
        selected.pivot(
            index="observation_date", columns="variable_id", values="value"
        )
        .reset_index()
        .rename(columns={"observation_date": date_column})
    )
    for variable in variables:
        if variable not in panel:
            panel[variable] = pd.NA
    return panel[[date_column, *variables]].sort_values(date_column)


def _slow_frequency_panels(
    fact: pd.DataFrame, county_monthly: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    state = _wide_by_date(
        fact.loc[
            fact["geo_id"].eq("STATE_06")
            & fact["final_frequency"].eq("quarterly")
        ],
        STATE_QUARTERLY_VARIABLES,
        date_column="quarter_end",
    )
    state.insert(1, "state_fips", "06")
    state.insert(2, "state_name", "California")
    state["bea_ca_personal_income_growth_qoq"] = state[
        "bea_ca_personal_income_q_sa"
    ].pct_change(1, fill_method=None)
    state["bea_ca_personal_income_growth_yoy"] = state[
        "bea_ca_personal_income_q_sa"
    ].pct_change(4, fill_method=None)
    state["hvs_rental_vacancy_change_yoy_pp"] = state[
        "hvs_rental_vacancy_rate_ca"
    ].diff(4)

    metro_monthly = _wide_by_date(
        fact.loc[fact["final_frequency"].eq("monthly")],
        METRO_MONTHLY_VARIABLES,
        date_column="date",
    )
    metro_monthly.insert(1, "ces_geography_id", "CBSA_31080")
    metro_monthly.insert(
        2,
        "ces_geography_name",
        "Los Angeles-Long Beach-Anaheim, CA MSA",
    )
    metro_monthly.insert(3, "cpi_geography_id", "S49A")
    metro_monthly.insert(
        4,
        "cpi_geography_name",
        "Los Angeles-Long Beach-Anaheim, CA published CPI area",
    )
    metro_monthly["ces_total_nonfarm_employment_growth_mom"] = metro_monthly[
        "ces_total_nonfarm_employment_sa"
    ].pct_change(1, fill_method=None)
    metro_monthly["ces_total_nonfarm_employment_growth_yoy"] = metro_monthly[
        "ces_total_nonfarm_employment_sa"
    ].pct_change(12, fill_method=None)
    metro_monthly["cpi_inflation_yoy"] = metro_monthly[
        "cpi_u_all_items_la_nsa"
    ].pct_change(12, fill_method=None)

    metro_quarterly = _wide_by_date(
        fact.loc[fact["final_frequency"].eq("quarterly")],
        METRO_QUARTERLY_VARIABLES,
        date_column="quarter_end",
    )
    metro_quarterly.insert(1, "fhfa_geography_id", "METRODIV_31084")
    metro_quarterly.insert(
        2,
        "fhfa_geography_name",
        "Los Angeles-Long Beach-Glendale, CA Metropolitan Division",
    )
    metro_quarterly.insert(3, "hvs_geography_id", "MSA_PROVIDER_LABEL")
    metro_quarterly.insert(
        4,
        "hvs_geography_name",
        "Los Angeles published HVS MSA (vintage-specific label)",
    )
    metro_quarterly["hvs_rental_vacancy_change_yoy_pp"] = metro_quarterly[
        "hvs_rental_vacancy_rate_la_metro"
    ].diff(4)
    metro_quarterly["fhfa_hpi_growth_yoy"] = metro_quarterly[
        "fhfa_hpi_la_metropolitan_division_purchase_only_sa"
    ].pct_change(4, fill_method=None)

    county = _wide_by_date(
        fact.loc[
            fact["geo_id"].eq("COUNTY_06037")
            & fact["final_frequency"].eq("annual")
        ],
        COUNTY_ANNUAL_VARIABLES,
        date_column="year_end",
    )
    county.insert(1, "county_fips", "06037")
    county.insert(2, "county_name", "Los Angeles County")
    county.insert(3, "state_fips", "06")
    annual_sales = county_monthly[["date", "homes_sold", "zhvi"]].copy()
    annual_sales["year_end"] = (
        annual_sales["date"].dt.to_period("Y").dt.to_timestamp("Y")
    )
    annual_sales = (
        annual_sales.groupby("year_end", as_index=False)
        .agg(
            homes_sold_annual=("homes_sold", "sum"),
            homes_sold_months_reported=("homes_sold", "count"),
            zhvi_year_end=("zhvi", "last"),
            latest_month=("date", lambda values: values.max().month),
        )
    )
    annual_sales.loc[
        annual_sales["homes_sold_months_reported"].lt(12),
        "homes_sold_annual",
    ] = pd.NA
    annual_sales.loc[
        annual_sales["latest_month"].ne(12), "zhvi_year_end"
    ] = pd.NA
    annual_sales = annual_sales.loc[
        annual_sales[["homes_sold_annual", "zhvi_year_end"]]
        .notna()
        .any(axis=1)
    ].drop(columns="latest_month")
    county = county.merge(annual_sales, on="year_end", how="outer").sort_values(
        "year_end"
    )
    county["county_fips"] = county["county_fips"].fillna("06037")
    county["county_name"] = county["county_name"].fillna(
        "Los Angeles County"
    )
    county["state_fips"] = county["state_fips"].fillna("06")
    county["bea_personal_income_growth_yoy"] = county[
        "bea_la_county_personal_income_annual"
    ].pct_change(1, fill_method=None)
    county["fhfa_hpi_growth_yoy"] = county[
        "fhfa_hpi_la_county_all_transactions_nsa_annual"
    ].pct_change(1, fill_method=None)
    county["zhvi_growth_yoy_year_end"] = county["zhvi_year_end"].pct_change(
        1, fill_method=None
    )
    county["fhfa_vs_zhvi_growth_gap_yoy"] = (
        county["fhfa_hpi_growth_yoy"] - county["zhvi_growth_yoy_year_end"]
    )

    return {
        "state_quarterly": state,
        "metro_monthly": metro_monthly,
        "metro_quarterly": metro_quarterly,
        "county_annual": county,
    }


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
    panel = apartment_completion_proxies(panel)
    panel = multifamily_under_construction_proxies(panel)
    panel = _add_construction_proxy_quality_flags(panel, fact)
    return panel.sort_values("date")


def _comparable_reference_spec(methodology: dict) -> dict[str, object]:
    """Return the exact configuration contract stored with frozen parameters."""

    return {
        "methodology_id": str(methodology["methodology_id"]),
        "target_county_fips": [
            str(value) for value in methodology["target_county_fips"]
        ],
        "tightness_components": [str(value) for value in MARKET_TIGHTNESS_COMPONENTS],
        "tightness_reference_start": str(
            methodology["tightness_reference_start"]
        ),
        "tightness_reference_end": str(methodology["tightness_reference_end"]),
        "heat_reference_start": str(methodology["heat_reference_start"]),
        "heat_reference_end": str(methodology["heat_reference_end"]),
        "normalization_scope": str(methodology["normalization_scope"]),
        "standard_deviation_ddof": int(
            methodology["standard_deviation_ddof"]
        ),
    }


def _read_frozen_comparable_parameters(
    path: Path, expected_spec: dict[str, object]
) -> dict:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Invalid comparable-county reference artifact: {path}"
        ) from exc
    if artifact.get("specification") != expected_spec:
        raise ValueError(
            "Frozen comparable-county parameters do not match the active "
            "configuration; approve a new methodology version rather than "
            "silently refitting."
        )
    if not isinstance(artifact.get("tightness"), dict) or not isinstance(
        artifact.get("heat"), dict
    ):
        raise ValueError(
            "Frozen comparable-county artifact must contain tightness and heat parameters"
        )
    return artifact


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _comparable_source_lineage(selected: pd.DataFrame) -> list[dict[str, object]]:
    lineage_columns = [
        "source_id",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
    ]
    available = [column for column in lineage_columns if column in selected]
    if not available:
        return []
    lineage = selected[available].drop_duplicates().fillna("")
    return [
        {column: str(value) for column, value in row.items()}
        for row in lineage.sort_values(available).to_dict("records")
    ]


def _comparable_county_panel(
    fact: pd.DataFrame, config: ProjectConfig
) -> tuple[pd.DataFrame, dict | None]:
    """Build the document-specified eight-county monthly analytical layer."""

    methodology = config.pipeline["comparable_county_analytics"]
    county_fips = tuple(str(value) for value in methodology["target_county_fips"])
    if len(county_fips) != 8 or len(set(county_fips)) != 8:
        raise ValueError("Comparable analytics require exactly eight unique counties")
    la_county = str(methodology["la_county_fips"])
    peers = tuple(str(value) for value in methodology["peer_county_fips"])
    if la_county not in county_fips or set(peers) != set(county_fips) - {la_county}:
        raise ValueError(
            "Comparable analytics require Los Angeles and its exact seven configured peers"
        )
    configured_components = tuple(
        str(value) for value in methodology["tightness_components"]
    )
    if configured_components != MARKET_TIGHTNESS_COMPONENTS:
        raise ValueError(
            "Comparable tightness must use months_of_supply, days_on_market, "
            "and price_drop_share in that order"
        )
    if int(methodology["standard_deviation_ddof"]) != 0:
        raise ValueError("Comparable analytics currently require population SD (ddof=0)")
    if str(methodology["normalization_scope"]) != "pooled_eight_counties":
        raise ValueError("Comparable analytics require pooled_eight_counties scope")

    selected = _latest_values(
        fact.loc[
            fact["geo_id"].isin({f"COUNTY_{value}" for value in county_fips})
            & fact["variable_id"].isin(COMPARABLE_COUNTY_VARIABLES)
            & fact["final_frequency"].eq("monthly")
        ]
    )
    output_columns = [
        "date",
        "geo_id",
        "county_fips",
        "county_name",
        "state_fips",
        *COMPARABLE_COUNTY_VARIABLES,
    ]
    if selected.empty:
        return pd.DataFrame(columns=output_columns), None

    keys = selected[["observation_date", "geo_id"]].drop_duplicates()
    values = (
        selected.pivot_table(
            index=["observation_date", "geo_id"],
            columns="variable_id",
            values="value",
            aggfunc="last",
        )
        .reset_index()
    )
    panel = keys.merge(
        values,
        on=["observation_date", "geo_id"],
        how="left",
        validate="one_to_one",
    ).rename(columns={"observation_date": "date"})
    panel["date"] = pd.to_datetime(panel["date"])
    panel["county_fips"] = panel["geo_id"].str.removeprefix("COUNTY_")
    panel["county_name"] = panel["county_fips"].map(COMPARABLE_COUNTY_NAMES)
    panel["state_fips"] = panel["county_fips"].str[:2]
    for variable in COMPARABLE_COUNTY_VARIABLES:
        if variable not in panel:
            panel[variable] = pd.NA
    panel = panel.sort_values(["county_fips", "date"]).reset_index(drop=True)

    price_momentum = home_price_momentum(panel)
    panel["zhvi_momentum_yoy_log_pct"] = price_momentum[
        "home_price_momentum_yoy"
    ]
    panel["zhvi_momentum_3m_annualized_log_pct"] = price_momentum[
        "home_price_momentum_3m_annualized"
    ]
    panel["zori_momentum_yoy_log_pct"] = exact_calendar_log_momentum(
        panel,
        "zori_smoothed_sa",
        lag_months=12,
        scale=100.0,
    )
    panel["zori_momentum_3m_annualized_log_pct"] = exact_calendar_log_momentum(
        panel,
        "zori_smoothed_sa",
        lag_months=3,
        scale=400.0,
    )
    panel["price_to_rent_ratio_zillow"] = positive_price_to_rent_ratio(
        panel["zhvi"], panel["zori_smoothed_sa"]
    )
    panel["price_to_rent_ratio_log_change_mom_pct"] = exact_calendar_log_momentum(
        panel,
        "price_to_rent_ratio_zillow",
        lag_months=1,
        scale=100.0,
    )
    panel["price_to_rent_ratio_log_change_yoy_pct"] = exact_calendar_log_momentum(
        panel,
        "price_to_rent_ratio_zillow",
        lag_months=12,
        scale=100.0,
    )
    panel["laus_employment_momentum_yoy_log_pct"] = laus_employment_momentum_yoy(
        panel
    )

    expected_spec = _comparable_reference_spec(methodology)
    parameters_path = config.root / str(methodology["frozen_parameters_path"])
    fitted_now = not parameters_path.exists()
    if fitted_now:
        if not bool(methodology.get("allow_initial_parameter_fit", False)):
            raise FileNotFoundError(
                "Frozen comparable-county parameters are missing and initial "
                f"fitting is disabled: {parameters_path}"
            )
        tightness_parameters = fit_market_tightness_parameters(
            panel,
            reference_start=str(methodology["tightness_reference_start"]),
            reference_end=str(methodology["tightness_reference_end"]),
        )
    else:
        artifact = _read_frozen_comparable_parameters(
            parameters_path, expected_spec
        )
        tightness_parameters = artifact["tightness"]

    tightness, tightness_coverage = market_tightness_index_3c(
        panel, tightness_parameters
    )
    panel["market_tightness_index_3c_fixed"] = tightness
    panel["market_tightness_components_3c"] = tightness_coverage
    tightness_zscores = apply_pooled_zscores(
        panel, tightness_parameters, columns=MARKET_TIGHTNESS_COMPONENTS
    )
    panel["months_of_supply_z_fixed"] = tightness_zscores["months_of_supply"]
    panel["days_on_market_z_fixed"] = tightness_zscores["days_on_market"]
    panel["price_drop_share_z_fixed"] = tightness_zscores["price_drop_share"]

    heat_inputs = panel.assign(
        home_price_momentum_3m_annualized=panel[
            "zhvi_momentum_3m_annualized_log_pct"
        ],
        market_tightness_index=panel["market_tightness_index_3c_fixed"],
    )
    if fitted_now:
        heat_parameters = fit_county_heat_parameters(
            heat_inputs,
            reference_start=str(methodology["heat_reference_start"]),
            reference_end=str(methodology["heat_reference_end"]),
        )
        artifact = {
            "artifact_version": 1,
            "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "status": "frozen_production_parameters",
            "assumption_status": str(methodology.get("assumption_status", "")),
            "assumption_note": str(methodology.get("assumption_note", "")),
            "specification": expected_spec,
            "tightness": tightness_parameters,
            "heat": heat_parameters,
            "source_lineage": _comparable_source_lineage(selected),
        }
        _write_json_atomic(parameters_path, artifact)
    else:
        heat_parameters = artifact["heat"]

    panel["county_housing_market_heat_index"] = (
        county_housing_market_heat_index(heat_inputs, heat_parameters)
    )
    heat_zscores = apply_pooled_zscores(
        heat_inputs,
        heat_parameters,
        columns=("home_price_momentum_3m_annualized", "market_tightness_index"),
    )
    panel["zhvi_momentum_3m_z_fixed_for_heat"] = heat_zscores[
        "home_price_momentum_3m_annualized"
    ]
    panel["market_tightness_z_fixed_for_heat"] = heat_zscores[
        "market_tightness_index"
    ]
    panel["county_housing_market_heat_rank"] = (
        panel.groupby("date", sort=False)["county_housing_market_heat_index"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )
    panel["county_housing_market_heat_count"] = panel.groupby(
        "date", sort=False
    )["county_housing_market_heat_index"].transform("count").astype("Int64")

    relative_inputs = {
        "zhvi_momentum_yoy_log_pct": "la_relative_zhvi_momentum_yoy_log_pct",
        "zhvi_momentum_3m_annualized_log_pct": (
            "la_relative_zhvi_momentum_3m_annualized_log_pct"
        ),
        "zori_momentum_yoy_log_pct": "la_relative_zori_momentum_yoy_log_pct",
        "zori_momentum_3m_annualized_log_pct": (
            "la_relative_zori_momentum_3m_annualized_log_pct"
        ),
        "market_tightness_index_3c_fixed": (
            "la_relative_market_tightness_index_3c_fixed"
        ),
        "price_to_rent_ratio_zillow": "la_relative_price_to_rent_ratio_zillow",
    }
    relative = la_relative_market_performance(
        panel,
        tuple(relative_inputs),
        la_county_fips=la_county,
        peer_county_fips=peers,
    )
    relative = relative.rename(
        columns={
            f"{source}_la_relative_to_seven_peers": output
            for source, output in relative_inputs.items()
        }
    )
    panel = panel.merge(relative, on="date", how="left", validate="many_to_one")
    peer_rows = ~panel["county_fips"].eq(la_county)
    panel.loc[peer_rows, list(relative_inputs.values())] = pd.NA
    panel["la_relative_required_peer_count"] = 7

    vintages = (
        selected.groupby(["observation_date", "geo_id"])["source_vintage"]
        .max()
        .rename("latest_source_vintage")
        .reset_index()
        .rename(columns={"observation_date": "date"})
    )
    panel = panel.merge(vintages, on=["date", "geo_id"], how="left")
    panel["source_coverage_count"] = panel[COMPARABLE_COUNTY_VARIABLES].notna().sum(
        axis=1
    )
    panel["panel_quality_flag"] = panel["source_coverage_count"].map(
        lambda count: "complete_inputs" if count == len(COMPARABLE_COUNTY_VARIABLES) else "partial"
    )
    panel["normalization_assumption_status"] = str(
        methodology.get("assumption_status", "")
    )
    return panel.sort_values(["date", "county_fips"]).reset_index(drop=True), artifact


def _merge_la_comparable_features(
    county_panel: pd.DataFrame, comparable_panel: pd.DataFrame
) -> pd.DataFrame:
    """Expose the new LA values in the legacy LA-only panel without redefining it."""

    feature_columns = [
        "zhvi_momentum_yoy_log_pct",
        "zhvi_momentum_3m_annualized_log_pct",
        "zori_momentum_yoy_log_pct",
        "zori_momentum_3m_annualized_log_pct",
        "price_to_rent_ratio_log_change_mom_pct",
        "price_to_rent_ratio_log_change_yoy_pct",
        "laus_employment_momentum_yoy_log_pct",
        "market_tightness_index_3c_fixed",
        "market_tightness_components_3c",
        "county_housing_market_heat_index",
        "county_housing_market_heat_rank",
        "county_housing_market_heat_count",
        "la_relative_zhvi_momentum_yoy_log_pct",
        "la_relative_zhvi_momentum_3m_annualized_log_pct",
        "la_relative_zori_momentum_yoy_log_pct",
        "la_relative_zori_momentum_3m_annualized_log_pct",
        "la_relative_market_tightness_index_3c_fixed",
        "la_relative_price_to_rent_ratio_zillow",
        "la_relative_required_peer_count",
        "normalization_assumption_status",
    ]
    if comparable_panel.empty:
        result = county_panel.copy()
        for column in feature_columns:
            result[column] = pd.NA
        return result
    la = comparable_panel.loc[
        comparable_panel["county_fips"].eq("06037"),
        ["date", *feature_columns],
    ]
    return county_panel.merge(la, on="date", how="left", validate="one_to_one")


def _add_construction_proxy_quality_flags(
    panel: pd.DataFrame, fact: pd.DataFrame
) -> pd.DataFrame:
    """Attach scenario and BPS-regime lineage to each modeled proxy value."""

    result = panel.copy()
    bps = _latest_values(
        fact.loc[
            fact["geo_id"].eq("COUNTY_06037")
            & fact["variable_id"].eq("permits_5_plus_units")
            & fact["final_frequency"].eq("monthly")
        ]
    )
    quality_by_month = {
        pd.Timestamp(row.observation_date).to_period("M"): (
            "" if pd.isna(row.quality_flag) else str(row.quality_flag)
        )
        for row in bps[
            ["observation_date", "value", "quality_flag"]
        ].itertuples(index=False)
        if pd.notna(pd.to_numeric(row.value, errors="coerce"))
    }

    def flag_for_window(
        date: object,
        ages: list[int],
        *,
        scenario: str,
        stock: bool,
    ) -> str:
        month = pd.Timestamp(date).to_period("M")
        source_flags = [quality_by_month.get(month - age) for age in ages]
        kind = (
            "modeled_multifamily_construction_pipeline_stock"
            if stock
            else "modeled_apartment_completion_flow"
        )
        if any(flag is None for flag in source_flags):
            return (
                f"{kind};scenario_{scenario};"
                "insufficient_required_lag_history_or_source_null"
            )
        joined = ";".join(source_flags)
        parts = [
            kind,
            f"scenario_{scenario}",
            "bps_5_plus_permit_authorizations_input",
            "complete_required_lag_history",
            "not_observed_completions_or_projects",
        ]
        if stock:
            parts.append("cohort_survival_stock_flow")
        if "includes_imputation" in joined:
            parts.append("source_includes_imputation")
        has_pre = "bps_pre_2022_sample_tally" in joined
        has_post = "bps_2022_plus_full_universe" in joined
        if has_pre:
            parts.append("bps_pre_2022_sample_tally_input")
        if has_post:
            parts.append("bps_2022_plus_full_universe_input")
        if has_pre and has_post:
            parts.append("bps_regime_mixed_input_window")
        return ";".join(parts)

    for scenario, weights in APARTMENT_COMPLETION_LAG_WEIGHTS.items():
        completion_column = APARTMENT_COMPLETION_PROXY_COLUMNS[scenario]
        stock_column = MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS[scenario]
        completion_flags = [
            flag_for_window(
                date,
                list(weights),
                scenario=scenario,
                stock=False,
            )
            for date in result["date"]
        ]
        stock_flags = [
            flag_for_window(
                date,
                list(range(max(weights))),
                scenario=scenario,
                stock=True,
            )
            for date in result["date"]
        ]
        result[f"{completion_column}_quality_flag"] = pd.Series(
            completion_flags, index=result.index, dtype="string"
        )
        result[f"{stock_column}_quality_flag"] = pd.Series(
            stock_flags, index=result.index, dtype="string"
        )
    return result


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


def _calculated_zip_growth(
    panel: pd.DataFrame, level_column: str, months: int
) -> pd.Series:
    """Fractional ZIP growth using an exact calendar-month lag.

    A row-shift can bridge missing source months and would create a false
    one- or twelve-month comparison. The explicit date join preserves source
    gaps as missing.
    """

    prior = panel[["date", "postal_zip", level_column]].copy()
    prior["date"] = prior["date"] + pd.DateOffset(months=months)
    prior = prior.rename(columns={level_column: "_prior"})
    aligned = panel[["date", "postal_zip", level_column]].merge(
        prior, on=["date", "postal_zip"], how="left", sort=False
    )
    current = pd.to_numeric(aligned[level_column], errors="coerce")
    previous = pd.to_numeric(aligned["_prior"], errors="coerce")
    valid = current.gt(0) & previous.gt(0)
    return (current / previous - 1.0).where(valid)


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
    }
    panel = panel.sort_values(["postal_zip", "date"]).reset_index(drop=True)
    for level_column, yoy_column in calculated_columns.items():
        panel[yoy_column] = _calculated_zip_yoy(panel, level_column)
    for source, prefix in (("zhvi", "zhvi"), ("zori", "zori")):
        panel[f"{prefix}_growth_mom"] = _calculated_zip_growth(
            panel, source, 1
        )
        panel[f"{prefix}_growth_yoy"] = _calculated_zip_growth(
            panel, source, 12
        )
    panel["price_to_rent_ratio_zillow"] = price_to_rent_ratio(
        panel["zhvi"], panel["zori"]
    )
    panel["price_to_rent_ratio"] = panel["price_to_rent_ratio_zillow"]
    panel["implied_gross_rent_yield"] = implied_gross_rent_yield(
        panel["zhvi"], panel["zori"]
    )
    panel["zhvi_momentum_yoy_log_pct"] = exact_calendar_log_momentum(
        panel,
        "zhvi",
        lag_months=12,
        scale=100.0,
        group_columns=("postal_zip",),
    )
    panel["zhvi_momentum_3m_annualized_log_pct"] = (
        exact_calendar_log_momentum(
            panel,
            "zhvi",
            lag_months=3,
            scale=400.0,
            group_columns=("postal_zip",),
        )
    )
    panel["zori_momentum_yoy_log_pct"] = exact_calendar_log_momentum(
        panel,
        "zori",
        lag_months=12,
        scale=100.0,
        group_columns=("postal_zip",),
    )
    panel["zori_momentum_3m_annualized_log_pct"] = exact_calendar_log_momentum(
        panel,
        "zori",
        lag_months=3,
        scale=400.0,
        group_columns=("postal_zip",),
    )
    panel["price_to_rent_ratio_log_change_mom_pct"] = (
        exact_calendar_log_momentum(
            panel,
            "price_to_rent_ratio_zillow",
            lag_months=1,
            scale=100.0,
            group_columns=("postal_zip",),
        )
    )
    panel["price_to_rent_ratio_log_change_yoy_pct"] = (
        exact_calendar_log_momentum(
            panel,
            "price_to_rent_ratio_zillow",
            lag_months=12,
            scale=100.0,
            group_columns=("postal_zip",),
        )
    )
    la_ratio_fact = _latest_values(
        fact.loc[
            fact["geo_id"].eq("COUNTY_06037")
            & fact["variable_id"].isin({"zhvi", "zori_smoothed_sa"})
            & fact["final_frequency"].eq("monthly")
        ]
    )
    if la_ratio_fact.empty:
        panel["la_county_price_to_rent_ratio_zillow"] = pd.NA
    else:
        la_ratio = (
            la_ratio_fact.pivot(
                index="observation_date", columns="variable_id", values="value"
            )
            .reset_index()
            .rename(columns={"observation_date": "date"})
        )
        for variable in ("zhvi", "zori_smoothed_sa"):
            if variable not in la_ratio:
                la_ratio[variable] = pd.NA
        la_ratio["la_county_price_to_rent_ratio_zillow"] = (
            positive_price_to_rent_ratio(
                la_ratio["zhvi"], la_ratio["zori_smoothed_sa"]
            )
        )
        panel = panel.merge(
            la_ratio[["date", "la_county_price_to_rent_ratio_zillow"]],
            on="date",
            how="left",
            validate="many_to_one",
        )
    zip_ratio = pd.to_numeric(panel["price_to_rent_ratio_zillow"], errors="coerce")
    county_ratio = pd.to_numeric(
        panel["la_county_price_to_rent_ratio_zillow"], errors="coerce"
    )
    valid_relative_ratio = zip_ratio.gt(0) & county_ratio.gt(0)
    panel["relative_valuation_pressure_la_zip"] = (
        pd.Series(pd.NA, index=panel.index, dtype="Float64")
    )
    panel.loc[valid_relative_ratio, "relative_valuation_pressure_la_zip"] = (
        np.log(zip_ratio.loc[valid_relative_ratio])
        - np.log(
            county_ratio.loc[valid_relative_ratio]
        )
    )
    for closing_cost in config.pipeline["mortgage"].get(
        "closing_cost_scenarios", []
    ):
        label = str(float(closing_cost) * 100).rstrip("0").rstrip(".")
        label = label.replace(".", "_") + "pct"
        panel[f"closing_cost_saleprice_{label}"] = (
            panel["median_sale_price"] * float(closing_cost)
        )
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


def _validate_regime_configuration(methodology: dict) -> None:
    expected = {
        "training_start": REGIME_TRAINING_START.date().isoformat(),
        "training_end": REGIME_TRAINING_END.date().isoformat(),
        "classification_start": REGIME_APPLICATION_START.date().isoformat(),
        "cluster_count": REGIME_CLUSTER_COUNT,
        "random_seed": REGIME_RANDOM_SEED,
        "n_init": REGIME_N_INIT,
        "max_iterations": REGIME_MAX_ITERATIONS,
        "standard_deviation_ddof": 0,
        "feature_order": list(REGIME_FEATURE_COLUMNS),
    }
    actual = {
        "training_start": str(methodology["training_start"]),
        "training_end": str(methodology["training_end"]),
        "classification_start": str(methodology["classification_start"]),
        "cluster_count": int(methodology["cluster_count"]),
        "random_seed": int(methodology["random_seed"]),
        "n_init": int(methodology["n_init"]),
        "max_iterations": int(methodology["max_iterations"]),
        "standard_deviation_ddof": int(methodology["standard_deviation_ddof"]),
        "feature_order": [str(value) for value in methodology["feature_order"]],
    }
    if actual != expected:
        raise ValueError(
            f"ZIP market-regime configuration differs from {REGIME_MODEL_VERSION}: "
            f"expected {expected}, got {actual}"
        )
    if not np.isclose(
        float(methodology["tolerance"]), REGIME_TOLERANCE, rtol=0.0, atol=0.0
    ):
        raise ValueError(
            "ZIP market-regime convergence tolerance differs from the frozen code contract"
        )
    if [int(value) for value in methodology["quarter_end_months"]] != [3, 6, 9, 12]:
        raise ValueError("ZIP market regimes require March/June/September/December ends")
    if int(methodology["volatility_window_months"]) != 24:
        raise ValueError("ZIP market regimes require a 24-month volatility window")


def _zip_regime_outputs(
    zip_panel: pd.DataFrame,
    fact: pd.DataFrame,
    config: ProjectConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict | None]:
    """Build and lineage the frozen-model quarterly ZIP regime products."""

    methodology = config.pipeline["zip_market_regimes"]
    _validate_regime_configuration(methodology)
    if zip_panel.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), None
    model_path = config.root / str(methodology["model_path"])
    if not model_path.exists() and not bool(
        methodology.get("allow_initial_model_fit", False)
    ):
        raise FileNotFoundError(
            "Frozen ZIP market-regime model is missing and initial fitting is "
            f"disabled: {model_path}"
        )
    regime_fact = _latest_values(
        fact.loc[
            fact["geo_id"].astype("string").str.startswith("ZIP_")
            & fact["variable_id"].isin({"zhvi", "zori_smoothed_sa"})
            & fact["final_frequency"].eq("monthly")
        ]
    )
    source_lineage = _comparable_source_lineage(regime_fact)
    outputs = build_zip_market_regimes(
        zip_panel,
        model_path,
        source_lineage=source_lineage,
    )
    model = load_market_regime_model(model_path)
    vintage = "|".join(
        sorted(
            {
                str(value)
                for value in regime_fact.get(
                    "source_vintage", pd.Series(dtype="string")
                ).dropna()
                if str(value)
            }
        )
    )
    retrieved_at = "|".join(
        sorted(
            {
                str(value)
                for value in regime_fact.get(
                    "retrieved_at", pd.Series(dtype="string")
                ).dropna()
                if str(value)
            }
        )
    )
    source_files = "|".join(
        sorted(
            {
                str(value)
                for value in regime_fact.get(
                    "source_filename", pd.Series(dtype="string")
                ).dropna()
                if str(value)
            }
        )
    )
    checksums = "|".join(
        sorted(
            {
                str(value)
                for value in regime_fact.get(
                    "checksum_sha256", pd.Series(dtype="string")
                ).dropna()
                if str(value)
            }
        )
    )

    assignments = outputs.assignments.copy()
    summary = outputs.summary.copy()
    transitions = outputs.transitions.copy()
    for frame in (assignments, summary, transitions):
        frame["county_fips"] = "06037"
        frame["source_vintage"] = vintage or pd.NA
        frame["retrieved_at"] = retrieved_at or pd.NA
        frame["source_filename"] = source_files or pd.NA
        frame["checksum_sha256"] = checksums or pd.NA
        frame["market_regime_model_version"] = model["model_version"]
        frame["market_regime_model_fingerprint_sha256"] = model[
            "model_fingerprint_sha256"
        ]
    summary["zip_market_regime_share"] = summary[
        "regime_share_of_classified"
    ]
    summary["zip_market_regime_changed_share"] = summary[
        "changed_regime_share_of_classified"
    ]
    summary["zip_market_regime_entry_share"] = summary[
        "entered_regime_share_of_classified"
    ]
    summary["zip_market_regime_classified_coverage"] = summary[
        "classification_coverage_share_of_universe"
    ]
    transitions["zip_market_regime_transition_share"] = transitions[
        "transition_share_of_classified"
    ]
    return assignments, summary, transitions, model


def _homebuying_scenario_layer(
    county: pd.DataFrame,
    zip_panel: pd.DataFrame,
    config: ProjectConfig,
) -> pd.DataFrame:
    county_source = county[["date", "county_fips", "median_sale_price"]].copy()
    county_source["geo_id"] = "COUNTY_06037"
    county_source["geography_level"] = "county"
    county_source["postal_zip"] = pd.NA
    county_source["price_source_variable_id"] = "median_sale_price"
    county_source["price_native_frequency"] = "monthly"
    county_source["price_rolling_window_months"] = pd.NA

    zip_source = zip_panel[
        [
            "date",
            "primary_county_fips",
            "postal_zip",
            "median_sale_price",
            "redfin_frequency",
            "redfin_window_months",
        ]
    ].copy()
    zip_source = zip_source.rename(
        columns={
            "primary_county_fips": "county_fips",
            "redfin_frequency": "price_native_frequency",
            "redfin_window_months": "price_rolling_window_months",
        }
    )
    zip_source["geo_id"] = "ZIP_" + zip_source["postal_zip"].astype(str)
    zip_source["geography_level"] = "postal_zip"
    zip_source["price_source_variable_id"] = (
        "redfin_zip_median_sale_price_nsa_rolling_3m"
    )
    observations = pd.concat(
        [county_source, zip_source], ignore_index=True, sort=False
    )
    return homebuying_cost_scenarios(
        observations, config.pipeline["mortgage"]
    )


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
        columns = [
            "date",
            "geo_id",
            *id_columns,
            "latest_source_vintage",
            "source_coverage_count",
        ]
        if frequency == "annual":
            columns.extend(
                [
                    "frequency_treatment",
                    "is_mixed_frequency_control",
                    "is_carried_annual_control",
                ]
            )
        return pd.DataFrame(columns=columns)
    # ``pivot_table`` drops an index group when every value in that group is
    # null.  Those groups are still real provider observations and must remain
    # visible in an analytical control panel.  HUD PIT, for example, has an
    # explicit 2010 four-CoC composite whose three analytical values are all
    # suppressed because one or more members are incomplete.  Build the key
    # frame independently and left-join the wide values so that such rows are
    # retained without filling or fabricating any value.
    panel_keys = selected[["observation_date", "geo_id"]].drop_duplicates()
    wide_values = (
        selected.pivot_table(
            index=["observation_date", "geo_id"],
            columns="variable_id",
            values="value",
            aggfunc="last",
        )
        .reset_index()
    )
    panel = (
        panel_keys.merge(
            wide_values,
            on=["observation_date", "geo_id"],
            how="left",
            validate="one_to_one",
        )
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
    if frequency == "annual":
        panel["frequency_treatment"] = "native_annual_retained"
        panel["is_mixed_frequency_control"] = False
        panel["is_carried_annual_control"] = False
    return panel.sort_values(["geo_id", "date"])


def _add_real_homeowners_premium(state_annual: pd.DataFrame) -> pd.DataFrame:
    """Deflate nominal California premium to constant 2023 dollars."""

    result = state_annual.copy()
    output = "ca_homeowners_avg_written_premium_real"
    quality_output = f"{output}_quality_flag"
    required = {
        "date",
        "geo_id",
        "ca_homeowners_avg_written_premium",
        "ca_cpi_u_all_items_annual",
    }
    result[output] = pd.NA
    result[quality_output] = pd.Series(pd.NA, index=result.index, dtype="string")
    if result.empty or not required.issubset(result.columns):
        return result

    premium = pd.to_numeric(
        result["ca_homeowners_avg_written_premium"], errors="coerce"
    )
    cpi = pd.to_numeric(result["ca_cpi_u_all_items_annual"], errors="coerce")
    dates = pd.to_datetime(result["date"], errors="coerce")
    base_rows = result.loc[dates.dt.year.eq(2023), ["geo_id"]].copy()
    base_rows["base_cpi"] = cpi.loc[base_rows.index]
    base_cpi = (
        base_rows.loc[base_rows["base_cpi"].gt(0)]
        .drop_duplicates("geo_id", keep="last")
        .set_index("geo_id")["base_cpi"]
    )
    matched_base = result["geo_id"].map(base_cpi)
    valid = premium.gt(0) & cpi.gt(0) & matched_base.gt(0)
    result[output] = premium.mul(matched_base).div(cpi).where(valid)
    result.loc[valid, quality_output] = (
        "constructed_real_state_control;constant_2023_dollars;"
        "matched_native_annual_ca_cpi;not_interpolated;not_county_estimate"
    )
    return result


def _annual_input_quality_by_date(
    fact: pd.DataFrame, variable_id: str, geo_id: str
) -> dict[pd.Timestamp, str]:
    selected = _latest_values(
        fact.loc[
            fact["variable_id"].eq(variable_id)
            & fact["geo_id"].eq(geo_id)
            & fact["final_frequency"].eq("annual")
        ]
    )
    return {
        pd.Timestamp(row.observation_date): str(row.quality_flag)
        for row in selected[["observation_date", "quality_flag"]].itertuples(
            index=False
        )
    }


def _add_approved_annual_rates(
    county_annual: pd.DataFrame, fact: pd.DataFrame
) -> pd.DataFrame:
    """Build the two approved same-year PEP-denominated annual rates."""

    result = county_annual.copy()
    specifications = (
        (
            "net_exemption_rate",
            "irs_soi_net_migration_exemptions",
            "census_pep_county_population_annual",
            "constructed_annual_migration_rate;per_1000_residents",
        ),
        (
            "permits_1_unit_per_1000_housing_units",
            "permits_1_unit_annual_final",
            "census_pep_county_housing_units_annual",
            "constructed_annual_permit_rate;per_1000_housing_units",
        ),
    )
    for output, numerator_column, denominator_column, base_quality in specifications:
        quality_column = f"{output}_quality_flag"
        result[output] = pd.NA
        result[quality_column] = pd.Series(
            pd.NA, index=result.index, dtype="string"
        )
        if not {numerator_column, denominator_column}.issubset(result.columns):
            continue
        numerator = pd.to_numeric(result[numerator_column], errors="coerce")
        denominator = pd.to_numeric(result[denominator_column], errors="coerce")
        valid = numerator.notna() & denominator.gt(0)
        result[output] = numerator.div(denominator).mul(1_000.0).where(valid)
        numerator_quality = _annual_input_quality_by_date(
            fact, numerator_column, "COUNTY_06037"
        )
        denominator_quality = _annual_input_quality_by_date(
            fact, denominator_column, "COUNTY_06037"
        )
        for index in result.index[valid]:
            date = pd.Timestamp(result.at[index, "date"])
            source_flags = [
                numerator_quality.get(date),
                denominator_quality.get(date),
            ]
            source_flags = [flag for flag in source_flags if flag]
            result.at[index, quality_column] = ";".join(
                [
                    base_quality,
                    "matched_same_ending_year",
                    "native_annual_inputs_retained",
                    "not_interpolated",
                    *source_flags,
                ]
            )
    return result


def _state_monthly_control_panel(
    fact: pd.DataFrame, config: ProjectConfig
) -> pd.DataFrame:
    """Build the provenance-rich California monthly control panel."""

    monthly_id = "ca_homeowners_avg_written_premium_monthly_control"
    selected = _latest_values(
        fact.loc[
            fact["geo_id"].eq("STATE_06")
            & fact["variable_id"].eq(monthly_id)
            & fact["final_frequency"].eq("monthly")
        ]
    )
    columns = [
        "date",
        "geo_id",
        "state_fips",
        "state_name",
        monthly_id,
        "source_annual_date",
        "source_name",
        "source_url",
        "source_release_date",
        "source_id",
        "source_vintage",
        "vintage_date",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
        "native_frequency",
        "final_frequency",
        "transformation_id",
        "transformation_method",
        "is_observed",
        "is_constructed",
        "is_proxy",
        "is_imputed",
        "is_interpolated",
        "scenario_name",
        "quality_flag",
        "frequency_treatment",
        "is_mixed_frequency_control",
        "is_carried_annual_control",
        "source_coverage_count",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)

    source = next(
        item
        for item in config.sources
        if item.source_id == "ca_doi_residential_property_part_i"
    )
    panel = selected.rename(
        columns={
            "observation_date": "date",
            "source_observation_date": "source_annual_date",
            "value": monthly_id,
        }
    ).copy()
    panel["state_name"] = "California"
    panel["source_name"] = panel["provider"]
    panel["source_url"] = source.source_page_url
    panel["source_release_date"] = pd.to_datetime(
        panel["provider_last_updated"], errors="coerce"
    )
    panel["vintage_date"] = panel["source_vintage"]
    panel["transformation_method"] = "calendar_year_step_function_carry_forward"
    panel["is_observed"] = False
    panel["is_constructed"] = True
    panel["is_proxy"] = False
    panel["scenario_name"] = pd.NA
    panel["frequency_treatment"] = (
        "calendar_year_carry_for_mixed_frequency_analysis"
    )
    panel["is_mixed_frequency_control"] = True
    panel["is_carried_annual_control"] = True
    panel["source_coverage_count"] = 1
    return panel[columns].sort_values(["geo_id", "date"])


def _merge_structural_columns(
    panel: pd.DataFrame,
    structural: pd.DataFrame,
    *,
    structural_date: str,
    geo_id: str,
) -> pd.DataFrame:
    """Add source-specific derived columns without narrowing the general panel."""

    if structural.empty:
        return panel
    supplement = structural.copy()
    supplement["date"] = pd.to_datetime(supplement[structural_date])
    supplement["geo_id"] = geo_id
    join_keys = ["date", "geo_id"]
    added_columns = [
        column
        for column in supplement.columns
        if column not in panel.columns and column not in join_keys
    ]
    if not added_columns:
        return panel
    return panel.merge(
        supplement[[*join_keys, *added_columns]],
        on=join_keys,
        how="left",
        validate="many_to_one",
    )


def _county_quarterly_from_annual_controls(county_annual: pd.DataFrame) -> pd.DataFrame:
    """Allocate selected annual county controls to quarters with explicit flags."""

    required = {
        "date",
        "geo_id",
        "county_fips",
        "county_name",
        "state_fips",
        "natural_population_change_annual",
    }
    if county_annual.empty or not required.issubset(county_annual.columns):
        return pd.DataFrame(
            columns=[
                "date",
                "geo_id",
                "county_fips",
                "county_name",
                "state_fips",
                "natural_population_change_quarterly_allocated",
                "source_annual_date",
                "latest_source_vintage",
                "source_coverage_count",
                "is_imputed",
                "is_interpolated",
                "allocation_method",
            ]
        )
    rows: list[dict[str, object]] = []
    for _, row in county_annual.iterrows():
        annual_value = pd.to_numeric(
            row.get("natural_population_change_annual"), errors="coerce"
        )
        if pd.isna(annual_value):
            continue
        year = pd.Timestamp(row["date"]).year
        for quarter in range(1, 5):
            rows.append(
                {
                    "date": pd.Timestamp(year=year, month=quarter * 3, day=1)
                    + pd.offsets.MonthEnd(0),
                    "geo_id": row["geo_id"],
                    "county_fips": row["county_fips"],
                    "county_name": row["county_name"],
                    "state_fips": row["state_fips"],
                    "natural_population_change_quarterly_allocated": (
                        float(annual_value) / 4.0
                    ),
                    "source_annual_date": row["date"],
                    "latest_source_vintage": row.get("latest_source_vintage"),
                    "source_coverage_count": 1,
                    "is_imputed": True,
                    "is_interpolated": True,
                    "allocation_method": "annual_total_evenly_allocated_to_four_quarters",
                }
            )
    return pd.DataFrame(rows).sort_values(["geo_id", "date"])


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
    }
    rows = []
    for metric, (provider_column, calculated_column) in pairs.items():
        if provider_column not in panel or calculated_column not in panel:
            continue
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


def _add_acs_rental_availability(
    county_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Add the matched-vintage ACS renter-market availability proxy.

    The published B25003 and B25004 component estimates and 90% margins of
    error remain as separate columns.  A rate is exposed only when both
    estimates and MOEs are usable and neither component MOE is as large as
    its estimate.  This keeps the annual ACS proxy distinct from monthly
    rental-listing inventory and from the quarterly HUD-USPS proxy.
    """

    result = county_annual.copy()
    rate_column = "acs_rental_availability_rate"
    quality_column = "acs_rental_availability_quality_flag"
    result[rate_column] = pd.Series(float("nan"), index=result.index, dtype="float64")
    result[quality_column] = "not_available"
    required = {
        "acs_renter_occupied_units",
        "acs_renter_occupied_units_moe",
        "acs_for_rent_vacant_units",
        "acs_for_rent_vacant_units_moe",
    }
    if result.empty or not required.issubset(result.columns):
        return result

    values = result[list(sorted(required))].apply(
        pd.to_numeric, errors="coerce"
    )
    renter = values["acs_renter_occupied_units"]
    renter_moe = values["acs_renter_occupied_units_moe"]
    for_rent = values["acs_for_rent_vacant_units"]
    for_rent_moe = values["acs_for_rent_vacant_units_moe"]
    estimate_missing = renter.isna() | for_rent.isna()
    estimate_negative = renter.lt(0) | for_rent.lt(0)
    moe_missing = renter_moe.isna() | for_rent_moe.isna()
    moe_negative = renter_moe.lt(0) | for_rent_moe.lt(0)
    denominator = renter + for_rent
    nonpositive_denominator = denominator.le(0) & ~estimate_missing
    high_relative_moe = (
        ((renter.eq(0) & renter_moe.gt(0)) | (renter.gt(0) & renter_moe.ge(renter)))
        | (
            (for_rent.eq(0) & for_rent_moe.gt(0))
            | (for_rent.gt(0) & for_rent_moe.ge(for_rent))
        )
    )

    result.loc[estimate_missing, quality_column] = (
        "suppressed_missing_component_estimate"
    )
    result.loc[~estimate_missing & estimate_negative, quality_column] = (
        "suppressed_negative_component_estimate"
    )
    result.loc[
        ~estimate_missing & ~estimate_negative & nonpositive_denominator,
        quality_column,
    ] = "suppressed_nonpositive_denominator"
    eligible_estimates = (
        ~estimate_missing & ~estimate_negative & ~nonpositive_denominator
    )
    result.loc[eligible_estimates & moe_missing, quality_column] = (
        "suppressed_missing_component_moe"
    )
    result.loc[eligible_estimates & ~moe_missing & moe_negative, quality_column] = (
        "suppressed_negative_component_moe"
    )
    reliability_evaluable = eligible_estimates & ~moe_missing & ~moe_negative
    result.loc[reliability_evaluable & high_relative_moe, quality_column] = (
        "suppressed_high_relative_moe"
    )
    valid = reliability_evaluable & ~high_relative_moe
    result.loc[valid, rate_column] = for_rent.loc[valid] / denominator.loc[valid]
    result.loc[valid, quality_column] = "ok"
    return result


def _add_acs_noncontrolled_exposure(
    annual_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Add annual ACS structure shares and noncontrolled-exposure bounds.

    B25032 provides renter-occupied structure shares.  Vintage-normalized
    B25127 inputs provide lower and upper recent-construction shares even
    though the published year-built bins change across ACS releases.  The
    noncontrolled structural proxy is deliberately a bound, not an observed
    legal-eligibility point estimate: its lower bound is the recent age band,
    while its upper bound unions recent/mixed age bands with all one-unit
    rentals and subtracts the one-unit overlap reported by B25127.

    A bound is exposed only after the source components reconcile.  The union
    upper bound is then capped at one.  No pre-2020 value or nonannual timing is
    created, and no exact ``noncontrolled_exposure_share`` column is emitted.
    """

    result = annual_panel.copy()
    structure_share_columns = tuple(ACS_B25032_STRUCTURE_GROUPS)
    recent_share_columns = (
        "acs_renter_recent_construction_share_lower_bound",
        "acs_renter_recent_construction_share_upper_bound",
    )
    derived_columns = (
        *structure_share_columns,
        *recent_share_columns,
        *ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
    )
    for column in derived_columns:
        result[column] = pd.Series(
            float("nan"), index=result.index, dtype="float64"
        )
    if result.empty:
        return result

    structure_valid = pd.Series(False, index=result.index, dtype=bool)
    b25032_total = pd.Series(float("nan"), index=result.index, dtype="float64")
    b25032_one_unit = pd.Series(
        float("nan"), index=result.index, dtype="float64"
    )
    b25032_components = tuple(
        component
        for components in ACS_B25032_STRUCTURE_GROUPS.values()
        for component in components
    )
    b25032_required = {"acs_b25032_renter_total", *b25032_components}
    if b25032_required.issubset(result.columns):
        b25032 = result[list(sorted(b25032_required))].apply(
            pd.to_numeric, errors="coerce"
        )
        b25032_total = b25032["acs_b25032_renter_total"]
        component_values = b25032[list(b25032_components)]
        component_sum = component_values.sum(
            axis=1, min_count=len(b25032_components)
        )
        structure_valid = (
            b25032_total.gt(0)
            & component_values.notna().all(axis=1)
            & component_values.ge(0).all(axis=1)
            & component_sum.sub(b25032_total)
            .abs()
            .le(ACS_COMPONENT_RECONCILIATION_TOLERANCE)
        )
        for output_column, input_columns in ACS_B25032_STRUCTURE_GROUPS.items():
            numerator = b25032[list(input_columns)].sum(
                axis=1, min_count=len(input_columns)
            )
            result.loc[structure_valid, output_column] = (
                numerator.loc[structure_valid] / b25032_total.loc[structure_valid]
            )
        one_unit_columns = ACS_B25032_STRUCTURE_GROUPS[
            "acs_renter_occupied_1_unit_share"
        ]
        b25032_one_unit = b25032[list(one_unit_columns)].sum(
            axis=1, min_count=len(one_unit_columns)
        )

    recent_valid = pd.Series(False, index=result.index, dtype=bool)
    b25127_total = pd.Series(float("nan"), index=result.index, dtype="float64")
    recent_total = pd.Series(float("nan"), index=result.index, dtype="float64")
    mixed_total = pd.Series(float("nan"), index=result.index, dtype="float64")
    recent_one_unit = pd.Series(
        float("nan"), index=result.index, dtype="float64"
    )
    mixed_one_unit = pd.Series(
        float("nan"), index=result.index, dtype="float64"
    )
    b25127_band_columns = {
        band: tuple(
            f"acs_b25127_renter_{band}_{suffix}"
            for suffix in ("total", *ACS_B25127_STRUCTURE_SUFFIXES)
        )
        for band in ("recent_lower_bound", "cutoff_mixed")
    }
    b25127_required = {
        "acs_b25127_renter_total",
        *b25127_band_columns["recent_lower_bound"],
        *b25127_band_columns["cutoff_mixed"],
    }
    if b25127_required.issubset(result.columns):
        b25127 = result[list(sorted(b25127_required))].apply(
            pd.to_numeric, errors="coerce"
        )
        b25127_total = b25127["acs_b25127_renter_total"]
        recent_total = b25127[
            "acs_b25127_renter_recent_lower_bound_total"
        ]
        mixed_total = b25127["acs_b25127_renter_cutoff_mixed_total"]
        recent_detail = [
            f"acs_b25127_renter_recent_lower_bound_{suffix}"
            for suffix in ACS_B25127_STRUCTURE_SUFFIXES
        ]
        mixed_detail = [
            f"acs_b25127_renter_cutoff_mixed_{suffix}"
            for suffix in ACS_B25127_STRUCTURE_SUFFIXES
        ]
        recent_detail_sum = b25127[recent_detail].sum(
            axis=1, min_count=len(recent_detail)
        )
        mixed_detail_sum = b25127[mixed_detail].sum(
            axis=1, min_count=len(mixed_detail)
        )
        all_b25127_values = b25127[list(sorted(b25127_required))]
        recent_valid = (
            b25127_total.gt(0)
            & all_b25127_values.notna().all(axis=1)
            & all_b25127_values.ge(0).all(axis=1)
            & recent_detail_sum.sub(recent_total)
            .abs()
            .le(ACS_COMPONENT_RECONCILIATION_TOLERANCE)
            & mixed_detail_sum.sub(mixed_total)
            .abs()
            .le(ACS_COMPONENT_RECONCILIATION_TOLERANCE)
            & recent_total.add(mixed_total).le(
                b25127_total + ACS_COMPONENT_RECONCILIATION_TOLERANCE
            )
        )
        result.loc[
            recent_valid,
            "acs_renter_recent_construction_share_lower_bound",
        ] = recent_total.loc[recent_valid] / b25127_total.loc[recent_valid]
        result.loc[
            recent_valid,
            "acs_renter_recent_construction_share_upper_bound",
        ] = recent_total.loc[recent_valid].add(
            mixed_total.loc[recent_valid]
        ) / b25127_total.loc[recent_valid]
        recent_one_unit = b25127[
            "acs_b25127_renter_recent_lower_bound_1_unit"
        ]
        mixed_one_unit = b25127[
            "acs_b25127_renter_cutoff_mixed_1_unit"
        ]

    if "date" not in result:
        return result
    dates = pd.to_datetime(result["date"], errors="coerce")
    source_totals_match = b25032_total.sub(b25127_total).abs().le(
        ACS_COMPONENT_RECONCILIATION_TOLERANCE
    )
    age_band_one_unit = recent_one_unit.add(mixed_one_unit)
    one_unit_overlap_valid = age_band_one_unit.le(
        b25032_one_unit + ACS_COMPONENT_RECONCILIATION_TOLERANCE
    )
    proxy_valid = (
        structure_valid
        & recent_valid
        & source_totals_match
        & one_unit_overlap_valid
        & dates.dt.year.ge(ACS_NONCONTROLLED_STRUCTURAL_PROXY_START_YEAR)
    )
    union_numerator = (
        recent_total
        .add(mixed_total)
        .add(b25032_one_unit)
        .sub(recent_one_unit)
        .sub(mixed_one_unit)
    )
    proxy_lower = recent_total / b25127_total
    proxy_upper = (union_numerator / b25127_total).clip(upper=1.0)
    lower_column, upper_column = ACS_NONCONTROLLED_EXPOSURE_COLUMNS
    result.loc[proxy_valid, lower_column] = proxy_lower.loc[proxy_valid]
    result.loc[proxy_valid, upper_column] = proxy_upper.loc[proxy_valid]
    return result


def _add_noncontrolled_rent_growth_structural_sensitivity(
    annual_panel: pd.DataFrame,
    monthly_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Scale December ZORI YoY by same-year ACS structural bounds.

    This is deliberately a narrow annual sensitivity construction.  It pairs
    an ACS five-year estimate ending on December 31 with the ZORI year-over-
    year change observed at that exact December month end.  It does not carry
    ACS values forward to other months, join Census ZCTAs to postal ZIPs, or
    estimate rent growth *within* legally noncontrolled units.
    """

    result = annual_panel.copy()
    for column in NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS:
        result[column] = pd.Series(
            float("nan"), index=result.index, dtype="float64"
        )
    if result.empty or monthly_panel.empty:
        return result
    if "date" not in result or "date" not in monthly_panel:
        return result
    if "zori_growth_yoy" not in monthly_panel:
        return result
    if not set(ACS_NONCONTROLLED_EXPOSURE_COLUMNS).issubset(result.columns):
        return result

    monthly = monthly_panel[["date", "zori_growth_yoy"]].copy()
    monthly["date"] = pd.to_datetime(monthly["date"], errors="coerce")
    monthly = monthly.loc[
        monthly["date"].dt.month.eq(12)
        & monthly["date"].dt.day.eq(31)
    ].copy()
    if monthly["date"].duplicated().any():
        raise ValueError("December county ZORI growth contains duplicate dates")
    december_growth = pd.to_numeric(
        monthly.set_index("date")["zori_growth_yoy"], errors="coerce"
    ).replace([float("inf"), float("-inf")], pd.NA)

    annual_dates = pd.to_datetime(result["date"], errors="coerce")
    matched_growth = annual_dates.map(december_growth)
    for exposure_column, output_column in zip(
        ACS_NONCONTROLLED_EXPOSURE_COLUMNS,
        NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS,
    ):
        exposure = pd.to_numeric(
            result[exposure_column], errors="coerce"
        ).replace([float("inf"), float("-inf")], pd.NA)
        valid = exposure.between(0, 1) & matched_growth.notna()
        result.loc[valid, output_column] = (
            exposure.loc[valid] * matched_growth.loc[valid]
        )
    return result


def _add_acs_occupied_apartment_change(
    county_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Difference consecutive ACS renter-occupied five-year vintages.

    Adjacent ACS five-year estimates overlap for four of five survey years.
    The result is therefore a vintage-to-vintage change measure, not an
    observed one-year flow and not a quarterly series.
    """

    result = county_annual.copy()
    value_column = "net_change_occupied_apartments_annual"
    quality_column = "net_change_occupied_apartments_annual_quality_flag"
    result[value_column] = pd.Series(float("nan"), index=result.index, dtype="float64")
    result[quality_column] = "not_available_missing_current_or_prior_vintage"
    required = {"date", "geo_id", "acs_renter_occupied_units"}
    if result.empty or not required.issubset(result.columns):
        return result

    ordered = result.sort_values(["geo_id", "date"])
    current = pd.to_numeric(ordered["acs_renter_occupied_units"], errors="coerce")
    prior = current.groupby(ordered["geo_id"], sort=False).shift(1)
    dates = pd.to_datetime(ordered["date"], errors="coerce")
    prior_dates = dates.groupby(ordered["geo_id"], sort=False).shift(1)
    consecutive = dates.dt.year.sub(prior_dates.dt.year).eq(1)
    has_pair = current.notna() & prior.notna()
    valid = consecutive & has_pair
    ordered.loc[has_pair & ~consecutive, quality_column] = (
        "not_available_nonconsecutive_vintage"
    )
    ordered.loc[valid, value_column] = current.loc[valid] - prior.loc[valid]
    ordered.loc[valid, quality_column] = (
        "overlapping_5year_vintage_difference_not_one_year_flow"
    )
    result.loc[ordered.index, [value_column, quality_column]] = ordered[
        [value_column, quality_column]
    ]
    return result


def _add_acs_county_price_to_income(
    county_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Add an exact matched-vintage ACS county price-to-income ratio.

    B25077 median home value and B19013 median household income must already
    be aligned on the county and ACS five-year ending date by the annual panel.
    Published nulls and invalid inputs remain null; no annual value is carried
    into a quarter or month.
    """

    result = county_annual.copy()
    value_column = "county_annual_price_to_income_acs"
    quality_column = f"{value_column}_quality_flag"
    result[value_column] = pd.Series(
        float("nan"), index=result.index, dtype="float64"
    )
    result[quality_column] = "not_available_missing_source_input"
    required = {"acs_median_home_value", "acs_median_household_income"}
    if result.empty or not required.issubset(result.columns):
        return result

    home_value = pd.to_numeric(
        result["acs_median_home_value"], errors="coerce"
    )
    household_income = pd.to_numeric(
        result["acs_median_household_income"], errors="coerce"
    )
    negative_home_value = home_value.lt(0)
    nonpositive_income = household_income.le(0)
    valid = (
        home_value.notna()
        & household_income.notna()
        & ~negative_home_value
        & ~nonpositive_income
    )
    result.loc[negative_home_value, quality_column] = (
        "not_available_negative_home_value"
    )
    result.loc[
        household_income.notna() & nonpositive_income,
        quality_column,
    ] = "not_available_nonpositive_household_income"
    result.loc[valid, value_column] = (
        home_value.loc[valid] / household_income.loc[valid]
    )
    result.loc[valid, quality_column] = "matched_acs_5year_vintage_ratio"
    return result


def _add_acs_zcta_price_to_income(
    zcta_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Add a matched-vintage ACS ZCTA home-value-to-income ratio.

    Both inputs are five-year estimates for the same ZCTA and ending year.
    Published nulls remain null, a nonpositive income denominator is not used,
    and the result is never relabeled as a postal-ZIP observation.
    """

    result = zcta_annual.copy()
    value_column = "zcta_annual_price_to_income_acs"
    quality_column = "zcta_annual_price_to_income_acs_quality_flag"
    result[value_column] = pd.Series(float("nan"), index=result.index, dtype="float64")
    result[quality_column] = "not_available_missing_source_input"
    required = {
        "acs_median_home_value",
        "acs_median_household_income",
    }
    if result.empty or not required.issubset(result.columns):
        return result

    home_value = pd.to_numeric(
        result["acs_median_home_value"], errors="coerce"
    )
    household_income = pd.to_numeric(
        result["acs_median_household_income"], errors="coerce"
    )
    negative_home_value = home_value.lt(0)
    nonpositive_income = household_income.le(0)
    valid = (
        home_value.notna()
        & household_income.notna()
        & ~negative_home_value
        & ~nonpositive_income
    )
    result.loc[negative_home_value, quality_column] = (
        "not_available_negative_home_value"
    )
    result.loc[
        household_income.notna() & nonpositive_income,
        quality_column,
    ] = "not_available_nonpositive_household_income"
    result.loc[valid, value_column] = (
        home_value.loc[valid] / household_income.loc[valid]
    )
    result.loc[valid, quality_column] = "matched_acs_5year_vintage_ratio"
    return result


def _add_acs_zcta_renter_occupied_change(
    zcta_annual: pd.DataFrame,
) -> pd.DataFrame:
    """Add annual ZCTA differences between consecutive ACS B25003 vintages.

    Adjacent ACS five-year estimates overlap for four survey years.  This is a
    vintage difference, not a one-year flow and not a quarterly observation.
    """

    value_column = "zcta_renter_occupied_units_vintage_change_annual"
    quality_column = (
        "zcta_renter_occupied_units_vintage_change_annual_quality_flag"
    )
    result = zcta_annual.copy()
    result[value_column] = pd.Series(float("nan"), index=result.index, dtype="float64")
    result[quality_column] = "not_available_missing_current_or_prior_vintage"
    required = {"date", "geo_id", "acs_renter_occupied_units"}
    if result.empty or not required.issubset(result.columns):
        return result

    ordered = result.sort_values(["geo_id", "date"])
    current = pd.to_numeric(ordered["acs_renter_occupied_units"], errors="coerce")
    prior = current.groupby(ordered["geo_id"], sort=False).shift(1)
    dates = pd.to_datetime(ordered["date"], errors="coerce")
    prior_dates = dates.groupby(ordered["geo_id"], sort=False).shift(1)
    consecutive = dates.dt.year.sub(prior_dates.dt.year).eq(1)
    has_pair = current.notna() & prior.notna()
    valid = consecutive & has_pair
    ordered.loc[has_pair & ~consecutive, quality_column] = (
        "not_available_nonconsecutive_vintage"
    )
    ordered.loc[valid, value_column] = current.loc[valid] - prior.loc[valid]
    ordered.loc[valid, quality_column] = (
        "overlapping_5year_vintage_difference_not_one_year_flow"
    )
    result.loc[ordered.index, [value_column, quality_column]] = ordered[
        [value_column, quality_column]
    ]
    return result


def _zcta_annual_control_panel(fact: pd.DataFrame) -> pd.DataFrame:
    """Build a wide annual Census-ZCTA panel without postal-ZIP coercion."""

    panel = _wide_control_panel(
        fact,
        geo_prefixes=("ZCTA_",),
        frequency="annual",
        id_columns={},
    )
    if panel.empty:
        return panel
    geo_ids = panel["geo_id"].astype("string")
    invalid = ~geo_ids.str.fullmatch(r"ZCTA_\d{5}", na=False)
    if invalid.any():
        bad = sorted(set(geo_ids.loc[invalid].dropna().astype(str)))
        raise ValueError(f"Invalid Census ZCTA geography identifiers: {bad[:10]}")
    panel.insert(2, "zcta", geo_ids.str.removeprefix("ZCTA_"))
    panel.insert(3, "geography_type", "zcta")
    panel = _add_acs_zcta_price_to_income(panel)
    panel = _add_acs_zcta_renter_occupied_change(panel)
    return panel.sort_values(["zcta", "date"]).reset_index(drop=True)


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
    ingestible_variable_ids = {
        str(item["variable_id"])
        for item in config.variables
        if str(item.get("active_status", "Active")).casefold() != "quarantined"
    }
    fact = _read_clean_files(config)
    fact = fact.loc[fact["variable_id"].isin(ingestible_variable_ids)].copy()
    native_fact_rows = len(fact)
    fact = _add_ca_doi_monthly_control(fact)
    fact = _date_filter(fact, start_date, end_date)
    fact = fact.sort_values(
        ["observation_date", "geo_id", "variable_id", "source_vintage"]
    )
    macro_monthly = macro_monthly_panel(fact)
    county = _county_panel(fact, config)
    comparable_counties, comparable_parameters = _comparable_county_panel(
        fact, config
    )
    county = _merge_la_comparable_features(county, comparable_counties)
    zip_panel = _zip_panel(fact, config)
    (
        zip_regimes,
        zip_regime_summary,
        zip_regime_transitions,
        zip_regime_model,
    ) = _zip_regime_outputs(zip_panel, fact, config)
    homebuying_scenarios = _homebuying_scenario_layer(
        county, zip_panel, config
    )
    _redfin_yoy_report(config, zip_panel)
    slow = _slow_frequency_panels(fact, county)
    state_quarterly = _wide_control_panel(
        fact,
        geo_prefixes=("STATE_",),
        frequency="quarterly",
        id_columns={"state_fips": "06", "state_name": "California"},
    )
    state_annual = _wide_control_panel(
        fact,
        geo_prefixes=("STATE_",),
        frequency="annual",
        id_columns={"state_fips": "06", "state_name": "California"},
    )
    if "ca_homeowners_avg_written_premium" in state_annual:
        state_annual = state_annual.sort_values(["geo_id", "date"])
        premium = state_annual["ca_homeowners_avg_written_premium"]
        prior_premium = state_annual.groupby("geo_id", sort=False)[
            "ca_homeowners_avg_written_premium"
        ].shift(1)
        prior_date = state_annual.groupby("geo_id", sort=False)["date"].shift(1)
        consecutive = (
            pd.to_datetime(state_annual["date"]).dt.year
            - pd.to_datetime(prior_date).dt.year
        ).eq(1)
        valid = consecutive & premium.gt(0) & prior_premium.gt(0)
        state_annual["ca_homeowners_avg_written_premium_yoy"] = (
            premium.div(prior_premium).sub(1).where(valid)
        )
    state_annual = _add_real_homeowners_premium(state_annual)
    state_monthly = _state_monthly_control_panel(fact, config)
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
    county_annual = _add_approved_annual_rates(county_annual, fact)
    zcta_annual = _zcta_annual_control_panel(fact)
    coc_annual = _wide_control_panel(
        fact.loc[fact["state_fips"].astype("string").eq("06")],
        geo_prefixes=("COC_",),
        frequency="annual",
        id_columns={
            "state_fips": "06",
            "geography_type": "continuum_of_care_or_explicit_composite"
        },
    )
    county_annual = _add_acs_rental_availability(county_annual)
    county_annual = _add_acs_occupied_apartment_change(county_annual)
    county_annual = _add_acs_noncontrolled_exposure(county_annual)
    county_annual = _add_noncontrolled_rent_growth_structural_sensitivity(
        county_annual, county
    )
    zcta_annual = _add_acs_noncontrolled_exposure(zcta_annual)
    state_quarterly = _merge_structural_columns(
        state_quarterly,
        slow["state_quarterly"],
        structural_date="quarter_end",
        geo_id="STATE_06",
    )
    metro_monthly = _merge_structural_columns(
        metro_monthly,
        slow["metro_monthly"],
        structural_date="date",
        geo_id="METRO_31080",
    )
    county_annual = _add_acs_county_price_to_income(county_annual)
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
    if {
        "cdc_wonder_live_births_annual",
        "cdc_wonder_deaths_annual",
    }.issubset(county_annual.columns):
        county_annual["natural_population_change_annual"] = (
            county_annual["cdc_wonder_live_births_annual"]
            - county_annual["cdc_wonder_deaths_annual"]
        )
    county_annual = _merge_structural_columns(
        county_annual,
        slow["county_annual"],
        structural_date="year_end",
        geo_id="COUNTY_06037",
    )
    county_quarterly = _county_quarterly_from_annual_controls(county_annual)
    final_dir = config.root / "data" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    fact.to_parquet(final_dir / "fact_housing_observation.parquet", index=False)
    macro_monthly.to_parquet(final_dir / "macro_financial_monthly.parquet", index=False)
    county.to_parquet(final_dir / "county_monthly_panel.parquet", index=False)
    comparable_counties.to_parquet(
        final_dir / "comparable_county_monthly_analytics.parquet", index=False
    )
    zip_panel.to_parquet(final_dir / "zip_monthly_panel.parquet", index=False)
    zip_regimes.to_parquet(
        final_dir / "zip_market_regime_quarterly.parquet", index=False
    )
    zip_regime_summary.to_parquet(
        final_dir / "la_zip_market_regime_summary_quarterly.parquet", index=False
    )
    zip_regime_transitions.to_parquet(
        final_dir / "la_zip_market_regime_transitions_quarterly.parquet", index=False
    )
    homebuying_scenarios.to_parquet(
        final_dir / "homebuying_cost_scenarios.parquet", index=False
    )
    state_monthly.to_parquet(
        final_dir / "state_monthly_controls.parquet", index=False
    )
    state_quarterly.to_parquet(
        final_dir / "state_quarterly_panel.parquet", index=False
    )
    state_annual.to_parquet(
        final_dir / "state_annual_controls.parquet", index=False
    )
    metro_monthly.to_parquet(
        final_dir / "metro_monthly_controls.parquet", index=False
    )
    slow["metro_quarterly"].to_parquet(
        final_dir / "metro_quarterly_panel.parquet", index=False
    )
    metro_quarterly.to_parquet(
        final_dir / "metro_quarterly_controls.parquet", index=False
    )
    county_annual.to_parquet(
        final_dir / "county_annual_controls.parquet", index=False
    )
    zcta_annual.to_parquet(
        final_dir / "zcta_annual_controls.parquet", index=False
    )
    coc_annual.to_parquet(
        final_dir / "coc_annual_controls.parquet", index=False
    )
    county_quarterly.to_parquet(
        final_dir / "county_quarterly_controls.parquet", index=False
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
        monthly_finance_rows = prior["transformation_id"].astype(str).str.startswith(
            ("version_b_", "version_c_"), na=False
        )
        prior = prior.loc[
            prior["source_id"].isin(active_sources) | monthly_finance_rows
        ]
    executed_at = pd.Timestamp.now(tz="UTC").isoformat()
    acs_renter_county_inputs = fact.loc[
        fact["variable_id"].eq("acs_renter_occupied_units")
        & fact["geo_id"].eq("COUNTY_06037")
    ].copy()
    acs_county_price_income_inputs = fact.loc[
        fact["variable_id"].isin(
            {"acs_median_home_value", "acs_median_household_income"}
        )
        & fact["geo_id"].eq("COUNTY_06037")
    ].copy()
    acs_zcta_price_income_inputs = fact.loc[
        fact["variable_id"].isin(
            {"acs_median_home_value", "acs_median_household_income"}
        )
        & fact["geo_id"].astype("string").str.startswith("ZCTA_")
    ].copy()
    acs_renter_zcta_inputs = fact.loc[
        fact["variable_id"].eq("acs_renter_occupied_units")
        & fact["geo_id"].astype("string").str.startswith("ZCTA_")
    ].copy()
    acs_b25032_inputs = fact.loc[
        fact["source_id"].eq("census_acs_5year_b25032")
        & fact["variable_id"].astype("string").str.startswith("acs_b25032_")
    ].copy()
    acs_b25127_inputs = fact.loc[
        fact["source_id"].eq("census_acs_5year_b25127")
        & fact["variable_id"].astype("string").str.startswith("acs_b25127_")
    ].copy()
    acs_noncontrolled_inputs = pd.concat(
        [acs_b25032_inputs, acs_b25127_inputs], ignore_index=True
    )
    zori_noncontrolled_inputs = fact.loc[
        fact["variable_id"].eq("zori_smoothed_sa")
        & fact["geo_id"].eq("COUNTY_06037")
    ].copy()
    bps_5_plus_inputs = fact.loc[
        fact["variable_id"].eq("permits_5_plus_units")
        & fact["geo_id"].eq("COUNTY_06037")
        & fact["final_frequency"].eq("monthly")
    ].copy()
    real_premium_inputs = fact.loc[
        fact["variable_id"].isin(
            {
                "ca_homeowners_avg_written_premium",
                "ca_cpi_u_all_items_annual",
            }
        )
        & fact["geo_id"].eq("STATE_06")
        & fact["final_frequency"].eq("annual")
    ].copy()
    net_exemption_rate_inputs = fact.loc[
        fact["variable_id"].isin(
            {
                "irs_soi_net_migration_exemptions",
                "census_pep_county_population_annual",
            }
        )
        & fact["geo_id"].eq("COUNTY_06037")
        & fact["final_frequency"].eq("annual")
    ].copy()
    permit_rate_inputs = fact.loc[
        fact["variable_id"].isin(
            {
                "permits_1_unit_annual_final",
                "census_pep_county_housing_units_annual",
            }
        )
        & fact["geo_id"].eq("COUNTY_06037")
        & fact["final_frequency"].eq("annual")
    ].copy()

    def _joined_lineage(frame: pd.DataFrame, column: str) -> str | None:
        if column not in frame:
            return None
        values = sorted(
            {
                str(value)
                for value in frame[column].dropna()
                if str(value)
            }
        )
        return "|".join(values) or None

    panel_transformations = pd.DataFrame(
        [
            {
                "transformation_id": "fact_union_latest_clean_v1",
                "source_id": "multiple",
                "input_file": "data/clean/*.parquet",
                "output_file": "data/final/fact_housing_observation.parquet",
                "input_rows_or_shape": native_fact_rows,
                "output_rows": native_fact_rows,
                "description": (
                    "Union the latest selected clean source vintages without "
                    "interpolation; explicitly documented derived controls are "
                    "added by separate transformations."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": (
                    "ca_doi_premium_calendar_year_monthly_control_v1"
                ),
                "source_id": "ca_doi_residential_property_part_i",
                "input_file": "data/clean/ca_doi_residential_property_part_i.parquet",
                "output_file": (
                    "data/final/fact_housing_observation.parquet|"
                    "data/final/state_monthly_controls.parquet"
                ),
                "input_rows_or_shape": int(
                    fact["variable_id"]
                    .eq("ca_homeowners_avg_written_premium")
                    .sum()
                ),
                "output_rows": len(state_monthly),
                "description": (
                    "Repeat each annual California Homeowners Multi-Peril average "
                    "written premium at all 12 month ends of the same exposure "
                    "year for downstream mixed-frequency analysis. Retain annual "
                    "provenance and native frequency; mark every row constructed, "
                    "non-observed, carried annual, and not interpolated."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": (
                    fact.loc[
                        fact["variable_id"].eq(
                            "ca_homeowners_avg_written_premium"
                        ),
                        "source_vintage",
                    ].max()
                    if not fact.empty
                    else None
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "bps_5_plus_distributed_completion_lag_v1",
                "source_id": "census_bps_county",
                "input_file": "data/clean/census_bps_county.parquet",
                "output_file": "data/final/county_monthly_panel.parquet",
                "input_rows_or_shape": len(bps_5_plus_inputs),
                "output_rows": int(
                    sum(
                        county.get(column, pd.Series(dtype="float64"))
                        .notna()
                        .sum()
                        for column in APARTMENT_COMPLETION_PROXY_COLUMNS.values()
                    )
                ),
                "description": (
                    "Distribute each Los Angeles County five-plus-unit BPS permit "
                    "cohort across the exact workbook-approved Central (12-17: "
                    "20%, 18-23: 55%, 24-30: 25%) and Slow (18-23: 20%, "
                    "24-29: 50%, 30-36: 30%) completion-lag buckets, equally "
                    "within each bucket. Require every weighted calendar-month "
                    "input, propagate source nulls, preserve the 2022 BPS regime "
                    "break, and label both outputs as modeled proxies rather than "
                    "observed apartment completions."
                ),
                "code_module": "src.features.derived|src.harmonization.build",
                "source_vintage": _joined_lineage(
                    bps_5_plus_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    bps_5_plus_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    bps_5_plus_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    bps_5_plus_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "bps_5_plus_stock_flow_pipeline_v1",
                "source_id": "census_bps_county",
                "input_file": "data/clean/census_bps_county.parquet",
                "output_file": "data/final/county_monthly_panel.parquet",
                "input_rows_or_shape": len(bps_5_plus_inputs),
                "output_rows": int(
                    sum(
                        county.get(column, pd.Series(dtype="float64"))
                        .notna()
                        .sum()
                        for column in MULTIFAMILY_UNDER_CONSTRUCTION_PROXY_COLUMNS.values()
                    )
                ),
                "description": (
                    "Initialize each Central and Slow modeled construction-pipeline "
                    "stock from every still-active five-plus-unit permit cohort, "
                    "then apply the stock-flow identity of prior stock plus current "
                    "permits minus scenario completions. Require complete 30- or "
                    "36-month active-cohort history, never assume an opening zero, "
                    "and reinitialize only after complete history returns. These "
                    "are modeled stocks, not observed projects under construction."
                ),
                "code_module": "src.features.derived|src.harmonization.build",
                "source_vintage": _joined_lineage(
                    bps_5_plus_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    bps_5_plus_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    bps_5_plus_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    bps_5_plus_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "ca_homeowners_premium_real_2023_cpi_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/ca_doi_residential_property_part_i.parquet|"
                    "data/clean/ca_dir_california_cpi.parquet"
                ),
                "output_file": "data/final/state_annual_controls.parquet",
                "input_rows_or_shape": len(real_premium_inputs),
                "output_rows": int(
                    state_annual.get(
                        "ca_homeowners_avg_written_premium_real",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Match nominal statewide Homeowners Multi-Peril average written "
                    "premium to the California annual CPI-U for the same calendar "
                    "year and multiply by CPI_2023 / CPI_year. Retain the canonical "
                    "annual frequency, constant-2023-dollar label, source gaps, and "
                    "state geography; do not interpolate or imply a county premium."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    real_premium_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    real_premium_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    real_premium_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    real_premium_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "irs_soi_net_exemptions_per_1000_pep_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/irs_soi_county_migration.parquet|"
                    "data/clean/census_pep_county_population_v2019.parquet|"
                    "data/clean/census_pep_county_population_v2025.parquet"
                ),
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": len(net_exemption_rate_inputs),
                "output_rows": int(
                    county_annual.get(
                        "net_exemption_rate", pd.Series(dtype="float64")
                    ).notna().sum()
                ),
                "description": (
                    "Divide IRS SOI net migration exemptions by the matched-ending-"
                    "year Census PEP county resident population and multiply by "
                    "1,000. Preserve the PEP vintage segments and IRS 2022-2023 "
                    "methodology break; suppress missing or nonpositive denominators "
                    "and do not substitute ACS population or create subannual data."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    net_exemption_rate_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    net_exemption_rate_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    net_exemption_rate_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    net_exemption_rate_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "bps_1_unit_per_1000_pep_housing_units_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_bps_county_annual_final.parquet|"
                    "data/clean/census_pep_county_housing_units_v2019.parquet|"
                    "data/clean/census_pep_county_housing_units_v2025.parquet"
                ),
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": len(permit_rate_inputs),
                "output_rows": int(
                    county_annual.get(
                        "permits_1_unit_per_1000_housing_units",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Divide official final annual one-unit BPS permit authorizations "
                    "by the same-year Census PEP county housing-unit estimate and "
                    "multiply by 1,000 over the confirmed 2010-2025 overlap. Preserve "
                    "the PEP vintage and BPS 2022 universe breaks and imputation "
                    "flags; never substitute ACS or a fixed housing stock."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    permit_rate_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    permit_rate_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    permit_rate_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    permit_rate_inputs, "retrieved_at"
                ),
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
                "description": (
                    "Pivot postal-ZIP observations while retaining partial-coverage "
                    "rows; calculate exact-calendar Zillow growth, positive-only "
                    "price-to-rent and gross-yield fields, and canonical sale-price "
                    "closing-cost scenarios without filling source gaps."
                ),
                "code_module": "src.harmonization.build|src.features.derived",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "homebuying_cost_scenario_layer_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/final/county_monthly_panel.parquet|"
                    "data/final/zip_monthly_panel.parquet"
                ),
                "output_file": "data/final/homebuying_cost_scenarios.parquet",
                "input_rows_or_shape": len(county) + len(zip_panel),
                "output_rows": len(homebuying_scenarios),
                "description": (
                    "Cross each positive Redfin county or ZIP price observation "
                    "with every configured down-payment scenario and the approved "
                    "sale-price 2/3/5-percent and loan-amount 3/6-percent closing-"
                    "cost assumptions. Keep ZIP rolling-three-month provenance, "
                    "exclude prepaid escrow, and never select one arbitrary down "
                    "payment for a loan-based sensitivity."
                ),
                "code_module": "src.harmonization.build|src.features.derived",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "native_frequency_control_panels_v1",
                "source_id": "multiple",
                "input_file": "data/final/fact_housing_observation.parquet",
                "output_file": (
                    "data/final/state_monthly_controls.parquet|"
                    "data/final/state_quarterly_panel.parquet|"
                    "data/final/state_annual_controls.parquet|"
                    "data/final/metro_monthly_controls.parquet|"
                    "data/final/metro_quarterly_controls.parquet|"
                    "data/final/county_annual_controls.parquet|"
                    "data/final/zcta_annual_controls.parquet|"
                    "data/final/coc_annual_controls.parquet|"
                    "data/final/county_quarterly_controls.parquet"
                ),
                "input_rows_or_shape": len(fact),
                "output_rows": (
                    len(state_monthly)
                    + len(state_quarterly)
                    + len(state_annual)
                    + len(metro_monthly)
                    + len(metro_quarterly)
                    + len(county_annual)
                    + len(zcta_annual)
                    + len(coc_annual)
                    + len(county_quarterly)
                ),
                "description": (
                    "Pivot state, metro, annual county, and native CoC controls. "
                    "Also pivot annual Census-ZCTA controls while preserving the "
                    "ZCTA identifier as Census geography rather than postal ZIP. "
                    "Except for the explicitly authorized and flagged California "
                    "DOI calendar-year monthly step control, retain native frequency. "
                    "Calculate the exact annual California DOI average-premium "
                    "year-over-year rate without filling missing years. Preserve "
                    "the explicit four-CoC HUD composite as a CoC composite, never "
                    "as Los Angeles County. "
                    "Also compute documented annual ACS proxies such as "
                    "owner-occupied housing value and implied aggregate mortgage "
                    "debt from B25106/B25081/Freddie Mac assumptions, plus the "
                    "CDC WONDER annual natural population change and its clearly "
                    "flagged quarterly allocation."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "acs_b25032_renter_structure_shares_v1",
                "source_id": "census_acs_5year_b25032",
                "input_file": "data/clean/census_acs_5year_b25032.parquet",
                "output_file": (
                    "data/final/county_annual_controls.parquet|"
                    "data/final/zcta_annual_controls.parquet"
                ),
                "input_rows_or_shape": len(acs_b25032_inputs),
                "output_rows": int(
                    county_annual.get(
                        "acs_renter_occupied_1_unit_share",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                    + zcta_annual.get(
                        "acs_renter_occupied_1_unit_share",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Divide reconciled ACS B25032 renter-occupied structure "
                    "components by the matched renter total to produce one-unit, "
                    "two-to-four-unit, five-plus-unit, and mobile/other annual "
                    "shares. Preserve county and exact Census-ZCTA identity; do "
                    "not create postal-ZIP or nonannual observations."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_b25032_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_b25032_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_b25032_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_b25032_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": (
                    "acs_b25127_recent_construction_share_bounds_v1"
                ),
                "source_id": "census_acs_5year_b25127",
                "input_file": "data/clean/census_acs_5year_b25127.parquet",
                "output_file": (
                    "data/final/county_annual_controls.parquet|"
                    "data/final/zcta_annual_controls.parquet"
                ),
                "input_rows_or_shape": len(acs_b25127_inputs),
                "output_rows": int(
                    county_annual.get(
                        "acs_renter_recent_construction_share_lower_bound",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                    + zcta_annual.get(
                        "acs_renter_recent_construction_share_lower_bound",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Use the vintage-normalized ACS B25127 renter year-built "
                    "bands after reconciling their structure components. The "
                    "lower share is recent_lower_bound_total/renter_total and "
                    "the upper share adds cutoff_mixed_total. Published ACS bin "
                    "definitions change by vintage, so these are annual structural "
                    "bounds rather than a stable legal-eligibility classification."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_b25127_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_b25127_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_b25127_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_b25127_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": (
                    "acs_noncontrolled_exposure_structural_bounds_v1"
                ),
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_acs_5year_b25032.parquet|"
                    "data/clean/census_acs_5year_b25127.parquet"
                ),
                "output_file": (
                    "data/final/county_annual_controls.parquet|"
                    "data/final/zcta_annual_controls.parquet"
                ),
                "input_rows_or_shape": len(acs_noncontrolled_inputs),
                "output_rows": int(
                    county_annual.get(
                        ACS_NONCONTROLLED_EXPOSURE_COLUMNS[0],
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                    + zcta_annual.get(
                        ACS_NONCONTROLLED_EXPOSURE_COLUMNS[0],
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Publish annual structural proxy bounds from 2020 onward "
                    "only after B25032 and B25127 totals/components reconcile. "
                    "The lower bound is B25127 recent_total/renter_total. The "
                    "upper bound unions recent and mixed age bands with B25032 "
                    "one-unit rentals, subtracts the B25127 recent/mixed one-unit "
                    "overlap, and caps the reconciled ratio at one. This is a "
                    "coarse ACS structural-exposure proxy, not observed or legal "
                    "rent-control eligibility; no exact point, policy crosswalk, "
                    "monthly carry-forward, or postal-ZIP relabeling is created."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_noncontrolled_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_noncontrolled_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_noncontrolled_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_noncontrolled_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": (
                    "noncontrolled_rent_growth_structural_sensitivity_v1"
                ),
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_acs_5year_b25032.parquet|"
                    "data/clean/census_acs_5year_b25127.parquet|"
                    "data/clean/zillow_zori_county.parquet"
                ),
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": (
                    len(acs_noncontrolled_inputs)
                    + len(zori_noncontrolled_inputs)
                ),
                "output_rows": int(
                    county_annual.get(
                        NONCONTROLLED_RENT_GROWTH_STRUCTURAL_SENSITIVITY_COLUMNS[0],
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "At each exact December 31 from 2020 onward, multiply the "
                    "Los Angeles County ZORI year-over-year fractional change "
                    "by the same-ending-year ACS structural lower and upper "
                    "noncontrolled-exposure bounds. These are annual exposure-"
                    "scaled sensitivity bounds, not observed rent growth within "
                    "legally noncontrolled units. No ACS carry-forward, monthly "
                    "expansion, exact exposure point, or ZCTA-to-postal-ZIP join "
                    "is created."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    pd.concat(
                        [acs_noncontrolled_inputs, zori_noncontrolled_inputs],
                        ignore_index=True,
                    ),
                    "source_vintage",
                ),
                "source_filename": _joined_lineage(
                    pd.concat(
                        [acs_noncontrolled_inputs, zori_noncontrolled_inputs],
                        ignore_index=True,
                    ),
                    "source_filename",
                ),
                "checksum_sha256": _joined_lineage(
                    pd.concat(
                        [acs_noncontrolled_inputs, zori_noncontrolled_inputs],
                        ignore_index=True,
                    ),
                    "checksum_sha256",
                ),
                "retrieved_at": _joined_lineage(
                    pd.concat(
                        [acs_noncontrolled_inputs, zori_noncontrolled_inputs],
                        ignore_index=True,
                    ),
                    "retrieved_at",
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "acs_rental_availability_rate_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_acs_5year_b25003.parquet|"
                    "data/clean/census_acs_5year_b25004.parquet"
                ),
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": len(
                    fact.loc[
                        fact["variable_id"].isin(
                            {
                                "acs_renter_occupied_units",
                                "acs_renter_occupied_units_moe",
                                "acs_for_rent_vacant_units",
                                "acs_for_rent_vacant_units_moe",
                            }
                        )
                    ]
                ),
                "output_rows": int(
                    county_annual.get(
                        "acs_rental_availability_rate",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Match ACS B25003 renter-occupied units and B25004 for-rent "
                    "vacant units by county and ACS ending year; calculate "
                    "F/(R+F), retain both published component MOEs, and suppress "
                    "the rate when an input is invalid or either 90-percent "
                    "component MOE is at least its estimate. This annual proxy "
                    "is not monthly rental-listing inventory."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": fact["source_vintage"].max() if not fact.empty else None,
                "executed_at": executed_at,
            },
            {
                "transformation_id": "acs_renter_occupied_vintage_change_v1",
                "source_id": "census_acs_5year_b25003",
                "input_file": "data/clean/census_acs_5year_b25003.parquet",
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": len(acs_renter_county_inputs),
                "output_rows": int(
                    county_annual.get(
                        "net_change_occupied_apartments_annual",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Subtract consecutive published ACS B25003 renter-occupied "
                    "housing-unit five-year estimates for Los Angeles County. "
                    "This is not a literal multifamily-apartment universe. Adjacent "
                    "vintages overlap for four survey years, so this is labeled "
                    "as a vintage-to-vintage change rather than a one-year flow; "
                    "no quarterly timing is created, and the point difference is "
                    "not a significance test. Published current/prior MOEs remain "
                    "available in the annual controls."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_renter_county_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_renter_county_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_renter_county_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_renter_county_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "acs_county_price_to_income_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_acs_5year_b19013.parquet|"
                    "data/clean/census_acs_5year_b25077.parquet"
                ),
                "output_file": "data/final/county_annual_controls.parquet",
                "input_rows_or_shape": len(acs_county_price_income_inputs),
                "output_rows": int(
                    county_annual.get(
                        "county_annual_price_to_income_acs",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Match ACS B25077 median home value and B19013 median "
                    "household income on Los Angeles County and the exact "
                    "five-year ending vintage, then divide home value by "
                    "positive income. Preserve source nulls and invalid "
                    "denominators; retain the result only in county annual "
                    "controls without quarterly or monthly expansion."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_county_price_income_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_county_price_income_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_county_price_income_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_county_price_income_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "acs_zcta_price_to_income_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/census_acs_5year_b19013.parquet|"
                    "data/clean/census_acs_5year_b25077.parquet"
                ),
                "output_file": "data/final/zcta_annual_controls.parquet",
                "input_rows_or_shape": len(acs_zcta_price_income_inputs),
                "output_rows": int(
                    zcta_annual.get(
                        "zcta_annual_price_to_income_acs",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Match ACS B25077 median home value and B19013 median "
                    "household income on the exact Census ZCTA and five-year "
                    "ending vintage, then divide home value by income. Preserve "
                    "published source nulls and suppress nonpositive denominators; "
                    "do not coerce Census ZCTAs to USPS postal ZIPs or create "
                    "quarterly timing."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_zcta_price_income_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_zcta_price_income_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_zcta_price_income_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_zcta_price_income_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": (
                    "acs_zcta_renter_occupied_vintage_change_v1"
                ),
                "source_id": "census_acs_5year_b25003",
                "input_file": "data/clean/census_acs_5year_b25003.parquet",
                "output_file": "data/final/zcta_annual_controls.parquet",
                "input_rows_or_shape": len(acs_renter_zcta_inputs),
                "output_rows": int(
                    zcta_annual.get(
                        "zcta_renter_occupied_units_vintage_change_annual",
                        pd.Series(dtype="float64"),
                    ).notna().sum()
                ),
                "description": (
                    "Within each exact Census ZCTA, subtract the immediately "
                    "prior ending-year ACS B25003 renter-occupied estimate only "
                    "when both five-year vintages exist and are consecutive. "
                    "Adjacent vintages overlap for four survey years, so the "
                    "result is not a one-year or quarterly flow and no timing is "
                    "invented."
                ),
                "code_module": "src.harmonization.build",
                "source_vintage": _joined_lineage(
                    acs_renter_zcta_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    acs_renter_zcta_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    acs_renter_zcta_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    acs_renter_zcta_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
        ]
    )
    comparable_inputs = fact.loc[
        fact["geo_id"].isin(
            {
                f"COUNTY_{value}"
                for value in config.pipeline["comparable_county_analytics"][
                    "target_county_fips"
                ]
            }
        )
        & fact["variable_id"].isin(COMPARABLE_COUNTY_VARIABLES)
    ]
    zip_analytics_inputs = fact.loc[
        fact["variable_id"].isin({"zhvi", "zori_smoothed_sa"})
        & (
            fact["geo_id"].astype("string").str.startswith("ZIP_")
            | fact["geo_id"].eq("COUNTY_06037")
        )
    ]
    supplemental_transformations = pd.DataFrame(
        [
            {
                "transformation_id": "la_comparable_county_analytics_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/zillow_county_zhvi.parquet|"
                    "data/clean/zillow_county_zori_smoothed_sa.parquet|"
                    "data/clean/redfin_county_monthly.parquet|"
                    "data/clean/bls_laus_la_county.parquet"
                ),
                "output_file": (
                    "data/final/comparable_county_monthly_analytics.parquet|"
                    "metadata/comparable_county_reference_parameters_v1.json"
                ),
                "input_rows_or_shape": len(comparable_inputs),
                "output_rows": len(comparable_counties),
                "description": (
                    "Build the exact eight-county monthly comparison layer. "
                    "Construct 100-times 12-month and 400-times three-month "
                    "annualized calendar-log ZHVI/ZORI momentum, Zillow price-"
                    "to-rent level plus explicit one- and twelve-month log "
                    "changes, LAUS resident-employment log momentum, exact "
                    "three-component tightness excluding inventory, LA-minus-"
                    "all-seven-peer measures, and the two-component heat index. "
                    "Apply frozen pooled population-SD parameters from the "
                    "configured pre-COVID reference; require all three tightness "
                    "inputs and all seven peers without filling source gaps."
                ),
                "code_module": "src.harmonization.build|src.features.comparable_counties",
                "source_vintage": _joined_lineage(
                    comparable_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    comparable_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    comparable_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    comparable_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": "la_zip_momentum_relative_valuation_v1",
                "source_id": "multiple",
                "input_file": (
                    "data/clean/zillow_zip_zhvi.parquet|"
                    "data/clean/zillow_zip_zori_smoothed_sa.parquet|"
                    "data/clean/zillow_county_zhvi.parquet|"
                    "data/clean/zillow_county_zori_smoothed_sa.parquet"
                ),
                "output_file": "data/final/zip_monthly_panel.parquet",
                "input_rows_or_shape": len(zip_analytics_inputs),
                "output_rows": len(zip_panel),
                "description": (
                    "Add exact-calendar ZHVI/ZORI log momentum, Zillow price-to-"
                    "rent log changes, and ln(PTR_zip)-ln(PTR_LA County) relative "
                    "valuation pressure to the existing Los Angeles postal-ZIP "
                    "monthly panel. Require positive matched inputs, preserve "
                    "source nulls, and never relabel postal ZIPs as Census ZCTAs."
                ),
                "code_module": "src.harmonization.build|src.features.comparable_counties",
                "source_vintage": _joined_lineage(
                    zip_analytics_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    zip_analytics_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    zip_analytics_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    zip_analytics_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
            {
                "transformation_id": str(
                    config.pipeline["zip_market_regimes"]["methodology_id"]
                ),
                "source_id": "multiple",
                "input_file": (
                    "data/final/zip_monthly_panel.parquet|"
                    f"{config.pipeline['zip_market_regimes']['model_path']}"
                ),
                "output_file": (
                    "data/final/zip_market_regime_quarterly.parquet|"
                    "data/final/la_zip_market_regime_summary_quarterly.parquet|"
                    "data/final/la_zip_market_regime_transitions_quarterly.parquet"
                ),
                "input_rows_or_shape": len(zip_panel),
                "output_rows": (
                    f"assignments={len(zip_regimes)};summary={len(zip_regime_summary)};"
                    f"transitions={len(zip_regime_transitions)}"
                ),
                "description": (
                    "Construct quarter-end three-month annualized log ZHVI/ZORI "
                    "momentum, matched Zillow price-to-rent, and a complete 24-"
                    "month monthly-log-growth volatility window. Fit k=4 once "
                    "over complete 2015Q2-2019Q4 postal-ZIP quarters, persist and "
                    "fingerprint the scaler/centers and empirical labels, then "
                    "assign complete 2020-and-later observations to the nearest "
                    "frozen center. Publish changing universe, classified, and "
                    "consecutive-quarter denominators plus full transitions. "
                    "Canonical changed, entry and transition shares use current "
                    "classified N_t; suppress those shares unless all current "
                    "classified ZIPs have a prior-quarter classification. Keep "
                    "comparable-pair percentages as separately named diagnostics."
                ),
                "code_module": "src.features.market_regimes",
                "source_vintage": _joined_lineage(
                    zip_analytics_inputs, "source_vintage"
                ),
                "source_filename": _joined_lineage(
                    zip_analytics_inputs, "source_filename"
                ),
                "checksum_sha256": _joined_lineage(
                    zip_analytics_inputs, "checksum_sha256"
                ),
                "retrieved_at": _joined_lineage(
                    zip_analytics_inputs, "retrieved_at"
                ),
                "executed_at": executed_at,
            },
        ]
    )
    panel_transformations = pd.concat(
        [panel_transformations, supplemental_transformations], ignore_index=True
    )
    regime_share_log = supplemental_transformations.loc[
        supplemental_transformations["transformation_id"].eq(
            config.pipeline["zip_market_regimes"]["methodology_id"]
        )
    ].copy()
    regime_share_log["transformation_id"] = config.pipeline["zip_market_regimes"].get(
        "transition_share_methodology_id", "la_zip_regime_current_classified_denominator_v1"
    )
    regime_share_log["output_file"] = (
        "data/final/la_zip_market_regime_summary_quarterly.parquet|"
        "data/final/la_zip_market_regime_transitions_quarterly.parquet"
    )
    regime_share_log["description"] = (
        "zip_market_regime_changed_share, zip_market_regime_entry_share and "
        "zip_market_regime_transition_share = 100 * observed numerator / current "
        "classified N_t, published only when every currently classified ZIP has "
        "a classification in the exact previous quarter. Otherwise retain null, "
        "missing_previous_classification_count and transition_denominator_status. "
        "Preserve *_of_comparable conditional diagnostics as separate columns; "
        "never substitute their denominator for the document's N_t."
    )
    regime_share_log["code_module"] = "src.features.market_regimes|src.harmonization.build"
    panel_transformations = pd.concat([panel_transformations, regime_share_log], ignore_index=True)
    macro_inputs = fact.loc[fact.source_id.isin(MACRO_SOURCE_IDS) & fact.final_frequency.eq("monthly")]
    if not macro_inputs.empty:
        panel_transformations = pd.concat([panel_transformations, pd.DataFrame([{
            "transformation_id": "macro_financial_monthly_geography_preserving_pivot_v2",
            "source_id": "multiple",
            "input_file": "|".join(f"data/clean/{source}.parquet" for source in sorted(macro_inputs.source_id.unique())),
            "output_file": "data/final/macro_financial_monthly.parquet",
            "input_rows_or_shape": len(macro_inputs), "output_rows": len(macro_monthly),
            "description": "Enforce the explicit 27-field monthly delivery contract by its own US, California, or LA division geography; no national-to-state/local broadcasting, interpolation, or filling. Exclude native daily/weekly audit inputs and required-quarterly GDP from monthly output. Retain all native inputs and formulas in fact and source transformation logs.",
            "code_module": "src.features.macro_financial",
            "source_vintage": _joined_lineage(macro_inputs, "source_vintage"),
            "source_filename": _joined_lineage(macro_inputs, "source_filename"),
            "checksum_sha256": _joined_lineage(macro_inputs, "checksum_sha256"),
            "retrieved_at": _joined_lineage(macro_inputs, "retrieved_at"),
            "executed_at": executed_at,
        }])], ignore_index=True)
    transformations = pd.concat([prior, panel_transformations], ignore_index=True)
    transformations = transformations.drop_duplicates(
        ["transformation_id", "source_id", "source_vintage"], keep="last"
    )
    transformations.to_csv(transformation_path, index=False)
    register_monthly_finance_contracts(root=config.root)
    if (config.root / "config" / "nine_constructions.yaml").is_file():
        build_nine_constructions(config)
    logger.info(
        "Built fact=%s, county=%s, comparable_county=%s, ZIP=%s observations/rows",
        len(fact),
        len(county),
        len(comparable_counties),
        len(zip_panel),
    )
    return {
        "fact": fact,
        "macro_financial_monthly": macro_monthly,
        "county": county,
        "comparable_counties": comparable_counties,
        "zip": zip_panel,
        "zip_market_regimes": zip_regimes,
        "zip_market_regime_summary": zip_regime_summary,
        "zip_market_regime_transitions": zip_regime_transitions,
        "homebuying_cost_scenarios": homebuying_scenarios,
        "state_monthly": state_monthly,
        "state_quarterly": state_quarterly,
        "state_annual": state_annual,
        "metro_monthly": metro_monthly,
        "metro_quarterly": metro_quarterly,
        "metro_quarterly_panel": slow["metro_quarterly"],
        "county_annual": county_annual,
        "zcta_annual": zcta_annual,
        "coc_annual": coc_annual,
        "county_quarterly": county_quarterly,
        "geography": geography,
        "crosswalk": crosswalk,
    }
