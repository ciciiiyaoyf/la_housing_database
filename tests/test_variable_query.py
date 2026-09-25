from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.reporting.query import (
    export_catalog,
    export_observations,
    export_table,
    main,
    list_options,
    query_catalog,
    query_observations,
    query_table,
)


@pytest.fixture
def connection():
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE SCHEMA metadata")
        con.execute("CREATE SCHEMA analytics")
        con.execute(
            "CREATE TABLE metadata.variable_dictionary ("
            "variable_id VARCHAR, unit VARCHAR, unit_simplified VARCHAR, "
            "temporal_type VARCHAR, temporal_type_note VARCHAR, monetary_basis VARCHAR)"
        )
        con.executemany(
            "INSERT INTO metadata.variable_dictionary VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("listings", "homes", "count", "stock", "Provider snapshot", "not_applicable"),
                ("sales", "homes", "count", "flow", "Completed monthly transactions", "not_applicable"),
                ("debt", "inflation_adjusted_dollars", "$", "stock", "Annual benchmark", "real"),
                ("usps_vacancy", "addresses", "count", "stock", "Restricted", "not_applicable"),
            ],
        )
        con.execute(
            "CREATE TABLE analytics.fact_housing_observation ("
            "observation_date DATE, period_start DATE, period_end DATE, "
            "geo_id VARCHAR, county_fips VARCHAR, variable_id VARCHAR, value DOUBLE, "
            "unit VARCHAR, source_vintage VARCHAR, retrieved_at VARCHAR, "
            "source_filename VARCHAR, checksum_sha256 VARCHAR)"
        )
        con.executemany(
            "INSERT INTO analytics.fact_housing_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-01-31", "2026-01-01", "2026-01-31", "ZIP_00501", "06037", "listings", 12.5, "homes", "2026-02", "2026-02-02T12:00:00Z", "official.csv", "abc"),
                ("2026-02-28", "2026-02-01", "2026-02-28", "ZIP_00501", "06037", "listings", None, "homes", "2026-03", "2026-03-02T12:00:00Z", "official.csv", "def"),
                ("2026-01-31", "2026-01-01", "2026-01-31", "COUNTY_06037", "06037", "sales", 20.0, "homes", "2026-02", "2026-02-02T12:00:00Z", "official.csv", "abc"),
            ],
        )
        for table in (
            "housing_finance_benchmark", "housing_finance_monthly_hist", "housing_finance_monthly_rt"
        ):
            con.execute(
                f"CREATE TABLE analytics.{table} (variable_id VARCHAR, geo_id VARCHAR, "
                "county_fips VARCHAR, month DATE, reference_date DATE, value DOUBLE, "
                "unit VARCHAR, source_period_start DATE, source_period_end DATE, "
                "vintage_id VARCHAR, source_filename VARCHAR)"
            )
        con.execute(
            "INSERT INTO analytics.housing_finance_benchmark VALUES "
            "('debt', 'COUNTY_06037', '06037', NULL, DATE '2022-07-01', 500, "
            "'inflation_adjusted_dollars', DATE '2020-01-01', DATE '2024-12-31', '2024_ACS5', 'acs.dat')"
        )
        for table in ("housing_finance_monthly_hist", "housing_finance_monthly_rt"):
            con.executemany(
                f"INSERT INTO analytics.{table} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("debt", "COUNTY_06037", "06037", "2026-01-31", "2022-07-01", 500, "inflation_adjusted_dollars", "2020-01-01", "2024-12-31", "vintage_1", "acs.dat"),
                    ("debt", "COUNTY_06037", "06037", "2026-01-31", "2022-07-01", 510, "inflation_adjusted_dollars", "2020-01-01", "2024-12-31", "vintage_2", "acs.dat"),
                ],
            )
        yield con


def test_catalog_filters_use_metadata_and_bound_values(connection) -> None:
    result = query_catalog(connection, units=["count"], temporal_types=["flow"])
    assert result["variable_id"].tolist() == ["sales"]
    assert result["unit"].tolist() == ["homes"]
    assert query_catalog(connection, units=["count' OR 1=1 --"]).empty
    assert len(query_catalog(connection)) == 4


