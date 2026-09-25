from pathlib import Path
import json
from dataclasses import replace

import pandas as pd
import pytest

from src.common.config import load_config
from src.ingestion.download import _import_local_file
from src.validation.qa import _redfin_zip_panel_checks

from src.cleaning.redfin_zip import (
    REQUIRED_COLUMNS,
    SUCCESSOR_COLUMNS,
    OFFICIAL_COLUMNS,
    DOM_DAYS_COLUMN,
    DOM_DAYS_VARIABLE,
    clean_redfin_zip_key_metrics,
    redfin_zip_schema_version,
    validate_redfin_zip_schema,
)


def _row(postal_zip: str, begin: str, end: str) -> dict:
    return {
        "LAST UPDATED": "2026-06-03",
        "FREQUENCY": "Rolling 3 Months",
        "PERIOD BEGIN": begin,
        "PERIOD END": end,
        "REGION ID": "123",
        "REGION TYPE": "Zip",
        "REGION NAME": postal_zip,
        "METRO": "Los Angeles, CA metro area",
        "HOMES SOLD": 10,
        "HOMES SOLD YOY (%)": 5.0,
        "MEDIAN SALE PRICE NSA ($)": 500_000,
        "MEDIAN SALE PRICE NSA YOY (%)": 4.0,
        "MEDIAN DAYS ON MARKET (DAYS)": 30,
        "MEDIAN DAYS ON MARKET YOY (%)": -2.0,
        "NEW LISTINGS": 20,
        "NEW LISTINGS YOY (%)": 3.0,
        "ACTIVE LISTINGS": 40,
        "ACTIVE LISTINGS YOY (%)": 2.0,
        "PENDING SALES": 8,
        "PENDING SALES YOY (%)": 1.0,
    }


def test_redfin_zip_cleaner_filters_with_geography_and_preserves_window(
    tmp_path: Path,
):
    raw = tmp_path / "redfin.csv"
    pd.DataFrame(
        [
            _row("90001", "2019-04-01", "2019-06-30"),
            _row("90001", "2019-05-01", "2019-07-31"),
            _row("93001", "2019-04-01", "2019-06-30"),
        ],
        columns=REQUIRED_COLUMNS,
    ).to_csv(raw, index=False)
    geography = tmp_path / "geography.parquet"
    pd.DataFrame(
        {
            "postal_zip": ["90001", "93001"],
            "county_fips": ["06037", "06111"],
        }
    ).to_parquet(geography, index=False)
    output = tmp_path / "redfin_zip_monthly_la_county.parquet"
    observations = tmp_path / "redfin_zip_observations.parquet"
    coverage = tmp_path / "coverage"
    record = {
        "data_vintage": "2026-07-02",
        "retrieved_at": "2026-07-02T00:00:00+00:00",
        "source_filename": raw.name,
        "checksum_sha256": "abc123",
    }

    fact = clean_redfin_zip_key_metrics(
        raw,
        record,
        geography,
        output,
        observations,
        coverage,
    )
    clean = pd.read_parquet(output)

    assert len(clean) == 2
    assert set(clean["postal_zip"]) == {"90001"}
    assert clean["primary_county_fips"].eq("06037").all()
    assert clean["region_type"].eq("Zip").all()
    assert clean["frequency"].eq("Rolling 3 Months").all()
    assert clean["rolling_window_months"].eq(3).all()
    assert not clean.duplicated(
        ["postal_zip", "period_end", "source_vintage"]
    ).any()
    assert len(fact) == 24
    assert fact["variable_id"].nunique() == 12
    assert fact["native_frequency"].eq("rolling_3_months").all()
    assert fact["checksum_sha256"].eq("abc123").all()
    assert fact["provider_last_updated"].eq("2026-06-03").all()
    assert (coverage / "redfin_zip_unmatched.csv").exists()
    unmatched = pd.read_csv(
        coverage / "redfin_zip_unmatched.csv", dtype={"postal_zip": str}
    )
    assert set(unmatched["postal_zip"]) == {"93001"}


def test_redfin_zip_schema_change_fails_clearly():
    with pytest.raises(ValueError, match="schema changed"):
        validate_redfin_zip_schema(REQUIRED_COLUMNS[:-1])


