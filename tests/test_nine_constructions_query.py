"""Mixed construction exports retain actual periods, geography, and missingness."""

from __future__ import annotations

import csv

import duckdb
import pandas as pd
import pytest

from src.common.config import load_config
from src.reporting.data_browser import DATASET_LABELS, preview, variable_options
from src.reporting.query import export_table, list_datasets, list_options, query_table


MOMENTUM = "zhvi_momentum_3m_annualized_log_pct"
RELATIVE = "la_relative_zhvi_momentum_yoy_log_pct"
REGIME = "zip_market_regime_id"
SHARE = "zip_market_regime_share"
TRANSITION = "zip_market_regime_transition_share"


@pytest.fixture
def constructions():
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA analytics")
        dictionary = pd.DataFrame(load_config().variables)
        connection.register("dictionary_input", dictionary)
        connection.execute("CREATE TABLE metadata.variable_dictionary AS SELECT * FROM dictionary_input")
        common = {
            "date": pd.Timestamp("2026-03-31"), "county_fips": "06037",
            "source_file": "data/final/source.parquet", "source_file_checksum_sha256": "fixture-checksum",
        }
        rows = [
            {"row_type": "county_monthly", "geo_id": "COUNTY_06037", "frequency": "monthly",
             "geography_type": "county", "source_table": "analytics.comparable_county_monthly_analytics",
             "applicable_variable_ids": f"{MOMENTUM}|{RELATIVE}", MOMENTUM: None, RELATIVE: 1.5},
            {"row_type": "county_monthly", "geo_id": "COUNTY_06059", "frequency": "monthly",
             "geography_type": "county", "county_fips": "06059",
             "source_table": "analytics.comparable_county_monthly_analytics",
             "applicable_variable_ids": MOMENTUM, MOMENTUM: 2.0},
            {"row_type": "zip_monthly", "geo_id": "ZIP_00501", "postal_zip": "00501",
             "frequency": "monthly", "geography_type": "postal_zip",
             "source_table": "analytics.zip_monthly_panel", "applicable_variable_ids": MOMENTUM, MOMENTUM: 3.0},
            {"row_type": "zip_regime_quarterly", "geo_id": "ZIP_00501", "postal_zip": "00501",
             "frequency": "quarterly", "geography_type": "postal_zip",
             "source_table": "analytics.zip_market_regime_quarterly",
             "applicable_variable_ids": f"{MOMENTUM}|{REGIME}", MOMENTUM: 3.0, REGIME: None,
             "classification_status": "missing_inputs", "complete_regime_inputs": False},
            {"row_type": "zip_regime_summary_quarterly", "geo_id": "COUNTY_06037",
             "frequency": "quarterly", "geography_type": "county", "regime_id": 1,
             "source_table": "analytics.la_zip_market_regime_summary_quarterly",
             "applicable_variable_ids": SHARE, SHARE: 0.25, "regime_zip_count": 1,
             "classified_zip_count": 4, "universe_zip_count": 5},
            {"row_type": "zip_regime_transition_quarterly", "geo_id": "COUNTY_06037",
             "frequency": "quarterly", "geography_type": "county", "from_regime_id": 1, "to_regime_id": 2,
             "source_table": "analytics.la_zip_market_regime_transitions_quarterly",
             "applicable_variable_ids": TRANSITION, TRANSITION: None, "transition_zip_count": 1,
             "classified_zip_count": 4, "comparable_zip_count": 3,
             "missing_previous_classification_count": 1, "transition_denominator_status": "incomplete"},
        ]
        frame = pd.DataFrame([{**common, **row} for row in rows])
        connection.register("construction_input", frame)
        connection.execute("CREATE TABLE analytics.nine_constructions AS SELECT * FROM construction_input")
        yield connection


def test_dataset_registration_and_actual_frequency_options(constructions):
    dataset = list_datasets().set_index("dataset").loc["nine-constructions"]
    assert dataset["table_name"] == "analytics.nine_constructions"
    assert DATASET_LABELS["nine-constructions"].startswith("Nine housing constructions")
    options = list_options(constructions, "frequency", kind="table", dataset="nine-constructions")
    assert options["value"].tolist() == ["monthly", "quarterly"]
    empty = list_options(
        constructions, "frequency", kind="table", dataset="nine-constructions", geographies=["zcta"]
    )
    assert empty.empty


def test_monthly_filter_prunes_quarterly_columns_and_preserves_nulls(constructions):
    data, definitions = query_table(constructions, dataset="nine-constructions", frequencies=["monthly"])
    assert len(data) == 3
    assert set(data["frequency"]) == {"monthly"}
    assert REGIME not in data and SHARE not in data and TRANSITION not in data
    assert set(definitions["variable_id"]) == {MOMENTUM, RELATIVE}
    assert pd.isna(data.loc[data.geo_id.eq("COUNTY_06037"), MOMENTUM].iloc[0])
    assert set(definitions.delivery_frequencies) == {"monthly"}


def test_quarterly_geography_filter_keeps_input_snapshot_without_resampling(constructions):
    data, definitions = query_table(
        constructions, dataset="nine-constructions", frequencies=["quarterly"], geographies=["postal_zip"]
    )
    assert data.row_type.tolist() == ["zip_regime_quarterly"]
    assert data.geo_id.tolist() == ["ZIP_00501"]
    assert data[MOMENTUM].tolist() == [3.0]
    assert pd.isna(data[REGIME].iloc[0])
    assert set(definitions.variable_id) == {MOMENTUM, REGIME}
    # This is a retained quarterly snapshot of a monthly input, not a new unit/frequency conversion.
    entry = definitions.set_index("variable_id").loc[MOMENTUM]
    assert entry["final_frequency"] == "monthly"
    assert entry["delivery_frequencies"] == "quarterly"