def test_observation_filters_preserve_values_nulls_and_native_metadata(connection) -> None:
    result = query_observations(
        connection, variable_ids=["listings"], units=["count"], temporal_types=["stock"],
        geo_ids=["ZIP_00501"], start_date="2026-01-31", end_date="2026-02-28",
    )
    assert result["value"].iloc[0] == 12.5
    assert pd.isna(result["value"].iloc[1])
    assert result["unit"].tolist() == ["homes", "homes"]
    assert result["county_fips"].tolist() == ["06037", "06037"]
    assert result["source_vintage"].tolist() == ["2026-02", "2026-03"]
    assert result["checksum_sha256"].tolist() == ["abc", "def"]
    assert result["period_start"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-01", "2026-02-01"]
    assert query_observations(connection, geo_ids=["ZIP_00501' OR 1=1 --"]).empty


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_download_preserves_leading_zeros_and_labels(connection, tmp_path, suffix) -> None:
    output = tmp_path / f"stock.{suffix}"
    assert export_observations(connection, output, variable_ids=["listings"]) == 2
    if suffix == "csv":
        with output.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["county_fips"] == "06037"
        result = pd.read_csv(output, dtype={"county_fips": "string"})
    else:
        result = pd.read_parquet(output)
        assert result["county_fips"].tolist() == ["06037", "06037"]
    assert result["geo_id"].tolist() == ["ZIP_00501", "ZIP_00501"]
    assert result["unit_simplified"].tolist() == ["count", "count"]
    assert result["temporal_type"].tolist() == ["stock", "stock"]
    assert result["value"].iloc[0] == 12.5
    assert pd.isna(result["value"].iloc[1])


def test_finance_date_filters_keep_benchmark_interval_and_all_vintages(connection) -> None:
    benchmark = query_observations(
        connection, dataset="version-a", start_date="2022-07-01", end_date="2022-07-01"
    )
    assert len(benchmark) == 1
    assert pd.isna(benchmark["month"].iloc[0])
    assert benchmark["source_period_start"].iloc[0] == pd.Timestamp("2020-01-01")
    assert benchmark["source_period_end"].iloc[0] == pd.Timestamp("2024-12-31")
    for dataset in ("version-b", "version-c"):
        result = query_observations(
            connection, dataset=dataset, start_date="2026-01-31", end_date="2026-01-31"
        )
        assert set(result["vintage_id"]) == {"vintage_1", "vintage_2"}
        assert result["unit_simplified"].tolist() == ["$", "$"]
        assert result["monetary_basis"].tolist() == ["real", "real"]


@pytest.mark.parametrize(
    "filters, message",
    [
        ({"dataset": "main; DROP SCHEMA analytics CASCADE"}, "Unknown dataset"),
        ({"start_date": "2026-02-30"}, "Invalid calendar date"),
        ({"start_date": "20260101"}, "YYYY-MM-DD"),
        ({"start_date": "2026-02-01", "end_date": "2026-01-01"}, "on or before"),
    ],
)
def test_invalid_selectors_stop_before_creating_output(connection, tmp_path, filters, message) -> None:
    output = tmp_path / "download.csv"
    with pytest.raises(ValueError, match=message):
        export_observations(connection, output, **filters)
    assert not output.exists()


def test_metadata_structural_errors_stop_queries(connection) -> None:
    connection.execute("INSERT INTO metadata.variable_dictionary SELECT * FROM metadata.variable_dictionary WHERE variable_id='sales'")
    with pytest.raises(ValueError, match="one non-null row"):
        query_observations(connection)
    connection.execute("DELETE FROM metadata.variable_dictionary WHERE variable_id='sales'")
    with pytest.raises(ValueError, match="no dictionary classification"):
        query_observations(connection)


def test_stale_dictionary_error_explains_rebuild(connection) -> None:
    connection.execute("ALTER TABLE metadata.variable_dictionary DROP temporal_type")
    with pytest.raises(ValueError, match="rebuild the database"):
        query_catalog(connection)


def test_restricted_observations_cannot_enter_download(connection, tmp_path) -> None:
    connection.execute(
        "INSERT INTO analytics.fact_housing_observation "
        "SELECT observation_date, period_start, period_end, geo_id, county_fips, "
        "'usps_vacancy', value, unit, source_vintage, retrieved_at, source_filename, checksum_sha256 "
        "FROM analytics.fact_housing_observation LIMIT 1"
    )
    output = tmp_path / "restricted.csv"
    with pytest.raises(ValueError, match="restricted HUD-USPS"):
        export_observations(connection, output, variable_ids=["usps_vacancy"])
    assert not output.exists()
    # Metadata remains discoverable without disclosing licensed observations.
    assert len(query_catalog(connection, variable_ids=["usps_vacancy"])) == 1


def test_exports_never_replace_existing_files(connection, tmp_path) -> None:
    output = tmp_path / "archive.csv"
    original = b"immutable existing file\n"
    output.write_bytes(original)
    with pytest.raises(FileExistsError, match="choose a new filename"):
        export_observations(connection, output)
    with pytest.raises(FileExistsError, match="choose a new filename"):
        export_catalog(query_catalog(connection), output)
    assert output.read_bytes() == original


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_empty_download_retains_schema(connection, tmp_path, suffix) -> None:
    output = tmp_path / f"empty.{suffix}"
    assert export_observations(connection, output, start_date="2099-01-01") == 0
    frame = pd.read_csv(output) if suffix == "csv" else pd.read_parquet(output)
    assert frame.empty
    assert {"variable_id", "unit", "unit_simplified", "temporal_type"} <= set(frame)


def test_cli_database_is_read_only(tmp_path: Path, capsys) -> None:
    database = tmp_path / "missing.duckdb"
    with pytest.raises(SystemExit) as error:
        main(["--database", str(database), "catalog"])
    assert error.value.code == 2
    assert "Database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_explicit_variable_absent_from_dataset_is_not_silent_empty_download(connection, tmp_path) -> None:
    with pytest.raises(ValueError, match="no observations in dataset"):
        export_observations(connection, tmp_path / "wrong.csv", variable_ids=["debt"])
    assert not (tmp_path / "wrong.csv").exists()


def test_restricted_source_guard_survives_variable_rename(connection, tmp_path) -> None:
    connection.execute("ALTER TABLE analytics.fact_housing_observation ADD source_id VARCHAR")
    connection.execute("UPDATE analytics.fact_housing_observation SET source_id='hud_usps_ncwm_quarterly' WHERE variable_id='listings'")
    with pytest.raises(ValueError, match="restricted HUD-USPS"):
        export_observations(connection, tmp_path / "renamed.csv", variable_ids=["listings"])
    assert not (tmp_path / "renamed.csv").exists()


@pytest.fixture
def wide_connection(connection):
    connection.executemany(
        "INSERT INTO metadata.variable_dictionary VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("zori_smoothed_sa", "USD/month", "$", "period_statistic", "Smoothed rent", "nominal"),
            ("redfin_zip_homes_sold_rolling_3m", "count", "count", "rolling_window", "Rolling three months", "not_applicable"),
            ("new_apartments_completed_proxy_central", "units", "count", "flow", "Constructed completion proxy", "not_applicable"),
        ],
    )
    connection.execute(
        "CREATE TABLE analytics.county_monthly_panel AS SELECT DATE '2026-01-31' date, "
        "'06037' county_fips, 2000.0 zori, 14.0 new_apartments_completed_proxy_central, "
        "'full_lag_window' new_apartments_completed_proxy_central_quality_flag, "
        "'2026-02' latest_source_vintage, 99.0 unknown_metric"
    )
    connection.execute(
        "CREATE TABLE analytics.zip_monthly_panel AS SELECT DATE '2026-01-31' date, "
        "'00501' postal_zip, 20.0 homes_sold, 'rolling_3m' redfin_frequency, 3 redfin_window_months"
    )
    return connection


