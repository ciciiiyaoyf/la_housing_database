"""Isolated orchestration for housing-finance Versions B and C.

The pipeline treats the frozen Version A parquet as a read-only anchor.  It
never calls a Version A writer, and it verifies the freeze manifest before and
after every build.  Version B is rebuilt atomically; Version C is stored as an
append-only information-cutoff partition.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

import pandas as pd
import yaml

from src.common.config import ROOT
from src.common.io import append_csv, sha256_file
from src.housing_finance_monthly.metadata import register_monthly_finance_build
from src.housing_finance_monthly.qa import monthly_finance_qa_results
from src.housing_finance_monthly.realtime import (
    add_source_availability,
    build_realtime_monthly,
    select_latest_eligible_snapshot,
    stepwise_indicator_values,
)
from src.housing_finance_monthly.retrospective import build_retrospective_monthly
from src.housing_finance_monthly.schema import (
    MONTHLY_FINANCE_COLUMNS,
    MONTHLY_LINEAGE_COLUMNS,
    VERSION_B_DATABASE_NAME,
    VERSION_C_DATABASE_NAME,
    monthly_finance_contract_errors,
    monthly_lineage_contract_errors,
    normalize_monthly_finance,
    normalize_monthly_lineage,
)
from src.housing_finance_monthly.sources import (
    read_all_ca_redfin_monthly,
    read_all_ca_zillow_zhvi,
    read_california_nmdb_upb,
)


VersionKey = Literal["B", "C"]
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPLETION_SCHEMA_VERSION = "housing_finance_output_pair_v1"


@dataclass
class MonthlyBuildResult:
    observations: pd.DataFrame
    lineage: pd.DataFrame
    qa_summary: pd.DataFrame
    qa_failures: pd.DataFrame
    diagnostics: pd.DataFrame
    output_path: Path | None = None
    lineage_path: Path | None = None


def _version_key(version: str) -> VersionKey:
    normalized = str(version).strip().upper()
    if normalized in {"B", "VERSION B"}:
        return "B"
    if normalized in {"C", "VERSION C"}:
        return "C"
    raise ValueError("version must be B or C")


def _inclusive_cutoff(value: str | date | pd.Timestamp) -> pd.Timestamp:
    """Return the final UTC nanosecond of a strict calendar cutoff date."""

    if isinstance(value, str):
        text = value.strip()
        if not _DATE_ONLY.fullmatch(text):
            raise ValueError("information_cutoff_date must be a date in YYYY-MM-DD form")
        parsed = pd.Timestamp(text)
    elif isinstance(value, datetime):
        parsed = pd.Timestamp(value)
        local = parsed.tz_localize(None) if parsed.tzinfo is not None else parsed
        if local != local.normalize():
            raise ValueError("information_cutoff_date cannot include a time of day")
    elif isinstance(value, date):
        parsed = pd.Timestamp(value)
    else:
        parsed = pd.to_datetime(value, errors="raise")
        parsed = pd.Timestamp(parsed)
        local = parsed.tz_localize(None) if parsed.tzinfo is not None else parsed
        if local != local.normalize():
            raise ValueError("information_cutoff_date cannot include a time of day")
    date_text = parsed.date().isoformat()
    start = pd.Timestamp(date_text, tz="UTC")
    return start + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)


def _config_path(root: Path, version: VersionKey) -> Path:
    filename = (
        "version_b_monthly_hist.yaml"
        if version == "B"
        else "version_c_monthly_rt.yaml"
    )
    return root / "config" / filename


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def verify_version_a_freeze(root: Path = ROOT) -> pd.DataFrame:
    """Verify every accepted Version A byte before B/C work begins."""

    manifest_path = root / "metadata" / "version_a_freeze_manifest.csv"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = pd.read_csv(manifest_path, dtype=str)
    required = {"relative_path", "sha256"}
    if not required.issubset(manifest.columns) or manifest.empty:
        raise ValueError("Version A freeze manifest is empty or malformed")
    if manifest["relative_path"].duplicated().any():
        raise ValueError("Version A freeze manifest has duplicate paths")

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for record in manifest.to_dict("records"):
        path = root / str(record["relative_path"])
        actual = sha256_file(path) if path.is_file() else None
        expected = str(record["sha256"]).strip().lower()
        status = "pass" if actual == expected else "fail"
        rows.append(
            {
                "relative_path": str(record["relative_path"]),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "status": status,
            }
        )
        if status == "fail":
            failures.append(str(record["relative_path"]))
    if failures:
        raise RuntimeError(f"Version A freeze mismatch: {failures}")
    return pd.DataFrame(rows)


def _manifest_asset(
    root: Path,
    source_id: str,
    *,
    manifest_name: str = "raw_file_manifest.csv",
    cutoff: pd.Timestamp | None = None,
) -> tuple[Path, dict[str, str]]:
    manifest_path = root / "metadata" / manifest_name
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    frame = pd.read_csv(manifest_path, dtype=str)
    selected = frame.loc[frame["source_id"].eq(source_id)].copy()
    if selected.empty:
        raise FileNotFoundError(f"No archived {source_id} asset")
    selected["_retrieved"] = pd.to_datetime(
        selected["retrieved_at"], errors="coerce", utc=True, format="mixed"
    )
    if cutoff is not None:
        selected = selected.loc[selected["_retrieved"].le(cutoff)]
    if selected.empty:
        raise FileNotFoundError(
            f"No archived {source_id} snapshot was retrieved by {cutoff}"
        )
    selected = selected.sort_values(
        ["_retrieved", "checksum_sha256", "source_filename"]
    )
    record = {
        key: str(value)
        for key, value in selected.iloc[-1].drop(labels="_retrieved").items()
        if pd.notna(value)
    }
    path = root / record["relative_path"]
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != record["checksum_sha256"].lower():
        raise RuntimeError(f"Archived source checksum mismatch: {path}")
    return path, record


def _source_metadata(record: Mapping[str, str]) -> dict[str, Any]:
    source_id = str(record["source_id"])
    release = str(record.get("release_date", "")).strip() or None
    return {
        "source_vintage": record.get("data_vintage", record.get("source_vintage")),
        "source_filename": record.get("source_filename"),
        "checksum_sha256": record.get("checksum_sha256"),
        "retrieved_at": record.get("retrieved_at"),
        "release_date": release,
        "source_release_dates": f"{source_id}={release or 'unavailable'}",
    }


def _load_indicators(
    root: Path,
    *,
    version: VersionKey,
    cutoff: pd.Timestamp | None,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]]:
    asset_cutoff = cutoff if version == "C" else None
    redfin_path, redfin_record = _manifest_asset(
        root, "redfin_county_monthly", cutoff=asset_cutoff
    )
    zillow_path, zillow_record = _manifest_asset(
        root, "zillow_county_zhvi", cutoff=asset_cutoff
    )
    redfin = read_all_ca_redfin_monthly(
        redfin_path, source_metadata=_source_metadata(redfin_record)
    )
    zillow = read_all_ca_zillow_zhvi(
        zillow_path, source_metadata=_source_metadata(zillow_record)
    )

    frames: dict[str, pd.DataFrame] = {"redfin": redfin, "zillow": zillow}
    records: dict[str, dict[str, Any]] = {
        "redfin": dict(redfin_record),
        "zillow": dict(zillow_record),
    }
    if version == "B":
        nmdb_path, nmdb_record = _manifest_asset(
            root,
            "fhfa_nmdb_ca_upb",
            manifest_name="version_a_raw_file_manifest.csv",
        )
        frames["nmdb"] = read_california_nmdb_upb(
            nmdb_path, source_metadata=_source_metadata(nmdb_record)
        )
        records["nmdb"] = dict(nmdb_record)
    else:
        pmms_path = root / "data" / "clean" / "freddie_mac_pmms.parquet"
        if not pmms_path.is_file():
            raise FileNotFoundError(pmms_path)
        pmms = pd.read_parquet(pmms_path)
        pmms["retrieved_at"] = pd.to_datetime(
            pmms["retrieved_at"], errors="coerce", utc=True
        )
        pmms = pmms.loc[pmms["retrieved_at"].le(cutoff)].copy()
        if pmms.empty:
            raise FileNotFoundError(
                f"No cleaned PMMS snapshot was retrieved by {cutoff}"
            )
        frames["pmms"] = pmms
        pmms_path_raw, pmms_record = _manifest_asset(
            root, "freddie_mac_pmms", cutoff=cutoff
        )
        if set(pmms["checksum_sha256"].dropna().astype(str)) != {
            pmms_record["checksum_sha256"]
        }:
            raise RuntimeError(
                "Cleaned PMMS snapshot does not match the cutoff-selected raw asset"
            )
        records["pmms"] = {
            **pmms_record,
            "_clean_path": str(pmms_path),
            "_clean_checksum_sha256": sha256_file(pmms_path),
        }
    return frames, records


def _benchmark_variable_id(variable_id: str, version: VersionKey) -> str:
    suffix = "_monthly_hist" if version == "B" else "_monthly_rt"
    proxy_suffix = f"_redfin_sales_value_proxy{suffix}"
    text = str(variable_id)
    if version == "C" and text == (
        "acs_implied_first_mortgage_balance_raw_composite_pmms_"
        "sensitivity_monthly_rt"
    ):
        return "acs_implied_first_mortgage_balance_raw_benchmark"
    if text.endswith(proxy_suffix):
        return text[: -len(proxy_suffix)] + "_benchmark"
    if not text.endswith(suffix):
        raise ValueError(f"Unexpected Version {version} variable ID: {text}")
    return text[: -len(suffix)] + "_benchmark"


def _cutoff_eligible_benchmark(
    benchmark: pd.DataFrame, cutoff: pd.Timestamp
) -> pd.DataFrame:
    """Return only Version A rows that were actually archived by ``cutoff``."""

    work = add_source_availability(benchmark)
    retrieved = pd.to_datetime(work["retrieved_at"], errors="coerce", utc=True)
    reference = pd.to_datetime(work["reference_date"], errors="coerce", utc=True)
    eligible = (
        work["availability_date"].le(cutoff)
        & retrieved.le(cutoff)
        & reference.le(cutoff)
    )
    result = work.loc[eligible].copy()
    if result.empty:
        raise ValueError(f"No Version A benchmark row is eligible by {cutoff}")
    return result


def _anchor_lookup(
    benchmark: pd.DataFrame, *, cutoff: pd.Timestamp | None = None
) -> pd.DataFrame:
    work = benchmark.copy()
    if "variable_id" not in work:
        work["variable_id"] = work["variable_name"]
    work["county_fips"] = work["county_fips"].astype("string").str.zfill(5)
    if cutoff is not None:
        work = _cutoff_eligible_benchmark(work, cutoff)
    else:
        work = add_source_availability(work)
    for column in ("reference_date", "source_period_start", "source_period_end"):
        work[column] = pd.to_datetime(work[column], errors="coerce")
    if "estimate_variant" not in work:
        work["estimate_variant"] = "central"
    work = work.sort_values(
        [
            "county_fips",
            "variable_id",
            "reference_date",
            "source_period_end",
            "availability_date",
            "estimate_variant",
        ],
        na_position="first",
    ).drop_duplicates(["county_fips", "variable_id"], keep="last")
    return work.set_index(["county_fips", "variable_id"], drop=False)


def _adapt_realtime_to_shared_schema(
    raw: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
    method_version: str,
) -> pd.DataFrame:
    """Add the shared B/C contract fields while preserving C vintage fields."""

    result = raw.copy()
    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result["benchmark_variable_id"] = result["variable_id"].map(
        lambda value: _benchmark_variable_id(value, "C")
    )
    result["information_cutoff_date"] = cutoff.tz_convert(None).normalize()
    result["method_version"] = method_version
    anchor_prefix = "__version_a_anchor__"
    anchors = _anchor_lookup(benchmark, cutoff=cutoff).reset_index(drop=True)
    anchors = anchors.rename(columns={"variable_id": "benchmark_variable_id"})
    anchor_columns = {
        column: f"{anchor_prefix}{column}"
        for column in anchors.columns
        if column not in {"county_fips", "benchmark_variable_id"}
    }
    anchors = anchors.rename(columns=anchor_columns)
    anchors[f"{anchor_prefix}present"] = True
    result["__adapter_row_order__"] = range(len(result))
    result = result.merge(
        anchors,
        on=["county_fips", "benchmark_variable_id"],
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values("__adapter_row_order__", kind="stable")

    missing_anchor = result[f"{anchor_prefix}present"].isna()
    if missing_anchor.any():
        missing = result.loc[
            missing_anchor, ["county_fips", "benchmark_variable_id"]
        ].drop_duplicates()
        keys = [tuple(row) for row in missing.itertuples(index=False, name=None)]
        raise KeyError(f"Missing Version A anchor for {keys}")

    def values(value: Any) -> pd.Series:
        if isinstance(value, pd.Series):
            return value.reindex(result.index)
        return pd.Series(value, index=result.index)

    def blank(value: pd.Series) -> pd.Series:
        text = value.astype("string")
        return value.isna() | text.str.strip().eq("")

    def coalesce(*candidates: Any) -> pd.Series:
        if not candidates:
            return pd.Series(pd.NA, index=result.index)
        # Object dtype keeps pandas from rejecting a valid fallback solely
        # because an all-null raw column was inferred as float/datetime.
        output = values(candidates[0]).astype("object").copy()
        for candidate in candidates[1:]:
            mask = blank(output)
            if not mask.any():
                break
            fallback = values(candidate)
            output.loc[mask] = fallback.loc[mask]
        return output

    def current(column: str) -> pd.Series:
        return result[column] if column in result else values(pd.NA)

    def anchor(column: str) -> pd.Series:
        name = f"{anchor_prefix}{column}"
        return result[name] if name in result else values(pd.NA)

    def fill(column: str, *fallbacks: Any) -> None:
        result[column] = coalesce(current(column), *fallbacks)

    availability = pd.to_datetime(
        current("availability_date"), errors="coerce", utc=True
    )
    derived_release = availability.dt.tz_convert(None).dt.normalize()
    state_fips = coalesce(anchor("state_fips"), "06").astype("string").str.zfill(2)
    geo_id = coalesce(
        anchor("geo_id"), "COUNTY_" + result["county_fips"].astype("string")
    )

    fill("state_fips", state_fips)
    fill("geo_id", geo_id)
    fill("reported_value", None)
    fill("margin_of_error", None)
    fill("source_frequency", anchor("source_frequency"), "modeled")
    fill("source_period_start", anchor("source_period_start"))
    fill("source_period_end", anchor("source_period_end"))
    fill("reference_date", anchor("reference_date"))
    fill("release_date", derived_release, anchor("release_date"))
    fill("source_release_dates", anchor("source_release_dates"))
    fill("source_geography", anchor("source_geography"), "county")
    fill("parameter_geography", anchor("parameter_geography"), "county")
    fill("payment_frequency", anchor("payment_frequency"))
    fill("is_imputed", False)
    fill("is_interpolated", False)
    fill("is_suppressed", False)
    fill("sample_n", anchor("sample_n"))
    fill("coverage_rate", anchor("coverage_rate"))
    fill("low_estimate", None)
    fill("central_estimate", current("value"))
    fill("high_estimate", None)
    fill("universe_definition", anchor("universe_definition"), "unknown")
    fill("source_vintage", anchor("source_vintage"))
    fill("retrieved_at", availability, anchor("retrieved_at"))
    fill("source_filename", anchor("source_filename"))
    fill("checksum_sha256", anchor("checksum_sha256"))
    fill("quality_flag", "modeled_realtime_value")
    fill("benchmark_vintage", anchor("benchmark_vintage"))
    fill("estimate_variant", "central")
    fill("source_id", anchor("source_id"))
    result["variable_name"] = result["variable_id"]

    internal = [
        column
        for column in result.columns
        if column.startswith(anchor_prefix) or column == "__adapter_row_order__"
    ]
    result = result.drop(columns=internal)

    for column in MONTHLY_FINANCE_COLUMNS:
        if column not in result:
            result[column] = None
    normalized = normalize_monthly_finance(result)
    extras = [column for column in result.columns if column not in MONTHLY_FINANCE_COLUMNS]
    output = pd.concat(
        [normalized.reset_index(drop=True), result[extras].reset_index(drop=True)],
        axis=1,
    )
    return output


def _asset_coverage(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    for column in ("month", "observation_date", "period_end"):
        if column in frame:
            dates = pd.to_datetime(frame[column], errors="coerce")
            if dates.notna().any():
                return dates.min().normalize(), dates.max().normalize()
    raise ValueError("Indicator asset has no usable observation dates")


def _manifest_release(record: Mapping[str, Any]) -> pd.Timestamp:
    release = pd.to_datetime(record.get("release_date"), errors="coerce")
    retrieved = pd.to_datetime(record.get("retrieved_at"), errors="coerce", utc=True)
    if pd.isna(retrieved):
        raise ValueError(f"Asset retrieval timestamp is missing: {record}")
    if pd.isna(release):
        release = retrieved.tz_convert(None).normalize()
    return pd.Timestamp(release).tz_localize(None).normalize()


def _freeze_hash(root: Path, relative_path: str) -> str:
    manifest = pd.read_csv(
        root / "metadata" / "version_a_freeze_manifest.csv", dtype=str
    )
    match = manifest.loc[manifest["relative_path"].eq(relative_path), "sha256"]
    if len(match) != 1:
        raise ValueError(f"Freeze manifest does not uniquely identify {relative_path}")
    return str(match.iloc[0]).lower()


_LINEAGE_CONTEXT_COLUMNS = [
    "lineage_id",
    "database_version",
    "information_cutoff_date",
    "county_fips",
    "month",
    "variable_id",
    "estimate_variant",
]


def _asset_lineage_rows(
    requests: list[dict[str, Any]],
    *,
    frame: pd.DataFrame,
    record: Mapping[str, Any],
    source_frequency: str,
) -> pd.DataFrame:
    """Materialize lineage for exact source points already proven to exist."""

    if not requests:
        return pd.DataFrame(columns=MONTHLY_LINEAGE_COLUMNS)
    result = pd.DataFrame(requests)
    result["reference_date"] = pd.to_datetime(
        result["reference_date"], errors="raise"
    ).dt.tz_localize(None)
    result = result.drop_duplicates(
        [*_LINEAGE_CONTEXT_COLUMNS, "input_role", "reference_date"]
    )
    start, end = _asset_coverage(frame)
    outside = result["reference_date"].lt(start) | result["reference_date"].gt(end)
    if outside.any():
        bad = result.loc[outside, ["input_role", "reference_date"]].head(10)
        raise ValueError(f"Lineage references outside asset coverage: {bad.to_dict('records')}")
    result["source_id"] = str(record["source_id"])
    result["source_vintage"] = str(
        record.get("data_vintage", record.get("source_vintage", "unknown"))
    )
    result["source_frequency"] = source_frequency
    result["source_period_start"] = start
    result["source_period_end"] = end
    result["release_date"] = _manifest_release(record)
    result["retrieved_at"] = pd.to_datetime(
        record.get("retrieved_at"), errors="raise", utc=True
    )
    result["source_filename"] = str(record["source_filename"])
    result["checksum_sha256"] = str(record["checksum_sha256"]).lower()
    return result[MONTHLY_LINEAGE_COLUMNS]


def _anchor_lineage_rows(
    requests: list[dict[str, Any]],
    *,
    benchmark: pd.DataFrame,
    root: Path,
    cutoff: pd.Timestamp | None,
) -> pd.DataFrame:
    """Materialize exact, cutoff-eligible Version A anchor inputs."""

    if not requests:
        return pd.DataFrame(columns=MONTHLY_LINEAGE_COLUMNS)
    requested = pd.DataFrame(requests).drop_duplicates(
        [*_LINEAGE_CONTEXT_COLUMNS, "input_role", "anchor_variable_id"]
    )
    anchors = _anchor_lookup(benchmark, cutoff=cutoff).reset_index(drop=True)
    keep = [
        "county_fips",
        "variable_id",
        "source_frequency",
        "source_period_start",
        "source_period_end",
        "reference_date",
        "release_date",
        "source_geography",
        "parameter_geography",
        "retrieved_at",
        "benchmark_vintage",
        "availability_date",
        "availability_date_basis",
    ]
    anchors = anchors[keep].rename(
        columns={
            "variable_id": "anchor_variable_id",
            **{
                column: f"anchor_{column}"
                for column in keep
                if column not in {"county_fips", "variable_id"}
            },
        }
    )
    result = requested.merge(
        anchors,
        on=["county_fips", "anchor_variable_id"],
        how="left",
        validate="many_to_one",
    )
    missing = result["anchor_reference_date"].isna()
    if missing.any():
        keys = result.loc[
            missing, ["county_fips", "anchor_variable_id", "input_role"]
        ].drop_duplicates()
        raise KeyError(f"Lineage anchor missing for {keys.to_dict('records')}")

    release = pd.to_datetime(result["anchor_release_date"], errors="coerce")
    conservative = ~result["anchor_availability_date_basis"].isin(
        ["provider_release_date", "release_date"]
    )
    release.loc[conservative] = pd.to_datetime(
        result.loc[conservative, "anchor_availability_date"],
        errors="coerce",
        utc=True,
    ).dt.tz_convert(None).dt.normalize()
    result["source_id"] = "version_a_housing_finance_benchmark"
    result["source_vintage"] = result["anchor_benchmark_vintage"].fillna(
        "Version A"
    )
    result["source_frequency"] = result["anchor_source_frequency"].fillna(
        "source period"
    )
    result["source_period_start"] = result["anchor_source_period_start"]
    result["source_period_end"] = result["anchor_source_period_end"]
    result["reference_date"] = result["anchor_reference_date"]
    result["release_date"] = release
    result["source_geography"] = result["anchor_source_geography"].fillna(
        "county"
    )
    result["parameter_geography"] = result[
        "anchor_parameter_geography"
    ].fillna("county")
    result["retrieved_at"] = result["anchor_retrieved_at"]
    result["source_filename"] = "housing_finance_benchmark.parquet"
    result["checksum_sha256"] = _freeze_hash(
        root, "data/final/housing_finance_benchmark.parquet"
    )
    return result[MONTHLY_LINEAGE_COLUMNS]


def _month_column(frame: pd.DataFrame) -> str:
    for column in ("month", "observation_date", "period_end"):
        if column in frame:
            return column
    raise ValueError("Indicator asset has no usable observation date")


def _point_index(frame: pd.DataFrame) -> tuple[set[pd.Timestamp], set[tuple[str, pd.Timestamp]]]:
    dates = pd.to_datetime(frame[_month_column(frame)], errors="coerce").dt.tz_localize(None)
    dates = dates.map(lambda value: value.to_period("M").to_timestamp("M") if pd.notna(value) else pd.NaT)
    all_dates = set(dates.dropna())
    if "county_fips" not in frame:
        return all_dates, set()
    counties = frame["county_fips"].astype("string").str.zfill(5)
    county_dates = {
        (str(county), pd.Timestamp(month))
        for county, month in zip(counties, dates)
        if pd.notna(county) and pd.notna(month)
    }
    return all_dates, county_dates


def _nmdb_brackets(frame: pd.DataFrame, target: pd.Timestamp) -> list[pd.Timestamp]:
    observed = sorted(
        set(pd.to_datetime(frame["month"], errors="coerce").dropna().dt.normalize())
    )
    target = pd.Timestamp(target).to_period("M").to_timestamp("M")
    if not observed or target < observed[0] or target > observed[-1]:
        return []
    if target in observed:
        return [target]
    lower = max((value for value in observed if value < target), default=None)
    upper = min((value for value in observed if value > target), default=None)
    return [value for value in (lower, upper) if value is not None]


def _term_years(variable_id: str) -> int:
    match = re.search(r"pmms_(20|25|30)y_", str(variable_id))
    if not match:
        raise ValueError(f"Cannot identify PMMS term in {variable_id}")
    return int(match.group(1))


def _build_lineage(
    observations: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    root: Path,
    version: VersionKey,
    indicator_frames: Mapping[str, pd.DataFrame],
    asset_records: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    work = normalize_monthly_finance(observations)
    cutoff: pd.Timestamp | None = None
    if version == "C":
        cutoffs = work["information_cutoff_date"].dropna().drop_duplicates()
        if len(cutoffs) != 1:
            raise ValueError("Version C lineage requires one information cutoff")
        cutoff = _inclusive_cutoff(pd.Timestamp(cutoffs.iloc[0]).date())
    anchors = _anchor_lookup(benchmark, cutoff=cutoff)

    anchor_requests: list[dict[str, Any]] = []
    redfin_requests: list[dict[str, Any]] = []
    nmdb_requests: list[dict[str, Any]] = []
    zillow_requests: list[dict[str, Any]] = []
    pmms_requests: list[dict[str, Any]] = []

    def context(row: pd.Series) -> dict[str, Any]:
        return {column: row[column] for column in _LINEAGE_CONTEXT_COLUMNS}

    def anchor_row(county: str, variable_id: str) -> pd.Series:
        try:
            return anchors.loc[(county, variable_id)]
        except KeyError as exc:
            raise KeyError(
                f"Missing cutoff-eligible Version A anchor {(county, variable_id)}"
            ) from exc

    def add_anchor(
        row_context: dict[str, Any], role: str, variable_id: str
    ) -> pd.Series:
        county = str(row_context["county_fips"])
        selected = anchor_row(county, variable_id)
        anchor_requests.append(
            {
                **row_context,
                "input_role": role,
                "anchor_variable_id": variable_id,
            }
        )
        return selected

    def add_point(
        target: list[dict[str, Any]],
        row_context: dict[str, Any],
        *,
        role: str,
        reference_date: Any,
        source_geography: str,
        parameter_geography: str,
    ) -> None:
        target.append(
            {
                **row_context,
                "input_role": role,
                "reference_date": pd.Timestamp(reference_date),
                "source_geography": source_geography,
                "parameter_geography": parameter_geography,
            }
        )

    redfin = indicator_frames["redfin"].copy()
    redfin["county_fips"] = redfin["county_fips"].astype("string").str.zfill(5)
    redfin_date_column = _month_column(redfin)
    redfin["_lineage_date"] = pd.to_datetime(
        redfin[redfin_date_column], errors="coerce"
    ).map(lambda value: pd.Timestamp(value).to_period("M").to_timestamp("M"))
    redfin_dates, redfin_county_dates = _point_index(redfin)

    zillow = indicator_frames["zillow"].copy()
    zillow["county_fips"] = zillow["county_fips"].astype("string").str.zfill(5)
    zillow_date_column = _month_column(zillow)
    zillow["_lineage_date"] = pd.to_datetime(
        zillow[zillow_date_column], errors="coerce"
    ).map(lambda value: pd.Timestamp(value).to_period("M").to_timestamp("M"))
    _, zillow_county_dates = _point_index(zillow)

    selected_redfin = redfin
    selected_hpi = zillow
    pmms_by_month: dict[pd.Timestamp, pd.Timestamp] = {}
    pmms_rates = pd.DataFrame()
    hpi_dates_by_county: dict[str, list[pd.Timestamp]] = {}
    if version == "C":
        assert cutoff is not None
        selected_redfin = select_latest_eligible_snapshot(redfin, cutoff)
        selected_redfin["_lineage_date"] = pd.to_datetime(
            selected_redfin[redfin_date_column], errors="coerce"
        ).map(lambda value: pd.Timestamp(value).to_period("M").to_timestamp("M"))
        selected_redfin = selected_redfin.loc[
            selected_redfin["_lineage_date"].le(cutoff.tz_convert(None))
        ].copy()
        redfin_dates = set(selected_redfin["_lineage_date"].dropna())
        redfin_county_dates = {
            (str(county).zfill(5), pd.Timestamp(reference))
            for county, reference in zip(
                selected_redfin["county_fips"],
                selected_redfin["_lineage_date"],
            )
            if pd.notna(reference)
        }

        selected_hpi = select_latest_eligible_snapshot(zillow, cutoff)
        selected_hpi["_lineage_date"] = pd.to_datetime(
            selected_hpi[zillow_date_column], errors="coerce"
        ).map(lambda value: pd.Timestamp(value).to_period("M").to_timestamp("M"))
        for county, group in selected_hpi.groupby("county_fips", sort=False):
            hpi_dates_by_county[str(county)] = sorted(
                set(group["_lineage_date"].dropna())
            )

        pmms_transforms = {
            "version_c_stepwise_pmms_rate_debt_sensitivity_v1",
            "version_c_hpi_updated_pmms_equity_v1",
            "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
            "version_c_pmms_composite_raw_debt_nowcast_v1",
        }
        pmms_months = sorted(
            work.loc[
                work["transformation_id"].astype(str).isin(pmms_transforms),
                "month",
            ].dropna().unique()
        )
        if pmms_months:
            rates = indicator_frames["pmms"].copy()
            if (
                "variable_id" in rates
                and rates["variable_id"]
                .eq("mortgage_rate_30y_monthly_median")
                .any()
            ):
                rates = rates.loc[
                    rates["variable_id"].eq("mortgage_rate_30y_monthly_median")
                ]
            pmms_rates = rates
            step = stepwise_indicator_values(pmms_rates, pmms_months, cutoff)
            step = step.sort_values("indicator_observation_date").drop_duplicates(
                "month", keep="last"
            )
            pmms_by_month = {
                pd.Timestamp(row.month): pd.Timestamp(row.indicator_observation_date)
                for row in step.itertuples()
            }

    def selected_pmms_point(target_month: pd.Timestamp) -> pd.Timestamp:
        """Resolve and cache the exact cutoff-eligible PMMS point used for a month."""

        reference = pd.Timestamp(target_month).to_period("M").to_timestamp("M")
        cached = pmms_by_month.get(reference)
        if cached is not None:
            return cached
        if version != "C" or cutoff is None:
            raise KeyError(f"No PMMS point selected for {reference}")
        rates = pmms_rates
        if rates.empty:
            rates = indicator_frames["pmms"].copy()
            if (
                "variable_id" in rates
                and rates["variable_id"]
                .eq("mortgage_rate_30y_monthly_median")
                .any()
            ):
                rates = rates.loc[
                    rates["variable_id"].eq("mortgage_rate_30y_monthly_median")
                ]
        step = stepwise_indicator_values(rates, [reference], cutoff)
        if step.empty:
            raise KeyError(f"No PMMS point selected for {reference}")
        selected = pd.Timestamp(
            step.sort_values("indicator_observation_date").iloc[-1][
                "indicator_observation_date"
            ]
        )
        pmms_by_month[reference] = selected
        return selected

    b_debt_transforms = {
        "version_b_single_anchor_nmdb_redfin_raw_debt_v1",
        "version_b_single_anchor_nmdb_redfin_pmms_debt_v1",
    }
    b_equity_transform = "version_b_hpi_updated_pmms_equity_v1"
    c_pmms_debt_transforms = {
        "version_c_stepwise_pmms_rate_debt_sensitivity_v1",
        "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
        "version_c_pmms_composite_raw_debt_nowcast_v1",
    }
    c_equity_transform = "version_c_hpi_updated_pmms_equity_v1"

    for _, row in work.iterrows():
        row_context = context(row)
        county = str(row["county_fips"])
        month = pd.Timestamp(row["month"])
        transformation = str(row["transformation_id"])
        target_anchor_id = str(row["benchmark_variable_id"])

        target_role = "output_definition_anchor"
        if transformation in {
            "version_b_reference_year_payment_repeat_v1",
            "version_b_hmda_annual_rate_repeat_v1",
            "version_c_latest_payment_carry_v1",
            "version_c_latest_hmda_rate_carry_v1",
        }:
            target_role = "carried_version_a_anchor"
        elif "redfin_purchase_value_proxy" in transformation:
            target_role = "annual_hmda_flow_anchor"
        elif transformation in b_debt_transforms:
            target_role = "debt_level_anchor"
        elif transformation in {
            "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
            "version_c_pmms_composite_raw_debt_nowcast_v1",
        }:
            target_role = "raw_debt_level_anchor"
        target_anchor = add_anchor(row_context, target_role, target_anchor_id)

        if "redfin_purchase_value_proxy" in transformation:
            period_field = (
                "source_period_start" if version == "B" else "source_period_end"
            )
            anchor_year = pd.Timestamp(target_anchor[period_field]).year
            annual = selected_redfin.loc[
                selected_redfin["county_fips"].eq(county)
                & selected_redfin["_lineage_date"].dt.year.eq(anchor_year)
            ].sort_values("_lineage_date")
            for reference in annual["_lineage_date"].dropna().drop_duplicates():
                add_point(
                    redfin_requests,
                    row_context,
                    role="redfin_annual_weight_denominator",
                    reference_date=reference,
                    source_geography=f"county {county}",
                    parameter_geography="county annual purchase-value weights",
                )
            if (county, month) in redfin_county_dates:
                add_point(
                    redfin_requests,
                    row_context,
                    role="redfin_monthly_weight_numerator",
                    reference_date=month,
                    source_geography=f"county {county}",
                    parameter_geography="county monthly purchase-value proxy",
                )

        if version == "B" and (
            transformation in b_debt_transforms or transformation == b_equity_transform
        ):
            if transformation == b_equity_transform:
                years = _term_years(str(row["variable_id"]))
                debt_anchor_id = (
                    "acs_implied_first_mortgage_balance_raw_pmms_"
                    f"{years}y_sensitivity_benchmark"
                )
                debt_anchor = add_anchor(
                    row_context, "accounting_debt_anchor", debt_anchor_id
                )
                mort_value = add_anchor(
                    row_context,
                    "mortgaged_owner_value_anchor",
                    "acs_aggregate_owner_value_mortgaged_benchmark",
                )
                add_anchor(
                    row_context,
                    "no_mortgage_owner_value_anchor",
                    "acs_aggregate_owner_value_no_mortgage_benchmark",
                )
                hpi_anchor_month = pd.Timestamp(
                    mort_value["reference_date"]
                ).to_period("M").to_timestamp("M")
                for role, reference in (
                    ("zillow_hpi_anchor", hpi_anchor_month),
                    ("zillow_hpi_target", month),
                ):
                    if (county, reference) in zillow_county_dates:
                        add_point(
                            zillow_requests,
                            row_context,
                            role=role,
                            reference_date=reference,
                            source_geography=f"county {county}",
                            parameter_geography="county HPI ratio",
                        )
            else:
                debt_anchor = target_anchor

            anchor_month = pd.Timestamp(
                debt_anchor["reference_date"]
            ).to_period("M").to_timestamp("M")
            for prefix, reference in (("current", month), ("anchor", anchor_month)):
                brackets = _nmdb_brackets(indicator_frames["nmdb"], reference)
                if len(brackets) == 1:
                    roles = [f"nmdb_{prefix}_observation"]
                else:
                    roles = [f"nmdb_{prefix}_lower", f"nmdb_{prefix}_upper"]
                for role, point in zip(roles, brackets):
                    add_point(
                        nmdb_requests,
                        row_context,
                        role=role,
                        reference_date=point,
                        source_geography="California",
                        parameter_geography="California all-mortgage UPB growth",
                    )
                if reference in redfin_dates:
                    add_point(
                        redfin_requests,
                        row_context,
                        role=f"redfin_{prefix}_share_denominator_slice",
                        reference_date=reference,
                        source_geography="California counties",
                        parameter_geography="California complete-county sales-share denominator",
                    )
                if (county, reference) in redfin_county_dates:
                    add_point(
                        redfin_requests,
                        row_context,
                        role=f"redfin_{prefix}_share_numerator",
                        reference_date=reference,
                        source_geography=f"county {county}",
                        parameter_geography="county sales-share numerator",
                    )

        if version == "C" and (
            transformation in c_pmms_debt_transforms
            or transformation == c_equity_transform
        ):
            # Both the term scenarios and their composite envelope are
            # capitalized from the carried aggregate-payment anchor.  The
            # composite additionally scales its term envelope to the eligible
            # Version A raw-debt level at that anchor's reference month.
            add_anchor(
                row_context,
                "aggregate_payment_anchor",
                "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
            )
            pmms_reference = selected_pmms_point(month)
            add_point(
                pmms_requests,
                row_context,
                role="pmms_latest_eligible_rate_point",
                reference_date=pmms_reference,
                source_geography="United States",
                parameter_geography="United States mortgage origination-rate sensitivity",
            )

            if transformation in {
                "version_c_composite_pmms_raw_debt_nowcast_sensitivity_v1",
                "version_c_pmms_composite_raw_debt_nowcast_v1",
            }:
                raw_reference_month = pd.Timestamp(
                    target_anchor["reference_date"]
                ).to_period("M").to_timestamp("M")
                add_point(
                    pmms_requests,
                    row_context,
                    role="pmms_reference_envelope_rate_point",
                    reference_date=selected_pmms_point(raw_reference_month),
                    source_geography="United States",
                    parameter_geography=(
                        "United States raw-debt reference-envelope rate sensitivity"
                    ),
                )

            if transformation == c_equity_transform:
                value_anchor_id = (
                    "acs_aggregate_owner_value_mortgaged_benchmark"
                    if "_mortgaged_" in str(row["variable_id"])
                    else "acs_aggregate_owner_value_all_benchmark"
                )
                value_anchor = add_anchor(
                    row_context, "owner_value_anchor", value_anchor_id
                )
                available_hpi = hpi_dates_by_county.get(county, [])
                base_limit = pd.Timestamp(
                    value_anchor["reference_date"]
                ).to_period("M").to_timestamp("M")
                target_limit = min(month, cutoff.tz_convert(None))
                base = [point for point in available_hpi if point <= base_limit]
                target = [point for point in available_hpi if point <= target_limit]
                if not base or not target:
                    raise KeyError(
                        f"No Zillow HPI anchor/target point for {(county, month)}"
                    )
                for role, reference in (
                    ("zillow_hpi_anchor", base[-1]),
                    ("zillow_hpi_target", target[-1]),
                ):
                    add_point(
                        zillow_requests,
                        row_context,
                        role=role,
                        reference_date=reference,
                        source_geography=f"county {county}",
                        parameter_geography="county HPI ratio",
                    )

    frames = [
        _anchor_lineage_rows(
            anchor_requests,
            benchmark=benchmark,
            root=root,
            cutoff=cutoff,
        ),
        _asset_lineage_rows(
            redfin_requests,
            frame=indicator_frames["redfin"],
            record=asset_records["redfin"],
            source_frequency="monthly",
        ),
        _asset_lineage_rows(
            zillow_requests,
            frame=indicator_frames["zillow"],
            record=asset_records["zillow"],
            source_frequency="monthly",
        ),
    ]
    if version == "B":
        frames.append(
            _asset_lineage_rows(
                nmdb_requests,
                frame=indicator_frames["nmdb"],
                record=asset_records["nmdb"],
                source_frequency="quarterly",
            )
        )
    else:
        frames.append(
            _asset_lineage_rows(
                pmms_requests,
                frame=indicator_frames["pmms"],
                record=asset_records["pmms"],
                source_frequency="weekly source aggregated to monthly median",
            )
        )
    lineage = pd.concat(
        [frame for frame in frames if not frame.empty], ignore_index=True
    )
    return normalize_monthly_lineage(lineage)


def _dictionary_frame(path: Path) -> pd.DataFrame:
    payload = _load_yaml(path)
    variables = payload.get("variables")
    if not isinstance(variables, list):
        raise ValueError(f"{path} must contain a variables list")
    return pd.DataFrame(variables)


def preflight(
    version: str,
    *,
    root: Path = ROOT,
    information_cutoff_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return machine-readable readiness results without writing final data."""

    key = _version_key(version)
    cutoff = (
        _inclusive_cutoff(information_cutoff_date)
        if information_cutoff_date is not None
        else None
    )
    if key == "C" and cutoff is None:
        raise ValueError("Version C requires information_cutoff_date")
    if key == "B" and cutoff is not None:
        raise ValueError("Version B does not accept information_cutoff_date")
    config = _load_yaml(_config_path(root, key))
    rows: list[dict[str, Any]] = []

    def record(check_id: str, severity: str, status: str, detail: str) -> None:
        rows.append(
            {
                "check_id": check_id,
                "severity": severity,
                "status": status,
                "detail": detail,
            }
        )

    try:
        frozen = verify_version_a_freeze(root)
        record(
            "version_a_freeze",
            "error",
            "pass",
            f"All {len(frozen)} frozen Version A files match.",
        )
    except Exception as exc:
        record("version_a_freeze", "error", "fail", str(exc))

    required_paths = [
        _resolve(root, config["benchmark_input_path"]),
        _resolve(root, config["variable_dictionary_path"]),
        _resolve(root, config["transformation_catalog_path"]),
    ]
    missing = [str(path) for path in required_paths if not path.is_file()]
    record(
        "required_configuration_and_anchor_files",
        "error",
        "fail" if missing else "pass",
        f"Missing: {missing}" if missing else "All required files exist.",
    )
    try:
        indicator_frames, _ = _load_indicators(root, version=key, cutoff=cutoff)
        detail = ", ".join(
            f"{name}={len(frame):,}" for name, frame in indicator_frames.items()
        )
        record("immutable_indicator_inputs", "error", "pass", detail)
    except Exception as exc:
        record("immutable_indicator_inputs", "error", "fail", str(exc))

    benchmark_path = _resolve(root, config["benchmark_input_path"])
    if benchmark_path.is_file():
        benchmark = pd.read_parquet(benchmark_path)
        calibrated = benchmark.loc[
            benchmark["variable_id"].eq(
                "acs_implied_first_mortgage_balance_cal_benchmark"
            ),
            "value",
        ]
        populated = int(calibrated.notna().sum())
        record(
            "calibrated_debt_anchor",
            "warning",
            "warn" if populated == 0 else "pass",
            (
                "No compatible calibrated debt anchor; canonical debt/equity "
                "will remain explicit nulls and PMMS outputs remain sensitivities."
                if populated == 0
                else f"{populated} calibrated county anchors available."
            ),
        )
        acs_vintages = benchmark.loc[
            benchmark["variable_id"].str.startswith("acs_", na=False),
            "benchmark_vintage",
        ].dropna().nunique()
        record(
            "acs_anchor_vintages",
            "warning",
            "warn" if acs_vintages < 2 else "pass",
            f"Current frozen Version A contains {acs_vintages} ACS anchor vintage(s).",
        )
    return pd.DataFrame(rows)


