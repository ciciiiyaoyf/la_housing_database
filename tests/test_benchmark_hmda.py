import math
from zipfile import ZipFile

import pandas as pd
import pytest

from src.benchmark.hmda import (
    HMDAFormatError,
    clean_hmda_modified_lar,
    iter_hmda_modified_lar_chunks,
)


def _record() -> dict[str, str]:
    return {
        "source_id": "hmda_modified_lar_2023",
        "data_vintage": "2024-03-26",
        "retrieved_at": "2026-07-25T12:00:00+00:00",
        "source_filename": "2023_public_lar.csv",
        "checksum_sha256": "source-manifest-checksum",
        "release_date": "2024-03-26",
    }


def _base_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "activity_year": "2023",
        "state_code": "CA",
        "county_code": "06037",
        "action_taken": "1",
        "loan_type": "1",
        "loan_purpose": "1",
        "lien_status": "1",
        "combined_loan_to_value_ratio": "80",
        "property_value": "100",
        "occupancy_type": "1",
        # This input must never be used to repair CLTV.
        "loan_amount": "80",
    }
    row.update(updates)
    return row


def _metric(
    result: pd.DataFrame,
    variable_name: str,
) -> pd.Series:
    match = result.loc[result["variable_name"].eq(variable_name)]
    assert len(match) == 1
    return match.iloc[0]


def test_csv_filters_universe_and_builds_all_and_loan_type_metrics(tmp_path):
    rows = [
        _base_row(property_value="100", combined_loan_to_value_ratio="79"),
        _base_row(property_value="200", combined_loan_to_value_ratio="95"),
        _base_row(
            loan_type="2",
            property_value="300",
            combined_loan_to_value_ratio="89",
        ),
        _base_row(
            loan_type="3",
            property_value="400",
            combined_loan_to_value_ratio="110",
        ),
        _base_row(
            loan_type="4",
            property_value="500",
            combined_loan_to_value_ratio="100",
        ),
        _base_row(
            property_value="600",
            combined_loan_to_value_ratio="",
            loan_amount="480",
        ),
        _base_row(action_taken="2", property_value="999"),
        _base_row(loan_purpose="2", property_value="999"),
        _base_row(occupancy_type="2", property_value="999"),
        _base_row(lien_status="2", property_value="999"),
        _base_row(state_code="NV", county_code="32003", property_value="999"),
    ]
    source = tmp_path / "lar.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    result = clean_hmda_modified_lar(
        source,
        _record(),
        minimum_sample=2,
        split_minimum_sample=2,
        chunksize=2,
    )

    assert len(result) == 4 * 7
    assert set(result["loan_type_group"]) == {
        "all",
        "conventional",
        "fha",
        "va",
    }
    assert result["county_fips"].unique().tolist() == ["06037"]
    assert result["county_fips"].map(type).eq(str).all()

    median = _metric(
        result,
        "hmda_median_downpayment_rate_benchmark",
    )
    assert median["input_n"] == 6
    assert median["sample_n"] == 5
    assert median["coverage_rate"] == pytest.approx(5 / 6)
    assert median["value"] == pytest.approx(0.05)
    assert median["negative_downpayment_n"] == 1

    weighted = _metric(
        result,
        "hmda_property_value_weighted_downpayment_rate_benchmark",
    )
    assert weighted["value"] == pytest.approx(24 / 1500)
    aggregate = _metric(
        result,
        "hmda_aggregate_downpayment_flow_benchmark",
    )
    assert aggregate["value"] == pytest.approx(24)
    assert _metric(
        result,
        "hmda_share_downpayment_below_3pct_benchmark",
    )["value"] == pytest.approx(2 / 5)
    assert _metric(
        result,
        "hmda_share_downpayment_below_5pct_benchmark",
    )["value"] == pytest.approx(2 / 5)
    assert _metric(
        result,
        "hmda_share_downpayment_below_10pct_benchmark",
    )["value"] == pytest.approx(3 / 5)
    assert _metric(
        result,
        "hmda_share_downpayment_below_20pct_benchmark",
    )["value"] == pytest.approx(4 / 5)

    conventional = _metric(
        result,
        "hmda_median_downpayment_rate_conventional_benchmark",
    )
    assert conventional["input_n"] == 3
    assert conventional["sample_n"] == 2
    assert conventional["value"] == pytest.approx(0.13)


