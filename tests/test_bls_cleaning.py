import json

from src.cleaning.bls import clean_bls


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
