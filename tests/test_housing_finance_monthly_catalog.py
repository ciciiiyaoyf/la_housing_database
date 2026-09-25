from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import yaml

from src.housing_finance_monthly import realtime as rt
from src.housing_finance_monthly import retrospective as hist


ROOT = Path(__file__).resolve().parents[1]
VERSION_A_DICTIONARY = ROOT / "config" / "version_a_variables.yaml"
VERSION_B_DICTIONARY = ROOT / "config" / "version_b_variables.yaml"
VERSION_B_CATALOG = (
    ROOT / "metadata" / "version_b_transformation_catalog.csv"
)
VERSION_C_DICTIONARY = ROOT / "config" / "version_c_variables.yaml"
VERSION_C_CATALOG = (
    ROOT / "metadata" / "version_c_transformation_catalog.csv"
)


def _monthly_id(benchmark_id: str) -> str:
    assert benchmark_id.endswith("_benchmark")
    return benchmark_id.removesuffix("_benchmark") + "_monthly_rt"


def _proxy_id(benchmark_id: str) -> str:
    assert benchmark_id.endswith("_benchmark")
    return (
        benchmark_id.removesuffix("_benchmark")
        + "_redfin_sales_value_proxy_monthly_rt"
    )


def _expected_variable_contract() -> dict[str, dict[str, str]]:
    expected: dict[str, dict[str, str]] = {}

    def add(
        variable_id: str,
        benchmark_id: str,
        unit: str,
        output_status: str,
        transformation_id: str,
    ) -> None:
        assert variable_id not in expected
        expected[variable_id] = {
            "benchmark_variable_id": benchmark_id,
            "unit": unit,
            "output_status": output_status,
            "transformation_id": transformation_id,
        }

    add(
        rt.PAYMENT_MONTHLY_ID,
        rt.PAYMENT_BENCHMARK_ID,
        "USD/month",
        "canonical_modeled_carry",
        rt.TRANSFORM_PAYMENT,
    )
    payment_control_units = {
        "acs_first_mortgage_pi_per_mortgaged_unit_benchmark": "USD/unit/month",
        "acs_annual_first_mortgage_debt_service_benchmark": "USD/year",
        "acs_mortgage_debt_service_to_income_benchmark": "ratio",
    }
    for benchmark_id, variable_id in rt.PAYMENT_CONTROL_IDS.items():
        add(
            variable_id,
            benchmark_id,
            payment_control_units[benchmark_id],
            "canonical_modeled_carry",
            rt.TRANSFORM_PAYMENT,
        )
    for benchmark_id in rt.HMDA_RATE_IDS:
        add(
            _monthly_id(benchmark_id),
            benchmark_id,
            "rate",
            "canonical_modeled_carry",
            rt.TRANSFORM_HMDA_RATE,
        )
    for benchmark_id in rt.HMDA_THRESHOLD_SHARE_IDS:
        add(
            _monthly_id(benchmark_id),
            benchmark_id,
            "share",
            "canonical_modeled_carry",
            rt.TRANSFORM_HMDA_RATE,
        )
    for benchmark_id in rt.HMDA_FLOW_IDS:
        add(
            _monthly_id(benchmark_id),
            benchmark_id,
            "USD",
            "canonical_null",
            rt.TRANSFORM_HMDA_FLOW_NULL,
        )
        add(
            _proxy_id(benchmark_id),
            benchmark_id,
            "USD",
            "proxy",
            rt.TRANSFORM_HMDA_PROXY,
        )

    for variable_id, benchmark_id in {
        rt.CANONICAL_DEBT_ID: (
            "acs_implied_first_mortgage_balance_cal_benchmark"
        ),
        rt.CANONICAL_EQUITY_MORTGAGED_ID: (
            "acs_implied_owner_occupied_home_equity_mortgaged_benchmark"
        ),
        rt.CANONICAL_EQUITY_ALL_ID: (
            "acs_implied_owner_occupied_home_equity_all_benchmark"
        ),
    }.items():
        add(
            variable_id,
            benchmark_id,
            "USD",
            "canonical_null",
            rt.TRANSFORM_CALIBRATED_NULL,
        )
    for variable_id, (benchmark_id, unit) in (
        rt.CALIBRATED_NORMALIZED_NULL_IDS.items()
    ):
        add(
            variable_id,
            benchmark_id,
            unit,
            "canonical_null",
            rt.TRANSFORM_CALIBRATED_NULL,
        )

    add(
        rt.COMPOSITE_RAW_DEBT_ID,
        "acs_implied_first_mortgage_balance_raw_benchmark",
        "USD",
        "scenario",
        rt.TRANSFORM_COMPOSITE_RAW_DEBT,
    )

    for variable_id in rt.PMMS_DEBT_IDS.values():
        add(
            variable_id,
            variable_id.removesuffix("_monthly_rt") + "_benchmark",
            "USD",
            "scenario",
            rt.TRANSFORM_PMMS_DEBT,
        )
    for variable_id in (
        list(rt.PMMS_EQUITY_MORTGAGED_IDS.values())
        + list(rt.PMMS_EQUITY_ALL_IDS.values())
    ):
        add(
            variable_id,
            variable_id.removesuffix("_monthly_rt") + "_benchmark",
            "USD",
            "scenario",
            rt.TRANSFORM_PMMS_EQUITY,
        )
    return expected


