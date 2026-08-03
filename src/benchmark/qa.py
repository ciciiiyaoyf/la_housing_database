"""Structural and accounting-identity QA for Version A benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.benchmark.schema import benchmark_contract_errors, normalize_benchmark

CALIFORNIA_COUNTY_COUNT = 58
ACS_AGGREGATE_PARTITION_ROUNDING_TOLERANCE = 100.0

REQUIRED_DIRECT_ACS_VARIABLES = (
    "acs_total_population_benchmark",
    "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark",
    "acs_owner_occupied_units_benchmark",
    "acs_mortgaged_owner_units_benchmark",
    "acs_aggregate_owner_value_all_benchmark",
    "acs_aggregate_owner_value_mortgaged_benchmark",
    "acs_aggregate_owner_value_no_mortgage_benchmark",
    "acs_aggregate_household_income_owner_benchmark",
    "acs_aggregate_household_income_mortgaged_owner_benchmark",
    "acs_aggregate_household_income_no_mortgage_owner_benchmark",
)

REQUIRED_DERIVED_VARIABLES = (
    "pums_first_mortgage_pi_share_applied_benchmark",
    "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
    "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
    "acs_annual_first_mortgage_debt_service_benchmark",
    "acs_mortgage_debt_service_to_income_benchmark",
    "acs_implied_first_mortgage_balance_raw_benchmark",
    "acs_implied_first_mortgage_balance_cal_benchmark",
    "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_owner_unit_benchmark",
    "acs_first_mortgage_debt_per_capita_benchmark",
    "acs_first_mortgage_debt_to_owner_income_benchmark",
    "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
    "acs_implied_owner_occupied_home_equity_all_benchmark",
    "acs_aggregate_ltv_mortgaged_benchmark",
    "acs_equity_rate_mortgaged_benchmark",
    "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
    "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark",
    "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark",
)


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


def _close(
    left: pd.Series,
    right: pd.Series,
    *,
    absolute_tolerance: float = 0.0,
) -> pd.Series:
    left_numeric = pd.to_numeric(left, errors="coerce")
    right_numeric = pd.to_numeric(right, errors="coerce")
    scale = np.maximum(
        np.maximum(left_numeric.abs(), right_numeric.abs()),
        1.0,
    )
    tolerance = np.maximum(
        1e-8 * scale,
        float(absolute_tolerance),
    )
    return (left_numeric - right_numeric).abs().le(tolerance)


def _identity_failures(
    frame: pd.DataFrame,
    *,
    check_id: str,
    result_variable: str,
    input_variables: tuple[str, ...],
    expected: Any,
    absolute_tolerance: float = 0.0,
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
    failed = ~_close(
        work[result_variable],
        calculated,
        absolute_tolerance=absolute_tolerance,
    )
    if not failed.any():
        return pd.DataFrame(columns=[*keys, "actual", "expected"])
    output = work.loc[failed].reset_index()[keys]
    output["actual"] = work.loc[failed, result_variable].to_numpy()
    output["expected"] = calculated.loc[failed].to_numpy()
    output["identity"] = check_id
    return output


def _required_output_failures(
    frame: pd.DataFrame,
    direct_acs: pd.DataFrame,
    *,
    required_variables: tuple[str, ...],
) -> pd.DataFrame:
    """Return missing required rows for every county/ACS-vintage anchor.

    Published or input-dependent missing values are valid, but the row itself
    must remain present with an explicit quality flag where applicable.
    """
    keys = ["county_fips", "benchmark_vintage", "reference_date"]
    anchors = direct_acs.loc[:, keys].drop_duplicates()
    if anchors.empty:
        return pd.DataFrame(columns=[*keys, "required_variable"])
    required = pd.DataFrame({"required_variable": required_variables})
    expected = anchors.assign(_join_key=1).merge(
        required.assign(_join_key=1),
        on="_join_key",
        how="inner",
    ).drop(columns="_join_key")
    actual = (
        frame.loc[:, [*keys, "variable_name"]]
        .rename(columns={"variable_name": "required_variable"})
        .drop_duplicates()
    )
    merged = expected.merge(
        actual,
        on=[*keys, "required_variable"],
        how="left",
        indicator=True,
    )
    return merged.loc[
        merged["_merge"].eq("left_only"),
        [*keys, "required_variable"],
    ]


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
        expected_counties = set(CALIFORNIA_COUNTY_FIPS)
        incomplete_rows: list[dict[str, Any]] = []
        for (vintage, variable), group in direct_acs.groupby(
            ["benchmark_vintage", "variable_name"],
            dropna=False,
            sort=True,
        ):
            actual_counties = set(group["county_fips"].dropna().astype(str))
            if actual_counties != expected_counties:
                incomplete_rows.append(
                    {
                        "benchmark_vintage": vintage,
                        "variable_name": variable,
                        "county_count": len(actual_counties),
                        "missing_counties": "|".join(
                            sorted(expected_counties - actual_counties)
                        ),
                        "unexpected_counties": "|".join(
                            sorted(actual_counties - expected_counties)
                        ),
                    }
                )
        incomplete_acs = pd.DataFrame(incomplete_rows)
    else:
        incomplete_acs = pd.DataFrame(
            columns=[
                "benchmark_vintage",
                "variable_name",
                "county_count",
                "missing_counties",
                "unexpected_counties",
            ]
        )
    check(
        "acs_all_california_counties",
        "error",
        incomplete_acs,
        (
            "Each direct ACS variable-vintage must contain the exact official "
            "set of 58 California county FIPS codes, including published "
            "missing rows."
        ),
    )

    check(
        "version_a_required_direct_acs_inventory",
        "error",
        _required_output_failures(
            work,
            direct_acs,
            required_variables=REQUIRED_DIRECT_ACS_VARIABLES,
        ),
        (
            "Every county/ACS-vintage must retain all ten direct ACS input "
            "rows, including published nulls."
        ),
    )

    check(
        "version_a_required_output_inventory",
        "error",
        _required_output_failures(
            work,
            direct_acs,
            required_variables=REQUIRED_DERIVED_VARIABLES,
        ),
        (
            "Every county/ACS-vintage must retain every core Version A "
            "derived row; unavailable inputs produce flagged nulls rather "
            "than omitted variables."
        ),
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
            "aggregate_owner_value_partition_identity",
            "acs_aggregate_owner_value_all_benchmark",
            (
                "acs_aggregate_owner_value_mortgaged_benchmark",
                "acs_aggregate_owner_value_no_mortgage_benchmark",
            ),
            lambda wide: wide[
                "acs_aggregate_owner_value_mortgaged_benchmark"
            ]
            + wide["acs_aggregate_owner_value_no_mortgage_benchmark"],
            (
                "Published all-owner aggregate value must equal the "
                "mortgaged-owner plus mortgage-free-owner components within "
                "$100, the maximum discrepancy from Census independently "
                "rounding each aggregate matrix cell to the nearest $100."
            ),
        ),
        (
            "aggregate_owner_income_partition_identity",
            "acs_aggregate_household_income_owner_benchmark",
            (
                "acs_aggregate_household_income_mortgaged_owner_benchmark",
                "acs_aggregate_household_income_no_mortgage_owner_benchmark",
            ),
            lambda wide: wide[
                "acs_aggregate_household_income_mortgaged_owner_benchmark"
            ]
            + wide[
                "acs_aggregate_household_income_no_mortgage_owner_benchmark"
            ],
            (
                "Published all-owner aggregate income must equal the "
                "mortgaged-owner plus mortgage-free-owner components within "
                "$100, the maximum discrepancy from Census independently "
                "rounding each aggregate matrix cell to the nearest $100."
            ),
        ),
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
            "debt_per_mortgaged_unit_identity",
            "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
            (
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_mortgaged_owner_units_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ]
            / wide["acs_mortgaged_owner_units_benchmark"],
            "Debt per mortgaged owner unit must use the direct ACS mortgaged-unit denominator.",
        ),
        (
            "debt_per_owner_unit_identity",
            "acs_first_mortgage_debt_per_owner_unit_benchmark",
            (
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_owner_occupied_units_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ]
            / wide["acs_owner_occupied_units_benchmark"],
            "Debt per owner unit must use all owner-occupied units.",
        ),
        (
            "debt_per_capita_identity",
            "acs_first_mortgage_debt_per_capita_benchmark",
            (
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_total_population_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ]
            / wide["acs_total_population_benchmark"],
            "Debt per capita must use the direct ACS total-population denominator.",
        ),
        (
            "debt_to_owner_income_identity",
            "acs_first_mortgage_debt_to_owner_income_benchmark",
            (
                "acs_implied_first_mortgage_balance_cal_benchmark",
                "acs_aggregate_household_income_owner_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ]
            / wide["acs_aggregate_household_income_owner_benchmark"],
            "Debt-to-owner-income must use published aggregate income for all owner households.",
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
        (
            "equity_per_owner_unit_identity",
            "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
            (
                "acs_implied_owner_occupied_home_equity_all_benchmark",
                "acs_owner_occupied_units_benchmark",
            ),
            lambda wide: wide[
                "acs_implied_owner_occupied_home_equity_all_benchmark"
            ]
            / wide["acs_owner_occupied_units_benchmark"],
            "Equity per owner unit must use all-owner equity and all owner-occupied units.",
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
                absolute_tolerance=(
                    ACS_AGGREGATE_PARTITION_ROUNDING_TOLERANCE
                    if check_id
                    in {
                        "aggregate_owner_value_partition_identity",
                        "aggregate_owner_income_partition_identity",
                    }
                    else 0.0
                ),
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


def benchmark_coverage_reports(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return row-level county coverage and source-variable summaries.

    Missing and suppressed observations remain rows in the county report.
    The summary distinguishes county presence from nonmissing-value coverage
    so a published null can never be mistaken for a missing county.
    """
    work = normalize_benchmark(frame)
    county_columns = [
        "benchmark_vintage",
        "reference_date",
        "release_date",
        "source_release_dates",
        "county_fips",
        "source_id",
        "variable_name",
        "value",
        "is_suppressed",
        "sample_n",
        "coverage_rate",
        "parameter_geography",
        "quality_flag",
    ]
    county = work.loc[:, county_columns].copy()
    county["county_row_present"] = True
    county["value_available"] = county["value"].notna()
    county["value_missing"] = county["value"].isna()
    county = county.sort_values(
        ["benchmark_vintage", "source_id", "variable_name", "county_fips"],
        na_position="last",
    ).reset_index(drop=True)

    keys = ["benchmark_vintage", "source_id", "variable_name"]
    rows: list[dict[str, Any]] = []
    for key, group in county.groupby(keys, dropna=False, sort=True):
        vintage, source_id, variable_name = key
        county_count = int(group["county_fips"].nunique())
        available_count = int(group["value_available"].sum())
        reported_coverage = pd.to_numeric(
            group["coverage_rate"], errors="coerce"
        )
        release_dates = pd.to_datetime(group["release_date"], errors="coerce")
        reference_dates = pd.to_datetime(
            group["reference_date"], errors="coerce"
        )
        parameter_geographies = sorted(
            {
                str(value)
                for value in group["parameter_geography"].dropna()
                if str(value)
            }
        )
        source_release_audits = sorted(
            {
                str(value)
                for value in group["source_release_dates"].dropna()
                if str(value)
            }
        )
        rows.append(
            {
                "benchmark_vintage": vintage,
                "source_id": source_id,
                "variable_name": variable_name,
                "county_count": county_count,
                "expected_california_county_count": CALIFORNIA_COUNTY_COUNT,
                "county_presence_rate": (
                    county_count / CALIFORNIA_COUNTY_COUNT
                ),
                "value_available_count": available_count,
                "value_missing_count": int(group["value_missing"].sum()),
                "suppressed_count": int(
                    group["is_suppressed"].fillna(False).sum()
                ),
                "value_coverage_rate": (
                    available_count / county_count
                    if county_count
                    else np.nan
                ),
                "minimum_input_coverage_rate": (
                    float(reported_coverage.min())
                    if reported_coverage.notna().any()
                    else np.nan
                ),
                "mean_input_coverage_rate": (
                    float(reported_coverage.mean())
                    if reported_coverage.notna().any()
                    else np.nan
                ),
                "reference_date_min": (
                    reference_dates.min()
                    if reference_dates.notna().any()
                    else pd.NaT
                ),
                "reference_date_max": (
                    reference_dates.max()
                    if reference_dates.notna().any()
                    else pd.NaT
                ),
                "release_date_min": (
                    release_dates.min()
                    if release_dates.notna().any()
                    else pd.NaT
                ),
                "release_date_max": (
                    release_dates.max()
                    if release_dates.notna().any()
                    else pd.NaT
                ),
                "source_release_dates": "|".join(source_release_audits),
                "parameter_geographies": "|".join(parameter_geographies),
            }
        )
    source = pd.DataFrame(rows)
    if not source.empty:
        source = source.sort_values(
            keys, na_position="last"
        ).reset_index(drop=True)
    return county, source


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
        county_coverage, source_coverage = benchmark_coverage_reports(frame)
        coverage_dir = Path(root) / "output" / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        county_coverage.to_csv(
            coverage_dir / "version_a_county_coverage.csv", index=False
        )
        source_coverage.to_csv(
            coverage_dir / "version_a_source_coverage.csv", index=False
        )
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


__all__ = [
    "benchmark_coverage_reports",
    "benchmark_qa_results",
    "run_benchmark_qa",
]
