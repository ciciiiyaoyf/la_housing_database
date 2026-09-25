import pandas as pd
import pytest

from src.harmonization.build import (
    _add_acs_noncontrolled_exposure,
    _add_noncontrolled_rent_growth_structural_sensitivity,
)
from src.validation.qa import (
    _acs_noncontrolled_exposure_failures,
    _acs_noncontrolled_schema_failures,
    _noncontrolled_rent_growth_sensitivity_failures,
    _noncontrolled_rent_growth_sensitivity_schema_failures,
)


def _source_row(year: int = 2020, geo_id: str = "COUNTY_06037") -> dict[str, object]:
    return {
        "date": pd.Timestamp(year=year, month=12, day=31),
        "geo_id": geo_id,
        "acs_b25032_renter_total": 100.0,
        "acs_b25032_renter_1_detached": 25.0,
        "acs_b25032_renter_1_attached": 5.0,
        "acs_b25032_renter_2_units": 10.0,
        "acs_b25032_renter_3_to_4_units": 10.0,
        "acs_b25032_renter_5_to_9_units": 15.0,
        "acs_b25032_renter_10_to_19_units": 10.0,
        "acs_b25032_renter_20_to_49_units": 10.0,
        "acs_b25032_renter_50_plus_units": 10.0,
        "acs_b25032_renter_mobile_home": 3.0,
        "acs_b25032_renter_boat_rv_other": 2.0,
        "acs_b25127_renter_total": 100.0,
        "acs_b25127_renter_recent_lower_bound_total": 20.0,
        "acs_b25127_renter_recent_lower_bound_1_unit": 5.0,
        "acs_b25127_renter_recent_lower_bound_2_to_4_units": 3.0,
        "acs_b25127_renter_recent_lower_bound_5_to_19_units": 4.0,
        "acs_b25127_renter_recent_lower_bound_20_to_49_units": 3.0,
        "acs_b25127_renter_recent_lower_bound_50_plus_units": 4.0,
        "acs_b25127_renter_recent_lower_bound_mobile_other": 1.0,
        "acs_b25127_renter_cutoff_mixed_total": 30.0,
        "acs_b25127_renter_cutoff_mixed_1_unit": 10.0,
        "acs_b25127_renter_cutoff_mixed_2_to_4_units": 5.0,
        "acs_b25127_renter_cutoff_mixed_5_to_19_units": 5.0,
        "acs_b25127_renter_cutoff_mixed_20_to_49_units": 4.0,
        "acs_b25127_renter_cutoff_mixed_50_plus_units": 5.0,
        "acs_b25127_renter_cutoff_mixed_mobile_other": 1.0,
    }


def test_acs_structural_shares_and_noncontrolled_union_bounds() -> None:
    source = pd.DataFrame([_source_row(2019), _source_row(2020)])

    result = _add_acs_noncontrolled_exposure(source)

    assert result["acs_renter_occupied_1_unit_share"].tolist() == [0.30, 0.30]
    assert result["acs_renter_occupied_2_to_4_units_share"].tolist() == [0.20, 0.20]
    assert result["acs_renter_occupied_5_plus_units_share"].tolist() == [0.45, 0.45]
    assert result["acs_renter_occupied_mobile_other_share"].tolist() == [0.05, 0.05]
    assert result[
        "acs_renter_recent_construction_share_lower_bound"
    ].tolist() == [0.20, 0.20]
    assert result[
        "acs_renter_recent_construction_share_upper_bound"
    ].tolist() == [0.50, 0.50]
    assert pd.isna(
        result.loc[
            0,
            "noncontrolled_exposure_share_structural_proxy_lower_bound",
        ]
    )
    assert pd.isna(
        result.loc[
            0,
            "noncontrolled_exposure_share_structural_proxy_upper_bound",
        ]
    )
    assert result.loc[
        1,
        "noncontrolled_exposure_share_structural_proxy_lower_bound",
    ] == pytest.approx(0.20)
    assert result.loc[
        1,
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    ] == pytest.approx(0.65)
    assert "noncontrolled_exposure_share" not in result.columns


def test_noncontrolled_union_upper_is_capped_only_after_valid_reconciliation() -> None:
    row = _source_row()
    row.update(
        {
            "acs_b25032_renter_1_detached": 85.0,
            "acs_b25032_renter_1_attached": 5.0,
            "acs_b25032_renter_2_units": 1.0,
            "acs_b25032_renter_3_to_4_units": 1.0,
            "acs_b25032_renter_5_to_9_units": 2.0,
            "acs_b25032_renter_10_to_19_units": 1.0,
            "acs_b25032_renter_20_to_49_units": 1.0,
            "acs_b25032_renter_50_plus_units": 1.0,
            "acs_b25032_renter_mobile_home": 2.0,
            "acs_b25032_renter_boat_rv_other": 1.0,
        }
    )
    result = _add_acs_noncontrolled_exposure(pd.DataFrame([row]))

    assert result.loc[
        0,
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    ] == pytest.approx(1.0)


