from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.housing_finance_monthly.realtime import (
    CANONICAL_DEBT_ID,
    CANONICAL_EQUITY_ALL_ID,
    CANONICAL_EQUITY_MORTGAGED_ID,
    CALIBRATED_NORMALIZED_NULL_IDS,
    COMPOSITE_RAW_DEBT_ID,
    HMDA_RATE_IDS,
    HMDA_THRESHOLD_SHARE_IDS,
    NoEligibleVintageError,
    PAYMENT_MONTHLY_ID,
    PAYMENT_CONTROL_BENCHMARK_IDS,
    PAYMENT_CONTROL_IDS,
    PMMS_DEBT_IDS,
    PMMS_EQUITY_ALL_IDS,
    PMMS_EQUITY_MORTGAGED_IDS,
    add_source_availability,
    build_pmms_debt_scenarios,
    build_realtime_monthly,
    build_redfin_purchase_value_proxy_flows,
    carry_latest_hmda_rates,
    carry_latest_payment,
    carry_latest_payment_controls,
    deterministic_vintage_id,
    stepwise_indicator_values,
)


CUTOFF = "2026-08-17"
COUNTY = "06037"


def _anchor(
    variable_id: str,
    value: float | None,
    *,
    source_id: str,
    release_date: str,
    retrieved_at: str,
    source_period_start: str,
    source_period_end: str,
    reference_date: str,
    benchmark_vintage: str,
    unit: str,
    quality_flag: str = "reported",
    is_suppressed: bool = False,
    low_estimate: float | None = None,
    high_estimate: float | None = None,
    sample_n: int = 500,
    coverage_rate: float = 0.95,
) -> dict:
    return {
        "county_fips": COUNTY,
        "variable_id": variable_id,
        "variable_name": variable_id,
        "value": value,
        "unit": unit,
        "estimate_variant": "central",
        "source_id": source_id,
        "source_vintage": benchmark_vintage,
        "source_filename": f"{source_id}_{benchmark_vintage}.csv",
        "checksum_sha256": f"sha-{source_id}-{benchmark_vintage}",
        "source_period_start": source_period_start,
        "source_period_end": source_period_end,
        "reference_date": reference_date,
        "release_date": release_date,
        "source_release_dates": f"{source_id}={release_date}",
        "retrieved_at": retrieved_at,
        "benchmark_vintage": benchmark_vintage,
        "quality_flag": quality_flag,
        "is_suppressed": is_suppressed,
        "low_estimate": low_estimate,
        "central_estimate": value,
        "high_estimate": high_estimate,
        "sample_n": sample_n,
        "coverage_rate": coverage_rate,
        "source_frequency": "annual" if source_id == "hmda" else "5-year",
        "source_geography": "county",
        "parameter_geography": "county",
        "universe_definition": "owner-occupied first mortgages",
        "payment_frequency": "monthly" if source_id == "acs_pums" else None,
    }


