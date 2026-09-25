from pathlib import Path

import pandas as pd

from src.cleaning.irs_migration import (
    IRS_ENHANCED_MATCHING_BREAK_FLAG,
    clean_irs_soi_county_migration,
)


def _write_migration_file(
    path: Path,
    direction: str,
    *,
    returns: int,
    exemptions: int,
    agi: int,
) -> None:
    if direction == "inflow":
        coordinates = {
            "dest_statefips": "06",
            "dest_countyfips": "037",
            "origin_statefips": "96",
            "origin_countyfips": "000",
        }
    else:
        coordinates = {
            "dest_statefips": "96",
            "dest_countyfips": "000",
            "origin_statefips": "06",
            "origin_countyfips": "037",
        }
    pd.DataFrame(
        [
            {
                **coordinates,
                "n1": returns,
                "n2": exemptions,
                "agi": agi,
            }
        ]
    ).to_csv(path, index=False)


def _record(path: Path, period: str, direction: str) -> dict[str, str]:
    return {
        "source_id": "irs_soi_county_migration",
        "archive_period": period,
        "migration_direction": direction,
        "data_vintage": period,
        "retrieved_at": "2026-08-07T00:00:00+00:00",
        "source_filename": path.name,
        "checksum_sha256": f"checksum-{period}-{direction}",
        "provider_last_updated": f"{period[-4:]}-12-31",
    }


def test_irs_2022_2023_method_break_is_flagged_without_changing_values(tmp_path):
    archive_files: list[tuple[Path, dict[str, str]]] = []
    for period, inflow, outflow in (
        ("2021-2022", (120, 240, 360), (100, 200, 300)),
        ("2022-2023", (150, 300, 450), (110, 220, 330)),
    ):
        for direction, values in (("inflow", inflow), ("outflow", outflow)):
            path = tmp_path / f"{direction}_{period}.csv"
            _write_migration_file(
                path,
                direction,
                returns=values[0],
                exemptions=values[1],
                agi=values[2],
            )
            archive_files.append((path, _record(path, period, direction)))

    result = clean_irs_soi_county_migration(
        archive_files,
        "irs_soi_county_migration",
        tmp_path / "clean.parquet",
        tmp_path / "coverage.csv",
    )

    prior = result.loc[result["observation_date"].eq("2022-12-31")]
    changed = result.loc[result["observation_date"].eq("2023-12-31")]
    assert len(prior) == len(changed) == 9
    assert ~prior["quality_flag"].str.contains(IRS_ENHANCED_MATCHING_BREAK_FLAG).any()
    assert changed["quality_flag"].str.contains(
        IRS_ENHANCED_MATCHING_BREAK_FLAG
    ).all()
    assert changed["quality_flag"].str.startswith(
        "filing_year_pair_2022-2023;"
    ).all()
    net_exemptions = changed.loc[
        changed["variable_id"].eq("irs_soi_net_migration_exemptions"),
        "value",
    ].item()
    assert net_exemptions == 80
    assert changed["reported_value"].equals(changed["value"])
    assert set(changed["transformation_id"]) == {
        "irs_soi_county_migration_net_v2"
    }
    assert not changed["is_imputed"].any()
    assert not changed["is_interpolated"].any()