def _temporary_path(path: Path, label: str) -> Path:
    return path.with_name(f".{path.name}.{label}.{uuid4().hex}.tmp")


def _stage_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path, "stage")
    try:
        frame.to_parquet(temporary, index=False)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return temporary


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    temporary = _stage_parquet(frame, path)
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path, "stage")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _stage_json(payload: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path, "stage")
    try:
        temporary.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return temporary


def _atomic_json(payload: Mapping[str, Any], path: Path) -> None:
    temporary = _stage_json(payload, path)
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parquet_matches(frame: pd.DataFrame, path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        existing = pd.read_parquet(path)
        pd.testing.assert_frame_equal(
            existing.reset_index(drop=True),
            frame.reset_index(drop=True),
            check_dtype=False,
            check_like=False,
        )
    except (AssertionError, OSError, ValueError):
        return False
    return True


def _completion_marker_path(version: VersionKey, output_path: Path) -> Path:
    if version == "C":
        return output_path.parent / "_COMPLETE.json"
    return output_path.with_suffix(".complete.json")


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _completion_payload(
    root: Path,
    *,
    version: VersionKey,
    output_path: Path,
    lineage_path: Path,
    observation_sha256: str,
    lineage_sha256: str,
    observation_rows: int,
    lineage_rows: int,
    cutoff: pd.Timestamp | None,
) -> dict[str, Any]:
    return {
        "schema_version": _COMPLETION_SCHEMA_VERSION,
        "database_version": version,
        "information_cutoff_date": (
            cutoff.date().isoformat() if cutoff is not None else None
        ),
        "observation_path": _relative(root, output_path),
        "observation_sha256": observation_sha256,
        "observation_rows": observation_rows,
        "lineage_path": _relative(root, lineage_path),
        "lineage_sha256": lineage_sha256,
        "lineage_rows": lineage_rows,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _require_completion_marker(
    root: Path,
    *,
    version: VersionKey,
    output_path: Path,
    lineage_path: Path,
    cutoff: pd.Timestamp | None,
) -> Path:
    marker_path = _completion_marker_path(version, output_path)
    if not marker_path.is_file():
        raise FileNotFoundError(f"Housing-finance output pair is not complete: {marker_path}")
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid completion marker: {marker_path}") from exc
    expected = {
        "schema_version": _COMPLETION_SCHEMA_VERSION,
        "database_version": version,
        "information_cutoff_date": (
            cutoff.date().isoformat() if cutoff is not None else None
        ),
        "observation_path": _relative(root, output_path),
        "lineage_path": _relative(root, lineage_path),
    }
    mismatched = [key for key, value in expected.items() if payload.get(key) != value]
    if mismatched:
        raise RuntimeError(
            f"Completion marker metadata mismatch for {marker_path}: {mismatched}"
        )
    if not output_path.is_file() or not lineage_path.is_file():
        raise RuntimeError(f"Completion marker references a missing output pair: {marker_path}")
    actual_observation = sha256_file(output_path)
    actual_lineage = sha256_file(lineage_path)
    if payload.get("observation_sha256") != actual_observation:
        raise RuntimeError(f"Completion marker observation checksum mismatch: {output_path}")
    if payload.get("lineage_sha256") != actual_lineage:
        raise RuntimeError(f"Completion marker lineage checksum mismatch: {lineage_path}")
    return marker_path


def _write_output_pair(
    root: Path,
    *,
    version: VersionKey,
    observations: pd.DataFrame,
    lineage: pd.DataFrame,
    output_path: Path,
    lineage_path: Path,
    cutoff: pd.Timestamp | None,
) -> Path:
    """Commit observations and lineage as one completion-gated file pair."""

    marker_path = _completion_marker_path(version, output_path)
    if marker_path.exists():
        _require_completion_marker(
            root,
            version=version,
            output_path=output_path,
            lineage_path=lineage_path,
            cutoff=cutoff,
        )
        exact = _parquet_matches(observations, output_path) and _parquet_matches(
            lineage, lineage_path
        )
        if exact:
            return marker_path
        if version == "C":
            raise FileExistsError(
                f"Append-only Version C output pair already differs: {output_path.parent}"
            )
    elif (
        output_path.is_file()
        and lineage_path.is_file()
        and _parquet_matches(observations, output_path)
        and _parquet_matches(lineage, lineage_path)
    ):
        payload = _completion_payload(
            root,
            version=version,
            output_path=output_path,
            lineage_path=lineage_path,
            observation_sha256=sha256_file(output_path),
            lineage_sha256=sha256_file(lineage_path),
            observation_rows=len(observations),
            lineage_rows=len(lineage),
            cutoff=cutoff,
        )
        _atomic_json(payload, marker_path)
        return marker_path

    if version == "C":
        for frame, path in ((observations, output_path), (lineage, lineage_path)):
            if path.exists() and not _parquet_matches(frame, path):
                raise FileExistsError(
                    f"Uncommitted Version C output already differs: {path}"
                )

    staged_observation = _stage_parquet(observations, output_path)
    try:
        staged_lineage = _stage_parquet(lineage, lineage_path)
    except Exception:
        if staged_observation.exists():
            staged_observation.unlink()
        raise
    observation_sha = (
        sha256_file(output_path)
        if version == "C" and output_path.is_file()
        else sha256_file(staged_observation)
    )
    lineage_sha = (
        sha256_file(lineage_path)
        if version == "C" and lineage_path.is_file()
        else sha256_file(staged_lineage)
    )
    payload = _completion_payload(
        root,
        version=version,
        output_path=output_path,
        lineage_path=lineage_path,
        observation_sha256=observation_sha,
        lineage_sha256=lineage_sha,
        observation_rows=len(observations),
        lineage_rows=len(lineage),
        cutoff=cutoff,
    )
    staged_marker = _stage_json(payload, marker_path)

    if version == "C":
        created: list[Path] = []
        try:
            if output_path.exists():
                staged_observation.unlink()
            else:
                staged_observation.replace(output_path)
                created.append(output_path)
            if lineage_path.exists():
                staged_lineage.unlink()
            else:
                staged_lineage.replace(lineage_path)
                created.append(lineage_path)
            staged_marker.replace(marker_path)
        except Exception:
            for path in created:
                if path.exists():
                    path.unlink()
            raise
        finally:
            for path in (staged_observation, staged_lineage, staged_marker):
                if path.exists():
                    path.unlink()
        return marker_path

    final_paths = [marker_path, output_path, lineage_path]
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    rollback_failed = False
    try:
        for path in final_paths:
            if path.exists():
                backup = _temporary_path(path, "backup")
                path.replace(backup)
                backups[path] = backup
        staged_observation.replace(output_path)
        committed.append(output_path)
        staged_lineage.replace(lineage_path)
        committed.append(lineage_path)
        staged_marker.replace(marker_path)
        committed.append(marker_path)
    except Exception as exc:
        for path in committed:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    rollback_failed = True
        for path, backup in backups.items():
            if backup.exists():
                try:
                    backup.replace(path)
                except OSError:
                    rollback_failed = True
        if rollback_failed:
            preserved = [str(path) for path in backups.values() if path.exists()]
            raise RuntimeError(
                "Version B output-pair rollback failed; recoverable backups were "
                f"preserved: {preserved}"
            ) from exc
        raise
    finally:
        for path in (staged_observation, staged_lineage, staged_marker):
            if path.exists():
                path.unlink()
        if not rollback_failed:
            for backup in backups.values():
                if backup.exists():
                    backup.unlink()
    return marker_path


def _write_build_manifest(
    root: Path,
    *,
    version: VersionKey,
    config: Mapping[str, Any],
    asset_records: Mapping[str, Mapping[str, Any]],
    output_path: Path,
    lineage_path: Path,
    completion_path: Path,
    cutoff: pd.Timestamp | None,
) -> None:
    paths: list[tuple[str, Path]] = [
        ("version_a_benchmark", _resolve(root, config["benchmark_input_path"])),
        ("version_config", _config_path(root, version)),
        ("version_dictionary", _resolve(root, config["variable_dictionary_path"])),
        (
            "transformation_catalog",
            _resolve(root, config["transformation_catalog_path"]),
        ),
        ("observations", output_path),
        ("lineage", lineage_path),
        ("completion_marker", completion_path),
    ]
    for role, record in asset_records.items():
        paths.append((role, root / str(record["relative_path"])))
        clean_path = record.get("_clean_path")
        if clean_path:
            resolved_clean = _resolve(root, str(clean_path))
            expected_clean = str(record.get("_clean_checksum_sha256", "")).lower()
            actual_clean = sha256_file(resolved_clean)
            if expected_clean and actual_clean != expected_clean:
                raise RuntimeError(
                    f"Clean input changed during the build: {resolved_clean}"
                )
            paths.append((f"{role}_clean_input", resolved_clean))
    generated_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "database_version": version,
            "information_cutoff_date": (
                cutoff.date().isoformat() if cutoff is not None else None
            ),
            "input_role": role,
            "relative_path": _relative(root, path),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "generated_at": generated_at,
        }
        for role, path in paths
    ]
    manifest_path = _resolve(root, config["build_manifest_path"])
    incoming = pd.DataFrame(rows)
    key_columns = ["information_cutoff_date", "input_role", "relative_path"]
    immutable_columns = [
        "database_version",
        "information_cutoff_date",
        "input_role",
        "relative_path",
        "sha256",
        "byte_size",
    ]

    def signature(frame: pd.DataFrame) -> pd.DataFrame:
        required = set(immutable_columns)
        if not required.issubset(frame.columns):
            missing = sorted(required - set(frame.columns))
            raise ValueError(f"Build manifest is missing immutable columns: {missing}")
        normalized = frame[immutable_columns].fillna("").astype(str)
        return normalized.sort_values(key_columns, kind="stable").reset_index(drop=True)

    if incoming.duplicated(key_columns).any():
        raise ValueError("Incoming build manifest contains duplicate immutable keys")
    if manifest_path.exists():
        existing = pd.read_csv(manifest_path, dtype=str)
        signature(existing)
        if existing.duplicated(key_columns).any():
            raise ValueError(f"Build manifest contains duplicate keys: {manifest_path}")
        if version == "C":
            cutoff_text = cutoff.date().isoformat() if cutoff is not None else ""
            same_cutoff = existing["information_cutoff_date"].fillna("").eq(
                cutoff_text
            )
            if same_cutoff.any():
                try:
                    pd.testing.assert_frame_equal(
                        signature(existing.loc[same_cutoff]),
                        signature(incoming),
                        check_dtype=False,
                    )
                except AssertionError as exc:
                    raise FileExistsError(
                        "Append-only Version C build-manifest rows already differ "
                        f"for {cutoff_text}"
                    ) from exc
                return
            incoming = pd.concat([existing, incoming], ignore_index=True, sort=False)
        else:
            try:
                pd.testing.assert_frame_equal(
                    signature(existing), signature(incoming), check_dtype=False
                )
            except AssertionError:
                pass
            else:
                return
    _atomic_csv(incoming, manifest_path)


