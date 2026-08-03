"""Clean the official BLS CPI-U annual-average flat-file snapshot.

Version A uses the not-seasonally-adjusted U.S. city average, all-items
Consumer Price Index for All Urban Consumers (series ``CUUR0000SA0``).  BLS
encodes an annual average as period ``M13`` in its time-series flat files.
Monthly observations are intentionally ignored rather than averaged again.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd


CPI_U_SERIES_ID = "CUUR0000SA0"
CPI_U_ANNUAL_PERIOD = "M13"
CPI_U_BASE_YEAR = 2024
CPI_U_SOURCE_ID = "bls_cpi_u_us_city_average"

CPI_CLEAN_COLUMNS = (
    "year",
    "series_id",
    "period",
    "value",
    "unit",
    "source_frequency",
    "source_period_start",
    "source_period_end",
    "reference_date",
    "release_date",
    "source_release_dates",
    "source_geography",
    "parameter_geography",
    "source_id",
    "source_vintage",
    "retrieved_at",
    "source_filename",
    "checksum_sha256",
    "source_page_url",
    "transformation_id",
    "lineage_id",
    "quality_flag",
    "notes",
)


class CPIFormatError(ValueError):
    """Raised when an official CPI snapshot violates its declared contract."""


def _canonical_column(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _read_bls_flat_file(path: Path) -> pd.DataFrame:
    """Read a BLS tabular download without assuming a fabricated endpoint."""
    try:
        frame = pd.read_csv(
            path,
            sep=None,
            engine="python",
            dtype=str,
            compression="infer",
        )
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise CPIFormatError(f"cannot parse BLS CPI flat file: {path.name}") from exc
    if frame.empty:
        raise CPIFormatError("BLS CPI flat file is empty")

    canonical = [_canonical_column(column) for column in frame.columns]
    if len(canonical) != len(set(canonical)):
        raise CPIFormatError("BLS CPI flat file has duplicate canonical columns")
    frame.columns = canonical
    required = {"series_id", "year", "period", "value"}
    missing = required.difference(frame.columns)
    if missing:
        raise CPIFormatError(
            f"BLS CPI flat file is missing columns: {sorted(missing)}"
        )
    for column in required:
        frame[column] = frame[column].astype("string").str.strip()
    return frame


def _timestamp(value: Any, name: str, *, utc: bool = False) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce", utc=utc)
    if pd.isna(parsed):
        raise CPIFormatError(f"{name} must be an explicit valid date or timestamp")
    return pd.Timestamp(parsed)


def clean_bls_cpi_u_annual(
    source_path: Path,
    archive_metadata: Mapping[str, Any],
    *,
    release_date: str,
    output_path: Path | None = None,
) -> pd.DataFrame:
    """Normalize published CPI-U annual averages from one archived snapshot.

    The input must contain the standard BLS time-series concepts
    ``series_id``, ``year``, ``period``, and ``value``.  Only exact
    ``CUUR0000SA0``/``M13`` rows are retained.  The caller supplies the
    provider release date because the bulk file itself does not reliably
    encode that date.
    """
    source_path = Path(source_path)
    required_metadata = {
        "data_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
    }
    missing_metadata = required_metadata.difference(archive_metadata)
    if missing_metadata:
        raise CPIFormatError(
            f"CPI archive metadata is missing: {sorted(missing_metadata)}"
        )
    if archive_metadata.get("source_id", CPI_U_SOURCE_ID) != CPI_U_SOURCE_ID:
        raise CPIFormatError(
            f"CPI archive source_id must be {CPI_U_SOURCE_ID}"
        )

    release = _timestamp(release_date, "release_date").normalize()
    retrieved = _timestamp(
        archive_metadata["retrieved_at"], "retrieved_at", utc=True
    )
    frame = _read_bls_flat_file(source_path)
    selected = frame.loc[
        frame["series_id"].eq(CPI_U_SERIES_ID)
        & frame["period"].str.upper().eq(CPI_U_ANNUAL_PERIOD)
    ].copy()
    if selected.empty:
        raise CPIFormatError(
            f"snapshot has no {CPI_U_SERIES_ID}/{CPI_U_ANNUAL_PERIOD} "
            "annual-average observations"
        )

    year_numeric = pd.to_numeric(selected["year"], errors="coerce")
    value_numeric = pd.to_numeric(selected["value"], errors="coerce")
    invalid_year = (
        year_numeric.isna()
        | year_numeric.mod(1).ne(0)
        | year_numeric.lt(1913)
        | year_numeric.gt(9999)
    )
    if invalid_year.any():
        raise CPIFormatError("CPI annual-average years must be four-digit years")
    if value_numeric.isna().any() or value_numeric.le(0).any():
        raise CPIFormatError("CPI annual-average values must be positive numbers")

    selected["year"] = year_numeric.astype(int)
    selected["value"] = value_numeric.astype(float)
    duplicate = selected.duplicated("year", keep=False)
    if duplicate.any():
        years = sorted(selected.loc[duplicate, "year"].unique().tolist())
        raise CPIFormatError(f"duplicate CPI annual-average years: {years}")
    latest_source_period_end = pd.Timestamp(
        int(selected["year"].max()), 12, 31
    )
    if release.date() < latest_source_period_end.date():
        raise CPIFormatError(
            "release_date cannot precede the latest retained CPI "
            f"source_period_end ({latest_source_period_end.date().isoformat()})"
        )

    rows: list[dict[str, Any]] = []
    for _, row in selected.sort_values("year").iterrows():
        year = int(row["year"])
        lineage_payload = (
            f"{archive_metadata['checksum_sha256']}|{CPI_U_SERIES_ID}|"
            f"{CPI_U_ANNUAL_PERIOD}|{year}"
        )
        rows.append(
            {
                "year": year,
                "series_id": CPI_U_SERIES_ID,
                "period": CPI_U_ANNUAL_PERIOD,
                "value": float(row["value"]),
                "unit": "index_1982_1984_100",
                "source_frequency": "annual_average",
                "source_period_start": pd.Timestamp(year, 1, 1),
                "source_period_end": pd.Timestamp(year, 12, 31),
                # A marker inside the annual averaging interval, not a
                # point-in-time CPI observation.
                "reference_date": pd.Timestamp(year, 7, 1),
                "release_date": release,
                "source_release_dates": (
                    f"{CPI_U_SOURCE_ID}={release.date().isoformat()}"
                ),
                "source_geography": "United States city average",
                "parameter_geography": "US",
                "source_id": CPI_U_SOURCE_ID,
                "source_vintage": str(archive_metadata["data_vintage"]),
                "retrieved_at": retrieved,
                "source_filename": str(archive_metadata["source_filename"]),
                "checksum_sha256": str(archive_metadata["checksum_sha256"]),
                "source_page_url": str(
                    archive_metadata.get(
                        "source_page_url",
                        "https://www.bls.gov/cpi/data.htm",
                    )
                ),
                "transformation_id": "bls_cpi_u_annual_average_v1",
                "lineage_id": hashlib.sha256(
                    lineage_payload.encode("utf-8")
                ).hexdigest(),
                "quality_flag": "published_annual_average",
                "notes": (
                    "Published BLS M13 annual average for CPI-U U.S. city "
                    "average, all items, not seasonally adjusted."
                ),
            }
        )

    result = pd.DataFrame(rows, columns=CPI_CLEAN_COLUMNS)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(output_path, index=False)
    return result


def cpi_u_annual_index(
    cpi_rows: pd.DataFrame | None,
) -> dict[int, pd.Series]:
    """Validate cleaned CPI input and index it by calendar year."""
    if cpi_rows is None or cpi_rows.empty:
        return {}
    required = {
        "year",
        "series_id",
        "period",
        "value",
        "source_frequency",
        "source_period_start",
        "source_period_end",
        "release_date",
        "source_release_dates",
        "source_id",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
    }
    missing = required.difference(cpi_rows.columns)
    if missing:
        raise KeyError(f"missing cleaned CPI columns: {sorted(missing)}")
    selected = cpi_rows.copy()
    if not selected["series_id"].astype("string").str.strip().eq(
        CPI_U_SERIES_ID
    ).all():
        raise CPIFormatError(
            f"cleaned CPI series_id must be exactly {CPI_U_SERIES_ID}"
        )
    if not selected["period"].astype("string").str.strip().eq(
        CPI_U_ANNUAL_PERIOD
    ).all():
        raise CPIFormatError(
            f"cleaned CPI period must be exactly {CPI_U_ANNUAL_PERIOD}"
        )
    if not selected["source_id"].astype("string").str.strip().eq(
        CPI_U_SOURCE_ID
    ).all():
        raise CPIFormatError(
            f"cleaned CPI source_id must be exactly {CPI_U_SOURCE_ID}"
        )
    if not selected["source_frequency"].astype("string").str.strip().eq(
        "annual_average"
    ).all():
        raise CPIFormatError(
            "cleaned CPI source_frequency must be exactly annual_average"
        )

    provenance_columns = (
        "source_release_dates",
        "source_vintage",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
    )
    for column in provenance_columns:
        values = selected[column].astype("string").str.strip()
        if values.isna().any() or values.eq("").any():
            raise CPIFormatError(
                f"cleaned CPI {column} must be populated on every row"
            )

    selected["_year"] = pd.to_numeric(selected["year"], errors="coerce")
    selected["_value"] = pd.to_numeric(selected["value"], errors="coerce")
    if (
        selected["_year"].isna().any()
        or selected["_year"].mod(1).ne(0).any()
        or selected["_value"].isna().any()
        or selected["_value"].le(0).any()
    ):
        raise CPIFormatError("cleaned CPI years and values are invalid")
    selected["_year"] = selected["_year"].astype(int)
    duplicate = selected.duplicated("_year", keep=False)
    if duplicate.any():
        years = sorted(selected.loc[duplicate, "_year"].unique().tolist())
        raise CPIFormatError(f"duplicate cleaned CPI years: {years}")

    selected["_period_start"] = pd.to_datetime(
        selected["source_period_start"],
        errors="coerce",
        utc=True,
        format="mixed",
    )
    selected["_period_end"] = pd.to_datetime(
        selected["source_period_end"],
        errors="coerce",
        utc=True,
        format="mixed",
    )
    selected["_release"] = pd.to_datetime(
        selected["release_date"],
        errors="coerce",
        utc=True,
        format="mixed",
    )
    selected["_retrieved"] = pd.to_datetime(
        selected["retrieved_at"],
        errors="coerce",
        utc=True,
        format="mixed",
    )
    date_columns = ("_period_start", "_period_end", "_release", "_retrieved")
    if any(selected[column].isna().any() for column in date_columns):
        raise CPIFormatError(
            "cleaned CPI source periods, release_date, and retrieved_at "
            "must be valid"
        )

    expected_start = pd.to_datetime(
        selected["_year"].astype(str) + "-01-01", utc=True
    )
    expected_end = pd.to_datetime(
        selected["_year"].astype(str) + "-12-31", utc=True
    )
    if not selected["_period_start"].eq(expected_start).all() or not selected[
        "_period_end"
    ].eq(expected_end).all():
        raise CPIFormatError(
            "cleaned CPI source periods must span the exact calendar year"
        )
    if selected["_release"].lt(selected["_period_end"]).any():
        raise CPIFormatError(
            "cleaned CPI release_date cannot precede source_period_end"
        )
    if selected["_retrieved"].lt(selected["_release"]).any():
        raise CPIFormatError(
            "cleaned CPI retrieved_at cannot precede release_date"
        )

    release_keys = selected["_release"].dt.date.astype(str)
    if not selected["source_vintage"].astype("string").eq(release_keys).all():
        raise CPIFormatError(
            "cleaned CPI source_vintage must equal the release-date key"
        )
    expected_release_tokens = (
        CPI_U_SOURCE_ID + "=" + release_keys.astype("string")
    )
    if not selected["source_release_dates"].astype("string").eq(
        expected_release_tokens
    ).all():
        raise CPIFormatError(
            "cleaned CPI source_release_dates must contain the exact BLS "
            "source-specific release date"
        )

    snapshot_columns = (
        "release_date",
        "source_release_dates",
        "source_vintage",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
    )
    if any(selected[column].nunique(dropna=False) != 1 for column in snapshot_columns):
        raise CPIFormatError(
            "cleaned CPI input must contain one complete release snapshot"
        )
    return {
        int(row["_year"]): row
        for _, row in selected.sort_values("_year").iterrows()
    }


__all__ = [
    "CPIFormatError",
    "CPI_U_ANNUAL_PERIOD",
    "CPI_U_BASE_YEAR",
    "CPI_U_SERIES_ID",
    "CPI_U_SOURCE_ID",
    "clean_bls_cpi_u_annual",
    "cpi_u_annual_index",
]
