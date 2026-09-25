from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.common.variable_metadata import (
    ENRICHMENT_COLUMNS,
    PROVENANCE_PATH,
    REGISTRY_PATH,
    ROOT,
    TEMPORAL_TYPES,
    enrich_variable_metadata,
    load_temporal_type_definitions,
    load_variable_metadata,
    validate_variable_metadata,
)


def _configured_variables() -> pd.DataFrame:
    rows = []
    for filename in (
        "variables.yaml", "version_a_variables.yaml",
        "version_b_variables.yaml", "version_c_variables.yaml",
    ):
        document = yaml.safe_load((ROOT / "config" / filename).read_text(encoding="utf-8"))
        rows.extend(document["variables"])
    return pd.DataFrame(rows)


def _registry_root(tmp_path: Path, frame: pd.DataFrame) -> Path:
    path = tmp_path / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return tmp_path


def test_every_configured_variable_has_one_explicit_compatible_mapping():
    configured = _configured_variables()
    registry = load_variable_metadata()
    assert configured.variable_id.is_unique
    assert registry.variable_id.is_unique
    assert validate_variable_metadata(enrich_variable_metadata(configured), require_all=True) == []
    assert set(registry.temporal_type) <= TEMPORAL_TYPES
    assert set(load_temporal_type_definitions()) == TEMPORAL_TYPES


def test_enrichment_preserves_values_original_units_row_order_and_index():
    original = pd.DataFrame(
        {
            "variable_id": ["zori_smoothed_sa", "zhvi", "zori_smoothed_sa"],
            "unit": ["USD/month", "USD", "USD/month"],
            "value": [2500.5, 600_000.0, float("nan")],
            "geography_id": ["06037", "06037", "00001"],
        },
        index=[8, 2, 8],
    )
    enriched = enrich_variable_metadata(original)
    pd.testing.assert_frame_equal(enriched.loc[:, original.columns], original)
    assert all(column not in original for column in ENRICHMENT_COLUMNS)
    pd.testing.assert_frame_equal(enrich_variable_metadata(enriched), enriched)


def test_label_simplification_never_rescales_or_imposes_share_bounds():
    registry = load_variable_metadata()
    growth = registry.loc[
        registry.unit_original.eq("fraction") & registry.temporal_type.eq("growth_change")
    ].iloc[0]
    original = pd.DataFrame(
        {"variable_id": [growth.variable_id] * 3, "unit": ["fraction"] * 3,
         "value": [-0.8, 0.2, 1.5]}
    )
    enriched = enrich_variable_metadata(original)
    assert enriched.unit_simplified.tolist() == ["share"] * 3
    assert enriched.value.tolist() == [-0.8, 0.2, 1.5]
    assert all("negative or exceed one" in note for note in enriched.unit_simplification_note)
    assert validate_variable_metadata(enriched) == []


def test_monetary_price_basis_and_dollar_scales_remain_distinct():
    registry = load_variable_metadata()
    adjusted = registry.loc[registry.unit_original.eq("inflation_adjusted_USD_per_year")]
    assert not adjusted.empty
    assert set(adjusted.unit_simplified) == {"USD/year"}
    assert set(adjusted.monetary_basis) == {"inflation_adjusted_unspecified_base"}
    scaled = registry.loc[registry.unit_original.isin(
        ["constant_2024_USD", "thousands_of_current_dollars",
         "millions_of_chained_2017_dollars_saar"]
    )]
    assert not scaled.empty
    assert scaled.unit_original.tolist() == scaled.unit_simplified.tolist()


def test_unknown_variables_and_changed_source_units_fail_closed():
    with pytest.raises(ValueError, match="missing mappings"):
        enrich_variable_metadata(pd.DataFrame([{"variable_id": "not_registered", "unit": "USD"}]))
    # Similar simplified labels do not make changing declared units permissible.
    with pytest.raises(ValueError, match="unit mismatch"):
        enrich_variable_metadata(pd.DataFrame([{"variable_id": "zhvi", "unit": "current_dollars"}]))
    with pytest.raises(ValueError, match="missing variable_id"):
        enrich_variable_metadata(pd.DataFrame({"value": [1]}))


def test_validation_detects_stale_labels_but_enrichment_refreshes_them():
    original = pd.DataFrame([{"variable_id": "zhvi", "unit": "USD", "temporal_type": "flow"}])
    assert any("temporal_type mismatch" in error for error in validate_variable_metadata(original))
    refreshed = enrich_variable_metadata(original)
    assert refreshed.temporal_type.item() == "period_statistic"
    assert validate_variable_metadata(refreshed) == []
    assert any("missing registry IDs" in error for error in validate_variable_metadata(refreshed, require_all=True))


def test_complete_catalog_rejects_duplicate_blank_and_unenriched_rows():
    complete = enrich_variable_metadata(_configured_variables())
    duplicated = pd.concat([complete, complete.iloc[:1]], ignore_index=True)
    assert validate_variable_metadata(duplicated) == []
    assert any("duplicate variable IDs" in error for error in validate_variable_metadata(duplicated, require_all=True))
    blank = complete.copy()
    blank.loc[blank.index[0], "variable_id"] = None
    assert any("blank variable_id" in error for error in validate_variable_metadata(blank, require_all=True))
    missing = complete.drop(columns=["temporal_type"])
    assert any("missing enrichment columns" in error for error in validate_variable_metadata(missing, require_all=True))
    blank_label = complete.copy()
    blank_label.loc[blank_label.index[0], "unit_simplified"] = " "
    assert any("blank unit_simplified" in error for error in validate_variable_metadata(blank_label, require_all=True))


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [("temporal_type", "guess", "unknown temporal types"),
     ("unit_simplified", "percent", "unsupported unit simplification"),
     ("temporal_type_note", "", "blank temporal_type_note")],
)
def test_registry_structural_failures_stop_loading(tmp_path, column, value, message):
    registry = load_variable_metadata().iloc[:1].copy()
    registry.loc[registry.index[0], column] = value
    with pytest.raises(ValueError, match=message):
        load_variable_metadata(_registry_root(tmp_path, registry))


def test_duplicate_registry_mapping_fails(tmp_path):
    first = load_variable_metadata().iloc[:1]
    root = _registry_root(tmp_path, pd.concat([first, first], ignore_index=True))
    with pytest.raises(ValueError, match="duplicate variable IDs"):
        load_variable_metadata(root)


def test_reviewed_corrections_and_workbook_provenance_are_preserved():
    registry = load_variable_metadata().set_index("variable_id")
    premium = registry.loc["ca_homeowners_avg_written_premium"]
    assert premium.workbook_temporal_type == "flow"
    assert premium.temporal_type == "derived_ratio"
    assert "WP / WE" in premium.metadata_review_note
    assert registry.loc["bea_la_county_personal_income_annual", "temporal_type"] == "flow"
    assert registry.loc["acs_implied_average_mortgage_balance_proxy", "temporal_type"] == "period_statistic"
    provenance = json.loads((ROOT / PROVENANCE_PATH).read_text(encoding="utf-8"))
    assert len(provenance["source_checksum_sha256"]) == 64
    assert provenance["source_retrieved_at"] is None
    assert provenance["source_variable_count"] == registry.workbook_temporal_type.ne("").sum()
    assert provenance["added_variable_count"] == registry.workbook_temporal_type.eq("").sum()
    assert set(provenance["added_variable_ids"]) == set(registry.index[registry.workbook_temporal_type.eq("")])
    assert provenance["configured_variable_count"] == len(registry)


def test_missing_registry_is_an_explicit_failure(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_variable_metadata(tmp_path)
