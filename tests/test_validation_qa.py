import pandas as pd
import pytest

from src.geography.build import CALIFORNIA_COUNTY_FIPS
from src.validation.qa import (
    GEO_ID_PATTERN,
    _classify_geo_type,
    _hud_usps_county_coverage_failures,
    _hud_usps_geography_identity_failures,
)


@pytest.mark.parametrize(
    ("geo_id", "expected"),
    [
        ("US", "national"),
        ("STATE_06", "state"),
        ("COUNTY_06037", "county"),
        ("TRACT_06037101110", "tract_2020"),
        (
            "ZIPCOUNTYPART_06037_90001",
            "postal_zip_county_part",
        ),
        ("ZIP_90001", "postal_zip"),
        ("ZCTA_90001", "zcta"),
        ("METRO_31080", "metro"),
        ("METRODIV_31084", "metro_division"),
        ("BOGUS_1", "unknown"),
    ],
)
def test_classify_geo_type_preserves_supported_geographies(
    geo_id: str, expected: str
) -> None:
    assert _classify_geo_type(geo_id) == expected


@pytest.mark.parametrize(
    "geo_id",
    ["TRACT_06037101110", "ZIPCOUNTYPART_06037_90001"],
)
def test_hud_usps_geo_ids_match_fixed_width_contract(geo_id: str) -> None:
    assert GEO_ID_PATTERN.fullmatch(geo_id)


@pytest.mark.parametrize(
    "geo_id",
    [
        "TRACT_6037101110",
        "TRACT_060371011100",
        "ZIPCOUNTYPART_6037_90001",
        "ZIPCOUNTYPART_06037_9001",
    ],
)
def test_hud_usps_geo_ids_reject_width_loss(geo_id: str) -> None:
    assert not GEO_ID_PATTERN.fullmatch(geo_id)


def _active_hud_facts() -> pd.DataFrame:
    date = pd.Timestamp("2026-03-31")
    county = pd.DataFrame(
        {
            "observation_date": date,
            "geo_id": [f"COUNTY_{fips}" for fips in CALIFORNIA_COUNTY_FIPS],
            "state_fips": "06",
            "county_fips": CALIFORNIA_COUNTY_FIPS,
            "variable_id": "usps_total_residential_addresses",
            "source_id": "hud_usps_ncwm_quarterly",
        }
    )
    detailed = pd.DataFrame(
        {
            "observation_date": [date, date],
            "geo_id": [
                "TRACT_06037101110",
                "ZIPCOUNTYPART_06037_90001",
            ],
            "state_fips": ["06", "06"],
            "county_fips": ["06037", "06037"],
            "variable_id": [
                "usps_total_residential_addresses",
                "usps_total_residential_addresses",
            ],
            "source_id": [
                "hud_usps_ncwm_quarterly",
                "hud_usps_ncwm_quarterly",
            ],
        }
    )
    return pd.concat([county, detailed], ignore_index=True)


def test_hud_usps_geography_safeguards_accept_complete_consistent_facts() -> None:
    fact = _active_hud_facts()

    assert _hud_usps_geography_identity_failures(fact).empty
    assert _hud_usps_county_coverage_failures(fact).empty


def test_hud_usps_geography_safeguards_reject_mismatch_and_missing_county() -> None:
    fact = _active_hud_facts()
    fact.loc[fact["geo_id"].eq("TRACT_06037101110"), "county_fips"] = "06059"
    fact = fact.loc[~fact["geo_id"].eq("COUNTY_06115")]

    identity = _hud_usps_geography_identity_failures(fact)
    coverage = _hud_usps_county_coverage_failures(fact)

    assert identity["geo_id"].tolist() == ["TRACT_06037101110"]
    assert coverage["missing_counties"].tolist() == ["06115"]
