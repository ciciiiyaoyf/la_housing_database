"""ACS PUMS ingestion and geography support for Version A benchmarks.

This module keeps the ACS PUMS principal-and-interest parameter at its native
source period.  It does not interpolate the parameter, repeat it to monthly
rows, or silently substitute the California result for a county result.

County parameters are calculated only from PUMAs that the official Census
tract-to-PUMA relationship file shows are wholly contained in one county.
PUMAs spanning multiple counties remain in the California calculation but are
not assigned to any individual county.
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

from src.benchmark.finance import calculate_pums_pi_share


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


class PUMSFormatError(ValueError):
    """Raised when a PUMS or tract-to-PUMA file violates its schema."""


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
    return {column: lookup[column] for column in PUMS_REQUIRED_COLUMNS}


def _read_selected_pums_csv(
    source: Path | Any,
    *,
    source_name: str,
) -> pd.DataFrame:
    """Read only the required fields after validating the complete header."""
    header = pd.read_csv(
        source,
        nrows=0,
        encoding="utf-8-sig",
    )
    selected = _required_pums_source_columns(header.columns)

    if hasattr(source, "seek"):
        source.seek(0)
    frame = pd.read_csv(
        source,
        usecols=list(selected.values()),
        dtype="string",
        encoding="utf-8-sig",
        low_memory=False,
    )
    frame = frame.rename(columns={original: name for name, original in selected.items()})
    frame = frame.loc[:, list(PUMS_REQUIRED_COLUMNS)]
    frame["PUMA"] = _normalise_code_series(
        frame["PUMA"], width=5, name=f"{source_name}: PUMA"
    )
    frame.attrs["source_filename"] = source_name
    return frame


def read_pums_housing(path: str | Path) -> pd.DataFrame:
    """Read an official California ACS PUMS housing CSV or single-CSV ZIP.

    Only the fields required by the methodology are loaded.  All nonidentifier
    fields remain source strings so missing flags and source values are not
    altered before the finance calculation performs its strict conversions.
    A ZIP is accepted only when it contains exactly one CSV member.
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
    lookup = _column_lookup(frame.columns)
    missing = [column for column in PUMS_REQUIRED_COLUMNS if column not in lookup]
    if missing:
        raise PUMSFormatError(f"missing required PUMS columns: {missing}")


def _standardise_pums_frame(frame: pd.DataFrame) -> pd.DataFrame:
    selected = _required_pums_source_columns(frame.columns)
    result = frame.loc[:, list(selected.values())].rename(
        columns={original: name for name, original in selected.items()}
    )
    result = result.loc[:, list(PUMS_REQUIRED_COLUMNS)].copy()
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


def _mapping_for_builder(
    relationship: pd.DataFrame,
    *,
    state_fips: str,
    relationship_vintage: int | None,
) -> pd.DataFrame:
    if {"county_fips", "PUMA"}.issubset(relationship.columns):
        mapping = relationship.copy()
        if "is_wholly_contained" in mapping:
            mapping = mapping.loc[
                mapping["is_wholly_contained"].fillna(False).astype(bool)
            ].copy()
        mapping["county_fips"] = _normalise_code_series(
            mapping["county_fips"], width=5, name="county_fips"
        )
        mapping["PUMA"] = _normalise_code_series(
            mapping["PUMA"], width=5, name="PUMA"
        )
        mapping = mapping.loc[
            mapping["county_fips"].str[:2].eq(state_fips)
        ].copy()
        if "state_fips" not in mapping:
            mapping["state_fips"] = mapping["county_fips"].str[:2]
        if "relationship_vintage" not in mapping:
            mapping["relationship_vintage"] = relationship_vintage
        if "county_puma_count" not in mapping:
            mapping["county_puma_count"] = mapping.groupby("county_fips")[
                "PUMA"
            ].transform("nunique")
        if "county_wholly_contained_puma_count" not in mapping:
            mapping["county_wholly_contained_puma_count"] = mapping.groupby(
                "county_fips"
            )["PUMA"].transform("nunique")
        if "county_all_pumas_wholly_contained" not in mapping:
            # A bare PUMA-to-county list cannot establish that the county has
            # no additional shared PUMA.  Preserve that uncertainty so the
            # output recommends an explicit broader-geography fallback.
            mapping["county_all_pumas_wholly_contained"] = pd.NA
        if "puma_county_count" not in mapping:
            mapping["puma_county_count"] = 1
        mapping["is_wholly_contained"] = True
    else:
        mapping = derive_wholly_contained_pumas(
            relationship,
            state_fips=state_fips,
            relationship_vintage=relationship_vintage,
        )

    assignments = mapping[
        ["PUMA", "county_fips"]
    ].drop_duplicates()
    duplicate_assignments = assignments.groupby("PUMA")[
        "county_fips"
    ].nunique()
    if bool(duplicate_assignments.gt(1).any()):
        raise PUMSFormatError(
            "a purported wholly-contained PUMA is assigned to multiple counties"
        )
    return mapping.drop_duplicates(["county_fips", "PUMA"]).reset_index(drop=True)


