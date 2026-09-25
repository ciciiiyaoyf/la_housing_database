from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest

from src.cleaning.macro_bea_claims import clean_macro_bea_claims
from src.ingestion.macro_claims import (
    CLAIMS_CSV_ENDPOINT,
    CLAIMS_ENDPOINT,
    download_claims,
    read_claims_csv,
)


def _record(source="dol_ca_ui_claims", retrieved="2026-03-15T12:00:00Z"):
    return {
        "source_id": source, "data_vintage": "test-vintage", "retrieved_at": retrieved,
        "source_filename": "provider_fixture.xml", "checksum_sha256": "fixture-checksum",
    }


def _xml(dates, *, missing=None, geography="California"):
    root = ET.Element("r539cyState", {"rundate": "3/15/2026"})
    for offset, date in enumerate(pd.to_datetime(dates)):
        week = ET.SubElement(root, "week")
        fields = {
            "stateName": geography,
            "weekEnded": date.strftime("%m/%d/%Y"),
            "ReflectingWeekEnded": (date - pd.Timedelta(days=7)).strftime("%m/%d/%Y"),
            "InitialClaims": str(100 + offset * 10),
            "ContinuedClaims": str(1000 + offset * 10),
        }
        if missing == offset:
            fields["ContinuedClaims"] = ""
        for tag, text in fields.items():
            ET.SubElement(week, tag).text = text
    return ET.tostring(root)


def _clean_claims(tmp_path, payload, retrieved="2026-03-15T12:00:00Z"):
    raw = tmp_path / "source.xml"
    raw.write_bytes(payload)
    return clean_macro_bea_claims(raw, _record(retrieved=retrieved), "dol_ca_ui_claims", tmp_path / "clean.parquet")


def _one(frame, variable, date):
    rows = frame.loc[frame.variable_id.eq(variable) & frame.observation_date.eq(pd.Timestamp(date))]
    assert len(rows) == 1
    return rows.iloc[0]


def test_claims_uses_reflecting_date_and_monthly_mean(tmp_path):
    dates = pd.date_range("2026-01-03", "2026-02-07", freq="W-SAT")
    out = _clean_claims(tmp_path, _xml(dates))
    initial = _one(out, "ca_initial_claims_monthly_average", "2026-01-31")
    continued = _one(out, "ca_continued_claims_monthly_average", "2026-01-31")
    assert initial.value == 120
    assert continued.value == 1030  # Feb 7 report describes Jan 31 continued claims.
    assert _one(out, "ca_continued_claims_weekly", "2025-12-27").value == 1000
    assert _one(out, "ca_continued_claims_monthly_observation_count", "2026-01-31").value == 5
    assert continued.source_observation_date == pd.Timestamp("2026-01-31")
    assert set(out.geo_id) == {"STATE_06"}
    assert set(out.state_fips) == {"06"}
    assert not out.is_seasonally_adjusted.any()
    assert not out.is_interpolated.any()
    assert not out.is_imputed.any()
    assert set(out.checksum_sha256) == {"fixture-checksum"}
    assert pd.isna(initial.reported_value)


def test_missing_week_and_missing_value_do_not_become_zero(tmp_path):
    dates = pd.date_range("2026-01-03", "2026-02-07", freq="W-SAT")
    out = _clean_claims(tmp_path, _xml(dates, missing=3))
    assert pd.isna(_one(out, "ca_continued_claims_monthly_average", "2026-01-31").value)
    assert _one(out, "ca_continued_claims_monthly_observation_count", "2026-01-31").value == 4
    assert _one(out, "ca_initial_claims_monthly_average", "2026-01-31").value == 120
    out = _clean_claims(tmp_path, _xml(dates.delete(3)))
    assert pd.isna(_one(out, "ca_initial_claims_monthly_average", "2026-01-31").value)