def test_table_download_includes_panel_only_variable_and_companion_dictionary(wide_connection, tmp_path) -> None:
    output = tmp_path / "completions.parquet"
    count, metadata_path, omitted = export_table(
        wide_connection, output, dataset="county-monthly", temporal_types=["flow"]
    )
    assert count == 1
    assert metadata_path.name == "completions.variables.csv"
    assert omitted == ["unknown_metric"]
    frame = pd.read_parquet(output)
    assert frame["county_fips"].tolist() == ["06037"]
    assert frame["new_apartments_completed_proxy_central"].tolist() == [14.0]
    assert frame["new_apartments_completed_proxy_central_quality_flag"].tolist() == ["full_lag_window"]
    assert frame["latest_source_vintage"].tolist() == ["2026-02"]
    assert "zori" not in frame
    dictionary = pd.read_csv(metadata_path)
    assert dictionary["variable_id"].tolist() == ["new_apartments_completed_proxy_central"]
    assert dictionary["temporal_type"].tolist() == ["flow"]


def test_wide_aliases_preserve_zori_and_redfin_rolling_window_identity(wide_connection) -> None:
    rent, rent_dictionary = query_table(
        wide_connection, dataset="county-monthly", variable_ids=["zori_smoothed_sa"]
    )
    assert rent["zori"].tolist() == [2000.0]
    assert rent_dictionary["table_column"].tolist() == ["zori"]
    assert rent_dictionary["variable_id"].tolist() == ["zori_smoothed_sa"]
    sales, sales_dictionary = query_table(
        wide_connection, dataset="zip-monthly", temporal_types=["rolling_window"]
    )
    assert sales["postal_zip"].tolist() == ["00501"]
    assert sales["homes_sold"].tolist() == [20.0]
    assert sales["redfin_window_months"].tolist() == [3]
    assert sales_dictionary["variable_id"].tolist() == ["redfin_zip_homes_sold_rolling_3m"]


