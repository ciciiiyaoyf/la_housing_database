"""Official ACS 5-year inputs for source-faithful Version A benchmarks.

The acquisition functions request the estimate and 90 percent margin of error
for a small, explicit set of Detailed Table variables.  One immutable archive
represents one ACS 5-year vintage: either the existing API composite JSON or
an exact-byte table-based Summary File ZIP.  Cleaning keeps the published
60-month interval intact: values are not assigned to December, interpolated,
or distributed to months.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
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
ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION = (
    "acs_finance_benchmark_summary_file_zip_v1"
)
ACS_SUMMARY_FILE_ROOT = (
    "https://www2.census.gov/programs-surveys/acs/summary_file"
)
ACS_SUMMARY_FILE_FIRST_VINTAGE = 2022
ACS_SUMMARY_MANIFEST_MEMBER = "manifest.json"
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

ACS_SUMMARY_VARIABLE_MAP: dict[str, dict[str, str]] = {
    variable_id: {
        "table_id": metadata["table_id"],
        "estimate_variable": (
            f"{metadata['table_id']}_E{variable_id.rsplit('_', 1)[1]}"
        ),
        "moe_variable": (
            f"{metadata['table_id']}_M{variable_id.rsplit('_', 1)[1]}"
        ),
    }
    for variable_id, metadata in ACS_VARIABLE_MAP.items()
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


def _normalise_summary_file_vintage(vintage: int | str) -> int:
    year = _normalise_vintage(vintage)
    if year < ACS_SUMMARY_FILE_FIRST_VINTAGE:
        raise ValueError(
            "the verified ACS table-based Summary File fallback is available "
            f"only for vintages {ACS_SUMMARY_FILE_FIRST_VINTAGE} and later"
        )
    return year


def build_acs_summary_table_url(
    vintage: int | str,
    table_id: str,
) -> str:
    """Return the official 5-year table-based Summary File URL."""
    year = _normalise_summary_file_vintage(vintage)
    if table_id not in ACS_TABLE_VARIABLES:
        raise KeyError(f"unsupported ACS finance table: {table_id}")
    filename = f"acsdt5y{year}-{table_id.lower()}.dat"
    return (
        f"{ACS_SUMMARY_FILE_ROOT}/{year}/table-based-SF/data/5YRData/"
        f"{filename}"
    )


def build_acs_summary_geography_url(vintage: int | str) -> str:
    """Return the official 5-year table-based geography-label URL."""
    year = _normalise_summary_file_vintage(vintage)
    filename = f"Geos{year}5YR.txt"
    return (
        f"{ACS_SUMMARY_FILE_ROOT}/{year}/table-based-SF/documentation/"
        f"{filename}"
    )


def _summary_file_variables(variable_id: str) -> tuple[str, str]:
    metadata = ACS_SUMMARY_VARIABLE_MAP[variable_id]
    return metadata["estimate_variable"], metadata["moe_variable"]


def _expected_summary_table_filename(
    vintage: int | str,
    table_id: str,
) -> str:
    year = _normalise_summary_file_vintage(vintage)
    if table_id not in ACS_TABLE_VARIABLES:
        raise KeyError(f"unsupported ACS finance table: {table_id}")
    return f"acsdt5y{year}-{table_id.lower()}.dat"


def _expected_summary_geography_filename(vintage: int | str) -> str:
    year = _normalise_summary_file_vintage(vintage)
    return f"Geos{year}5YR.txt"


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


def _download_summary_member(
    package: zipfile.ZipFile,
    *,
    client: requests.Session,
    url: str,
    source_filename: str,
    retrieved_at: str,
    timeout: float,
) -> dict[str, Any]:
    response = None
    checksum = hashlib.sha256()
    byte_size = 0
    try:
        try:
            response = client.get(
                url,
                stream=True,
                timeout=timeout,
                headers={"User-Agent": ACS_USER_AGENT},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"ACS Summary File request failed for {source_filename}"
            ) from exc
        with package.open(source_filename, "w", force_zip64=True) as target:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    raise ACSFormatError(
                        f"{source_filename} download yielded non-byte content"
                    )
                checksum.update(chunk)
                byte_size += len(chunk)
                target.write(chunk)
    finally:
        close = (
            getattr(response, "close", None)
            if response is not None
            else None
        )
        if callable(close):
            close()

    if byte_size == 0:
        raise ACSFormatError(
            f"official ACS Summary File asset is empty: {source_filename}"
        )
    return {
        "source_url": url,
        "source_filename": source_filename,
        "archive_member": source_filename,
        "checksum_sha256": checksum.hexdigest(),
        "byte_size": byte_size,
        "retrieved_at": retrieved_at,
        "retrieval_method": "official_url_stream",
    }


def archive_acs_finance_summary_files(
    vintage: int | str,
    output_path: str | Path,
    *,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    release_date: date | datetime | str | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    """Archive exact official table-based Summary File bytes in one ZIP.

    The package contains the five configured Detailed Table files, the
    corresponding geography-label file, and a manifest that records the
    official URL, source filename, retrieval timestamp, byte size, and SHA-256
    checksum for every provider file.  Provider bytes are streamed directly
    into ZIP members and are never rewritten.  Existing archives are never
    replaced.
    """
    year = _normalise_summary_file_vintage(vintage)
    retrieved = _normalise_retrieved_at(retrieved_at)
    release = _normalise_optional_date(release_date)
    if release is None:
        raise ValueError(
            "release_date is required for an auditable ACS vintage snapshot"
        )
    period_start, period_end, reference_date = acs_five_year_period(year)
    if pd.Timestamp(release) < period_end:
        raise ValueError("release_date cannot precede the ACS source period end")

    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"ACS raw archive already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    retrieved_text = retrieved.isoformat()
    owns_client = session is None
    client = session or requests.Session()
    manifest: dict[str, Any] = {
        "schema_version": ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION,
        "provider": "U.S. Census Bureau",
        "dataset": (
            "American Community Survey 5-Year Detailed Tables, "
            "Table-Based Summary File"
        ),
        "dataset_path": (
            f"summary_file/{year}/table-based-SF/data/5YRData"
        ),
        "acquisition_method": "official_table_based_summary_file",
        "vintage": str(year),
        "retrieved_at": retrieved_text,
        "release_date": release,
        "source_period_start": period_start.date().isoformat(),
        "source_period_end": period_end.date().isoformat(),
        "reference_date": reference_date.date().isoformat(),
        "source_interval_months": 60,
        "geography_selection": {
            "summary_level": "050",
            "component": "00",
            "state_fips": CALIFORNIA_STATE_FIPS,
            "county_fips": list(CALIFORNIA_COUNTY_FIPS),
        },
        "source_assets": {
            "geography_labels": {},
            "tables": {},
        },
        "variable_map": ACS_VARIABLE_MAP,
        "summary_file_variable_map": ACS_SUMMARY_VARIABLE_MAP,
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{path.name}.",
            suffix=".part",
            dir=path.parent,
            delete=False,
        ) as temporary_handle:
            temporary_path = Path(temporary_handle.name)
            with zipfile.ZipFile(
                temporary_handle,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as package:
                geography_filename = _expected_summary_geography_filename(year)
                manifest["source_assets"]["geography_labels"] = (
                    _download_summary_member(
                        package,
                        client=client,
                        url=build_acs_summary_geography_url(year),
                        source_filename=geography_filename,
                        retrieved_at=retrieved_text,
                        timeout=timeout,
                    )
                )
                for table_id in ACS_TABLE_VARIABLES:
                    source_filename = _expected_summary_table_filename(
                        year, table_id
                    )
                    table_asset = _download_summary_member(
                        package,
                        client=client,
                        url=build_acs_summary_table_url(year, table_id),
                        source_filename=source_filename,
                        retrieved_at=retrieved_text,
                        timeout=timeout,
                    )
                    table_asset["table_id"] = table_id
                    manifest["source_assets"]["tables"][
                        table_id
                    ] = table_asset

                package.writestr(
                    ACS_SUMMARY_MANIFEST_MEMBER,
                    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                )
            temporary_handle.flush()
            os.fsync(temporary_handle.fileno())

        # Validate the complete temporary package before making its canonical
        # path visible.  os.link is an atomic, no-overwrite publication step
        # on the same filesystem, so interruption cannot leave a partial raw
        # archive at ``path`` and a concurrent writer cannot be overwritten.
        _load_summary_zip_payload(
            temporary_path,
            require_complete_counties=True,
        )
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise FileExistsError(
                f"ACS raw archive already exists: {path}"
            ) from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        if owns_client:
            client.close()

    return {
        "schema_version": ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION,
        "acquisition_method": "official_table_based_summary_file",
        "vintage": str(year),
        "retrieved_at": retrieved_text,
        "release_date": release,
        "source_filename": path.name,
        "archive_path": str(path),
        "checksum_sha256": _sha256(path),
        "request_url": {
            table_id: build_acs_summary_table_url(year, table_id)
            for table_id in ACS_TABLE_VARIABLES
        },
        "response_count": len(ACS_TABLE_VARIABLES),
        "source_assets": manifest["source_assets"],
    }


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


def _read_pipe_header_and_rows(
    package: zipfile.ZipFile,
    member: str,
):
    binary = package.open(member, "r")
    text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
    reader = csv.reader(text, delimiter="|")
    try:
        header = next(reader)
    except StopIteration as exc:
        text.close()
        raise ACSFormatError(f"{member} is empty") from exc
    except UnicodeDecodeError as exc:
        text.close()
        raise ACSFormatError(f"{member} is not valid UTF-8") from exc
    if not header or any(not column for column in header):
        text.close()
        raise ACSFormatError(f"{member} has a blank or missing header")
    if len(header) != len(set(header)):
        text.close()
        raise ACSFormatError(f"{member} contains duplicate header fields")
    return text, reader, header


def _summary_geography_rows(
    package: zipfile.ZipFile,
    member: str,
    *,
    require_complete_counties: bool,
) -> dict[str, tuple[str, str]]:
    text, reader, header = _read_pipe_header_and_rows(package, member)
    required = (
        "STUSAB",
        "SUMLEVEL",
        "COMPONENT",
        "STATE",
        "COUNTY",
        "GEO_ID",
        "NAME",
    )
    missing = [field for field in required if field not in header]
    if missing:
        text.close()
        raise ACSFormatError(
            f"{member} lacks required geography fields: {missing}"
        )
    positions = {field: header.index(field) for field in required}
    rows: dict[str, tuple[str, str]] = {}
    try:
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ACSFormatError(
                    f"{member} row {row_number} has width {len(row)}; "
                    f"expected {len(header)}"
                )
            if (
                row[positions["STUSAB"]] != "CA"
                or row[positions["SUMLEVEL"]] != "050"
                or row[positions["COMPONENT"]] != "00"
                or row[positions["STATE"]] != CALIFORNIA_STATE_FIPS
            ):
                continue
            county = row[positions["COUNTY"]]
            if not re.fullmatch(r"\d{3}", county):
                raise ACSFormatError(
                    f"{member} row {row_number} has invalid county code "
                    f"{county!r}"
                )
            county_fips = f"{CALIFORNIA_STATE_FIPS}{county}"
            if county_fips not in CALIFORNIA_COUNTY_FIPS:
                raise ACSFormatError(
                    f"{member} returned unknown California county "
                    f"{county_fips}"
                )
            geo_id = row[positions["GEO_ID"]]
            expected_geo_id = f"0500000US{county_fips}"
            if geo_id != expected_geo_id:
                raise ACSFormatError(
                    f"{member} row {row_number} has GEO_ID {geo_id!r}; "
                    f"expected {expected_geo_id!r}"
                )
            name = row[positions["NAME"]]
            if not name.strip():
                raise ACSFormatError(
                    f"{member} row {row_number} has a blank county name"
                )
            if geo_id in rows:
                raise ACSFormatError(
                    f"{member} returned duplicate county geography {geo_id}"
                )
            rows[geo_id] = (county_fips, name)
    except UnicodeDecodeError as exc:
        raise ACSFormatError(f"{member} is not valid UTF-8") from exc
    finally:
        text.close()

    actual = {county_fips for county_fips, _ in rows.values()}
    expected = set(CALIFORNIA_COUNTY_FIPS)
    if require_complete_counties and actual != expected:
        raise ACSFormatError(
            "ACS Summary File geography county coverage changed: "
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    if not rows:
        raise ACSFormatError(
            "ACS Summary File geography labels contain no California counties"
        )
    return rows


def _summary_table_response(
    package: zipfile.ZipFile,
    member: str,
    table_id: str,
    geography_rows: Mapping[str, tuple[str, str]],
    *,
    require_complete_counties: bool,
) -> list[list[str]]:
    text, reader, header = _read_pipe_header_and_rows(package, member)
    required = ["GEO_ID"]
    for variable_id in ACS_TABLE_VARIABLES[table_id]:
        required.extend(_summary_file_variables(variable_id))
    missing = [field for field in required if field not in header]
    if missing:
        text.close()
        raise ACSFormatError(
            f"{member} lacks configured estimate/MOE fields: {missing}"
        )
    positions = {field: header.index(field) for field in required}
    selected: dict[str, list[str]] = {}
    try:
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ACSFormatError(
                    f"{member} row {row_number} has width {len(row)}; "
                    f"expected {len(header)}"
                )
            geo_id = row[positions["GEO_ID"]]
            if geo_id not in geography_rows:
                continue
            if geo_id in selected:
                raise ACSFormatError(
                    f"{member} returned duplicate county geography {geo_id}"
                )
            selected[geo_id] = [row[positions[field]] for field in required[1:]]
    except UnicodeDecodeError as exc:
        raise ACSFormatError(f"{member} is not valid UTF-8") from exc
    finally:
        text.close()

    expected_geo_ids = set(geography_rows)
    actual_geo_ids = set(selected)
    if require_complete_counties and actual_geo_ids != expected_geo_ids:
        missing_fips = sorted(
            geography_rows[geo_id][0]
            for geo_id in expected_geo_ids - actual_geo_ids
        )
        extra_geo_ids = sorted(actual_geo_ids - expected_geo_ids)
        raise ACSFormatError(
            f"{table_id} Summary File county coverage changed: "
            f"missing={missing_fips}, extra_geo_ids={extra_geo_ids}"
        )
    if not selected:
        raise ACSFormatError(
            f"{table_id} Summary File contains no California county rows"
        )

    response: list[list[str]] = [list(expected_acs_header(table_id))]
    for geo_id in sorted(selected, key=lambda value: geography_rows[value][0]):
        county_fips, county_name = geography_rows[geo_id]
        response.append(
            [
                county_name,
                *selected[geo_id],
                CALIFORNIA_STATE_FIPS,
                county_fips[-3:],
            ]
        )
    return response


def _validate_summary_asset(
    package: zipfile.ZipFile,
    asset: Any,
    *,
    expected_url: str,
    expected_filename: str,
    expected_retrieved_at: str,
) -> dict[str, Any]:
    if not isinstance(asset, dict):
        raise ACSFormatError(
            f"ACS Summary File asset metadata missing for {expected_filename}"
        )
    expected_fields = {
        "source_url": expected_url,
        "source_filename": expected_filename,
        "archive_member": expected_filename,
        "retrieved_at": expected_retrieved_at,
        "retrieval_method": "official_url_stream",
    }
    for field, expected in expected_fields.items():
        if asset.get(field) != expected:
            raise ACSFormatError(
                f"{expected_filename} {field} must be {expected!r}; "
                f"got {asset.get(field)!r}"
            )

    checksum = asset.get("checksum_sha256")
    if not isinstance(checksum, str) or not re.fullmatch(
        r"[0-9a-f]{64}", checksum
    ):
        raise ACSFormatError(
            f"{expected_filename} checksum_sha256 must contain 64 lowercase "
            "hexadecimal characters"
        )
    byte_size = asset.get("byte_size")
    if (
        isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size <= 0
    ):
        raise ACSFormatError(
            f"{expected_filename} byte_size must be a positive integer"
        )

    try:
        info = package.getinfo(expected_filename)
    except KeyError as exc:
        raise ACSFormatError(
            f"ACS Summary File package lacks {expected_filename}"
        ) from exc
    if info.is_dir() or info.file_size != byte_size:
        raise ACSFormatError(
            f"{expected_filename} byte size differs from its manifest"
        )

    digest = hashlib.sha256()
    with package.open(expected_filename, "r") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != checksum:
        raise ACSFormatError(
            f"{expected_filename} checksum differs from its manifest"
        )
    return dict(asset)


def _load_summary_zip_payload(
    path: Path,
    *,
    require_complete_counties: bool,
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as package:
            names = package.namelist()
            if len(names) != len(set(names)):
                raise ACSFormatError(
                    "ACS Summary File package contains duplicate member names"
                )
            if ACS_SUMMARY_MANIFEST_MEMBER not in names:
                raise ACSFormatError(
                    "ACS Summary File package lacks manifest.json"
                )
            manifest_info = package.getinfo(ACS_SUMMARY_MANIFEST_MEMBER)
            if manifest_info.file_size > 10 * 1024 * 1024:
                raise ACSFormatError(
                    "ACS Summary File manifest.json is unexpectedly large"
                )
            try:
                manifest = json.loads(
                    package.read(ACS_SUMMARY_MANIFEST_MEMBER).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ACSFormatError(
                    "ACS Summary File manifest.json is invalid"
                ) from exc
            if not isinstance(manifest, dict):
                raise ACSFormatError(
                    "ACS Summary File manifest root must be a JSON object"
                )
            if (
                manifest.get("schema_version")
                != ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION
            ):
                raise ACSFormatError(
                    "unsupported ACS Summary File archive schema version: "
                    f"{manifest.get('schema_version')!r}"
                )
            if (
                manifest.get("acquisition_method")
                != "official_table_based_summary_file"
            ):
                raise ACSFormatError(
                    "ACS Summary File acquisition_method is invalid"
                )

            year = _normalise_summary_file_vintage(manifest.get("vintage"))
            _validate_variable_map(manifest.get("variable_map"))
            if (
                manifest.get("summary_file_variable_map")
                != ACS_SUMMARY_VARIABLE_MAP
            ):
                raise ACSFormatError(
                    "ACS Summary File variable map differs from the "
                    "configured source-field contract"
                )
            geography_selection = manifest.get("geography_selection")
            if geography_selection != {
                "summary_level": "050",
                "component": "00",
                "state_fips": CALIFORNIA_STATE_FIPS,
                "county_fips": list(CALIFORNIA_COUNTY_FIPS),
            }:
                raise ACSFormatError(
                    "ACS Summary File geography selection changed"
                )
            retrieved_at = manifest.get("retrieved_at")
            if not isinstance(retrieved_at, str) or pd.isna(
                pd.to_datetime(retrieved_at, utc=True, errors="coerce")
            ):
                raise ACSFormatError(
                    "ACS Summary File retrieved_at must be a valid timestamp"
                )

            source_assets = manifest.get("source_assets")
            if not isinstance(source_assets, dict) or set(source_assets) != {
                "geography_labels",
                "tables",
            }:
                raise ACSFormatError(
                    "ACS Summary File source_assets structure changed"
                )
            table_assets = source_assets.get("tables")
            if not isinstance(table_assets, dict) or set(table_assets) != set(
                ACS_TABLE_VARIABLES
            ):
                raise ACSFormatError(
                    "ACS Summary File table asset set changed"
                )

            geography_filename = _expected_summary_geography_filename(year)
            expected_members = {
                ACS_SUMMARY_MANIFEST_MEMBER,
                geography_filename,
                *(
                    _expected_summary_table_filename(year, table_id)
                    for table_id in ACS_TABLE_VARIABLES
                ),
            }
            if set(names) != expected_members:
                raise ACSFormatError(
                    "ACS Summary File package member set changed: "
                    f"missing={sorted(expected_members - set(names))}, "
                    f"extra={sorted(set(names) - expected_members)}"
                )

            geography_asset = _validate_summary_asset(
                package,
                source_assets.get("geography_labels"),
                expected_url=build_acs_summary_geography_url(year),
                expected_filename=geography_filename,
                expected_retrieved_at=retrieved_at,
            )
            validated_table_assets: dict[str, dict[str, Any]] = {}
            for table_id in ACS_TABLE_VARIABLES:
                filename = _expected_summary_table_filename(year, table_id)
                table_asset = table_assets[table_id]
                if (
                    not isinstance(table_asset, dict)
                    or table_asset.get("table_id") != table_id
                ):
                    raise ACSFormatError(
                        f"{table_id} Summary File asset table_id changed"
                    )
                validated_table_assets[table_id] = _validate_summary_asset(
                    package,
                    table_asset,
                    expected_url=build_acs_summary_table_url(year, table_id),
                    expected_filename=filename,
                    expected_retrieved_at=retrieved_at,
                )

            geography_rows = _summary_geography_rows(
                package,
                geography_filename,
                require_complete_counties=require_complete_counties,
            )
            responses: dict[str, list[list[str]]] = {}
            for table_id in ACS_TABLE_VARIABLES:
                responses[table_id] = _summary_table_response(
                    package,
                    _expected_summary_table_filename(year, table_id),
                    table_id,
                    geography_rows,
                    require_complete_counties=require_complete_counties,
                )
    except zipfile.BadZipFile as exc:
        raise ACSFormatError(
            f"invalid ACS Summary File ZIP archive: {path}"
        ) from exc

    payload = dict(manifest)
    payload["responses"] = responses
    payload["request_url"] = {
        table_id: validated_table_assets[table_id]["source_url"]
        for table_id in ACS_TABLE_VARIABLES
    }
    payload["_table_provenance"] = validated_table_assets
    payload["_geography_provenance"] = geography_asset
    return payload


def _load_payload(
    source: str | Path | Mapping[str, Any],
    *,
    require_complete_counties: bool = True,
) -> tuple[dict[str, Any], str | None, str | None]:
    if isinstance(source, Mapping):
        if (
            source.get("schema_version")
            == ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION
        ):
            raise ACSFormatError(
                "ACS Summary File archives must be supplied as ZIP paths so "
                "embedded provider bytes can be checksum-validated"
            )
        return dict(source), source.get("source_filename"), source.get(
            "checksum_sha256"
        )

    path = Path(source)
    if path.suffix.lower() == ".zip" or zipfile.is_zipfile(path):
        return (
            _load_summary_zip_payload(
                path,
                require_complete_counties=require_complete_counties,
            ),
            path.name,
            _sha256(path),
        )
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
    schema_version = payload.get("schema_version")
    if schema_version not in {
        ACS_RAW_SCHEMA_VERSION,
        ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION,
    }:
        raise ACSFormatError(
            "unsupported ACS finance archive schema version: "
            f"{schema_version!r}"
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
    if schema_version == ACS_RAW_SCHEMA_VERSION:
        if payload.get("geography_query") != {
            "for": "county:*",
            "in": f"state:{CALIFORNIA_STATE_FIPS}",
        }:
            raise ACSFormatError(
                "ACS archive geography query must request all California "
                "counties"
            )
    else:
        if payload.get("acquisition_method") != (
            "official_table_based_summary_file"
        ):
            raise ACSFormatError(
                "ACS Summary File archive acquisition_method changed"
            )
        if payload.get("geography_selection") != {
            "summary_level": "050",
            "component": "00",
            "state_fips": CALIFORNIA_STATE_FIPS,
            "county_fips": list(CALIFORNIA_COUNTY_FIPS),
        }:
            raise ACSFormatError(
                "ACS Summary File archive geography selection changed"
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
        expected_url = (
            build_acs_request_url(year, table_id)
            if schema_version == ACS_RAW_SCHEMA_VERSION
            else build_acs_summary_table_url(year, table_id)
        )
        if request_urls[table_id] != expected_url:
            raise ACSFormatError(
                f"{table_id} request URL differs from its official "
                "acquisition URL"
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
    payload, source_filename, checksum = _load_payload(
        source,
        require_complete_counties=require_complete_counties,
    )
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
    is_summary_file = (
        payload.get("schema_version")
        == ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION
    )
    table_provenance = (
        payload.get("_table_provenance", {}) if is_summary_file else {}
    )
    geography_provenance = (
        payload.get("_geography_provenance", {}) if is_summary_file else {}
    )
    rows: list[dict[str, Any]] = []
    county_fips_values = sorted(
        set.intersection(*(set(table) for table in table_rows.values()))
    )

    for variable_id, metadata in ACS_VARIABLE_MAP.items():
        table_id = metadata["table_id"]
        header = expected_acs_header(table_id)
        estimate_position = header.index(metadata["estimate_variable"])
        moe_position = header.index(metadata["moe_variable"])
        if is_summary_file:
            table_asset = table_provenance[table_id]
            row_retrieved = pd.to_datetime(
                table_asset["retrieved_at"],
                utc=True,
                errors="raise",
            )
            row_source_filename = table_asset["source_filename"]
            row_checksum = table_asset["checksum_sha256"]
            row_estimate_variable, row_moe_variable = (
                _summary_file_variables(variable_id)
            )
            row_notes = (
                "Official ACS 5-year Detailed Table estimate and published "
                "90% MOE from the Census Table-Based Summary File; retained "
                "at its 60-month source interval and not allocated to months."
            )
        else:
            row_retrieved = retrieved
            row_source_filename = (
                source_filename or payload.get("source_filename")
            )
            row_checksum = checksum
            row_estimate_variable = metadata["estimate_variable"]
            row_moe_variable = metadata["moe_variable"]
            row_notes = (
                "Official ACS 5-year Detailed Table estimate and published "
                "90% MOE; retained at its 60-month source interval and not "
                "allocated to months."
            )
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
            row = {
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
                "universe_definition": metadata["universe_definition"],
                "notes": row_notes,
                "state_fips": CALIFORNIA_STATE_FIPS,
                "county_name": source_row[0],
                "source_variable": variable_id,
                "estimate_variable": row_estimate_variable,
                "moe_variable": row_moe_variable,
                "moe": moe,
                "margin_of_error": moe,
                "moe_confidence_level": 0.90,
                "unit": metadata["unit"],
                "description": metadata["description"],
                "source_interval_months": 60,
                "retrieved_at": row_retrieved,
                "source_filename": row_source_filename,
                "source_vintage": str(year),
                "checksum_sha256": row_checksum,
                "request_url": request_urls[table_id],
                "estimate_raw": estimate_raw,
                "moe_raw": moe_raw,
                "estimate_status": estimate_status,
                "moe_status": moe_status,
                "quality_flag": quality_flag,
            }
            if is_summary_file:
                row.update(
                    {
                        "acquisition_method": (
                            "official_table_based_summary_file"
                        ),
                        "archive_filename": source_filename,
                        "archive_checksum_sha256": checksum,
                        "geography_source_filename": (
                            geography_provenance["source_filename"]
                        ),
                        "geography_checksum_sha256": (
                            geography_provenance["checksum_sha256"]
                        ),
                        "geography_source_url": (
                            geography_provenance["source_url"]
                        ),
                        "canonical_api_estimate_variable": (
                            metadata["estimate_variable"]
                        ),
                        "canonical_api_moe_variable": (
                            metadata["moe_variable"]
                        ),
                    }
                )
            rows.append(row)

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


def acs_finance_archive_metadata(
    source: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Read and validate acquisition metadata from a v2 JSON or v3 ZIP.

    ZIP member checksums and complete California county coverage are verified
    before metadata is returned.  This gives pipeline callers one schema-aware
    entry point without treating Summary File assets as API responses.
    """
    payload, source_filename, checksum = _load_payload(source)
    year, _ = _validate_archive(
        payload,
        require_complete_counties=True,
    )
    _, period_end, _ = acs_five_year_period(year)
    release = pd.to_datetime(payload.get("release_date"), errors="coerce")
    if pd.isna(release):
        raise ACSFormatError("ACS archive release_date must be populated")
    if pd.Timestamp(release) < period_end:
        raise ACSFormatError("ACS release_date cannot precede source_period_end")

    schema_version = payload["schema_version"]
    result: dict[str, Any] = {
        "schema_version": schema_version,
        "acquisition_method": (
            "census_api"
            if schema_version == ACS_RAW_SCHEMA_VERSION
            else "official_table_based_summary_file"
        ),
        "vintage": str(year),
        "retrieved_at": payload["retrieved_at"],
        "release_date": pd.Timestamp(release).date().isoformat(),
        "source_period_start": payload["source_period_start"],
        "source_period_end": payload["source_period_end"],
        "reference_date": payload["reference_date"],
        "source_interval_months": payload["source_interval_months"],
        "source_filename": (
            source_filename or payload.get("source_filename")
        ),
        "checksum_sha256": checksum,
        "request_url": dict(payload["request_url"]),
    }
    if schema_version == ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION:
        result["source_assets"] = payload["source_assets"]
    return result


# Concise aliases for pipeline callers.
download_acs_finance_vintage = archive_acs_finance_vintage
download_acs_finance_summary_files = archive_acs_finance_summary_files
clean_acs_finance = clean_acs_finance_vintage


__all__ = [
    "ACSFormatError",
    "ACS_RAW_SCHEMA_VERSION",
    "ACS_SPECIAL_VALUES",
    "ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION",
    "ACS_SUMMARY_FILE_FIRST_VINTAGE",
    "ACS_TABLE_VARIABLES",
    "ACS_VARIABLE_MAP",
    "CALIFORNIA_COUNTY_FIPS",
    "acs_finance_archive_metadata",
    "acs_five_year_period",
    "archive_acs_finance_summary_files",
    "archive_acs_finance_vintage",
    "build_acs_request_url",
    "build_acs_summary_geography_url",
    "build_acs_summary_table_url",
    "clean_acs_finance",
    "clean_acs_finance_vintage",
    "download_acs_finance_summary_files",
    "download_acs_finance_vintage",
    "expected_acs_header",
    "fetch_acs_finance_payload",
]
