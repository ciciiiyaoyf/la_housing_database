"""Fixed geography contract for LA and its seven comparison counties."""

from __future__ import annotations

import re
from typing import Any


# Order is intentional: Los Angeles is the focal county and the following
# seven counties are the equal-weight comparison set from the approved method.
COMPARABLE_COUNTIES: tuple[tuple[str, str], ...] = (
    ("06037", "Los Angeles County"),
    ("06059", "Orange County"),
    ("06065", "Riverside County"),
    ("06071", "San Bernardino County"),
    ("06073", "San Diego County"),
    ("06067", "Sacramento County"),
    ("06085", "Santa Clara County"),
    ("06001", "Alameda County"),
)
PRIMARY_COUNTY_FIPS = "06037"
COMPARABLE_COUNTY_FIPS: tuple[str, ...] = tuple(
    county_fips for county_fips, _ in COMPARABLE_COUNTIES
)
PEER_COUNTY_FIPS: tuple[str, ...] = tuple(
    county_fips
    for county_fips in COMPARABLE_COUNTY_FIPS
    if county_fips != PRIMARY_COUNTY_FIPS
)
COMPARABLE_COUNTY_FIPS_TO_NAME: dict[str, str] = dict(COMPARABLE_COUNTIES)
COMPARABLE_COUNTY_NAME_TO_FIPS: dict[str, str] = {
    county_name: county_fips for county_fips, county_name in COMPARABLE_COUNTIES
}

_COUNTY_SUFFIX = re.compile(r",\s*(?:ca|california)\s*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_county_name(value: Any) -> str:
    """Normalize provider county labels without inferring geography by row order."""

    text = _COUNTY_SUFFIX.sub("", str(value).strip())
    return _WHITESPACE.sub(" ", text).casefold()


_NORMALIZED_COUNTY_NAME_TO_FIPS = {
    normalize_county_name(county_name): county_fips
    for county_name, county_fips in COMPARABLE_COUNTY_NAME_TO_FIPS.items()
}


def comparable_county_fips(value: Any) -> str | None:
    """Return a configured FIPS code for a provider county label, if selected."""

    return _NORMALIZED_COUNTY_NAME_TO_FIPS.get(normalize_county_name(value))


LAUS_MEASURES: tuple[tuple[str, str, str], ...] = (
    ("03", "unemployment_rate", "percent"),
    ("04", "unemployment", "persons"),
    ("05", "employment", "persons"),
    ("06", "labor_force", "persons"),
)


def laus_series_id(county_fips: str, measure_code: str) -> str:
    """Build the documented BLS LAUS county-series identifier."""

    normalized_fips = str(county_fips).zfill(5)
    if normalized_fips not in COMPARABLE_COUNTY_FIPS:
        raise ValueError(f"County FIPS is outside the comparison contract: {county_fips}")
    normalized_measure = str(measure_code).zfill(2)
    if normalized_measure not in {code for code, _, _ in LAUS_MEASURES}:
        raise ValueError(f"Unsupported LAUS measure code: {measure_code}")
    return f"LAUCN{normalized_fips}00000000{normalized_measure}"


COMPARABLE_LAUS_SERIES: dict[str, tuple[str, str, str]] = {
    laus_series_id(county_fips, measure_code): (county_fips, variable_id, unit)
    for county_fips in COMPARABLE_COUNTY_FIPS
    for measure_code, variable_id, unit in LAUS_MEASURES
}
COMPARABLE_LAUS_SERIES_IDS: tuple[str, ...] = tuple(COMPARABLE_LAUS_SERIES)
PRIMARY_LAUS_SERIES_IDS: tuple[str, ...] = tuple(
    series_id
    for series_id, (county_fips, _, _) in COMPARABLE_LAUS_SERIES.items()
    if county_fips == PRIMARY_COUNTY_FIPS
)


__all__ = [
    "COMPARABLE_COUNTIES",
    "COMPARABLE_COUNTY_FIPS",
    "COMPARABLE_COUNTY_FIPS_TO_NAME",
    "COMPARABLE_COUNTY_NAME_TO_FIPS",
    "COMPARABLE_LAUS_SERIES",
    "COMPARABLE_LAUS_SERIES_IDS",
    "PEER_COUNTY_FIPS",
    "PRIMARY_COUNTY_FIPS",
    "PRIMARY_LAUS_SERIES_IDS",
    "comparable_county_fips",
    "laus_series_id",
    "normalize_county_name",
]