def test_wide_filters_fail_for_missing_variables_and_empty_column_selection(wide_connection) -> None:
    with pytest.raises(ValueError, match="absent from table"):
        query_table(wide_connection, dataset="zip-monthly", variable_ids=["sales"])
    with pytest.raises(ValueError, match="Unknown temporal_type"):
        query_table(wide_connection, dataset="county-monthly", temporal_types=["flow' OR 1=1 --"])
    with pytest.raises(ValueError, match="No classified variable columns"):
        query_table(wide_connection, dataset="county-monthly", temporal_types=["cumulative"])
    with pytest.raises(ValueError, match="Unknown table dataset"):
        query_table(wide_connection, dataset="county_monthly_panel; DROP SCHEMA analytics CASCADE")


def test_table_preflights_both_output_paths(wide_connection, tmp_path) -> None:
    metadata = tmp_path / "stock.variables.csv"
    metadata.write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_table(wide_connection, tmp_path / "stock.csv", dataset="county-monthly")
    assert metadata.read_text(encoding="utf-8") == "preserved"
    assert not (tmp_path / "stock.csv").exists()


def test_wide_filters_retain_observation_identity_and_provenance(connection) -> None:
    connection.execute(
        "CREATE TABLE analytics.la_zip_market_regime_transitions_quarterly AS "
        "SELECT DATE '2026-03-31' quarter_end, DATE '2025-12-31' previous_quarter_end, "
        "'1' from_regime_id, '2' to_regime_id, 'cold' from_regime_label, 'hot' to_regime_label, "
        "'06037' county_fips, 'model_v1' market_regime_model_version, 'abc' checksum_sha256, 10.0 sales"
    )
    frame, metadata = query_table(
        connection, dataset="zip-regime-transitions-quarterly", temporal_types=["flow"]
    )
    assert {"previous_quarter_end", "from_regime_id", "to_regime_id", "from_regime_label", "to_regime_label", "county_fips", "market_regime_model_version", "checksum_sha256"} <= set(frame)
    assert metadata["table_column"].tolist() == ["sales"]
    connection.execute(
        "CREATE TABLE analytics.coc_annual_controls AS SELECT DATE '2025-12-31' date, "
        "'COC_CA_600' geo_id, 'CA-600' coc_id, '06' state_fips, 10.0 listings"
    )
    frame, _ = query_table(connection, dataset="coc-annual", temporal_types=["stock"])
    assert frame["coc_id"].tolist() == ["CA-600"]
    assert frame["state_fips"].tolist() == ["06"]


@pytest.fixture
def scenario_connection(connection):
    ids = ["closing_cost_loan_3pct", "closing_cost_loan_6pct", "estimated_mortgage_amount_scenario", "total_cash_to_close_scenario", "down_payment_10pct", "down_payment_20pct"]
    connection.executemany(
        "INSERT INTO metadata.variable_dictionary VALUES (?, ?, ?, ?, ?, ?)",
        [(variable, "current_dollars", "USD", "period_statistic", "Constructed scenario", "nominal") for variable in ids],
    )
    connection.execute(
        "CREATE TABLE analytics.homebuying_cost_scenarios (date DATE, geo_id VARCHAR, "
        "county_fips VARCHAR, median_sale_price DOUBLE, price_source_variable_id VARCHAR, "
        "down_payment_variable_id VARCHAR, down_payment_share DOUBLE, down_payment_amount DOUBLE, "
        "estimated_mortgage_amount_scenario DOUBLE, closing_cost_variable_id VARCHAR, "
        "closing_cost_basis VARCHAR, closing_cost_share DOUBLE, closing_cost_amount DOUBLE, "
        "total_cash_to_close_scenario DOUBLE, scenario_name VARCHAR, is_interpolated BOOLEAN)"
    )
    connection.executemany(
        "INSERT INTO analytics.homebuying_cost_scenarios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-01-31", "COUNTY_06037", "06037", 100000, "sales", "down_payment_10pct", 0.1, 10000, 90000, "closing_cost_loan_3pct", "loan_amount", 0.03, 2700, 12700, "down10_close3", False),
            ("2026-01-31", "COUNTY_06037", "06037", 100000, "sales", "down_payment_20pct", 0.2, 20000, 80000, "closing_cost_loan_6pct", "loan_amount", 0.06, 4800, 24800, "down20_close6", False),
        ],
    )
    return connection


