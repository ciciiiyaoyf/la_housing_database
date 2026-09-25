from __future__ import annotations

import csv
import math
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.benchmark.schema import BENCHMARK_COLUMNS
from src.housing_finance_monthly.retrospective import (
    CANONICAL_DEBT_BENCHMARK_IDS,
    CANONICAL_EQUITY_BENCHMARK_IDS,
    CANONICAL_NULL_BENCHMARK_IDS,
    HMDA_FLOW_BENCHMARK_IDS,
    HMDA_RATE_BENCHMARK_IDS,
    HMDA_SHARE_BENCHMARK_IDS,
    PAYMENT_BENCHMARK_IDS,
    PMMS_DEBT_BENCHMARK_IDS,
    PMMS_EQUITY_ALL_BENCHMARK_IDS,
    PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS,
    RAW_DEBT_BENCHMARK_ID,
    TRANSFORM_CALIBRATED_NULL,
    TRANSFORM_HMDA_FLOW_NULL,
    TRANSFORM_HMDA_PROXY,
    TRANSFORM_HMDA_RATE,
    TRANSFORM_PAYMENT,
    TRANSFORM_PMMS_DEBT,
    TRANSFORM_PMMS_EQUITY,
    TRANSFORM_RAW_DEBT,
    build_retrospective_monthly,
    monthly_hist_id,
    redfin_proxy_id,
    residual_safe_distribution,
)
from src.housing_finance_monthly.schema import (
    MONTHLY_FINANCE_COLUMNS,
    monthly_finance_contract_errors,
)
from src.housing_finance_monthly.sources import (
    CALIFORNIA_COUNTY_NAME_TO_FIPS,
    read_all_ca_redfin_monthly,
    read_all_ca_zillow_zhvi,
    read_california_nmdb_upb,
    verified_california_county_mapping,
)


ROOT = Path(__file__).resolve().parents[1]


def _anchor(
    variable_id: str,
    value: float | None,
    *,
    year: int = 2022,
    unit: str = "USD",
    estimate_variant: str = "central",
    is_suppressed: bool = False,
) -> dict[str, object]:
    low = None if value is None else value * 0.9
    high = None if value is None else value * 1.1
    row = dict.fromkeys(BENCHMARK_COLUMNS)
    row.update(
        {
            "county_fips": "06037",
            "state_fips": "06",
            "geo_id": "COUNTY_06037",
            "month": None,
            "value": value,
            "variable_name": variable_id,
            "variable_id": variable_id,
            "unit": unit,
            "estimate_variant": estimate_variant,
            "reported_value": value,
            "database_version": "A. Benchmark",
            "source_id": "version_a_test",
            "source_frequency": "annual",
            "source_period_start": f"{year}-01-01",
            "source_period_end": f"{year}-12-31",
            "reference_date": f"{year}-07-01",
            "release_date": f"{year + 1}-01-01",
            "source_release_dates": f"version_a_test={year + 1}-01-01",
            "source_geography": "county",
            "parameter_geography": "county",
            "monthly_method": "not_applicable_source_period",
            "construction_method": "test Version A anchor",
            "payment_frequency": "monthly",
            "is_modeled": False,
            "is_nowcast": False,
            "is_imputed": False,
            "is_interpolated": False,
            "is_suppressed": is_suppressed,
            "benchmark_vintage": f"test_{year}",
            "sample_n": 100,
            "coverage_rate": 1.0,
            "low_estimate": low,
            "central_estimate": value,
            "high_estimate": high,
            "universe_definition": "test owner-occupied universe",
            "notes": "synthetic test anchor",
            "source_vintage": f"test_{year}",
            "retrieved_at": "2026-02-01T00:00:00Z",
            "source_filename": "version_a_test.parquet",
            "checksum_sha256": "a" * 64,
            "transformation_id": "version_a_test_v1",
            "lineage_id": ("b" * 63) + str(year % 10),
            "quality_flag": (
                "suppressed_low_sample"
                if is_suppressed
                else (
                    "calibration_unavailable"
                    if value is None
                    else "test_anchor"
                )
            ),
        }
    )
    return row


