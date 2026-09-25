"""Dispatch the latest archived raw vintage to source-specific cleaners."""

from __future__ import annotations

import logging

import pandas as pd

from src.cleaning.acs import clean_acs_table
from src.cleaning.ahs_table_creator import (
    SOURCE_ID as AHS_TABLE_CREATOR_SOURCE_ID,
    clean_ahs_table_creator,
)
from src.cleaning.bea import clean_bea
from src.cleaning.bls import clean_bls
from src.cleaning.bls_flat import clean_bls_flat
from src.cleaning.ca_cpi import SOURCE_ID as CA_CPI_SOURCE_ID
from src.cleaning.ca_cpi import clean_ca_cpi
from src.cleaning.ca_doi import clean_ca_doi_homeowners
from src.cleaning.cdc_wonder import clean_cdc_wonder
from src.cleaning.census_bps import clean_bps_archive
from src.cleaning.census_pep import SOURCE_IDS as CENSUS_PEP_SOURCE_IDS
from src.cleaning.census_pep import clean_census_pep
from src.cleaning.bps_annual import clean_bps_annual_archive
from src.cleaning.eviction_lab import clean_eviction_lab_county_estimates
from src.cleaning.fhfa import clean_fhfa
from src.cleaning.freddie import clean_freddie
from src.cleaning.irs_migration import clean_irs_soi_county_migration
from src.cleaning.hud_usps import clean_hud_usps_archive
from src.cleaning.hud_pit import (
    LA_FOUR_COC_COMPOSITE_ID,
    LA_FOUR_COC_IDS,
    clean_hud_pit,
)
from src.cleaning.official import clean_hvs
from src.cleaning.redfin import clean_redfin
from src.cleaning.redfin_zip import clean_redfin_zip_key_metrics
from src.cleaning.sipp import SOURCE_IDS as SIPP_SOURCE_IDS
from src.cleaning.sipp import clean_sipp_state_sensitivity
from src.cleaning.zillow import clean_zillow
from src.cleaning.macro_rates import RATE_SERIES, clean_macro_rates
from src.cleaning.macro_labor import clean_macro_labor
from src.cleaning.macro_bea_claims import clean_macro_bea_claims
from src.common.config import ProjectConfig
from src.common.io import raw_path_for_vintage, source_archive_paths
from src.ingestion.bps_annual import SOURCE_ID as BPS_ANNUAL_SOURCE_ID


HUD_USPS_SOURCE_ID = "hud_usps_ncwm_quarterly"
CA_DOI_SOURCE_ID = "ca_doi_residential_property_part_i"
REQUIRED_PAUSED_VARIABLE_SOURCE_IDS = frozenset(
    {
        CA_DOI_SOURCE_ID,
        CA_CPI_SOURCE_ID,
        *CENSUS_PEP_SOURCE_IDS,
    }
)
HUD_USPS_QUARANTINE_TRANSFORMATION_ID = (
    "hud_usps_quarantine_registration_v1"
)
MACRO_CLEANING_MODULES = {
    **{source_id: "src.cleaning.macro_rates" for source_id in RATE_SERIES},
    "bls_ces_ca_la_industry_earnings": "src.cleaning.macro_labor",
    "dol_ca_ui_claims": "src.cleaning.macro_bea_claims",
    "bea_ca_real_gdp": "src.cleaning.macro_bea_claims",
}


