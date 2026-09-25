"""Observation and lineage contracts for housing-finance Versions B and C.

Version B is a fully revised retrospective monthly history. Version C is an
append-only real-time monthly vintage identified by an information cutoff.
Both are modeled layers derived from source-faithful Version A anchors, but
this module deliberately contains no Version A write or build behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from src.benchmark.schema import (
    BENCHMARK_COLUMNS,
    BOOLEAN_COLUMNS,
    DATE_COLUMNS,
    NUMERIC_COLUMNS,
)


VersionKey = Literal["B", "C"]

VERSION_B_DATABASE_NAME = "B. Retrospective monthly"
VERSION_C_DATABASE_NAME = "C. Real-time monthly vintage"
VERSION_B_SUFFIX = "_monthly_hist"
VERSION_C_SUFFIX = "_monthly_rt"


@dataclass(frozen=True)
class _VersionContract:
    key: VersionKey
    database_name: str
    suffix: str


_VERSION_CONTRACTS = {
    "B": _VersionContract(
        key="B",
        database_name=VERSION_B_DATABASE_NAME,
        suffix=VERSION_B_SUFFIX,
    ),
    "C": _VersionContract(
        key="C",
        database_name=VERSION_C_DATABASE_NAME,
        suffix=VERSION_C_SUFFIX,
    ),
}

MONTHLY_ADDITIONAL_COLUMNS = [
    "benchmark_variable_id",
    "information_cutoff_date",
    "method_version",
    "matched_owner_value_stock",
    "accounting_debt_variable_id",
]

MONTHLY_FINANCE_COLUMNS = [
    *BENCHMARK_COLUMNS,
    *MONTHLY_ADDITIONAL_COLUMNS,
]

MONTHLY_LINEAGE_COLUMNS = [
    "lineage_id",
    "database_version",
    "information_cutoff_date",
    "county_fips",
    "month",
    "variable_id",
    "estimate_variant",
    "input_role",
    "source_id",
    "source_vintage",
    "source_frequency",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
    "source_geography",
    "parameter_geography",
    "retrieved_at",
    "source_filename",
    "checksum_sha256",
]

MONTHLY_DATE_COLUMNS = [*DATE_COLUMNS, "information_cutoff_date"]
MONTHLY_NUMERIC_COLUMNS = [*NUMERIC_COLUMNS, "matched_owner_value_stock"]
MONTHLY_BOOLEAN_COLUMNS = list(BOOLEAN_COLUMNS)

LINEAGE_DATE_COLUMNS = [
    "information_cutoff_date",
    "month",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
]

_OBSERVATION_REQUIRED_TEXT = (
    "county_fips",
    "variable_id",
    "variable_name",
    "unit",
    "estimate_variant",
    "database_version",
    "source_id",
    "source_frequency",
    "source_geography",
    "parameter_geography",
    "monthly_method",
    "construction_method",
    "benchmark_vintage",
    "universe_definition",
    "transformation_id",
    "lineage_id",
    "benchmark_variable_id",
    "method_version",
)

_LINEAGE_REQUIRED_TEXT = (
    "lineage_id",
    "database_version",
    "county_fips",
    "variable_id",
    "estimate_variant",
    "input_role",
    "source_id",
    "source_vintage",
    "source_frequency",
    "source_geography",
    "parameter_geography",
    "source_filename",
    "checksum_sha256",
)


def _version_contract(version: str) -> _VersionContract:
    text = str(version).strip()
    aliases = {
        "B": "B",
        VERSION_B_DATABASE_NAME.casefold(): "B",
        "C": "C",
        VERSION_C_DATABASE_NAME.casefold(): "C",
    }
    key = aliases.get(text.upper()) or aliases.get(text.casefold())
    if key not in _VERSION_CONTRACTS:
        raise ValueError("version must identify housing-finance Version B or C")
    return _VERSION_CONTRACTS[key]


def _blank(series: pd.Series) -> pd.Series:
    text = series.astype("string")
    return text.isna() | text.str.strip().eq("")


def _coerce_boolean(series: pd.Series) -> pd.Series:
    """Coerce common serialized booleans while preserving invalid values."""

    if isinstance(series.dtype, pd.BooleanDtype):
        return series
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype("boolean")
    normalized = series.astype("string").str.strip().str.casefold()
    mapped = normalized.map(
        {
            "true": True,
            "1": True,
            "yes": True,
            "false": False,
            "0": False,
            "no": False,
        }
    )
    return mapped.astype("boolean")


def _normalize_fips(series: pd.Series, width: int) -> pd.Series:
    result = series.astype("string").str.strip()
    result = result.mask(result.eq(""))
    return result.str.zfill(width)


def _month_end_failures(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return (
        parsed.isna()
        | ~parsed.dt.is_month_end
        | parsed.ne(parsed.dt.normalize())
    )


def _same_with_null(left: pd.Series, right: pd.Series) -> pd.Series:
    return left.eq(right) | (left.isna() & right.isna())


def empty_monthly_finance() -> pd.DataFrame:
    """Return an empty frame with the complete Version B/C observation schema."""

    return pd.DataFrame(columns=MONTHLY_FINANCE_COLUMNS)


def normalize_monthly_finance(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize monthly finance observations without deleting missing rows."""

    result = frame.copy()
    for column in MONTHLY_FINANCE_COLUMNS:
        if column not in result:
            result[column] = None
    result = result[MONTHLY_FINANCE_COLUMNS]

    for column in MONTHLY_DATE_COLUMNS:
        result[column] = pd.to_datetime(
            result[column],
            errors="coerce",
            utc=column == "retrieved_at",
        )
    for column in MONTHLY_NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in MONTHLY_BOOLEAN_COLUMNS:
        result[column] = _coerce_boolean(result[column])

    result["county_fips"] = _normalize_fips(result["county_fips"], 5)
    result["state_fips"] = _normalize_fips(result["state_fips"], 2)
    missing_geo = result["geo_id"].isna() & result["county_fips"].notna()
    result.loc[missing_geo, "geo_id"] = (
        "COUNTY_" + result.loc[missing_geo, "county_fips"]
    )
    missing_id = result["variable_id"].isna()
    result.loc[missing_id, "variable_id"] = result.loc[
        missing_id, "variable_name"
    ]
    missing_name = result["variable_name"].isna()
    result.loc[missing_name, "variable_name"] = result.loc[
        missing_name, "variable_id"
    ]
    return result


