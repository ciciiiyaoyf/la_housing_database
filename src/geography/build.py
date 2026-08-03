"""Build geography dimension without conflating postal ZIPs and ZCTAs."""

from __future__ import annotations

import pandas as pd

from src.common.config import ProjectConfig


# California's county component codes are the 58 odd values from 001 to 115.
# Use the complete official set when deciding whether active facts provide a
# valid statewide county dimension; do not accept an arbitrary collection of
# identifiers merely because each identifier has five digits.
CALIFORNIA_COUNTY_FIPS: tuple[str, ...] = tuple(
    f"06{county:03d}" for county in range(1, 116, 2)
)


def _fact_geo_ids(fact: pd.DataFrame, prefix: str) -> list[str]:
    return sorted(
        str(item)
        for item in fact.get("geo_id", pd.Series(dtype="string")).dropna().unique()
        if str(item).startswith(prefix)
    )


def _county_name_from_fact(
    fact: pd.DataFrame, geo_id: str, county_fips: str
) -> str:
    """Use an attached official label when available without requiring it."""

    if "county_name" in fact:
        names = (
            fact.loc[fact["geo_id"].astype("string").eq(geo_id), "county_name"]
            .dropna()
            .astype("string")
            .str.strip()
        )
        names = names.loc[names.ne("")].drop_duplicates()
        if len(names) == 1:
            return str(names.iloc[0])
    return f"California county {county_fips}"


def build_geography(
    config: ProjectConfig, fact: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    geography = pd.DataFrame(config.geographies)
    configured_ids = set(geography["geo_id"].astype(str))
    county_ids = [
        geo_id
        for geo_id in _fact_geo_ids(fact, "COUNTY_")
        if geo_id.removeprefix("COUNTY_") in CALIFORNIA_COUNTY_FIPS
        and geo_id not in configured_ids
    ]
    county_rows = pd.DataFrame(
        [
            {
                "geo_id": geo_id,
                "geo_type": "county",
                "geo_name": _county_name_from_fact(
                    fact, geo_id, geo_id.removeprefix("COUNTY_")
                ),
                "state_fips": "06",
                "county_fips": geo_id.removeprefix("COUNTY_"),
                "cbsa_code": None,
                "postal_zip": None,
                "zcta": None,
                "source": "active fact geography",
                "source_region_id": geo_id.removeprefix("COUNTY_"),
                "valid_from": None,
                "valid_to": None,
            }
            for geo_id in county_ids
        ]
    )
    zip_ids = _fact_geo_ids(fact, "ZIP_")
    zip_rows = pd.DataFrame(
        [
            {
                "geo_id": geo_id,
                "geo_type": "postal_zip",
                "geo_name": geo_id.removeprefix("ZIP_"),
                "state_fips": "06",
                "county_fips": "06037",
                "cbsa_code": "31080",
                "postal_zip": geo_id.removeprefix("ZIP_"),
                "zcta": None,
                "source": "Zillow/Redfin provider geography",
                "source_region_id": geo_id.removeprefix("ZIP_"),
                "valid_from": None,
                "valid_to": None,
            }
            for geo_id in zip_ids
        ]
    )
    tract_ids = _fact_geo_ids(fact, "TRACT_")
    tract_rows = pd.DataFrame(
        [
            {
                "geo_id": geo_id,
                "geo_type": "tract_2020",
                "geo_name": geo_id.removeprefix("TRACT_"),
                "state_fips": geo_id.removeprefix("TRACT_")[:2],
                "county_fips": geo_id.removeprefix("TRACT_")[:5],
                "cbsa_code": "31080",
                "postal_zip": None,
                "zcta": None,
                "source": "HUD-USPS standardized 2020 Census tract",
                "source_region_id": geo_id.removeprefix("TRACT_"),
                "valid_from": None,
                "valid_to": None,
            }
            for geo_id in tract_ids
        ]
    )
    zip_part_ids = _fact_geo_ids(fact, "ZIPCOUNTYPART_")
    zip_part_rows = pd.DataFrame(
        [
            {
                "geo_id": geo_id,
                "geo_type": "postal_zip_county_part",
                "geo_name": geo_id.rsplit("_", 1)[-1],
                "state_fips": geo_id.removeprefix("ZIPCOUNTYPART_")[:2],
                "county_fips": geo_id.removeprefix("ZIPCOUNTYPART_")[:5],
                "cbsa_code": "31080",
                "postal_zip": geo_id.rsplit("_", 1)[-1],
                "zcta": None,
                "source": "HUD-USPS modeled postal ZIP county part",
                "source_region_id": geo_id.removeprefix("ZIPCOUNTYPART_"),
                "valid_from": None,
                "valid_to": None,
            }
            for geo_id in zip_part_ids
        ]
    )
    geography = pd.concat(
        [geography, county_rows, zip_rows, tract_rows, zip_part_rows],
        ignore_index=True,
    )
    geography = geography.drop_duplicates("geo_id", keep="last")
    crosswalk = geography.assign(
        source_native_id=geography["source_region_id"],
        source_native_name=geography["geo_name"],
        crosswalk_version=config.crosswalk_version,
        primary_county_flag=geography["county_fips"].eq("06037"),
        county_allocation_share=geography["county_fips"].map(
            lambda item: 1.0 if item == "06037" else None
        ),
    )[
        [
            "geo_id",
            "geo_type",
            "state_fips",
            "county_fips",
            "cbsa_code",
            "postal_zip",
            "zcta",
            "source_native_id",
            "source_native_name",
            "valid_from",
            "valid_to",
            "crosswalk_version",
            "primary_county_flag",
            "county_allocation_share",
        ]
    ]
    geography.to_parquet(config.root / "data" / "clean" / "geography.parquet", index=False)
    crosswalk.to_parquet(
        config.root / "data" / "clean" / "geography_crosswalk.parquet", index=False
    )
    return geography, crosswalk
