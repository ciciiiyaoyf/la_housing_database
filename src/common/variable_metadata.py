"""Explicit variable units and temporal semantics without transforming values.

The versioned CSV is the authority for classifications, including variables
added since the reference workbook.  Neither variable names nor temporal types
are used to choose frequency conversions.  Existing source-specific conversion
rules continue to own that decision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path("config/variable_metadata.csv")
PROVENANCE_PATH = Path("config/variable_metadata_provenance.json")
ENRICHMENT_COLUMNS = (
    "unit_simplified",
    "temporal_type",
    "temporal_type_note",
    "monetary_basis",
    "unit_simplification_note",
    "metadata_source",
    "workbook_temporal_type",
    "workbook_temporal_type_note",
    "metadata_review_note",
)
REGISTRY_COLUMNS = ("variable_id", "unit_original", *ENRICHMENT_COLUMNS)
TEMPORAL_TYPES = frozenset(
    {
        "flow", "stock", "period_average", "period_statistic", "derived_ratio",
        "growth_change", "rolling_window", "cumulative", "metadata_or_assumption",
    }
)
# These are label equivalences, not numerical conversions or admissible ranges.
# In particular, a growth rate labeled "share" may be negative or exceed one.
UNIT_SIMPLIFICATIONS = {
    "current_dollars": "USD",
    "current_dollars_per_month": "USD/month",
    "dollars_per_month": "USD/month",
    "inflation_adjusted_USD_per_year": "USD/year",
    "housing_units": "units",
    "fraction": "share",
    "proportion": "share",
    "current_dollars_per_person": "USD/person",
    "current_dollars_per_exposure": "USD/exposure",
}


def load_variable_metadata(root: Path = ROOT) -> pd.DataFrame:
    """Load and structurally validate the versioned, per-variable registry.

    Missing files fail explicitly.  Callers supporting legacy fixture projects
    may first check ``root / REGISTRY_PATH``; they must not silently ignore a
    missing registry after enabling this contract.
    """
    path = Path(root) / REGISTRY_PATH
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(set(REGISTRY_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Variable metadata registry missing columns: {missing}")
    required = (
        "variable_id", "unit_original", "unit_simplified", "temporal_type",
        "temporal_type_note", "monetary_basis", "unit_simplification_note",
        "metadata_source",
    )
    errors: list[str] = []
    for column in required:
        if frame[column].str.strip().eq("").any():
            errors.append(f"blank {column}")
    duplicates = frame.loc[frame["variable_id"].duplicated(), "variable_id"].tolist()
    if duplicates:
        errors.append(f"duplicate variable IDs: {duplicates}")
    invalid = sorted(set(frame["temporal_type"]) - TEMPORAL_TYPES)
    if invalid:
        errors.append(f"unknown temporal types: {invalid}")
    invalid_workbook = sorted(
        set(frame["workbook_temporal_type"]) - TEMPORAL_TYPES - {""}
    )
    if invalid_workbook:
        errors.append(f"unknown workbook temporal types: {invalid_workbook}")
    expected_units = frame["unit_original"].map(
        lambda value: UNIT_SIMPLIFICATIONS.get(value, value)
    )
    bad_units = frame.loc[
        frame["unit_simplified"].ne(expected_units), "variable_id"
    ].tolist()
    if bad_units:
        errors.append(f"unsupported unit simplification for: {bad_units}")
    changed = frame["workbook_temporal_type"].ne("") & frame[
        "workbook_temporal_type"
    ].ne(frame["temporal_type"])
    unexplained = frame.loc[
        changed & frame["metadata_review_note"].str.strip().eq(""), "variable_id"
    ].tolist()
    if unexplained:
        errors.append(f"undocumented workbook classification changes: {unexplained}")
    if errors:
        raise ValueError("Invalid variable metadata registry: " + "; ".join(errors))
    return frame.loc[:, REGISTRY_COLUMNS].copy()


def load_temporal_type_definitions(root: Path = ROOT) -> dict[str, str]:
    """Return the workbook's documented temporal-type definitions."""
    provenance = json.loads((Path(root) / PROVENANCE_PATH).read_text(encoding="utf-8"))
    return dict(provenance["temporal_type_definitions"])


