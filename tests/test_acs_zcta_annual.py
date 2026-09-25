from pathlib import Path

import pandas as pd

from src.harmonization.build import (
    _add_acs_zcta_price_to_income,
    _add_acs_zcta_renter_occupied_change,
    _zcta_annual_control_panel,
)
from src.validation.qa import _acs_zcta_annual_failures


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_zcta_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2011-12-31",
                    "2012-12-31",
                    "2013-12-31",
                    "2011-12-31",
                    "2012-12-31",
                    "2013-12-31",
                ]
            ),
            "geo_id": ["ZCTA_90001"] * 3 + ["ZCTA_90002"] * 3,
            "zcta": ["90001"] * 3 + ["90002"] * 3,
            "geography_type": ["zcta"] * 6,
            "acs_median_home_value": [100.0, 110.0, None, 200.0, 220.0, 240.0],
            "acs_median_household_income": [10.0, 11.0, 12.0, 20.0, None, 24.0],
            "acs_renter_occupied_units": [50.0, 60.0, 55.0, 80.0, 100.0, 115.0],
        }
    )


def test_zcta_annual_formulas_preserve_source_nulls_and_vintage_semantics() -> None:
    source = _synthetic_zcta_panel()
    result = _add_acs_zcta_price_to_income(source)
    result = _add_acs_zcta_renter_occupied_change(result).set_index(
        ["geo_id", "date"]
    )

    assert result.loc[
        ("ZCTA_90001", pd.Timestamp("2011-12-31")),
        "zcta_annual_price_to_income_acs",
    ] == 10.0
    assert pd.isna(
        result.loc[
            ("ZCTA_90001", pd.Timestamp("2013-12-31")),
            "zcta_annual_price_to_income_acs",
        ]
    )
    assert pd.isna(
        result.loc[
            ("ZCTA_90002", pd.Timestamp("2012-12-31")),
            "zcta_annual_price_to_income_acs",
        ]
    )
    assert result.loc[
        ("ZCTA_90001", pd.Timestamp("2012-12-31")),
        "zcta_renter_occupied_units_vintage_change_annual",
    ] == 10.0
    assert result.loc[
        ("ZCTA_90001", pd.Timestamp("2013-12-31")),
        "zcta_renter_occupied_units_vintage_change_annual",
    ] == -5.0
    assert pd.isna(
        result.loc[
            ("ZCTA_90001", pd.Timestamp("2011-12-31")),
            "zcta_renter_occupied_units_vintage_change_annual",
        ]
    )
    assert set(result.reset_index()["zcta"]) == {"90001", "90002"}


def test_zcta_annual_qa_catches_identity_formula_and_grid_failures() -> None:
    valid = _add_acs_zcta_price_to_income(_synthetic_zcta_panel())
    valid = _add_acs_zcta_renter_occupied_change(valid)
    assert _acs_zcta_annual_failures(
        valid,
        {"90001", "90002"},
        {2011, 2012, 2013},
    ).empty

    invalid = valid.copy()
    invalid.loc[0, "zcta"] = "90099"
    invalid.loc[1, "zcta_annual_price_to_income_acs"] = 999.0
    invalid.loc[
        2,
        "zcta_renter_occupied_units_vintage_change_annual_quality_flag",
    ] = "one_year_flow"
    failures = _acs_zcta_annual_failures(
        invalid,
        {"90001", "90002"},
        {2011, 2012, 2013},
    )
    assert {
        "zcta_identity_mismatch",
        "zcta_price_to_income_formula_mismatch",
        "nonnull_zcta_renter_change_without_overlap_flag",
    }.issubset(set(failures["failure_reason"]))


def test_repository_fact_builds_complete_2011_2024_zcta_annual_controls() -> None:
    fact = pd.read_parquet(ROOT / "data/final/fact_housing_observation.parquet")
    panel = _zcta_annual_control_panel(fact)

    assert len(panel) == 274 * 14
    assert panel["geo_id"].nunique() == panel["zcta"].nunique() == 274
    assert set(panel["date"].dt.year) == set(range(2011, 2025))
    assert panel["geo_id"].eq("ZCTA_" + panel["zcta"]).all()
    assert panel["geography_type"].eq("zcta").all()
    assert "postal_zip" not in panel.columns
    assert panel["zcta_annual_price_to_income_acs"].notna().sum() == 3_830
    assert (
        panel["zcta_renter_occupied_units_vintage_change_annual"]
        .notna()
        .sum()
        == 274 * 13
    )
    first_vintage = panel["date"].dt.year.eq(2011)
    assert panel.loc[
        first_vintage,
        "zcta_renter_occupied_units_vintage_change_annual",
    ].isna().all()
    assert panel.loc[
        ~first_vintage,
        "zcta_renter_occupied_units_vintage_change_annual",
    ].notna().all()