@pytest.mark.parametrize(
    ("columns", "version"),
    [
        (REQUIRED_COLUMNS, "legacy_key_metrics_v1"),
        (SUCCESSOR_COLUMNS, "key_metrics_days_yoy_v2"),
        (OFFICIAL_COLUMNS, "official_all_metrics_v3"),
    ],
)
def test_exact_versioned_contracts(columns, version):
    assert redfin_zip_schema_version(columns) == version
    with pytest.raises(ValueError, match="schema changed"):
        redfin_zip_schema_version(tuple(reversed(columns)))
    with pytest.raises(ValueError, match="schema changed"):
        redfin_zip_schema_version((*columns, "UNREVIEWED METRIC"))


@pytest.mark.parametrize("columns", [SUCCESSOR_COLUMNS, OFFICIAL_COLUMNS])
def test_successor_units_selection_raw_only_and_cache_version(tmp_path, columns):
    raw = tmp_path / "official.csv"
    records = []
    for postal_zip, begin, end in [
        ("09001", "2018-12-01", "2019-02-28"),
        ("09001", "2019-01-01", "2019-03-31"),
        ("09001", "2019-02-01", "2019-04-30"),
        ("93001", "2019-01-01", "2019-03-31"),
    ]:
        record = dict.fromkeys(columns, 123.0)
        record.update(_row(postal_zip, begin, end))
        record["MEDIAN DAYS ON MARKET YOY (DAYS)"] = -6.0
        records.append(record)
    pd.DataFrame(records, columns=columns).to_csv(raw, index=False)
    original_bytes = raw.read_bytes()
    geography = tmp_path / "geography.parquet"
    pd.DataFrame({
        "postal_zip": ["09001", "93001"], "county_fips": ["06037", "06111"]
    }).to_parquet(geography, index=False)
    output = tmp_path / "clean.parquet"
    observations = tmp_path / "observations.parquet"
    coverage = tmp_path / "coverage"
    manifest = {
        "data_vintage": "2026-09-25", "retrieved_at": "2026-09-25T00:00:00+00:00",
        "source_filename": raw.name, "checksum_sha256": "synthetic-fixture-checksum",
        "selected_start_date": "2019-01-01", "selected_end_date": "2019-03-31",
    }

    def clean():
        return clean_redfin_zip_key_metrics(
            raw, manifest, geography, output, observations, coverage
        )

    fact = clean()
    wide = pd.read_parquet(output)
    assert len(wide) == 1
    assert wide["postal_zip"].tolist() == ["09001"]
    assert wide["period_end"].dt.strftime("%Y-%m-%d").tolist() == ["2019-03-31"]
    assert wide["active_listings"].tolist() == [40]
    assert "inventory" not in wide
    assert wide[DOM_DAYS_COLUMN].tolist() == [-6.0]
    assert wide["median_days_on_market_yoy_pct_provider"].isna().all()
    day_observations = fact.loc[fact["variable_id"].eq(DOM_DAYS_VARIABLE)]
    assert day_observations["value"].tolist() == [-6.0]
    assert day_observations["unit"].tolist() == ["days"]
    legacy = fact.loc[fact["variable_id"].eq(
        "redfin_zip_median_days_on_market_yoy_pct_provider_rolling_3m"
    )]
    assert legacy["value"].isna().all()
    assert legacy["unit"].eq("percentage_points").all()
    assert legacy["quality_flag"].str.contains("source_field_not_in_schema").all()
    assert len(fact) == 13
    assert fact["transformation_id"].eq(
        f"redfin_zip_{redfin_zip_schema_version(columns)}_to_long_v2"
    ).all()
    metadata = json.loads(output.with_suffix(".metadata.json").read_text())
    assert "MEDIAN NEW LISTING PRICE PER SQ.FT. ($)" in metadata["raw_only_metric_fields"]
    assert metadata["source_schema_version"] == redfin_zip_schema_version(columns)
    audit = pd.read_csv(coverage / "redfin_zip_source_audit.csv")
    assert audit["total_rows"].iloc[0] == 4  # Entire national raw file was audited.
    assert raw.read_bytes() == original_bytes

    # Equal raw bytes with changed selection must rebuild instead of returning
    # cached rows. Missing cleaner-version metadata must also invalidate cache.
    manifest["selected_end_date"] = "2019-04-30"
    assert len(clean()) == 26
    metadata = json.loads(output.with_suffix(".metadata.json").read_text())
    metadata.pop("cleaner_version")
    output.with_suffix(".metadata.json").write_text(json.dumps(metadata))
    damaged = pd.read_parquet(output)
    damaged[DOM_DAYS_COLUMN] = 999
    damaged.to_parquet(output, index=False)
    clean()
    assert pd.read_parquet(output)[DOM_DAYS_COLUMN].eq(-6).all()
    assert raw.read_bytes() == original_bytes
    manifest["retrieved_at"] = "2026-09-26T00:00:00+00:00"
    manifest["source_filename"] = "official_redownload.csv"
    updated = clean()
    assert updated["retrieved_at"].eq(manifest["retrieved_at"]).all()
    assert updated["source_filename"].eq(manifest["source_filename"]).all()
    # A changed geography reference must be consulted even when raw is unchanged.
    pd.DataFrame({
        "postal_zip": ["09001", "93001"], "county_fips": ["06111", "06111"]
    }).to_parquet(geography, index=False)
    with pytest.raises(ValueError, match="No verified Los Angeles County ZIPs"):
        clean()


