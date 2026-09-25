"""Versioned project ZCTA scope kept separate from postal ZIP geography."""

from __future__ import annotations

import json
import re
from pathlib import Path


REFERENCE_RELATIVE_PATH = Path("config") / "la_zcta_reference.json"


def load_zcta_reference(root: Path) -> tuple[tuple[str, ...], dict]:
    """Load and structurally validate the configured Census ZCTA universe."""
    path = root / REFERENCE_RELATIVE_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing versioned ZCTA reference: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("geography_type") != "zcta":
        raise ValueError("ZCTA reference geography_type must be 'zcta'")
    if payload.get("postal_zip_equivalence") is not False:
        raise ValueError("ZCTA reference must explicitly reject postal-ZIP equivalence")
    raw_values = payload.get("zctas")
    if not isinstance(raw_values, list) or not raw_values:
        raise ValueError("ZCTA reference must contain a non-empty zctas list")
    zctas = tuple(str(value) for value in raw_values)
    invalid = [value for value in zctas if not re.fullmatch(r"\d{5}", value)]
    if invalid:
        raise ValueError(f"Invalid ZCTA identifiers in reference: {invalid[:5]}")
    if len(zctas) != len(set(zctas)):
        raise ValueError("ZCTA reference contains duplicate identifiers")
    if tuple(sorted(zctas)) != zctas:
        raise ValueError("ZCTA reference must be sorted")
    return zctas, payload