def monthly_finance_contract_errors(
    frame: pd.DataFrame,
    *,
    version: VersionKey | str,
) -> list[str]:
    """Return structural errors for one Version B or Version C observation set."""

    contract = _version_contract(version)
    missing = [column for column in MONTHLY_FINANCE_COLUMNS if column not in frame]
    if missing:
        return [f"missing monthly finance columns: {missing}"]

    work = normalize_monthly_finance(frame)
    errors: list[str] = []

    blank_fields = [
        column for column in _OBSERVATION_REQUIRED_TEXT if _blank(work[column]).any()
    ]
    if blank_fields:
        errors.append(
            "required monthly finance text fields cannot be blank: "
            f"{blank_fields}"
        )

    invalid_fips = work["county_fips"].isna() | ~work[
        "county_fips"
    ].str.fullmatch(r"\d{5}", na=False)
    if invalid_fips.any():
        errors.append("county_fips must be a five-character string")

    if _month_end_failures(work["month"]).any():
        errors.append("month must be a nonmissing calendar month-end at midnight")

    invalid_suffix = ~work["variable_id"].astype("string").str.endswith(
        contract.suffix, na=False
    ) | ~work["variable_name"].astype("string").str.endswith(
        contract.suffix, na=False
    )
    if invalid_suffix.any():
        errors.append(
            f"Version {contract.key} variable IDs and names must end in "
            f"{contract.suffix}"
        )
    unequal_names = work["variable_id"].astype("string").ne(
        work["variable_name"].astype("string")
    )
    if unequal_names.any():
        errors.append("variable_id and variable_name must match")

    invalid_benchmark_id = ~work["benchmark_variable_id"].astype(
        "string"
    ).str.endswith("_benchmark", na=False)
    if invalid_benchmark_id.any():
        errors.append("benchmark_variable_id must identify a Version A _benchmark row")

    invalid_version = work["database_version"].astype("string").ne(
        contract.database_name
    )
    if invalid_version.any():
        errors.append(
            f"database_version must equal {contract.database_name!r} for "
            f"Version {contract.key}"
        )

    missing_dates = work[
        [
            "month",
            "source_period_start",
            "source_period_end",
            "reference_date",
            "release_date",
        ]
    ].isna()
    if missing_dates.any().any():
        errors.append(
            "month, source periods, reference_date, and release_date must be populated"
        )
    invalid_period = work["source_period_start"].gt(work["source_period_end"])
    if invalid_period.any():
        errors.append("source_period_start cannot be later than source_period_end")

    incomplete_flags = [
        column
        for column in MONTHLY_BOOLEAN_COLUMNS
        if work[column].isna().any()
    ]
    if incomplete_flags:
        errors.append(f"modeled and missingness flags must be explicit: {incomplete_flags}")

    if (~work["is_modeled"].fillna(False)).any():
        errors.append(f"every Version {contract.key} observation must be modeled")

    if contract.key == "B":
        if work["information_cutoff_date"].notna().any():
            errors.append("Version B information_cutoff_date must be null")
        if work["is_nowcast"].fillna(True).any():
            errors.append("Version B observations cannot be marked as nowcasts")
        duplicate_key = [
            "county_fips",
            "month",
            "variable_id",
            "estimate_variant",
        ]
    else:
        if work["information_cutoff_date"].isna().any():
            errors.append("Version C information_cutoff_date must be populated")
        if work["is_interpolated"].fillna(True).any():
            errors.append("Version C observations cannot use future-endpoint interpolation")
        future_release = work["release_date"].gt(work["information_cutoff_date"])
        if future_release.any():
            errors.append("Version C release_date cannot exceed information_cutoff_date")
        month_period = work["month"].dt.to_period("M")
        cutoff_period = work["information_cutoff_date"].dt.to_period("M")
        if month_period.gt(cutoff_period).fillna(False).any():
            errors.append("Version C cannot contain a month after its cutoff month")
        duplicate_key = [
            "information_cutoff_date",
            "county_fips",
            "month",
            "variable_id",
            "estimate_variant",
        ]

    if work.duplicated(duplicate_key).any():
        errors.append(f"duplicate monthly finance key: {duplicate_key}")
    if work["lineage_id"].duplicated().any():
        errors.append("lineage_id must identify exactly one monthly observation")

    invalid_coverage = work["coverage_rate"].notna() & ~work[
        "coverage_rate"
    ].between(0, 1)
    if invalid_coverage.any():
        errors.append("coverage_rate must be between zero and one")
    invalid_sample = work["sample_n"].notna() & (
        work["sample_n"].lt(0) | work["sample_n"].mod(1).ne(0)
    )
    if invalid_sample.any():
        errors.append("sample_n must be a nonnegative integer")

    complete_bounds = work[
        ["low_estimate", "central_estimate", "high_estimate"]
    ].notna().all(axis=1)
    invalid_bounds = complete_bounds & (
        work["low_estimate"].gt(work["central_estimate"])
        | work["central_estimate"].gt(work["high_estimate"])
    )
    if invalid_bounds.any():
        errors.append("estimates must satisfy low <= central <= high")

    missing_value_without_flag = work["value"].isna() & ~(
        work["is_suppressed"].fillna(False) | ~_blank(work["quality_flag"])
    )
    if missing_value_without_flag.any():
        errors.append("null monthly values require is_suppressed or a quality_flag")
    return errors


