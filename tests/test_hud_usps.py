import math
import json
from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.hud_usps import (
    ACTIVE_PROVENANCE_STATUS,
    HUD_USPS_VARIABLE_IDS,
    PROVENANCE_STATUS,
    build_hud_usps_wide_tables,
    clean_hud_usps_archive,
    normalize_hud_usps_quarterly_tables,
    normalize_hud_usps_table,
)
from src.common.config import load_config
from src.ingestion.hud_usps import _targets


def _values(
    *,
    total: float = 1_000,
    short: float = 10,
    long: float = 5,
    no_stat: float = 20,
) -> dict[str, float]:
    vacant = short + long
    return {
        "total_residential_addresses": total,
        "short_term_vacant_residential_addresses": short,
        "long_term_vacant_residential_addresses": long,
        "vacant_residential_addresses": vacant,
        "no_stat_residential_addresses": no_stat,
        "active_residential_addresses": total - vacant - no_stat,
        "usps_residential_vacancy_rate": vacant / total,
        "usps_residential_no_stat_rate": no_stat / total,
    }


def _county(**value_overrides: float) -> pd.DataFrame:
    values = _values()
    values.update(value_overrides)
    return pd.DataFrame(
        [
            {
                "quarter_end": "2026-03-31",
                "county_fips": "06037",
                "county_name": "Los Angeles County",
                "is_modeled_geography": 0,
                **values,
            }
        ]
    )


def _tract(**value_overrides: float) -> pd.DataFrame:
    values = _values()
    values.update(value_overrides)
    return pd.DataFrame(
        [
            {
                "quarter_end": "2026-03-31",
                "tract_geoid": "06037101110",
                "county_fips": "06037",
                "county_name": "Los Angeles County",
                "is_modeled_geography": False,
                **values,
            }
        ]
    )


def _zip(**value_overrides: float) -> pd.DataFrame:
    values = _values(total=100.25, short=1.25, long=0.25, no_stat=2.5)
    values.update(value_overrides)
    return pd.DataFrame(
        [
            {
                "quarter_end": "2026-03-31",
                "zip_code": "90001",
                "zip_city": "Los Angeles",
                "geography_scope": "Los Angeles County portion",
                "crosswalk_vintage": "HUD-USPS-to-2020-tract snapshot",
                "source_tract_count": 2,
                "crosswalk_relationship_count": 3,
                "source_incomplete_allocations": 0,
                "low_address_count_flag": False,
                "is_modeled_geography": 1,
                **values,
            }
        ]
    )


def test_combines_three_geographies_and_all_eight_variables() -> None:
    result = normalize_hud_usps_quarterly_tables(
        _county(), _tract(), _zip()
    )
    observations = result.observations

    assert len(observations) == 24
    assert set(observations["variable_id"]) == set(HUD_USPS_VARIABLE_IDS)
    assert set(observations["geo_type"]) == {
        "county",
        "tract_2020",
        "postal_zip_county_part",
    }
    assert observations["provenance_status"].eq(PROVENANCE_STATUS).all()
    assert observations.loc[
        observations["geo_type"].eq("postal_zip_county_part"), "is_modeled"
    ].all()
    assert not observations.loc[
        ~observations["geo_type"].eq("postal_zip_county_part"), "is_modeled"
    ].any()
    zip_rows = observations.loc[
        observations["geo_type"].eq("postal_zip_county_part")
    ]
    assert zip_rows["geo_id"].eq("ZIPCOUNTYPART_06037_90001").all()
    assert zip_rows["postal_zip"].eq("90001").all()
    assert zip_rows["county_fips"].eq("06037").all()
    assert zip_rows["geography_scope"].eq("Los Angeles County portion").all()
    assert observations["quarter_end"].eq(pd.Timestamp("2026-03-31")).all()


def test_null_values_are_preserved_and_not_derived() -> None:
    county = _county()
    county.loc[0, "short_term_vacant_residential_addresses"] = math.nan
    county.loc[0, "vacant_residential_addresses"] = math.nan
    county.loc[0, "usps_residential_vacancy_rate"] = math.nan

    observations = normalize_hud_usps_table(county, "county").observations
    values = observations.set_index("variable_id")["value"]

    assert pd.isna(values["usps_short_term_vacant_residential_addresses"])
    assert pd.isna(values["usps_vacant_residential_addresses"])
    assert pd.isna(values["usps_residential_vacancy_rate"])
    assert values["usps_no_stat_residential_addresses"] == 20
    assert values["usps_residential_no_stat_rate"] == 0.02


