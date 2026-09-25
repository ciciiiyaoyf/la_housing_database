"""Immutable authenticated acquisition for HUD-USPS quarterly source data."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file

USER_AGENT = "UCLA housing research data pipeline/0.2"
NCWM_API = "https://www.huduser.gov/hudapi/public/uspsncwm"
CROSSWALK_API = "https://www.huduser.gov/hudapi/public/usps"
COUNTY_REFERENCE_FILE = (
    "https://www2.census.gov/geo/docs/reference/codes2020/cou/"
    "st06_ca_cou2020.txt"
)
STATE_FIPS = "06"
STATE_ABBREVIATION = "CA"
HUD_API_MIN_INTERVAL_SECONDS = 1.05
LICENSE_NOTICE = (
    "Contains in whole or in part confidential and proprietary property of the "
    "United States Postal Service subject to the protection of copyright and "
    "other law, under license. All rights reserved.\n\n"
    "The HUD-USPS sublicense restricts use, access, and redistribution. Keep "
    "these data and derived files in authorized, access-controlled project "
    "storage. Do not place them in a public repository or distribute them "
    "outside the licensed stated purpose.\n"
)

_THREAD_LOCAL = threading.local()


def _session(token: str | None = None) -> requests.Session:
    key = "authenticated_session" if token else "public_session"
    session = getattr(_THREAD_LOCAL, key, None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        if token:
            session.headers["Authorization"] = f"Bearer {token}"
        setattr(_THREAD_LOCAL, key, session)
    return session


def _quarters(start_date: str, end_date: str) -> list[pd.Period]:
    start = pd.Timestamp(start_date).to_period("Q")
    end = pd.Timestamp(end_date).to_period("Q")
    if start > end:
        raise ValueError(f"HUD-USPS start quarter {start} is after end quarter {end}")
    return list(pd.period_range(start, end, freq="Q"))


def _latest_published_quarter(token: str) -> pd.Period:
    """Ask the documented endpoint for its latest published USPS period.

    HUD documents omitted year/month as selecting the latest available release:
    https://www.huduser.gov/portal/dataset/uspsncwm-api.html
    Only the returned period is used here; observations remain in immutable archives.
    """
    response = _session(token).get(
        NCWM_API, params={"type": 1, "stateid": STATE_FIPS}, timeout=120
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("results"):
        raise ValueError("HUD latest-release response contains no USPS results")
    try:
        year, month = int(payload["year"]), int(payload["month"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("HUD latest-release response lacks a valid year/month") from exc
    if month not in {3, 6, 9, 12} or not 2008 <= year <= datetime.now(timezone.utc).year:
        raise ValueError("HUD latest-release response has an invalid quarterly period")
    return pd.Period(year=year, quarter=month // 3, freq="Q")


def _targets(source: SourceConfig, start_date: str, end_date: str) -> list[dict[str, Any]]:
    quarters = _quarters(start_date, end_date)
    targets: list[dict[str, Any]] = []
    for period in quarters:
        quarter_end = period.end_time.normalize()
        params = {
            "type": 1,
            "year": int(quarter_end.year),
            "month": int(quarter_end.month),
            "stateid": STATE_FIPS,
        }
        targets.append(
            {
                "artifact_role": "ncwm_standardized_2020",
                "archive_period": f"{period.year}-Q{period.quarter}",
                "provider_vintage": f"{period.year}Q{period.quarter}",
                "period_end": quarter_end.date().isoformat(),
                "download_url": f"{NCWM_API}?{urlencode(params)}",
                "request_params": params,
                "provider": source.provider,
            }
        )

    crosswalk_period = quarters[-1]
    crosswalk_params = {
        "type": 6,
        "query": STATE_ABBREVIATION,
        "year": crosswalk_period.year,
        "quarter": crosswalk_period.quarter,
    }
    targets.append(
        {
            "artifact_role": "tract_zip_crosswalk",
            "archive_period": (
                f"{crosswalk_period.year}-Q{crosswalk_period.quarter}-crosswalk"
            ),
            "provider_vintage": (
                f"{crosswalk_period.year}Q{crosswalk_period.quarter}"
            ),
            "period_end": crosswalk_period.end_time.date().isoformat(),
            "download_url": f"{CROSSWALK_API}?{urlencode(crosswalk_params)}",
            "request_params": crosswalk_params,
            "provider": source.provider,
        }
    )
    targets.append(
        {
            "artifact_role": "county_fips_reference_2020",
            "archive_period": "2020-county-fips-reference",
            "provider_vintage": "2020 Census",
            "period_end": "2020-04-01",
            "download_url": COUNTY_REFERENCE_FILE,
            "request_params": {},
            "provider": "U.S. Census Bureau",
            "original_filename": "st06_ca_cou2020.txt",
            "file_extension": "txt",
        }
    )
    return targets


def _existing_files(config: ProjectConfig, source_id: str) -> pd.DataFrame:
    path = config.root / "metadata" / "raw_file_manifest.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return frame
    for column in ("artifact_role", "archive_period"):
        if column not in frame:
            frame[column] = None
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    frame = (
        frame.loc[
            frame["artifact_role"].notna() & frame["archive_period"].notna()
        ]
        .sort_values("_retrieved")
        .groupby(["artifact_role", "archive_period"], as_index=False)
        .tail(1)
    )
    exists = [
        (config.root / str(row.relative_path)).is_file()
        and sha256_file(config.root / str(row.relative_path)) == str(row.checksum_sha256)
        for row in frame.itertuples()
    ]
    return frame.loc[exists].drop(columns="_retrieved")


def _validate_payload(target: dict[str, Any], content: bytes) -> int:
    role = target["artifact_role"]
    if role == "county_fips_reference_2020":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "Census county reference is not valid UTF-8 text"
            ) from exc
        lines = [line for line in text.splitlines() if line.strip()]
        expected_header = (
            "STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT"
        )
        if not lines or lines[0] != expected_header:
            raise ValueError("Census county reference schema changed")
        if len(lines) != 59:
            raise ValueError(
                "Census California county reference must contain 58 rows"
            )
        if any(
            len(line.split("|")) != 7
            or line.split("|")[0] != "CA"
            or line.split("|")[1] != STATE_FIPS
            for line in lines[1:]
        ):
            raise ValueError("Census county reference contains invalid rows")
        return len(lines) - 1
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("HUD-USPS endpoint did not return valid JSON") from exc

    if role == "ncwm_standardized_2020":
        expected_year = str(target["request_params"]["year"])
        expected_month = int(target["request_params"]["month"])
        if str(payload.get("year")) != expected_year:
            raise ValueError(
                f"HUD-USPS payload year {payload.get('year')!r} != {expected_year}"
            )
        if int(payload.get("month", -1)) != expected_month:
            raise ValueError(
                f"HUD-USPS payload month {payload.get('month')!r} != {expected_month}"
            )
        rows = payload.get("results")
        if not isinstance(rows, list) or not rows:
            raise ValueError("HUD-USPS NCWM response has no results")
        required = {
            "TRACT ID",
            "STCNTY",
            "TOTAL_RESIDENTIAL_ADDRESSES",
            "STV_RESIDENTIAL_ADDRESSES",
            "LTV_RESIDENTIAL_ADDRESSES",
            "NO_STAT_RESIDENTIAL_ADDRESSES",
            "Active_Residential_Addresses",
        }
        missing = sorted(required - set(rows[0]))
        if missing:
            raise ValueError(f"HUD-USPS NCWM schema missing fields: {missing}")
        return len(rows)
    if role == "tract_zip_crosswalk":
        data = payload.get("data")
        if isinstance(data, list):
            if len(data) != 1 or not isinstance(data[0], dict):
                raise ValueError("HUD-USPS crosswalk response has ambiguous data")
            data = data[0]
        if not isinstance(data, dict):
            raise ValueError("HUD-USPS crosswalk response lacks data object")
        rows = data.get("results")
        if not isinstance(rows, list) or not rows:
            raise ValueError("HUD-USPS crosswalk response has no results")
        required = {"tract", "geoid", "res_ratio"}
        missing = sorted(required - set(rows[0]))
        if missing:
            raise ValueError(f"HUD-USPS crosswalk schema missing fields: {missing}")
        return len(rows)
    raise ValueError(f"Unsupported HUD-USPS artifact role: {role}")


def _archive_one(
    config: ProjectConfig,
    source: SourceConfig,
    target: dict[str, Any],
    token: str,
    raw_dir: Path,
) -> tuple[dict | None, dict]:
    retrieved_at = datetime.now(timezone.utc)
    role = str(target["artifact_role"])
    period_token = re.sub(r"[^A-Za-z0-9]+", "", str(target["archive_period"]))
    temp = raw_dir / (
        f".{source.source_id}.{role}.{period_token}."
        f"{retrieved_at:%Y%m%dT%H%M%S%fZ}.part"
    )
    coverage = {
        "source_id": source.source_id,
        "artifact_role": role,
        "archive_period": target["archive_period"],
        "provider_vintage": target["provider_vintage"],
        "download_url": target["download_url"],
        "retrieved_at": retrieved_at.isoformat(),
        "download_status": "failed",
        "http_status": None,
        "relative_path": None,
        "checksum_sha256": None,
        "response_observation_count": None,
        "detail": None,
    }
    try:
        authenticated = role != "county_fips_reference_2020"
        response = _session(token if authenticated else None).get(
            target["download_url"],
            timeout=120,
        )
        coverage["http_status"] = response.status_code
        response.raise_for_status()
        row_count = _validate_payload(target, response.content)
        temp.write_bytes(response.content)
        checksum = sha256_file(temp)
        extension = str(target.get("file_extension") or "json")
        filename = (
            f"{source.source_id}_{role}_{period_token}_"
            f"{retrieved_at:%Y%m%dT%H%M%SZ}_{checksum[:12]}.{extension}"
        )
        destination = raw_dir / filename
        if destination.exists():
            raise FileExistsError(f"Refusing to replace immutable HUD archive: {destination}")
        temp.replace(destination)
        record = {
            "source_id": source.source_id,
            "provider": target["provider"],
            "dataset_name": source.dataset_name,
            "source_page_url": source.source_page_url,
            "download_url": target["download_url"],
            "request_query": urlencode(target["request_params"]),
            "native_frequency": source.native_frequency,
            "geography_levels": "|".join(source.geography_levels),
            "retrieved_at": retrieved_at.isoformat(),
            "data_vintage": retrieved_at.date().isoformat(),
            "source_filename": destination.name,
            "original_filename": target.get(
                "original_filename", "api_response.json"
            ),
            "stored_filename": destination.name,
            "relative_path": destination.relative_to(config.root).as_posix(),
            "byte_size": destination.stat().st_size,
            "checksum_sha256": checksum,
            "retrieval_method": (
                "authorized_official_api"
                if authenticated
                else "official_public_api"
            ),
            "artifact_role": role,
            "archive_period": target["archive_period"],
            "provider_vintage": target["provider_vintage"],
            "provider_release_date": None,
            "provider_release_date_status": "not_published_by_endpoint",
            "response_latest_observation": target["period_end"],
            "response_observation_count": row_count,
            "license_or_use_notes": source.license_or_use_notes,
            "revision_notes": source.revision_notes,
            "revision_policy": source.revision_notes,
            "ingestion_status": "downloaded",
        }
        coverage.update(
            {
                "download_status": "downloaded",
                "relative_path": record["relative_path"],
                "checksum_sha256": checksum,
                "response_observation_count": row_count,
            }
        )
        return record, coverage
    except Exception as exc:
        coverage["detail"] = f"{type(exc).__name__}: {exc}"
        return None, coverage
    finally:
        if temp.exists():
            temp.unlink()


def _write_source_manifest(config: ProjectConfig, source_id: str) -> None:
    raw_manifest = config.root / "metadata" / "raw_file_manifest.csv"
    if not raw_manifest.exists():
        return
    frame = pd.read_csv(raw_manifest, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    frame = (
        frame.sort_values("_retrieved")
        .groupby(["artifact_role", "archive_period"], as_index=False)
        .tail(1)
        .sort_values(["artifact_role", "archive_period"])
    )
    columns = [
        "source_id",
        "provider",
        "artifact_role",
        "archive_period",
        "provider_vintage",
        "provider_release_date",
        "provider_release_date_status",
        "download_url",
        "request_query",
        "source_filename",
        "relative_path",
        "retrieved_at",
        "byte_size",
        "checksum_sha256",
        "response_observation_count",
        "retrieval_method",
        "ingestion_status",
    ]
    output = config.root / "metadata" / "hud_usps_source_manifest.csv"
    frame.reindex(columns=columns).to_csv(output, index=False)


def download_hud_usps_archive(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool,
    start_date: str | None,
    end_date: str | None,
    refresh_latest: bool = False,
) -> dict:
    """Archive every quarterly NCWM response and the exact support vintages."""

    logger = logging.getLogger("la_housing.download.hud_usps")
    effective_start = start_date or source.selected_start_date or "2008-01-01"
    effective_end = end_date or source.selected_end_date or "2026-03-31"
    if refresh_latest:
        token = (os.getenv("HUDUSER_API_TOKEN") or "").strip()
        if not token:
            raise PermissionError(
                "Set HUDUSER_API_TOKEN to verify the latest official HUD-USPS release; "
                "a cached archive alone cannot establish freshness."
            )
        latest = _latest_published_quarter(token)
        supported = pd.Timestamp(source.selected_end_date or "2026-03-31").to_period("Q")
        requested = pd.Timestamp(effective_end).to_period("Q")
        if requested > supported or (end_date is None and latest > supported):
            raise RuntimeError(
                f"HUD publishes {latest}, but the active cleaner and fixed ZIP crosswalk "
                f"are approved only through {supported}. Review and version the "
                "geography/coverage contract before extending the archive."
            )
        if requested > latest:
            raise RuntimeError(f"HUD latest release {latest} precedes requested endpoint {requested}")
    targets = _targets(source, effective_start, effective_end)
    existing = _existing_files(config, source.source_id)
    existing_keys = (
        {
            (str(row.artifact_role), str(row.archive_period)): row
            for row in existing.itertuples()
        }
        if not existing.empty
        else {}
    )

    raw_dir = config.root / "data" / "restricted" / "hud_usps" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir.parent / "LICENSE_NOTICE.txt").write_text(
        LICENSE_NOTICE, encoding="utf-8"
    )

    records: list[dict] = []
    coverage: list[dict] = []
    pending: list[dict[str, Any]] = []
    for target in targets:
        key = (str(target["artifact_role"]), str(target["archive_period"]))
        prior = existing_keys.get(key)
        recheck_latest = (
            refresh_latest
            and key[0] == "ncwm_standardized_2020"
            and key[1] == f"{pd.Timestamp(effective_end).year}-Q{pd.Timestamp(effective_end).quarter}"
        )
        if not force and prior is not None and not recheck_latest:
            coverage.append(
                {
                    "source_id": source.source_id,
                    "artifact_role": key[0],
                    "archive_period": key[1],
                    "provider_vintage": prior.provider_vintage,
                    "download_url": prior.download_url,
                    "retrieved_at": prior.retrieved_at,
                    "download_status": "reused",
                    "http_status": None,
                    "relative_path": prior.relative_path,
                    "checksum_sha256": prior.checksum_sha256,
                    "response_observation_count": getattr(
                        prior, "response_observation_count", None
                    ),
                    "detail": "Reused latest immutable local archive.",
                }
            )
        else:
            pending.append(target)

    token = (os.getenv("HUDUSER_API_TOKEN") or "").strip()
    authenticated_pending = [
        target
        for target in pending
        if target["artifact_role"] != "county_fips_reference_2020"
    ]
    if authenticated_pending and not token:
        raise PermissionError(
            "Set HUDUSER_API_TOKEN to an authorized HUD User bearer token. "
            "The token is required only for HUD artifacts not already present "
            "in the immutable local archive; it is used only in the "
            "Authorization header and is never written to disk or logs."
        )

    # HUD's API terms cap callers at 60 requests per minute. Keep the
    # authenticated requests sequential and slightly more than one second
    # apart; the public Census reference is not subject to that HUD limit.
    last_hud_request_started_at: float | None = None
    for target in pending:
        authenticated = target["artifact_role"] != "county_fips_reference_2020"
        record: dict | None = None
        result: dict = {}
        for attempt in range(1, 4):
            if authenticated and last_hud_request_started_at is not None:
                elapsed = time.monotonic() - last_hud_request_started_at
                remaining = HUD_API_MIN_INTERVAL_SECONDS - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            if authenticated:
                last_hud_request_started_at = time.monotonic()
            record, result = _archive_one(
                config, source, target, token, raw_dir
            )
            result["attempts"] = attempt
            if record is not None:
                break
            status = result.get("http_status")
            detail = str(result.get("detail") or "")
            retryable = status in {429, 500, 502, 503, 504} or any(
                marker in detail
                for marker in ("ConnectionError", "Timeout", "ReadTimeout")
            )
            if not retryable:
                break
            time.sleep(min(2**attempt, 8))
        coverage.append(result)
        if record is not None:
            records.append(record)

    if records:
        append_csv(config.root / "metadata" / "raw_file_manifest.csv", records)
    coverage_frame = pd.DataFrame(coverage).sort_values(
        ["artifact_role", "archive_period"]
    )
    coverage_path = (
        config.root / "output" / "coverage" / "hud_usps_archive_download.csv"
    )
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_frame.to_csv(coverage_path, index=False)
    _write_source_manifest(config, source.source_id)

    failures = coverage_frame.loc[
        ~coverage_frame["download_status"].isin({"downloaded", "reused"})
    ]
    if not failures.empty:
        examples = failures[
            ["artifact_role", "archive_period", "detail"]
        ].head(5).to_dict("records")
        raise RuntimeError(
            f"HUD-USPS archive is incomplete: {len(failures)} failed artifacts; "
            f"examples={examples}"
        )

    expected_quarters = len(_quarters(effective_start, effective_end))
    ncwm = coverage_frame.loc[
        coverage_frame["artifact_role"].eq("ncwm_standardized_2020")
    ]
    roles = set(coverage_frame["artifact_role"])
    required_roles = {
        "ncwm_standardized_2020",
        "tract_zip_crosswalk",
        "county_fips_reference_2020",
    }
    if len(ncwm) != expected_quarters or roles != required_roles:
        raise RuntimeError(
            "HUD-USPS archive coverage is structurally incomplete: "
            f"quarters={len(ncwm)}/{expected_quarters}, roles={sorted(roles)}"
        )
    logger.info(
        "HUD-USPS archive complete: %s quarters, %s new files, %s reused files",
        expected_quarters,
        len(records),
        sum(item["download_status"] == "reused" for item in coverage),
    )
    return {
        "source_id": source.source_id,
        "archive_start": ncwm["archive_period"].min(),
        "archive_end": ncwm["archive_period"].max(),
        "archive_files": len(coverage_frame),
        "new_files": len(records),
        "ingestion_status": "downloaded",
    }