def test_noncontrolled_bounds_suppress_invalid_one_unit_overlap() -> None:
    row = _source_row()
    row.update(
        {
            "acs_b25032_renter_1_detached": 5.0,
            "acs_b25032_renter_1_attached": 5.0,
            "acs_b25032_renter_2_units": 20.0,
            "acs_b25032_renter_3_to_4_units": 10.0,
            "acs_b25032_renter_5_to_9_units": 20.0,
            "acs_b25032_renter_10_to_19_units": 10.0,
            "acs_b25032_renter_20_to_49_units": 10.0,
            "acs_b25032_renter_50_plus_units": 10.0,
            "acs_b25032_renter_mobile_home": 5.0,
            "acs_b25032_renter_boat_rv_other": 5.0,
        }
    )
    result = _add_acs_noncontrolled_exposure(pd.DataFrame([row]))

    assert pd.isna(
        result.loc[
            0,
            "noncontrolled_exposure_share_structural_proxy_lower_bound",
        ]
    )
    assert pd.isna(
        result.loc[
            0,
            "noncontrolled_exposure_share_structural_proxy_upper_bound",
        ]
    )
    failures = _acs_noncontrolled_exposure_failures(result)
    assert (
        "b25127_age_band_one_unit_exceeds_b25032_one_unit_total"
        in set(failures["failure_reason"])
    )


def test_noncontrolled_exposure_qa_accepts_county_and_exact_zcta_outputs() -> None:
    county = _add_acs_noncontrolled_exposure(pd.DataFrame([_source_row()]))
    zcta_source = pd.DataFrame([_source_row(geo_id="ZCTA_90001")])
    zcta_source["zcta"] = "90001"
    zcta_source["geography_type"] = "zcta"
    zcta = _add_acs_noncontrolled_exposure(zcta_source)

    assert _acs_noncontrolled_exposure_failures(county).empty
    assert _acs_noncontrolled_exposure_failures(zcta).empty


def test_noncontrolled_exposure_qa_rejects_formula_and_schema_expansion() -> None:
    annual = _add_acs_noncontrolled_exposure(pd.DataFrame([_source_row()]))
    annual.loc[
        0,
        "noncontrolled_exposure_share_structural_proxy_upper_bound",
    ] = 0.60
    failures = _acs_noncontrolled_exposure_failures(annual)
    assert (
        "noncontrolled_exposure_share_structural_proxy_upper_bound_formula_mismatch"
        in set(failures["failure_reason"])
    )

    monthly = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-01-31")],
            "noncontrolled_exposure_share_structural_proxy_lower_bound": [0.2],
        }
    )
    fact = pd.DataFrame(
        {
            "variable_id": ["noncontrolled_exposure_share"],
            "final_frequency": ["annual"],
        }
    )
    schema = _acs_noncontrolled_schema_failures(
        {"county_annual": annual, "county_monthly": monthly, "fact": fact}
    )
    reasons = set(schema["failure_reason"])
    assert "structural_column_outside_county_or_zcta_annual_panel" in reasons
    assert "exact_noncontrolled_point_variable_present" in reasons


def test_noncontrolled_rent_growth_structural_sensitivity_uses_exact_december() -> None:
    annual = _add_acs_noncontrolled_exposure(
        pd.DataFrame([_source_row(2020), _source_row(2021)])
    )
    monthly = pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2020-11-30"),
                pd.Timestamp("2020-12-31"),
                pd.Timestamp("2021-12-31"),
            ],
            "zori_growth_yoy": [9.0, 0.10, -0.05],
        }
    )

    result = _add_noncontrolled_rent_growth_structural_sensitivity(
        annual, monthly
    )

    assert result[
        "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound"
    ].tolist() == pytest.approx([0.02, -0.01])
    assert result[
        "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound"
    ].tolist() == pytest.approx([0.065, -0.0325])
    assert _noncontrolled_rent_growth_sensitivity_failures(
        result, monthly
    ).empty


def test_noncontrolled_rent_growth_sensitivity_preserves_missing_december() -> None:
    annual = _add_acs_noncontrolled_exposure(
        pd.DataFrame([_source_row(2020), _source_row(2021)])
    )
    monthly = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-12-31")],
            "zori_growth_yoy": [0.10],
        }
    )

    result = _add_noncontrolled_rent_growth_structural_sensitivity(
        annual, monthly
    )

    assert result.loc[
        0,
        "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound",
    ] == pytest.approx(0.02)
    assert pd.isna(
        result.loc[
            1,
            "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound",
        ]
    )


def test_noncontrolled_rent_growth_sensitivity_qa_rejects_tampering_and_expansion() -> None:
    annual = _add_acs_noncontrolled_exposure(pd.DataFrame([_source_row(2020)]))
    monthly = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-12-31")],
            "zori_growth_yoy": [0.10],
        }
    )
    annual = _add_noncontrolled_rent_growth_structural_sensitivity(
        annual, monthly
    )
    annual.loc[
        0,
        "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound",
    ] = 0.99
    failures = _noncontrolled_rent_growth_sensitivity_failures(annual, monthly)
    assert (
        "exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound_formula_mismatch"
        in set(failures["failure_reason"])
    )

    monthly["exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound"] = 0.02
    fact = pd.DataFrame(
        {
            "variable_id": ["exposure_adjusted_noncontrolled_rent_growth"],
            "final_frequency": ["annual"],
        }
    )
    schema = _noncontrolled_rent_growth_sensitivity_schema_failures(
        {"county_annual": annual, "county_monthly": monthly, "fact": fact}
    )
    reasons = set(schema["failure_reason"])
    assert "rent_growth_sensitivity_outside_county_annual" in reasons
    assert "exact_noncontrolled_rent_growth_variable_present" in reasons
