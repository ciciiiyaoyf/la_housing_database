from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.common.config import load_config
from src.harmonization.build import (
    _add_acs_county_price_to_income,
    _wide_control_panel,
)
from src.reporting.catalog import IMPLEMENTATION, _proxy_methodology_assumptions
from src.validation.qa import (
    HUD_PIT_ANNUAL_IDS,
    _acs_county_price_to_income_failures,
    _hud_pit_annual_panel_failures,
    _issue_13_15_annual_placement_failures,
)


ROOT = Path(__file__).resolve().parents[1]


def _county_price_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2010-12-31", "2011-12-31"]),
            "geo_id": ["COUNTY_06037", "COUNTY_06037"],
            "acs_median_home_value": [400_000.0, 420_000.0],
            "acs_median_household_income": [50_000.0, 60_000.0],
        }
    )


def test_county_price_to_income_construction_and_structural_qa() -> None:
    result = _add_acs_county_price_to_income(_county_price_panel())

    assert result["county_annual_price_to_income_acs"].tolist() == [8.0, 7.0]
    assert result[
        "county_annual_price_to_income_acs_quality_flag"
    ].eq("matched_acs_5year_vintage_ratio").all()
    assert _acs_county_price_to_income_failures(
        result, {2010, 2011}
    ).empty

    invalid = result.copy()
    invalid.loc[0, "county_annual_price_to_income_acs"] = 99.0
    invalid.loc[1, "county_annual_price_to_income_acs_quality_flag"] = (
        "not_matched"
    )
    failures = _acs_county_price_to_income_failures(
        invalid, {2010, 2011, 2012}
    )

    assert {
        "county_price_to_income_formula_mismatch",
        "nonnull_county_price_to_income_without_matched_vintage_flag",
        "unexpected_county_price_to_income_year_grid",
    }.issubset(set(failures["failure_reason"]))


def _approved_issue_panels() -> dict[str, pd.DataFrame]:
    annual_date = pd.to_datetime(["2020-12-31"])
    return {
        "county_monthly": pd.DataFrame(),
        "zip_monthly": pd.DataFrame(),
        "county_quarterly": pd.DataFrame(),
        "state_monthly": pd.DataFrame(),
        "state_quarterly": pd.DataFrame(),
        "state_annual": pd.DataFrame(),
        "metro_monthly": pd.DataFrame(),
        "metro_quarterly": pd.DataFrame(),
        "metro_quarterly_controls": pd.DataFrame(),
        "homebuying_scenarios": pd.DataFrame(),
        "county_annual": pd.DataFrame(
            {
                "date": annual_date,
                "geo_id": ["COUNTY_06037"],
                "net_change_occupied_apartments_annual": [1.0],
                "county_annual_price_to_income_acs": [8.0],
            }
        ),
        "zcta_annual": pd.DataFrame(
            {
                "date": annual_date,
                "geo_id": ["ZCTA_90001"],
                "zcta_renter_occupied_units_vintage_change_annual": [2.0],
                "zcta_annual_price_to_income_acs": [7.0],
            }
        ),
        "coc_annual": pd.DataFrame(
            {
                "date": annual_date,
                "geo_id": ["COC_COMPOSITE_LA_FOUR_COC_COMPOSITE"],
                **{variable_id: [np.nan] for variable_id in HUD_PIT_ANNUAL_IDS},
            }
        ),
    }