@pytest.mark.parametrize("columns", [REQUIRED_COLUMNS, SUCCESSOR_COLUMNS, OFFICIAL_COLUMNS])
def test_manual_import_accepts_versioned_contracts_and_preserves_archives(tmp_path, columns):
    config = replace(load_config(), root=tmp_path)
    source = next(item for item in config.sources if item.source_id == "redfin_zip_monthly")
    assert source.expected_columns == OFFICIAL_COLUMNS
    csv = tmp_path / "official_export.csv"
    payload = dict.fromkeys(columns, 0)
    payload.update(_row("09001", "2019-01-01", "2019-03-31"))
    payload["MEDIAN DAYS ON MARKET YOY (DAYS)"] = -6
    pd.DataFrame([payload], columns=columns).to_csv(csv, index=False)
    original = csv.read_bytes()
    raw_dir = tmp_path / "data" / "raw" / source.raw_subdir
    raw_dir.mkdir(parents=True)
    earlier = raw_dir / "earlier_vintage.csv"
    earlier.write_bytes(b"immutable earlier vintage")

    imported = _import_local_file(config, source, str(csv), force=False)
    assert imported["source_schema_version"] == redfin_zip_schema_version(columns)
    assert imported["provider_last_updated"] == "2026-06-03"
    assert imported["data_vintage"] == "2026-06-03"
    assert imported["original_filename"] == csv.name
    assert imported["download_url"] == ""
    assert "original download URL was not supplied" in imported["acquisition_note"]
    assert imported["response_latest_observation"] == "2019-03-31"
    archived = tmp_path / imported["relative_path"]
    assert archived.read_bytes() == original
    assert earlier.read_bytes() == b"immutable earlier vintage"
    reused = _import_local_file(config, source, str(csv), force=False)
    assert reused["relative_path"] == imported["relative_path"]
    forced = _import_local_file(config, source, str(csv), force=True)
    assert forced["relative_path"] != imported["relative_path"]
    assert archived.read_bytes() == original
    assert earlier.read_bytes() == b"immutable earlier vintage"

    invalid = tmp_path / "invalid.csv"
    pd.DataFrame([payload, payload], columns=columns).to_csv(invalid, index=False)
    manifest = tmp_path / "metadata" / "raw_file_manifest.csv"
    manifest_before = manifest.read_bytes()
    with pytest.raises(ValueError, match="duplicate_zip_periods=1"):
        _import_local_file(config, source, str(invalid), force=True)
    assert manifest.read_bytes() == manifest_before


