from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.hud_pit import (
    EXPECTED_YEARS,
    LA_FOUR_COC_COMPOSITE_ID,
    LA_FOUR_COC_IDS,
    clean_hud_pit,
)


def _write_workbook(
    path: Path,
    *,
    omit_column: str | None = None,
    old_sheets_without_count_types: bool = False,
    count_type_overrides: dict[tuple[int, str], str] | None = None,
) -> None:
    count_type_overrides = count_type_overrides or {}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for year in EXPECTED_YEARS:
            rows = []
            for index, coc_id in enumerate((*LA_FOUR_COC_IDS, "CA-500"), start=1):
                sheltered = year + index
                if year == 2021 and coc_id == "CA-600":
                    count_type = "Sheltered and full unsheltered count"
                elif year == 2021 and coc_id == "CA-606":
                    count_type = "Sheltered and partial unsheltered count"
                elif year == 2021:
                    count_type = "Sheltered-Only Count"
                elif year == 2023 and coc_id == "CA-607":
                    count_type = "Sheltered-Only Count*"
                elif year == 2014 and coc_id == "CA-606":
                    count_type = "Sheltered-Only Count"
                else:
                    count_type = "Sheltered and Unsheltered Count"
                count_type = count_type_overrides.get(
                    (year, coc_id), count_type
                )
                sheltered_only = count_type.casefold().startswith("sheltered-only")
                unsheltered = 0 if sheltered_only else 100 + index
                rows.append(
                    {
                        "CoC Number": coc_id,
                        "Count Types": count_type,
                        "Overall Homeless": sheltered + unsheltered,
                        "Sheltered Total Homeless": sheltered,
                        "Unsheltered Homeless": unsheltered,
                    }
                )
            frame = pd.DataFrame(rows)
            if old_sheets_without_count_types and year in {2007, 2008}:
                frame = frame.drop(columns="Count Types")
            if omit_column is not None:
                frame = frame.drop(columns=omit_column)
            frame.to_excel(writer, sheet_name=f"{year} PIT", index=False, startrow=2)


def _record(path: Path) -> dict[str, str]:
    return {
        "source_id": "hud_pit",
        "data_vintage": "2025",
        "retrieved_at": "2026-08-07T00:00:00+00:00",
        "source_filename": path.name,
        "checksum_sha256": "test-checksum",
        "provider_last_updated": "2025-12-31",
    }