def finalize_monthly_finance(
    frame: pd.DataFrame,
    output_path: Path | None = None,
    *,
    version: VersionKey | str,
    validate: bool = True,
) -> pd.DataFrame:
    """Normalize, validate, sort, and optionally persist monthly observations."""

    contract = _version_contract(version)
    result = normalize_monthly_finance(frame)
    sort_columns = [
        "county_fips",
        "month",
        "variable_id",
        "estimate_variant",
    ]
    if contract.key == "C":
        sort_columns.insert(0, "information_cutoff_date")
    result = result.sort_values(sort_columns, na_position="last").reset_index(
        drop=True
    )
    if validate:
        errors = monthly_finance_contract_errors(result, version=contract.key)
        if errors:
            raise ValueError("; ".join(errors))
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(output_path, index=False)
    return result


def empty_monthly_lineage() -> pd.DataFrame:
    """Return an empty frame with the normalized per-input lineage schema."""

    return pd.DataFrame(columns=MONTHLY_LINEAGE_COLUMNS)


def normalize_monthly_lineage(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize one-row-per-input lineage without collapsing source records."""

    result = frame.copy()
    for column in MONTHLY_LINEAGE_COLUMNS:
        if column not in result:
            result[column] = None
    result = result[MONTHLY_LINEAGE_COLUMNS]
    for column in LINEAGE_DATE_COLUMNS:
        result[column] = pd.to_datetime(result[column], errors="coerce")
    result["retrieved_at"] = pd.to_datetime(
        result["retrieved_at"], errors="coerce", utc=True
    )
    result["county_fips"] = _normalize_fips(result["county_fips"], 5)
    result["checksum_sha256"] = (
        result["checksum_sha256"].astype("string").str.strip().str.lower()
    )
    return result


def monthly_lineage_contract_errors(
    lineage: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    version: VersionKey | str,
) -> list[str]:
    """Return errors for normalized per-input lineage and observation links."""

    contract = _version_contract(version)
    missing = [column for column in MONTHLY_LINEAGE_COLUMNS if column not in lineage]
    if missing:
        return [f"missing monthly lineage columns: {missing}"]

    observation_errors = monthly_finance_contract_errors(
        observations,
        version=contract.key,
    )
    if observation_errors:
        return [f"observations: {error}" for error in observation_errors]

    work = normalize_monthly_lineage(lineage)
    observed = normalize_monthly_finance(observations)
    errors: list[str] = []

    blank_fields = [
        column for column in _LINEAGE_REQUIRED_TEXT if _blank(work[column]).any()
    ]
    if blank_fields:
        errors.append(
            "required monthly lineage text fields cannot be blank: "
            f"{blank_fields}"
        )

    invalid_version = work["database_version"].astype("string").ne(
        contract.database_name
    )
    if invalid_version.any():
        errors.append(
            f"lineage database_version must equal {contract.database_name!r}"
        )
    invalid_suffix = ~work["variable_id"].astype("string").str.endswith(
        contract.suffix, na=False
    )
    if invalid_suffix.any():
        errors.append(f"lineage variable_id must end in {contract.suffix}")
    invalid_fips = work["county_fips"].isna() | ~work[
        "county_fips"
    ].str.fullmatch(r"\d{5}", na=False)
    if invalid_fips.any():
        errors.append("lineage county_fips must be a five-character string")
    if _month_end_failures(work["month"]).any():
        errors.append("lineage month must be a calendar month-end at midnight")

    required_dates = [
        "month",
        "source_period_start",
        "source_period_end",
        "reference_date",
        "release_date",
        "retrieved_at",
    ]
    if work[required_dates].isna().any().any():
        errors.append(
            "lineage source periods, reference, release, and retrieval dates "
            "must be populated"
        )
    invalid_period = work["source_period_start"].gt(work["source_period_end"])
    invalid_reference = work["reference_date"].lt(
        work["source_period_start"]
    ) | work["reference_date"].gt(work["source_period_end"])
    if invalid_period.any():
        errors.append("lineage source period start cannot exceed its end")
    if invalid_reference.any():
        errors.append("lineage reference_date must lie inside its source period")

    release_dates = work["release_date"].dt.date
    retrieval_dates = work["retrieved_at"].dt.date
    if release_dates.gt(retrieval_dates).fillna(False).any():
        errors.append("lineage release_date cannot be later than retrieved_at")

    invalid_checksum = ~work["checksum_sha256"].str.fullmatch(
        r"[0-9a-f]{64}", na=False
    )
    if invalid_checksum.any():
        errors.append("lineage checksum_sha256 must be a 64-character hex digest")

    if contract.key == "B":
        if work["information_cutoff_date"].notna().any():
            errors.append("Version B lineage information_cutoff_date must be null")
    else:
        if work["information_cutoff_date"].isna().any():
            errors.append("Version C lineage information_cutoff_date must be populated")
        future_release = work["release_date"].gt(work["information_cutoff_date"])
        if future_release.any():
            errors.append("Version C lineage release_date cannot exceed its cutoff")
        cutoff_dates = work["information_cutoff_date"].dt.date
        if retrieval_dates.gt(cutoff_dates).fillna(False).any():
            errors.append("Version C lineage retrieved_at cannot exceed its cutoff")

    lineage_key = [
        "lineage_id",
        "input_role",
        "source_id",
        "source_vintage",
        "reference_date",
        "source_filename",
        "checksum_sha256",
    ]
    if work.duplicated(lineage_key).any():
        errors.append(f"duplicate monthly lineage input key: {lineage_key}")

    observed_ids = set(observed["lineage_id"].dropna().astype(str))
    lineage_ids = set(work["lineage_id"].dropna().astype(str))
    missing_ids = sorted(observed_ids - lineage_ids)
    orphan_ids = sorted(lineage_ids - observed_ids)
    if missing_ids:
        errors.append(f"observations without input lineage: {missing_ids}")
    if orphan_ids:
        errors.append(f"orphan monthly lineage IDs: {orphan_ids}")

    context_columns = [
        "database_version",
        "information_cutoff_date",
        "county_fips",
        "month",
        "variable_id",
        "estimate_variant",
    ]
    observation_context = observed[["lineage_id", *context_columns]].rename(
        columns={column: f"{column}_observation" for column in context_columns}
    )
    joined = work.merge(
        observation_context,
        on="lineage_id",
        how="inner",
        validate="many_to_one",
    )
    inconsistent_columns: list[str] = []
    for column in context_columns:
        if not _same_with_null(
            joined[column], joined[f"{column}_observation"]
        ).all():
            inconsistent_columns.append(column)
    if inconsistent_columns:
        errors.append(
            "lineage context does not match its observation: "
            f"{inconsistent_columns}"
        )
    return errors


__all__ = [
    "MONTHLY_FINANCE_COLUMNS",
    "MONTHLY_LINEAGE_COLUMNS",
    "VERSION_B_DATABASE_NAME",
    "VERSION_B_SUFFIX",
    "VERSION_C_DATABASE_NAME",
    "VERSION_C_SUFFIX",
    "empty_monthly_finance",
    "empty_monthly_lineage",
    "finalize_monthly_finance",
    "monthly_finance_contract_errors",
    "monthly_lineage_contract_errors",
    "normalize_monthly_finance",
    "normalize_monthly_lineage",
]