def _benchmarks() -> pd.DataFrame:
    rows = [
        _anchor(
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            1_000.0,
            source_id="acs_pums",
            release_date="2026-07-26",
            retrieved_at="2026-07-26T08:00:00Z",
            source_period_start="2020-01-01",
            source_period_end="2024-12-31",
            reference_date="2022-07-01",
            benchmark_vintage="2024_ACS5",
            unit="USD/month",
            low_estimate=900.0,
            high_estimate=1_100.0,
        ),
        _anchor(
            "acs_aggregate_owner_value_mortgaged_benchmark",
            1_000_000.0,
            source_id="acs",
            release_date="2026-01-29",
            retrieved_at="2026-07-26T08:00:00Z",
            source_period_start="2020-01-01",
            source_period_end="2024-12-31",
            reference_date="2022-07-01",
            benchmark_vintage="2024_ACS5",
            unit="USD",
            low_estimate=900_000.0,
            high_estimate=1_100_000.0,
        ),
        _anchor(
            "acs_aggregate_owner_value_all_benchmark",
            1_500_000.0,
            source_id="acs",
            release_date="2026-01-29",
            retrieved_at="2026-07-26T08:00:00Z",
            source_period_start="2020-01-01",
            source_period_end="2024-12-31",
            reference_date="2022-07-01",
            benchmark_vintage="2024_ACS5",
            unit="USD",
            low_estimate=1_400_000.0,
            high_estimate=1_600_000.0,
        ),
    ]
    for index, benchmark_id in enumerate(PAYMENT_CONTROL_BENCHMARK_IDS, start=1):
        value = (100.0, 12_000.0, 0.20)[index - 1]
        unit = ("USD/unit/month", "USD/year", "ratio")[index - 1]
        rows.append(
            _anchor(
                benchmark_id,
                value,
                source_id="acs_pums",
                release_date="2026-07-26",
                retrieved_at="2026-07-26T08:00:00Z",
                source_period_start="2020-01-01",
                source_period_end="2024-12-31",
                reference_date="2022-07-01",
                benchmark_vintage="2024_ACS5",
                unit=unit,
                low_estimate=value * 0.9,
                high_estimate=value * 1.1,
            )
        )
    rows.append(
        _anchor(
            "acs_implied_first_mortgage_balance_raw_benchmark",
            150_000.0,
            source_id="raw_debt_benchmark",
            release_date="2026-07-28",
            retrieved_at="2026-07-30T09:30:00Z",
            source_period_start="2020-01-01",
            source_period_end="2024-12-31",
            reference_date="2022-07-01",
            benchmark_vintage="2024_ACS5_PMMS",
            unit="USD",
            low_estimate=120_000.0,
            high_estimate=200_000.0,
        )
    )
    for index, variable_id in enumerate(HMDA_RATE_IDS, start=1):
        rows.append(
            _anchor(
                variable_id,
                index / 100.0,
                source_id="hmda",
                release_date="2026-08-02",
                retrieved_at="2026-08-07T19:21:11Z",
                source_period_start="2025-01-01",
                source_period_end="2025-12-31",
                reference_date="2025-07-01",
                benchmark_vintage="2025_HMDA",
                unit="ratio",
                low_estimate=index / 100.0 - 0.001,
                high_estimate=index / 100.0 + 0.001,
            )
        )
    for index, variable_id in enumerate(HMDA_THRESHOLD_SHARE_IDS, start=1):
        suppressed = variable_id == "hmda_share_downpayment_below_3pct_va_benchmark"
        rows.append(
            _anchor(
                variable_id,
                None if suppressed else index / 100.0,
                source_id="hmda",
                release_date="2026-08-02",
                retrieved_at="2026-08-07T19:21:11Z",
                source_period_start="2025-01-01",
                source_period_end="2025-12-31",
                reference_date="2025-07-01",
                benchmark_vintage="2025_HMDA",
                unit="share",
                quality_flag="minimum_sample_suppressed" if suppressed else "reported",
                is_suppressed=suppressed,
                sample_n=40 if suppressed else 200,
                coverage_rate=0.80 if suppressed else 0.95,
            )
        )
    for index, variable_id in enumerate(
        (
            "hmda_aggregate_downpayment_flow_benchmark",
            "hmda_aggregate_downpayment_flow_conventional_benchmark",
            "hmda_aggregate_downpayment_flow_fha_benchmark",
            "hmda_aggregate_downpayment_flow_va_benchmark",
        ),
        start=1,
    ):
        rows.append(
            _anchor(
                variable_id,
                1_200.0 * index,
                source_id="hmda",
                release_date="2026-08-02",
                retrieved_at="2026-08-07T19:21:11Z",
                source_period_start="2025-01-01",
                source_period_end="2025-12-31",
                reference_date="2025-07-01",
                benchmark_vintage="2025_HMDA",
                unit="USD",
                low_estimate=1_100.0 * index,
                high_estimate=1_300.0 * index,
            )
        )
    return pd.DataFrame(rows)


