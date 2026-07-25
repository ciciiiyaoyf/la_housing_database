import math
import zipfile

import pandas as pd
import pytest

from src.benchmark.pums import (
    PUMSFormatError,
    PUMS_REPLICATE_WEIGHT_COLUMNS,
    PUMS_REQUIRED_COLUMNS,
    build_pums_pi_share_parameters,
    derive_county_puma_pool_hierarchy,
    derive_wholly_contained_pumas,
    filter_owner_first_mortgage,
    read_pums_housing,
    read_tract_puma_relationship,
    resolve_pums_geography_contract,
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


def _with_replicate_weights(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in PUMS_REPLICATE_WEIGHT_COLUMNS:
        result[column] = result["WGTP"]
    return result


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
    assert "PUMA" in csv_result.attrs["source_columns"]

    zip_path = tmp_path / "csv_hca.zip"
    payload = source.to_csv(index=False).encode()
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("nested/psam_h06.csv", payload)
        archive.writestr("README.txt", "official archive notes")
    zip_result = read_pums_housing(zip_path)
    pd.testing.assert_frame_equal(csv_result, zip_result)
    assert zip_result.attrs["source_filename"] == "csv_hca.zip"
    assert zip_result.attrs["source_member_filename"] == "nested/psam_h06.csv"


def test_reader_retains_full_header_for_2022_dual_puma_guard(tmp_path):
    source = _pums_frame().iloc[:2].copy()
    source["PUMA10"] = source["PUMA"]
    source["PUMA20"] = source["PUMA"]
    path = tmp_path / "dual_2022.csv"
    source.to_csv(path, index=False)

    result = read_pums_housing(path)
    assert tuple(result.columns) == PUMS_REQUIRED_COLUMNS
    assert {"PUMA10", "PUMA20"}.issubset(result.attrs["source_columns"])
    with pytest.raises(PUMSFormatError, match="dual-vintage"):
        resolve_pums_geography_contract(
            2022,
            columns=result.attrs["source_columns"],
        )


def test_reader_retains_complete_replicate_set_and_rejects_partial_set(
    tmp_path,
):
    source = _with_replicate_weights(_pums_frame().iloc[:2])
    source.loc[0, "WGTP1"] = -1
    complete_path = tmp_path / "complete_replicates.csv"
    source.to_csv(complete_path, index=False)

    result = read_pums_housing(complete_path)
    assert tuple(result.columns) == (
        *PUMS_REQUIRED_COLUMNS,
        *PUMS_REPLICATE_WEIGHT_COLUMNS,
    )
    assert result.loc[0, "WGTP1"] == -1
    assert all(
        pd.api.types.is_integer_dtype(result[column])
        for column in PUMS_REPLICATE_WEIGHT_COLUMNS
    )

    partial = _pums_frame().iloc[:2].copy()
    partial["WGTP1"] = partial["WGTP"]
    partial_path = tmp_path / "partial_replicates.csv"
    partial.to_csv(partial_path, index=False)
    with pytest.raises(PUMSFormatError, match="partial PUMS replicate-weight"):
        read_pums_housing(partial_path)
    with pytest.raises(PUMSFormatError, match="partial PUMS replicate-weight"):
        filter_owner_first_mortgage(partial)


@pytest.mark.parametrize("bad_value", [None, "not-a-weight", 1.5])
def test_reader_rejects_invalid_replicate_weight_values(tmp_path, bad_value):
    source = _with_replicate_weights(_pums_frame().iloc[:2])
    source["WGTP7"] = source["WGTP7"].astype(object)
    source.loc[0, "WGTP7"] = bad_value
    path = tmp_path / "invalid_replicate.csv"
    source.to_csv(path, index=False)

    with pytest.raises(PUMSFormatError, match="replicate weights"):
        read_pums_housing(path)


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


def test_release_contract_rejects_mixed_or_unverified_puma_vintages():
    assert resolve_pums_geography_contract(
        2021, columns=PUMS_REQUIRED_COLUMNS
    )["relationship_vintage"] == 2010
    updated_2022 = resolve_pums_geography_contract(
        2022, columns=PUMS_REQUIRED_COLUMNS
    )
    assert updated_2022["relationship_vintage"] == 2020
    assert updated_2022["is_census_crosswalked"]

    with pytest.raises(PUMSFormatError, match="mixed 2000/2010"):
        resolve_pums_geography_contract(
            2015, columns=PUMS_REQUIRED_COLUMNS
        )
    with pytest.raises(PUMSFormatError, match="dual-vintage"):
        resolve_pums_geography_contract(
            2022, columns=(*PUMS_REQUIRED_COLUMNS, "PUMA10", "PUMA20")
        )
    with pytest.raises(PUMSFormatError, match="column inspection"):
        resolve_pums_geography_contract(2022)
    with pytest.raises(PUMSFormatError, match="not yet verified"):
        resolve_pums_geography_contract(
            2025, columns=PUMS_REQUIRED_COLUMNS
        )
    with pytest.raises(PUMSFormatError, match="requires the 2010"):
        resolve_pums_geography_contract(
            2021,
            columns=PUMS_REQUIRED_COLUMNS,
            relationship_vintage=2020,
        )


def test_pool_hierarchy_uses_complete_county_then_connected_region(tmp_path):
    hierarchy = derive_county_puma_pool_hierarchy(
        _pipe_relationship(tmp_path),
        relationship_vintage=2020,
    ).set_index("county_fips")

    assert hierarchy.loc["06037", "preferred_geography_tier"] == "county"
    assert hierarchy.loc["06037", "pool_member_pumas"] == "00101"
    assert hierarchy.loc["06059", "preferred_geography_tier"] == "regional"
    assert (
        hierarchy.loc["06059", "preferred_parameter_geography"]
        == "puma_region:06059+06065"
    )
    assert hierarchy.loc["06059", "pool_member_counties"] == "06059|06065"
    assert hierarchy.loc["06059", "pool_member_pumas"] == "00102"
    assert hierarchy.loc["06059", "county_shared_puma_count"] == 1


def test_filter_requires_owner_occupied_first_mortgage_and_rejects_bad_codes():
    result = filter_owner_first_mortgage(_pums_frame())
    assert len(result) == 4
    assert set(result["PUMA"]) == {"00101", "00102", "00103"}

    bad = _pums_frame()
    bad["TEN"] = bad["TEN"].astype(object)
    bad.loc[0, "TEN"] = "owner"
    with pytest.raises(PUMSFormatError, match="TEN contains nonnumeric"):
        filter_owner_first_mortgage(bad)


def test_builds_record_pooled_county_regional_and_state_parameters(tmp_path):
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
        require_complete_california_relationship=False,
        source_vintage="2023",
        source_filename="csv_hca.zip",
        retrieved_at="2024-12-13T00:00:00+00:00",
        checksum_sha256="pums-checksum",
        relationship_source_filename="tract_to_puma_2020.txt",
        relationship_retrieved_at="2024-12-13T00:00:00+00:00",
        relationship_checksum_sha256="relationship-checksum",
    )

    pi = result.loc[
        result["variable_name"].eq(
            "pums_first_mortgage_pi_share_benchmark"
        )
    ].set_index("county_fips", drop=False)
    junior = result.loc[
        result["variable_name"].eq(
            "pums_junior_lien_payment_per_mortgaged_unit_benchmark"
        )
    ].set_index("county_fips", drop=False)

    state = pi.loc[pd.NA]
    la = pi.loc["06037"]
    orange_riverside = pi.loc[["06059", "06065"]]
    san_diego = pi.loc["06073"]
    assert state["sample_n"] == 3
    assert state["input_n"] == 4
    assert state["coverage_rate"] == pytest.approx(0.75)
    assert state["value"] == pytest.approx(3_800 / 8_500)
    assert not state["uncertainty_available"]
    assert state["uncertainty_status"] == (
        "unavailable_no_replicate_weights"
    )
    assert state["replicate_weight_count"] == 0
    assert math.isnan(state["margin_of_error"])
    assert math.isnan(state["low_estimate"])
    assert math.isnan(state["high_estimate"])
    assert "pums_replicate_uncertainty_unavailable" in state["quality_flag"]
    assert "no bounds were invented" in state["notes"]
    assert la["sample_n"] == 2
    assert la["value"] == pytest.approx(2_800 / 6_500)
    assert la["parameter_geography_level"] == "county"

    # Shared PUMA records are pooled once over their connected region and the
    # same record/WGTP result is attached to each member county.
    assert set(orange_riverside["geography_tier"]) == {"regional"}
    assert set(orange_riverside["parameter_geography"]) == {
        "puma_region:06059+06065"
    }
    assert set(orange_riverside["pool_member_counties"]) == {
        "06059|06065"
    }
    assert orange_riverside["value"].tolist() == pytest.approx([0.5, 0.5])

    # An unknown escrow flag is not guessed.  The failed preferred county pool
    # stays in diagnostics and the selected value is an explicit CA fallback.
    assert san_diego["preferred_pool_input_n"] == 1
    assert san_diego["preferred_pool_sample_n"] == 0
    assert math.isnan(san_diego["raw_pi_share"])
    assert san_diego["value"] == pytest.approx(state["value"])
    assert san_diego["geography_tier"] == "state_fallback"
    assert san_diego["fallback_applied"]
    assert "california_pums_fallback" in san_diego["quality_flag"]

    # SMP is an adjusted monthly payment per eligible unit.  It is emitted as
    # a separate zero-valued diagnostic and never enters the P&I share.
    assert (junior["value"] == 0).all()
    assert (junior["sample_n"] == junior["input_n"]).all()
    assert set(junior["unit"]) == {"USD/unit/month"}
    assert set(result["source_release_dates"]) == {
        "census_acs5_pums_housing_ca=2024-12-12"
    }

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


def test_uses_official_acs_sdr_formula_for_pi_ratio_and_junior_mean(
    tmp_path,
):
    source = _pums_frame().iloc[:2].copy()
    source["WGTP"] = [1, 1]
    source["MRGP"] = [100, 300]
    source["MRGT"] = [2, 2]
    source["MRGI"] = [2, 2]
    source["SMP"] = [0, 200]
    source["TAXAMT"] = [None, None]
    source["INSP"] = [None, None]
    source["SMOCP"] = [200, 400]
    source["ADJHSG"] = [1_000_000, 1_000_000]
    source["PUMA"] = ["00101", "00101"]
    for replicate, column in enumerate(
        PUMS_REPLICATE_WEIGHT_COLUMNS,
        start=1,
    ):
        source[column] = [2, 1] if replicate <= 40 else [1, 2]

    result = build_pums_pi_share_parameters(
        source,
        _pipe_relationship(tmp_path),
        benchmark_vintage="2023_ACS_5YR_PUMS",
        source_period_start="2019-01-01",
        source_period_end="2023-12-31",
        reference_date="2021-07-01",
        release_date="2024-12-12",
        relationship_vintage=2020,
        minimum_sample=1,
        require_complete_california_relationship=False,
    )
    state = result.loc[result["county_fips"].isna()].set_index(
        "variable_name"
    )
    pi = state.loc["pums_first_mortgage_pi_share_benchmark"]
    junior = state.loc[
        "pums_junior_lien_payment_per_mortgaged_unit_benchmark"
    ]

    pi_replicates = [500 / 800] * 40 + [700 / 1_000] * 40
    pi_point = 400 / 600
    pi_se = math.sqrt(
        (4 / 80)
        * sum((estimate - pi_point) ** 2 for estimate in pi_replicates)
    )
    assert pi["value"] == pytest.approx(pi_point)
    assert pi["standard_error"] == pytest.approx(pi_se)
    assert pi["margin_of_error"] == pytest.approx(1.645 * pi_se)
    assert pi["low_estimate"] == pytest.approx(
        pi_point - 1.645 * pi_se
    )
    assert pi["high_estimate"] == pytest.approx(
        pi_point + 1.645 * pi_se
    )

    junior_replicates = [200 / 3] * 40 + [400 / 3] * 40
    junior_point = 100.0
    junior_se = math.sqrt(
        (4 / 80)
        * sum(
            (estimate - junior_point) ** 2
            for estimate in junior_replicates
        )
    )
    assert junior["value"] == pytest.approx(junior_point)
    assert junior["standard_error"] == pytest.approx(junior_se)
    assert junior["margin_of_error"] == pytest.approx(1.645 * junior_se)
    assert junior["low_estimate"] == pytest.approx(
        junior_point - 1.645 * junior_se
    )
    assert junior["high_estimate"] == pytest.approx(
        junior_point + 1.645 * junior_se
    )

    for row in (pi, junior):
        assert row["uncertainty_available"]
        assert row["uncertainty_status"] == "available"
        assert (
            row["uncertainty_method"]
            == "acs_successive_difference_replication"
        )
        assert row["confidence_level"] == pytest.approx(0.90)
        assert row["replicate_weight_count"] == 80


def test_builder_rejects_incomplete_or_unmapped_relationship(tmp_path):
    relationship = _pipe_relationship(tmp_path)
    common = {
        "benchmark_vintage": "2023_ACS_5YR_PUMS",
        "source_period_start": "2019-01-01",
        "source_period_end": "2023-12-31",
        "reference_date": "2021-07-01",
        "release_date": "2024-12-12",
        "relationship_vintage": 2020,
    }
    with pytest.raises(PUMSFormatError, match="exactly all 58 counties"):
        build_pums_pi_share_parameters(
            _pums_frame(),
            relationship,
            **common,
        )

    missing_puma = relationship.loc[
        ~relationship["PUMA5CE"].eq("00103")
    ].copy()
    with pytest.raises(PUMSFormatError, match="PUMAs absent"):
        build_pums_pi_share_parameters(
            _pums_frame(),
            missing_puma,
            require_complete_california_relationship=False,
            **common,
        )


def test_minimum_sample_keeps_raw_pool_diagnostics_when_no_fallback_qualifies(
    tmp_path,
):
    result = build_pums_pi_share_parameters(
        _pums_frame(),
        _pipe_relationship(tmp_path),
        benchmark_vintage="2023_ACS_5YR_PUMS",
        source_period_start="2019-01-01",
        source_period_end="2023-12-31",
        reference_date="2021-07-01",
        release_date="2024-12-12",
        minimum_sample=4,
        require_complete_california_relationship=False,
        source_vintage="2023",
        source_filename="csv_hca.zip",
        retrieved_at="2024-12-13T00:00:00+00:00",
        checksum_sha256="pums-checksum",
        relationship_source_filename="tract_to_puma_2020.txt",
        relationship_retrieved_at="2024-12-13T00:00:00+00:00",
        relationship_checksum_sha256="relationship-checksum",
    )
    la = result.loc[
        result["variable_name"].eq(
            "pums_first_mortgage_pi_share_benchmark"
        )
        & result["county_fips"].eq("06037")
    ].iloc[0]
    assert la["sample_n"] == 2
    assert math.isnan(la["value"])
    assert la["raw_pi_share"] == pytest.approx(2_800 / 6_500)
    assert la["is_suppressed"]
    assert la["missing_reason"] == "below_minimum_sample"
    assert not la["fallback_applied"]