def _benchmark() -> pd.DataFrame:
    payment = [
        ("acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark", 120.0, "USD/month"),
        ("acs_first_mortgage_pi_per_mortgaged_unit_benchmark", 12.0, "USD/unit/month"),
        ("acs_annual_first_mortgage_debt_service_benchmark", 1440.0, "USD/year"),
        ("acs_mortgage_debt_service_to_income_benchmark", 0.12, "ratio"),
    ]
    rates: list[tuple[str, float]] = []
    for group, offset in (("", 0.0), ("_conventional", 0.01), ("_fha", 0.02), ("_va", 0.03)):
        rates.extend(
            [
                (f"hmda_median_downpayment_rate{group}_benchmark", 0.10 + offset),
                (
                    f"hmda_property_value_weighted_downpayment_rate{group}_benchmark",
                    0.12 + offset,
                ),
            ]
        )
    flows = [
        ("hmda_aggregate_downpayment_flow_benchmark", 1200.0),
        ("hmda_aggregate_downpayment_flow_conventional_benchmark", 900.0),
        ("hmda_aggregate_downpayment_flow_fha_benchmark", 200.0),
        ("hmda_aggregate_downpayment_flow_va_benchmark", 100.0),
    ]
    rows = [_anchor(name, value, unit=unit) for name, value, unit in payment]
    rows += [_anchor(name, value, year=2025, unit="rate") for name, value in rates]
    for position, name in enumerate(HMDA_SHARE_BENCHMARK_IDS, start=1):
        suppressed = name == "hmda_share_downpayment_below_20pct_va_benchmark"
        rows.append(
            _anchor(
                name,
                None if suppressed else position / 100.0,
                year=2025,
                unit="share",
                is_suppressed=suppressed,
            )
        )
    rows += [_anchor(name, value, year=2025) for name, value in flows]
    null_units = {
        "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark": "USD/unit",
        "acs_first_mortgage_debt_per_owner_unit_benchmark": "USD/unit",
        "acs_first_mortgage_debt_per_capita_benchmark": "USD/person",
        "acs_first_mortgage_debt_to_owner_income_benchmark": "ratio",
        "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark": "USD/unit",
        "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark": "constant_2024_USD",
        "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark": "constant_2024_USD",
        "acs_aggregate_ltv_mortgaged_benchmark": "ratio",
        "acs_equity_rate_mortgaged_benchmark": "ratio",
    }
    rows += [
        _anchor(
            name,
            None,
            unit=null_units.get(name, "USD"),
        )
        for name in CANONICAL_NULL_BENCHMARK_IDS
    ]
    rows += [
        _anchor(RAW_DEBT_BENCHMARK_ID, 550.0),
        _anchor("acs_aggregate_owner_value_mortgaged_benchmark", 2000.0),
        _anchor("acs_aggregate_owner_value_no_mortgage_benchmark", 1000.0),
    ]
    for years, debt_value in ((20, 500.0), (25, 600.0), (30, 700.0)):
        rows.extend(
            [
                _anchor(
                    "acs_implied_first_mortgage_balance_raw_pmms_"
                    f"{years}y_sensitivity_benchmark",
                    debt_value,
                    estimate_variant=f"pmms_{years}y_sensitivity",
                ),
                _anchor(
                    "acs_implied_owner_occupied_home_equity_mortgaged_pmms_"
                    f"{years}y_scenario_benchmark",
                    2000.0 - debt_value,
                    estimate_variant=f"pmms_{years}y_scenario",
                ),
                _anchor(
                    "acs_implied_owner_occupied_home_equity_all_pmms_"
                    f"{years}y_scenario_benchmark",
                    3000.0 - debt_value,
                    estimate_variant=f"pmms_{years}y_scenario",
                ),
            ]
        )
    return pd.DataFrame(rows)


