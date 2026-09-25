from dataclasses import replace
import json

import pandas as pd

from src.common.config import load_config
from src.geography.build import CALIFORNIA_COUNTY_FIPS, build_geography


def test_build_geography_adds_all_active_california_counties(tmp_path) -> None:
    config = replace(load_config(), root=tmp_path)
    (tmp_path / "data" / "clean").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "la_zcta_reference.json").write_text(
        json.dumps(
            {
                "geography_type": "zcta",
                "postal_zip_equivalence": False,
                "validated_source_id": "census_acs_5year_b19013",
                "zctas": ["90001"],
            }
        ),
        encoding="utf-8",
    )
    fact = pd.DataFrame(
        {
            "geo_id": [
                *[f"COUNTY_{fips}" for fips in CALIFORNIA_COUNTY_FIPS],
                "TRACT_06037101110",
                "ZIPCOUNTYPART_06037_90001",
            ],
            "county_fips": [
                *CALIFORNIA_COUNTY_FIPS,
                "06037",
                "06037",
            ],
            "county_name": [
                *[f"Official county {fips}" for fips in CALIFORNIA_COUNTY_FIPS],
                "Los Angeles County",
                "Los Angeles County",
            ],
        }
    )

    geography, crosswalk = build_geography(config, fact)

    counties = geography.loc[geography["geo_type"].eq("county")]
    assert set(counties["county_fips"]) == set(CALIFORNIA_COUNTY_FIPS)
    assert counties["geo_id"].nunique() == 58
    la_county = counties.loc[counties["geo_id"].eq("COUNTY_06037")].iloc[0]
    assert la_county["geo_name"] == "Los Angeles County"
    assert la_county["source"] == "project"
    assert la_county["cbsa_code"] == "31080"
    alameda = counties.loc[counties["geo_id"].eq("COUNTY_06001")].iloc[0]
    assert alameda["geo_name"] == "Alameda County"
    assert alameda["source"] == "project comparable-county contract"
    assert pd.isna(alameda["cbsa_code"])

    tract = geography.loc[geography["geo_id"].eq("TRACT_06037101110")].iloc[0]
    assert tract["geo_type"] == "tract_2020"
    assert tract["county_fips"] == "06037"
    zip_part = geography.loc[
        geography["geo_id"].eq("ZIPCOUNTYPART_06037_90001")
    ].iloc[0]
    assert zip_part["geo_type"] == "postal_zip_county_part"
    assert zip_part["postal_zip"] == "90001"
    zip_part_crosswalk = crosswalk.loc[
        crosswalk["geo_id"].eq("ZIPCOUNTYPART_06037_90001")
    ].iloc[0]
    assert zip_part_crosswalk["source_native_id"] == "06037_90001"
    zcta = geography.loc[geography["geo_id"].eq("ZCTA_90001")].iloc[0]
    assert zcta["geo_type"] == "zcta"
    assert zcta["zcta"] == "90001"
    assert pd.isna(zcta["postal_zip"])
    assert pd.isna(zcta["county_fips"])
