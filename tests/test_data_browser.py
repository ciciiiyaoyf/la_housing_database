"""The browser uses the same read-only filters and downloads as the CLI."""

from __future__ import annotations

import csv
import io
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb
import pandas as pd
import pytest

from src.common.config import ROOT, load_config
from src.reporting.data_browser import BrowserServer


@pytest.fixture
def browser(tmp_path):
    database = tmp_path / "housing.duckdb"
    dictionary = pd.DataFrame(load_config().variables)
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE SCHEMA metadata")
        con.execute("CREATE SCHEMA analytics")
        con.register("dictionary_frame", dictionary)
        con.execute("CREATE TABLE metadata.variable_dictionary AS SELECT * FROM dictionary_frame")
        con.execute(
            "CREATE TABLE analytics.county_monthly_panel AS "
            "SELECT DATE '2026-01-31' date, '06037' county_fips, "
            "12.0 permits_total, 2100.0 zori, 'fixture-vintage' latest_source_vintage "
            "UNION ALL SELECT DATE '2026-01-31', '06059', 22.0, 2200.0, 'fixture-vintage'"
        )
        con.execute(
            "CREATE TABLE analytics.fact_housing_observation AS "
            "SELECT DATE '2026-01-31' observation_date, 'COUNTY_06037' geo_id, "
            "'zori_smoothed_sa' variable_id, 2100.0 AS \"value\", 'USD/month' unit, "
            "'fixture-vintage' source_vintage, 'source.csv' source_filename, 'abc123' checksum_sha256 "
            "UNION ALL SELECT DATE '2026-01-31', 'ZIP_00501', 'zori_smoothed_sa', 2150.0, 'USD/month', 'fixture-vintage', 'source.csv', 'abc123' "
            "UNION ALL SELECT DATE '2026-01-31', 'COUNTY_06037', 'permits_total', 12.0, 'units', 'fixture-vintage', 'permits.csv', 'def456'"
        )
        con.execute(
            "CREATE TABLE analytics.homebuying_cost_scenarios AS "
            "SELECT DATE '2026-01-31' date, 'COUNTY_06037' geo_id, '06037' county_fips, "
            "'county' geography_level, 'median_sale_price' price_source_variable_id, 100000.0 median_sale_price, "
            "'down_payment_10pct' down_payment_variable_id, 0.1 down_payment_share, 10000.0 down_payment_amount, "
            "90000.0 estimated_mortgage_amount_scenario, 'closing_cost_loan_3pct' closing_cost_variable_id, "
            "'loan_amount' closing_cost_basis, 0.03 closing_cost_share, 2700.0 closing_cost_amount, "
            "12700.0 total_cash_to_close_scenario, 'down10_close3' scenario_name "
            "UNION ALL SELECT DATE '2026-01-31', 'ZIP_00501', '06037', 'postal_zip', "
            "'redfin_zip_median_sale_price_nsa_rolling_3m', 110000.0, 'down_payment_10pct', 0.1, 11000.0, "
            "99000.0, 'closing_cost_loan_3pct', 'loan_amount', 0.03, 2970.0, 13970.0, 'down10_close3'"
        )
    server = BrowserServer(("127.0.0.1", 0), database, ROOT / "scripts/data_browser.html")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", database
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def read_json(url):
    with urlopen(url, timeout=15) as response:
        return json.load(response)


def test_preview_and_csv_use_same_geography_and_domain_filters(browser):
    address, database = browser
    query = "?kind=table&dataset=county-monthly&geo_id=COUNTY_06037&domain=construction&frequency=monthly"
    result = read_json(address + "/api/preview" + query)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["county_fips"] == "06037"
    assert result["rows"][0]["permits_total"] == 12.0
    assert "zori" not in result["columns"]
    with urlopen(address + "/api/download" + query, timeout=15) as response:
        assert response.headers["Content-Disposition"] == 'attachment; filename="county-monthly.csv"'
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["county_fips"] == "06037"
    assert float(rows[0]["permits_total"]) == 12.0
    assert rows[0]["latest_source_vintage"] == "fixture-vintage"
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM analytics.county_monthly_panel").fetchone()[0] == 2


def test_definition_download_matches_selected_columns(browser):
    address, _ = browser
    with urlopen(address + "/api/definitions?kind=table&dataset=county-monthly&domain=construction", timeout=15) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert [row["variable_id"] for row in rows] == ["permits_total"]
    assert rows[0]["table_column"] == "permits_total"
    assert rows[0]["temporal_type"] == "flow"