def _expected_version_b_contract() -> dict[str, dict[str, str]]:
    """Exact closed output surface of build_retrospective_monthly."""

    expected: dict[str, dict[str, str]] = {}

    def add(
        variable_id: str,
        benchmark_id: str,
        unit: str,
        output_status: str,
        transformation_id: str,
    ) -> None:
        assert variable_id not in expected
        expected[variable_id] = {
            "benchmark_variable_id": benchmark_id,
            "unit": unit,
            "output_status": output_status,
            "transformation_id": transformation_id,
        }

    payment_units = {
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark": "USD/month",
        "acs_first_mortgage_pi_per_mortgaged_unit_benchmark": "USD/unit/month",
        "acs_annual_first_mortgage_debt_service_benchmark": "USD/year",
        "acs_mortgage_debt_service_to_income_benchmark": "ratio",
    }
    for benchmark_id in hist.PAYMENT_BENCHMARK_IDS:
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            payment_units[benchmark_id],
            "canonical_modeled_repeat",
            hist.TRANSFORM_PAYMENT,
        )
    for benchmark_id in hist.HMDA_RATE_BENCHMARK_IDS:
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            "rate",
            "canonical_modeled_repeat",
            hist.TRANSFORM_HMDA_RATE,
        )
    for benchmark_id in hist.HMDA_SHARE_BENCHMARK_IDS:
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            "share",
            "canonical_modeled_repeat",
            hist.TRANSFORM_HMDA_RATE,
        )
    for benchmark_id in hist.HMDA_FLOW_BENCHMARK_IDS:
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            "USD",
            "canonical_null",
            hist.TRANSFORM_HMDA_FLOW_NULL,
        )
        add(
            hist.redfin_proxy_id(benchmark_id),
            benchmark_id,
            "USD",
            "proxy",
            hist.TRANSFORM_HMDA_PROXY,
        )
    canonical_null_units = {
        "acs_implied_first_mortgage_balance_cal_benchmark": "USD",
        "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark": "USD/unit",
        "acs_first_mortgage_debt_per_owner_unit_benchmark": "USD/unit",
        "acs_first_mortgage_debt_per_capita_benchmark": "USD/person",
        "acs_first_mortgage_debt_to_owner_income_benchmark": "ratio",
        "acs_implied_owner_occupied_home_equity_mortgaged_benchmark": "USD",
        "acs_implied_owner_occupied_home_equity_all_benchmark": "USD",
        "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark": "USD/unit",
        "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark": "constant_2024_USD",
        "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark": "constant_2024_USD",
        "acs_aggregate_ltv_mortgaged_benchmark": "ratio",
        "acs_equity_rate_mortgaged_benchmark": "ratio",
    }
    for benchmark_id in hist.CANONICAL_NULL_BENCHMARK_IDS:
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            canonical_null_units[benchmark_id],
            "canonical_null",
            hist.TRANSFORM_CALIBRATED_NULL,
        )
    add(
        hist.monthly_hist_id(hist.RAW_DEBT_BENCHMARK_ID),
        hist.RAW_DEBT_BENCHMARK_ID,
        "USD",
        "scenario",
        hist.TRANSFORM_RAW_DEBT,
    )
    for benchmark_id in hist.PMMS_DEBT_BENCHMARK_IDS.values():
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            "USD",
            "scenario",
            hist.TRANSFORM_PMMS_DEBT,
        )
    for benchmark_id in (
        list(hist.PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS.values())
        + list(hist.PMMS_EQUITY_ALL_BENCHMARK_IDS.values())
    ):
        add(
            hist.monthly_hist_id(benchmark_id),
            benchmark_id,
            "USD",
            "scenario",
            hist.TRANSFORM_PMMS_EQUITY,
        )
    return expected


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