def _metadata(source_id: str, frequency: str, checksum_character: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_frequency": frequency,
        "source_filename": f"{source_id}.csv",
        "source_vintage": "test_vintage",
        "retrieved_at": pd.Timestamp("2026-02-01", tz="UTC"),
        "release_date": pd.Timestamp("2026-01-01"),
        "source_release_dates": f"{source_id}=2026-01-01",
        "checksum_sha256": checksum_character * 64,
        "quality_flag": f"{source_id}_test",
    }


def _indicators() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    redfin_rows: list[dict[str, object]] = []
    redfin_meta = _metadata("redfin_county_monthly", "monthly", "c")
    for month, sold in zip(
        pd.date_range("2022-06-30", "2022-09-30", freq="ME"),
        (8.0, 10.0, 12.0, 14.0),
        strict=True,
    ):
        redfin_rows.append(
            {
                "county_fips": "06037",
                "month": month,
                "homes_sold": sold,
                "median_sale_price": 100.0,
                "purchase_value_proxy": sold * 100.0,
                **redfin_meta,
            }
        )
    for position, month in enumerate(
        pd.date_range("2025-01-31", "2025-12-31", freq="ME"), start=1
    ):
        redfin_rows.append(
            {
                "county_fips": "06037",
                "month": month,
                "homes_sold": float(position),
                "median_sale_price": 100.0,
                "purchase_value_proxy": float(position * 100),
                **redfin_meta,
            }
        )
    zillow_meta = _metadata("zillow_county_zhvi", "monthly", "d")
    zillow = pd.DataFrame(
        [
            {
                "county_fips": "06037",
                "month": month,
                "zhvi": value,
                **zillow_meta,
            }
            for month, value in zip(
                pd.date_range("2022-06-30", "2022-09-30", freq="ME"),
                (90.0, 100.0, 110.0, 120.0),
                strict=True,
            )
        ]
    )
    nmdb_meta = _metadata("fhfa_nmdb_ca_upb", "quarterly", "e")
    nmdb = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2022-06-30"),
                "nmdb_ca_all_mortgages_upb": 100.0,
                **nmdb_meta,
            },
            {
                "month": pd.Timestamp("2022-09-30"),
                "nmdb_ca_all_mortgages_upb": 110.0,
                **nmdb_meta,
            },
        ]
    )
    return pd.DataFrame(redfin_rows), zillow, nmdb


def _expected_version_b_ids() -> set[str]:
    benchmark_ids = (
        *PAYMENT_BENCHMARK_IDS,
        *HMDA_RATE_BENCHMARK_IDS,
        *HMDA_SHARE_BENCHMARK_IDS,
        RAW_DEBT_BENCHMARK_ID,
        *CANONICAL_NULL_BENCHMARK_IDS,
        *PMMS_DEBT_BENCHMARK_IDS.values(),
        *PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS.values(),
        *PMMS_EQUITY_ALL_BENCHMARK_IDS.values(),
    )
    expected = {monthly_hist_id(name) for name in benchmark_ids}
    expected.update(redfin_proxy_id(name) for name in HMDA_FLOW_BENCHMARK_IDS)
    expected.update(monthly_hist_id(name) for name in HMDA_FLOW_BENCHMARK_IDS)
    return expected


