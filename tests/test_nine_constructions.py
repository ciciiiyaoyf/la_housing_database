"""Native-row delivery must retain meaning, missingness, keys and provenance."""

from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace

import pandas as pd
import pytest

from src.common.io import sha256_file
from src.harmonization.nine_constructions import (
    ROW_TYPES,
    build_nine_constructions,
    read_nine_constructions_contract,
)


@pytest.fixture
def workspace(tmp_path):
    project = Path(__file__).resolve().parents[1]
    (tmp_path / "config").mkdir()
    (tmp_path / "metadata").mkdir()
    (tmp_path / "data" / "final").mkdir(parents=True)
    copyfile(project / "config" / "nine_constructions.yaml", tmp_path / "config" / "nine_constructions.yaml")
    copyfile(project / "metadata" / "data_dictionary.csv", tmp_path / "metadata" / "data_dictionary.csv")
    contract = read_nine_constructions_contract(tmp_path)
    for source in contract["sources"]:
        columns = set([source["date_column"], source["id_column"], *source["key_columns"], *source["variable_columns"], *source["context_columns"]])
        frame = pd.DataFrame({column: [pd.NA, pd.NA] for column in columns})
        for variable in source["variable_columns"]:
            frame[variable] = [0.123456789, None]
        frame[source["date_column"]] = pd.Timestamp("2026-06-30")
        frame[source["id_column"]] = ["06037", "06059"] if source["row_type"] == "county_monthly" else ["01001", "90001"] if source["geography_type"] == "postal_zip" else ["06037", "06037"]
        for field in ("county_fips", "primary_county_fips"):
            if field in columns and field != source["id_column"]:
                frame[field] = "06037"
        if "state_fips" in columns:
            frame["state_fips"] = "06"
        if "geo_id" in columns:
            frame["geo_id"] = "COUNTY_" + frame["county_fips"]
        for field in ("regime_id", "to_regime_id", "zip_market_regime_id"):
            if field in columns:
                frame[field] = [1, 2]
        if "from_regime_id" in columns:
            frame["from_regime_id"] = [1, 1]
        frame["unrelated_provider_field"] = 999
        frame.to_parquet(tmp_path / source["source_file"], index=False)
    return SimpleNamespace(root=tmp_path), contract


def test_union_keeps_native_rows_nulls_applicability_and_provenance(workspace):
    config, contract = workspace
    result = build_nine_constructions(config, write_outputs=False)
    assert len(result) == 10
    assert result.groupby("row_type").size().to_dict() == dict.fromkeys(ROW_TYPES, 2)
    assert not result.duplicated(["row_type", "source_row_key"]).any()
    assert not (config.root / contract["output_path"]).exists()
    assert "unrelated_provider_field" not in result
    assert "homes_sold" not in result
    for source in contract["sources"]:
        rows = result.loc[result.row_type.eq(source["row_type"])]
        original = pd.read_parquet(config.root / source["source_file"])
        assert rows.frequency.eq(source["frequency"]).all()
        assert rows.geography_type.eq(source["geography_type"]).all()
        assert rows.source_file_checksum_sha256.eq(sha256_file(config.root / source["source_file"])).all()
        assert rows.source_table.eq(source["source_table"]).all()
        for variable in source["variable_columns"]:
            pd.testing.assert_series_equal(rows[variable].reset_index(drop=True), original[variable].convert_dtypes(), check_dtype=False, check_names=False)
    zips = result.loc[result.row_type.eq("zip_monthly")]
    assert zips.geo_id.tolist() == ["ZIP_01001", "ZIP_90001"]
    assert zips.postal_zip.tolist() == ["01001", "90001"]
    assert zips["market_tightness_index_3c_fixed"].isna().all()
    assert "market_tightness_index_3c_fixed" not in zips.iloc[0].applicable_variable_ids.split("|")
    # A missing applicable value remains selectable; applicability is not a
    # non-null-value test. The second ZIP's momentum is intentionally missing.
    assert pd.isna(zips.iloc[1].zhvi_momentum_yoy_log_pct)
    assert "zhvi_momentum_yoy_log_pct" in zips.iloc[1].applicable_variable_ids.split("|")
    counties = result.loc[result.row_type.eq("county_monthly")]
    relative = "la_relative_zhvi_momentum_yoy_log_pct"
    assert relative in counties.iloc[0].applicable_variable_ids.split("|")
    assert relative not in counties.iloc[1].applicable_variable_ids.split("|")
    aggregate = result.loc[result.row_type.isin(ROW_TYPES[-2:])]
    assert aggregate.geo_id.eq("COUNTY_06037").all()
    assert aggregate.geography_scope.eq("Los Angeles County postal-ZIP aggregate").all()


def test_publish_preserves_source_files_and_creates_dictionary_and_log(workspace):
    config, contract = workspace
    before = {s["source_file"]: sha256_file(config.root / s["source_file"]) for s in contract["sources"]}
    result = build_nine_constructions(config)
    pd.testing.assert_frame_equal(pd.read_parquet(config.root / contract["output_path"]), result)
    definitions = pd.read_csv(config.root / contract["dictionary_path"])
    assert set(definitions.construction_number) == set(range(1, 10))
    assert definitions.variable_id.is_unique
    assert definitions.variable_id.equals(definitions.table_column)
    assert set(definitions.variable_id) == {v for f in contract["families"].values() for v in f["variables"]}
    row = definitions.set_index("variable_id").loc["zhvi_momentum_3m_annualized_log_pct"]
    assert row.row_frequencies == "monthly|quarterly"
    assert row.final_frequency == "monthly"
    for path, checksum in before.items():
        assert sha256_file(config.root / path) == checksum
    log = pd.read_csv(config.root / "metadata" / "transformation_log.csv")
    assert log.iloc[-1].transformation_id == contract["methodology_id"]
    assert log.iloc[-1].output_rows == 10
    assert not list((config.root / "data" / "final").glob("*.tmp"))


@pytest.mark.parametrize("failure", ["missing_file", "missing_column", "duplicate_key", "lost_leading_zero", "invalid_quarter", "wrong_county", "inapplicable_value"])
def test_structural_failures_stop_before_publication(workspace, failure):
    config, contract = workspace
    source = contract["sources"][2 if failure in {"invalid_quarter", "wrong_county"} else 0]
    path = config.root / source["source_file"]
    frame = pd.read_parquet(path)
    if failure == "missing_file":
        path.unlink()
    else:
        if failure == "missing_column":
            frame = frame.drop(columns=[source["variable_columns"][0]])
        elif failure == "duplicate_key":
            frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        elif failure == "lost_leading_zero":
            frame["county_fips"] = [6037, 6059]
        elif failure == "invalid_quarter":
            frame["quarter_end"] = pd.Timestamp("2026-05-31")
        elif failure == "wrong_county":
            frame["county_fips"] = "06059"
        elif failure == "inapplicable_value":
            frame.loc[1, "la_relative_zhvi_momentum_yoy_log_pct"] = 2.0
        frame.to_parquet(path, index=False)
    with pytest.raises((ValueError, FileNotFoundError)):
        build_nine_constructions(config)
    assert not (config.root / contract["output_path"]).exists()
    assert not (config.root / contract["dictionary_path"]).exists()
    assert not (config.root / "metadata" / "transformation_log.csv").exists()
