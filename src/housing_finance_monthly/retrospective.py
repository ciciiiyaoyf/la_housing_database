"""Retrospective monthly (Version B) housing-finance transformations.

This module consumes the frozen Version A benchmark table plus normalized
monthly indicators.  It never changes or rewrites Version A.  Canonical
monthly values that cannot be identified from public inputs remain explicit
nulls; proxy and PMMS sensitivity results use separate variable identifiers.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.schema import normalize_benchmark
from src.housing_finance_monthly.schema import (
    MONTHLY_FINANCE_COLUMNS,
    VERSION_B_DATABASE_NAME,
    finalize_monthly_finance,
)


METHOD_VERSION = "version_b_monthly_hist_v1"

PAYMENT_BENCHMARK_IDS = (
    "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
    "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
    "acs_annual_first_mortgage_debt_service_benchmark",
    "acs_mortgage_debt_service_to_income_benchmark",
)

HMDA_RATE_BENCHMARK_IDS = (
    "hmda_median_downpayment_rate_benchmark",
    "hmda_property_value_weighted_downpayment_rate_benchmark",
    "hmda_median_downpayment_rate_conventional_benchmark",
    "hmda_property_value_weighted_downpayment_rate_conventional_benchmark",
    "hmda_median_downpayment_rate_fha_benchmark",
    "hmda_property_value_weighted_downpayment_rate_fha_benchmark",
    "hmda_median_downpayment_rate_va_benchmark",
    "hmda_property_value_weighted_downpayment_rate_va_benchmark",
)

HMDA_SHARE_BENCHMARK_IDS = tuple(
    f"hmda_share_downpayment_below_{threshold}pct{loan_type}_benchmark"
    for loan_type in ("", "_conventional", "_fha", "_va")
    for threshold in (3, 5, 10, 20)
)

HMDA_FLOW_BENCHMARK_IDS = (
    "hmda_aggregate_downpayment_flow_benchmark",
    "hmda_aggregate_downpayment_flow_conventional_benchmark",
    "hmda_aggregate_downpayment_flow_fha_benchmark",
    "hmda_aggregate_downpayment_flow_va_benchmark",
)

PMMS_DEBT_BENCHMARK_IDS = {
    years: (
        "acs_implied_first_mortgage_balance_raw_pmms_"
        f"{years}y_sensitivity_benchmark"
    )
    for years in (20, 25, 30)
}

PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS = {
    years: (
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_"
        f"{years}y_scenario_benchmark"
    )
    for years in (20, 25, 30)
}

PMMS_EQUITY_ALL_BENCHMARK_IDS = {
    years: (
        "acs_implied_owner_occupied_home_equity_all_pmms_"
        f"{years}y_scenario_benchmark"
    )
    for years in (20, 25, 30)
}

CALIBRATED_DEBT_BENCHMARK_ID = (
    "acs_implied_first_mortgage_balance_cal_benchmark"
)
RAW_DEBT_BENCHMARK_ID = "acs_implied_first_mortgage_balance_raw_benchmark"
CANONICAL_DEBT_BENCHMARK_IDS = (
    CALIBRATED_DEBT_BENCHMARK_ID,
    "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_capita_benchmark",
    "acs_first_mortgage_debt_to_owner_income_benchmark",
)
CANONICAL_EQUITY_MORTGAGED_BENCHMARK_ID = (
    "acs_implied_owner_occupied_home_equity_mortgaged_benchmark"
)
CANONICAL_EQUITY_ALL_BENCHMARK_ID = (
    "acs_implied_owner_occupied_home_equity_all_benchmark"
)
CANONICAL_EQUITY_BENCHMARK_IDS = (
    CANONICAL_EQUITY_MORTGAGED_BENCHMARK_ID,
    CANONICAL_EQUITY_ALL_BENCHMARK_ID,
    "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
    "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark",
    "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark",
    "acs_aggregate_ltv_mortgaged_benchmark",
    "acs_equity_rate_mortgaged_benchmark",
)
CANONICAL_NULL_BENCHMARK_IDS = (
    *CANONICAL_DEBT_BENCHMARK_IDS,
    *CANONICAL_EQUITY_BENCHMARK_IDS,
)
VALUE_MORTGAGED_BENCHMARK_ID = (
    "acs_aggregate_owner_value_mortgaged_benchmark"
)
VALUE_NO_MORTGAGE_BENCHMARK_ID = (
    "acs_aggregate_owner_value_no_mortgage_benchmark"
)

TRANSFORM_PAYMENT = "version_b_reference_year_payment_repeat_v1"
TRANSFORM_HMDA_RATE = "version_b_hmda_annual_rate_repeat_v1"
TRANSFORM_HMDA_FLOW_NULL = "version_b_hmda_flow_unavailable_v1"
TRANSFORM_HMDA_PROXY = (
    "version_b_redfin_purchase_value_proxy_distribution_v1"
)
TRANSFORM_CALIBRATED_NULL = "version_b_calibrated_target_unavailable_v1"
TRANSFORM_RAW_DEBT = "version_b_single_anchor_nmdb_redfin_raw_debt_v1"
TRANSFORM_PMMS_DEBT = "version_b_single_anchor_nmdb_redfin_pmms_debt_v1"
TRANSFORM_PMMS_EQUITY = "version_b_hpi_updated_pmms_equity_v1"


class VersionBContractError(ValueError):
    """Raised when Version B would violate a stated construction contract."""


class _DiagnosticsPayload(list[dict[str, Any]]):
    """Equality-safe attrs payload whose public copy remains a DataFrame."""

    def copy(self) -> pd.DataFrame:
        return pd.DataFrame(self)


def monthly_hist_id(benchmark_id: str) -> str:
    """Replace the terminal Version A suffix with the Version B suffix."""

    text = str(benchmark_id)
    if not text.endswith("_benchmark"):
        raise VersionBContractError(
            f"benchmark variable does not end in _benchmark: {text}"
        )
    return text[: -len("_benchmark")] + "_monthly_hist"


def redfin_proxy_id(benchmark_id: str) -> str:
    """Return the separately labeled Redfin purchase-value proxy ID."""

    prefix = str(benchmark_id)
    if not prefix.endswith("_benchmark"):
        raise VersionBContractError(
            f"benchmark variable does not end in _benchmark: {prefix}"
        )
    return (
        prefix[: -len("_benchmark")]
        + "_redfin_sales_value_proxy_monthly_hist"
    )


def _month_end(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise VersionBContractError(f"invalid month/date: {value!r}")
    result = pd.Timestamp(parsed)
    if result.tzinfo is not None:
        result = result.tz_convert(None)
    return result.to_period("M").to_timestamp("M")


def _number(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return math.nan
    result = float(parsed)
    return result if math.isfinite(result) else math.nan


def _finite(value: Any) -> bool:
    return not math.isnan(_number(value))


def _tokens(value: Any) -> list[str]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return []
    return [
        token.strip()
        for token in str(value).split("|")
        if token.strip() and token.strip() not in {"<NA>", "None", "nan"}
    ]


def _pipe(values: Sequence[Any]) -> str | None:
    result: list[str] = []
    for value in values:
        for token in _tokens(value):
            if token not in result:
                result.append(token)
    return "|".join(result) if result else None


def _quality(*values: Any) -> str:
    return _pipe(values) or "modeled_version_b"


def _release_audit(rows: Sequence[Mapping[str, Any] | pd.Series]) -> str:
    audit: dict[str, str] = {}
    for row in rows:
        raw_audit = row.get("source_release_dates")
        for token in _tokens(raw_audit):
            source_id, separator, raw_date = token.partition("=")
            if separator and source_id.strip():
                audit.setdefault(source_id.strip(), raw_date.strip() or "unavailable")
        release = pd.to_datetime(row.get("release_date"), errors="coerce")
        release_text = (
            "unavailable"
            if pd.isna(release)
            else pd.Timestamp(release).date().isoformat()
        )
        for source_id in _tokens(row.get("source_id")):
            audit.setdefault(source_id, release_text)
    return "|".join(f"{source_id}={audit[source_id]}" for source_id in audit)


def _max_date(
    rows: Sequence[Mapping[str, Any] | pd.Series], column: str, *, utc: bool = False
) -> pd.Timestamp | pd.NaT:
    values = [pd.to_datetime(row.get(column), errors="coerce", utc=utc) for row in rows]
    known = [pd.Timestamp(value) for value in values if not pd.isna(value)]
    return max(known) if known else pd.NaT


def _lineage_id(
    *,
    county_fips: str,
    month: pd.Timestamp,
    variable_id: str,
    estimate_variant: str,
    transformation_id: str,
    checksum_sha256: Any,
) -> str:
    payload = "|".join(
        [
            county_fips,
            month.date().isoformat(),
            variable_id,
            estimate_variant,
            transformation_id,
            str(checksum_sha256 or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bound(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _model_row(
    anchor: Mapping[str, Any] | pd.Series,
    *,
    month: Any,
    variable_id: str,
    value: Any,
    low: Any = None,
    high: Any = None,
    transformation_id: str,
    monthly_method: str,
    construction_method: str,
    quality_flag: str,
    extras: Sequence[Mapping[str, Any] | pd.Series] = (),
    is_interpolated: bool = False,
    unit: str | None = None,
    estimate_variant: str | None = None,
    notes: str | None = None,
    method_version: str = METHOD_VERSION,
) -> dict[str, Any]:
    target = _month_end(month)
    benchmark_id = str(anchor.get("variable_id") or anchor.get("variable_name"))
    county = str(anchor.get("county_fips")).zfill(5)
    variant = str(estimate_variant or anchor.get("estimate_variant") or "central")
    inputs = [anchor, *extras]
    source_id = _pipe([row.get("source_id") for row in inputs])
    source_frequency = _pipe([row.get("source_frequency") for row in inputs])
    source_vintage = _pipe([row.get("source_vintage") for row in inputs])
    source_filename = _pipe([row.get("source_filename") for row in inputs])
    checksum = _pipe([row.get("checksum_sha256") for row in inputs])
    numeric = _bound(value)
    low_value = _bound(low)
    high_value = _bound(high)
    row = dict.fromkeys(MONTHLY_FINANCE_COLUMNS)
    row.update(
        {
            "county_fips": county,
            "state_fips": str(anchor.get("state_fips") or county[:2]).zfill(2),
            "geo_id": str(anchor.get("geo_id") or f"COUNTY_{county}"),
            "month": target,
            "value": numeric,
            "variable_name": variable_id,
            "variable_id": variable_id,
            "unit": unit or anchor.get("unit") or "unknown",
            "estimate_variant": variant,
            "reported_value": None,
            "margin_of_error": None,
            "database_version": VERSION_B_DATABASE_NAME,
            "source_id": source_id or "version_a_housing_finance_benchmark",
            "source_frequency": source_frequency or "modeled",
            "source_period_start": anchor.get("source_period_start"),
            "source_period_end": anchor.get("source_period_end"),
            "reference_date": anchor.get("reference_date"),
            "release_date": _max_date(inputs, "release_date"),
            "source_release_dates": _release_audit(inputs),
            "source_geography": anchor.get("source_geography") or "county",
            "parameter_geography": anchor.get("parameter_geography") or "county",
            "monthly_method": monthly_method,
            "construction_method": construction_method,
            "payment_frequency": anchor.get("payment_frequency"),
            "is_modeled": True,
            "is_nowcast": False,
            "is_imputed": False,
            "is_interpolated": bool(is_interpolated),
            "is_suppressed": bool(anchor.get("is_suppressed") or False),
            "benchmark_vintage": anchor.get("benchmark_vintage") or "Version A",
            "sample_n": anchor.get("sample_n"),
            "coverage_rate": anchor.get("coverage_rate"),
            "low_estimate": low_value,
            "central_estimate": numeric,
            "high_estimate": high_value,
            "universe_definition": anchor.get("universe_definition") or "unknown",
            "notes": notes or anchor.get("notes"),
            "source_vintage": source_vintage or "Version A",
            "retrieved_at": _max_date(inputs, "retrieved_at", utc=True),
            "source_filename": source_filename or "housing_finance_benchmark.parquet",
            "checksum_sha256": checksum or anchor.get("lineage_id"),
            "transformation_id": transformation_id,
            "quality_flag": quality_flag,
            "benchmark_variable_id": benchmark_id,
            "information_cutoff_date": None,
            "method_version": method_version,
        }
    )
    row["lineage_id"] = _lineage_id(
        county_fips=county,
        month=target,
        variable_id=variable_id,
        estimate_variant=variant,
        transformation_id=transformation_id,
        checksum_sha256=row["checksum_sha256"],
    )
    return row


def _benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    work = normalize_benchmark(frame)
    work["variable_id"] = work["variable_id"].fillna(work["variable_name"])
    work["county_fips"] = work["county_fips"].astype("string").str.zfill(5)
    return work


def _normalized_indicator(
    frame: pd.DataFrame,
    *,
    label: str,
    required: Sequence[str],
    county: bool,
) -> pd.DataFrame:
    missing = set(required).difference(frame.columns)
    if missing:
        raise VersionBContractError(f"{label} missing columns: {sorted(missing)}")
    work = frame.copy()
    work["month"] = pd.to_datetime(work["month"], errors="coerce")
    if work["month"].isna().any():
        raise VersionBContractError(f"{label} contains invalid months")
    work["month"] = work["month"].dt.to_period("M").dt.to_timestamp("M")
    if county:
        work["county_fips"] = work["county_fips"].astype("string").str.zfill(5)
        if ~work["county_fips"].str.fullmatch(r"06\d{3}", na=False).all():
            raise VersionBContractError(f"{label} contains invalid California FIPS")
        key = ["county_fips", "month"]
    else:
        key = ["month"]
    if work.duplicated(key).any():
        raise VersionBContractError(f"{label} contains duplicate keys: {key}")
    return work.sort_values(key).reset_index(drop=True)


def _prepare_redfin(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "purchase_value_proxy" not in work:
        required_values = {"median_sale_price", "homes_sold"}
        missing = required_values.difference(work.columns)
        if missing:
            raise VersionBContractError(
                "Redfin requires purchase_value_proxy or median_sale_price and homes_sold"
            )
        work["purchase_value_proxy"] = (
            pd.to_numeric(work["median_sale_price"], errors="coerce")
            * pd.to_numeric(work["homes_sold"], errors="coerce")
        )
    work = _normalized_indicator(
        work,
        label="Redfin",
        required=("county_fips", "month", "homes_sold", "purchase_value_proxy"),
        county=True,
    )
    for column in ("homes_sold", "purchase_value_proxy"):
        original = work[column]
        work[column] = pd.to_numeric(original, errors="coerce")
        invalid = original.notna() & work[column].isna()
        if invalid.any() or np.isinf(work[column].dropna()).any():
            raise VersionBContractError(f"Redfin {column} is not numeric")
        if work[column].dropna().lt(0).any():
            raise VersionBContractError(f"Redfin {column} cannot be negative")
    return work


def _prepare_zillow(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.rename(columns={"value": "zhvi"}).copy() if "zhvi" not in frame else frame.copy()
    work = _normalized_indicator(
        work,
        label="Zillow",
        required=("county_fips", "month", "zhvi"),
        county=True,
    )
    original = work["zhvi"]
    work["zhvi"] = pd.to_numeric(original, errors="coerce")
    if (original.notna() & work["zhvi"].isna()).any():
        raise VersionBContractError("Zillow ZHVI is not numeric")
    if work["zhvi"].dropna().le(0).any():
        raise VersionBContractError("Zillow ZHVI must be positive")
    return work


def _prepare_nmdb(frame: pd.DataFrame) -> pd.DataFrame:
    value_column = next(
        (
            column
            for column in ("nmdb_ca_all_mortgages_upb", "value", "upb")
            if column in frame
        ),
        None,
    )
    if value_column is None:
        raise VersionBContractError("NMDB requires nmdb_ca_all_mortgages_upb")
    work = frame.copy()
    if value_column != "nmdb_ca_all_mortgages_upb":
        work = work.rename(columns={value_column: "nmdb_ca_all_mortgages_upb"})
    work = _normalized_indicator(
        work,
        label="NMDB",
        required=("month", "nmdb_ca_all_mortgages_upb"),
        county=False,
    )
    original = work["nmdb_ca_all_mortgages_upb"]
    work["nmdb_ca_all_mortgages_upb"] = pd.to_numeric(original, errors="coerce")
    if (original.notna() & work["nmdb_ca_all_mortgages_upb"].isna()).any():
        raise VersionBContractError("NMDB UPB is not numeric")
    if work["nmdb_ca_all_mortgages_upb"].isna().any() or work[
        "nmdb_ca_all_mortgages_upb"
    ].le(0).any():
        raise VersionBContractError("NMDB UPB must be complete and positive")
    return work


def _method_version(config: Mapping[str, Any] | None) -> str:
    return str((config or {}).get("method_version", METHOD_VERSION))


def repeat_reference_year_payments(
    benchmark: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Repeat each Version A payment anchor in its reference calendar year."""

    work = _benchmark(benchmark)
    anchors = work.loc[work["variable_id"].isin(PAYMENT_BENCHMARK_IDS)]
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        reference = pd.to_datetime(anchor["reference_date"], errors="coerce")
        if pd.isna(reference):
            raise VersionBContractError("payment anchor requires reference_date")
        target_id = monthly_hist_id(str(anchor["variable_id"]))
        for month in pd.date_range(
            f"{pd.Timestamp(reference).year}-01-31",
            f"{pd.Timestamp(reference).year}-12-31",
            freq="ME",
        ):
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=target_id,
                    value=anchor["value"],
                    low=anchor["low_estimate"],
                    high=anchor["high_estimate"],
                    transformation_id=TRANSFORM_PAYMENT,
                    monthly_method="reference_year_payment_level_repeated_not_divided",
                    construction_method=(
                        "Version A ACS/PUMS structural payment level repeated in "
                        "each month of its reference calendar year"
                    ),
                    quality_flag=_quality(
                        anchor["quality_flag"], "reference_year_level_repeat"
                    ),
                    method_version=_method_version(config),
                )
            )
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def repeat_hmda_rates(
    benchmark: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Repeat annual HMDA rates in all 12 activity-year months, not divide them."""

    work = _benchmark(benchmark)
    anchors = work.loc[
        work["variable_id"].isin(
            (*HMDA_RATE_BENCHMARK_IDS, *HMDA_SHARE_BENCHMARK_IDS)
        )
    ]
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        start = pd.to_datetime(anchor["source_period_start"], errors="coerce")
        if pd.isna(start):
            raise VersionBContractError("HMDA rate anchor requires source_period_start")
        year = pd.Timestamp(start).year
        target_id = monthly_hist_id(str(anchor["variable_id"]))
        for month in pd.date_range(f"{year}-01-31", f"{year}-12-31", freq="ME"):
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=target_id,
                    value=anchor["value"],
                    low=anchor["low_estimate"],
                    high=anchor["high_estimate"],
                    transformation_id=TRANSFORM_HMDA_RATE,
                    monthly_method="hmda_activity_year_rate_repeated_not_divided",
                    construction_method=(
                        "Annual HMDA down-payment rate copied to each activity-year month"
                    ),
                    quality_flag=_quality(
                        anchor["quality_flag"], "annual_rate_repeated_not_divided"
                    ),
                    method_version=_method_version(config),
                )
            )
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def residual_safe_distribution(total: Any, weights: Sequence[Any]) -> list[float]:
    """Distribute a finite total and force the last value to absorb residual."""

    annual = _number(total)
    numeric = np.asarray([_number(value) for value in weights], dtype=float)
    if math.isnan(annual) or numeric.size == 0:
        raise VersionBContractError("distribution requires a finite total and weights")
    if np.isnan(numeric).any() or (numeric < 0).any() or numeric.sum() <= 0:
        raise VersionBContractError("distribution weights must be complete and nonnegative")
    shares = numeric / math.fsum(numeric.tolist())
    result = [float(annual * share) for share in shares]
    if len(result) == 1:
        result[0] = annual
    else:
        result[-1] = float(annual - math.fsum(result[:-1]))
        # Move the residual absorber by representable floats until the same
        # ``math.fsum`` used by structural QA reproduces the annual anchor
        # exactly.  Adding the arithmetic residual once is insufficient for a
        # few large dollar totals when that residual is below the last value's
        # ULP and therefore rounds away.
        for _ in range(128):
            current = math.fsum(result)
            if current == annual:
                break
            direction = math.inf if current < annual else -math.inf
            adjusted = math.nextafter(result[-1], direction)
            if adjusted == result[-1] or adjusted < 0:
                break
            result[-1] = adjusted
        if math.fsum(result) != annual:
            raise VersionBContractError(
                "unable to enforce exact annual-flow floating-point identity"
            )
    return result


def build_hmda_monthly_flows(
    benchmark: pd.DataFrame,
    redfin: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Publish canonical null flows and separately labeled Redfin proxies."""

    work = _benchmark(benchmark)
    indicator = _prepare_redfin(redfin)
    anchors = work.loc[work["variable_id"].isin(HMDA_FLOW_BENCHMARK_IDS)]
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        start = pd.to_datetime(anchor["source_period_start"], errors="coerce")
        if pd.isna(start):
            raise VersionBContractError("HMDA flow anchor requires source_period_start")
        year = pd.Timestamp(start).year
        months = list(pd.date_range(f"{year}-01-31", f"{year}-12-31", freq="ME"))
        county = str(anchor["county_fips"])
        annual_indicator = indicator.loc[
            indicator["county_fips"].eq(county)
            & indicator["month"].dt.year.eq(year)
        ].sort_values("month")
        complete = (
            len(annual_indicator) == 12
            and annual_indicator["month"].nunique() == 12
            and annual_indicator["purchase_value_proxy"].notna().all()
            and annual_indicator["purchase_value_proxy"].sum() > 0
        )
        distributed: dict[str, list[float]] = {}
        if complete and _finite(anchor["value"]):
            distributed["value"] = residual_safe_distribution(
                anchor["value"], annual_indicator["purchase_value_proxy"]
            )
            for field in ("low_estimate", "high_estimate"):
                if _finite(anchor[field]):
                    distributed[field] = residual_safe_distribution(
                        anchor[field], annual_indicator["purchase_value_proxy"]
                    )
        canonical_id = monthly_hist_id(str(anchor["variable_id"]))
        proxy_id = redfin_proxy_id(str(anchor["variable_id"]))
        is_loan_type = str(anchor["variable_id"]) != HMDA_FLOW_BENCHMARK_IDS[0]
        for position, month in enumerate(months):
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=canonical_id,
                    value=None,
                    low=None,
                    high=None,
                    transformation_id=TRANSFORM_HMDA_FLOW_NULL,
                    monthly_method="explicit_unavailable_no_monthly_financed_volume",
                    construction_method=(
                        "Canonical monthly financed purchase-dollar flow remains null"
                    ),
                    quality_flag=(
                        "financed_purchase_value_monthly_source_unavailable"
                    ),
                    notes=(
                        "Redfin all-sale purchase-value weights are published only "
                        "under a separately labeled proxy variable."
                    ),
                    method_version=_method_version(config),
                )
            )
            extra = (
                [annual_indicator.iloc[position]] if complete else []
            )
            proxy_quality = (
                "redfin_purchase_value_proxy_distribution"
                if complete and "value" in distributed
                else "redfin_annual_weight_coverage_or_hmda_anchor_unavailable"
            )
            if is_loan_type:
                proxy_quality = _quality(
                    proxy_quality,
                    "monthly_loan_type_sales_composition_unavailable_total_market_proxy_used",
                )
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=proxy_id,
                    value=(distributed.get("value", [None] * 12)[position]),
                    low=(distributed.get("low_estimate", [None] * 12)[position]),
                    high=(distributed.get("high_estimate", [None] * 12)[position]),
                    transformation_id=TRANSFORM_HMDA_PROXY,
                    monthly_method="annual_hmda_flow_distributed_by_redfin_purchase_value_proxy",
                    construction_method=(
                        "Annual HMDA down-payment dollars allocated by monthly "
                        "Redfin median sale price times homes sold; final month "
                        "absorbs the floating-point residual"
                    ),
                    quality_flag=proxy_quality,
                    extras=extra,
                    notes=(
                        "Proxy is not the canonical financed-purchase flow; Redfin "
                        "includes cash and non-HMDA sales."
                    ),
                    method_version=_method_version(config),
                )
            )
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def interpolate_nmdb_monthly(nmdb: pd.DataFrame) -> pd.DataFrame:
    """Log-interpolate quarterly NMDB UPB to month ends, without extrapolation."""

    observed = _prepare_nmdb(nmdb)
    months = pd.date_range(observed["month"].min(), observed["month"].max(), freq="ME")
    observed_months = pd.DatetimeIndex(observed["month"])
    logged_values = np.log(
        observed["nmdb_ca_all_mortgages_upb"].to_numpy(dtype=float)
    )
    monthly_log = np.interp(
        months.asi8.astype(float),
        observed_months.asi8.astype(float),
        logged_values,
    )
    result = pd.DataFrame(
        {
            "month": months,
            "nmdb_ca_all_mortgages_upb": np.exp(monthly_log),
            "is_interpolated": ~months.isin(observed["month"]),
        }
    )
    metadata = observed.iloc[-1]
    for column in (
        "source_id",
        "source_frequency",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "release_date",
        "source_release_dates",
        "checksum_sha256",
        "quality_flag",
    ):
        if column in observed:
            values = observed[column].dropna().astype(str).unique()
            if len(values) > 1:
                raise VersionBContractError(
                    f"NMDB normalized input mixes {column} values"
                )
            result[column] = metadata.get(column)
    result["quality_flag"] = result.get(
        "quality_flag", "nmdb_all_mortgages_growth_indicator_only"
    )
    return result


