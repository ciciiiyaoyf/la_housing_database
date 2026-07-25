"""Assemble the native-frequency Version A housing-finance benchmark table."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.finance import (
    calculate_aggregate_mortgage_payment,
    calibrate_implied_debt_to_california,
    implied_debt_from_distribution,
    implied_debt_scenarios,
)
from src.benchmark.schema import (
    BENCHMARK_COLUMNS,
    benchmark_row,
    finalize_benchmark,
    normalize_benchmark,
)


ACS_SELECTED_COSTS = (
    "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark"
)
ACS_MORTGAGED_UNITS = "acs_mortgaged_owner_units_benchmark"
ACS_MORTGAGED_INCOME = (
    "acs_aggregate_household_income_mortgaged_owner_benchmark"
)
ACS_MORTGAGED_VALUE = "acs_aggregate_owner_value_mortgaged_benchmark"
ACS_NO_MORTGAGE_VALUE = (
    "acs_aggregate_owner_value_no_mortgage_benchmark"
)

PAYMENT_OUTPUTS: tuple[tuple[str, str, str], ...] = (
    (
        "aggregate_monthly_pi",
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
        "USD/month",
    ),
    (
        "pi_per_mortgaged_unit",
        "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
        "USD/unit/month",
    ),
    (
        "annual_debt_service",
        "acs_annual_first_mortgage_debt_service_benchmark",
        "USD/year",
    ),
    (
        "mortgage_debt_service_to_income",
        "acs_mortgage_debt_service_to_income_benchmark",
        "ratio",
    ),
)

EQUITY_OUTPUTS: tuple[tuple[str, str], ...] = (
    (
        "equity_mortgaged",
        "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
    ),
    (
        "equity_all",
        "acs_implied_owner_occupied_home_equity_all_benchmark",
    ),
    (
        "aggregate_ltv",
        "acs_aggregate_ltv_mortgaged_benchmark",
    ),
    (
        "equity_rate",
        "acs_equity_rate_mortgaged_benchmark",
    ),
)


def _as_timestamp(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    return pd.NaT if pd.isna(parsed) else pd.Timestamp(parsed)


def _number(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return math.nan if pd.isna(parsed) else float(parsed)


def _finite(value: Any) -> bool:
    number = _number(value)
    return not math.isnan(number) and math.isfinite(number)


def _lineage(*values: Any) -> str:
    payload = "|".join("" if pd.isna(value) else str(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pipe(values: list[Any]) -> str | None:
    tokens = [str(value) for value in values if not pd.isna(value) and str(value)]
    return "|".join(dict.fromkeys(tokens)) if tokens else None


def _source_contract(
    frame: pd.DataFrame | None,
    *,
    default_source_id: str,
    default_transformation_id: str,
) -> pd.DataFrame:
    """Map a source-specific benchmark cleaner into the final contract."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    result = frame.copy()
    defaults: dict[str, Any] = {
        "state_fips": "06",
        "geo_id": None,
        "variable_id": None,
        "estimate_variant": "published",
        "reported_value": None,
        "database_version": "A. Benchmark",
        "source_id": default_source_id,
        "monthly_method": "not_applicable_source_period",
        "construction_method": "direct_published_source",
        "payment_frequency": None,
        "is_modeled": False,
        "is_nowcast": False,
        "is_imputed": False,
        "is_interpolated": False,
        "is_suppressed": False,
        "transformation_id": default_transformation_id,
    }
    for column, value in defaults.items():
        if column not in result:
            result[column] = value
        else:
            result[column] = result[column].fillna(value)

    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result["geo_id"] = result["geo_id"].fillna(
        "COUNTY_" + result["county_fips"]
    )
    result["variable_id"] = result["variable_id"].fillna(
        result["variable_name"]
    )
    result["reported_value"] = result["reported_value"].fillna(result["value"])
    if "meets_minimum_sample" in result:
        suppressed = ~result["meets_minimum_sample"].fillna(True).astype(bool)
        result.loc[suppressed, "is_suppressed"] = True
        result.loc[
            suppressed & result.get(
                "quality_flag", pd.Series(index=result.index, dtype="object")
            ).isna(),
            "quality_flag",
        ] = "insufficient_sample"
    missing_without_flag = result["value"].isna() & result.get(
        "quality_flag", pd.Series(index=result.index, dtype="object")
    ).isna()
    result.loc[missing_without_flag, "quality_flag"] = "source_value_unavailable"
    result["lineage_id"] = [
        _lineage(
            row.get("source_id"),
            row.get("checksum_sha256"),
            row.get("county_fips"),
            row.get("reference_date"),
            row.get("variable_name"),
        )
        for _, row in result.iterrows()
    ]
    return normalize_benchmark(result)


