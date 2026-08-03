from scripts.rebuild_hud_usps_official import add_derived, quarters

import pandas as pd
import pytest


def test_quarter_range_is_inclusive() -> None:
    values = quarters(2025, 2026, 3)
    assert values == [(2025, 3), (2025, 6), (2025, 9), (2025, 12), (2026, 3)]


def test_official_formulas_keep_no_stat_separate() -> None:
    frame = pd.DataFrame({
        "total_residential_addresses": [100.0],
        "short_term_vacant_residential_addresses": [3.0],
        "long_term_vacant_residential_addresses": [2.0],
        "no_stat_residential_addresses": [10.0],
    })
    add_derived(frame)
    assert frame.loc[0, "vacant_residential_addresses"] == 5
    assert frame.loc[0, "usps_residential_vacancy_rate"] == pytest.approx(0.05)
    assert frame.loc[0, "usps_residential_no_stat_rate"] == pytest.approx(0.10)
