"""Dispatch the latest archived raw vintage to source-specific cleaners."""

from __future__ import annotations

import logging

import pandas as pd

from src.cleaning.acs import clean_acs_table
from src.cleaning.bea import clean_bea
from src.cleaning.bls import clean_bls
from src.cleaning.bls_flat import clean_bls_flat
from src.cleaning.cdc_wonder import clean_cdc_wonder
from src.cleaning.census_bps import clean_bps_archive
from src.cleaning.eviction_lab import clean_eviction_lab_county_estimates
from src.cleaning.fhfa import clean_fhfa
from src.cleaning.freddie import clean_freddie
from src.cleaning.irs_migration import clean_irs_soi_county_migration
from src.cleaning.hud_usps import clean_hud_usps_archive
from src.cleaning.official import clean_hvs
from src.cleaning.redfin import clean_redfin
from src.cleaning.redfin_zip import clean_redfin_zip_key_metrics
from src.cleaning.zillow import clean_zillow
from src.common.config import ProjectConfig
from src.common.io import raw_path_for_vintage, source_archive_paths


HUD_USPS_SOURCE_ID = "hud_usps_ncwm_quarterly"
HUD_USPS_QUARANTINE_TRANSFORMATION_ID = (
    "hud_usps_quarantine_registration_v1"
)


def _hud_usps_transformation_lineage(
    archive_files: list[tuple],
) -> dict[str, str]:
    """Summarize every selected HUD-USPS artifact in one auditable log row."""

    records_by_role: dict[str, list[dict[str, str]]] = {}
    for _, record in archive_files:
        role = str(record.get("artifact_role", ""))
        records_by_role.setdefault(role, []).append(record)

    ncwm_records = sorted(
        records_by_role.get("ncwm_standardized_2020", []),
        key=lambda item: str(item.get("archive_period", "")),
    )
    crosswalk_records = records_by_role.get("tract_zip_crosswalk", [])
    county_reference_records = records_by_role.get(
        "county_fips_reference_2020", []
    )
    if not ncwm_records:
        raise ValueError("HUD-USPS lineage has no quarterly NCWM artifacts")
    if len(crosswalk_records) != 1:
        raise ValueError("HUD-USPS lineage requires exactly one crosswalk")
    if len(county_reference_records) != 1:
        raise ValueError(
            "HUD-USPS lineage requires exactly one county reference"
        )

    ordered_records = [
        *ncwm_records,
        crosswalk_records[0],
        county_reference_records[0],
    ]
    relative_paths = [
        str(record.get("relative_path") or record.get("source_filename") or "")
        for record in ordered_records
    ]
    if any(not path for path in relative_paths):
        raise ValueError("HUD-USPS lineage contains an unnamed input artifact")

    start_period = str(ncwm_records[0].get("archive_period", ""))
    end_period = str(ncwm_records[-1].get("archive_period", ""))
    crosswalk = crosswalk_records[0]
    county_reference = county_reference_records[0]
    crosswalk_vintage = str(crosswalk.get("provider_vintage", "unknown"))
    county_vintage = str(
        county_reference.get("provider_vintage", "unknown")
    )
    input_shape = (
        f"{len(ordered_records)}-file manifest: {len(ncwm_records)} NCWM "
        f"quarters {start_period}..{end_period}; 1 crosswalk "
        f"{crosswalk_vintage}; 1 county reference {county_vintage}"
    )
    description = (
        "Rebuild the HUD-USPS all-residential vacancy proxy from the complete "
        f"official archive: {len(ncwm_records)} NCWM quarters "
        f"{start_period}..{end_period}; fixed tract-to-ZIP crosswalk "
        f"{crosswalk_vintage} ({relative_paths[-2]}); county validation "
        f"reference {county_vintage} ({relative_paths[-1]}). Postal ZIP "
        "outputs remain modeled Los Angeles County portions and no-stat "
        "addresses remain a separate diagnostic."
    )
    data_vintages = sorted(
        {
            str(record["data_vintage"])
            for record in ordered_records
            if record.get("data_vintage")
        }
    )
    return {
        "input_file": "|".join(relative_paths),
        "input_rows_or_shape": input_shape,
        "description": description,
        "source_vintage": "|".join(data_vintages),
    }


