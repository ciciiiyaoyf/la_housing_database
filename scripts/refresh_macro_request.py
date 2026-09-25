"""Refresh only the source contracts required by the September macro request.

Every source uses the normal strict refresh/immutable archive machinery.  This
runner does not publish a database or silently replace failed source data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.cleaning.runner import clean_sources
from src.common.config import load_config
from src.features.macro_financial import MACRO_SOURCE_IDS
from src.ingestion.refresh import refresh_latest_sources, verify_refreshed_clean_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", help="Restrict to a requested macro source; repeatable.")
    arguments = parser.parse_args()
    config = load_config(ROOT)
    selected = arguments.source or sorted(MACRO_SOURCE_IDS)
    if not set(selected).issubset(MACRO_SOURCE_IDS):
        parser.error("Only September-request macro sources are supported")
    report = ROOT / "metadata" / "macro_request_refresh_status.csv"
    prior = pd.read_csv(report, dtype=str) if report.exists() else pd.DataFrame()
    records = []
    for source_id in selected:
        print(f"Refreshing {source_id}", flush=True)
        row = {"source_id": source_id, "attempted_at": pd.Timestamp.now(tz="UTC").isoformat()}
        try:
            refresh_latest_sources(config, source_filter=source_id)
            clean_sources(config, source_filter=source_id, strict=True)
            verify_refreshed_clean_outputs(config, source_filter=source_id)
            frame = pd.read_parquet(ROOT / "data" / "clean" / f"{source_id}.parquet")
            nonnull = frame.loc[frame.value.notna()]
            row.update(status="succeeded", rows=len(frame), nonnull_rows=len(nonnull),
                       first_observation=nonnull.observation_date.min(),
                       latest_observation=nonnull.observation_date.max(), error="")
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        row["completed_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        records.append(row)
        retained = prior.loc[~prior.source_id.isin(selected)] if not prior.empty else prior
        pd.concat([retained, pd.DataFrame(records)], ignore_index=True).to_csv(report, index=False)
        print(row, flush=True)
    return int(any(row["status"] != "succeeded" for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