def test_open_month_not_published_even_if_last_saturday_observed(tmp_path):
    dates = pd.date_range("2026-01-03", "2026-01-31", freq="W-SAT")
    out = _clean_claims(tmp_path, _xml(dates), retrieved="2026-01-31T23:00:00Z")
    monthly = out.loc[out.final_frequency.eq("monthly")]
    assert not monthly.observation_date.eq(pd.Timestamp("2026-01-31")).any()
    assert len(out.loc[out.variable_id.eq("ca_initial_claims_weekly")]) == 5


@pytest.mark.parametrize("dates,geo,error", [
    (["2026-01-03", "2026-01-03"], "California", "duplicate"),
    (["2026-01-04"], "California", "Saturdays"),
    (["2026-01-03"], "Nevada", "non-California"),
    (["2027-01-02"], "California", "future"),
])
def test_claims_structural_failures(tmp_path, dates, geo, error):
    with pytest.raises(ValueError, match=error):
        _clean_claims(tmp_path, _xml(dates, geography=geo))


def _gdp_zip(tmp_path, values, *, unit="Millions of chained 2017 dollars", duplicate=False):
    raw = tmp_path / "gdp.zip"
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["GeoFIPS", "GeoName", "TableName", "LineCode", "Description", "Unit", *values])
    record = [' "06000"', "California", "SQGDP9", "1", "All industry total ", unit, *values.values()]
    writer.writerow(record)
    if duplicate:
        writer.writerow(record)
    writer.writerow(["Last updated: June 25, 2026-- new statistics for 2026:Q1."])
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("SQGDP9__ALL_AREAS_2005_2026.csv", stream.getvalue())
    return raw


def _clean_gdp(tmp_path, values, **kwargs):
    return clean_macro_bea_claims(
        _gdp_zip(tmp_path, values, **kwargs), _record("bea_ca_real_gdp"),
        "bea_ca_real_gdp", tmp_path / "gdp.parquet",
    )


def test_gdp_exact_compounded_growth_and_provenance(tmp_path):
    out = _clean_gdp(tmp_path, {"2025:Q1": 100, "2025:Q2": 110, "2025:Q3": 115, "2025:Q4": 120, "2026:Q1": 130})
    assert _one(out, "bea_ca_real_gdp_qoq_annualized_pct", "2025-06-30").value == pytest.approx(46.41)
    assert _one(out, "bea_ca_real_gdp_yoy_pct", "2026-03-31").value == pytest.approx(30)
    assert pd.isna(_one(out, "bea_ca_real_gdp_yoy_pct", "2025-12-31").value)
    assert out.is_seasonally_adjusted.all()
    assert set(out.provider_last_updated) == {"2026-06-25"}
    assert set(out.source_vintage) == {"test-vintage"}
    assert set(out.loc[out.variable_id.eq("bea_ca_real_gdp_quarterly"), "unit"]) == {"millions_of_chained_2017_dollars_saar"}


def test_gdp_gap_never_uses_previous_available_quarter(tmp_path):
    out = _clean_gdp(tmp_path, {"2025:Q1": 100, "2025:Q3": 120, "2026:Q1": 130})
    assert pd.isna(_one(out, "bea_ca_real_gdp_qoq_annualized_pct", "2025-09-30").value)
    assert _one(out, "bea_ca_real_gdp_yoy_pct", "2026-03-31").value == pytest.approx(30)


@pytest.mark.parametrize("kwargs,error", [
    ({"unit": "Millions of current dollars"}, "unit/base year"),
    ({"duplicate": True}, "exactly one California"),
])
def test_gdp_rejects_wrong_units_and_duplicate_total(tmp_path, kwargs, error):
    with pytest.raises(ValueError, match=error):
        _clean_gdp(tmp_path, {"2025:Q1": 100}, **kwargs)


def test_gdp_rejects_zero_and_unexplained_missing_values(tmp_path):
    with pytest.raises(ValueError, match="nonpositive"):
        _clean_gdp(tmp_path, {"2025:Q1": 0})
    with pytest.raises(ValueError, match="unexplained"):
        _clean_gdp(tmp_path, {"2025:Q1": 100, "2025:Q2": "garbage"})


