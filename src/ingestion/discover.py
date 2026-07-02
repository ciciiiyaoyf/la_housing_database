"""Verify configured source endpoints and publish a discovery report."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from src.common.config import ProjectConfig


def discover_sources(
    config: ProjectConfig,
    source_filter: str | None = None,
    offline: bool = False,
) -> pd.DataFrame:
    logger = logging.getLogger("la_housing.discover")
    records: list[dict] = []
    timeout = int(config.pipeline["request_timeout_seconds"])
    for source in config.selected_sources(source_filter):
        status = "configured"
        detail = source.manual_reason
        checked_at = datetime.now(timezone.utc).isoformat()
        if not offline:
            try:
                response = requests.head(
                    source.download_url,
                    timeout=timeout,
                    allow_redirects=True,
                    headers={
                        "User-Agent": (
                            "UCLA housing research data pipeline "
                            "yifan@g.ucla.edu"
                        )
                    },
                )
                status = (
                    "verified"
                    if response.ok or response.status_code in {400, 403, 405}
                    else f"http_{response.status_code}"
                )
                detail = f"HEAD {response.status_code}; {detail}".strip("; ")
            except requests.RequestException as exc:
                status = "unreachable"
                detail = f"{type(exc).__name__}: {exc}"
                logger.warning("Discovery failed for %s: %s", source.source_id, exc)
        records.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "dataset_name": source.dataset_name,
                "source_page_url": source.source_page_url,
                "download_url": source.download_url,
                "native_frequency": source.native_frequency,
                "geography_levels": "|".join(source.geography_levels),
                "file_format": source.file_format,
                "endpoint_verified_at": config.verified_at,
                "checked_at": checked_at,
                "discovery_status": status,
                "auto_download": source.auto_download,
                "manual_reason": source.manual_reason,
                "manual_filename": source.manual_filename,
                "selected_start_date": source.selected_start_date,
                "selected_end_date": source.selected_end_date,
                "expected_columns": "|".join(source.expected_columns),
                "series_ids": "|".join(source.series_ids),
                "rolling_window_months": source.rolling_window_months,
                "methodology_notes": source.methodology_notes,
                "license_or_use_notes": source.license_or_use_notes,
                "revision_notes": source.revision_notes,
                "discovery_detail": detail,
            }
        )
    frame = pd.DataFrame(records)
    frame.to_csv(
        config.root / "metadata" / "source_discovery.csv", index=False
    )
    return frame
