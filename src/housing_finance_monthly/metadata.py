"""Global metadata registration for housing-finance Versions B and C.

The general pipeline owns the global CSV schemas, while the isolated monthly
finance pipelines own only rows identified here.  Registration therefore
removes and replaces B/C-owned rows without rewriting unrelated metadata.
Version A and raw files are read only.
The dictionary's required columns are emitted in their canonical order, followed
by any unrelated extension columns in their existing relative order.

``register_monthly_finance_contracts`` is safe before materialization and
publishes both closed 58-variable dictionaries plus the six product-family
catalog rows.  ``register_monthly_finance_build`` is the post-build entry
point: it first verifies the checksum-gated completion marker, then registers
the contracts and one executed transformation-log row per transformation
actually present in the completed observations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

import pandas as pd
import yaml

from src.common.config import ROOT
from src.common.io import sha256_file
from src.common.variable_metadata import ENRICHMENT_COLUMNS, enrich_variable_metadata


VersionKey = Literal["B", "C"]
_COMPLETION_SCHEMA_VERSION = "housing_finance_output_pair_v1"
_EXPECTED_VARIABLE_COUNT = 58

GLOBAL_DICTIONARY_COLUMNS = (
    "variable_id",
    "display_name",
    "definition",
    "provider",
    "original_field",
    "unit",
    "native_frequency",
    "final_frequency",
    "geography",
    "seasonal_adjustment",
    "smoothing",
    "transformation",
    "missing_value_policy",
    "revision_policy",
    "methodological_notes",
    "market_segment",
    "variable_role",
    "data_domain",
    "source_geography",
    "source_frequency",
    "model_frequency",
    "is_observed",
    "is_constructed",
    "is_proxy",
    "active_status",
    "observed_constructed_proxy_or_scenario",
    *ENRICHMENT_COLUMNS,
)

GLOBAL_CATALOG_COLUMNS = (
    "Source",
    "Frequency",
    "Geography",
    "Key variables",
    "source_id",
    "provider",
    "product",
    "official_source_page",
    "exact_download_url_or_api",
    "access_method",
    "active_status",
    "provenance_status",
    "observed_constructed_proxy_or_scenario",
    "manual_download_required",
    "manual_reason",
    "raw_file_format",
    "raw_file_path",
    "source_manifest_path",
    "original_filename",
    "retrieval_date",
    "provider_vintage",
    "checksum_sha256",
    "native_frequency",
    "analytical_frequency",
    "geography_level",
    "geography_name",
    "geography_id",
    "start_date",
    "end_date",
    "row_count",
    "variable_ids",
    "seasonal_adjustment",
    "smoothing",
    "units",
    "destination_clean_table",
    "destination_analytical_table",
    "update_command",
    "missing_value_policy",
    "revision_policy",
    "known_limitations",
    "license_or_use_notes",
    "last_verified",
    "notes",
)

TRANSFORMATION_LOG_COLUMNS = (
    "transformation_id",
    "source_id",
    "input_file",
    "output_file",
    "input_rows_or_shape",
    "output_rows",
    "description",
    "code_module",
    "source_vintage",
    "executed_at",
    "source_filename",
    "checksum_sha256",
    "retrieved_at",
)


@dataclass(frozen=True)
class _VersionSpec:
    key: VersionKey
    suffix: str
    database_name: str
    dictionary_path: str
    transformation_catalog_path: str
    benchmark_path: str
    observation_product_id: str
    lineage_product_id: str
    completion_product_id: str
    observation_path: str
    lineage_path: str
    completion_path: str
    build_manifest_path: str
    observation_table: str
    lineage_table: str
    update_command: str
    revision_policy: str


_SPECS: dict[VersionKey, _VersionSpec] = {
    "B": _VersionSpec(
        key="B",
        suffix="_monthly_hist",
        database_name="B. Retrospective monthly",
        dictionary_path="config/version_b_variables.yaml",
        transformation_catalog_path="metadata/version_b_transformation_catalog.csv",
        benchmark_path="data/final/housing_finance_benchmark.parquet",
        observation_product_id="housing_finance_version_b_observations",
        lineage_product_id="housing_finance_version_b_lineage",
        completion_product_id="housing_finance_version_b_completion_marker",
        observation_path="data/final/housing_finance_monthly_hist.parquet",
        lineage_path="data/final/housing_finance_monthly_hist_lineage.parquet",
        completion_path="data/final/housing_finance_monthly_hist.complete.json",
        build_manifest_path="metadata/version_b_build_manifest.csv",
        observation_table="analytics.housing_finance_monthly_hist",
        lineage_table="metadata.housing_finance_monthly_hist_lineage",
        update_command="python run_version_b.py --stage all",
        revision_policy=(
            "Atomically replace the complete retrospective observation/lineage "
            "pair only after structural QA succeeds."
        ),
    ),
    "C": _VersionSpec(
        key="C",
        suffix="_monthly_rt",
        database_name="C. Real-time monthly vintage",
        dictionary_path="config/version_c_variables.yaml",
        transformation_catalog_path="metadata/version_c_transformation_catalog.csv",
        benchmark_path="data/final/housing_finance_benchmark.parquet",
        observation_product_id="housing_finance_version_c_observations",
        lineage_product_id="housing_finance_version_c_lineage",
        completion_product_id="housing_finance_version_c_completion_marker",
        observation_path=(
            "data/final/housing_finance_monthly_rt/"
            "information_cutoff_date=YYYY-MM-DD/part.parquet"
        ),
        lineage_path=(
            "data/final/housing_finance_monthly_rt_lineage/"
            "information_cutoff_date=YYYY-MM-DD/part.parquet"
        ),
        completion_path=(
            "data/final/housing_finance_monthly_rt/"
            "information_cutoff_date=YYYY-MM-DD/_COMPLETE.json"
        ),
        build_manifest_path="metadata/version_c_build_manifest.csv",
        observation_table="analytics.housing_finance_monthly_rt",
        lineage_table="metadata.housing_finance_monthly_rt_lineage",
        update_command=(
            "python run_version_c.py --stage all "
            "--information-cutoff YYYY-MM-DD"
        ),
        revision_policy=(
            "Append one immutable checksum-gated partition per information "
            "cutoff; allow only an exact idempotent rerun."
        ),
    ),
}

PRODUCT_SOURCE_IDS = frozenset(
    value
    for spec in _SPECS.values()
    for value in (
        spec.observation_product_id,
        spec.lineage_product_id,
        spec.completion_product_id,
    )
)

_CATALOG_BUILD_FIELDS = (
    "raw_file_path",
    "original_filename",
    "retrieval_date",
    "provider_vintage",
    "checksum_sha256",
    "start_date",
    "end_date",
    "row_count",
    "last_verified",
    "notes",
)


@dataclass(frozen=True)
class MetadataRegistrationResult:
    """Counts written by one contract or completed-build registration."""

    dictionary_rows: int
    catalog_rows: int
    transformation_rows: int
    version: str | None = None
    information_cutoff_date: str | None = None


@dataclass(frozen=True)
class _CompletedBuild:
    version: VersionKey
    observations: pd.DataFrame
    lineage: pd.DataFrame
    observation_path: Path
    lineage_path: Path
    completion_path: Path
    information_cutoff_date: str | None
    completed_at: str


def _version_key(version: str) -> VersionKey:
    normalized = str(version).strip().upper()
    if normalized in {"B", "VERSION B"}:
        return "B"
    if normalized in {"C", "VERSION C"}:
        return "C"
    raise ValueError("version must be B or C")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _variables(root: Path, spec: _VersionSpec) -> list[dict[str, Any]]:
    payload = _load_yaml(root / spec.dictionary_path)
    variables = payload.get("variables")
    if not isinstance(variables, list):
        raise ValueError(f"{spec.dictionary_path} must contain a variables list")
    if len(variables) != _EXPECTED_VARIABLE_COUNT:
        raise ValueError(
            f"Version {spec.key} dictionary must contain exactly "
            f"{_EXPECTED_VARIABLE_COUNT} variables, found {len(variables)}"
        )
    identifiers = [str(item.get("variable_id", "")) for item in variables]
    if any(not value.endswith(spec.suffix) for value in identifiers):
        raise ValueError(f"Version {spec.key} dictionary contains an invalid suffix")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Version {spec.key} dictionary contains duplicate IDs")
    return [dict(item) for item in variables]


def _catalog(root: Path, spec: _VersionSpec) -> pd.DataFrame:
    frame = pd.read_csv(root / spec.transformation_catalog_path, dtype=str)
    required = {"transformation_id", "concept", "formula", "missing_value_policy"}
    if not required.issubset(frame.columns):
        raise ValueError(
            f"{spec.transformation_catalog_path} is missing "
            f"{sorted(required - set(frame.columns))}"
        )
    if frame["transformation_id"].duplicated().any():
        raise ValueError(f"{spec.transformation_catalog_path} has duplicate IDs")
    return frame


def _read_csv(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=list(columns))
    frame = pd.read_csv(path, dtype=str)
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing global columns: {missing}")
    return frame


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write one CSV through a unique sibling and an atomic replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.stage.{uuid4().hex}")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _pipe(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        candidates = [values]
    else:
        try:
            candidates = list(values)
        except TypeError:
            candidates = [values]
    output: list[str] = []
    for value in candidates:
        text = _text(value)
        if text and text not in output:
            output.append(text)
    return "|".join(output)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Metadata path must stay inside the project root: {path}") from exc


def _resolved(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _semantics(variable: Mapping[str, Any]) -> str:
    if bool(variable.get("proxy")):
        return "proxy"
    if bool(variable.get("scenario")):
        return "scenario sensitivity"
    status = str(variable.get("output_status", ""))
    if status == "canonical_null":
        return "schema-only canonical null"
    if status == "canonical_modeled_repeat":
        return "modeled retrospective monthly level"
    if status == "canonical_modeled_carry":
        return "modeled real-time carry/nowcast"
    return status.replace("_", " ") or "constructed"


def _display_name(
    variable: Mapping[str, Any], anchor: Mapping[str, Any], spec: _VersionSpec
) -> str:
    configured = _text(variable.get("display_name"))
    if configured:
        return configured
    base = _text(anchor.get("display_name")) or str(variable["benchmark_variable_id"])
    if base.casefold().endswith(" benchmark"):
        base = base[: -len(" benchmark")]
    if bool(variable.get("proxy")):
        base += " - Redfin sales-value proxy"
    elif bool(variable.get("scenario")):
        base += " - sensitivity/scenario"
    layer = "retrospective monthly" if spec.key == "B" else "real-time monthly"
    return f"{base} - {layer}"


def _contract_dictionary_rows(
    root: Path, existing: pd.DataFrame
) -> pd.DataFrame:
    if "variable_id" not in existing:
        raise ValueError("Global data dictionary requires variable_id")
    anchors = existing.loc[
        ~existing["variable_id"].astype("string").str.endswith(
            ("_monthly_hist", "_monthly_rt"), na=False
        )
    ].copy()
    duplicate_anchors = anchors.loc[
        anchors["variable_id"].duplicated(keep=False), "variable_id"
    ]
    if not duplicate_anchors.empty:
        raise ValueError(
            "Global data dictionary has duplicate benchmark IDs: "
            f"{sorted(set(duplicate_anchors.astype(str)))}"
        )
    anchor_index = anchors.set_index("variable_id", drop=False)
    rows: list[dict[str, Any]] = []
    for spec in _SPECS.values():
        catalog = _catalog(root, spec).set_index("transformation_id", drop=False)
        for variable in _variables(root, spec):
            benchmark_id = str(variable["benchmark_variable_id"])
            if benchmark_id not in anchor_index.index:
                raise KeyError(
                    f"Global data dictionary has no Version A anchor {benchmark_id}"
                )
            transformation_id = str(variable["transformation_id"])
            if transformation_id not in catalog.index:
                raise KeyError(
                    f"Version {spec.key} catalog has no {transformation_id}"
                )
            anchor = anchor_index.loc[benchmark_id].to_dict()
            transformation = catalog.loc[transformation_id].to_dict()
            base_definition = _text(anchor.get("definition")).rstrip(".")
            indicator_sources = _pipe(variable.get("indicator_source_ids"))
            indicator_variables = _pipe(variable.get("indicator_variable_ids"))
            notes = [
                f"database_version={spec.database_name}",
                f"benchmark_variable_id={benchmark_id}",
                f"output_status={variable.get('output_status')}",
                f"expected_value_policy={variable.get('expected_value_policy')}",
            ]
            if indicator_sources:
                notes.append(f"indicator_source_ids={indicator_sources}")
            configured_notes = _text(variable.get("methodological_notes"))
            if configured_notes:
                notes.append(configured_notes)
            row = {column: anchor.get(column, "") for column in existing.columns}
            row.update(
                {
                    "variable_id": str(variable["variable_id"]),
                    "display_name": _display_name(variable, anchor, spec),
                    "definition": (
                        f"{base_definition}. Version {spec.key} monthly "
                        f"construction: {variable.get('expected_value_policy')}."
                    ),
                    "provider": (
                        "LA Housing Database; derived from the frozen Version A "
                        "benchmark and archived indicators"
                    ),
                    "original_field": _pipe(
                        [benchmark_id, indicator_variables]
                    ),
                    "unit": variable.get("unit", anchor.get("unit", "")),
                    "native_frequency": "mixed source frequencies",
                    "final_frequency": "monthly",
                    "geography": "county",
                    "seasonal_adjustment": "source-specific; none added",
                    "smoothing": "source-specific; no universal smoothing",
                    "transformation": transformation_id,
                    "missing_value_policy": (
                        _text(transformation.get("missing_value_policy"))
                        or str(variable.get("expected_value_policy", "preserve null"))
                    ),
                    "revision_policy": spec.revision_policy,
                    "methodological_notes": "; ".join(notes),
                    "data_domain": "finance",
                    "source_geography": (
                        "county plus parameter geography recorded in normalized lineage"
                    ),
                    "source_frequency": "mixed source frequencies recorded in lineage",
                    "model_frequency": "monthly",
                    "is_observed": False,
                    "is_constructed": True,
                    "is_proxy": bool(variable.get("proxy")),
                    "active_status": "Active",
                    "observed_constructed_proxy_or_scenario": _semantics(variable),
                }
            )
            rows.append(row)
    result = pd.DataFrame(rows, columns=existing.columns)
    if len(result) != 2 * _EXPECTED_VARIABLE_COUNT:
        raise AssertionError("Monthly finance dictionary registration is incomplete")
    if result["variable_id"].duplicated().any():
        raise ValueError("Monthly finance dictionaries overlap")
    return result


def _merge_dictionary(root: Path, base: pd.DataFrame | None = None) -> tuple[pd.DataFrame, int]:
    path = root / "metadata" / "data_dictionary.csv"
    existing = _read_csv(path, GLOBAL_DICTIONARY_COLUMNS) if base is None else base.copy()
    for column in GLOBAL_DICTIONARY_COLUMNS:
        if column not in existing:
            existing[column] = ""
    # General dictionaries may inherit YAML key insertion order. Registration
    # must publish the stable CSV schema, while retaining every extension field
    # and all unrelated values rather than projecting only known columns.
    extension_columns = [
        column for column in existing.columns
        if column not in GLOBAL_DICTIONARY_COLUMNS
    ]
    existing = existing.loc[:, [*GLOBAL_DICTIONARY_COLUMNS, *extension_columns]]
    incoming = _contract_dictionary_rows(root, existing)
    owned = existing["variable_id"].astype("string").str.endswith(
        ("_monthly_hist", "_monthly_rt"), na=False
    )
    merged = pd.concat(
        [existing.loc[~owned], incoming], ignore_index=True, sort=False
    )[existing.columns]
    if (root / "config" / "variable_metadata.csv").exists():
        merged = enrich_variable_metadata(merged, root)
    return merged, len(incoming)


def _blank_catalog_row(columns: pd.Index | list[str]) -> dict[str, Any]:
    return {column: "" for column in columns}


def _product_rows(root: Path, columns: pd.Index | list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for spec in _SPECS.values():
        variables = _variables(root, spec)
        variable_ids = _pipe(item["variable_id"] for item in variables)
        units = _pipe(item.get("unit") for item in variables)
        common = {
            "Frequency": "monthly",
            "Geography": "California counties",
            "provider": "LA Housing Database",
            "official_source_page": "",
            "exact_download_url_or_api": "",
            "access_method": "generated_repository_output",
            "active_status": "Active",
            "provenance_status": "checksum-gated completed output pair",
            "manual_download_required": False,
            "manual_reason": "",
            "source_manifest_path": spec.build_manifest_path,
            "native_frequency": "monthly modeled product",
            "analytical_frequency": "monthly",
            "geography_level": "county",
            "geography_name": "California counties",
            "geography_id": "county_fips",
            "variable_ids": variable_ids,
            "seasonal_adjustment": "source-specific inputs; none added",
            "smoothing": "source-specific inputs; no universal smoothing",
            "units": units,
            "destination_clean_table": "",
            "update_command": spec.update_command,
            "missing_value_policy": (
                "Preserve all explicit canonical-null and suppressed rows; "
                "never substitute a proxy for a canonical target."
            ),
            "revision_policy": spec.revision_policy,
            "known_limitations": (
                "Canonical financed-purchase flow and calibrated debt/equity "
                "remain explicit nulls until method-compatible inputs exist."
            ),
            "license_or_use_notes": (
                "Derived repository output; upstream provider attribution and "
                "use terms continue to apply."
            ),
        }

        observation = _blank_catalog_row(columns)
        observation.update(common)
        observation.update(
            {
                "Source": f"Housing finance Version {spec.key} observations",
                "Key variables": variable_ids,
                "source_id": spec.observation_product_id,
                "product": f"{spec.database_name} observations",
                "observed_constructed_proxy_or_scenario": (
                    "modeled retrospective monthly observations"
                    if spec.key == "B"
                    else "cutoff-safe modeled real-time observations"
                ),
                "raw_file_format": "parquet",
                "raw_file_path": spec.observation_path,
                "original_filename": Path(spec.observation_path).name,
                "destination_analytical_table": spec.observation_table,
                "notes": (
                    "Version B is one atomic revised-history file committed only "
                    "with its companion lineage and completion marker."
                    if spec.key == "B"
                    else "Version C is an append-only information_cutoff_date "
                    "partition family; only checksum-valid completed pairs are active."
                ),
            }
        )
        rows.append(observation)

        lineage = _blank_catalog_row(columns)
        lineage.update(common)
        lineage.update(
            {
                "Source": f"Housing finance Version {spec.key} normalized lineage",
                "Key variables": (
                    "lineage_id|variable_id|input_role|source_id|source_vintage|"
                    "release_date|retrieved_at|source_filename|checksum_sha256"
                ),
                "source_id": spec.lineage_product_id,
                "product": f"{spec.database_name} per-input lineage",
                "observed_constructed_proxy_or_scenario": "source lineage metadata",
                "raw_file_format": "parquet",
                "raw_file_path": spec.lineage_path,
                "original_filename": Path(spec.lineage_path).name,
                "destination_analytical_table": spec.lineage_table,
                "notes": (
                    "One normalized lineage product paired with the Version B "
                    "observation file."
                    if spec.key == "B"
                    else "One normalized lineage partition for every completed "
                    "Version C observation cutoff partition."
                ),
            }
        )
        rows.append(lineage)

        completion = _blank_catalog_row(columns)
        completion.update(common)
        completion.update(
            {
                "Source": f"Housing finance Version {spec.key} completion marker",
                "Frequency": (
                    "per completed rebuild" if spec.key == "B" else "per cutoff vintage"
                ),
                "Key variables": (
                    "schema_version|database_version|information_cutoff_date|"
                    "observation_path|observation_sha256|observation_rows|"
                    "lineage_path|lineage_sha256|lineage_rows|completed_at"
                ),
                "source_id": spec.completion_product_id,
                "product": f"{spec.database_name} completion gate",
                "observed_constructed_proxy_or_scenario": "completion metadata",
                "raw_file_format": "json",
                "raw_file_path": spec.completion_path,
                "original_filename": Path(spec.completion_path).name,
                "native_frequency": (
                    "per completed rebuild" if spec.key == "B" else "per cutoff vintage"
                ),
                "analytical_frequency": "not applicable",
                "variable_ids": "",
                "units": "metadata",
                "destination_analytical_table": "completion gate; not a fact table",
                "notes": (
                    "The marker commits the exact Version B observation/lineage "
                    "checksums and row counts; an unmarked pair is incomplete."
                    if spec.key == "B"
                    else "Each append-only cutoff is active only when its sibling "
                    "_COMPLETE.json commits the exact observation/lineage pair."
                ),
            }
        )
        rows.append(completion)
    return pd.DataFrame(rows, columns=columns)


def _preserve_catalog_build_fields(
    incoming: pd.DataFrame, existing: pd.DataFrame
) -> pd.DataFrame:
    if existing.empty:
        return incoming
    prior = existing.loc[existing["source_id"].isin(PRODUCT_SOURCE_IDS)].copy()
    if prior.empty:
        return incoming
    prior = prior.drop_duplicates("source_id", keep="last").set_index("source_id")
    result = incoming.copy()
    for index, source_id in result["source_id"].items():
        if source_id not in prior.index:
            continue
        # Only a row written from a completed build has a provider vintage.
        # Do not carry arbitrary stale placeholder content into the new
        # authoritative product definitions.
        if not _text(prior.at[source_id, "provider_vintage"]):
            continue
        for column in _CATALOG_BUILD_FIELDS:
            value = _text(prior.at[source_id, column])
            if value:
                result.at[index, column] = value
    return result


def _merge_catalog(
    root: Path, build: _CompletedBuild | None = None
) -> tuple[pd.DataFrame, int]:
    path = root / "metadata" / "data_catalog.csv"
    existing = _read_csv(path, GLOBAL_CATALOG_COLUMNS)
    incoming = _preserve_catalog_build_fields(
        _product_rows(root, existing.columns), existing
    )
    if build is not None:
        incoming = _catalog_with_build(root, incoming, build)
    owned = existing["source_id"].isin(PRODUCT_SOURCE_IDS)
    merged = pd.concat(
        [existing.loc[~owned], incoming], ignore_index=True, sort=False
    )[existing.columns]
    return merged, len(incoming)


def _utc_iso(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def _catalog_with_build(
    root: Path, incoming: pd.DataFrame, build: _CompletedBuild
) -> pd.DataFrame:
    spec = _SPECS[build.version]
    result = incoming.copy()
    existing_c_cutoff = ""
    if build.version == "C":
        prior = result.loc[
            result["source_id"].eq(spec.observation_product_id), "provider_vintage"
        ]
        existing_c_cutoff = (
            _text(prior.iloc[0]).removeprefix("information_cutoff_date=")
            if len(prior)
            else ""
        )
        if (
            existing_c_cutoff
            and build.information_cutoff_date
            and existing_c_cutoff > build.information_cutoff_date
        ):
            return result

    completed = build.completed_at
    verified = pd.Timestamp(completed).date().isoformat()
    vintage = (
        f"information_cutoff_date={build.information_cutoff_date}"
        if build.version == "C"
        else f"completed_at={completed}"
    )
    observation_months = pd.to_datetime(
        build.observations.get("month"), errors="coerce"
    )
    lineage_months = pd.to_datetime(build.lineage.get("month"), errors="coerce")
    values = {
        spec.observation_product_id: {
            "path": build.observation_path,
            "rows": len(build.observations),
            "start": observation_months.min(),
            "end": observation_months.max(),
            "checksum": sha256_file(build.observation_path),
        },
        spec.lineage_product_id: {
            "path": build.lineage_path,
            "rows": len(build.lineage),
            "start": lineage_months.min(),
            "end": lineage_months.max(),
            "checksum": sha256_file(build.lineage_path),
        },
        spec.completion_product_id: {
            "path": build.completion_path,
            "rows": 1,
            "start": (
                pd.Timestamp(build.information_cutoff_date)
                if build.information_cutoff_date
                else pd.NaT
            ),
            "end": (
                pd.Timestamp(build.information_cutoff_date)
                if build.information_cutoff_date
                else pd.NaT
            ),
            "checksum": sha256_file(build.completion_path),
        },
    }
    for source_id, metadata in values.items():
        mask = result["source_id"].eq(source_id)
        if mask.sum() != 1:
            raise ValueError(f"Catalog product row is not unique: {source_id}")
        base_note = _text(result.loc[mask, "notes"].iloc[0])
        base_note = base_note.split(" Registered completed output:", 1)[0]
        result.loc[mask, "raw_file_path"] = _relative(root, metadata["path"])
        result.loc[mask, "original_filename"] = metadata["path"].name
        result.loc[mask, "retrieval_date"] = completed
        result.loc[mask, "provider_vintage"] = vintage
        result.loc[mask, "checksum_sha256"] = metadata["checksum"]
        result.loc[mask, "start_date"] = (
            ""
            if pd.isna(metadata["start"])
            else pd.Timestamp(metadata["start"]).date().isoformat()
        )
        result.loc[mask, "end_date"] = (
            ""
            if pd.isna(metadata["end"])
            else pd.Timestamp(metadata["end"]).date().isoformat()
        )
        result.loc[mask, "row_count"] = str(metadata["rows"])
        result.loc[mask, "last_verified"] = verified
        result.loc[mask, "notes"] = (
            f"{base_note} Registered completed output: "
            f"{_relative(root, metadata['path'])}; {vintage}."
        )
    return result


def _completion_path(version: VersionKey, observation_path: Path) -> Path:
    return (
        observation_path.parent / "_COMPLETE.json"
        if version == "C"
        else observation_path.with_suffix(".complete.json")
    )


def _completed_build(
    version: VersionKey,
    *,
    root: Path,
    observation_path: str | Path,
    lineage_path: str | Path,
    completion_path: str | Path | None,
    observations: pd.DataFrame | None,
    lineage: pd.DataFrame | None,
) -> _CompletedBuild:
    observation_file = _resolved(root, observation_path)
    lineage_file = _resolved(root, lineage_path)
    marker_file = (
        _resolved(root, completion_path)
        if completion_path is not None
        else _completion_path(version, observation_file)
    )
    for path in (observation_file, lineage_file, marker_file):
        _relative(root, path)
        if not path.is_file():
            raise FileNotFoundError(path)
    try:
        payload = json.loads(marker_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid completion marker: {marker_file}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid completion marker: {marker_file}")
    cutoff = _text(payload.get("information_cutoff_date")) or None
    expected = {
        "schema_version": _COMPLETION_SCHEMA_VERSION,
        "database_version": version,
        "observation_path": _relative(root, observation_file),
        "lineage_path": _relative(root, lineage_file),
    }
    mismatched = [key for key, value in expected.items() if payload.get(key) != value]
    if version == "B" and cutoff is not None:
        mismatched.append("information_cutoff_date")
    if version == "C":
        try:
            cutoff = pd.Timestamp(cutoff).date().isoformat() if cutoff else None
        except ValueError as exc:
            raise RuntimeError("Version C completion cutoff is invalid") from exc
        if cutoff is None or payload.get("information_cutoff_date") != cutoff:
            mismatched.append("information_cutoff_date")
    actual_observation_sha = sha256_file(observation_file)
    actual_lineage_sha = sha256_file(lineage_file)
    if payload.get("observation_sha256") != actual_observation_sha:
        mismatched.append("observation_sha256")
    if payload.get("lineage_sha256") != actual_lineage_sha:
        mismatched.append("lineage_sha256")
    if mismatched:
        raise RuntimeError(
            f"Completion marker does not commit this output pair: {sorted(set(mismatched))}"
        )

    observation_frame = (
        observations.copy() if observations is not None else pd.read_parquet(observation_file)
    )
    lineage_frame = lineage.copy() if lineage is not None else pd.read_parquet(lineage_file)
    if int(payload.get("observation_rows", -1)) != len(observation_frame):
        raise RuntimeError("Completion marker observation row count mismatch")
    if int(payload.get("lineage_rows", -1)) != len(lineage_frame):
        raise RuntimeError("Completion marker lineage row count mismatch")
    if version == "C" and "information_cutoff_date" in observation_frame:
        observed_cutoffs = {
            pd.Timestamp(value).date().isoformat()
            for value in observation_frame["information_cutoff_date"].dropna().unique()
        }
        if observed_cutoffs != {cutoff}:
            raise RuntimeError("Version C observations do not match the completion cutoff")
    completed_value = _text(payload.get("completed_at"))
    if not completed_value:
        raise RuntimeError("Completion marker has no completed_at timestamp")
    completed_at = _utc_iso(completed_value)
    return _CompletedBuild(
        version=version,
        observations=observation_frame,
        lineage=lineage_frame,
        observation_path=observation_file,
        lineage_path=lineage_file,
        completion_path=marker_file,
        information_cutoff_date=cutoff,
        completed_at=completed_at,
    )


def _asset_paths(
    root: Path,
    spec: _VersionSpec,
    asset_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[Path]]:
    benchmark = root / spec.benchmark_path
    if not benchmark.is_file():
        raise FileNotFoundError(benchmark)
    result: dict[str, list[Path]] = {
        "version_a_housing_finance_benchmark": [benchmark]
    }
    for record in asset_records.values():
        source_id = _text(record.get("source_id"))
        relative_path = _text(record.get("relative_path"))
        if not source_id or not relative_path:
            raise ValueError("Every asset record requires source_id and relative_path")
        path = _resolved(root, relative_path)
        _relative(root, path)
        if not path.is_file():
            raise FileNotFoundError(path)
        expected = _text(record.get("checksum_sha256")).lower()
        if expected and sha256_file(path) != expected:
            raise RuntimeError(f"Asset checksum mismatch: {path}")
        paths = [path]
        clean_path = _text(record.get("_clean_path"))
        if clean_path:
            clean = _resolved(root, clean_path)
            _relative(root, clean)
            if not clean.is_file():
                raise FileNotFoundError(clean)
            expected_clean = _text(record.get("_clean_checksum_sha256")).lower()
            if expected_clean and sha256_file(clean) != expected_clean:
                raise RuntimeError(f"Clean asset checksum mismatch: {clean}")
            paths.append(clean)
        result[source_id] = paths
    return result


def _mapping_text(
    frame: pd.DataFrame, key: str, value: str
) -> str:
    if key not in frame or value not in frame:
        return ""
    pairs = frame[[key, value]].dropna().astype(str).drop_duplicates()
    records = sorted(
        f"{row[0]}={row[1]}" for row in pairs.itertuples(index=False, name=None)
    )
    return "|".join(records)


def _transformation_rows(
    root: Path,
    build: _CompletedBuild,
    asset_records: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    spec = _SPECS[build.version]
    required_observation = {"transformation_id", "lineage_id"}
    required_lineage = {
        "lineage_id",
        "source_id",
        "source_vintage",
        "source_filename",
        "checksum_sha256",
        "retrieved_at",
    }
    if not required_observation.issubset(build.observations.columns):
        raise ValueError(
            "Observations are missing transformation metadata columns: "
            f"{sorted(required_observation - set(build.observations.columns))}"
        )
    if not required_lineage.issubset(build.lineage.columns):
        raise ValueError(
            "Lineage is missing transformation metadata columns: "
            f"{sorted(required_lineage - set(build.lineage.columns))}"
        )
    catalog = _catalog(root, spec)
    used = list(
        dict.fromkeys(
            build.observations["transformation_id"].dropna().astype(str).tolist()
        )
    )
    catalog_ids = set(catalog["transformation_id"])
    missing = sorted(set(used) - catalog_ids)
    if missing:
        raise ValueError(f"Used transformations are absent from the catalog: {missing}")
    catalog = catalog.set_index("transformation_id", drop=False)
    path_index = _asset_paths(root, spec, asset_records)
    output_paths = _pipe(
        _relative(root, path)
        for path in (
            build.observation_path,
            build.lineage_path,
            build.completion_path,
        )
    )
    output_checksums = _pipe(
        f"{_relative(root, path)}={sha256_file(path)}"
        for path in (
            build.observation_path,
            build.lineage_path,
            build.completion_path,
        )
    )
    rows: list[dict[str, Any]] = []
    for transformation_id in used:
        observations = build.observations.loc[
            build.observations["transformation_id"].astype(str).eq(transformation_id)
        ]
        lineage_ids = set(observations["lineage_id"].dropna().astype(str))
        lineage = build.lineage.loc[
            build.lineage["lineage_id"].astype(str).isin(lineage_ids)
        ].copy()
        if lineage.empty:
            raise ValueError(f"No lineage rows resolve {transformation_id}")
        source_ids = sorted(set(lineage["source_id"].dropna().astype(str)))
        unresolved = sorted(set(source_ids) - set(path_index))
        if unresolved:
            raise ValueError(
                f"No actual input path was supplied for sources {unresolved}"
            )
        input_paths = [
            path for source_id in source_ids for path in path_index[source_id]
        ]
        actual_input_files = _pipe(_relative(root, path) for path in input_paths)
        input_checksums = _pipe(
            f"{_relative(root, path)}={sha256_file(path)}" for path in input_paths
        )
        retrieved = pd.to_datetime(
            lineage["retrieved_at"], errors="coerce", utc=True, format="mixed"
        )
        retrieved_at = retrieved.max().isoformat() if retrieved.notna().any() else ""
        source_vintages = _mapping_text(lineage, "source_id", "source_vintage")
        vintage_prefix = (
            f"information_cutoff_date={build.information_cutoff_date}"
            if build.version == "C"
            else "database_version=B_retrospective_history"
        )
        catalog_row = catalog.loc[transformation_id]
        description_parts = [
            _text(catalog_row.get("concept")),
            _text(catalog_row.get("formula")),
            _text(catalog_row.get("benchmark_rule")),
        ]
        description = "; ".join(value for value in description_parts if value)
        rows.append(
            {
                "transformation_id": transformation_id,
                "source_id": _pipe(source_ids),
                "input_file": actual_input_files,
                "output_file": output_paths,
                "input_rows_or_shape": (
                    f"{len(lineage)} normalized input-lineage rows across "
                    f"{len(input_paths)} actual input file(s)"
                ),
                "output_rows": str(len(observations)),
                "description": description,
                "code_module": (
                    "src.housing_finance_monthly.retrospective"
                    if build.version == "B"
                    else "src.housing_finance_monthly.realtime"
                ),
                "source_vintage": _pipe([vintage_prefix, source_vintages]),
                "executed_at": build.completed_at,
                "source_filename": _mapping_text(
                    lineage, "source_id", "source_filename"
                ),
                "checksum_sha256": _pipe(
                    [
                        _mapping_text(lineage, "source_id", "checksum_sha256"),
                        input_checksums,
                        output_checksums,
                    ]
                ),
                "retrieved_at": retrieved_at,
            }
        )
    return pd.DataFrame(rows, columns=TRANSFORMATION_LOG_COLUMNS)


def _merge_transformation_log(
    root: Path,
    build: _CompletedBuild,
    asset_records: Mapping[str, Mapping[str, Any]],
) -> tuple[pd.DataFrame, int]:
    path = root / "metadata" / "transformation_log.csv"
    existing = _read_csv(path, TRANSFORMATION_LOG_COLUMNS)
    incoming = _transformation_rows(root, build, asset_records)
    transformation_ids = existing["transformation_id"].fillna("").astype(str)
    if build.version == "B":
        owned = transformation_ids.str.startswith("version_b_")
    else:
        prefix = f"information_cutoff_date={build.information_cutoff_date}"
        owned = transformation_ids.str.startswith("version_c_") & existing[
            "source_vintage"
        ].fillna("").astype(str).str.split("|").map(
            lambda values: prefix in values
        )
    merged = pd.concat(
        [existing.loc[~owned], incoming], ignore_index=True, sort=False
    )[existing.columns]
    return merged, len(incoming)


def register_monthly_finance_contracts(
    *, root: str | Path = ROOT
) -> MetadataRegistrationResult:
    """Merge both 58-ID contracts and six product rows into global metadata.

    This function does not require outputs and does not write transformation
    execution rows.  Existing non-B/C rows retain their original order and
    contents.  Each target CSV is replaced atomically.
    """

    project_root = Path(root)
    dictionary, dictionary_count = _merge_dictionary(project_root)
    catalog, catalog_count = _merge_catalog(project_root)
    _atomic_csv(dictionary, project_root / "metadata" / "data_dictionary.csv")
    _atomic_csv(catalog, project_root / "metadata" / "data_catalog.csv")
    return MetadataRegistrationResult(
        dictionary_rows=dictionary_count,
        catalog_rows=catalog_count,
        transformation_rows=0,
    )


def register_monthly_finance_build(
    version: str,
    *,
    root: str | Path = ROOT,
    observation_path: str | Path,
    lineage_path: str | Path,
    completion_path: str | Path | None = None,
    asset_records: Mapping[str, Mapping[str, Any]],
    observations: pd.DataFrame | None = None,
    lineage: pd.DataFrame | None = None,
) -> MetadataRegistrationResult:
    """Register one checksum-complete B build or C cutoff vintage.

    Call this only after the observation/lineage pair and completion marker
    have been committed successfully.  The function independently validates
    those artifacts before preparing any metadata write.  Version B execution
    rows replace prior Version B execution rows.  Version C execution rows
    replace only the same cutoff and preserve all other cutoff vintages.
    """

    project_root = Path(root)
    key = _version_key(version)
    build = _completed_build(
        key,
        root=project_root,
        observation_path=observation_path,
        lineage_path=lineage_path,
        completion_path=completion_path,
        observations=observations,
        lineage=lineage,
    )

    # Prepare and fully validate all frames before any global CSV changes.
    dictionary, dictionary_count = _merge_dictionary(project_root)
    catalog, catalog_count = _merge_catalog(project_root, build)
    transformations, transformation_count = _merge_transformation_log(
        project_root, build, asset_records
    )

    _atomic_csv(dictionary, project_root / "metadata" / "data_dictionary.csv")
    _atomic_csv(catalog, project_root / "metadata" / "data_catalog.csv")
    _atomic_csv(
        transformations, project_root / "metadata" / "transformation_log.csv"
    )
    return MetadataRegistrationResult(
        dictionary_rows=dictionary_count,
        catalog_rows=catalog_count,
        transformation_rows=transformation_count,
        version=key,
        information_cutoff_date=build.information_cutoff_date,
    )


__all__ = [
    "GLOBAL_CATALOG_COLUMNS",
    "GLOBAL_DICTIONARY_COLUMNS",
    "MetadataRegistrationResult",
    "PRODUCT_SOURCE_IDS",
    "TRANSFORMATION_LOG_COLUMNS",
    "register_monthly_finance_build",
    "register_monthly_finance_contracts",
]
