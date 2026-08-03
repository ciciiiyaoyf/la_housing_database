"""Clean the California DOI residential property insurance workbook."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pandas as pd

from src.cleaning.common import finalize

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _shared_items(field: ET.Element) -> list[str | None]:
    items = field.find(f"{NS}sharedItems")
    if items is None:
        return []
    return [child.attrib.get("v") for child in items]


def clean_ca_residential_property_insurance(
    path: Path, record: dict[str, str], output_path: Path
) -> pd.DataFrame:
    """Aggregate Homeowners Multi-Peril written premium/exposure by year.

    The official XLSX stores its source rows in an embedded pivot cache rather
    than a visible worksheet. Fields are documented in the cache definition.
    """
    with ZipFile(path) as workbook:
        definition = ET.fromstring(
            workbook.read("xl/pivotCache/pivotCacheDefinition1.xml")
        )
        records = ET.fromstring(
            workbook.read("xl/pivotCache/pivotCacheRecords1.xml")
        )
    fields = definition.find(f"{NS}cacheFields")
    if fields is None:
        raise ValueError("California DOI workbook has no pivot-cache fields")
    field_list = list(fields)
    names = [field.attrib.get("name", "") for field in field_list]
    required = ["EXP_YEAR", "POLICY_TYPE", "WP", "WE"]
    missing = sorted(set(required) - set(names))
    if missing:
        raise ValueError(f"California DOI workbook missing fields: {missing}")
    shared = [_shared_items(field) for field in field_list]
    rows: list[dict[str, object]] = []
    for raw_row in list(records):
        values: list[object] = []
        for index, cell in enumerate(list(raw_row)):
            value = cell.attrib.get("v")
            if cell.tag == f"{NS}x":
                value = shared[index][int(value)] if value is not None else None
            values.append(value)
        item = dict(zip(names, values))
        if item.get("POLICY_TYPE") != "Homeowners Multi-Peril":
            continue
        rows.append(
            {
                "year": int(float(str(item["EXP_YEAR"]))),
                "written_premium": float(str(item["WP"])),
                "written_exposure": float(str(item["WE"])),
            }
        )
    if not rows:
        raise ValueError("No Homeowners Multi-Peril rows found in DOI workbook")
    annual = pd.DataFrame(rows).groupby("year", as_index=False).sum()
    annual["average_written_premium"] = (
        annual["written_premium"] / annual["written_exposure"].replace(0, pd.NA)
    )
    variable_units = {
        "ca_homeowners_written_premium": "current_dollars",
        "ca_homeowners_written_exposure": "policy_year_exposures",
        "ca_homeowners_avg_written_premium": "current_dollars_per_exposure",
    }
    parts: list[pd.DataFrame] = []
    for source_column, variable_id in (
        ("written_premium", "ca_homeowners_written_premium"),
        ("written_exposure", "ca_homeowners_written_exposure"),
        ("average_written_premium", "ca_homeowners_avg_written_premium"),
    ):
        part = annual[["year", source_column]].rename(columns={source_column: "value"})
        part["variable_id"] = variable_id
        part["unit"] = variable_units[variable_id]
        parts.append(part)
    long = pd.concat(parts, ignore_index=True)
    long["reported_value"] = long["value"]
    long["observation_date"] = pd.to_datetime(long["year"].astype(str) + "-12-31")
    long["geo_id"] = "STATE_06"
    long["source_id"] = "ca_doi_residential_property_insurance"
    long["source_vintage"] = record.get("data_vintage")
    long["retrieved_at"] = record.get("retrieved_at")
    long["native_frequency"] = "annual"
    long["final_frequency"] = "annual"
    long["transformation_id"] = "ca_doi_homeowners_multi_peril_annual_v1"
    long["is_imputed"] = False
    long["is_interpolated"] = False
    long["is_seasonally_adjusted"] = False
    return finalize(long.drop(columns="year"), output_path)
