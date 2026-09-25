from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.benchmark.schema import BENCHMARK_COLUMNS
from src.common.io import sha256_file
from src.housing_finance_monthly.pipeline import (
    _anchor_lookup,
    _adapt_realtime_to_shared_schema,
    _benchmark_variable_id,
    _build_lineage,
    _inclusive_cutoff,
    _latest_c_partition,
    _manifest_asset,
    _require_completion_marker,
    _write_build_manifest,
    _write_output_pair,
    main,
)
from src.housing_finance_monthly.schema import (
    VERSION_B_DATABASE_NAME,
    VERSION_C_DATABASE_NAME,
    monthly_finance_contract_errors,
)


BENCHMARK_ID = "hmda_median_downpayment_rate_benchmark"
MONTHLY_ID = "hmda_median_downpayment_rate_monthly_rt"


def _benchmark_row() -> pd.DataFrame:
    row = dict.fromkeys(BENCHMARK_COLUMNS)
    row.update(
        {
            "county_fips": "06037",
            "state_fips": "06",
            "geo_id": "COUNTY_06037",
            "month": pd.Timestamp("2024-12-31"),
            "value": 0.18,
            "variable_name": BENCHMARK_ID,
            "variable_id": BENCHMARK_ID,
            "unit": "share",
            "estimate_variant": "central",
            "database_version": "A. Benchmark",
            "source_id": "ffiec_hmda",
            "source_frequency": "annual",
            "source_period_start": pd.Timestamp("2024-01-01"),
            "source_period_end": pd.Timestamp("2024-12-31"),
            "reference_date": pd.Timestamp("2024-12-31"),
            "release_date": pd.Timestamp("2026-01-15"),
            "source_release_dates": "ffiec_hmda=2026-01-15",
            "source_geography": "county",
            "parameter_geography": "county",
            "monthly_method": "not_applicable_native_annual",
            "construction_method": "published annual benchmark",
            "is_modeled": False,
            "is_nowcast": False,
            "is_imputed": False,
            "is_interpolated": False,
            "is_suppressed": False,
            "benchmark_vintage": "HMDA 2024",
            "sample_n": 100,
            "coverage_rate": 1.0,
            "central_estimate": 0.18,
            "universe_definition": "eligible first-lien home-purchase loans",
            "notes": "fixture",
            "source_vintage": "2024",
            "retrieved_at": pd.Timestamp("2026-01-16T12:00:00Z"),
            "source_filename": "hmda_2024.csv",
            "checksum_sha256": "a" * 64,
            "transformation_id": "version_a_hmda_rate_v1",
            "lineage_id": "b" * 64,
            "quality_flag": "published",
        }
    )
    return pd.DataFrame([row])


def _raw_row(month: str, *, lineage: str, vintage_id: str) -> dict[str, object]:
    return {
        "county_fips": "6037",
        "month": pd.Timestamp(month),
        "value": 0.18,
        "variable_name": MONTHLY_ID,
        "variable_id": MONTHLY_ID,
        "unit": "share",
        "estimate_variant": np.nan,
        "database_version": VERSION_C_DATABASE_NAME,
        "as_of_date": pd.Timestamp("2026-03-01T00:00:00Z"),
        "vintage_id": vintage_id,
        "availability_date": pd.Timestamp("2026-02-15T18:30:00Z"),
        "source_id": np.nan,
        "source_vintage": np.nan,
        "source_filename": pd.NA,
        "checksum_sha256": "",
        "source_period_start": pd.NaT,
        "source_period_end": pd.NaT,
        "reference_date": pd.NaT,
        "release_date": pd.NaT,
        "benchmark_vintage": pd.NA,
        "monthly_method": "latest_released_hmda_rate_repeated_not_divided",
        "construction_method": "carry eligible annual rate",
        "is_modeled": True,
        "is_nowcast": False,
        "is_imputed": np.nan,
        "is_interpolated": np.nan,
        "is_suppressed": np.nan,
        "quality_flag": "",
        "transformation_id": "version_c_latest_released_hmda_rate_v1",
        "lineage_id": lineage,
        "notes": "fixture",
    }


def _lineage_root(tmp_path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "relative_path": "data/final/housing_finance_benchmark.parquet",
                "sha256": "f" * 64,
            }
        ]
    ).to_csv(metadata / "version_a_freeze_manifest.csv", index=False)


