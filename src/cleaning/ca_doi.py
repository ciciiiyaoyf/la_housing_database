"""California DOI residential-property premium and exposure cleaning.

The official workbook exposes its underlying observations through an OOXML
pivot cache.  Reading the rendered pivot-table cells would make the result
depend on workbook filters and cached display state, so this module reads the
cache definition and records directly.
"""

from __future__ import annotations

import math
import posixpath
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


SOURCE_ID = "ca_doi_residential_property_part_i"
POLICY_TYPE = "Homeowners Multi-Peril"
EXPECTED_EXPOSURE_YEARS = frozenset(range(2008, 2024))
REQUIRED_FIELDS = ("EXP_YEAR", "POLICY_TYPE", "WP", "WE")
TRANSFORMATION_ID = "ca_doi_homeowners_multi_peril_annual_v1"

SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PIVOT_RECORD_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "pivotCacheRecords"
)

MAX_DEFINITION_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024 * 1024
MAX_RECORD_COUNT = 1_000_000


def _qualified(local_name: str) -> str:
    return f"{{{SPREADSHEET_NS}}}{local_name}"


def _member_xml(
    package: ZipFile, member: str, *, maximum_bytes: int
) -> ET.Element:
    try:
        info = package.getinfo(member)
    except KeyError as exc:
        raise ValueError(f"CA DOI workbook is missing OOXML member {member}") from exc
    if info.file_size > maximum_bytes:
        raise ValueError(
            f"CA DOI OOXML member {member} is unexpectedly large: "
            f"{info.file_size} bytes"
        )
    try:
        return ET.fromstring(package.read(member))
    except ET.ParseError as exc:
        raise ValueError(f"CA DOI OOXML member is invalid XML: {member}") from exc


def _primitive_value(element: ET.Element) -> object:
    local_name = element.tag.rsplit("}", 1)[-1]
    if local_name == "m":
        return None
    value = element.get("v")
    if value is None:
        raise ValueError(
            f"CA DOI pivot-cache value {local_name!r} lacks a v attribute"
        )
    if local_name == "b":
        return value == "1"
    if local_name in {"n", "d", "s", "e"}:
        return value
    raise ValueError(f"Unsupported CA DOI pivot-cache value type: {local_name}")


def _cache_fields(definition: ET.Element) -> list[tuple[str, list[object]]]:
    fields_parent = definition.find(_qualified("cacheFields"))
    if fields_parent is None:
        raise ValueError("CA DOI pivot cache has no cacheFields element")
    fields: list[tuple[str, list[object]]] = []
    for field in fields_parent.findall(_qualified("cacheField")):
        name = str(field.get("name") or "").strip()
        if not name:
            raise ValueError("CA DOI pivot cache contains an unnamed field")
        shared_parent = field.find(_qualified("sharedItems"))
        shared = (
            [_primitive_value(item) for item in list(shared_parent)]
            if shared_parent is not None
            else []
        )
        fields.append((name, shared))
    names = [name for name, _ in fields]
    if len(names) != len(set(names)):
        raise ValueError("CA DOI pivot cache contains duplicate field names")
    return fields


def _resolve_relationship_target(definition_member: str, target: str) -> str:
    normalized_target = target.replace("\\", "/")
    if normalized_target.startswith("/"):
        resolved = posixpath.normpath(normalized_target.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(
                str(PurePosixPath(definition_member).parent),
                normalized_target,
            )
        )
    if resolved == ".." or resolved.startswith("../"):
        raise ValueError("CA DOI pivot-cache relationship escapes the workbook")
    return resolved


