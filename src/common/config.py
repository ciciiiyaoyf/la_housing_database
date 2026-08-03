"""Validated YAML configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    provider: str
    dataset_name: str
    source_page_url: str
    download_url: str
    native_frequency: str
    geography_levels: tuple[str, ...]
    file_format: str
    key_variables: tuple[str, ...] = ()
    raw_subdir: str = ""
    access_method: str = "official_download"
    destination_clean_table: str = ""
    destination_analytical_table: str = ""
    enabled: bool = True
    auto_download: bool = True
    manual_reason: str = ""
    manual_filename: str = ""
    selected_start_date: str = ""
    selected_end_date: str = ""
    expected_columns: tuple[str, ...] = ()
    series_ids: tuple[str, ...] = ()
    rolling_window_months: int | None = None
    methodology_notes: str = ""
    license_or_use_notes: str = ""
    revision_notes: str = ""
    provenance_status: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceConfig":
        required = {
            "source_id",
            "provider",
            "dataset_name",
            "source_page_url",
            "download_url",
            "native_frequency",
            "geography_levels",
            "file_format",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Source config missing {sorted(missing)}")
        unknown = raw.keys() - {field.name for field in cls.__dataclass_fields__.values()}
        if unknown:
            raise ValueError(f"Unknown keys for {raw.get('source_id')}: {sorted(unknown)}")
        return cls(
            **{
                **raw,
                "geography_levels": tuple(raw["geography_levels"]),
                "expected_columns": tuple(raw.get("expected_columns", ())),
                "series_ids": tuple(raw.get("series_ids", ())),
                "key_variables": tuple(raw.get("key_variables", ())),
            }
        )


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    verified_at: str
    sources: tuple[SourceConfig, ...]
    pipeline: dict[str, Any]
    variables: tuple[dict[str, Any], ...]
    geographies: tuple[dict[str, Any], ...]
    crosswalk_version: str

    def selected_sources(self, source: str | None = None) -> tuple[SourceConfig, ...]:
        enabled = tuple(item for item in self.sources if item.enabled)
        if source is None:
            return enabled
        selected = tuple(item for item in enabled if item.source_id == source)
        if not selected:
            valid = ", ".join(item.source_id for item in enabled)
            raise ValueError(f"Unknown or disabled source '{source}'. Choose from: {valid}")
        return selected


def _yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _classified_variable(raw: dict[str, Any]) -> dict[str, Any]:
    """Add stable catalog/modeling classification fields."""
    variable = dict(raw)
    variable_id = str(variable["variable_id"])
    constructed = (
        str(variable.get("provider", "")).lower() == "constructed"
        or "growth_" in variable_id
        or variable_id
        in {
            "mortgage_rate_30y_monthly_average",
            "mortgage_rate_30y_monthly_median",
            "mortgage_rate_30y_month_end",
            "mortgage_rate_30y_observation_count",
        }
    )
    if variable_id.endswith("_moe"):
        market_segment, role, domain = "Data quality", "Metadata", "uncertainty"
    elif any(token in variable_id for token in ("zori", "rent", "renter", "hvs_")):
        market_segment, role, domain = "Renter", "Outcome", "rental_market"
    elif any(
        token in variable_id
        for token in (
            "unemployment",
            "labor_force",
            "employment",
            "ces_",
        )
    ):
        market_segment, role, domain = "Labor", "Control", "labor_market"
    elif variable_id.startswith("acs_"):
        market_segment, role, domain = "Demographic", "Predictor", "structural"
    elif variable_id.startswith(("cpi_", "bea_")):
        market_segment, role, domain = "Macro", "Control", "macro"
    elif "permit" in variable_id:
        market_segment, role, domain = "Supply", "Supply", "construction"
    elif "mortgage" in variable_id:
        market_segment, role, domain = "Affordability", "Control", "finance"
    elif variable_id.startswith("fhfa_"):
        market_segment, role, domain = "Homeowner", "Benchmark", "home_values"
    else:
        market_segment, role, domain = "Homeowner", "Outcome", "for_sale_market"
    variable.setdefault("market_segment", market_segment)
    variable.setdefault("variable_role", role)
    variable.setdefault("data_domain", domain)
    variable.setdefault("source_geography", variable.get("geography"))
    variable.setdefault("source_frequency", variable.get("native_frequency"))
    variable.setdefault("model_frequency", variable.get("final_frequency"))
    variable.setdefault("is_observed", not constructed)
    variable.setdefault("is_constructed", constructed)
    variable.setdefault("is_proxy", False)
    variable.setdefault("active_status", "Active")
    return variable


def load_config(root: Path = ROOT) -> ProjectConfig:
    source_doc = _yaml(root / "config" / "sources.yaml")
    pipeline_doc = _yaml(root / "config" / "pipeline.yaml")
    variable_doc = _yaml(root / "config" / "variables.yaml")
    version_a_variable_path = root / "config" / "version_a_variables.yaml"
    version_a_variable_doc = (
        _yaml(version_a_variable_path)
        if version_a_variable_path.exists()
        else {"variables": []}
    )
    geography_doc = _yaml(root / "config" / "geographies.yaml")
    sources = tuple(SourceConfig.from_dict(item) for item in source_doc["sources"])
    ids = [source.source_id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("source_id values must be unique")
    return ProjectConfig(
        root=root,
        verified_at=str(source_doc["verified_at"]),
        sources=sources,
        pipeline=pipeline_doc,
        variables=tuple(
            _classified_variable(item)
            for item in [
                *variable_doc["variables"],
                *version_a_variable_doc["variables"],
            ]
        ),
        geographies=tuple(geography_doc["geographies"]),
        crosswalk_version=str(geography_doc["crosswalk_version"]),
    )
