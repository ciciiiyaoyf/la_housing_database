import pandas as pd

from src.cleaning.zillow import clean_zillow


def test_zillow_county_wide_to_long(tmp_path):
    raw = tmp_path / "zillow.csv"
    pd.DataFrame(
        {
            "RegionName": ["Los Angeles County", "Orange County"],
            "StateName": ["CA", "CA"],
            "2000-01-31": [100.0, 200.0],
            "2000-02-29": [101.0, 201.0],
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
    assert len(result) == 2
    assert set(result["geo_id"]) == {"COUNTY_06037"}
    assert result["variable_id"].eq("zhvi").all()
    assert result["is_seasonally_adjusted"].all()


def test_zori_uses_only_smoothed_seasonally_adjusted_product(tmp_path):
    source_id = "zillow_county_zori_smoothed_sa"
    raw = tmp_path / f"{source_id}.csv"
    pd.DataFrame(
        {
            "RegionName": ["Los Angeles County"],
            "StateName": ["CA"],
            "2015-01-31": [2_000.0],
            "2015-02-28": [2_010.0],
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