def test_county_selection_does_not_broadcast_zip_rows(constructions):
    data, definitions = query_table(constructions, dataset="nine-constructions", geo_ids=["COUNTY_06037"])
    assert set(data.row_type) == {"county_monthly", "zip_regime_summary_quarterly", "zip_regime_transition_quarterly"}
    assert set(data.geo_id) == {"COUNTY_06037"}
    assert REGIME not in definitions.variable_id.tolist()


def test_variable_selection_uses_applicability_not_value_presence(constructions):
    data, definitions = query_table(constructions, dataset="nine-constructions", variable_ids=[TRANSITION])
    assert len(data) == 1
    assert data.row_type.tolist() == ["zip_regime_transition_quarterly"]
    assert pd.isna(data[TRANSITION].iloc[0])
    assert data["transition_zip_count"].tolist() == [1.0]
    assert data["classified_zip_count"].tolist() == [4.0]
    assert data["comparable_zip_count"].tolist() == [3.0]
    assert data["missing_previous_classification_count"].tolist() == [1.0]
    assert data["transition_denominator_status"].tolist() == ["incomplete"]
    assert definitions.variable_id.tolist() == [TRANSITION]
    assert definitions.applicable_row_types.tolist() == ["zip_regime_transition_quarterly"]
    la, _ = query_table(constructions, dataset="nine-constructions", variable_ids=[RELATIVE])
    assert la.geo_id.tolist() == ["COUNTY_06037"]


def test_browser_options_preview_and_export_match(constructions, tmp_path):
    options = variable_options(constructions, "table", "nine-constructions", geographies=["postal_zip"])
    assert {row["value"] for row in options} == {MOMENTUM, REGIME}
    selected = dict(variable_ids=[REGIME], geographies=["postal_zip"])
    result = preview(constructions, "table", "nine-constructions", selected)
    assert len(result["rows"]) == 1
    assert result["rows"][0][REGIME] is None
    assert result["rows"][0]["postal_zip"] == "00501"
    assert any("applicable_variable_ids" in note for note in result["notes"])
    output = tmp_path / "nine.csv"
    count, companion, omitted = export_table(constructions, output, dataset="nine-constructions", **selected)
    assert count == 1 and not omitted
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["postal_zip"] == "00501" and rows[0][REGIME] == ""
    with companion.open(encoding="utf-8", newline="") as handle:
        metadata = list(csv.DictReader(handle))
    assert [row["variable_id"] for row in metadata] == [REGIME]
    assert metadata[0]["applicable_source_tables"] == "analytics.zip_market_regime_quarterly"


def test_unmatched_row_slice_has_no_inapplicable_definitions(constructions):
    data, definitions = query_table(
        constructions, dataset="nine-constructions", variable_ids=[RELATIVE], frequencies=["quarterly"]
    )
    assert data.empty and definitions.empty
    assert "row_type" in data and "variable_id" in definitions


def test_companion_adds_family_mapping_without_broadening_selected_applicability(constructions):
    constructions.execute(
        "CREATE TABLE metadata.nine_constructions_dictionary AS SELECT "
        "? variable_id, 1 construction_number, 'Home-price momentum' construction_name, "
        "'county_monthly|zip_monthly|zip_regime_quarterly' applicable_row_types, "
        "'Preserve native rows' packaging_note", [MOMENTUM]
    )
    _, definitions = query_table(
        constructions, dataset="nine-constructions", variable_ids=[MOMENTUM],
        geographies=["postal_zip"], frequencies=["quarterly"],
    )
    assert definitions.construction_number.tolist() == [1]
    assert definitions.construction_name.tolist() == ["Home-price momentum"]
    assert definitions.applicable_row_types.tolist() == ["zip_regime_quarterly"]
    assert definitions.delivery_frequencies.tolist() == ["quarterly"]


def test_variable_picker_groups_nine_families_before_sorting_labels(constructions):
    constructions.execute(
        "CREATE TABLE metadata.nine_constructions_dictionary "
        "(variable_id VARCHAR, construction_number VARCHAR, construction_name VARCHAR)"
    )
    constructions.executemany(
        "INSERT INTO metadata.nine_constructions_dictionary VALUES (?, ?, ?)",
        [(MOMENTUM, "1", "Home-price momentum"), (RELATIVE, "7", "LA relative to peers"),
         (REGIME, "8", "LA postal-ZIP market regimes"), (SHARE, "8", "LA postal-ZIP market regimes"),
         (TRANSITION, "8", "LA postal-ZIP market regimes")],
    )
    constructions.executemany(
        "UPDATE metadata.variable_dictionary SET display_name = ? WHERE variable_id = ?",
        [("Zulu momentum", MOMENTUM), ("Alpha relative", RELATIVE), ("Zulu regime", REGIME),
         ("Alpha share", SHARE), ("Middle transition", TRANSITION)],
    )
    options = variable_options(constructions, "table", "nine-constructions")
    assert [item["value"] for item in options] == [MOMENTUM, RELATIVE, SHARE, TRANSITION, REGIME]
    assert [item["construction_number"] for item in options] == [1, 7, 8, 8, 8]
    assert [item["construction_name"] for item in options[:2]] == [
        "Home-price momentum", "LA relative to peers"
    ]
    postal = variable_options(constructions, "table", "nine-constructions", geographies=["postal_zip"])
    assert [item["value"] for item in postal] == [MOMENTUM, REGIME]
    assert [item["construction_number"] for item in postal] == [1, 8]


def test_variable_picker_keeps_options_when_optional_family_metadata_is_absent(constructions):
    options = variable_options(constructions, "table", "nine-constructions")
    assert len(options) == 5
    assert all(set(item) == {"value", "label", "category_id", "category_label"} for item in options)
