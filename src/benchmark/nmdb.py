"""Strict normalization for manual FHFA NMDB dashboard exports.

The FHFA National Mortgage Database outstanding-mortgage dashboard does not
have a stable bulk endpoint in this project.  This module therefore accepts
only an already-downloaded official CSV (or an in-memory frame representing
that CSV) plus explicit column and source metadata.  It never downloads data,
constructs a joint distribution from separate marginals, normalizes weights,
or expands a published period to monthly observations.

Two parameter tables are produced for :func:`build_version_a_benchmark`:

* a genuinely joint outstanding-mortgage coupon-rate by remaining-term
  distribution; and
* one universe-compatible published California unpaid-principal-balance
  control per benchmark vintage.

``column_map`` is always canonical-name to source-column-name.  Period fields
may be mapped from source columns or supplied as constants in
``source_metadata``.  Provenance fields are constants for the downloaded
export and cannot be mapped away.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd


FHFA_NMDB_SOURCE_ID = "fhfa_nmdb_outstanding"
FHFA_NMDB_PROVIDER = "Federal Housing Finance Agency"

_PERIOD_FIELDS = (
    "benchmark_vintage",
    "reference_date",
    "source_period_start",
    "source_period_end",
    "release_date",
    "source_frequency",
    "parameter_geography",
)

_DISTRIBUTION_MAPPING_FIELDS = frozenset(
    {
        "annual_rate",
        "remaining_months",
        "weight",
        *_PERIOD_FIELDS,
    }
)
_UPB_MAPPING_FIELDS = frozenset({"value", *_PERIOD_FIELDS})

_JOINT_KINDS = {
    "joint_rate_remaining_term",
    "joint_outstanding_rate_remaining_term_distribution",
}
_OUTSTANDING_POPULATIONS = {
    "outstanding_mortgages",
    "outstanding_residential_mortgages",
}
_UPB_CONTROL_TYPES = {
    "unpaid_principal_balance",
    "outstanding_unpaid_principal_balance",
    "upb",
}
_CALIFORNIA_GEOGRAPHIES = {
    "06",
    "ca",
    "california",
    "state:06",
    "state_06",
}
_FORBIDDEN_MONTHLY_METHOD = re.compile(
    r"interpol|nowcast|carry.?forward|repeat|distribut|monthly.?alloc",
    flags=re.IGNORECASE,
)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


class NMDBFormatError(ValueError):
    """Raised when a manual NMDB export violates the Version A contract."""


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_source(
    source: pd.DataFrame | str | Path,
    source_metadata: Mapping[str, Any],
) -> tuple[pd.DataFrame, str, str]:
    if isinstance(source, pd.DataFrame):
        frame = source.copy()
        source_filename = source_metadata.get("source_filename")
        checksum = source_metadata.get("checksum_sha256")
        if _is_blank(source_filename):
            raise NMDBFormatError(
                "source_filename is required for an in-memory NMDB export"
            )
        if _is_blank(checksum) or not _SHA256.fullmatch(str(checksum).strip()):
            raise NMDBFormatError(
                "a 64-character checksum_sha256 is required for an "
                "in-memory NMDB export"
            )
        resolved_filename = str(source_filename).strip()
        resolved_checksum = str(checksum).strip().lower()
    else:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.casefold() != ".csv":
            raise NMDBFormatError(
                f"{path.name}: expected an official dashboard CSV export"
            )
        try:
            frame = pd.read_csv(path, dtype="object", low_memory=False)
        except (UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
            raise NMDBFormatError(
                f"{path.name}: cannot parse NMDB dashboard CSV"
            ) from exc
        resolved_filename = path.name
        resolved_checksum = _sha256(path)
        supplied_filename = source_metadata.get("source_filename")
        if not _is_blank(supplied_filename) and (
            str(supplied_filename).strip() != resolved_filename
        ):
            raise NMDBFormatError(
                "source_filename does not match the supplied CSV filename"
            )
        supplied_checksum = source_metadata.get("checksum_sha256")
        if not _is_blank(supplied_checksum) and (
            str(supplied_checksum).strip().casefold() != resolved_checksum
        ):
            raise NMDBFormatError(
                "checksum_sha256 does not match the supplied CSV"
            )

    if frame.empty:
        raise NMDBFormatError("NMDB export cannot be empty")
    if frame.columns.duplicated().any():
        duplicates = frame.columns[frame.columns.duplicated()].astype(str).tolist()
        raise NMDBFormatError(f"NMDB export contains duplicate columns: {duplicates}")
    return frame, resolved_filename, resolved_checksum


def _validate_column_map(
    frame: pd.DataFrame,
    column_map: Mapping[str, str],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
) -> dict[str, str]:
    if not isinstance(column_map, Mapping):
        raise TypeError("column_map must be a canonical-to-source mapping")
    unknown = sorted(set(column_map).difference(allowed))
    if unknown:
        raise NMDBFormatError(f"unsupported canonical column mappings: {unknown}")
    missing = sorted(required.difference(column_map))
    if missing:
        raise NMDBFormatError(f"missing required column mappings: {missing}")

    resolved: dict[str, str] = {}
    for canonical, source_column in column_map.items():
        if not isinstance(source_column, str) or not source_column.strip():
            raise NMDBFormatError(
                f"column mapping for {canonical} must name one source column"
            )
        if source_column not in frame.columns:
            raise NMDBFormatError(
                f"mapped source column is absent: {canonical} -> {source_column}"
            )
        resolved[canonical] = source_column
    duplicate_targets = pd.Series(list(resolved.values())).duplicated(keep=False)
    if duplicate_targets.any():
        repeated = sorted(
            set(pd.Series(list(resolved.values())).loc[duplicate_targets].tolist())
        )
        raise NMDBFormatError(
            f"one source column cannot supply multiple concepts: {repeated}"
        )
    return resolved


def _require_text(metadata: Mapping[str, Any], field: str) -> str:
    value = metadata.get(field)
    if _is_blank(value):
        raise NMDBFormatError(f"{field} source metadata is required")
    return str(value).strip()


def _validate_source_metadata(
    source_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(source_metadata, Mapping):
        raise TypeError("source_metadata must be a mapping")

    source_vintage = _require_text(source_metadata, "source_vintage")
    universe_definition = _require_text(source_metadata, "universe_definition")
    source_page_url = _require_text(source_metadata, "source_page_url")
    parsed_url = urlparse(source_page_url)
    hostname = (parsed_url.hostname or "").casefold()
    if parsed_url.scheme.casefold() != "https" or not (
        hostname == "fhfa.gov" or hostname.endswith(".fhfa.gov")
    ):
        raise NMDBFormatError(
            "source_page_url must be an official HTTPS fhfa.gov page"
        )

    retrieved = pd.to_datetime(
        source_metadata.get("retrieved_at"), errors="coerce", utc=True
    )
    if pd.isna(retrieved):
        raise NMDBFormatError("retrieved_at source metadata is required and valid")

    monthly_method = str(
        source_metadata.get(
            "monthly_method", "not_applicable_source_period"
        )
    ).strip()
    if not monthly_method:
        raise NMDBFormatError("monthly_method cannot be blank")
    if _FORBIDDEN_MONTHLY_METHOD.search(monthly_method):
        raise NMDBFormatError(
            "monthly interpolation, allocation, repetition, and nowcasting "
            "are prohibited in Version A"
        )
    for flag in ("is_modeled", "is_nowcast", "is_imputed", "is_interpolated"):
        if flag in source_metadata and source_metadata[flag] is not False:
            raise NMDBFormatError(f"{flag} must be explicitly false when supplied")

    source_id = source_metadata.get("source_id", FHFA_NMDB_SOURCE_ID)
    if _token(source_id) != FHFA_NMDB_SOURCE_ID:
        raise NMDBFormatError(
            f"source_id must identify {FHFA_NMDB_SOURCE_ID}"
        )
    return {
        "source_id": FHFA_NMDB_SOURCE_ID,
        "provider": FHFA_NMDB_PROVIDER,
        "source_vintage": source_vintage,
        "retrieved_at": retrieved,
        "source_page_url": source_page_url,
        "universe_definition": universe_definition,
        "monthly_method": monthly_method,
        "notes": (
            None
            if _is_blank(source_metadata.get("notes"))
            else str(source_metadata["notes"]).strip()
        ),
    }


def _series_from_mapping_or_metadata(
    frame: pd.DataFrame,
    column_map: Mapping[str, str],
    source_metadata: Mapping[str, Any],
    field: str,
) -> pd.Series:
    if field in column_map:
        return frame[column_map[field]].copy()
    if field not in source_metadata or _is_blank(source_metadata.get(field)):
        raise NMDBFormatError(
            f"{field} must be mapped from the source or supplied as metadata"
        )
    return pd.Series(source_metadata[field], index=frame.index, dtype="object")


def _strict_numeric(series: pd.Series, field: str) -> pd.Series:
    blank = series.map(_is_blank)
    numeric = pd.to_numeric(series.mask(blank), errors="coerce").astype(float)
    invalid = ~blank & numeric.isna()
    if invalid.any():
        examples = (
            series.loc[invalid].astype(str).drop_duplicates().head(3).tolist()
        )
        raise NMDBFormatError(f"{field} contains nonnumeric values: {examples}")
    if numeric.isna().any():
        raise NMDBFormatError(f"{field} cannot contain missing values")
    if not numeric.map(math.isfinite).all():
        raise NMDBFormatError(f"{field} must contain only finite values")
    return numeric


def _strict_text(series: pd.Series, field: str) -> pd.Series:
    result = series.astype("string").str.strip()
    if result.isna().any() or result.eq("").any():
        raise NMDBFormatError(f"{field} cannot contain missing or blank values")
    return result.astype(str)


def _strict_dates(series: pd.Series, field: str) -> pd.Series:
    blank = series.map(_is_blank)
    result = pd.to_datetime(series.mask(blank), errors="coerce")
    if blank.any() or result.isna().any():
        raise NMDBFormatError(f"{field} must contain valid, nonmissing dates")
    if getattr(result.dt, "tz", None) is not None:
        result = result.dt.tz_convert(None)
    return result.dt.normalize()


def _period_columns(
    frame: pd.DataFrame,
    column_map: Mapping[str, str],
    source_metadata: Mapping[str, Any],
) -> pd.DataFrame:
    period = pd.DataFrame(index=frame.index)
    for field in _PERIOD_FIELDS:
        raw = _series_from_mapping_or_metadata(
            frame, column_map, source_metadata, field
        )
        if field in {
            "reference_date",
            "source_period_start",
            "source_period_end",
            "release_date",
        }:
            period[field] = _strict_dates(raw, field)
        else:
            period[field] = _strict_text(raw, field)

    invalid_order = (
        period["source_period_start"].gt(period["source_period_end"])
        | period["reference_date"].lt(period["source_period_start"])
        | period["reference_date"].gt(period["source_period_end"])
        | period["release_date"].lt(period["source_period_end"])
    )
    if invalid_order.any():
        raise NMDBFormatError(
            "source periods must satisfy start <= reference <= end <= release"
        )

    # build_version_a_benchmark selects distributions and controls by benchmark
    # vintage first.  One vintage therefore must identify one period/geography.
    identity = period[
        [
            "benchmark_vintage",
            "reference_date",
            "source_period_start",
            "source_period_end",
            "source_frequency",
            "parameter_geography",
        ]
    ].drop_duplicates()
    if identity.duplicated("benchmark_vintage", keep=False).any():
        vintages = sorted(
            identity.loc[
                identity.duplicated("benchmark_vintage", keep=False),
                "benchmark_vintage",
            ].unique()
        )
        raise NMDBFormatError(
            "each benchmark_vintage must identify exactly one source period "
            f"and geography; ambiguous vintages: {vintages}"
        )
    return period


def _common_output(
    period: pd.DataFrame,
    provenance: Mapping[str, Any],
    *,
    source_filename: str,
    checksum_sha256: str,
    transformation_id: str,
) -> pd.DataFrame:
    result = period.copy()
    result["database_version"] = "A. Benchmark"
    result["source_id"] = provenance["source_id"]
    result["provider"] = provenance["provider"]
    result["source_geography"] = result["parameter_geography"]
    result["month"] = pd.NaT
    result["monthly_method"] = provenance["monthly_method"]
    result["is_modeled"] = False
    result["is_nowcast"] = False
    result["is_imputed"] = False
    result["is_interpolated"] = False
    result["source_vintage"] = provenance["source_vintage"]
    result["retrieved_at"] = provenance["retrieved_at"]
    result["source_filename"] = source_filename
    result["checksum_sha256"] = checksum_sha256
    result["source_page_url"] = provenance["source_page_url"]
    result["transformation_id"] = transformation_id
    result["universe_definition"] = provenance["universe_definition"]
    result["notes"] = provenance["notes"]
    return result


def normalize_nmdb_joint_distribution(
    source: pd.DataFrame | str | Path,
    *,
    column_map: Mapping[str, str],
    source_metadata: Mapping[str, Any],
    weight_tolerance: float = 1e-8,
) -> pd.DataFrame:
    """Normalize a published joint rate × remaining-term distribution.

    Required mappings are ``annual_rate``, ``remaining_months``, and
    ``weight``.  Source units are explicit metadata:

    * ``rate_unit``: ``decimal`` or ``percent``;
    * ``remaining_term_unit``: ``months`` or ``years``; and
    * ``weight_unit``: ``share`` or ``percent``.

    ``distribution_kind`` must attest that the official export is a joint
    rate/term table, and ``mortgage_population`` must identify outstanding
    mortgages.  Separate rate and term marginals are rejected; this function
    never combines them.  Published weights must already sum to one within
    each source period and parameter geography.
    """
    if not isinstance(weight_tolerance, (int, float)) or isinstance(
        weight_tolerance, bool
    ):
        raise TypeError("weight_tolerance must be numeric")
    if not math.isfinite(float(weight_tolerance)) or weight_tolerance < 0:
        raise ValueError("weight_tolerance must be finite and nonnegative")

    provenance = _validate_source_metadata(source_metadata)
    kind = _token(_require_text(source_metadata, "distribution_kind"))
    if kind not in _JOINT_KINDS:
        raise NMDBFormatError(
            "distribution_kind must identify a published joint outstanding "
            "rate × remaining-term distribution; separate marginals are "
            "not accepted"
        )
    population = _token(_require_text(source_metadata, "mortgage_population"))
    if population not in _OUTSTANDING_POPULATIONS:
        raise NMDBFormatError(
            "mortgage_population must identify outstanding mortgages"
        )

    rate_unit = _token(_require_text(source_metadata, "rate_unit"))
    if rate_unit not in {"decimal", "percent"}:
        raise NMDBFormatError("rate_unit must be decimal or percent")
    term_unit = _token(_require_text(source_metadata, "remaining_term_unit"))
    if term_unit not in {"month", "months", "year", "years"}:
        raise NMDBFormatError(
            "remaining_term_unit must be months or years"
        )
    weight_unit = _token(_require_text(source_metadata, "weight_unit"))
    if weight_unit not in {"share", "decimal", "percent"}:
        raise NMDBFormatError("weight_unit must be share or percent")

    frame, source_filename, checksum = _load_source(source, source_metadata)
    mapping = _validate_column_map(
        frame,
        column_map,
        allowed=_DISTRIBUTION_MAPPING_FIELDS,
        required=frozenset({"annual_rate", "remaining_months", "weight"}),
    )
    period = _period_columns(frame, mapping, source_metadata)

    annual_rate = _strict_numeric(frame[mapping["annual_rate"]], "annual_rate")
    remaining_months = _strict_numeric(
        frame[mapping["remaining_months"]], "remaining_months"
    )
    weight = _strict_numeric(frame[mapping["weight"]], "weight")
    if rate_unit == "percent":
        annual_rate = annual_rate / 100.0
    if term_unit in {"year", "years"}:
        remaining_months = remaining_months * 12.0
    if weight_unit == "percent":
        weight = weight / 100.0

    if annual_rate.lt(0).any():
        raise NMDBFormatError("annual_rate cannot be negative")
    if remaining_months.le(0).any():
        raise NMDBFormatError("remaining_months must be positive")
    if weight.lt(0).any():
        raise NMDBFormatError("weight cannot be negative")

    result = _common_output(
        period,
        provenance,
        source_filename=source_filename,
        checksum_sha256=checksum,
        transformation_id="nmdb_joint_rate_remaining_term_manual_v1",
    )
    result["annual_rate"] = annual_rate
    result["remaining_months"] = remaining_months
    result["weight"] = weight
    result["rates_are_percent"] = False
    result["rate_unit"] = "decimal"
    result["remaining_term_unit"] = "months"
    result["weight_unit"] = "share"
    result["distribution_kind"] = "joint_rate_remaining_term"
    result["mortgage_population"] = "outstanding_mortgages"
    result["joint_distribution"] = True

    group_key = [
        "benchmark_vintage",
        "reference_date",
        "source_period_start",
        "source_period_end",
        "parameter_geography",
    ]
    duplicate_cells = result.duplicated(
        group_key + ["annual_rate", "remaining_months"],
        keep=False,
    )
    if duplicate_cells.any():
        raise NMDBFormatError(
            "joint distribution contains duplicate rate × remaining-term "
            "cells within a source period/geography"
        )
    totals = result.groupby(group_key, dropna=False)["weight"].sum()
    invalid_totals = ~totals.map(
        lambda value: math.isclose(
            float(value),
            1.0,
            rel_tol=0.0,
            abs_tol=float(weight_tolerance),
        )
    )
    if invalid_totals.any():
        detail = {
            str(key): float(value)
            for key, value in totals.loc[invalid_totals].items()
        }
        raise NMDBFormatError(
            "published joint weights must sum to one within every source "
            f"period/geography; observed totals: {detail}"
        )

    columns = [
        "benchmark_vintage",
        "reference_date",
        "annual_rate",
        "remaining_months",
        "weight",
        "parameter_geography",
        "source_period_start",
        "source_period_end",
        "release_date",
        "source_frequency",
        "month",
        "database_version",
        "source_id",
        "provider",
        "source_geography",
        "monthly_method",
        "is_modeled",
        "is_nowcast",
        "is_imputed",
        "is_interpolated",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
        "transformation_id",
        "universe_definition",
        "notes",
        "rates_are_percent",
        "rate_unit",
        "remaining_term_unit",
        "weight_unit",
        "distribution_kind",
        "mortgage_population",
        "joint_distribution",
    ]
    return result.loc[:, columns].sort_values(
        [
            "benchmark_vintage",
            "reference_date",
            "parameter_geography",
            "annual_rate",
            "remaining_months",
        ]
    ).reset_index(drop=True)


def normalize_nmdb_california_upb(
    source: pd.DataFrame | str | Path,
    *,
    column_map: Mapping[str, str],
    source_metadata: Mapping[str, Any],
) -> pd.DataFrame:
    """Normalize compatible published California outstanding UPB controls.

    ``value`` is the only required source-column mapping.  ``value_unit`` must
    explicitly identify USD, thousand USD, million USD, or billion USD.
    ``control_type`` must identify unpaid principal balance and
    ``compatible_with_county_debt`` must be the boolean ``True``.  There may
    be only one California control for each benchmark vintage/reference date;
    duplicate controls are never summed or otherwise resolved silently.
    """
    provenance = _validate_source_metadata(source_metadata)
    control_type = _token(_require_text(source_metadata, "control_type"))
    if control_type not in _UPB_CONTROL_TYPES:
        raise NMDBFormatError(
            "control_type must identify outstanding unpaid principal balance"
        )
    if source_metadata.get("compatible_with_county_debt") is not True:
        raise NMDBFormatError(
            "compatible_with_county_debt must be explicitly true"
        )
    unit = _token(_require_text(source_metadata, "value_unit"))
    scale = {
        "usd": 1.0,
        "thousand_usd": 1_000.0,
        "usd_thousands": 1_000.0,
        "million_usd": 1_000_000.0,
        "usd_millions": 1_000_000.0,
        "billion_usd": 1_000_000_000.0,
        "usd_billions": 1_000_000_000.0,
    }.get(unit)
    if scale is None:
        raise NMDBFormatError(
            "value_unit must be USD, thousand USD, million USD, or billion USD"
        )

    frame, source_filename, checksum = _load_source(source, source_metadata)
    mapping = _validate_column_map(
        frame,
        column_map,
        allowed=_UPB_MAPPING_FIELDS,
        required=frozenset({"value"}),
    )
    period = _period_columns(frame, mapping, source_metadata)
    geography = period["parameter_geography"].map(_token)
    if not geography.isin(_CALIFORNIA_GEOGRAPHIES).all():
        observed = sorted(period["parameter_geography"].unique().tolist())
        raise NMDBFormatError(
            "California UPB controls must have California parameter_geography; "
            f"observed: {observed}"
        )
    period["parameter_geography"] = "California"

    value = _strict_numeric(frame[mapping["value"]], "value") * scale
    if value.lt(0).any():
        raise NMDBFormatError("California UPB cannot be negative")

    result = _common_output(
        period,
        provenance,
        source_filename=source_filename,
        checksum_sha256=checksum,
        transformation_id="nmdb_california_upb_manual_v1",
    )
    result["value"] = value
    result["unit"] = "USD"
    result["variable_name"] = "california_unpaid_principal_balance_benchmark"
    result["control_type"] = "unpaid_principal_balance"
    result["compatible_with_county_debt"] = True
    result["quality_flag"] = "published_california_upb_control"

    duplicate_key = ["benchmark_vintage", "reference_date"]
    if result.duplicated(duplicate_key, keep=False).any():
        duplicates = sorted(
            result.loc[
                result.duplicated(duplicate_key, keep=False),
                "benchmark_vintage",
            ].unique()
        )
        raise NMDBFormatError(
            "ambiguous duplicate California UPB controls for benchmark "
            f"vintages: {duplicates}"
        )

    columns = [
        "benchmark_vintage",
        "reference_date",
        "value",
        "parameter_geography",
        "source_period_start",
        "source_period_end",
        "release_date",
        "source_frequency",
        "month",
        "variable_name",
        "unit",
        "database_version",
        "source_id",
        "provider",
        "source_geography",
        "monthly_method",
        "is_modeled",
        "is_nowcast",
        "is_imputed",
        "is_interpolated",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
        "transformation_id",
        "universe_definition",
        "notes",
        "control_type",
        "compatible_with_county_debt",
        "quality_flag",
    ]
    return result.loc[:, columns].sort_values(
        ["benchmark_vintage", "reference_date"]
    ).reset_index(drop=True)


__all__ = [
    "FHFA_NMDB_PROVIDER",
    "FHFA_NMDB_SOURCE_ID",
    "NMDBFormatError",
    "normalize_nmdb_california_upb",
    "normalize_nmdb_joint_distribution",
]
