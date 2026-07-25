from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from src.benchmark.nmdb import (
    NMDBFormatError,
    normalize_nmdb_california_upb,
    normalize_nmdb_joint_distribution,
)


CHECKSUM = "a" * 64
SOURCE_PAGE = (
    "https://www.fhfa.gov/data/dashboard/"
    "nmdb-outstanding-residential-mortgage-statistics"
)


def _common_metadata(**updates: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "benchmark_vintage": "2024_ACS5",
        "source_period_start": "2024-10-01",
        "source_period_end": "2024-12-31",
        "reference_date": "2024-12-31",
        "release_date": "2025-03-28",
        "source_frequency": "quarterly",
        "parameter_geography": "US",
        "source_vintage": "NMDB_2024Q4_dashboard_export",
        "retrieved_at": "2025-04-02T17:00:00Z",
        "source_filename": "nmdb_export.csv",
        "checksum_sha256": CHECKSUM,
        "source_page_url": SOURCE_PAGE,
        "universe_definition": "Outstanding closed-end residential mortgages",
        "monthly_method": "not_applicable_source_period",
        "is_modeled": False,
        "is_nowcast": False,
        "is_imputed": False,
        "is_interpolated": False,
    }
    metadata.update(updates)
    return metadata


def _distribution_metadata(**updates: object) -> dict[str, object]:
    metadata = _common_metadata(
        distribution_kind="joint_rate_remaining_term",
        mortgage_population="outstanding_mortgages",
        rate_unit="percent",
        remaining_term_unit="years",
        weight_unit="percent",
    )
    metadata.update(updates)
    return metadata


def _distribution_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Coupon": [3.0, 3.0, 6.0, 6.0],
            "Years Remaining": [10, 20, 10, 20],
            "Joint Share": [10.0, 20.0, 30.0, 40.0],
        }
    )


def _distribution_columns() -> dict[str, str]:
    return {
        "annual_rate": "Coupon",
        "remaining_months": "Years Remaining",
        "weight": "Joint Share",
    }


def test_normalizes_genuine_joint_distribution_for_builder() -> None:
    result = normalize_nmdb_joint_distribution(
        _distribution_frame(),
        column_map=_distribution_columns(),
        source_metadata=_distribution_metadata(),
    )

    assert list(result["annual_rate"]) == [0.03, 0.03, 0.06, 0.06]
    assert list(result["remaining_months"]) == [120.0, 240.0, 120.0, 240.0]
    assert result["weight"].sum() == pytest.approx(1.0)
    assert result["rates_are_percent"].eq(False).all()
    assert result["benchmark_vintage"].eq("2024_ACS5").all()
    assert result["reference_date"].eq(pd.Timestamp("2024-12-31")).all()
    assert result["parameter_geography"].eq("US").all()
    assert result["source_filename"].eq("nmdb_export.csv").all()
    assert result["source_vintage"].eq(
        "NMDB_2024Q4_dashboard_export"
    ).all()
    assert result["checksum_sha256"].eq(CHECKSUM).all()
    assert result["is_interpolated"].eq(False).all()
    assert result["month"].isna().all()


def test_csv_uses_actual_filename_and_checksum(tmp_path) -> None:
    path = tmp_path / "official_nmdb.csv"
    _distribution_frame().to_csv(path, index=False)
    metadata = _distribution_metadata()
    metadata.pop("source_filename")
    metadata.pop("checksum_sha256")

    result = normalize_nmdb_joint_distribution(
        path,
        column_map=_distribution_columns(),
        source_metadata=metadata,
    )

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["source_filename"].eq(path.name).all()
    assert result["checksum_sha256"].eq(expected).all()


