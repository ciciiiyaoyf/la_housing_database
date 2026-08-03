"""Native-frequency observation contract for Version A benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


BENCHMARK_COLUMNS = [
    "county_fips",
    "state_fips",
    "geo_id",
    "month",
    "value",
    "variable_name",
    "variable_id",
    "unit",
    "estimate_variant",
    "reported_value",
    "margin_of_error",
    "database_version",
    "source_id",
    "source_frequency",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
    "source_release_dates",
    "source_geography",
    "parameter_geography",
    "monthly_method",
    "construction_method",
    "payment_frequency",
    "is_modeled",
    "is_nowcast",
    "is_imputed",
    "is_interpolated",
    "is_suppressed",
    "benchmark_vintage",
    "sample_n",
    "coverage_rate",
    "low_estimate",
    "central_estimate",
    "high_estimate",
    "universe_definition",
    "notes",
    "source_vintage",
    "retrieved_at",
    "source_filename",
    "checksum_sha256",
    "transformation_id",
    "lineage_id",
    "quality_flag",
]

DATE_COLUMNS = [
    "month",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
    "retrieved_at",
]

BOOLEAN_COLUMNS = [
    "is_modeled",
    "is_nowcast",
    "is_imputed",
    "is_interpolated",
    "is_suppressed",
]

NUMERIC_COLUMNS = [
    "value",
    "reported_value",
    "margin_of_error",
    "sample_n",
    "coverage_rate",
    "low_estimate",
    "central_estimate",
    "high_estimate",
]


def empty_benchmark() -> pd.DataFrame:
    """Return an empty frame with the complete benchmark contract."""
    return pd.DataFrame(columns=BENCHMARK_COLUMNS)


def normalize_benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a benchmark frame without deleting published missing rows."""
    result = frame.copy()
    for column in BENCHMARK_COLUMNS:
        if column not in result:
            result[column] = None
    result = result[BENCHMARK_COLUMNS]

    for column in DATE_COLUMNS:
        result[column] = pd.to_datetime(
            result[column], errors="coerce", utc=column == "retrieved_at"
        )
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in BOOLEAN_COLUMNS:
        result[column] = result[column].astype("boolean")

    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result["state_fips"] = result["state_fips"].astype("string").str.zfill(2)
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


