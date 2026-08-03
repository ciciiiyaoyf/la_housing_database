"""Validate HUD/USPS quarterly residential-address vacancy data.

The legacy normalizers preserve the quarantined status of the historical
derived snapshot.  The active archive path separately rebuilds the same
county, tract, and modeled ZIP-part contracts from immutable official HUD API
responses plus an official Census county reference.

The postal-ZIP table represents the Los Angeles County portion of a postal ZIP.
It is modeled from 2020 Census tracts and must never be treated as either a
whole postal ZIP or a Census ZCTA.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final, Literal

import numpy as np
import pandas as pd

from src.cleaning.common import OBSERVATION_COLUMNS, finalize

GeographyType = Literal["county", "tract_2020", "postal_zip_county_part"]

PROVENANCE_STATUS: Final = "quarantined_derived_snapshot"
ACTIVE_PROVENANCE_STATUS: Final = "official_raw_archive_rebuilt"
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
ZIP_ALLOCATION_MAX_RESIDUAL_SHARE: Final = 0.001


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


RAW_COMPONENT_COLUMNS: Final[dict[str, str]] = {
    "TOTAL_RESIDENTIAL_ADDRESSES": "total_residential_addresses",
    "STV_RESIDENTIAL_ADDRESSES": "short_term_vacant_residential_addresses",
    "LTV_RESIDENTIAL_ADDRESSES": "long_term_vacant_residential_addresses",
    "NO_STAT_RESIDENTIAL_ADDRESSES": "no_stat_residential_addresses",
    "Active_Residential_Addresses": "active_residential_addresses",
}


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read archived HUD-USPS JSON: {path}") from exc


def _manifest_role(record: dict[str, str]) -> str:
    role = str(record.get("artifact_role", "")).strip()
    if not role:
        raise ValueError(
            f"HUD-USPS manifest row lacks artifact_role: "
            f"{record.get('source_filename')}"
        )
    return role


def _archive_period_end(record: dict[str, str], payload: dict) -> pd.Timestamp:
    archive_period = str(record.get("archive_period", ""))
    match = pd.Series([archive_period]).str.extract(r"^(\d{4})-Q([1-4])$").iloc[0]
    if match.isna().any():
        raise ValueError(f"Invalid HUD-USPS archive_period: {archive_period!r}")
    period = pd.Period(f"{match.iloc[0]}Q{match.iloc[1]}", freq="Q")
    quarter_end = period.end_time.normalize()
    if str(payload.get("year")) != str(period.year):
        raise ValueError(
            f"HUD-USPS manifest/payload year mismatch for {archive_period}"
        )
    if int(payload.get("month", -1)) != int(quarter_end.month):
        raise ValueError(
            f"HUD-USPS manifest/payload month mismatch for {archive_period}"
        )
    return quarter_end


def _county_reference(path: Path) -> dict[str, str]:
    if path.suffix.lower() == ".txt":
        try:
            frame = pd.read_csv(path, sep="|", dtype="string")
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            raise ValueError(
                f"Could not read Census county reference: {path}"
            ) from exc
        required_columns = [
            "STATE",
            "STATEFP",
            "COUNTYFP",
            "COUNTYNS",
            "COUNTYNAME",
            "CLASSFP",
            "FUNCSTAT",
        ]
        if list(frame.columns) != required_columns:
            raise ValueError(
                "Census county FIPS reference schema changed: "
                f"{list(frame.columns)}"
            )
        if len(frame) != 58 or not frame["STATEFP"].eq(CA_STATE_FIPS).all():
            raise ValueError(
                "Official California county reference must contain 58 counties"
            )
        county_fips = frame["STATEFP"] + frame["COUNTYFP"].str.zfill(3)
        reference = dict(zip(county_fips, frame["COUNTYNAME"], strict=True))
        if len(reference) != 58 or any(not str(name).strip() for name in reference.values()):
            raise ValueError("Census county reference contains invalid identifiers")
        return {str(key): str(value).strip() for key, value in reference.items()}

    payload = _read_json(path)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Census county FIPS reference is empty")
    header = [str(value) for value in payload[0]]
    if header != ["NAME", "state", "county"]:
        raise ValueError(f"Census county FIPS reference schema changed: {header}")
    reference: dict[str, str] = {}
    for values in payload[1:]:
        row = dict(zip(header, values, strict=True))
        county_fips = f"{str(row['state']).zfill(2)}{str(row['county']).zfill(3)}"
        name = str(row["NAME"]).removesuffix(", California").strip()
        if not county_fips.startswith(CA_STATE_FIPS) or not name:
            raise ValueError(
                f"Invalid California county reference row: {row}"
            )
        reference[county_fips] = name
    if len(reference) != 58:
        raise ValueError(
            f"Official California county reference must contain 58 counties; "
            f"found {len(reference)}"
        )
    return reference


def _normalized_ncwm_frame(
    payload: dict,
    quarter_end: pd.Timestamp,
    county_reference: dict[str, str],
) -> pd.DataFrame:
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"HUD-USPS {quarter_end.date()} response has no results")
    frame = pd.DataFrame(rows)
    required = {"TRACT ID", "STCNTY", *RAW_COMPONENT_COLUMNS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} response missing fields: {missing}"
        )
    result = pd.DataFrame(index=frame.index)
    result["quarter_end"] = quarter_end
    result["tract_geoid"] = (
        frame["TRACT ID"].astype("string").str.strip().str.zfill(11)
    )
    result["county_fips"] = (
        frame["STCNTY"].astype("string").str.strip().str.zfill(5)
    )
    valid_tract = result["tract_geoid"].str.fullmatch(r"\d{11}", na=False)
    valid_county = result["county_fips"].str.fullmatch(r"06\d{3}", na=False)
    if not valid_tract.all() or not valid_county.all():
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} contains malformed California IDs"
        )
    mismatch = result["tract_geoid"].str[:5].ne(result["county_fips"])
    if mismatch.any():
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} tract/county IDs do not agree"
        )
    unknown_counties = sorted(set(result["county_fips"]) - set(county_reference))
    if unknown_counties:
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} contains unknown county FIPS: "
            f"{unknown_counties[:5]}"
        )
    result["county_name"] = result["county_fips"].map(county_reference)
    for raw_column, clean_column in RAW_COMPONENT_COLUMNS.items():
        values = pd.to_numeric(frame[raw_column], errors="coerce")
        invalid = frame[raw_column].notna() & values.isna()
        if invalid.any():
            examples = frame.loc[invalid, raw_column].astype(str).tolist()[:5]
            raise ValueError(
                f"HUD-USPS {quarter_end.date()} {raw_column} has invalid "
                f"values: {examples}"
            )
        result[clean_column] = values.astype("Float64")
    duplicate = result.duplicated("tract_geoid", keep=False)
    if duplicate.any():
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} has duplicate standardized tracts"
        )
    result["vacant_residential_addresses"] = (
        result["short_term_vacant_residential_addresses"]
        + result["long_term_vacant_residential_addresses"]
    )
    components = [
        "total_residential_addresses",
        "short_term_vacant_residential_addresses",
        "long_term_vacant_residential_addresses",
        "no_stat_residential_addresses",
        "active_residential_addresses",
    ]
    result["source_tract_complete"] = result[components].notna().all(axis=1)
    complete = result["source_tract_complete"]
    difference = result["total_residential_addresses"] - (
        result["active_residential_addresses"]
        + result["short_term_vacant_residential_addresses"]
        + result["long_term_vacant_residential_addresses"]
        + result["no_stat_residential_addresses"]
    )
    failure = complete & difference.abs().gt(1e-8)
    if failure.any():
        raise ValueError(
            f"HUD-USPS {quarter_end.date()} source component identity failed"
        )
    result["component_balance_difference"] = difference.where(complete)
    total = result["total_residential_addresses"]
    result["usps_residential_vacancy_rate"] = (
        result["vacant_residential_addresses"] / total
    ).where(complete & total.gt(0))
    result["usps_residential_no_stat_rate"] = (
        result["no_stat_residential_addresses"] / total
    ).where(complete & total.gt(0))
    return result


def _county_table(
    tracts: pd.DataFrame,
    county_reference: dict[str, str],
) -> pd.DataFrame:
    component_columns = [
        "total_residential_addresses",
        "short_term_vacant_residential_addresses",
        "long_term_vacant_residential_addresses",
        "vacant_residential_addresses",
        "no_stat_residential_addresses",
        "active_residential_addresses",
    ]
    grouped = tracts.groupby("county_fips", sort=True, dropna=False)
    counts = grouped[component_columns].sum(min_count=1)
    component_complete = grouped[component_columns].count().eq(
        grouped.size(), axis=0
    )
    counts = counts.where(component_complete)
    counts["source_tract_count"] = grouped.size()
    counts["source_tracts_missing_required"] = grouped[
        "source_tract_complete"
    ].apply(lambda values: int((~values).sum()))
    counts = counts.reset_index()
    if set(counts["county_fips"]) != set(county_reference):
        missing = sorted(set(county_reference) - set(counts["county_fips"]))
        extra = sorted(set(counts["county_fips"]) - set(county_reference))
        raise ValueError(
            f"HUD-USPS quarter does not contain exactly 58 California counties: "
            f"missing={missing}, extra={extra}"
        )
    counts["county_name"] = counts["county_fips"].map(county_reference)
    counts["quarter_end"] = tracts["quarter_end"].iloc[0]
    complete = counts["source_tracts_missing_required"].eq(0)
    total = counts["total_residential_addresses"]
    counts["usps_residential_vacancy_rate"] = (
        counts["vacant_residential_addresses"] / total
    ).where(complete & total.gt(0))
    counts["usps_residential_no_stat_rate"] = (
        counts["no_stat_residential_addresses"] / total
    ).where(complete & total.gt(0))
    counts["component_balance_difference"] = total - (
        counts["active_residential_addresses"]
        + counts["vacant_residential_addresses"]
        + counts["no_stat_residential_addresses"]
    )
    counts["is_modeled_geography"] = False
    return counts


def _crosswalk_frame(path: Path) -> tuple[pd.DataFrame, str]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("HUD-USPS tract-to-ZIP crosswalk must be a JSON object")
    data = payload.get("data")
    if isinstance(data, list):
        if len(data) != 1 or not isinstance(data[0], dict):
            raise ValueError("HUD-USPS crosswalk has ambiguous data blocks")
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("HUD-USPS crosswalk lacks a data object")
    rows = data.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("HUD-USPS tract-to-ZIP crosswalk has no results")
    frame = pd.DataFrame(rows)
    required = {"tract", "geoid", "res_ratio", "city"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"HUD-USPS crosswalk missing fields: {missing}")
    result = pd.DataFrame(
        {
            "tract_geoid": frame["tract"].astype("string").str.strip().str.zfill(11),
            "zip_code": frame["geoid"].astype("string").str.strip().str.zfill(5),
            "zip_city": frame["city"].astype("string").str.strip(),
            "res_ratio": pd.to_numeric(frame["res_ratio"], errors="coerce"),
        }
    )
    result = result.loc[result["tract_geoid"].str.startswith(LA_COUNTY_FIPS)].copy()
    valid = (
        result["tract_geoid"].str.fullmatch(r"\d{11}", na=False)
        & result["zip_code"].str.fullmatch(r"\d{5}", na=False)
        & result["zip_city"].notna()
        & result["zip_city"].ne("")
        & result["res_ratio"].notna()
        & result["res_ratio"].ge(0)
        & result["res_ratio"].le(1)
    )
    if not valid.all():
        raise ValueError("HUD-USPS crosswalk contains malformed LA relationships")
    if result.duplicated(["tract_geoid", "zip_code"]).any():
        raise ValueError("HUD-USPS crosswalk has duplicate tract-ZIP relationships")
    ratio_sums = result.groupby("tract_geoid")["res_ratio"].sum()
    bad_sums = ~(ratio_sums.eq(0) | np.isclose(ratio_sums, 1, atol=1e-6, rtol=0))
    if bad_sums.any():
        examples = ratio_sums.loc[bad_sums].head().to_dict()
        raise ValueError(
            f"HUD-USPS tract residential ratios must sum to zero or one: {examples}"
        )
    year = payload.get("year") or data.get("year")
    quarter = payload.get("quarter") or data.get("quarter")
    if year is None or quarter is None:
        raise ValueError("HUD-USPS crosswalk payload lacks year/quarter vintage")
    quarter_number = int(str(quarter).upper().removeprefix("Q"))
    if quarter_number not in {1, 2, 3, 4}:
        raise ValueError(f"Invalid HUD-USPS crosswalk quarter: {quarter!r}")
    vintage = f"{int(year)}Q{quarter_number}"
    return result.sort_values(["tract_geoid", "zip_code"]), vintage


def _zip_table(
    la_tracts: pd.DataFrame,
    crosswalk: pd.DataFrame,
    crosswalk_vintage: str,
) -> pd.DataFrame:
    source_tracts = set(la_tracts["tract_geoid"])
    crosswalk_tracts = set(crosswalk["tract_geoid"])
    if source_tracts != crosswalk_tracts:
        raise ValueError(
            "HUD-USPS LA tract set does not match the fixed crosswalk: "
            f"source_only={len(source_tracts - crosswalk_tracts)}, "
            f"crosswalk_only={len(crosswalk_tracts - source_tracts)}"
        )
    merged = la_tracts.merge(
        crosswalk, on="tract_geoid", how="inner", validate="one_to_many"
    )
    component_columns = [
        "total_residential_addresses",
        "short_term_vacant_residential_addresses",
        "long_term_vacant_residential_addresses",
        "no_stat_residential_addresses",
        "active_residential_addresses",
    ]
    for column in component_columns:
        merged[f"_allocated_{column}"] = merged[column] * merged["res_ratio"]
    merged["_incomplete_positive"] = (
        ~merged["source_tract_complete"] & merged["res_ratio"].gt(0)
    )
    grouped = merged.groupby("zip_code", sort=True, dropna=False)
    allocated_columns = [f"_allocated_{column}" for column in component_columns]
    result = grouped[allocated_columns].sum(min_count=1)
    result.columns = component_columns
    result["zip_city"] = grouped["zip_city"].agg(
        lambda values: "|".join(sorted(set(values.dropna().astype(str))))
    )
    result["source_tract_count"] = grouped["tract_geoid"].nunique()
    result["crosswalk_relationship_count"] = grouped.size()
    result["source_incomplete_allocations"] = grouped[
        "_incomplete_positive"
    ].sum().astype(int)
    result = result.reset_index()
    result["vacant_residential_addresses"] = (
        result["short_term_vacant_residential_addresses"]
        + result["long_term_vacant_residential_addresses"]
    )
    complete = result["source_incomplete_allocations"].eq(0)
    for column in [*component_columns, "vacant_residential_addresses"]:
        result[column] = result[column].where(complete)
    total = result["total_residential_addresses"]
    result["usps_residential_vacancy_rate"] = (
        result["vacant_residential_addresses"] / total
    ).where(complete & total.gt(0))
    result["usps_residential_no_stat_rate"] = (
        result["no_stat_residential_addresses"] / total
    ).where(complete & total.gt(0))
    result["quarter_end"] = la_tracts["quarter_end"].iloc[0]
    result["geography_scope"] = LA_ZIP_SCOPE
    result["crosswalk_vintage"] = crosswalk_vintage
    result["low_address_count_flag"] = complete & total.lt(100)
    result["is_modeled_geography"] = True
    return result


def _zip_allocation_coverage(
    la_tracts: pd.DataFrame,
    postal_zip: pd.DataFrame,
) -> pd.DataFrame:
    component_columns = [
        "total_residential_addresses",
        "short_term_vacant_residential_addresses",
        "long_term_vacant_residential_addresses",
        "no_stat_residential_addresses",
        "active_residential_addresses",
    ]
    source = la_tracts.groupby("quarter_end")[component_columns].sum(
        min_count=1
    )
    allocated = postal_zip.groupby("quarter_end")[component_columns].sum(
        min_count=1
    )
    records: list[dict[str, object]] = []
    for quarter_end in source.index:
        for column in component_columns:
            source_value = source.at[quarter_end, column]
            allocated_value = allocated.at[quarter_end, column]
            residual = source_value - allocated_value
            residual_share = (
                residual / source_value
                if pd.notna(source_value) and source_value != 0
                else pd.NA
            )
            records.append(
                {
                    "quarter_end": quarter_end,
                    "component": column,
                    "la_tract_total": source_value,
                    "modeled_zip_allocated_total": allocated_value,
                    "unallocated_residual": residual,
                    "unallocated_residual_share": residual_share,
                }
            )
    coverage = pd.DataFrame(records)
    excessive = coverage["unallocated_residual_share"].abs().gt(
        ZIP_ALLOCATION_MAX_RESIDUAL_SHARE
    )
    if excessive.fillna(False).any():
        examples = coverage.loc[
            excessive,
            ["quarter_end", "component", "unallocated_residual_share"],
        ].head().to_dict("records")
        raise ValueError(
            "HUD-USPS modeled ZIP allocation residual exceeds the documented "
            f"tolerance: {examples}"
        )
    return coverage


def build_hud_usps_wide_tables(
    archive_files: list[tuple[Path, dict[str, str]]],
    expected_start: str | None = None,
    expected_end: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Rebuild county, LA tract, and modeled LA ZIP-part tables from raw JSON."""

    if not archive_files:
        raise ValueError("No HUD-USPS raw archives were supplied")
    by_role: dict[str, list[tuple[Path, dict[str, str]]]] = {}
    for path, record in archive_files:
        by_role.setdefault(_manifest_role(record), []).append((path, record))
    required_roles = {
        "ncwm_standardized_2020",
        "tract_zip_crosswalk",
        "county_fips_reference_2020",
    }
    if set(by_role) != required_roles:
        raise ValueError(
            f"HUD-USPS raw archive roles must be {sorted(required_roles)}; "
            f"found {sorted(by_role)}"
        )
    if len(by_role["tract_zip_crosswalk"]) != 1:
        raise ValueError("Exactly one HUD-USPS crosswalk vintage is required")
    if len(by_role["county_fips_reference_2020"]) != 1:
        raise ValueError("Exactly one Census county reference is required")

    county_reference = _county_reference(
        by_role["county_fips_reference_2020"][0][0]
    )
    crosswalk_record = by_role["tract_zip_crosswalk"][0][1]
    crosswalk, crosswalk_vintage = _crosswalk_frame(
        by_role["tract_zip_crosswalk"][0][0]
    )
    manifest_crosswalk_vintage = str(
        crosswalk_record.get("provider_vintage", "")
    ).strip()
    if manifest_crosswalk_vintage and (
        manifest_crosswalk_vintage != crosswalk_vintage
    ):
        raise ValueError(
            "HUD-USPS crosswalk payload vintage does not match its manifest"
        )
    ncwm_files = sorted(
        by_role["ncwm_standardized_2020"],
        key=lambda item: str(item[1].get("archive_period", "")),
    )
    periods = [str(record.get("archive_period", "")) for _, record in ncwm_files]
    if len(periods) != len(set(periods)):
        raise ValueError("HUD-USPS archive contains duplicate quarter vintages")
    parsed_periods = pd.PeriodIndex(
        [period.replace("-", "") for period in periods], freq="Q"
    )
    if (expected_start is None) != (expected_end is None):
        raise ValueError(
            "HUD-USPS expected archive endpoints must be supplied together"
        )
    range_start = (
        pd.Period(expected_start.replace("-", ""), freq="Q")
        if expected_start is not None
        else parsed_periods.min()
    )
    range_end = (
        pd.Period(expected_end.replace("-", ""), freq="Q")
        if expected_end is not None
        else parsed_periods.max()
    )
    expected_periods = pd.period_range(range_start, range_end, freq="Q")
    if list(parsed_periods) != list(expected_periods):
        raise ValueError(
            "HUD-USPS quarterly archive does not match the required endpoints"
        )

    county_parts: list[pd.DataFrame] = []
    tract_parts: list[pd.DataFrame] = []
    zip_parts: list[pd.DataFrame] = []
    quarter_records: dict[pd.Timestamp, dict[str, str]] = {}
    expected_la_tracts: set[str] | None = None
    for path, record in ncwm_files:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"HUD-USPS NCWM archive is not an object: {path}")
        quarter_end = _archive_period_end(record, payload)
        quarter_records[quarter_end] = record
        all_tracts = _normalized_ncwm_frame(
            payload, quarter_end, county_reference
        )
        county_parts.append(_county_table(all_tracts, county_reference))
        la_tracts = all_tracts.loc[
            all_tracts["county_fips"].eq(LA_COUNTY_FIPS)
        ].copy()
        current_la_tracts = set(la_tracts["tract_geoid"])
        if expected_la_tracts is None:
            expected_la_tracts = current_la_tracts
            if len(expected_la_tracts) != 2_494:
                raise ValueError(
                    "HUD-USPS standardized LA geography must contain 2,494 "
                    f"2020 tracts; found {len(expected_la_tracts)}"
                )
        elif current_la_tracts != expected_la_tracts:
            raise ValueError(
                f"HUD-USPS LA standardized tract spine changed at {quarter_end.date()}"
            )
        la_tracts["is_modeled_geography"] = False
        tract_parts.append(la_tracts)
        zip_parts.append(_zip_table(la_tracts, crosswalk, crosswalk_vintage))

    county = pd.concat(county_parts, ignore_index=True, sort=False)
    tract = pd.concat(tract_parts, ignore_index=True, sort=False)
    postal_zip = pd.concat(zip_parts, ignore_index=True, sort=False)
    zip_allocation_coverage = _zip_allocation_coverage(tract, postal_zip)
    for frame in (county, tract, postal_zip):
        source_records = frame["quarter_end"].map(quarter_records)
        frame["source_vintage"] = source_records.map(
            lambda record: record.get("provider_vintage")
        )
        frame["source_filename"] = source_records.map(
            lambda record: record.get("source_filename")
        )
        frame["source_checksum_sha256"] = source_records.map(
            lambda record: record.get("checksum_sha256")
        )
        frame["source_retrieved_at"] = source_records.map(
            lambda record: record.get("retrieved_at")
        )
    postal_zip["crosswalk_source_filename"] = crosswalk_record.get(
        "source_filename"
    )
    postal_zip["crosswalk_checksum_sha256"] = crosswalk_record.get(
        "checksum_sha256"
    )
    postal_zip["crosswalk_retrieved_at"] = crosswalk_record.get(
        "retrieved_at"
    )
    if county.duplicated(["county_fips", "quarter_end"]).any():
        raise ValueError("HUD-USPS rebuilt county table has duplicate keys")
    if tract.duplicated(["tract_geoid", "quarter_end"]).any():
        raise ValueError("HUD-USPS rebuilt tract table has duplicate keys")
    if postal_zip.duplicated(["zip_code", "quarter_end"]).any():
        raise ValueError("HUD-USPS rebuilt ZIP table has duplicate keys")
    metadata: dict[str, object] = {
        "quarter_records": quarter_records,
        "crosswalk_record": crosswalk_record,
        "county_reference_record": by_role["county_fips_reference_2020"][0][1],
        "crosswalk_vintage": crosswalk_vintage,
        "zip_allocation_coverage": zip_allocation_coverage,
        "crosswalk_zero_ratio_tract_count": int(
            crosswalk.groupby("tract_geoid")["res_ratio"].sum().eq(0).sum()
        ),
    }
    return county, tract, postal_zip, metadata