def test_low_sample_splits_are_null_with_diagnostics_and_metadata(tmp_path):
    source = tmp_path / "lar.csv"
    pd.DataFrame(
        [
            _base_row(loan_type="1"),
            _base_row(
                loan_type="2",
                property_value="250",
                combined_loan_to_value_ratio="96",
            ),
        ]
    ).to_csv(source, index=False)

    result = clean_hmda_modified_lar(
        source,
        _record(),
        minimum_sample=2,
        split_minimum_sample=2,
        chunksize=1,
    )

    fha = _metric(
        result,
        "hmda_median_downpayment_rate_fha_benchmark",
    )
    assert fha["input_n"] == 1
    assert fha["sample_n"] == 1
    assert fha["coverage_rate"] == 1.0
    assert not bool(fha["meets_minimum_sample"])
    assert bool(fha["is_suppressed"])
    assert fha["quality_flag"] == "insufficient_sample"
    assert math.isnan(fha["value"])
    assert "below the minimum of 2" in fha["notes"]

    va = _metric(
        result,
        "hmda_median_downpayment_rate_va_benchmark",
    )
    assert va["input_n"] == 0
    assert va["sample_n"] == 0
    assert math.isnan(va["coverage_rate"])
    assert math.isnan(va["value"])

    all_loans = _metric(
        result,
        "hmda_median_downpayment_rate_benchmark",
    )
    assert not bool(all_loans["is_suppressed"])
    assert all_loans["quality_flag"] == "reported"

    assert fha["database_version"] == "A. Benchmark"
    assert fha["source_frequency"] == "annual"
    assert fha["source_period_start"] == "2023-01-01"
    assert fha["source_period_end"] == "2023-12-31"
    assert fha["reference_date"] == "2023-07-01"
    assert fha["month"] is None
    assert fha["release_date"] == "2024-03-26"
    assert not bool(fha["is_modeled"])
    assert not bool(fha["is_nowcast"])
    assert fha["benchmark_vintage"] == "2023_HMDA_Modified_LAR"
    assert fha["source_vintage"] == "2024-03-26"
    assert fha["retrieved_at"] == "2026-07-25T12:00:00+00:00"
    assert fha["checksum_sha256"] == "source-manifest-checksum"
    assert fha["source_filename"] == "2023_public_lar.csv"
    assert "Cash buyers are excluded" in fha["universe_definition"]
    assert "never replaced with loan_amount/property_value" in fha["notes"]


def test_zip_with_one_csv_is_read_in_chunks_and_preserves_county_fips(tmp_path):
    csv_path = tmp_path / "inside.csv"
    pd.DataFrame(
        [
            _base_row(county_code="06001"),
            _base_row(county_code="06037"),
            _base_row(county_code="06075"),
        ]
    ).to_csv(csv_path, index=False)
    archive = tmp_path / "lar.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.write(csv_path, arcname="nested/modified_lar.csv")

    chunks = list(iter_hmda_modified_lar_chunks(archive, chunksize=1))
    assert [len(chunk) for chunk in chunks] == [1, 1, 1]
    assert [chunk.iloc[0]["county_code"] for chunk in chunks] == [
        "06001",
        "06037",
        "06075",
    ]

    result = clean_hmda_modified_lar(
        archive,
        _record(),
        minimum_sample=1,
        split_minimum_sample=1,
        chunksize=1,
    )
    assert result["county_fips"].drop_duplicates().tolist() == [
        "06001",
        "06037",
        "06075",
    ]


def test_zip_with_provider_pipe_delimited_txt_is_supported(tmp_path):
    txt_path = tmp_path / "2025_combined_mlar.txt"
    pd.DataFrame(
        [
            _base_row(activity_year="2025", county_code="06037"),
            _base_row(activity_year="2025", county_code="06059"),
        ]
    ).to_csv(txt_path, sep="|", index=False)
    archive = tmp_path / "2025_combined_mlar_header.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.write(txt_path, arcname=txt_path.name)

    chunks = list(iter_hmda_modified_lar_chunks(archive, chunksize=1))
    assert [chunk.iloc[0]["county_code"] for chunk in chunks] == [
        "06037",
        "06059",
    ]


def test_cltv_is_never_repaired_from_loan_amount(tmp_path):
    source = tmp_path / "lar.csv"
    pd.DataFrame(
        [
            _base_row(
                property_value="500000",
                loan_amount="400000",
                combined_loan_to_value_ratio="",
            )
        ]
    ).to_csv(source, index=False)

    result = clean_hmda_modified_lar(
        source,
        _record(),
        minimum_sample=1,
        split_minimum_sample=1,
    )
    all_median = _metric(
        result,
        "hmda_median_downpayment_rate_benchmark",
    )
    assert all_median["input_n"] == 1
    assert all_median["property_value_positive_n"] == 1
    assert all_median["cltv_valid_n"] == 0
    assert all_median["sample_n"] == 0
    assert math.isnan(all_median["value"])


def test_zip_must_contain_exactly_one_supported_data_file(tmp_path):
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("one.csv", "activity_year\n2023\n")
        zipped.writestr("two.csv", "activity_year\n2023\n")

    with pytest.raises(HMDAFormatError, match="exactly one CSV or TXT"):
        list(iter_hmda_modified_lar_chunks(archive))


def test_release_date_is_explicit_and_not_inferred(tmp_path):
    source = tmp_path / "lar.csv"
    pd.DataFrame([_base_row()]).to_csv(source, index=False)
    record = _record()
    del record["release_date"]

    with pytest.raises(ValueError, match="release_date must be supplied"):
        clean_hmda_modified_lar(source, record)


def test_missing_modified_lar_schema_is_structural_failure(tmp_path):
    source = tmp_path / "lar.csv"
    pd.DataFrame({"activity_year": ["2023"]}).to_csv(source, index=False)

    with pytest.raises(HMDAFormatError, match="missing required"):
        list(iter_hmda_modified_lar_chunks(source))
