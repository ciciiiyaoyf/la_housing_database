"""Refresh the six provider contracts used by the exact county/ZIP analytics.

Acquires and strictly cleans sources only; it does not publish analytical tables.
Raw archives and frozen reference/model parameters are never replaced.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.cleaning.runner import clean_sources
from src.common.config import load_config
from src.ingestion.refresh import refresh_latest_sources, verify_refreshed_clean_outputs

SOURCE_IDS = (
    "zillow_county_zhvi", "zillow_zip_zhvi",
    "zillow_county_zori_smoothed_sa", "zillow_zip_zori_smoothed_sa",
    "redfin_county_monthly", "bls_laus_la_county",
)


def main() -> int:
    config = load_config(ROOT)
    records = []
    for source in SOURCE_IDS:
        print(f"Refreshing {source}", flush=True)
        row = {"source_id": source, "attempted_at": pd.Timestamp.now(tz="UTC").isoformat()}
        try:
            refresh_latest_sources(config, source_filter=source)
            clean_sources(config, source_filter=source, strict=True)
            verify_refreshed_clean_outputs(config, source_filter=source)
            data = pd.read_parquet(ROOT / "data/clean" / f"{source}.parquet")
            valid = data.loc[data.value.notna()]
            row.update(status="succeeded", rows=len(data), nonnull_rows=len(valid),
                       latest_observation=valid.observation_date.max(), error="")
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        row["completed_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        records.append(row)
        pd.DataFrame(records).to_csv(ROOT / "metadata/county_analytics_refresh_status.csv", index=False)
        print(row, flush=True)
    return int(any(row["status"] != "succeeded" for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
