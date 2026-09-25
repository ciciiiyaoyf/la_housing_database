"""Source-specific acquisition with immutable, checksummed raw archives."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote, urlparse

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file
from src.cleaning.acs import ACS_API_REQUEST_COLUMN_OVERRIDES, ACS_TABLE_MAP
from src.cleaning.ahs_table_creator import SOURCE_ID as AHS_TABLE_CREATOR_SOURCE_ID
from src.cleaning.sipp import SOURCE_IDS as SIPP_SOURCE_IDS
from src.cleaning.sipp import published_release_date as sipp_published_release_date
from src.geography.comparable_counties import (
    COMPARABLE_LAUS_SERIES_IDS,
    PRIMARY_LAUS_SERIES_IDS,
)
from src.geography.zcta_reference import load_zcta_reference
from src.ingestion.bps_archive import download_bps_archive
from src.ingestion.bps_annual import (
    SOURCE_ID as BPS_ANNUAL_SOURCE_ID,
    download_bps_annual_county,
)
from src.ingestion.irs_migration import download_irs_soi_county_migration
from src.ingestion.hud_usps import download_hud_usps_archive
from src.ingestion.macro_claims import download_claims

RAW_SUBDIR = {
    "Zillow Research": "zillow",
    "Redfin Data Center": "redfin",
    "U.S. Bureau of Labor Statistics": "bls",
    "Freddie Mac": "freddie_mac",
    "U.S. Census Bureau": "census_bps",
    "Federal Housing Finance Agency": "fhfa",
    "U.S. Bureau of Economic Analysis": "bea",
    "Internal Revenue Service": "irs_soi",
}

BLS_SERIES = PRIMARY_LAUS_SERIES_IDS
BLS_PUBLIC_SERIES_LIMIT = 25
USER_AGENT = "UCLA housing research data pipeline yifan@g.ucla.edu"
LOCAL_FILE_SOURCE_IDS = {
    "redfin_zip_monthly",
    AHS_TABLE_CREATOR_SOURCE_ID,
}
HUD_PIT_SOURCE_ID = "hud_pit_coc_2007_2025"
HUD_PIT_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/vnd.ms-excel.sheet.binary.macroEnabled.12,"
        "application/octet-stream,*/*"
    ),
    "Referer": (
        "https://www.huduser.gov/portal/datasets/ahar/"
        "2025-ahar-part-1-pit-estimates-of-homelessness-in-the-us.html"
    ),
}

ACS_HISTORICAL_TABLES = {
    "census_acs_5year_b05002",
    "census_acs_5year_b07001",
    "census_acs_5year_b11001",
    "census_acs_5year_b19013",
    "census_acs_5year_b25001",
    "census_acs_5year_b25002",
    "census_acs_5year_b25003",
    "census_acs_5year_b25004",
    "census_acs_5year_b25032",
    "census_acs_5year_b25064",
    "census_acs_5year_b25077",
    "census_acs_5year_b25081",
    "census_acs_5year_b25106",
    "census_acs_5year_b25127",
}

ACS_API_FALLBACK_LATEST_5YEAR = 2024
ACS_API_MIN_5YEAR = 2010
ACS_API_MIN_ZCTA = 2011
ACS_API_MIN_UCGID_ZCTA = 2020
ACS_API_GROUP_ENDPOINT = (
    "https://api.census.gov/data/{year}/acs/acs5/groups/{table_id}.json"
)


def _raw_subdir(source: SourceConfig) -> str:
    return source.raw_subdir or RAW_SUBDIR[source.provider]


def _today_manifest_record(
    config: ProjectConfig,
    source_id: str,
    request_contract: dict[str, str] | None = None,
) -> tuple[Path, dict] | None:
    """Return today's latest archive only when its request contract matches."""
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return None
    frame = pd.read_csv(manifest_path, dtype=str)
    rows = frame.loc[frame["source_id"] == source_id].copy()
    if rows.empty:
        return None
    retrieved = pd.to_datetime(
        rows["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    rows = rows.loc[retrieved.dt.date == datetime.now(timezone.utc).date()]
    if rows.empty:
        return None
    if request_contract:
        missing_columns = set(request_contract).difference(rows.columns)
        if missing_columns:
            return None
        for field, expected in request_contract.items():
            rows = rows.loc[rows[field].fillna("").astype(str).eq(str(expected))]
            if rows.empty:
                return None
    rows = rows.assign(
        _retrieved=pd.to_datetime(
            rows["retrieved_at"], utc=True, errors="coerce", format="mixed"
        )
    ).sort_values("_retrieved")
    record = rows.iloc[-1].drop(labels="_retrieved").to_dict()
    path = config.root / str(record["relative_path"])
    return (path, record) if path.exists() else None


def _checksum_manifest_record(
    config: ProjectConfig, source_id: str, checksum: str
) -> tuple[Path, dict] | None:
    manifest_path = config.root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return None
    frame = pd.read_csv(manifest_path, dtype=str)
    rows = frame.loc[
        frame["source_id"].eq(source_id)
        & frame["checksum_sha256"].eq(checksum)
    ]
    if rows.empty:
        return None
    record = rows.iloc[-1].to_dict()
    path = config.root / str(record["relative_path"])
    return (path, record) if path.exists() else None


def _mark_manual_resolved(
    config: ProjectConfig, source_id: str, resolved_at: str
) -> None:
    path = config.root / "metadata" / "manual_downloads.csv"
    if not path.exists():
        return
    frame = pd.read_csv(path, dtype=str)
    if "status" not in frame:
        frame["status"] = "open"
    if "resolved_at" not in frame:
        frame["resolved_at"] = None
    matches = frame["source_id"].eq(source_id)
    frame.loc[matches, "status"] = "resolved"
    frame.loc[matches, "resolved_at"] = resolved_at
    frame.to_csv(path, index=False)


def _write_response(response: requests.Response, temp_path: Path, chunk_bytes: int) -> None:
    response.raise_for_status()
    with temp_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=chunk_bytes):
            if chunk:
                handle.write(chunk)


def _latest_bps_url(source: SourceConfig) -> str:
    """Resolve the newest published current-month ASCII file, checking four lags."""
    match = re.match(
        r"^(https://www2\.census\.gov/econ/bps/(?:County|State)/)(?:co|st)\d{4}c\.txt$",
        source.download_url,
    )
    if not match:
        return source.download_url
    prefix = "co" if source.source_id.endswith("county") else "st"
    anchor = pd.Timestamp.now().to_period("M")
    for lag in range(4):
        period = anchor - lag
        candidate = f"{match.group(1)}{prefix}{period.strftime('%y%m')}c.txt"
        try:
            response = requests.head(
                candidate,
                timeout=30,
                allow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            if response.ok:
                return candidate
        except requests.RequestException:
            continue
    return source.download_url


def _download_bls(
    source: SourceConfig,
    temp_path: Path,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, str | int]:
    start_year = pd.Timestamp(start_date or "1990-01-01").year
    end_year = pd.Timestamp(end_date or date.today()).year
    configured_series_ids = tuple(source.series_ids or BLS_SERIES)
    if (
        source.source_id == "bls_laus_la_county"
        and set(configured_series_ids) == set(PRIMARY_LAUS_SERIES_IDS)
    ):
        # Preserve the existing configured source ID while expanding its raw
        # request to the approved eight-county comparison contract.
        series_ids = COMPARABLE_LAUS_SERIES_IDS
    else:
        series_ids = configured_series_ids
    if not series_ids:
        raise ValueError("No BLS series IDs are configured")
    payloads: list[dict] = []
    requests_made: list[dict] = []
    observation_dates: list[pd.Timestamp] = []
    observation_count = 0
    session = requests.Session()
    for chunk_start in range(start_year, end_year + 1, 10):
        chunk_end = min(chunk_start + 9, end_year)
        # All-employee hourly earnings begin in 2007. Do not demand invented
        # earnings observations in 1990s requests, or truncate sector history.
        eligible_series_ids = tuple(
            series_id for series_id in series_ids
            if not (
                source.source_id == "bls_ces_ca_la_industry_earnings"
                and series_id.endswith("0500000003")
                and chunk_end < 2007
            )
        )
        for series_offset in range(0, len(eligible_series_ids), BLS_PUBLIC_SERIES_LIMIT):
            series_batch = eligible_series_ids[
                series_offset : series_offset + BLS_PUBLIC_SERIES_LIMIT
            ]
            request_payload = {
                "seriesid": list(series_batch),
                "startyear": str(chunk_start),
                "endyear": str(chunk_end),
            }
            response = session.post(
                source.download_url,
                json=request_payload,
                timeout=120,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"BLS returned invalid JSON for {chunk_start}-{chunk_end}"
                ) from exc
            if not isinstance(body, dict):
                raise RuntimeError(
                    f"BLS returned a non-object JSON payload for "
                    f"{chunk_start}-{chunk_end}"
                )
            if body.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError(
                    f"BLS API error for {chunk_start}-{chunk_end}: {body}"
                )
            results = body.get("Results")
            response_series = (
                results.get("series") if isinstance(results, dict) else None
            )
            if not isinstance(response_series, list):
                raise RuntimeError(
                    f"BLS response has no Results.series list for "
                    f"{chunk_start}-{chunk_end}"
                )
            by_id = {
                item.get("seriesID"): item
                for item in response_series
                if isinstance(item, dict)
            }
            missing_series = sorted(set(series_batch) - set(by_id))
            if missing_series:
                raise RuntimeError(
                    f"BLS response missing configured series for "
                    f"{chunk_start}-{chunk_end}: {missing_series}"
                )
            for series_id in series_batch:
                data = by_id[series_id].get("data")
                if not isinstance(data, list):
                    raise RuntimeError(
                        f"BLS series {series_id} has no data list for "
                        f"{chunk_start}-{chunk_end}"
                    )
                monthly_items = [
                    item
                    for item in data
                    if isinstance(item, dict)
                    and str(item.get("period", "")).startswith("M")
                    and str(item.get("period")) != "M13"
                ]
                if not monthly_items:
                    raise RuntimeError(
                        f"BLS series {series_id} returned no monthly "
                        f"observations for {chunk_start}-{chunk_end}"
                    )
                numeric_observations = 0
                for item in monthly_items:
                    try:
                        year = int(item["year"])
                        month = int(str(item["period"])[1:])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise RuntimeError(
                            f"BLS series {series_id} contains an invalid "
                            f"monthly observation: {item}"
                        ) from exc
                    try:
                        float(item["value"])
                    except (KeyError, TypeError, ValueError):
                        # BLS publishes unavailable months as nonnumeric values
                        # (for example "-" with explanatory footnote X).
                        continue
                    observation_dates.append(
                        pd.Timestamp(year=year, month=month, day=1)
                        + pd.offsets.MonthEnd(0)
                    )
                    numeric_observations += 1
                if numeric_observations == 0:
                    raise RuntimeError(
                        f"BLS series {series_id} returned no numeric monthly "
                        f"observations for {chunk_start}-{chunk_end}"
                    )
                observation_count += numeric_observations
            requests_made.append(request_payload)
            payloads.append(body)
    if not observation_dates:
        raise RuntimeError("BLS returned no monthly observations")
    temp_path.write_text(
        json.dumps(
            {
                "source_url": source.download_url,
                "series_ids": series_ids,
                "requests": requests_made,
                "responses": payloads,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "request_series_ids": "|".join(series_ids),
        "request_start_year": str(start_year),
        "request_end_year": str(end_year),
        "response_latest_observation": max(observation_dates)
        .date()
        .isoformat(),
        "response_observation_count": str(observation_count),
    }


def _acs_table_id(source_id: str) -> str:
    return source_id.rsplit("_", 1)[-1].lower()


def _acs_api_variable(summary_file_variable: str) -> str:
    """Convert table-based ACS names like B25106_E004 to API B25106_004E."""
    match = re.fullmatch(r"([A-Z0-9]+)_([EM])(\d{3})", summary_file_variable)
    if not match:
        raise ValueError(f"Unsupported ACS variable name: {summary_file_variable}")
    table, estimate_or_moe, sequence = match.groups()
    return f"{table}_{sequence}{estimate_or_moe}"


def _canonical_json_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@lru_cache(maxsize=None)
def _probe_latest_acs_5year_vintage(table_id: str, max_year: int) -> int:
    """Return the newest published ACS 5-year year at or below ``max_year``.

    A detailed-table group endpoint is probed instead of assuming that the
    current calendar year, or a configuration vintage, has been published.
    The probe deliberately does not send ``CENSUS_API_KEY``: group metadata is
    public, and omitting credentials makes it impossible for this discovery
    request or its exception path to expose a key. Results are cached for the
    life of the Python process by table and search ceiling.
    """
    normalized_table = table_id.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", normalized_table):
        raise ValueError(f"Invalid ACS table id for vintage discovery: {table_id}")
    if max_year < ACS_API_MIN_5YEAR:
        raise ValueError(
            f"ACS 5-year discovery ceiling {max_year} precedes "
            f"the supported minimum {ACS_API_MIN_5YEAR}"
        )

    for year in range(max_year, ACS_API_MIN_5YEAR - 1, -1):
        endpoint = ACS_API_GROUP_ENDPOINT.format(
            year=year,
            table_id=normalized_table,
        )
        try:
            response = requests.get(
                endpoint,
                timeout=30,
                headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
            )
        except requests.RequestException:
            raise RuntimeError(
                "Census ACS group-endpoint connection failed during latest-"
                f"vintage discovery for {normalized_table}"
            ) from None

        if response.status_code in {400, 404}:
            continue
        if response.status_code != 200:
            raise RuntimeError(
                "Census ACS group endpoint returned HTTP "
                f"{response.status_code} during latest-vintage discovery for "
                f"{normalized_table}"
            )
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(
                "Census ACS group endpoint returned invalid JSON during latest-"
                f"vintage discovery for {normalized_table}"
            ) from None
        if not isinstance(payload, dict) or not isinstance(
            payload.get("variables"), dict
        ):
            raise RuntimeError(
                "Census ACS group endpoint returned an unexpected schema during "
                f"latest-vintage discovery for {normalized_table}"
            )
        if payload["variables"]:
            return year

    raise RuntimeError(
        "No published ACS 5-year group endpoint was found at or below "
        f"{max_year} for {normalized_table}"
    )


def _resolve_acs_5year_end_year(
    source: SourceConfig,
    end_date: str | None,
) -> tuple[int, str]:
    """Resolve the effective ACS end year and record how it was chosen."""
    logger = logging.getLogger("la_housing.download")
    table_id = _acs_table_id(source.source_id).upper()
    explicit_cap = pd.Timestamp(end_date).year if end_date else None

    # All configured detailed tables are already validated through the prior
    # fallback vintage. An explicit earlier cap therefore needs no network
    # discovery and remains exactly the user's requested upper bound.
    if (
        explicit_cap is not None
        and explicit_cap <= ACS_API_FALLBACK_LATEST_5YEAR
    ):
        return explicit_cap, "explicit_user_end_date"

    search_ceiling = min(explicit_cap or date.today().year, date.today().year)
    try:
        latest_year = _probe_latest_acs_5year_vintage(table_id, search_ceiling)
    except (RuntimeError, ValueError) as exc:
        configured_fallback = pd.Timestamp(
            source.selected_end_date
            or f"{ACS_API_FALLBACK_LATEST_5YEAR}-12-31"
        ).year
        fallback_year = min(
            configured_fallback,
            explicit_cap if explicit_cap is not None else configured_fallback,
        )
        # Do not render the underlying exception: Requests exceptions can carry
        # prepared URLs, and a clear neutral warning is enough to explain the
        # safe fallback without risking credential disclosure.
        logger.warning(
            "Latest ACS 5-year vintage discovery failed for table %s via the "
            "official Census group endpoint (%s); using safe fallback year %s",
            table_id,
            type(exc).__name__,
            fallback_year,
        )
        return fallback_year, "configured_fallback_after_discovery_failure"

    effective_year = min(
        latest_year,
        explicit_cap if explicit_cap is not None else latest_year,
    )
    logger.info(
        "Resolved latest ACS 5-year vintage for table %s as %s via the "
        "official Census group endpoint",
        table_id,
        effective_year,
    )
    return effective_year, "official_census_group_endpoint"


def _acs_request_contract(
    config: ProjectConfig,
    source: SourceConfig,
    start_date: str | None,
    end_date: str | None,
    *,
    latest_available_year: int | None = None,
    end_year_resolution: str | None = None,
) -> dict[str, str]:
    """Build the exact, stable contract used for ACS same-day reuse."""
    table_id = _acs_table_id(source.source_id)
    variable_map = ACS_TABLE_MAP.get(table_id)
    request_columns = ACS_API_REQUEST_COLUMN_OVERRIDES.get(table_id)
    if request_columns is None and variable_map is not None:
        request_columns = tuple(variable_map)
    if request_columns is None:
        raise ValueError(f"No ACS variable map is configured for {source.source_id}")
    start_year = max(
        ACS_API_MIN_5YEAR,
        pd.Timestamp(start_date or source.selected_start_date or "2010-01-01").year,
    )
    if latest_available_year is None:
        latest_available_year = ACS_API_FALLBACK_LATEST_5YEAR
        configured_end_year = pd.Timestamp(
            end_date
            or source.selected_end_date
            or f"{ACS_API_FALLBACK_LATEST_5YEAR}-12-31"
        ).year
        end_year_resolution = end_year_resolution or "configured_static_range"
    else:
        configured_end_year = (
            pd.Timestamp(end_date).year if end_date else latest_available_year
        )
        end_year_resolution = (
            end_year_resolution or "provided_latest_available_year"
        )
    end_year = min(latest_available_year, configured_end_year)
    if start_year > end_year:
        raise ValueError(
            f"Invalid ACS 5-year request range for {source.source_id}: "
            f"{start_year}>{end_year}"
        )
    variables = [_acs_api_variable(column) for column in request_columns]
    zctas, zcta_reference = load_zcta_reference(config.root)
    geography_parts = ["county:037 in state:06"]
    if start_year <= 2019 and end_year >= ACS_API_MIN_ZCTA:
        geography_parts.append(
            f"{max(start_year, ACS_API_MIN_ZCTA)}-{min(end_year, 2019)} "
            "ZCTA:* in state:06 (cleaned to versioned ZCTA list)"
        )
    if end_year >= ACS_API_MIN_UCGID_ZCTA:
        geography_parts.append(
            f"{max(start_year, ACS_API_MIN_UCGID_ZCTA)}-{end_year} nationwide "
            "ZCTA:* (cleaned to versioned ZCTA list)"
        )
    geographies = "|".join(geography_parts)
    return {
        "request_source_id": source.source_id,
        "request_api_base": "https://api.census.gov/data/{year}/acs/acs5",
        "request_latest_vintage_probe": ACS_API_GROUP_ENDPOINT.format(
            year="{year}",
            table_id=table_id.upper(),
        ),
        "request_table_id": table_id.upper(),
        "request_variables": ",".join(["NAME", *variables]),
        "request_selected_variables": "|".join(variables),
        "request_geographies": geographies,
        "request_start_year": str(start_year),
        "request_end_year": str(end_year),
        "request_latest_available_year": str(latest_available_year),
        "request_end_year_resolution": str(end_year_resolution),
        "request_zcta_count": str(len(zctas)),
        "request_zcta_reference_id": str(zcta_reference["reference_id"]),
        "request_zcta_reference_version": str(zcta_reference["version"]),
        "request_zcta_reference_sha256": _canonical_json_sha256(zcta_reference),
        "request_zcta_min_year": str(ACS_API_MIN_ZCTA),
        "request_zcta_unavailable_years": (
            "2010" if start_year <= 2010 <= end_year else ""
        ),
    }


def _redact_census_api_key(value: object, api_key: str | None) -> str:
    """Remove plain and URL-encoded forms of a Census API key from text."""
    text = str(value)
    if not api_key:
        return text
    variants = {
        api_key,
        quote(api_key, safe=""),
        quote_plus(api_key, safe=""),
    }
    for secret in sorted(variants, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "CENSUS_API_KEY")
    return text


def _download_census_acs_5year_api(
    config: ProjectConfig,
    source: SourceConfig,
    temp_path: Path,
    start_date: str | None,
    end_date: str | None,
    *,
    request_contract: dict[str, str] | None = None,
) -> dict[str, str]:
    """Download selected ACS 5-year detailed table cells through the Census API."""
    contract = request_contract or _acs_request_contract(
        config, source, start_date, end_date
    )
    table_id = contract["request_table_id"].lower()
    api_key = (os.environ.get("CENSUS_API_KEY") or "").strip() or None
    start_year = int(contract["request_start_year"])
    end_year = int(contract["request_end_year"])
    variables = contract["request_selected_variables"].split("|")
    get_clause = contract["request_variables"]
    requests_made: list[dict] = []
    observation_count = 0
    session = requests.Session()
    for year in range(start_year, end_year + 1):
        base_url = f"https://api.census.gov/data/{year}/acs/acs5"
        geography_requests = [
            (
                "county",
                {
                    "get": get_clause,
                    "for": "county:037",
                    "in": "state:06",
                },
            )
        ]
        # The ACS API's supported geography predicates vary by vintage. The
        # 2010 API has no ZCTA geography. From 2011 through 2019 it publishes
        # state-scoped ZCTA collections; cleaning filters that official
        # California response against the versioned project ZCTA list. From
        # 2020 onward ZCTAs are national API geographies, so the official
        # nationwide response is filtered during cleaning.
        if ACS_API_MIN_ZCTA <= year < ACS_API_MIN_UCGID_ZCTA:
            geography_requests.append(
                (
                    "zcta",
                    {
                        "get": get_clause,
                        "for": "zip code tabulation area:*",
                        "in": "state:06",
                    },
                )
            )
        elif year >= ACS_API_MIN_UCGID_ZCTA:
            geography_requests.append(
                (
                    "zcta",
                    {
                        "get": get_clause,
                        "for": "zip code tabulation area:*",
                    },
                )
            )
        for geography, params in geography_requests:
            request_params = dict(params)
            if api_key:
                request_params["key"] = api_key
            try:
                response = session.get(
                    base_url,
                    params=request_params,
                    timeout=120,
                    headers={"User-Agent": "UCLA housing research data pipeline/0.1"},
                )
            except requests.RequestException:
                # Requests exceptions can embed the fully prepared URL, including
                # its API key. Suppress that exception context so credentials can
                # never reach pipeline logs or the acquisition failure manifest.
                raise RuntimeError(
                    f"Census ACS API connection failed for "
                    f"{source.source_id} {year} {geography}"
                ) from None
            attempted_url = (
                response.history[0].request.url
                if getattr(response, "history", None)
                else getattr(getattr(response, "request", None), "url", response.url)
            )
            request_url = _redact_census_api_key(attempted_url, api_key)
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                status_suffix = f" (HTTP {status_code})" if status_code else ""
                raise RuntimeError(
                    f"Census ACS API request failed for "
                    f"{source.source_id} {year} {geography}{status_suffix}. "
                    f"Request URL: {request_url}"
                ) from None
            try:
                body = response.json()
            except ValueError:
                text = _redact_census_api_key(response.text, api_key)
                text = text.replace("\n", " ").strip()[:500]
                lower_text = text.casefold()
                if "missing key" in lower_text or (
                    "valid" in lower_text and "key" in lower_text
                ):
                    raise RuntimeError(
                        "The Census Data API link pattern is available for this "
                        "historical ACS table, but the API rejected this request "
                        "without a valid key. Set CENSUS_API_KEY and retry. No "
                        f"partial raw file was archived. Request URL: {request_url}"
                    ) from None
                raise RuntimeError(
                    f"Census ACS API returned invalid JSON for "
                    f"{source.source_id} {year} {geography}: {text}"
                ) from None
            if not isinstance(body, list) or len(body) < 2:
                raise RuntimeError(
                    f"Census ACS API returned no observations for "
                    f"{source.source_id} {year} {geography}"
                )
            header = [str(item) for item in body[0]]
            missing = sorted(set(["NAME", *variables]) - set(header))
            if missing:
                raise RuntimeError(
                    f"Census ACS API response missing columns for "
                    f"{source.source_id} {year} {geography}: "
                    f"{_redact_census_api_key(missing, api_key)}"
                )
            requests_made.append(
                {
                    "year": year,
                    "geography": geography,
                    "url": request_url,
                    "params_without_key": {
                        key: value
                        for key, value in request_params.items()
                        if key != "key"
                    },
                    "variables": variables,
                    "api_get": get_clause,
                    "response": body,
                }
            )
            observation_count += len(body) - 1
    temp_path.write_text(
        json.dumps(
            {
                "source_url": source.download_url,
                "source_page_url": source.source_page_url,
                "api": "Census ACS 5-year API",
                "dataset": "acs/acs5",
                "table_id": table_id,
                "start_year": start_year,
                "end_year": end_year,
                "request_contract": contract,
                "requests": requests_made,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        **contract,
        "response_latest_observation": f"{end_year}-12-31",
        "response_observation_count": str(observation_count),
    }


def _normalize_metadata_column(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _provider_last_updated(path: Path, source: SourceConfig) -> str | None:
    if source.source_id in SIPP_SOURCE_IDS:
        return sipp_published_release_date(path)
    if source.provider != "Redfin Data Center":
        return None
    read_options: dict[str, object] = {"nrows": 1, "dtype": str}
    if source.file_format == "tsv.gz":
        read_options.update({"sep": "\t", "compression": "gzip"})
    try:
        header = pd.read_csv(path, **read_options)
    except Exception:
        return None
    if header.empty:
        return None
    by_normalized = {
        _normalize_metadata_column(column): column for column in header.columns
    }
    column = by_normalized.get("last_updated")
    if column is None:
        return None
    value = str(header.iloc[0].get(column, "")).strip()
    return value or None


def _source_filename_from_url(download_url: str) -> str | None:
    filename = Path(urlparse(download_url).path).name
    return filename or None


def _extension(source: SourceConfig) -> str:
    return {
        "csv": ".csv",
        "dat": ".dat",
        "json": ".json",
        "xml": ".xml",
        "txt": ".txt",
        "tsv.gz": ".tsv.gz",
        "xlsx": ".xlsx",
        "xlsb": ".xlsb",
        "pdf": ".pdf",
        "zip": ".zip",
    }[source.file_format]


def _import_local_file(
    config: ProjectConfig,
    source: SourceConfig,
    local_file: str,
    force: bool,
) -> dict:
    logger = logging.getLogger("la_housing.download")
    path = Path(local_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Local source file does not exist: {path}")
    header = pd.read_csv(path, nrows=1, dtype=str)
    actual_columns = tuple(header.columns)
    if source.expected_columns and actual_columns != source.expected_columns:
        missing = sorted(set(source.expected_columns) - set(actual_columns))
        extra = sorted(set(actual_columns) - set(source.expected_columns))
        raise ValueError(
            f"{source.source_id} local-file schema changed: "
            f"missing={missing}, extra={extra}, "
            f"expected_order={list(source.expected_columns)}, "
            f"actual_order={list(actual_columns)}"
        )
    checksum = sha256_file(path)
    if not force:
        existing = _checksum_manifest_record(
            config, source.source_id, checksum
        )
        if existing:
            _mark_manual_resolved(
                config,
                source.source_id,
                datetime.now(timezone.utc).isoformat(),
            )
            logger.info(
                "Reusing identical archived local file for %s",
                source.source_id,
            )
            return existing[1]
    retrieved_at = datetime.now(timezone.utc)
    range_label = (
        f"{source.selected_start_date[:7]}_to_{source.selected_end_date[:7]}"
        if source.selected_start_date and source.selected_end_date
        else "selected_range"
    )
    filename = (
        f"{source.source_id}_{range_label}_"
        f"{retrieved_at:%Y%m%dT%H%M%SZ}_{checksum[:12]}"
        f"{_extension(source)}"
    )
    raw_dir = config.root / "data" / "raw" / _raw_subdir(source)
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / filename
    temp_path = raw_dir / f".{filename}.part"
    try:
        shutil.copyfile(path, temp_path)
        copied_checksum = sha256_file(temp_path)
        if copied_checksum != checksum:
            raise IOError(
                f"Checksum changed while importing {path.name}: "
                f"{checksum} != {copied_checksum}"
            )
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    first = header.iloc[0]
    source_last_updated = str(first.get("LAST UPDATED", "")).strip()
    source_frequency = str(first.get("FREQUENCY", "")).strip()
    provider_vintage = (
        "2023" if source.source_id == AHS_TABLE_CREATOR_SOURCE_ID else None
    )
    data_vintage = provider_vintage or retrieved_at.date().isoformat()
    record = {
        "source_id": source.source_id,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": source.source_page_url,
        "download_url": source.download_url,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": retrieved_at.isoformat(),
        "data_vintage": data_vintage,
        "source_filename": destination.name,
        "original_filename": path.name,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": "manual_official_download_import",
        "source_last_updated": source_last_updated,
        "provider_last_updated": source_last_updated,
        "revision_policy": source.revision_notes,
        "selected_start_date": source.selected_start_date,
        "selected_end_date": source.selected_end_date,
        "source_frequency": source_frequency,
        "provider_vintage": provider_vintage,
        "survey_year": provider_vintage,
        "request_query": (
            source.download_url
            if source.source_id == AHS_TABLE_CREATOR_SOURCE_ID
            else None
        ),
        "rolling_window_months": source.rolling_window_months,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "ingestion_status": "manually_imported",
    }
    append_csv(config.root / "metadata" / "raw_file_manifest.csv", [record])
    _mark_manual_resolved(
        config, source.source_id, retrieved_at.isoformat()
    )
    logger.info(
        "Imported %s unchanged (%s bytes, sha256=%s)",
        source.source_id,
        record["byte_size"],
        checksum[:12],
    )
    return record


def download_source(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool,
    explicitly_selected: bool,
    start_date: str | None,
    end_date: str | None,
    local_file: str | None = None,
) -> dict:
    logger = logging.getLogger("la_housing.download")
    if local_file:
        if source.source_id not in LOCAL_FILE_SOURCE_IDS:
            raise ValueError(
                "--local-file is currently supported only for "
                f"{sorted(LOCAL_FILE_SOURCE_IDS)}"
            )
        return _import_local_file(config, source, local_file, force)
    if not source.auto_download:
        raise PermissionError(source.manual_reason or "Explicit source selection required")
    if source.source_id.startswith("census_bps_"):
        if source.source_id == BPS_ANNUAL_SOURCE_ID:
            configured_start = start_date or source.selected_start_date or "1990-01-01"
            configured_end = end_date or source.selected_end_date or "2025-12-31"
            records = download_bps_annual_county(
                config.root,
                start_year=int(str(configured_start)[:4]),
                end_year=int(str(configured_end)[:4]),
                force=force,
            )
            logger.info(
                "Archived/reused %s annual BPS files for %s-%s",
                len(records),
                str(configured_start)[:4],
                str(configured_end)[:4],
            )
            return {
                "source_id": source.source_id,
                "archive_count": len(records),
                "selected_start_year": str(configured_start)[:4],
                "selected_end_year": str(configured_end)[:4],
                "download_status": "complete",
            }
        return download_bps_archive(
            config,
            source,
            force=force,
            start_date=start_date,
            end_date=end_date,
        )
    if source.source_id == "irs_soi_county_migration":
        return download_irs_soi_county_migration(
            config,
            source,
            force=force,
            start_date=start_date,
            end_date=end_date,
        )
    if source.source_id == "hud_usps_ncwm_quarterly":
        return download_hud_usps_archive(
            config,
            source,
            force=force,
            start_date=start_date,
            end_date=end_date,
        )
    acs_request_contract = None
    if source.source_id in ACS_HISTORICAL_TABLES:
        latest_available_year, end_year_resolution = (
            _resolve_acs_5year_end_year(source, end_date)
        )
        acs_request_contract = _acs_request_contract(
            config,
            source,
            start_date,
            end_date,
            latest_available_year=latest_available_year,
            end_year_resolution=end_year_resolution,
        )
    if not force:
        existing = _today_manifest_record(
            config,
            source.source_id,
            request_contract=acs_request_contract,
        )
        if existing:
            logger.info("Reusing today's raw archive for %s", source.source_id)
            return existing[1]

    raw_dir = config.root / "data" / "raw" / _raw_subdir(source)
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc)
    retrieval_token = (
        f"{retrieved_at:%Y%m%dT%H%M%S%fZ}"
        if acs_request_contract is not None
        else f"{retrieved_at:%Y%m%dT%H%M%SZ}"
    )
    temp_path = raw_dir / f".{source.source_id}.{retrieval_token}.part"
    resolved_download_url = source.download_url
    request_metadata: dict[str, str | int] = {}
    original_filename = (
        "api_response.json"
        if source.file_format == "json"
        and source.provider == "U.S. Bureau of Labor Statistics"
        else unquote(Path(urlparse(resolved_download_url).path).name)
    )
    try:
        if source.file_format == "json" and source.source_id.startswith("bls_"):
            request_metadata = _download_bls(
                source, temp_path, start_date, end_date
            )
        elif source.source_id == "dol_ca_ui_claims":
            request_metadata = download_claims(source, temp_path, start_date, end_date)
        elif source.source_id in ACS_HISTORICAL_TABLES:
            request_metadata = _download_census_acs_5year_api(
                config,
                source,
                temp_path,
                start_date,
                end_date,
                request_contract=acs_request_contract,
            )
        else:
            request_headers = (
                HUD_PIT_DOWNLOAD_HEADERS
                if source.source_id == HUD_PIT_SOURCE_ID
                else {"User-Agent": USER_AGENT}
            )
            with requests.get(
                resolved_download_url,
                stream=True,
                timeout=int(config.pipeline["request_timeout_seconds"]),
                headers=request_headers,
            ) as response:
                _write_response(
                    response,
                    temp_path,
                    int(config.pipeline["download_chunk_bytes"]),
                )
                disposition = response.headers.get("Content-Disposition", "")
                match = re.search(r'filename="?([^";]+)', disposition)
                if match:
                    original_filename = match.group(1).strip()
        if source.file_format in {"zip", "xlsx"}:
            if temp_path.read_bytes()[:2] != b"PK":
                raise ValueError(
                    f"{source.source_id} did not return a valid ZIP/XLSX payload"
                )
        elif source.file_format == "xlsb":
            # Modern XLSB workbooks are OOXML ZIP containers whose worksheet
            # payloads happen to be binary. They therefore use the PK signature,
            # not the legacy OLE compound-file signature.
            if temp_path.read_bytes()[:2] != b"PK":
                raise ValueError(
                    f"{source.source_id} did not return a valid XLSB payload"
                )
        elif source.file_format in {"dat", "txt"}:
            prefix = temp_path.read_bytes()[:200].lstrip().lower()
            if prefix.startswith((b"<!doctype html", b"<html")):
                raise ValueError(
                    f"{source.source_id} returned HTML instead of source data"
                )
        elif source.file_format == "pdf":
            if temp_path.read_bytes()[:5] != b"%PDF-":
                raise ValueError(
                    f"{source.source_id} did not return a valid PDF payload"
                )
        checksum = sha256_file(temp_path)
        filename = (
            f"{source.source_id}_{retrieval_token}_"
            f"{checksum[:12]}{_extension(source)}"
        )
        destination = raw_dir / filename
        if destination.exists():
            raise FileExistsError(
                f"Refusing to replace immutable raw archive: {destination.name}"
            )
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    provider_last_updated = (
        source.provider_release_date
        or _provider_last_updated(destination, source)
    )

    record = {
        "source_id": source.source_id,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": source.source_page_url,
        "download_url": resolved_download_url,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": retrieved_at.isoformat(),
        "data_vintage": source.provider_vintage or retrieved_at.date().isoformat(),
        "source_filename": destination.name,
        "original_filename": original_filename,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": (
            "official_api"
            if source.file_format == "json"
            and source.provider == "U.S. Bureau of Labor Statistics"
            else source.access_method
        ),
        "source_last_updated": provider_last_updated,
        "provider_last_updated": provider_last_updated,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "revision_policy": source.revision_notes,
        "ingestion_status": "downloaded",
        "provider_vintage": (
            source.provider_vintage
            or ("2024" if source.source_id in SIPP_SOURCE_IDS else None)
        ),
        "provider_release_date": (
            source.provider_release_date
            or (provider_last_updated if source.source_id in SIPP_SOURCE_IDS else None)
        ),
        "provider_release_date_status": (
            "embedded_in_official_workbook"
            if source.source_id in SIPP_SOURCE_IDS
            else None
        ),
        **request_metadata,
    }
    append_csv(config.root / "metadata" / "raw_file_manifest.csv", [record])
    logger.info(
        "Archived %s (%s bytes, sha256=%s)",
        source.source_id,
        record["byte_size"],
        checksum[:12],
    )
    return record


def download_sources(
    config: ProjectConfig,
    source_filter: str | None,
    force: bool,
    offline: bool,
    start_date: str | None,
    end_date: str | None,
    local_file: str | None = None,
    *,
    refresh_latest: bool = False,
) -> tuple[list[dict], list[dict]]:
    logger = logging.getLogger("la_housing.download")
    if local_file and source_filter not in LOCAL_FILE_SOURCE_IDS:
        raise ValueError(
            "--local-file requires --source to be one of "
            f"{sorted(LOCAL_FILE_SOURCE_IDS)}"
        )
    if offline:
        logger.info("Offline mode: download stage skipped")
        return [], []
    completed: list[dict] = []
    manual: list[dict] = []
    for source in config.selected_sources(source_filter):
        source_force = force
        source_end_date = end_date
        if refresh_latest:
            # Full-history and point-in-time provider files are fetched anew so
            # revisions and newly appended periods are observed.  Periodized
            # archives reuse already checksummed historical files and request
            # only missing periods; forcing those sources would redownload
            # hundreds of immutable files without improving freshness.
            source_force = not (
                source.source_id.startswith("census_bps_")
                or source.source_id
                in {
                    "irs_soi_county_migration",
                    "hud_usps_ncwm_quarterly",
                }
            )
            if (
                source_end_date is None
                and source.source_id
                in {"census_bps_county", "census_bps_state"}
            ):
                source_end_date = datetime.now(timezone.utc).date().isoformat()
        try:
            completed.append(
                download_source(
                    config,
                    source,
                    force=source_force,
                    explicitly_selected=source_filter is not None,
                    start_date=start_date,
                    end_date=source_end_date,
                    local_file=local_file,
                )
            )
        except Exception as exc:  # keep independent sources moving
            reason = f"{type(exc).__name__}: {exc}"
            logger.error("Could not acquire %s: %s", source.source_id, reason)
            if source_filter is not None and not refresh_latest:
                raise RuntimeError(
                    f"Could not acquire explicitly selected source "
                    f"{source.source_id}: {reason}"
                ) from exc
            manual.append(
                {
                    "source_id": source.source_id,
                    "provider": source.provider,
                    "source_page_url": source.source_page_url,
                    "download_url": source.download_url,
                    "expected_directory": f"data/raw/{_raw_subdir(source)}/",
                    "reason": reason,
                    "instruction": (
                        (
                            "Download the official file without modifying it, then run "
                            "python run_pipeline.py --stage download --source "
                            f"{source.source_id} --local-file PATH_TO_FILE. "
                            "The import command creates the immutable raw archive and provenance record."
                        )
                        if source.source_id in LOCAL_FILE_SOURCE_IDS
                        else (
                            "This source has no generic --local-file importer. Follow "
                            "the source-specific access notes and approved ingestion "
                            "workflow before rebuilding: "
                            f"{source.manual_reason or source.source_page_url}"
                        )
                    ),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    if manual:
        pd.DataFrame(manual).to_csv(
            config.root / "metadata" / "manual_downloads.csv", index=False
        )
    return completed, manual
