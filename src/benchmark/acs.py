"""Official ACS 5-year inputs for source-faithful Version A benchmarks.

The acquisition functions request the estimate and 90 percent margin of error
for a small, explicit set of Detailed Table variables.  One immutable
composite JSON file represents one ACS 5-year vintage.  Cleaning keeps the
published 60-month interval intact: values are not assigned to December,
interpolated, or distributed to months.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

from src.benchmark.finance import (
    REQUIRED_BENCHMARK_METADATA_FIELDS,
    validate_benchmark_metadata,
)


ACS_API_ROOT = "https://api.census.gov/data"
ACS_DATASET = "acs/acs5"
ACS_RAW_SCHEMA_VERSION = "acs_finance_benchmark_raw_v2"
ACS_USER_AGENT = "UCLA housing research data pipeline/0.2"
CALIFORNIA_STATE_FIPS = "06"

# California's 58 county components are the odd county codes from 001 to 115.
# Keeping the official five-character GEOID here makes an incomplete or
# accidentally non-county API response a structural failure.
CALIFORNIA_COUNTY_FIPS: tuple[str, ...] = tuple(
    f"06{county:03d}" for county in range(1, 116, 2)
)

ACS_VARIABLE_MAP: dict[str, dict[str, str]] = {
    "B01003_001": {
        "table_id": "B01003",
        "estimate_variable": "B01003_001E",
        "moe_variable": "B01003_001M",
        "variable_name": "acs_total_population_benchmark",
        "unit": "persons",
        "description": "Total resident population",
        "universe_definition": "Total population",
    },
    "B25089_002": {
        "table_id": "B25089",
        "estimate_variable": "B25089_002E",
        "moe_variable": "B25089_002M",
        "variable_name": (
            "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark"
        ),
        "unit": "dollars_per_month",
        "description": (
            "Aggregate selected monthly owner costs for housing units with "
            "a mortgage"
        ),
        "universe_definition": "Owner-occupied housing units",
    },
    "B25081_001": {
        "table_id": "B25081",
        "estimate_variable": "B25081_001E",
        "moe_variable": "B25081_001M",
        "variable_name": "acs_owner_occupied_units_benchmark",
        "unit": "housing_units",
        "description": "Total owner-occupied housing units",
        "universe_definition": "Owner-occupied housing units",
    },
    "B25081_002": {
        "table_id": "B25081",
        "estimate_variable": "B25081_002E",
        "moe_variable": "B25081_002M",
        "variable_name": "acs_mortgaged_owner_units_benchmark",
        "unit": "housing_units",
        "description": (
            "Owner-occupied housing units with a mortgage, contract to "
            "purchase, or similar debt"
        ),
        "universe_definition": "Owner-occupied housing units",
    },
    "B25082_001": {
        "table_id": "B25082",
        "estimate_variable": "B25082_001E",
        "moe_variable": "B25082_001M",
        "variable_name": "acs_aggregate_owner_value_all_benchmark",
        "unit": "dollars",
        "description": "Aggregate value of all owner-occupied housing units",
        "universe_definition": "Owner-occupied housing units",
    },
    "B25082_002": {
        "table_id": "B25082",
        "estimate_variable": "B25082_002E",
        "moe_variable": "B25082_002M",
        "variable_name": "acs_aggregate_owner_value_mortgaged_benchmark",
        "unit": "dollars",
        "description": (
            "Aggregate value of owner-occupied housing units with a mortgage"
        ),
        "universe_definition": "Owner-occupied housing units",
    },
    "B25082_003": {
        "table_id": "B25082",
        "estimate_variable": "B25082_003E",
        "moe_variable": "B25082_003M",
        "variable_name": (
            "acs_aggregate_owner_value_no_mortgage_benchmark"
        ),
        "unit": "dollars",
        "description": (
            "Aggregate value of owner-occupied housing units without a "
            "mortgage"
        ),
        "universe_definition": "Owner-occupied housing units",
    },
    "B25120_002": {
        "table_id": "B25120",
        "estimate_variable": "B25120_002E",
        "moe_variable": "B25120_002M",
        "variable_name": (
            "acs_aggregate_household_income_owner_benchmark"
        ),
        "unit": "inflation_adjusted_dollars_per_year",
        "description": (
            "Aggregate household income in the past 12 months for "
            "owner-occupied housing units"
        ),
        "universe_definition": "Occupied housing units",
    },
    "B25120_003": {
        "table_id": "B25120",
        "estimate_variable": "B25120_003E",
        "moe_variable": "B25120_003M",
        "variable_name": (
            "acs_aggregate_household_income_mortgaged_owner_benchmark"
        ),
        "unit": "inflation_adjusted_dollars_per_year",
        "description": (
            "Aggregate household income in the past 12 months for "
            "owner-occupied housing units with a mortgage"
        ),
        "universe_definition": "Occupied housing units",
    },
    "B25120_004": {
        "table_id": "B25120",
        "estimate_variable": "B25120_004E",
        "moe_variable": "B25120_004M",
        "variable_name": (
            "acs_aggregate_household_income_no_mortgage_owner_benchmark"
        ),
        "unit": "inflation_adjusted_dollars_per_year",
        "description": (
            "Aggregate household income in the past 12 months for "
            "owner-occupied housing units without a mortgage"
        ),
        "universe_definition": "Occupied housing units",
    },
}

ACS_TABLE_VARIABLES: dict[str, tuple[str, ...]] = {
    table_id: tuple(
        variable_id
        for variable_id, metadata in ACS_VARIABLE_MAP.items()
        if metadata["table_id"] == table_id
    )
    for table_id in ("B01003", "B25089", "B25081", "B25082", "B25120")
}

# Census represents unavailable estimates with large negative sentinel values.
# Preserve the raw code in the clean output while treating it as unavailable,
# rather than allowing it to enter benchmark calculations.
ACS_SPECIAL_VALUES: frozenset[int] = frozenset(
    {
        -222_222_222,
        -333_333_333,
        -555_555_555,
        -666_666_666,
        -777_777_777,
        -888_888_888,
        -999_999_999,
    }
)


class ACSFormatError(ValueError):
    """Raised when an ACS response or archive violates its structural contract."""


def _normalise_vintage(vintage: int | str) -> int:
    if isinstance(vintage, bool):
        raise ValueError("ACS vintage must be a four-digit year")
    try:
        year = int(vintage)
    except (TypeError, ValueError) as exc:
        raise ValueError("ACS vintage must be a four-digit year") from exc
    if str(vintage).strip() != str(year) or not 2009 <= year <= 9999:
        raise ValueError("ACS vintage must be a four-digit year of 2009 or later")
    return year


def acs_five_year_period(
    vintage: int | str,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Return the source start, source end, and documented midpoint convention.

    A vintage ending in year ``Y`` spans January of ``Y-4`` through December
    of ``Y``.  The benchmark reference date is July 1 of the middle year.  It
    is a period midpoint marker, not a claim that the ACS estimate is a July
    stock observation.
    """
    year = _normalise_vintage(vintage)
    return (
        pd.Timestamp(year=year - 4, month=1, day=1),
        pd.Timestamp(year=year, month=12, day=31),
        pd.Timestamp(year=year - 2, month=7, day=1),
    )


