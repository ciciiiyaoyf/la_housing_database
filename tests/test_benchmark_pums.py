import math
import zipfile

import pandas as pd
import pytest

from src.benchmark.pums import (
    PUMSFormatError,
    PUMS_REQUIRED_COLUMNS,
    build_pums_pi_share_parameters,
    derive_wholly_contained_pumas,
    filter_owner_first_mortgage,
    read_pums_housing,
    read_tract_puma_relationship,
)
from src.benchmark.schema import BENCHMARK_COLUMNS, benchmark_contract_errors


def _pums_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "WGTP": [2, 1, 1, 1, 100, 100],
            "TEN": [1, 1, 1, 1, 3, 1],
            "MRGX": [1, 1, 1, 1, 1, 2],
            "MRGP": [2_000, 1_000, 1_000, 1_100, 9_999, 9_999],
            "MRGT": [1, 2, 2, None, 2, 2],
            "MRGI": [1, 2, 2, 2, 2, 2],
            "SMP": [0, 0, 0, 0, 0, 0],
            "TAXAMT": [12_000, None, None, 1_200, None, None],
            "INSP": [1_200, None, None, 600, None, None],
            "SMOCP": [2_500, 1_500, 2_000, 1_600, 9_999, 9_999],
            "ADJHSG": [1_000_000] * 6,
            "PUMA": ["00101", "00101", "00102", "00103", "00101", "00101"],
        }
    )


def _pipe_relationship(tmp_path) -> pd.DataFrame:
    path = tmp_path / "tract_to_puma_2020.txt"
    path.write_text(
        "\n".join(
            [
                "STATEFP20|COUNTYFP20|TRACTCE20|PUMA5CE20",
                "06|037|000100|00101",
                "06|037|000200|00101",
                "06|059|000100|00102",
                "06|065|000100|00102",
                "06|073|000100|00103",
                "41|001|000100|00101",
            ]
        ),
        encoding="utf-8",
    )
    return read_tract_puma_relationship(
        path,
        relationship_vintage=2020,
    )


def test_reads_csv_or_single_csv_zip_and_preserves_missing_flags(tmp_path):
    source = _pums_frame().iloc[:2].copy()
    source.loc[1, "MRGT"] = None
    csv_path = tmp_path / "psam_h06.csv"
    source.to_csv(csv_path, index=False)

    csv_result = read_pums_housing(csv_path)
    assert tuple(csv_result.columns) == PUMS_REQUIRED_COLUMNS
    assert csv_result["PUMA"].tolist() == ["00101", "00101"]
    assert pd.isna(csv_result.loc[1, "MRGT"])
    assert len(csv_result.attrs["checksum_sha256"]) == 64

    zip_path = tmp_path / "csv_hca.zip"
    payload = source.to_csv(index=False).encode()
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("nested/psam_h06.csv", payload)
        archive.writestr("README.txt", "official archive notes")
    zip_result = read_pums_housing(zip_path)
    pd.testing.assert_frame_equal(csv_result, zip_result)
    assert zip_result.attrs["source_filename"] == "csv_hca.zip"
    assert zip_result.attrs["source_member_filename"] == "nested/psam_h06.csv"


def test_rejects_missing_columns_and_multi_csv_zip(tmp_path):
    missing_path = tmp_path / "missing.csv"
    _pums_frame().drop(columns="SMP").to_csv(missing_path, index=False)
    with pytest.raises(PUMSFormatError, match="SMP"):
        read_pums_housing(missing_path)

    zip_path = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("one.csv", "WGTP\n1\n")
        archive.writestr("two.csv", "WGTP\n1\n")
    with pytest.raises(PUMSFormatError, match="exactly one CSV"):
        read_pums_housing(zip_path)


@pytest.mark.parametrize(
    ("delimiter", "header"),
    [
        ("|", "STATEFP10|COUNTYFP10|TRACTCE10|PUMA5CE10"),
        (",", "STATEFP,COUNTYFP,TRACTCE,PUMA5CE"),
    ],
)
def test_relationship_reader_supports_2010_2020_layouts_and_delimiters(
    tmp_path,
    delimiter,
    header,
):
    path = tmp_path / "relationship.txt"
    rows = [
        header,
        delimiter.join(["6", "37", "100", "101"]),
        delimiter.join(["6", "37", "200", "101"]),
    ]
    path.write_text("\n".join(rows), encoding="utf-8")
    result = read_tract_puma_relationship(
        path,
        relationship_vintage=2010,
    )
    assert result.loc[0, "STATEFP"] == "06"
    assert result.loc[0, "COUNTYFP"] == "037"
    assert result.loc[0, "TRACTCE"] == "000100"
    assert result.loc[0, "PUMA5CE"] == "00101"