def _benchmark_for(
    variable_id: str,
    *,
    reference_date: str = "2024-06-30",
    source_period_start: str = "2024-01-01",
    source_period_end: str = "2024-12-31",
) -> pd.DataFrame:
    result = _benchmark_row()
    result["variable_id"] = variable_id
    result["variable_name"] = variable_id
    result["reference_date"] = pd.Timestamp(reference_date)
    result["source_period_start"] = pd.Timestamp(source_period_start)
    result["source_period_end"] = pd.Timestamp(source_period_end)
    return result


def _lineage_observation(
    *,
    version: str,
    variable_id: str,
    benchmark_variable_id: str,
    transformation_id: str,
    month: str,
    lineage_id: str,
    cutoff: str | None = None,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "lineage_id": lineage_id,
                "database_version": (
                    VERSION_B_DATABASE_NAME if version == "B" else VERSION_C_DATABASE_NAME
                ),
                "information_cutoff_date": (
                    pd.Timestamp(cutoff) if cutoff is not None else pd.NaT
                ),
                "county_fips": "06037",
                "month": pd.Timestamp(month),
                "variable_id": variable_id,
                "benchmark_variable_id": benchmark_variable_id,
                "estimate_variant": "central",
                "transformation_id": transformation_id,
            }
        ]
    )


def _asset_record(source_id: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "data_vintage": "fixture_vintage",
        "source_filename": f"{source_id}.csv",
        "checksum_sha256": "1" * 64,
        "release_date": "2026-01-01",
        "retrieved_at": "2026-01-02T12:00:00Z",
    }


def _eligible_source_rows(frame: pd.DataFrame, source_id: str) -> pd.DataFrame:
    result = frame.copy()
    result["source_id"] = source_id
    result["source_vintage"] = "fixture_vintage"
    result["source_filename"] = f"{source_id}.csv"
    result["checksum_sha256"] = "1" * 64
    result["release_date"] = pd.Timestamp("2026-01-01")
    result["retrieved_at"] = pd.Timestamp("2026-01-02T12:00:00Z")
    return result


def test_vectorized_realtime_adapter_is_null_safe_and_contract_valid() -> None:
    rows = [
        _raw_row("2026-02-28", lineage="c" * 64, vintage_id="rt_second"),
        _raw_row("2026-01-31", lineage="d" * 64, vintage_id="rt_first"),
    ]
    # Nonblank raw provenance remains authoritative; the other row exercises
    # null/blank fallback from an all-null, float-inferred raw column.
    rows[0].update(
        {
            "source_id": "composite_hmda_source",
            "source_vintage": "raw_vintage",
            "source_filename": "raw_input.csv",
            "checksum_sha256": "e" * 64,
            "release_date": pd.Timestamp("2026-02-01"),
            "quality_flag": "raw_quality",
            "estimate_variant": "raw_variant",
        }
    )
    raw = pd.DataFrame(rows)

    output = _adapt_realtime_to_shared_schema(
        raw,
        _benchmark_row(),
        cutoff=pd.Timestamp("2026-03-01T00:00:00Z"),
        method_version="version_c_monthly_rt_v1",
    )

    assert output["vintage_id"].tolist() == ["rt_second", "rt_first"]
    assert output["as_of_date"].tolist() == raw["as_of_date"].tolist()
    assert output["county_fips"].tolist() == ["06037", "06037"]
    assert output["variable_name"].eq(MONTHLY_ID).all()
    assert output["benchmark_variable_id"].eq(BENCHMARK_ID).all()
    assert output["information_cutoff_date"].eq(pd.Timestamp("2026-03-01")).all()

    first, fallback = output.iloc[0], output.iloc[1]
    assert first["source_id"] == "composite_hmda_source"
    assert first["source_vintage"] == "raw_vintage"
    assert first["source_filename"] == "raw_input.csv"
    assert first["checksum_sha256"] == "e" * 64
    assert first["release_date"] == pd.Timestamp("2026-02-01")
    assert first["quality_flag"] == "raw_quality"
    assert first["estimate_variant"] == "raw_variant"

    assert fallback["source_id"] == "ffiec_hmda"
    assert fallback["source_vintage"] == "2024"
    assert fallback["source_filename"] == "hmda_2024.csv"
    assert fallback["checksum_sha256"] == "a" * 64
    assert fallback["source_frequency"] == "annual"
    assert fallback["source_period_start"] == pd.Timestamp("2024-01-01")
    assert fallback["source_period_end"] == pd.Timestamp("2024-12-31")
    assert fallback["release_date"] == pd.Timestamp("2026-02-15")
    assert fallback["retrieved_at"] == pd.Timestamp("2026-02-15T18:30:00Z")
    assert fallback["quality_flag"] == "modeled_realtime_value"
    assert fallback["estimate_variant"] == "central"
    assert fallback["central_estimate"] == pytest.approx(0.18)
    assert not bool(fallback["is_imputed"])
    assert not bool(fallback["is_interpolated"])
    assert not bool(fallback["is_suppressed"])
    assert monthly_finance_contract_errors(output, version="C") == []