def expected_acs_header(table_id: str) -> tuple[str, ...]:
    """Return the only accepted ordered API header for a configured table."""
    if table_id not in ACS_TABLE_VARIABLES:
        raise KeyError(f"unsupported ACS finance table: {table_id}")
    columns = ["NAME"]
    for variable_id in ACS_TABLE_VARIABLES[table_id]:
        metadata = ACS_VARIABLE_MAP[variable_id]
        columns.extend(
            [metadata["estimate_variable"], metadata["moe_variable"]]
        )
    columns.extend(["state", "county"])
    return tuple(columns)


def build_acs_request_url(vintage: int | str, table_id: str) -> str:
    """Build the reproducible public request URL without exposing an API key."""
    year = _normalise_vintage(vintage)
    header = expected_acs_header(table_id)
    params = (
        ("get", ",".join(header[:-2])),
        ("for", "county:*"),
        ("in", f"state:{CALIFORNIA_STATE_FIPS}"),
    )
    query = urlencode(params, safe=",:*")
    return f"{ACS_API_ROOT}/{year}/{ACS_DATASET}?{query}"


def _validate_response(
    response_data: Any,
    table_id: str,
    *,
    require_complete_counties: bool,
) -> dict[str, Sequence[Any]]:
    if not isinstance(response_data, list) or not response_data:
        raise ACSFormatError(f"{table_id} response must be a nonempty JSON array")

    expected_header = expected_acs_header(table_id)
    actual_header = response_data[0]
    if not isinstance(actual_header, list) or tuple(actual_header) != expected_header:
        raise ACSFormatError(
            f"{table_id} header changed: expected={list(expected_header)}, "
            f"actual={actual_header}"
        )

    rows_by_fips: dict[str, Sequence[Any]] = {}
    expected_width = len(expected_header)
    for row_number, row in enumerate(response_data[1:], start=1):
        if not isinstance(row, list) or len(row) != expected_width:
            actual_width = len(row) if isinstance(row, list) else None
            raise ACSFormatError(
                f"{table_id} row {row_number} has width {actual_width}; "
                f"expected {expected_width}"
            )
        state = row[-2]
        county = row[-1]
        if state != CALIFORNIA_STATE_FIPS:
            raise ACSFormatError(
                f"{table_id} row {row_number} returned non-California "
                f"state code {state!r}"
            )
        if not isinstance(county, str) or not re.fullmatch(r"\d{3}", county):
            raise ACSFormatError(
                f"{table_id} row {row_number} has invalid county code "
                f"{county!r}"
            )
        county_fips = f"{state}{county}"
        if county_fips not in CALIFORNIA_COUNTY_FIPS:
            raise ACSFormatError(
                f"{table_id} returned unknown California county {county_fips}"
            )
        if county_fips in rows_by_fips:
            raise ACSFormatError(
                f"{table_id} returned duplicate county {county_fips}"
            )
        if not isinstance(row[0], str) or not row[0].strip():
            raise ACSFormatError(
                f"{table_id} row {row_number} has a blank county name"
            )
        rows_by_fips[county_fips] = row

    if require_complete_counties:
        actual = set(rows_by_fips)
        expected = set(CALIFORNIA_COUNTY_FIPS)
        if actual != expected:
            raise ACSFormatError(
                f"{table_id} county coverage changed: "
                f"missing={sorted(expected - actual)}, "
                f"extra={sorted(actual - expected)}"
            )
    elif not rows_by_fips:
        raise ACSFormatError(f"{table_id} returned no California county rows")
    return rows_by_fips