@pytest.mark.parametrize("columns", [SUCCESSOR_COLUMNS, OFFICIAL_COLUMNS])
def test_provider_na_is_missing_in_audit_and_observations(tmp_path, columns):
    row = dict.fromkeys(columns, "NA")
    row.update(_row("90001", "2019-01-01", "2019-03-31"))
    row["HOMES SOLD"] = "NA"
    row["MEDIAN DAYS ON MARKET YOY (DAYS)"] = "NA"
    row["PENDING SALES"] = ""
    raw = tmp_path / "official.csv"
    pd.DataFrame([row], columns=columns).to_csv(raw, index=False)
    original = raw.read_bytes()
    geography = tmp_path / "geography.parquet"
    pd.DataFrame({"postal_zip": ["90001"], "county_fips": ["06037"]}).to_parquet(geography)
    record = {
        "data_vintage": "2026-06-03", "retrieved_at": "2026-09-25T00:00:00+00:00",
        "source_filename": raw.name, "checksum_sha256": "test-only-checksum",
    }
    observations = clean_redfin_zip_key_metrics(
        raw, record, geography, tmp_path / "clean.parquet",
        tmp_path / "observations.parquet", tmp_path / "coverage",
    )
    null_counts = pd.read_csv(
        tmp_path / "coverage" / "redfin_zip_source_null_counts.csv"
    ).set_index("source_field")["null_count"]
    for column in (
        "HOMES SOLD", "PENDING SALES", "MEDIAN DAYS ON MARKET YOY (DAYS)",
        "MEDIAN NEW LISTING PRICE PER SQ.FT. ($)",
    ):
        assert null_counts[column] == 1
    assert null_counts["ACTIVE LISTINGS"] == 0
    missing_ids = {
        "redfin_zip_homes_sold_rolling_3m", "redfin_zip_pending_sales_rolling_3m",
        DOM_DAYS_VARIABLE,
    }
    assert observations.loc[observations.variable_id.isin(missing_ids), "value"].isna().all()
    assert raw.read_bytes() == original


def _panel_qa_fixture():
    date = pd.Timestamp("2026-08-31")
    fact = pd.DataFrame({
        "observation_date": [date, date], "geo_id": ["ZIP_90001", "ZIP_90002"],
        "variable_id": ["zhvi", "zori_smoothed_sa"],
    })
    clean = pd.DataFrame({"period_end": [date, date], "postal_zip": ["90001", "90003"]})
    panel = pd.DataFrame({"date": [date, date], "postal_zip": ["90001", "90002"]})
    for metric in (
        "homes_sold", "median_sale_price_nsa", "median_days_on_market",
        "new_listings", "active_listings",
    ):
        clean[metric] = [10.0, 20.0]
        panel[metric] = [10.0, None]
    return fact, panel, clean


def test_redfin_source_only_keys_warn_without_expanding_zillow_spine():
    fact, panel, clean = _panel_qa_fixture()
    checks = {name: (severity, rows) for name, severity, rows, _ in _redfin_zip_panel_checks(fact, panel, clean)}
    assert all(rows.empty for severity, rows in checks.values() if severity == "error")
    severity, outside = checks["redfin_zip_outside_zillow_spine"]
    assert severity == "warning"
    assert outside["postal_zip"].tolist() == ["90003"]
    assert set(clean.postal_zip) == {"90001", "90003"}
    assert set(panel.postal_zip) == {"90001", "90002"}


@pytest.mark.parametrize("defect", ["corrupt_value", "missing_spine_row", "extra_spine_row"])
def test_redfin_matched_values_and_exact_spine_remain_structural_errors(defect):
    fact, panel, clean = _panel_qa_fixture()
    if defect == "corrupt_value":
        panel.loc[panel.postal_zip.eq("90001"), "homes_sold"] = 999.0
        expected = "redfin_zip_panel_matches_clean"
    elif defect == "missing_spine_row":
        panel = panel.loc[panel.postal_zip.ne("90001")]
        expected = "redfin_zip_left_join_preserves_zillow_spine"
    else:
        extra = panel.iloc[[0]].copy()
        extra["postal_zip"] = "90003"
        panel = pd.concat([panel, extra], ignore_index=True)
        expected = "redfin_zip_left_join_preserves_zillow_spine"
    checks = {name: (severity, rows) for name, severity, rows, _ in _redfin_zip_panel_checks(fact, panel, clean)}
    severity, failures = checks[expected]
    assert severity == "error"
    assert not failures.empty
    assert checks["redfin_zip_outside_zillow_spine"][0] == "warning"
