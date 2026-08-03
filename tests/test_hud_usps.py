import math

import pandas as pd
import pytest

from src.cleaning.hud_usps import (
    HUD_USPS_VARIABLE_IDS,
    PROVENANCE_STATUS,
    normalize_hud_usps_quarterly_tables,
    normalize_hud_usps_table,
)


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
