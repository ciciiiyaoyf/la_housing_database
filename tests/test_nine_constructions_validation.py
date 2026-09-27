"""A delivery package cannot pass validation with stale or altered records."""

from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

import src.validation.nine_constructions as validation


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    for folder in ("config", "metadata", "data/final", "database"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/nine_constructions.yaml").write_text("fixture: true")
    config = SimpleNamespace(root=tmp_path, pipeline={"database_path": "database/housing.duckdb"})
    expected = pd.DataFrame({
        "row_type": ["county_monthly", "zip_regime_quarterly"],
        "source_row_key": ["2026-05-31|06037", "2026-06-30|00501"],
        "date": pd.to_datetime(["2026-05-31", "2026-06-30"]),
        "frequency": ["monthly", "quarterly"],
        "geo_id": ["COUNTY_06037", "ZIP_00501"],
        "value": [1.25, None],
    })
    monkeypatch.setattr(validation, "build_nine_constructions", lambda *_args, **_kwargs: expected.copy())
    expected.to_parquet(tmp_path / "data/final/nine_constructions.parquet", index=False)
    dictionary = pd.DataFrame({"variable_id": ["example"], "construction_number": ["1"]})
    dictionary.to_csv(tmp_path / "metadata/nine_constructions_dictionary.csv", index=False)
    with duckdb.connect(str(tmp_path / "database/housing.duckdb")) as con:
        con.execute("CREATE SCHEMA analytics; CREATE SCHEMA metadata")
        con.register("observations", expected)
        con.register("dictionary", dictionary)
        con.execute("CREATE TABLE analytics.nine_constructions AS SELECT * FROM observations")
        con.execute("CREATE TABLE metadata.nine_constructions_dictionary AS SELECT * FROM dictionary")
    return config


def test_matching_delivery_passes_without_writing(delivery):
    before = (delivery.root / "data/final/nine_constructions.parquet").read_bytes()
    assert validation.nine_constructions_failures(delivery).empty
    assert (delivery.root / "data/final/nine_constructions.parquet").read_bytes() == before


@pytest.mark.parametrize("sql", [
    "UPDATE analytics.nine_constructions SET value=0 WHERE value IS NULL",
    "UPDATE analytics.nine_constructions SET frequency='monthly' WHERE frequency='quarterly'",
    "UPDATE analytics.nine_constructions SET geo_id='COUNTY_06037' WHERE geo_id='ZIP_00501'",
    "DELETE FROM analytics.nine_constructions WHERE value IS NULL",
    "INSERT INTO analytics.nine_constructions SELECT * FROM analytics.nine_constructions LIMIT 1",
    "UPDATE metadata.nine_constructions_dictionary SET construction_number='9'",
])
def test_changed_values_nulls_grain_or_dictionary_fail(delivery, sql):
    with duckdb.connect(str(delivery.root / "database/housing.duckdb")) as con:
        con.execute(sql)
    assert not validation.nine_constructions_failures(delivery).empty


def test_missing_or_stale_parquet_fails(delivery):
    path = delivery.root / "data/final/nine_constructions.parquet"
    altered = pd.read_parquet(path)
    altered.loc[0, "value"] = 9.0
    altered.to_parquet(path, index=False)
    failures = validation.nine_constructions_failures(delivery)
    assert "data/final/nine_constructions.parquet" in set(failures.location)
    path.unlink()
    assert not validation.nine_constructions_failures(delivery).empty
