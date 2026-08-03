import numpy as np
import pandas as pd
import pytest

from src.harmonization.build import _add_acs_rental_availability
from src.validation.qa import _acs_rental_availability_failures


def _annual_components() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31", "2020-12-31"]
            ),
            "case": [
                "valid",
                "high_relative_moe",
                "missing_estimate",
                "missing_moe",
                "nonpositive_denominator",
            ],
            "acs_renter_occupied_units": [900.0, 100.0, np.nan, 900.0, 0.0],
            "acs_renter_occupied_units_moe": [50.0, 100.0, 50.0, np.nan, 0.0],
            "acs_for_rent_vacant_units": [100.0, 10.0, 100.0, 100.0, 0.0],
            "acs_for_rent_vacant_units_moe": [20.0, 1.0, 20.0, 20.0, 0.0],
        }
    ).set_index("case", drop=False)


def test_add_acs_rental_availability_constructs_and_suppresses():
    result = _add_acs_rental_availability(_annual_components())

    assert result.loc["valid", "acs_rental_availability_rate"] == pytest.approx(0.1)
    assert result.loc["valid", "acs_rental_availability_quality_flag"] == "ok"

    expected_flags = {
        "high_relative_moe": "suppressed_high_relative_moe",
        "missing_estimate": "suppressed_missing_component_estimate",
        "missing_moe": "suppressed_missing_component_moe",
        "nonpositive_denominator": "suppressed_nonpositive_denominator",
    }
    for case, expected_flag in expected_flags.items():
        assert pd.isna(result.loc[case, "acs_rental_availability_rate"])
        assert result.loc[case, "acs_rental_availability_quality_flag"] == expected_flag


def test_acs_rental_availability_qa_accepts_constructed_output():
    valid = _add_acs_rental_availability(_annual_components().loc[["valid"]])

    assert _acs_rental_availability_failures(valid).empty


def test_acs_rental_availability_qa_catches_mismatch_and_unsuppressed_high_moe():
    invalid = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-12-31", "2023-12-31"]),
            "acs_renter_occupied_units": [900.0, 100.0],
            "acs_renter_occupied_units_moe": [50.0, 100.0],
            "acs_for_rent_vacant_units": [100.0, 10.0],
            "acs_for_rent_vacant_units_moe": [20.0, 1.0],
            "acs_rental_availability_rate": [0.2, 10.0 / 110.0],
            "acs_rental_availability_quality_flag": ["ok", "ok"],
        }
    )

    failures = _acs_rental_availability_failures(invalid)

    assert "formula_mismatch" in set(failures["failure_reason"])
    assert "unsuppressed_high_relative_moe" in set(failures["failure_reason"])