def test_vectorized_realtime_adapter_reports_all_missing_anchor_keys() -> None:
    raw = pd.DataFrame(
        [_raw_row("2026-01-31", lineage="f" * 64, vintage_id="rt_missing")]
    )
    raw["county_fips"] = "06059"

    with pytest.raises(KeyError, match="06059"):
        _adapt_realtime_to_shared_schema(
            raw,
            _benchmark_row(),
            cutoff=pd.Timestamp("2026-03-01T00:00:00Z"),
            method_version="version_c_monthly_rt_v1",
        )


def test_cutoff_anchor_lookup_rejects_a_later_retrieved_benchmark() -> None:
    early = _benchmark_for(BENCHMARK_ID, reference_date="2024-12-31")
    early["release_date"] = pd.Timestamp("2026-01-15")
    early["retrieved_at"] = pd.Timestamp("2026-01-16T12:00:00Z")
    late = _benchmark_for(BENCHMARK_ID, reference_date="2025-12-31")
    late["release_date"] = pd.Timestamp("2026-02-15")
    late["retrieved_at"] = pd.Timestamp("2026-04-01T12:00:00Z")
    selected = _anchor_lookup(
        pd.concat([early, late], ignore_index=True),
        cutoff=_inclusive_cutoff("2026-03-01"),
    )

    assert selected.iloc[0]["reference_date"] == pd.Timestamp("2024-12-31")


def test_version_b_flow_lineage_lists_all_annual_weights_and_current_point(
    tmp_path,
) -> None:
    _lineage_root(tmp_path)
    benchmark_id = "hmda_aggregate_downpayment_flow_benchmark"
    benchmark = _benchmark_for(benchmark_id)
    observation = _lineage_observation(
        version="B",
        variable_id=(
            "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_hist"
        ),
        benchmark_variable_id=benchmark_id,
        transformation_id=(
            "version_b_redfin_purchase_value_proxy_distribution_v1"
        ),
        month="2024-06-30",
        lineage_id="2" * 64,
    )
    redfin = _eligible_source_rows(
        pd.DataFrame(
            {
                "county_fips": ["06037"] * 12,
                "month": pd.date_range("2024-01-31", "2024-12-31", freq="ME"),
                "purchase_value_proxy": range(1, 13),
            }
        ),
        "redfin_county_monthly",
    )
    zillow = _eligible_source_rows(
        pd.DataFrame(
            {"county_fips": ["06037"], "month": [pd.Timestamp("2024-06-30")]}
        ),
        "zillow_county_zhvi",
    )
    nmdb = _eligible_source_rows(
        pd.DataFrame({"month": [pd.Timestamp("2024-06-30")]}),
        "fhfa_nmdb_ca_upb",
    )
    lineage = _build_lineage(
        observation,
        benchmark,
        root=tmp_path,
        version="B",
        indicator_frames={"redfin": redfin, "zillow": zillow, "nmdb": nmdb},
        asset_records={
            "redfin": _asset_record("redfin_county_monthly"),
            "zillow": _asset_record("zillow_county_zhvi"),
            "nmdb": _asset_record("fhfa_nmdb_ca_upb"),
        },
    )

    weights = lineage.loc[
        lineage["input_role"].eq("redfin_annual_weight_denominator")
    ]
    assert len(weights) == 12
    assert weights["reference_date"].nunique() == 12
    assert "redfin_monthly_weight_numerator" in set(lineage["input_role"])
    assert "annual_hmda_flow_anchor" in set(lineage["input_role"])


