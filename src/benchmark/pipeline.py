"""Command-line orchestration for the native-frequency Version A layer."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.benchmark.acquisition import (
    archive_benchmark_file,
    benchmark_asset_for_data_vintage,
    download_benchmark_file,
    normalize_release_date,
)
from src.benchmark.acs import (
    acs_finance_archive_metadata,
    acs_five_year_period,
    archive_acs_finance_summary_files,
    clean_acs_finance_vintage,
    fetch_acs_finance_payload,
)
from src.benchmark.build import build_version_a_benchmark
from src.benchmark.cpi import (
    CPI_U_SOURCE_ID,
    clean_bls_cpi_u_annual,
)
from src.benchmark.hmda import clean_hmda_modified_lar
from src.benchmark.nmdb import (
    FHFA_NMDB_CA_UPB_SOURCE_ID,
    FHFA_NMDB_JOINT_DISTRIBUTION_SOURCE_ID,
    normalize_nmdb_california_upb,
    normalize_nmdb_joint_distribution,
)
from src.benchmark.pums import (
    build_pums_pi_share_parameters,
    read_pums_housing,
    read_tract_puma_relationship,
    resolve_pums_geography_contract,
)
from src.benchmark.qa import run_benchmark_qa
from src.common.config import ROOT
from src.common.io import append_csv, ensure_directories
from src.common.logging import configure_logging


ACS_SOURCE = "census_acs5_finance"
PUMS_SOURCE = "census_acs5_pums_housing_ca"
PUMA_2010_SOURCE = "census_tract_to_puma_2010"
PUMA_2020_SOURCE = "census_tract_to_puma_2020"
HMDA_SOURCE = "cfpb_hmda_modified_lar"
NMDB_DISTRIBUTION_SOURCE = FHFA_NMDB_JOINT_DISTRIBUTION_SOURCE_ID
NMDB_UPB_SOURCE = FHFA_NMDB_CA_UPB_SOURCE_ID
CPI_SOURCE = CPI_U_SOURCE_ID
CPI_DOWNLOAD_URL = (
    "https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems"
)

AUTOMATIC_SOURCES = (
    ACS_SOURCE,
    PUMS_SOURCE,
    PUMA_2010_SOURCE,
    PUMA_2020_SOURCE,
    CPI_SOURCE,
)
MANUAL_SOURCES = (
    HMDA_SOURCE,
    NMDB_DISTRIBUTION_SOURCE,
    NMDB_UPB_SOURCE,
)
SOURCE_CHOICES = (*AUTOMATIC_SOURCES, *MANUAL_SOURCES)
RELEASE_REQUIRED_SOURCES = {
    ACS_SOURCE,
    PUMS_SOURCE,
    CPI_SOURCE,
    HMDA_SOURCE,
    NMDB_DISTRIBUTION_SOURCE,
    NMDB_UPB_SOURCE,
}

PUMA_URLS = {
    2010: (
        "https://www2.census.gov/geo/docs/maps-data/data/rel/"
        "2010_Census_Tract_to_2010_PUMA.txt"
    ),
    2020: (
        "https://www2.census.gov/geo/docs/maps-data/data/rel2020/"
        "2020_Census_Tract_to_2020_PUMA.txt"
    ),
}


def _required_release_date(
    source_id: str,
    release_date: str | None,
) -> str:
    release = normalize_release_date(release_date)
    if release in {"", "unavailable"}:
        raise ValueError(
            f"--release-date is required for {source_id} and must be a "
            "published calendar date"
        )
    return release


def _archived_asset_for_release(
    root: Path,
    source_id: str,
    data_vintage: str,
    release_date: str | None,
) -> tuple[Path, dict[str, str]]:
    """Select an immutable asset only when its archived release matches."""
    release = _required_release_date(source_id, release_date)
    exact = benchmark_asset_for_data_vintage(
        root,
        source_id,
        str(data_vintage),
        release_date=release,
    )
    if exact:
        return exact
    other = benchmark_asset_for_data_vintage(
        root,
        source_id,
        str(data_vintage),
    )
    if not other:
        raise FileNotFoundError(
            f"No archived {source_id} vintage {data_vintage} "
            f"for release {release}"
        )
    archived_release = normalize_release_date(
        other[1].get("release_date")
    )
    archived_label = archived_release or "missing"
    raise ValueError(
        f"{source_id} vintage {data_vintage} archived release_date "
        f"{archived_label} does not match requested release_date {release}"
    )


def _single_release_vintage_range(
    source_id: str,
    years: range,
    *,
    operation: str,
) -> range:
    if len(years) > 1:
        raise ValueError(
            f"{operation} for {source_id} accepts exactly one vintage per "
            "--release-date; repeat the command per vintage or use all-auto "
            "with --release-dates-json"
        )
    return years


def _settings(root: Path) -> dict[str, Any]:
    path = root / "config" / "version_a_benchmark.yaml"
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _year_range(
    root: Path,
    start_year: int | None,
    end_year: int | None,
    *,
    source_id: str = ACS_SOURCE,
    clamp_to_source: bool = False,
) -> range:
    source = _settings(root)["sources"][source_id]
    earliest = int(source["first_supported_vintage"])
    latest = int(source["latest_verified_vintage"])
    start = latest if start_year is None else start_year
    end = start if end_year is None else end_year
    if clamp_to_source:
        start = max(start, earliest)
        end = min(end, latest)
        return range(start, end + 1) if start <= end else range(0)
    if start < earliest or end > latest or start > end:
        raise ValueError(
            f"{source_id} vintages must be within {earliest}-{latest} "
            "and start_year cannot exceed end_year"
        )
    return range(start, end + 1)


def _automatic_metadata(source_id: str, vintage: int | str) -> dict[str, str]:
    if source_id == CPI_SOURCE:
        return {
            "provider": "U.S. Bureau of Labor Statistics",
            "dataset_name": (
                "CPI-U U.S. City Average All Items Time-Series Flat File"
            ),
            "source_page_url": "https://www.bls.gov/cpi/data.htm",
            "download_url": CPI_DOWNLOAD_URL,
            "native_frequency": "monthly with published M13 annual average",
            "source_geography": "United States city average",
        }
    if source_id == PUMS_SOURCE:
        return {
            "provider": "U.S. Census Bureau",
            "dataset_name": "ACS 5-Year PUMS California Housing Records",
            "source_page_url": (
                "https://www.census.gov/programs-surveys/acs/microdata/access.html"
            ),
            "download_url": (
                "https://www2.census.gov/programs-surveys/acs/data/pums/"
                f"{vintage}/5-Year/csv_hca.zip"
            ),
            "native_frequency": "ACS 5-year",
            "source_geography": "California PUMA",
        }
    if source_id in {PUMA_2010_SOURCE, PUMA_2020_SOURCE}:
        relationship = 2010 if source_id == PUMA_2010_SOURCE else 2020
        return {
            "provider": "U.S. Census Bureau",
            "dataset_name": f"{relationship} Census Tract-to-PUMA Relationship",
            "source_page_url": (
                "https://www.census.gov/programs-surveys/geography/"
                "guidance/geo-areas/pumas.html"
            ),
            "download_url": PUMA_URLS[relationship],
            "native_frequency": "decennial relationship",
            "source_geography": "tract-to-PUMA",
        }
    raise KeyError(source_id)


def _manual_metadata(source_id: str) -> dict[str, str]:
    if source_id == HMDA_SOURCE:
        return {
            "provider": "CFPB and FFIEC",
            "dataset_name": "HMDA Modified Loan/Application Register",
            "source_page_url": (
                "https://ffiec.cfpb.gov/data-publication/modified-lar"
            ),
            "native_frequency": "annual",
            "source_geography": "loan record with county",
        }
    if source_id == NMDB_DISTRIBUTION_SOURCE:
        return {
            "provider": "Federal Housing Finance Agency",
            "dataset_name": "NMDB Joint Outstanding Rate-Term Distribution",
            "source_page_url": (
                "https://www.fhfa.gov/data/dashboard/"
                "nmdb-outstanding-residential-mortgage-statistics"
            ),
            "native_frequency": "published dashboard period",
            "source_geography": "United States or state",
        }
    if source_id == NMDB_UPB_SOURCE:
        return {
            "provider": "Federal Housing Finance Agency",
            "dataset_name": "NMDB California Outstanding UPB Control",
            "source_page_url": (
                "https://www.fhfa.gov/data/dashboard/"
                "nmdb-outstanding-residential-mortgage-statistics"
            ),
            "native_frequency": "published dashboard period",
            "source_geography": "California",
        }
    raise KeyError(source_id)


def _download_acs(
    root: Path,
    vintage: int,
    *,
    force: bool,
    release_date: str | None,
) -> dict[str, str]:
    release = _required_release_date(ACS_SOURCE, release_date)
    existing = benchmark_asset_for_data_vintage(
        root,
        ACS_SOURCE,
        str(vintage),
        release_date=release,
    )
    if existing and not force:
        return existing[1]
    has_api_key = bool((os.getenv("CENSUS_API_KEY") or "").strip())
    if has_api_key:
        payload = fetch_acs_finance_payload(
            vintage,
            release_date=release,
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        try:
            return archive_benchmark_file(
                root,
                ACS_SOURCE,
                temporary,
                provider="U.S. Census Bureau",
                dataset_name="ACS 5-Year Detailed Tables: Housing Finance",
                source_page_url=(
                    "https://www.census.gov/data/developers/"
                    "data-sets/acs-5year.html"
                ),
                download_url=f"https://api.census.gov/data/{vintage}/acs/acs5",
                native_frequency="ACS 5-year",
                source_geography="California counties",
                data_vintage=str(vintage),
                release_date=release,
                retrieval_method="official_api_composite_json",
                force_duplicate=force,
                notes=(
                    "Five exact table calls; composite archive retains all "
                    "responses. The API key is never archived."
                ),
            )
        finally:
            temporary.unlink(missing_ok=True)

    if vintage < 2022:
        raise RuntimeError(
            "CENSUS_API_KEY is required for ACS vintages before 2022; "
            "the official table-based Summary File fallback is verified "
            "only for 2022 and later"
        )
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary = (
            Path(temporary_directory)
            / f"acs_finance_summary_files_{vintage}.zip"
        )
        archive_acs_finance_summary_files(
            vintage,
            temporary,
            release_date=release,
        )
        return archive_benchmark_file(
            root,
            ACS_SOURCE,
            temporary,
            provider="U.S. Census Bureau",
            dataset_name="ACS 5-Year Detailed Tables: Housing Finance",
            source_page_url=(
                "https://www.census.gov/programs-surveys/acs/data/"
                "summary-file.html"
            ),
            download_url=(
                "https://www2.census.gov/programs-surveys/acs/"
                f"summary_file/{vintage}/table-based-SF/"
            ),
            native_frequency="ACS 5-year",
            source_geography="California counties",
            data_vintage=str(vintage),
            release_date=release,
            retrieval_method="official_table_based_summary_file_zip",
            force_duplicate=force,
            notes=(
                "Five official Detailed Table files and the matching 5-year "
                "geography-label file are retained byte-for-byte in one "
                "checksummed ZIP package."
            ),
        )


def download_version_a(
    root: Path,
    *,
    source_id: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    local_file: str | None = None,
    release_date: str | None = None,
    force: bool = False,
    offline: bool = False,
) -> list[dict[str, str]]:
    """Acquire automatic sources or archive one official manual source."""
    if source_id is None:
        raise ValueError(
            "download requires --source; use --stage all-auto with "
            "source-specific release dates for ACS/PUMS/CPI"
        )
    requested_release = (
        _required_release_date(source_id, release_date)
        if source_id in RELEASE_REQUIRED_SOURCES
        else (
            normalize_release_date(release_date)
            if release_date is not None
            else None
        )
    )
    if source_id in MANUAL_SOURCES:
        if not local_file:
            raise ValueError(f"--local-file is required for {source_id}")
        if start_year is None:
            raise ValueError("--start-year is required as the manual data vintage")
        if end_year is not None:
            if end_year < start_year:
                raise ValueError("start_year cannot exceed end_year")
            _single_release_vintage_range(
                source_id,
                range(start_year, end_year + 1),
                operation="download/import",
            )
        metadata = _manual_metadata(source_id)
        record = archive_benchmark_file(
            root,
            source_id,
            Path(local_file),
            **metadata,
            download_url="",
            data_vintage=str(start_year),
            release_date=requested_release,
            retrieval_method="manual_official_dashboard_or_combined_file_import",
            force_duplicate=force,
            notes=f"Explicit provider release_date={requested_release}.",
        )
        return [record]
    if source_id == CPI_SOURCE:
        release_key = str(requested_release)
        metadata = _automatic_metadata(CPI_SOURCE, release_key)
        if local_file:
            return [
                archive_benchmark_file(
                    root,
                    CPI_SOURCE,
                    Path(local_file),
                    **{
                        key: value
                        for key, value in metadata.items()
                        if key != "download_url"
                    },
                    download_url=metadata["download_url"],
                    data_vintage=release_key,
                    release_date=release_key,
                    retrieval_method="manual_official_bls_flat_file_import",
                    force_duplicate=force,
                    notes=(
                        f"Explicit provider release_date={release_key}; "
                        "CUUR0000SA0 M13 is selected during cleaning."
                    ),
                )
            ]
        existing = benchmark_asset_for_data_vintage(
            root,
            CPI_SOURCE,
            release_key,
            release_date=release_key,
        )
        if offline:
            if not existing:
                raise FileNotFoundError(
                    f"No archived {CPI_SOURCE} release {release_key}"
                )
            return [existing[1]]
        if existing and not force:
            return [existing[1]]
        return [
            download_benchmark_file(
                root,
                CPI_SOURCE,
                metadata.pop("download_url"),
                **metadata,
                data_vintage=release_key,
                release_date=release_key,
                force_duplicate=force,
                notes=(
                    f"Explicit provider release_date={release_key}; "
                    "CUUR0000SA0 M13 is selected during cleaning."
                ),
            )
        ]
    if local_file:
        raise ValueError(
            "--local-file is accepted only for manual Version A sources "
            "or the BLS CPI snapshot"
        )
    selected = (source_id,)
    records: list[dict[str, str]] = []
    for selected_source in selected:
        if selected_source not in AUTOMATIC_SOURCES:
            raise ValueError(f"Unknown Version A source: {selected_source}")
        if selected_source == ACS_SOURCE:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(
                    root,
                    start_year,
                    end_year,
                    source_id=ACS_SOURCE,
                    clamp_to_source=False,
                ),
                operation="download",
            )
            for vintage in years:
                existing = benchmark_asset_for_data_vintage(
                    root,
                    selected_source,
                    str(vintage),
                    release_date=requested_release,
                )
                if offline:
                    if not existing:
                        raise FileNotFoundError(
                            f"No archived {selected_source} vintage {vintage}"
                        )
                    records.append(existing[1])
                else:
                    records.append(
                        _download_acs(
                            root,
                            vintage,
                            force=force,
                            release_date=release_date,
                        )
                    )
        elif selected_source == PUMS_SOURCE:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(
                    root,
                    start_year,
                    end_year,
                    source_id=PUMS_SOURCE,
                    clamp_to_source=False,
                ),
                operation="download",
            )
            for vintage in years:
                existing = benchmark_asset_for_data_vintage(
                    root,
                    selected_source,
                    str(vintage),
                    release_date=requested_release,
                )
                if existing and not force:
                    records.append(existing[1])
                elif offline:
                    raise FileNotFoundError(
                        f"No archived {selected_source} vintage {vintage}"
                    )
                else:
                    metadata = _automatic_metadata(selected_source, vintage)
                    records.append(
                        download_benchmark_file(
                            root,
                            selected_source,
                            metadata.pop("download_url"),
                            **metadata,
                            data_vintage=str(vintage),
                            release_date=requested_release,
                            force_duplicate=force,
                        )
                    )
        else:
            relationship = 2010 if selected_source == PUMA_2010_SOURCE else 2020
            existing = benchmark_asset_for_data_vintage(
                root,
                selected_source,
                str(relationship),
                release_date=requested_release,
            )
            if existing and not force:
                records.append(existing[1])
            elif offline:
                raise FileNotFoundError(
                    f"No archived {selected_source} relationship"
                )
            else:
                metadata = _automatic_metadata(selected_source, relationship)
                records.append(
                    download_benchmark_file(
                        root,
                        selected_source,
                        metadata.pop("download_url"),
                        **metadata,
                        data_vintage=str(relationship),
                        release_date=requested_release,
                        force_duplicate=force,
                    )
                )
    return records


def _clean_acs(
    root: Path,
    vintage: int,
    *,
    release_date: str | None,
) -> Path:
    release = _required_release_date(ACS_SOURCE, release_date)
    archived = _archived_asset_for_release(
        root,
        ACS_SOURCE,
        str(vintage),
        release,
    )
    archive_metadata = acs_finance_archive_metadata(archived[0])
    embedded_release = normalize_release_date(
        archive_metadata.get("release_date")
    )
    if embedded_release != release:
        raise ValueError(
            f"{ACS_SOURCE} vintage {vintage} embedded release_date "
            f"{embedded_release or 'missing'} does not match archived "
            f"release_date {release}"
        )
    output = root / "data" / "benchmark" / "clean" / f"acs_finance_{vintage}.parquet"
    clean_acs_finance_vintage(archived[0], output)
    return output


def _clean_pums(
    root: Path,
    vintage: int,
    *,
    release_date: str | None,
    minimum_sample: int,
) -> Path:
    release = _required_release_date(PUMS_SOURCE, release_date)
    pums_asset = _archived_asset_for_release(
        root,
        PUMS_SOURCE,
        str(vintage),
        release,
    )
    pums = read_pums_housing(pums_asset[0])
    geography_contract = resolve_pums_geography_contract(
        vintage,
        source_frequency="5-year",
        columns=pums.attrs.get("source_columns", pums.columns),
    )
    relationship_vintage = int(
        geography_contract["relationship_vintage"]
    )
    relationship_source = (
        PUMA_2020_SOURCE
        if relationship_vintage == 2020
        else PUMA_2010_SOURCE
    )
    relationship_asset = benchmark_asset_for_data_vintage(
        root, relationship_source, str(relationship_vintage)
    )
    if not relationship_asset:
        raise FileNotFoundError(
            f"No archived {relationship_vintage} tract-to-PUMA relationship"
        )
    relationship = read_tract_puma_relationship(
        relationship_asset[0],
        relationship_vintage=relationship_vintage,
    )
    period_start, period_end, reference_date = acs_five_year_period(vintage)
    result = build_pums_pi_share_parameters(
        pums,
        relationship,
        benchmark_vintage=f"{vintage}_ACS5",
        source_period_start=period_start,
        source_period_end=period_end,
        reference_date=reference_date,
        release_date=release,
        relationship_vintage=relationship_vintage,
        source_frequency="5-year",
        source_id=PUMS_SOURCE,
        source_vintage=pums_asset[1]["data_vintage"],
        source_filename=pums_asset[1]["source_filename"],
        retrieved_at=pums_asset[1]["retrieved_at"],
        checksum_sha256=pums_asset[1]["checksum_sha256"],
        relationship_source_filename=relationship_asset[1]["source_filename"],
        relationship_retrieved_at=relationship_asset[1]["retrieved_at"],
        relationship_checksum_sha256=relationship_asset[1]["checksum_sha256"],
        minimum_sample=minimum_sample,
    )
    output = root / "data" / "benchmark" / "clean" / f"pums_pi_share_{vintage}.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output, index=False)
    return output


def _clean_hmda(
    root: Path,
    vintage: int,
    *,
    release_date: str | None,
    minimum_sample: int,
    split_minimum_sample: int,
) -> Path:
    release = _required_release_date(HMDA_SOURCE, release_date)
    asset = _archived_asset_for_release(
        root,
        HMDA_SOURCE,
        str(vintage),
        release,
    )
    output = root / "data" / "benchmark" / "clean" / f"hmda_downpayment_{vintage}.parquet"
    clean_hmda_modified_lar(
        asset[0],
        asset[1],
        release_date=release,
        activity_year=vintage,
        minimum_sample=minimum_sample,
        split_minimum_sample=split_minimum_sample,
        output_path=output,
    )
    return output


def _clean_cpi(root: Path, *, release_date: str | None) -> Path:
    release_key = _required_release_date(CPI_SOURCE, release_date)
    asset = _archived_asset_for_release(
        root,
        CPI_SOURCE,
        release_key,
        release_key,
    )
    output = (
        root
        / "data"
        / "benchmark"
        / "clean"
        / f"bls_cpi_u_annual_{release_key}.parquet"
    )
    clean_bls_cpi_u_annual(
        asset[0],
        asset[1],
        release_date=release_key,
        output_path=output,
    )
    return output


def _json_mapping(value: str | None, name: str) -> dict[str, Any]:
    if not value:
        raise ValueError(f"--{name.replace('_', '-')} is required")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--{name.replace('_', '-')} must be a JSON object"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"--{name.replace('_', '-')} must be a JSON object")
    return parsed


def _release_date_mapping_json(
    value: str | None,
) -> dict[str, dict[int | str, str]]:
    parsed = _json_mapping(value, "release_dates_json")
    required = {ACS_SOURCE, PUMS_SOURCE, CPI_SOURCE}
    missing = required.difference(parsed)
    if missing:
        raise ValueError(
            "--release-dates-json must include source-specific dates for "
            f"{sorted(missing)}"
        )
    unknown = set(parsed).difference(SOURCE_CHOICES)
    if unknown:
        raise ValueError(
            f"--release-dates-json contains unknown sources: {sorted(unknown)}"
        )
    normalized: dict[str, dict[int | str, str]] = {}
    for source_id, vintage_dates in parsed.items():
        if not isinstance(vintage_dates, dict):
            raise ValueError(
                "--release-dates-json values must be vintage-to-date objects; "
                f"{source_id} is not"
            )
        if source_id == CPI_SOURCE and set(vintage_dates) != {"snapshot"}:
            raise ValueError(
                f"{CPI_SOURCE} must contain exactly one 'snapshot' release date"
            )
        normalized[source_id] = {}
        for raw_vintage, raw_date in vintage_dates.items():
            if source_id == CPI_SOURCE:
                vintage: int | str = "snapshot"
            else:
                try:
                    vintage = int(raw_vintage)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid release vintage for {source_id}: "
                        f"{raw_vintage!r}"
                    ) from exc
            date = pd.to_datetime(raw_date, errors="coerce")
            if pd.isna(date):
                raise ValueError(
                    f"invalid release date for {source_id} "
                    f"{vintage}: {raw_date!r}"
                )
            normalized[source_id][vintage] = (
                pd.Timestamp(date).date().isoformat()
            )
    return normalized


def _clean_nmdb(
    root: Path,
    source_id: str,
    vintage: int,
    *,
    column_map_json: str | None,
    source_metadata_json: str | None,
) -> Path:
    column_map = _json_mapping(column_map_json, "column_map_json")
    supplied_metadata = _json_mapping(
        source_metadata_json, "source_metadata_json"
    )
    release = _required_release_date(
        source_id,
        supplied_metadata.get("release_date"),
    )
    asset = _archived_asset_for_release(
        root,
        source_id,
        str(vintage),
        release,
    )
    supplied_metadata["release_date"] = release
    source_page_url = _manual_metadata(source_id)["source_page_url"]
    source_metadata = {
        **supplied_metadata,
        "source_id": source_id,
        "source_vintage": asset[1]["data_vintage"],
        "retrieved_at": asset[1]["retrieved_at"],
        "source_filename": asset[1]["source_filename"],
        "checksum_sha256": asset[1]["checksum_sha256"],
        "source_page_url": source_page_url,
    }
    if source_id == NMDB_DISTRIBUTION_SOURCE:
        result = normalize_nmdb_joint_distribution(
            asset[0],
            column_map=column_map,
            source_metadata=source_metadata,
        )
        filename = f"nmdb_joint_distribution_{vintage}.parquet"
    else:
        result = normalize_nmdb_california_upb(
            asset[0],
            column_map=column_map,
            source_metadata=source_metadata,
        )
        filename = f"nmdb_ca_upb_{vintage}.parquet"
    output = root / "data" / "benchmark" / "clean" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output, index=False)
    return output


def clean_version_a(
    root: Path,
    *,
    source_id: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    release_date: str | None = None,
    minimum_sample: int = 1,
    split_minimum_sample: int = 100,
    column_map_json: str | None = None,
    source_metadata_json: str | None = None,
) -> list[Path]:
    """Clean selected archived Version A assets without temporal conversion."""
    if source_id is None:
        raise ValueError(
            "clean requires --source; use --stage all-auto with "
            "source-specific release dates for ACS/PUMS/CPI"
        )
    if source_id == CPI_SOURCE:
        return [_clean_cpi(root, release_date=release_date)]
    selected = (source_id,)
    outputs: list[Path] = []
    for selected_source in selected:
        if selected_source == ACS_SOURCE:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(
                    root,
                    start_year,
                    end_year,
                    source_id=ACS_SOURCE,
                    clamp_to_source=False,
                ),
                operation="clean",
            )
            outputs.extend(
                _clean_acs(
                    root,
                    year,
                    release_date=release_date,
                )
                for year in years
            )
        elif selected_source == PUMS_SOURCE:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(
                    root,
                    start_year,
                    end_year,
                    source_id=PUMS_SOURCE,
                    clamp_to_source=False,
                ),
                operation="clean",
            )
            outputs.extend(
                _clean_pums(
                    root,
                    year,
                    release_date=release_date,
                    minimum_sample=minimum_sample,
                )
                for year in years
            )
        elif selected_source == HMDA_SOURCE:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(
                    root,
                    start_year,
                    end_year,
                    source_id=HMDA_SOURCE,
                ),
                operation="clean",
            )
            outputs.extend(
                _clean_hmda(
                    root,
                    year,
                    release_date=release_date,
                    minimum_sample=max(50, minimum_sample),
                    split_minimum_sample=split_minimum_sample,
                )
                for year in years
            )
        elif selected_source in {
            NMDB_DISTRIBUTION_SOURCE,
            NMDB_UPB_SOURCE,
        }:
            years = _single_release_vintage_range(
                selected_source,
                _year_range(root, start_year, end_year),
                operation="clean",
            )
            outputs.extend(
                _clean_nmdb(
                    root,
                    selected_source,
                    year,
                    column_map_json=column_map_json,
                    source_metadata_json=source_metadata_json,
                )
                for year in years
            )
        elif selected_source in {PUMA_2010_SOURCE, PUMA_2020_SOURCE}:
            raise ValueError(
                f"{selected_source} is an input to another cleaner; "
                "select the PUMS source instead."
            )
        else:
            raise ValueError(f"Unknown Version A source: {selected_source}")
    return outputs


def _concat_parquet(paths: list[Path]) -> pd.DataFrame:
    return (
        pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        if paths
        else pd.DataFrame()
    )


def _latest_cpi_snapshot(paths: list[Path]) -> pd.DataFrame:
    """Select one complete CPI release rather than mixing revised histories."""
    combined = _concat_parquet(paths)
    if combined.empty:
        return combined
    required = {
        "release_date",
        "retrieved_at",
        "source_filename",
        "checksum_sha256",
    }
    missing = required.difference(combined.columns)
    if missing:
        raise KeyError(f"missing cleaned CPI provenance: {sorted(missing)}")
    snapshots = combined[
        [
            "release_date",
            "retrieved_at",
            "source_filename",
            "checksum_sha256",
        ]
    ].drop_duplicates()
    snapshots["_release"] = pd.to_datetime(
        snapshots["release_date"], errors="coerce"
    )
    snapshots["_retrieved"] = pd.to_datetime(
        snapshots["retrieved_at"], errors="coerce", utc=True
    )
    if snapshots["_release"].isna().any() or snapshots["_retrieved"].isna().any():
        raise ValueError("cleaned CPI snapshot has invalid release provenance")
    selected = snapshots.sort_values(
        ["_release", "_retrieved", "source_filename"]
    ).iloc[-1]
    return combined.loc[
        combined["checksum_sha256"].astype(str).eq(
            str(selected["checksum_sha256"])
        )
        & combined["source_filename"].astype(str).eq(
            str(selected["source_filename"])
        )
    ].copy()


def build_version_a(root: Path) -> pd.DataFrame:
    """Assemble all locally cleaned Version A vintages."""
    clean_dir = root / "data" / "benchmark" / "clean"
    acs = _concat_parquet(sorted(clean_dir.glob("acs_finance_*.parquet")))
    pums = _concat_parquet(sorted(clean_dir.glob("pums_pi_share_*.parquet")))
    hmda = _concat_parquet(sorted(clean_dir.glob("hmda_downpayment_*.parquet")))
    cpi = _latest_cpi_snapshot(
        sorted(clean_dir.glob("bls_cpi_u_annual_*.parquet"))
    )
    if acs.empty:
        raise FileNotFoundError("No cleaned ACS finance benchmark inputs")
    pmms_path = root / "data" / "clean" / "freddie_mac_pmms.parquet"
    pmms = pd.read_parquet(pmms_path) if pmms_path.exists() else None

    nmdb_distribution_paths = sorted(
        clean_dir.glob("nmdb_joint_distribution_*.parquet")
    )
    nmdb_upb_paths = sorted(clean_dir.glob("nmdb_ca_upb_*.parquet"))
    distributions = _concat_parquet(nmdb_distribution_paths)
    upb = _concat_parquet(nmdb_upb_paths)
    output = root / "data" / "final" / "housing_finance_benchmark.parquet"
    la_export = root / "output" / "exports" / "la_version_a_benchmark.csv"
    return build_version_a_benchmark(
        acs,
        pums,
        hmda_rows=None if hmda.empty else hmda,
        cpi_rows=None if cpi.empty else cpi,
        pmms_rows=pmms,
        nmdb_distributions=None if distributions.empty else distributions,
        california_upb=None if upb.empty else upb,
        output_path=output,
        la_export_path=la_export,
    )


def validate_version_a(root: Path) -> pd.DataFrame:
    path = root / "data" / "final" / "housing_finance_benchmark.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    return run_benchmark_qa(path, root=root)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--stage",
        choices=("download", "clean", "build", "validate", "all-auto"),
        required=True,
    )
    command.add_argument("--source", choices=SOURCE_CHOICES)
    command.add_argument("--start-year", type=int)
    command.add_argument("--end-year", type=int)
    command.add_argument("--local-file")
    command.add_argument("--release-date", help="Explicit provider release date")
    command.add_argument(
        "--release-dates-json",
        help=(
            "Source-to-date JSON used by all-auto so ACS, PUMS, and CPI "
            "releases remain distinct"
        ),
    )
    command.add_argument(
        "--column-map-json",
        help="Canonical-to-source JSON mapping for an NMDB manual export",
    )
    command.add_argument(
        "--source-metadata-json",
        help="Explicit period, units, universe, and semantic JSON for NMDB",
    )
    command.add_argument("--minimum-sample", type=int, default=1)
    command.add_argument("--split-minimum-sample", type=int, default=100)
    command.add_argument("--force-download", action="store_true")
    command.add_argument("--offline", action="store_true")
    command.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    root = ROOT
    ensure_directories(root)
    logger = configure_logging(root, arguments.log_level)
    started = datetime.now(timezone.utc)
    status = "succeeded"
    error = ""
    try:
        if arguments.stage == "download":
            records = download_version_a(
                root,
                source_id=arguments.source,
                start_year=arguments.start_year,
                end_year=arguments.end_year,
                local_file=arguments.local_file,
                release_date=arguments.release_date,
                force=arguments.force_download,
                offline=arguments.offline,
            )
            logger.info("Archived %s Version A source assets", len(records))
        elif arguments.stage == "clean":
            outputs = clean_version_a(
                root,
                source_id=arguments.source,
                start_year=arguments.start_year,
                end_year=arguments.end_year,
                release_date=arguments.release_date,
                minimum_sample=arguments.minimum_sample,
                split_minimum_sample=arguments.split_minimum_sample,
                column_map_json=arguments.column_map_json,
                source_metadata_json=arguments.source_metadata_json,
            )
            logger.info("Wrote %s Version A clean tables", len(outputs))
        elif arguments.stage == "build":
            result = build_version_a(root)
            logger.info("Built %s Version A benchmark rows", len(result))
        elif arguments.stage == "validate":
            validate_version_a(root)
        elif arguments.stage == "all-auto":
            if arguments.release_date:
                raise ValueError(
                    "all-auto requires --release-dates-json; one "
                    "--release-date cannot describe ACS, PUMS, and CPI"
                )
            release_dates = _release_date_mapping_json(
                arguments.release_dates_json
            )
            for relationship_source in (
                PUMA_2010_SOURCE,
                PUMA_2020_SOURCE,
            ):
                download_version_a(
                    root,
                    source_id=relationship_source,
                    force=arguments.force_download,
                    offline=arguments.offline,
                )
            for data_source in (ACS_SOURCE, PUMS_SOURCE):
                years = _year_range(
                    root,
                    arguments.start_year,
                    arguments.end_year,
                    source_id=data_source,
                    clamp_to_source=True,
                )
                for vintage in years:
                    if vintage not in release_dates[data_source]:
                        raise ValueError(
                            "--release-dates-json is missing "
                            f"{data_source} vintage {vintage}"
                        )
                    source_release_date = release_dates[data_source][vintage]
                    download_version_a(
                        root,
                        source_id=data_source,
                        start_year=vintage,
                        end_year=vintage,
                        release_date=source_release_date,
                        force=arguments.force_download,
                        offline=arguments.offline,
                    )
                    clean_version_a(
                        root,
                        source_id=data_source,
                        start_year=vintage,
                        end_year=vintage,
                        release_date=source_release_date,
                        minimum_sample=arguments.minimum_sample,
                        split_minimum_sample=arguments.split_minimum_sample,
                        column_map_json=arguments.column_map_json,
                        source_metadata_json=arguments.source_metadata_json,
                    )
            cpi_release_date = release_dates[CPI_SOURCE]["snapshot"]
            download_version_a(
                root,
                source_id=CPI_SOURCE,
                release_date=cpi_release_date,
                force=arguments.force_download,
                offline=arguments.offline,
            )
            clean_version_a(
                root,
                source_id=CPI_SOURCE,
                release_date=cpi_release_date,
            )
            build_version_a(root)
            validate_version_a(root)
        else:
            raise ValueError(arguments.stage)
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Version A pipeline failed")
    finally:
        append_csv(
            root / "metadata" / "version_a_pipeline_run_log.csv",
            [
                {
                    "started_at": started.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "stage": arguments.stage,
                    "source": arguments.source,
                    "start_year": arguments.start_year,
                    "end_year": arguments.end_year,
                    "offline": arguments.offline,
                    "status": status,
                    "error": error,
                }
            ],
        )
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