def test_download_preserves_bytes_and_distinct_endpoints(tmp_path, monkeypatch):
    payload = _xml(["2026-01-03", "2026-01-10"])
    calls = []
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=payload, headers={"Content-Disposition": "attachment;filename=r539cy.xml"}, raise_for_status=lambda: None)
    monkeypatch.setattr("src.ingestion.macro_claims.requests.post", fake_post)
    source = SimpleNamespace(source_id="dol_ca_ui_claims", download_url=CLAIMS_ENDPOINT, selected_start_date="1986-01-01")
    scratch = tmp_path / "scratch.part"
    metadata = download_claims(source, scratch, None, "2026-12-31")
    assert scratch.read_bytes() == payload
    assert calls[0][1]["data"] == {"level": "state", "strtdate": "1986", "enddate": "2026", "filetype": "xml", "states[]": "CA"}
    assert metadata["initial_claims_last_week"] == "2026-01-10"
    assert metadata["continued_claims_last_week"] == "2026-01-03"
    assert metadata["original_filename"] == "r539cy.xml"
    assert "provider_last_updated" not in metadata
    assert json.loads(metadata["request_query"])["states[]"] == "CA"
    with pytest.raises(FileExistsError):
        download_claims(source, scratch, None, "2026-12-31")


def test_download_rejects_html_or_wrong_geography_before_writing(tmp_path, monkeypatch):
    source = SimpleNamespace(source_id="dol_ca_ui_claims", download_url=CLAIMS_ENDPOINT, selected_start_date="1986-01-01")
    for payload in (b"<html>provider error</html>", _xml(["2026-01-03"], geography="Nevada")):
        monkeypatch.setattr("src.ingestion.macro_claims.requests.post", lambda *args, **kwargs: SimpleNamespace(content=payload, headers={}, raise_for_status=lambda: None))
        scratch = tmp_path / "must_not_exist.part"
        with pytest.raises(ValueError):
            download_claims(source, scratch, None, "2026-12-31")
        assert not scratch.exists()