def test_options_and_page_are_self_contained(browser):
    address, _ = browser
    options = read_json(address + "/api/options?kind=table&dataset=county-monthly&field=geo-id")
    assert {row["value"] for row in options["options"]} == {"COUNTY_06037", "COUNTY_06059"}
    with urlopen(address + "/", timeout=15) as response:
        assert response.headers["Content-Type"].startswith("text/html")
        assert b"Download CSV" in response.read()


def test_invalid_filters_and_remote_origins_fail_without_download(browser):
    address, _ = browser
    for request, expected in (
        (address + "/api/download?dataset=not-a-table", 400),
        (address + "/api/preview?proxy=unknown", 400),
        (Request(address + "/api/datasets", headers={"Origin": "https://example.com"}), 403),
        (Request(address + "/api/datasets", headers={"Host": "example.com"}), 403),
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=15)
        assert error.value.code == expected
        assert "error" in json.load(error.value)


def test_variable_options_only_offer_selected_dataset_ids_and_human_labels(browser):
    address, _ = browser
    result = read_json(address + "/api/options?kind=table&dataset=county-monthly&field=variable")
    options = {item["value"]: item["label"] for item in result["options"]}
    assert set(options) == {"permits_total", "zori_smoothed_sa"}
    assert options["zori_smoothed_sa"].endswith("(zori_smoothed_sa)")
    assert options["zori_smoothed_sa"] != "zori_smoothed_sa (zori_smoothed_sa)"
    assert "homes_sold" not in options


def test_source_variable_options_use_actual_geography_rows(browser):
    address, _ = browser
    result = read_json(address + "/api/options?kind=observations&dataset=main&field=variable&geography=postal_zip")
    assert [item["value"] for item in result["options"]] == ["zori_smoothed_sa"]
    empty = read_json(address + "/api/options?kind=observations&dataset=main&field=variable&geography=zcta")
    assert empty["options"] == []


def test_single_variable_download_and_definitions_match_preview(browser):
    address, _ = browser
    query = "?kind=table&dataset=county-monthly&variable=zori_smoothed_sa&geo_id=COUNTY_06037"
    result = read_json(address + "/api/preview" + query)
    assert result["metadata_count"] == 1
    assert result["rows"][0]["zori"] == 2100.0
    assert "permits_total" not in result["columns"]
    with urlopen(address + "/api/download" + query, timeout=15) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["county_fips"] == "06037"
    assert rows[0]["zori"] == "2100.0"
    assert "permits_total" not in rows[0]
    with urlopen(address + "/api/definitions" + query, timeout=15) as response:
        definitions = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert [row["variable_id"] for row in definitions] == ["zori_smoothed_sa"]
    assert definitions[0]["table_column"] == "zori"


def test_single_source_variable_csv_retains_native_id_and_provenance(browser):
    address, _ = browser
    query = "?kind=observations&dataset=main&variable=zori_smoothed_sa&geography=postal_zip"
    with urlopen(address + "/api/download" + query, timeout=15) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["variable_id"] == "zori_smoothed_sa"
    assert rows[0]["geo_id"] == "ZIP_00501"
    assert rows[0]["checksum_sha256"] == "abc123"
    with urlopen(address + "/api/definitions" + query, timeout=15) as response:
        definitions = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert [row["variable_id"] for row in definitions] == ["zori_smoothed_sa"]


def test_scenario_variable_options_follow_row_key_ids_and_geography(browser):
    address, _ = browser
    result = read_json(address + "/api/options?kind=table&dataset=homebuying-scenarios&field=variable&geography=postal_zip")
    ids = [item["value"] for item in result["options"]]
    assert len(ids) == len(set(ids))
    assert "closing_cost_loan_3pct" in ids
    assert "closing_cost_loan_6pct" not in ids
    assert "redfin_zip_median_sale_price_nsa_rolling_3m" in ids
    assert "median_sale_price" not in ids


def test_single_scenario_variable_csv_and_definitions_use_row_specific_id(browser):
    address, _ = browser
    variable = "redfin_zip_median_sale_price_nsa_rolling_3m"
    query = f"?kind=table&dataset=homebuying-scenarios&geography=postal_zip&variable={variable}"
    with urlopen(address + "/api/download" + query, timeout=15) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["geo_id"] == "ZIP_00501"
    assert rows[0]["price_source_variable_id"] == variable
    assert float(rows[0]["median_sale_price"]) == 110000.0
    assert "down_payment_amount" not in rows[0]
    assert "closing_cost_amount" not in rows[0]
    with urlopen(address + "/api/definitions" + query, timeout=15) as response:
        definitions = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert [row["variable_id"] for row in definitions] == [variable]
    assert definitions[0]["row_identifier_column"] == "price_source_variable_id"
    assert definitions[0]["row_identifier_value"] == variable