def _redfin() -> pd.DataFrame:
    rows: list[dict] = []
    for month in pd.date_range("2025-01-31", "2025-12-31", freq="ME"):
        rows.append(
            {
                "county_fips": COUNTY,
                "observation_date": month,
                "homes_sold": 1.0,
                "median_sale_price": 10.0,
            }
        )
    rows.extend(
        [
            {
                "county_fips": COUNTY,
                "observation_date": "2026-01-31",
                "homes_sold": 2.0,
                "median_sale_price": 10.0,
            },
            {
                "county_fips": COUNTY,
                "observation_date": "2026-05-31",
                "homes_sold": 3.0,
                "median_sale_price": 10.0,
            },
        ]
    )
    result = pd.DataFrame(rows)
    result["source_id"] = "redfin_county_monthly"
    result["source_vintage"] = "2026-06-02"
    result["provider_last_updated"] = "2026-06-02T14:33:24Z"
    result["retrieved_at"] = "2026-07-02T06:08:28Z"
    result["source_filename"] = "redfin_20260602.tsv.gz"
    result["checksum_sha256"] = "redfin-sha"
    result["source_frequency"] = "monthly"
    result["source_release_dates"] = "redfin_county_monthly=2026-06-02"
    result["source_geography"] = "county"
    return result


def _pmms(*, include_future_poison: bool = False) -> pd.DataFrame:
    rows = [
        {
            "county_fips": None,
            "observation_date": "2022-06-30",
            "variable_id": "mortgage_rate_30y_monthly_median",
            "value": 4.8,
            "source_id": "freddie_mac_pmms",
            "source_vintage": "2026-07-01",
            "source_release_dates": "freddie_mac_pmms=unavailable",
            "retrieved_at": "2026-07-01T22:33:36Z",
            "source_filename": "pmms_current.csv",
            "checksum_sha256": "pmms-current-sha",
            "native_frequency": "weekly",
            "final_frequency": "monthly",
            "geo_id": "US",
        },
        {
            "county_fips": None,
            "observation_date": "2022-07-31",
            "variable_id": "mortgage_rate_30y_monthly_median",
            "value": 5.0,
            "source_id": "freddie_mac_pmms",
            "source_vintage": "2026-07-01",
            "source_release_dates": "freddie_mac_pmms=unavailable",
            "retrieved_at": "2026-07-01T22:33:36Z",
            "source_filename": "pmms_current.csv",
            "checksum_sha256": "pmms-current-sha",
            "native_frequency": "weekly",
            "final_frequency": "monthly",
            "geo_id": "US",
        },
        {
            "county_fips": None,
            "observation_date": "2026-05-31",
            "variable_id": "mortgage_rate_30y_monthly_median",
            "value": 6.0,
            "source_id": "freddie_mac_pmms",
            "source_vintage": "2026-07-01",
            "source_release_dates": "freddie_mac_pmms=unavailable",
            "retrieved_at": "2026-07-01T22:33:36Z",
            "source_filename": "pmms_current.csv",
            "checksum_sha256": "pmms-current-sha",
            "native_frequency": "weekly",
            "final_frequency": "monthly",
            "geo_id": "US",
        }
    ]
    if include_future_poison:
        rows.append(
            {
                "county_fips": None,
                "observation_date": "2026-06-30",
                "variable_id": "mortgage_rate_30y_monthly_median",
                "value": 99.0,
                "source_id": "freddie_mac_pmms",
                "source_vintage": "2026-09-01",
                "source_release_dates": "freddie_mac_pmms=unavailable",
                "retrieved_at": "2026-09-01T00:00:00Z",
                "source_filename": "pmms_future.csv",
                "checksum_sha256": "pmms-future-sha",
                "native_frequency": "weekly",
                "final_frequency": "monthly",
                "geo_id": "US",
            }
        )
    return pd.DataFrame(rows)


def _hpi() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "county_fips": COUNTY,
                "observation_date": "2022-06-30",
                "value": 99.0,
            },
            {
                "county_fips": COUNTY,
                "observation_date": "2022-07-31",
                "value": 100.0,
            },
            {
                "county_fips": COUNTY,
                "observation_date": "2026-05-31",
                "value": 120.0,
            },
        ]
    ).assign(
        source_id="zillow_county_zhvi",
        source_vintage="2026-07-01",
        retrieved_at="2026-07-01T22:32:40Z",
        source_filename="zillow_county_20260701.csv",
        checksum_sha256="zillow-sha",
        source_frequency="monthly",
        source_release_dates="zillow_county_zhvi=unavailable",
        source_geography="county",
    )


