"""Archive and audit Eviction Lab's official ETS product registry.

The workbook decision for eviction trends requires a periodic check for a
genuinely high-frequency Los Angeles County product.  This module audits the
provider's own ``Get the Data`` registry rather than inferring availability
from search results or substituting another geography.  It does not ingest or
alter the retained 2000-2018 annual county estimates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests


SOURCE_ID = "eviction_lab_ets_coverage_audit"
PROVIDER = "Eviction Lab"
PRODUCT_NAME = "Eviction Tracking System product registry"
SOURCE_PAGE_URL = "https://evictionlab.org/eviction-tracking/get-the-data/"
USER_AGENT = "UCLA housing research data pipeline/0.2"
TARGET_COUNTY_FIPS = "06037"
TARGET_GEOGRAPHY = "Los Angeles County, California"
ANNUAL_SOURCE_ID = "eviction_lab_county_estimates"
ANNUAL_START_YEAR = 2000
ANNUAL_END_YEAR = 2018
EXPECTED_HEADERS = (
    "Site",
    "Prepandemic Baseline Years",
    "Smallest Geography",
    "Weekly Data",
    "Monthly Data",
    "Last Updated",
)
OFFICIAL_DOWNLOAD_HOSTS = frozenset(
    {
        "evictionlab.org",
        "www.evictionlab.org",
        "eviction-lab-data-downloads.s3.amazonaws.com",
    }
)
TARGET_SITE_PATTERN = re.compile(r"\b(?:los\s+angeles|california)\b", re.I)


class EvictionLabETSAuditError(RuntimeError):
    """Raised when the official registry cannot support a safe conclusion."""


class _ETSTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_table = False
        self.in_cell = False
        self.current_cell_tag = ""
        self.current_cell_text: list[str] = []
        self.current_cell_links: list[str] = []
        self.current_row: list[dict[str, object]] = []
        self.rows: list[list[dict[str, object]]] = []
        self.page_text_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "ets-get-data":
            if self.in_target_table or self.rows:
                raise EvictionLabETSAuditError(
                    "Official ETS page contains duplicate product registries"
                )
            self.in_target_table = True
        elif self.in_target_table and tag == "tr":
            self.current_row = []
        elif self.in_target_table and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell_tag = tag
            self.current_cell_text = []
            self.current_cell_links = []
        elif self.in_cell and tag == "a" and attributes.get("href"):
            self.current_cell_links.append(str(attributes["href"]))

    def handle_data(self, data: str) -> None:
        self.page_text_parts.append(data)
        if self.in_cell:
            self.current_cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_target_table and self.in_cell and tag == self.current_cell_tag:
            text = " ".join(" ".join(self.current_cell_text).split())
            self.current_row.append(
                {
                    "tag": self.current_cell_tag,
                    "text": text,
                    "links": tuple(self.current_cell_links),
                }
            )
            self.in_cell = False
            self.current_cell_tag = ""
        elif self.in_target_table and tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
        elif self.in_target_table and tag == "table":
            self.in_target_table = False

    @property
    def page_text(self) -> str:
        return " ".join(" ".join(self.page_text_parts).split())


def _official_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in OFFICIAL_DOWNLOAD_HOSTS


def parse_ets_product_registry(payload: bytes) -> tuple[pd.DataFrame, dict[str, str]]:
    """Parse and validate the official ETS product table from archived HTML."""
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvictionLabETSAuditError(
            "Official ETS product registry is not UTF-8 HTML"
        ) from exc

    parser = _ETSTableParser()
    parser.feed(html)
    if not parser.rows:
        raise EvictionLabETSAuditError(
            "Official ETS page has no table with id='ets-get-data'"
        )
    headers = tuple(
        str(cell["text"])
        for cell in parser.rows[0]
        if cell["tag"] == "th"
    )
    if headers != EXPECTED_HEADERS:
        raise EvictionLabETSAuditError(
            f"Official ETS product registry headers changed: {headers}"
        )

    records: list[dict[str, object]] = []
    for row in parser.rows[1:]:
        if not row:
            continue
        if len(row) != len(EXPECTED_HEADERS) or any(
            cell["tag"] != "td" for cell in row
        ):
            raise EvictionLabETSAuditError(
                "Official ETS product registry contains a malformed data row"
            )
        cells = {header: row[index] for index, header in enumerate(headers)}
        site = str(cells["Site"]["text"]).strip()
        weekly_links = tuple(cells["Weekly Data"]["links"])
        monthly_links = tuple(cells["Monthly Data"]["links"])
        weekly_url = (
            urljoin(SOURCE_PAGE_URL, str(weekly_links[0])) if weekly_links else ""
        )
        monthly_url = (
            urljoin(SOURCE_PAGE_URL, str(monthly_links[0]))
            if monthly_links
            else ""
        )
        if len(weekly_links) > 1 or len(monthly_links) > 1:
            raise EvictionLabETSAuditError(
                f"Official ETS registry has multiple downloads for {site}"
            )
        for download_url in (weekly_url, monthly_url):
            if download_url and not _official_url(download_url):
                raise EvictionLabETSAuditError(
                    f"ETS registry points to a non-official host: {download_url}"
                )
        matched_terms = sorted(
            {match.group(0).lower() for match in TARGET_SITE_PATTERN.finditer(site)}
        )
        records.append(
            {
                "site": site,
                "prepandemic_baseline_years": str(
                    cells["Prepandemic Baseline Years"]["text"]
                ),
                "smallest_geography": str(cells["Smallest Geography"]["text"]),
                "weekly_url": weekly_url,
                "monthly_url": monthly_url,
                "last_updated": str(cells["Last Updated"]["text"]),
                "has_weekly_product": bool(weekly_url),
                "has_monthly_product": bool(monthly_url),
                "target_geography_match": bool(matched_terms),
                "target_match_terms": "|".join(matched_terms),
                "qualifying_high_frequency_target_product": bool(matched_terms)
                and bool(weekly_url or monthly_url),
            }
        )
    registry = pd.DataFrame(records)
    if registry.empty or registry["site"].duplicated().any():
        raise EvictionLabETSAuditError(
            "Official ETS product registry is empty or has duplicate sites"
        )
    required_umbrella_rows = {"All Cities", "All States"}
    if not required_umbrella_rows.issubset(set(registry["site"])):
        raise EvictionLabETSAuditError(
            "Official ETS registry is incomplete: All Cities/All States missing"
        )

    version_match = re.search(
        r"Eviction Tracking System:\s*Version\s*([0-9]+(?:\.[0-9]+)*)",
        parser.page_text,
        flags=re.I,
    )
    page_updated_match = re.search(
        r"Last Updated:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        parser.page_text,
        flags=re.I,
    )
    if not version_match or not page_updated_match:
        raise EvictionLabETSAuditError(
            "Official ETS page no longer publishes a release version and update date"
        )
    page_metadata = {
        "ets_version": version_match.group(1),
        "provider_page_last_updated": page_updated_match.group(1),
    }
    return registry, page_metadata


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _append_audit_row(path: Path, record: dict[str, object]) -> None:
    incoming = pd.DataFrame([record])
    if path.exists():
        existing = pd.read_csv(path, dtype=str)
        frame = pd.concat([existing, incoming], ignore_index=True, sort=False)
    else:
        frame = incoming
    _write_csv_atomic(frame, path)


def refresh_ets_coverage_audit(
    root: Path,
    *,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
    timeout_seconds: int = 60,
) -> tuple[dict[str, object], pd.DataFrame]:
    """Archive the live official registry and write machine-readable results."""
    root = Path(root).resolve()
    client = session or requests.Session()
    try:
        response = client.get(
            SOURCE_PAGE_URL,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise EvictionLabETSAuditError(
            f"Unable to retrieve official Eviction Lab registry: {exc}"
        ) from exc
    status_code = int(getattr(response, "status_code", 0))
    if status_code != 200:
        raise EvictionLabETSAuditError(
            f"Official Eviction Lab registry returned HTTP {status_code}"
        )
    payload = bytes(response.content)
    registry, page_metadata = parse_ets_product_registry(payload)

    retrieved = retrieved_at or datetime.now(timezone.utc)
    if retrieved.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    retrieved = retrieved.astimezone(timezone.utc)
    checksum = hashlib.sha256(payload).hexdigest()
    raw_dir = root / "data" / "raw" / "eviction_lab" / "ets_coverage_registry"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = (
        "eviction_lab_ets_get_the_data_"
        f"{retrieved:%Y%m%dT%H%M%S%fZ}_{checksum[:12]}.html"
    )
    destination = raw_dir / stored_filename
    if destination.exists():
        raise FileExistsError(
            f"Refusing to replace immutable ETS registry archive: {destination}"
        )
    temporary = raw_dir / f".{stored_filename}.part"
    try:
        temporary.write_bytes(payload)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != checksum:
            raise EvictionLabETSAuditError(
                "Archived ETS registry checksum does not match response"
            )
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    relative_archive = destination.relative_to(root).as_posix()
    registry_relative = "metadata/eviction_lab_ets_registry_snapshot.csv"
    registry = registry.copy()
    registry.insert(0, "source_id", SOURCE_ID)
    registry.insert(1, "retrieved_at", retrieved.isoformat())
    registry.insert(2, "source_page_url", SOURCE_PAGE_URL)
    registry.insert(3, "source_filename", stored_filename)
    registry.insert(4, "checksum_sha256", checksum)
    registry.insert(5, "ets_version", page_metadata["ets_version"])
    registry.insert(
        6,
        "provider_page_last_updated",
        page_metadata["provider_page_last_updated"],
    )
    _write_csv_atomic(registry, root / registry_relative)

    target_matches = registry.loc[registry["target_geography_match"]]
    qualifying_matches = registry.loc[
        registry["qualifying_high_frequency_target_product"]
    ]
    available = not qualifying_matches.empty
    result = (
        "available_review_before_integration"
        if available
        else "not_available_in_official_ets_registry"
    )
    decision = (
        "review_new_provider_product_before_integration"
        if available
        else "retain_annual_2000_2018_without_interpolation"
    )
    headers = getattr(response, "headers", {}) or {}
    audit_record: dict[str, object] = {
        "audit_id": f"{SOURCE_ID}_{retrieved:%Y%m%dT%H%M%SZ}_{checksum[:12]}",
        "source_id": SOURCE_ID,
        "provider": PROVIDER,
        "product_name": PRODUCT_NAME,
        "ets_version": page_metadata["ets_version"],
        "source_page_url": SOURCE_PAGE_URL,
        "source_filename": stored_filename,
        "archive_relative_path": relative_archive,
        "registry_snapshot_relative_path": registry_relative,
        "retrieved_at": retrieved.isoformat(),
        "provider_page_last_updated": page_metadata[
            "provider_page_last_updated"
        ],
        "http_status": status_code,
        "http_last_modified": headers.get("Last-Modified", ""),
        "http_etag": headers.get("ETag", ""),
        "byte_size": len(payload),
        "checksum_sha256": checksum,
        "retrieval_method": "official_public_web_registry",
        "credential_required": False,
        "target_geography": TARGET_GEOGRAPHY,
        "target_county_fips": TARGET_COUNTY_FIPS,
        "registry_row_count": len(registry),
        "weekly_product_row_count": int(registry["has_weekly_product"].sum()),
        "monthly_product_row_count": int(registry["has_monthly_product"].sum()),
        "target_site_match_count": len(target_matches),
        "qualifying_target_product_count": len(qualifying_matches),
        "matched_target_sites": "|".join(target_matches["site"].astype(str)),
        "high_frequency_la_county_product_available": available,
        "audit_result": result,
        "decision": decision,
        "retained_annual_source_id": ANNUAL_SOURCE_ID,
        "retained_annual_start_year": ANNUAL_START_YEAR,
        "retained_annual_end_year": ANNUAL_END_YEAR,
        "retained_annual_frequency": "annual",
        "retained_annual_series_modified": False,
        "interpolation_authorized": False,
        "notes": (
            "Availability is determined from the provider's complete ETS data "
            "registry. A Los Angeles or California row must expose a weekly or "
            "monthly official download before the result can be available."
        ),
    }
    _append_audit_row(
        root / "metadata" / "eviction_lab_ets_source_check.csv",
        audit_record,
    )
    return audit_record, registry


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive and audit Eviction Lab ETS coverage for LA County"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout-seconds", type=int, default=60)
    arguments = parser.parse_args()
    record, _ = refresh_ets_coverage_audit(
        arguments.root,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    main()
