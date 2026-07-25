"""Source-faithful annual HMDA Modified LAR down-payment benchmarks.

This module intentionally stops at the annual HMDA source period.  It does
not repeat annual rates into months, distribute annual flows, interpolate
small samples, or replace missing combined LTV with ``loan_amount`` divided
by ``property_value``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import BadZipFile, ZipFile

import pandas as pd

from src.benchmark.finance import (
    calculate_hmda_downpayment_metrics,
    validate_benchmark_metadata,
)


REQUIRED_HMDA_COLUMNS: tuple[str, ...] = (
    "activity_year",
    "state_code",
    "county_code",
    "action_taken",
    "loan_type",
    "loan_purpose",
    "lien_status",
    "combined_loan_to_value_ratio",
    "property_value",
    "occupancy_type",
)

LOAN_TYPE_GROUPS: tuple[tuple[str, str | None], ...] = (
    ("all", None),
    ("conventional", "1"),
    ("fha", "2"),
    ("va", "3"),
)

METRIC_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "hmda_median_downpayment_rate",
        "hmda_median_downpayment_rate",
        "rate",
    ),
    (
        "hmda_property_value_weighted_downpayment_rate",
        "hmda_property_value_weighted_downpayment_rate",
        "rate",
    ),
    (
        "hmda_aggregate_downpayment_flow",
        "hmda_aggregate_downpayment_flow",
        "USD",
    ),
    (
        "share_downpayment_below_3pct",
        "hmda_share_downpayment_below_3pct",
        "share",
    ),
    (
        "share_downpayment_below_5pct",
        "hmda_share_downpayment_below_5pct",
        "share",
    ),
    (
        "share_downpayment_below_10pct",
        "hmda_share_downpayment_below_10pct",
        "share",
    ),
    (
        "share_downpayment_below_20pct",
        "hmda_share_downpayment_below_20pct",
        "share",
    ),
)

DIAGNOSTIC_FIELDS: tuple[str, ...] = (
    "input_n",
    "property_value_reported_n",
    "property_value_numeric_n",
    "property_value_positive_n",
    "cltv_reported_n",
    "cltv_numeric_n",
    "cltv_valid_n",
    "cltv_invalid_bounds_n",
    "sample_n",
    "coverage_rate",
    "property_value_coverage_rate",
    "cltv_coverage_rate",
    "meets_minimum_sample",
    "negative_downpayment_n",
)

FILTER_DEFINITION = (
    "HMDA originated (action_taken=1), home-purchase (loan_purpose=1), "
    "principal-residence (occupancy_type=1), first-lien (lien_status=1) "
    "mortgage-financed loans"
)

CASH_BUYER_NOTE = (
    "Cash buyers are excluded because HMDA observes mortgage applications "
    "and originations, not all property transfers."
)

CLTV_NOTE = (
    "Down payment uses reported combined_loan_to_value_ratio and "
    "property_value. Missing or invalid CLTV is never replaced with "
    "loan_amount/property_value, and values are not winsorized."
)


class HMDAFormatError(ValueError):
    """Raised when a Modified LAR input does not match the required schema."""


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _record_value(
    record: Mapping[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = record.get(key)
        if not _is_blank(value):
            return value
    return None


def _date_text(value: Any, field_name: str) -> str:
    if _is_blank(value):
        raise ValueError(
            f"{field_name} must be supplied explicitly; it is not inferred"
        )
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{field_name} must be a valid date")
    return pd.Timestamp(parsed).date().isoformat()


def _year_text(value: Any, field_name: str = "activity_year") -> str | None:
    if _is_blank(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 4 or not text.isdigit():
        raise ValueError(f"{field_name} must be a four-digit year")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _csv_payload(path: Path) -> Iterator[BinaryIO]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("rb") as handle:
            yield handle
        return
    if suffix != ".zip":
        raise HMDAFormatError(
            f"{path.name}: expected a CSV or ZIP containing one CSV"
        )

    try:
        with ZipFile(path) as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir()
                and Path(member.filename).suffix.casefold() == ".csv"
            ]
            if len(members) != 1:
                raise HMDAFormatError(
                    f"{path.name}: ZIP must contain exactly one CSV; "
                    f"found {len(members)}"
                )
            with archive.open(members[0], "r") as handle:
                yield handle
    except BadZipFile as exc:
        raise HMDAFormatError(f"{path.name}: invalid ZIP archive") from exc


def iter_hmda_modified_lar_chunks(
    path: str | Path,
    *,
    chunksize: int = 100_000,
) -> Iterator[pd.DataFrame]:
    """Yield required Modified LAR columns as strings from CSV or one-file ZIP."""
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not isinstance(chunksize, int) or isinstance(chunksize, bool):
        raise TypeError("chunksize must be an integer")
    if chunksize <= 0:
        raise ValueError("chunksize must be positive")

    with _csv_payload(source_path) as payload:
        try:
            reader = pd.read_csv(
                payload,
                usecols=list(REQUIRED_HMDA_COLUMNS),
                dtype=str,
                keep_default_na=False,
                low_memory=False,
                chunksize=chunksize,
            )
            yield from reader
        except (UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
            raise HMDAFormatError(
                f"{source_path.name}: cannot parse Modified LAR CSV"
            ) from exc
        except ValueError as exc:
            if "Usecols do not match" in str(exc):
                raise HMDAFormatError(
                    f"{source_path.name}: missing required Modified LAR columns"
                ) from exc
            raise


def _clean_codes(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _normalize_county_fips(series: pd.Series) -> pd.Series:
    """Retain only published five-character county FIPS identifiers."""
    county = _clean_codes(series)
    return county.where(county.str.fullmatch(r"\d{5}", na=False))


def _eligible_hmda_rows(chunk: pd.DataFrame) -> pd.DataFrame:
    code = {
        column: _clean_codes(chunk[column])
        for column in (
            "action_taken",
            "loan_purpose",
            "occupancy_type",
            "lien_status",
        )
    }
    eligible = (
        code["action_taken"].eq("1")
        & code["loan_purpose"].eq("1")
        & code["occupancy_type"].eq("1")
        & code["lien_status"].eq("1")
    )
    return chunk.loc[
        eligible,
        [
            "_activity_year",
            "_county_fips",
            "loan_type",
            "property_value",
            "combined_loan_to_value_ratio",
        ],
    ].copy()


def _loan_group_variable(base_name: str, loan_group: str) -> str:
    group_suffix = "" if loan_group == "all" else f"_{loan_group}"
    return f"{base_name}{group_suffix}_benchmark"


def _group_universe(loan_group: str, loan_code: str | None) -> str:
    if loan_code is None:
        detail = "all reported HMDA loan types"
    else:
        detail = f"{loan_group.upper()} loans (loan_type={loan_code})"
    return f"{FILTER_DEFINITION}; {detail}. {CASH_BUYER_NOTE}"


def _group_notes(
    loan_group: str,
    minimum_sample: int,
    meets_minimum: bool,
) -> str:
    notes = [CLTV_NOTE, CASH_BUYER_NOTE]
    if not meets_minimum:
        notes.append(
            f"{loan_group} estimates are null because valid sample_n is "
            f"below the minimum of {minimum_sample}; diagnostics are retained."
        )
    return " ".join(notes)


def _metadata_for_year(
    *,
    year: str,
    source_path: Path,
    record: Mapping[str, Any],
    release_date: str,
    checksum_sha256: str,
) -> dict[str, Any]:
    source_vintage = _record_value(record, "source_vintage", "data_vintage")
    benchmark_vintage = _record_value(record, "benchmark_vintage")
    if _is_blank(benchmark_vintage):
        benchmark_vintage = f"{year}_HMDA_Modified_LAR"

    return {
        "database_version": "A. Benchmark",
        "source_frequency": "annual",
        "native_frequency": "annual",
        "final_frequency": "annual",
        "source_period_start": f"{year}-01-01",
        "source_period_end": f"{year}-12-31",
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "reference_date": f"{year}-07-01",
        "observation_date": f"{year}-07-01",
        "month": None,
        "release_date": release_date,
        "source_geography": "county",
        "input_geography": "loan/application record with tract and county",
        "parameter_geography": "county",
        "monthly_method": "not_applicable_source_period",
        "is_modeled": False,
        "is_nowcast": False,
        "is_imputed": False,
        "is_interpolated": False,
        "benchmark_vintage": str(benchmark_vintage),
        "source_vintage": (
            None if _is_blank(source_vintage) else str(source_vintage)
        ),
        "retrieved_at": _record_value(record, "retrieved_at"),
        "checksum_sha256": checksum_sha256,
        "source_filename": str(
            _record_value(record, "source_filename") or source_path.name
        ),
        "source_id": _record_value(record, "source_id")
        or "hmda_modified_lar",
        "provider": "FFIEC HMDA",
        "construction_method": (
            "Direct annual county aggregation of reported property_value "
            "and combined_loan_to_value_ratio"
        ),
    }


def clean_hmda_modified_lar(
    path: str | Path,
    record: Mapping[str, Any] | None = None,
    *,
    release_date: Any = None,
    activity_year: int | str | None = None,
    minimum_sample: int = 50,
    split_minimum_sample: int = 100,
    maximum_cltv_inclusive: float = 150.0,
    chunksize: int = 100_000,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Build annual county Version A down-payment observations.

    The result is long-form: each county/year/loan-type group has one row per
    metric.  ``all`` includes every eligible reported loan type, while the
    conventional, FHA, and VA rows use HMDA ``loan_type`` codes 1, 2, and 3.
    Counties observed in the California source are retained even when an
    eligible loan-type group has zero records; low-sample values are null but
    their diagnostics remain populated.

    ``release_date`` must be supplied either directly or in ``record``.  It is
    never inferred from the activity year, retrieval timestamp, or filename.
    """
    source_path = Path(path)
    source_record: Mapping[str, Any] = record or {}
    resolved_release_date = _date_text(
        release_date
        if not _is_blank(release_date)
        else _record_value(source_record, "release_date"),
        "release_date",
    )
    selected_year = _year_text(
        activity_year
        if not _is_blank(activity_year)
        else _record_value(source_record, "activity_year", "year")
    )
    if minimum_sample < 0 or split_minimum_sample < 0:
        raise ValueError("minimum sample thresholds cannot be negative")

    observed_keys: set[tuple[str, str]] = set()
    eligible_parts: dict[tuple[str, str], list[pd.DataFrame]] = {}
    saw_chunk = False
    for chunk in iter_hmda_modified_lar_chunks(
        source_path,
        chunksize=chunksize,
    ):
        saw_chunk = True
        state = _clean_codes(chunk["state_code"]).str.upper()
        county = _normalize_county_fips(chunk["county_code"])
        years = _clean_codes(chunk["activity_year"])
        valid_year = years.str.fullmatch(r"\d{4}", na=False)
        california = state.isin({"CA", "06", "6", "CALIFORNIA"})
        california &= county.str.startswith("06", na=False)
        selected = california & county.notna() & valid_year
        if selected_year is not None:
            selected &= years.eq(selected_year)

        if not selected.any():
            continue
        selected_chunk = chunk.loc[selected].copy()
        selected_chunk["_activity_year"] = years.loc[selected].astype(str)
        selected_chunk["_county_fips"] = county.loc[selected].astype(str)
        chunk_keys = zip(
            selected_chunk["_activity_year"],
            selected_chunk["_county_fips"],
            strict=True,
        )
        observed_keys.update(chunk_keys)

        eligible = _eligible_hmda_rows(selected_chunk)
        if eligible.empty:
            continue
        for key, group in eligible.groupby(
            ["_activity_year", "_county_fips"],
            sort=False,
            dropna=False,
        ):
            clean_key = (str(key[0]), str(key[1]))
            eligible_parts.setdefault(clean_key, []).append(
                group[
                    [
                        "loan_type",
                        "property_value",
                        "combined_loan_to_value_ratio",
                    ]
                ].copy()
            )

    if not saw_chunk:
        raise HMDAFormatError(f"{source_path.name}: Modified LAR CSV has no rows")
    if not observed_keys:
        year_detail = f" for activity_year={selected_year}" if selected_year else ""
        raise ValueError(
            f"{source_path.name}: no California records with valid county FIPS"
            f"{year_detail}"
        )

    checksum_sha256 = str(
        _record_value(source_record, "checksum_sha256") or _sha256(source_path)
    )
    output_rows: list[dict[str, Any]] = []
    empty_group = pd.DataFrame(
        columns=["property_value", "combined_loan_to_value_ratio"]
    )
    for year, county_fips in sorted(observed_keys):
        all_loans = (
            pd.concat(eligible_parts[(year, county_fips)], ignore_index=True)
            if (year, county_fips) in eligible_parts
            else pd.DataFrame(
                columns=[
                    "loan_type",
                    "property_value",
                    "combined_loan_to_value_ratio",
                ]
            )
        )
        year_metadata = _metadata_for_year(
            year=year,
            source_path=source_path,
            record=source_record,
            release_date=resolved_release_date,
            checksum_sha256=checksum_sha256,
        )
        for loan_group, loan_code in LOAN_TYPE_GROUPS:
            if loan_code is None:
                group = all_loans
                group_minimum = minimum_sample
            else:
                loan_type = _clean_codes(all_loans["loan_type"])
                group = all_loans.loc[loan_type.eq(loan_code)]
                group_minimum = split_minimum_sample
            metric_input = (
                group[
                    ["property_value", "combined_loan_to_value_ratio"]
                ].copy()
                if not group.empty
                else empty_group
            )
            metrics = calculate_hmda_downpayment_metrics(
                metric_input,
                minimum_sample=group_minimum,
                maximum_cltv_inclusive=maximum_cltv_inclusive,
            )
            diagnostics = {
                field: metrics[field] for field in DIAGNOSTIC_FIELDS
            }
            common = {
                **year_metadata,
                "activity_year": year,
                "year": int(year),
                "geo_id": f"COUNTY_{county_fips}",
                "state_fips": "06",
                "county_fips": county_fips,
                "geography": "county",
                "loan_type_group": loan_group,
                "loan_type_code": loan_code,
                "minimum_sample": group_minimum,
                "universe_definition": _group_universe(
                    loan_group,
                    loan_code,
                ),
                "notes": _group_notes(
                    loan_group,
                    group_minimum,
                    bool(metrics["meets_minimum_sample"]),
                ),
                "is_suppressed": not bool(metrics["meets_minimum_sample"]),
                "quality_flag": (
                    "reported"
                    if bool(metrics["meets_minimum_sample"])
                    else "insufficient_sample"
                ),
                **diagnostics,
            }
            for metric_key, base_name, unit in METRIC_SPECS:
                value = float(metrics[metric_key])
                if math.isnan(value):
                    value = math.nan
                variable_name = _loan_group_variable(
                    base_name,
                    loan_group,
                )
                output_rows.append(
                    {
                        **common,
                        "variable_name": variable_name,
                        "variable_id": variable_name,
                        "value": value,
                        "unit": unit,
                        "low_estimate": math.nan,
                        "central_estimate": value,
                        "high_estimate": math.nan,
                    }
                )

    result = pd.DataFrame(output_rows)
    validate_benchmark_metadata(result)
    group_order = {
        name: order
        for order, (name, _) in enumerate(LOAN_TYPE_GROUPS)
    }
    metric_order = {
        _loan_group_variable(base_name, loan_group): order
        for loan_group, _ in LOAN_TYPE_GROUPS
        for order, (_, base_name, _) in enumerate(METRIC_SPECS)
    }
    result["_group_order"] = result["loan_type_group"].map(group_order)
    result["_metric_order"] = result["variable_name"].map(metric_order)
    result = (
        result.sort_values(
            [
                "activity_year",
                "county_fips",
                "_group_order",
                "_metric_order",
            ]
        )
        .drop(columns=["_group_order", "_metric_order"])
        .reset_index(drop=True)
    )
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(destination, index=False)
    return result


def clean_hmda(
    path: str | Path,
    record: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    """Compatibility entry point for the Modified LAR benchmark cleaner."""
    return clean_hmda_modified_lar(path, record, **kwargs)
