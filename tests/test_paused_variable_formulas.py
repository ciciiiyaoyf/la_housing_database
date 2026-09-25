from __future__ import annotations

import pandas as pd
import pytest

from src.common.config import load_config
from src.features.derived import APARTMENT_COMPLETION_LAG_WEIGHTS
from src.harmonization.build import (
    _add_approved_annual_rates,
    _add_real_homeowners_premium,
    _wide_control_panel,
)


def _annual_fact(
    variable_id: str,
    values: dict[int, float],
    *,
    geo_id: str,
    quality_flag: str,
) -> pd.DataFrame:
    dates = pd.to_datetime([f"{year}-12-31" for year in values])
    return pd.DataFrame(
        {
            "observation_date": dates,
            "geo_id": geo_id,
            "variable_id": variable_id,
            "value": list(values.values()),
            "final_frequency": "annual",
            "source_vintage": "test-vintage",
            "retrieved_at": "2026-08-16T00:00:00+00:00",
            "quality_flag": quality_flag,
        }
    )


def test_real_premium_uses_exact_2023_california_cpi_base() -> None:
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-31", "2023-12-31"]),
            "geo_id": "STATE_06",
            "ca_homeowners_avg_written_premium": [1_600.0, 1_800.0],
            "ca_cpi_u_all_items_annual": [320.0, 336.0],
        }
    )

    result = _add_real_homeowners_premium(panel)

    assert result.loc[0, "ca_homeowners_avg_written_premium_real"] == pytest.approx(
        1_600.0 * 336.0 / 320.0
    )
    assert result.loc[1, "ca_homeowners_avg_written_premium_real"] == 1_800.0
    assert result[
        "ca_homeowners_avg_written_premium_real_quality_flag"
    ].str.contains("not_interpolated", regex=False).all()


def test_real_premium_preserves_missing_same_year_cpi() -> None:
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-31", "2023-12-31"]),
            "geo_id": "STATE_06",
            "ca_homeowners_avg_written_premium": [1_600.0, 1_800.0],
            "ca_cpi_u_all_items_annual": [pd.NA, 336.0],
        }
    )

    result = _add_real_homeowners_premium(panel)

    assert pd.isna(result.loc[0, "ca_homeowners_avg_written_premium_real"])
    assert result.loc[1, "ca_homeowners_avg_written_premium_real"] == 1_800.0


def test_approved_rates_use_matched_native_annual_pep_denominators() -> None:
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-31", "2023-12-31"]),
            "geo_id": "COUNTY_06037",
            "irs_soi_net_migration_exemptions": [-100_000.0, -80_000.0],
            "census_pep_county_population_annual": [10_000_000.0, 0.0],
            "permits_1_unit_annual_final": [8_000.0, 9_000.0],
            "census_pep_county_housing_units_annual": [4_000_000.0, pd.NA],
        }
    )
    fact = pd.concat(
        [
            _annual_fact(
                "irs_soi_net_migration_exemptions",
                {2022: -100_000.0, 2023: -80_000.0},
                geo_id="COUNTY_06037",
                quality_flag="filing_year_pair",
            ),
            _annual_fact(
                "census_pep_county_population_annual",
                {2022: 10_000_000.0, 2023: 0.0},
                geo_id="COUNTY_06037",
                quality_flag="pep_vintage_2025",
            ),
            _annual_fact(
                "permits_1_unit_annual_final",
                {2022: 8_000.0, 2023: 9_000.0},
                geo_id="COUNTY_06037",
                quality_flag="bps_2022_plus_full_universe",
            ),
            _annual_fact(
                "census_pep_county_housing_units_annual",
                {2022: 4_000_000.0, 2023: 4_100_000.0},
                geo_id="COUNTY_06037",
                quality_flag="pep_vintage_2025",
            ),
        ],
        ignore_index=True,
    )

    result = _add_approved_annual_rates(panel, fact)

    assert result.loc[0, "net_exemption_rate"] == -10.0
    assert pd.isna(result.loc[1, "net_exemption_rate"])
    assert result.loc[0, "permits_1_unit_per_1000_housing_units"] == 2.0
    assert pd.isna(result.loc[1, "permits_1_unit_per_1000_housing_units"])
    assert "pep_vintage_2025" in result.loc[
        0, "net_exemption_rate_quality_flag"
    ]


def test_wide_annual_panel_marks_native_frequency_without_carry() -> None:
    fact = _annual_fact(
        "ca_cpi_u_all_items_annual",
        {2023: 331.804},
        geo_id="STATE_06",
        quality_flag="published_annual_average",
    )

    panel = _wide_control_panel(
        fact,
        geo_prefixes=("STATE_",),
        frequency="annual",
        id_columns={"state_fips": "06", "state_name": "California"},
    )

    assert panel["frequency_treatment"].eq("native_annual_retained").all()
    assert panel["is_mixed_frequency_control"].eq(False).all()
    assert panel["is_carried_annual_control"].eq(False).all()


def test_configured_construction_scenarios_match_executable_weights() -> None:
    configured = load_config().pipeline["construction_proxies"]

    assert set(configured["scenarios"]) == {"central", "slow"}
    assert configured["require_complete_lag_history"] is True
    for scenario, scenario_config in configured["scenarios"].items():
        expanded: dict[int, float] = {}
        for bucket in scenario_config["lag_buckets"]:
            start = int(bucket["start_month"])
            end = int(bucket["end_month"])
            monthly_weight = float(bucket["completion_share"]) / (
                end - start + 1
            )
            expanded.update(
                {lag: monthly_weight for lag in range(start, end + 1)}
            )
        assert expanded == pytest.approx(
            APARTMENT_COMPLETION_LAG_WEIGHTS[scenario]
        )
        assert sum(expanded.values()) == pytest.approx(1.0)
