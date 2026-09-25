from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.ahs_table_creator import (
    AGGREGATE_PROXY_VARIABLE_ID,
    EXPECTED_COLUMNS,
    EXPECTED_SECTION_SEQUENCE,
    HOUSEHOLDS_VARIABLE_ID,
    OUTSTANDING_PRINCIPAL_BINS,
    SOURCE_ID,
    clean_ahs_table_creator,
    parse_ahs_table_creator,
)
from src.common.config import load_config
from src.ingestion.download import download_sources


BINS = (
    ("Less than $20,000", "120.8", "36.2"),
    ("$20,000 to $39,999", "112.0", "42.7"),
    ("$40,000 to $59,999", "58.9", "20.8"),
    ("$60,000 to $79,999", "77.4", "25.9"),
    ("$80,000 to $99,999", "128.0", "32.8"),
    ("$100,000 to $119,999", "138.5", "37.9"),
    ("$120,000 to $149,999", "95.5", "28.4"),
    ("$150,000 to $199,999", "236.7", "44.5"),
    ("$200,000 to $249,999", "297.0", "61.9"),
    ("$250,000 to $299,999", "301.3", "55.7"),
    ("$300,000 to $349,999", "292.2", "51.1"),
    ("$350,000 to $399,999", "255.6", "45.5"),
    ("$400,000 to $499,999", "389.5", "61.4"),
    ("$500,000 or more", "877.8", "97.4"),
)


