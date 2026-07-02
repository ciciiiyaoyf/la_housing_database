"""Dispatch the latest archived raw vintage to source-specific cleaners."""

from __future__ import annotations

import logging

import pandas as pd

from src.cleaning.bls import clean_bls
from src.cleaning.census_bps import clean_bps_archive
from src.cleaning.freddie import clean_freddie
from src.cleaning.redfin import clean_redfin
from src.cleaning.redfin_zip import clean_redfin_zip_key_metrics
from src.cleaning.zillow import clean_zillow
from src.common.config import ProjectConfig
from src.common.io import raw_path_for_vintage, source_archive_paths


def clean_sources(
    config: ProjectConfig,
    source_filter: str | None = None,
    vintage: str | None = None,
) -> tuple[list[pd.DataFrame], list[dict]]:
    logger = logging.getLogger("la_housing.clean")
    cleaned: list[pd.DataFrame] = []
    transformations: list[dict] = []
    for source in config.selected_sources(source_filter):
        if source.source_id.startswith("census_bps_"):
            archive_files = source_archive_paths(
                config.root, source.source_id, vintage
            )
            if not archive_files:
                qualifier = f" at or before vintage {vintage}" if vintage else ""
                logger.warning(
                    "No raw archive for %s%s; skipping clean",
                    source.source_id,
                    qualifier,
                )
                continue
            output_path = (
                config.root / "data" / "clean" / f"{source.source_id}.parquet"
            )
            try:
                frame = clean_bps_archive(
                    archive_files,
                    source.source_id,
                    output_path,
                    config.root
                    / "output"
                    / "coverage"
                    / "bps_archive_coverage.csv",
                )
                record = archive_files[-1][1]
            except Exception as exc:
                logger.error(
                    "Cleaning failed for %s archive: %s", source.source_id, exc
                )
                continue
        else:
            latest = raw_path_for_vintage(config.root, source.source_id, vintage)
            if latest is None:
                qualifier = f" at vintage {vintage}" if vintage else ""
                logger.warning(
                    "No raw archive for %s%s; skipping clean",
                    source.source_id,
                    qualifier,
                )
                continue
            path, record = latest
            output_path = config.root / "data" / "clean" / (
                "redfin_zip_monthly_la_county.parquet"
                if source.source_id == "redfin_zip_monthly"
                else f"{source.source_id}.parquet"
            )
            try:
                if source.source_id.startswith("zillow_"):
                    frame = clean_zillow(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id == "redfin_zip_monthly":
                    frame = clean_redfin_zip_key_metrics(
                        path,
                        record,
                        config.root
                        / "data"
                        / "clean"
                        / "geography.parquet",
                        output_path,
                        config.root
                        / "data"
                        / "interim"
                        / "redfin_zip_monthly_observations.parquet",
                        config.root / "output" / "coverage",
                    )
                elif source.source_id.startswith("redfin_"):
                    frame = clean_redfin(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id == "bls_laus_la_county":
                    frame = clean_bls(path, record, output_path)
                elif source.source_id == "freddie_mac_pmms":
                    frame = clean_freddie(path, record, output_path)
                else:
                    logger.warning(
                        "No cleaner registered for %s", source.source_id
                    )
                    continue
            except Exception as exc:
                logger.error("Cleaning failed for %s: %s", source.source_id, exc)
                continue
        try:
            cleaned.append(frame)
            for transformation_id, group in frame.groupby("transformation_id"):
                transformations.append(
                    {
                        "transformation_id": transformation_id,
                        "source_id": source.source_id,
                        "input_file": record["source_filename"],
                        "output_file": (
                            "data/clean/redfin_zip_monthly_la_county.parquet"
                            "|data/interim/redfin_zip_monthly_observations.parquet"
                            if source.source_id == "redfin_zip_monthly"
                            else output_path.relative_to(
                                config.root
                            ).as_posix()
                        ),
                        "input_rows_or_shape": "provider-specific",
                        "output_rows": len(group),
                        "description": (
                            "Validate the Key Metrics schema, filter the national "
                            "CSV through the versioned LA County ZIP reference, "
                            "preserve rolling-three-month windows and provider YoY "
                            "values, and emit the LA clean table plus long observations."
                            if source.source_id == "redfin_zip_monthly"
                            else
                            "Source-specific normalization to the shared observation "
                            "contract; identifiers retained as strings and missing "
                            "values not interpolated."
                        ),
                        "code_module": transformation_id.rsplit("_v", 1)[0],
                        "source_vintage": record["data_vintage"],
                        "executed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    }
                )
            logger.info("Cleaned %s: %s observations", source.source_id, len(frame))
        except Exception as exc:
            logger.error(
                "Transformation logging failed for %s: %s",
                source.source_id,
                exc,
            )
    if transformations:
        transformation_path = (
            config.root / "metadata" / "transformation_log.csv"
        )
        current = pd.DataFrame(transformations)
        if transformation_path.exists():
            prior = pd.read_csv(transformation_path, dtype=str)
            active_sources = {
                source.source_id for source in config.sources
            } | {"multiple"}
            prior = prior.loc[prior["source_id"].isin(active_sources)]
            replaced_sources = set(current["source_id"])
            prior = prior.loc[~prior["source_id"].isin(replaced_sources)]
            current = pd.concat([prior, current], ignore_index=True, sort=False)
        current = current.drop_duplicates(
            ["transformation_id", "source_id", "source_vintage"], keep="last"
        )
        current.to_csv(transformation_path, index=False)
    return cleaned, transformations
