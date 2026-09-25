"""Read immutable all-California indicators used by housing-finance versions.

The general Zillow and Redfin cleaners intentionally retain only Los Angeles
County.  Version B needs all 58 California counties, so this module reads the
same archived provider files without changing them.  County names are mapped
through an explicit Census county-code contract; source row order is never
used to infer a FIPS code.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# Census county ANSI/FIPS codes for California.  Names match the county labels
# in the official ACS geography file after state suffix normalization.
CALIFORNIA_COUNTY_NAME_TO_FIPS: dict[str, str] = {
    "Alameda County": "06001",
    "Alpine County": "06003",
    "Amador County": "06005",
    "Butte County": "06007",
    "Calaveras County": "06009",
    "Colusa County": "06011",
    "Contra Costa County": "06013",
    "Del Norte County": "06015",
    "El Dorado County": "06017",
    "Fresno County": "06019",
    "Glenn County": "06021",
    "Humboldt County": "06023",
    "Imperial County": "06025",
    "Inyo County": "06027",
    "Kern County": "06029",
    "Kings County": "06031",
    "Lake County": "06033",
    "Lassen County": "06035",
    "Los Angeles County": "06037",
    "Madera County": "06039",
    "Marin County": "06041",
    "Mariposa County": "06043",
    "Mendocino County": "06045",
    "Merced County": "06047",
    "Modoc County": "06049",
    "Mono County": "06051",
    "Monterey County": "06053",
    "Napa County": "06055",
    "Nevada County": "06057",
    "Orange County": "06059",
    "Placer County": "06061",
    "Plumas County": "06063",
    "Riverside County": "06065",
    "Sacramento County": "06067",
    "San Benito County": "06069",
    "San Bernardino County": "06071",
    "San Diego County": "06073",
    "San Francisco County": "06075",
    "San Joaquin County": "06077",
    "San Luis Obispo County": "06079",
    "San Mateo County": "06081",
    "Santa Barbara County": "06083",
    "Santa Clara County": "06085",
    "Santa Cruz County": "06087",
    "Shasta County": "06089",
    "Sierra County": "06091",
    "Siskiyou County": "06093",
    "Solano County": "06095",
    "Sonoma County": "06097",
    "Stanislaus County": "06099",
    "Sutter County": "06101",
    "Tehama County": "06103",
    "Trinity County": "06105",
    "Tulare County": "06107",
    "Tuolumne County": "06109",
    "Ventura County": "06111",
    "Yolo County": "06113",
    "Yuba County": "06115",
}
CALIFORNIA_COUNTY_FIPS: tuple[str, ...] = tuple(
    sorted(CALIFORNIA_COUNTY_NAME_TO_FIPS.values())
)

_DATE_COLUMN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _normalise_column(value: Any) -> str:
    return _NON_ALPHANUMERIC.sub("_", str(value).strip().casefold()).strip("_")


def _normalise_county_name(value: Any) -> str:
    text = str(value).strip().casefold()
    text = re.sub(r",\s*(?:ca|california)\s*$", "", text)
    return " ".join(_NON_ALPHANUMERIC.sub(" ", text).split())


def _normalise_fips(value: Any) -> str:
    if pd.isna(value):
        raise ValueError("county FIPS cannot be missing")
    text = re.sub(r"\.0$", "", str(value).strip())
    if not text.isdigit():
        raise ValueError(f"invalid county FIPS: {value!r}")
    if len(text) == 3:
        text = "06" + text
    elif len(text) < 5:
        text = text.zfill(5)
    if not re.fullmatch(r"06\d{3}", text):
        raise ValueError(f"not a California county FIPS: {value!r}")
    return text


def verified_california_county_mapping(
    mapping: Mapping[str, Any] | pd.DataFrame | None = None,
) -> dict[str, str]:
    """Return a normalized, exact 58-county name-to-FIPS mapping.

    A supplied DataFrame must contain ``county_name`` and ``county_fips``.
    Supplied mappings are accepted only when they contain each official
    California county FIPS exactly once.
    """

    if mapping is None:
        items = CALIFORNIA_COUNTY_NAME_TO_FIPS.items()
    elif isinstance(mapping, pd.DataFrame):
        required = {"county_name", "county_fips"}
        missing = required.difference(mapping.columns)
        if missing:
            raise KeyError(f"county mapping missing columns: {sorted(missing)}")
        items = zip(mapping["county_name"], mapping["county_fips"], strict=True)
    else:
        items = mapping.items()

    result: dict[str, str] = {}
    for raw_name, raw_fips in items:
        name = _normalise_county_name(raw_name)
        fips = _normalise_fips(raw_fips)
        if not name:
            raise ValueError("county mapping contains a blank name")
        if name in result and result[name] != fips:
            raise ValueError(f"county mapping has conflicting rows for {raw_name!r}")
        result[name] = fips

    actual = list(result.values())
    expected = set(CALIFORNIA_COUNTY_FIPS)
    if len(result) != 58 or len(set(actual)) != 58 or set(actual) != expected:
        raise ValueError(
            "county mapping must contain each of the 58 California counties "
            "exactly once"
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fields(
    path: Path,
    source_id: str,
    source_frequency: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    supplied = dict(metadata or {})
    release = supplied.get("release_date")
    release_text = (
        "unavailable"
        if release is None or pd.isna(release) or not str(release).strip()
        else pd.Timestamp(release).date().isoformat()
    )
    return {
        "source_id": source_id,
        "source_frequency": source_frequency,
        "source_filename": supplied.get("source_filename", path.name),
        "source_vintage": supplied.get("source_vintage", path.stem),
        "retrieved_at": supplied.get("retrieved_at", pd.NaT),
        "release_date": release,
        "source_release_dates": supplied.get(
            "source_release_dates", f"{source_id}={release_text}"
        ),
        "checksum_sha256": supplied.get("checksum_sha256", _sha256(path)),
    }


def _strict_numeric(series: pd.Series, label: str) -> pd.Series:
    blank = series.map(lambda value: isinstance(value, str) and not value.strip())
    reported = series.notna() & ~blank
    numeric = pd.to_numeric(series.mask(blank), errors="coerce").astype(float)
    invalid = reported & numeric.isna()
    if invalid.any():
        examples = series.loc[invalid].astype(str).drop_duplicates().head(3).tolist()
        raise ValueError(f"{label} contains nonnumeric values: {examples}")
    if np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError(f"{label} contains infinite values")
    return numeric


def _boolean_value(value: Any) -> bool | None:
    if pd.isna(value) or not str(value).strip():
        return None
    normalized = str(value).strip().casefold()
    if normalized in {"false", "0", "no", "n"}:
        return False
    if normalized in {"true", "1", "yes", "y"}:
        return True
    raise ValueError(f"invalid boolean value: {value!r}")


def _deduplicate_redfin(group: pd.DataFrame) -> pd.Series:
    ranked = group.copy()
    if "is_seasonally_adjusted" in ranked:
        flags = ranked["is_seasonally_adjusted"].map(_boolean_value)
        # Actual (NSA) transaction counts are preferred for annual allocation.
        ranks = flags.map({False: 2, True: 1})
        ranked["_selection_rank"] = ranks.where(ranks.notna(), 0)
    else:
        ranked["_selection_rank"] = 0
    ranked = ranked.loc[
        ranked["_selection_rank"].eq(ranked["_selection_rank"].max())
    ]
    comparison = ranked[["median_sale_price", "homes_sold"]].drop_duplicates()
    if len(comparison) > 1:
        county = str(group["county_fips"].iloc[0])
        month = pd.Timestamp(group["month"].iloc[0]).date()
        raise ValueError(f"conflicting Redfin rows for {county} / {month}")
    return ranked.iloc[-1]


def read_all_ca_redfin_monthly(
    path: str | Path,
    *,
    county_mapping: Mapping[str, Any] | pd.DataFrame | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    chunksize: int = 100_000,
) -> pd.DataFrame:
    """Read all-California Redfin county monthly sales indicators."""

    source_path = Path(path)
    mapping = verified_california_county_mapping(county_mapping)
    pieces: list[pd.DataFrame] = []
    for raw in pd.read_csv(
        source_path,
        sep="\t",
        compression="infer",
        chunksize=chunksize,
        low_memory=False,
    ):
        raw = raw.rename(columns={column: _normalise_column(column) for column in raw})
        required = {"region", "median_sale_price", "homes_sold"}
        missing = required.difference(raw.columns)
        if missing:
            raise KeyError(f"Redfin source missing columns: {sorted(missing)}")
        state = raw.get("state_code", raw.get("state"))
        if state is None:
            raise KeyError("Redfin source missing state/state_code")
        selected = state.astype(str).str.strip().str.upper().eq("CA")
        if "region_type" in raw:
            selected &= raw["region_type"].astype(str).str.casefold().str.contains(
                "county", na=False
            )
        if "property_type" in raw:
            selected &= raw["property_type"].astype(str).str.strip().str.casefold().isin(
                {"all residential", "all homes"}
            )
        if "period_duration" in raw:
            duration = pd.to_numeric(raw["period_duration"], errors="coerce")
            selected &= duration.isna() | duration.between(28, 31)
        if selected.any():
            pieces.append(raw.loc[selected].copy())
    if not pieces:
        raise ValueError(f"no California county rows in {source_path.name}")

    selected = pd.concat(pieces, ignore_index=True)
    date_column = "period_end" if "period_end" in selected else "period_begin"
    if date_column not in selected:
        raise KeyError("Redfin source missing period_end/period_begin")
    dates = pd.to_datetime(selected[date_column], errors="coerce")
    invalid_dates = selected[date_column].notna() & dates.isna()
    if invalid_dates.any():
        raise ValueError("Redfin source contains invalid monthly dates")
    selected["source_observation_date"] = dates
    selected["month"] = dates.dt.to_period("M").dt.to_timestamp("M")

    normalized_names = selected["region"].map(_normalise_county_name)
    selected["county_fips"] = normalized_names.map(mapping)
    unknown = selected.loc[selected["county_fips"].isna(), "region"].drop_duplicates()
    if not unknown.empty:
        raise ValueError(f"unmapped California Redfin counties: {unknown.tolist()}")
    observed_counties = set(selected["county_fips"].astype(str))
    if observed_counties != set(CALIFORNIA_COUNTY_FIPS):
        raise ValueError(
            "Redfin archive must identify all 58 California counties; "
            f"missing={sorted(set(CALIFORNIA_COUNTY_FIPS) - observed_counties)}"
        )

    selected["median_sale_price"] = _strict_numeric(
        selected["median_sale_price"], "Redfin median_sale_price"
    )
    selected["homes_sold"] = _strict_numeric(
        selected["homes_sold"], "Redfin homes_sold"
    )
    if selected["median_sale_price"].dropna().le(0).any():
        raise ValueError("Redfin median_sale_price must be positive when reported")
    if selected["homes_sold"].dropna().lt(0).any():
        raise ValueError("Redfin homes_sold cannot be negative")

    rows = [
        _deduplicate_redfin(group)
        for _, group in selected.groupby(["county_fips", "month"], sort=True)
    ]
    result = pd.DataFrame(rows).reset_index(drop=True)
    result["purchase_value_proxy"] = (
        result["median_sale_price"] * result["homes_sold"]
    )
    if "is_seasonally_adjusted" in result:
        result["is_seasonally_adjusted"] = result[
            "is_seasonally_adjusted"
        ].map(_boolean_value).astype("boolean")
    else:
        result["is_seasonally_adjusted"] = pd.Series(
            False, index=result.index, dtype="boolean"
        )
    for key, value in _source_fields(
        source_path,
        "redfin_county_monthly",
        "monthly",
        source_metadata,
    ).items():
        result[key] = value
    result["quality_flag"] = np.where(
        result["is_seasonally_adjusted"].fillna(False),
        "redfin_all_residential_sa",
        "redfin_all_residential_nsa",
    )
    columns = [
        "county_fips",
        "month",
        "source_observation_date",
        "median_sale_price",
        "homes_sold",
        "purchase_value_proxy",
        "is_seasonally_adjusted",
        "source_id",
        "source_frequency",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "release_date",
        "source_release_dates",
        "checksum_sha256",
        "quality_flag",
    ]
    return result.loc[:, columns].sort_values(
        ["county_fips", "month"]
    ).reset_index(drop=True)


def read_all_ca_zillow_zhvi(
    path: str | Path,
    *,
    county_mapping: Mapping[str, Any] | pd.DataFrame | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    chunksize: int = 2_500,
) -> pd.DataFrame:
    """Read the all-California county ZHVI history from one archived CSV."""

    source_path = Path(path)
    mapping = verified_california_county_mapping(county_mapping)
    pieces: list[pd.DataFrame] = []
    for raw in pd.read_csv(source_path, chunksize=chunksize, low_memory=False):
        state_column = "StateName" if "StateName" in raw else "State"
        if state_column not in raw:
            raise KeyError("Zillow source missing StateName/State")
        selected = raw[state_column].astype(str).str.strip().isin({"CA", "California"})
        if selected.any():
            pieces.append(raw.loc[selected].copy())
    if not pieces:
        raise ValueError(f"no California county rows in {source_path.name}")
    source = pd.concat(pieces, ignore_index=True)
    if "RegionName" not in source:
        raise KeyError("Zillow source missing RegionName")

    normalized_names = source["RegionName"].map(_normalise_county_name)
    source["county_fips"] = normalized_names.map(mapping)
    unknown = source.loc[source["county_fips"].isna(), "RegionName"].drop_duplicates()
    if not unknown.empty:
        raise ValueError(f"unmapped California Zillow counties: {unknown.tolist()}")
    if set(source["county_fips"].astype(str)) != set(CALIFORNIA_COUNTY_FIPS):
        raise ValueError("Zillow archive must identify all 58 California counties")
    if source["county_fips"].duplicated().any():
        raise ValueError("Zillow archive contains duplicate California county rows")

    if {"StateCodeFIPS", "MunicipalCodeFIPS"}.issubset(source.columns):
        state = pd.to_numeric(source["StateCodeFIPS"], errors="coerce")
        county = pd.to_numeric(source["MunicipalCodeFIPS"], errors="coerce")
        direct = state.astype("Int64").astype("string").str.zfill(2) + county.astype(
            "Int64"
        ).astype("string").str.zfill(3)
        mismatch = direct.ne(source["county_fips"].astype("string"))
        if mismatch.any():
            raise ValueError("Zillow county name and provider FIPS columns disagree")

    date_columns = [column for column in source if _DATE_COLUMN.fullmatch(str(column))]
    if not date_columns:
        raise ValueError("Zillow source has no ISO date columns")
    long = source.melt(
        id_vars=["county_fips"],
        value_vars=date_columns,
        var_name="source_observation_date",
        value_name="zhvi",
    )
    long["source_observation_date"] = pd.to_datetime(
        long["source_observation_date"], errors="raise"
    )
    long["month"] = (
        long["source_observation_date"].dt.to_period("M").dt.to_timestamp("M")
    )
    long["zhvi"] = _strict_numeric(long["zhvi"], "Zillow ZHVI")
    if long["zhvi"].dropna().le(0).any():
        raise ValueError("Zillow ZHVI must be positive when reported")
    for key, value in _source_fields(
        source_path,
        "zillow_county_zhvi",
        "monthly",
        source_metadata,
    ).items():
        long[key] = value
    long["quality_flag"] = "zillow_smoothed_seasonally_adjusted"
    columns = [
        "county_fips",
        "month",
        "source_observation_date",
        "zhvi",
        "source_id",
        "source_frequency",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "release_date",
        "source_release_dates",
        "checksum_sha256",
        "quality_flag",
    ]
    return long.loc[:, columns].sort_values(
        ["county_fips", "month"]
    ).reset_index(drop=True)


def read_california_nmdb_upb(
    path: str | Path,
    *,
    source_metadata: Mapping[str, Any] | None = None,
    chunksize: int = 200_000,
) -> pd.DataFrame:
    """Read California all-mortgage NMDB UPB as a growth-only indicator."""

    source_path = Path(path)
    pieces: list[pd.DataFrame] = []
    for raw in pd.read_csv(source_path, chunksize=chunksize, low_memory=False):
        raw = raw.rename(columns={column: _normalise_column(column) for column in raw})
        required = {"geoid", "market", "seriesid", "value1"}
        missing = required.difference(raw.columns)
        if missing:
            raise KeyError(f"NMDB source missing columns: {sorted(missing)}")
        selected = raw["geoid"].astype(str).str.strip().str.upper().eq("CA")
        selected &= raw["market"].astype(str).str.strip().str.casefold().eq(
            "all mortgages"
        )
        selected &= raw["seriesid"].astype(str).str.strip().str.upper().eq(
            "TOT_UPB"
        )
        if "geolevel" in raw:
            selected &= raw["geolevel"].astype(str).str.casefold().eq("state")
        if "frequency" in raw:
            selected &= raw["frequency"].astype(str).str.casefold().eq("quarterly")
        if "suppressed" in raw:
            suppressed = pd.to_numeric(raw["suppressed"], errors="coerce")
            selected &= suppressed.eq(0)
        if selected.any():
            pieces.append(raw.loc[selected].copy())
    if not pieces:
        raise ValueError(f"no California all-mortgage TOT_UPB rows in {source_path.name}")
    selected = pd.concat(pieces, ignore_index=True)
    selected["nmdb_ca_all_mortgages_upb"] = _strict_numeric(
        selected["value1"], "NMDB TOT_UPB VALUE1"
    )
    if selected["nmdb_ca_all_mortgages_upb"].isna().any():
        raise ValueError("NMDB California TOT_UPB cannot be missing")
    if selected["nmdb_ca_all_mortgages_upb"].le(0).any():
        raise ValueError("NMDB California TOT_UPB must be positive")
    if "value2" in selected:
        value2 = _strict_numeric(selected["value2"], "NMDB TOT_UPB VALUE2")
        comparable = value2.notna()
        if not np.allclose(
            selected.loc[comparable, "nmdb_ca_all_mortgages_upb"],
            value2.loc[comparable],
            rtol=0.0,
            atol=0.0,
        ):
            raise ValueError("NMDB VALUE1 and VALUE2 disagree for California TOT_UPB")

    if {"year", "month"}.issubset(selected.columns):
        year = pd.to_numeric(selected["year"], errors="coerce")
        month = pd.to_numeric(selected["month"], errors="coerce")
    elif {"year", "quarter"}.issubset(selected.columns):
        year = pd.to_numeric(selected["year"], errors="coerce")
        month = pd.to_numeric(selected["quarter"], errors="coerce") * 3
    elif "period" in selected:
        parsed = selected["period"].astype(str).str.extract(r"^(\d{4})Q([1-4])$")
        year = pd.to_numeric(parsed[0], errors="coerce")
        month = pd.to_numeric(parsed[1], errors="coerce") * 3
    else:
        raise KeyError("NMDB source lacks year/month, year/quarter, or period")
    if year.isna().any() or month.isna().any() or ~month.isin({3, 6, 9, 12}).all():
        raise ValueError("NMDB California TOT_UPB has invalid quarterly periods")
    selected["month"] = pd.to_datetime(
        {"year": year.astype(int), "month": month.astype(int), "day": 1}
    ).dt.to_period("M").dt.to_timestamp("M")
    if selected["month"].duplicated().any():
        raise ValueError("NMDB California TOT_UPB has duplicate quarterly periods")
    for key, value in _source_fields(
        source_path,
        "fhfa_nmdb_ca_upb",
        "quarterly",
        source_metadata,
    ).items():
        selected[key] = value
    selected["is_interpolated"] = False
    selected["quality_flag"] = (
        "nmdb_all_mortgages_growth_indicator_only;not_level_calibration"
    )
    columns = [
        "month",
        "nmdb_ca_all_mortgages_upb",
        "is_interpolated",
        "source_id",
        "source_frequency",
        "source_filename",
        "source_vintage",
        "retrieved_at",
        "release_date",
        "source_release_dates",
        "checksum_sha256",
        "quality_flag",
    ]
    return selected.loc[:, columns].sort_values("month").reset_index(drop=True)


__all__ = [
    "CALIFORNIA_COUNTY_FIPS",
    "CALIFORNIA_COUNTY_NAME_TO_FIPS",
    "read_all_ca_redfin_monthly",
    "read_all_ca_zillow_zhvi",
    "read_california_nmdb_upb",
    "verified_california_county_mapping",
]