def _normalise_retrieved_at(value: datetime | None) -> datetime:
    retrieved_at = value or datetime.now(timezone.utc)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    return retrieved_at.astimezone(timezone.utc)


def _normalise_optional_date(value: date | datetime | str | None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid release_date: {value!r}")
    return pd.Timestamp(parsed).date().isoformat()


def fetch_acs_finance_payload(
    vintage: int | str,
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    release_date: date | datetime | str | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    """Fetch and validate one composite ACS finance payload.

    The returned object is ready to archive.  API keys are used for the
    request but deliberately excluded from archived request URLs.
    """
    year = _normalise_vintage(vintage)
    retrieved = _normalise_retrieved_at(retrieved_at)
    release = _normalise_optional_date(release_date)
    if release is None:
        raise ValueError(
            "release_date is required for an auditable ACS vintage snapshot"
        )
    period_start, period_end, reference_date = acs_five_year_period(year)
    if release is not None and pd.Timestamp(release) < period_end:
        raise ValueError("release_date cannot precede the ACS source period end")

    client = session or requests.Session()
    census_key = api_key or os.getenv("CENSUS_API_KEY")
    responses: dict[str, Any] = {}
    request_urls: dict[str, str] = {}
    base_url = f"{ACS_API_ROOT}/{year}/{ACS_DATASET}"

    for table_id in ACS_TABLE_VARIABLES:
        expected_header = expected_acs_header(table_id)
        params = {
            "get": ",".join(expected_header[:-2]),
            "for": "county:*",
            "in": f"state:{CALIFORNIA_STATE_FIPS}",
        }
        if census_key:
            params["key"] = census_key
        try:
            response = client.get(
                base_url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": ACS_USER_AGENT},
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"ACS API request failed for {year} {table_id}"
            ) from exc
        except ValueError as exc:
            raise ACSFormatError(
                f"ACS API returned invalid JSON for {year} {table_id}"
            ) from exc
        _validate_response(
            body,
            table_id,
            require_complete_counties=True,
        )
        request_urls[table_id] = build_acs_request_url(year, table_id)
        responses[table_id] = body

    return {
        "schema_version": ACS_RAW_SCHEMA_VERSION,
        "provider": "U.S. Census Bureau",
        "dataset": "American Community Survey 5-Year Detailed Tables",
        "dataset_path": ACS_DATASET,
        "vintage": str(year),
        "retrieved_at": retrieved.isoformat(),
        "release_date": release,
        "source_period_start": period_start.date().isoformat(),
        "source_period_end": period_end.date().isoformat(),
        "reference_date": reference_date.date().isoformat(),
        "source_interval_months": 60,
        "geography_query": {
            "for": "county:*",
            "in": f"state:{CALIFORNIA_STATE_FIPS}",
        },
        "request_url": request_urls,
        "responses": responses,
        "variable_map": ACS_VARIABLE_MAP,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_acs_finance_vintage(
    vintage: int | str,
    output_path: str | Path,
    **fetch_kwargs: Any,
) -> dict[str, Any]:
    """Fetch one vintage and create a new immutable composite JSON archive.

    Existing files are never replaced.  The return value contains the
    checksum and source filename needed by an external raw-file manifest.
    """
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"ACS raw archive already exists: {path}")

    payload = fetch_acs_finance_payload(vintage, **fetch_kwargs)
    payload["source_filename"] = path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    created_here = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            created_here = True
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # A failed first write is not a valid archived vintage.
        if created_here and path.exists():
            path.unlink()
        raise

    return {
        "vintage": payload["vintage"],
        "retrieved_at": payload["retrieved_at"],
        "source_filename": path.name,
        "archive_path": str(path),
        "checksum_sha256": _sha256(path),
        "request_url": payload["request_url"],
        "response_count": len(payload["responses"]),
    }


def _load_payload(
    source: str | Path | Mapping[str, Any],
) -> tuple[dict[str, Any], str | None, str | None]:
    if isinstance(source, Mapping):
        return dict(source), source.get("source_filename"), source.get(
            "checksum_sha256"
        )

    path = Path(source)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ACSFormatError(f"invalid ACS archive JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ACSFormatError("ACS archive root must be a JSON object")
    return payload, path.name, _sha256(path)


def _validate_variable_map(value: Any) -> None:
    if value != ACS_VARIABLE_MAP:
        raise ACSFormatError(
            "ACS variable map differs from the configured Version A contract"
        )


def _validate_archive(
    payload: Mapping[str, Any],
    *,
    require_complete_counties: bool,
) -> tuple[int, dict[str, dict[str, Sequence[Any]]]]:
    if payload.get("schema_version") != ACS_RAW_SCHEMA_VERSION:
        raise ACSFormatError(
            "unsupported ACS finance archive schema version: "
            f"{payload.get('schema_version')!r}"
        )
    year = _normalise_vintage(payload.get("vintage"))
    _validate_variable_map(payload.get("variable_map"))

    period_start, period_end, reference_date = acs_five_year_period(year)
    expected_period = {
        "source_period_start": period_start.date().isoformat(),
        "source_period_end": period_end.date().isoformat(),
        "reference_date": reference_date.date().isoformat(),
        "source_interval_months": 60,
    }
    for field, expected in expected_period.items():
        if payload.get(field) != expected:
            raise ACSFormatError(
                f"ACS archive {field} must be {expected!r}; "
                f"got {payload.get(field)!r}"
            )
    if payload.get("geography_query") != {
        "for": "county:*",
        "in": f"state:{CALIFORNIA_STATE_FIPS}",
    }:
        raise ACSFormatError(
            "ACS archive geography query must request all California counties"
        )

    retrieved_at = pd.to_datetime(
        payload.get("retrieved_at"), utc=True, errors="coerce"
    )
    if pd.isna(retrieved_at):
        raise ACSFormatError("ACS archive retrieved_at must be a valid timestamp")

    request_urls = payload.get("request_url")
    if not isinstance(request_urls, dict) or set(request_urls) != set(
        ACS_TABLE_VARIABLES
    ):
        raise ACSFormatError("ACS archive request_url table set changed")
    for table_id in ACS_TABLE_VARIABLES:
        expected_url = build_acs_request_url(year, table_id)
        if request_urls[table_id] != expected_url:
            raise ACSFormatError(
                f"{table_id} request URL differs from the official "
                "California county query"
            )

    responses = payload.get("responses")
    if not isinstance(responses, dict) or set(responses) != set(
        ACS_TABLE_VARIABLES
    ):
        raise ACSFormatError("ACS archive response table set changed")

    validated: dict[str, dict[str, Sequence[Any]]] = {}
    county_sets: list[set[str]] = []
    for table_id in ACS_TABLE_VARIABLES:
        rows = _validate_response(
            responses[table_id],
            table_id,
            require_complete_counties=require_complete_counties,
        )
        validated[table_id] = rows
        county_sets.append(set(rows))
    if any(counties != county_sets[0] for counties in county_sets[1:]):
        raise ACSFormatError(
            "ACS table responses do not contain the same California counties"
        )
    return year, validated


def _parse_acs_number(value: Any, field: str) -> tuple[float, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return math.nan, "null"
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ACSFormatError(
            f"{field} contains a nonnumeric ACS value: {value!r}"
        ) from exc
    if not math.isfinite(numeric):
        raise ACSFormatError(f"{field} must be finite or null")
    if numeric.is_integer() and int(numeric) in ACS_SPECIAL_VALUES:
        return math.nan, f"census_special_value_{int(numeric)}"
    return numeric, "reported"


def _status(estimate_status: str, moe_status: str) -> str:
    if estimate_status == "reported" and moe_status == "reported":
        return "reported"
    if estimate_status != "reported" and moe_status != "reported":
        return "estimate_and_moe_unavailable"
    if estimate_status != "reported":
        return "estimate_unavailable"
    return "moe_unavailable"


def clean_acs_finance_vintage(
    source: str | Path | Mapping[str, Any],
    output_path: str | Path | None = None,
    *,
    require_complete_counties: bool = True,
) -> pd.DataFrame:
    """Convert one composite ACS archive to long Version A benchmark rows.

    A row is emitted for every county-variable pair even when Census reports
    null or a suppression sentinel.  ``low_estimate`` and ``high_estimate``
    use the published 90 percent MOE; no interval or point estimate is
    synthesized when either required input is unavailable.
    """
    payload, source_filename, checksum = _load_payload(source)
    year, table_rows = _validate_archive(
        payload,
        require_complete_counties=require_complete_counties,
    )
    period_start, period_end, reference_date = acs_five_year_period(year)

    release_raw = payload.get("release_date")
    release = (
        pd.NaT
        if release_raw is None
        else pd.to_datetime(release_raw, errors="coerce")
    )
    if pd.isna(release):
        raise ACSFormatError("ACS archive release_date must be populated")
    if pd.notna(release) and pd.Timestamp(release) < period_end:
        raise ACSFormatError("ACS release_date cannot precede source_period_end")

    retrieved = pd.to_datetime(
        payload["retrieved_at"], utc=True, errors="raise"
    )
    request_urls = payload["request_url"]
    rows: list[dict[str, Any]] = []
    county_fips_values = sorted(
        set.intersection(*(set(table) for table in table_rows.values()))
    )

    for variable_id, metadata in ACS_VARIABLE_MAP.items():
        table_id = metadata["table_id"]
        header = expected_acs_header(table_id)
        estimate_position = header.index(metadata["estimate_variable"])
        moe_position = header.index(metadata["moe_variable"])
        for county_fips in county_fips_values:
            source_row = table_rows[table_id][county_fips]
            estimate_raw = source_row[estimate_position]
            moe_raw = source_row[moe_position]
            estimate, estimate_status = _parse_acs_number(
                estimate_raw, metadata["estimate_variable"]
            )
            moe, moe_status = _parse_acs_number(
                moe_raw, metadata["moe_variable"]
            )
            if not math.isnan(moe) and moe < 0:
                raise ACSFormatError(
                    f"{metadata['moe_variable']} returned a negative MOE "
                    f"for {county_fips}"
                )
            central = estimate
            low = (
                estimate - moe
                if not math.isnan(estimate) and not math.isnan(moe)
                else math.nan
            )
            high = (
                estimate + moe
                if not math.isnan(estimate) and not math.isnan(moe)
                else math.nan
            )
            quality_flag = _status(estimate_status, moe_status)
            rows.append(
                {
                    "county_fips": county_fips,
                    "month": pd.NaT,
                    "value": estimate,
                    "variable_name": metadata["variable_name"],
                    "database_version": "A. Benchmark",
                    "source_frequency": "5-year",
                    "source_period_start": period_start,
                    "source_period_end": period_end,
                    "reference_date": reference_date,
                    "release_date": release,
                    "source_release_dates": (
                        None
                        if pd.isna(release)
                        else (
                            "census_acs5_finance="
                            f"{pd.Timestamp(release).date().isoformat()}"
                        )
                    ),
                    "source_geography": "county",
                    "parameter_geography": "county",
                    "monthly_method": (
                        "not_applicable_source_60_month_interval"
                    ),
                    "is_modeled": False,
                    "is_nowcast": False,
                    "benchmark_vintage": f"{year}_ACS5",
                    "sample_n": None,
                    "coverage_rate": None,
                    "low_estimate": low,
                    "central_estimate": central,
                    "high_estimate": high,
                    "universe_definition": metadata[
                        "universe_definition"
                    ],
                    "notes": (
                        "Official ACS 5-year Detailed Table estimate and "
                        "published 90% MOE; retained at its 60-month source "
                        "interval and not allocated to months."
                    ),
                    "state_fips": CALIFORNIA_STATE_FIPS,
                    "county_name": source_row[0],
                    "source_variable": variable_id,
                    "estimate_variable": metadata["estimate_variable"],
                    "moe_variable": metadata["moe_variable"],
                    "moe": moe,
                    "margin_of_error": moe,
                    "moe_confidence_level": 0.90,
                    "unit": metadata["unit"],
                    "description": metadata["description"],
                    "source_interval_months": 60,
                    "retrieved_at": retrieved,
                    "source_filename": (
                        source_filename or payload.get("source_filename")
                    ),
                    "source_vintage": str(year),
                    "checksum_sha256": checksum,
                    "request_url": request_urls[table_id],
                    "estimate_raw": estimate_raw,
                    "moe_raw": moe_raw,
                    "estimate_status": estimate_status,
                    "moe_status": moe_status,
                    "quality_flag": quality_flag,
                }
            )

    frame = pd.DataFrame(rows)
    missing_metadata = [
        field
        for field in REQUIRED_BENCHMARK_METADATA_FIELDS
        if field not in frame.columns
    ]
    if missing_metadata:
        raise ACSFormatError(
            f"clean ACS rows lack benchmark metadata: {missing_metadata}"
        )

    validate_benchmark_metadata(frame)

    extra_columns = [
        column
        for column in frame.columns
        if column not in REQUIRED_BENCHMARK_METADATA_FIELDS
    ]
    frame = frame[
        [*REQUIRED_BENCHMARK_METADATA_FIELDS, *extra_columns]
    ].sort_values(["county_fips", "variable_name"], ignore_index=True)

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(temp, index=False)
        temp.replace(path)
    return frame


# Concise aliases for pipeline callers.
download_acs_finance_vintage = archive_acs_finance_vintage
clean_acs_finance = clean_acs_finance_vintage


__all__ = [
    "ACSFormatError",
    "ACS_SPECIAL_VALUES",
    "ACS_TABLE_VARIABLES",
    "ACS_VARIABLE_MAP",
    "CALIFORNIA_COUNTY_FIPS",
    "acs_five_year_period",
    "archive_acs_finance_vintage",
    "build_acs_request_url",
    "clean_acs_finance",
    "clean_acs_finance_vintage",
    "download_acs_finance_vintage",
    "expected_acs_header",
    "fetch_acs_finance_payload",
]