def test_wholly_contained_mapping_excludes_shared_pumas(tmp_path):
    relationship = _pipe_relationship(tmp_path)
    mapping = derive_wholly_contained_pumas(relationship)

    assert set(mapping["PUMA"]) == {"00101", "00103"}
    assert dict(zip(mapping["PUMA"], mapping["county_fips"], strict=True)) == {
        "00101": "06037",
        "00103": "06073",
    }
    assert mapping["is_wholly_contained"].all()
    assert mapping["county_all_pumas_wholly_contained"].all()


def test_filter_requires_owner_occupied_first_mortgage_and_rejects_bad_codes():
    result = filter_owner_first_mortgage(_pums_frame())
    assert len(result) == 4
    assert set(result["PUMA"]) == {"00101", "00102", "00103"}

    bad = _pums_frame()
    bad["TEN"] = bad["TEN"].astype(object)
    bad.loc[0, "TEN"] = "owner"
    with pytest.raises(PUMSFormatError, match="TEN contains nonnumeric"):
        filter_owner_first_mortgage(bad)


def test_builds_source_period_parameters_without_silent_county_fallback(tmp_path):
    relationship = _pipe_relationship(tmp_path)
    result = build_pums_pi_share_parameters(
        _pums_frame(),
        relationship,
        benchmark_vintage="2023_ACS_5YR_PUMS",
        source_period_start="2019-01-01",
        source_period_end="2023-12-31",
        reference_date="2021-07-01",
        release_date="2024-12-12",
        relationship_vintage=2020,
        minimum_sample=1,
    )

    assert set(result["parameter_geography"]) == {
        "state:06",
        "county:06037",
        "county:06073",
    }
    # The shared PUMA 00102 contributes to California but is not assigned to
    # either county that it intersects.
    assert "county:06059" not in set(result["parameter_geography"])
    assert "county:06065" not in set(result["parameter_geography"])

    state = result.set_index("parameter_geography").loc["state:06"]
    la = result.set_index("parameter_geography").loc["county:06037"]
    san_diego = result.set_index("parameter_geography").loc["county:06073"]
    assert state["sample_n"] == 3
    assert state["input_n"] == 4
    assert state["coverage_rate"] == pytest.approx(0.75)
    assert state["value"] == pytest.approx(3_800 / 8_500)
    assert la["sample_n"] == 2
    assert la["value"] == pytest.approx(2_800 / 6_500)
    assert la["parameter_geography_level"] == "county"

    # An unknown/missing escrow flag is not guessed; the county row and its
    # diagnostics remain present for an explicit downstream fallback choice.
    assert san_diego["input_n"] == 1
    assert san_diego["sample_n"] == 0
    assert san_diego["mrgt_missing_n"] == 1
    assert math.isnan(san_diego["value"])
    assert san_diego["is_missing"]
    assert san_diego["fallback_recommended"]

    assert result["month"].isna().all()
    assert not result["is_interpolated"].any()
    assert set(result["monthly_method"]) == {"not_applicable_source_period"}
    assert set(result["benchmark_vintage"]) == {"2023_ACS_5YR_PUMS"}
    assert set(result["source_period_start"]) == {pd.Timestamp("2019-01-01")}
    assert set(result["reference_date"]) == {pd.Timestamp("2021-07-01")}
    assert set(BENCHMARK_COLUMNS).issubset(result.columns)
    assert (
        benchmark_contract_errors(result.loc[result["county_fips"].notna()])
        == []
    )


def test_minimum_sample_suppresses_value_but_keeps_raw_diagnostics(tmp_path):
    result = build_pums_pi_share_parameters(
        _pums_frame(),
        _pipe_relationship(tmp_path),
        benchmark_vintage="2023_ACS_5YR_PUMS",
        source_period_start="2019-01-01",
        source_period_end="2023-12-31",
        reference_date="2021-07-01",
        minimum_sample=3,
    )
    la = result.set_index("parameter_geography").loc["county:06037"]
    assert la["sample_n"] == 2
    assert math.isnan(la["value"])
    assert la["raw_pi_share"] == pytest.approx(2_800 / 6_500)
    assert la["is_suppressed"]
    assert la["missing_reason"] == "below_minimum_sample"
