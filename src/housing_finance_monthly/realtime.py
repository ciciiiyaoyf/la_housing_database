"""Strict real-time (Version C) housing-finance transformations.

Version C is deliberately separate from the frozen Version A benchmark code.
Every generated observation is keyed by an explicit data cutoff and may use
only source assets available by that cutoff.  Missing source releases produce
an error or an explicitly flagged null; they are never repaired with a future
anchor or a two-sided interpolation endpoint.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
import json
import math
from typing import Any

import numpy as np
import pandas as pd


DATABASE_VERSION = "C. Real-time monthly vintage"
CONFIG_VERSION = "housing_finance_version_c_v1"

PAYMENT_BENCHMARK_ID = (
    "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
)
PAYMENT_MONTHLY_ID = (
    "acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_rt"
)

PAYMENT_CONTROL_BENCHMARK_IDS = (
    "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
    "acs_annual_first_mortgage_debt_service_benchmark",
    "acs_mortgage_debt_service_to_income_benchmark",
)

PAYMENT_CONTROL_IDS = {
    benchmark_id: benchmark_id.removesuffix("_benchmark") + "_monthly_rt"
    for benchmark_id in PAYMENT_CONTROL_BENCHMARK_IDS
}

CANONICAL_DEBT_ID = "acs_implied_first_mortgage_balance_cal_monthly_rt"
CANONICAL_EQUITY_MORTGAGED_ID = (
    "acs_implied_owner_occupied_home_equity_mortgaged_monthly_rt"
)
CANONICAL_EQUITY_ALL_ID = (
    "acs_implied_owner_occupied_home_equity_all_monthly_rt"
)

VALUE_MORTGAGED_BENCHMARK_ID = (
    "acs_aggregate_owner_value_mortgaged_benchmark"
)
VALUE_ALL_BENCHMARK_ID = "acs_aggregate_owner_value_all_benchmark"
RAW_DEBT_BENCHMARK_ID = "acs_implied_first_mortgage_balance_raw_benchmark"

HMDA_RATE_IDS = (
    "hmda_median_downpayment_rate_benchmark",
    "hmda_property_value_weighted_downpayment_rate_benchmark",
    "hmda_median_downpayment_rate_conventional_benchmark",
    "hmda_property_value_weighted_downpayment_rate_conventional_benchmark",
    "hmda_median_downpayment_rate_fha_benchmark",
    "hmda_property_value_weighted_downpayment_rate_fha_benchmark",
    "hmda_median_downpayment_rate_va_benchmark",
    "hmda_property_value_weighted_downpayment_rate_va_benchmark",
)

HMDA_THRESHOLD_SHARE_IDS = tuple(
    "hmda_share_downpayment_below_"
    f"{threshold}pct{loan_type}_benchmark"
    for loan_type in ("", "_conventional", "_fha", "_va")
    for threshold in (3, 5, 10, 20)
)

HMDA_FLOW_IDS = (
    "hmda_aggregate_downpayment_flow_benchmark",
    "hmda_aggregate_downpayment_flow_conventional_benchmark",
    "hmda_aggregate_downpayment_flow_fha_benchmark",
    "hmda_aggregate_downpayment_flow_va_benchmark",
)

PMMS_DEBT_IDS = {
    20: (
        "acs_implied_first_mortgage_balance_raw_pmms_20y_"
        "sensitivity_monthly_rt"
    ),
    25: (
        "acs_implied_first_mortgage_balance_raw_pmms_25y_"
        "sensitivity_monthly_rt"
    ),
    30: (
        "acs_implied_first_mortgage_balance_raw_pmms_30y_"
        "sensitivity_monthly_rt"
    ),
}

PMMS_EQUITY_MORTGAGED_IDS = {
    years: (
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_"
        f"{years}y_scenario_monthly_rt"
    )
    for years in (20, 25, 30)
}

PMMS_EQUITY_ALL_IDS = {
    years: (
        "acs_implied_owner_occupied_home_equity_all_pmms_"
        f"{years}y_scenario_monthly_rt"
    )
    for years in (20, 25, 30)
}

CALIBRATED_NORMALIZED_NULL_IDS = {
    "acs_first_mortgage_debt_per_mortgaged_owner_unit_monthly_rt": (
        "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
        "USD/unit",
    ),
    "acs_first_mortgage_debt_per_owner_unit_monthly_rt": (
        "acs_first_mortgage_debt_per_owner_unit_benchmark",
        "USD/unit",
    ),
    "acs_first_mortgage_debt_per_capita_monthly_rt": (
        "acs_first_mortgage_debt_per_capita_benchmark",
        "USD/person",
    ),
    "acs_first_mortgage_debt_to_owner_income_monthly_rt": (
        "acs_first_mortgage_debt_to_owner_income_benchmark",
        "ratio",
    ),
    "acs_implied_owner_occupied_home_equity_per_owner_unit_monthly_rt": (
        "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
        "USD/unit",
    ),
    "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_monthly_rt": (
        "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark",
        "constant_2024_USD",
    ),
    "acs_implied_owner_occupied_home_equity_all_real_2024_monthly_rt": (
        "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark",
        "constant_2024_USD",
    ),
    "acs_aggregate_ltv_mortgaged_monthly_rt": (
        "acs_aggregate_ltv_mortgaged_benchmark",
        "ratio",
    ),
    "acs_equity_rate_mortgaged_monthly_rt": (
        "acs_equity_rate_mortgaged_benchmark",
        "ratio",
    ),
}

COMPOSITE_RAW_DEBT_ID = "acs_implied_first_mortgage_balance_raw_monthly_rt"

TRANSFORM_PAYMENT = "version_c_latest_payment_carry_v1"
TRANSFORM_HMDA_RATE = "version_c_latest_hmda_rate_carry_v1"
TRANSFORM_HMDA_FLOW_NULL = "version_c_hmda_flow_unavailable_v1"
TRANSFORM_HMDA_PROXY = (
    "version_c_redfin_purchase_value_proxy_nowcast_v1"
)
TRANSFORM_CALIBRATED_NULL = (
    "version_c_calibrated_target_unavailable_v1"
)
TRANSFORM_PMMS_DEBT = "version_c_stepwise_pmms_rate_debt_sensitivity_v1"
TRANSFORM_COMPOSITE_RAW_DEBT = (
    "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1"
)
TRANSFORM_PMMS_EQUITY = "version_c_hpi_updated_pmms_equity_v1"


class VersionCContractError(ValueError):
    """Raised when a real-time construction would violate its contract."""


class NoEligibleVintageError(VersionCContractError):
    """Raised when no archived source was available by the requested cutoff."""


def _as_utc(value: Any, *, name: str) -> pd.Timestamp:
    result = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(result):
        raise VersionCContractError(f"{name} must be a valid timestamp")
    return pd.Timestamp(result)


def _utc_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True)


def _date_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    values = pd.to_datetime(frame[column], errors="coerce")
    if getattr(values.dt, "tz", None) is not None:
        values = values.dt.tz_convert(None)
    return values


def _month_end(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise VersionCContractError(f"invalid month: {value!r}")
    parsed = pd.Timestamp(parsed)
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert(None)
    return parsed.to_period("M").to_timestamp("M")


def normalize_months(
    months: Sequence[Any], as_of_date: Any
) -> tuple[pd.Timestamp, ...]:
    """Normalize target months and prohibit targets beyond the cutoff month."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    cutoff_month = _month_end(cutoff)
    normalized = tuple(sorted({_month_end(month) for month in months}))
    if not normalized:
        raise VersionCContractError("at least one target month is required")
    future = [month for month in normalized if month > cutoff_month]
    if future:
        raise VersionCContractError(
            "Version C target months cannot be later than the cutoff month: "
            f"{future}"
        )
    return normalized