def pmms_reference_rate_parameters(
    pmms_rows: pd.DataFrame,
    acs_rows: pd.DataFrame,
    *,
    variable_id: str = "mortgage_rate_30y_monthly_median",
) -> pd.DataFrame:
    """Select the exact PMMS calendar month containing each ACS midpoint.

    The returned rate is a decimal annual rate.  No nearest-neighbor, carry
    forward, or interpolation is allowed; a missing reference month remains
    absent so the debt benchmark can publish an explicit missing row.
    """
    required_pmms = {"observation_date", "variable_id", "value"}
    missing = required_pmms.difference(pmms_rows.columns)
    if missing:
        raise KeyError(f"missing PMMS columns: {sorted(missing)}")
    required_acs = {"benchmark_vintage", "reference_date"}
    missing = required_acs.difference(acs_rows.columns)
    if missing:
        raise KeyError(f"missing ACS columns: {sorted(missing)}")

    pmms = pmms_rows.loc[pmms_rows["variable_id"].eq(variable_id)].copy()
    pmms["_period"] = pd.to_datetime(
        pmms["observation_date"], errors="coerce"
    ).dt.to_period("M")
    pmms["_rate_percent"] = pd.to_numeric(pmms["value"], errors="coerce")
    duplicate = pmms.duplicated("_period", keep=False)
    if duplicate.any():
        raise ValueError(
            f"PMMS has duplicate {variable_id} observations by calendar month"
        )
    pmms = pmms.set_index("_period")

    vintages = acs_rows[
        ["benchmark_vintage", "reference_date"]
    ].drop_duplicates()
    rows: list[dict[str, Any]] = []
    for _, vintage in vintages.iterrows():
        reference = _as_timestamp(vintage["reference_date"])
        if pd.isna(reference):
            continue
        period = reference.to_period("M")
        if period not in pmms.index:
            continue
        source = pmms.loc[period]
        rate_percent = _number(source["_rate_percent"])
        if math.isnan(rate_percent):
            continue
        if rate_percent < 0:
            raise ValueError("PMMS rates cannot be negative")
        rows.append(
            {
                "benchmark_vintage": vintage["benchmark_vintage"],
                "reference_date": reference,
                "value": rate_percent / 100.0,
                "unit": "decimal_annual_rate",
                "variable_id": variable_id,
                "parameter_geography": "US",
                "source_id": "freddie_mac_pmms",
                "source_vintage": source.get("source_vintage"),
                "retrieved_at": source.get("retrieved_at"),
                "source_filename": source.get("source_filename"),
                "quality_flag": "pmms_origination_rate_sensitivity",
            }
        )
    return pd.DataFrame(rows)


def _indexed_acs(group: pd.DataFrame) -> dict[str, pd.Series]:
    duplicate = group.duplicated("variable_name", keep=False)
    if duplicate.any():
        names = sorted(group.loc[duplicate, "variable_name"].astype(str).unique())
        raise ValueError(f"duplicate ACS variables within county-vintage: {names}")
    return {
        str(row["variable_name"]): row
        for _, row in group.iterrows()
    }


def _estimate(
    values: Mapping[str, pd.Series],
    variable: str,
    field: str = "value",
) -> float:
    row = values.get(variable)
    return math.nan if row is None else _number(row.get(field))


