import pandas as pd

from src.cleaning.zillow import clean_zillow
from src.geography.comparable_counties import (
    COMPARABLE_COUNTIES,
    COMPARABLE_COUNTY_FIPS,
)


def test_zillow_county_wide_to_long(tmp_path):
    raw = tmp_path / "zillow.csv"
    county_names = [name for _, name in COMPARABLE_COUNTIES]
    pd.DataFrame(
        {
            "RegionName": [*county_names, "Ventura County"],
            "StateName": ["CA"] * 9,
            "2000-01-31": list(range(100, 109)),
            "2000-02-29": list(range(200, 209)),
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }
    result = clean_zillow(
        raw, record, "zillow_county_zhvi", tmp_path / "clean.parquet"
    )
    assert len(result) == 16
    assert set(result["geo_id"]) == {
        f"COUNTY_{county_fips}" for county_fips in COMPARABLE_COUNTY_FIPS
    }
    assert set(result["county_fips"]) == set(COMPARABLE_COUNTY_FIPS)
    assert result["state_fips"].eq("06").all()
    assert result["variable_id"].eq("zhvi").all()
    assert result["is_seasonally_adjusted"].all()


def test_zori_uses_only_smoothed_seasonally_adjusted_product(tmp_path):
    source_id = "zillow_county_zori_smoothed_sa"
    raw = tmp_path / f"{source_id}.csv"
    county_names = [name for _, name in COMPARABLE_COUNTIES]
    pd.DataFrame(
        {
            "RegionName": county_names,
            "StateName": ["CA"] * len(county_names),
            "2015-01-31": [2_000.0 + value for value in range(len(county_names))],
            "2015-02-28": [2_010.0 + value for value in range(len(county_names))],
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }
    result = clean_zillow(raw, record, source_id, tmp_path / "clean.parquet")
    assert set(result["variable_id"]) == {"zori_smoothed_sa"}
    assert result["is_seasonally_adjusted"].all()


def test_zillow_zip_scope_remains_los_angeles_postal_zips(tmp_path):
    raw = tmp_path / "zillow_zip.csv"
    pd.DataFrame(
        {
            "RegionName": ["90001", "92602"],
            "CountyName": ["Los Angeles County", "Orange County"],
            "StateName": ["CA", "CA"],
            "2025-01-31": [500_000.0, 800_000.0],
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }

    result = clean_zillow(
        raw, record, "zillow_zip_zhvi", tmp_path / "clean.parquet"
    )

    assert result["geo_id"].tolist() == ["ZIP_90001"]


def test_inactive_zori_product_is_rejected(tmp_path):
    source_id = "zillow_county_zori_smoothed_nsa"
    raw = tmp_path / f"{source_id}.csv"
    pd.DataFrame(
        {
            "RegionName": ["Los Angeles County"],
            "StateName": ["CA"],
            "2015-01-31": [2_000.0],
        }
    ).to_csv(raw, index=False)
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }
    try:
        clean_zillow(raw, record, source_id, tmp_path / "clean.parquet")
    except ValueError as error:
        assert "Unsupported inactive Zillow ZORI source" in str(error)
    else:
        raise AssertionError("Inactive Zillow ZORI source was not rejected")