def test_availability_uses_strict_release_update_retrieval_precedence() -> None:
    frame = pd.DataFrame(
        [
            {
                "release_date": "2026-01-01",
                "provider_last_updated": "2026-01-02",
                "retrieved_at": "2026-01-03",
            },
            {
                "provider_last_updated": "2026-02-02",
                "retrieved_at": "2026-02-03",
            },
            {"retrieved_at": "2026-03-03"},
            {
                "release_date": "2020-01-01",
                "source_release_dates": "crosswalk=unavailable",
                "retrieved_at": "2026-04-03",
            },
        ]
    )

    result = add_source_availability(frame)

    assert list(result["availability_date_basis"]) == [
        "release_date",
        "provider_last_updated",
        "retrieved_at_conservative",
        "retrieved_at_conservative",
    ]
    assert result.loc[3, "availability_date"] == pd.Timestamp(
        "2026-04-03", tz="UTC"
    )


def test_future_anchor_cannot_poison_payment_or_vintage_id() -> None:
    current = _benchmarks().loc[
        lambda frame: frame["variable_id"].eq(
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
        )
    ]
    future = pd.DataFrame(
        [
            _anchor(
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
                999_999.0,
                source_id="acs_pums",
                release_date="2026-09-01",
                retrieved_at="2026-09-02",
                source_period_start="2021-01-01",
                source_period_end="2025-12-31",
                reference_date="2023-07-01",
                benchmark_vintage="2025_ACS5",
                unit="USD/month",
            )
        ]
    )
    poisoned = pd.concat([current, future], ignore_index=True)

    baseline = carry_latest_payment(current, ["2026-08-31"], CUTOFF)
    result = carry_latest_payment(poisoned, ["2026-08-31"], CUTOFF)

    assert result.loc[0, "value"] == 1_000.0
    assert result.loc[0, "vintage_id"] == baseline.loc[0, "vintage_id"]
    assert deterministic_vintage_id(CUTOFF, current) == deterministic_vintage_id(
        CUTOFF, poisoned
    )


def test_past_cutoff_without_archived_anchor_fails_instead_of_backfilling() -> None:
    with pytest.raises(NoEligibleVintageError):
        carry_latest_payment(_benchmarks(), ["2026-06-30"], "2026-07-01")


def test_hmda_rate_carry_includes_all_loan_types_and_never_divides() -> None:
    result = carry_latest_hmda_rates(
        _benchmarks(), ["2026-05-31"], CUTOFF
    ).sort_values("variable_id")
    rates = result.loc[
        result["variable_id"].isin(
            {variable_id.removesuffix("_benchmark") + "_monthly_rt" for variable_id in HMDA_RATE_IDS}
        )
    ]

    assert len(result) == 24
    assert result["variable_id"].str.endswith("_monthly_rt").all()
    assert sorted(rates["value"]) == pytest.approx(
        sorted(index / 100.0 for index in range(1, 9))
    )
    assert rates["unit"].eq("rate").all()
    assert result.loc[~result.index.isin(rates.index), "unit"].eq("share").all()
    assert result["is_nowcast"].all()
    assert not result["is_interpolated"].any()
    assert set(result["transformation_id"]) == {
        "version_c_latest_hmda_rate_carry_v1"
    }


def test_payment_controls_and_hmda_threshold_diagnostics_carry_bounds() -> None:
    controls = carry_latest_payment_controls(
        _benchmarks(), ["2026-05-31"], CUTOFF
    ).set_index("variable_id")
    assert set(controls.index) == set(PAYMENT_CONTROL_IDS.values())
    for row in controls.itertuples():
        assert row.low_estimate < row.central_estimate < row.high_estimate
        assert row.central_estimate == pytest.approx(row.value)
        assert row.sample_n == 500
        assert row.coverage_rate == pytest.approx(0.95)
        assert row.retrieved_at == "2026-07-26T08:00:00Z"

    shares = carry_latest_hmda_rates(
        _benchmarks(), ["2026-05-31"], CUTOFF
    )
    suppressed_id = "hmda_share_downpayment_below_3pct_va_monthly_rt"
    suppressed = shares.loc[shares["variable_id"].eq(suppressed_id)].iloc[0]
    assert suppressed["unit"] == "share"
    assert pd.isna(suppressed["value"])
    assert suppressed["is_suppressed"]
    assert suppressed["sample_n"] == 40
    assert suppressed["coverage_rate"] == pytest.approx(0.80)
    assert suppressed["quality_flag"] == "minimum_sample_suppressed"