def test_version_b_debt_lineage_uses_exact_nmdb_brackets_and_share_slices(
    tmp_path,
) -> None:
    _lineage_root(tmp_path)
    benchmark_id = "acs_implied_first_mortgage_balance_raw_benchmark"
    benchmark = _benchmark_for(benchmark_id, reference_date="2024-06-30")
    observation = _lineage_observation(
        version="B",
        variable_id="acs_implied_first_mortgage_balance_raw_monthly_hist",
        benchmark_variable_id=benchmark_id,
        transformation_id="version_b_single_anchor_nmdb_redfin_raw_debt_v1",
        month="2024-08-31",
        lineage_id="3" * 64,
    )
    redfin = _eligible_source_rows(
        pd.DataFrame(
            {
                "county_fips": ["06037", "06037"],
                "month": [pd.Timestamp("2024-06-30"), pd.Timestamp("2024-08-31")],
            }
        ),
        "redfin_county_monthly",
    )
    zillow = _eligible_source_rows(
        pd.DataFrame(
            {"county_fips": ["06037"], "month": [pd.Timestamp("2024-08-31")]}
        ),
        "zillow_county_zhvi",
    )
    nmdb = _eligible_source_rows(
        pd.DataFrame(
            {
                "month": pd.to_datetime(
                    ["2024-03-31", "2024-06-30", "2024-09-30"]
                )
            }
        ),
        "fhfa_nmdb_ca_upb",
    )
    lineage = _build_lineage(
        observation,
        benchmark,
        root=tmp_path,
        version="B",
        indicator_frames={"redfin": redfin, "zillow": zillow, "nmdb": nmdb},
        asset_records={
            "redfin": _asset_record("redfin_county_monthly"),
            "zillow": _asset_record("zillow_county_zhvi"),
            "nmdb": _asset_record("fhfa_nmdb_ca_upb"),
        },
    )
    role_dates = lineage.set_index("input_role")["reference_date"]

    assert role_dates["nmdb_current_lower"] == pd.Timestamp("2024-06-30")
    assert role_dates["nmdb_current_upper"] == pd.Timestamp("2024-09-30")
    assert role_dates["nmdb_anchor_observation"] == pd.Timestamp("2024-06-30")
    assert {
        "redfin_current_share_denominator_slice",
        "redfin_current_share_numerator",
        "redfin_anchor_share_denominator_slice",
        "redfin_anchor_share_numerator",
    }.issubset(set(lineage["input_role"]))


