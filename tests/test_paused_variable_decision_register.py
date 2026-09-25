from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.config import load_config


ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = ROOT / "metadata" / "paused_variables_decision_register.csv"

DECISION_COMPLETE_ISSUES = {3, 4, 5, 10, 12, 13, 14, 15, 16, 17}
CLOSED_ISSUES = {1, 2, 6, 7, 8, 9, 11}

VERSION_A_NULL_CONTRACT_IDS = {
    "acs_implied_first_mortgage_balance_cal_benchmark",
    "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_capita_benchmark",
    "acs_first_mortgage_debt_to_owner_income_benchmark",
    "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
    "acs_implied_owner_occupied_home_equity_all_benchmark",
    "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
    "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark",
    "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark",
    "acs_aggregate_ltv_mortgaged_benchmark",
    "acs_equity_rate_mortgaged_benchmark",
}

INTENTIONALLY_ABSENT_IDS = {
    "new_apartments_completed_proxy",
    "multifamily_under_construction_proxy",
    "noncontrolled_exposure_share",
    "exposure_adjusted_noncontrolled_rent_growth",
    "single_family_rental_construction_proxy",
    "rental_listing_inventory_monthly",
}


def _register() -> pd.DataFrame:
    return pd.read_csv(REGISTER_PATH, dtype={"issue_id": "int64"}).fillna("")


def _tokens(value: object) -> set[str]:
    return {item for item in str(value).split("|") if item}


def test_register_has_exact_workbook_issue_set_and_status_classes() -> None:
    register = _register()

    assert register.columns.tolist() == [
        "issue_id",
        "variable_or_group",
        "workbook_status",
        "status_class",
        "implementation_disposition",
        "required_configured_ids",
        "intentionally_absent_ids",
        "version_a_null_contract_ids",
        "canonical_output_locations",
        "evidence_paths",
        "authority_reference",
        "decision_summary",
    ]
    assert len(register) == 17
    assert register["issue_id"].is_unique
    assert set(register["issue_id"]) == set(range(1, 18))
    assert set(
        register.loc[
            register["status_class"].eq("decision_complete"), "issue_id"
        ]
    ) == DECISION_COMPLETE_ISSUES
    assert set(
        register.loc[register["status_class"].eq("closed"), "issue_id"]
    ) == CLOSED_ISSUES
    assert set(register["status_class"]) == {"decision_complete", "closed"}

    expected_status = {
        1: "Closed — scenario framework retained",
        2: "Closed — scenario framework retained",
        3: "Decision complete — acquire/use CA CPI and construct real series",
        4: "Decision complete — ready to implement",
        5: "Decision complete — ready to implement",
        6: "Closed — not pursued",
        7: "Closed — exact target not pursued",
        8: "Closed — not pursued",
        9: "Closed",
        10: "Decision complete — applies broadly",
        11: "Closed — not pursued under current methodology",
        12: "Decision complete — source-update check remains",
        13: "Decision complete",
        14: "Decision complete",
        15: "Decision complete",
        16: "Decision complete — acquire matched PEP series and calculate",
        17: "Decision complete subject to denominator coverage check",
    }
    assert register.set_index("issue_id")["workbook_status"].to_dict() == (
        expected_status
    )
    assert register["authority_reference"].ne("").all()
    issue_4_authority = register.set_index("issue_id").loc[
        4, "authority_reference"
    ]
    assert "Issue_4_Apartment_Completions_Proxy_Workflow.xlsx" in (
        issue_4_authority
    )


def test_every_required_id_is_configured_and_forbidden_exact_targets_are_absent() -> None:
    register = _register()
    configured = {
        str(item["variable_id"]) for item in load_config(ROOT).variables
    }
    required = set().union(
        *register["required_configured_ids"].map(_tokens)
    )
    intentionally_absent = set().union(
        *register["intentionally_absent_ids"].map(_tokens)
    )

    assert required <= configured
    assert intentionally_absent == INTENTIONALLY_ABSENT_IDS
    assert intentionally_absent.isdisjoint(configured)


