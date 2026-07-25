"""Command-line orchestration for the native-frequency Version A layer."""

from __future__ import annotations

import argparse
import json
import logging
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
)
from src.benchmark.acs import (
    acs_five_year_period,
    clean_acs_finance_vintage,
    fetch_acs_finance_payload,
)
from src.benchmark.build import build_version_a_benchmark
from src.benchmark.hmda import clean_hmda_modified_lar
from src.benchmark.nmdb import (
    normalize_nmdb_california_upb,
    normalize_nmdb_joint_distribution,
)
from src.benchmark.pums import (
    build_pums_pi_share_parameters,
    read_pums_housing,
    read_tract_puma_relationship,
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
NMDB_DISTRIBUTION_SOURCE = "fhfa_nmdb_joint_distribution"
NMDB_UPB_SOURCE = "fhfa_nmdb_ca_upb"

AUTOMATIC_SOURCES = (
    ACS_SOURCE,
    PUMS_SOURCE,
    PUMA_2010_SOURCE,
    PUMA_2020_SOURCE,
)
MANUAL_SOURCES = (
    HMDA_SOURCE,
    NMDB_DISTRIBUTION_SOURCE,
    NMDB_UPB_SOURCE,
)
SOURCE_CHOICES = (*AUTOMATIC_SOURCES, *MANUAL_SOURCES)

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
) -> range:
    source = _settings(root)["sources"][ACS_SOURCE]
    earliest = int(source["first_supported_vintage"])
    latest = int(source["latest_verified_vintage"])
    start = latest if start_year is None else start_year
    end = start if end_year is None else end_year
    if start < earliest or end > latest or start > end:
        raise ValueError(
            f"ACS/PUMS vintages must be within {earliest}-{latest} "
            "and start_year cannot exceed end_year"
        )
    return range(start, end + 1)


def _relationship_vintage(acs_vintage: int) -> int:
    # ACS PUMS adopted 2020 PUMAs beginning with the 2022 vintage.
    return 2020 if acs_vintage >= 2022 else 2010


def _automatic_metadata(source_id: str, vintage: int) -> dict[str, str]:
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
    existing = benchmark_asset_for_data_vintage(root, ACS_SOURCE, str(vintage))
    if existing and not force:
        return existing[1]
    payload = fetch_acs_finance_payload(
        vintage,
        release_date=release_date,
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
            retrieval_method="official_api_composite_json",
            force_duplicate=force,
            notes="Four exact table calls; composite archive retains all responses.",
        )
    finally:
        temporary.unlink(missing_ok=True)


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
    if source_id in MANUAL_SOURCES:
        if not local_file:
            raise ValueError(f"--local-file is required for {source_id}")
        if start_year is None:
            raise ValueError("--start-year is required as the manual data vintage")
        metadata = _manual_metadata(source_id)
        record = archive_benchmark_file(
            root,
            source_id,
            Path(local_file),
            **metadata,
            download_url="",
            data_vintage=str(start_year),
            retrieval_method="manual_official_dashboard_or_combined_file_import",
            force_duplicate=force,
            notes=(
                f"Explicit release_date={release_date}"
                if release_date
                else "Release date must be supplied at cleaning time."
            ),
        )
        return [record]
    if local_file:
        raise ValueError("--local-file is accepted only for manual Version A sources")

    years = _year_range(root, start_year, end_year)
    selected = (
        (source_id,)
        if source_id
        else (ACS_SOURCE, PUMS_SOURCE, PUMA_2010_SOURCE, PUMA_2020_SOURCE)
    )
    records: list[dict[str, str]] = []
    for selected_source in selected:
        if selected_source not in AUTOMATIC_SOURCES:
            raise ValueError(f"Unknown Version A source: {selected_source}")
        if selected_source == ACS_SOURCE:
            for vintage in years:
                existing = benchmark_asset_for_data_vintage(
                    root, selected_source, str(vintage)
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
            for vintage in years:
                existing = benchmark_asset_for_data_vintage(
                    root, selected_source, str(vintage)
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
                        )
                    )
        else:
            relationship = 2010 if selected_source == PUMA_2010_SOURCE else 2020
            existing = benchmark_asset_for_data_vintage(
                root, selected_source, str(relationship)
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
                    )
                )
    return records