def _qa_inputs(
    root: Path, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dictionary = _dictionary_frame(_resolve(root, config["variable_dictionary_path"]))
    catalog = pd.read_csv(
        _resolve(root, config["transformation_catalog_path"]), dtype=str
    )
    return dictionary, catalog


def _write_qa(
    root: Path,
    config: Mapping[str, Any],
    summary: pd.DataFrame,
    failures: pd.DataFrame,
) -> None:
    summary_path = _resolve(root, config["qa_summary_path"])
    failures_path = _resolve(root, config["qa_failures_path"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    failures.to_csv(failures_path, index=False)


def _assert_qa(summary: pd.DataFrame) -> None:
    failed = summary.loc[
        summary["severity"].eq("error") & summary["status"].eq("fail")
    ]
    if not failed.empty:
        identifiers = failed["check_id"].astype(str).tolist()
        raise RuntimeError(f"Monthly housing-finance structural QA failed: {identifiers}")


def _filter_months(
    frame: pd.DataFrame,
    *,
    start_month: str | None,
    end_month: str | None,
) -> pd.DataFrame:
    result = frame.copy()
    months = pd.to_datetime(result["month"], errors="raise")
    if start_month:
        start = pd.Period(start_month, freq="M").to_timestamp("M")
        result = result.loc[months.ge(start)]
        months = pd.to_datetime(result["month"])
    if end_month:
        end = pd.Period(end_month, freq="M").to_timestamp("M")
        result = result.loc[months.le(end)]
    return result.reset_index(drop=True)


def build_version(
    version: str,
    *,
    root: Path = ROOT,
    start_month: str | None = None,
    end_month: str | None = None,
    information_cutoff_date: str | pd.Timestamp | None = None,
    write_outputs: bool = True,
    strict_primary_concepts: bool = False,
) -> MonthlyBuildResult:
    """Build one isolated Version B history or one append-only Version C vintage."""

    key = _version_key(version)
    cutoff = (
        _inclusive_cutoff(information_cutoff_date)
        if information_cutoff_date is not None
        else None
    )
    if key == "C" and cutoff is None:
        raise ValueError("Version C requires information_cutoff_date")
    if key == "B" and cutoff is not None:
        raise ValueError("Version B does not accept information_cutoff_date")
    verify_version_a_freeze(root)
    config = _load_yaml(_config_path(root, key))
    required_paths = [
        _resolve(root, config["benchmark_input_path"]),
        _resolve(root, config["variable_dictionary_path"]),
        _resolve(root, config["transformation_catalog_path"]),
    ]
    missing = [path for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Required build files are missing: {missing}")
    benchmark = pd.read_parquet(required_paths[0])
    indicators, assets = _load_indicators(root, version=key, cutoff=cutoff)
    if key == "B":
        observations = build_retrospective_monthly(
            benchmark,
            indicators["redfin"],
            indicators["zillow"],
            indicators["nmdb"],
            config=config,
        )
        observations = _filter_months(
            observations, start_month=start_month, end_month=end_month
        )
        diagnostics = observations.attrs.get("diagnostics", pd.DataFrame()).copy()
    else:
        # The real-time builder must never see a Version A row that was not
        # both available and physically retrieved by this vintage's cutoff.
        benchmark = _cutoff_eligible_benchmark(benchmark, cutoff)
        # Include the full calendar year containing the earliest active
        # benchmark.  Payment-level QA reconciles a modeled monthly annual
        # average to the Version A anchor, so starting at the ACS midpoint
        # month would silently omit January through June from that identity.
        default_start = pd.Timestamp("2022-01-31")
        start = (
            pd.Period(start_month, freq="M").to_timestamp("M")
            if start_month
            else default_start
        )
        end = (
            pd.Period(end_month, freq="M").to_timestamp("M")
            if end_month
            else cutoff.tz_convert(None).to_period("M").to_timestamp("M")
        )
        if end < start:
            raise ValueError("end_month cannot be earlier than start_month")
        months = pd.date_range(start=start, end=end, freq="ME")
        raw = build_realtime_monthly(
            benchmark,
            months,
            cutoff,
            redfin_rows=indicators["redfin"],
            pmms_rows=indicators["pmms"],
            hpi_rows=indicators["zillow"].rename(columns={"zhvi": "value"}),
            config_version=str(config["method_version"]),
        )
        observations = _adapt_realtime_to_shared_schema(
            raw,
            benchmark,
            cutoff=cutoff,
            method_version=str(config["method_version"]),
        )
        diagnostics = pd.DataFrame(
            [
                {
                    "component": variable_id,
                    "row_count": len(group),
                    "non_null_count": int(group["value"].notna().sum()),
                }
                for variable_id, group in observations.groupby("variable_id")
            ]
        )

    observation_errors = monthly_finance_contract_errors(observations, version=key)
    if observation_errors:
        raise RuntimeError("; ".join(observation_errors))
    lineage = _build_lineage(
        observations,
        benchmark,
        root=root,
        version=key,
        indicator_frames=indicators,
        asset_records=assets,
    )
    lineage_errors = monthly_lineage_contract_errors(
        lineage, observations, version=key
    )
    if lineage_errors:
        raise RuntimeError("; ".join(lineage_errors))

    dictionary, catalog = _qa_inputs(root, config)
    qa_summary, qa_failures = monthly_finance_qa_results(
        observations,
        lineage,
        version=key,
        dictionary=dictionary,
        transformation_catalog=catalog,
        benchmark=benchmark,
    )
    _assert_qa(qa_summary)
    if strict_primary_concepts:
        canonical = observations.loc[
            observations["variable_id"].isin(
                [
                    f"acs_implied_first_mortgage_balance_cal_{'monthly_hist' if key == 'B' else 'monthly_rt'}",
                    f"acs_implied_owner_occupied_home_equity_mortgaged_{'monthly_hist' if key == 'B' else 'monthly_rt'}",
                    f"acs_implied_owner_occupied_home_equity_all_{'monthly_hist' if key == 'B' else 'monthly_rt'}",
                ]
            )
        ]
        if canonical.empty or canonical["value"].isna().any():
            raise RuntimeError(
                "Strict primary-concept build is blocked by unavailable calibrated debt/equity anchors"
            )

    output_path: Path | None = None
    lineage_path: Path | None = None
    if write_outputs:
        if key == "B":
            output_path = _resolve(root, config["output_path"])
            lineage_path = _resolve(root, config["lineage_output_path"])
            la_export = _resolve(root, config["la_export_path"])
        else:
            cutoff_text = cutoff.date().isoformat()
            output_path = _resolve(root, config["output_root"]) / (
                f"information_cutoff_date={cutoff_text}"
            ) / "part.parquet"
            lineage_path = _resolve(root, config["lineage_output_root"]) / (
                f"information_cutoff_date={cutoff_text}"
            ) / "part.parquet"
            la_export = _resolve(
                root,
                str(config["la_export_template"]).format(
                    information_cutoff_date=cutoff_text
                ),
            )
        completion_path = _write_output_pair(
            root,
            version=key,
            observations=observations,
            lineage=lineage,
            output_path=output_path,
            lineage_path=lineage_path,
            cutoff=cutoff,
        )
        la_export.parent.mkdir(parents=True, exist_ok=True)
        observations.loc[observations["county_fips"].eq("06037")].to_csv(
            la_export, index=False
        )
        _write_qa(root, config, qa_summary, qa_failures)
        _write_build_manifest(
            root,
            version=key,
            config=config,
            asset_records=assets,
            output_path=output_path,
            lineage_path=lineage_path,
            completion_path=completion_path,
            cutoff=cutoff,
        )
        register_monthly_finance_build(
            key,
            root=root,
            observation_path=output_path,
            lineage_path=lineage_path,
            completion_path=completion_path,
            asset_records=assets,
            observations=observations,
            lineage=lineage,
        )
    verify_version_a_freeze(root)
    return MonthlyBuildResult(
        observations=observations,
        lineage=lineage,
        qa_summary=qa_summary,
        qa_failures=qa_failures,
        diagnostics=diagnostics,
        output_path=output_path,
        lineage_path=lineage_path,
    )


def _latest_c_partition(root: Path, config: Mapping[str, Any]) -> tuple[Path, Path]:
    output_root = _resolve(root, config["output_root"])
    partitions = sorted(
        path
        for path in output_root.glob("information_cutoff_date=*/part.parquet")
        if _completion_marker_path("C", path).is_file()
    )
    if not partitions:
        raise FileNotFoundError(f"No completed Version C partitions under {output_root}")
    output_path = partitions[-1]
    lineage_path = _resolve(root, config["lineage_output_root"]) / (
        output_path.parent.name
    ) / "part.parquet"
    if not lineage_path.is_file():
        raise FileNotFoundError(lineage_path)
    cutoff_text = output_path.parent.name.removeprefix("information_cutoff_date=")
    _require_completion_marker(
        root,
        version="C",
        output_path=output_path,
        lineage_path=lineage_path,
        cutoff=_inclusive_cutoff(cutoff_text),
    )
    return output_path, lineage_path


def validate_version(
    version: str,
    *,
    root: Path = ROOT,
    information_cutoff_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    key = _version_key(version)
    config = _load_yaml(_config_path(root, key))
    verify_version_a_freeze(root)
    if key == "B":
        if information_cutoff_date is not None:
            raise ValueError("Version B does not accept information_cutoff_date")
        output_path = _resolve(root, config["output_path"])
        lineage_path = _resolve(root, config["lineage_output_path"])
        completion_cutoff = None
    elif information_cutoff_date is None:
        output_path, lineage_path = _latest_c_partition(root, config)
        completion_cutoff = _inclusive_cutoff(
            output_path.parent.name.removeprefix("information_cutoff_date=")
        )
    else:
        completion_cutoff = _inclusive_cutoff(information_cutoff_date)
        cutoff_text = completion_cutoff.date().isoformat()
        output_path = _resolve(root, config["output_root"]) / (
            f"information_cutoff_date={cutoff_text}"
        ) / "part.parquet"
        lineage_path = _resolve(root, config["lineage_output_root"]) / (
            f"information_cutoff_date={cutoff_text}"
        ) / "part.parquet"
    _require_completion_marker(
        root,
        version=key,
        output_path=output_path,
        lineage_path=lineage_path,
        cutoff=completion_cutoff,
    )
    observations = pd.read_parquet(output_path)
    lineage = pd.read_parquet(lineage_path)
    dictionary, catalog = _qa_inputs(root, config)
    benchmark = pd.read_parquet(_resolve(root, config["benchmark_input_path"]))
    summary, failures = monthly_finance_qa_results(
        observations,
        lineage,
        version=key,
        dictionary=dictionary,
        transformation_catalog=catalog,
        benchmark=benchmark,
    )
    _write_qa(root, config, summary, failures)
    _assert_qa(summary)
    verify_version_a_freeze(root)
    return summary


def _parser(version: VersionKey) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("preflight", "build", "validate", "all"), required=True
    )
    parser.add_argument("--start-month", help="Inclusive YYYY-MM")
    parser.add_argument("--end-month", help="Inclusive YYYY-MM")
    parser.add_argument(
        "--information-cutoff",
        help="Inclusive YYYY-MM-DD cutoff; required for Version C preflight/build/all",
    )
    parser.add_argument("--strict-primary-concepts", action="store_true")
    if version == "B":
        parser.description = "Build isolated retrospective monthly housing finance."
    else:
        parser.description = "Build one append-only real-time housing-finance vintage."
    return parser


def main(version: str, argv: list[str] | None = None) -> int:
    key = _version_key(version)
    parser = _parser(key)
    arguments = parser.parse_args(argv)
    if key == "B" and arguments.information_cutoff:
        parser.error("Version B rejects --information-cutoff")
    if (
        key == "C"
        and arguments.stage in {"preflight", "build", "all"}
        and not arguments.information_cutoff
    ):
        parser.error(
            "Version C preflight/build/all requires --information-cutoff"
        )
    if arguments.information_cutoff:
        try:
            _inclusive_cutoff(arguments.information_cutoff)
        except ValueError as exc:
            parser.error(str(exc))
    if arguments.stage not in {"build", "all"}:
        ignored = []
        if arguments.start_month:
            ignored.append("--start-month")
        if arguments.end_month:
            ignored.append("--end-month")
        if arguments.strict_primary_concepts:
            ignored.append("--strict-primary-concepts")
        if ignored:
            parser.error(
                f"Stage {arguments.stage!r} does not accept: {', '.join(ignored)}"
            )

    config = _load_yaml(_config_path(ROOT, key))
    started = datetime.now(timezone.utc)
    status = "succeeded"
    error = ""
    try:
        if arguments.stage in {"preflight", "all"}:
            readiness = preflight(
                key,
                root=ROOT,
                information_cutoff_date=(
                    arguments.information_cutoff if key == "C" else None
                ),
            )
            path = ROOT / "output" / "qa" / (
                "version_b_preflight.csv"
                if key == "B"
                else "version_c_preflight.csv"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            readiness.to_csv(path, index=False)
            failed = readiness.loc[
                readiness["severity"].eq("error")
                & readiness["status"].eq("fail")
            ]
            if not failed.empty:
                raise RuntimeError("Preflight contains structural failures")
        if arguments.stage in {"build", "all"}:
            build_version(
                key,
                root=ROOT,
                start_month=arguments.start_month,
                end_month=arguments.end_month,
                information_cutoff_date=(
                    arguments.information_cutoff if key == "C" else None
                ),
                strict_primary_concepts=arguments.strict_primary_concepts,
            )
        if arguments.stage in {"validate", "all"}:
            validate_version(
                key,
                root=ROOT,
                information_cutoff_date=(
                    arguments.information_cutoff if key == "C" else None
                ),
            )
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr)
    finally:
        append_csv(
            _resolve(ROOT, config["pipeline_run_log_path"]),
            [
                {
                    "started_at": started.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "database_version": key,
                    "stage": arguments.stage,
                    "information_cutoff_date": arguments.information_cutoff,
                    "start_month": arguments.start_month,
                    "end_month": arguments.end_month,
                    "strict_primary_concepts": arguments.strict_primary_concepts,
                    "status": status,
                    "error": error,
                }
            ],
        )
    return 0 if status == "succeeded" else 1


__all__ = [
    "MonthlyBuildResult",
    "build_version",
    "main",
    "preflight",
    "validate_version",
    "verify_version_a_freeze",
]