def test_version_b_dictionary_is_the_closed_full_builder_surface() -> None:
    """Version B is asserted separately from the Version C surface below."""

    dictionary = _load_yaml(VERSION_B_DICTIONARY)
    variables = dictionary["variables"]
    expected = _expected_version_b_contract()

    assert dictionary["database_version"] == hist.VERSION_B_DATABASE_NAME
    assert dictionary["config_version"] == hist.METHOD_VERSION
    assert dictionary["output_id_suffix"] == "_monthly_hist"
    assert len(variables) == len(expected) == 58

    by_id = {row["variable_id"]: row for row in variables}
    assert len(by_id) == len(variables), "Version B IDs must be unique"
    assert set(by_id) == set(expected), (
        "Version B dictionary must document exactly every ID that the full "
        "build_retrospective_monthly builder can emit"
    )
    assert all(variable_id.endswith("_monthly_hist") for variable_id in by_id)
    for variable_id, contract in expected.items():
        for field, value in contract.items():
            assert by_id[variable_id][field] == value, (
                f"{variable_id}: wrong {field}"
            )
        assert by_id[variable_id]["construction_anchor_variable_ids"]
        assert by_id[variable_id]["expected_value_policy"]

    assert Counter(row["output_status"] for row in variables) == {
        "canonical_modeled_repeat": 28,
        "canonical_null": 16,
        "proxy": 4,
        "scenario": 10,
    }
    assert set(dictionary["contract"]["output_statuses"]) == {
        "canonical_modeled_repeat",
        "canonical_null",
        "proxy",
        "scenario",
    }


def test_version_b_benchmark_mappings_and_catalog_are_exact() -> None:
    version_a_ids = {
        row["variable_id"]
        for row in _load_yaml(VERSION_A_DICTIONARY)["variables"]
    }
    version_b = _load_yaml(VERSION_B_DICTIONARY)["variables"]
    missing = {
        row["benchmark_variable_id"]
        for row in version_b
        if row["benchmark_variable_id"] not in version_a_ids
    }
    assert not missing, f"Version B has unmapped Version A benchmarks: {missing}"

    with VERSION_B_CATALOG.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {row["transformation_id"]: row for row in rows}
    assert len(by_id) == len(rows), "Version B transformation IDs must be unique"
    runtime_ids = {
        hist.TRANSFORM_PAYMENT,
        hist.TRANSFORM_HMDA_RATE,
        hist.TRANSFORM_HMDA_FLOW_NULL,
        hist.TRANSFORM_HMDA_PROXY,
        hist.TRANSFORM_CALIBRATED_NULL,
        hist.TRANSFORM_RAW_DEBT,
        hist.TRANSFORM_PMMS_DEBT,
        hist.TRANSFORM_PMMS_EQUITY,
    }
    dictionary_ids = {row["transformation_id"] for row in version_b}
    assert set(by_id) == runtime_ids == dictionary_ids
    assert all(row["version"] == "B" for row in rows)
    assert all(row["status"] == "active" for row in rows)

    debt_text = " ".join(by_id[hist.TRANSFORM_PMMS_DEBT].values()).lower()
    assert "growth indicator only" in debt_text
    assert "calibration" not in by_id[hist.TRANSFORM_PMMS_DEBT]["formula"].lower()
    assert "explicit null" in by_id[
        hist.TRANSFORM_HMDA_FLOW_NULL
    ]["formula"].lower()