def test_version_b_builder_exact_ids_identities_and_flags() -> None:
    redfin, zillow, nmdb = _indicators()
    result = build_retrospective_monthly(_benchmark(), redfin, zillow, nmdb)

    assert list(result.columns) == MONTHLY_FINANCE_COLUMNS
    assert monthly_finance_contract_errors(result, version="B") == []
    assert result["variable_id"].str.endswith("_monthly_hist").all()
    expected_ids = _expected_version_b_ids()
    assert len(expected_ids) == 58
    assert set(result["variable_id"]) == expected_ids
    assert result["is_modeled"].all()
    assert not result["is_nowcast"].any()
    assert not result.duplicated(
        ["county_fips", "month", "variable_id", "estimate_variant"]
    ).any()

    payment_id = "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_hist"
    payment = result.loc[result["variable_id"].eq(payment_id)]
    assert len(payment) == 12
    assert payment["value"].mean() == pytest.approx(120.0)
    assert set(payment["transformation_id"]) == {TRANSFORM_PAYMENT}

    rate_id = "hmda_median_downpayment_rate_va_monthly_hist"
    rate = result.loc[result["variable_id"].eq(rate_id)]
    assert len(rate) == 12
    assert rate["value"].eq(0.13).all()
    assert set(rate["transformation_id"]) == {TRANSFORM_HMDA_RATE}

    canonical_flow_id = "hmda_aggregate_downpayment_flow_monthly_hist"
    canonical_flow = result.loc[result["variable_id"].eq(canonical_flow_id)]
    assert len(canonical_flow) == 12
    assert canonical_flow["value"].isna().all()
    assert set(canonical_flow["transformation_id"]) == {TRANSFORM_HMDA_FLOW_NULL}

    proxy_id = (
        "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_hist"
    )
    proxy = result.loc[result["variable_id"].eq(proxy_id)].sort_values("month")
    assert len(proxy) == 12
    assert math.fsum(proxy["value"].tolist()) == 1200.0
    assert set(proxy["transformation_id"]) == {TRANSFORM_HMDA_PROXY}

    canonical_debt_id = "acs_implied_first_mortgage_balance_cal_monthly_hist"
    canonical_debt = result.loc[result["variable_id"].eq(canonical_debt_id)]
    assert canonical_debt["value"].isna().all()
    assert set(canonical_debt["transformation_id"]) == {TRANSFORM_CALIBRATED_NULL}

    debt_id = (
        "acs_implied_first_mortgage_balance_raw_pmms_20y_"
        "sensitivity_monthly_hist"
    )
    debt = result.loc[result["variable_id"].eq(debt_id)].set_index("month")
    assert debt.loc[pd.Timestamp("2022-07-31"), "value"] == pytest.approx(500.0)
    assert set(debt["transformation_id"]) == {TRANSFORM_PMMS_DEBT}

    mort_equity_id = (
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_"
        "scenario_monthly_hist"
    )
    all_equity_id = (
        "acs_implied_owner_occupied_home_equity_all_pmms_20y_"
        "scenario_monthly_hist"
    )
    mort_equity = result.loc[
        result["variable_id"].eq(mort_equity_id)
    ].set_index("month")
    all_equity = result.loc[result["variable_id"].eq(all_equity_id)].set_index("month")
    anchor_month = pd.Timestamp("2022-07-31")
    assert mort_equity.loc[anchor_month, "value"] == pytest.approx(1500.0)
    assert all_equity.loc[anchor_month, "value"] == pytest.approx(2500.0)
    assert set(mort_equity["transformation_id"]) == {TRANSFORM_PMMS_EQUITY}
    assert "diagnostics" in result.attrs


def test_hmda_share_controls_repeat_suppression_and_do_not_divide() -> None:
    redfin, zillow, nmdb = _indicators()
    result = build_retrospective_monthly(_benchmark(), redfin, zillow, nmdb)

    populated_id = "hmda_share_downpayment_below_3pct_monthly_hist"
    populated = result.loc[result["variable_id"].eq(populated_id)]
    assert len(populated) == 12
    assert populated["value"].eq(0.01).all()
    assert populated["central_estimate"].eq(0.01).all()
    assert not populated["is_suppressed"].any()
    assert set(populated["transformation_id"]) == {TRANSFORM_HMDA_RATE}
    assert populated["monthly_method"].eq(
        "hmda_activity_year_rate_repeated_not_divided"
    ).all()

    suppressed_id = "hmda_share_downpayment_below_20pct_va_monthly_hist"
    suppressed = result.loc[result["variable_id"].eq(suppressed_id)]
    assert len(suppressed) == 12
    assert suppressed["value"].isna().all()
    assert suppressed["low_estimate"].isna().all()
    assert suppressed["high_estimate"].isna().all()
    assert suppressed["is_suppressed"].all()
    assert suppressed["quality_flag"].str.contains("suppressed_low_sample").all()