def _flag_diagnostics(frame: pd.DataFrame, column: str) -> dict[str, int]:
    reported = _reported_mask(frame[column])
    normalised = frame[column].astype("string").str.strip().str.casefold()
    known = normalised.isin({"1", "2", "y", "yes", "n", "no"})
    return {
        f"{column.lower()}_missing_n": int((~reported).sum()),
        f"{column.lower()}_unknown_n": int((reported & ~known).sum()),
    }


def _group_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    metrics = calculate_pums_pi_share(frame)
    diagnostics: dict[str, Any] = dict(metrics)
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


def _quality_flag(
    *,
    metrics: dict[str, Any],
    meets_minimum_sample: bool,
    county_mapping_complete: bool,
) -> str | None:
    flags: list[str] = []
    if not meets_minimum_sample:
        flags.append("insufficient_sample")
    coverage = metrics["coverage_rate"]
    if not pd.isna(coverage) and float(coverage) < 1:
        flags.append("incomplete_pi_inputs")
    if metrics["share_out_of_bounds"]:
        flags.append("pi_share_out_of_bounds_review")
    if not county_mapping_complete:
        flags.append("county_has_shared_puma")
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
    source_id: str = "census_acs5_pums_housing",
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
) -> pd.DataFrame:
    """Calculate source-period California and county PUMS P&I parameters.

    The California row uses every eligible California PUMS housing record.
    County rows use only PUMAs wholly contained in that county.  Shared PUMAs
    are deliberately omitted from county calculations; California fallback is
    available to the caller but is never applied here.

    Estimates below ``minimum_sample`` remain represented as missing output
    rows with their diagnostics and unsuppressed ``raw_pi_share`` retained.
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
    release = _normalise_date(
        release_date, "release_date", allow_missing=True
    )
    if start > end:
        raise ValueError("source_period_start cannot exceed source_period_end")
    if not start <= reference <= end:
        raise ValueError("reference_date must fall inside the source period")
    if not pd.isna(release) and release < end:
        raise ValueError("release_date cannot precede source_period_end")

    eligible = filter_owner_first_mortgage(pums)
    mapping = _mapping_for_builder(
        relationship,
        state_fips=state_code,
        relationship_vintage=relationship_vintage,
    )
    resolved_relationship_vintage = relationship_vintage
    if (
        resolved_relationship_vintage is None
        and "relationship_vintage" in mapping.columns
    ):
        mapping_vintages = pd.to_numeric(
            mapping["relationship_vintage"], errors="coerce"
        )
        reported_mapping_vintage = _reported_mask(
            mapping["relationship_vintage"]
        )
        if bool((reported_mapping_vintage & mapping_vintages.isna()).any()):
            raise PUMSFormatError(
                "relationship mapping contains an invalid relationship_vintage"
            )
        mapping_vintages = mapping_vintages.dropna().unique()
        if len(mapping_vintages) > 1:
            raise PUMSFormatError(
                "relationship mapping contains multiple relationship vintages"
            )
        if len(mapping_vintages) == 1:
            resolved_relationship_vintage = int(mapping_vintages[0])
    state_metrics = _group_diagnostics(eligible)
    state_weight = float(state_metrics["eligible_weight"])

    common: dict[str, Any] = {
        "month": pd.NaT,
        "variable_name": "pums_first_mortgage_pi_share_benchmark",
        "variable_id": "pums_first_mortgage_pi_share_benchmark",
        "unit": "share",
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
        "source_geography": "PUMA",
        "monthly_method": "not_applicable_source_period",
        "construction_method": (
            "WGTP-weighted first-mortgage P&I divided by selected monthly "
            "owner costs; county parameters use wholly-contained PUMAs only"
        ),
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
        "transformation_id": (
            "acs_pums_pi_share_wholly_contained_puma_v1"
        ),
        "lineage_id": lineage_id,
        "low_estimate": math.nan,
        "high_estimate": math.nan,
        "universe_definition": (
            "Owner-occupied ACS PUMS housing units with a first mortgage "
            "(TEN=1 and MRGX=1)"
        ),
    }

    rows: list[dict[str, Any]] = []

    def append_row(
        group: pd.DataFrame,
        *,
        county_fips: Any,
        parameter_geography: str,
        parameter_geography_level: str,
        county_mapping_complete: bool,
        relationship_puma_count: int,
    ) -> None:
        metrics = state_metrics if parameter_geography_level == "state" else (
            _group_diagnostics(group)
        )
        raw_share = metrics["pi_share"]
        meets_minimum = int(metrics["sample_n"]) >= minimum_sample
        value = raw_share if meets_minimum else math.nan
        fallback_recommended = (
            parameter_geography_level == "county"
            and (not meets_minimum or not county_mapping_complete)
        )
        if not meets_minimum:
            missing_reason = "below_minimum_sample"
        elif pd.isna(raw_share):
            missing_reason = "no_complete_weighted_records"
        else:
            missing_reason = None
        notes = (
            "California PUMS parameter; no county fallback applied."
            if parameter_geography_level == "state"
            else (
                "County parameter uses only PUMAs wholly contained in the "
                "county; no California fallback applied."
            )
        )
        row = {
            **common,
            "state_fips": state_code,
            "county_fips": county_fips,
            "geo_id": (
                f"STATE_{state_code}"
                if parameter_geography_level == "state"
                else f"COUNTY_{county_fips}"
            ),
            "parameter_geography": parameter_geography,
            "parameter_geography_level": parameter_geography_level,
            "value": value,
            "pi_share": value,
            "raw_pi_share": raw_share,
            "central_estimate": value,
            "sample_n": int(metrics["sample_n"]),
            "input_n": int(metrics["input_n"]),
            "coverage_rate": metrics["coverage_rate"],
            "weighted_coverage_rate": metrics["weighted_coverage_rate"],
            "weighted_pi_total": metrics["weighted_pi_total"],
            "weighted_smoc_total": metrics["weighted_smoc_total"],
            "eligible_weight": metrics["eligible_weight"],
            "eligible_weight_share_of_state": (
                metrics["eligible_weight"] / state_weight
                if state_weight > 0
                else math.nan
            ),
            "puma_count": metrics["puma_count"],
            "relationship_puma_count": relationship_puma_count,
            "meets_minimum_sample": meets_minimum,
            "is_suppressed": not meets_minimum,
            "is_missing": bool(pd.isna(value)),
            "missing_reason": missing_reason,
            "share_out_of_bounds": metrics["share_out_of_bounds"],
            "county_all_pumas_wholly_contained": county_mapping_complete,
            "fallback_recommended": fallback_recommended,
            "quality_flag": _quality_flag(
                metrics=metrics,
                meets_minimum_sample=meets_minimum,
                county_mapping_complete=county_mapping_complete,
            ),
            "notes": notes,
        }
        for name, metric_value in metrics.items():
            if name.endswith("_missing_n") or name.endswith("_unknown_n"):
                row[name] = metric_value
        rows.append(row)

    append_row(
        eligible,
        county_fips=pd.NA,
        parameter_geography=f"state:{state_code}",
        parameter_geography_level="state",
        county_mapping_complete=True,
        relationship_puma_count=int(eligible["PUMA"].nunique()),
    )

    for county_fips, county_mapping in mapping.groupby(
        "county_fips", sort=True
    ):
        pumas = set(county_mapping["PUMA"])
        group = eligible.loc[eligible["PUMA"].isin(pumas)].copy()
        complete_values = county_mapping[
            "county_all_pumas_wholly_contained"
        ].dropna()
        mapping_complete = (
            bool(complete_values.all()) if not complete_values.empty else False
        )
        append_row(
            group,
            county_fips=county_fips,
            parameter_geography=f"county:{county_fips}",
            parameter_geography_level="county",
            county_mapping_complete=mapping_complete,
            relationship_puma_count=len(pumas),
        )

    result = pd.DataFrame(rows)
    result["county_fips"] = result["county_fips"].astype("string")
    return result.reset_index(drop=True)


# Descriptive aliases keep the public entry points easy to discover.
read_ca_pums_housing = read_pums_housing
filter_owner_occupied_first_mortgage = filter_owner_first_mortgage
calculate_pums_pi_share_parameters = build_pums_pi_share_parameters


__all__ = [
    "PUMSFormatError",
    "PUMS_REQUIRED_COLUMNS",
    "RELATIONSHIP_REQUIRED_COLUMNS",
    "build_pums_pi_share_parameters",
    "calculate_pums_pi_share_parameters",
    "derive_wholly_contained_pumas",
    "filter_owner_first_mortgage",
    "filter_owner_occupied_first_mortgage",
    "read_ca_pums_housing",
    "read_pums_housing",
    "read_tract_puma_relationship",
    "require_pums_columns",
]