def _csv(dates, *, missing_component=None, extra_state=True):
    stream = io.StringIO()
    fields = ["st", "rptdate", "c2", "c3", "c6", "c7", "c8", "c11", "c12"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for offset, date in enumerate(pd.to_datetime(dates)):
        record = {
            "st": "CA", "rptdate": date.strftime("%Y-%m-%d"),
            "c2": (date - pd.Timedelta(days=7)).strftime("%Y-%m-%d"),
            "c3": 98 + 10 * offset, "c7": 2, "c6": 99,
            "c8": 980 + 10 * offset, "c12": 20, "c11": 999,
        }
        if missing_component and offset == missing_component[0]:
            record[missing_component[1]] = ""
        writer.writerow(record)
        if extra_state:
            writer.writerow({**record, "st": "NV", "c3": 9, "c8": 90})
    return stream.getvalue().encode()


def test_csv_reconstructs_xml_values_and_filters_state(tmp_path):
    dates = pd.date_range("2026-01-03", "2026-02-07", freq="W-SAT")
    xml_frame = _clean_claims(tmp_path, _xml(dates))
    csv_frame = _clean_claims(tmp_path, _csv(dates))
    columns = ["variable_id", "observation_date", "source_observation_date", "value", "reported_value"]
    pd.testing.assert_frame_equal(xml_frame[columns].reset_index(drop=True), csv_frame[columns].reset_index(drop=True))
    native = csv_frame.loc[csv_frame.final_frequency.eq("weekly")]
    assert len(native) == 12  # Nevada never enters clean observations.
    assert set(native.transformation_id) == {"dol_ca_ui_claims_native_week_csv_v2"}
    assert native.quality_flag.str.contains("includes_workshare_equivalents").all()
    assert _one(csv_frame, "ca_continued_claims_weekly", "2026-01-31").value == 1050


@pytest.mark.parametrize("component,kind", [("c3", "initial"), ("c7", "initial"), ("c8", "continued"), ("c12", "continued")])
def test_csv_missing_either_component_keeps_aggregate_missing(tmp_path, component, kind):
    dates = pd.date_range("2026-01-03", "2026-02-07", freq="W-SAT")
    out = _clean_claims(tmp_path, _csv(dates, missing_component=(3, component)))
    assert pd.isna(_one(out, f"ca_{kind}_claims_monthly_average", "2026-01-31").value)
    assert _one(out, f"ca_{kind}_claims_monthly_observation_count", "2026-01-31").value == 4


def test_csv_known_latest_provider_components():
    payload = b"st,rptdate,c2,c3,c7,c8,c12\nCA,2026-09-05,2026-08-29,37356,32,320257,779\n"
    row = read_claims_csv(payload).iloc[0]
    assert row.InitialClaims == 37388
    assert row.ContinuedClaims == 321036
    assert row.c2 == pd.Timestamp("2026-08-29")


@pytest.mark.parametrize("payload,error", [
    (b"<html>Provider error</html>", "missing required fields"),
    (b"st,rptdate,c2,c3,c7,c8,c12\nNV,2026-01-03,2025-12-27,1,0,1,0\n", "no California"),
    (b"st,rptdate,c2,c3,c7,c8,c12\nCA,2026-01-03,2026-01-03,1,0,1,0\n", "seven days"),
    (b"st,rptdate,c2,c3,c7,c8,c12\nCA,2026-01-03,2025-12-27,-1,0,1,0\n", "invalid claim counts"),
])
def test_bulk_csv_schema_and_date_validation(payload, error):
    with pytest.raises(ValueError, match=error):
        read_claims_csv(payload)


def test_bulk_download_preserves_all_states_bytes_and_real_file_timestamp(tmp_path, monkeypatch):
    payload = _csv(["2026-01-03", "2026-01-10"])
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            content=payload,
            headers={"Last-Modified": "Tue, 15 Sep 2026 19:50:57 GMT"},
            raise_for_status=lambda: None,
        )
    monkeypatch.setattr("src.ingestion.macro_claims.requests.get", fake_get)
    source = SimpleNamespace(source_id="dol_ca_ui_claims", download_url=CLAIMS_CSV_ENDPOINT, selected_start_date="1986-01-01")
    scratch = tmp_path / "new_bulk.part"
    metadata = download_claims(source, scratch, "2026-01-05", "2026-01-06")
    assert scratch.read_bytes() == payload  # No raw filtering by state or requested dates.
    assert len(calls) == 1
    assert calls[0][0] == CLAIMS_CSV_ENDPOINT
    assert metadata["request_method"] == "GET"
    assert metadata["original_filename"] == "ar539.csv"
    assert metadata["response_observation_count"] == 4
    assert metadata["initial_claims_last_week"] == "2026-01-10"
    assert metadata["continued_claims_last_week"] == "2026-01-03"
    assert metadata["provider_last_updated"] == "2026-09-15T19:50:57+00:00"
    assert "not_release_date" in metadata["provider_last_updated_status"]
    assert "provider_report_run_date" not in metadata
    with pytest.raises(FileExistsError):
        download_claims(source, scratch, None, None)


def test_bulk_download_does_not_silently_fallback_to_xml(tmp_path, monkeypatch):
    def fake_get(*args, **kwargs):
        return SimpleNamespace(content=b"<html>Source unavailable</html>", headers={}, raise_for_status=lambda: None)
    monkeypatch.setattr("src.ingestion.macro_claims.requests.get", fake_get)
    monkeypatch.setattr("src.ingestion.macro_claims.requests.post", lambda *args, **kwargs: pytest.fail("Must not substitute older XML"))
    source = SimpleNamespace(source_id="dol_ca_ui_claims", download_url=CLAIMS_CSV_ENDPOINT)
    scratch = tmp_path / "no_fallback.part"
    with pytest.raises(ValueError):
        download_claims(source, scratch, None, None)
    assert not scratch.exists()
