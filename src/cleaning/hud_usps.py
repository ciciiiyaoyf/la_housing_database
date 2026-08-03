"""Validate a quarantined HUD/USPS-derived quarterly snapshot.

This module deliberately does *not* ingest or authenticate an official HUD or
USPS release.  It only validates and normalizes three already-derived tables
whose raw-file provenance is incomplete.  Callers must keep the resulting
``provenance_status`` unchanged until the observations can be rebuilt from an
archived provider file.

The postal-ZIP table represents the Los Angeles County portion of a postal ZIP.
It is modeled from 2020 Census tracts and must never be treated as either a
whole postal ZIP or a Census ZCTA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import pandas as pd

GeographyType = Literal["county", "tract_2020", "postal_zip_county_part"]

PROVENANCE_STATUS: Final = "quarantined_derived_snapshot"
LA_COUNTY_FIPS: Final = "06037"
CA_STATE_FIPS: Final = "06"
LA_ZIP_SCOPE: Final = "Los Angeles County portion"

VALUE_COLUMNS: Final[tuple[str, ...]] = (
    "total_residential_addresses",
    "short_term_vacant_residential_addresses",
    "long_term_vacant_residential_addresses",
    "vacant_residential_addresses",
    "no_stat_residential_addresses",
    "active_residential_addresses",
    "usps_residential_vacancy_rate",
    "usps_residential_no_stat_rate",
)

VARIABLE_SPECS: Final[dict[str, tuple[str, str]]] = {
    "total_residential_addresses": (
        "usps_total_residential_addresses",
        "addresses",
    ),
    "short_term_vacant_residential_addresses": (
        "usps_short_term_vacant_residential_addresses",
        "addresses",
    ),
    "long_term_vacant_residential_addresses": (
        "usps_long_term_vacant_residential_addresses",
        "addresses",
    ),
    "vacant_residential_addresses": (
        "usps_vacant_residential_addresses",
        "addresses",
    ),
    "no_stat_residential_addresses": (
        "usps_no_stat_residential_addresses",
        "addresses",
    ),
    "active_residential_addresses": (
        "usps_active_residential_addresses",
        "addresses",
    ),
    "usps_residential_vacancy_rate": (
        "usps_residential_vacancy_rate",
        "ratio",
    ),
    "usps_residential_no_stat_rate": (
        "usps_residential_no_stat_rate",
        "ratio",
    ),
}

HUD_USPS_VARIABLE_IDS: Final[tuple[str, ...]] = tuple(
    variable_id for variable_id, _ in VARIABLE_SPECS.values()
)

COUNTY_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "quarter_end",
    "county_fips",
    "county_name",
    "is_modeled_geography",
    *VALUE_COLUMNS,
)
TRACT_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "quarter_end",
    "tract_geoid",
    "county_fips",
    "county_name",
    "is_modeled_geography",
    *VALUE_COLUMNS,
)
ZIP_DIAGNOSTIC_COLUMNS: Final[tuple[str, ...]] = (
    "source_tract_count",
    "crosswalk_relationship_count",
    "source_incomplete_allocations",
    "low_address_count_flag",
)
ZIP_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "quarter_end",
    "zip_code",
    "zip_city",
    "geography_scope",
    "crosswalk_vintage",
    "is_modeled_geography",
    *ZIP_DIAGNOSTIC_COLUMNS,
    *VALUE_COLUMNS,
)

WARNING_COLUMNS: Final[tuple[str, ...]] = (
    "geo_type",
    "geo_id",
    "quarter_end",
    "variable_id",
    "warning_code",
    "value",
    "message",
)

# Values outside these ranges are still mathematically possible, so they are
# review warnings rather than structural failures.
HIGH_RATE_WARNING_THRESHOLD: Final = 0.20
RATE_CHANGE_WARNING_THRESHOLD: Final = 0.10
LOW_ADDRESS_WARNING_THRESHOLD: Final = 25.0


@dataclass(frozen=True)
class HudUspsNormalizationResult:
    """Long observations plus non-fatal, reviewable economic warnings."""

    observations: pd.DataFrame
    warnings: pd.DataFrame


def _required_columns(geo_type: GeographyType) -> tuple[str, ...]:
    if geo_type == "county":
        return COUNTY_REQUIRED_COLUMNS
    if geo_type == "tract_2020":
        return TRACT_REQUIRED_COLUMNS
    if geo_type == "postal_zip_county_part":
        return ZIP_REQUIRED_COLUMNS
    raise ValueError(f"Unsupported HUD/USPS geography type: {geo_type!r}")


def _fail_if_missing_columns(
    frame: pd.DataFrame,
    geo_type: GeographyType,
) -> None:
    missing = sorted(set(_required_columns(geo_type)) - set(frame.columns))
    if missing:
        raise ValueError(
            f"HUD/USPS {geo_type} table is missing required columns: {missing}"
        )


def _parse_quarter_end(values: pd.Series, geo_type: GeographyType) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    invalid = values.notna() & parsed.isna()
    if invalid.any() or parsed.isna().any():
        rows = values.index[parsed.isna()].tolist()[:5]
        raise ValueError(
            f"HUD/USPS {geo_type} quarter_end is missing or invalid at rows "
            f"{rows}"
        )
    expected = parsed.dt.to_period("Q").dt.end_time.dt.normalize()
    not_quarter_end = parsed.dt.normalize().ne(expected)
    if not_quarter_end.any():
        examples = parsed.loc[not_quarter_end].astype(str).tolist()[:5]
        raise ValueError(
            f"HUD/USPS {geo_type} quarter_end must be a calendar-quarter end; "
            f"examples={examples}"
        )
    return parsed.dt.normalize()


def _strict_identifier(
    values: pd.Series,
    *,
    column: str,
    width: int,
) -> pd.Series:
    if not isinstance(values.dtype, pd.StringDtype) and values.dtype != object:
        raise ValueError(
            f"HUD/USPS {column} must be stored as a string to preserve leading "
            "zeros"
        )
    normalized = values.astype("string").str.strip()
    valid = normalized.str.fullmatch(rf"\d{{{width}}}", na=False)
    if not valid.all():
        examples = values.loc[~valid].astype(str).tolist()[:5]
        raise ValueError(
            f"HUD/USPS {column} must contain exactly {width} digits; "
            f"examples={examples}"
        )
    return normalized


def _strict_boolean(values: pd.Series, column: str) -> pd.Series:
    true_values = {True, 1, "1", "true", "True", "TRUE"}
    false_values = {False, 0, "0", "false", "False", "FALSE"}

    def convert(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        if value in true_values:
            return True
        if value in false_values:
            return False
        return pd.NA

    converted = values.map(convert).astype("boolean")
    if converted.isna().any():
        examples = values.loc[converted.isna()].astype(str).tolist()[:5]
        raise ValueError(
            f"HUD/USPS {column} must contain explicit boolean values; "
            f"examples={examples}"
        )
    return converted


def _numeric(values: pd.Series, column: str) -> pd.Series:
    converted = pd.to_numeric(values, errors="coerce")
    invalid = values.notna() & converted.isna()
    if invalid.any():
        examples = values.loc[invalid].astype(str).tolist()[:5]
        raise ValueError(
            f"HUD/USPS {column} contains non-numeric values: {examples}"
        )
    return converted.astype("Float64")


def _raise_rows(message: str, mask: pd.Series) -> None:
    rows = mask.index[mask.fillna(False)].tolist()[:5]
    if rows:
        raise ValueError(f"{message}; rows={rows}")


def _validate_geography(
    frame: pd.DataFrame,
    geo_type: GeographyType,
) -> pd.DataFrame:
    result = frame.copy()
    result["quarter_end"] = _parse_quarter_end(result["quarter_end"], geo_type)
    result["is_modeled_geography"] = _strict_boolean(
        result["is_modeled_geography"], "is_modeled_geography"
    )

    expected_modeled = geo_type == "postal_zip_county_part"
    _raise_rows(
        f"HUD/USPS {geo_type} is_modeled_geography must be "
        f"{expected_modeled}",
        result["is_modeled_geography"].ne(expected_modeled),
    )

    if geo_type == "county":
        result["county_fips"] = _strict_identifier(
            result["county_fips"], column="county_fips", width=5
        )
        _raise_rows(
            "HUD/USPS county table must contain California county FIPS codes",
            ~result["county_fips"].str.startswith(CA_STATE_FIPS),
        )
        key_columns = ["county_fips", "quarter_end"]
        result["geo_id"] = "COUNTY_" + result["county_fips"]
        result["state_fips"] = result["county_fips"].str[:2]
        result["tract_geoid"] = pd.NA
        result["postal_zip"] = pd.NA
    elif geo_type == "tract_2020":
        result["tract_geoid"] = _strict_identifier(
            result["tract_geoid"], column="tract_geoid", width=11
        )
        result["county_fips"] = _strict_identifier(
            result["county_fips"], column="county_fips", width=5
        )
        _raise_rows(
            "HUD/USPS 2020 tract county_fips must match tract_geoid",
            result["tract_geoid"].str[:5].ne(result["county_fips"]),
        )
        _raise_rows(
            "HUD/USPS 2020 tract table must contain Los Angeles County tracts",
            result["county_fips"].ne(LA_COUNTY_FIPS),
        )
        key_columns = ["tract_geoid", "quarter_end"]
        result["geo_id"] = "TRACT_" + result["tract_geoid"]
        result["state_fips"] = result["county_fips"].str[:2]
        result["postal_zip"] = pd.NA
        result["geography_vintage"] = "2020"
    else:
        result["zip_code"] = _strict_identifier(
            result["zip_code"], column="zip_code", width=5
        )
        scope = result["geography_scope"].astype("string").str.strip()
        _raise_rows(
            "HUD/USPS ZIP geography_scope must identify the LA County portion",
            scope.ne(LA_ZIP_SCOPE),
        )
        result["geography_scope"] = scope
        for column in ("zip_city", "crosswalk_vintage"):
            missing = result[column].astype("string").str.strip().eq("") | result[
                column
            ].isna()
            _raise_rows(f"HUD/USPS ZIP {column} cannot be blank", missing)
        for column in ZIP_DIAGNOSTIC_COLUMNS[:-1]:
            result[column] = _numeric(result[column], column)
            _raise_rows(
                f"HUD/USPS ZIP {column} cannot be negative",
                result[column].lt(0),
            )
            non_integer = result[column].notna() & ~np.isclose(
                result[column].astype(float),
                np.round(result[column].astype(float)),
                atol=1e-9,
                rtol=0,
            )
            _raise_rows(
                f"HUD/USPS ZIP {column} must be integer-valued",
                pd.Series(non_integer, index=result.index),
            )
        result["low_address_count_flag"] = _strict_boolean(
            result["low_address_count_flag"], "low_address_count_flag"
        )
        key_columns = ["zip_code", "quarter_end"]
        result["county_fips"] = LA_COUNTY_FIPS
        result["state_fips"] = CA_STATE_FIPS
        result["postal_zip"] = result["zip_code"]
        result["tract_geoid"] = pd.NA
        result["county_name"] = "Los Angeles County"
        result["geo_id"] = (
            "ZIPCOUNTYPART_" + LA_COUNTY_FIPS + "_" + result["zip_code"]
        )

    duplicate = result.duplicated(key_columns, keep=False)
    _raise_rows(
        f"HUD/USPS {geo_type} table has duplicate geography-quarter keys",
        duplicate,
    )

    if "county_name" in result:
        blank_county = result["county_name"].astype("string").str.strip().eq("") | (
            result["county_name"].isna()
        )
        _raise_rows("HUD/USPS county_name cannot be blank", blank_county)
        inconsistent_names = (
            result.groupby("county_fips", dropna=False)["county_name"]
            .transform(lambda values: values.astype("string").nunique(dropna=True))
            .gt(1)
        )
        _raise_rows(
            "HUD/USPS county_fips maps to multiple county_name values",
            inconsistent_names,
        )

    result["geo_type"] = geo_type
    result["is_modeled"] = expected_modeled
    return result


def _validate_values(
    frame: pd.DataFrame,
    geo_type: GeographyType,
) -> pd.DataFrame:
    result = frame.copy()
    for column in VALUE_COLUMNS:
        result[column] = _numeric(result[column], column)

    count_columns = VALUE_COLUMNS[:6]
    for column in count_columns:
        _raise_rows(
            f"HUD/USPS {column} cannot be negative",
            result[column].lt(0),
        )

    if geo_type != "postal_zip_county_part":
        for column in count_columns:
            values = result[column]
            non_integer = values.notna() & ~np.isclose(
                values.astype(float),
                np.round(values.astype(float)),
                atol=1e-9,
                rtol=0,
            )
            _raise_rows(
                f"HUD/USPS observed {geo_type} {column} must be integer-valued",
                pd.Series(non_integer, index=result.index),
            )

    for column in VALUE_COLUMNS[-2:]:
        _raise_rows(
            f"HUD/USPS {column} must be a ratio in [0, 1]",
            result[column].notna()
            & (result[column].lt(0) | result[column].gt(1)),
        )

    tolerance = 1e-7 if geo_type == "postal_zip_county_part" else 1e-8
    _validate_identity(
        result,
        "vacant_residential_addresses",
        (
            "short_term_vacant_residential_addresses",
            "long_term_vacant_residential_addresses",
        ),
        tolerance,
        "vacant residential addresses must equal short-term plus long-term",
    )
    _validate_identity(
        result,
        "total_residential_addresses",
        (
            "active_residential_addresses",
            "vacant_residential_addresses",
            "no_stat_residential_addresses",
        ),
        tolerance,
        "total residential addresses must equal active plus vacant plus no-stat",
    )
    _validate_rate(
        result,
        "usps_residential_vacancy_rate",
        "vacant_residential_addresses",
        tolerance,
    )
    _validate_rate(
        result,
        "usps_residential_no_stat_rate",
        "no_stat_residential_addresses",
        tolerance,
    )
    return result


def _validate_identity(
    frame: pd.DataFrame,
    total_column: str,
    component_columns: tuple[str, ...],
    tolerance: float,
    description: str,
) -> None:
    complete = frame[[total_column, *component_columns]].notna().all(axis=1)
    expected = frame[list(component_columns)].sum(axis=1, min_count=len(component_columns))
    mismatch = complete & ~np.isclose(
        frame[total_column].astype(float),
        expected.astype(float),
        atol=tolerance,
        rtol=1e-10,
    )
    _raise_rows(f"HUD/USPS formula failure: {description}", mismatch)


def _validate_rate(
    frame: pd.DataFrame,
    rate_column: str,
    numerator_column: str,
    tolerance: float,
) -> None:
    complete = frame[[rate_column, numerator_column, "total_residential_addresses"]].notna().all(
        axis=1
    )
    denominator = frame["total_residential_addresses"]
    invalid_zero = complete & denominator.eq(0)
    _raise_rows(
        f"HUD/USPS formula failure: {rate_column} must be null when total "
        "residential addresses are zero",
        invalid_zero,
    )
    comparable = complete & denominator.gt(0)
    expected = frame[numerator_column] / denominator
    mismatch = comparable & ~np.isclose(
        frame[rate_column].astype(float),
        expected.astype(float),
        atol=tolerance,
        rtol=1e-10,
    )
    _raise_rows(
        f"HUD/USPS formula failure: {rate_column} must equal "
        f"{numerator_column} / total_residential_addresses",
        mismatch,
    )


def _warning_frame(frame: pd.DataFrame) -> pd.DataFrame:
    warnings: list[dict[str, object]] = []

    def add_warning(
        row: pd.Series,
        *,
        variable_id: str | None,
        warning_code: str,
        value: object,
        message: str,
    ) -> None:
        warnings.append(
            {
                "geo_type": row["geo_type"],
                "geo_id": row["geo_id"],
                "quarter_end": row["quarter_end"],
                "variable_id": variable_id,
                "warning_code": warning_code,
                "value": value,
                "message": message,
            }
        )

    for column in VALUE_COLUMNS[-2:]:
        variable_id = VARIABLE_SPECS[column][0]
        high = frame[column].gt(HIGH_RATE_WARNING_THRESHOLD).fillna(False)
        for _, row in frame.loc[high].iterrows():
            add_warning(
                row,
                variable_id=variable_id,
                warning_code="high_rate_review",
                value=row[column],
                message=(
                    f"Plausible but unusually high {column}; review rather than "
                    "discard."
                ),
            )

    ordered = frame.sort_values(["geo_id", "quarter_end"])
    for column in VALUE_COLUMNS[-2:]:
        variable_id = VARIABLE_SPECS[column][0]
        changes = ordered.groupby("geo_id", sort=False)[column].diff().abs()
        large_change = changes.gt(RATE_CHANGE_WARNING_THRESHOLD).fillna(False)
        for index, row in ordered.loc[large_change].iterrows():
            add_warning(
                row,
                variable_id=variable_id,
                warning_code="large_quarterly_rate_change",
                value=changes.loc[index],
                message=(
                    f"Absolute quarter-to-quarter change in {column} exceeds "
                    f"{RATE_CHANGE_WARNING_THRESHOLD:.2f}."
                ),
            )

    low_total = frame["total_residential_addresses"].lt(
        LOW_ADDRESS_WARNING_THRESHOLD
    ).fillna(False)
    for _, row in frame.loc[low_total].iterrows():
        add_warning(
            row,
            variable_id="usps_total_residential_addresses",
            warning_code="low_address_count",
            value=row["total_residential_addresses"],
            message="Low address denominator can make rates unstable.",
        )

    if "low_address_count_flag" in frame:
        flagged = frame["low_address_count_flag"].fillna(False)
        for _, row in frame.loc[flagged].iterrows():
            add_warning(
                row,
                variable_id="usps_total_residential_addresses",
                warning_code="source_low_address_count_flag",
                value=row["total_residential_addresses"],
                message="The modeled ZIP snapshot flags a low address count.",
            )
    if "source_incomplete_allocations" in frame:
        incomplete = frame["source_incomplete_allocations"].gt(0).fillna(False)
        for _, row in frame.loc[incomplete].iterrows():
            add_warning(
                row,
                variable_id=None,
                warning_code="source_incomplete_allocations",
                value=row["source_incomplete_allocations"],
                message=(
                    "The modeled ZIP snapshot reports incomplete source "
                    "allocations."
                ),
            )

    if not warnings:
        return pd.DataFrame(columns=WARNING_COLUMNS)
    return (
        pd.DataFrame(warnings, columns=WARNING_COLUMNS)
        .sort_values(["geo_type", "geo_id", "quarter_end", "warning_code"])
        .reset_index(drop=True)
    )


def normalize_hud_usps_table(
    frame: pd.DataFrame,
    geo_type: GeographyType,
) -> HudUspsNormalizationResult:
    """Validate one quarterly table and return long observations and warnings.

    Structural problems raise ``ValueError``. Missing address values remain
    missing and are not inferred from neighboring quarters or geographies.
    Formula checks are applied only where all required components are present.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("HUD/USPS input must be a pandas DataFrame")
    _fail_if_missing_columns(frame, geo_type)
    if frame.empty:
        raise ValueError(f"HUD/USPS {geo_type} table cannot be empty")

    wide = _validate_geography(frame, geo_type)
    wide = _validate_values(wide, geo_type)
    warning_frame = _warning_frame(wide)

    id_columns = [
        "quarter_end",
        "geo_id",
        "geo_type",
        "state_fips",
        "county_fips",
        "county_name",
        "tract_geoid",
        "postal_zip",
        "is_modeled",
    ]
    optional_columns = (
        "geography_vintage",
        "zip_city",
        "geography_scope",
        "crosswalk_vintage",
        *ZIP_DIAGNOSTIC_COLUMNS,
    )
    id_columns.extend(column for column in optional_columns if column in wide)

    observations = wide.melt(
        id_vars=id_columns,
        value_vars=list(VALUE_COLUMNS),
        var_name="_source_column",
        value_name="value",
    )
    observations["variable_id"] = observations["_source_column"].map(
        lambda column: VARIABLE_SPECS[column][0]
    )
    observations["unit"] = observations["_source_column"].map(
        lambda column: VARIABLE_SPECS[column][1]
    )
    observations["observation_date"] = observations["quarter_end"]
    observations["source_observation_date"] = observations["quarter_end"]
    observations["period_end"] = observations["quarter_end"]
    observations["period_start"] = (
        observations["quarter_end"].dt.to_period("Q").dt.start_time.dt.normalize()
    )
    observations["native_frequency"] = "quarterly"
    observations["final_frequency"] = "quarterly"
    observations["provenance_status"] = PROVENANCE_STATUS
    observations["transformation_id"] = (
        "hud_usps_quarantined_snapshot_to_long_v1"
    )
    observations["quality_flag"] = PROVENANCE_STATUS

    # Explicitly do not populate provider retrieval, checksum, or raw-vintage
    # fields: this snapshot lacks the evidence needed to claim official ingest.
    ordered_columns = [
        "observation_date",
        "source_observation_date",
        "quarter_end",
        "period_start",
        "period_end",
        "geo_id",
        "geo_type",
        "state_fips",
        "county_fips",
        "county_name",
        "tract_geoid",
        "postal_zip",
        "variable_id",
        "value",
        "unit",
        "native_frequency",
        "final_frequency",
        "provenance_status",
        "is_modeled",
        "transformation_id",
        "quality_flag",
        *[column for column in optional_columns if column in observations],
    ]
    observations = (
        observations[ordered_columns]
        .sort_values(["geo_type", "geo_id", "variable_id", "quarter_end"])
        .reset_index(drop=True)
    )
    observations["value"] = observations["value"].astype("Float64")
    observations["is_modeled"] = observations["is_modeled"].astype("boolean")
    return HudUspsNormalizationResult(observations, warning_frame)


def normalize_hud_usps_quarterly_tables(
    county: pd.DataFrame,
    tract_2020: pd.DataFrame,
    postal_zip_county_part: pd.DataFrame,
) -> HudUspsNormalizationResult:
    """Validate and combine county, 2020-tract, and modeled ZIP-part tables."""

    parts = (
        normalize_hud_usps_table(county, "county"),
        normalize_hud_usps_table(tract_2020, "tract_2020"),
        normalize_hud_usps_table(
            postal_zip_county_part, "postal_zip_county_part"
        ),
    )
    observations = pd.concat(
        [part.observations for part in parts], ignore_index=True, sort=False
    )
    warnings = pd.concat(
        [part.warnings for part in parts], ignore_index=True, sort=False
    )
    observations = observations.sort_values(
        ["geo_type", "geo_id", "variable_id", "quarter_end"]
    ).reset_index(drop=True)
    if warnings.empty:
        warnings = pd.DataFrame(columns=WARNING_COLUMNS)
    else:
        warnings = warnings.sort_values(
            ["geo_type", "geo_id", "quarter_end", "warning_code"]
        ).reset_index(drop=True)
    return HudUspsNormalizationResult(observations, warnings)
