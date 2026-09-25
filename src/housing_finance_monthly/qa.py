"""Structural, accounting, and plausibility QA for finance Versions B and C."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.housing_finance_monthly.schema import (
    MONTHLY_LINEAGE_COLUMNS,
    VERSION_B_DATABASE_NAME,
    VERSION_B_SUFFIX,
    VERSION_C_DATABASE_NAME,
    VERSION_C_SUFFIX,
    monthly_finance_contract_errors,
    monthly_lineage_contract_errors,
    normalize_monthly_finance,
    normalize_monthly_lineage,
)


CALIFORNIA_COUNTY_COUNT = 58
MAX_FAILURE_ROWS = 1_000


@dataclass(frozen=True)
class MonthlyFinanceCheck:
    check_id: str
    severity: str
    status: str
    failure_count: int
    detail: str


def _version_settings(version: str) -> tuple[str, str, str]:
    text = str(version).strip()
    normalized = text.upper()
    if normalized == "B" or text.casefold() == VERSION_B_DATABASE_NAME.casefold():
        return "B", VERSION_B_DATABASE_NAME, VERSION_B_SUFFIX
    if normalized == "C" or text.casefold() == VERSION_C_DATABASE_NAME.casefold():
        return "C", VERSION_C_DATABASE_NAME, VERSION_C_SUFFIX
    raise ValueError("version must identify housing-finance Version B or C")


def _result(
    check_id: str,
    severity: str,
    failures: pd.DataFrame,
    detail: str,
) -> MonthlyFinanceCheck:
    count = len(failures)
    status = "fail" if count and severity == "error" else "warn" if count else "pass"
    return MonthlyFinanceCheck(check_id, severity, status, count, detail)


def _skipped(check_id: str, detail: str) -> MonthlyFinanceCheck:
    return MonthlyFinanceCheck(check_id, "error", "skip", 0, detail)


def _failure_frame(reason: str, **values: Any) -> pd.DataFrame:
    return pd.DataFrame([{"reason": reason, **values}])


def _read_frame(source: pd.DataFrame | str | Path) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source.copy()
    path = Path(source)
    if path.suffix.casefold() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str)


def _append_reason(
    frames: list[pd.DataFrame],
    frame: pd.DataFrame,
    reason: str,
) -> None:
    if frame.empty:
        return
    output = frame.copy()
    output.insert(0, "reason", reason)
    frames.append(output)


def _california_retention_failures(
    observations: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    expected = set(CALIFORNIA_COUNTY_FIPS)
    group_columns = ["month", "variable_id", "estimate_variant"]
    if version_key == "C":
        group_columns.insert(0, "information_cutoff_date")
    if observations.empty:
        return _failure_frame("no_monthly_finance_observations")

    rows: list[dict[str, Any]] = []
    for group_key, group in observations.groupby(
        group_columns,
        dropna=False,
        sort=True,
    ):
        values = group_key if isinstance(group_key, tuple) else (group_key,)
        actual = set(group["county_fips"].dropna().astype(str))
        if actual == expected and len(group) == CALIFORNIA_COUNTY_COUNT:
            continue
        row = dict(zip(group_columns, values, strict=True))
        row.update(
            {
                "reason": "incomplete_california_county_set",
                "county_row_count": len(group),
                "unique_county_count": len(actual),
                "expected_county_count": CALIFORNIA_COUNTY_COUNT,
                "missing_counties": "|".join(sorted(expected - actual)),
                "unexpected_counties": "|".join(sorted(actual - expected)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _dictionary_coverage_failures(
    observations: pd.DataFrame,
    dictionary: pd.DataFrame,
    *,
    suffix: str,
    database_name: str,
) -> pd.DataFrame:
    if "variable_id" not in dictionary:
        return _failure_frame("dictionary_missing_variable_id_column")
    configured = dictionary.copy()
    configured["variable_id"] = configured["variable_id"].astype("string")
    if "database_version" in configured:
        version_rows = configured["database_version"].astype("string").eq(
            database_name
        )
        if version_rows.any():
            configured = configured.loc[version_rows]
        else:
            configured = configured.loc[
                configured["variable_id"].str.endswith(suffix, na=False)
            ]
    else:
        configured = configured.loc[
            configured["variable_id"].str.endswith(suffix, na=False)
        ]

    rows: list[dict[str, Any]] = []
    duplicates = configured.loc[
        configured["variable_id"].duplicated(keep=False), "variable_id"
    ]
    rows.extend(
        {"reason": "duplicate_dictionary_variable_id", "variable_id": value}
        for value in sorted(set(duplicates.dropna().astype(str)))
    )
    actual = set(observations["variable_id"].dropna().astype(str))
    expected = set(configured["variable_id"].dropna().astype(str))
    rows.extend(
        {"reason": "missing_dictionary_variable_id", "variable_id": value}
        for value in sorted(actual - expected)
    )
    rows.extend(
        {"reason": "configured_variable_not_emitted", "variable_id": value}
        for value in sorted(expected - actual)
    )
    if not expected:
        rows.append(
            {
                "reason": "version_dictionary_has_no_variables",
                "variable_id": None,
            }
        )
    return pd.DataFrame(rows)


def _dictionary_row_contract_failures(
    observations: pd.DataFrame,
    dictionary: pd.DataFrame,
    *,
    suffix: str,
    database_name: str,
) -> pd.DataFrame:
    required = {
        "variable_id",
        "unit",
        "transformation_id",
        "benchmark_variable_id",
    }
    missing_columns = sorted(required - set(dictionary.columns))
    if missing_columns:
        return _failure_frame(
            "dictionary_missing_contract_columns",
            columns="|".join(missing_columns),
        )
    configured = dictionary.copy()
    configured["variable_id"] = configured["variable_id"].astype("string")
    if "database_version" in configured:
        exact_version = configured["database_version"].astype("string").eq(
            database_name
        )
        configured = (
            configured.loc[exact_version]
            if exact_version.any()
            else configured.loc[
                configured["variable_id"].str.endswith(suffix, na=False)
            ]
        )
    else:
        configured = configured.loc[
            configured["variable_id"].str.endswith(suffix, na=False)
        ]
    configured = configured.drop_duplicates("variable_id", keep=False)
    if configured.empty:
        return pd.DataFrame(columns=["reason"])

    expected = configured.set_index("variable_id")
    rows: list[dict[str, Any]] = []
    for variable_id, group in observations.groupby("variable_id", sort=True):
        if variable_id not in expected.index:
            continue
        contract = expected.loc[variable_id]
        for observation_column, dictionary_column in (
            ("unit", "unit"),
            ("transformation_id", "transformation_id"),
            ("benchmark_variable_id", "benchmark_variable_id"),
        ):
            actual = {
                str(value).strip()
                for value in group[observation_column].dropna()
                if str(value).strip()
            }
            required_value = str(contract[dictionary_column]).strip()
            if actual == {required_value}:
                continue
            rows.append(
                {
                    "reason": f"dictionary_{dictionary_column}_mismatch",
                    "variable_id": variable_id,
                    "expected": required_value,
                    "actual": "|".join(sorted(actual)),
                }
            )
    return pd.DataFrame(rows)


def _catalog_coverage_failures(
    observations: pd.DataFrame,
    catalog: pd.DataFrame,
    *,
    database_name: str,
) -> pd.DataFrame:
    if "transformation_id" not in catalog:
        return _failure_frame("catalog_missing_transformation_id_column")
    configured = catalog.copy()
    configured["transformation_id"] = configured["transformation_id"].astype(
        "string"
    )
    if "database_version" in configured:
        version_rows = configured["database_version"].astype("string").eq(
            database_name
        )
        if version_rows.any():
            configured = configured.loc[version_rows]

    rows: list[dict[str, Any]] = []
    duplicates = configured.loc[
        configured["transformation_id"].duplicated(keep=False),
        "transformation_id",
    ]
    rows.extend(
        {
            "reason": "duplicate_catalog_transformation_id",
            "transformation_id": value,
        }
        for value in sorted(set(duplicates.dropna().astype(str)))
    )
    actual = set(observations["transformation_id"].dropna().astype(str))
    expected = set(configured["transformation_id"].dropna().astype(str))
    rows.extend(
        {
            "reason": "missing_catalog_transformation_id",
            "transformation_id": value,
        }
        for value in sorted(actual - expected)
    )
    rows.extend(
        {
            "reason": "catalog_transformation_not_emitted",
            "transformation_id": value,
        }
        for value in sorted(expected - actual)
    )
    if not expected:
        rows.append(
            {
                "reason": "version_catalog_has_no_transformations",
                "transformation_id": None,
            }
        )
    return pd.DataFrame(rows)


def _version_b_flag_failures(observations: pd.DataFrame) -> pd.DataFrame:
    failed = observations.loc[
        ~observations["is_modeled"].fillna(False)
        | observations["is_nowcast"].fillna(True)
        | observations["information_cutoff_date"].notna(),
        [
            "county_fips",
            "month",
            "variable_id",
            "is_modeled",
            "is_nowcast",
            "information_cutoff_date",
        ],
    ].copy()
    if not failed.empty:
        failed.insert(0, "reason", "invalid_version_b_modeled_flags")
    return failed


def _source_release_timing_failures(
    observations: pd.DataFrame,
    lineage: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    observation_columns = [
        "lineage_id",
        "county_fips",
        "variable_id",
        "source_period_end",
        "release_date",
    ]
    observation_failures = observations.loc[
        observations["release_date"].lt(observations["source_period_end"]),
        observation_columns,
    ]
    _append_reason(
        frames,
        observation_failures,
        "observation_release_precedes_source_period_end",
    )
    lineage_columns = [
        "lineage_id",
        "source_id",
        "source_vintage",
        "source_period_end",
        "release_date",
    ]
    lineage_failures = lineage.loc[
        lineage["release_date"].lt(lineage["source_period_end"]),
        lineage_columns,
    ]
    _append_reason(
        frames,
        lineage_failures,
        "lineage_release_precedes_source_period_end",
    )
    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame(columns=["reason"])
    )


def _version_c_information_failures(
    observations: pd.DataFrame,
    lineage: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    observation_columns = [
        "lineage_id",
        "county_fips",
        "month",
        "variable_id",
        "release_date",
        "information_cutoff_date",
        "is_interpolated",
    ]
    missing_cutoff = observations.loc[
        observations["information_cutoff_date"].isna(), observation_columns
    ]
    _append_reason(frames, missing_cutoff, "missing_information_cutoff")
    interpolated = observations.loc[
        observations["is_interpolated"].fillna(True), observation_columns
    ]
    _append_reason(frames, interpolated, "future_endpoint_interpolation")
    future_release = observations.loc[
        observations["release_date"].gt(
            observations["information_cutoff_date"]
        ),
        observation_columns,
    ]
    _append_reason(frames, future_release, "observation_release_after_cutoff")
    month_period = observations["month"].dt.to_period("M")
    cutoff_period = observations["information_cutoff_date"].dt.to_period("M")
    future_month = observations.loc[
        month_period.gt(cutoff_period).fillna(False), observation_columns
    ]
    _append_reason(frames, future_month, "observation_month_after_cutoff_month")
    future_observation_period = observations.loc[
        observations["source_period_end"].gt(
            observations["information_cutoff_date"]
        ),
        [
            *observation_columns,
            "source_period_end",
            "reference_date",
        ],
    ]
    _append_reason(
        frames,
        future_observation_period,
        "observation_source_period_after_cutoff",
    )
    future_observation_reference = observations.loc[
        observations["reference_date"].gt(
            observations["information_cutoff_date"]
        ),
        [
            *observation_columns,
            "source_period_end",
            "reference_date",
        ],
    ]
    _append_reason(
        frames,
        future_observation_reference,
        "observation_reference_date_after_cutoff",
    )

    normalized_lineage = normalize_monthly_lineage(lineage)
    lineage_columns = [
        "lineage_id",
        "source_id",
        "source_vintage",
        "release_date",
        "retrieved_at",
        "information_cutoff_date",
        "source_period_end",
        "reference_date",
    ]
    lineage_future_release = normalized_lineage.loc[
        normalized_lineage["release_date"].gt(
            normalized_lineage["information_cutoff_date"]
        ),
        lineage_columns,
    ]
    _append_reason(
        frames,
        lineage_future_release,
        "lineage_release_after_cutoff",
    )
    retrieval_dates = normalized_lineage["retrieved_at"].dt.date
    cutoff_dates = normalized_lineage["information_cutoff_date"].dt.date
    lineage_future_retrieval = normalized_lineage.loc[
        retrieval_dates.gt(cutoff_dates).fillna(False), lineage_columns
    ]
    _append_reason(
        frames,
        lineage_future_retrieval,
        "lineage_retrieval_after_cutoff",
    )
    lineage_future_period = normalized_lineage.loc[
        normalized_lineage["source_period_end"].gt(
            normalized_lineage["information_cutoff_date"]
        ),
        lineage_columns,
    ]
    _append_reason(
        frames,
        lineage_future_period,
        "lineage_source_period_after_cutoff",
    )
    lineage_future_reference = normalized_lineage.loc[
        normalized_lineage["reference_date"].gt(
            normalized_lineage["information_cutoff_date"]
        ),
        lineage_columns,
    ]
    _append_reason(
        frames,
        lineage_future_reference,
        "lineage_reference_date_after_cutoff",
    )
    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame(columns=["reason"])
    )


def _estimate_bound_failures(observations: pd.DataFrame) -> pd.DataFrame:
    complete = observations[
        ["low_estimate", "central_estimate", "high_estimate"]
    ].notna().all(axis=1)
    failed = observations.loc[
        complete
        & (
            observations["low_estimate"].gt(observations["central_estimate"])
            | observations["central_estimate"].gt(
                observations["high_estimate"]
            )
        ),
        [
            "county_fips",
            "month",
            "variable_id",
            "estimate_variant",
            "low_estimate",
            "central_estimate",
            "high_estimate",
        ],
    ].copy()
    if not failed.empty:
        failed.insert(0, "reason", "estimate_order_violation")
    return failed


def _economic_plausibility_failures(observations: pd.DataFrame) -> pd.DataFrame:
    values = pd.to_numeric(observations["value"], errors="coerce")
    names = observations["variable_id"].astype("string").str.casefold()
    rate_like = names.str.contains(
        r"rate|share|ratio|ltv|yield",
        regex=True,
        na=False,
    )
    implausible_rate = rate_like & values.notna() & ~values.between(-0.5, 1.5)
    nonnegative_like = names.str.contains(
        r"balance|debt|payment|flow",
        regex=True,
        na=False,
    ) & ~names.str.contains("equity", na=False)
    negative_level = nonnegative_like & values.lt(0)
    failed = observations.loc[
        implausible_rate | negative_level,
        ["county_fips", "month", "variable_id", "value"],
    ].copy()
    if failed.empty:
        return failed
    failed.insert(0, "reason", "")
    failed.loc[implausible_rate.loc[failed.index], "reason"] = (
        "rate_or_ratio_outside_broad_review_range"
    )
    failed.loc[negative_level.loc[failed.index], "reason"] = (
        "negative_non_equity_finance_level"
    )
    return failed


def _normalize_benchmark(benchmark: pd.DataFrame) -> pd.DataFrame:
    result = benchmark.copy()
    if "variable_id" not in result and "variable_name" in result:
        result["variable_id"] = result["variable_name"]
    required = [
        "county_fips",
        "variable_id",
        "benchmark_vintage",
        "value",
        "estimate_variant",
        "low_estimate",
        "central_estimate",
        "high_estimate",
        "source_period_start",
        "source_period_end",
        "reference_date",
        "release_date",
    ]
    for column in required:
        if column not in result:
            result[column] = None
    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result["variable_id"] = result["variable_id"].astype("string")
    result["benchmark_vintage"] = result["benchmark_vintage"].astype("string")
    result["estimate_variant"] = (
        result["estimate_variant"].astype("string").fillna("central")
    )
    for column in (
        "value",
        "low_estimate",
        "central_estimate",
        "high_estimate",
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in (
        "source_period_start",
        "source_period_end",
        "reference_date",
        "release_date",
    ):
        result[column] = pd.to_datetime(result[column], errors="coerce")
    return result[required]


def _benchmark_join(
    observations: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized = _normalize_benchmark(benchmark)
    key = [
        "county_fips",
        "variable_id",
        "benchmark_vintage",
    ]
    duplicates = normalized.loc[normalized.duplicated(key, keep=False)].copy()
    usable = normalized.drop_duplicates(key, keep="first").rename(
        columns={
            "variable_id": "benchmark_variable_id",
            "estimate_variant": "benchmark_estimate_variant",
            "value": "benchmark_value",
            "low_estimate": "benchmark_low_estimate",
            "central_estimate": "benchmark_central_estimate",
            "high_estimate": "benchmark_high_estimate",
            "source_period_start": "benchmark_source_period_start",
            "source_period_end": "benchmark_source_period_end",
            "reference_date": "benchmark_reference_date",
            "release_date": "benchmark_release_date",
        }
    )
    joined = observations.merge(
        usable,
        on=[
            "county_fips",
            "benchmark_variable_id",
            "benchmark_vintage",
        ],
        how="left",
        indicator="benchmark_merge_status",
    )
    return joined, duplicates


def _benchmark_anchor_failures(
    joined: pd.DataFrame,
    duplicates: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if not duplicates.empty:
        _append_reason(
            frames,
            duplicates,
            "duplicate_benchmark_anchor_key",
        )
    missing = joined.loc[
        joined["benchmark_merge_status"].ne("both"),
        [
            "county_fips",
            "month",
            "variable_id",
            "benchmark_variable_id",
            "benchmark_vintage",
            "estimate_variant",
        ],
    ]
    _append_reason(frames, missing, "monthly_observation_without_benchmark_anchor")
    if version_key == "C":
        lookahead = joined.loc[
            joined["benchmark_release_date"].gt(
                joined["information_cutoff_date"]
            ),
            [
                "county_fips",
                "month",
                "variable_id",
                "benchmark_variable_id",
                "benchmark_release_date",
                "information_cutoff_date",
            ],
        ]
        _append_reason(frames, lookahead, "benchmark_anchor_released_after_cutoff")
        future_period = joined.loc[
            joined["benchmark_source_period_end"].gt(
                joined["information_cutoff_date"]
            ),
            [
                "county_fips",
                "month",
                "variable_id",
                "benchmark_variable_id",
                "benchmark_source_period_end",
                "information_cutoff_date",
            ],
        ]
        _append_reason(
            frames,
            future_period,
            "benchmark_anchor_period_after_cutoff",
        )
        future_reference = joined.loc[
            joined["benchmark_reference_date"].gt(
                joined["information_cutoff_date"]
            ),
            [
                "county_fips",
                "month",
                "variable_id",
                "benchmark_variable_id",
                "benchmark_reference_date",
                "information_cutoff_date",
            ],
        ]
        _append_reason(
            frames,
            future_reference,
            "benchmark_anchor_reference_after_cutoff",
        )
    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame(columns=["reason"])
    )


def _benchmark_bound_presence_failures(joined: pd.DataFrame) -> pd.DataFrame:
    anchor_has_bounds = joined[
        [
            "benchmark_low_estimate",
            "benchmark_central_estimate",
            "benchmark_high_estimate",
        ]
    ].notna().all(axis=1)
    output_has_value = pd.to_numeric(joined["value"], errors="coerce").notna()
    output_has_bounds = joined[
        ["low_estimate", "central_estimate", "high_estimate"]
    ].notna().all(axis=1)
    failed = joined.loc[
        joined["benchmark_merge_status"].eq("both")
        & anchor_has_bounds
        & output_has_value
        & ~output_has_bounds,
        [
            "county_fips",
            "month",
            "variable_id",
            "benchmark_variable_id",
            "benchmark_vintage",
            "low_estimate",
            "central_estimate",
            "high_estimate",
            "benchmark_low_estimate",
            "benchmark_central_estimate",
            "benchmark_high_estimate",
        ],
    ].copy()
    if not failed.empty:
        failed.insert(0, "reason", "eligible_benchmark_bounds_not_propagated")
    return failed


def _close(left: float, right: float) -> bool:
    return bool(
        np.isclose(
            left,
            right,
            rtol=1e-10,
            atol=0.01,
            equal_nan=False,
        )
    )


def _identity_group_columns(version_key: str) -> list[str]:
    columns = [
        "county_fips",
        "variable_id",
        "benchmark_variable_id",
        "benchmark_vintage",
        "estimate_variant",
        "calendar_year",
    ]
    if version_key == "C":
        columns.insert(0, "information_cutoff_date")
    return columns


def _group_is_due(
    group: pd.DataFrame,
    *,
    version_key: str,
    anchor_year: int,
) -> bool:
    if version_key == "B":
        return True
    cutoff = group["information_cutoff_date"].iloc[0]
    return pd.notna(cutoff) and pd.Timestamp(cutoff) >= pd.Timestamp(
        year=anchor_year,
        month=12,
        day=31,
    )


def _annual_flow_identity_failures(
    joined: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    transformations = joined["transformation_id"].astype("string")
    selected = joined.loc[
        joined["benchmark_variable_id"]
        .astype("string")
        .str.contains("aggregate_downpayment_flow", case=False, na=False)
        & transformations.str.contains("proxy", case=False, na=False)
        & ~transformations.str.contains(
            "flow_unavailable",
            case=False,
            na=False,
        )
        & joined["benchmark_merge_status"].eq("both")
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["reason"])
    selected["calendar_year"] = selected["month"].dt.year
    rows: list[dict[str, Any]] = []
    group_columns = _identity_group_columns(version_key)
    for group_key, group in selected.groupby(
        group_columns,
        dropna=False,
        sort=True,
    ):
        anchor_end = group["benchmark_source_period_end"].iloc[0]
        if pd.isna(anchor_end):
            rows.append(
                {
                    "reason": "annual_flow_anchor_period_missing",
                    "group_key": str(group_key),
                }
            )
            continue
        anchor_year = int(pd.Timestamp(anchor_end).year)
        calendar_year = int(group["calendar_year"].iloc[0])
        if calendar_year != anchor_year:
            continue
        if not _group_is_due(
            group,
            version_key=version_key,
            anchor_year=anchor_year,
        ):
            continue
        actual = pd.to_numeric(group["value"], errors="coerce")
        expected = pd.to_numeric(
            group["benchmark_value"], errors="coerce"
        ).iloc[0]
        unique_months = group["month"].nunique()
        actual_sum = (
            math.fsum(float(value) for value in actual)
            if actual.notna().all()
            else np.nan
        )
        null_identity = (
            unique_months == 12
            and actual.isna().all()
            and pd.isna(expected)
        )
        missing_weight_identity = (
            unique_months == 12
            and actual.isna().all()
            and group["quality_flag"]
            .astype("string")
            .str.contains(
                "redfin_annual_weight_coverage_or_hmda_anchor_unavailable",
                regex=False,
                na=False,
            )
            .all()
        )
        if null_identity or missing_weight_identity:
            continue
        if (
            unique_months != 12
            or actual.isna().any()
            or pd.isna(actual_sum)
            or pd.isna(expected)
            or float(actual_sum) != float(expected)
        ):
            row = dict(zip(group_columns, group_key, strict=True))
            row.update(
                {
                    "reason": "annual_flow_sum_mismatch",
                    "month_count": unique_months,
                    "actual_monthly_sum": actual_sum,
                    "benchmark_value": expected,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _repeated_rate_identity_failures(joined: pd.DataFrame) -> pd.DataFrame:
    selected = joined.loc[
        joined["benchmark_variable_id"]
        .astype("string")
        .str.contains(
            r"downpayment_rate|share_downpayment_below",
            case=False,
            na=False,
            regex=True,
        )
        & joined["benchmark_merge_status"].eq("both")
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["reason"])
    actual = pd.to_numeric(selected["value"], errors="coerce")
    expected = pd.to_numeric(selected["benchmark_value"], errors="coerce")
    close = pd.Series(
        np.isclose(
            actual,
            expected,
            rtol=1e-10,
            atol=1e-12,
            equal_nan=False,
        ),
        index=selected.index,
    )
    quality_flags = selected["quality_flag"].astype("string")
    explicit_null = selected["is_suppressed"].fillna(False) | (
        quality_flags.notna() & quality_flags.str.strip().ne("")
    )
    matched_suppressed_null = (
        actual.isna() & expected.isna() & explicit_null
    )
    # A real-time vintage must not carry an annual HMDA rate backward before
    # the benchmark's own source period.  Those pre-period months are
    # deliberately null even though the selected, currently available anchor
    # has a value.  Accept only the explicit builder flag on rows that are
    # chronologically before the anchor period; other null/non-null mismatches
    # remain structural failures.
    benchmark_start = pd.to_datetime(
        selected["benchmark_source_period_start"], errors="coerce"
    )
    before_anchor_period_null = (
        actual.isna()
        & expected.notna()
        & benchmark_start.notna()
        & selected["month"].lt(benchmark_start)
        & quality_flags.eq("before_anchor_source_period")
    )
    failed = selected.loc[
        ~(close | matched_suppressed_null | before_anchor_period_null),
        [
            "county_fips",
            "month",
            "variable_id",
            "benchmark_variable_id",
            "benchmark_vintage",
            "value",
            "benchmark_value",
            "is_suppressed",
            "quality_flag",
        ],
    ].copy()
    if not failed.empty:
        failed.insert(0, "reason", "repeated_rate_does_not_match_benchmark")
    return failed


def _payment_benchmark_identity_failures(
    joined: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    selected = joined.loc[
        joined["benchmark_variable_id"]
        .astype("string")
        .str.contains(
            (
                r"estimated_aggregate_monthly_first_mortgage_pi|"
                r"first_mortgage_pi_per_mortgaged_unit|"
                r"annual_first_mortgage_debt_service|"
                r"mortgage_debt_service_to_income"
            ),
            case=False,
            na=False,
            regex=True,
        )
        & joined["benchmark_merge_status"].eq("both")
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["reason"])
    selected["calendar_year"] = selected["month"].dt.year
    rows: list[dict[str, Any]] = []
    group_columns = _identity_group_columns(version_key)
    for group_key, group in selected.groupby(
        group_columns,
        dropna=False,
        sort=True,
    ):
        anchor_reference = group["benchmark_reference_date"].iloc[0]
        if pd.isna(anchor_reference):
            rows.append(
                {
                    "reason": "payment_anchor_reference_date_missing",
                    "group_key": str(group_key),
                }
            )
            continue
        anchor_year = int(pd.Timestamp(anchor_reference).year)
        calendar_year = int(group["calendar_year"].iloc[0])
        if calendar_year != anchor_year:
            continue
        if not _group_is_due(
            group,
            version_key=version_key,
            anchor_year=anchor_year,
        ):
            continue
        actual = pd.to_numeric(group["value"], errors="coerce")
        expected = pd.to_numeric(
            group["benchmark_value"], errors="coerce"
        ).iloc[0]
        unique_months = group["month"].nunique()
        actual_mean = actual.mean()
        if (
            unique_months != 12
            or pd.isna(actual_mean)
            or pd.isna(expected)
            or not _close(float(actual_mean), float(expected))
        ):
            row = dict(zip(group_columns, group_key, strict=True))
            row.update(
                {
                    "reason": "annual_average_payment_benchmark_mismatch",
                    "month_count": unique_months,
                    "actual_monthly_mean": actual_mean,
                    "benchmark_value": expected,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _raw_debt_reference_anchor_failures(
    joined: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    """Require the modeled raw-debt path to meet its Version A level envelope."""

    suffix = "monthly_hist" if version_key == "B" else "monthly_rt"
    variable_id = f"acs_implied_first_mortgage_balance_raw_{suffix}"
    selected = joined.loc[
        joined["variable_id"].astype("string").eq(variable_id)
        & joined["benchmark_merge_status"].eq("both")
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["reason"])

    selected["benchmark_reference_month"] = pd.to_datetime(
        selected["benchmark_reference_date"], errors="coerce"
    ).dt.to_period("M").dt.to_timestamp("M")
    group_columns = [
        "county_fips",
        "variable_id",
        "benchmark_variable_id",
        "benchmark_vintage",
        "estimate_variant",
    ]
    if version_key == "C":
        group_columns.insert(0, "information_cutoff_date")
    rows: list[dict[str, Any]] = []
    comparisons = (
        ("value", "benchmark_value"),
        ("low_estimate", "benchmark_low_estimate"),
        ("central_estimate", "benchmark_central_estimate"),
        ("high_estimate", "benchmark_high_estimate"),
    )
    for group_key, group in selected.groupby(
        group_columns, dropna=False, sort=True
    ):
        reference = group["benchmark_reference_month"].iloc[0]
        row_base = dict(zip(group_columns, group_key, strict=True))
        if pd.isna(reference):
            rows.append(
                {
                    **row_base,
                    "reason": "raw_debt_anchor_reference_date_missing",
                }
            )
            continue
        if reference < group["month"].min() or reference > group["month"].max():
            continue
        at_reference = group.loc[group["month"].eq(reference)]
        if len(at_reference) != 1:
            rows.append(
                {
                    **row_base,
                    "reason": "raw_debt_reference_month_missing_or_duplicated",
                    "benchmark_reference_month": reference,
                    "reference_row_count": len(at_reference),
                }
            )
            continue
        row = at_reference.iloc[0]
        mismatched: list[str] = []
        for actual_column, expected_column in comparisons:
            actual = pd.to_numeric(pd.Series([row[actual_column]]), errors="coerce").iloc[0]
            expected = pd.to_numeric(
                pd.Series([row[expected_column]]), errors="coerce"
            ).iloc[0]
            both_null = pd.isna(actual) and pd.isna(expected)
            exact = (
                pd.notna(actual)
                and pd.notna(expected)
                and bool(np.isclose(float(actual), float(expected), rtol=0.0, atol=1e-9))
            )
            if not (both_null or exact):
                mismatched.append(actual_column)
        if mismatched:
            rows.append(
                {
                    **row_base,
                    "reason": "raw_debt_reference_anchor_mismatch",
                    "benchmark_reference_month": reference,
                    "mismatched_fields": "|".join(mismatched),
                }
            )
    return pd.DataFrame(rows)


def _debt_equity_stock_identity_failures(
    observations: pd.DataFrame,
    *,
    version_key: str,
) -> pd.DataFrame:
    """Verify scenario debt plus equity against the matched modeled value stock."""

    transformations = observations["transformation_id"].astype("string")
    selected = observations.loc[
        transformations.str.contains(
            "hpi_updated_pmms_equity", case=False, na=False
        )
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["reason"])

    failures: list[pd.DataFrame] = []
    accounting_id = selected["accounting_debt_variable_id"].astype("string")
    missing_accounting_id = accounting_id.isna() | accounting_id.str.strip().eq("")
    if missing_accounting_id.any():
        frame = selected.loc[missing_accounting_id].copy()
        frame.insert(0, "reason", "accounting_debt_variable_id_missing")
        failures.append(frame)
    selected = selected.loc[~missing_accounting_id].copy()
    if selected.empty:
        return pd.concat(failures, ignore_index=True, sort=False)

    context = ["county_fips", "month"]
    if version_key == "C":
        context.insert(0, "information_cutoff_date")
    debt_key = [*context, "variable_id"]
    debt = observations[
        [*context, "variable_id", "value"]
    ].rename(
        columns={
            "variable_id": "accounting_debt_variable_id",
            "value": "accounting_debt_value",
        }
    )
    duplicate_debt = debt.duplicated(
        [*context, "accounting_debt_variable_id"], keep=False
    )
    if duplicate_debt.any():
        duplicate_keys = debt.loc[
            duplicate_debt, [*context, "accounting_debt_variable_id"]
        ].drop_duplicates()
        affected = selected.merge(
            duplicate_keys,
            on=[*context, "accounting_debt_variable_id"],
            how="inner",
        )
        if not affected.empty:
            affected.insert(0, "reason", "duplicate_accounting_debt_rows")
            failures.append(affected)
    debt = debt.drop_duplicates(
        [*context, "accounting_debt_variable_id"], keep="first"
    )
    joined = selected.merge(
        debt,
        on=[*context, "accounting_debt_variable_id"],
        how="left",
        indicator="accounting_debt_merge_status",
        validate="many_to_one",
    )
    missing_debt = joined["accounting_debt_merge_status"].ne("both")
    if missing_debt.any():
        frame = joined.loc[missing_debt].copy()
        frame.insert(0, "reason", "matching_accounting_debt_row_missing")
        failures.append(frame)

    comparable = joined.loc[~missing_debt].copy()
    equity_value = pd.to_numeric(comparable["value"], errors="coerce")
    debt_value = pd.to_numeric(
        comparable["accounting_debt_value"], errors="coerce"
    )
    owner_value = pd.to_numeric(
        comparable["matched_owner_value_stock"], errors="coerce"
    )
    complete = equity_value.notna() & debt_value.notna() & owner_value.notna()
    quality_flags = comparable["quality_flag"].astype("string")
    explicit_unavailable = (
        equity_value.isna()
        & (debt_value.isna() | owner_value.isna())
        & quality_flags.str.contains(
            "hpi_anchor_or_matching_pmms_debt_unavailable",
            case=False,
            na=False,
        )
    )
    partial = ~(complete | explicit_unavailable)
    if partial.any():
        frame = comparable.loc[partial].copy()
        frame.insert(0, "reason", "partial_debt_equity_accounting_components")
        failures.append(frame)

    if complete.any():
        close = np.isclose(
            equity_value.loc[complete] + debt_value.loc[complete],
            owner_value.loc[complete],
            rtol=1e-10,
            atol=0.01,
            equal_nan=False,
        )
        mismatch_index = comparable.loc[complete].index[~close]
        if len(mismatch_index):
            frame = comparable.loc[mismatch_index].copy()
            frame.insert(0, "reason", "debt_plus_equity_not_equal_owner_value")
            failures.append(frame)

    if not failures:
        return pd.DataFrame(columns=["reason"])
    return pd.concat(failures, ignore_index=True, sort=False)


def monthly_finance_qa_results(
    observations: pd.DataFrame,
    lineage: pd.DataFrame,
    *,
    version: str,
    dictionary: pd.DataFrame,
    transformation_catalog: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    require_all_california_counties: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return QA summary and bounded failure rows for one B or C dataset."""

    version_key, database_name, suffix = _version_settings(version)
    work = normalize_monthly_finance(observations)
    normalized_lineage = normalize_monthly_lineage(lineage)
    results: list[MonthlyFinanceCheck] = []
    failure_frames: list[pd.DataFrame] = []

    def check(
        check_id: str,
        severity: str,
        failures: pd.DataFrame,
        detail: str,
    ) -> None:
        results.append(_result(check_id, severity, failures, detail))
        if not failures.empty:
            bounded = failures.head(MAX_FAILURE_ROWS).copy()
            bounded.insert(0, "severity", severity)
            bounded.insert(0, "check_id", check_id)
            failure_frames.append(bounded)

    observation_contract = monthly_finance_contract_errors(
        observations,
        version=version_key,
    )
    check(
        "monthly_finance_contract",
        "error",
        pd.DataFrame({"reason": observation_contract}),
        "Suffix, version, month-end, key, flags, cutoff, and value metadata must satisfy the shared schema.",
    )

    lineage_contract = monthly_lineage_contract_errors(
        lineage,
        observations,
        version=version_key,
    )
    check(
        "monthly_lineage_contract",
        "error",
        pd.DataFrame({"reason": lineage_contract}),
        "Every output row must have complete, matching per-input source lineage.",
    )

    if require_all_california_counties:
        retention = _california_retention_failures(
            work,
            version_key=version_key,
        )
    else:
        retention = pd.DataFrame(columns=["reason"])
    check(
        "california_58_county_retention",
        "error",
        retention,
        "Every version/month/variable/variant group must retain exactly the 58 official California counties, including flagged null rows.",
    )

    check(
        "version_dictionary_exact_coverage",
        "error",
        _dictionary_coverage_failures(
            work,
            dictionary,
            suffix=suffix,
            database_name=database_name,
        ),
        "Emitted variable IDs and the authoritative version dictionary must match exactly with no duplicates.",
    )
    check(
        "version_dictionary_row_contract",
        "error",
        _dictionary_row_contract_failures(
            work,
            dictionary,
            suffix=suffix,
            database_name=database_name,
        ),
        "Each emitted variable must match its dictionary unit, Version A anchor mapping, and transformation policy.",
    )
    check(
        "transformation_catalog_exact_coverage",
        "error",
        _catalog_coverage_failures(
            work,
            transformation_catalog,
            database_name=database_name,
        ),
        "Used transformation IDs and the authoritative version catalog must match exactly with no duplicates.",
    )

    if version_key == "B":
        check(
            "version_b_modeled_no_nowcast",
            "error",
            _version_b_flag_failures(work),
            "Every Version B row is modeled, never a nowcast, and has no real-time cutoff.",
        )
    else:
        check(
            "version_c_cutoff_no_interpolation_lookahead",
            "error",
            _version_c_information_failures(work, normalized_lineage),
            "Version C forbids future-endpoint interpolation and any observation, release, or retrieved snapshot after its cutoff.",
        )

    check(
        "source_release_timing",
        "error",
        _source_release_timing_failures(work, normalized_lineage),
        "Published input releases cannot precede the end of the source period they describe.",
    )

    check(
        "estimate_bounds_order",
        "error",
        _estimate_bound_failures(work),
        "Complete uncertainty bounds must satisfy low <= central <= high.",
    )

    if benchmark is None:
        results.extend(
            [
                _skipped(
                    "benchmark_anchor_coverage",
                    "No Version A benchmark frame was supplied; anchor reconciliation was not evaluated.",
                ),
                _skipped(
                    "annual_downpayment_flow_sum_identity",
                    "No Version A benchmark frame was supplied; annual flow reconciliation was not evaluated.",
                ),
                _skipped(
                    "repeated_downpayment_rate_identity",
                    "No Version A benchmark frame was supplied; repeated-rate reconciliation was not evaluated.",
                ),
                _skipped(
                    "annual_average_payment_benchmark_identity",
                    "No Version A benchmark frame was supplied; payment benchmark reconciliation was not evaluated.",
                ),
                _skipped(
                    "benchmark_uncertainty_bounds_presence",
                    "No Version A benchmark frame was supplied; uncertainty-bound propagation was not evaluated.",
                ),
                _skipped(
                    "raw_debt_reference_anchor_identity",
                    "No Version A benchmark frame was supplied; the raw-debt reference anchor was not evaluated.",
                ),
            ]
        )
    else:
        joined, duplicate_benchmarks = _benchmark_join(work, benchmark)
        check(
            "benchmark_anchor_coverage",
            "error",
            _benchmark_anchor_failures(
                joined,
                duplicate_benchmarks,
                version_key=version_key,
            ),
            "Every monthly observation must resolve to one source-faithful Version A anchor; C anchors must have been released by the cutoff.",
        )
        check(
            "annual_downpayment_flow_sum_identity",
            "error",
            _annual_flow_identity_failures(joined, version_key=version_key),
            "Twelve distributed monthly down-payment flows must sum exactly to the matched annual benchmark.",
        )
        check(
            "repeated_downpayment_rate_identity",
            "error",
            _repeated_rate_identity_failures(joined),
            "A repeated annual down-payment rate must equal, not divide, its matched annual benchmark.",
        )
        check(
            "annual_average_payment_benchmark_identity",
            "error",
            _payment_benchmark_identity_failures(
                joined,
                version_key=version_key,
            ),
            "The complete anchor-year average of a modeled monthly payment path must match its Version A payment benchmark.",
        )
        check(
            "benchmark_uncertainty_bounds_presence",
            "error",
            _benchmark_bound_presence_failures(joined),
            "Populated modeled rows must retain low/central/high bounds whenever their eligible Version A anchor provides them.",
        )
        check(
            "raw_debt_reference_anchor_identity",
            "error",
            _raw_debt_reference_anchor_failures(
                joined,
                version_key=version_key,
            ),
            "The modeled raw-debt path must meet the Version A raw-debt central, low, and high levels in its reference month.",
        )

    check(
        "debt_equity_owner_value_stock_identity",
        "error",
        _debt_equity_stock_identity_failures(
            work,
            version_key=version_key,
        ),
        "Each populated PMMS scenario must satisfy debt plus equity equals its matched HPI-updated owner-value stock.",
    )

    check(
        "economic_plausibility_review",
        "warning",
        _economic_plausibility_failures(work),
        "Broad economic outliers are retained as warnings for review, never silently deleted or converted to structural failures.",
    )

    summary = pd.DataFrame(asdict(result) for result in results)
    failures = (
        pd.concat(failure_frames, ignore_index=True, sort=False)
        if failure_frames
        else pd.DataFrame(columns=["check_id", "severity", "reason"])
    )
    return summary, failures