@pytest.mark.parametrize(
    "variable_id,scenario_anchor,transformation_id,actual_anchor_id,actual_role",
    [
        (
            "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity_monthly_rt",
            "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity_benchmark",
            "version_c_stepwise_pmms_rate_debt_sensitivity_v1",
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            "aggregate_payment_anchor",
        ),
        (
            "acs_implied_first_mortgage_balance_raw_composite_pmms_sensitivity_monthly_rt",
            "acs_implied_first_mortgage_balance_raw_benchmark",
            "version_c_pmms_composite_raw_debt_nowcast_v1",
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            "aggregate_payment_anchor",
        ),
        (
            "acs_implied_first_mortgage_balance_raw_monthly_rt",
            "acs_implied_first_mortgage_balance_raw_benchmark",
            "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
            "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            "aggregate_payment_anchor",
        ),
    ],
)
def test_version_c_pmms_lineage_uses_actual_anchor_and_last_eligible_rate_point(
    tmp_path,
    variable_id,
    scenario_anchor,
    transformation_id,
    actual_anchor_id,
    actual_role,
) -> None:
    _lineage_root(tmp_path)
    benchmark = pd.concat(
        [_benchmark_for(scenario_anchor), _benchmark_for(actual_anchor_id)],
        ignore_index=True,
    ).drop_duplicates(["county_fips", "variable_id"])
    observation = _lineage_observation(
        version="C",
        variable_id=variable_id,
        benchmark_variable_id=scenario_anchor,
        transformation_id=transformation_id,
        month="2026-02-28",
        cutoff="2026-03-01",
        lineage_id="4" * 64,
    )
    redfin = _eligible_source_rows(
        pd.DataFrame(
            {"county_fips": ["06037"], "month": [pd.Timestamp("2024-06-30")]}
        ),
        "redfin_county_monthly",
    )
    zillow = _eligible_source_rows(
        pd.DataFrame(
            {"county_fips": ["06037"], "month": [pd.Timestamp("2024-06-30")]}
        ),
        "zillow_county_zhvi",
    )
    pmms = _eligible_source_rows(
        pd.DataFrame(
            {
                "observation_date": pd.to_datetime(
                    ["2024-06-20", "2026-01-15", "2026-02-20"]
                ),
                "variable_id": [
                    "mortgage_rate_30y_monthly_median",
                    "mortgage_rate_30y_monthly_median",
                    "mortgage_rate_30y_monthly_median",
                ],
                "value": [6.0, 6.1, 6.2],
            }
        ),
        "freddie_mac_pmms",
    )
    lineage = _build_lineage(
        observation,
        benchmark,
        root=tmp_path,
        version="C",
        indicator_frames={"redfin": redfin, "zillow": zillow, "pmms": pmms},
        asset_records={
            "redfin": _asset_record("redfin_county_monthly"),
            "zillow": _asset_record("zillow_county_zhvi"),
            "pmms": _asset_record("freddie_mac_pmms"),
        },
    )
    roles = set(lineage["input_role"])
    pmms_row = lineage.loc[
        lineage["input_role"].eq("pmms_latest_eligible_rate_point")
    ].iloc[0]

    composite = transformation_id in {
        "version_c_pmms_composite_raw_debt_nowcast_v1",
        "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
    }
    target_role = "raw_debt_level_anchor" if composite else "output_definition_anchor"
    assert {target_role, actual_role}.issubset(roles)
    assert pmms_row["reference_date"] == pd.Timestamp("2026-02-20")
    assert pmms_row["source_geography"] == "United States"
    assert pmms_row["parameter_geography"].startswith("United States")
    if composite:
        reference_row = lineage.loc[
            lineage["input_role"].eq("pmms_reference_envelope_rate_point")
        ].iloc[0]
        assert reference_row["reference_date"] == pd.Timestamp("2024-06-20")
    assert _benchmark_variable_id(variable_id, "C") == scenario_anchor


def test_version_c_equity_lineage_uses_value_payment_and_exact_hpi_points(
    tmp_path,
) -> None:
    _lineage_root(tmp_path)
    equity_anchor = (
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_"
        "20y_scenario_benchmark"
    )
    payment_anchor = (
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
    )
    value_anchor = "acs_aggregate_owner_value_mortgaged_benchmark"
    benchmark = pd.concat(
        [
            _benchmark_for(equity_anchor),
            _benchmark_for(payment_anchor),
            _benchmark_for(value_anchor, reference_date="2024-06-30"),
        ],
        ignore_index=True,
    )
    observation = _lineage_observation(
        version="C",
        variable_id=(
            "acs_implied_owner_occupied_home_equity_mortgaged_pmms_"
            "20y_scenario_monthly_rt"
        ),
        benchmark_variable_id=equity_anchor,
        transformation_id="version_c_hpi_updated_pmms_equity_v1",
        month="2026-02-28",
        cutoff="2026-03-01",
        lineage_id="5" * 64,
    )
    redfin = _eligible_source_rows(
        pd.DataFrame(
            {"county_fips": ["06037"], "month": [pd.Timestamp("2024-06-30")]}
        ),
        "redfin_county_monthly",
    )
    zillow = _eligible_source_rows(
        pd.DataFrame(
            {
                "county_fips": ["06037", "06037"],
                "month": pd.to_datetime(["2024-05-31", "2026-02-28"]),
                "zhvi": [700_000.0, 800_000.0],
            }
        ),
        "zillow_county_zhvi",
    )
    pmms = _eligible_source_rows(
        pd.DataFrame(
            {
                "observation_date": [pd.Timestamp("2026-02-20")],
                "variable_id": ["mortgage_rate_30y_monthly_median"],
                "value": [6.2],
            }
        ),
        "freddie_mac_pmms",
    )
    lineage = _build_lineage(
        observation,
        benchmark,
        root=tmp_path,
        version="C",
        indicator_frames={"redfin": redfin, "zillow": zillow, "pmms": pmms},
        asset_records={
            "redfin": _asset_record("redfin_county_monthly"),
            "zillow": _asset_record("zillow_county_zhvi"),
            "pmms": _asset_record("freddie_mac_pmms"),
        },
    )
    role_dates = lineage.set_index("input_role")["reference_date"]

    assert {
        "output_definition_anchor",
        "aggregate_payment_anchor",
        "owner_value_anchor",
        "pmms_latest_eligible_rate_point",
        "zillow_hpi_anchor",
        "zillow_hpi_target",
    }.issubset(set(lineage["input_role"]))
    assert role_dates["zillow_hpi_anchor"] == pd.Timestamp("2024-05-31")
    assert role_dates["zillow_hpi_target"] == pd.Timestamp("2026-02-28")