def test_version_c_dictionary_is_the_closed_full_builder_surface() -> None:
    dictionary = _load_yaml(VERSION_C_DICTIONARY)
    variables = dictionary["variables"]
    expected = _expected_variable_contract()

    assert dictionary["database_version"] == rt.DATABASE_VERSION
    assert dictionary["config_version"] == rt.CONFIG_VERSION
    assert dictionary["output_id_suffix"] == "_monthly_rt"
    assert len(variables) == len(expected) == 58

    by_id = {row["variable_id"]: row for row in variables}
    assert len(by_id) == len(variables), "Version C IDs must be unique"
    assert set(by_id) == set(expected), (
        "Version C dictionary must document exactly every ID that the full "
        "build_realtime_monthly builder can emit"
    )
    assert all(variable_id.endswith("_monthly_rt") for variable_id in by_id)

    for variable_id, contract in expected.items():
        row = by_id[variable_id]
        for field, value in contract.items():
            assert row[field] == value, f"{variable_id}: wrong {field}"
        assert row["construction_anchor_variable_ids"]
        assert row["expected_value_policy"]

    assert Counter(row["output_status"] for row in variables) == {
        "canonical_modeled_carry": 28,
        "canonical_null": 16,
        "proxy": 4,
        "scenario": 10,
    }
    assert set(dictionary["contract"]["output_statuses"]) == {
        "canonical_modeled_carry",
        "canonical_null",
        "proxy",
        "scenario",
    }

    for row in variables:
        status = row["output_status"]
        if status in {"canonical_modeled_carry", "canonical_null"}:
            assert row["canonical"] is True
            assert row["proxy"] is False
            assert row["scenario"] is False
        elif status == "proxy":
            assert row["canonical"] is False
            assert row["proxy"] is True
            # Runtime proxy rows are explicitly separated as scenarios.
            assert row["scenario"] is True
        else:
            assert status == "scenario"
            assert row["canonical"] is False
            assert row["proxy"] is False
            assert row["scenario"] is True

        if status == "canonical_null":
            assert row["expected_value_policy"].startswith("explicit_null")


def test_version_c_benchmark_mappings_resolve_to_version_a() -> None:
    version_a_ids = {
        row["variable_id"]
        for row in _load_yaml(VERSION_A_DICTIONARY)["variables"]
    }
    version_c = _load_yaml(VERSION_C_DICTIONARY)["variables"]

    missing = {
        row["benchmark_variable_id"]
        for row in version_c
        if row["benchmark_variable_id"] not in version_a_ids
    }
    assert not missing, f"Version C has unmapped Version A benchmarks: {missing}"


def test_version_c_transformation_catalog_matches_runtime_and_dictionary() -> None:
    with VERSION_C_CATALOG.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {row["transformation_id"]: row for row in rows}
    assert len(by_id) == len(rows), "Version C transformation IDs must be unique"

    runtime_ids = {
        rt.TRANSFORM_PAYMENT,
        rt.TRANSFORM_HMDA_RATE,
        rt.TRANSFORM_HMDA_FLOW_NULL,
        rt.TRANSFORM_HMDA_PROXY,
        rt.TRANSFORM_CALIBRATED_NULL,
        rt.TRANSFORM_COMPOSITE_RAW_DEBT,
        rt.TRANSFORM_PMMS_DEBT,
        rt.TRANSFORM_PMMS_EQUITY,
    }
    dictionary_ids = {
        row["transformation_id"]
        for row in _load_yaml(VERSION_C_DICTIONARY)["variables"]
    }
    assert set(by_id) == runtime_ids == dictionary_ids
    assert all(row["version"] == "C" for row in rows)
    assert all(row["status"] == "active" for row in rows)

    stale_id = "version_c_stepwise_nmdb_pmms_debt_v1"
    assert stale_id not in by_id
    pmms = by_id[rt.TRANSFORM_PMMS_DEBT]
    pmms_text = " ".join(pmms.values()).lower()
    assert "nmdb" not in pmms_text
    assert "p&i" in pmms["input_roles"].lower()
    assert "pmms" in pmms["input_roles"].lower()
    assert "annuity" in pmms["formula"].lower()
    assert "never populate canonical calibrated debt" in pmms[
        "benchmark_rule"
    ].lower()

    assert (
        by_id[rt.TRANSFORM_HMDA_FLOW_NULL]["missing_value_policy"]
        == "Flag financed_purchase_value_monthly_source_unavailable"
    )
    assert by_id[rt.TRANSFORM_CALIBRATED_NULL][
        "missing_value_policy"
    ].startswith("Flag calibrated_debt_anchor_unavailable")