def add_source_availability(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach the first trustworthy source-availability timestamp.

    Precedence is an explicitly supplied ``availability_date``, then a
    provider release date, then a provider-updated timestamp, and finally the
    immutable retrieval timestamp.  A composite release audit containing an
    ``=unavailable`` token cannot use its row-level release date; it falls back
    to retrieval unless an explicit availability date was supplied.
    """
    result = frame.copy()
    availability = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    basis = pd.Series(pd.NA, index=result.index, dtype="string")

    if "availability_date" in result:
        explicit = _utc_series(result["availability_date"])
        mask = explicit.notna()
        availability.loc[mask] = explicit.loc[mask]
        if "availability_date_basis" in result:
            supplied_basis = result["availability_date_basis"].astype("string")
            basis.loc[mask] = supplied_basis.loc[mask].fillna(
                "explicit_availability_date"
            )
        else:
            basis.loc[mask] = "explicit_availability_date"

    release_audit_unavailable = pd.Series(False, index=result.index)
    if "source_release_dates" in result:
        release_audit_unavailable = (
            result["source_release_dates"]
            .astype("string")
            .str.contains(r"(?:^|\|)[^|]+=unavailable(?:\||$)", case=False, na=False)
        )

    for column in ("provider_release_date", "release_date"):
        if column not in result:
            continue
        candidate = _utc_series(result[column])
        mask = availability.isna() & candidate.notna() & ~release_audit_unavailable
        availability.loc[mask] = candidate.loc[mask]
        basis.loc[mask] = column

    for column in ("provider_last_updated", "source_last_updated"):
        if column not in result:
            continue
        candidate = _utc_series(result[column])
        mask = availability.isna() & candidate.notna() & ~release_audit_unavailable
        availability.loc[mask] = candidate.loc[mask]
        basis.loc[mask] = column

    if "retrieved_at" in result:
        candidate = _utc_series(result["retrieved_at"])
        mask = availability.isna() & candidate.notna()
        availability.loc[mask] = candidate.loc[mask]
        basis.loc[mask] = "retrieved_at_conservative"

    missing = availability.isna()
    if missing.any():
        raise VersionCContractError(
            "source availability is missing for rows "
            f"{result.index[missing].tolist()[:20]}"
        )

    if "retrieved_at" in result:
        retrieved = _utc_series(result["retrieved_at"])
        impossible = (
            retrieved.notna()
            & basis.isin(["provider_release_date", "release_date"])
            & availability.gt(retrieved)
        )
        if impossible.any():
            raise VersionCContractError(
                "provider release date cannot be after retrieval"
            )

    result["availability_date"] = availability
    result["availability_date_basis"] = basis
    return result


def select_latest_eligible_snapshot(
    frame: pd.DataFrame,
    as_of_date: Any,
    *,
    source_id: str | None = None,
) -> pd.DataFrame:
    """Return the latest whole archived source snapshot available at cutoff."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    work = add_source_availability(frame)
    if source_id is not None:
        if "source_id" not in work:
            raise VersionCContractError("snapshot rows require source_id")
        work = work.loc[work["source_id"].astype(str).eq(source_id)]
    eligible = work.loc[work["availability_date"].le(cutoff)].copy()
    if eligible.empty:
        label = source_id or "requested source"
        raise NoEligibleVintageError(
            f"no {label} snapshot was available by {cutoff.isoformat()}"
        )

    if "source_id" not in eligible:
        latest = eligible["availability_date"].max()
        return eligible.loc[eligible["availability_date"].eq(latest)].copy()

    selected: list[pd.DataFrame] = []
    for _, group in eligible.groupby("source_id", dropna=False, sort=True):
        latest = group["availability_date"].max()
        candidates = group.loc[group["availability_date"].eq(latest)].copy()
        # A single snapshot can legitimately contain many observation rows.
        # If two independently retrieved assets tie, select one deterministically.
        asset_columns = [
            column
            for column in ("source_vintage", "source_filename", "checksum_sha256")
            if column in candidates
        ]
        if asset_columns:
            candidates["_asset_key"] = candidates[asset_columns].fillna("").astype(
                str
            ).agg("|".join, axis=1)
            keys = candidates["_asset_key"].drop_duplicates()
            if len(keys) > 1:
                chosen = sorted(keys)[-1]
                candidates = candidates.loc[candidates["_asset_key"].eq(chosen)]
            candidates = candidates.drop(columns="_asset_key")
        selected.append(candidates)
    return pd.concat(selected, ignore_index=True)


def _input_frames(
    inputs: pd.DataFrame | Mapping[str, pd.DataFrame] | Iterable[pd.DataFrame],
) -> list[pd.DataFrame]:
    if isinstance(inputs, pd.DataFrame):
        return [inputs]
    if isinstance(inputs, Mapping):
        frames: list[pd.DataFrame] = []
        for name, frame in sorted(inputs.items()):
            tagged = frame.copy()
            if "source_id" not in tagged:
                tagged["source_id"] = str(name)
            frames.append(tagged)
        return frames
    return [frame.copy() for frame in inputs]


def deterministic_vintage_id(
    as_of_date: Any,
    inputs: pd.DataFrame | Mapping[str, pd.DataFrame] | Iterable[pd.DataFrame],
    *,
    config_version: str = CONFIG_VERSION,
) -> str:
    """Build an order-independent ID from the cutoff and eligible assets."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    frames = _input_frames(inputs)
    assets: list[dict[str, str]] = []
    for position, frame in enumerate(frames):
        if frame.empty:
            continue
        tagged = frame.copy()
        if "source_id" not in tagged:
            tagged["source_id"] = f"anonymous_{position}"
        work = add_source_availability(tagged)
        work = work.loc[work["availability_date"].le(cutoff)]
        for source, group in work.groupby("source_id", dropna=False, sort=True):
            latest = group["availability_date"].max()
            latest_rows = group.loc[group["availability_date"].eq(latest)]
            asset_columns = [
                column
                for column in (
                    "source_vintage",
                    "source_filename",
                    "checksum_sha256",
                )
                if column in latest_rows
            ]
            if asset_columns:
                distinct = latest_rows[asset_columns].drop_duplicates()
            else:
                distinct = pd.DataFrame([{}])
            for row in distinct.to_dict("records"):
                assets.append(
                    {
                        "source_id": str(source),
                        "availability_date": latest.isoformat(),
                        **{key: str(value) for key, value in row.items()},
                    }
                )
    assets = sorted(assets, key=lambda item: json.dumps(item, sort_keys=True))
    payload = {
        "as_of_date": cutoff.isoformat(),
        "config_version": config_version,
        "assets": assets,
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"rt_{cutoff:%Y%m%d}_{digest}"


def _variable_column(frame: pd.DataFrame) -> str:
    if "variable_id" in frame:
        return "variable_id"
    if "variable_name" in frame:
        return "variable_name"
    raise VersionCContractError("input rows require variable_id or variable_name")


def _latest_eligible_anchors(
    anchors: pd.DataFrame,
    as_of_date: Any,
    *,
    variable_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    cutoff = _as_utc(as_of_date, name="as_of_date")
    work = anchors.copy()
    variable_column = _variable_column(work)
    work["variable_id"] = work[variable_column].astype("string")
    if variable_ids is not None:
        requested = set(variable_ids)
        work = work.loc[work["variable_id"].isin(requested)]
    if work.empty:
        raise NoEligibleVintageError("no requested benchmark anchors exist")
    work = add_source_availability(work)
    work = work.loc[work["availability_date"].le(cutoff)].copy()
    if work.empty:
        raise NoEligibleVintageError(
            f"no requested benchmark anchor was available by {cutoff.isoformat()}"
        )
    if "county_fips" not in work:
        raise VersionCContractError("benchmark anchors require county_fips")
    work["county_fips"] = work["county_fips"].astype("string").str.zfill(5)
    if "estimate_variant" not in work:
        work["estimate_variant"] = "central"
    for column in ("reference_date", "source_period_start", "source_period_end"):
        work[column] = _date_series(work, column)
    group_columns = ["county_fips", "variable_id", "estimate_variant"]
    sort_columns = [
        *group_columns,
        "reference_date",
        "source_period_end",
        "availability_date",
    ]
    if "benchmark_vintage" in work:
        sort_columns.append("benchmark_vintage")
    work = work.sort_values(sort_columns, na_position="first")
    return work.groupby(group_columns, dropna=False, sort=True).tail(1).copy()


def _value(row: pd.Series, column: str, default: Any = None) -> Any:
    value = row[column] if column in row.index else default
    return default if pd.isna(value) else value


def _numeric_value(row: pd.Series, column: str) -> float | None:
    value = pd.to_numeric(
        pd.Series([_value(row, column, np.nan)]), errors="coerce"
    ).iloc[0]
    return None if pd.isna(value) else float(value)


def _timestamp_max(*values: Any, utc: bool = False) -> pd.Timestamp | Any:
    parsed = pd.to_datetime(pd.Series(list(values)), errors="coerce", utc=utc)
    return pd.NaT if parsed.notna().sum() == 0 else parsed.max()


def _composite_text(*values: Any) -> str | None:
    parts: list[str] = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        for part in str(value).split("|"):
            clean = part.strip()
            if clean and clean not in parts:
                parts.append(clean)
    return "|".join(parts) if parts else None


def _release_audit(row: pd.Series) -> str | None:
    supplied = _value(row, "source_release_dates", None)
    if supplied is not None:
        return str(supplied)
    source_id = _value(row, "source_id", None)
    if source_id is None:
        return None
    release = pd.to_datetime(_value(row, "release_date", pd.NaT), errors="coerce")
    release_text = "unavailable" if pd.isna(release) else release.date().isoformat()
    return f"{source_id}={release_text}"


def _source_frequency(row: pd.Series, default: str | None = None) -> str | None:
    direct = _value(row, "source_frequency", None)
    if direct is not None:
        return str(direct)
    native = _value(row, "native_frequency", None)
    final = _value(row, "final_frequency", None)
    if native is not None and final is not None and str(native) != str(final):
        return f"{native} to {final}"
    if native is not None:
        return str(native)
    return default


def _source_geography(row: pd.Series, default: str | None = None) -> str | None:
    direct = _value(row, "source_geography", None)
    if direct is not None:
        return str(direct)
    geo_id = _value(row, "geo_id", None)
    if geo_id is not None and str(geo_id).upper() == "US":
        return "United States"
    if _value(row, "county_fips", None) is not None:
        return "county"
    return default


def _lineage_id(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return sha256(payload.encode()).hexdigest()


def _output_row(
    *,
    county_fips: str,
    month: pd.Timestamp,
    value: float | None,
    variable_id: str,
    unit: str,
    estimate_variant: str,
    as_of_date: pd.Timestamp,
    vintage_id: str,
    availability_date: Any,
    availability_date_basis: str | None,
    source_id: str | None,
    source_vintage: str | None,
    source_filename: str | None,
    checksum_sha256: str | None,
    source_period_start: Any,
    source_period_end: Any,
    reference_date: Any,
    release_date: Any,
    benchmark_vintage: str | None,
    monthly_method: str,
    construction_method: str,
    is_nowcast: bool,
    is_carried_forward: bool,
    is_scenario: bool,
    is_suppressed: bool,
    quality_flag: str,
    transformation_id: str,
    notes: str,
    reported_value: float | None = None,
    margin_of_error: float | None = None,
    source_frequency: str | None = None,
    source_release_dates: str | None = None,
    source_geography: str | None = None,
    parameter_geography: str | None = None,
    payment_frequency: str | None = None,
    sample_n: float | None = None,
    coverage_rate: float | None = None,
    low_estimate: float | None = None,
    central_estimate: float | None = None,
    high_estimate: float | None = None,
    universe_definition: str | None = None,
    retrieved_at: Any = pd.NaT,
) -> dict[str, Any]:
    lineage = _lineage_id(
        county_fips,
        month.date(),
        variable_id,
        estimate_variant,
        vintage_id,
        checksum_sha256,
        transformation_id,
    )
    return {
        "county_fips": str(county_fips).zfill(5),
        "month": month,
        "value": value,
        "reported_value": reported_value,
        "margin_of_error": margin_of_error,
        "variable_name": variable_id,
        "variable_id": variable_id,
        "unit": unit,
        "estimate_variant": estimate_variant,
        "database_version": DATABASE_VERSION,
        "as_of_date": as_of_date,
        "vintage_id": vintage_id,
        "availability_date": availability_date,
        "availability_date_basis": availability_date_basis,
        "source_id": source_id,
        "source_frequency": source_frequency,
        "source_vintage": source_vintage,
        "source_filename": source_filename,
        "checksum_sha256": checksum_sha256,
        "source_period_start": source_period_start,
        "source_period_end": source_period_end,
        "reference_date": reference_date,
        "release_date": release_date,
        "source_release_dates": source_release_dates,
        "source_geography": source_geography,
        "parameter_geography": parameter_geography,
        "benchmark_vintage": benchmark_vintage,
        "monthly_method": monthly_method,
        "construction_method": construction_method,
        "is_modeled": True,
        "is_nowcast": bool(is_nowcast),
        "is_imputed": False,
        "is_interpolated": False,
        "is_carried_forward": bool(is_carried_forward),
        "is_scenario": bool(is_scenario),
        "is_suppressed": bool(is_suppressed),
        "payment_frequency": payment_frequency,
        "sample_n": sample_n,
        "coverage_rate": coverage_rate,
        "low_estimate": low_estimate,
        "central_estimate": value if central_estimate is None else central_estimate,
        "high_estimate": high_estimate,
        "universe_definition": universe_definition,
        "lookahead_safe": True,
        "quality_flag": quality_flag,
        "transformation_id": transformation_id,
        "lineage_id": lineage,
        "notes": notes,
        "retrieved_at": retrieved_at,
    }


def carry_latest_anchor_monthly(
    anchors: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    source_variable_id: str,
    output_variable_id: str,
    transformation_id: str,
    monthly_method: str,
    unit_override: str | None = None,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Carry the latest eligible annual/ACS anchor without interpolation."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    selected = _latest_eligible_anchors(
        anchors, cutoff, variable_ids=[source_variable_id]
    )
    vintage = vintage_id or deterministic_vintage_id(cutoff, selected)
    rows: list[dict[str, Any]] = []
    for _, anchor in selected.iterrows():
        start = _value(anchor, "source_period_start", pd.NaT)
        end = _value(anchor, "source_period_end", pd.NaT)
        reference = _value(anchor, "reference_date", pd.NaT)
        anchor_value = pd.to_numeric(
            pd.Series([_value(anchor, "value", np.nan)]), errors="coerce"
        ).iloc[0]
        anchor_low = _numeric_value(anchor, "low_estimate")
        anchor_central = _numeric_value(anchor, "central_estimate")
        anchor_high = _numeric_value(anchor, "high_estimate")
        if anchor_central is None and pd.notna(anchor_value):
            anchor_central = float(anchor_value)
        for month in targets:
            before_period = pd.notna(start) and month < _month_end(start)
            value = None if before_period or pd.isna(anchor_value) else float(anchor_value)
            low = None if before_period else anchor_low
            central = None if before_period else anchor_central
            high = None if before_period else anchor_high
            if before_period:
                quality = "before_anchor_source_period"
            else:
                quality = str(_value(anchor, "quality_flag", "modeled_carry"))
            is_suppressed = bool(_value(anchor, "is_suppressed", False))
            rows.append(
                _output_row(
                    county_fips=str(anchor["county_fips"]),
                    month=month,
                    value=value,
                    variable_id=output_variable_id,
                    unit=unit_override or str(_value(anchor, "unit", "unknown")),
                    estimate_variant=str(
                        _value(anchor, "estimate_variant", "central")
                    ),
                    as_of_date=cutoff,
                    vintage_id=vintage,
                    availability_date=anchor["availability_date"],
                    availability_date_basis=str(
                        anchor["availability_date_basis"]
                    ),
                    source_id=str(_value(anchor, "source_id", "")),
                    source_vintage=str(_value(anchor, "source_vintage", "")),
                    source_filename=str(_value(anchor, "source_filename", "")),
                    checksum_sha256=str(
                        _value(anchor, "checksum_sha256", "")
                    ),
                    source_period_start=start,
                    source_period_end=end,
                    reference_date=reference,
                    release_date=_value(anchor, "release_date", pd.NaT),
                    benchmark_vintage=str(
                        _value(anchor, "benchmark_vintage", "")
                    ),
                    monthly_method=monthly_method,
                    construction_method=(
                        "Latest eligible source-frequency anchor carried as a "
                        "level; no future endpoint or interpolation"
                    ),
                    is_nowcast=(pd.notna(end) and month > _month_end(end)),
                    is_carried_forward=(
                        pd.isna(reference) or month != _month_end(reference)
                    ),
                    is_scenario=False,
                    is_suppressed=is_suppressed,
                    quality_flag=quality,
                    transformation_id=transformation_id,
                    notes="Generated only from information available by as_of_date.",
                    reported_value=(
                        None if before_period else _numeric_value(anchor, "reported_value")
                    ),
                    margin_of_error=(
                        None if before_period else _numeric_value(anchor, "margin_of_error")
                    ),
                    source_frequency=_source_frequency(anchor),
                    source_release_dates=_release_audit(anchor),
                    source_geography=_source_geography(anchor, "county"),
                    parameter_geography=str(
                        _value(anchor, "parameter_geography", "county")
                    ),
                    payment_frequency=(
                        str(_value(anchor, "payment_frequency", "")) or None
                    ),
                    sample_n=_numeric_value(anchor, "sample_n"),
                    coverage_rate=_numeric_value(anchor, "coverage_rate"),
                    low_estimate=low,
                    central_estimate=central,
                    high_estimate=high,
                    universe_definition=str(
                        _value(anchor, "universe_definition", "unknown")
                    ),
                    retrieved_at=_value(anchor, "retrieved_at", pd.NaT),
                )
            )
    return pd.DataFrame(rows)


def carry_latest_payment(
    anchors: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    return carry_latest_anchor_monthly(
        anchors,
        months,
        as_of_date,
        source_variable_id=PAYMENT_BENCHMARK_ID,
        output_variable_id=PAYMENT_MONTHLY_ID,
        transformation_id=TRANSFORM_PAYMENT,
        monthly_method="latest_released_payment_benchmark_carry_forward",
        vintage_id=vintage_id,
    )


def carry_latest_payment_controls(
    anchors: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Carry all three published payment controls using the cutoff contract."""
    variable_column = _variable_column(anchors)
    available_ids = set(anchors[variable_column].astype(str))
    frames = [
        carry_latest_anchor_monthly(
            anchors,
            months,
            as_of_date,
            source_variable_id=benchmark_id,
            output_variable_id=PAYMENT_CONTROL_IDS[benchmark_id],
            transformation_id=TRANSFORM_PAYMENT,
            monthly_method="latest_released_payment_control_carry_forward",
            vintage_id=vintage_id,
        )
        for benchmark_id in PAYMENT_CONTROL_BENCHMARK_IDS
        if benchmark_id in available_ids
    ]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _monthly_id(benchmark_id: str) -> str:
    if not benchmark_id.endswith("_benchmark"):
        raise VersionCContractError(
            f"benchmark variable does not end in _benchmark: {benchmark_id}"
        )
    return benchmark_id[: -len("_benchmark")] + "_monthly_rt"


def carry_latest_hmda_rates(
    anchors: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Repeat latest eligible HMDA rates, including all loan-type variants."""
    variable_column = _variable_column(anchors)
    available_ids = set(anchors[variable_column].astype(str))
    frames: list[pd.DataFrame] = []
    for benchmark_id in (*HMDA_RATE_IDS, *HMDA_THRESHOLD_SHARE_IDS):
        if benchmark_id not in available_ids:
            continue
        frames.append(
            carry_latest_anchor_monthly(
                anchors,
                months,
                as_of_date,
                source_variable_id=benchmark_id,
                output_variable_id=_monthly_id(benchmark_id),
                transformation_id=TRANSFORM_HMDA_RATE,
                monthly_method="latest_released_hmda_rate_repeated_not_divided",
                unit_override=(
                    "share"
                    if benchmark_id in HMDA_THRESHOLD_SHARE_IDS
                    else "rate"
                ),
                vintage_id=vintage_id,
            )
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _null_rows(
    counties: Sequence[str],
    months: Sequence[Any],
    as_of_date: Any,
    *,
    variable_ids: Sequence[str],
    quality_flag: str,
    transformation_id: str,
    notes: str,
    vintage_id: str,
    units: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    rows = [
        _output_row(
            county_fips=county,
            month=month,
            value=None,
            variable_id=variable_id,
            unit=(units or {}).get(variable_id, "USD"),
            estimate_variant="central",
            as_of_date=cutoff,
            vintage_id=vintage_id,
            availability_date=pd.NaT,
            availability_date_basis=None,
            source_id=None,
            source_vintage=None,
            source_filename=None,
            checksum_sha256=None,
            source_period_start=pd.NaT,
            source_period_end=pd.NaT,
            reference_date=pd.NaT,
            release_date=pd.NaT,
            benchmark_vintage=None,
            monthly_method="explicit_unavailable_no_substitution",
            construction_method="No method-compliant canonical input available",
            is_nowcast=False,
            is_carried_forward=False,
            is_scenario=False,
            is_suppressed=False,
            quality_flag=quality_flag,
            transformation_id=transformation_id,
            notes=notes,
        )
        for county in counties
        for month in targets
        for variable_id in variable_ids
    ]
    return pd.DataFrame(rows)


def canonical_unavailable_rows(
    counties: Sequence[str],
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str,
) -> pd.DataFrame:
    """Materialize required canonical nulls without relabeling proxies."""
    flow_ids = [_monthly_id(variable_id) for variable_id in HMDA_FLOW_IDS]
    flow = _null_rows(
        counties,
        months,
        as_of_date,
        variable_ids=flow_ids,
        quality_flag="financed_purchase_value_monthly_source_unavailable",
        transformation_id=TRANSFORM_HMDA_FLOW_NULL,
        notes=(
            "Canonical HMDA monthly flow is null; a Redfin sales-value proxy, "
            "when available, is published under a separate variable ID."
        ),
        vintage_id=vintage_id,
    )
    calibrated = _null_rows(
        counties,
        months,
        as_of_date,
        variable_ids=[
            CANONICAL_DEBT_ID,
            CANONICAL_EQUITY_MORTGAGED_ID,
            CANONICAL_EQUITY_ALL_ID,
            *CALIBRATED_NORMALIZED_NULL_IDS,
        ],
        quality_flag="calibrated_debt_anchor_unavailable",
        transformation_id=TRANSFORM_CALIBRATED_NULL,
        notes=(
            "No universe-compatible calibrated debt anchor is available; "
            "PMMS sensitivities remain separate scenarios."
        ),
        vintage_id=vintage_id,
        units={
            CANONICAL_DEBT_ID: "USD",
            CANONICAL_EQUITY_MORTGAGED_ID: "USD",
            CANONICAL_EQUITY_ALL_ID: "USD",
            **{
                variable_id: unit
                for variable_id, (_, unit) in CALIBRATED_NORMALIZED_NULL_IDS.items()
            },
        },
    )
    return pd.concat([flow, calibrated], ignore_index=True)


def _redfin_proxy_rows(
    redfin_rows: pd.DataFrame, as_of_date: Any
) -> pd.DataFrame:
    cutoff = _as_utc(as_of_date, name="as_of_date")
    selected = select_latest_eligible_snapshot(redfin_rows, cutoff)
    selected = selected.rename(
        columns={
            "PERIOD_END": "period_end",
            "HOMES_SOLD": "homes_sold",
            "MEDIAN_SALE_PRICE": "median_sale_price",
        }
    )
    if "county_fips" not in selected:
        raise VersionCContractError(
            "Redfin proxy rows require a versioned county_fips crosswalk"
        )
    selected["county_fips"] = (
        selected["county_fips"].astype("string").str.zfill(5)
    )
    date_column = next(
        (
            column
            for column in ("month", "observation_date", "period_end")
            if column in selected
        ),
        None,
    )
    if date_column is None:
        raise VersionCContractError("Redfin proxy rows require an observation date")
    selected["month"] = selected[date_column].map(_month_end)
    selected = selected.loc[
        selected["month"].le(cutoff.tz_convert(None))
    ].copy()

    if "purchase_value_proxy" in selected:
        selected["purchase_value_proxy"] = pd.to_numeric(
            selected["purchase_value_proxy"], errors="coerce"
        )
        return selected

    if {"homes_sold", "median_sale_price"}.issubset(selected.columns):
        selected["purchase_value_proxy"] = (
            pd.to_numeric(selected["homes_sold"], errors="coerce")
            * pd.to_numeric(selected["median_sale_price"], errors="coerce")
        )
        return selected

    if {"variable_id", "value"}.issubset(selected.columns):
        keys = ["county_fips", "month"]
        wide = selected.pivot_table(
            index=keys, columns="variable_id", values="value", aggfunc="first"
        ).reset_index()
        required = {"homes_sold", "median_sale_price"}
        if not required.issubset(wide.columns):
            raise VersionCContractError(
                "Redfin long rows require homes_sold and median_sale_price"
            )
        wide["purchase_value_proxy"] = (
            pd.to_numeric(wide["homes_sold"], errors="coerce")
            * pd.to_numeric(wide["median_sale_price"], errors="coerce")
        )
        for column in (
            "availability_date",
            "availability_date_basis",
            "source_id",
            "source_vintage",
            "source_filename",
            "checksum_sha256",
            "release_date",
        ):
            if column in selected:
                wide[column] = selected[column].dropna().iloc[0]
        return wide
    raise VersionCContractError(
        "Redfin proxy requires purchase_value_proxy or sales count and price"
    )


def _proxy_flow_id(benchmark_id: str) -> str:
    base = benchmark_id[: -len("_benchmark")]
    return f"{base}_redfin_sales_value_proxy_monthly_rt"


def _residual_corrected_allocations(
    annual_value: float | None, weights: pd.DataFrame
) -> dict[pd.Timestamp, float | None]:
    months = list(weights["month"].map(_month_end))
    if annual_value is None:
        return dict.fromkeys(months)
    numeric = pd.to_numeric(weights["purchase_value_proxy"], errors="coerce")
    denominator = math.fsum(numeric.tolist())
    if not math.isfinite(denominator) or denominator <= 0:
        return dict.fromkeys(months)
    raw = [float(annual_value * weight / denominator) for weight in numeric]
    if raw:
        raw[-1] = float(annual_value - math.fsum(raw[:-1]))
        for _ in range(128):
            current = math.fsum(raw)
            if current == annual_value:
                break
            direction = math.inf if current < annual_value else -math.inf
            adjusted = math.nextafter(raw[-1], direction)
            if adjusted == raw[-1] or adjusted < 0:
                break
            raw[-1] = adjusted
        if math.fsum(raw) != annual_value:
            raise VersionCContractError(
                "unable to enforce exact annual-flow floating-point identity"
            )
    return dict(zip(months, raw, strict=True))


def build_redfin_purchase_value_proxy_flows(
    hmda_flow_anchors: pd.DataFrame,
    redfin_rows: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Allocate/nowcast HMDA flows under a separately labeled proxy ID."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    anchors = _latest_eligible_anchors(
        hmda_flow_anchors, cutoff, variable_ids=HMDA_FLOW_IDS
    )
    proxy = _redfin_proxy_rows(redfin_rows, cutoff)
    vintage = vintage_id or deterministic_vintage_id(
        cutoff, {"hmda": anchors, "redfin": proxy}
    )
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        county = str(anchor["county_fips"])
        source_end = _value(anchor, "source_period_end", pd.NaT)
        if pd.isna(source_end):
            raise VersionCContractError("HMDA flow anchor requires source_period_end")
        source_year = pd.Timestamp(source_end).year
        county_proxy = proxy.loc[proxy["county_fips"].eq(county)].copy()
        county_proxy = county_proxy.sort_values(
            ["month", "availability_date"], kind="stable"
        ).drop_duplicates("month", keep="last")
        benchmark_weights = county_proxy.loc[
            county_proxy["month"].dt.year.eq(source_year)
        ]
        valid_weights = benchmark_weights.dropna(
            subset=["purchase_value_proxy"]
        ).sort_values("month")
        complete = valid_weights["month"].nunique() == 12
        denominator = pd.to_numeric(
            valid_weights["purchase_value_proxy"], errors="coerce"
        ).sum(min_count=1)
        anchor_value = pd.to_numeric(
            pd.Series([_value(anchor, "value", np.nan)]), errors="coerce"
        ).iloc[0]
        annual_central = _numeric_value(anchor, "central_estimate")
        if annual_central is None and pd.notna(anchor_value):
            annual_central = float(anchor_value)
        annual_low = _numeric_value(anchor, "low_estimate")
        annual_high = _numeric_value(anchor, "high_estimate")
        finalized_central: dict[pd.Timestamp, float | None] = {}
        finalized_low: dict[pd.Timestamp, float | None] = {}
        finalized_high: dict[pd.Timestamp, float | None] = {}
        if complete and pd.notna(denominator) and denominator > 0:
            ordered_weights = valid_weights
            finalized_central = _residual_corrected_allocations(
                annual_central, ordered_weights
            )
            finalized_low = _residual_corrected_allocations(
                annual_low, ordered_weights
            )
            finalized_high = _residual_corrected_allocations(
                annual_high, ordered_weights
            )
        proxy_availability = proxy["availability_date"].max()
        combined_availability = max(
            pd.Timestamp(anchor["availability_date"]),
            pd.Timestamp(proxy_availability),
        )
        for month in targets:
            target = county_proxy.loc[county_proxy["month"].eq(month)]
            target_weight = (
                pd.to_numeric(target["purchase_value_proxy"], errors="coerce").iloc[0]
                if not target.empty
                else np.nan
            )
            reason = "redfin_sales_value_proxy_nowcast"
            output_value: float | None = None
            output_low: float | None = None
            output_high: float | None = None
            if pd.isna(anchor_value):
                reason = str(_value(anchor, "quality_flag", "hmda_anchor_unavailable"))
            elif not complete or pd.isna(denominator) or denominator <= 0:
                reason = "annual_proxy_weights_incomplete"
            elif month.year < source_year:
                reason = "before_hmda_anchor_year"
            elif pd.isna(target_weight):
                reason = "monthly_proxy_weight_unavailable"
            elif month.year == source_year:
                output_value = finalized_central.get(month)
                output_low = finalized_low.get(month)
                output_high = finalized_high.get(month)
                reason = "redfin_sales_value_proxy_finalized_allocation"
            else:
                output_value = (
                    None
                    if annual_central is None
                    else float(annual_central / denominator * target_weight)
                )
                output_low = (
                    None
                    if annual_low is None
                    else float(annual_low / denominator * target_weight)
                )
                output_high = (
                    None
                    if annual_high is None
                    else float(annual_high / denominator * target_weight)
                )
            benchmark_id = str(anchor["variable_id"])
            proxy_source = target.iloc[-1] if not target.empty else proxy.iloc[-1]
            rows.append(
                _output_row(
                    county_fips=county,
                    month=month,
                    value=output_value,
                    variable_id=_proxy_flow_id(benchmark_id),
                    unit="USD",
                    estimate_variant="redfin_sales_value_proxy",
                    as_of_date=cutoff,
                    vintage_id=vintage,
                    availability_date=combined_availability,
                    availability_date_basis=(
                        f"{anchor['availability_date_basis']}|"
                        f"{proxy_source['availability_date_basis']}"
                    ),
                    source_id=(
                        f"{_value(anchor, 'source_id', '')}|"
                        f"{_value(proxy_source, 'source_id', '')}"
                    ),
                    source_vintage=(
                        f"{_value(anchor, 'source_vintage', '')}|"
                        f"{_value(proxy_source, 'source_vintage', '')}"
                    ),
                    source_filename=(
                        f"{_value(anchor, 'source_filename', '')}|"
                        f"{_value(proxy_source, 'source_filename', '')}"
                    ),
                    checksum_sha256=(
                        f"{_value(anchor, 'checksum_sha256', '')}|"
                        f"{_value(proxy_source, 'checksum_sha256', '')}"
                    ),
                    source_period_start=_value(
                        anchor, "source_period_start", pd.NaT
                    ),
                    source_period_end=source_end,
                    reference_date=_value(anchor, "reference_date", pd.NaT),
                    release_date=_timestamp_max(
                        _value(anchor, "release_date", pd.NaT),
                        _value(proxy_source, "release_date", pd.NaT),
                    ),
                    benchmark_vintage=str(
                        _value(anchor, "benchmark_vintage", "")
                    ),
                    monthly_method="redfin_sales_count_times_median_price_proxy",
                    construction_method=(
                        "Annual HMDA flow divided by same-year Redfin "
                        "homes-sold times median-price weights; later months "
                        "use the frozen scale as a proxy nowcast"
                    ),
                    is_nowcast=month.year > source_year,
                    is_carried_forward=False,
                    is_scenario=True,
                    is_suppressed=bool(
                        _value(anchor, "is_suppressed", False)
                    ),
                    quality_flag=reason,
                    transformation_id=TRANSFORM_HMDA_PROXY,
                    notes=(
                        "Proxy is not actual financed purchase value and never "
                        "populates the canonical HMDA flow variable."
                    ),
                    source_frequency=_composite_text(
                        _source_frequency(anchor),
                        _source_frequency(proxy_source, "monthly"),
                    ),
                    source_release_dates=_composite_text(
                        _release_audit(anchor), _release_audit(proxy_source)
                    ),
                    source_geography=_composite_text(
                        _source_geography(anchor, "county"),
                        _source_geography(proxy_source, "county"),
                    ),
                    parameter_geography=_composite_text(
                        _value(anchor, "parameter_geography", "county"),
                        _source_geography(proxy_source, "county"),
                    ),
                    sample_n=_numeric_value(anchor, "sample_n"),
                    coverage_rate=_numeric_value(anchor, "coverage_rate"),
                    low_estimate=output_low,
                    central_estimate=output_value,
                    high_estimate=output_high,
                    universe_definition=str(
                        _value(anchor, "universe_definition", "unknown")
                    ),
                    retrieved_at=_timestamp_max(
                        _value(anchor, "retrieved_at", pd.NaT),
                        _value(proxy_source, "retrieved_at", pd.NaT),
                        utc=True,
                    ),
                )
            )
    return pd.DataFrame(rows)


def _indicator_date_column(frame: pd.DataFrame) -> str:
    for column in ("observation_date", "month", "period_end"):
        if column in frame:
            return column
    raise VersionCContractError("indicator rows require an observation date")


def stepwise_indicator_values(
    indicator_rows: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    source_id: str | None = None,
) -> pd.DataFrame:
    """Select the last released indicator observation at each target month."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    selected = select_latest_eligible_snapshot(
        indicator_rows, cutoff, source_id=source_id
    )
    date_column = _indicator_date_column(selected)
    selected["indicator_observation_date"] = _date_series(selected, date_column)
    selected["indicator_value"] = pd.to_numeric(
        selected["value"], errors="coerce"
    )
    if "county_fips" not in selected:
        selected["county_fips"] = pd.NA
    selected["_indicator_group"] = (
        selected["county_fips"].astype("string").fillna("__ALL__")
    )
    group_values = selected["_indicator_group"].drop_duplicates()
    rows: list[dict[str, Any]] = []
    cutoff_naive = cutoff.tz_convert(None)
    for county in group_values:
        group = selected.loc[selected["_indicator_group"].eq(county)].sort_values(
            "indicator_observation_date"
        )
        for month in targets:
            observation_cutoff = min(month, cutoff_naive)
            eligible = group.loc[
                group["indicator_observation_date"].le(observation_cutoff)
            ]
            if eligible.empty:
                continue
            row = eligible.iloc[-1].copy()
            row["month"] = month
            row["indicator_staleness_months"] = (
                (month.year - row["indicator_observation_date"].year) * 12
                + month.month
                - row["indicator_observation_date"].month
            )
            rows.append(row.to_dict())
    return pd.DataFrame(rows)


def _annuity_factor(rate_value: float, months: int) -> float:
    annual_rate = rate_value / 100.0 if abs(rate_value) > 1 else rate_value
    monthly_rate = annual_rate / 12.0
    if monthly_rate == 0:
        return float(months)
    return float((1.0 - (1.0 + monthly_rate) ** (-months)) / monthly_rate)


def build_pmms_debt_scenarios(
    payment_rows: pd.DataFrame,
    pmms_rows: pd.DataFrame,
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
    rate_variable_id: str = "mortgage_rate_30y_monthly_median",
) -> pd.DataFrame:
    """Capitalize carried P&I using stepwise, eligible PMMS sensitivities."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    payments = payment_rows.loc[
        payment_rows["variable_id"].eq(PAYMENT_MONTHLY_ID)
    ].copy()
    if payments.empty:
        return pd.DataFrame()
    rates = pmms_rows.copy()
    if "variable_id" in rates and rates["variable_id"].eq(rate_variable_id).any():
        rates = rates.loc[rates["variable_id"].eq(rate_variable_id)]
    target_months = sorted(payments["month"].map(_month_end).unique())
    step = stepwise_indicator_values(rates, target_months, cutoff)
    if step.empty:
        raise NoEligibleVintageError("no eligible PMMS rate observation")
    # PMMS is national, so merge by target month only.
    step = step.sort_values("indicator_observation_date").drop_duplicates(
        "month", keep="last"
    )
    vintage = vintage_id or deterministic_vintage_id(
        cutoff, {"payment": payments, "pmms": rates}
    )
    rows: list[dict[str, Any]] = []
    for _, payment in payments.iterrows():
        month = _month_end(payment["month"])
        rate_match = step.loc[step["month"].eq(month)]
        if rate_match.empty:
            continue
        rate = rate_match.iloc[-1]
        payment_value = pd.to_numeric(
            pd.Series([payment["value"]]), errors="coerce"
        ).iloc[0]
        rate_value = pd.to_numeric(
            pd.Series([rate["indicator_value"]]), errors="coerce"
        ).iloc[0]
        payment_low = _numeric_value(payment, "low_estimate")
        payment_high = _numeric_value(payment, "high_estimate")
        combined_availability = max(
            pd.Timestamp(payment["availability_date"]),
            pd.Timestamp(rate["availability_date"]),
        )
        for years, variable_id in PMMS_DEBT_IDS.items():
            annuity_factor = (
                None if pd.isna(rate_value) else _annuity_factor(rate_value, years * 12)
            )
            value = (
                None
                if pd.isna(payment_value) or annuity_factor is None
                else float(payment_value * annuity_factor)
            )
            low = (
                None
                if payment_low is None or annuity_factor is None
                else float(payment_low * annuity_factor)
            )
            high = (
                None
                if payment_high is None or annuity_factor is None
                else float(payment_high * annuity_factor)
            )
            rows.append(
                _output_row(
                    county_fips=str(payment["county_fips"]),
                    month=month,
                    value=value,
                    variable_id=variable_id,
                    unit="USD",
                    estimate_variant=f"pmms_{years}y_sensitivity",
                    as_of_date=cutoff,
                    vintage_id=vintage,
                    availability_date=combined_availability,
                    availability_date_basis=(
                        f"{payment['availability_date_basis']}|"
                        f"{rate['availability_date_basis']}"
                    ),
                    source_id=(
                        f"{payment['source_id']}|{_value(rate, 'source_id', '')}"
                    ),
                    source_vintage=(
                        f"{payment['source_vintage']}|"
                        f"{_value(rate, 'source_vintage', '')}"
                    ),
                    source_filename=(
                        f"{payment['source_filename']}|"
                        f"{_value(rate, 'source_filename', '')}"
                    ),
                    checksum_sha256=(
                        f"{payment['checksum_sha256']}|"
                        f"{_value(rate, 'checksum_sha256', '')}"
                    ),
                    source_period_start=payment["source_period_start"],
                    source_period_end=payment["source_period_end"],
                    reference_date=payment["reference_date"],
                    release_date=_timestamp_max(
                        payment["release_date"],
                        _value(rate, "release_date", pd.NaT),
                    ),
                    benchmark_vintage=str(payment["benchmark_vintage"]),
                    monthly_method="stepwise_latest_released_pmms_rate",
                    construction_method=(
                        "Carried aggregate P&I multiplied by a fixed-term "
                        "annuity factor at the last eligible PMMS rate"
                    ),
                    is_nowcast=bool(payment["is_nowcast"])
                    or int(rate["indicator_staleness_months"]) > 0,
                    is_carried_forward=bool(payment["is_carried_forward"])
                    or int(rate["indicator_staleness_months"]) > 0,
                    is_scenario=True,
                    is_suppressed=bool(payment["is_suppressed"]),
                    quality_flag="pmms_origination_rate_sensitivity",
                    transformation_id=TRANSFORM_PMMS_DEBT,
                    notes=(
                        "PMMS is an origination-rate sensitivity and is not "
                        "the canonical outstanding-stock debt series."
                    ),
                    source_frequency=_composite_text(
                        _value(payment, "source_frequency", None),
                        _source_frequency(rate, "weekly to monthly"),
                    ),
                    source_release_dates=_composite_text(
                        _value(payment, "source_release_dates", None),
                        _release_audit(rate),
                    ),
                    source_geography=_composite_text(
                        _value(payment, "source_geography", "county"),
                        _source_geography(rate, "United States"),
                    ),
                    parameter_geography=_composite_text(
                        _value(payment, "parameter_geography", "county"),
                        _source_geography(rate, "United States"),
                    ),
                    payment_frequency=_value(
                        payment, "payment_frequency", None
                    ),
                    sample_n=_numeric_value(payment, "sample_n"),
                    coverage_rate=_numeric_value(payment, "coverage_rate"),
                    low_estimate=low,
                    central_estimate=value,
                    high_estimate=high,
                    universe_definition=str(
                        _value(payment, "universe_definition", "unknown")
                    ),
                    retrieved_at=_timestamp_max(
                        _value(payment, "retrieved_at", pd.NaT),
                        _value(rate, "retrieved_at", pd.NaT),
                        utc=True,
                    ),
                )
            )
    return pd.DataFrame(rows)


def build_composite_raw_debt_nowcast(
    debt_scenarios: pd.DataFrame,
    benchmarks: pd.DataFrame,
    pmms_rows: pd.DataFrame,
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Benchmark the PMMS term envelope to the eligible Version A raw stock.

    Central uses the 25-year capitalization.  The low endpoint uses the
    payment-low bound capitalized at 20 years, and the high endpoint uses the
    payment-high bound capitalized at 30 years.  Each path is scaled separately
    to its eligible Version A raw-debt value at the anchor reference month.
    The reference envelope is built internally even when the requested output
    months exclude that month.  This remains a raw PMMS sensitivity and is
    never the California-calibrated canonical debt target.
    """
    if debt_scenarios.empty:
        return pd.DataFrame()
    cutoff = _as_utc(as_of_date, name="as_of_date")
    vintage = vintage_id or deterministic_vintage_id(
        cutoff,
        {"benchmarks": benchmarks, "pmms": pmms_rows},
    )
    try:
        anchors = _latest_eligible_anchors(
            benchmarks,
            cutoff,
            variable_ids=[RAW_DEBT_BENCHMARK_ID],
        )
    except NoEligibleVintageError:
        anchors = pd.DataFrame()

    reference_scenarios = pd.DataFrame()
    if not anchors.empty:
        missing_reference = anchors["reference_date"].isna()
        if missing_reference.any():
            raise VersionCContractError(
                "raw-debt benchmark requires a reference_date"
            )
        reference_months = sorted(
            anchors["reference_date"].map(_month_end).unique()
        )
        reference_payments = carry_latest_payment(
            benchmarks,
            reference_months,
            cutoff,
            vintage_id=vintage,
        )
        try:
            reference_scenarios = build_pmms_debt_scenarios(
                reference_payments,
                pmms_rows,
                cutoff,
                vintage_id=vintage,
            )
        except NoEligibleVintageError:
            # Keep the composite output explicit-null rather than reverting to
            # an unbenchmarked PMMS level when the reference envelope cannot
            # be reconstructed from cutoff-eligible observations.
            reference_scenarios = pd.DataFrame()

    anchor_by_county = {
        str(row["county_fips"]): row
        for _, row in anchors.iterrows()
    }

    def scale(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        result = numerator / denominator
        return float(result) if math.isfinite(result) else None

    rows: list[dict[str, Any]] = []
    key = ["county_fips", "month", "vintage_id"]
    for _, group in debt_scenarios.groupby(key, sort=True, dropna=False):
        by_id = group.set_index("variable_id", drop=False)
        if not set(PMMS_DEBT_IDS.values()).issubset(by_id.index):
            continue
        low_row = by_id.loc[PMMS_DEBT_IDS[20]]
        central_row = by_id.loc[PMMS_DEBT_IDS[25]]
        high_row = by_id.loc[PMMS_DEBT_IDS[30]]
        county = str(central_row["county_fips"])
        month = _month_end(central_row["month"])
        anchor = anchor_by_county.get(county)
        reference = pd.NaT
        reference_group = pd.DataFrame()
        reference_by_id = pd.DataFrame()
        if anchor is not None:
            reference = _month_end(anchor["reference_date"])
            if not reference_scenarios.empty:
                reference_group = reference_scenarios.loc[
                    reference_scenarios["county_fips"].astype(str).eq(county)
                    & reference_scenarios["month"].map(_month_end).eq(reference)
                ]
            if not reference_group.empty:
                reference_by_id = reference_group.set_index(
                    "variable_id", drop=False
                )

        reference_complete = (
            not reference_by_id.empty
            and set(PMMS_DEBT_IDS.values()).issubset(reference_by_id.index)
        )
        anchor_central = (
            _numeric_value(anchor, "value") if anchor is not None else None
        )
        anchor_low = (
            _numeric_value(anchor, "low_estimate") if anchor is not None else None
        )
        anchor_high = (
            _numeric_value(anchor, "high_estimate") if anchor is not None else None
        )
        if reference_complete:
            reference_low_row = reference_by_id.loc[PMMS_DEBT_IDS[20]]
            reference_central_row = reference_by_id.loc[PMMS_DEBT_IDS[25]]
            reference_high_row = reference_by_id.loc[PMMS_DEBT_IDS[30]]
            central_scale = scale(
                anchor_central,
                _numeric_value(reference_central_row, "value"),
            )
            low_scale = scale(
                anchor_low,
                _numeric_value(reference_low_row, "low_estimate"),
            )
            high_scale = scale(
                anchor_high,
                _numeric_value(reference_high_row, "high_estimate"),
            )
        else:
            reference_central_row = None
            central_scale = low_scale = high_scale = None

        target_central = _numeric_value(central_row, "value")
        target_low = _numeric_value(low_row, "low_estimate")
        target_high = _numeric_value(high_row, "high_estimate")
        scales = (central_scale, low_scale, high_scale)
        scales_complete = all(value is not None for value in scales)
        if month == reference:
            central = anchor_central if central_scale is not None else None
            low = anchor_low if low_scale is not None else None
            high = anchor_high if high_scale is not None else None
        else:
            central = (
                None
                if target_central is None or central_scale is None
                else float(target_central * central_scale)
            )
            low = (
                None
                if target_low is None or low_scale is None
                else float(target_low * low_scale)
            )
            high = (
                None
                if target_high is None or high_scale is None
                else float(target_high * high_scale)
            )

        if scales_complete:
            quality_flag = "benchmark_scaled_composite_pmms_raw_debt_sensitivity"
        elif any(value is not None for value in scales):
            quality_flag = (
                "partial_benchmark_scaled_composite_pmms_raw_debt_sensitivity"
            )
        else:
            quality_flag = "raw_debt_benchmark_or_reference_envelope_unavailable"

        availability_values = [central_row["availability_date"]]
        if anchor is not None:
            availability_values.append(anchor["availability_date"])
        if reference_complete:
            availability_values.append(
                reference_central_row["availability_date"]
            )
        availability_date = _timestamp_max(*availability_values, utc=True)
        is_suppressed = bool(group["is_suppressed"].fillna(False).any())
        if anchor is not None:
            is_suppressed = is_suppressed or bool(
                _value(anchor, "is_suppressed", False)
            )
        output = _output_row(
                county_fips=str(central_row["county_fips"]),
                month=month,
                value=central,
                variable_id=COMPOSITE_RAW_DEBT_ID,
                unit="USD",
                estimate_variant="composite_pmms_raw_debt_sensitivity",
                as_of_date=cutoff,
                vintage_id=vintage,
                availability_date=availability_date,
                availability_date_basis=_composite_text(
                    _value(central_row, "availability_date_basis", None),
                    _value(anchor, "availability_date_basis", None)
                    if anchor is not None
                    else None,
                    _value(reference_central_row, "availability_date_basis", None)
                    if reference_complete
                    else None,
                ),
                source_id=_composite_text(
                    _value(anchor, "source_id", None) if anchor is not None else None,
                    _value(central_row, "source_id", None),
                ),
                source_vintage=_composite_text(
                    _value(anchor, "source_vintage", None)
                    if anchor is not None
                    else None,
                    _value(central_row, "source_vintage", None),
                ),
                source_filename=_composite_text(
                    _value(anchor, "source_filename", None)
                    if anchor is not None
                    else None,
                    _value(central_row, "source_filename", None),
                ),
                checksum_sha256=_composite_text(
                    _value(anchor, "checksum_sha256", None)
                    if anchor is not None
                    else None,
                    _value(central_row, "checksum_sha256", None),
                ),
                source_period_start=(
                    _value(anchor, "source_period_start", pd.NaT)
                    if anchor is not None
                    else central_row["source_period_start"]
                ),
                source_period_end=(
                    _value(anchor, "source_period_end", pd.NaT)
                    if anchor is not None
                    else central_row["source_period_end"]
                ),
                reference_date=reference,
                release_date=_timestamp_max(
                    _value(anchor, "release_date", pd.NaT)
                    if anchor is not None
                    else pd.NaT,
                    _value(central_row, "release_date", pd.NaT),
                ),
                benchmark_vintage=(
                    str(_value(anchor, "benchmark_vintage", ""))
                    if anchor is not None
                    else str(central_row["benchmark_vintage"])
                ),
                monthly_method="benchmark_scaled_payment_pmms_term_envelope_nowcast",
                construction_method=(
                    "Separately scale payment-central at 25 years payment-low "
                    "at 20 years and payment-high at 30 years to the eligible "
                    "Version A raw-debt anchor at its reference month"
                ),
                is_nowcast=bool(group["is_nowcast"].fillna(False).any()),
                is_carried_forward=bool(
                    group["is_carried_forward"].fillna(False).any()
                )
                or (pd.notna(reference) and month != reference),
                is_scenario=True,
                is_suppressed=is_suppressed,
                quality_flag=quality_flag,
                transformation_id=TRANSFORM_COMPOSITE_RAW_DEBT,
                notes=(
                    "Version A raw debt is the level anchor. This remains a "
                    "PMMS sensitivity and never populates calibrated debt."
                ),
                source_frequency=_composite_text(
                    _value(anchor, "source_frequency", None)
                    if anchor is not None
                    else None,
                    _value(central_row, "source_frequency", None),
                ),
                source_release_dates=_composite_text(
                    _release_audit(anchor) if anchor is not None else None,
                    _value(central_row, "source_release_dates", None),
                ),
                source_geography=_composite_text(
                    _source_geography(anchor, "county")
                    if anchor is not None
                    else None,
                    _value(central_row, "source_geography", None),
                ),
                parameter_geography=_composite_text(
                    _value(anchor, "parameter_geography", None)
                    if anchor is not None
                    else None,
                    _value(central_row, "parameter_geography", None),
                ),
                payment_frequency=_value(
                    central_row, "payment_frequency", None
                ),
                sample_n=(
                    _numeric_value(anchor, "sample_n")
                    if anchor is not None
                    else _numeric_value(central_row, "sample_n")
                ),
                coverage_rate=(
                    _numeric_value(anchor, "coverage_rate")
                    if anchor is not None
                    else _numeric_value(central_row, "coverage_rate")
                ),
                low_estimate=low,
                central_estimate=central,
                high_estimate=high,
                universe_definition=str(
                    _value(anchor, "universe_definition", "unknown")
                    if anchor is not None
                    else _value(central_row, "universe_definition", "unknown")
                ),
                retrieved_at=_timestamp_max(
                    _value(anchor, "retrieved_at", pd.NaT)
                    if anchor is not None
                    else pd.NaT,
                    _value(central_row, "retrieved_at", pd.NaT),
                    _value(reference_central_row, "retrieved_at", pd.NaT)
                    if reference_complete
                    else pd.NaT,
                    utc=True,
                ),
            )
        output["raw_debt_reference_month"] = reference
        output["raw_debt_central_scale_factor"] = central_scale
        output["raw_debt_low_scale_factor"] = low_scale
        output["raw_debt_high_scale_factor"] = high_scale
        rows.append(output)
    return pd.DataFrame(rows)


def update_owner_values_with_hpi(
    value_anchors: pd.DataFrame,
    hpi_rows: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Update matched owner-value anchors with an eligible county HPI ratio."""
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    anchors = _latest_eligible_anchors(
        value_anchors,
        cutoff,
        variable_ids=[VALUE_MORTGAGED_BENCHMARK_ID, VALUE_ALL_BENCHMARK_ID],
    )
    hpi = select_latest_eligible_snapshot(hpi_rows, cutoff)
    if "county_fips" not in hpi:
        raise VersionCContractError("HPI rows require county_fips")
    hpi["county_fips"] = hpi["county_fips"].astype("string").str.zfill(5)
    date_column = _indicator_date_column(hpi)
    hpi["hpi_observation_date"] = _date_series(hpi, date_column)
    hpi["hpi_value"] = pd.to_numeric(hpi["value"], errors="coerce")
    vintage = vintage_id or deterministic_vintage_id(
        cutoff, {"value": anchors, "hpi": hpi}
    )
    rows: list[dict[str, Any]] = []
    cutoff_naive = cutoff.tz_convert(None)
    for _, anchor in anchors.iterrows():
        county = str(anchor["county_fips"])
        county_hpi = hpi.loc[hpi["county_fips"].eq(county)].sort_values(
            "hpi_observation_date"
        )
        reference = pd.Timestamp(anchor["reference_date"])
        base_hpi_rows = county_hpi.loc[
            county_hpi["hpi_observation_date"].le(_month_end(reference))
        ]
        if base_hpi_rows.empty:
            continue
        base_hpi = base_hpi_rows.iloc[-1]
        anchor_value = pd.to_numeric(
            pd.Series([anchor["value"]]), errors="coerce"
        ).iloc[0]
        anchor_low = _numeric_value(anchor, "low_estimate")
        anchor_high = _numeric_value(anchor, "high_estimate")
        for month in targets:
            latest = county_hpi.loc[
                county_hpi["hpi_observation_date"].le(min(month, cutoff_naive))
            ]
            if latest.empty:
                continue
            target_hpi = latest.iloc[-1]
            denominator = float(base_hpi["hpi_value"])
            ratio = (
                None
                if pd.isna(target_hpi["hpi_value"]) or denominator == 0
                else float(target_hpi["hpi_value"] / denominator)
            )
            value = (
                None
                if pd.isna(anchor_value)
                or ratio is None
                else float(anchor_value * ratio)
            )
            low = (
                None if anchor_low is None or ratio is None else anchor_low * ratio
            )
            high = (
                None if anchor_high is None or ratio is None else anchor_high * ratio
            )
            source_id = str(anchor["variable_id"])
            universe = "mortgaged" if source_id == VALUE_MORTGAGED_BENCHMARK_ID else "all"
            combined_availability = max(
                pd.Timestamp(anchor["availability_date"]),
                pd.Timestamp(target_hpi["availability_date"]),
            )
            rows.append(
                {
                    "county_fips": county,
                    "month": month,
                    "value": value,
                    "low_estimate": low,
                    "central_estimate": value,
                    "high_estimate": high,
                    "value_universe": universe,
                    "as_of_date": cutoff,
                    "vintage_id": vintage,
                    "availability_date": combined_availability,
                    "availability_date_basis": (
                        f"{anchor['availability_date_basis']}|"
                        f"{target_hpi['availability_date_basis']}"
                    ),
                    "source_id": (
                        f"{_value(anchor, 'source_id', '')}|"
                        f"{_value(target_hpi, 'source_id', '')}"
                    ),
                    "source_vintage": (
                        f"{_value(anchor, 'source_vintage', '')}|"
                        f"{_value(target_hpi, 'source_vintage', '')}"
                    ),
                    "source_filename": (
                        f"{_value(anchor, 'source_filename', '')}|"
                        f"{_value(target_hpi, 'source_filename', '')}"
                    ),
                    "checksum_sha256": (
                        f"{_value(anchor, 'checksum_sha256', '')}|"
                        f"{_value(target_hpi, 'checksum_sha256', '')}"
                    ),
                    "source_period_start": anchor["source_period_start"],
                    "source_period_end": anchor["source_period_end"],
                    "reference_date": reference,
                    "release_date": _timestamp_max(
                        _value(anchor, "release_date", pd.NaT),
                        _value(target_hpi, "release_date", pd.NaT),
                    ),
                    "source_release_dates": _composite_text(
                        _release_audit(anchor), _release_audit(target_hpi)
                    ),
                    "source_frequency": _composite_text(
                        _source_frequency(anchor),
                        _source_frequency(target_hpi, "monthly"),
                    ),
                    "source_geography": _composite_text(
                        _source_geography(anchor, "county"),
                        _source_geography(target_hpi, "county"),
                    ),
                    "parameter_geography": _composite_text(
                        _value(anchor, "parameter_geography", "county"),
                        _source_geography(target_hpi, "county"),
                    ),
                    "retrieved_at": _timestamp_max(
                        _value(anchor, "retrieved_at", pd.NaT),
                        _value(target_hpi, "retrieved_at", pd.NaT),
                        utc=True,
                    ),
                    "sample_n": _numeric_value(anchor, "sample_n"),
                    "coverage_rate": _numeric_value(anchor, "coverage_rate"),
                    "universe_definition": str(
                        _value(anchor, "universe_definition", "unknown")
                    ),
                    "benchmark_vintage": str(
                        _value(anchor, "benchmark_vintage", "")
                    ),
                    "hpi_observation_date": target_hpi["hpi_observation_date"],
                    "is_carried_forward": (
                        _month_end(target_hpi["hpi_observation_date"]) < month
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_pmms_equity_scenarios(
    debt_scenarios: pd.DataFrame,
    value_anchors: pd.DataFrame,
    hpi_rows: pd.DataFrame,
    as_of_date: Any,
    *,
    vintage_id: str | None = None,
) -> pd.DataFrame:
    """Subtract scenario debt from HPI-updated matched owner-value stocks."""
    if debt_scenarios.empty:
        return pd.DataFrame()
    cutoff = _as_utc(as_of_date, name="as_of_date")
    months = sorted(debt_scenarios["month"].map(_month_end).unique())
    vintage = vintage_id or deterministic_vintage_id(
        cutoff,
        {"debt": debt_scenarios, "values": value_anchors, "hpi": hpi_rows},
    )
    values = update_owner_values_with_hpi(
        value_anchors, hpi_rows, months, cutoff, vintage_id=vintage
    )
    rows: list[dict[str, Any]] = []
    for years, debt_id in PMMS_DEBT_IDS.items():
        debts = debt_scenarios.loc[debt_scenarios["variable_id"].eq(debt_id)]
        for _, debt in debts.iterrows():
            county = str(debt["county_fips"])
            month = _month_end(debt["month"])
            matched = values.loc[
                values["county_fips"].eq(county) & values["month"].eq(month)
            ]
            for universe, output_id in (
                ("mortgaged", PMMS_EQUITY_MORTGAGED_IDS[years]),
                ("all", PMMS_EQUITY_ALL_IDS[years]),
            ):
                stock = matched.loc[matched["value_universe"].eq(universe)]
                if stock.empty:
                    continue
                stock_row = stock.iloc[-1]
                debt_value = pd.to_numeric(
                    pd.Series([debt["value"]]), errors="coerce"
                ).iloc[0]
                stock_value = pd.to_numeric(
                    pd.Series([stock_row["value"]]), errors="coerce"
                ).iloc[0]
                debt_low = _numeric_value(debt, "low_estimate")
                debt_high = _numeric_value(debt, "high_estimate")
                stock_low = _numeric_value(stock_row, "low_estimate")
                stock_high = _numeric_value(stock_row, "high_estimate")
                value = (
                    None
                    if pd.isna(debt_value) or pd.isna(stock_value)
                    else float(stock_value - debt_value)
                )
                low = (
                    None
                    if stock_low is None or debt_high is None
                    else float(stock_low - debt_high)
                )
                high = (
                    None
                    if stock_high is None or debt_low is None
                    else float(stock_high - debt_low)
                )
                combined_availability = max(
                    pd.Timestamp(debt["availability_date"]),
                    pd.Timestamp(stock_row["availability_date"]),
                )
                equity_row = _output_row(
                        county_fips=county,
                        month=month,
                        value=value,
                        variable_id=output_id,
                        unit="USD",
                        estimate_variant=f"pmms_{years}y_scenario",
                        as_of_date=cutoff,
                        vintage_id=vintage,
                        availability_date=combined_availability,
                        availability_date_basis=(
                            f"{debt['availability_date_basis']}|"
                            f"{stock_row['availability_date_basis']}"
                        ),
                        source_id=(
                            f"{debt['source_id']}|{stock_row['source_id']}"
                        ),
                        source_vintage=(
                            f"{debt['source_vintage']}|"
                            f"{stock_row['source_vintage']}"
                        ),
                        source_filename=(
                            f"{debt['source_filename']}|"
                            f"{stock_row['source_filename']}"
                        ),
                        checksum_sha256=(
                            f"{debt['checksum_sha256']}|"
                            f"{stock_row['checksum_sha256']}"
                        ),
                        source_period_start=stock_row["source_period_start"],
                        source_period_end=stock_row["source_period_end"],
                        reference_date=stock_row["reference_date"],
                        release_date=_timestamp_max(
                            debt["release_date"], stock_row["release_date"]
                        ),
                        benchmark_vintage=stock_row["benchmark_vintage"],
                        monthly_method="hpi_ratio_minus_pmms_scenario_debt",
                        construction_method=(
                            "Latest eligible owner-value anchor updated by an "
                            "eligible HPI ratio less separately labeled PMMS debt"
                        ),
                        is_nowcast=bool(debt["is_nowcast"]),
                        is_carried_forward=bool(debt["is_carried_forward"])
                        or bool(stock_row["is_carried_forward"]),
                        is_scenario=True,
                        is_suppressed=bool(debt["is_suppressed"]),
                        quality_flag="hpi_updated_pmms_equity_scenario",
                        transformation_id=TRANSFORM_PMMS_EQUITY,
                        notes=(
                            "Scenario equity is not the canonical calibrated "
                            "owner-equity series."
                        ),
                        source_frequency=_composite_text(
                            _value(debt, "source_frequency", None),
                            _value(stock_row, "source_frequency", None),
                        ),
                        source_release_dates=_composite_text(
                            _value(debt, "source_release_dates", None),
                            _value(stock_row, "source_release_dates", None),
                        ),
                        source_geography=_composite_text(
                            _value(debt, "source_geography", None),
                            _value(stock_row, "source_geography", None),
                        ),
                        parameter_geography=_composite_text(
                            _value(debt, "parameter_geography", None),
                            _value(stock_row, "parameter_geography", None),
                        ),
                        payment_frequency=_value(
                            debt, "payment_frequency", None
                        ),
                        sample_n=_numeric_value(debt, "sample_n"),
                        coverage_rate=_numeric_value(debt, "coverage_rate"),
                        low_estimate=low,
                        central_estimate=value,
                        high_estimate=high,
                        universe_definition=str(
                            _value(stock_row, "universe_definition", "unknown")
                        ),
                        retrieved_at=_timestamp_max(
                            _value(debt, "retrieved_at", pd.NaT),
                            _value(stock_row, "retrieved_at", pd.NaT),
                            utc=True,
                        ),
                    )
                equity_row["matched_owner_value_stock"] = (
                    None if pd.isna(stock_value) else float(stock_value)
                )
                equity_row["accounting_debt_variable_id"] = str(
                    debt["variable_id"]
                )
                rows.append(equity_row)
    return pd.DataFrame(rows)


def _validate_output(frame: pd.DataFrame, cutoff: pd.Timestamp) -> None:
    if frame.empty:
        raise VersionCContractError("Version C build produced no rows")
    if not frame["variable_id"].astype(str).str.endswith("_monthly_rt").all():
        raise VersionCContractError("every Version C ID must end in _monthly_rt")
    if frame["is_interpolated"].fillna(True).any():
        raise VersionCContractError("Version C interpolation is prohibited")
    nonnull_availability = frame["availability_date"].notna()
    available = _utc_series(frame.loc[nonnull_availability, "availability_date"])
    if available.gt(cutoff).any():
        raise VersionCContractError("Version C output uses a future source")
    key = [
        "county_fips",
        "month",
        "vintage_id",
        "variable_id",
        "estimate_variant",
    ]
    if frame.duplicated(key).any():
        raise VersionCContractError(f"duplicate Version C key: {key}")


def build_realtime_monthly(
    benchmarks: pd.DataFrame,
    months: Sequence[Any],
    as_of_date: Any,
    *,
    redfin_rows: pd.DataFrame | None = None,
    pmms_rows: pd.DataFrame | None = None,
    hpi_rows: pd.DataFrame | None = None,
    config_version: str = CONFIG_VERSION,
) -> pd.DataFrame:
    """Build strict Version C outputs from source-faithful benchmark rows.

    The canonical calibrated debt, canonical equity, and canonical monthly
    financed-purchase flows remain explicit nulls.  Redfin flow proxies and
    PMMS debt/equity sensitivities use separate IDs and cannot substitute for
    those canonical targets.
    """
    cutoff = _as_utc(as_of_date, name="as_of_date")
    targets = normalize_months(months, cutoff)
    inputs: dict[str, pd.DataFrame] = {"benchmarks": benchmarks}
    if redfin_rows is not None:
        inputs["redfin"] = redfin_rows
    if pmms_rows is not None:
        inputs["pmms"] = pmms_rows
    if hpi_rows is not None:
        inputs["hpi"] = hpi_rows
    vintage = deterministic_vintage_id(
        cutoff, inputs, config_version=config_version
    )

    payment = carry_latest_payment(
        benchmarks, targets, cutoff, vintage_id=vintage
    )
    payment_controls = carry_latest_payment_controls(
        benchmarks, targets, cutoff, vintage_id=vintage
    )
    rates = carry_latest_hmda_rates(
        benchmarks, targets, cutoff, vintage_id=vintage
    )
    counties = sorted(
        benchmarks["county_fips"].dropna().astype("string").str.zfill(5).unique()
    )
    canonical_nulls = canonical_unavailable_rows(
        counties, targets, cutoff, vintage_id=vintage
    )
    frames = [payment, payment_controls, rates, canonical_nulls]

    variable_column = _variable_column(benchmarks)
    benchmark_ids = set(benchmarks[variable_column].astype(str))
    if redfin_rows is not None and benchmark_ids.intersection(HMDA_FLOW_IDS):
        frames.append(
            build_redfin_purchase_value_proxy_flows(
                benchmarks,
                redfin_rows,
                targets,
                cutoff,
                vintage_id=vintage,
            )
        )

    scenario_debt = pd.DataFrame()
    if pmms_rows is not None:
        scenario_debt = build_pmms_debt_scenarios(
            payment, pmms_rows, cutoff, vintage_id=vintage
        )
        frames.append(scenario_debt)
        frames.append(
            build_composite_raw_debt_nowcast(
                scenario_debt,
                benchmarks,
                pmms_rows,
                cutoff,
                vintage_id=vintage,
            )
        )
    if (
        hpi_rows is not None
        and not scenario_debt.empty
        and {
            VALUE_MORTGAGED_BENCHMARK_ID,
            VALUE_ALL_BENCHMARK_ID,
        }.issubset(benchmark_ids)
    ):
        frames.append(
            build_pmms_equity_scenarios(
                scenario_debt,
                benchmarks,
                hpi_rows,
                cutoff,
                vintage_id=vintage,
            )
        )

    result = pd.concat(
        [frame for frame in frames if frame is not None and not frame.empty],
        ignore_index=True,
        sort=False,
    )
    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result = result.sort_values(
        ["county_fips", "month", "variable_id", "estimate_variant"]
    ).reset_index(drop=True)
    _validate_output(result, cutoff)
    return result


__all__ = [
    "CANONICAL_DEBT_ID",
    "CANONICAL_EQUITY_ALL_ID",
    "CANONICAL_EQUITY_MORTGAGED_ID",
    "CALIBRATED_NORMALIZED_NULL_IDS",
    "COMPOSITE_RAW_DEBT_ID",
    "DATABASE_VERSION",
    "HMDA_FLOW_IDS",
    "HMDA_RATE_IDS",
    "HMDA_THRESHOLD_SHARE_IDS",
    "NoEligibleVintageError",
    "PAYMENT_MONTHLY_ID",
    "PAYMENT_CONTROL_BENCHMARK_IDS",
    "PAYMENT_CONTROL_IDS",
    "PMMS_DEBT_IDS",
    "PMMS_EQUITY_ALL_IDS",
    "PMMS_EQUITY_MORTGAGED_IDS",
    "VersionCContractError",
    "add_source_availability",
    "build_pmms_debt_scenarios",
    "build_composite_raw_debt_nowcast",
    "build_pmms_equity_scenarios",
    "build_realtime_monthly",
    "build_redfin_purchase_value_proxy_flows",
    "canonical_unavailable_rows",
    "carry_latest_anchor_monthly",
    "carry_latest_hmda_rates",
    "carry_latest_payment",
    "carry_latest_payment_controls",
    "deterministic_vintage_id",
    "normalize_months",
    "select_latest_eligible_snapshot",
    "stepwise_indicator_values",
    "update_owner_values_with_hpi",
]