def test_cutoff_is_strict_date_only_and_includes_the_whole_utc_day(
    tmp_path,
) -> None:
    cutoff = _inclusive_cutoff("2026-08-17")
    assert cutoff == pd.Timestamp("2026-08-17T23:59:59.999999999Z")
    assert pd.Timestamp("2026-08-17T23:45:00Z") <= cutoff
    assert pd.Timestamp("2026-08-18T00:00:00Z") > cutoff
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _inclusive_cutoff("2026-08-17T12:00:00Z")
    with pytest.raises(ValueError, match="time of day"):
        _inclusive_cutoff(pd.Timestamp("2026-08-17T12:00:00Z"))

    metadata = tmp_path / "metadata"
    raw_dir = tmp_path / "data" / "raw"
    metadata.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    same_day = raw_dir / "same.csv"
    next_day = raw_dir / "next.csv"
    same_day.write_text("same", encoding="utf-8")
    next_day.write_text("next", encoding="utf-8")
    pd.DataFrame(
        [
            {
                "source_id": "fixture",
                "retrieved_at": "2026-08-17T23:45:00+00:00",
                "checksum_sha256": sha256_file(same_day),
                "source_filename": same_day.name,
                "relative_path": "data/raw/same.csv",
            },
            {
                "source_id": "fixture",
                "retrieved_at": "2026-08-18T00:00:00+00:00",
                "checksum_sha256": sha256_file(next_day),
                "source_filename": next_day.name,
                "relative_path": "data/raw/next.csv",
            },
        ]
    ).to_csv(metadata / "raw_file_manifest.csv", index=False)
    selected, _ = _manifest_asset(tmp_path, "fixture", cutoff=cutoff)
    assert selected == same_day


def test_version_c_output_pair_is_append_only_and_exactly_idempotent(
    tmp_path,
) -> None:
    observations = pd.DataFrame({"id": [1, 2], "value": [10.0, 20.0]})
    lineage = pd.DataFrame({"id": [1, 2], "source": ["a", "b"]})
    cutoff = _inclusive_cutoff("2026-08-17")
    output = (
        tmp_path
        / "data/final/housing_finance_monthly_rt"
        / "information_cutoff_date=2026-08-17/part.parquet"
    )
    lineage_path = (
        tmp_path
        / "data/final/housing_finance_monthly_rt_lineage"
        / "information_cutoff_date=2026-08-17/part.parquet"
    )

    marker = _write_output_pair(
        tmp_path,
        version="C",
        observations=observations,
        lineage=lineage,
        output_path=output,
        lineage_path=lineage_path,
        cutoff=cutoff,
    )
    marker_bytes = marker.read_bytes()
    output_bytes = output.read_bytes()
    lineage_bytes = lineage_path.read_bytes()
    assert _require_completion_marker(
        tmp_path,
        version="C",
        output_path=output,
        lineage_path=lineage_path,
        cutoff=cutoff,
    ) == marker

    rerun = _write_output_pair(
        tmp_path,
        version="C",
        observations=observations.copy(),
        lineage=lineage.copy(),
        output_path=output,
        lineage_path=lineage_path,
        cutoff=cutoff,
    )
    assert rerun == marker
    assert marker.read_bytes() == marker_bytes
    assert output.read_bytes() == output_bytes
    assert lineage_path.read_bytes() == lineage_bytes

    changed = lineage.assign(source=["a", "changed"])
    with pytest.raises(FileExistsError, match="already differs"):
        _write_output_pair(
            tmp_path,
            version="C",
            observations=observations,
            lineage=changed,
            output_path=output,
            lineage_path=lineage_path,
            cutoff=cutoff,
        )
    assert marker.read_bytes() == marker_bytes
    assert output.read_bytes() == output_bytes
    assert lineage_path.read_bytes() == lineage_bytes