def _source_archive_paths_by_period_and_direction(
    root, source_id: str, vintage: str | None = None
) -> list[tuple]:
    manifest_path = root / "metadata" / "raw_file_manifest.csv"
    if not manifest_path.exists():
        return []
    frame = pd.read_csv(manifest_path, dtype=str)
    frame = frame.loc[frame["source_id"].eq(source_id)].copy()
    if frame.empty:
        return []
    for column in ("archive_period", "migration_direction"):
        if column not in frame:
            frame[column] = None
    frame = frame.loc[
        frame["archive_period"].notna()
        & frame["migration_direction"].notna()
    ].copy()
    if frame.empty:
        return []
    frame["_retrieved"] = pd.to_datetime(
        frame["retrieved_at"], utc=True, errors="coerce", format="mixed"
    )
    if vintage:
        cutoff = pd.Timestamp(vintage, tz="UTC") + pd.Timedelta(days=1)
        frame = frame.loc[frame["_retrieved"] < cutoff]
    frame = (
        frame.sort_values("_retrieved")
        .groupby(["archive_period", "migration_direction"], as_index=False)
        .tail(1)
        .sort_values(["archive_period", "migration_direction"])
    )
    selected = []
    for _, row in frame.iterrows():
        record = {
            key: str(value)
            for key, value in row.drop(labels="_retrieved").to_dict().items()
            if pd.notna(value)
        }
        path = root / record["relative_path"]
        if path.exists():
            selected.append((path, record))
    return selected


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
        elif source.source_id == "irs_soi_county_migration":
            archive_files = _source_archive_paths_by_period_and_direction(
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
                frame = clean_irs_soi_county_migration(
                    archive_files,
                    source.source_id,
                    output_path,
                    config.root
                    / "output"
                    / "coverage"
                    / "irs_soi_migration_clean_coverage.csv",
                )
                record = archive_files[-1][1]
            except Exception as exc:
                logger.error(
                    "Cleaning failed for %s archive: %s", source.source_id, exc
                )
                continue
        elif source.source_id == HUD_USPS_SOURCE_ID:
            archive_files = source_archive_paths(
                config.root, source.source_id, vintage
            )
            if not archive_files:
                qualifier = f" at or before vintage {vintage}" if vintage else ""
                message = (
                    f"No raw archive for {source.source_id}{qualifier}; "
                    "skipping clean"
                )
                if source_filter == HUD_USPS_SOURCE_ID:
                    raise FileNotFoundError(message)
                logger.warning(message)
                continue
            output_path = (
                config.root / "data" / "clean" / f"{source.source_id}.parquet"
            )
            try:
                frame = clean_hud_usps_archive(
                    archive_files,
                    source.source_id,
                    output_path,
                    config.root / "output" / "qa" / "hud_usps_warnings.csv",
                    config.root / "data" / "restricted" / "hud_usps" / "active",
                )
                ncwm_records = [
                    record
                    for _, record in archive_files
                    if record.get("artifact_role") == "ncwm_standardized_2020"
                ]
                record = ncwm_records[-1]
            except Exception as exc:
                logger.error(
                    "Cleaning failed for %s archive: %s", source.source_id, exc
                )
                if source_filter == HUD_USPS_SOURCE_ID:
                    raise
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
                elif source.source_id in {
                    "bls_ces_la_metro",
                    "bls_cpi_la_metro",
                }:
                    frame = clean_bls_flat(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id == "freddie_mac_pmms":
                    frame = clean_freddie(path, record, output_path)
                elif source.source_id.startswith("fhfa_hpi_"):
                    frame = clean_fhfa(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id.startswith("bea_"):
                    frame = clean_bea(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id.startswith("census_acs_"):
                    frame = clean_acs_table(
                        path,
                        record,
                        source.source_id,
                        config.root / "data" / "clean" / "geography.parquet",
                        output_path,
                    )
                elif source.source_id.startswith("census_hvs_"):
                    frame = clean_hvs(
                        path,
                        record,
                        source.source_id,
                        output_path,
                    )
                elif source.source_id.startswith("cdc_wonder_"):
                    frame = clean_cdc_wonder(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id == "eviction_lab_county_estimates":
                    frame = clean_eviction_lab_county_estimates(
                        path, record, output_path
                    )
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
            hud_lineage = (
                _hud_usps_transformation_lineage(archive_files)
                if source.source_id == HUD_USPS_SOURCE_ID
                else None
            )
            for transformation_id, group in frame.groupby("transformation_id"):
                transformations.append(
                    {
                        "transformation_id": transformation_id,
                        "source_id": source.source_id,
                        "input_file": (
                            hud_lineage["input_file"]
                            if hud_lineage
                            else record["source_filename"]
                        ),
                        "output_file": (
                            "data/clean/redfin_zip_monthly_la_county.parquet"
                            "|data/interim/redfin_zip_monthly_observations.parquet"
                            if source.source_id == "redfin_zip_monthly"
                            else output_path.relative_to(
                                config.root
                            ).as_posix()
                        ),
                        "input_rows_or_shape": (
                            hud_lineage["input_rows_or_shape"]
                            if hud_lineage
                            else "provider-specific"
                        ),
                        "output_rows": len(group),
                        "description": (
                            hud_lineage["description"]
                            if hud_lineage
                            else (
                                "Validate the Key Metrics schema, filter the national "
                                "CSV through the versioned LA County ZIP reference, "
                                "preserve rolling-three-month windows and provider YoY "
                                "values, and emit the LA clean table plus long observations."
                                if source.source_id == "redfin_zip_monthly"
                                else
                                "Source-specific normalization to the shared observation "
                                "contract; identifiers retained as strings and missing "
                                "values not interpolated."
                            )
                        ),
                        "code_module": transformation_id.rsplit("_v", 1)[0],
                        "source_vintage": (
                            hud_lineage["source_vintage"]
                            if hud_lineage
                            else record["data_vintage"]
                        ),
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
            if source_filter == HUD_USPS_SOURCE_ID:
                raise
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
            preserve_hud_quarantine = prior[
                prior["source_id"].eq(HUD_USPS_SOURCE_ID)
                & prior["transformation_id"].eq(
                    HUD_USPS_QUARANTINE_TRANSFORMATION_ID
                )
            ]
            prior = prior.loc[~prior["source_id"].isin(replaced_sources)]
            prior = pd.concat(
                [prior, preserve_hud_quarantine],
                ignore_index=True,
                sort=False,
            )
            current = pd.concat([prior, current], ignore_index=True, sort=False)
        current = current.drop_duplicates(
            ["transformation_id", "source_id", "source_vintage"], keep="last"
        )
        current.to_csv(transformation_path, index=False)
    return cleaned, transformations
