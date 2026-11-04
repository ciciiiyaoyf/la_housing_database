"""Regression tests using a small, explicitly synthetic DuckDB fixture."""

import duckdb
import pandas as pd
import pytest

from src.forecasting.data import load_inputs


@pytest.fixture
def fixture_database(tmp_path):
    path = tmp_path / "fixture.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("""
            CREATE SCHEMA metadata;
            CREATE SCHEMA analytics;
            CREATE SCHEMA raw;
            CREATE TABLE metadata.geography AS
            SELECT * FROM (VALUES
               ('COUNTY_06037','Los Angeles County','county','06','06037'),
               ('COUNTY_06059','Orange County','county','06','06059'),
               ('COUNTY_06065','Riverside County','county','06','06065'),
               ('COUNTY_06073','San Diego County','county','06','06073'),
               ('STATE_06','California','state','06',NULL)
            ) AS g(geo_id,geo_name,geo_type,state_fips,county_fips);
            CREATE TABLE metadata.variable_dictionary AS
            SELECT variable_id, variable_id AS display_name, '' AS definition, unit,
                   '' AS smoothing, '' AS seasonal_adjustment, '' AS methodological_notes,
                   '' AS transformation FROM (VALUES ('zhvi','USD'), ('zori_smoothed_sa','USD/month'),
                   ('permits_total','units'), ('active_inventory','count')) AS v(variable_id,unit);
            CREATE TABLE analytics.fact_housing_observation AS
            SELECT 'COUNTY_06037' AS geo_id, '06' AS state_fips, '06037' AS county_fips,
                   'zhvi' AS variable_id, stamp AS observation_date, amount AS value, 'USD' AS unit,
                   'fixture' AS provider, 'monthly' AS native_frequency, 'monthly' AS final_frequency,
                   'zillow_county_zhvi' AS source_id, 'fixture-v1' AS source_vintage,
                   '2021-01-01T01:00:00+00:00' AS retrieved_at, 'fixture.csv' AS source_filename,
                   repeat('a',64) AS checksum_sha256, 'fixture-transform' AS transformation_id,
                   FALSE AS is_imputed, FALSE AS is_interpolated, TRUE AS is_seasonally_adjusted,
                   NULL::VARCHAR AS quality_flag FROM (VALUES
                   (TIMESTAMP '2020-01-31',100.0::DOUBLE), (TIMESTAMP '2020-03-31',NULL),
                   (TIMESTAMP '2020-04-30',120.0::DOUBLE)) AS s(stamp,amount);
            CREATE TABLE raw.file_manifest AS
            SELECT DISTINCT source_id, source_vintage AS data_vintage, retrieved_at, source_filename,
                   checksum_sha256, 'fixture.csv' AS original_filename, 'fixture/file' AS relative_path,
                   NULL::VARCHAR AS download_url, 'county' AS geography_levels
            FROM analytics.fact_housing_observation;
        """)
    return path


def test_missing_month_keeps_calendar_and_source_null_distinct(fixture_database):
    observations, inventory = load_inputs(fixture_database)
    assert list(observations["source_row_present"]) == [True, False, True, True]
    assert observations["value"].isna().tolist() == [False, True, True, False]
    assert pd.isna(observations.loc[1, "source_filename"])
    assert observations.loc[2, "source_filename"] == "fixture.csv"
    row = inventory.query("geo_id == 'COUNTY_06037' and variable_id == 'zhvi'").iloc[0]
    assert row["observed_months"] == 2 and row["expected_months"] == 4
    assert row["longest_contiguous_months"] == 1 and row["source_rows"] == 3
    assert row["missing_month_dates"] == "2020-02, 2020-03"
    assert row["county_fips"] == "06037"
    assert inventory.query("geo_id == 'STATE_06'").iloc[0]["status"] == "unavailable"
    assert not observations["geo_id"].eq("STATE_06").any()


@pytest.mark.parametrize("change, expected_error", [
    ("INSERT INTO analytics.fact_housing_observation SELECT * FROM analytics.fact_housing_observation LIMIT 1",
     "one observation per calendar month-end"),
    ("UPDATE raw.file_manifest SET checksum_sha256 = repeat('b',64)", "lineage"),
    ("UPDATE raw.file_manifest SET geography_levels = 'postal_zip'", "county geography"),
    ("UPDATE analytics.fact_housing_observation SET county_fips = '06059'", "county identifier"),
    ("UPDATE analytics.fact_housing_observation SET is_interpolated = TRUE", "interpolated"),
    ("UPDATE analytics.fact_housing_observation SET native_frequency = 'rolling_3_months'", "native monthly"),
    ("UPDATE analytics.fact_housing_observation SET is_seasonally_adjusted = FALSE", "seasonally adjusted"),
    ("UPDATE analytics.fact_housing_observation SET source_id = 'archived_nsa'; "
     "UPDATE raw.file_manifest SET source_id = 'archived_nsa'", "unexpected county source"),
])
def test_incompatible_observations_fail_closed(fixture_database, change, expected_error):
    with duckdb.connect(str(fixture_database)) as con:
        con.execute(change)
    with pytest.raises(ValueError, match=expected_error):
        load_inputs(fixture_database)
