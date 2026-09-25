"""The modeling handoff selects source rows without filling or rewriting them."""

import duckdb
import pandas as pd
import pytest

from scripts.load_model_data import load_model_data


@pytest.fixture
def model_database(tmp_path):
    database = tmp_path / "housing.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA analytics")
        connection.execute(
            "CREATE TABLE analytics.county_monthly_panel "
            "(date TIMESTAMP, county_fips VARCHAR, zori DOUBLE)"
        )
        connection.executemany(
            "INSERT INTO analytics.county_monthly_panel VALUES (?, ?, ?)",
            [
                ("2020-03-31", "06037", None),
                ("2019-12-31", "06037", 1900.0),
                ("2020-01-31", "06037", 2000.5),
                ("2020-02-29", "06059", 9999.0),
                ("2020-02-29", "06037", 2010.75),
            ],
        )
    return database


def test_model_rows_preserve_identifiers_values_nulls_and_chronology(model_database):
    frame = load_model_data(model_database)

    assert list(frame.columns) == ["date", "county_fips", "zori"]
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2020-01-31", "2020-02-29", "2020-03-31",
    ]
    assert frame["county_fips"].tolist() == ["06037", "06037", "06037"]
    assert frame["zori"].iloc[:2].tolist() == [2000.5, 2010.75]
    assert pd.isna(frame["zori"].iloc[2])
    with duckdb.connect(str(model_database), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*), count(zori) FROM analytics.county_monthly_panel"
        ).fetchone() == (5, 4)


def test_saved_sql_is_found_outside_project_working_folder(model_database, tmp_path, monkeypatch):
    caller_folder = tmp_path / "another_working_folder"
    caller_folder.mkdir()
    monkeypatch.chdir(caller_folder)

    frame = load_model_data(model_database)

    assert len(frame) == 3
    assert frame["county_fips"].eq("06037").all()
    assert frame["zori"].isna().sum() == 1


def test_missing_database_is_rejected_without_creating_a_file(tmp_path):
    missing_database = tmp_path / "missing.duckdb"

    with pytest.raises(FileNotFoundError, match="Database not found"):
        load_model_data(missing_database)

    assert not missing_database.exists()