def test_version_a_calibrated_targets_have_explicit_all_null_contracts() -> None:
    register = _register()
    null_contract_ids = set().union(
        *register["version_a_null_contract_ids"].map(_tokens)
    )
    assert null_contract_ids == VERSION_A_NULL_CONTRACT_IDS
    assert null_contract_ids == set().union(
        _tokens(
            register.set_index("issue_id").loc[
                1, "version_a_null_contract_ids"
            ]
        ),
        _tokens(
            register.set_index("issue_id").loc[
                2, "version_a_null_contract_ids"
            ]
        ),
    )

    benchmark = pd.read_parquet(
        ROOT / "data" / "final" / "housing_finance_benchmark.parquet",
        columns=["variable_id", "value"],
    )
    targets = benchmark.loc[
        benchmark["variable_id"].isin(null_contract_ids)
    ]
    coverage = targets.groupby("variable_id")["value"].agg(
        row_count="size", nonnull_count="count"
    )
    assert set(coverage.index) == null_contract_ids
    assert coverage["row_count"].eq(58).all()
    assert coverage["nonnull_count"].eq(0).all()
    assert "all twelve calibrated" in register.set_index("issue_id").loc[
        9, "decision_summary"
    ]


def test_issue_4_and_5_have_exact_four_ids_and_panel_view_locations() -> None:
    register = _register().set_index("issue_id")
    expected = {
        4: {
            "new_apartments_completed_proxy_central",
            "new_apartments_completed_proxy_slow",
        },
        5: {
            "multifamily_under_construction_proxy_central",
            "multifamily_under_construction_proxy_slow",
        },
    }
    expected_locations = {
        "analytics.county_monthly_panel",
        "analytics.renter_market_monthly",
    }

    assert set().union(
        *(
            _tokens(register.loc[issue, "required_configured_ids"])
            for issue in (4, 5)
        )
    ) == set().union(*expected.values())
    for issue, variable_ids in expected.items():
        assert _tokens(register.loc[issue, "required_configured_ids"]) == (
            variable_ids
        )
        assert _tokens(register.loc[issue, "canonical_output_locations"]) == (
            expected_locations
        )
        assert _tokens(register.loc[issue, "intentionally_absent_ids"]) == {
            "new_apartments_completed_proxy"
            if issue == 4
            else "multifamily_under_construction_proxy"
        }


def test_canonical_output_locations_match_current_architecture() -> None:
    register = _register().set_index("issue_id")
    expected = {
        1: {"analytics.housing_finance_benchmark", "analytics.state_annual_controls"},
        2: {"analytics.housing_finance_benchmark", "analytics.state_annual_controls"},
        3: {"analytics.state_annual_controls"},
        4: {"analytics.county_monthly_panel", "analytics.renter_market_monthly"},
        5: {"analytics.county_monthly_panel", "analytics.renter_market_monthly"},
        6: {"none_intentional"},
        7: {"analytics.county_annual_controls", "analytics.zcta_annual_controls"},
        8: {"none_intentional"},
        9: {"analytics.housing_finance_benchmark", "analytics.state_annual_controls"},
        10: {
            "analytics.state_annual_controls",
            "analytics.county_annual_controls",
            "analytics.zcta_annual_controls",
            "analytics.coc_annual_controls",
        },
        11: {"none_intentional"},
        12: {"analytics.fact_housing_observation", "analytics.county_annual_controls"},
        13: {"analytics.county_annual_controls", "analytics.zcta_annual_controls"},
        14: {"analytics.county_annual_controls", "analytics.zcta_annual_controls"},
        15: {"analytics.fact_housing_observation", "analytics.coc_annual_controls"},
        16: {"analytics.fact_housing_observation", "analytics.county_annual_controls"},
        17: {"analytics.fact_housing_observation", "analytics.county_annual_controls"},
    }
    actual = {
        issue: _tokens(register.loc[issue, "canonical_output_locations"])
        for issue in register.index
    }
    assert actual == expected


def test_all_evidence_paths_exist_including_issue_12_source_check() -> None:
    register = _register().set_index("issue_id")
    issue_12_paths = _tokens(register.loc[12, "evidence_paths"])
    assert "metadata/eviction_lab_ets_source_check.csv" in issue_12_paths

    missing = [
        f"Issue {issue}: {path}"
        for issue, row in register.iterrows()
        for path in _tokens(row["evidence_paths"])
        if not (ROOT / path).exists()
    ]
    assert not missing, "Missing decision-register evidence:\n" + "\n".join(
        missing
    )