def test_redfin_proxy_is_separate_and_finalized_flow_sums_to_hmda() -> None:
    targets = list(pd.date_range("2025-01-31", "2025-12-31", freq="ME"))
    targets.append(pd.Timestamp("2026-01-31"))
    result = build_redfin_purchase_value_proxy_flows(
        _benchmarks(), _redfin(), targets, CUTOFF
    )
    all_proxy_id = (
        "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_rt"
    )
    all_rows = result.loc[result["variable_id"].eq(all_proxy_id)]
    finalized = all_rows.loc[all_rows["month"].dt.year.eq(2025)]
    nowcast = all_rows.loc[all_rows["month"].eq(pd.Timestamp("2026-01-31"))]

    assert math.fsum(finalized["value"].tolist()) == 1_200.0
    assert math.fsum(finalized["low_estimate"].tolist()) == 1_100.0
    assert math.fsum(finalized["high_estimate"].tolist()) == 1_300.0
    assert nowcast.iloc[0]["value"] == pytest.approx(200.0)
    assert nowcast.iloc[0]["is_nowcast"]
    assert result["is_scenario"].all()
    assert not result["variable_id"].eq(
        "hmda_aggregate_downpayment_flow_monthly_rt"
    ).any()


def test_stepwise_indicator_ignores_future_snapshot_and_future_observation() -> None:
    result = stepwise_indicator_values(
        _pmms(include_future_poison=True), ["2026-05-31"], CUTOFF
    )

    assert len(result) == 1
    assert result.loc[0, "indicator_value"] == 6.0
    assert result.loc[0, "checksum_sha256"] == "pmms-current-sha"


def test_pmms_sensitivity_uses_only_stepwise_eligible_rate() -> None:
    payment = carry_latest_payment(_benchmarks(), ["2026-05-31"], CUTOFF)
    result = build_pmms_debt_scenarios(
        payment, _pmms(include_future_poison=True), CUTOFF
    )
    twenty_years = result.loc[
        result["variable_id"].eq(PMMS_DEBT_IDS[20])
    ].iloc[0]
    monthly_rate = 0.06 / 12
    expected_factor = (1 - (1 + monthly_rate) ** -240) / monthly_rate

    assert twenty_years["value"] == pytest.approx(1_000 * expected_factor)
    assert twenty_years["is_scenario"]
    assert twenty_years["transformation_id"] == (
        "version_c_stepwise_pmms_rate_debt_sensitivity_v1"
    )
    ordered = result.set_index("variable_id")["value"]
    assert ordered[PMMS_DEBT_IDS[20]] < ordered[PMMS_DEBT_IDS[25]]
    assert ordered[PMMS_DEBT_IDS[25]] < ordered[PMMS_DEBT_IDS[30]]