def test_unmarked_partial_c_partition_is_not_exposed_as_latest(tmp_path) -> None:
    observations = pd.DataFrame({"id": [1]})
    lineage = pd.DataFrame({"id": [1], "source": ["a"]})
    config = {
        "output_root": "data/final/housing_finance_monthly_rt",
        "lineage_output_root": "data/final/housing_finance_monthly_rt_lineage",
    }
    completed_output = (
        tmp_path
        / config["output_root"]
        / "information_cutoff_date=2026-08-16/part.parquet"
    )
    completed_lineage = (
        tmp_path
        / config["lineage_output_root"]
        / "information_cutoff_date=2026-08-16/part.parquet"
    )
    _write_output_pair(
        tmp_path,
        version="C",
        observations=observations,
        lineage=lineage,
        output_path=completed_output,
        lineage_path=completed_lineage,
        cutoff=_inclusive_cutoff("2026-08-16"),
    )
    partial = (
        tmp_path
        / config["output_root"]
        / "information_cutoff_date=2026-08-17/part.parquet"
    )
    partial.parent.mkdir(parents=True)
    observations.to_parquet(partial, index=False)

    assert _latest_c_partition(tmp_path, config) == (
        completed_output,
        completed_lineage,
    )


def test_c_pair_staging_failure_leaves_no_visible_or_final_half_pair(
    tmp_path, monkeypatch
) -> None:
    import src.housing_finance_monthly.pipeline as pipeline

    observations = pd.DataFrame({"id": [1]})
    lineage = pd.DataFrame({"id": [1], "source": ["a"]})
    output = (
        tmp_path
        / "data/final/housing_finance_monthly_rt"
        / "information_cutoff_date=2026-08-17/part.parquet"
    )
    lineage_path = (
        tmp_path
        / "data/final/housing_finance_monthly_rt_lineage"
        / "information_cutoff_date=2026-08-17/part.parquet"
    )
    original_stage = pipeline._stage_parquet
    calls = 0

    def fail_second(frame, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated lineage staging failure")
        return original_stage(frame, path)

    monkeypatch.setattr(pipeline, "_stage_parquet", fail_second)
    with pytest.raises(OSError, match="lineage staging"):
        _write_output_pair(
            tmp_path,
            version="C",
            observations=observations,
            lineage=lineage,
            output_path=output,
            lineage_path=lineage_path,
            cutoff=_inclusive_cutoff("2026-08-17"),
        )
    assert not output.exists()
    assert not lineage_path.exists()
    assert not (output.parent / "_COMPLETE.json").exists()


def test_version_b_pair_stages_before_replacement_and_exact_rerun_is_stable(
    tmp_path, monkeypatch
) -> None:
    import src.housing_finance_monthly.pipeline as pipeline

    observations = pd.DataFrame({"id": [1], "value": [10.0]})
    lineage = pd.DataFrame({"id": [1], "source": ["a"]})
    output = tmp_path / "data/final/housing_finance_monthly_hist.parquet"
    lineage_path = tmp_path / "data/final/housing_finance_monthly_hist_lineage.parquet"
    marker = _write_output_pair(
        tmp_path,
        version="B",
        observations=observations,
        lineage=lineage,
        output_path=output,
        lineage_path=lineage_path,
        cutoff=None,
    )
    original = (output.read_bytes(), lineage_path.read_bytes(), marker.read_bytes())
    _write_output_pair(
        tmp_path,
        version="B",
        observations=observations.copy(),
        lineage=lineage.copy(),
        output_path=output,
        lineage_path=lineage_path,
        cutoff=None,
    )
    assert (output.read_bytes(), lineage_path.read_bytes(), marker.read_bytes()) == original

    def fail_marker(*_args, **_kwargs):
        raise OSError("simulated marker staging failure")

    monkeypatch.setattr(pipeline, "_stage_json", fail_marker)
    with pytest.raises(OSError, match="simulated"):
        _write_output_pair(
            tmp_path,
            version="B",
            observations=observations.assign(value=[99.0]),
            lineage=lineage,
            output_path=output,
            lineage_path=lineage_path,
            cutoff=None,
        )
    assert (output.read_bytes(), lineage_path.read_bytes(), marker.read_bytes()) == original


def test_c_build_manifest_is_immutable_atomic_and_tracks_clean_pmms(tmp_path) -> None:
    files = {
        "benchmark": tmp_path / "data/final/housing_finance_benchmark.parquet",
        "config": tmp_path / "config/version_c_monthly_rt.yaml",
        "dictionary": tmp_path / "config/version_c_variables.yaml",
        "catalog": tmp_path / "metadata/version_c_transformation_catalog.csv",
        "output": tmp_path
        / "data/final/housing_finance_monthly_rt/information_cutoff_date=2026-08-17/part.parquet",
        "lineage": tmp_path
        / "data/final/housing_finance_monthly_rt_lineage/information_cutoff_date=2026-08-17/part.parquet",
        "marker": tmp_path
        / "data/final/housing_finance_monthly_rt/information_cutoff_date=2026-08-17/_COMPLETE.json",
        "raw": tmp_path / "data/raw/pmms.csv",
        "clean": tmp_path / "data/clean/freddie_mac_pmms.parquet",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{path.name}".encode())
    config = {
        "benchmark_input_path": "data/final/housing_finance_benchmark.parquet",
        "variable_dictionary_path": "config/version_c_variables.yaml",
        "transformation_catalog_path": "metadata/version_c_transformation_catalog.csv",
        "build_manifest_path": "metadata/version_c_build_manifest.csv",
    }
    record = {
        "relative_path": "data/raw/pmms.csv",
        "_clean_path": str(files["clean"]),
        "_clean_checksum_sha256": sha256_file(files["clean"]),
    }
    cutoff = _inclusive_cutoff("2026-08-17")
    _write_build_manifest(
        tmp_path,
        version="C",
        config=config,
        asset_records={"pmms": record},
        output_path=files["output"],
        lineage_path=files["lineage"],
        completion_path=files["marker"],
        cutoff=cutoff,
    )
    manifest_path = tmp_path / config["build_manifest_path"]
    first_bytes = manifest_path.read_bytes()
    manifest = pd.read_csv(manifest_path)
    assert "pmms_clean_input" in set(manifest["input_role"])
    clean_row = manifest.loc[manifest["input_role"].eq("pmms_clean_input")].iloc[0]
    assert clean_row["sha256"] == sha256_file(files["clean"])

    _write_build_manifest(
        tmp_path,
        version="C",
        config=config,
        asset_records={"pmms": record},
        output_path=files["output"],
        lineage_path=files["lineage"],
        completion_path=files["marker"],
        cutoff=cutoff,
    )
    assert manifest_path.read_bytes() == first_bytes

    files["clean"].write_bytes(b"changed clean input")
    changed_record = {
        **record,
        "_clean_checksum_sha256": sha256_file(files["clean"]),
    }
    with pytest.raises(FileExistsError, match="already differ"):
        _write_build_manifest(
            tmp_path,
            version="C",
            config=config,
            asset_records={"pmms": changed_record},
            output_path=files["output"],
            lineage_path=files["lineage"],
            completion_path=files["marker"],
            cutoff=cutoff,
        )
    assert manifest_path.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "version,argv",
    [
        (
            "C",
            [
                "--stage",
                "preflight",
                "--information-cutoff",
                "2026-08-17",
                "--start-month",
                "2025-01",
            ],
        ),
        ("B", ["--stage", "validate", "--strict-primary-concepts"]),
        (
            "C",
            [
                "--stage",
                "validate",
                "--information-cutoff",
                "2026-08-17T12:00:00Z",
            ],
        ),
        ("C", ["--stage", "preflight"]),
    ],
)
def test_cli_rejects_irrelevant_flags_and_non_date_cutoffs(version, argv) -> None:
    with pytest.raises(SystemExit) as exc:
        main(version, argv)
    assert exc.value.code == 2