def _debt_month_domain(
    nmdb: pd.DataFrame,
    redfin: pd.DataFrame,
    config: Mapping[str, Any] | None,
) -> list[pd.Timestamp]:
    months = sorted(set(nmdb["month"]).intersection(set(redfin["month"])))
    if config and config.get("start_month") is not None:
        start = _month_end(config["start_month"])
        months = [month for month in months if month >= start]
    if config and config.get("end_month") is not None:
        end = _month_end(config["end_month"])
        months = [month for month in months if month <= end]
    return [pd.Timestamp(month) for month in months]


def _build_single_anchor_nmdb_redfin_debt(
    benchmark: pd.DataFrame,
    redfin: pd.DataFrame,
    nmdb: pd.DataFrame,
    *,
    raw_anchor: bool,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Scale raw or PMMS anchors by NMDB growth and county sales share."""

    work = _benchmark(benchmark)
    indicator = _prepare_redfin(redfin)
    monthly_nmdb = interpolate_nmdb_monthly(nmdb)
    scenario_ids = (
        {RAW_DEBT_BENCHMARK_ID}
        if raw_anchor
        else set(PMMS_DEBT_BENCHMARK_IDS.values())
    )
    anchors = work.loc[work["variable_id"].isin(scenario_ids)].copy()
    duplicate = anchors.duplicated(["county_fips", "variable_id"], keep=False)
    if duplicate.any():
        raise VersionBContractError(
            "single-anchor retrospective debt method requires one anchor per "
            "county and variable"
        )
    expected = sorted(anchors["county_fips"].dropna().astype(str).unique())
    if not expected:
        return pd.DataFrame(columns=MONTHLY_FINANCE_COLUMNS)
    domain = _debt_month_domain(monthly_nmdb, indicator, config)
    nmdb_by_month = monthly_nmdb.set_index("month")
    redfin_lookup = {
        (str(row["county_fips"]), pd.Timestamp(row["month"])): row
        for _, row in indicator.iterrows()
    }

    # Shares are either computed over the complete expected county set or null
    # for every county in that month; partial denominators are prohibited.
    shares: dict[tuple[str, pd.Timestamp], float] = {}
    for month in domain:
        one_month = indicator.loc[
            indicator["month"].eq(month) & indicator["county_fips"].isin(expected)
        ]
        complete = (
            len(one_month) == len(expected)
            and set(one_month["county_fips"]) == set(expected)
            and one_month["homes_sold"].notna().all()
            and one_month["homes_sold"].ge(0).all()
            and one_month["homes_sold"].sum() > 0
        )
        if complete:
            denominator = math.fsum(one_month["homes_sold"].astype(float).tolist())
            for _, row in one_month.iterrows():
                shares[(str(row["county_fips"]), month)] = float(
                    row["homes_sold"] / denominator
                )

    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        county = str(anchor["county_fips"])
        anchor_month = _month_end(anchor["reference_date"])
        anchor_nmdb = (
            _number(nmdb_by_month.loc[anchor_month, "nmdb_ca_all_mortgages_upb"])
            if anchor_month in nmdb_by_month.index
            else math.nan
        )
        anchor_share = shares.get((county, anchor_month), math.nan)
        output_id = monthly_hist_id(str(anchor["variable_id"]))
        for month in domain:
            nmdb_row = nmdb_by_month.loc[month]
            redfin_row = redfin_lookup.get((county, month))
            current_share = shares.get((county, month), math.nan)
            ratio = math.nan
            if (
                _finite(anchor["value"])
                and not math.isnan(anchor_nmdb)
                and anchor_nmdb > 0
                and not math.isnan(anchor_share)
                and anchor_share > 0
                and not math.isnan(current_share)
            ):
                ratio = float(
                    (_number(nmdb_row["nmdb_ca_all_mortgages_upb"]) / anchor_nmdb)
                    * (current_share / anchor_share)
                )
            value = _number(anchor["value"]) * ratio if not math.isnan(ratio) else None
            low = _number(anchor["low_estimate"]) * ratio if _finite(anchor["low_estimate"]) and not math.isnan(ratio) else None
            high = _number(anchor["high_estimate"]) * ratio if _finite(anchor["high_estimate"]) and not math.isnan(ratio) else None
            extra: list[Mapping[str, Any] | pd.Series] = [nmdb_row]
            if redfin_row is not None:
                extra.append(redfin_row)
            if raw_anchor:
                quality = (
                    "raw_debt_single_anchor;nmdb_all_mortgages_growth_only;"
                    "county_sales_share_indicator"
                    if not math.isnan(ratio)
                    else "raw_debt_monthly_indicator_or_anchor_unavailable"
                )
                transformation_id = TRANSFORM_RAW_DEBT
                construction_method = (
                    "Version A raw implied-debt anchor scaled by the California "
                    "NMDB all-mortgage UPB ratio and complete-set county Redfin "
                    "homes-sold share ratio"
                )
                notes = (
                    "NMDB all-mortgage UPB supplies growth only and is not a "
                    "calibration level; the output preserves the Version A raw "
                    "debt definition."
                )
            else:
                quality = (
                    "pmms_term_sensitivity_single_anchor;"
                    "nmdb_all_mortgages_growth_only;county_sales_share_indicator"
                    if not math.isnan(ratio)
                    else "pmms_monthly_indicator_or_anchor_unavailable"
                )
                transformation_id = TRANSFORM_PMMS_DEBT
                construction_method = (
                    "Version A PMMS term-sensitivity anchor scaled by the "
                    "California NMDB all-mortgage UPB ratio and complete-set "
                    "county Redfin homes-sold share ratio"
                )
                notes = (
                    "NMDB all-mortgage UPB supplies growth only and is not a "
                    "calibration level; this PMMS result is a sensitivity."
                )
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=output_id,
                    value=value,
                    low=low,
                    high=high,
                    transformation_id=transformation_id,
                    monthly_method="single_anchor_nmdb_growth_times_county_sales_share",
                    construction_method=construction_method,
                    quality_flag=quality,
                    extras=extra,
                    is_interpolated=bool(nmdb_row.get("is_interpolated", False)),
                    notes=notes,
                    method_version=_method_version(config),
                )
            )
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def build_raw_debt_monthly(
    benchmark: pd.DataFrame,
    redfin: pd.DataFrame,
    nmdb: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Scale the populated Version A raw debt anchor without relabeling it PMMS."""

    return _build_single_anchor_nmdb_redfin_debt(
        benchmark,
        redfin,
        nmdb,
        raw_anchor=True,
        config=config,
    )


def build_pmms_debt_sensitivities(
    benchmark: pd.DataFrame,
    redfin: pd.DataFrame,
    nmdb: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Scale each Version A PMMS anchor by NMDB growth and county sales share."""

    return _build_single_anchor_nmdb_redfin_debt(
        benchmark,
        redfin,
        nmdb,
        raw_anchor=False,
        config=config,
    )


def _canonical_null_rows(
    benchmark: pd.DataFrame,
    months: Sequence[pd.Timestamp],
    benchmark_ids: Sequence[str],
    *,
    config: Mapping[str, Any] | None,
) -> pd.DataFrame:
    work = _benchmark(benchmark)
    anchors = work.loc[work["variable_id"].isin(benchmark_ids)]
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        output_id = monthly_hist_id(str(anchor["variable_id"]))
        for month in months:
            rows.append(
                _model_row(
                    anchor,
                    month=month,
                    variable_id=output_id,
                    value=None,
                    low=None,
                    high=None,
                    transformation_id=TRANSFORM_CALIBRATED_NULL,
                    monthly_method="explicit_unavailable_no_canonical_calibration",
                    construction_method=(
                        "Canonical calibrated monthly target remains explicit null"
                    ),
                    quality_flag="universe_compatible_calibration_control_unavailable",
                    notes=(
                        "PMMS debt and equity sensitivities are published separately "
                        "and never substitute for this canonical target."
                    ),
                    method_version=_method_version(config),
                )
            )
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def build_pmms_equity_scenarios(
    benchmark: pd.DataFrame,
    debt: pd.DataFrame,
    zillow: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Update ACS owner values by county ZHVI and subtract matching PMMS debt."""

    if debt.empty:
        return pd.DataFrame(columns=MONTHLY_FINANCE_COLUMNS)
    work = _benchmark(benchmark)
    hpi = _prepare_zillow(zillow)
    scenario_equity_ids = set(PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS.values()) | set(
        PMMS_EQUITY_ALL_BENCHMARK_IDS.values()
    )
    equity_anchors = work.loc[work["variable_id"].isin(scenario_equity_ids)]
    value_anchors = work.loc[
        work["variable_id"].isin(
            [VALUE_MORTGAGED_BENCHMARK_ID, VALUE_NO_MORTGAGE_BENCHMARK_ID]
        )
    ]
    if value_anchors.duplicated(["county_fips", "variable_id"], keep=False).any():
        raise VersionBContractError(
            "HPI value update requires one Version A owner-value anchor per universe"
        )
    value_lookup = value_anchors.set_index(["county_fips", "variable_id"])
    debt_lookup = {
        (str(county), str(variable_id)): group.sort_values("month")
        for (county, variable_id), group in debt.groupby(
            ["county_fips", "variable_id"], sort=False
        )
    }
    hpi_lookup = {
        str(county): group.set_index("month").sort_index()
        for county, group in hpi.groupby("county_fips", sort=False)
    }
    rows: list[dict[str, Any]] = []
    for _, equity_anchor in equity_anchors.iterrows():
        benchmark_id = str(equity_anchor["variable_id"])
        years = next(
            term
            for term in (20, 25, 30)
            if f"pmms_{term}y_" in benchmark_id
        )
        mortgaged = "_mortgaged_" in benchmark_id
        county = str(equity_anchor["county_fips"])
        debt_id = monthly_hist_id(PMMS_DEBT_BENCHMARK_IDS[years])
        county_debt = debt_lookup.get((county, debt_id))
        if county_debt is None:
            continue
        keys = [
            (county, VALUE_MORTGAGED_BENCHMARK_ID),
            (county, VALUE_NO_MORTGAGE_BENCHMARK_ID),
        ]
        if any(key not in value_lookup.index for key in keys):
            continue
        mort_anchor = value_lookup.loc[keys[0]]
        free_anchor = value_lookup.loc[keys[1]]
        anchor_month = _month_end(mort_anchor["reference_date"])
        county_hpi = hpi_lookup.get(county)
        if county_hpi is None:
            continue
        anchor_hpi = (
            _number(county_hpi.loc[anchor_month, "zhvi"])
            if anchor_month in county_hpi.index
            else math.nan
        )
        for _, debt_row in county_debt.iterrows():
            month = _month_end(debt_row["month"])
            hpi_row = county_hpi.loc[month] if month in county_hpi.index else None
            ratio = (
                _number(hpi_row["zhvi"]) / anchor_hpi
                if hpi_row is not None and not math.isnan(anchor_hpi) and anchor_hpi > 0
                else math.nan
            )
            debt_value = _number(debt_row["value"])
            debt_low = _number(debt_row["low_estimate"])
            debt_high = _number(debt_row["high_estimate"])
            mort_value = _number(mort_anchor["value"]) * ratio if not math.isnan(ratio) else math.nan
            free_value = _number(free_anchor["value"]) * ratio if not math.isnan(ratio) else math.nan
            mort_low = _number(mort_anchor["low_estimate"]) * ratio if _finite(mort_anchor["low_estimate"]) and not math.isnan(ratio) else math.nan
            mort_high = _number(mort_anchor["high_estimate"]) * ratio if _finite(mort_anchor["high_estimate"]) and not math.isnan(ratio) else math.nan
            free_low = _number(free_anchor["low_estimate"]) * ratio if _finite(free_anchor["low_estimate"]) and not math.isnan(ratio) else math.nan
            free_high = _number(free_anchor["high_estimate"]) * ratio if _finite(free_anchor["high_estimate"]) and not math.isnan(ratio) else math.nan
            value = (
                mort_value - debt_value
                if not math.isnan(mort_value) and not math.isnan(debt_value)
                else None
            )
            low = (
                mort_low - debt_high
                if not math.isnan(mort_low) and not math.isnan(debt_high)
                else None
            )
            high = (
                mort_high - debt_low
                if not math.isnan(mort_high) and not math.isnan(debt_low)
                else None
            )
            if not mortgaged:
                value = value + free_value if value is not None and not math.isnan(free_value) else None
                low = low + free_low if low is not None and not math.isnan(free_low) else None
                high = high + free_high if high is not None and not math.isnan(free_high) else None
            matched_owner_value = (
                mort_value
                if mortgaged and not math.isnan(mort_value)
                else (
                    mort_value + free_value
                    if not mortgaged
                    and not math.isnan(mort_value)
                    and not math.isnan(free_value)
                    else None
                )
            )
            extras: list[Mapping[str, Any] | pd.Series] = [
                mort_anchor,
                free_anchor,
                debt_row,
            ]
            if hpi_row is not None:
                extras.append(hpi_row)
            equity_row = _model_row(
                    equity_anchor,
                    month=month,
                    variable_id=monthly_hist_id(benchmark_id),
                    value=value,
                    low=low,
                    high=high,
                    transformation_id=TRANSFORM_PMMS_EQUITY,
                    monthly_method="hpi_ratio_updated_owner_values_minus_matching_pmms_debt",
                    construction_method=(
                        "Matched ACS mortgaged and no-mortgage owner-value anchors "
                        "updated by county ZHVI ratio, less matching PMMS term debt"
                    ),
                    quality_flag=(
                        "hpi_updated_pmms_equity_scenario"
                        if value is not None
                        else "hpi_anchor_or_matching_pmms_debt_unavailable"
                    ),
                    extras=extras,
                    is_interpolated=bool(debt_row["is_interpolated"]),
                    notes=(
                        "Owner-stock growth is omitted; this scenario is not the "
                        "canonical calibrated owner-equity series."
                    ),
                    method_version=_method_version(config),
                )
            equity_row["matched_owner_value_stock"] = matched_owner_value
            equity_row["accounting_debt_variable_id"] = str(
                debt_row["variable_id"]
            )
            rows.append(equity_row)
    return pd.DataFrame(rows, columns=MONTHLY_FINANCE_COLUMNS)


def build_retrospective_monthly(
    benchmark: pd.DataFrame,
    redfin: pd.DataFrame,
    zillow: pd.DataFrame,
    nmdb: pd.DataFrame,
    *,
    config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build the complete Version B observation set.

    The returned DataFrame uses the shared monthly-finance schema.  A compact
    diagnostics table is attached at ``result.attrs['diagnostics']``.
    """

    payments = repeat_reference_year_payments(benchmark, config=config)
    rates = repeat_hmda_rates(benchmark, config=config)
    flows = build_hmda_monthly_flows(benchmark, redfin, config=config)
    raw_debt = build_raw_debt_monthly(
        benchmark, redfin, nmdb, config=config
    )
    debt = build_pmms_debt_sensitivities(
        benchmark, redfin, nmdb, config=config
    )
    debt_months = sorted(
        pd.to_datetime(
            pd.concat(
                [raw_debt["month"], debt["month"]], ignore_index=True
            ).dropna()
        ).unique()
    )
    canonical_debt = _canonical_null_rows(
        benchmark,
        debt_months,
        CANONICAL_DEBT_BENCHMARK_IDS,
        config=config,
    )
    equity = build_pmms_equity_scenarios(
        benchmark, debt, zillow, config=config
    )
    equity_months = sorted(pd.to_datetime(equity["month"].dropna()).unique())
    canonical_equity = _canonical_null_rows(
        benchmark,
        equity_months or debt_months,
        CANONICAL_EQUITY_BENCHMARK_IDS,
        config=config,
    )
    frames = [
        payments,
        rates,
        flows,
        raw_debt,
        canonical_debt,
        debt,
        canonical_equity,
        equity,
    ]
    nonempty = [frame for frame in frames if frame is not None and not frame.empty]
    if not nonempty:
        raise VersionBContractError("Version B build produced no observations")
    result = finalize_monthly_finance(
        pd.concat(nonempty, ignore_index=True), version="B", validate=True
    )
    diagnostics = pd.DataFrame(
        [
            {
                "component": name,
                "row_count": len(frame),
                "non_null_count": int(frame["value"].notna().sum()) if not frame.empty else 0,
            }
            for name, frame in (
                ("payment", payments),
                ("hmda_rate", rates),
                ("hmda_flow", flows),
                ("raw_debt", raw_debt),
                ("canonical_debt", canonical_debt),
                ("pmms_debt", debt),
                ("canonical_equity", canonical_equity),
                ("pmms_equity", equity),
            )
        ]
    )
    # Pandas propagates ``attrs`` through slices and compares them during
    # merges.  A DataFrame nested directly in attrs has elementwise equality
    # and makes that comparison ambiguous.  Store records while preserving
    # the pipeline's existing ``.copy() -> DataFrame`` diagnostics contract.
    result.attrs["diagnostics"] = _DiagnosticsPayload(
        diagnostics.to_dict(orient="records")
    )
    return result


__all__ = [
    "CALIBRATED_DEBT_BENCHMARK_ID",
    "CANONICAL_DEBT_BENCHMARK_IDS",
    "CANONICAL_EQUITY_ALL_BENCHMARK_ID",
    "CANONICAL_EQUITY_BENCHMARK_IDS",
    "CANONICAL_EQUITY_MORTGAGED_BENCHMARK_ID",
    "CANONICAL_NULL_BENCHMARK_IDS",
    "HMDA_FLOW_BENCHMARK_IDS",
    "HMDA_RATE_BENCHMARK_IDS",
    "HMDA_SHARE_BENCHMARK_IDS",
    "METHOD_VERSION",
    "PAYMENT_BENCHMARK_IDS",
    "PMMS_DEBT_BENCHMARK_IDS",
    "PMMS_EQUITY_ALL_BENCHMARK_IDS",
    "PMMS_EQUITY_MORTGAGED_BENCHMARK_IDS",
    "RAW_DEBT_BENCHMARK_ID",
    "TRANSFORM_CALIBRATED_NULL",
    "TRANSFORM_HMDA_FLOW_NULL",
    "TRANSFORM_HMDA_PROXY",
    "TRANSFORM_HMDA_RATE",
    "TRANSFORM_PAYMENT",
    "TRANSFORM_PMMS_DEBT",
    "TRANSFORM_PMMS_EQUITY",
    "TRANSFORM_RAW_DEBT",
    "VersionBContractError",
    "build_hmda_monthly_flows",
    "build_pmms_debt_sensitivities",
    "build_pmms_equity_scenarios",
    "build_raw_debt_monthly",
    "build_retrospective_monthly",
    "interpolate_nmdb_monthly",
    "monthly_hist_id",
    "redfin_proxy_id",
    "repeat_hmda_rates",
    "repeat_reference_year_payments",
    "residual_safe_distribution",
]
