"""Build geography dimension without conflating postal ZIPs and ZCTAs."""

from __future__ import annotations

import pandas as pd

from src.common.config import ProjectConfig


def build_geography(
    config: ProjectConfig, fact: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    geography = pd.DataFrame(config.geographies)
    zip_ids = sorted(
        item
        for item in fact.get("geo_id", pd.Series(dtype=str)).dropna().unique()
        if str(item).startswith("ZIP_")
    )
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
    geography = pd.concat([geography, zip_rows], ignore_index=True)
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
