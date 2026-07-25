"""ACS PUMS ingestion and geography support for Version A benchmarks.

This module keeps the ACS PUMS principal-and-interest parameter at its native
source period.  It does not interpolate the parameter, repeat it to monthly
rows, or silently substitute the California result for a county result.

County parameters use a documented hierarchy.  A county estimate is used only
when every PUMA touching that county is wholly contained.  Otherwise the
records are pooled over the complete connected county/PUMA region.  A
California fallback is used only when that preferred pool is unavailable or
does not meet the configured sample threshold.
"""

from __future__ import annotations

import math
import re
import zipfile
from hashlib import sha256
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.benchmark.finance import (
    adjust_pums_housing_dollars,
    calculate_pums_first_mortgage_pi,
    calculate_pums_junior_lien_payment,
    calculate_pums_pi_share,
)


PUMS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "WGTP",
    "TEN",
    "MRGX",
    "MRGP",
    "MRGT",
    "MRGI",
    "SMP",
    "TAXAMT",
    "INSP",
    "SMOCP",
    "ADJHSG",
    "PUMA",
)

PUMS_REPLICATE_WEIGHT_COLUMNS: tuple[str, ...] = tuple(
    f"WGTP{replicate}" for replicate in range(1, 81)
)

RELATIONSHIP_REQUIRED_COLUMNS: tuple[str, ...] = (
    "STATEFP",
    "COUNTYFP",
    "TRACTCE",
    "PUMA5CE",
)

_RELATIONSHIP_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "STATEFP": ("STATEFP", "STATEFP10", "STATEFP20", "STATE"),
    "COUNTYFP": ("COUNTYFP", "COUNTYFP10", "COUNTYFP20", "COUNTY"),
    "TRACTCE": ("TRACTCE", "TRACTCE10", "TRACTCE20", "TRACT"),
    "PUMA5CE": (
        "PUMA5CE",
        "PUMA5CE10",
        "PUMA5CE20",
        "PUMACE",
        "PUMACE10",
        "PUMACE20",
        "PUMA",
    ),
}

SUPPORTED_PUMS_5_YEAR_VINTAGES: tuple[int, ...] = tuple(range(2016, 2025))
PUMS_DUAL_GEOGRAPHY_VINTAGES: tuple[int, ...] = (2012, 2013, 2014, 2015, 2022)


class PUMSFormatError(ValueError):
    """Raised when a PUMS or tract-to-PUMA file violates its schema."""


