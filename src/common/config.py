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


def load_config(root: Path = ROOT) -> ProjectConfig:
    source_doc = _yaml(root / "config" / "sources.yaml")
    pipeline_doc = _yaml(root / "config" / "pipeline.yaml")
    variable_doc = _yaml(root / "config" / "variables.yaml")
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
        variables=tuple(variable_doc["variables"]),
        geographies=tuple(geography_doc["geographies"]),
        crosswalk_version=str(geography_doc["crosswalk_version"]),
    )
