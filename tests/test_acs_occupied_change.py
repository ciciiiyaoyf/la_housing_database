import pandas as pd

from src.harmonization.build import _add_acs_occupied_apartment_change
from src.validation.qa import _acs_occupied_change_failures


def test_acs_occupied_change_requires_consecutive_published_vintages():
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2010-12-31", "2011-12-31", "2013-12-31", "2014-12-31"]
            ),
            "geo_id": ["COUNTY_06037"] * 4,
            "acs_renter_occupied_units": [100.0, 112.0, 130.0, None],
        }
    )

    result = _add_acs_occupied_apartment_change(panel).set_index("date")

    assert pd.isna(
        result.loc[pd.Timestamp("2010-12-31"), "net_change_occupied_apartments_annual"]
    )
    assert (
        result.loc[
            pd.Timestamp("2011-12-31"), "net_change_occupied_apartments_annual"
        ]
        == 12.0
    )
    assert pd.isna(
        result.loc[pd.Timestamp("2013-12-31"), "net_change_occupied_apartments_annual"]
    )
    assert (
        result.loc[
            pd.Timestamp("2013-12-31"),
            "net_change_occupied_apartments_annual_quality_flag",
        ]
        == "not_available_nonconsecutive_vintage"
    )
    assert (
        result.loc[
            pd.Timestamp("2011-12-31"),
            "net_change_occupied_apartments_annual_quality_flag",
        ]
        == "overlapping_5year_vintage_difference_not_one_year_flow"
    )


def test_acs_occupied_change_qa_catches_formula_mismatch():
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2010-12-31", "2011-12-31"]),
            "geo_id": ["COUNTY_06037", "COUNTY_06037"],
            "acs_renter_occupied_units": [100.0, 112.0],
        }
    )
    valid = _add_acs_occupied_apartment_change(panel)
    assert _acs_occupied_change_failures(valid).empty

    invalid = valid.copy()
    invalid.loc[invalid["date"].eq(pd.Timestamp("2011-12-31")), "net_change_occupied_apartments_annual"] = 99.0
    failures = _acs_occupied_change_failures(invalid)
    assert "formula_mismatch" in set(failures["failure_reason"])


def test_acs_occupied_change_qa_requires_grid_and_no_quarterly_expansion():
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2010-12-31", "2011-12-31"]),
            "geo_id": ["COUNTY_06037", "COUNTY_06037"],
            "acs_renter_occupied_units": [100.0, 112.0],
        }
    )
    annual = _add_acs_occupied_apartment_change(panel)
    quarterly = pd.DataFrame(
        {
            "date": pd.to_datetime(["2011-03-31"]),
            "net_change_occupied_apartments_annual": [3.0],
        }
    )

    failures = _acs_occupied_change_failures(
        annual,
        quarterly,
        {2010, 2011, 2012},
    )

    assert {
        "unexpected_input_year_grid",
        "unexpected_change_year_grid",
        "annual_change_expanded_to_quarterly",
    }.issubset(set(failures["failure_reason"]))