@pytest.mark.parametrize(
    ("metadata_update", "message"),
    [
        ({"distribution_kind": "separate_marginals"}, "separate marginals"),
        ({"mortgage_population": "new_originations"}, "outstanding mortgages"),
        ({"monthly_method": "linear monthly interpolation"}, "interpolation"),
        ({"is_interpolated": True}, "is_interpolated"),
    ],
)
def test_rejects_nonjoint_or_non_version_a_inputs(
    metadata_update: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(NMDBFormatError, match=message):
        normalize_nmdb_joint_distribution(
            _distribution_frame(),
            column_map=_distribution_columns(),
            source_metadata=_distribution_metadata(**metadata_update),
        )


@pytest.mark.parametrize(
    ("column", "replacement", "message"),
    [
        ("Joint Share", [10.0, 20.0, None, 70.0], "cannot contain missing"),
        ("Joint Share", [10.0, -20.0, 30.0, 80.0], "cannot be negative"),
        ("Joint Share", [10.0, 20.0, 30.0, 30.0], "sum to one"),
        ("Coupon", [3.0, -3.0, 6.0, 6.0], "cannot be negative"),
        ("Years Remaining", [10, 20, 0, 20], "must be positive"),
    ],
)
def test_rejects_invalid_distribution_values(
    column: str,
    replacement: list[object],
    message: str,
) -> None:
    frame = _distribution_frame()
    frame[column] = replacement
    with pytest.raises(NMDBFormatError, match=message):
        normalize_nmdb_joint_distribution(
            frame,
            column_map=_distribution_columns(),
            source_metadata=_distribution_metadata(),
        )


def test_weights_are_checked_per_period_and_geography() -> None:
    frame = _distribution_frame()
    frame["Period"] = ["2024-12-31"] * 2 + ["2025-03-31"] * 2
    frame["Start"] = ["2024-10-01"] * 2 + ["2025-01-01"] * 2
    frame["End"] = ["2024-12-31"] * 2 + ["2025-03-31"] * 2
    frame["Release"] = ["2025-03-28"] * 2 + ["2025-06-27"] * 2
    frame["Vintage"] = ["2024_ACS5"] * 2 + ["2025_ACS5"] * 2
    frame["Joint Share"] = [50.0, 50.0, 40.0, 60.0]
    mapping = {
        **_distribution_columns(),
        "reference_date": "Period",
        "source_period_start": "Start",
        "source_period_end": "End",
        "release_date": "Release",
        "benchmark_vintage": "Vintage",
    }
    metadata = _distribution_metadata()
    for field in (
        "reference_date",
        "source_period_start",
        "source_period_end",
        "release_date",
        "benchmark_vintage",
    ):
        metadata.pop(field)

    result = normalize_nmdb_joint_distribution(
        frame,
        column_map=mapping,
        source_metadata=metadata,
    )

    totals = result.groupby("benchmark_vintage")["weight"].sum().to_dict()
    assert totals == pytest.approx({"2024_ACS5": 1.0, "2025_ACS5": 1.0})


@pytest.mark.parametrize(
    "missing_field",
    ["reference_date", "source_period_start", "source_period_end"],
)
def test_rejects_missing_period_metadata(missing_field: str) -> None:
    metadata = _distribution_metadata()
    metadata.pop(missing_field)
    with pytest.raises(NMDBFormatError, match=missing_field):
        normalize_nmdb_joint_distribution(
            _distribution_frame(),
            column_map=_distribution_columns(),
            source_metadata=metadata,
        )


def _upb_metadata(**updates: object) -> dict[str, object]:
    metadata = _common_metadata(
        parameter_geography="California",
        control_type="unpaid_principal_balance",
        compatible_with_county_debt=True,
        value_unit="billion_usd",
        universe_definition=(
            "Outstanding California first-lien closed-end residential "
            "mortgages compatible with the county debt universe"
        ),
    )
    metadata.update(updates)
    return metadata


def test_normalizes_compatible_california_upb_for_builder() -> None:
    result = normalize_nmdb_california_upb(
        pd.DataFrame({"Published UPB": [812.5]}),
        column_map={"value": "Published UPB"},
        source_metadata=_upb_metadata(),
    )

    row = result.iloc[0]
    assert row["value"] == 812_500_000_000.0
    assert row["parameter_geography"] == "California"
    assert row["benchmark_vintage"] == "2024_ACS5"
    assert row["reference_date"] == pd.Timestamp("2024-12-31")
    assert row["source_filename"] == "nmdb_export.csv"
    assert row["source_vintage"] == "NMDB_2024Q4_dashboard_export"
    assert row["checksum_sha256"] == CHECKSUM
    assert row["compatible_with_county_debt"]
    assert not row["is_interpolated"]


@pytest.mark.parametrize(
    ("metadata_update", "message"),
    [
        ({"parameter_geography": "US"}, "California parameter_geography"),
        ({"compatible_with_county_debt": False}, "explicitly true"),
        ({"control_type": "mortgage_count"}, "unpaid principal balance"),
        ({"monthly_method": "annual value repeated monthly"}, "repetition"),
    ],
)
def test_rejects_incompatible_california_controls(
    metadata_update: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(NMDBFormatError, match=message):
        normalize_nmdb_california_upb(
            pd.DataFrame({"Published UPB": [812.5]}),
            column_map={"value": "Published UPB"},
            source_metadata=_upb_metadata(**metadata_update),
        )


def test_rejects_negative_and_ambiguous_duplicate_upb_controls() -> None:
    with pytest.raises(NMDBFormatError, match="cannot be negative"):
        normalize_nmdb_california_upb(
            pd.DataFrame({"Published UPB": [-1]}),
            column_map={"value": "Published UPB"},
            source_metadata=_upb_metadata(),
        )

    with pytest.raises(NMDBFormatError, match="ambiguous duplicate"):
        normalize_nmdb_california_upb(
            pd.DataFrame({"Published UPB": [800.0, 812.5]}),
            column_map={"value": "Published UPB"},
            source_metadata=_upb_metadata(),
        )
