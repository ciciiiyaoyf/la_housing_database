"""Structural and accounting-identity QA for Version A benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.schema import benchmark_contract_errors, normalize_benchmark


@dataclass(frozen=True)
class BenchmarkCheck:
    check_id: str
    severity: str
    status: str
    failure_count: int
    detail: str


def _result(
    check_id: str,
    severity: str,
    failures: pd.DataFrame,
    detail: str,
) -> BenchmarkCheck:
    count = len(failures)
    status = "fail" if count and severity == "error" else "warn" if count else "pass"
    return BenchmarkCheck(check_id, severity, status, count, detail)


def _close(left: pd.Series, right: pd.Series) -> pd.Series:
    left_numeric = pd.to_numeric(left, errors="coerce")
    right_numeric = pd.to_numeric(right, errors="coerce")
    scale = np.maximum(
        np.maximum(left_numeric.abs(), right_numeric.abs()),
        1.0,
    )
    return (left_numeric - right_numeric).abs().le(1e-8 * scale)


def _identity_failures(
    frame: pd.DataFrame,
    *,
    check_id: str,
    result_variable: str,
    input_variables: tuple[str, ...],
    expected: Any,
) -> pd.DataFrame:
    keys = ["county_fips", "benchmark_vintage", "reference_date"]
    selected = frame.loc[
        frame["variable_name"].isin((result_variable, *input_variables)),
        [*keys, "variable_name", "value"],
    ]
    if selected.empty:
        return pd.DataFrame(columns=[*keys, "actual", "expected"])
    duplicate = selected.duplicated([*keys, "variable_name"], keep=False)
    if duplicate.any():
        duplicate_rows = selected.loc[duplicate].copy()
        duplicate_rows["actual"] = np.nan
        duplicate_rows["expected"] = np.nan
        return duplicate_rows
    wide = selected.pivot(index=keys, columns="variable_name", values="value")
    required = {result_variable, *input_variables}
    if not required.issubset(wide.columns):
        return pd.DataFrame(columns=[*keys, "actual", "expected"])
    complete = wide[list(required)].notna().all(axis=1)
    if not complete.any():
        return pd.DataFrame(columns=[*keys, "actual", "expected"])
    work = wide.loc[complete].copy()
    calculated = expected(work)
    failed = ~_close(work[result_variable], calculated)
    if not failed.any():
        return pd.DataFrame(columns=[*keys, "actual", "expected"])
    output = work.loc[failed].reset_index()[keys]
    output["actual"] = work.loc[failed, result_variable].to_numpy()
    output["expected"] = calculated.loc[failed].to_numpy()
    output["identity"] = check_id
    return output


def benchmark_qa_results(
    frame: pd.DataFrame,
    *,
    require_complete_acs_counties: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return Version A QA summary and bounded failure rows."""
    work = normalize_benchmark(frame)
    results: list[BenchmarkCheck] = []
    failures: list[pd.DataFrame] = []

    def check(
        check_id: str,
        severity: str,
        failed: pd.DataFrame,
        detail: str,
    ) -> None:
        results.append(_result(check_id, severity, failed, detail))
        if not failed.empty:
            bounded = failed.head(1_000).copy()
            bounded.insert(0, "severity", severity)
            bounded.insert(0, "check_id", check_id)
            failures.append(bounded)

    contract = benchmark_contract_errors(work)
    contract_failures = pd.DataFrame({"error": contract})
    check(
        "benchmark_contract",
        "error",
        contract_failures,
        "All rows must satisfy the native-frequency Version A contract.",
    )

    non_california = work.loc[
        ~work["state_fips"].astype("string").eq("06")
        | ~work["county_fips"].astype("string").str.startswith("06", na=False),
        ["state_fips", "county_fips", "variable_name"],
    ]
    check(
        "california_county_scope",
        "error",
        non_california,
        "The Version A finance table is restricted to California counties.",
    )

    dates = work[
        [
            "source_period_start",
            "source_period_end",
            "reference_date",
            "release_date",
            "source_frequency",
            "county_fips",
            "variable_name",
        ]
    ].copy()
    invalid_dates = dates.loc[
        dates["source_period_start"].isna()
        | dates["source_period_end"].isna()
        | dates["reference_date"].isna()
        | dates["source_period_start"].gt(dates["source_period_end"])
        | dates["reference_date"].lt(dates["source_period_start"])
        | dates["reference_date"].gt(dates["source_period_end"])
        | (
            dates["release_date"].notna()
            & dates["release_date"].lt(dates["source_period_end"])
        )
    ]
    check(
        "source_period_dates",
        "error",
        invalid_dates,
        "Reference dates must lie inside valid source periods; releases cannot precede period end.",
    )

    frequency = dates["source_frequency"].astype("string").str.lower()
    acs_frequency = frequency.str.replace("_", "-", regex=False).isin(
        {"5-year", "acs-5-year"}
    )
    starts = dates["source_period_start"]
    ends = dates["source_period_end"]
    invalid_acs_period = dates.loc[
        acs_frequency
        & (
            starts.dt.month.ne(1)
            | starts.dt.day.ne(1)
            | ends.dt.month.ne(12)
            | ends.dt.day.ne(31)
            | (ends.dt.year - starts.dt.year).ne(4)
        )
    ]
    check(
        "acs_five_year_interval",
        "error",
        invalid_acs_period,
        "ACS 5-year observations must retain their full five-calendar-year interval.",
    )

    annual_frequency = frequency.eq("annual")
    invalid_annual_period = dates.loc[
        annual_frequency
        & (
            starts.dt.year.ne(ends.dt.year)
            | starts.dt.month.ne(1)
            | starts.dt.day.ne(1)
            | ends.dt.month.ne(12)
            | ends.dt.day.ne(31)
        )
    ]
    check(
        "annual_source_interval",
        "error",
        invalid_annual_period,
        "Annual observations must retain January-through-December source periods.",
    )

    direct_acs = work.loc[
        work["source_id"].astype("string").eq("census_acs5_finance")
        & work["variable_name"].astype("string").str.startswith("acs_")
    ]
    if require_complete_acs_counties and not direct_acs.empty:
        county_counts = (
            direct_acs.groupby(["benchmark_vintage", "variable_name"])
            ["county_fips"]
            .nunique()
            .reset_index(name="county_count")
        )
        incomplete_acs = county_counts.loc[county_counts["county_count"].ne(58)]
    else:
        incomplete_acs = pd.DataFrame(
            columns=["benchmark_vintage", "variable_name", "county_count"]
        )
    check(
        "acs_all_california_counties",
        "error",
        incomplete_acs,
        "Each direct ACS variable-vintage must contain all 58 California counties, including published missing rows.",
    )

    la_missing_rows: list[dict[str, Any]] = []
    for (vintage, variable), group in work.groupby(
        ["benchmark_vintage", "variable_name"], dropna=False
    ):
        if not group["county_fips"].eq("06037").any():
            la_missing_rows.append(
                {
                    "benchmark_vintage": vintage,
                    "variable_name": variable,
                }
            )
    check(
        "los_angeles_primary_output_present",
        "warning",
        pd.DataFrame(la_missing_rows),
        "Every available variable-vintage should include the LA County primary output.",
    )

    identities = (
        (
            "annual_payment_identity",
            "acs_annual_first_mortgage_debt_service_benchmark",
            ("acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",),
            lambda wide: 12.0
            * wide[
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
            ],
            "Annual debt service must equal twelve times aggregate monthly P&I.",
        ),
        (
            "payment_per_unit_identity",
            "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
            (
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
                "acs_mortgaged_owner_units_benchmark",
            ),
            lambda wide: wide[
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark"
            ]
            / wide["acs_mortgaged_owner_units_benchmark"],
            "Per-unit P&I must equal aggregate P&I divided by mortgaged units.",
        ),
        (
            "mortgage_dsr_identity",
            "acs_mortgage_debt_service_to_income_benchmark",
            (
                "acs_annual_first_mortgage_debt_service_benchmark",
                "acs_aggregate_household_income_mortgaged_owner_benchmark",
            ),
            lambda wide: wide[
                "acs_annual_first_mortgage_debt_service_benchmark"
            ]
            / wide["acs_aggregate_household_income_mortgaged_owner_benchmark"],
            "Mortgage DSR must equal annual first-mortgage P&I divided by matched income.",
        ),
        (
            "mortgaged_equity_identity",
            "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
            (
                "acs_aggregate_owner_value_mortgaged_benchmark",
                "acs_implied_first_mortgage_balance_cal_benchmark",
            ),
            lambda wide: wide[
                "acs_aggregate_owner_value_mortgaged_benchmark"
            ]
            - wide["acs_implied_first_mortgage_balance_cal_benchmark"],
            "Mortgaged-owner equity must equal matched owner value less calibrated debt.",
        ),
        (
            "all_owner_equity_identity",
            "acs_implied_owner_occupied_home_equity_all_benchmark",
            (
                "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
                "acs_aggregate_owner_value_no_mortgage_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_owner_occupied_home_equity_mortgaged_benchmark"
            ]
            + wide["acs_aggregate_owner_value_no_mortgage_benchmark"],
            "All-owner equity must add mortgage-free owner value to mortgaged-owner equity.",
        ),
        (
            "aggregate_ltv_identity",
            "acs_aggregate_ltv_mortgaged_benchmark",
            (
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_aggregate_owner_value_mortgaged_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ]
            / wide["acs_aggregate_owner_value_mortgaged_benchmark"],
            "Aggregate LTV must divide calibrated debt by matched mortgaged-owner value.",
        ),
        (
            "equity_rate_identity",
            "acs_equity_rate_mortgaged_benchmark",
            ("acs_aggregate_ltv_mortgaged_benchmark",),
            lambda wide: 1.0
            - wide["acs_aggregate_ltv_mortgaged_benchmark"],
            "The matched-universe equity rate must equal one minus aggregate LTV.",
        ),
    )
    for check_id, result_variable, inputs, calculation, detail in identities:
        check(
            check_id,
            "error",
            _identity_failures(
                work,
                check_id=check_id,
                result_variable=result_variable,
                input_variables=inputs,
                expected=calculation,
            ),
            detail,
        )

    summary = pd.DataFrame(asdict(result) for result in results)
    failure_frame = (
        pd.concat(failures, ignore_index=True, sort=False)
        if failures
        else pd.DataFrame(columns=["check_id", "severity"])
    )
    return summary, failure_frame