def _active_observation_contract(
    normalized: pd.DataFrame,
    metadata: dict[str, object],
    source_id: str,
) -> pd.DataFrame:
    quarter_records = metadata["quarter_records"]
    if not isinstance(quarter_records, dict):
        raise TypeError("HUD-USPS quarter provenance mapping is invalid")
    frame = normalized.copy()
    frame["observation_date"] = pd.to_datetime(frame["observation_date"])
    records = frame["observation_date"].map(quarter_records)
    if records.isna().any():
        raise ValueError("HUD-USPS observations lack quarter-level raw provenance")
    frame["source_vintage"] = records.map(
        lambda record: record.get("provider_vintage")
        or record.get("data_vintage")
    )
    frame["retrieved_at"] = records.map(lambda record: record.get("retrieved_at"))
    frame["source_filename"] = records.map(
        lambda record: record.get("source_filename")
    )
    frame["checksum_sha256"] = records.map(
        lambda record: record.get("checksum_sha256")
    )
    crosswalk_record = metadata["crosswalk_record"]
    if not isinstance(crosswalk_record, dict):
        raise TypeError("HUD-USPS crosswalk provenance mapping is invalid")
    modeled_zip = frame["geo_type"].eq("postal_zip_county_part")
    frame["crosswalk_vintage"] = frame.get(
        "crosswalk_vintage", pd.Series(index=frame.index, dtype="string")
    ).where(modeled_zip)
    frame["crosswalk_source_filename"] = crosswalk_record.get(
        "source_filename"
    )
    frame["crosswalk_source_filename"] = frame[
        "crosswalk_source_filename"
    ].where(modeled_zip)
    frame["crosswalk_checksum_sha256"] = crosswalk_record.get(
        "checksum_sha256"
    )
    frame["crosswalk_checksum_sha256"] = frame[
        "crosswalk_checksum_sha256"
    ].where(modeled_zip)
    frame["crosswalk_retrieved_at"] = crosswalk_record.get("retrieved_at")
    frame["crosswalk_retrieved_at"] = frame[
        "crosswalk_retrieved_at"
    ].where(modeled_zip)
    # HUD's endpoint does not publish a release or last-updated date. The
    # quarter end is an observation date and must not be relabeled as source
    # metadata.
    frame["provider_last_updated"] = None
    frame["provider"] = "HUD-USPS"
    frame["source_id"] = source_id
    frame["reported_value"] = frame["value"].where(
        frame["geo_type"].eq("tract_2020")
        & frame["variable_id"].isin(
            {
                "usps_total_residential_addresses",
                "usps_short_term_vacant_residential_addresses",
                "usps_long_term_vacant_residential_addresses",
                "usps_no_stat_residential_addresses",
                "usps_active_residential_addresses",
            }
        )
    )
    frame["is_imputed"] = False
    frame["is_interpolated"] = False
    frame["is_seasonally_adjusted"] = False
    frame["transformation_id"] = "hud_usps_official_raw_to_quarterly_v2"
    crosswalk_vintage = str(metadata["crosswalk_vintage"])
    frame["quality_flag"] = frame["geo_type"].map(
        {
            "county": "official_raw_rebuilt",
            "tract_2020": "official_raw_rebuilt_restricted",
            "postal_zip_county_part": (
                "modeled_fixed_tract_zip_crosswalk_"
                f"{crosswalk_vintage}_restricted"
            ),
        }
    )
    frame["provenance_status"] = ACTIVE_PROVENANCE_STATUS
    for column in OBSERVATION_COLUMNS:
        if column not in frame:
            frame[column] = None
    return frame


