from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.common.config import load_config
from src.ingestion.eviction_lab_ets_audit import (
    SOURCE_PAGE_URL,
    EvictionLabETSAuditError,
    parse_ets_product_registry,
    refresh_ets_coverage_audit,
)


def _page(*site_rows: tuple[str, str, str]) -> bytes:
    rows = []
    for site, weekly_url, monthly_url in site_rows:
        rows.append(
            f"""
            <tr>
              <td>{site}</td><td>2018-2019</td><td>Census Tract</td>
              <td><a href="{weekly_url}">Download CSV</a></td>
              <td><a href="{monthly_url}">Download CSV</a></td>
              <td>08/2026</td>
            </tr>
            """
        )
    return f"""
    <html><body>
      <p>Eviction Tracking System: Version 3.0.</p>
      <table id="ets-get-data">
        <thead><tr>
          <th>Site</th><th>Prepandemic Baseline Years</th>
          <th>Smallest Geography</th><th>Weekly Data</th>
          <th>Monthly Data</th><th>Last Updated</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      <p>Last Updated: August 8, 2026</p>
    </body></html>
    """.encode()


def _complete_page(*extra_rows: tuple[str, str, str]) -> bytes:
    return _page(
        (
            "All Cities",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/all.csv",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/all-month.csv",
        ),
        (
            "All States",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/states.csv",
            "/uploads/states-month.csv",
        ),
        (
            "Phoenix",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/phx.csv",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/phx-month.csv",
        ),
        *extra_rows,
    )


class _Response:
    status_code = 200
    headers = {
        "Last-Modified": "Sat, 08 Aug 2026 12:00:00 GMT",
        "ETag": '"fixture"',
    }

    def __init__(self, content: bytes) -> None:
        self.content = content


class _Session:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.content)


def test_parser_reports_no_la_product_from_complete_official_registry() -> None:
    registry, metadata = parse_ets_product_registry(_complete_page())

    assert list(registry["site"]) == ["All Cities", "All States", "Phoenix"]
    assert not registry["target_geography_match"].any()
    assert not registry["qualifying_high_frequency_target_product"].any()
    assert registry["weekly_url"].str.startswith("https://").all()
    assert metadata == {
        "ets_version": "3.0",
        "provider_page_last_updated": "August 8, 2026",
    }


@pytest.mark.parametrize("site", ("Los Angeles", "California"))
def test_parser_detects_new_target_product_instead_of_preserving_stale_result(
    site: str,
) -> None:
    target = (
        site,
        "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/target.csv",
        "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/target-month.csv",
    )
    registry, _ = parse_ets_product_registry(_complete_page(target))
    match = registry.loc[registry["site"].eq(site)].iloc[0]

    assert bool(match["target_geography_match"])
    assert bool(match["qualifying_high_frequency_target_product"])


def test_incomplete_registry_cannot_be_used_to_claim_unavailability() -> None:
    payload = _page(
        (
            "All Cities",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/all.csv",
            "https://eviction-lab-data-downloads.s3.amazonaws.com/ets/all-month.csv",
        )
    )
    with pytest.raises(EvictionLabETSAuditError, match="incomplete"):
        parse_ets_product_registry(payload)


def test_refresh_archives_registry_and_writes_machine_readable_negative_audit(
    tmp_path: Path,
) -> None:
    payload = _complete_page()
    session = _Session(payload)
    checked_at = datetime(2026, 8, 17, 19, 30, tzinfo=timezone.utc)

    audit, registry = refresh_ets_coverage_audit(
        tmp_path,
        session=session,
        retrieved_at=checked_at,
    )

    archive = tmp_path / str(audit["archive_relative_path"])
    assert archive.read_bytes() == payload
    assert audit["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
    assert audit["credential_required"] is False
    assert audit["high_frequency_la_county_product_available"] is False
    assert audit["audit_result"] == "not_available_in_official_ets_registry"
    assert audit["decision"] == "retain_annual_2000_2018_without_interpolation"
    assert audit["retained_annual_series_modified"] is False
    assert audit["interpolation_authorized"] is False
    assert len(registry) == 3
    assert session.calls[0][0] == SOURCE_PAGE_URL
    saved = pd.read_csv(
        tmp_path / "metadata" / "eviction_lab_ets_source_check.csv",
        dtype=str,
    )
    assert saved.loc[0, "target_county_fips"] == "06037"
    assert saved.loc[0, "retained_annual_start_year"] == "2000"
    assert saved.loc[0, "retained_annual_end_year"] == "2018"


def test_current_annual_eviction_source_contract_remains_unchanged() -> None:
    root = Path(__file__).resolve().parents[1]
    source = next(
        item
        for item in load_config(root).sources
        if item.source_id == "eviction_lab_county_estimates"
    )

    assert source.native_frequency == "annual"
    assert source.geography_levels == ("county",)
    assert source.selected_start_date == "2000-01-01"
    assert source.selected_end_date == "2018-12-31"