@pytest.mark.parametrize(
    ("column", "bad_value", "message"),
    [
        ("vacant_residential_addresses", 16, "short-term plus long-term"),
        ("active_residential_addresses", 966, "active plus vacant plus no-stat"),
        ("usps_residential_vacancy_rate", 0.5, "vacant_residential_addresses"),
        ("usps_residential_no_stat_rate", 0.5, "no_stat_residential_addresses"),
    ],
)
def test_formula_failures_raise(
    column: str,
    bad_value: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_hud_usps_table(_county(**{column: bad_value}), "county")


@pytest.mark.parametrize(
    ("column", "bad_value", "message"),
    [
        ("total_residential_addresses", -1, "cannot be negative"),
        ("usps_residential_vacancy_rate", 1.01, r"ratio in \[0, 1\]"),
    ],
)
def test_impossible_bounds_raise(
    column: str,
    bad_value: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_hud_usps_table(_county(**{column: bad_value}), "county")


def test_plausible_outlier_returns_warning_instead_of_raising() -> None:
    county = pd.DataFrame(
        [
            {
                **_county().iloc[0].to_dict(),
                **_values(total=100, short=20, long=10, no_stat=5),
                "quarter_end": "2025-12-31",
            },
            {
                **_county().iloc[0].to_dict(),
                **_values(total=100, short=5, long=1, no_stat=5),
                "quarter_end": "2026-03-31",
            },
        ]
    )

    result = normalize_hud_usps_table(county, "county")

    assert len(result.observations) == 16
    assert "high_rate_review" in set(result.warnings["warning_code"])
    assert "large_quarterly_rate_change" in set(
        result.warnings["warning_code"]
    )


@pytest.mark.parametrize(
    ("mutator", "geo_type", "message"),
    [
        (
            lambda frame: frame.assign(county_fips="6037"),
            "county",
            "exactly 5 digits",
        ),
        (
            lambda frame: frame.assign(quarter_end="2026-03-30"),
            "county",
            "calendar-quarter end",
        ),
        (
            lambda frame: pd.concat([frame, frame], ignore_index=True),
            "county",
            "duplicate geography-quarter keys",
        ),
        (
            lambda frame: frame.assign(tract_geoid="06059101110"),
            "tract_2020",
            "must match tract_geoid",
        ),
        (
            lambda frame: frame.assign(is_modeled_geography=1),
            "tract_2020",
            "must be False",
        ),
        (
            lambda frame: frame.assign(is_modeled_geography=0),
            "postal_zip_county_part",
            "must be True",
        ),
        (
            lambda frame: frame.assign(geography_scope="Whole ZIP"),
            "postal_zip_county_part",
            "LA County portion",
        ),
    ],
)
def test_structural_keys_ids_and_model_flags_raise(
    mutator,
    geo_type: str,
    message: str,
) -> None:
    base = {
        "county": _county,
        "tract_2020": _tract,
        "postal_zip_county_part": _zip,
    }[geo_type]()
    with pytest.raises(ValueError, match=message):
        normalize_hud_usps_table(mutator(base), geo_type)


def test_no_stat_is_separate_from_vacancy_numerator() -> None:
    observations = normalize_hud_usps_table(_county(), "county").observations
    values = observations.set_index("variable_id")["value"]

    assert values["usps_residential_vacancy_rate"] == pytest.approx(0.015)
    assert values["usps_residential_no_stat_rate"] == pytest.approx(0.02)
    assert values["usps_active_residential_addresses"] == 965
    assert (
        values["usps_vacant_residential_addresses"]
        + values["usps_no_stat_residential_addresses"]
        + values["usps_active_residential_addresses"]
        == values["usps_total_residential_addresses"]
    )


def test_modeled_zip_allows_fractional_allocations_and_surfaces_diagnostics() -> None:
    zip_table = _zip(
        source_incomplete_allocations=1,
        low_address_count_flag=True,
    )

    result = normalize_hud_usps_table(
        zip_table, "postal_zip_county_part"
    )

    assert result.observations["is_modeled"].all()
    assert result.observations[
        "source_incomplete_allocations"
    ].eq(1).all()
    assert {
        "source_incomplete_allocations",
        "source_low_address_count_flag",
    }.issubset(set(result.warnings["warning_code"]))


def test_official_archive_targets_cover_every_quarter_and_support_file() -> None:
    config = load_config()
    source = next(
        item
        for item in config.sources
        if item.source_id == "hud_usps_ncwm_quarterly"
    )

    targets = _targets(source, "2008-01-01", "2026-03-31")

    ncwm = [
        target
        for target in targets
        if target["artifact_role"] == "ncwm_standardized_2020"
    ]
    assert len(ncwm) == 73
    assert ncwm[0]["archive_period"] == "2008-Q1"
    assert ncwm[-1]["archive_period"] == "2026-Q1"
    assert {target["artifact_role"] for target in targets} == {
        "ncwm_standardized_2020",
        "tract_zip_crosswalk",
        "county_fips_reference_2020",
    }
    county_reference = next(
        target
        for target in targets
        if target["artifact_role"] == "county_fips_reference_2020"
    )
    assert county_reference["download_url"].endswith("st06_ca_cou2020.txt")
    assert county_reference["original_filename"] == "st06_ca_cou2020.txt"


def _write_active_archive_fixture(
    root: Path,
) -> list[tuple[Path, dict[str, str]]]:
    county_codes = [f"{number:03d}" for number in range(58)]
    if "037" not in county_codes:
        county_codes[-1] = "037"
    county_codes = sorted(set(county_codes))
    while len(county_codes) < 58:
        candidate = f"{len(county_codes) + 100:03d}"
        county_codes.append(candidate)
    county_rows = [[f"County {code}, California", "06", code] for code in county_codes]
    county_payload = [["NAME", "state", "county"], *county_rows]
    county_path = root / "county_reference.json"
    county_path.write_text(json.dumps(county_payload), encoding="utf-8")

    results = []
    la_tracts = [f"06037{number:06d}" for number in range(2_494)]
    for tract in la_tracts:
        results.append(
            {
                "TRACT ID": tract,
                "STCNTY": "06037",
                "TOTAL_RESIDENTIAL_ADDRESSES": "100",
                "STV_RESIDENTIAL_ADDRESSES": "1",
                "LTV_RESIDENTIAL_ADDRESSES": "1",
                "NO_STAT_RESIDENTIAL_ADDRESSES": "3",
                "Active_Residential_Addresses": "95",
            }
        )
    for code in county_codes:
        if code == "037":
            continue
        results.append(
            {
                "TRACT ID": f"06{code}000001",
                "STCNTY": f"06{code}",
                "TOTAL_RESIDENTIAL_ADDRESSES": "100",
                "STV_RESIDENTIAL_ADDRESSES": "1",
                "LTV_RESIDENTIAL_ADDRESSES": "1",
                "NO_STAT_RESIDENTIAL_ADDRESSES": "3",
                "Active_Residential_Addresses": "95",
            }
        )
    ncwm_path = root / "ncwm_2026q1.json"
    ncwm_path.write_text(
        json.dumps(
            {"year": "2026", "month": 3, "type": "1", "results": results}
        ),
        encoding="utf-8",
    )

    crosswalk_path = root / "crosswalk_2026q1.json"
    crosswalk_path.write_text(
        json.dumps(
            {
                "data": {
                    "year": "2026",
                    "quarter": "Q1",
                    "results": [
                        {
                            "tract": tract,
                            "geoid": "90001",
                            "res_ratio": 1,
                            "city": "Los Angeles",
                        }
                        for tract in la_tracts
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    common = {
        "source_id": "hud_usps_ncwm_quarterly",
        "data_vintage": "2026-08-03",
        "retrieved_at": "2026-08-03T08:00:00+00:00",
        "checksum_sha256": "a" * 64,
    }
    return [
        (
            ncwm_path,
            {
                **common,
                "artifact_role": "ncwm_standardized_2020",
                "archive_period": "2026-Q1",
                "provider_vintage": "2026Q1",
                "source_filename": ncwm_path.name,
            },
        ),
        (
            crosswalk_path,
            {
                **common,
                "artifact_role": "tract_zip_crosswalk",
                "archive_period": "2026-Q1-crosswalk",
                "provider_vintage": "2026Q1",
                "source_filename": crosswalk_path.name,
            },
        ),
        (
            county_path,
            {
                **common,
                "artifact_role": "county_fips_reference_2020",
                "archive_period": "2020-county-fips-reference",
                "provider_vintage": "2020 Census",
                "source_filename": county_path.name,
            },
        ),
    ]


def test_rebuilds_active_tables_from_archived_official_json(tmp_path: Path) -> None:
    archives = _write_active_archive_fixture(tmp_path)

    county, tract, postal_zip, metadata = build_hud_usps_wide_tables(archives)

    assert len(county) == 58
    assert len(tract) == 2_494
    assert len(postal_zip) == 1
    assert metadata["crosswalk_vintage"] == "2026Q1"
    assert metadata["crosswalk_zero_ratio_tract_count"] == 0
    allocation = metadata["zip_allocation_coverage"]
    assert allocation["unallocated_residual"].eq(0).all()
    la_county = county.loc[county["county_fips"].eq("06037")].iloc[0]
    assert la_county["usps_residential_vacancy_rate"] == pytest.approx(0.02)
    assert postal_zip.iloc[0]["usps_residential_vacancy_rate"] == pytest.approx(
        0.02
    )


def test_county_aggregation_does_not_emit_partial_component_sums(
    tmp_path: Path,
) -> None:
    archives = _write_active_archive_fixture(tmp_path)
    ncwm_path = next(
        path
        for path, record in archives
        if record["artifact_role"] == "ncwm_standardized_2020"
    )
    payload = json.loads(ncwm_path.read_text(encoding="utf-8"))
    payload["results"][0]["STV_RESIDENTIAL_ADDRESSES"] = None
    ncwm_path.write_text(json.dumps(payload), encoding="utf-8")

    county, _, _, _ = build_hud_usps_wide_tables(archives)

    la_county = county.loc[county["county_fips"].eq("06037")].iloc[0]
    assert pd.isna(la_county["short_term_vacant_residential_addresses"])
    assert pd.isna(la_county["vacant_residential_addresses"])
    assert pd.isna(la_county["usps_residential_vacancy_rate"])
    assert la_county["total_residential_addresses"] == 249_400
    assert la_county["source_tracts_missing_required"] == 1


def test_active_cleaner_preserves_raw_provenance_and_model_flags(
    tmp_path: Path,
) -> None:
    archives = _write_active_archive_fixture(tmp_path)
    output = tmp_path / "clean.parquet"

    cleaned = clean_hud_usps_archive(
        archives,
        "hud_usps_ncwm_quarterly",
        output,
        tmp_path / "warnings.csv",
        tmp_path / "restricted",
        expected_start="2026-Q1",
        expected_end="2026-Q1",
    )

    assert output.is_file()
    assert (tmp_path / "hud_usps_zip_allocation_coverage.csv").is_file()
    lineage_path = tmp_path / "restricted" / "long_observations_with_lineage.parquet"
    assert lineage_path.is_file()
    lineage = pd.read_parquet(lineage_path)
    assert len(cleaned) == (58 + 2_494 + 1) * 8
    assert cleaned["source_id"].eq("hud_usps_ncwm_quarterly").all()
    assert cleaned["source_vintage"].eq("2026Q1").all()
    assert cleaned["checksum_sha256"].eq("a" * 64).all()
    assert cleaned["provider_last_updated"].isna().all()
    tract_components = cleaned.loc[
        cleaned["geo_id"].str.startswith("TRACT_")
        & cleaned["variable_id"].eq("usps_total_residential_addresses")
    ]
    constructed_components = cleaned.loc[
        ~cleaned["geo_id"].str.startswith("TRACT_")
        & cleaned["variable_id"].eq("usps_total_residential_addresses")
    ]
    assert tract_components["reported_value"].notna().all()
    assert constructed_components["reported_value"].isna().all()
    assert lineage.loc[
        lineage["geo_type"].eq("postal_zip_county_part"), "is_modeled"
    ].all()
    assert not lineage.loc[
        ~lineage["geo_type"].eq("postal_zip_county_part"), "is_modeled"
    ].any()
    zip_lineage = lineage.loc[
        lineage["geo_type"].eq("postal_zip_county_part")
    ]
    assert zip_lineage["crosswalk_vintage"].eq("2026Q1").all()
    assert zip_lineage["crosswalk_source_filename"].eq(
        "crosswalk_2026q1.json"
    ).all()
    assert zip_lineage["crosswalk_checksum_sha256"].eq("a" * 64).all()
    assert lineage["provenance_status"].eq(ACTIVE_PROVENANCE_STATUS).all()
    assert not cleaned["is_imputed"].any()
    assert not cleaned["is_interpolated"].any()
    assert cleaned["transformation_id"].eq(
        "hud_usps_official_raw_to_quarterly_v2"
    ).all()
    assert ACTIVE_PROVENANCE_STATUS == "official_raw_archive_rebuilt"
