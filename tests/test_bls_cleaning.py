import json

from src.cleaning.bls import clean_bls
from src.geography.comparable_counties import (
    COMPARABLE_COUNTY_FIPS,
    COMPARABLE_LAUS_SERIES_IDS,
)


def test_bls_series_mapping(tmp_path):
    series_ids = [
        "LAUCN060370000000003",
        "LAUCN060370000000004",
        "LAUCN060370000000005",
        "LAUCN060370000000006",
    ]
    raw = tmp_path / "bls.json"
    raw.write_text(
        json.dumps(
            {
                "responses": [
                    {
                        "Results": {
                            "series": [
                                {
                                    "seriesID": series_id,
                                    "data": [
                                        {
                                            "year": "2025",
                                            "period": "M01",
                                            "value": "5.2",
                                            "footnotes": [{}],
                                        }
                                    ],
                                }
                                for series_id in series_ids
                            ]
                        }
                    }
                ]
            }
        )
    )
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }
    result = clean_bls(raw, record, tmp_path / "clean.parquet")
    unemployment_rate = result.loc[
        result["variable_id"].eq("unemployment_rate")
    ].iloc[0]
    assert unemployment_rate["value"] == 5.2
    assert str(unemployment_rate["observation_date"].date()) == "2025-01-31"


def test_bls_comparable_counties_map_series_to_eight_geographies(tmp_path):
    raw = tmp_path / "bls_comparable.json"
    raw.write_text(
        json.dumps(
            {
                "series_ids": list(COMPARABLE_LAUS_SERIES_IDS),
                "responses": [
                    {
                        "Results": {
                            "series": [
                                {
                                    "seriesID": series_id,
                                    "data": [
                                        {
                                            "year": "2025",
                                            "period": "M01",
                                            "value": "5.2",
                                            "footnotes": [{}],
                                        }
                                    ],
                                }
                                for series_id in COMPARABLE_LAUS_SERIES_IDS
                            ]
                        }
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }

    result = clean_bls(raw, record, tmp_path / "clean.parquet")

    assert len(result) == 32
    assert set(result["county_fips"]) == set(COMPARABLE_COUNTY_FIPS)
    assert set(result["geo_id"]) == {
        f"COUNTY_{county_fips}" for county_fips in COMPARABLE_COUNTY_FIPS
    }
    assert result.groupby("county_fips")["variable_id"].nunique().eq(4).all()


def test_bls_missing_series_fails_clearly(tmp_path):
    raw = tmp_path / "bls_missing.json"
    raw.write_text(
        json.dumps(
            {
                "responses": [
                    {
                        "Results": {
                            "series": [
                                {
                                    "seriesID": "LAUCN060370000000003",
                                    "data": [
                                        {
                                            "year": "2025",
                                            "period": "M01",
                                            "value": "5.2",
                                            "footnotes": [{}],
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ]
            }
        )
    )
    record = {
        "data_vintage": "2026-07-01",
        "retrieved_at": "2026-07-01T00:00:00+00:00",
        "source_filename": raw.name,
    }
    try:
        clean_bls(raw, record, tmp_path / "clean.parquet")
    except ValueError as error:
        assert "missing configured series" in str(error)
    else:
        raise AssertionError("Missing BLS series were not rejected")