def test_full_builder_keeps_canonical_targets_null_and_scenarios_separate() -> None:
    result = build_realtime_monthly(
        _benchmarks(),
        ["2026-05-31"],
        CUTOFF,
        redfin_rows=_redfin(),
        pmms_rows=_pmms(include_future_poison=True),
        hpi_rows=_hpi(),
    )

    canonical = result.loc[
        result["variable_id"].isin(
            {
                "hmda_aggregate_downpayment_flow_monthly_rt",
                CANONICAL_DEBT_ID,
                CANONICAL_EQUITY_MORTGAGED_ID,
                CANONICAL_EQUITY_ALL_ID,
            }
        )
    ]
    assert len(canonical) == 4
    assert canonical["value"].isna().all()
    assert not canonical["is_scenario"].any()
    assert not canonical["is_nowcast"].any()

    assert result.loc[
        result["variable_id"].eq(
            "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_rt"
        ),
        "value",
    ].notna().all()
    for variable_id in PMMS_DEBT_IDS.values():
        assert result.loc[result["variable_id"].eq(variable_id), "value"].notna().all()
    for variable_id in (
        *PMMS_EQUITY_MORTGAGED_IDS.values(),
        *PMMS_EQUITY_ALL_IDS.values(),
    ):
        assert result.loc[result["variable_id"].eq(variable_id), "value"].notna().all()

    assert result["variable_id"].str.endswith("_monthly_rt").all()
    assert result["database_version"].eq(
        "C. Real-time monthly vintage"
    ).all()
    assert result["is_modeled"].all()
    assert not result["is_interpolated"].any()
    assert result["lookahead_safe"].all()
    available = pd.to_datetime(
        result.loc[result["availability_date"].notna(), "availability_date"],
        utc=True,
    )
    assert available.le(pd.Timestamp(CUTOFF, tz="UTC")).all()
    assert result["vintage_id"].nunique() == 1

    twenty_year_debt = result.loc[
        result["variable_id"].eq(PMMS_DEBT_IDS[20]), "value"
    ].iloc[0]
    mort_equity = result.loc[
        result["variable_id"].eq(PMMS_EQUITY_MORTGAGED_IDS[20]),
        "value",
    ].iloc[0]
    all_equity = result.loc[
        result["variable_id"].eq(PMMS_EQUITY_ALL_IDS[20]), "value"
    ].iloc[0]
    assert mort_equity + twenty_year_debt == pytest.approx(1_200_000.0)
    assert all_equity + twenty_year_debt == pytest.approx(1_800_000.0)