def _select_pums_parameter(
    pums: pd.DataFrame,
    *,
    county_fips: str,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.Series | None:
    if pums.empty:
        return None
    candidates = pums.copy()
    by_vintage = candidates["benchmark_vintage"].astype(str).eq(
        str(benchmark_vintage)
    )
    if by_vintage.any():
        candidates = candidates.loc[by_vintage]
    else:
        dates = pd.to_datetime(candidates["reference_date"], errors="coerce")
        candidates = candidates.loc[dates.eq(reference_date)]
    if candidates.empty:
        return None

    county = candidates.loc[
        candidates["county_fips"].astype("string").eq(county_fips)
    ]
    if not county.empty:
        row = county.iloc[-1]
        mapping_complete = bool(
            row.get("county_all_pumas_wholly_contained", False)
        )
        if mapping_complete and _finite(row.get("value")):
            return row
    state = candidates.loc[
        candidates.get(
            "parameter_geography_level",
            pd.Series(index=candidates.index, dtype="object"),
        ).eq("state")
    ]
    state = state.loc[state["state_fips"].astype("string").eq("06")]
    return state.iloc[-1] if not state.empty and _finite(state.iloc[-1].get("value")) else None


def _lookup_rate(
    rates: pd.DataFrame,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.Series | None:
    if rates.empty:
        return None
    candidates = rates.loc[
        rates["benchmark_vintage"].astype(str).eq(str(benchmark_vintage))
    ]
    if candidates.empty and "reference_date" in rates:
        dates = pd.to_datetime(rates["reference_date"], errors="coerce")
        candidates = rates.loc[dates.eq(reference_date)]
    if len(candidates) > 1:
        raise ValueError(
            f"multiple rate parameters match benchmark vintage {benchmark_vintage}"
        )
    return candidates.iloc[0] if len(candidates) == 1 else None


def _lookup_distribution(
    distributions: pd.DataFrame | None,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.DataFrame:
    if distributions is None or distributions.empty:
        return pd.DataFrame()
    candidates = distributions.loc[
        distributions["benchmark_vintage"].astype(str).eq(str(benchmark_vintage))
    ]
    if candidates.empty and "reference_date" in distributions:
        dates = pd.to_datetime(distributions["reference_date"], errors="coerce")
        candidates = distributions.loc[dates.eq(reference_date)]
    return candidates.copy()


def _lookup_ca_upb(
    california_upb: pd.DataFrame | Mapping[str, float] | None,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> float:
    if california_upb is None:
        return math.nan
    if isinstance(california_upb, Mapping):
        return _number(california_upb.get(str(benchmark_vintage)))
    if california_upb.empty:
        return math.nan
    candidates = california_upb.loc[
        california_upb["benchmark_vintage"].astype(str).eq(
            str(benchmark_vintage)
        )
    ]
    if candidates.empty and "reference_date" in california_upb:
        dates = pd.to_datetime(
            california_upb["reference_date"], errors="coerce"
        )
        candidates = california_upb.loc[dates.eq(reference_date)]
    if len(candidates) > 1:
        raise ValueError(
            f"multiple California UPB controls match {benchmark_vintage}"
        )
    return _number(candidates.iloc[0]["value"]) if len(candidates) == 1 else math.nan


def _derived_base(
    *,
    acs_group: pd.DataFrame,
    variable_name: str,
    unit: str,
    value: float,
    low: float = math.nan,
    central: float | None = None,
    high: float = math.nan,
    construction_method: str,
    parameter_geography: str,
    sample_n: Any = None,
    coverage_rate: Any = None,
    quality_flag: str | None = None,
    notes: str,
    source_ids: list[Any],
    source_files: list[Any],
    source_vintages: list[Any],
    retrieved: list[Any],
    checksums: list[Any],
) -> dict[str, Any]:
    anchor = acs_group.iloc[0]
    central_value = value if central is None else central
    source_id = _pipe(source_ids)
    source_filename = _pipe(source_files)
    source_vintage = _pipe(source_vintages)
    retrieved_values = [
        pd.to_datetime(item, utc=True, errors="coerce")
        for item in retrieved
        if not pd.isna(item)
    ]
    retrieved_at = (
        max(item for item in retrieved_values if not pd.isna(item))
        if any(not pd.isna(item) for item in retrieved_values)
        else pd.NaT
    )
    checksum = _pipe(checksums)
    missing = not _finite(value)
    return benchmark_row(
        county_fips=str(anchor["county_fips"]),
        state_fips="06",
        geo_id=f"COUNTY_{anchor['county_fips']}",
        month=pd.NaT,
        value=value,
        variable_name=variable_name,
        variable_id=variable_name,
        unit=unit,
        estimate_variant="central",
        reported_value=None,
        database_version="A. Benchmark",
        source_id=source_id,
        source_frequency=str(anchor["source_frequency"]),
        source_period_start=anchor["source_period_start"],
        source_period_end=anchor["source_period_end"],
        reference_date=anchor["reference_date"],
        release_date=anchor.get("release_date"),
        source_geography="county",
        parameter_geography=parameter_geography,
        monthly_method="not_applicable_source_period",
        construction_method=construction_method,
        payment_frequency="monthly" if "monthly" in unit else None,
        is_modeled=False,
        is_nowcast=False,
        is_imputed=False,
        is_interpolated=False,
        is_suppressed=False,
        benchmark_vintage=str(anchor["benchmark_vintage"]),
        sample_n=sample_n,
        coverage_rate=coverage_rate,
        low_estimate=low,
        central_estimate=central_value,
        high_estimate=high,
        universe_definition=(
            "Owner-occupied housing units with a mortgage, contract to "
            "purchase, or similar debt"
        ),
        notes=notes,
        source_vintage=source_vintage,
        retrieved_at=retrieved_at,
        source_filename=source_filename,
        checksum_sha256=checksum,
        transformation_id="version_a_finance_benchmark_v1",
        lineage_id=_lineage(
            source_id,
            checksum,
            anchor["county_fips"],
            anchor["reference_date"],
            variable_name,
        ),
        quality_flag=(
            quality_flag
            if quality_flag is not None
            else ("parameter_unavailable" if missing else "derived_source_faithful")
        ),
    )


def _ratio_bounds(
    numerator_low: float,
    numerator_high: float,
    denominator_low: float,
    denominator_high: float,
) -> tuple[float, float]:
    low = (
        numerator_low / denominator_high
        if _finite(numerator_low)
        and _finite(denominator_high)
        and denominator_high > 0
        else math.nan
    )
    high = (
        numerator_high / denominator_low
        if _finite(numerator_high)
        and _finite(denominator_low)
        and denominator_low > 0
        else math.nan
    )
    return low, high


def build_version_a_benchmark(
    acs_rows: pd.DataFrame,
    pums_parameters: pd.DataFrame,
    *,
    hmda_rows: pd.DataFrame | None = None,
    pmms_rows: pd.DataFrame | None = None,
    rate_parameters: pd.DataFrame | None = None,
    nmdb_distributions: pd.DataFrame | None = None,
    california_upb: pd.DataFrame | Mapping[str, float] | None = None,
    output_path: Path | None = None,
    la_export_path: Path | None = None,
) -> pd.DataFrame:
    """Build all Version A finance observations at their published periods.

    ACS and HMDA source estimates are retained alongside the four requested
    finance constructs.  County PUMS P&I shares are used only where every PUMA
    is wholly contained in that county; otherwise the California parameter is
    applied and labeled.  NMDB joint rate/term input is preferred.  When it is
    absent, an exact-month PMMS rate may be used only as a documented
    20/25/30-year sensitivity.  Calibrated debt and equity remain null unless
    a compatible published California UPB control is supplied.
    """
    required_acs = {
        "county_fips",
        "variable_name",
        "value",
        "benchmark_vintage",
        "reference_date",
        "source_period_start",
        "source_period_end",
        "source_frequency",
    }
    missing = required_acs.difference(acs_rows.columns)
    if missing:
        raise KeyError(f"missing ACS benchmark columns: {sorted(missing)}")

    acs_contract = _source_contract(
        acs_rows,
        default_source_id="census_acs5_finance",
        default_transformation_id="acs5_finance_direct_v1",
    )
    hmda_contract = _source_contract(
        hmda_rows,
        default_source_id="cfpb_hmda_modified_lar",
        default_transformation_id="hmda_downpayment_annual_v1",
    )
    if rate_parameters is None:
        rates = (
            pmms_reference_rate_parameters(pmms_rows, acs_rows)
            if pmms_rows is not None and not pmms_rows.empty
            else pd.DataFrame()
        )
    else:
        rates = rate_parameters.copy()

    constructed: list[dict[str, Any]] = []
    debt_rows: list[dict[str, Any]] = []
    group_key = ["county_fips", "benchmark_vintage", "reference_date"]
    for (_, benchmark_vintage, reference_raw), group in acs_rows.groupby(
        group_key, sort=True, dropna=False
    ):
        county_fips = str(group.iloc[0]["county_fips"]).zfill(5)
        reference_date = _as_timestamp(reference_raw)
        values = _indexed_acs(group)
        pums = _select_pums_parameter(
            pums_parameters,
            county_fips=county_fips,
            benchmark_vintage=str(benchmark_vintage),
            reference_date=reference_date,
        )
        share = math.nan if pums is None else _number(pums.get("value"))
        pums_geography = (
            "unavailable"
            if pums is None
            else str(pums.get("parameter_geography"))
        )
        source_ids: list[Any] = ["census_acs5_finance"]
        source_files: list[Any] = [group.iloc[0].get("source_filename")]
        source_vintages: list[Any] = [group.iloc[0].get("source_vintage")]
        retrieved: list[Any] = [group.iloc[0].get("retrieved_at")]
        checksums: list[Any] = [group.iloc[0].get("checksum_sha256")]
        if pums is not None:
            source_ids.append("census_acs5_pums_housing_ca")
            source_files.extend(
                [
                    pums.get("source_filename"),
                    pums.get("relationship_source_filename"),
                ]
            )
            source_vintages.append(pums.get("benchmark_vintage"))
            retrieved.append(pums.get("retrieved_at"))
            checksums.append(pums.get("checksum_sha256"))

        selected = _estimate(values, ACS_SELECTED_COSTS)
        units = _estimate(values, ACS_MORTGAGED_UNITS)
        income = _estimate(values, ACS_MORTGAGED_INCOME)
        payment = calculate_aggregate_mortgage_payment(
            selected,
            share,
            mortgaged_units=units,
            aggregate_income_mortgaged_owners=income,
        )
        selected_low = max(0.0, _estimate(values, ACS_SELECTED_COSTS, "low_estimate"))
        selected_high = _estimate(values, ACS_SELECTED_COSTS, "high_estimate")
        payment_low = selected_low * share if _finite(share) else math.nan
        payment_high = selected_high * share if _finite(share) else math.nan
        units_low = _estimate(values, ACS_MORTGAGED_UNITS, "low_estimate")
        units_high = _estimate(values, ACS_MORTGAGED_UNITS, "high_estimate")
        income_low = _estimate(values, ACS_MORTGAGED_INCOME, "low_estimate")
        income_high = _estimate(values, ACS_MORTGAGED_INCOME, "high_estimate")
        per_unit_low, per_unit_high = _ratio_bounds(
            payment_low, payment_high, units_low, units_high
        )
        dsr_low, dsr_high = _ratio_bounds(
            12.0 * payment_low if _finite(payment_low) else math.nan,
            12.0 * payment_high if _finite(payment_high) else math.nan,
            income_low,
            income_high,
        )
        output_bounds = {
            "aggregate_monthly_pi": (payment_low, payment_high),
            "pi_per_mortgaged_unit": (per_unit_low, per_unit_high),
            "annual_debt_service": (
                12.0 * payment_low if _finite(payment_low) else math.nan,
                12.0 * payment_high if _finite(payment_high) else math.nan,
            ),
            "mortgage_debt_service_to_income": (dsr_low, dsr_high),
        }
        parameter_quality = (
            "pums_parameter_unavailable"
            if pums is None
            else (
                "california_pums_fallback"
                if pums.get("parameter_geography_level") == "state"
                else "county_pums_parameter"
            )
        )
        constructed.append(
            _derived_base(
                acs_group=group,
                variable_name="pums_first_mortgage_pi_share_applied_benchmark",
                unit="ratio",
                value=share,
                low=math.nan,
                high=math.nan,
                construction_method="PUMS weighted first-mortgage P&I share",
                parameter_geography=pums_geography,
                sample_n=None if pums is None else pums.get("sample_n"),
                coverage_rate=None if pums is None else pums.get("coverage_rate"),
                quality_flag=parameter_quality,
                notes=(
                    "County PUMS shares are used only for counties whose PUMAs "
                    "are wholly contained; otherwise the published California "
                    "PUMS parameter is applied without temporal interpolation."
                ),
                source_ids=source_ids,
                source_files=source_files,
                source_vintages=source_vintages,
                retrieved=retrieved,
                checksums=checksums,
            )
        )
        for metric_key, variable_name, unit in PAYMENT_OUTPUTS:
            low, high = output_bounds[metric_key]
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=unit,
                    value=payment[metric_key],
                    low=low,
                    high=high,
                    construction_method=(
                        "ACS published aggregate level multiplied by the "
                        "source-period PUMS weighted P&I share"
                    ),
                    parameter_geography=pums_geography,
                    sample_n=None if pums is None else pums.get("sample_n"),
                    coverage_rate=(
                        None if pums is None else pums.get("coverage_rate")
                    ),
                    quality_flag=(
                        "pums_parameter_unavailable"
                        if pums is None
                        else parameter_quality
                    ),
                    notes=(
                        "Retained at the ACS 5-year source interval. Published "
                        "ACS 90% MOEs supply bounds where ratio arithmetic "
                        "permits; no monthly allocation is performed."
                    ),
                    source_ids=source_ids,
                    source_files=source_files,
                    source_vintages=source_vintages,
                    retrieved=retrieved,
                    checksums=checksums,
                )
            )

        distribution = _lookup_distribution(
            nmdb_distributions,
            str(benchmark_vintage),
            reference_date,
        )
        rate = _lookup_rate(rates, str(benchmark_vintage), reference_date)
        aggregate_pi = payment["aggregate_monthly_pi"]
        if not distribution.empty:
            raw_debt = implied_debt_from_distribution(
                aggregate_pi,
                distribution,
                rates_are_percent=bool(
                    distribution.get(
                        "rates_are_percent",
                        pd.Series([False]),
                    ).iloc[0]
                ),
            )
            debt_low = debt_high = raw_debt
            debt_method = "NMDB published joint outstanding rate-term distribution"
            debt_geography = str(
                distribution.get(
                    "parameter_geography",
                    pd.Series(["US"]),
                ).iloc[0]
            )
            debt_quality = "nmdb_joint_distribution"
            source_ids.append("fhfa_nmdb_outstanding")
            source_files.append(
                distribution.get(
                    "source_filename", pd.Series([None])
                ).iloc[0]
            )
            source_vintages.append(
                distribution.get(
                    "source_vintage", pd.Series([None])
                ).iloc[0]
            )
            retrieved.append(
                distribution.get("retrieved_at", pd.Series([None])).iloc[0]
            )
            checksums.append(
                distribution.get(
                    "checksum_sha256", pd.Series([None])
                ).iloc[0]
            )
        elif rate is not None and _finite(rate.get("value")):
            scenarios = implied_debt_scenarios(
                aggregate_pi,
                _number(rate.get("value")),
                (20, 25, 30),
            )
            debt_low, raw_debt, debt_high = (
                scenarios[20.0],
                scenarios[25.0],
                scenarios[30.0],
            )
            debt_method = "PMMS exact-reference-month 20/25/30-year sensitivity"
            debt_geography = str(rate.get("parameter_geography", "US"))
            debt_quality = "pmms_origination_rate_sensitivity"
            source_ids.append(rate.get("source_id", "freddie_mac_pmms"))
            source_files.append(rate.get("source_filename"))
            source_vintages.append(rate.get("source_vintage"))
            retrieved.append(rate.get("retrieved_at"))
            checksums.append(rate.get("checksum_sha256"))
        else:
            raw_debt = debt_low = debt_high = math.nan
            debt_method = "rate-term parameter unavailable"
            debt_geography = "unavailable"
            debt_quality = "rate_term_parameter_unavailable"

        raw_row = _derived_base(
            acs_group=group,
            variable_name="acs_implied_first_mortgage_balance_raw_benchmark",
            unit="USD",
            value=raw_debt,
            low=debt_low,
            high=debt_high,
            construction_method=debt_method,
            parameter_geography=f"{pums_geography}|{debt_geography}",
            sample_n=None if pums is None else pums.get("sample_n"),
            coverage_rate=None if pums is None else pums.get("coverage_rate"),
            quality_flag=debt_quality,
            notes=(
                "Fixed-rate-equivalent first-lien debt. NMDB joint outstanding "
                "rate/remaining-term input is preferred; PMMS is explicitly a "
                "fallback sensitivity and not an outstanding-stock coupon."
            ),
            source_ids=source_ids,
            source_files=source_files,
            source_vintages=source_vintages,
            retrieved=retrieved,
            checksums=checksums,
        )
        constructed.append(raw_row)
        debt_rows.append(raw_row)

    debt_frame = pd.DataFrame(debt_rows)
    calibrated_by_key: dict[tuple[str, str], dict[str, float]] = {}
    calibration_status_by_vintage: dict[str, str] = {}
    if not debt_frame.empty:
        for vintage, group in debt_frame.groupby("benchmark_vintage", sort=True):
            reference = _as_timestamp(group.iloc[0]["reference_date"])
            upb = _lookup_ca_upb(california_upb, str(vintage), reference)
            raw_series = group.set_index("county_fips")["value"]
            if not _finite(upb):
                calibration_status_by_vintage[str(vintage)] = (
                    "california_upb_calibration_unavailable"
                )
            elif raw_series.isna().any():
                calibration_status_by_vintage[str(vintage)] = (
                    "county_raw_debt_incomplete"
                )
            else:
                calibration = calibrate_implied_debt_to_california(
                    raw_series, upb
                )
                factor = float(calibration["calibration_factor"])
                calibration_status_by_vintage[str(vintage)] = (
                    "calibrated_to_published_california_upb"
                )
                for _, row in group.iterrows():
                    key = (str(row["county_fips"]), str(vintage))
                    calibrated_by_key[key] = {
                        "value": _number(row["value"]) * factor,
                        "low": _number(row["low_estimate"]) * factor,
                        "high": _number(row["high_estimate"]) * factor,
                        "factor": factor,
                    }

    for (_, benchmark_vintage, _), group in acs_rows.groupby(
        group_key, sort=True, dropna=False
    ):
        county_fips = str(group.iloc[0]["county_fips"]).zfill(5)
        key = (county_fips, str(benchmark_vintage))
        calibrated = calibrated_by_key.get(key)
        if calibrated is None:
            calibrated = {
                "value": math.nan,
                "low": math.nan,
                "high": math.nan,
                "factor": math.nan,
            }
            calibration_quality = calibration_status_by_vintage.get(
                str(benchmark_vintage),
                "california_upb_calibration_unavailable",
            )
        else:
            calibration_quality = "calibrated_to_published_california_upb"
        cal_row = _derived_base(
            acs_group=group,
            variable_name="acs_implied_first_mortgage_balance_cal_benchmark",
            unit="USD",
            value=calibrated["value"],
            low=calibrated["low"],
            high=calibrated["high"],
            construction_method="California ratio calibration of raw county debt",
            parameter_geography="California",
            quality_flag=calibration_quality,
            notes=(
                "Calibration factor equals compatible published California UPB "
                "divided by the sum of nonmissing county raw debt. The value "
                "remains null when that control is unavailable or any county "
                "raw debt is missing."
            ),
            source_ids=[
                "census_acs5_finance",
                "census_acs5_pums_housing_ca",
                "fhfa_nmdb_outstanding",
            ],
            source_files=[group.iloc[0].get("source_filename")],
            source_vintages=[group.iloc[0].get("source_vintage")],
            retrieved=[group.iloc[0].get("retrieved_at")],
            checksums=[group.iloc[0].get("checksum_sha256")],
        )
        constructed.append(cal_row)

        values = _indexed_acs(group)
        value_mort = _estimate(values, ACS_MORTGAGED_VALUE)
        value_mort_low = _estimate(values, ACS_MORTGAGED_VALUE, "low_estimate")
        value_mort_high = _estimate(values, ACS_MORTGAGED_VALUE, "high_estimate")
        value_free = _estimate(values, ACS_NO_MORTGAGE_VALUE)
        value_free_low = _estimate(
            values, ACS_NO_MORTGAGE_VALUE, "low_estimate"
        )
        value_free_high = _estimate(
            values, ACS_NO_MORTGAGE_VALUE, "high_estimate"
        )
        debt = calibrated["value"]
        debt_low = calibrated["low"]
        debt_high = calibrated["high"]
        if _finite(value_mort) and _finite(debt):
            equity_mort = value_mort - debt
            equity_mort_low = (
                value_mort_low - debt_high
                if _finite(value_mort_low) and _finite(debt_high)
                else math.nan
            )
            equity_mort_high = (
                value_mort_high - debt_low
                if _finite(value_mort_high) and _finite(debt_low)
                else math.nan
            )
            aggregate_ltv = debt / value_mort if value_mort > 0 else math.nan
            ltv_low, ltv_high = _ratio_bounds(
                debt_low, debt_high, value_mort_low, value_mort_high
            )
            equity_rate = (
                1.0 - aggregate_ltv if _finite(aggregate_ltv) else math.nan
            )
            equity_rate_low = (
                1.0 - ltv_high if _finite(ltv_high) else math.nan
            )
            equity_rate_high = (
                1.0 - ltv_low if _finite(ltv_low) else math.nan
            )
        else:
            equity_mort = equity_mort_low = equity_mort_high = math.nan
            aggregate_ltv = ltv_low = ltv_high = math.nan
            equity_rate = equity_rate_low = equity_rate_high = math.nan
        if _finite(equity_mort) and _finite(value_free):
            equity_all = equity_mort + value_free
            equity_all_low = (
                equity_mort_low + value_free_low
                if _finite(equity_mort_low) and _finite(value_free_low)
                else math.nan
            )
            equity_all_high = (
                equity_mort_high + value_free_high
                if _finite(equity_mort_high) and _finite(value_free_high)
                else math.nan
            )
        else:
            equity_all = equity_all_low = equity_all_high = math.nan
        equity_metrics = {
            "equity_mortgaged": (
                equity_mort,
                equity_mort_low,
                equity_mort_high,
                "USD",
            ),
            "equity_all": (
                equity_all,
                equity_all_low,
                equity_all_high,
                "USD",
            ),
            "aggregate_ltv": (
                aggregate_ltv,
                ltv_low,
                ltv_high,
                "ratio",
            ),
            "equity_rate": (
                equity_rate,
                equity_rate_low,
                equity_rate_high,
                "ratio",
            ),
        }
        for metric, variable_name in EQUITY_OUTPUTS:
            value, low, high, unit = equity_metrics[metric]
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=unit,
                    value=value,
                    low=low,
                    high=high,
                    construction_method=(
                        "Matched-universe ACS aggregate owner value less "
                        "California-calibrated first-mortgage debt"
                    ),
                    parameter_geography="county|California",
                    quality_flag=calibration_quality,
                    notes=(
                        "Mortgaged-owner value and debt use the same universe. "
                        "Mortgage-free owner value is added only for the "
                        "all-owner equity measure."
                    ),
                    source_ids=[
                        "census_acs5_finance",
                        "census_acs5_pums_housing_ca",
                        "fhfa_nmdb_outstanding",
                    ],
                    source_files=[group.iloc[0].get("source_filename")],
                    source_vintages=[group.iloc[0].get("source_vintage")],
                    retrieved=[group.iloc[0].get("retrieved_at")],
                    checksums=[group.iloc[0].get("checksum_sha256")],
                )
            )

    constructed_frame = pd.DataFrame(constructed)
    combined = pd.concat(
        [acs_contract, hmda_contract, normalize_benchmark(constructed_frame)],
        ignore_index=True,
        sort=False,
    )
    result = finalize_benchmark(combined, output_path)
    if la_export_path is not None:
        la_export_path.parent.mkdir(parents=True, exist_ok=True)
        result.loc[result["county_fips"].eq("06037")].to_csv(
            la_export_path, index=False
        )
    return result


__all__ = [
    "build_version_a_benchmark",
    "pmms_reference_rate_parameters",
]
