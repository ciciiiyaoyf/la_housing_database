"""Pure calculations for the source-faithful Version A finance benchmarks.

The functions in this module intentionally stop at the published source
period.  They do not interpolate, carry values forward, distribute annual
flows to months, or substitute county observations with broader geographies.
Any broader PUMS/NMDB parameter must be supplied explicitly by the caller and
identified in the observation metadata.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_BENCHMARK_METADATA_FIELDS: tuple[str, ...] = (
    "county_fips",
    "month",
    "value",
    "variable_name",
    "database_version",
    "source_frequency",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
    "source_geography",
    "parameter_geography",
    "monthly_method",
    "is_modeled",
    "is_nowcast",
    "benchmark_vintage",
    "sample_n",
    "coverage_rate",
    "low_estimate",
    "central_estimate",
    "high_estimate",
    "universe_definition",
    "notes",
)


class BenchmarkMetadataError(ValueError):
    """Raised when a Version A observation violates its metadata contract."""


def _missing(value: Any) -> bool:
    """Return a scalar missing-value decision."""
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def _finite_float(value: Any, name: str) -> float:
    if _missing(value):
        return math.nan
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric or missing") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite or missing")
    return result


def _numeric_series_strict(series: pd.Series, name: str) -> pd.Series:
    """Convert numeric text while distinguishing missing from invalid values."""
    blank = series.map(lambda value: isinstance(value, str) and not value.strip())
    reported = series.notna() & ~blank
    numeric = pd.to_numeric(series.mask(blank), errors="coerce").astype(float)
    invalid = reported & numeric.isna()
    if invalid.any():
        examples = series.loc[invalid].astype(str).drop_duplicates().head(3).tolist()
        raise ValueError(f"{name} contains nonnumeric values: {examples}")
    if np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError(f"{name} must contain only finite values or missing values")
    return numeric


def annuity_factor(
    annual_rate: float | int | None,
    remaining_months: float | int | None,
) -> float:
    """Return the mortgage annuity factor for a decimal annual rate.

    ``annual_rate`` is a decimal (for example, ``0.06`` for 6 percent).
    Missing inputs produce ``NaN``.  At a zero rate the continuous limit is
    exactly the number of remaining months, avoiding a divide-by-zero.
    """
    rate = _finite_float(annual_rate, "annual_rate")
    months = _finite_float(remaining_months, "remaining_months")
    if math.isnan(rate) or math.isnan(months):
        return math.nan
    if rate < 0:
        raise ValueError("annual_rate cannot be negative")
    if months <= 0:
        raise ValueError("remaining_months must be positive")
    if rate == 0:
        return months

    monthly_rate = rate / 12.0
    # expm1/log1p is stable for rates close to zero.
    return -math.expm1(-months * math.log1p(monthly_rate)) / monthly_rate


def adjust_pums_housing_dollars(
    values: pd.Series | float | int | None,
    adjhsg: pd.Series | float | int | None,
) -> pd.Series | float:
    """Apply the ACS PUMS housing-dollar adjustment ``X * ADJHSG / 1e6``.

    Missing amounts or adjustment factors stay missing.  Nonpositive
    adjustment factors and negative dollar inputs are structural errors rather
    than values to repair silently.
    """
    if isinstance(values, pd.Series) or isinstance(adjhsg, pd.Series):
        if isinstance(values, pd.Series) and isinstance(adjhsg, pd.Series):
            index = values.index.union(adjhsg.index)
        elif isinstance(values, pd.Series):
            index = values.index
        else:
            index = adjhsg.index

        if isinstance(values, pd.Series):
            amount = _numeric_series_strict(values.reindex(index), "values")
        else:
            amount = pd.Series(values, index=index, dtype="object")
            amount = _numeric_series_strict(amount, "values")
        if isinstance(adjhsg, pd.Series):
            factor = _numeric_series_strict(adjhsg.reindex(index), "adjhsg")
        else:
            factor = pd.Series(adjhsg, index=index, dtype="object")
            factor = _numeric_series_strict(factor, "adjhsg")

        if (amount.dropna() < 0).any():
            raise ValueError("PUMS housing-dollar values cannot be negative")
        if (factor.dropna() <= 0).any():
            raise ValueError("ADJHSG must be positive when reported")
        return amount * factor / 1_000_000.0

    amount_value = _finite_float(values, "values")
    factor_value = _finite_float(adjhsg, "adjhsg")
    if math.isnan(amount_value) or math.isnan(factor_value):
        return math.nan
    if amount_value < 0:
        raise ValueError("PUMS housing-dollar values cannot be negative")
    if factor_value <= 0:
        raise ValueError("ADJHSG must be positive when reported")
    return amount_value * factor_value / 1_000_000.0


def _normalise_flag(value: Any) -> tuple[str, Any]:
    if _missing(value):
        return ("missing", None)
    if isinstance(value, (bool, np.bool_)):
        return ("bool", bool(value))
    if isinstance(value, (int, np.integer)):
        return ("number", int(value))
    if isinstance(value, (float, np.floating)) and math.isfinite(float(value)):
        number = float(value)
        return (
            ("number", int(number))
            if number.is_integer()
            else ("number", number)
        )
    return ("text", str(value).strip().casefold())


def _decode_inclusion_flag(
    series: pd.Series,
    included_values: Sequence[Any],
    excluded_values: Sequence[Any],
) -> pd.Series:
    included = {_normalise_flag(value) for value in included_values}
    excluded = {_normalise_flag(value) for value in excluded_values}
    if included & excluded:
        raise ValueError("included_values and excluded_values overlap")

    decoded = pd.Series(pd.NA, index=series.index, dtype="boolean")
    normalised = series.map(_normalise_flag)
    decoded.loc[normalised.isin(included)] = True
    decoded.loc[normalised.isin(excluded)] = False
    return decoded


def calculate_pums_first_mortgage_pi(
    frame: pd.DataFrame,
    *,
    mortgage_payment_col: str = "MRGP",
    property_tax_col: str = "TAXAMT",
    insurance_col: str = "INSP",
    adjhsg_col: str = "ADJHSG",
    tax_included_col: str = "MRGT",
    insurance_included_col: str = "MRGI",
    included_flag_values: Sequence[Any] = (True, 1, "1", "Y", "YES"),
    excluded_flag_values: Sequence[Any] = (False, 2, "2", "N", "NO"),
) -> pd.DataFrame:
    """Calculate record-level first-mortgage principal and interest.

    The input must already be restricted to owner-occupied housing records
    with a first mortgage.  Unknown or missing inclusion flags leave P&I
    missing; the function never guesses whether escrow is embedded.  ``SMP``
    is intentionally not added to the primary first-lien payment.
    """
    required = {
        mortgage_payment_col,
        property_tax_col,
        insurance_col,
        adjhsg_col,
        tax_included_col,
        insurance_included_col,
    }
    missing_columns = sorted(required.difference(frame.columns))
    if missing_columns:
        raise KeyError(f"missing PUMS columns: {missing_columns}")

    result = frame.copy()
    result["mrgp_adj"] = adjust_pums_housing_dollars(
        result[mortgage_payment_col], result[adjhsg_col]
    )
    result["tax_annual_adj"] = adjust_pums_housing_dollars(
        result[property_tax_col], result[adjhsg_col]
    )
    result["insurance_annual_adj"] = adjust_pums_housing_dollars(
        result[insurance_col], result[adjhsg_col]
    )
    result["tax_monthly_adj"] = result["tax_annual_adj"] / 12.0
    result["insurance_monthly_adj"] = result["insurance_annual_adj"] / 12.0

    result["tax_included"] = _decode_inclusion_flag(
        result[tax_included_col],
        included_flag_values,
        excluded_flag_values,
    )
    result["insurance_included"] = _decode_inclusion_flag(
        result[insurance_included_col],
        included_flag_values,
        excluded_flag_values,
    )

    tax_deduction = result["tax_monthly_adj"].where(
        result["tax_included"].fillna(False), 0.0
    )
    tax_deduction = tax_deduction.mask(result["tax_included"].isna())
    insurance_deduction = result["insurance_monthly_adj"].where(
        result["insurance_included"].fillna(False), 0.0
    )
    insurance_deduction = insurance_deduction.mask(
        result["insurance_included"].isna()
    )
    result["pi_first"] = (
        result["mrgp_adj"] - tax_deduction - insurance_deduction
    ).clip(lower=0.0)
    return result


def weighted_pi_share(
    frame: pd.DataFrame,
    *,
    pi_col: str = "pi_first",
    selected_owner_cost_col: str = "smoc_adj",
    weight_col: str = "WGTP",
) -> dict[str, float | int | bool | None]:
    """Return the complete-case weighted P&I share and coverage diagnostics."""
    required = {pi_col, selected_owner_cost_col, weight_col}
    missing_columns = sorted(required.difference(frame.columns))
    if missing_columns:
        raise KeyError(f"missing weighted-share columns: {missing_columns}")

    pi = _numeric_series_strict(frame[pi_col], pi_col)
    smoc = _numeric_series_strict(
        frame[selected_owner_cost_col], selected_owner_cost_col
    )
    weight = _numeric_series_strict(frame[weight_col], weight_col)
    if (pi.dropna() < 0).any():
        raise ValueError("first-mortgage P&I cannot be negative")
    if (smoc.dropna() < 0).any():
        raise ValueError("selected owner costs cannot be negative")
    if (weight.dropna() < 0).any():
        raise ValueError("WGTP cannot be negative")

    positive_weight = weight.notna() & weight.gt(0)
    valid = (
        positive_weight
        & pi.notna()
        & smoc.notna()
        & smoc.gt(0)
    )
    input_n = int(len(frame))
    sample_n = int(valid.sum())
    valid_weight = float(weight.loc[valid].sum()) if sample_n else 0.0
    available_weight = (
        float(weight.loc[positive_weight].sum()) if positive_weight.any() else 0.0
    )
    numerator = (
        float((weight.loc[valid] * pi.loc[valid]).sum())
        if sample_n
        else math.nan
    )
    denominator = (
        float((weight.loc[valid] * smoc.loc[valid]).sum())
        if sample_n
        else math.nan
    )
    share = (
        numerator / denominator
        if not math.isnan(denominator) and denominator > 0
        else math.nan
    )
    out_of_bounds = (
        bool(share < 0 or share > 1) if not math.isnan(share) else None
    )
    return {
        "pi_share": share,
        "weighted_pi_total": numerator,
        "weighted_smoc_total": denominator,
        "input_n": input_n,
        "sample_n": sample_n,
        "coverage_rate": sample_n / input_n if input_n else math.nan,
        "weighted_coverage_rate": (
            valid_weight / available_weight if available_weight > 0 else math.nan
        ),
        "share_out_of_bounds": out_of_bounds,
    }


def calculate_pums_pi_share(
    frame: pd.DataFrame,
    *,
    selected_owner_cost_col: str = "SMOCP",
    weight_col: str = "WGTP",
    **pi_kwargs: Any,
) -> dict[str, float | int | bool | None]:
    """Calculate PUMS record-level P&I and its weighted share of SMOCP."""
    if selected_owner_cost_col not in frame:
        raise KeyError(f"missing PUMS column: {selected_owner_cost_col}")
    adjhsg_col = str(pi_kwargs.get("adjhsg_col", "ADJHSG"))
    if adjhsg_col not in frame:
        raise KeyError(f"missing PUMS column: {adjhsg_col}")

    components = calculate_pums_first_mortgage_pi(frame, **pi_kwargs)
    components["smoc_adj"] = adjust_pums_housing_dollars(
        components[selected_owner_cost_col], components[adjhsg_col]
    )
    return weighted_pi_share(
        components,
        selected_owner_cost_col="smoc_adj",
        weight_col=weight_col,
    )


def _reported_mask(series: pd.Series) -> pd.Series:
    blank = series.map(lambda value: isinstance(value, str) and not value.strip())
    return series.notna() & ~blank


def _threshold_label(threshold: float) -> str:
    percent = threshold * 100.0
    if float(percent).is_integer():
        return f"{int(percent)}pct"
    return f"{percent:g}pct".replace(".", "_")


def calculate_hmda_downpayment_metrics(
    frame: pd.DataFrame,
    *,
    property_value_col: str = "property_value",
    cltv_col: str = "combined_loan_to_value_ratio",
    minimum_cltv_exclusive: float = 0.0,
    maximum_cltv_inclusive: float = 150.0,
    minimum_sample: int = 50,
    thresholds: Sequence[float] = (0.03, 0.05, 0.10, 0.20),
    suppress_below_minimum: bool = True,
) -> dict[str, float | int | bool]:
    """Aggregate HMDA CLTV-based down-payment metrics for one county-period.

    The caller must first restrict the records to originated home-purchase
    loans on principal residences and the desired lien/loan-type universe.
    Diagnostics use the full supplied eligible universe before the CLTV
    validity filter.  CLTV is never replaced with loan amount divided by
    property value, and no winsorization is performed implicitly.
    """
    if property_value_col not in frame or cltv_col not in frame:
        missing_columns = sorted(
            {property_value_col, cltv_col}.difference(frame.columns)
        )
        raise KeyError(f"missing HMDA columns: {missing_columns}")
    if minimum_sample < 0:
        raise ValueError("minimum_sample cannot be negative")
    if maximum_cltv_inclusive <= minimum_cltv_exclusive:
        raise ValueError("maximum CLTV must exceed minimum CLTV")
    if any(threshold < 0 or threshold > 1 for threshold in thresholds):
        raise ValueError("down-payment thresholds must be between zero and one")

    property_raw = frame[property_value_col]
    cltv_raw = frame[cltv_col]
    property_reported = _reported_mask(property_raw)
    cltv_reported = _reported_mask(cltv_raw)
    property_value = pd.to_numeric(property_raw, errors="coerce").astype(float)
    cltv = pd.to_numeric(cltv_raw, errors="coerce").astype(float)
    property_numeric = property_value.notna() & np.isfinite(property_value)
    cltv_numeric = cltv.notna() & np.isfinite(cltv)
    property_positive = property_numeric & property_value.gt(0)
    cltv_valid = (
        cltv_numeric
        & cltv.gt(minimum_cltv_exclusive)
        & cltv.le(maximum_cltv_inclusive)
    )
    valid = property_positive & cltv_valid

    input_n = int(len(frame))
    sample_n = int(valid.sum())
    meets_minimum = sample_n >= minimum_sample
    coverage_rate = sample_n / input_n if input_n else math.nan
    metrics: dict[str, float | int | bool] = {
        "input_n": input_n,
        "property_value_reported_n": int(property_reported.sum()),
        "property_value_numeric_n": int(property_numeric.sum()),
        "property_value_positive_n": int(property_positive.sum()),
        "cltv_reported_n": int(cltv_reported.sum()),
        "cltv_numeric_n": int(cltv_numeric.sum()),
        "cltv_valid_n": int(cltv_valid.sum()),
        "cltv_invalid_bounds_n": int((cltv_numeric & ~cltv_valid).sum()),
        "sample_n": sample_n,
        "coverage_rate": coverage_rate,
        "property_value_coverage_rate": (
            float(property_positive.mean()) if input_n else math.nan
        ),
        "cltv_coverage_rate": (
            float(cltv_valid.mean()) if input_n else math.nan
        ),
        "meets_minimum_sample": meets_minimum,
    }

    if sample_n:
        valid_property = property_value.loc[valid]
        downpayment_rate = 1.0 - cltv.loc[valid] / 100.0
        downpayment_dollars = valid_property * downpayment_rate
        median_rate = float(downpayment_rate.median())
        aggregate_dollars = float(downpayment_dollars.sum())
        weighted_rate = float(aggregate_dollars / valid_property.sum())
        negative_n = int(downpayment_rate.lt(0).sum())
    else:
        downpayment_rate = pd.Series(dtype=float)
        median_rate = math.nan
        aggregate_dollars = math.nan
        weighted_rate = math.nan
        negative_n = 0

    metrics["negative_downpayment_n"] = negative_n
    publish = meets_minimum or not suppress_below_minimum
    metrics["hmda_median_downpayment_rate"] = (
        median_rate if publish else math.nan
    )
    metrics["hmda_property_value_weighted_downpayment_rate"] = (
        weighted_rate if publish else math.nan
    )
    metrics["hmda_aggregate_downpayment_flow"] = (
        aggregate_dollars if publish else math.nan
    )
    for threshold in thresholds:
        label = _threshold_label(float(threshold))
        share = (
            float(downpayment_rate.lt(threshold).mean())
            if sample_n
            else math.nan
        )
        metrics[f"share_downpayment_below_{label}"] = (
            share if publish else math.nan
        )
    return metrics


def implied_debt_scenarios(
    aggregate_monthly_pi: float | int | None,
    annual_rate: float | int | None,
    remaining_term_years: Sequence[float | int] = (20, 25, 30),
) -> dict[float, float]:
    """Return fixed-rate-equivalent debt for explicit remaining-term scenarios."""
    payment = _finite_float(aggregate_monthly_pi, "aggregate_monthly_pi")
    if not math.isnan(payment) and payment < 0:
        raise ValueError("aggregate_monthly_pi cannot be negative")
    if not remaining_term_years:
        raise ValueError("at least one remaining-term scenario is required")

    result: dict[float, float] = {}
    for term in remaining_term_years:
        years = _finite_float(term, "remaining_term_years")
        if years <= 0:
            raise ValueError("remaining-term scenarios must be positive")
        factor = annuity_factor(annual_rate, years * 12.0)
        result[years] = (
            payment * factor
            if not math.isnan(payment) and not math.isnan(factor)
            else math.nan
        )
    return result


def implied_debt_from_distribution(
    aggregate_monthly_pi: float | int | None,
    distribution: pd.DataFrame,
    *,
    annual_rate_col: str = "annual_rate",
    remaining_months_col: str = "remaining_months",
    weight_col: str = "weight",
    rates_are_percent: bool = False,
    normalize_weights: bool = False,
    weight_tolerance: float = 1e-8,
) -> float:
    """Capitalize aggregate monthly P&I with a joint rate-term distribution."""
    required = {annual_rate_col, remaining_months_col, weight_col}
    missing_columns = sorted(required.difference(distribution.columns))
    if missing_columns:
        raise KeyError(f"missing distribution columns: {missing_columns}")
    if distribution.empty:
        raise ValueError("rate-term distribution cannot be empty")

    weight = _numeric_series_strict(distribution[weight_col], weight_col)
    if weight.isna().any():
        raise ValueError("distribution weights cannot be missing")
    if (weight < 0).any():
        raise ValueError("distribution weights cannot be negative")
    positive = weight > 0
    if not positive.any():
        raise ValueError("distribution must contain positive probability mass")

    rate = _numeric_series_strict(
        distribution[annual_rate_col], annual_rate_col
    )
    months = _numeric_series_strict(
        distribution[remaining_months_col], remaining_months_col
    )
    if rate.loc[positive].isna().any() or months.loc[positive].isna().any():
        raise ValueError("positive-weight distribution rows require rate and term")
    if rates_are_percent:
        rate = rate / 100.0
    if (rate.loc[positive] < 0).any():
        raise ValueError("distribution rates cannot be negative")
    if (months.loc[positive] <= 0).any():
        raise ValueError("distribution remaining months must be positive")

    total_weight = float(weight.sum())
    if normalize_weights:
        weight = weight / total_weight
    elif not math.isclose(
        total_weight, 1.0, rel_tol=0.0, abs_tol=weight_tolerance
    ):
        raise ValueError(
            "distribution weights must sum to one; set normalize_weights=True "
            "to normalize explicitly"
        )

    factor = pd.Series(
        [
            annuity_factor(row_rate, row_months)
            if row_weight > 0
            else 0.0
            for row_rate, row_months, row_weight in zip(
                rate, months, weight, strict=True
            )
        ],
        index=distribution.index,
        dtype=float,
    )
    weighted_factor = float((weight * factor).sum())
    payment = _finite_float(aggregate_monthly_pi, "aggregate_monthly_pi")
    if math.isnan(payment):
        return math.nan
    if payment < 0:
        raise ValueError("aggregate_monthly_pi cannot be negative")
    return payment * weighted_factor


def calibrate_implied_debt_to_california(
    raw_county_debt: pd.Series,
    california_benchmark: float | int | None = None,
) -> dict[str, Any]:
    """Optionally scale observed county debts to a compatible CA benchmark.

    Missing county values remain missing and are not replaced.  With no
    benchmark, the raw series is returned unchanged and ``was_calibrated`` is
    false, so the absence of calibration cannot be mistaken for a factor of
    one.
    """
    raw = _numeric_series_strict(raw_county_debt, "raw_county_debt")
    if (raw.dropna() < 0).any():
        raise ValueError("raw county debt cannot be negative")
    raw_total = float(raw.sum(min_count=1)) if raw.notna().any() else math.nan

    if california_benchmark is None or _missing(california_benchmark):
        return {
            "calibrated_debt": raw.copy(),
            "calibration_factor": math.nan,
            "raw_california_total": raw_total,
            "benchmark_california_total": math.nan,
            "was_calibrated": False,
        }

    benchmark = _finite_float(
        california_benchmark, "california_benchmark"
    )
    if benchmark < 0:
        raise ValueError("California benchmark debt cannot be negative")
    if math.isnan(raw_total):
        raise ValueError("cannot calibrate without any reported county debt")
    if raw_total == 0:
        if benchmark != 0:
            raise ValueError("cannot calibrate a zero raw total to a nonzero benchmark")
        factor = 1.0
    else:
        factor = benchmark / raw_total
    calibrated = raw * factor
    return {
        "calibrated_debt": calibrated,
        "calibration_factor": factor,
        "raw_california_total": raw_total,
        "benchmark_california_total": benchmark,
        "was_calibrated": True,
    }


def calculate_aggregate_mortgage_payment(
    aggregate_selected_owner_costs: float | int | None,
    pi_share: float | int | None,
    *,
    mortgaged_units: float | int | None = None,
    aggregate_income_mortgaged_owners: float | int | None = None,
) -> dict[str, float]:
    """Calculate the ACS/PUMS benchmark payment level and burden measures."""
    selected_costs = _finite_float(
        aggregate_selected_owner_costs,
        "aggregate_selected_owner_costs",
    )
    share = _finite_float(pi_share, "pi_share")
    if not math.isnan(selected_costs) and selected_costs < 0:
        raise ValueError("aggregate selected owner costs cannot be negative")
    if not math.isnan(share) and not 0 <= share <= 1:
        raise ValueError("pi_share must be between zero and one")

    aggregate_pi = (
        selected_costs * share
        if not math.isnan(selected_costs) and not math.isnan(share)
        else math.nan
    )
    annual_debt_service = (
        12.0 * aggregate_pi if not math.isnan(aggregate_pi) else math.nan
    )

    units = _finite_float(mortgaged_units, "mortgaged_units")
    if not math.isnan(units) and units < 0:
        raise ValueError("mortgaged_units cannot be negative")
    pi_per_unit = (
        aggregate_pi / units
        if not math.isnan(aggregate_pi) and not math.isnan(units) and units > 0
        else math.nan
    )

    income = _finite_float(
        aggregate_income_mortgaged_owners,
        "aggregate_income_mortgaged_owners",
    )
    if not math.isnan(income) and income < 0:
        raise ValueError("aggregate income cannot be negative")
    mortgage_dsr = (
        annual_debt_service / income
        if not math.isnan(annual_debt_service)
        and not math.isnan(income)
        and income > 0
        else math.nan
    )
    return {
        "aggregate_monthly_pi": aggregate_pi,
        "pi_per_mortgaged_unit": pi_per_unit,
        "annual_debt_service": annual_debt_service,
        "mortgage_debt_service_to_income": mortgage_dsr,
    }


def calculate_matched_universe_equity(
    mortgaged_owner_value: float | int | None,
    mortgaged_owner_debt: float | int | None,
    *,
    mortgage_free_owner_value: float | int | None = None,
    owner_units: float | int | None = None,
) -> dict[str, float]:
    """Calculate equity and aggregate LTV on exactly matched owner universes."""
    value_mort = _finite_float(
        mortgaged_owner_value, "mortgaged_owner_value"
    )
    debt_mort = _finite_float(
        mortgaged_owner_debt, "mortgaged_owner_debt"
    )
    value_free = _finite_float(
        mortgage_free_owner_value, "mortgage_free_owner_value"
    )
    units = _finite_float(owner_units, "owner_units")
    for name, value in (
        ("mortgaged_owner_value", value_mort),
        ("mortgaged_owner_debt", debt_mort),
        ("mortgage_free_owner_value", value_free),
        ("owner_units", units),
    ):
        if not math.isnan(value) and value < 0:
            raise ValueError(f"{name} cannot be negative")

    equity_mort = (
        value_mort - debt_mort
        if not math.isnan(value_mort) and not math.isnan(debt_mort)
        else math.nan
    )
    aggregate_ltv = (
        debt_mort / value_mort
        if not math.isnan(debt_mort)
        and not math.isnan(value_mort)
        and value_mort > 0
        else math.nan
    )
    equity_rate = (
        1.0 - aggregate_ltv
        if not math.isnan(aggregate_ltv)
        else math.nan
    )
    total_owner_value = (
        value_mort + value_free
        if not math.isnan(value_mort) and not math.isnan(value_free)
        else math.nan
    )
    equity_all = (
        equity_mort + value_free
        if not math.isnan(equity_mort) and not math.isnan(value_free)
        else math.nan
    )
    equity_per_owner_unit = (
        equity_all / units
        if not math.isnan(equity_all) and not math.isnan(units) and units > 0
        else math.nan
    )
    mortgaged_identity_residual = (
        value_mort - (debt_mort + equity_mort)
        if not math.isnan(equity_mort)
        else math.nan
    )
    all_owner_identity_residual = (
        total_owner_value - (debt_mort + equity_all)
        if not math.isnan(total_owner_value)
        and not math.isnan(debt_mort)
        and not math.isnan(equity_all)
        else math.nan
    )
    return {
        "equity_mortgaged_owner": equity_mort,
        "equity_all_owner": equity_all,
        "total_owner_value": total_owner_value,
        "aggregate_ltv_mortgaged": aggregate_ltv,
        "equity_rate_mortgaged": equity_rate,
        "equity_per_owner_unit": equity_per_owner_unit,
        "mortgaged_identity_residual": mortgaged_identity_residual,
        "all_owner_identity_residual": all_owner_identity_residual,
    }


def _is_benchmark_version(value: Any) -> bool:
    if _missing(value):
        return False
    compact = re.sub(r"[^a-z0-9]+", "", str(value).casefold())
    return compact in {
        "a",
        "benchmark",
        "abenchmark",
        "versiona",
        "versionabenchmark",
    }


def _is_explicit_false(value: Any) -> bool:
    return isinstance(value, (bool, np.bool_)) and not bool(value)


def _blank(value: Any) -> bool:
    return _missing(value) or (isinstance(value, str) and not value.strip())


def _parse_date(value: Any) -> pd.Timestamp | None:
    if _missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed)


def validate_benchmark_metadata(
    records: Mapping[str, Any] | Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate required fields and Version A source-faithful invariants.

    Returns a list of human-readable errors.  Structural failures raise
    :class:`BenchmarkMetadataError` by default.
    """
    if isinstance(records, pd.DataFrame):
        frame = records.copy()
    elif isinstance(records, Mapping):
        frame = pd.DataFrame([dict(records)])
    else:
        frame = pd.DataFrame([dict(record) for record in records])

    errors: list[str] = []
    missing_columns = [
        field
        for field in REQUIRED_BENCHMARK_METADATA_FIELDS
        if field not in frame.columns
    ]
    if missing_columns:
        errors.append(f"missing required metadata fields: {missing_columns}")
    else:
        required_text = (
            "county_fips",
            "variable_name",
            "database_version",
            "source_frequency",
            "source_period_start",
            "source_period_end",
            "reference_date",
            "release_date",
            "source_geography",
            "parameter_geography",
            "monthly_method",
            "benchmark_vintage",
            "universe_definition",
        )
        disallowed_monthly_methods = (
            "interpol",
            "nowcast",
            "carryforward",
            "distributed",
            "modeled",
        )
        for row_index, row in frame.iterrows():
            prefix = f"row {row_index}"
            for field in required_text:
                if _blank(row[field]):
                    errors.append(f"{prefix}: {field} must be populated")

            fips = row["county_fips"]
            if not isinstance(fips, str) or not re.fullmatch(r"\d{5}", fips):
                errors.append(
                    f"{prefix}: county_fips must be a five-character string"
                )
            variable_name = row["variable_name"]
            if not _blank(variable_name) and not str(variable_name).endswith(
                "_benchmark"
            ):
                errors.append(
                    f"{prefix}: variable_name must end with _benchmark"
                )
            if not _is_benchmark_version(row["database_version"]):
                errors.append(
                    f"{prefix}: database_version must identify Version A benchmark"
                )
            if not _is_explicit_false(row["is_modeled"]):
                errors.append(f"{prefix}: Version A is_modeled must be boolean False")
            if not _is_explicit_false(row["is_nowcast"]):
                errors.append(f"{prefix}: Version A is_nowcast must be boolean False")

            method = re.sub(
                r"[^a-z0-9]+",
                "",
                str(row["monthly_method"]).casefold(),
            )
            if any(token in method for token in disallowed_monthly_methods):
                errors.append(
                    f"{prefix}: monthly_method is not source-faithful for Version A"
                )

            start = _parse_date(row["source_period_start"])
            end = _parse_date(row["source_period_end"])
            reference = _parse_date(row["reference_date"])
            release = _parse_date(row["release_date"])
            for field, parsed in (
                ("source_period_start", start),
                ("source_period_end", end),
                ("reference_date", reference),
                ("release_date", release),
            ):
                if parsed is None:
                    errors.append(f"{prefix}: {field} must be a valid date")
            if start is not None and end is not None and start > end:
                errors.append(f"{prefix}: source period start must not exceed end")
            if (
                start is not None
                and end is not None
                and reference is not None
                and not start <= reference <= end
            ):
                errors.append(
                    f"{prefix}: reference_date must fall inside the source period"
                )
            if release is not None and end is not None and release < end:
                errors.append(
                    f"{prefix}: release_date cannot precede source_period_end"
                )
            if not _blank(row["month"]) and _parse_date(row["month"]) is None:
                errors.append(f"{prefix}: month must be a valid date or missing")

            sample_n = row["sample_n"]
            if not _missing(sample_n):
                try:
                    sample = _finite_float(sample_n, "sample_n")
                except ValueError:
                    errors.append(
                        f"{prefix}: sample_n must be numeric or missing"
                    )
                else:
                    if sample < 0 or not sample.is_integer():
                        errors.append(
                            f"{prefix}: sample_n must be a nonnegative integer "
                            "or missing"
                        )
            coverage = row["coverage_rate"]
            if not _missing(coverage):
                try:
                    coverage_value = _finite_float(
                        coverage, "coverage_rate"
                    )
                except ValueError:
                    errors.append(
                        f"{prefix}: coverage_rate must be numeric or missing"
                    )
                else:
                    if not 0 <= coverage_value <= 1:
                        errors.append(
                            f"{prefix}: coverage_rate must be between zero and one"
                        )

            estimates: dict[str, float] = {}
            for field in ("low_estimate", "central_estimate", "high_estimate"):
                if not _missing(row[field]):
                    try:
                        estimates[field] = _finite_float(row[field], field)
                    except ValueError:
                        errors.append(
                            f"{prefix}: {field} must be numeric or missing"
                        )
            if len(estimates) == 3 and not (
                estimates["low_estimate"]
                <= estimates["central_estimate"]
                <= estimates["high_estimate"]
            ):
                errors.append(
                    f"{prefix}: estimates must satisfy low <= central <= high"
                )

    if errors and raise_on_error:
        raise BenchmarkMetadataError("; ".join(errors))
    return errors