def _records_member(package: ZipFile, definition_member: str) -> str:
    definition_path = PurePosixPath(definition_member)
    relationships_member = str(
        definition_path.parent
        / "_rels"
        / f"{definition_path.name}.rels"
    )
    relationships = _member_xml(
        package, relationships_member, maximum_bytes=MAX_DEFINITION_BYTES
    )
    targets = [
        relationship.get("Target")
        for relationship in relationships.findall(
            f"{{{RELATIONSHIP_NS}}}Relationship"
        )
        if relationship.get("Type") == PIVOT_RECORD_RELATIONSHIP
    ]
    targets = [target for target in targets if target]
    if len(targets) != 1:
        raise ValueError(
            "CA DOI pivot-cache definition must reference exactly one records part"
        )
    member = _resolve_relationship_target(definition_member, targets[0])
    if member not in package.namelist():
        raise ValueError(
            f"CA DOI pivot-cache records relationship is missing: {member}"
        )
    return member


def _decode_record_value(element: ET.Element, shared: list[object]) -> object:
    local_name = element.tag.rsplit("}", 1)[-1]
    if local_name != "x":
        return _primitive_value(element)
    raw_index = element.get("v")
    try:
        index = int(str(raw_index))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"CA DOI pivot-cache shared-item index is invalid: {raw_index!r}"
        ) from exc
    if index < 0 or index >= len(shared):
        raise ValueError(
            f"CA DOI pivot-cache shared-item index {index} is out of range"
        )
    return shared[index]


def read_ca_doi_pivot_records(path: Path) -> pd.DataFrame:
    """Return the four required source fields from the matching OOXML cache."""

    try:
        with ZipFile(path) as package:
            definitions = sorted(
                member
                for member in package.namelist()
                if member.startswith("xl/pivotCache/pivotCacheDefinition")
                and member.endswith(".xml")
            )
            matches: list[
                tuple[str, ET.Element, list[tuple[str, list[object]]]]
            ] = []
            for definition_member in definitions:
                definition = _member_xml(
                    package,
                    definition_member,
                    maximum_bytes=MAX_DEFINITION_BYTES,
                )
                fields = _cache_fields(definition)
                names = {name for name, _ in fields}
                if set(REQUIRED_FIELDS).issubset(names):
                    matches.append((definition_member, definition, fields))
            if len(matches) != 1:
                raise ValueError(
                    "CA DOI workbook must contain exactly one pivot cache with "
                    f"fields {list(REQUIRED_FIELDS)}; found {len(matches)}"
                )

            definition_member, definition, fields = matches[0]
            declared_count = int(definition.get("recordCount") or "0")
            if declared_count <= 0 or declared_count > MAX_RECORD_COUNT:
                raise ValueError(
                    "CA DOI pivot cache declares an invalid record count: "
                    f"{declared_count}"
                )
            records = _member_xml(
                package,
                _records_member(package, definition_member),
                maximum_bytes=MAX_RECORD_BYTES,
            )
    except BadZipFile as exc:
        raise ValueError(f"CA DOI workbook is not a valid XLSX file: {path.name}") from exc

    rows: list[dict[str, object]] = []
    required_positions = {
        name: index
        for index, (name, _) in enumerate(fields)
        if name in REQUIRED_FIELDS
    }
    for record in records.findall(_qualified("r")):
        values = list(record)
        if len(values) > len(fields):
            raise ValueError(
                "CA DOI pivot-cache record has more values than cache fields"
            )
        row: dict[str, object] = {}
        for name in REQUIRED_FIELDS:
            position = required_positions[name]
            if position >= len(values):
                raise ValueError(
                    f"CA DOI pivot-cache record omits required field {name}"
                )
            row[name] = _decode_record_value(
                values[position], fields[position][1]
            )
        rows.append(row)
    records_count = int(records.get("count") or str(len(rows)))
    if declared_count != len(rows) or records_count != len(rows):
        raise ValueError(
            "CA DOI pivot-cache record counts disagree: "
            f"definition={declared_count}, records={records_count}, parsed={len(rows)}"
        )
    return pd.DataFrame(rows, columns=REQUIRED_FIELDS)


