from dataclasses import replace
import shutil

import pandas as pd
import pytest

from src.common.config import ROOT, load_config
from src.common.io import sha256_file
from src.reporting.export import export_outputs


def test_public_exports_reject_restricted_hud_usps_columns(tmp_path) -> None:
    config = replace(load_config(), root=tmp_path)
    final = tmp_path / "data" / "final"
    final.mkdir(parents=True)
    pd.DataFrame(
        {
            "observation_date": ["2026-03-31"],
            "geo_id": ["COUNTY_06037"],
            "usps_residential_vacancy_rate": [0.012],
        }
    ).to_parquet(final / "county_monthly_panel.parquet", index=False)

    with pytest.raises(ValueError, match="restricted HUD-USPS"):
        export_outputs(config)

    assert not (
        tmp_path / "output" / "exports" / "la_county_monthly_panel.csv"
    ).exists()


@pytest.fixture
def frozen_benchmark_project(tmp_path):
    config = replace(load_config(), root=tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copyfile(ROOT / "config/variable_metadata.csv", tmp_path / "config/variable_metadata.csv")
    final = tmp_path / "data/final"
    final.mkdir(parents=True)
    frame = pd.DataFrame({
        "county_fips": ["06037"] * 4,
        "variable_id": [
            "acs_aggregate_household_income_owner_benchmark",
            "acs_aggregate_owner_value_all_benchmark",
            "hmda_median_downpayment_rate_benchmark",
            "hmda_share_downpayment_below_3pct_benchmark",
        ],
        "unit": ["inflation_adjusted_dollars_per_year", "dollars", "rate", "share"],
        "value": [12345678.25, None, 0.15, 0.08],
        "source_filename": ["frozen-source.csv"] * 4,
        "checksum_sha256": ["a" * 64] * 4,
    })
    path = final / "housing_finance_benchmark.parquet"
    frame.to_parquet(path, index=False)
    return config, frame, path


def test_benchmark_export_preserves_frozen_units_values_and_file(frozen_benchmark_project):
    config, frame, path = frozen_benchmark_project
    before = sha256_file(path)
    assert export_outputs(config)["version_a_la_rows"] == 4
    exported = pd.read_csv(
        config.root / "output/exports/la_version_a_benchmark.csv",
        dtype={"county_fips": str},
    )
    pd.testing.assert_frame_equal(exported[frame.columns], frame)
    assert exported["unit_simplified"].tolist() == ["USD/year", "USD", "ratio", "ratio"]
    assert exported["temporal_type"].notna().all()
    assert exported["unit_simplification_note"].str.contains("Frozen Version A retains").all()
    assert sha256_file(path) == before


@pytest.mark.parametrize("variable_id, unit", [
    ("hmda_median_downpayment_rate_benchmark", "percent"),
    ("acs_aggregate_owner_value_all_benchmark", "thousands_of_dollars"),
    ("acs_total_population_benchmark", "dollars"),
    ("unregistered_benchmark", "dollars"),
])
def test_benchmark_aliases_do_not_hide_unreviewed_units(
    frozen_benchmark_project, variable_id, unit,
):
    config, frame, path = frozen_benchmark_project
    frame.loc[0, ["variable_id", "unit"]] = [variable_id, unit]
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="Variable metadata"):
        export_outputs(config)
    assert not (config.root / "output/exports/la_version_a_benchmark.csv").exists()
