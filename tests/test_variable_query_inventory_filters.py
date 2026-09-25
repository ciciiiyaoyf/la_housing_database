"""Inventory filters must preserve native rows and distinguish metadata scope."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.reporting.query import (
    export_observations,
    export_table,
    main,
    query_catalog,
    query_observations,
    query_table,
)


def _put(connection, name: str, frame: pd.DataFrame) -> None:
    connection.register("inventory_fixture", frame)
    connection.execute(f"CREATE TABLE {name} AS SELECT * FROM inventory_fixture")
    connection.unregister("inventory_fixture")


@pytest.fixture
def inventory_database(tmp_path: Path) -> Path:
    database = tmp_path / "inventory.duckdb"
    definitions = [
        ("shared_level", "rental_market", "county|postal_zip|zcta|state|national", "monthly|annual", "monthly|annual", "False", "false"),
        ("sales", "for_sale_market", "county|postal_zip", "monthly", "monthly", "true", "True"),
        ("unknown_flags", "macro", "national", "monthly", "monthly", None, None),
        ("blank_flags", "macro", "national", "monthly", "monthly", "", ""),
        ("debt_benchmark", "finance", "county", "annual", "annual", "False", "True"),
        ("debt_monthly_hist", "finance", "county", "mixed source frequencies", "monthly", "False", "True"),
        ("debt_monthly_rt", "finance", "county", "mixed source frequencies", "monthly", "False", "True"),
        ("debt_monthly_history", "finance_review", "county", "monthly", "monthly", "False", "false"),
        ("country_control", "macro", "country", "daily", "monthly", "False", "True"),
        ("annual_control", "property_insurance", "state", "annual", "monthly", "False", "True"),
    ]
    rows = []
    for variable, domain, geography, native, final, proxy, constructed in definitions:
        rows.append({
            "variable_id": variable, "display_name": variable,
            "unit": "fraction", "unit_simplified": "share",
            "temporal_type": "flow" if variable == "sales" else "period_statistic",
            "temporal_type_note": "Synthetic integration fixture; values remain unchanged.",
            "monetary_basis": "not_applicable", "geography": geography,
            "native_frequency": native, "final_frequency": final,
            "data_domain": domain, "is_proxy": proxy, "is_constructed": constructed,
        })
    observations = []
    source_rows = [
        ("shared_level", "COUNTY_06037", "monthly", "monthly", -0.25),
        ("shared_level", "ZIP_00501", "monthly", "monthly", 1.5),
        ("shared_level", "ZCTA_00501", "annual_5_year_estimate", "annual", 2.5),
        ("shared_level", "STATE_06", "monthly", "monthly", 3.5),
        ("shared_level", "US", "monthly", "monthly", 4.5),
        ("sales", "COUNTY_06037", "monthly", "monthly", 12.5),
        ("sales", "ZIP_00501", "monthly", "monthly", 13.5),
        ("annual_control", "STATE_06", "annual", "monthly", 944.142971801626),
        ("country_control", "US", "daily", "monthly", 5.25),
        ("unknown_flags", "US", "monthly", "monthly", None),
        ("blank_flags", "US", "monthly", "monthly", None),
    ]
    for variable, geo_id, native, final, value in source_rows:
        observations.append({
            "observation_date": pd.Timestamp("2025-12-31"),
            "period_start": pd.Timestamp("2025-01-01" if native.startswith("annual") else "2025-12-01"),
            "period_end": pd.Timestamp("2025-12-31"),
            "geo_id": geo_id, "variable_id": variable, "value": value,
            "native_frequency": native, "final_frequency": final, "unit": "fraction",
            "source_vintage": "fixture-2026", "source_filename": "fixture.csv",
            "retrieved_at": "2026-01-02T03:04:05+00:00", "checksum_sha256": "c" * 64,
        })
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA analytics")
        _put(connection, "metadata.variable_dictionary", pd.DataFrame(rows))
        _put(connection, "analytics.fact_housing_observation", pd.DataFrame(observations))
        for table, key, identifiers, variable in (
            ("county_monthly_panel", "county_fips", ["06037", "06059"], "shared_level"),
            ("zip_monthly_panel", "postal_zip", ["00501", "90001"], "shared_level"),
            ("zcta_annual_controls", "zcta", ["00501", "90001"], "shared_level"),
        ):
            _put(connection, "analytics." + table, pd.DataFrame({
                "date": pd.to_datetime(["2025-12-31"] * 2), key: identifiers,
                variable: [-0.25, 1.5], "source_vintage": ["fixture-2026"] * 2,
                "source_filename": ["fixture.csv"] * 2, "checksum_sha256": ["c" * 64] * 2,
            }))
        _put(connection, "analytics.state_monthly_controls", pd.DataFrame({
            "date": pd.to_datetime(["2025-12-31"] * 2), "geo_id": ["STATE_06", "STATE_36"],
            "state_fips": ["06", "36"], "annual_control": [944.142971801626, 1200.0],
            "native_frequency": ["annual"] * 2, "final_frequency": ["monthly"] * 2,
            "source_annual_date": pd.to_datetime(["2025-12-31"] * 2),
            "source_vintage": ["fixture-2026"] * 2, "source_filename": ["fixture.csv"] * 2,
            "checksum_sha256": ["c" * 64] * 2, "is_carried_annual_control": [True] * 2,
        }))
    return database


@pytest.fixture
def inventory_connection(inventory_database):
    with duckdb.connect(str(inventory_database), read_only=True) as connection:
        yield connection


def test_catalog_matches_exact_inventory_tokens_and_combines_filters(inventory_connection) -> None:
    result = query_catalog(
        inventory_connection, geographies=["postal_zip"], frequencies=["annual"],
        domains=["rental_market"], proxy=False, constructed=False,
    )
    assert result["variable_id"].tolist() == ["shared_level"]
    assert query_catalog(inventory_connection, domains=["finance"])["variable_id"].tolist() == [
        "debt_benchmark", "debt_monthly_hist", "debt_monthly_rt",
    ]
    assert query_catalog(inventory_connection, native_frequencies=["annual"], frequencies=["monthly"])["variable_id"].tolist() == [
        "annual_control", "shared_level",
    ]


def test_boolean_false_filters_do_not_treat_unknown_as_false(inventory_connection) -> None:
    false_rows = query_catalog(inventory_connection, proxy=False, constructed=False)
    assert set(false_rows["variable_id"]) == {"debt_monthly_history", "shared_level"}
    assert query_catalog(inventory_connection, proxy=True)["variable_id"].tolist() == ["sales"]
    unfiltered = set(query_catalog(inventory_connection)["variable_id"])
    assert {"unknown_flags", "blank_flags"} <= unfiltered
    assert {"unknown_flags", "blank_flags"}.isdisjoint(
        query_catalog(inventory_connection, proxy=False)["variable_id"]
    )


def test_country_alias_and_national_match_the_same_exact_geography(inventory_connection) -> None:
    canonical = query_catalog(inventory_connection, geographies=["national"])
    alias = query_catalog(inventory_connection, geographies=["country"])
    assert set(canonical["variable_id"]) == {"shared_level", "country_control", "unknown_flags", "blank_flags"}
    assert alias["variable_id"].tolist() == canonical["variable_id"].tolist()
    observations = query_observations(inventory_connection, geographies=["country"])
    assert set(observations["geo_id"]) == {"US"}


@pytest.mark.parametrize("layer, identifiers", [
    ("Version A", {"debt_benchmark"}),
    ("Version B", {"debt_monthly_hist"}),
    ("Version C", {"debt_monthly_rt"}),
    ("General", {"shared_level", "sales", "unknown_flags", "blank_flags", "debt_monthly_history", "country_control", "annual_control"}),
])
def test_layer_filters_use_exact_product_families(inventory_connection, layer, identifiers) -> None:
    assert set(query_catalog(inventory_connection, layers=[layer])["variable_id"]) == identifiers


@pytest.mark.parametrize("geography, expected", [
    ("county", {"COUNTY_06037"}), ("postal_zip", {"ZIP_00501"}),
    ("zcta", {"ZCTA_00501"}), ("state", {"STATE_06"}), ("national", {"US"}),
])
def test_observation_geography_filters_match_rows_not_variable_scope(inventory_connection, geography, expected) -> None:
    result = query_observations(inventory_connection, variable_ids=["shared_level"], geographies=[geography])
    assert set(result["geo_id"]) == expected


def test_observation_frequency_uses_native_rows_and_preserves_annual_controls(inventory_connection) -> None:
    annual = query_observations(inventory_connection, variable_ids=["shared_level"], frequencies=["annual"])
    assert annual["geo_id"].tolist() == ["ZCTA_00501"]
    carried = query_observations(
        inventory_connection, frequencies=["monthly"], native_frequencies=["annual"], geographies=["state"],
    )
    assert carried["variable_id"].tolist() == ["annual_control"]
    assert carried["value"].tolist() == [944.142971801626]
    assert carried["native_frequency"].tolist() == ["annual"]
    assert carried["final_frequency"].tolist() == ["monthly"]
    assert carried["period_start"].dt.strftime("%Y-%m-%d").tolist() == ["2025-01-01"]
    assert carried["checksum_sha256"].tolist() == ["c" * 64]


@pytest.mark.parametrize("dataset, geography, geo_id, key, expected", [
    ("county-monthly", "county", "COUNTY_06037", "county_fips", "06037"),
    ("zip-monthly", "postal_zip", "ZIP_00501", "postal_zip", "00501"),
    ("zcta-annual", "zcta", "ZCTA_00501", "zcta", "00501"),
])
def test_table_geography_filters_use_exact_native_keys(inventory_connection, dataset, geography, geo_id, key, expected) -> None:
    frame, metadata = query_table(
        inventory_connection, dataset=dataset, geographies=[geography], geo_ids=[geo_id],
        domains=["rental_market"], proxy=False,
    )
    assert frame[key].tolist() == [expected]
    assert frame["shared_level"].tolist() == [-0.25]
    assert frame["source_vintage"].tolist() == ["fixture-2026"]
    assert frame["checksum_sha256"].tolist() == ["c" * 64]
    assert metadata["variable_id"].tolist() == ["shared_level"]


def test_state_table_filters_do_not_turn_carried_annual_values_into_flows(inventory_connection) -> None:
    frame, metadata = query_table(
        inventory_connection, dataset="state-monthly", geo_ids=["STATE_06"], geographies=["state"],
        frequencies=["monthly"], native_frequencies=["annual"], constructed=True,
    )
    assert frame["annual_control"].tolist() == [944.142971801626]
    assert frame["native_frequency"].tolist() == ["annual"]
    assert frame["final_frequency"].tolist() == ["monthly"]
    assert frame["is_carried_annual_control"].tolist() == [True]
    assert metadata["variable_id"].tolist() == ["annual_control"]


def test_table_output_frequency_is_not_inferred_from_broad_variable_scope(inventory_connection) -> None:
    # The variable permits annual and monthly uses; this materialized table is
    # monthly and must not be relabeled by matching its broad dictionary scope.
    frame, metadata = query_table(
        inventory_connection, dataset="county-monthly", frequencies=["annual"],
        variable_ids=["shared_level"],
    )
    assert frame.empty
    assert metadata["variable_id"].tolist() == ["shared_level"]


def test_postal_zip_table_never_matches_census_zcta_geography(inventory_connection) -> None:
    frame, _ = query_table(
        inventory_connection, dataset="zip-monthly", geographies=["zcta"],
        variable_ids=["shared_level"],
    )
    assert frame.empty


def test_new_filter_values_are_bound_and_cannot_expand_selection(inventory_connection) -> None:
    injected = "finance' OR 1=1 --"
    assert query_catalog(inventory_connection, domains=[injected]).empty
    assert query_observations(inventory_connection, domains=[injected]).empty
    assert query_observations(inventory_connection, frequencies=["monthly' OR 1=1 --"]).empty
    frame, _ = query_table(inventory_connection, dataset="zip-monthly", geo_ids=["ZIP_00501' OR 1=1 --"])
    assert frame.empty
    assert len(query_catalog(inventory_connection)) == 10


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_filtered_downloads_preserve_native_values_and_never_overwrite(inventory_connection, tmp_path, suffix) -> None:
    output = tmp_path / f"slice.{suffix}"
    filters = {"geographies": ["postal_zip"], "domains": ["rental_market"], "constructed": False}
    assert export_observations(inventory_connection, output, **filters) == 1
    if suffix == "csv":
        with output.open(encoding="utf-8", newline="") as handle:
            result = list(csv.DictReader(handle))
        assert result[0]["geo_id"] == "ZIP_00501"
        assert float(result[0]["value"]) == 1.5
        assert result[0]["unit"] == "fraction"
        assert result[0]["checksum_sha256"] == "c" * 64
    else:
        frame = pd.read_parquet(output)
        assert frame["geo_id"].tolist() == ["ZIP_00501"]
        assert frame["value"].tolist() == [1.5]
        assert frame["unit"].tolist() == ["fraction"]
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        export_observations(inventory_connection, output, **filters)
    assert output.read_bytes() == before


def test_table_download_keeps_zeros_and_new_filter_metadata(inventory_connection, tmp_path) -> None:
    path = tmp_path / "postal.csv"
    count, sidecar, _ = export_table(
        inventory_connection, path, dataset="zip-monthly", geo_ids=["ZIP_00501"],
        geographies=["postal_zip"], domains=["rental_market"], frequencies=["monthly"],
    )
    assert count == 1
    with path.open(encoding="utf-8", newline="") as handle:
        result = list(csv.DictReader(handle))
    assert result[0]["postal_zip"] == "00501"
    assert float(result[0]["shared_level"]) == -0.25
    metadata = pd.read_csv(sidecar)
    assert metadata["data_domain"].tolist() == ["rental_market"]


def test_cli_new_filters_round_trip_into_a_native_table_export(inventory_database, tmp_path, capsys) -> None:
    output = tmp_path / "postal_cli.parquet"
    assert main([
        "--database", str(inventory_database), "table", "--dataset", "zip-monthly",
        "--geography", "postal_zip", "--geo-id", "ZIP_00501", "--frequency", "monthly",
        "--domain", "rental_market", "--layer", "General", "--proxy", "false",
        "--constructed", "false", "--output", str(output),
    ]) == 0
    assert pd.read_parquet(output)["postal_zip"].tolist() == ["00501"]
    assert "Exported 1 rows" in capsys.readouterr().out


def test_discovery_lists_split_values_and_exact_layers(inventory_connection) -> None:
    from src.reporting.query import list_datasets, list_options

    frequencies = list_options(inventory_connection, "frequency")
    assert set(frequencies["value"]) == {"annual", "monthly"}
    geographies = set(list_options(inventory_connection, "geography")["value"])
    assert {"county", "postal_zip", "zcta", "state", "national"} <= geographies
    assert "county|postal_zip" not in geographies
    assert set(list_options(inventory_connection, "layer")["value"]) == {"General", "Version A", "Version B", "Version C"}
    assert set(list_options(inventory_connection, "proxy")["value"]) == {"false", "true"}
    datasets = list_datasets(inventory_connection)
    assert {"kind", "dataset", "table_name", "date_column"} <= set(datasets)
    assert "main" in set(datasets.loc[datasets["kind"].eq("observations"), "dataset"])
    assert "zip-monthly" in set(datasets.loc[datasets["kind"].eq("table"), "dataset"])
    assert "metadata.variable_dictionary" not in set(datasets["table_name"])


def test_geo_id_options_round_trip_with_exact_postal_identifiers(inventory_connection) -> None:
    from src.reporting.query import list_options

    options = list_options(inventory_connection, "geo-id", kind="table", dataset="zip-monthly", geographies=["postal_zip"])
    assert "ZIP_00501" in set(options["value"])
    for geo_id in options["value"]:
        frame, _ = query_table(inventory_connection, dataset="zip-monthly", geo_ids=[geo_id])
        assert len(frame) == 1
        assert frame["postal_zip"].iloc[0] in {"00501", "90001"}
    observations = list_options(inventory_connection, "geo-id", kind="observations", dataset="main", geographies=["zcta"])
    assert observations["value"].tolist() == ["ZCTA_00501"]


def test_frequency_options_respect_selected_actual_geography(inventory_connection) -> None:
    from src.reporting.query import list_options

    frequencies = list_options(
        inventory_connection, "frequency", kind="observations", dataset="main", geographies=["zcta"],
    )
    assert frequencies["value"].tolist() == ["annual"]
    for frequency in frequencies["value"]:
        assert not query_observations(inventory_connection, geographies=["zcta"], frequencies=[frequency]).empty


def test_actual_native_frequency_overrides_broader_catalog_label(inventory_connection) -> None:
    from src.reporting.query import list_options

    # Several real ACS variables have catalog native_frequency='annual' while
    # their observations preserve 'annual_5_year_estimate'. The actual source
    # label must remain selectable, without requiring both strings to match.
    frequencies = list_options(
        inventory_connection, "native-frequency", kind="observations", dataset="main", geographies=["zcta"],
    )
    assert frequencies["value"].tolist() == ["annual_5_year_estimate"]
    result = query_observations(
        inventory_connection, variable_ids=["shared_level"], geographies=["zcta"],
        native_frequencies=["annual_5_year_estimate"],
    )
    assert result["value"].tolist() == [2.5]
    assert result["native_frequency"].tolist() == ["annual_5_year_estimate"]


def test_cli_options_and_datasets_enable_catalog_export_round_trip(inventory_database, tmp_path, capsys) -> None:
    prefix = ["--database", str(inventory_database)]
    assert main([*prefix, "datasets"]) == 0
    datasets = pd.read_csv(io.StringIO(capsys.readouterr().out))
    assert "zip-monthly" in set(datasets["dataset"])
    assert main([*prefix, "options", "--field", "domain"]) == 0
    choices = pd.read_csv(io.StringIO(capsys.readouterr().out))
    assert "rental_market" in set(choices["value"])
    output = tmp_path / "chosen_domain.csv"
    assert main([*prefix, "catalog", "--domain", "rental_market", "--output", str(output)]) == 0
    assert pd.read_csv(output)["variable_id"].tolist() == ["shared_level"]


def test_discovery_cannot_escape_finite_dataset_whitelist(inventory_connection) -> None:
    from src.reporting.query import list_options

    with pytest.raises(ValueError):
        list_options(inventory_connection, "geo-id", kind="table", dataset="zip-monthly; DROP SCHEMA metadata CASCADE")
    assert len(query_catalog(inventory_connection)) == 10