def run_monthly_finance_qa(
    observations: pd.DataFrame | str | Path,
    lineage: pd.DataFrame | str | Path,
    *,
    version: str,
    dictionary: pd.DataFrame | str | Path,
    transformation_catalog: pd.DataFrame | str | Path,
    benchmark: pd.DataFrame | str | Path | None = None,
    root: Path | None = None,
    raise_on_error: bool = True,
    require_all_california_counties: bool = True,
) -> pd.DataFrame:
    """Run QA, optionally persist reports, and raise on structural failures."""

    version_key, _, _ = _version_settings(version)
    observation_frame = _read_frame(observations)
    lineage_frame = _read_frame(lineage)
    dictionary_frame = _read_frame(dictionary)
    catalog_frame = _read_frame(transformation_catalog)
    benchmark_frame = None if benchmark is None else _read_frame(benchmark)
    summary, failures = monthly_finance_qa_results(
        observation_frame,
        lineage_frame,
        version=version_key,
        dictionary=dictionary_frame,
        transformation_catalog=catalog_frame,
        benchmark=benchmark_frame,
        require_all_california_counties=require_all_california_counties,
    )
    if root is not None:
        qa_dir = Path(root) / "output" / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        prefix = "version_b" if version_key == "B" else "version_c"
        summary.to_csv(qa_dir / f"{prefix}_qa_summary.csv", index=False)
        failures.to_csv(qa_dir / f"{prefix}_qa_failures.csv", index=False)

    failed_errors = summary.loc[
        summary["severity"].eq("error") & summary["status"].eq("fail")
    ]
    if raise_on_error and not failed_errors.empty:
        detail = ", ".join(
            f"{row.check_id}={row.failure_count}"
            for row in failed_errors.itertuples(index=False)
        )
        raise RuntimeError(
            f"Version {version_key} monthly finance structural QA failed: {detail}"
        )
    return summary


__all__ = [
    "MonthlyFinanceCheck",
    "monthly_finance_qa_results",
    "run_monthly_finance_qa",
]
