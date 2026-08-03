"""Archive and rebuild the official HUD-USPS residential vacancy proxy.

Licensed API responses are written below ``data/restricted`` and are never
committed.  A checksummed provenance index and derived parquet tables are
created beside them.  Set ``HUD_USER_ACCESS_TOKEN`` before running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cleaning.hud_usps import normalize_hud_usps_quarterly_tables
BASE = "https://www.huduser.gov/hudapi/public"
RAW = ROOT / "data" / "restricted" / "hud_usps" / "official" / "raw"
DERIVED = ROOT / "data" / "restricted" / "hud_usps" / "official" / "derived"
MANIFEST = ROOT / "data" / "restricted" / "hud_usps" / "official" / "provenance_manifest.csv"
COUNTY_REFERENCE_URL = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"
COUNT_FIELDS = {
    "total_residential_addresses": "TOTAL_RESIDENTIAL_ADDRESSES",
    "short_term_vacant_residential_addresses": "STV_RESIDENTIAL_ADDRESSES",
    "long_term_vacant_residential_addresses": "LTV_RESIDENTIAL_ADDRESSES",
    "no_stat_residential_addresses": "NO_STAT_RESIDENTIAL_ADDRESSES",
    "active_residential_addresses": "Active_Residential_Addresses",
}


def quarters(start_year: int, end_year: int, end_month: int) -> list[tuple[int, int]]:
    return [
        (year, month)
        for year in range(start_year, end_year + 1)
        for month in (3, 6, 9, 12)
        if year < end_year or month <= end_month
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_json(session: requests.Session, url: str, params: dict[str, object], path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        response = session.get(url, params=params, timeout=240)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        payload = response.json()
        temp = path.with_suffix(path.suffix + ".part")
        temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(path)
        return payload
    raise RuntimeError(f"HUD request failed after retries: {url} {params}")


def archive(args: argparse.Namespace) -> list[dict[str, object]]:
    token = os.environ.get("HUD_USER_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HUD_USER_ACCESS_TOKEN is not set")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    records: list[dict[str, object]] = []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for index, (year, month) in enumerate(quarters(args.start_year, args.end_year, args.end_month), 1):
        params = {"type": 1, "year": year, "month": month, "stateid": "06"}
        path = RAW / "ncwm" / f"hud_usps_ncwm_ca_{year}_{month:02d}.json"
        payload = fetch_json(session, f"{BASE}/uspsncwm", params, path)
        if not isinstance(payload.get("results"), list):
            raise RuntimeError(f"NCWM response lacks results: {path}")
        records.append(record(path, "ncwm", params, retrieved_at, len(payload["results"])))
        print(f"[{index:02d}] {year}-{month:02d}: {len(payload['results']):,} rows", flush=True)

    params = {"type": 6, "query": "CA", "year": args.crosswalk_year, "quarter": args.crosswalk_quarter}
    path = RAW / "crosswalk" / f"hud_usps_tract_zip_ca_{args.crosswalk_year}_q{args.crosswalk_quarter}.json"
    payload = fetch_json(session, f"{BASE}/usps", params, path)
    crosswalk_rows = extract_crosswalk_results(payload)
    records.append(record(path, "tract_zip_crosswalk", params, retrieved_at, len(crosswalk_rows)))
    reference_path = RAW / "reference" / "census_national_county2020.txt"
    if not reference_path.exists():
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        response = session.get(COUNTY_REFERENCE_URL, timeout=120)
        response.raise_for_status()
        reference_path.write_bytes(response.content)
    reference_rows = pd.read_csv(reference_path, sep="|", dtype=str)
    records.append({
        **record(reference_path, "census_county_reference", {}, retrieved_at, len(reference_rows)),
        "request_url": COUNTY_REFERENCE_URL,
    })
    pd.DataFrame(records).to_csv(MANIFEST, index=False)
    return records


def record(path: Path, dataset: str, params: dict[str, object], retrieved_at: str, rows: int) -> dict[str, object]:
    # The archive file's mtime is set when the response is first persisted and
    # remains stable on resumable cached runs; do not relabel cached bytes with
    # the time of a later manifest rebuild.
    archived_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    return {
        "dataset": dataset,
        "relative_path": path.relative_to(ROOT).as_posix(),
        "request_url": f"{BASE}/{'uspsncwm' if dataset == 'ncwm' else 'usps'}",
        "request_parameters": json.dumps(params, sort_keys=True),
        "retrieved_at_utc": archived_at,
        "byte_size": path.stat().st_size,
        "sha256": sha256(path),
        "row_count": rows,
        "license_scope": "HUD-USPS authorized API; restricted, do not commit",
    }


def extract_crosswalk_results(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, list) and data:
        results = data[0].get("results")
    elif isinstance(data, dict):
        results = data.get("results")
    else:
        results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("Crosswalk response lacks results")
    return results


def numeric(frame: pd.DataFrame, source: str) -> pd.Series:
    return pd.to_numeric(frame[source], errors="coerce")


def build(args: argparse.Namespace) -> None:
    pieces = []
    for year, month in quarters(args.start_year, args.end_year, args.end_month):
        path = RAW / "ncwm" / f"hud_usps_ncwm_ca_{year}_{month:02d}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        frame = pd.DataFrame(payload["results"])
        frame["quarter_end"] = pd.Period(f"{year}-{month:02d}", freq="Q").end_time.normalize()
        frame["tract_geoid"] = frame["TRACT ID"].astype("string").str.zfill(11)
        frame["county_fips"] = frame["STCNTY"].astype("string").str.zfill(5)
        frame["county_name"] = frame["COUNTY_NAME"].astype("string")
        for target, source in COUNT_FIELDS.items():
            frame[target] = numeric(frame, source)
        pieces.append(frame[["quarter_end", "tract_geoid", "county_fips", "county_name", *COUNT_FIELDS]])
    ca_tract = pd.concat(pieces, ignore_index=True)
    la_tract = ca_tract.loc[ca_tract["county_fips"].eq("06037")].copy()
    add_derived(la_tract)
    la_tract["is_modeled_geography"] = False

    county = ca_tract.groupby(["quarter_end", "county_fips"], as_index=False)[list(COUNT_FIELDS)].sum(min_count=1)
    reference = pd.read_csv(
        RAW / "reference" / "census_national_county2020.txt",
        sep="|",
        dtype=str,
    )
    reference = reference.loc[reference["STATEFP"].eq("06")].copy()
    reference["county_fips"] = reference["STATEFP"] + reference["COUNTYFP"]
    names = reference.set_index("county_fips")["COUNTYNAME"].map(
        lambda value: value if value.endswith("County") else f"{value} County"
    )
    county["county_name"] = county["county_fips"].map(names)
    add_derived(county)
    county["is_modeled_geography"] = False

    cross_path = RAW / "crosswalk" / f"hud_usps_tract_zip_ca_{args.crosswalk_year}_q{args.crosswalk_quarter}.json"
    cross = pd.DataFrame(extract_crosswalk_results(json.loads(cross_path.read_text(encoding="utf-8"))))
    # For crosswalk type 6 HUD returns ``tract`` as the input geography and
    # ``geoid`` as the related ZIP code (rather than naming that field ``zip``).
    tract_column = "tract"
    zip_column = "geoid"
    cross["tract_geoid"] = cross[tract_column].astype("string").str.zfill(11)
    cross["zip_code"] = cross[zip_column].astype("string").str.zfill(5)
    cross["res_ratio"] = pd.to_numeric(cross["res_ratio"], errors="coerce")
    cross = cross.loc[cross["tract_geoid"].str.startswith("06037") & cross["res_ratio"].gt(0)]
    allocated = la_tract.merge(cross[["tract_geoid", "zip_code", "res_ratio"]], on="tract_geoid", how="inner")
    allocated["source_incomplete"] = allocated[list(COUNT_FIELDS)].isna().any(axis=1)
    for column in COUNT_FIELDS:
        allocated[column] = allocated[column] * allocated["res_ratio"]
    zip_frame = allocated.groupby(["quarter_end", "zip_code"], as_index=False)[list(COUNT_FIELDS)].sum(min_count=1)
    diagnostics = allocated.groupby(["quarter_end", "zip_code"], as_index=False).agg(
        source_tract_count=("tract_geoid", "nunique"),
        crosswalk_relationship_count=("tract_geoid", "size"),
        source_incomplete_allocations=("source_incomplete", "sum"),
    )
    zip_frame = zip_frame.merge(diagnostics, on=["quarter_end", "zip_code"], validate="one_to_one")
    add_derived(zip_frame)
    zip_frame["county_fips"] = "06037"
    zip_frame["county_name"] = "Los Angeles County"
    zip_frame["geography_scope"] = "Los Angeles County portion"
    zip_frame["crosswalk_vintage"] = f"HUD-USPS {args.crosswalk_year} Q{args.crosswalk_quarter} tract-to-ZIP"
    zip_frame["is_modeled_geography"] = True
    zip_frame["low_address_count_flag"] = zip_frame["total_residential_addresses"].lt(25)
    zip_frame["zip_city"] = zip_frame["zip_code"].map(
        cross.drop_duplicates("zip_code").set_index("zip_code")["city"]
    ).fillna("Unknown")

    DERIVED.mkdir(parents=True, exist_ok=True)
    county.to_parquet(DERIVED / "ca_county_quarterly_usps_residential_vacancy_proxy.parquet", index=False)
    la_tract.to_parquet(DERIVED / "la_tract_quarterly_usps_residential_vacancy_proxy.parquet", index=False)
    zip_frame.to_parquet(DERIVED / "la_zip_quarterly_usps_residential_vacancy_proxy_modeled.parquet", index=False)
    normalized = normalize_hud_usps_quarterly_tables(county, la_tract, zip_frame)
    observations = normalized.observations.copy()
    observations["provenance_status"] = "official_authorized_api"
    observations["transformation_id"] = "hud_usps_official_archive_to_long_v1"
    observations["quality_flag"] = "official_restricted"
    clean_path = ROOT / "data" / "clean" / "hud_usps_ncwm_quarterly.parquet"
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    observations.to_parquet(clean_path, index=False)
    normalized.warnings.to_csv(DERIVED / "validation_warnings.csv", index=False)
    summary = pd.DataFrame([
        {"table": "county", "rows": len(county), "geographies": county["county_fips"].nunique(), "start": county["quarter_end"].min(), "end": county["quarter_end"].max()},
        {"table": "la_tract", "rows": len(la_tract), "geographies": la_tract["tract_geoid"].nunique(), "start": la_tract["quarter_end"].min(), "end": la_tract["quarter_end"].max()},
        {"table": "la_zip_county_part", "rows": len(zip_frame), "geographies": zip_frame["zip_code"].nunique(), "start": zip_frame["quarter_end"].min(), "end": zip_frame["quarter_end"].max()},
    ])
    summary.to_csv(DERIVED / "build_summary.csv", index=False)
    print(summary.to_string(index=False))


def add_derived(frame: pd.DataFrame) -> None:
    frame["vacant_residential_addresses"] = frame["short_term_vacant_residential_addresses"] + frame["long_term_vacant_residential_addresses"]
    total = frame["total_residential_addresses"].where(frame["total_residential_addresses"].gt(0))
    frame["usps_residential_vacancy_rate"] = frame["vacant_residential_addresses"] / total
    frame["usps_residential_no_stat_rate"] = frame["no_stat_residential_addresses"] / total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--end-month", type=int, default=3)
    parser.add_argument("--crosswalk-year", type=int, default=2026)
    parser.add_argument("--crosswalk-quarter", type=int, default=1)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    if not args.build_only:
        archive(args)
    build(args)


if __name__ == "__main__":
    main()