def benchmark_contract_errors(frame: pd.DataFrame) -> list[str]:
    """Return structural errors for the source-faithful benchmark layer."""
    errors: list[str] = []
    missing = [column for column in BENCHMARK_COLUMNS if column not in frame]
    if missing:
        return [f"missing benchmark columns: {missing}"]

    normalized = normalize_benchmark(frame)
    if normalized["county_fips"].isna().any() or not normalized[
        "county_fips"
    ].str.fullmatch(r"\d{5}", na=False).all():
        errors.append("county_fips must be a five-character string")
    if not normalized["variable_name"].str.endswith(
        "_benchmark", na=False
    ).all():
        errors.append("every Version A variable must end in _benchmark")
    versions = (
        normalized["database_version"]
        .astype("string")
        .str.lower()
        .str.replace(r"[^a-z0-9]", "", regex=True)
    )
    if not versions.isin(
        {"a", "benchmark", "abenchmark", "versiona", "versionabenchmark"}
    ).all():
        errors.append("database_version must identify Version A benchmark")
    for column in (
        "is_modeled",
        "is_nowcast",
        "is_imputed",
        "is_interpolated",
    ):
        if normalized[column].isna().any() or normalized[column].fillna(True).any():
            errors.append(f"{column} must be explicitly false in Version A")
    invalid_methods = normalized["monthly_method"].astype("string").str.contains(
        "interpol|nowcast|distribut|carry.?forward",
        case=False,
        regex=True,
        na=False,
    )
    if invalid_methods.any():
        errors.append("Version A monthly_method cannot create monthly observations")
    if normalized["release_date"].isna().any():
        errors.append("release_date must be populated for every Version A row")
    missing_release_audit = (
        normalized["source_release_dates"].isna()
        | normalized["source_release_dates"].astype("string").str.strip().eq("")
    )
    if missing_release_audit.any():
        errors.append(
            "source_release_dates must identify every row's source releases"
        )
    provenance_text = (
        "source_id",
        "source_vintage",
        "source_filename",
        "checksum_sha256",
        "transformation_id",
        "lineage_id",
    )
    for column in provenance_text:
        missing_provenance = (
            normalized[column].isna()
            | normalized[column].astype("string").str.strip().eq("")
        )
        if missing_provenance.any():
            errors.append(f"{column} must be populated for every Version A row")
    if normalized["retrieved_at"].isna().any():
        errors.append("retrieved_at must be populated for every Version A row")
    comparable_release_timing = (
        normalized["release_date"].notna()
        & normalized["retrieved_at"].notna()
    )
    invalid_release_timing = comparable_release_timing & (
        normalized["release_date"].dt.date
        > normalized["retrieved_at"].dt.date
    )
    if invalid_release_timing.any():
        errors.append("release_date cannot be later than retrieved_at")

    invalid_release_lineage = False
    for _, row in normalized.iterrows():
        source_ids = [
            token.strip()
            for token in str(row["source_id"]).split("|")
            if token.strip() and token.strip() != "<NA>"
        ]
        audit_ids: list[str] = []
        audit_values_valid = True
        for token in str(row["source_release_dates"]).split("|"):
            audit_id, separator, raw_value = token.partition("=")
            audit_id = audit_id.strip()
            raw_value = raw_value.strip()
            if not separator or not audit_id:
                audit_values_valid = False
                continue
            audit_ids.append(audit_id)
            if raw_value.casefold() != "unavailable" and pd.isna(
                pd.to_datetime(raw_value, errors="coerce")
            ):
                audit_values_valid = False
        if (
            not source_ids
            or len(source_ids) != len(set(source_ids))
            or len(audit_ids) != len(set(audit_ids))
            or set(source_ids) != set(audit_ids)
            or not audit_values_valid
        ):
            invalid_release_lineage = True
            break
    if invalid_release_lineage:
        errors.append(
            "source_release_dates must contain exactly one valid date or "
            "'unavailable' entry for every source_id"
        )
    invalid_coverage = normalized["coverage_rate"].notna() & ~normalized[
        "coverage_rate"
    ].between(0, 1)
    if invalid_coverage.any():
        errors.append("coverage_rate must be between zero and one")
    invalid_sample = normalized["sample_n"].notna() & (
        normalized["sample_n"].lt(0)
        | normalized["sample_n"].mod(1).ne(0)
    )
    if invalid_sample.any():
        errors.append("sample_n must be a nonnegative integer")
    complete_bounds = normalized[
        ["low_estimate", "central_estimate", "high_estimate"]
    ].notna().all(axis=1)
    invalid_bounds = complete_bounds & (
        normalized["low_estimate"].gt(normalized["central_estimate"])
        | normalized["central_estimate"].gt(normalized["high_estimate"])
    )
    if invalid_bounds.any():
        errors.append("estimates must satisfy low <= central <= high")
    missing_value_without_flag = normalized["value"].isna() & ~(
        normalized["is_suppressed"].fillna(False)
        | normalized["quality_flag"].notna()
    )
    if missing_value_without_flag.any():
        errors.append(
            "null benchmark values require is_suppressed or a quality_flag"
        )
    duplicate_key = [
        "county_fips",
        "reference_date",
        "variable_name",
        "estimate_variant",
        "benchmark_vintage",
    ]
    if normalized.duplicated(duplicate_key).any():
        errors.append(f"duplicate benchmark key: {duplicate_key}")
    return errors


def finalize_benchmark(
    frame: pd.DataFrame,
    output_path: Path | None = None,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Normalize, validate, and optionally persist benchmark observations.

    Unlike the monthly clean contract, this function intentionally preserves
    null-valued rows that document a published suppression, unavailable
    calibration, insufficient sample, or missing public parameter.
    """
    result = normalize_benchmark(frame)
    result = result.dropna(
        subset=["county_fips", "variable_name", "benchmark_vintage"]
    )
    result = result.sort_values(
        ["county_fips", "reference_date", "variable_name", "estimate_variant"],
        na_position="last",
    ).reset_index(drop=True)
    if validate:
        errors = benchmark_contract_errors(result)
        if errors:
            raise ValueError("; ".join(errors))
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(output_path, index=False)
    return result


def benchmark_row(**values: Any) -> dict[str, Any]:
    """Build one complete benchmark row while keeping unspecified fields null."""
    row = dict.fromkeys(BENCHMARK_COLUMNS)
    row.update(values)
    return row