def run_benchmark_qa(
    source: pd.DataFrame | str | Path,
    *,
    root: Path | None = None,
    raise_on_error: bool = True,
    require_complete_acs_counties: bool = True,
) -> pd.DataFrame:
    """Run QA, optionally persist reports, and stop on structural failures."""
    if isinstance(source, pd.DataFrame):
        frame = source
    else:
        path = Path(source)
        frame = (
            pd.read_parquet(path)
            if path.suffix.casefold() in {".parquet", ".pq"}
            else pd.read_csv(path, dtype={"county_fips": "string", "state_fips": "string"})
        )
    summary, failures = benchmark_qa_results(
        frame,
        require_complete_acs_counties=require_complete_acs_counties,
    )
    if root is not None:
        qa_dir = Path(root) / "output" / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        summary.to_csv(qa_dir / "version_a_qa_summary.csv", index=False)
        failures.to_csv(qa_dir / "version_a_qa_failures.csv", index=False)
    errors = summary.loc[
        summary["severity"].eq("error") & summary["status"].eq("fail")
    ]
    if raise_on_error and not errors.empty:
        detail = ", ".join(
            f"{row.check_id}={row.failure_count}"
            for row in errors.itertuples(index=False)
        )
        raise RuntimeError(f"Version A structural QA failed: {detail}")
    return summary


__all__ = ["benchmark_qa_results", "run_benchmark_qa"]