def _fixture_rows(
    *,
    bins: tuple[tuple[str, str, str], ...] = BINS,
    section_sequence: tuple[str, ...] = EXPECTED_SECTION_SEQUENCE,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = [
        ("CHARACTERISTICS OF PRIMARY MORTGAGES", "", ""),
        ("", "", ""),
        ("Households with at least one regular mortgage", "4,585.2", "150.5"),
        ("", "", ""),
    ]
    for heading in section_sequence[1:]:
        rows.append((heading, "", ""))
        if heading == "Total Outstanding Principal Amount":
            rows.append(("", "", ""))
            rows.extend(bins)
            rows.extend(
                [
                    ("Not reported", "1,203.8", "100.8"),
                    ("Median (dollars)", "301,000.0", "15,260.0"),
                    ("Mean (dollars)", "418,100.0", "31,960.0"),
                    ("", "", ""),
                ]
            )
    return rows


def _write_fixture(
    path: Path,
    *,
    columns: tuple[str, ...] = EXPECTED_COLUMNS,
    bins: tuple[tuple[str, str, str], ...] = BINS,
    section_sequence: tuple[str, ...] = EXPECTED_SECTION_SEQUENCE,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(columns)
        rows = _fixture_rows(bins=bins, section_sequence=section_sequence)
        if len(columns) == 2:
            writer.writerows((label, estimate) for label, estimate, _moe in rows)
        else:
            writer.writerows(rows)


def _record() -> dict[str, str]:
    return {
        "source_id": SOURCE_ID,
        "data_vintage": "2023",
        "retrieved_at": "2026-08-10T21:44:02+00:00",
        "source_filename": "AHS_TABLE14A_2023_CA_archive.csv",
        "checksum_sha256": "a" * 64,
        "provider_last_updated": "2026-08-10",
    }


def test_clean_ahs_emits_reported_case_proxy_and_audit_components(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ahs.csv"
    output = tmp_path / "ahs.parquet"
    _write_fixture(source)

    frame = clean_ahs_table_creator(
        source,
        _record(),
        output,
        expected_source_period=2023,
        expected_geography="California",
    )

    assert output.exists()
    assert len(frame) == 37
    assert frame["variable_id"].nunique() == 37
    assert set(frame["geo_id"]) == {"STATE_06"}
    assert set(frame["observation_date"]) == {pd.Timestamp("2023-12-31")}
    assert set(frame["source_id"]) == {SOURCE_ID}
    assert set(frame["checksum_sha256"]) == {"a" * 64}
    assert not frame["is_interpolated"].any()

    households = frame.loc[
        frame["variable_id"].eq(HOUSEHOLDS_VARIABLE_ID)
    ].iloc[0]
    assert households["value"] == pytest.approx(4_585_200.0)
    assert households["reported_value"] == pytest.approx(4_585.2)
    assert households["unit"] == "households"
    assert bool(households["is_observed"])
    assert not bool(households["is_proxy"])

    households_moe = frame.loc[
        frame["variable_id"].eq(f"{HOUSEHOLDS_VARIABLE_ID}_moe")
    ].iloc[0]
    assert households_moe["value"] == pytest.approx(150_500.0)
    assert households_moe["reported_value"] == pytest.approx(150.5)

    aggregate = frame.loc[
        frame["variable_id"].eq(AGGREGATE_PROXY_VARIABLE_ID)
    ].iloc[0]
    assert aggregate["value"] == pytest.approx(1_413_679_720_000.0)
    assert pd.isna(aggregate["reported_value"])
    assert bool(aggregate["is_proxy"])
    assert not bool(aggregate["is_observed"])
    assert "excludes_not_reported_cases" in aggregate["quality_flag"]
    assert "no_covariance_based_standard_error_or_ci" in aggregate["quality_flag"]
    assert not frame["variable_id"].str.contains("all_household").any()
    assert not frame["variable_id"].eq(
        f"{AGGREGATE_PROXY_VARIABLE_ID}_moe"
    ).any()


def test_parse_rejects_export_without_moe_column(tmp_path: Path) -> None:
    source = tmp_path / "without_moe.csv"
    _write_fixture(source, columns=("Characteristics", "Estimate"))

    with pytest.raises(ValueError, match="column contract changed"):
        parse_ahs_table_creator(
            source,
            expected_source_period=2023,
            expected_geography="California",
        )


def test_parse_rejects_section_order_drift(tmp_path: Path) -> None:
    source = tmp_path / "section_drift.csv"
    sections = list(EXPECTED_SECTION_SEQUENCE)
    sections[2], sections[3] = sections[3], sections[2]
    _write_fixture(source, section_sequence=tuple(sections))

    with pytest.raises(ValueError, match="section sequence changed"):
        parse_ahs_table_creator(
            source,
            expected_source_period=2023,
            expected_geography="California",
        )


def test_parse_rejects_reported_bin_reconciliation_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bad_reconciliation.csv"
    bad_bins = (*BINS[:-1], (BINS[-1][0], "870.0", BINS[-1][2]))
    _write_fixture(source, bins=bad_bins)

    with pytest.raises(ValueError, match="reconciliation failed"):
        parse_ahs_table_creator(
            source,
            expected_source_period=2023,
            expected_geography="California",
        )


@pytest.mark.parametrize(
    ("source_period", "geography", "message"),
    [
        (None, "California", "source period 2023"),
        (2022, "California", "source period 2023"),
        (2023, None, "geography California"),
        (2023, "United States", "geography California"),
    ],
)
def test_parse_requires_explicit_2023_california_context(
    tmp_path: Path,
    source_period: int | None,
    geography: str | None,
    message: str,
) -> None:
    source = tmp_path / "context.csv"
    _write_fixture(source)

    with pytest.raises(ValueError, match=message):
        parse_ahs_table_creator(
            source,
            expected_source_period=source_period,
            expected_geography=geography,
        )


def test_clean_rejects_missing_provenance(tmp_path: Path) -> None:
    source = tmp_path / "ahs.csv"
    _write_fixture(source)
    record = _record()
    record["checksum_sha256"] = ""

    with pytest.raises(ValueError, match="provenance fields"):
        clean_ahs_table_creator(
            source,
            record,
            tmp_path / "ahs.parquet",
            expected_source_period=2023,
            expected_geography="California",
        )


def test_bin_labels_match_official_target_contract() -> None:
    assert tuple(label for label, _slug in OUTSTANDING_PRINCIPAL_BINS) == tuple(
        label for label, _estimate, _moe in BINS
    )


def test_ahs_source_and_every_emitted_variable_are_configured() -> None:
    config = load_config()
    source = next(item for item in config.sources if item.source_id == SOURCE_ID)
    configured_ids = {str(item["variable_id"]) for item in config.variables}

    assert source.auto_download is False
    assert source.expected_columns == EXPECTED_COLUMNS
    assert source.selected_start_date == "2023-01-01"
    assert source.selected_end_date == "2023-12-31"
    assert len(source.key_variables) == 37
    assert set(source.key_variables).issubset(configured_ids)
    assert AGGREGATE_PROXY_VARIABLE_ID in source.key_variables
    assert not any("all_household" in item for item in source.key_variables)


def test_ahs_local_import_preserves_2023_context_and_checksum(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "AHS TABLE14A official.csv"
    _write_fixture(source_path)
    base = load_config()
    ahs_source = next(item for item in base.sources if item.source_id == SOURCE_ID)
    config = replace(base, root=tmp_path, sources=(ahs_source,))

    completed, manual = download_sources(
        config,
        SOURCE_ID,
        False,
        False,
        None,
        None,
        str(source_path),
    )

    assert manual == []
    assert len(completed) == 1
    record = completed[0]
    assert record["data_vintage"] == "2023"
    assert record["provider_vintage"] == "2023"
    assert record["survey_year"] == "2023"
    assert record["original_filename"] == source_path.name
    assert record["request_query"] == ahs_source.download_url
    archived = tmp_path / record["relative_path"]
    assert archived.read_bytes() == source_path.read_bytes()