def _macro_transformation_details(
    source_id: str,
    group: pd.DataFrame,
    variables: tuple[dict, ...],
    record: dict,
) -> dict[str, str]:
    """Add exact dictionary formulas, actual module and archived-source lineage.

    Legacy source log rows are unchanged. For a macro transformation group, every
    emitted variable must have an explicit dictionary transformation; a generic
    normalization description must not conceal a growth or aggregation rule.
    Cleaned provider-last-updated metadata takes precedence over the archive
    fallback, because BEA's release date is parsed from the provider ZIP member.
    """
    module = MACRO_CLEANING_MODULES.get(source_id)
    if module is None:
        return {}
    definitions = {str(item["variable_id"]): item for item in variables}
    variable_ids = sorted(group["variable_id"].dropna().astype(str).unique())
    if not variable_ids:
        raise ValueError(f"Macro transformation group has no variable IDs: {source_id}")
    descriptions = []
    for variable_id in variable_ids:
        definition = definitions.get(variable_id)
        if definition is None:
            raise ValueError(f"Macro transformation has no dictionary entry: {variable_id}")
        formula = str(definition.get("transformation") or "").strip()
        if not formula:
            raise ValueError(f"Macro variable has no explicit transformation: {variable_id}")
        description = f"{variable_id}: {formula}"
        notes = str(definition.get("methodological_notes") or "").strip()
        if notes:
            description += f" Methodological notes: {notes}"
        descriptions.append(description)

    def lineage_value(column: str) -> str:
        values = set()
        if column in group:
            values = {
                str(value).strip() for value in group[column]
                if pd.notna(value) and str(value).strip()
            }
        if values:
            return "|".join(sorted(values))
        fallback = record.get(column)
        if column == "provider_last_updated" and (
            fallback is None or pd.isna(fallback) or not str(fallback).strip()
        ):
            fallback = record.get("source_last_updated")
        return str(fallback).strip() if pd.notna(fallback) else ""

    return {
        "description": " | ".join(descriptions),
        "code_module": module,
        **{
            column: lineage_value(column)
            for column in (
                "source_filename", "checksum_sha256", "retrieved_at",
                "provider_last_updated",
            )
        },
    }


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
    *,
    strict: bool = False,
) -> tuple[list[pd.DataFrame], list[dict]]:
    logger = logging.getLogger("la_housing.clean")
    cleaned: list[pd.DataFrame] = []
    transformations: list[dict] = []
    for source in config.selected_sources(source_filter):
        if source.source_id == BPS_ANNUAL_SOURCE_ID:
            archive_files = source_archive_paths(
                config.root, source.source_id, vintage
            )
            if not archive_files:
                qualifier = f" at or before vintage {vintage}" if vintage else ""
                message = (
                    f"No raw archive for {source.source_id}{qualifier}; "
                    "skipping clean"
                )
                if strict or source_filter == BPS_ANNUAL_SOURCE_ID:
                    raise FileNotFoundError(message)
                logger.warning(message)
                continue
            output_path = (
                config.root / "data" / "clean" / f"{source.source_id}.parquet"
            )
            frame = clean_bps_annual_archive(archive_files, output_path)
            record = archive_files[-1][1]
        elif source.source_id.startswith("census_bps_"):
            archive_files = source_archive_paths(
                config.root, source.source_id, vintage
            )
            if not archive_files:
                qualifier = f" at or before vintage {vintage}" if vintage else ""
                message = (
                    f"No raw archive for {source.source_id}{qualifier}; "
                    "skipping clean"
                )
                if strict:
                    raise FileNotFoundError(message)
                logger.warning(message)
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
                if strict:
                    raise
                continue
        elif source.source_id == "irs_soi_county_migration":
            archive_files = _source_archive_paths_by_period_and_direction(
                config.root, source.source_id, vintage
            )
            if not archive_files:
                qualifier = f" at or before vintage {vintage}" if vintage else ""
                message = (
                    f"No raw archive for {source.source_id}{qualifier}; "
                    "skipping clean"
                )
                if strict:
                    raise FileNotFoundError(message)
                logger.warning(message)
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
                if strict:
                    raise
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
                if strict or source_filter == HUD_USPS_SOURCE_ID:
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
                if strict or source_filter == HUD_USPS_SOURCE_ID:
                    raise
                continue
        else:
            latest = raw_path_for_vintage(config.root, source.source_id, vintage)
            if latest is None:
                qualifier = f" at vintage {vintage}" if vintage else ""
                message = (
                    f"No raw archive for {source.source_id}{qualifier}; "
                    "skipping clean"
                )
                if strict:
                    raise FileNotFoundError(message)
                logger.warning(message)
                continue
            path, record = latest
            output_path = config.root / "data" / "clean" / (
                "redfin_zip_monthly_la_county.parquet"
                if source.source_id == "redfin_zip_monthly"
                else f"{source.source_id}.parquet"
            )
            try:
                if source.source_id in RATE_SERIES:
                    frame = clean_macro_rates(path, record, source.source_id, output_path)
                elif source.source_id == "bls_ces_ca_la_industry_earnings":
                    frame = clean_macro_labor(path, record, output_path)
                elif source.source_id in {"dol_ca_ui_claims", "bea_ca_real_gdp"}:
                    frame = clean_macro_bea_claims(path, record, source.source_id, output_path)
                elif source.source_id.startswith("zillow_"):
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
                elif source.source_id == CA_DOI_SOURCE_ID:
                    frame = clean_ca_doi_homeowners(
                        path, record, output_path
                    )
                elif source.source_id == CA_CPI_SOURCE_ID:
                    frame = clean_ca_cpi(path, record, output_path)
                elif source.source_id in CENSUS_PEP_SOURCE_IDS:
                    frame = clean_census_pep(path, record, output_path)
                elif source.source_id.startswith("bea_"):
                    frame = clean_bea(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id.startswith("census_acs_"):
                    request_end_year = str(record.get("request_end_year", "")).strip()
                    expected_end_date = (
                        f"{request_end_year}-12-31"
                        if request_end_year.isdigit()
                        else source.selected_end_date or None
                    )
                    frame = clean_acs_table(
                        path,
                        record,
                        source.source_id,
                        config.root / "data" / "clean" / "geography.parquet",
                        output_path,
                        expected_start_date=source.selected_start_date or None,
                        expected_end_date=expected_end_date,
                    )
                elif source.source_id.startswith("census_hvs_"):
                    frame = clean_hvs(
                        path,
                        record,
                        source.source_id,
                        output_path,
                    )
                elif source.source_id == "hud_pit_coc_2007_2025":
                    frame = clean_hud_pit(
                        path,
                        record,
                        output_path,
                        composite_mapping={
                            LA_FOUR_COC_COMPOSITE_ID: LA_FOUR_COC_IDS
                        },
                    )
                elif source.source_id.startswith("cdc_wonder_"):
                    frame = clean_cdc_wonder(
                        path, record, source.source_id, output_path
                    )
                elif source.source_id == "eviction_lab_county_estimates":
                    frame = clean_eviction_lab_county_estimates(
                        path, record, output_path
                    )
                elif source.source_id in SIPP_SOURCE_IDS:
                    frame = clean_sipp_state_sensitivity(
                        path, record, output_path
                    )
                elif source.source_id == AHS_TABLE_CREATOR_SOURCE_ID:
                    expected_period = int(source.selected_end_date[:4])
                    frame = clean_ahs_table_creator(
                        path,
                        record,
                        output_path,
                        expected_source_period=expected_period,
                        expected_geography="California",
                    )
                else:
                    message = f"No cleaner registered for {source.source_id}"
                    if strict:
                        raise NotImplementedError(message)
                    logger.warning(message)
                    continue
            except Exception as exc:
                logger.error("Cleaning failed for %s: %s", source.source_id, exc)
                if strict or (
                    source.source_id.startswith("census_acs_")
                    or source.source_id in REQUIRED_PAUSED_VARIABLE_SOURCE_IDS
                    or source_filter in {
                        "hud_pit_coc_2007_2025",
                        AHS_TABLE_CREATOR_SOURCE_ID,
                        *SIPP_SOURCE_IDS,
                    }
                ):
                    raise
                continue
        try:
            cleaned.append(frame)
            hud_lineage = (
                _hud_usps_transformation_lineage(archive_files)
                if source.source_id == HUD_USPS_SOURCE_ID
                else None
            )
            bps_annual_lineage = (
                {
                    "input_file": "|".join(
                        record["source_filename"] for _, record in archive_files
                    ),
                    "input_rows_or_shape": (
                        f"{len(archive_files)} final-annual county files"
                    ),
                    "description": (
                        "Parse each official final-annual Census county file, "
                        "retain Los Angeles County one-unit total and reported "
                        "units, preserve imputation and pre/post-2022 universe "
                        "flags, and keep the series annual without mixing it "
                        "with monthly current/YTD observations."
                    ),
                    "source_vintage": max(
                        str(record["data_vintage"])
                        for _, record in archive_files
                    ),
                }
                if source.source_id == BPS_ANNUAL_SOURCE_ID
                else None
            )
            lineage = hud_lineage or bps_annual_lineage
            for transformation_id, group in frame.groupby("transformation_id"):
                macro_details = _macro_transformation_details(
                    source.source_id, group, config.variables, record
                )
                transformations.append(
                    {
                        "transformation_id": transformation_id,
                        "source_id": source.source_id,
                        "input_file": (
                            lineage["input_file"]
                            if lineage
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
                            lineage["input_rows_or_shape"]
                            if lineage
                            else "provider-specific"
                        ),
                        "output_rows": len(group),
                        "description": (
                            lineage["description"]
                            if lineage
                            else (
                                "Validate the Key Metrics schema, filter the national "
                                "CSV through the versioned LA County ZIP reference, "
                                "preserve rolling-three-month windows and provider YoY "
                                "values, and emit the LA clean table plus long observations."
                                if source.source_id == "redfin_zip_monthly"
                                else (
                                    "Read the official California DOI XLSX "
                                    "OOXML pivot cache; filter POLICY_TYPE "
                                    "exactly to Homeowners Multi-Peril; sum "
                                    "WP and WE across all coverage ranges by "
                                    "EXP_YEAR; calculate sum(WP) / sum(WE); "
                                    "and emit nominal California annual "
                                    "controls without frequency expansion."
                                    if source.source_id == CA_DOI_SOURCE_ID
                                    else (
                                        "Validate the official 2025 SIPP California published "
                                        "state-table cells and embedded 2026-07-15 release note; "
                                        "multiply rounded households, holding share, and "
                                        "conditional mean into one 2024 statewide sensitivity; "
                                        "flag the broader universe, 99th-percentile truncation, "
                                        "rounding, and absence of a covariance-based standard "
                                        "error; never populate calibrated FHFA/NMDB county IDs."
                                        if source.source_id in SIPP_SOURCE_IDS
                                        else (
                                            "Validate the official 2023 California AHS Table 14A "
                                            "export and its published margins of error; normalize "
                                            "displayed household counts from thousands; reconcile "
                                            "fourteen reported outstanding-principal bins; and "
                                            "construct the reported-case proxy as their household "
                                            "sum times the published conditional mean. Exclude "
                                            "unreported balances, do not extrapolate to all "
                                            "mortgage households, and do not invent an aggregate "
                                            "standard error or confidence interval."
                                            if source.source_id
                                            == AHS_TABLE_CREATOR_SOURCE_ID
                                            else
                                            "Source-specific normalization to the shared observation "
                                            "contract; identifiers retained as strings and missing "
                                            "values not interpolated."
                                        )
                                    )
                                )
                            )
                        ),
                        "code_module": transformation_id.rsplit("_v", 1)[0],
                        "source_vintage": (
                            lineage["source_vintage"]
                            if lineage
                            else record["data_vintage"]
                        ),
                        "executed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                        **macro_details,
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
            if strict:
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
            monthly_finance_rows = prior["transformation_id"].astype(str).str.startswith(
                ("version_b_", "version_c_"), na=False
            )
            prior = prior.loc[
                prior["source_id"].isin(active_sources) | monthly_finance_rows
            ]
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