def test_scenarios_select_dynamic_row_ids_and_preserve_assumptions(scenario_connection, tmp_path) -> None:
    output = tmp_path / "closing.parquet"
    count, sidecar, omitted = export_table(
        scenario_connection, output, dataset="homebuying-scenarios", variable_ids=["closing_cost_loan_3pct"]
    )
    assert count == 1
    assert omitted == []
    frame = pd.read_parquet(output)
    assert frame["closing_cost_amount"].tolist() == [2700.0]
    assert frame["closing_cost_basis"].tolist() == ["loan_amount"]
    assert frame["down_payment_share"].tolist() == [0.1]
    assert frame["county_fips"].tolist() == ["06037"]
    assert "total_cash_to_close_scenario" not in frame
    labels = pd.read_csv(sidecar)
    assert labels["variable_id"].tolist() == ["closing_cost_loan_3pct"]
    assert labels["table_column"].tolist() == ["closing_cost_amount"]
    assert labels["row_identifier_column"].tolist() == ["closing_cost_variable_id"]
    assert labels["row_identifier_value"].tolist() == ["closing_cost_loan_3pct"]


def test_scenario_groups_intersect_without_changing_monetary_observations(scenario_connection) -> None:
    frame, labels = query_table(
        scenario_connection, dataset="homebuying-scenarios",
        variable_ids=["down_payment_20pct", "closing_cost_loan_6pct", "total_cash_to_close_scenario"],
    )
    assert len(frame) == 1
    assert frame["down_payment_amount"].tolist() == [20000.0]
    assert frame["closing_cost_amount"].tolist() == [4800.0]
    assert frame["total_cash_to_close_scenario"].tolist() == [24800.0]
    assert set(labels["variable_id"]) == {"down_payment_20pct", "closing_cost_loan_6pct", "total_cash_to_close_scenario"}
    direct, _ = query_table(
        scenario_connection, dataset="homebuying-scenarios", variable_ids=["estimated_mortgage_amount_scenario"]
    )
    assert direct["estimated_mortgage_amount_scenario"].tolist() == [90000.0, 80000.0]
    with pytest.raises(ValueError, match="absent from homebuying scenarios"):
        query_table(scenario_connection, dataset="homebuying-scenarios", variable_ids=["listings"])


def test_scenario_context_cannot_bypass_restricted_export_guard(scenario_connection, tmp_path) -> None:
    scenario_connection.execute("ALTER TABLE analytics.homebuying_cost_scenarios ADD usps_vacancy DOUBLE")
    with pytest.raises(ValueError, match="restricted HUD-USPS"):
        export_table(
            scenario_connection, tmp_path / "scenario.csv", dataset="homebuying-scenarios",
            variable_ids=["closing_cost_loan_3pct"],
        )
    assert not (tmp_path / "scenario.csv").exists()
    assert not (tmp_path / "scenario.variables.csv").exists()


def test_wide_frequency_filters_native_cadence_without_dropping_monthly_inputs(connection) -> None:
    connection.execute("ALTER TABLE metadata.variable_dictionary ADD final_frequency VARCHAR")
    connection.execute("UPDATE metadata.variable_dictionary SET final_frequency='monthly'")
    connection.execute(
        "CREATE TABLE analytics.zip_market_regime_quarterly AS "
        "SELECT DATE '2026-03-31' quarter_end, '00501' postal_zip, 14.0 sales"
    )
    assert list_options(
        connection, "frequency", kind="table", dataset="zip-regimes-quarterly"
    )["value"].tolist() == ["quarterly"]
    unfiltered, unfiltered_metadata = query_table(connection, dataset="zip-regimes-quarterly")
    filtered, filtered_metadata = query_table(
        connection, dataset="zip-regimes-quarterly", frequencies=["quarterly"]
    )
    pd.testing.assert_frame_equal(filtered, unfiltered)
    pd.testing.assert_frame_equal(filtered_metadata, unfiltered_metadata)
    mismatch, metadata = query_table(
        connection, dataset="zip-regimes-quarterly", frequencies=["monthly"]
    )
    assert mismatch.empty
    assert metadata["variable_id"].tolist() == ["sales"]
    assert len(query_catalog(connection, frequencies=["monthly"])) == 4