def test_raw_debt_uses_own_anchor_path_and_preserves_bounds() -> None:
    redfin, zillow, nmdb = _indicators()
    result = build_retrospective_monthly(_benchmark(), redfin, zillow, nmdb)
    raw_id = monthly_hist_id(RAW_DEBT_BENCHMARK_ID)
    raw = result.loc[result["variable_id"].eq(raw_id)].set_index("month")

    anchor_month = pd.Timestamp("2022-07-31")
    assert raw.loc[anchor_month, "value"] == pytest.approx(550.0)
    assert raw.loc[anchor_month, "low_estimate"] == pytest.approx(495.0)
    assert raw.loc[anchor_month, "high_estimate"] == pytest.approx(605.0)
    assert raw.loc[anchor_month, "benchmark_variable_id"] == RAW_DEBT_BENCHMARK_ID
    assert set(raw["transformation_id"]) == {TRANSFORM_RAW_DEBT}
    assert raw["quality_flag"].str.contains(
        "nmdb_all_mortgages_growth_only", regex=False
    ).all()
    assert not raw["quality_flag"].str.contains("pmms", case=False).any()
    assert raw["source_id"].str.contains("version_a_test", regex=False).all()
    assert raw["source_id"].str.contains("fhfa_nmdb_ca_upb", regex=False).all()
    assert raw["source_id"].str.contains("redfin_county_monthly", regex=False).all()


def test_all_twelve_calibrated_normalized_equity_ltv_targets_are_flagged_null() -> None:
    redfin, zillow, nmdb = _indicators()
    result = build_retrospective_monthly(_benchmark(), redfin, zillow, nmdb)
    target_ids = {monthly_hist_id(name) for name in CANONICAL_NULL_BENCHMARK_IDS}
    assert len(CANONICAL_DEBT_BENCHMARK_IDS) == 5
    assert len(CANONICAL_EQUITY_BENCHMARK_IDS) == 7
    assert len(target_ids) == 12

    targets = result.loc[result["variable_id"].isin(target_ids)]
    assert set(targets["variable_id"]) == target_ids
    assert targets.groupby("variable_id").size().eq(4).all()
    assert targets["value"].isna().all()
    assert targets["central_estimate"].isna().all()
    assert targets["low_estimate"].isna().all()
    assert targets["high_estimate"].isna().all()
    assert set(targets["transformation_id"]) == {TRANSFORM_CALIBRATED_NULL}
    assert targets["quality_flag"].eq(
        "universe_compatible_calibration_control_unavailable"
    ).all()


def test_version_b_dictionary_and_transform_catalog_cover_exact_58_surface() -> None:
    with (ROOT / "config" / "version_b_variables.yaml").open(
        encoding="utf-8"
    ) as handle:
        dictionary = yaml.safe_load(handle)
    variables = dictionary["variables"]
    by_id = {row["variable_id"]: row for row in variables}

    assert len(variables) == len(by_id) == 58
    assert set(by_id) == _expected_version_b_ids()
    assert Counter(row["output_status"] for row in variables) == {
        "canonical_modeled_repeat": 28,
        "canonical_null": 16,
        "scenario": 10,
        "proxy": 4,
    }
    assert by_id[monthly_hist_id(RAW_DEBT_BENCHMARK_ID)][
        "transformation_id"
    ] == TRANSFORM_RAW_DEBT

    with (ROOT / "metadata" / "version_b_transformation_catalog.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        catalog = {
            row["transformation_id"]: row for row in csv.DictReader(handle)
        }
    assert TRANSFORM_RAW_DEBT in catalog
    raw_text = " ".join(catalog[TRANSFORM_RAW_DEBT].values()).lower()
    assert "growth indicator only" in raw_text
    assert "pmms" not in raw_text


