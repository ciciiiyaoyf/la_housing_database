"""Strict parser for the 2023 AHS California Table Creator export.

The Table Creator CSV does not embed its selected geography or survey year.
Callers must therefore supply both assertions explicitly.  The constructed
aggregate uses only reported outstanding-principal cases and is deliberately
kept separate from provider-published component rows.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from src.cleaning.common import OBSERVATION_COLUMNS, metadata_fields


SOURCE_ID = "census_ahs_2023_ca_table14a"
EXPECTED_SOURCE_PERIOD = 2023
EXPECTED_GEOGRAPHY = "California"
EXPECTED_COLUMNS = (
    "Characteristics",
    "Estimate",
    "Margin of Error +/-",
)
BIN_RECONCILIATION_TOLERANCE_THOUSANDS = Decimal("0.2")

HOUSEHOLDS_VARIABLE_ID = "ahs_ca_households_with_regular_mortgage"
AGGREGATE_PROXY_VARIABLE_ID = (
    "ahs_ca_reported_case_primary_mortgage_outstanding_principal_proxy"
)

PUBLISHED_TRANSFORMATION_ID = "ahs_table14a_published_components_v1"
AGGREGATE_TRANSFORMATION_ID = (
    "ahs_table14a_reported_case_outstanding_principal_proxy_v1"
)

EXPECTED_SECTION_SEQUENCE = (
    "CHARACTERISTICS OF PRIMARY MORTGAGES",
    "Initial Amount Borrowed or Refinanced",
    "Term of Mortgage",
    "Interest Rate of Mortgage",
    "Year Mortgage Obtained",
    "Total Outstanding Principal Amount",
    "Mortgage Payment Amount",
    "Items Included in Mortgage Payment",
    "Frequency of Mortgage Payment",
    "Primary Mortgage Refinance Status",
    "Type of Federal Government Mortgage Insurance",
    "Type of Mortgage",
    "Cash Received in Primary Mortgage Refinance",
    (
        "Percent of Primary Mortgage Refinanced Cash Used for Home Additions, "
        "Improvements, or Repairs"
    ),
    "HOUSEHOLD CHARACTERISTICS",
    "Mortgages Currently on Property",
    "Total Remaining Debt Across All Mortgages or Similar Debts",
    "Current Total Loan as Percent of Value",
    "OWNERS WITH ONE OR MORE HOME-EQUITY LINE-OF-CREDIT MORTGAGES",
    "Total Home-Equity Line-of-Credit Limit",
    "Home-Equity Line-of-Credit Has a Current Balance",
    "Current Line-of-Credit Interest Rate",
    "Line-of-Credit Monthly Payment",
    "Line-of-Credit Amount Used for Home Additions, Improvements, or Repairs",
)

OUTSTANDING_PRINCIPAL_BINS = (
    ("Less than $20,000", "lt_20000"),
    ("$20,000 to $39,999", "20000_39999"),
    ("$40,000 to $59,999", "40000_59999"),
    ("$60,000 to $79,999", "60000_79999"),
    ("$80,000 to $99,999", "80000_99999"),
    ("$100,000 to $119,999", "100000_119999"),
    ("$120,000 to $149,999", "120000_149999"),
    ("$150,000 to $199,999", "150000_199999"),
    ("$200,000 to $249,999", "200000_249999"),
    ("$250,000 to $299,999", "250000_299999"),
    ("$300,000 to $349,999", "300000_349999"),
    ("$350,000 to $399,999", "350000_399999"),
    ("$400,000 to $499,999", "400000_499999"),
    ("$500,000 or more", "500000_plus"),
)

TARGET_SECTION_LABELS = (
    *(label for label, _ in OUTSTANDING_PRINCIPAL_BINS),
    "Not reported",
    "Median (dollars)",
    "Mean (dollars)",
)

EXTRA_OBSERVATION_COLUMNS = ("is_proxy", "is_observed")
OUTPUT_COLUMNS = (*OBSERVATION_COLUMNS, *EXTRA_OBSERVATION_COLUMNS)
_NUMERIC_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")


@dataclass(frozen=True)
class PublishedEstimate:
    estimate: Decimal
    margin_of_error: Decimal


@dataclass(frozen=True)
class ParsedMortgageTable:
    households: PublishedEstimate
    bins: tuple[tuple[str, str, PublishedEstimate], ...]
    not_reported: PublishedEstimate
    median: PublishedEstimate
    conditional_mean: PublishedEstimate
    aggregate_reported_case_proxy: Decimal


def _read_rows(path: Path) -> list[tuple[str, str, str]]:
    if path.suffix.lower() != ".csv":
        raise ValueError(f"AHS Table Creator input must be CSV: {path.name}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            try:
                header = tuple(next(reader))
            except StopIteration as exc:
                raise ValueError("AHS Table Creator CSV is empty") from exc
            if header != EXPECTED_COLUMNS:
                raise ValueError(
                    "AHS Table Creator column contract changed: "
                    f"expected={EXPECTED_COLUMNS!r}, actual={header!r}"
                )
            rows: list[tuple[str, str, str]] = []
            for line_number, row in enumerate(reader, start=2):
                if len(row) != len(EXPECTED_COLUMNS):
                    raise ValueError(
                        "AHS Table Creator row width changed at line "
                        f"{line_number}: expected=3, actual={len(row)}"
                    )
                rows.append(tuple(value.strip() for value in row))
    except UnicodeError as exc:
        raise ValueError(f"AHS Table Creator CSV is not valid UTF-8: {path.name}") from exc
    if not rows:
        raise ValueError("AHS Table Creator CSV contains no data rows")
    return rows


def _validate_sections(rows: list[tuple[str, str, str]]) -> None:
    headings = tuple(
        characteristic
        for characteristic, estimate, margin in rows
        if characteristic and not estimate and not margin
    )
    if headings != EXPECTED_SECTION_SEQUENCE:
        raise ValueError(
            "AHS Table Creator section sequence changed: "
            f"expected={EXPECTED_SECTION_SEQUENCE!r}, actual={headings!r}"
        )


def _unique_index(
    rows: list[tuple[str, str, str]], characteristic: str
) -> int:
    matches = [
        index
        for index, (label, _estimate, _margin) in enumerate(rows)
        if label == characteristic
    ]
    if len(matches) != 1:
        raise ValueError(
            "AHS Table Creator expected exactly one row labeled "
            f"{characteristic!r}; found={matches}"
        )
    return matches[0]


def _decimal(value: str, label: str, field: str) -> Decimal:
    normalized = value.replace(",", "")
    if not _NUMERIC_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"AHS Table Creator {label!r} {field} is not an unsuppressed "
            f"nonnegative number: {value!r}"
        )
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(
            f"AHS Table Creator {label!r} {field} is invalid: {value!r}"
        ) from exc
    if not number.is_finite():
        raise ValueError(
            f"AHS Table Creator {label!r} {field} is not finite: {value!r}"
        )
    return number


def _published(row: tuple[str, str, str]) -> PublishedEstimate:
    label, estimate, margin = row
    parsed = PublishedEstimate(
        estimate=_decimal(estimate, label, "estimate"),
        margin_of_error=_decimal(margin, label, "margin of error"),
    )
    if parsed.estimate <= 0:
        raise ValueError(f"AHS Table Creator {label!r} estimate must be positive")
    return parsed


def parse_ahs_table_creator(
    path: Path,
    *,
    expected_source_period: int | None,
    expected_geography: str | None,
) -> ParsedMortgageTable:
    """Parse and validate the target rows without assigning silent context."""

    if isinstance(expected_source_period, bool) or expected_source_period != 2023:
        raise ValueError(
            "AHS Table Creator caller must explicitly assert source period 2023"
        )
    if expected_geography != EXPECTED_GEOGRAPHY:
        raise ValueError(
            "AHS Table Creator caller must explicitly assert geography California"
        )

    rows = _read_rows(path)
    _validate_sections(rows)
    blank = ("", "", "")

    primary_heading = _unique_index(
        rows, "CHARACTERISTICS OF PRIMARY MORTGAGES"
    )
    initial_heading = _unique_index(rows, "Initial Amount Borrowed or Refinanced")
    household_rows = rows[primary_heading + 1 : initial_heading]
    if (
        len(household_rows) != 3
        or household_rows[0] != blank
        or household_rows[2] != blank
        or household_rows[1][0] != "Households with at least one regular mortgage"
    ):
        raise ValueError(
            "AHS Table Creator primary-mortgage household preamble changed"
        )
    households = _published(household_rows[1])

    target_heading = _unique_index(rows, "Total Outstanding Principal Amount")
    next_heading = _unique_index(rows, "Mortgage Payment Amount")
    target_rows = rows[target_heading + 1 : next_heading]
    if (
        len(target_rows) != len(TARGET_SECTION_LABELS) + 2
        or target_rows[0] != blank
        or target_rows[-1] != blank
        or tuple(row[0] for row in target_rows[1:-1]) != TARGET_SECTION_LABELS
    ):
        raise ValueError(
            "AHS Table Creator Total Outstanding Principal Amount sequence changed"
        )

    published_rows = target_rows[1:-1]
    bins = tuple(
        (label, slug, _published(published_rows[index]))
        for index, (label, slug) in enumerate(OUTSTANDING_PRINCIPAL_BINS)
    )
    not_reported = _published(published_rows[-3])
    median = _published(published_rows[-2])
    conditional_mean = _published(published_rows[-1])

    bin_sum_thousands = sum(
        (published.estimate for _label, _slug, published in bins),
        start=Decimal("0"),
    )
    expected_reported_thousands = households.estimate - not_reported.estimate
    reconciliation_difference = abs(
        bin_sum_thousands - expected_reported_thousands
    )
    if reconciliation_difference > BIN_RECONCILIATION_TOLERANCE_THOUSANDS:
        raise ValueError(
            "AHS reported-bin household reconciliation failed: "
            f"bins={bin_sum_thousands}, households_minus_not_reported="
            f"{expected_reported_thousands}, difference="
            f"{reconciliation_difference}, tolerance="
            f"{BIN_RECONCILIATION_TOLERANCE_THOUSANDS} thousand"
        )

    aggregate = (
        bin_sum_thousands * Decimal("1000") * conditional_mean.estimate
    )
    # Deliberately calculated only as a diagnostic.  It is not emitted because
    # it would assign reported-case mean debt to households with unreported debt.
    all_household_mean_scaled_extrapolation = (
        households.estimate * Decimal("1000") * conditional_mean.estimate
    )
    if all_household_mean_scaled_extrapolation < aggregate:
        raise ValueError("AHS all-household diagnostic is below reported-case proxy")

    return ParsedMortgageTable(
        households=households,
        bins=bins,
        not_reported=not_reported,
        median=median,
        conditional_mean=conditional_mean,
        aggregate_reported_case_proxy=aggregate,
    )


def _base_observation(
    *,
    variable_id: str,
    value: Decimal,
    reported_value: Decimal | None,
    unit: str,
    transformation_id: str,
    quality_flag: str,
    is_proxy: bool,
    is_observed: bool,
    record: dict[str, str],
) -> dict[str, object]:
    observation_date = pd.Timestamp("2023-12-31")
    row: dict[str, object] = {
        "observation_date": observation_date,
        "source_observation_date": observation_date,
        "period_start": pd.Timestamp("2023-01-01"),
        "period_end": observation_date,
        "geo_id": "STATE_06",
        "state_fips": "06",
        "county_fips": None,
        "variable_id": variable_id,
        "value": float(value),
        "reported_value": (
            float(reported_value) if reported_value is not None else None
        ),
        "unit": unit,
        "provider": "U.S. Census Bureau and HUD",
        "native_frequency": "biennial_survey_year",
        "final_frequency": "annual",
        "transformation_id": transformation_id,
        "is_imputed": False,
        "is_interpolated": False,
        "is_seasonally_adjusted": False,
        "quality_flag": quality_flag,
        "is_proxy": is_proxy,
        "is_observed": is_observed,
    }
    row.update(metadata_fields(record))
    return row


def _component_rows(
    *,
    variable_id: str,
    published: PublishedEstimate,
    unit: str,
    scale: Decimal,
    record: dict[str, str],
) -> list[dict[str, object]]:
    scale_note = (
        ";source_display_value_in_thousands;normalized_to_households"
        if scale == Decimal("1000")
        else ""
    )
    estimate = _base_observation(
        variable_id=variable_id,
        value=published.estimate * scale,
        reported_value=published.estimate,
        unit=unit,
        transformation_id=PUBLISHED_TRANSFORMATION_ID,
        quality_flag=(
            "provider_published_table_creator_estimate;table14a;"
            "caller_asserted_california_2023" + scale_note
        ),
        is_proxy=False,
        is_observed=True,
        record=record,
    )
    margin = _base_observation(
        variable_id=f"{variable_id}_moe",
        value=published.margin_of_error * scale,
        reported_value=published.margin_of_error,
        unit=unit,
        transformation_id=PUBLISHED_TRANSFORMATION_ID,
        quality_flag=(
            "provider_published_table_creator_margin_of_error;table14a;"
            "confidence_level_not_identified_in_csv;"
            "caller_asserted_california_2023" + scale_note
        ),
        is_proxy=False,
        is_observed=True,
        record=record,
    )
    return [estimate, margin]


def _validate_record(record: dict[str, str]) -> None:
    if record.get("source_id") != SOURCE_ID:
        raise ValueError(
            "AHS Table Creator cleaner received unexpected source_id="
            f"{record.get('source_id')!r}"
        )
    required = (
        "data_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
    )
    missing = [key for key in required if not str(record.get(key) or "").strip()]
    if missing:
        raise ValueError(f"AHS caller record lacks provenance fields: {missing}")
    checksum = str(record["checksum_sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise ValueError("AHS caller record checksum_sha256 must be 64 hex digits")


def _finalize(frame: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    missing = [column for column in OUTPUT_COLUMNS if column not in frame]
    if missing:
        raise ValueError(f"AHS clean rows lack required columns: {missing}")
    frame = frame[list(OUTPUT_COLUMNS)].copy()
    for column in (
        "observation_date",
        "source_observation_date",
        "period_start",
        "period_end",
    ):
        frame[column] = pd.to_datetime(frame[column], errors="raise")
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    frame["reported_value"] = pd.to_numeric(
        frame["reported_value"], errors="coerce"
    )
    for column in (
        "is_imputed",
        "is_interpolated",
        "is_seasonally_adjusted",
        "is_proxy",
        "is_observed",
    ):
        frame[column] = frame[column].astype("boolean")
    key_columns = ["observation_date", "geo_id", "variable_id"]
    if frame[key_columns].isna().any().any():
        raise ValueError("AHS clean rows contain a missing observation key")
    duplicates = frame.duplicated(key_columns, keep=False)
    if duplicates.any():
        values = frame.loc[duplicates, key_columns].astype(str).to_dict("records")
        raise ValueError(f"AHS clean rows contain duplicate keys: {values[:5]}")
    frame = frame.sort_values(key_columns).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False)
    return frame


def clean_ahs_table_creator(
    path: Path,
    record: dict[str, str],
    output_path: Path,
    *,
    expected_source_period: int | None = None,
    expected_geography: str | None = None,
) -> pd.DataFrame:
    """Emit published AHS components and one reported-case debt proxy."""

    _validate_record(record)
    parsed = parse_ahs_table_creator(
        path,
        expected_source_period=expected_source_period,
        expected_geography=expected_geography,
    )
    rows: list[dict[str, object]] = []
    rows.extend(
        _component_rows(
            variable_id=HOUSEHOLDS_VARIABLE_ID,
            published=parsed.households,
            unit="households",
            scale=Decimal("1000"),
            record=record,
        )
    )
    for _label, slug, published in parsed.bins:
        rows.extend(
            _component_rows(
                variable_id=(
                    "ahs_ca_primary_mortgage_outstanding_principal_"
                    f"{slug}_households"
                ),
                published=published,
                unit="households",
                scale=Decimal("1000"),
                record=record,
            )
        )
    rows.extend(
        _component_rows(
            variable_id=(
                "ahs_ca_primary_mortgage_outstanding_principal_"
                "not_reported_households"
            ),
            published=parsed.not_reported,
            unit="households",
            scale=Decimal("1000"),
            record=record,
        )
    )
    rows.extend(
        _component_rows(
            variable_id=(
                "ahs_ca_primary_mortgage_outstanding_principal_median"
            ),
            published=parsed.median,
            unit="current_dollars",
            scale=Decimal("1"),
            record=record,
        )
    )
    rows.extend(
        _component_rows(
            variable_id=(
                "ahs_ca_primary_mortgage_outstanding_principal_conditional_mean"
            ),
            published=parsed.conditional_mean,
            unit="current_dollars",
            scale=Decimal("1"),
            record=record,
        )
    )
    rows.append(
        _base_observation(
            variable_id=AGGREGATE_PROXY_VARIABLE_ID,
            value=parsed.aggregate_reported_case_proxy,
            reported_value=None,
            unit="current_dollars",
            transformation_id=AGGREGATE_TRANSFORMATION_ID,
            quality_flag=(
                "constructed_reported_case_proxy;sum_14_reported_bin_"
                "household_estimates_times_published_conditional_mean;"
                "excludes_not_reported_cases;does_not_assign_reported_mean_to_"
                "missing_balances;rounded_input_reconciliation_within_0_2_"
                "thousand_households;no_covariance_based_standard_error_or_ci;"
                "primary_mortgage_may_not_equal_legal_first_lien;"
                "statewide_public_calibration_proxy_not_exact_nmdb_control"
            ),
            is_proxy=True,
            is_observed=False,
            record=record,
        )
    )
    return _finalize(pd.DataFrame(rows), output_path)