def clean_hud_usps_archive(
    archive_files: list[tuple[Path, dict[str, str]]],
    source_id: str,
    output_path: Path,
    warnings_output_path: Path,
    restricted_output_dir: Path,
    expected_start: str = "2008-Q1",
    expected_end: str = "2026-Q1",
) -> pd.DataFrame:
    """Rebuild, validate, and persist active HUD-USPS long observations."""

    county, tract, postal_zip, metadata = build_hud_usps_wide_tables(
        archive_files,
        expected_start=expected_start,
        expected_end=expected_end,
    )
    result = normalize_hud_usps_quarterly_tables(county, tract, postal_zip)
    active = _active_observation_contract(
        result.observations, metadata, source_id
    )
    restricted_output_dir.mkdir(parents=True, exist_ok=True)
    county.to_parquet(
        restricted_output_dir / "ca_county_quarterly.parquet", index=False
    )
    tract.to_parquet(
        restricted_output_dir / "la_tract_2020_quarterly.parquet", index=False
    )
    postal_zip.to_parquet(
        restricted_output_dir / "la_postal_zip_county_part_quarterly.parquet",
        index=False,
    )
    warnings_output_path.parent.mkdir(parents=True, exist_ok=True)
    result.warnings.to_csv(warnings_output_path, index=False)
    allocation_coverage = metadata["zip_allocation_coverage"]
    if not isinstance(allocation_coverage, pd.DataFrame):
        raise TypeError("HUD-USPS ZIP allocation diagnostics are invalid")
    allocation_coverage.to_csv(
        warnings_output_path.with_name("hud_usps_zip_allocation_coverage.csv"),
        index=False,
    )
    active.to_parquet(
        restricted_output_dir / "long_observations_with_lineage.parquet",
        index=False,
    )
    return finalize(active, output_path)
