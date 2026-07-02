from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.redfin_zip import (
    REQUIRED_COLUMNS,
    clean_redfin_zip_key_metrics,
    validate_redfin_zip_schema,
)


def _row(postal_zip: str, begin: str, end: str) -> dict:
    return {
        "LAST UPDATED": "2026-06-03",
        "FREQUENCY": "Rolling 3 Months",
        "PERIOD BEGIN": begin,
        "PERIOD END": end,
        "REGION ID": "123",
        "REGION TYPE": "Zip",
        "REGION NAME": postal_zip,
        "METRO": "Los Angeles, CA metro area",
        "HOMES SOLD": 10,
        "HOMES SOLD YOY (%)": 5.0,
        "MEDIAN SALE PRICE NSA ($)": 500_000,
        "MEDIAN SALE PRICE NSA YOY (%)": 4.0,
        "MEDIAN DAYS ON MARKET (DAYS)": 30,
        "MEDIAN DAYS ON MARKET YOY (%)": -2.0,
        "NEW LISTINGS": 20,
        "NEW LISTINGS YOY (%)": 3.0,
        "ACTIVE LISTINGS": 40,
        "ACTIVE LISTINGS YOY (%)": 2.0,
        "PENDING SALES": 8,
        "PENDING SALES YOY (%)": 1.0,
    }


def test_redfin_zip_cleaner_filters_with_geography_and_preserves_window(
    tmp_path: Path,
):
    raw = tmp_path / "redfin.csv"
    pd.DataFrame(
        [
            _row("90001", "2019-04-01", "2019-06-30"),
            _row("90001", "2019-05-01", "2019-07-31"),
            _row("93001", "2019-04-01", "2019-06-30"),
        ],
        columns=REQUIRED_COLUMNS,
    ).to_csv(raw, index=False)
    geography = tmp_path / "geography.parquet"
    pd.DataFrame(
        {
            "postal_zip": ["90001", "93001"],
            "county_fips": ["06037", "06111"],
        }
    ).to_parquet(geography, index=False)
    output = tmp_path / "redfin_zip_monthly_la_county.parquet"
    observations = tmp_path / "redfin_zip_observations.parquet"
    coverage = tmp_path / "coverage"
    record = {
        "data_vintage": "2026-07-02",
        "retrieved_at": "2026-07-02T00:00:00+00:00",
        "source_filename": raw.name,
        "checksum_sha256": "abc123",
    }

    fact = clean_redfin_zip_key_metrics(
        raw,
        record,
        geography,
        output,
        observations,
        coverage,
    )
    clean = pd.read_parquet(output)

    assert len(clean) == 2
    assert set(clean["postal_zip"]) == {"90001"}
    assert clean["primary_county_fips"].eq("06037").all()
    assert clean["region_type"].eq("Zip").all()
    assert clean["frequency"].eq("Rolling 3 Months").all()
    assert clean["rolling_window_months"].eq(3).all()
    assert not clean.duplicated(
        ["postal_zip", "period_end", "source_vintage"]
    ).any()
    assert len(fact) == 24
    assert fact["variable_id"].nunique() == 12
    assert fact["native_frequency"].eq("rolling_3_months").all()
    assert (coverage / "redfin_zip_unmatched.csv").exists()
    unmatched = pd.read_csv(
        coverage / "redfin_zip_unmatched.csv", dtype={"postal_zip": str}
    )
    assert set(unmatched["postal_zip"]) == {"93001"}


def test_redfin_zip_schema_change_fails_clearly():
    with pytest.raises(ValueError, match="schema changed"):
        validate_redfin_zip_schema(REQUIRED_COLUMNS[:-1])