def _annual_homeowners(path: Path) -> pd.DataFrame:
    source = read_ca_doi_pivot_records(path)
    source = source.loc[source["POLICY_TYPE"].eq(POLICY_TYPE)].copy()
    if source.empty:
        raise ValueError(
            f"CA DOI workbook contains no exact POLICY_TYPE={POLICY_TYPE!r} rows"
        )
    for column in ("EXP_YEAR", "WP", "WE"):
        source[column] = pd.to_numeric(source[column], errors="coerce")
    if source[["EXP_YEAR", "WP", "WE"]].isna().any().any():
        raise ValueError("CA DOI homeowners rows contain missing or nonnumeric values")
    if not source["EXP_YEAR"].map(
        lambda value: float(value).is_integer()
    ).all():
        raise ValueError("CA DOI EXP_YEAR contains a noninteger value")
    source["EXP_YEAR"] = source["EXP_YEAR"].astype(int)
    actual_years = frozenset(source["EXP_YEAR"].unique())
    if actual_years != EXPECTED_EXPOSURE_YEARS:
        missing = sorted(EXPECTED_EXPOSURE_YEARS - actual_years)
        extra = sorted(actual_years - EXPECTED_EXPOSURE_YEARS)
        raise ValueError(
            "CA DOI Homeowners Multi-Peril exposure-year coverage changed: "
            f"missing={missing}, extra={extra}"
        )
    if not source[["WP", "WE"]].map(
        lambda value: math.isfinite(float(value))
    ).all().all():
        raise ValueError("CA DOI homeowners rows contain non-finite values")
    if (source["WE"] <= 0).any():
        raise ValueError("CA DOI homeowners rows contain nonpositive exposures")

    annual = (
        source.groupby("EXP_YEAR", as_index=False, sort=True)
        .agg(
            written_premium=("WP", "sum"),
            written_exposures=("WE", "sum"),
            coverage_range_rows=("WE", "size"),
        )
        .sort_values("EXP_YEAR")
        .reset_index(drop=True)
    )
    if (annual[["written_premium", "written_exposures"]] <= 0).any().any():
        raise ValueError("CA DOI annual homeowners aggregates must be positive")
    annual["average_written_premium"] = (
        annual["written_premium"] / annual["written_exposures"]
    )
    return annual


def clean_ca_doi_homeowners(
    path: Path,
    record: dict[str, str],
    output_path: Path,
) -> pd.DataFrame:
    """Build nominal California annual homeowners controls for 2008-2023."""

    if record.get("source_id") != SOURCE_ID:
        raise ValueError(
            f"CA DOI cleaner received unexpected source_id={record.get('source_id')!r}"
        )
    annual = _annual_homeowners(path)
    years = annual["EXP_YEAR"].astype(int)
    observation_date = pd.to_datetime(years.astype(str) + "-12-31")
    period_start = pd.to_datetime(years.astype(str) + "-01-01")
    specs = (
        ("written_premium", "ca_homeowners_written_premium", "current_dollars"),
        ("written_exposures", "ca_homeowners_written_exposures", "exposures"),
        (
            "average_written_premium",
            "ca_homeowners_avg_written_premium",
            "current_dollars_per_exposure",
        ),
    )
    parts: list[pd.DataFrame] = []
    for column, variable_id, unit in specs:
        values = pd.to_numeric(annual[column], errors="raise")
        part = pd.DataFrame(
            {
                "observation_date": observation_date,
                "source_observation_date": observation_date,
                "period_start": period_start,
                "period_end": observation_date,
                "geo_id": "STATE_06",
                "state_fips": "06",
                "county_fips": None,
                "variable_id": variable_id,
                "value": values,
                "reported_value": values,
                "unit": unit,
                "provider": "California Department of Insurance",
                "native_frequency": "annual",
                "final_frequency": "annual",
                "transformation_id": TRANSFORMATION_ID,
                "is_imputed": False,
                "is_interpolated": False,
                "is_seasonally_adjusted": False,
                "quality_flag": annual["coverage_range_rows"].map(
                    lambda count: (
                        "observed_state_control;nominal;"
                        "policy_type_homeowners_multi_peril;"
                        f"coverage_range_rows_{int(count)}"
                    )
                ),
            }
        )
        for key, value in metadata_fields(record).items():
            part[key] = value
        parts.append(part)
    return finalize(pd.concat(parts, ignore_index=True), output_path)