def test_incomplete_redfin_year_produces_explicit_null_proxy() -> None:
    redfin, zillow, nmdb = _indicators()
    redfin = redfin.loc[~redfin["month"].eq(pd.Timestamp("2025-03-31"))]
    result = build_retrospective_monthly(_benchmark(), redfin, zillow, nmdb)
    proxy = result.loc[
        result["variable_id"].eq(
            "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_hist"
        )
    ]
    assert len(proxy) == 12
    assert proxy["value"].isna().all()
    assert proxy["quality_flag"].str.contains("coverage").all()


def test_residual_safe_distribution_is_exact() -> None:
    result = residual_safe_distribution(10.0, [1.0, 2.0, 3.0, 7.0])
    assert math.fsum(result) == 10.0

    large_total = 5_566_044_965.38
    weights = [
        559.747456289384,
        368.0180176092136,
        425.46227305976845,
        806.2392473844322,
        589.0153230795825,
        970.4846959334616,
        602.4737151988286,
        283.29062720248754,
        513.115579738027,
        472.9018332726147,
        852.9260885964339,
        737.4995460179213,
    ]
    large_result = residual_safe_distribution(large_total, weights)
    assert math.fsum(large_result) == large_total


def test_source_readers_require_verified_all_county_mapping(tmp_path) -> None:
    mapping = verified_california_county_mapping()
    assert len(mapping) == 58
    assert set(mapping.values()) == set(CALIFORNIA_COUNTY_NAME_TO_FIPS.values())

    redfin = pd.DataFrame(
        [
            {
                "REGION": county,
                "STATE_CODE": "CA",
                "REGION_TYPE": "County",
                "PROPERTY_TYPE": "All Residential",
                "PERIOD_DURATION": 30,
                "PERIOD_END": "2025-01-31",
                "MEDIAN_SALE_PRICE": 500000,
                "HOMES_SOLD": 10,
                "IS_SEASONALLY_ADJUSTED": "false",
            }
            for county in CALIFORNIA_COUNTY_NAME_TO_FIPS
        ]
    )
    redfin_path = tmp_path / "redfin.tsv"
    redfin.to_csv(redfin_path, sep="\t", index=False)
    redfin_result = read_all_ca_redfin_monthly(redfin_path)
    assert redfin_result["county_fips"].nunique() == 58
    assert redfin_result["purchase_value_proxy"].eq(5_000_000).all()

    zillow = pd.DataFrame(
        [
            {
                "RegionName": county,
                "StateName": "CA",
                "2025-01-31": 500000,
            }
            for county in CALIFORNIA_COUNTY_NAME_TO_FIPS
        ]
    )
    zillow_path = tmp_path / "zillow.csv"
    zillow.to_csv(zillow_path, index=False)
    zillow_result = read_all_ca_zillow_zhvi(zillow_path)
    assert zillow_result["county_fips"].nunique() == 58

    nmdb = pd.DataFrame(
        [
            {
                "GEOID": "CA",
                "GEOLEVEL": "State",
                "MARKET": "All Mortgages",
                "SERIESID": "TOT_UPB",
                "FREQUENCY": "Quarterly",
                "SUPPRESSED": 0,
                "YEAR": 2025,
                "MONTH": 3,
                "VALUE1": 100.0,
                "VALUE2": 100.0,
            }
        ]
    )
    nmdb_path = tmp_path / "nmdb.csv"
    nmdb.to_csv(nmdb_path, index=False)
    nmdb_result = read_california_nmdb_upb(nmdb_path)
    assert nmdb_result.loc[0, "nmdb_ca_all_mortgages_upb"] == 100.0
    assert "growth_indicator_only" in nmdb_result.loc[0, "quality_flag"]