def validate_variable_metadata(
    frame: pd.DataFrame,
    root: Path = ROOT,
    *,
    require_all: bool = False,
    registry: pd.DataFrame | None = None,
) -> list[str]:
    """Report unmapped IDs, changed units and stale metadata in a catalog/frame.

    Repeated IDs are permitted for observation frames.  ``require_all`` is for
    a complete catalog: identifiers must be nonblank and unique, all registry
    IDs and enrichment columns must be present, and required labels nonblank.
    Unit compatibility is exact: changing a declared unit requires an explicit
    registry update, even if its simplified display label would be the same.
    """
    if "variable_id" not in frame:
        return ["Variable metadata input missing variable_id column"]
    metadata = load_variable_metadata(root) if registry is None else registry
    lookup = metadata.set_index("variable_id")
    errors: list[str] = []
    identifiers = frame["variable_id"].fillna("").astype(str)
    unknown = sorted(set(identifiers) - set(lookup.index))
    if unknown:
        errors.append(f"Variable metadata missing mappings for: {unknown}")
    if require_all:
        if identifiers.str.strip().eq("").any():
            errors.append("Variable metadata catalog contains blank variable_id")
        duplicates = sorted(set(identifiers.loc[identifiers.duplicated()]))
        if duplicates:
            errors.append(f"Variable metadata catalog duplicate variable IDs: {duplicates}")
        missing_columns = [column for column in ENRICHMENT_COLUMNS if column not in frame]
        if missing_columns:
            errors.append(f"Variable metadata catalog missing enrichment columns: {missing_columns}")
        # Workbook fields and review notes may legitimately be empty for
        # configured additions or unmodified workbook classifications.
        required_labels = (
            "unit_simplified", "temporal_type", "temporal_type_note",
            "monetary_basis", "unit_simplification_note", "metadata_source",
        )
        for column in required_labels:
            if column in frame and frame[column].fillna("").astype(str).str.strip().eq("").any():
                errors.append(f"Variable metadata catalog contains blank {column}")
        absent = sorted(set(lookup.index) - set(identifiers))
        if absent:
            errors.append(f"Variable metadata catalog missing registry IDs: {absent}")
    known = identifiers.isin(lookup.index)
    for column, registry_column in (
        ("unit", "unit_original"),
        ("unit_original", "unit_original"),
        *((name, name) for name in ENRICHMENT_COLUMNS),
    ):
        if column not in frame:
            continue
        expected = identifiers.map(lookup[registry_column]).fillna("")
        actual = frame[column].fillna("").astype(str)
        different = known & actual.ne(expected)
        if different.any():
            bad_ids = sorted(set(identifiers.loc[different]))
            errors.append(f"Variable metadata {column} mismatch for: {bad_ids}")
    return errors


def enrich_variable_metadata(
    frame: pd.DataFrame,
    root: Path = ROOT,
    *,
    strict: bool = True,
) -> pd.DataFrame:
    """Attach authoritative metadata while preserving row order, units and values.

    ``strict=False`` leaves unknown variables' added fields blank, making it
    useful only for diagnostic previews.  Published catalogs and observations
    must use the default strict coverage and unit checks.
    """
    metadata = load_variable_metadata(root)
    if "variable_id" not in frame:
        raise ValueError("Variable metadata input missing variable_id column")
    # Refresh extension fields idempotently; do not accept changed source units.
    input_columns = [name for name in frame if name not in ENRICHMENT_COLUMNS]
    errors = validate_variable_metadata(
        frame.loc[:, input_columns], root, registry=metadata
    )
    if strict and errors:
        raise ValueError("; ".join(errors))
    result = frame.copy()
    identifiers = result["variable_id"].fillna("").astype(str)
    lookup = metadata.set_index("variable_id")
    for column in ENRICHMENT_COLUMNS:
        result[column] = identifiers.map(lookup[column]).fillna("")
    return result