def _clean_acs(root: Path, vintage: int) -> Path:
    archived = benchmark_asset_for_data_vintage(root, ACS_SOURCE, str(vintage))
    if not archived:
        raise FileNotFoundError(f"No archived ACS finance vintage {vintage}")
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
    pums_asset = benchmark_asset_for_data_vintage(root, PUMS_SOURCE, str(vintage))
    if not pums_asset:
        raise FileNotFoundError(f"No archived PUMS housing vintage {vintage}")
    relationship_vintage = _relationship_vintage(vintage)
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
    pums = read_pums_housing(pums_asset[0])
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
        release_date=release_date,
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
    asset = benchmark_asset_for_data_vintage(root, HMDA_SOURCE, str(vintage))
    if not asset:
        raise FileNotFoundError(f"No archived HMDA vintage {vintage}")
    if not release_date:
        raise ValueError("--release-date is required to clean HMDA")
    output = root / "data" / "benchmark" / "clean" / f"hmda_downpayment_{vintage}.parquet"
    clean_hmda_modified_lar(
        asset[0],
        asset[1],
        release_date=release_date,
        activity_year=vintage,
        minimum_sample=minimum_sample,
        split_minimum_sample=split_minimum_sample,
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


def _clean_nmdb(
    root: Path,
    source_id: str,
    vintage: int,
    *,
    column_map_json: str | None,
    source_metadata_json: str | None,
) -> Path:
    asset = benchmark_asset_for_data_vintage(root, source_id, str(vintage))
    if not asset:
        raise FileNotFoundError(f"No archived {source_id} vintage {vintage}")
    column_map = _json_mapping(column_map_json, "column_map_json")
    supplied_metadata = _json_mapping(
        source_metadata_json, "source_metadata_json"
    )
    source_page_url = _manual_metadata(source_id)["source_page_url"]
    source_metadata = {
        **supplied_metadata,
        "source_id": "fhfa_nmdb_outstanding",
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
    years = _year_range(root, start_year, end_year)
    selected = (source_id,) if source_id else (ACS_SOURCE, PUMS_SOURCE)
    outputs: list[Path] = []
    for selected_source in selected:
        if selected_source == ACS_SOURCE:
            outputs.extend(_clean_acs(root, year) for year in years)
        elif selected_source == PUMS_SOURCE:
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


def build_version_a(root: Path) -> pd.DataFrame:
    """Assemble all locally cleaned Version A vintages."""
    clean_dir = root / "data" / "benchmark" / "clean"
    acs = _concat_parquet(sorted(clean_dir.glob("acs_finance_*.parquet")))
    pums = _concat_parquet(sorted(clean_dir.glob("pums_pi_share_*.parquet")))
    hmda = _concat_parquet(sorted(clean_dir.glob("hmda_downpayment_*.parquet")))
    if acs.empty:
        raise FileNotFoundError("No cleaned ACS finance benchmark inputs")
    if pums.empty:
        raise FileNotFoundError("No cleaned PUMS P&I-share parameters")
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
            download_version_a(
                root,
                start_year=arguments.start_year,
                end_year=arguments.end_year,
                release_date=arguments.release_date,
                force=arguments.force_download,
                offline=arguments.offline,
            )
            clean_version_a(
                root,
                start_year=arguments.start_year,
                end_year=arguments.end_year,
                release_date=arguments.release_date,
                minimum_sample=arguments.minimum_sample,
                split_minimum_sample=arguments.split_minimum_sample,
                column_map_json=arguments.column_map_json,
                source_metadata_json=arguments.source_metadata_json,
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
