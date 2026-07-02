"""Redfin monthly tracker cleaning with metric-specific semantics."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields, month_end

METRICS = {
    "median_sale_price": (("median_sale_price",), "USD"),
    "homes_sold": (("homes_sold",), "count"),
    "active_inventory": (("inventory", "active_inventory"), "count"),
    "new_listings": (("new_listings",), "count"),
    "pending_sales": (("pending_sales",), "count"),
    "months_of_supply": (("months_of_supply",), "months"),
    "days_on_market": (("median_dom", "median_days_on_market"), "days"),
    "price_drop_share": (
        ("price_drops", "percent_active_listings_with_price_drops"),
        "proportion",
    ),
    "sale_to_list_ratio": (
        ("avg_sale_to_list", "average_sale_to_list_ratio"),
        "ratio",
    ),
}


def _normalize(column: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", column.lower())).strip("_")


def _select_la(path: Path, geography: str) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        sep="\t",
        compression="gzip",
        chunksize=100_000,
        low_memory=False,
    ):
        chunk.columns = [_normalize(column) for column in chunk.columns]
        region = chunk.get("region", pd.Series(index=chunk.index, dtype=str)).astype(str)
        state = chunk.get(
            "state_code", chunk.get("state", pd.Series(index=chunk.index, dtype=str))
        ).astype(str)
        if geography == "county":
            mask = region.str.contains("Los Angeles County", case=False, na=False)
        else:
            parent = chunk.get(
                "parent_metro_region",
                chunk.get("county", pd.Series(index=chunk.index, dtype=str)),
            ).astype(str)
            mask = parent.str.contains("Los Angeles", case=False, na=False)
            mask |= state.eq("CA") & region.str.extract(r"(\d{5})", expand=False).notna()
        if "property_type" in chunk:
            mask &= chunk["property_type"].astype(str).str.lower().isin(
                {"all residential", "all homes"}
            )
        if "period_duration" in chunk:
            duration = pd.to_numeric(chunk["period_duration"], errors="coerce")
            mask &= duration.isin({28, 30, 31}) | duration.isna()
        if mask.any():
            pieces.append(chunk.loc[mask].copy())
    if not pieces:
        return pd.DataFrame()
    result = pd.concat(pieces, ignore_index=True)
    date_column = "period_end" if "period_end" in result else "period_begin"
    result["_date"] = pd.to_datetime(result[date_column], errors="coerce")
    if "is_seasonally_adjusted" in result:
        preferred = (
            result["is_seasonally_adjusted"]
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        result["_sa_rank"] = preferred.astype(int)
        result = result.sort_values("_sa_rank").drop_duplicates(
            ["_date", "region"], keep="last"
        )
    return result


def clean_redfin(
    path: Path,
    record: dict[str, str],
    source_id: str,
    output_path: Path,
) -> pd.DataFrame:
    geography = "zip" if "_zip_" in source_id else "county"
    source = _select_la(path, geography)
    if source.empty:
        raise ValueError(f"No usable Los Angeles rows found in {path.name}")
    rows: list[pd.DataFrame] = []
    for variable_id, (aliases, unit) in METRICS.items():
        original = next((alias for alias in aliases if alias in source), None)
        if original is None:
            continue
        part = source.copy()
        part["value"] = pd.to_numeric(part[original], errors="coerce")
        part["variable_id"] = variable_id
        part["unit"] = unit
        rows.append(part)
    if not rows:
        raise ValueError(f"Redfin schema change: no configured metrics in {path.name}")
    long = pd.concat(rows, ignore_index=True)
    source_date = pd.to_datetime(long["_date"], errors="coerce")
    long["source_observation_date"] = source_date
    long["observation_date"] = month_end(source_date)
    if "period_begin" in long:
        long["period_start"] = pd.to_datetime(long["period_begin"], errors="coerce")
    else:
        long["period_start"] = long["observation_date"].dt.to_period("M").dt.start_time
    if "period_end" in long:
        long["period_end"] = pd.to_datetime(long["period_end"], errors="coerce")
    else:
        long["period_end"] = long["observation_date"]
    if geography == "county":
        long["geo_id"] = "COUNTY_06037"
    else:
        postal_zip = (
            long["region"].astype(str).str.extract(r"(\d{5})", expand=False).str.zfill(5)
        )
        long["geo_id"] = "ZIP_" + postal_zip
    long["provider"] = "Redfin Data Center"
    long["native_frequency"] = "monthly"
    long["final_frequency"] = "monthly"
    for key, value in metadata_fields(record).items():
        long[key] = value
    long["transformation_id"] = f"{source_id}_provider_monthly_v1"
    long["is_imputed"] = False
    long["is_interpolated"] = False
    if "is_seasonally_adjusted" in long:
        long["is_seasonally_adjusted"] = (
            long["is_seasonally_adjusted"]
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
    else:
        long["is_seasonally_adjusted"] = False
    long["quality_flag"] = None
    return finalize(long, output_path)
