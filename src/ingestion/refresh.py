"""Strict newest-eligible source refresh with an explicit intervention report."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from src.common.config import ProjectConfig, SourceConfig
from src.common.io import latest_manifest_rows, sha256_file
from src.ingestion.download import download_sources


REFRESH_STATUS_COLUMNS = (
    "refresh_run_id",
    "attempted_at",
    "source_id",
    "provider",
    "dataset_name",
    "native_frequency",
    "geography_levels",
    "auto_download",
    "access_method",
    "source_page_url",
    "download_url",
    "refresh_status",
    "is_blocking",
    "action_required",
    "prior_retrieved_at",
    "latest_retrieved_at",
    "latest_source_vintage",
    "latest_observation_or_archive_period",
    "latest_raw_path",
    "latest_checksum_sha256",
    "archive_verified",
    "prior_clean_path",
    "prior_clean_row_count",
    "prior_clean_first_observation",
    "prior_clean_latest_observation",
    "refreshed_clean_path",
    "refreshed_clean_row_count",
    "refreshed_clean_first_observation",
    "refreshed_clean_latest_observation",
    "clean_refresh_status",
    "clean_refresh_detail",
    "detail",
)


class SourceRefreshBlocked(RuntimeError):
    """Raised after the machine-readable report records blocking source failures."""


_CLEAN_DATE_COLUMNS = (
    "observation_date",
    "period_end",
    "date",
    "month",
    "reference_date",
)
_CLEAN_BASELINE_COLUMNS = (
    "source_id",
    "clean_path",
    "row_count",
    "first_observation",
    "latest_observation",
    "accepted_at",
)


def _clean_path(root: Path, source_id: str) -> Path:
    filename = (
        "redfin_zip_monthly_la_county.parquet"
        if source_id == "redfin_zip_monthly"
        else f"{source_id}.parquet"
    )
    return root / "data" / "clean" / filename


def _clean_signature(root: Path, source_id: str) -> dict[str, object]:
    """Read a compact clean-table endpoint signature without loading its values."""

    path = _clean_path(root, source_id)
    relative = path.relative_to(root).as_posix()
    result: dict[str, object] = {
        "path": relative,
        "exists": path.is_file(),
        "row_count": None,
        "first_observation": "",
        "latest_observation": "",
        "error": "",
    }
    if not path.is_file():
        return result
    try:
        parquet = pq.ParquetFile(path)
        result["row_count"] = int(parquet.metadata.num_rows)
        date_column = next(
            (column for column in _CLEAN_DATE_COLUMNS if column in parquet.schema.names),
            None,
        )
        if date_column is not None and parquet.metadata.num_rows:
            values = pd.to_datetime(
                parquet.read(columns=[date_column]).column(date_column).to_pandas(),
                errors="coerce",
            ).dropna()
            if not values.empty:
                result["first_observation"] = values.min().isoformat()
                result["latest_observation"] = values.max().isoformat()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _signature_fields(prefix: str, signature: dict[str, object]) -> dict[str, object]:
    return {
        f"{prefix}_clean_path": signature["path"],
        f"{prefix}_clean_row_count": signature["row_count"],
        f"{prefix}_clean_first_observation": signature["first_observation"],
        f"{prefix}_clean_latest_observation": signature["latest_observation"],
    }


def _load_clean_baselines(root: Path) -> dict[str, dict[str, object]]:
    path = root / "metadata" / "source_clean_coverage_baseline.csv"
    if not path.is_file():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    if not set(_CLEAN_BASELINE_COLUMNS).issubset(frame.columns):
        raise ValueError(f"Malformed clean coverage baseline: {path}")
    if frame["source_id"].duplicated().any():
        raise ValueError(f"Duplicate clean coverage baseline source IDs: {path}")
    return {
        str(row["source_id"]): {
            "path": str(row["clean_path"]),
            "exists": True,
            "row_count": (
                int(row["row_count"]) if str(row["row_count"]).strip() else None
            ),
            "first_observation": str(row["first_observation"]),
            "latest_observation": str(row["latest_observation"]),
            "error": "",
        }
        for row in frame.to_dict("records")
    }


def _write_clean_baselines(
    root: Path,
    signatures: dict[str, dict[str, object]],
) -> Path:
    path = root / "metadata" / "source_clean_coverage_baseline.csv"
    existing = (
        pd.read_csv(path, dtype=str).fillna("")
        if path.is_file()
        else pd.DataFrame(columns=_CLEAN_BASELINE_COLUMNS)
    )
    retained = existing.loc[~existing["source_id"].isin(signatures)].copy()
    accepted_at = datetime.now(timezone.utc).isoformat()
    incoming = pd.DataFrame(
        [
            {
                "source_id": source_id,
                "clean_path": signature["path"],
                "row_count": signature["row_count"],
                "first_observation": signature["first_observation"],
                "latest_observation": signature["latest_observation"],
                "accepted_at": accepted_at,
            }
            for source_id, signature in sorted(signatures.items())
        ],
        columns=_CLEAN_BASELINE_COLUMNS,
    )
    result = pd.concat([retained, incoming], ignore_index=True).sort_values(
        "source_id"
    )
    temp = path.with_suffix(path.suffix + ".tmp")
    result.to_csv(temp, index=False)
    temp.replace(path)
    return path


def _latest_records(root: Path) -> dict[str, dict[str, str]]:
    frame = latest_manifest_rows(root)
    if frame.empty:
        return {}
    records: dict[str, dict[str, str]] = {}
    for _, row in frame.iterrows():
        record = {
            key: str(value)
            for key, value in row.to_dict().items()
            if pd.notna(value)
        }
        records[str(record["source_id"])] = record
    return records


def _archive_status(root: Path, record: dict[str, str] | None) -> tuple[str, bool]:
    if not record or not record.get("relative_path"):
        return "", False
    relative = str(record["relative_path"])
    path = root / relative
    if not path.is_file():
        return relative, False
    expected = str(record.get("checksum_sha256", "")).strip()
    if not expected:
        return relative, False
    return relative, sha256_file(path) == expected


def _completed_by_source(rows: list[dict]) -> dict[str, dict]:
    return {
        str(row["source_id"]): row
        for row in rows
        if row.get("source_id")
    }


def _latest_period(completed: dict | None, record: dict[str, str] | None) -> str:
    candidates = (
        "response_latest_observation",
        "archive_end",
        "selected_end_year",
        "request_end_year",
        "provider_last_updated",
        "source_last_updated",
        "provider_vintage",
        "data_vintage",
    )
    for source in (completed or {}, record or {}):
        for key in candidates:
            value = str(source.get(key, "")).strip()
            if value and value.lower() != "nan":
                return value
    return ""


def _action_for_manual_source(source: SourceConfig, *, has_archive: bool) -> str:
    if not has_archive:
        return (
            source.manual_reason
            or "Acquire and import the official provider file before rebuilding."
        )
    return (
        "Check the provider for a newer release. If one exists, import it with "
        f"python run_pipeline.py --stage download --source {source.source_id} "
        "--local-file PATH_TO_OFFICIAL_FILE, then rerun refresh."
    )


def _write_report(root: Path, frame: pd.DataFrame) -> Path:
    path = root / "metadata" / "source_refresh_status.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.reindex(columns=REFRESH_STATUS_COLUMNS).to_csv(temp, index=False)
    temp.replace(path)
    return path


def _provider_review_holds(config: ProjectConfig) -> dict[str, str]:
    """Validate explicit source freshness holds; empty reasons cannot waive review."""
    configured = config.pipeline.get("source_refresh_review_holds", {})
    if not isinstance(configured, Mapping):
        raise ValueError("source_refresh_review_holds must map source IDs to review reasons")
    known_sources = {source.source_id for source in config.sources}
    holds: dict[str, str] = {}
    for source_id, reason in configured.items():
        if not isinstance(source_id, str) or source_id not in known_sources:
            raise ValueError(f"Unknown source_refresh_review_holds source ID: {source_id!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"A nonempty provider-review reason is required for {source_id}")
        holds[source_id] = reason.strip()
    return holds


def refresh_latest_sources(
    config: ProjectConfig,
    *,
    source_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    local_file: str | None = None,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict], pd.DataFrame]:
    """Refresh auto sources, report manual sources, and stop on stale fallbacks.

    Automatically downloadable sources must complete successfully.  Manual or
    restricted sources may reuse an already checksummed archive, but the report
    keeps their current-release verification as an explicit user action.  A
    manual source with no valid archive is blocking.

    A configured provider-release review hold also blocks a successful archive:
    successful transfer/checksum verification alone does not certify freshness.
    """

    logger = logging.getLogger("la_housing.refresh")
    attempted = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = attempted.strftime("refresh_%Y%m%dT%H%M%S%fZ")
    selected = config.selected_sources(source_filter)
    review_holds = _provider_review_holds(config)
    before = _latest_records(config.root)
    accepted_clean = _load_clean_baselines(config.root)
    prior_clean = {
        source.source_id: (
            accepted_clean[source.source_id]
            if source.source_id in accepted_clean
            else _clean_signature(config.root, source.source_id)
        )
        for source in selected
    }
    completed, issues = download_sources(
        config,
        source_filter,
        True,
        False,
        start_date,
        end_date,
        local_file,
        refresh_latest=True,
    )
    after = _latest_records(config.root)
    completed_map = _completed_by_source(completed)
    issue_map = {str(row["source_id"]): row for row in issues}
    rows: list[dict] = []
    blocking_ids: list[str] = []
    manual_attention: list[str] = []

    for source in selected:
        prior = before.get(source.source_id)
        latest = after.get(source.source_id)
        relative_path, verified = _archive_status(config.root, latest)
        issue = issue_map.get(source.source_id)
        completed_row = completed_map.get(source.source_id)
        prior_retrieved = str((prior or {}).get("retrieved_at", ""))
        latest_retrieved = str((latest or {}).get("retrieved_at", ""))
        has_archive = bool(relative_path and verified)
        is_blocking = False
        action_required = ""
        detail = ""

        if issue is not None and source.auto_download:
            status = "automatic_refresh_failed"
            is_blocking = True
            action_required = str(issue.get("reason", "Retry or inspect the provider."))
            detail = action_required
        elif issue is not None and not source.auto_download:
            action_required = _action_for_manual_source(
                source, has_archive=has_archive
            )
            if has_archive and source_filter is None:
                status = "manual_check_required_existing_archive_reused"
                manual_attention.append(source.source_id)
                detail = str(issue.get("reason", source.manual_reason))
            else:
                status = "manual_refresh_required"
                is_blocking = True
                detail = str(issue.get("reason", source.manual_reason))
        elif not has_archive:
            status = "archive_missing_or_checksum_failed"
            is_blocking = True
            action_required = "Restore or reacquire a valid immutable raw archive."
            detail = "No checksum-valid latest raw archive is available."
        elif latest_retrieved and latest_retrieved != prior_retrieved:
            status = "new_archive_acquired"
        else:
            status = "latest_archive_verified_or_period_archive_reused"

        if not is_blocking and has_archive and source.source_id in review_holds:
            status = "provider_release_review_required"
            is_blocking = True
            action_required = review_holds[source.source_id]
            detail = (
                "Checksum-valid raw archive retained, but the configured provider "
                "release review hold prevents certifying it as the latest eligible release."
            )

        if is_blocking:
            blocking_ids.append(source.source_id)
        rows.append(
            {
                "refresh_run_id": run_id,
                "attempted_at": attempted.isoformat(),
                "source_id": source.source_id,
                "provider": source.provider,
                "dataset_name": source.dataset_name,
                "native_frequency": source.native_frequency,
                "geography_levels": "|".join(source.geography_levels),
                "auto_download": bool(source.auto_download),
                "access_method": source.access_method,
                "source_page_url": source.source_page_url,
                "download_url": source.download_url,
                "refresh_status": status,
                "is_blocking": is_blocking,
                "action_required": action_required,
                "prior_retrieved_at": prior_retrieved,
                "latest_retrieved_at": latest_retrieved,
                "latest_source_vintage": str(
                    (latest or {}).get(
                        "provider_vintage", (latest or {}).get("data_vintage", "")
                    )
                ),
                "latest_observation_or_archive_period": _latest_period(
                    completed_row, latest
                ),
                "latest_raw_path": relative_path,
                "latest_checksum_sha256": str(
                    (latest or {}).get("checksum_sha256", "")
                ),
                "archive_verified": verified,
                **_signature_fields(
                    "prior", prior_clean[source.source_id]
                ),
                "refreshed_clean_path": "",
                "refreshed_clean_row_count": None,
                "refreshed_clean_first_observation": "",
                "refreshed_clean_latest_observation": "",
                "clean_refresh_status": "pending_strict_clean",
                "clean_refresh_detail": str(
                    prior_clean[source.source_id].get("error", "")
                ),
                "detail": detail,
            }
        )

    report = pd.DataFrame(rows).reindex(columns=REFRESH_STATUS_COLUMNS)
    report_path = _write_report(config.root, report)
    if manual_attention:
        logger.warning(
            "Manual/restricted sources require a provider-release check: %s. "
            "See %s",
            ", ".join(manual_attention),
            report_path,
        )
    if blocking_ids:
        raise SourceRefreshBlocked(
            "Refresh stopped because required source acquisition, archive verification, "
            f"or provider-release review is blocked for: {', '.join(blocking_ids)}. See {report_path}."
        )
    return completed, issues, report


def verify_refreshed_clean_outputs(
    config: ProjectConfig,
    *,
    source_filter: str | None = None,
) -> pd.DataFrame:
    """Block publication when a refreshed clean endpoint regresses or truncates.

    Refresh acquisition records the pre-clean signature in
    ``source_refresh_status.csv``.  This check runs only in the strict refresh
    workflow, after cleaning and before harmonization.  A provider revision may
    legitimately remove history, but it must be reviewed explicitly rather than
    silently becoming the active endpoint.

    This stage cannot clear an acquisition/provider-release hold. The hold must
    be resolved in configuration and a successful acquisition rerun must replace
    the blocked acquisition report before clean coverage can be accepted.
    """

    path = config.root / "metadata" / "source_refresh_status.csv"
    if not path.is_file():
        raise FileNotFoundError(
            "Strict clean verification requires metadata/source_refresh_status.csv"
        )
    report = pd.read_csv(path, dtype=str).fillna("")
    review_holds = _provider_review_holds(config)
    selected_ids = {
        source.source_id for source in config.selected_sources(source_filter)
    }
    missing_rows = selected_ids.difference(set(report["source_id"]))
    if missing_rows:
        raise SourceRefreshBlocked(
            "Refresh status is missing selected sources: "
            + ", ".join(sorted(missing_rows))
        )

    blocking: list[str] = []
    accepted_signatures: dict[str, dict[str, object]] = {}
    for index in report.index[report["source_id"].isin(selected_ids)]:
        source_id = str(report.at[index, "source_id"])
        current = _clean_signature(config.root, source_id)
        for key, value in _signature_fields("refreshed", current).items():
            report.at[index, key] = "" if value is None else str(value)

        acquisition_status = str(report.at[index, "refresh_status"])
        recorded_block = str(report.at[index, "is_blocking"]).strip().lower() in {"true", "1", "yes"}
        acquisition_blocked = recorded_block and acquisition_status != "clean_endpoint_regression_or_truncation"
        provider_hold = source_id in review_holds or acquisition_status == "provider_release_review_required"
        if acquisition_blocked or provider_hold:
            # Preserve original acquisition failures and their interventions.
            # A newly configured hold also applies to an older successful report.
            if not acquisition_blocked or acquisition_status == "provider_release_review_required":
                report.at[index, "refresh_status"] = "provider_release_review_required"
                if source_id in review_holds:
                    report.at[index, "action_required"] = review_holds[source_id]
            report.at[index, "is_blocking"] = "True"
            report.at[index, "clean_refresh_status"] = "blocked_by_acquisition_or_provider_review"
            report.at[index, "clean_refresh_detail"] = (
                "Clean coverage cannot clear the acquisition/provider-release block. "
                + str(report.at[index, "action_required"])
            )
            blocking.append(source_id)
            continue

        failures: list[str] = []
        if current["error"]:
            failures.append(f"clean Parquet is unreadable: {current['error']}")
        elif not current["exists"]:
            failures.append("strict cleaning did not produce the configured clean table")
        else:
            prior_rows = pd.to_numeric(
                pd.Series([report.at[index, "prior_clean_row_count"]]),
                errors="coerce",
            ).iloc[0]
            current_rows = current["row_count"]
            if pd.notna(prior_rows) and current_rows is not None:
                if int(current_rows) < int(prior_rows):
                    failures.append(
                        f"row count regressed from {int(prior_rows)} to "
                        f"{int(current_rows)}"
                    )

            prior_first = pd.to_datetime(
                report.at[index, "prior_clean_first_observation"],
                errors="coerce",
                utc=True,
            )
            prior_latest = pd.to_datetime(
                report.at[index, "prior_clean_latest_observation"],
                errors="coerce",
                utc=True,
            )
            current_first = pd.to_datetime(
                current["first_observation"], errors="coerce", utc=True
            )
            current_latest = pd.to_datetime(
                current["latest_observation"], errors="coerce", utc=True
            )
            if pd.notna(prior_first) and pd.isna(current_first):
                failures.append("clean output lost its observation-date coverage")
            elif pd.notna(prior_first) and current_first > prior_first:
                failures.append(
                    "earliest observation advanced from "
                    f"{prior_first.date()} to {current_first.date()}"
                )
            if pd.notna(prior_latest) and pd.isna(current_latest):
                failures.append("clean output lost its latest-observation endpoint")
            elif pd.notna(prior_latest) and current_latest < prior_latest:
                failures.append(
                    "latest observation regressed from "
                    f"{prior_latest.date()} to {current_latest.date()}"
                )

        if failures:
            detail = "; ".join(failures)
            report.at[index, "clean_refresh_status"] = (
                "endpoint_regression_or_truncation_blocked"
            )
            report.at[index, "clean_refresh_detail"] = detail
            report.at[index, "refresh_status"] = (
                "clean_endpoint_regression_or_truncation"
            )
            report.at[index, "is_blocking"] = "True"
            report.at[index, "action_required"] = (
                "Inspect the new provider vintage and cleaner output. Approve a "
                "documented provider retraction/method change explicitly before "
                "replacing prior clean coverage."
            )
            blocking.append(source_id)
        else:
            had_prior = bool(report.at[index, "prior_clean_path"]) and bool(
                report.at[index, "prior_clean_row_count"]
            )
            report.at[index, "clean_refresh_status"] = (
                "verified_no_endpoint_regression"
                if had_prior
                else "new_clean_output_verified"
            )
            report.at[index, "clean_refresh_detail"] = ""
            accepted_signatures[source_id] = current

    report = report.reindex(columns=REFRESH_STATUS_COLUMNS)
    _write_report(config.root, report)
    if blocking:
        raise SourceRefreshBlocked(
            "Refresh stopped because source acquisition/provider review remains blocked "
            "or clean endpoint coverage regressed/was truncated for: "
            f"{', '.join(sorted(blocking))}. See {path}."
        )
    _write_clean_baselines(config.root, accepted_signatures)
    return report


__all__ = [
    "REFRESH_STATUS_COLUMNS",
    "SourceRefreshBlocked",
    "refresh_latest_sources",
    "verify_refreshed_clean_outputs",
]
