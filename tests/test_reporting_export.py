from dataclasses import replace

import pandas as pd
import pytest

from src.common.config import load_config
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
