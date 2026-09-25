from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.cleaning.redfin import clean_redfin
from src.geography.comparable_counties import (
    COMPARABLE_COUNTIES,
    COMPARABLE_COUNTY_FIPS,
    COMPARABLE_LAUS_SERIES_IDS,
    PEER_COUNTY_FIPS,
    PRIMARY_COUNTY_FIPS,
    comparable_county_fips,
)


ROOT = Path(__file__).resolve().parents[1]


def _record(path: Path) -> dict[str, str]:
    return {
        "data_vintage": "2026-08-01",
        "retrieved_at": "2026-08-02T00:00:00+00:00",
        "source_filename": path.name,
    }


def _redfin_source(path: Path, include_all_counties: bool = True) -> None:
    county_names = [name for _, name in COMPARABLE_COUNTIES]
    if not include_all_counties:
        county_names = county_names[:-1]
    county_names.append("Ventura County")
    size = len(county_names)
    pd.DataFrame(
        {
            "region": [f"{name}, CA" for name in county_names],
            "state_code": ["CA"] * size,
            "property_type": ["All Residential"] * size,
            "period_begin": ["2025-01-01"] * size,
            "period_end": ["2025-01-31"] * size,
            "period_duration": [31] * size,
            "is_seasonally_adjusted": [True] * size,
            "median_sale_price": [500_000 + index for index in range(size)],
            "months_of_supply": [3.0 + index / 10 for index in range(size)],
            "median_dom": [30 + index for index in range(size)],
        }
    ).to_csv(path, sep="\t", compression="gzip", index=False)


def test_comparable_county_contract_is_exact_and_builds_32_laus_series():
    assert PRIMARY_COUNTY_FIPS == "06037"
    assert len(COMPARABLE_COUNTY_FIPS) == 8
    assert len(PEER_COUNTY_FIPS) == 7
    assert len(COMPARABLE_LAUS_SERIES_IDS) == 32
    assert comparable_county_fips("Los Angeles County, CA") == "06037"
    assert comparable_county_fips("  san bernardino county, California ") == "06071"
    assert comparable_county_fips("Ventura County") is None


def test_configured_geographies_include_exact_comparison_set():
    raw = yaml.safe_load((ROOT / "config" / "geographies.yaml").read_text())
    configured = {
        str(row["county_fips"])
        for row in raw["geographies"]
        if row["geo_type"] == "county"
    }
    assert configured == set(COMPARABLE_COUNTY_FIPS)


def test_redfin_county_cleaner_retains_eight_counties_and_excludes_others(
    tmp_path,
):
    raw = tmp_path / "redfin.tsv.gz"
    _redfin_source(raw)

    result = clean_redfin(
        raw,
        _record(raw),
        "redfin_county_monthly",
        tmp_path / "clean.parquet",
    )

    assert len(result) == 8 * 3
    assert set(result["county_fips"]) == set(COMPARABLE_COUNTY_FIPS)
    assert set(result["geo_id"]) == {
        f"COUNTY_{county_fips}" for county_fips in COMPARABLE_COUNTY_FIPS
    }
    assert set(result["variable_id"]) == {
        "median_sale_price",
        "months_of_supply",
        "days_on_market",
    }
    assert result["state_fips"].eq("06").all()


def test_redfin_county_cleaner_rejects_missing_comparison_county(tmp_path):
    raw = tmp_path / "redfin_missing.tsv.gz"
    _redfin_source(raw, include_all_counties=False)

    with pytest.raises(
        ValueError, match="missing configured comparison counties"
    ):
        clean_redfin(
            raw,
            _record(raw),
            "redfin_county_monthly",
            tmp_path / "clean.parquet",
        )