def resolve_pums_geography_contract(
    acs_vintage: int,
    *,
    source_frequency: str = "5-year",
    columns: Iterable[Any] | None = None,
    relationship_vintage: int | None = None,
) -> dict[str, Any]:
    """Resolve the only supported PUMA/crosswalk contract for a release.

    The official 2012--2015 five-year releases mix 2000- and 2010-defined
    PUMAs.  The original 2022 five-year release likewise used separate
    ``PUMA10`` and ``PUMA20`` fields.  Those dual-vintage layouts cannot be
    assigned one tract-to-PUMA relationship and are rejected.

    Census subsequently published a single-2020-PUMA update for the 2022
    release.  It is accepted only when the inspected file exposes canonical
    ``PUMA`` and no dual-vintage PUMA fields.  The 2023 and later files are
    also published on a single, Census-crosswalked 2020 PUMA definition.
    Vintages after the latest verified release are rejected until their
    geography contract is reviewed.

    This helper is pure so acquisition/orchestration code can apply the same
    check before choosing a relationship file.
    """
    if not isinstance(acs_vintage, int) or isinstance(acs_vintage, bool):
        raise ValueError("acs_vintage must be an integer year")
    frequency = re.sub(
        r"[^a-z0-9]+", "", str(source_frequency).strip().casefold()
    )
    if frequency not in {"5year", "fiveyear", "acs5year", "acs5yr"}:
        raise ValueError(
            "PUMS county parameters currently support ACS 5-year files only"
        )
    if acs_vintage < 2009:
        raise PUMSFormatError(
            f"{acs_vintage} predates the ACS 5-year PUMS release series"
        )
    if acs_vintage > max(SUPPORTED_PUMS_5_YEAR_VINTAGES):
        raise PUMSFormatError(
            f"{acs_vintage} ACS 5-year PUMS geography is not yet verified; "
            f"latest supported vintage is {max(SUPPORTED_PUMS_5_YEAR_VINTAGES)}"
        )
    if acs_vintage <= 2011:
        raise PUMSFormatError(
            f"{acs_vintage} ACS 5-year PUMS uses unsupported 2000-defined PUMAs"
        )
    if 2012 <= acs_vintage <= 2015:
        raise PUMSFormatError(
            f"{acs_vintage} ACS 5-year PUMS spans mixed 2000/2010 PUMA "
            "definitions and cannot use one relationship file"
        )

    normalized_columns = (
        {_normalised_column_name(column) for column in columns}
        if columns is not None
        else None
    )
    dual_columns = (
        normalized_columns.intersection({"PUMA00", "PUMA10", "PUMA20"})
        if normalized_columns is not None
        else set()
    )
    if dual_columns:
        raise PUMSFormatError(
            "dual-vintage PUMS geography columns cannot be assigned one "
            f"relationship file: {sorted(dual_columns)}"
        )
    if normalized_columns is not None and "PUMA" not in normalized_columns:
        raise PUMSFormatError(
            "single-vintage county pooling requires the canonical PUMA column"
        )

    if acs_vintage <= 2021:
        expected_relationship = 2010
        geography_status = "single_2010"
        census_crosswalked = False
    else:
        expected_relationship = 2020
        census_crosswalked = acs_vintage >= 2023
        geography_status = (
            "census_crosswalked_single_2020"
            if census_crosswalked
            else "census_2022_puma_update_single_2020"
        )
        if acs_vintage == 2022 and normalized_columns is None:
            raise PUMSFormatError(
                "2022 ACS 5-year PUMS requires file-column inspection to "
                "distinguish the official single-2020-PUMA update from the "
                "original dual-vintage release"
            )
        if acs_vintage == 2022:
            census_crosswalked = True

    if (
        relationship_vintage is not None
        and relationship_vintage != expected_relationship
    ):
        raise PUMSFormatError(
            f"{acs_vintage} ACS 5-year PUMS requires the "
            f"{expected_relationship} tract-to-PUMA relationship, not "
            f"{relationship_vintage}"
        )
    return {
        "acs_vintage": acs_vintage,
        "source_frequency": "ACS 5-year",
        "relationship_vintage": expected_relationship,
        "puma_definition_vintage": expected_relationship,
        "geography_status": geography_status,
        "is_census_crosswalked": census_crosswalked,
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalised_column_name(value: Any) -> str:
    return str(value).lstrip("\ufeff").strip().upper()


def _column_lookup(columns: Iterable[Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for original in columns:
        normalised = _normalised_column_name(original)
        if normalised in lookup:
            raise PUMSFormatError(
                "duplicate columns after case/whitespace normalization: "
                f"{normalised}"
            )
        lookup[normalised] = original
    return lookup


def _required_pums_source_columns(columns: Iterable[Any]) -> dict[str, Any]:
    lookup = _column_lookup(columns)
    missing = [column for column in PUMS_REQUIRED_COLUMNS if column not in lookup]
    if missing:
        raise PUMSFormatError(f"missing required PUMS columns: {missing}")
    replicate_columns = [
        column for column in PUMS_REPLICATE_WEIGHT_COLUMNS if column in lookup
    ]
    if replicate_columns and len(replicate_columns) != len(
        PUMS_REPLICATE_WEIGHT_COLUMNS
    ):
        missing_replicates = [
            column
            for column in PUMS_REPLICATE_WEIGHT_COLUMNS
            if column not in lookup
        ]
        raise PUMSFormatError(
            "partial PUMS replicate-weight set; expected all WGTP1-WGTP80; "
            f"missing={missing_replicates}"
        )
    selected = {column: lookup[column] for column in PUMS_REQUIRED_COLUMNS}
    selected.update(
        {
            column: lookup[column]
            for column in PUMS_REPLICATE_WEIGHT_COLUMNS
            if column in lookup
        }
    )
    return selected


def _validate_and_normalise_replicate_weights(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Validate a complete official WGTP1--WGTP80 set when it is present."""
    present = [
        column
        for column in PUMS_REPLICATE_WEIGHT_COLUMNS
        if column in frame.columns
    ]
    if not present:
        return frame
    if len(present) != len(PUMS_REPLICATE_WEIGHT_COLUMNS):
        missing = [
            column
            for column in PUMS_REPLICATE_WEIGHT_COLUMNS
            if column not in frame.columns
        ]
        raise PUMSFormatError(
            "partial PUMS replicate-weight set; expected all WGTP1-WGTP80; "
            f"missing={missing}"
        )

    replicate_frame = frame.loc[:, list(PUMS_REPLICATE_WEIGHT_COLUMNS)]
    if all(
        pd.api.types.is_integer_dtype(replicate_frame[column])
        for column in PUMS_REPLICATE_WEIGHT_COLUMNS
    ) and not replicate_frame.isna().any().any():
        return frame

    validated: dict[str, pd.Series] = {}
    for column in PUMS_REPLICATE_WEIGHT_COLUMNS:
        numeric = _numeric_series_strict(replicate_frame[column], column)
        if numeric.isna().any():
            raise PUMSFormatError(
                f"{column} contains missing replicate weights"
            )
        if bool(numeric.mod(1).ne(0).any()):
            raise PUMSFormatError(
                f"{column} must contain integer replicate weights"
            )
        # ACS replicate weights may be zero or negative. They are kept as
        # signed integers and are used only for variance estimation.
        validated[column] = numeric.astype("int64")
    result = pd.concat(
        (
            frame.drop(columns=list(PUMS_REPLICATE_WEIGHT_COLUMNS)),
            pd.DataFrame(validated, index=frame.index),
        ),
        axis=1,
    ).loc[:, frame.columns]
    result.attrs = frame.attrs.copy()
    return result


def _read_selected_pums_csv(
    source: Path | Any,
    *,
    source_name: str,
) -> pd.DataFrame:
    """Read required fields plus an all-or-none set of 80 replicate weights."""
    header = pd.read_csv(
        source,
        nrows=0,
        encoding="utf-8-sig",
    )
    selected = _required_pums_source_columns(header.columns)

    if hasattr(source, "seek"):
        source.seek(0)
    dtype = {
        original: (
            "int64"
            if canonical in PUMS_REPLICATE_WEIGHT_COLUMNS
            else "string"
        )
        for canonical, original in selected.items()
    }
    try:
        frame = pd.read_csv(
            source,
            usecols=list(selected.values()),
            dtype=dtype,
            encoding="utf-8-sig",
            low_memory=False,
        )
    except (TypeError, ValueError) as exc:
        raise PUMSFormatError(
            f"{source_name}: replicate weights must be populated integers"
        ) from exc
    frame = frame.rename(
        columns={original: name for name, original in selected.items()}
    )
    frame = frame.loc[:, list(selected)]
    frame = _validate_and_normalise_replicate_weights(frame)
    frame["PUMA"] = _normalise_code_series(
        frame["PUMA"], width=5, name=f"{source_name}: PUMA"
    )
    frame.attrs["source_filename"] = source_name
    # Retain the complete inspected header even though only required columns
    # are loaded. The 2022 geography guard must still see PUMA10/PUMA20 if an
    # original dual-vintage release exposes them.
    frame.attrs["source_columns"] = tuple(str(column) for column in header.columns)
    return frame


def read_pums_housing(path: str | Path) -> pd.DataFrame:
    """Read an official California ACS PUMS housing CSV or single-CSV ZIP.

    Core methodology fields remain source strings so missing flags and source
    values are not altered before the finance calculation performs its strict
    conversions.  When the official header contains replicate weights, all 80
    ``WGTP1``--``WGTP80`` columns are loaded as validated signed integers.  A
    partial replicate set is rejected.  A ZIP is accepted only when it
    contains exactly one CSV member.
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    if source_path.suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(source_path) as archive:
                members = [
                    name
                    for name in archive.namelist()
                    if not name.endswith("/")
                    and Path(name).suffix.casefold() == ".csv"
                ]
                if len(members) != 1:
                    raise PUMSFormatError(
                        f"{source_path.name}: expected exactly one CSV member; "
                        f"found {len(members)}"
                    )
                member = members[0]
                with archive.open(member) as handle:
                    frame = _read_selected_pums_csv(
                        handle,
                        source_name=member,
                    )
        except zipfile.BadZipFile as exc:
            raise PUMSFormatError(f"{source_path.name}: invalid ZIP archive") from exc
        frame.attrs["source_member_filename"] = member
        frame.attrs["source_filename"] = source_path.name
        frame.attrs["checksum_sha256"] = _sha256(source_path)
        return frame

    if source_path.suffix.casefold() != ".csv":
        raise PUMSFormatError(
            f"{source_path.name}: expected a .csv file or single-CSV .zip archive"
        )
    frame = _read_selected_pums_csv(
        source_path,
        source_name=source_path.name,
    )
    frame.attrs["checksum_sha256"] = _sha256(source_path)
    return frame


def _reported_mask(series: pd.Series) -> pd.Series:
    blank = series.map(lambda value: isinstance(value, str) and not value.strip())
    return series.notna() & ~blank


def _numeric_series_strict(series: pd.Series, name: str) -> pd.Series:
    reported = _reported_mask(series)
    numeric = pd.to_numeric(series.where(reported), errors="coerce").astype(float)
    invalid = reported & numeric.isna()
    if invalid.any():
        examples = series.loc[invalid].astype(str).drop_duplicates().head(3).tolist()
        raise PUMSFormatError(f"{name} contains nonnumeric values: {examples}")
    finite = np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan))
    if bool((numeric.notna().to_numpy() & ~finite).any()):
        raise PUMSFormatError(f"{name} must contain finite values or missing values")
    return numeric


def _normalise_code(value: Any, *, width: int, name: str) -> Any:
    if pd.isna(value) or (isinstance(value, str) and not value.strip()):
        return pd.NA
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", maxsplit=1)[0]
    if not text.isdigit():
        raise PUMSFormatError(f"{name} must contain digits: {value!r}")
    if len(text) > width:
        raise PUMSFormatError(
            f"{name} exceeds its documented {width}-digit width: {value!r}"
        )
    return text.zfill(width)


def _normalise_code_series(
    series: pd.Series,
    *,
    width: int,
    name: str,
    allow_missing: bool = False,
) -> pd.Series:
    result = series.map(
        lambda value: _normalise_code(value, width=width, name=name)
    ).astype("string")
    if not allow_missing and result.isna().any():
        raise PUMSFormatError(f"{name} contains missing identifiers")
    return result


def require_pums_columns(frame: pd.DataFrame) -> None:
    """Raise a structural error unless every methodology field is present."""
    _required_pums_source_columns(frame.columns)


def _standardise_pums_frame(frame: pd.DataFrame) -> pd.DataFrame:
    selected = _required_pums_source_columns(frame.columns)
    result = frame.loc[:, list(selected.values())].rename(
        columns={original: name for name, original in selected.items()}
    )
    result = result.loc[:, list(selected)].copy()
    result = _validate_and_normalise_replicate_weights(result)
    result["PUMA"] = _normalise_code_series(
        result["PUMA"], width=5, name="PUMA"
    )
    return result


def filter_owner_first_mortgage(frame: pd.DataFrame) -> pd.DataFrame:
    """Retain ``TEN=1`` and ``MRGX=1`` without altering missing input flags."""
    source = _standardise_pums_frame(frame)
    tenure = _numeric_series_strict(source["TEN"], "TEN")
    first_mortgage = _numeric_series_strict(source["MRGX"], "MRGX")
    selected = tenure.eq(1) & first_mortgage.eq(1)
    return source.loc[selected].copy()


def _relationship_source_columns(columns: Iterable[Any]) -> dict[str, Any]:
    lookup = _column_lookup(columns)
    selected: dict[str, Any] = {}
    missing: list[str] = []
    for canonical, aliases in _RELATIONSHIP_COLUMN_ALIASES.items():
        matches = [lookup[alias] for alias in aliases if alias in lookup]
        if not matches:
            missing.append(canonical)
            continue
        if len(matches) > 1:
            raise PUMSFormatError(
                f"multiple columns match relationship field {canonical}: {matches}"
            )
        selected[canonical] = matches[0]
    if missing:
        raise PUMSFormatError(
            f"missing tract-to-PUMA relationship columns: {missing}"
        )
    return selected


def infer_relationship_vintage(columns: Iterable[Any]) -> int | None:
    """Infer 2010/2020 relationship vintage from official suffixed headers."""
    normalized = {_normalised_column_name(column) for column in columns}
    has_2010 = bool(
        normalized.intersection(
            {"STATEFP10", "COUNTYFP10", "TRACTCE10", "PUMA5CE10", "PUMACE10"}
        )
    )
    has_2020 = bool(
        normalized.intersection(
            {"STATEFP20", "COUNTYFP20", "TRACTCE20", "PUMA5CE20", "PUMACE20"}
        )
    )
    if has_2010 and has_2020:
        raise PUMSFormatError(
            "relationship header mixes 2010- and 2020-defined fields"
        )
    if has_2010:
        return 2010
    if has_2020:
        return 2020
    return None


def _standardise_relationship_frame(
    frame: pd.DataFrame,
    *,
    relationship_vintage: int | None = None,
    state_fips: str | None = "06",
) -> pd.DataFrame:
    selected = _relationship_source_columns(frame.columns)
    result = frame.loc[:, list(selected.values())].rename(
        columns={original: name for name, original in selected.items()}
    )
    result = result.loc[:, list(RELATIONSHIP_REQUIRED_COLUMNS)].copy()
    result["STATEFP"] = _normalise_code_series(
        result["STATEFP"], width=2, name="STATEFP"
    )
    result["COUNTYFP"] = _normalise_code_series(
        result["COUNTYFP"], width=3, name="COUNTYFP"
    )
    result["TRACTCE"] = _normalise_code_series(
        result["TRACTCE"], width=6, name="TRACTCE"
    )
    result["PUMA5CE"] = _normalise_code_series(
        result["PUMA5CE"], width=5, name="PUMA5CE"
    )

    if state_fips is not None:
        state_code = _normalise_code(
            state_fips, width=2, name="state_fips"
        )
        result = result.loc[result["STATEFP"].eq(state_code)].copy()
        if result.empty:
            raise PUMSFormatError(
                f"tract-to-PUMA relationship has no rows for state {state_code}"
            )

    tract_keys = ["STATEFP", "COUNTYFP", "TRACTCE"]
    pumas_per_tract = result.groupby(
        tract_keys, dropna=False
    )["PUMA5CE"].nunique()
    if bool(pumas_per_tract.gt(1).any()):
        examples = [
            "/".join(map(str, key if isinstance(key, tuple) else (key,)))
            for key in pumas_per_tract.loc[pumas_per_tract.gt(1)].index[:3]
        ]
        raise PUMSFormatError(
            "a census tract maps to multiple PUMAs, contrary to the official "
            f"relationship definition: {examples}"
        )

    result = result.drop_duplicates(
        ["STATEFP", "COUNTYFP", "TRACTCE", "PUMA5CE"]
    ).reset_index(drop=True)
    if relationship_vintage is not None:
        if relationship_vintage not in {2010, 2020}:
            raise ValueError("relationship_vintage must be 2010 or 2020")
        result["relationship_vintage"] = relationship_vintage
    elif "relationship_vintage" in frame.columns:
        vintage_raw = frame["relationship_vintage"]
        vintage_reported = _reported_mask(vintage_raw)
        vintages = pd.to_numeric(
            vintage_raw.where(vintage_reported), errors="coerce"
        )
        if bool((vintage_reported & vintages.isna()).any()):
            raise PUMSFormatError(
                "relationship_vintage contains a nonnumeric reported value"
            )
        reported_vintages = vintages.dropna()
        if bool(reported_vintages.mod(1).ne(0).any()):
            raise PUMSFormatError(
                "relationship_vintage must be an integer year"
            )
        unique_vintages = set(reported_vintages.astype(int))
        if not unique_vintages.issubset({2010, 2020}):
            raise PUMSFormatError(
                "relationship_vintage must contain only 2010 or 2020"
            )
        if len(unique_vintages) > 1:
            raise PUMSFormatError(
                "relationship frame contains multiple relationship vintages"
            )
        result["relationship_vintage"] = (
            next(iter(unique_vintages)) if unique_vintages else pd.NA
        )
    return result


def _detect_relationship_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next((line for line in handle if line.strip()), "")
    if "|" in header:
        return "|"
    if "," in header:
        # The currently published Census 2010 and 2020 national files use
        # commas; pipe-delimited provider extracts are also supported above.
        return ","
    if "\t" in header:
        return "\t"
    raise PUMSFormatError(
        f"{path.name}: cannot detect pipe, comma, or tab delimiter"
    )


def read_tract_puma_relationship(
    path: str | Path,
    *,
    relationship_vintage: int,
    state_fips: str | None = "06",
) -> pd.DataFrame:
    """Read an official 2010/2020 Census tract-to-PUMA relationship TXT.

    The reader supports the documented column names with or without vintage
    suffixes.  Pipe-delimited inputs are supported as requested; comma
    detection keeps compatibility with the national TXT files currently
    published by Census.
    """
    if relationship_vintage not in {2010, 2020}:
        raise ValueError("relationship_vintage must be 2010 or 2020")
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    delimiter = _detect_relationship_delimiter(source_path)
    source = pd.read_csv(
        source_path,
        sep=delimiter,
        dtype="string",
        encoding="utf-8-sig",
    )
    result = _standardise_relationship_frame(
        source,
        relationship_vintage=relationship_vintage,
        state_fips=state_fips,
    )
    result.attrs["source_filename"] = source_path.name
    result.attrs["delimiter"] = delimiter
    result.attrs["checksum_sha256"] = _sha256(source_path)
    return result


def derive_wholly_contained_pumas(
    relationship: pd.DataFrame,
    *,
    state_fips: str | None = "06",
    relationship_vintage: int | None = None,
) -> pd.DataFrame:
    """Return PUMAs appearing in exactly one county in the relationship file."""
    source = _standardise_relationship_frame(
        relationship,
        relationship_vintage=relationship_vintage,
        state_fips=state_fips,
    )
    source["county_fips"] = source["STATEFP"] + source["COUNTYFP"]
    puma_counties = source[
        ["STATEFP", "PUMA5CE", "county_fips"]
    ].drop_duplicates()
    puma_counties["puma_county_count"] = puma_counties.groupby(
        ["STATEFP", "PUMA5CE"]
    )["county_fips"].transform("nunique")

    county_all_counts = (
        puma_counties.groupby("county_fips")["PUMA5CE"]
        .nunique()
        .rename("county_puma_count")
    )
    contained = puma_counties.loc[
        puma_counties["puma_county_count"].eq(1)
    ].copy()
    county_contained_counts = (
        contained.groupby("county_fips")["PUMA5CE"]
        .nunique()
        .rename("county_wholly_contained_puma_count")
    )
    contained = contained.join(county_all_counts, on="county_fips")
    contained = contained.join(county_contained_counts, on="county_fips")
    contained["county_all_pumas_wholly_contained"] = contained[
        "county_wholly_contained_puma_count"
    ].eq(contained["county_puma_count"])
    contained["is_wholly_contained"] = True
    contained = contained.rename(
        columns={"STATEFP": "state_fips", "PUMA5CE": "PUMA"}
    )

    if "relationship_vintage" in source.columns:
        vintages = source["relationship_vintage"].dropna().unique()
        if len(vintages) > 1:
            raise PUMSFormatError(
                "relationship frame contains multiple relationship vintages"
            )
        contained["relationship_vintage"] = (
            vintages[0] if len(vintages) == 1 else pd.NA
        )
    elif relationship_vintage is not None:
        contained["relationship_vintage"] = relationship_vintage
    else:
        contained["relationship_vintage"] = pd.NA

    columns = [
        "state_fips",
        "county_fips",
        "PUMA",
        "relationship_vintage",
        "puma_county_count",
        "county_puma_count",
        "county_wholly_contained_puma_count",
        "county_all_pumas_wholly_contained",
        "is_wholly_contained",
    ]
    return contained.loc[:, columns].sort_values(
        ["county_fips", "PUMA"]
    ).reset_index(drop=True)


def _normalise_date(value: Any, name: str, *, allow_missing: bool = False) -> Any:
    if value is None or pd.isna(value):
        if allow_missing:
            return pd.NaT
        raise ValueError(f"{name} must be populated")
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid scalar date") from exc
    if pd.isna(result):
        if allow_missing:
            return pd.NaT
        raise ValueError(f"{name} must be populated")
    if result.tzinfo is not None:
        result = result.tz_convert("UTC").tz_localize(None)
    return result.normalize()


def _relationship_edges_for_builder(
    relationship: pd.DataFrame,
    *,
    state_fips: str,
    relationship_vintage: int | None,
) -> tuple[pd.DataFrame, dict[str, bool | None]]:
    """Return all observed county/PUMA edges and completeness declarations."""
    if {"county_fips", "PUMA"}.issubset(relationship.columns):
        mapping = relationship.copy()
        mapping["county_fips"] = _normalise_code_series(
            mapping["county_fips"], width=5, name="county_fips"
        )
        mapping["PUMA"] = _normalise_code_series(
            mapping["PUMA"], width=5, name="PUMA"
        )
        mapping = mapping.loc[
            mapping["county_fips"].str[:2].eq(state_fips)
        ].copy()
        if mapping.empty:
            raise PUMSFormatError(
                f"PUMA relationship has no rows for state {state_fips}"
            )
        edges = mapping[["county_fips", "PUMA"]].drop_duplicates()
        declarations: dict[str, bool | None] = {}
        if "county_all_pumas_wholly_contained" in mapping:
            for county, values in mapping.groupby(
                "county_fips", sort=True
            )["county_all_pumas_wholly_contained"]:
                reported = values.dropna()
                declarations[str(county)] = (
                    bool(reported.astype(bool).all())
                    if not reported.empty
                    else None
                )
        else:
            # A bare edge list does not prove that omitted shared PUMAs are
            # absent.  The observed graph can still support a regional pool,
            # but not an auditable wholly-contained county declaration.
            declarations = {
                str(county): None for county in edges["county_fips"].unique()
            }
    else:
        source = _standardise_relationship_frame(
            relationship,
            state_fips=state_fips,
            relationship_vintage=relationship_vintage,
        )
        source["county_fips"] = source["STATEFP"] + source["COUNTYFP"]
        edges = source[["county_fips", "PUMA5CE"]].rename(
            columns={"PUMA5CE": "PUMA"}
        ).drop_duplicates()
        declarations = {}

    edges = edges.sort_values(["county_fips", "PUMA"]).reset_index(drop=True)
    puma_county_count = edges.groupby("PUMA")["county_fips"].nunique()
    computed_complete = (
        edges.assign(
            puma_county_count=edges["PUMA"].map(puma_county_count)
        )
        .groupby("county_fips")["puma_county_count"]
        .max()
        .eq(1)
    )
    complete: dict[str, bool | None] = {}
    for county in edges["county_fips"].drop_duplicates():
        declared = declarations.get(str(county))
        complete[str(county)] = (
            bool(computed_complete.loc[county])
            if declared is None and not {"county_fips", "PUMA"}.issubset(
                relationship.columns
            )
            else declared
        )
    return edges, complete


def derive_county_puma_pool_hierarchy(
    relationship: pd.DataFrame,
    *,
    state_fips: str | None = "06",
    relationship_vintage: int | None = None,
) -> pd.DataFrame:
    """Build auditable county or connected-region PUMA pools.

    A county is its own parameter pool only when the complete official
    relationship shows that every touching PUMA belongs to that county alone.
    Otherwise, its pool is the full connected component in the bipartite
    county/PUMA graph.  This prevents a shared PUMA from being assigned to one
    county while also avoiding unweighted averages of separately calculated
    county ratios.
    """
    state_code = _normalise_code(
        state_fips, width=2, name="state_fips"
    ) if state_fips is not None else None
    if state_code is None:
        raise ValueError("state_fips must be populated for county pooling")
    edges, complete_declarations = _relationship_edges_for_builder(
        relationship,
        state_fips=state_code,
        relationship_vintage=relationship_vintage,
    )
    counties_by_puma = {
        str(puma): set(group["county_fips"].astype(str))
        for puma, group in edges.groupby("PUMA", sort=False)
    }
    pumas_by_county = {
        str(county): set(group["PUMA"].astype(str))
        for county, group in edges.groupby("county_fips", sort=False)
    }

    rows: list[dict[str, Any]] = []
    for target_county in sorted(pumas_by_county):
        target_pumas = pumas_by_county[target_county]
        computed_complete = all(
            len(counties_by_puma[puma]) == 1 for puma in target_pumas
        )
        declared_complete = complete_declarations.get(target_county)
        county_complete = (
            computed_complete
            if declared_complete is None
            and not {"county_fips", "PUMA"}.issubset(relationship.columns)
            else bool(declared_complete)
            if declared_complete is not None
            else False
        )

        if county_complete:
            member_counties = {target_county}
            member_pumas = set(target_pumas)
            tier = "county"
        else:
            member_counties = {target_county}
            member_pumas: set[str] = set()
            pending = [target_county]
            while pending:
                county = pending.pop()
                newly_seen_pumas = pumas_by_county[county] - member_pumas
                member_pumas.update(newly_seen_pumas)
                for puma in newly_seen_pumas:
                    for connected_county in counties_by_puma[puma]:
                        if connected_county not in member_counties:
                            member_counties.add(connected_county)
                            pending.append(connected_county)
            tier = "regional"

        county_members = tuple(sorted(member_counties))
        puma_members = tuple(sorted(member_pumas))
        pool_id = (
            f"county:{target_county}"
            if tier == "county"
            else "puma_region:" + "+".join(county_members)
        )
        rows.append(
            {
                "county_fips": target_county,
                "preferred_geography_tier": tier,
                "preferred_parameter_geography": pool_id,
                "county_mapping_complete": county_complete,
                "county_puma_count": len(target_pumas),
                "county_shared_puma_count": sum(
                    len(counties_by_puma[puma]) > 1
                    for puma in target_pumas
                ),
                "pool_member_county_count": len(county_members),
                "pool_member_puma_count": len(puma_members),
                "pool_member_counties": "|".join(county_members),
                "pool_member_pumas": "|".join(puma_members),
            }
        )
    return pd.DataFrame(rows).sort_values("county_fips").reset_index(drop=True)


def _flag_diagnostics(frame: pd.DataFrame, column: str) -> dict[str, int]:
    reported = _reported_mask(frame[column])
    normalised = frame[column].astype("string").str.strip().str.casefold()
    known = normalised.isin({"1", "2", "y", "yes", "n", "no"})
    return {
        f"{column.lower()}_missing_n": int((~reported).sum()),
        f"{column.lower()}_unknown_n": int((reported & ~known).sum()),
    }


def _replicate_weight_matrix(frame: pd.DataFrame) -> np.ndarray | None:
    present = [
        column
        for column in PUMS_REPLICATE_WEIGHT_COLUMNS
        if column in frame.columns
    ]
    if not present:
        return None
    if len(present) != len(PUMS_REPLICATE_WEIGHT_COLUMNS):
        missing = [
            column
            for column in PUMS_REPLICATE_WEIGHT_COLUMNS
            if column not in frame.columns
        ]
        raise PUMSFormatError(
            "partial PUMS replicate-weight set; expected all WGTP1-WGTP80; "
            f"missing={missing}"
        )
    matrix = frame.loc[
        :, list(PUMS_REPLICATE_WEIGHT_COLUMNS)
    ].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise PUMSFormatError(
            "WGTP1-WGTP80 must contain populated finite replicate weights"
        )
    if not np.equal(matrix, np.trunc(matrix)).all():
        raise PUMSFormatError(
            "WGTP1-WGTP80 must contain integer replicate weights"
        )
    return matrix


def _unavailable_sdr_uncertainty(
    status: str,
    *,
    replicate_weight_count: int,
) -> dict[str, Any]:
    return {
        "standard_error": math.nan,
        "margin_of_error": math.nan,
        "low_estimate": math.nan,
        "high_estimate": math.nan,
        "confidence_level": 0.90,
        "uncertainty_available": False,
        "uncertainty_status": status,
        "uncertainty_method": (
            "acs_successive_difference_replication"
            if replicate_weight_count
            else None
        ),
        "replicate_weight_count": replicate_weight_count,
    }


def _acs_sdr_90_uncertainty(
    full_estimate: Any,
    replicate_estimates: np.ndarray | None,
) -> dict[str, Any]:
    """Return the ACS SDR standard error and two-sided 90% bounds.

    The Census PUMS method calculates 80 estimates with ``WGTP1`` through
    ``WGTP80``, then uses
    ``variance = (4 / 80) * sum((replicate - full_estimate) ** 2)`` and
    ``MOE90 = 1.645 * sqrt(variance)``.  See the official 2020--2024 ACS
    5-year PUMS User Guide:
    https://www2.census.gov/programs-surveys/acs/tech_docs/pums/2020_2024ACS_PUMS_User_Guide.pdf
    """
    if replicate_estimates is None:
        return _unavailable_sdr_uncertainty(
            "unavailable_no_replicate_weights",
            replicate_weight_count=0,
        )
    if replicate_estimates.shape != (80,):
        raise PUMSFormatError(
            "ACS SDR uncertainty requires exactly 80 replicate estimates"
        )
    if not _finite_metric(full_estimate):
        return _unavailable_sdr_uncertainty(
            "unavailable_no_finite_point_estimate",
            replicate_weight_count=80,
        )
    if not np.isfinite(replicate_estimates).all():
        raise PUMSFormatError(
            "ACS SDR produced a nonfinite replicate estimate"
        )

    estimate = float(full_estimate)
    differences = replicate_estimates - estimate
    variance = (4.0 / 80.0) * float(np.dot(differences, differences))
    standard_error = math.sqrt(variance)
    margin_of_error = 1.645 * standard_error
    return {
        "standard_error": standard_error,
        "margin_of_error": margin_of_error,
        "low_estimate": estimate - margin_of_error,
        "high_estimate": estimate + margin_of_error,
        "confidence_level": 0.90,
        "uncertainty_available": True,
        "uncertainty_status": "available",
        "uncertainty_method": "acs_successive_difference_replication",
        "replicate_weight_count": 80,
    }


def _replicate_ratio_estimates(
    *,
    replicate_weights: np.ndarray | None,
    valid: pd.Series,
    numerator_values: pd.Series,
    denominator_values: pd.Series,
    metric_name: str,
) -> np.ndarray | None:
    if replicate_weights is None:
        return None
    valid_array = valid.to_numpy(dtype=bool)
    if not valid_array.any():
        return np.full(80, math.nan, dtype=float)
    numerator_data = np.where(
        valid_array,
        numerator_values.to_numpy(dtype=float),
        0.0,
    )
    denominator_data = np.where(
        valid_array,
        denominator_values.to_numpy(dtype=float),
        0.0,
    )
    numerator = numerator_data @ replicate_weights
    denominator = denominator_data @ replicate_weights
    invalid_denominator = ~np.isfinite(denominator) | (denominator == 0)
    if invalid_denominator.any():
        replicates = (
            np.flatnonzero(invalid_denominator).astype(int) + 1
        ).tolist()
        raise PUMSFormatError(
            f"{metric_name} has zero or nonfinite denominators for replicate "
            f"weights {replicates}"
        )
    estimates = numerator / denominator
    if not np.isfinite(estimates).all():
        raise PUMSFormatError(
            f"{metric_name} produced nonfinite replicate estimates"
        )
    return estimates


def _pi_sdr_uncertainty(
    frame: pd.DataFrame,
    *,
    full_estimate: Any,
    replicate_weights: np.ndarray | None,
) -> dict[str, Any]:
    if replicate_weights is None:
        return _acs_sdr_90_uncertainty(full_estimate, None)
    components = calculate_pums_first_mortgage_pi(frame)
    components["smoc_adj"] = adjust_pums_housing_dollars(
        components["SMOCP"], components["ADJHSG"]
    )
    pi = _numeric_series_strict(components["pi_first"], "pi_first")
    smoc = _numeric_series_strict(components["smoc_adj"], "smoc_adj")
    full_weights = _numeric_series_strict(components["WGTP"], "WGTP")
    valid = (
        full_weights.notna()
        & full_weights.gt(0)
        & pi.notna()
        & smoc.notna()
        & smoc.gt(0)
    )
    replicate_estimates = _replicate_ratio_estimates(
        replicate_weights=replicate_weights,
        valid=valid,
        numerator_values=pi,
        denominator_values=smoc,
        metric_name="PUMS P&I-share ratio",
    )
    return _acs_sdr_90_uncertainty(full_estimate, replicate_estimates)


def _junior_sdr_uncertainty(
    frame: pd.DataFrame,
    *,
    full_estimate: Any,
    replicate_weights: np.ndarray | None,
) -> dict[str, Any]:
    if replicate_weights is None:
        return _acs_sdr_90_uncertainty(full_estimate, None)
    payment_reported = _reported_mask(frame["SMP"])
    payment_raw = _numeric_series_strict(frame["SMP"], "SMP")
    adjustment = _numeric_series_strict(frame["ADJHSG"], "ADJHSG")
    payment = pd.Series(math.nan, index=frame.index, dtype=float)
    payment.loc[~payment_reported] = 0.0
    adjustable = (
        payment_reported & payment_raw.notna() & adjustment.notna()
    )
    payment.loc[adjustable] = (
        payment_raw.loc[adjustable]
        * adjustment.loc[adjustable]
        / 1_000_000.0
    )
    full_weights = _numeric_series_strict(frame["WGTP"], "WGTP")
    valid = full_weights.notna() & full_weights.gt(0) & payment.notna()
    replicate_estimates = _replicate_ratio_estimates(
        replicate_weights=replicate_weights,
        valid=valid,
        numerator_values=payment,
        denominator_values=pd.Series(1.0, index=frame.index),
        metric_name="PUMS junior-lien weighted mean",
    )
    return _acs_sdr_90_uncertainty(full_estimate, replicate_estimates)


def _pi_group_diagnostics(
    frame: pd.DataFrame,
    *,
    replicate_weights: np.ndarray | None = None,
) -> dict[str, Any]:
    metrics = calculate_pums_pi_share(frame)
    diagnostics: dict[str, Any] = dict(metrics)
    diagnostics.update(
        _pi_sdr_uncertainty(
            frame,
            full_estimate=metrics["pi_share"],
            replicate_weights=replicate_weights,
        )
    )
    diagnostics.update(_flag_diagnostics(frame, "MRGT"))
    diagnostics.update(_flag_diagnostics(frame, "MRGI"))
    for column in ("WGTP", "MRGP", "TAXAMT", "INSP", "SMOCP", "ADJHSG"):
        diagnostics[f"{column.lower()}_missing_n"] = int(
            (~_reported_mask(frame[column])).sum()
        )
    weights = _numeric_series_strict(frame["WGTP"], "WGTP")
    diagnostics["eligible_weight"] = float(weights.dropna().sum())
    diagnostics["puma_count"] = int(frame["PUMA"].nunique())
    return diagnostics


def _junior_group_diagnostics(
    frame: pd.DataFrame,
    *,
    replicate_weights: np.ndarray | None = None,
) -> dict[str, Any]:
    metrics = calculate_pums_junior_lien_payment(frame)
    diagnostics: dict[str, Any] = dict(metrics)
    diagnostics.update(
        _junior_sdr_uncertainty(
            frame,
            full_estimate=metrics["junior_lien_monthly_payment"],
            replicate_weights=replicate_weights,
        )
    )
    diagnostics["smp_structural_not_applicable_n"] = int(
        (~_reported_mask(frame["SMP"])).sum()
    )
    diagnostics["adjhsg_missing_n"] = int(
        (~_reported_mask(frame["ADJHSG"])).sum()
    )
    diagnostics["wgtp_missing_n"] = int(
        (~_reported_mask(frame["WGTP"])).sum()
    )
    diagnostics["puma_count"] = int(frame["PUMA"].nunique())
    return diagnostics


def _group_diagnostics(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    replicate_weights = _replicate_weight_matrix(frame)
    return {
        "pi": _pi_group_diagnostics(
            frame,
            replicate_weights=replicate_weights,
        ),
        "junior": _junior_group_diagnostics(
            frame,
            replicate_weights=replicate_weights,
        ),
    }


def _finite_metric(value: Any) -> bool:
    try:
        return not pd.isna(value) and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _quality_flag(
    *,
    metrics: dict[str, Any],
    meets_minimum_sample: bool,
    metric_kind: str,
    geography_tier: str,
    fallback_applied: bool,
    county_mapping_complete: bool | None,
) -> str | None:
    flags: list[str] = []
    if not meets_minimum_sample:
        flags.append("insufficient_sample")
    coverage = metrics["coverage_rate"]
    if not pd.isna(coverage) and float(coverage) < 1:
        flags.append(
            "incomplete_pi_inputs"
            if metric_kind == "pi"
            else "incomplete_smp_inputs"
        )
    if metric_kind == "pi" and metrics["share_out_of_bounds"]:
        flags.append("pi_share_out_of_bounds_review")
    if not metrics["uncertainty_available"]:
        flags.append("pums_replicate_uncertainty_unavailable")
    if county_mapping_complete is False:
        flags.append("county_has_shared_puma")
    if geography_tier == "regional":
        flags.append("regional_puma_pool")
    if fallback_applied:
        flags.append("california_pums_fallback")
    return "|".join(flags) if flags else None


def build_pums_pi_share_parameters(
    pums: pd.DataFrame,
    relationship: pd.DataFrame,
    *,
    benchmark_vintage: str,
    source_period_start: Any,
    source_period_end: Any,
    reference_date: Any,
    release_date: Any = None,
    relationship_vintage: int | None = None,
    state_fips: str = "06",
    source_frequency: str = "5-year",
    source_id: str = "census_acs5_pums_housing_ca",
    source_vintage: str | None = None,
    source_filename: str | None = None,
    source_member_filename: str | None = None,
    retrieved_at: Any = None,
    checksum_sha256: str | None = None,
    relationship_source_filename: str | None = None,
    relationship_retrieved_at: Any = None,
    relationship_checksum_sha256: str | None = None,
    lineage_id: str | None = None,
    minimum_sample: int = 1,
    require_complete_california_relationship: bool = True,
) -> pd.DataFrame:
    """Calculate P&I-share and junior-lien-payment PUMS parameters.

    Both measures are calculated directly from pooled records with ``WGTP``;
    county or regional ratios are never averages of separately estimated
    ratios.  Complete county pools are preferred, followed by the connected
    county/PUMA region, then an explicitly labeled California fallback when
    the preferred pool is unavailable or below ``minimum_sample``.

    ``SMP`` is emitted only as an ADJHSG-adjusted monthly payment per eligible
    mortgaged PUMS unit.  It remains separate from first-lien P&I and is never
    interpreted as a mortgage balance.
    """
    if not isinstance(minimum_sample, int) or isinstance(minimum_sample, bool):
        raise ValueError("minimum_sample must be an integer")
    if minimum_sample < 0:
        raise ValueError("minimum_sample cannot be negative")
    if not isinstance(benchmark_vintage, str) or not benchmark_vintage.strip():
        raise ValueError("benchmark_vintage must be populated")
    if not isinstance(source_frequency, str) or not source_frequency.strip():
        raise ValueError("source_frequency must be populated")
    if relationship_vintage is not None and relationship_vintage not in {2010, 2020}:
        raise ValueError("relationship_vintage must be 2010 or 2020")

    state_code = _normalise_code(state_fips, width=2, name="state_fips")
    start = _normalise_date(source_period_start, "source_period_start")
    end = _normalise_date(source_period_end, "source_period_end")
    reference = _normalise_date(reference_date, "reference_date")
    release = _normalise_date(release_date, "release_date")
    if start > end:
        raise ValueError("source_period_start cannot exceed source_period_end")
    if not start <= reference <= end:
        raise ValueError("reference_date must fall inside the source period")
    if not pd.isna(release) and release < end:
        raise ValueError("release_date cannot precede source_period_end")

    geography_contract = resolve_pums_geography_contract(
        end.year,
        source_frequency=source_frequency,
        columns=pums.attrs.get("source_columns", pums.columns),
        relationship_vintage=relationship_vintage,
    )
    resolved_relationship_vintage = int(
        geography_contract["relationship_vintage"]
    )
    header_relationship_vintage = infer_relationship_vintage(
        relationship.columns
    )
    if (
        header_relationship_vintage is not None
        and header_relationship_vintage != resolved_relationship_vintage
    ):
        raise PUMSFormatError(
            f"PUMS release requires the {resolved_relationship_vintage} "
            "relationship, but the relationship header identifies "
            f"{header_relationship_vintage}"
        )
    if "relationship_vintage" in relationship.columns:
        relationship_vintages = _numeric_series_strict(
            relationship["relationship_vintage"],
            "relationship_vintage",
        ).dropna()
        if bool(relationship_vintages.mod(1).ne(0).any()):
            raise PUMSFormatError(
                "relationship_vintage must contain integer years"
            )
        unique_relationship_vintages = set(
            relationship_vintages.astype(int)
        )
        if len(unique_relationship_vintages) > 1:
            raise PUMSFormatError(
                "relationship frame contains multiple relationship vintages"
            )
        if (
            unique_relationship_vintages
            and unique_relationship_vintages
            != {resolved_relationship_vintage}
        ):
            raise PUMSFormatError(
                f"PUMS release requires the {resolved_relationship_vintage} "
                "relationship, but relationship_vintage contains "
                f"{sorted(unique_relationship_vintages)}"
            )
    hierarchy = derive_county_puma_pool_hierarchy(
        relationship,
        state_fips=state_code,
        relationship_vintage=resolved_relationship_vintage,
    )
    relationship_edges, _ = _relationship_edges_for_builder(
        relationship,
        state_fips=state_code,
        relationship_vintage=resolved_relationship_vintage,
    )
    if require_complete_california_relationship:
        expected_counties = set(CALIFORNIA_COUNTY_FIPS)
        relationship_counties = set(
            relationship_edges["county_fips"].astype(str)
        )
        if relationship_counties != expected_counties:
            raise PUMSFormatError(
                "California tract-to-PUMA relationship must cover exactly all "
                "58 counties; "
                f"missing={sorted(expected_counties - relationship_counties)}, "
                f"extra={sorted(relationship_counties - expected_counties)}"
            )
    eligible = filter_owner_first_mortgage(pums)
    state_metrics = _group_diagnostics(eligible)
    state_weight = float(state_metrics["pi"]["eligible_weight"])
    relationship_pumas = set(relationship_edges["PUMA"])
    unmapped_pumas = set(eligible["PUMA"].astype(str)) - relationship_pumas
    if unmapped_pumas:
        raise PUMSFormatError(
            "eligible PUMS records contain PUMAs absent from the "
            f"release-compatible relationship: {sorted(unmapped_pumas)}"
        )
    mapped_records = eligible["PUMA"].isin(relationship_pumas)
    eligible_weights = _numeric_series_strict(eligible["WGTP"], "WGTP")
    positive_weights = eligible_weights.notna() & eligible_weights.gt(0)
    mapped_weight = float(
        eligible_weights.loc[mapped_records & positive_weights].sum()
    )
    available_weight = float(eligible_weights.loc[positive_weights].sum())
    relationship_record_coverage = (
        float(mapped_records.mean()) if len(eligible) else math.nan
    )
    relationship_weighted_coverage = (
        mapped_weight / available_weight if available_weight > 0 else math.nan
    )
    all_counties = tuple(sorted(hierarchy["county_fips"].astype(str)))
    all_pumas = tuple(sorted(eligible["PUMA"].astype(str).unique()))
    source_release_dates = (
        f"{source_id}={release.date().isoformat()}"
    )

    common: dict[str, Any] = {
        "month": pd.NaT,
        "estimate_variant": "central",
        "reported_value": math.nan,
        "margin_of_error": math.nan,
        "database_version": "A. Benchmark",
        "source_id": source_id,
        "source_frequency": source_frequency,
        "source_period_start": start,
        "source_period_end": end,
        "reference_date": reference,
        "release_date": release,
        "source_release_dates": source_release_dates,
        "source_geography": "PUMA",
        "monthly_method": "not_applicable_source_period",
        "payment_frequency": "monthly",
        "is_interpolated": False,
        "is_modeled": False,
        "is_nowcast": False,
        "is_imputed": False,
        "benchmark_vintage": benchmark_vintage.strip(),
        "source_vintage": source_vintage,
        "retrieved_at": retrieved_at,
        "source_filename": source_filename or pums.attrs.get("source_filename"),
        "source_member_filename": (
            source_member_filename
            or pums.attrs.get("source_member_filename")
        ),
        "checksum_sha256": (
            checksum_sha256 or pums.attrs.get("checksum_sha256")
        ),
        "relationship_source_filename": (
            relationship_source_filename
            or relationship.attrs.get("source_filename")
        ),
        "relationship_retrieved_at": relationship_retrieved_at,
        "relationship_checksum_sha256": (
            relationship_checksum_sha256
            or relationship.attrs.get("checksum_sha256")
        ),
        "relationship_vintage": resolved_relationship_vintage,
        "puma_definition_vintage": geography_contract[
            "puma_definition_vintage"
        ],
        "puma_geography_status": geography_contract["geography_status"],
        "puma_is_census_crosswalked": geography_contract[
            "is_census_crosswalked"
        ],
        "lineage_id": lineage_id,
        "low_estimate": math.nan,
        "high_estimate": math.nan,
        "universe_definition": (
            "Owner-occupied ACS PUMS housing units with a first mortgage "
            "(TEN=1 and MRGX=1)"
        ),
        "relationship_eligible_record_coverage_rate": (
            relationship_record_coverage
        ),
        "relationship_eligible_weighted_coverage_rate": (
            relationship_weighted_coverage
        ),
        "relationship_eligible_record_n": int(mapped_records.sum()),
        "relationship_eligible_input_n": int(len(eligible)),
    }

    rows: list[dict[str, Any]] = []
    metric_specs: dict[str, dict[str, Any]] = {
        "pi": {
            "variable_name": "pums_first_mortgage_pi_share_benchmark",
            "unit": "share",
            "value_key": "pi_share",
            "transformation_id": "acs_pums_pi_share_geography_hierarchy_v2",
            "construction_method": (
                "Record-level first-mortgage P&I divided by selected monthly "
                "owner costs, pooled with WGTP over a complete county PUMA "
                "pool, connected regional PUMA pool, or labeled California "
                "fallback"
            ),
        },
        "junior": {
            "variable_name": (
                "pums_junior_lien_payment_per_mortgaged_unit_benchmark"
            ),
            "unit": "USD/unit/month",
            "value_key": "junior_lien_monthly_payment",
            "transformation_id": "acs_pums_smp_payment_diagnostic_v1",
            "construction_method": (
                "ADJHSG-adjusted SMP pooled as a WGTP-weighted monthly "
                "payment per eligible mortgaged PUMS unit; payment-only "
                "diagnostic excluded from primary debt and equity"
            ),
        },
    }

    def append_row(
        *,
        metric_kind: str,
        selected_metrics: dict[str, Any],
        preferred_metrics: dict[str, Any],
        county_fips: Any,
        parameter_geography: str,
        parameter_geography_level: str,
        geography_tier: str,
        preferred_parameter_geography: str,
        preferred_geography_tier: str,
        county_mapping_complete: bool | None,
        selected_member_counties: tuple[str, ...],
        selected_member_pumas: tuple[str, ...],
        preferred_member_counties: tuple[str, ...],
        preferred_member_pumas: tuple[str, ...],
        county_puma_count: int | None,
        county_shared_puma_count: int | None,
        fallback_applied: bool,
    ) -> None:
        spec = metric_specs[metric_kind]
        value_key = str(spec["value_key"])
        raw_value = preferred_metrics[value_key]
        selected_raw_value = selected_metrics[value_key]
        meets_minimum = int(selected_metrics["sample_n"]) >= minimum_sample
        value = (
            selected_raw_value
            if meets_minimum and _finite_metric(selected_raw_value)
            else math.nan
        )
        if not meets_minimum:
            missing_reason = "below_minimum_sample"
        elif not _finite_metric(selected_raw_value):
            missing_reason = "no_complete_weighted_records"
        else:
            missing_reason = None
        fallback_recommended = (
            not pd.isna(county_fips) and not _finite_metric(value)
        )
        publish_uncertainty = bool(
            _finite_metric(value)
            and selected_metrics["uncertainty_available"]
        )
        if metric_kind == "pi":
            notes = (
                "First-lien P&I parameter calculated from pooled PUMS "
                "records; SMP is excluded."
            )
        else:
            notes = (
                "Junior-lien SMP monthly payment per eligible mortgaged "
                "PUMS unit. Per the PUMS codebook, blank SMP means no junior "
                "lien and contributes a structural zero; reported zero means "
                "a lien with no regular payment. This diagnostic is excluded "
                "from primary first-lien debt and equity."
            )
        if selected_metrics["uncertainty_available"]:
            notes += (
                " The 90% margin of error and bounds use the official ACS "
                "successive difference replication formula with all 80 "
                "housing replicate weights."
            )
        elif selected_metrics["replicate_weight_count"] == 0:
            notes += (
                " Replicate-weight uncertainty is unavailable because "
                "WGTP1-WGTP80 were not supplied; no bounds were invented."
            )
        else:
            notes += (
                " All 80 replicate weights were supplied, but uncertainty is "
                "unavailable because the full-sample estimate is not finite."
            )
        row = {
            **common,
            "variable_name": spec["variable_name"],
            "variable_id": spec["variable_name"],
            "unit": spec["unit"],
            "transformation_id": spec["transformation_id"],
            "construction_method": spec["construction_method"],
            "state_fips": state_code,
            "county_fips": county_fips,
            "geo_id": (
                f"STATE_{state_code}"
                if parameter_geography_level == "state"
                else f"COUNTY_{county_fips}"
            ),
            "parameter_geography": parameter_geography,
            "parameter_geography_level": parameter_geography_level,
            "geography_tier": geography_tier,
            "preferred_parameter_geography": preferred_parameter_geography,
            "preferred_geography_tier": preferred_geography_tier,
            "value": value,
            "central_estimate": value,
            "standard_error": (
                selected_metrics["standard_error"]
                if publish_uncertainty
                else math.nan
            ),
            "margin_of_error": (
                selected_metrics["margin_of_error"]
                if publish_uncertainty
                else math.nan
            ),
            "low_estimate": (
                selected_metrics["low_estimate"]
                if publish_uncertainty
                else math.nan
            ),
            "high_estimate": (
                selected_metrics["high_estimate"]
                if publish_uncertainty
                else math.nan
            ),
            "confidence_level": selected_metrics["confidence_level"],
            "uncertainty_available": publish_uncertainty,
            "uncertainty_status": (
                selected_metrics["uncertainty_status"]
                if _finite_metric(value)
                else "unavailable_estimate_not_published"
            ),
            "uncertainty_method": selected_metrics["uncertainty_method"],
            "replicate_weight_count": selected_metrics[
                "replicate_weight_count"
            ],
            "sample_n": int(selected_metrics["sample_n"]),
            "input_n": int(selected_metrics["input_n"]),
            "coverage_rate": selected_metrics["coverage_rate"],
            "weighted_coverage_rate": selected_metrics[
                "weighted_coverage_rate"
            ],
            "eligible_weight": selected_metrics["eligible_weight"],
            "eligible_weight_share_of_state": (
                selected_metrics["eligible_weight"] / state_weight
                if state_weight > 0
                else math.nan
            ),
            "puma_count": selected_metrics["puma_count"],
            "relationship_puma_count": len(selected_member_pumas),
            "pool_member_county_count": len(selected_member_counties),
            "pool_member_puma_count": len(selected_member_pumas),
            "pool_member_counties": "|".join(selected_member_counties),
            "pool_member_pumas": "|".join(selected_member_pumas),
            "preferred_pool_member_county_count": len(
                preferred_member_counties
            ),
            "preferred_pool_member_puma_count": len(preferred_member_pumas),
            "preferred_pool_member_counties": "|".join(
                preferred_member_counties
            ),
            "preferred_pool_member_pumas": "|".join(preferred_member_pumas),
            "county_puma_count": county_puma_count,
            "county_shared_puma_count": county_shared_puma_count,
            "preferred_pool_raw_value": raw_value,
            "preferred_pool_sample_n": int(preferred_metrics["sample_n"]),
            "preferred_pool_input_n": int(preferred_metrics["input_n"]),
            "preferred_pool_coverage_rate": preferred_metrics[
                "coverage_rate"
            ],
            "preferred_pool_weighted_coverage_rate": preferred_metrics[
                "weighted_coverage_rate"
            ],
            "preferred_pool_margin_of_error": preferred_metrics[
                "margin_of_error"
            ],
            "preferred_pool_low_estimate": preferred_metrics[
                "low_estimate"
            ],
            "preferred_pool_high_estimate": preferred_metrics[
                "high_estimate"
            ],
            "preferred_pool_uncertainty_status": preferred_metrics[
                "uncertainty_status"
            ],
            "meets_minimum_sample": meets_minimum,
            "is_suppressed": bool(pd.isna(value) and not meets_minimum),
            "is_missing": bool(pd.isna(value)),
            "missing_reason": missing_reason,
            "county_mapping_complete": county_mapping_complete,
            "county_all_pumas_wholly_contained": county_mapping_complete,
            "fallback_applied": fallback_applied,
            "fallback_recommended": fallback_recommended,
            "quality_flag": _quality_flag(
                metrics=selected_metrics,
                meets_minimum_sample=meets_minimum,
                metric_kind=metric_kind,
                geography_tier=geography_tier,
                fallback_applied=fallback_applied,
                county_mapping_complete=county_mapping_complete,
            ),
            "notes": notes,
        }
        if not lineage_id:
            lineage_payload = "|".join(
                (
                    str(checksum_sha256 or pums.attrs.get("checksum_sha256")),
                    str(
                        relationship_checksum_sha256
                        or relationship.attrs.get("checksum_sha256")
                    ),
                    str(county_fips),
                    str(spec["variable_name"]),
                    benchmark_vintage.strip(),
                )
            )
            row["lineage_id"] = sha256(
                lineage_payload.encode("utf-8")
            ).hexdigest()
        if metric_kind == "pi":
            row.update(
                {
                    "pi_share": value,
                    "raw_pi_share": raw_value,
                    "weighted_pi_total": selected_metrics[
                        "weighted_pi_total"
                    ],
                    "weighted_smoc_total": selected_metrics[
                        "weighted_smoc_total"
                    ],
                    "share_out_of_bounds": selected_metrics[
                        "share_out_of_bounds"
                    ],
                }
            )
        else:
            row.update(
                {
                    "junior_lien_monthly_payment": value,
                    "raw_junior_lien_monthly_payment": raw_value,
                    "weighted_junior_lien_monthly_payment_total": (
                        selected_metrics[
                            "weighted_junior_lien_monthly_payment_total"
                        ]
                    ),
                    "complete_case_weight": selected_metrics[
                        "complete_case_weight"
                    ],
                    "positive_payment_n": selected_metrics[
                        "positive_payment_n"
                    ],
                    "positive_payment_weight": selected_metrics[
                        "positive_payment_weight"
                    ],
                    "positive_payment_weighted_share": selected_metrics[
                        "positive_payment_weighted_share"
                    ],
                    "structural_no_junior_lien_n": selected_metrics[
                        "structural_no_junior_lien_n"
                    ],
                    "structural_no_junior_lien_weight": selected_metrics[
                        "structural_no_junior_lien_weight"
                    ],
                }
            )
        for name, metric_value in selected_metrics.items():
            if name.endswith("_missing_n") or name.endswith("_unknown_n"):
                row[name] = metric_value
        rows.append(row)

    for metric_kind in metric_specs:
        append_row(
            metric_kind=metric_kind,
            selected_metrics=state_metrics[metric_kind],
            preferred_metrics=state_metrics[metric_kind],
            county_fips=pd.NA,
            parameter_geography=f"state:{state_code}",
            parameter_geography_level="state",
            geography_tier="state",
            preferred_parameter_geography=f"state:{state_code}",
            preferred_geography_tier="state",
            county_mapping_complete=None,
            selected_member_counties=all_counties,
            selected_member_pumas=all_pumas,
            preferred_member_counties=all_counties,
            preferred_member_pumas=all_pumas,
            county_puma_count=None,
            county_shared_puma_count=None,
            fallback_applied=False,
        )

    pool_metrics_cache: dict[
        tuple[str, ...], dict[str, dict[str, Any]]
    ] = {}
    for pool in hierarchy.itertuples(index=False):
        preferred_pumas = tuple(
            value for value in str(pool.pool_member_pumas).split("|") if value
        )
        preferred_counties = tuple(
            value
            for value in str(pool.pool_member_counties).split("|")
            if value
        )
        if preferred_pumas not in pool_metrics_cache:
            preferred_group = eligible.loc[
                eligible["PUMA"].isin(preferred_pumas)
            ].copy()
            pool_metrics_cache[preferred_pumas] = _group_diagnostics(
                preferred_group
            )
        preferred_metrics = pool_metrics_cache[preferred_pumas]

        for metric_kind, spec in metric_specs.items():
            value_key = str(spec["value_key"])
            preferred = preferred_metrics[metric_kind]
            preferred_usable = (
                int(preferred["sample_n"]) >= minimum_sample
                and _finite_metric(preferred[value_key])
            )
            state_usable = (
                int(state_metrics[metric_kind]["sample_n"]) >= minimum_sample
                and _finite_metric(state_metrics[metric_kind][value_key])
            )
            fallback_applied = not preferred_usable and state_usable
            if fallback_applied:
                selected = state_metrics[metric_kind]
                selected_geography = f"state:{state_code}"
                selected_level = "state"
                selected_tier = "state_fallback"
                selected_counties = all_counties
                selected_pumas = all_pumas
            else:
                selected = preferred
                selected_geography = str(
                    pool.preferred_parameter_geography
                )
                selected_level = (
                    "county"
                    if pool.preferred_geography_tier == "county"
                    else "region"
                )
                selected_tier = str(pool.preferred_geography_tier)
                selected_counties = preferred_counties
                selected_pumas = preferred_pumas

            append_row(
                metric_kind=metric_kind,
                selected_metrics=selected,
                preferred_metrics=preferred,
                county_fips=str(pool.county_fips),
                parameter_geography=selected_geography,
                parameter_geography_level=selected_level,
                geography_tier=selected_tier,
                preferred_parameter_geography=str(
                    pool.preferred_parameter_geography
                ),
                preferred_geography_tier=str(
                    pool.preferred_geography_tier
                ),
                county_mapping_complete=bool(
                    pool.county_mapping_complete
                ),
                selected_member_counties=selected_counties,
                selected_member_pumas=selected_pumas,
                preferred_member_counties=preferred_counties,
                preferred_member_pumas=preferred_pumas,
                county_puma_count=int(pool.county_puma_count),
                county_shared_puma_count=int(
                    pool.county_shared_puma_count
                ),
                fallback_applied=fallback_applied,
            )

    result = pd.DataFrame(rows)
    result["county_fips"] = result["county_fips"].astype("string")
    return result.sort_values(
        ["variable_name", "county_fips"],
        na_position="first",
    ).reset_index(drop=True)


# Descriptive aliases keep the public entry points easy to discover.
read_ca_pums_housing = read_pums_housing
filter_owner_occupied_first_mortgage = filter_owner_first_mortgage
calculate_pums_pi_share_parameters = build_pums_pi_share_parameters


__all__ = [
    "PUMS_DUAL_GEOGRAPHY_VINTAGES",
    "PUMSFormatError",
    "PUMS_REPLICATE_WEIGHT_COLUMNS",
    "PUMS_REQUIRED_COLUMNS",
    "RELATIONSHIP_REQUIRED_COLUMNS",
    "SUPPORTED_PUMS_5_YEAR_VINTAGES",
    "build_pums_pi_share_parameters",
    "calculate_pums_pi_share_parameters",
    "derive_county_puma_pool_hierarchy",
    "derive_wholly_contained_pumas",
    "filter_owner_first_mortgage",
    "filter_owner_occupied_first_mortgage",
    "infer_relationship_vintage",
    "read_ca_pums_housing",
    "read_pums_housing",
    "read_tract_puma_relationship",
    "require_pums_columns",
    "resolve_pums_geography_contract",
]
