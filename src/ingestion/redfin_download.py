"""Acquire Redfin ZIP CSVs through the provider's public download controls.

The download hub constructs the selected CSV in the browser. No private API,
guessed object URL, saved login, cookie export, or anti-bot bypass is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

from src.cleaning.redfin_zip import validate_redfin_zip_schema
from src.common.config import ProjectConfig, SourceConfig
from src.common.io import append_csv, sha256_file

SOURCE_ID = "redfin_zip_monthly"
DOWNLOAD_HUB = "https://www.redfin.com/news/data-center/downloads/"


def _activate(card: Any, label: str) -> None:
    button = card.get_by_role("button", name=label, exact=True)
    if "active" not in (button.get_attribute("class") or "").split():
        button.click()


def _latest_option(select: Any) -> str:
    values = select.locator("option:not([disabled])").evaluate_all(
        "options => options.map(option => option.value)"
    )
    return max(value for value in values if value.isdigit())


def _browser_download(
    destination: Path, start_date: str, end_date: str | None, timeout_seconds: int
) -> dict[str, str]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Redfin needs the project's Playwright package. Install requirements.txt "
            "and run: python -m playwright install chromium"
        ) from exc
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            raise RuntimeError(
                "Redfin's browser could not start. Install its runtime with: "
                "python -m playwright install chromium"
            ) from exc
        try:
            page = browser.new_page(accept_downloads=True)
            page.set_default_timeout(60_000)
            response = page.goto(DOWNLOAD_HUB, wait_until="domcontentloaded")
            if response is None or response.status >= 400 or "ratelimited" in page.url:
                raise RuntimeError(
                    f"Redfin denied the public download page (HTTP "
                    f"{response.status if response else 'no response'}). No new data was imported; "
                    "retry later or use an official manual CSV with --local-file."
                )
            card = page.locator("#card-housing_market")
            card.get_by_role("button", name="Monthly", exact=True).wait_for()
            _activate(card, "Monthly")
            _activate(card, "Key Metrics")
            card.locator(".geo-type-select").select_option(label="Zip Codes")
            card.locator(".coverage-select").select_option(label="All Zip Codes")
            start = pd.Timestamp(start_date)
            card.locator(".start-b").select_option(str(start.year))
            card.locator(".start-a").select_option(f"{start.month:02}")
            if end_date:
                end = pd.Timestamp(end_date)
                card.locator(".end-b").select_option(str(end.year))
                card.locator(".end-a").select_option(f"{end.month:02}")
            else:
                card.locator(".end-b").select_option(_latest_option(card.locator(".end-b")))
                card.locator(".end-a").select_option(_latest_option(card.locator(".end-a")))
            _activate(card, "YoY")
            end = pd.Timestamp(
                f"{card.locator('.end-b').input_value()}-"
                f"{card.locator('.end-a').input_value()}-01"
            ) + pd.offsets.MonthEnd(0)
            # These selections are the public UI contract observed September 2026.
            # Stop if that contract changes instead of exporting a different product.
            if start > end:
                raise ValueError("Redfin start date exceeds the last selected release")
            with page.expect_download(timeout=timeout_seconds * 1000) as pending:
                card.get_by_role("button", name="Download Data", exact=True).click()
                tips = page.get_by_role("dialog").filter(has_text="Tips for using this data")
                tips.get_by_role("button", name="Download Data", exact=True).click()
            download = pending.value
            download.save_as(destination)
            return {
                "original_filename": download.suggested_filename,
                "selected_start_date": start.date().isoformat(),
                "selected_end_date": end.date().isoformat(),
                "request_query": "Housing Market Tracker|Monthly|Zip Codes|All Zip Codes|Key Metrics|YoY",
            }
        except Error as exc:
            raise RuntimeError(
                "Redfin browser download failed or timed out. Its public form may have "
                "changed or blocked the request. No new data was imported. "
                "Use an official CSV with --local-file if needed."
            ) from exc
        finally:
            browser.close()


def validate_zip_download(path: Path) -> dict[str, str | int]:
    """Reject wrong products, truncated dates, and malformed windows before archive."""
    header = pd.read_csv(path, nrows=0)
    if "MEDIAN DAYS ON MARKET YOY (DAYS)" in header.columns:
        raise ValueError(
            "Redfin's current ZIP download changes median days-on-market YoY "
            "from percent to days. It cannot populate the existing percentage-point "
            "variable. A versioned variable and source-contract migration is required; "
            "the active database has not been changed."
        )
    validate_redfin_zip_schema(tuple(header.columns))
    with duckdb.connect() as connection:
        connection.read_csv(str(path), header=True, all_varchar=True).create_view("source")
        result = connection.execute(
            '''SELECT count(*),
                min(try_cast("PERIOD BEGIN" AS DATE)),
                max(try_cast("PERIOD END" AS DATE)),
                min(try_cast("LAST UPDATED" AS DATE)),
                max(try_cast("LAST UPDATED" AS DATE)),
                count(*) FILTER (WHERE "FREQUENCY" IS DISTINCT FROM 'Rolling 3 Months'
                    OR "REGION TYPE" IS DISTINCT FROM 'Zip'
                    OR NOT regexp_full_match(coalesce("REGION NAME", ''), '[0-9]{5}')
                    OR try_cast("PERIOD BEGIN" AS DATE) IS NULL
                    OR try_cast("PERIOD END" AS DATE) IS NULL
                    OR try_cast("LAST UPDATED" AS DATE) IS NULL
                    OR try_cast("PERIOD BEGIN" AS DATE) !=
                        date_trunc('month', try_cast("PERIOD END" AS DATE)) - INTERVAL 2 MONTH
                    OR try_cast("PERIOD END" AS DATE) != last_day(try_cast("PERIOD END" AS DATE))),
                count(*) - count(DISTINCT ("REGION NAME", "PERIOD END"))
            FROM source'''
        ).fetchone()
    rows, first, last, first_update, last_update, invalid, duplicates = result
    if not rows or invalid or duplicates or first_update != last_update:
        raise ValueError(
            "Redfin ZIP download failed validation: "
            f"rows={rows}, invalid_windows_or_identifiers={invalid}, "
            f"duplicate_zip_periods={duplicates}, "
            f"provider_updates={first_update}/{last_update}"
        )
    return {
        "response_observation_count": rows,
        "response_first_observation": first.isoformat(),
        "response_latest_observation": last.isoformat(),
        "provider_last_updated": last_update.isoformat(),
        "source_frequency": "Rolling 3 Months",
    }


def download_redfin_zip(
    config: ProjectConfig,
    source: SourceConfig,
    *,
    force: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    downloader: Callable[..., dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Archive the provider's unmodified national CSV and record its selected range."""
    if source.source_id != SOURCE_ID:
        raise ValueError("The Redfin browser adapter only supports ZIP Key Metrics")
    start = start_date or source.selected_start_date or "2019-01-01"
    now = datetime.now(timezone.utc)
    manifest_path = config.root / "metadata/raw_file_manifest.csv"
    if manifest_path.exists() and not force:
        manifest = pd.read_csv(manifest_path, dtype=str).fillna("")
        for record in reversed(manifest.to_dict("records")):
            if (
                record["source_id"] == SOURCE_ID
                and record.get("retrieval_method") == "official_browser_download"
                and record.get("retrieved_at", "").startswith(now.date().isoformat())
                and record.get("selected_start_date") == start
                and record.get("requested_end_date", "") == (end_date or "latest_published")
            ):
                path = config.root / record["relative_path"]
                if path.is_file() and sha256_file(path) == record["checksum_sha256"]:
                    return {**record, "download_status": "reused_today"}
    raw_dir = config.root / "data/raw/redfin"
    raw_dir.mkdir(parents=True, exist_ok=True)
    token = now.strftime("%Y%m%dT%H%M%S%fZ")
    partial = raw_dir / f".{SOURCE_ID}_{token}.part"
    try:
        details = (downloader or _browser_download)(
            partial, start, end_date, int(config.pipeline.get("redfin_download_timeout_seconds", 900))
        )
        validation = validate_zip_download(partial)
        if validation["response_latest_observation"] != details["selected_end_date"]:
            raise ValueError("Redfin CSV does not reach the selected published end month")
        checksum = sha256_file(partial)
        destination = raw_dir / f"{SOURCE_ID}_{token}_{checksum[:12]}.csv"
        if destination.exists():
            raise FileExistsError(f"Refusing to replace raw archive: {destination.name}")
        partial.rename(destination)
    finally:
        if partial.exists():
            partial.unlink()
    record = {
        "source_id": SOURCE_ID,
        "provider": source.provider,
        "dataset_name": source.dataset_name,
        "source_page_url": DOWNLOAD_HUB,
        "download_url": DOWNLOAD_HUB,
        "native_frequency": source.native_frequency,
        "geography_levels": "|".join(source.geography_levels),
        "retrieved_at": now.isoformat(),
        "data_vintage": validation["provider_last_updated"],
        "source_filename": destination.name,
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(config.root).as_posix(),
        "byte_size": destination.stat().st_size,
        "checksum_sha256": checksum,
        "retrieval_method": "official_browser_download",
        "requested_end_date": end_date or "latest_published",
        "rolling_window_months": 3,
        "license_or_use_notes": source.license_or_use_notes,
        "revision_notes": source.revision_notes,
        "revision_policy": source.revision_notes,
        "ingestion_status": "downloaded",
        **details,
        **validation,
        "source_last_updated": validation["provider_last_updated"],
    }
    append_csv(manifest_path, [record])
    return record