def test_hud_pit_preserves_native_coc_rows_without_implicit_composite(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(raw, _record(raw), tmp_path / "clean.parquet")

    assert len(result) == len(EXPECTED_YEARS) * 5 * 3
    assert "COC_COMPOSITE_LA_FOUR_COC_COMPOSITE" not in set(result["geo_id"])
    assert set(result["geo_id"]) == {
        "COC_CA-600",
        "COC_CA-606",
        "COC_CA-607",
        "COC_CA-612",
        "COC_CA-500",
    }
    assert result["county_fips"].isna().all()
    assert set(result["state_fips"]) == {"06"}
    assert set(result["source_filename"]) == {raw.name}
    assert set(result["checksum_sha256"]) == {"test-checksum"}
    assert set(result["source_vintage"]) == {"2025"}
    assert result["source_observation_date"].isna().all()
    assert set(result["transformation_id"]) == {"hud_pit_native_coc_v2"}


def test_hud_pit_accepts_provider_2007_2008_layout_without_count_types(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw, old_sheets_without_count_types=True)

    result = clean_hud_pit(raw, _record(raw), tmp_path / "clean.parquet")

    old = result.loc[result["observation_date"].dt.year.isin([2007, 2008])]
    assert len(old) == 2 * 5 * 3
    assert set(old["geo_id"]) == {
        "COC_CA-600",
        "COC_CA-606",
        "COC_CA-607",
        "COC_CA-612",
        "COC_CA-500",
    }


def test_hud_pit_builds_only_the_explicit_four_coc_composite(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(
        raw,
        _record(raw),
        tmp_path / "clean.parquet",
        composite_mapping={LA_FOUR_COC_COMPOSITE_ID: LA_FOUR_COC_IDS},
    )

    composite = result.loc[
        result["geo_id"].eq("COC_COMPOSITE_LA_FOUR_COC_COMPOSITE")
    ]
    assert len(composite) == len(EXPECTED_YEARS) * 3
    assert composite["county_fips"].isna().all()
    assert composite["reported_value"].isna().all()
    assert composite["quality_flag"].str.contains("not_county").all()
    assert set(composite["transformation_id"]) == {
        "hud_pit_explicit_coc_composite_v2"
    }
    row = composite.loc[
        composite["observation_date"].eq(pd.Timestamp("2020-12-31"))
        & composite["variable_id"].eq("hud_pit_sheltered_homeless_annual")
    ].iloc[0]
    assert row["value"] == sum(2020 + index for index in range(1, 5))


def test_hud_pit_2021_count_types_are_applied_row_by_row(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(
        raw,
        _record(raw),
        tmp_path / "clean.parquet",
        composite_mapping={LA_FOUR_COC_COMPOSITE_ID: LA_FOUR_COC_IDS},
    )
    full = result.loc[
        result["observation_date"].eq("2021-12-31")
        & result["geo_id"].eq("COC_CA-600")
    ]
    assert full["value"].notna().all()
    assert full.loc[
        full["variable_id"].eq("hud_pit_unsheltered_homeless_annual"),
        "value",
    ].item() == 101
    assert full["quality_flag"].str.contains(
        "sheltered_and_unsheltered_count"
    ).all()
    assert ~full["quality_flag"].str.contains("2021_sheltered_only").any()

    partial = result.loc[
        result["observation_date"].eq("2021-12-31")
        & result["geo_id"].eq("COC_CA-606")
    ]
    assert partial.loc[
        partial["variable_id"].eq("hud_pit_sheltered_homeless_annual"),
        "value",
    ].notna().all()
    incomplete = partial.loc[
        partial["variable_id"].isin(
            {
                "hud_pit_overall_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        )
    ]
    assert incomplete["value"].isna().all()
    assert incomplete["reported_value"].notna().all()
    assert incomplete["quality_flag"].str.contains("partial_unsheltered").all()

    sheltered_only = result.loc[
        result["observation_date"].eq("2021-12-31")
        & result["geo_id"].eq("COC_CA-607")
    ]
    assert sheltered_only.loc[
        sheltered_only["variable_id"].eq("hud_pit_sheltered_homeless_annual"),
        "value",
    ].notna().all()
    assert sheltered_only.loc[
        sheltered_only["variable_id"].isin(
            {
                "hud_pit_overall_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        ),
        "value",
    ].isna().all()

    composite = result.loc[
        result["observation_date"].eq("2021-12-31")
        & result["geo_id"].eq("COC_COMPOSITE_LA_FOUR_COC_COMPOSITE")
        & result["variable_id"].isin(
            {
                "hud_pit_overall_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        )
    ]
    assert composite["value"].isna().all()


def test_non_2021_sheltered_only_rows_also_suppress_incomplete_metrics(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(
        raw,
        _record(raw),
        tmp_path / "clean.parquet",
        composite_mapping={LA_FOUR_COC_COMPOSITE_ID: LA_FOUR_COC_IDS},
    )
    rows = result.loc[
        result["observation_date"].eq("2014-12-31")
        & result["geo_id"].eq("COC_CA-606")
    ]
    assert rows.loc[
        rows["variable_id"].eq("hud_pit_sheltered_homeless_annual"), "value"
    ].notna().all()
    assert rows.loc[
        rows["variable_id"].isin(
            {
                "hud_pit_overall_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        ),
        "value",
    ].isna().all()
    composite = result.loc[
        result["observation_date"].eq("2014-12-31")
        & result["geo_id"].eq("COC_COMPOSITE_LA_FOUR_COC_COMPOSITE")
        & result["variable_id"].eq("hud_pit_overall_homeless_annual")
    ]
    assert composite["value"].isna().all()


def test_hud_pit_canonicalizes_starred_sheltered_only_label(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(raw, _record(raw), tmp_path / "clean.parquet")
    rows = result.loc[
        result["observation_date"].eq("2023-12-31")
        & result["geo_id"].eq("COC_CA-607")
    ]
    assert rows.loc[
        rows["variable_id"].eq("hud_pit_sheltered_homeless_annual"), "value"
    ].notna().all()
    incomplete = rows.loc[
        rows["variable_id"].isin(
            {
                "hud_pit_overall_homeless_annual",
                "hud_pit_unsheltered_homeless_annual",
            }
        )
    ]
    assert incomplete["value"].isna().all()
    assert incomplete["quality_flag"].str.contains(
        "sheltered_only_count"
    ).all()


def test_hud_pit_includes_2025_and_complete_composite(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    result = clean_hud_pit(
        raw,
        _record(raw),
        tmp_path / "clean.parquet",
        composite_mapping={LA_FOUR_COC_COMPOSITE_ID: LA_FOUR_COC_IDS},
    )
    assert max(result["observation_date"].dt.year) == 2025
    composite = result.loc[
        result["observation_date"].eq("2025-12-31")
        & result["geo_id"].eq("COC_COMPOSITE_LA_FOUR_COC_COMPOSITE")
    ]
    assert len(composite) == 3
    assert composite["value"].notna().all()
    assert composite["quality_flag"].str.contains("complete_sum").all()


def test_hud_pit_rejects_unknown_count_type(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(
        raw,
        count_type_overrides={(2025, "CA-600"): "Unexpected Count Type"},
    )

    with pytest.raises(ValueError, match="unsupported Count Types"):
        clean_hud_pit(raw, _record(raw), tmp_path / "clean.parquet")


def test_hud_pit_rejects_any_other_composite_membership(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw)

    with pytest.raises(ValueError, match="only supported HUD PIT derived geography"):
        clean_hud_pit(
            raw,
            _record(raw),
            tmp_path / "clean.parquet",
            composite_mapping={
                LA_FOUR_COC_COMPOSITE_ID: ("CA-600", "CA-606", "CA-607")
            },
        )


def test_hud_pit_schema_drift_fails_before_normalization(tmp_path):
    raw = tmp_path / "hud_pit.xlsx"
    _write_workbook(raw, omit_column="Unsheltered Homeless")

    with pytest.raises(ValueError, match="required columns"):
        clean_hud_pit(raw, _record(raw), tmp_path / "clean.parquet")