def test_full_builder_has_exact_58_id_surface_and_dictionary_contract() -> None:
    result = build_realtime_monthly(
        _benchmarks(),
        ["2026-05-31"],
        CUTOFF,
        redfin_rows=_redfin(),
        pmms_rows=_pmms(),
        hpi_rows=_hpi(),
    )
    expected = {
        PAYMENT_MONTHLY_ID,
        *PAYMENT_CONTROL_IDS.values(),
        *(
            benchmark_id.removesuffix("_benchmark") + "_monthly_rt"
            for benchmark_id in (*HMDA_RATE_IDS, *HMDA_THRESHOLD_SHARE_IDS)
        ),
        *(
            benchmark_id.removesuffix("_benchmark") + "_monthly_rt"
            for benchmark_id in (
                "hmda_aggregate_downpayment_flow_benchmark",
                "hmda_aggregate_downpayment_flow_conventional_benchmark",
                "hmda_aggregate_downpayment_flow_fha_benchmark",
                "hmda_aggregate_downpayment_flow_va_benchmark",
            )
        ),
        *(
            benchmark_id.removesuffix("_benchmark")
            + "_redfin_sales_value_proxy_monthly_rt"
            for benchmark_id in (
                "hmda_aggregate_downpayment_flow_benchmark",
                "hmda_aggregate_downpayment_flow_conventional_benchmark",
                "hmda_aggregate_downpayment_flow_fha_benchmark",
                "hmda_aggregate_downpayment_flow_va_benchmark",
            )
        ),
        CANONICAL_DEBT_ID,
        CANONICAL_EQUITY_MORTGAGED_ID,
        CANONICAL_EQUITY_ALL_ID,
        *CALIBRATED_NORMALIZED_NULL_IDS,
        *PMMS_DEBT_IDS.values(),
        COMPOSITE_RAW_DEBT_ID,
        *PMMS_EQUITY_MORTGAGED_IDS.values(),
        *PMMS_EQUITY_ALL_IDS.values(),
    }
    assert len(expected) == 58
    assert len(result) == 58
    assert set(result["variable_id"]) == expected

    root = Path(__file__).resolve().parents[1]
    with (root / "config/version_c_variables.yaml").open(encoding="utf-8") as handle:
        dictionary = yaml.safe_load(handle)
    documented = dictionary["variables"]
    assert len(documented) == 58
    assert {row["variable_id"] for row in documented} == expected
    documented_by_id = {row["variable_id"]: row for row in documented}
    assert documented_by_id[COMPOSITE_RAW_DEBT_ID]["benchmark_variable_id"] == (
        "acs_implied_first_mortgage_balance_raw_benchmark"
    )
    hmda_rate_ids = {
        benchmark_id.removesuffix("_benchmark") + "_monthly_rt"
        for benchmark_id in HMDA_RATE_IDS
    }
    assert {
        row["unit"] for row in documented if row["variable_id"] in hmda_rate_ids
    } == {"rate"}
    hmda_share_ids = {
        benchmark_id.removesuffix("_benchmark") + "_monthly_rt"
        for benchmark_id in HMDA_THRESHOLD_SHARE_IDS
    }
    assert {
        row["unit"] for row in documented if row["variable_id"] in hmda_share_ids
    } == {"share"}

    with (root / "metadata/version_c_transformation_catalog.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        catalog = list(csv.DictReader(handle))
    assert {row["transformation_id"] for row in catalog} == set(
        result["transformation_id"]
    )


def test_composite_raw_debt_bounds_and_provenance_are_truthful() -> None:
    def build(month: str) -> pd.DataFrame:
        return build_realtime_monthly(
            _benchmarks(),
            [month],
            CUTOFF,
            redfin_rows=_redfin(),
            pmms_rows=_pmms(),
            hpi_rows=_hpi(),
        ).set_index("variable_id")

    reference = build("2022-07-31")
    reference_composite = reference.loc[COMPOSITE_RAW_DEBT_ID]
    assert reference_composite["value"] == 150_000.0
    assert reference_composite["central_estimate"] == 150_000.0
    assert reference_composite["low_estimate"] == 120_000.0
    assert reference_composite["high_estimate"] == 200_000.0

    by_id = build("2026-05-31")
    composite = by_id.loc[COMPOSITE_RAW_DEBT_ID]
    central_scale = 150_000.0 / reference.loc[PMMS_DEBT_IDS[25], "value"]
    low_scale = 120_000.0 / reference.loc[
        PMMS_DEBT_IDS[20], "low_estimate"
    ]
    high_scale = 200_000.0 / reference.loc[
        PMMS_DEBT_IDS[30], "high_estimate"
    ]
    assert composite["value"] == pytest.approx(
        by_id.loc[PMMS_DEBT_IDS[25], "value"] * central_scale
    )
    assert composite["central_estimate"] == pytest.approx(composite["value"])
    assert composite["low_estimate"] == pytest.approx(
        by_id.loc[PMMS_DEBT_IDS[20], "low_estimate"] * low_scale
    )
    assert composite["high_estimate"] == pytest.approx(
        by_id.loc[PMMS_DEBT_IDS[30], "high_estimate"] * high_scale
    )
    assert composite["raw_debt_central_scale_factor"] == pytest.approx(
        central_scale
    )
    assert composite["raw_debt_low_scale_factor"] == pytest.approx(low_scale)
    assert composite["raw_debt_high_scale_factor"] == pytest.approx(high_scale)
    assert composite["raw_debt_reference_month"] == pd.Timestamp("2022-07-31")
    assert composite["low_estimate"] < composite["value"] < composite["high_estimate"]
    assert composite["is_scenario"]
    assert "benchmark_scaled" in composite["quality_flag"]
    assert composite["transformation_id"] == (
        "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1"
    )
    assert pd.Timestamp(composite["retrieved_at"]) == pd.Timestamp(
        "2026-07-30T09:30:00Z"
    )
    assert pd.Timestamp(composite["retrieved_at"]) != pd.Timestamp(
        composite["availability_date"]
    )
    assert "5-year" in composite["source_frequency"]
    assert "weekly to monthly" in composite["source_frequency"]
    assert "county" in composite["source_geography"]
    assert "United States" in composite["source_geography"]
    assert "raw_debt_benchmark" in composite["source_id"]
    assert "raw_debt_benchmark=2026-07-28" in composite["source_release_dates"]
    assert "acs_pums=2026-07-26" in composite["source_release_dates"]
    assert "freddie_mac_pmms=unavailable" in composite["source_release_dates"]

    backward = build("2022-06-30")
    backward_composite = backward.loc[COMPOSITE_RAW_DEBT_ID]
    assert backward_composite["value"] == pytest.approx(
        backward.loc[PMMS_DEBT_IDS[25], "value"] * central_scale
    )
    assert backward_composite["low_estimate"] == pytest.approx(
        backward.loc[PMMS_DEBT_IDS[20], "low_estimate"] * low_scale
    )
    assert backward_composite["high_estimate"] == pytest.approx(
        backward.loc[PMMS_DEBT_IDS[30], "high_estimate"] * high_scale
    )

    for variable_id in (
        *PMMS_EQUITY_MORTGAGED_IDS.values(),
        *PMMS_EQUITY_ALL_IDS.values(),
    ):
        row = by_id.loc[variable_id]
        assert row["low_estimate"] < row["central_estimate"] < row["high_estimate"]

    canonical = by_id.loc[list(CALIBRATED_NORMALIZED_NULL_IDS)]
    assert canonical["value"].isna().all()
    assert not canonical["is_nowcast"].any()
    assert canonical.loc[
        "acs_first_mortgage_debt_per_capita_monthly_rt", "unit"
    ] == "USD/person"
    assert canonical.loc["acs_aggregate_ltv_mortgaged_monthly_rt", "unit"] == "ratio"


def test_composite_raw_debt_ignores_future_raw_anchor() -> None:
    baseline_benchmarks = _benchmarks()
    future = pd.DataFrame(
        [
            _anchor(
                "acs_implied_first_mortgage_balance_raw_benchmark",
                999_999_999.0,
                source_id="raw_debt_benchmark",
                release_date="2026-09-01",
                retrieved_at="2026-09-02T00:00:00Z",
                source_period_start="2021-01-01",
                source_period_end="2025-12-31",
                reference_date="2023-07-01",
                benchmark_vintage="2025_ACS5_PMMS",
                unit="USD",
                low_estimate=888_888_888.0,
                high_estimate=1_111_111_111.0,
            )
        ]
    )
    poisoned_benchmarks = pd.concat(
        [baseline_benchmarks, future], ignore_index=True
    )

    def composite(benchmarks: pd.DataFrame) -> pd.Series:
        result = build_realtime_monthly(
            benchmarks,
            ["2026-05-31"],
            CUTOFF,
            redfin_rows=_redfin(),
            pmms_rows=_pmms(),
            hpi_rows=_hpi(),
        )
        return result.loc[result["variable_id"].eq(COMPOSITE_RAW_DEBT_ID)].iloc[0]

    baseline = composite(baseline_benchmarks)
    poisoned = composite(poisoned_benchmarks)
    assert poisoned["value"] == baseline["value"]
    assert poisoned["low_estimate"] == baseline["low_estimate"]
    assert poisoned["high_estimate"] == baseline["high_estimate"]
    assert poisoned["vintage_id"] == baseline["vintage_id"]
    assert poisoned["raw_debt_reference_month"] == pd.Timestamp("2022-07-31")


def test_composite_raw_debt_never_falls_back_to_unbenchmarked_level() -> None:
    pmms_without_reference_history = _pmms().loc[
        lambda frame: pd.to_datetime(frame["observation_date"]).dt.year.eq(2026)
    ]
    result = build_realtime_monthly(
        _benchmarks(),
        ["2026-05-31"],
        CUTOFF,
        redfin_rows=_redfin(),
        pmms_rows=pmms_without_reference_history,
        hpi_rows=_hpi(),
    )
    assert result["variable_id"].nunique() == 58
    composite = result.loc[
        result["variable_id"].eq(COMPOSITE_RAW_DEBT_ID)
    ].iloc[0]
    assert pd.isna(composite["value"])
    assert pd.isna(composite["low_estimate"])
    assert pd.isna(composite["high_estimate"])
    assert composite["quality_flag"] == (
        "raw_debt_benchmark_or_reference_envelope_unavailable"
    )
    fixed = result.loc[result["variable_id"].isin(PMMS_DEBT_IDS.values())]
    assert fixed["value"].notna().all()


def test_deterministic_vintage_id_is_order_independent() -> None:
    frame = _benchmarks().sample(frac=1, random_state=7).reset_index(drop=True)
    reverse = frame.iloc[::-1].reset_index(drop=True)

    assert deterministic_vintage_id(CUTOFF, frame) == deterministic_vintage_id(
        CUTOFF, reverse
    )
