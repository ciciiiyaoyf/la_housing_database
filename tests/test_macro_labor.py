"""Strict CES labor-source and exact calendar-lag construction tests."""

import copy
import json

import pandas as pd
import pytest

from src.cleaning.macro_labor import (
    BLS_CES_LABOR_SERIES_IDS,
    GROWTH_TRANSFORMATION_ID,
    LEVEL_TRANSFORMATION_ID,
    SERIES,
    SOURCE_ID,
    clean_macro_labor,
)


def _item(year, month, value, footnotes=None):
    return {
        "year": str(year), "period": f"M{month:02d}", "value": value,
        "footnotes": [{}] if footnotes is None else footnotes,
    }


def _payload():
    return {
        "series_ids": list(BLS_CES_LABOR_SERIES_IDS),
        "responses": [{
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{
                "seriesID": series_id,
                "data": [
                    _item(2024, 1, "100"),
                    _item(2025, 1, "110", [{"code": "P", "text": "Preliminary"}]),
                    _item(2025, 2, "120"),
                    _item(2025, 13, "999"),
                ],
            } for series_id in BLS_CES_LABOR_SERIES_IDS]},
        }],
    }


def _clean(tmp_path, payload):
    path = tmp_path / "provider_response.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    record = {
        "source_id": SOURCE_ID,
        "data_vintage": "2026-09-15",
        "retrieved_at": "2026-09-15T12:00:00Z",
        "source_filename": "provider_response.json",
        "checksum_sha256": "a" * 64,
        "provider_last_updated": "2026-08-21",
    }
    return clean_macro_labor(path, record, tmp_path / "clean.parquet")


def _one(frame, series_id, date, growth=False):
    variable, geo_id, _ = SERIES[series_id]
    if growth:
        variable += "_yoy_pct"
    return frame.loc[
        frame.variable_id.eq(variable) & frame.geo_id.eq(geo_id)
        & frame.observation_date.eq(pd.Timestamp(date))
    ].iloc[0]


def test_contract_all_series_geography_provenance_and_arithmetic_growth(tmp_path):
    frame = _clean(tmp_path, _payload())
    assert len(frame) == 12 * 3 * 2
    assert set(frame.geo_id) == {"STATE_06", "METRODIV_31084"}
    assert frame.state_fips.eq("06").all()
    assert frame.county_fips.isna().all()
    assert not frame.is_seasonally_adjusted.any()
    assert not frame.is_imputed.any()
    assert not frame.is_interpolated.any()
    assert frame.checksum_sha256.eq("a" * 64).all()
    assert frame.retrieved_at.eq("2026-09-15T12:00:00Z").all()
    assert frame.source_vintage.eq("2026-09-15").all()
    for series_id in SERIES:
        level = _one(frame, series_id, "2025-01-31")
        growth = _one(frame, series_id, "2025-01-31", growth=True)
        assert level.unit == SERIES[series_id][2]
        assert level.reported_value == 110
        assert level.transformation_id == LEVEL_TRANSFORMATION_ID
        assert level.source_observation_date == pd.Timestamp("2025-01-01")
        assert "P: Preliminary" in level.quality_flag
        assert growth.value == pytest.approx(10)
        assert growth.unit == "percent"
        assert pd.isna(growth.reported_value)
        assert growth.transformation_id == GROWTH_TRANSFORMATION_ID
        assert "current:P: Preliminary" in growth.quality_flag
        # A positional shift or previous-observation fallback must not fill Feb.
        feb = _one(frame, series_id, "2025-02-28", growth=True)
        assert pd.isna(feb.value)
        assert "exact_12_month_lag_unavailable" in feb.quality_flag


def test_published_null_and_lag_footnotes_are_preserved(tmp_path):
    payload = _payload()
    first = payload["responses"][0]["Results"]["series"][0]
    first["data"][0] = _item(2024, 1, "-", [{"code": "X", "text": "Not available"}])
    first["data"].append(_item(2026, 1, None, [{"code": "X", "text": "Not available"}]))
    frame = _clean(tmp_path, payload)
    level = _one(frame, first["seriesID"], "2024-01-31")
    assert pd.isna(level.value)
    assert "X: Not available" in level.quality_flag
    assert "provider_published_missing" in level.quality_flag
    growth = _one(frame, first["seriesID"], "2025-01-31", growth=True)
    assert pd.isna(growth.value)
    assert "lag12:X: Not available" in growth.quality_flag
    assert "lag12_provider_published_missing" in growth.quality_flag
    current_missing = _one(frame, first["seriesID"], "2026-01-31", growth=True)
    assert pd.isna(current_missing.value)
    assert "current_provider_published_missing" in current_missing.quality_flag


def test_identical_chunk_overlap_deduplicated_but_conflict_rejected(tmp_path):
    payload = _payload()
    payload["responses"].append(copy.deepcopy(payload["responses"][0]))
    assert len(_clean(tmp_path, payload)) == 72
    payload["responses"][1]["Results"]["series"][0]["data"][0]["value"] = "101"
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        _clean(tmp_path, payload)


def test_empty_early_ahe_chunk_allowed_but_missing_series_rejected(tmp_path):
    payload = _payload()
    early = copy.deepcopy(payload["responses"][0])
    for series in early["Results"]["series"]:
        series["data"] = []
    payload["responses"].insert(0, early)
    assert len(_clean(tmp_path, payload)) == 72
    payload["responses"][1]["Results"]["series"].pop()
    with pytest.raises(ValueError, match="missing configured monthly series"):
        _clean(tmp_path, payload)


@pytest.mark.parametrize("mutation, message", [
    ("declared_subset", "exactly all 12"),
    ("unknown_series", "unsupported series"),
    ("failed_response", "unsuccessful"),
    ("invalid_month", "invalid monthly period"),
    ("invalid_number", "invalid numeric"),
])
def test_structural_failures_stop_cleaning(tmp_path, mutation, message):
    payload = _payload()
    response = payload["responses"][0]
    first = response["Results"]["series"][0]
    if mutation == "declared_subset":
        payload["series_ids"].pop()
    elif mutation == "unknown_series":
        first["seriesID"] = "SMU_UNKNOWN"
    elif mutation == "failed_response":
        response["status"] = "REQUEST_NOT_PROCESSED"
    elif mutation == "invalid_month":
        first["data"][0]["period"] = "M14"
    elif mutation == "invalid_number":
        first["data"][0]["value"] = "corrupt"
    with pytest.raises(ValueError, match=message):
        _clean(tmp_path, payload)


def test_nonpositive_denominator_is_missing_not_infinite(tmp_path):
    payload = _payload()
    first = payload["responses"][0]["Results"]["series"][0]
    first["data"][0]["value"] = "0"
    frame = _clean(tmp_path, payload)
    growth = _one(frame, first["seriesID"], "2025-01-31", growth=True)
    assert pd.isna(growth.value)
    assert "lag12_nonpositive_denominator" in growth.quality_flag
