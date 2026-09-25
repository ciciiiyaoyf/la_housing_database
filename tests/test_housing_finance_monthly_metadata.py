from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.common.config import ROOT
from src.common.io import sha256_file
from src.housing_finance_monthly.metadata import (
    GLOBAL_CATALOG_COLUMNS,
    GLOBAL_DICTIONARY_COLUMNS,
    PRODUCT_SOURCE_IDS,
    TRANSFORMATION_LOG_COLUMNS,
    register_monthly_finance_build,
    register_monthly_finance_contracts,
)


CONFIG_FILES = (
    "config/version_b_variables.yaml",
    "config/version_c_variables.yaml",
    "metadata/version_b_transformation_catalog.csv",
    "metadata/version_c_transformation_catalog.csv",
)


def _copy_contract_root(tmp_path: Path) -> Path:
    for relative in CONFIG_FILES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    metadata = tmp_path / "metadata"
    dictionary = pd.read_csv(ROOT / "metadata/data_dictionary.csv", dtype=str)
    dictionary = dictionary.loc[
        ~dictionary["variable_id"].str.endswith(
            ("_monthly_hist", "_monthly_rt"), na=False
        )
    ]
    dictionary.to_csv(metadata / "data_dictionary.csv", index=False)
    catalog = pd.read_csv(ROOT / "metadata/data_catalog.csv", dtype=str)
    catalog = catalog.loc[~catalog["source_id"].isin(PRODUCT_SOURCE_IDS)]
    catalog.to_csv(metadata / "data_catalog.csv", index=False)
    pd.DataFrame(
        [
            {
                "transformation_id": "unrelated_transform_v1",
                "source_id": "unrelated_source",
                "input_file": "data/raw/unrelated.csv",
                "output_file": "data/clean/unrelated.parquet",
                "input_rows_or_shape": "10",
                "output_rows": "10",
                "description": "Unrelated row that must be preserved.",
                "code_module": "src.unrelated",
                "source_vintage": "2025",
                "executed_at": "2026-01-01T00:00:00+00:00",
                "source_filename": "unrelated.csv",
                "checksum_sha256": "a" * 64,
                "retrieved_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        columns=TRANSFORMATION_LOG_COLUMNS,
    ).to_csv(metadata / "transformation_log.csv", index=False)
    return tmp_path


def test_contract_registration_merges_58_plus_58_and_six_products_idempotently(
    tmp_path: Path,
) -> None:
    root = _copy_contract_root(tmp_path)
    dictionary_path = root / "metadata/data_dictionary.csv"
    catalog_path = root / "metadata/data_catalog.csv"
    unrelated_dictionary = pd.read_csv(dictionary_path, dtype=str).iloc[0].to_dict()
    unrelated_catalog = pd.read_csv(catalog_path, dtype=str).iloc[0].to_dict()

    # Stale owned placeholders must be replaced, not preserved.
    dictionary = pd.read_csv(dictionary_path, dtype=str)
    stale_dictionary = {column: "" for column in dictionary.columns}
    stale_dictionary.update(
        {
            "variable_id": "stale_monthly_hist",
            "display_name": "stale",
            "active_status": "stale",
        }
    )
    pd.concat(
        [dictionary, pd.DataFrame([stale_dictionary])], ignore_index=True
    ).to_csv(dictionary_path, index=False)
    catalog = pd.read_csv(catalog_path, dtype=str)
    stale_catalog = {column: "" for column in catalog.columns}
    stale_catalog.update(
        {
            "source_id": "housing_finance_version_b_observations",
            "Source": "stale",
            "notes": "stale",
        }
    )
    pd.concat([catalog, pd.DataFrame([stale_catalog])], ignore_index=True).to_csv(
        catalog_path, index=False
    )

    result = register_monthly_finance_contracts(root=root)

    assert result.dictionary_rows == 116
    assert result.catalog_rows == 6
    assert result.transformation_rows == 0
    dictionary = pd.read_csv(dictionary_path, dtype=str)
    assert list(dictionary.columns) == list(GLOBAL_DICTIONARY_COLUMNS)
    assert dictionary["variable_id"].str.endswith("_monthly_hist").sum() == 58
    assert dictionary["variable_id"].str.endswith("_monthly_rt").sum() == 58
    assert dictionary["variable_id"].is_unique
    assert "stale_monthly_hist" not in set(dictionary["variable_id"])
    actual_unrelated_dictionary = dictionary.loc[
        dictionary["variable_id"].eq(unrelated_dictionary["variable_id"])
    ].iloc[0]
    for column, value in unrelated_dictionary.items():
        if pd.isna(value):
            assert pd.isna(actual_unrelated_dictionary[column])
        else:
            assert actual_unrelated_dictionary[column] == value

    catalog = pd.read_csv(catalog_path, dtype=str)
    assert list(catalog.columns) == list(GLOBAL_CATALOG_COLUMNS)
    products = catalog.loc[catalog["source_id"].isin(PRODUCT_SOURCE_IDS)]
    assert len(products) == 6
    assert set(products["source_id"]) == PRODUCT_SOURCE_IDS
    assert not products["Source"].eq("stale").any()
    actual_unrelated_catalog = catalog.loc[
        catalog["source_id"].eq(unrelated_catalog["source_id"])
    ].iloc[0]
    for column, value in unrelated_catalog.items():
        if pd.isna(value):
            assert pd.isna(actual_unrelated_catalog[column])
        else:
            assert actual_unrelated_catalog[column] == value

    first_dictionary = dictionary_path.read_bytes()
    first_catalog = catalog_path.read_bytes()
    register_monthly_finance_contracts(root=root)
    assert dictionary_path.read_bytes() == first_dictionary
    assert catalog_path.read_bytes() == first_catalog


@pytest.mark.parametrize("with_extensions", [False, True])
def test_dictionary_registration_canonicalizes_shuffled_columns_without_data_loss(
    tmp_path: Path, with_extensions: bool
) -> None:
    root = _copy_contract_root(tmp_path)
    path = root / "metadata/data_dictionary.csv"
    original = pd.read_csv(path, dtype=str)
    canonical = list(GLOBAL_DICTIONARY_COLUMNS)
    # Keep any future preexisting extensions too, rather than relying on the
    # repository's current inventory to contain only the canonical schema.
    existing_extensions = [column for column in original if column not in canonical]
    custom_extensions = []
    if with_extensions:
        custom_extensions = ["researcher_annotation", "external_dictionary_key"]
        original[custom_extensions[0]] = "Unrelated note, preserved exactly."
        original[custom_extensions[1]] = [f"000{index}" for index in range(len(original))]
    extensions = [*existing_extensions, *custom_extensions]
    # Interleave extensions with reversed canonical fields to reproduce a YAML
    # insertion-order change and verify that registration normalizes it.
    reversed_columns = list(reversed(canonical))
    input_columns = [*extensions[:1], *reversed_columns, *extensions[1:]]
    original.loc[:, input_columns].to_csv(path, index=False)
    before = pd.read_csv(path, dtype=str)

    register_monthly_finance_contracts(root=root)

    after = pd.read_csv(path, dtype=str)
    assert list(after.columns) == [*canonical, *extensions]
    unrelated = after.loc[after["variable_id"].isin(before["variable_id"])]
    pd.testing.assert_frame_equal(
        unrelated.set_index("variable_id").loc[before["variable_id"], before.columns.drop("variable_id")],
        before.set_index("variable_id"),
        check_dtype=True,
    )
    assert after["variable_id"].str.endswith("_monthly_hist").sum() == 58
    assert after["variable_id"].str.endswith("_monthly_rt").sum() == 58
    first_bytes = path.read_bytes()
    register_monthly_finance_contracts(root=root)
    assert path.read_bytes() == first_bytes


def _write_completed_build(
    root: Path, version: str, *, cutoff: str = "2026-08-17"
) -> tuple[Path, Path, Path, dict[str, dict[str, str]], pd.DataFrame, pd.DataFrame]:
    benchmark_path = root / "data/final/housing_finance_benchmark.parquet"
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"benchmark": [1]}).to_parquet(benchmark_path, index=False)
    redfin_path = root / "data/raw/redfin/selected.tsv.gz"
    redfin_path.parent.mkdir(parents=True, exist_ok=True)
    redfin_path.write_bytes(b"selected immutable Redfin input")
    asset_records = {
        "redfin": {
            "source_id": "redfin_county_monthly",
            "relative_path": "data/raw/redfin/selected.tsv.gz",
            "source_filename": "selected.tsv.gz",
            "data_vintage": "2026-07",
            "retrieved_at": "2026-07-02T06:08:28+00:00",
            "checksum_sha256": sha256_file(redfin_path),
        }
    }
    if version == "B":
        observation_path = root / "data/final/housing_finance_monthly_hist.parquet"
        lineage_path = root / "data/final/housing_finance_monthly_hist_lineage.parquet"
        completion_path = observation_path.with_suffix(".complete.json")
        variable_ids = [
            "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_hist",
            "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_hist",
            "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_hist",
        ]
        transformations = [
            "version_b_reference_year_payment_repeat_v1",
            "version_b_reference_year_payment_repeat_v1",
            "version_b_redfin_purchase_value_proxy_distribution_v1",
        ]
        information_cutoffs = [pd.NaT, pd.NaT, pd.NaT]
        marker_cutoff = None
    else:
        partition = f"information_cutoff_date={cutoff}"
        observation_path = (
            root / "data/final/housing_finance_monthly_rt" / partition / "part.parquet"
        )
        lineage_path = (
            root
            / "data/final/housing_finance_monthly_rt_lineage"
            / partition
            / "part.parquet"
        )
        completion_path = observation_path.parent / "_COMPLETE.json"
        variable_ids = [
            "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_rt",
            "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_rt",
            "hmda_aggregate_downpayment_flow_redfin_sales_value_proxy_monthly_rt",
        ]
        transformations = [
            "version_c_latest_payment_carry_v1",
            "version_c_latest_payment_carry_v1",
            "version_c_redfin_purchase_value_proxy_nowcast_v1",
        ]
        information_cutoffs = [cutoff, cutoff, cutoff]
        marker_cutoff = cutoff
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    observations = pd.DataFrame(
        {
            "county_fips": ["06001", "06003", "06001"],
            "month": pd.to_datetime(["2025-01-31", "2025-01-31", "2025-02-28"]),
            "variable_id": variable_ids,
            "transformation_id": transformations,
            "lineage_id": ["lineage-1", "lineage-2", "lineage-3"],
            "information_cutoff_date": pd.to_datetime(information_cutoffs),
        }
    )
    benchmark_sha = sha256_file(benchmark_path)
    lineage_rows = [
        {
            "lineage_id": lineage_id,
            "month": month,
            "source_id": "version_a_housing_finance_benchmark",
            "source_vintage": "2024_ACS5",
            "source_filename": "housing_finance_benchmark.parquet",
            "checksum_sha256": benchmark_sha,
            "retrieved_at": "2026-07-26T07:53:17+00:00",
        }
        for lineage_id, month in zip(observations["lineage_id"], observations["month"])
    ]
    lineage_rows.append(
        {
            "lineage_id": "lineage-3",
            "month": pd.Timestamp("2025-02-28"),
            "source_id": "redfin_county_monthly",
            "source_vintage": "2026-07",
            "source_filename": "selected.tsv.gz",
            "checksum_sha256": sha256_file(redfin_path),
            "retrieved_at": "2026-07-02T06:08:28+00:00",
        }
    )
    lineage = pd.DataFrame(lineage_rows)
    observations.to_parquet(observation_path, index=False)
    lineage.to_parquet(lineage_path, index=False)
    completed_at = "2026-08-17T20:30:00+00:00"
    completion_path.write_text(
        json.dumps(
            {
                "schema_version": "housing_finance_output_pair_v1",
                "database_version": version,
                "information_cutoff_date": marker_cutoff,
                "observation_path": observation_path.relative_to(root).as_posix(),
                "observation_sha256": sha256_file(observation_path),
                "observation_rows": len(observations),
                "lineage_path": lineage_path.relative_to(root).as_posix(),
                "lineage_sha256": sha256_file(lineage_path),
                "lineage_rows": len(lineage),
                "completed_at": completed_at,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return (
        observation_path,
        lineage_path,
        completion_path,
        asset_records,
        observations,
        lineage,
    )


@pytest.mark.parametrize("version", ["B", "C"])
def test_successful_build_registration_records_actual_lineage_and_is_idempotent(
    tmp_path: Path, version: str
) -> None:
    root = _copy_contract_root(tmp_path)
    (
        observation_path,
        lineage_path,
        completion_path,
        asset_records,
        observations,
        lineage,
    ) = _write_completed_build(root, version)
    benchmark_bytes = (
        root / "data/final/housing_finance_benchmark.parquet"
    ).read_bytes()
    raw_bytes = (root / "data/raw/redfin/selected.tsv.gz").read_bytes()

    result = register_monthly_finance_build(
        version,
        root=root,
        observation_path=observation_path,
        lineage_path=lineage_path,
        completion_path=completion_path,
        asset_records=asset_records,
        observations=observations,
        lineage=lineage,
    )

    assert result.dictionary_rows == 116
    assert result.catalog_rows == 6
    assert result.transformation_rows == 2
    assert result.version == version
    assert result.information_cutoff_date == (
        "2026-08-17" if version == "C" else None
    )
    log_path = root / "metadata/transformation_log.csv"
    log = pd.read_csv(log_path, dtype=str)
    prefix = f"version_{version.lower()}_"
    rows = log.loc[log["transformation_id"].str.startswith(prefix)]
    assert len(rows) == 2
    assert set(rows["output_rows"].astype(int)) == {1, 2}
    assert rows["executed_at"].eq("2026-08-17T20:30:00+00:00").all()
    assert rows["output_file"].str.contains(
        observation_path.relative_to(root).as_posix(), regex=False
    ).all()
    assert rows["output_file"].str.contains(
        lineage_path.relative_to(root).as_posix(), regex=False
    ).all()
    assert rows["output_file"].str.contains(
        completion_path.relative_to(root).as_posix(), regex=False
    ).all()
    assert rows["checksum_sha256"].str.contains(sha256_file(observation_path)).all()
    assert rows["source_vintage"].str.contains("2024_ACS5").all()
    proxy = rows.loc[rows["source_id"].str.contains("redfin_county_monthly")].iloc[0]
    assert "data/raw/redfin/selected.tsv.gz" in proxy["input_file"]
    assert sha256_file(root / "data/raw/redfin/selected.tsv.gz") in proxy[
        "checksum_sha256"
    ]
    assert (
        log.loc[log["transformation_id"].eq("unrelated_transform_v1")].shape[0]
        == 1
    )

    catalog = pd.read_csv(root / "metadata/data_catalog.csv", dtype=str)
    owned = catalog.loc[catalog["source_id"].isin(PRODUCT_SOURCE_IDS)]
    current = owned.loc[
        owned["source_id"].str.contains(f"version_{version.lower()}_")
    ]
    assert set(current["row_count"].astype(int)) == {1, 3, 4}
    assert current["provider_vintage"].notna().all()
    assert current["checksum_sha256"].str.len().eq(64).all()
    assert (
        root / "data/final/housing_finance_benchmark.parquet"
    ).read_bytes() == benchmark_bytes
    assert (root / "data/raw/redfin/selected.tsv.gz").read_bytes() == raw_bytes

    dictionary_bytes = (root / "metadata/data_dictionary.csv").read_bytes()
    catalog_bytes = (root / "metadata/data_catalog.csv").read_bytes()
    log_bytes = log_path.read_bytes()
    register_monthly_finance_build(
        version,
        root=root,
        observation_path=observation_path,
        lineage_path=lineage_path,
        completion_path=completion_path,
        asset_records=asset_records,
        observations=observations,
        lineage=lineage,
    )
    assert (root / "metadata/data_dictionary.csv").read_bytes() == dictionary_bytes
    assert (root / "metadata/data_catalog.csv").read_bytes() == catalog_bytes
    assert log_path.read_bytes() == log_bytes


def test_version_c_transformation_log_preserves_prior_cutoff(
    tmp_path: Path,
) -> None:
    root = _copy_contract_root(tmp_path)
    first = _write_completed_build(root, "C", cutoff="2026-08-16")
    register_monthly_finance_build(
        "C",
        root=root,
        observation_path=first[0],
        lineage_path=first[1],
        completion_path=first[2],
        asset_records=first[3],
        observations=first[4],
        lineage=first[5],
    )
    second = _write_completed_build(root, "C", cutoff="2026-08-17")
    register_monthly_finance_build(
        "C",
        root=root,
        observation_path=second[0],
        lineage_path=second[1],
        completion_path=second[2],
        asset_records=second[3],
        observations=second[4],
        lineage=second[5],
    )

    log = pd.read_csv(root / "metadata/transformation_log.csv", dtype=str)
    version_c = log.loc[log["transformation_id"].str.startswith("version_c_")]
    assert len(version_c) == 4
    assert version_c["source_vintage"].str.startswith(
        "information_cutoff_date=2026-08-16"
    ).sum() == 2
    assert version_c["source_vintage"].str.startswith(
        "information_cutoff_date=2026-08-17"
    ).sum() == 2
    catalog = pd.read_csv(root / "metadata/data_catalog.csv", dtype=str)
    current = catalog.loc[
        catalog["source_id"].eq("housing_finance_version_c_observations")
    ].iloc[0]
    assert current["provider_vintage"] == "information_cutoff_date=2026-08-17"


def test_invalid_completion_marker_causes_no_global_metadata_write(
    tmp_path: Path,
) -> None:
    root = _copy_contract_root(tmp_path)
    build = _write_completed_build(root, "B")
    payload = json.loads(build[2].read_text(encoding="utf-8"))
    payload["observation_sha256"] = "0" * 64
    build[2].write_text(json.dumps(payload), encoding="utf-8")
    before = {
        name: (root / "metadata" / name).read_bytes()
        for name in (
            "data_dictionary.csv",
            "data_catalog.csv",
            "transformation_log.csv",
        )
    }

    with pytest.raises(RuntimeError, match="does not commit"):
        register_monthly_finance_build(
            "B",
            root=root,
            observation_path=build[0],
            lineage_path=build[1],
            completion_path=build[2],
            asset_records=build[3],
            observations=build[4],
            lineage=build[5],
        )

    for name, content in before.items():
        assert (root / "metadata" / name).read_bytes() == content


def test_atomic_csv_failure_leaves_original_dictionary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_contract_root(tmp_path)
    dictionary_path = root / "metadata/data_dictionary.csv"
    original = dictionary_path.read_bytes()

    def fail_to_csv(*_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_to_csv)
    with pytest.raises(OSError, match="simulated write failure"):
        register_monthly_finance_contracts(root=root)
    assert dictionary_path.read_bytes() == original
    assert not list(dictionary_path.parent.glob(".data_dictionary.csv.stage.*"))