def test_issue_13_15_placement_qa_checks_null_columns_and_wrong_geography() -> None:
    empty_fact = pd.DataFrame(columns=["variable_id"])
    approved = _approved_issue_panels()
    assert _issue_13_15_annual_placement_failures(
        empty_fact, approved
    ).empty

    invalid_fact = pd.DataFrame(
        {
            "observation_date": pd.to_datetime(
                ["2020-12-31", "2020-03-31"]
            ),
            "geo_id": ["COUNTY_06037", "COUNTY_06037"],
            "county_fips": ["06037", "06037"],
            "variable_id": [
                "county_annual_price_to_income_acs",
                "hud_pit_overall_homeless_annual",
            ],
            "native_frequency": ["derived", "quarterly"],
            "final_frequency": ["annual", "quarterly"],
            "is_interpolated": [False, False],
            "is_imputed": [False, False],
        }
    )
    invalid_panels = {name: panel.copy() for name, panel in approved.items()}
    # Presence is forbidden even when a misplaced column contains only nulls.
    invalid_panels["county_monthly"] = pd.DataFrame(
        {"county_annual_price_to_income_acs": [np.nan]}
    )
    invalid_panels["county_annual"]["zcta_annual_price_to_income_acs"] = np.nan

    failures = _issue_13_15_annual_placement_failures(
        invalid_fact, invalid_panels
    )
    reasons = set(failures["failure_reason"])

    assert "acs_derived_annual_field_present_in_fact" in reasons
    assert "hud_pit_fact_frequency_or_geography_mismatch" in reasons
    assert "issue_13_15_field_outside_approved_panel" in reasons


def _hud_fact_for_null_composite_year() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, value in ((2010, np.nan), (2011, 100.0)):
        for variable_id in sorted(HUD_PIT_ANNUAL_IDS):
            rows.append(
                {
                    "observation_date": pd.Timestamp(year, 12, 31),
                    "geo_id": "COC_COMPOSITE_LA_FOUR_COC_COMPOSITE",
                    "state_fips": "06",
                    "county_fips": pd.NA,
                    "variable_id": variable_id,
                    "value": value,
                    "native_frequency": "annual_point_in_time",
                    "final_frequency": "annual",
                    "source_vintage": "2025",
                    "retrieved_at": "2026-08-16T00:00:00+00:00",
                    "is_interpolated": False,
                    "is_imputed": False,
                }
            )
    return pd.DataFrame(rows)


def test_coc_panel_preserves_explicit_all_null_hud_composite_year() -> None:
    fact = _hud_fact_for_null_composite_year()
    panel = _wide_control_panel(
        fact,
        geo_prefixes=("COC_",),
        frequency="annual",
        id_columns={
            "state_fips": "06",
            "geography_type": "continuum_of_care_or_explicit_composite",
        },
    )

    row_2010 = panel.loc[panel["date"].eq(pd.Timestamp("2010-12-31"))]
    assert len(panel) == 2
    assert len(row_2010) == 1
    assert row_2010[list(HUD_PIT_ANNUAL_IDS)].isna().to_numpy().all()
    assert row_2010["source_coverage_count"].item() == 0
    assert _hud_pit_annual_panel_failures(fact, panel).empty

    failures = _hud_pit_annual_panel_failures(
        fact, panel.loc[panel["date"].dt.year.ne(2010)]
    )
    assert "hud_pit_geography_year_missing_from_panel" in set(
        failures["failure_reason"]
    )


def test_catalog_and_source_contracts_identify_panel_only_acs_outputs() -> None:
    config = load_config(ROOT)
    sources = {source.source_id: source for source in config.sources}
    for source_id in (
        "census_acs_5year_b19013",
        "census_acs_5year_b25077",
    ):
        source = sources[source_id]
        assert "county_annual_price_to_income_acs" in source.key_variables
        destinations = set(source.destination_analytical_table.split("|"))
        assert {
            "analytics.fact_housing_observation",
            "analytics.county_annual_controls",
            "analytics.zcta_annual_controls",
        } == destinations
        assert "constructed ratios are not fact observations" in (
            source.methodology_notes
        )

    b25003_destinations = set(
        sources[
            "census_acs_5year_b25003"
        ].destination_analytical_table.split("|")
    )
    assert {
        "analytics.fact_housing_observation",
        "analytics.county_annual_controls",
        "analytics.zcta_annual_controls",
    } == b25003_destinations

    occupied = IMPLEMENTATION[
        "Net Change in Occupied Apartments (quarterly)"
    ]
    assert "analytics.fact_housing_observation" not in occupied[
        "implemented_table"
    ].split("|")
    assumptions = _proxy_methodology_assumptions()
    county_ratio = assumptions.loc[
        assumptions["methodology_id"].eq("acs_county_price_to_income_v1")
    ]
    assert len(county_ratio) == 1
    assert county_ratio["variable_id"].item() == (
        "county_annual_price_to_income_acs"
    )
