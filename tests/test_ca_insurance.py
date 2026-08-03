from pathlib import Path

import pandas as pd

from src.cleaning.ca_insurance import clean_ca_residential_property_insurance


def test_ca_doi_cleaner_reads_official_pivot_cache(tmp_path: Path):
    # The official workbook is an XLSX whose underlying rows live in a pivot
    # cache. Build the smallest equivalent cache package for the cleaner.
    from zipfile import ZipFile

    definition = """<?xml version='1.0'?>
    <pivotCacheDefinition xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>
      <cacheFields count='4'>
        <cacheField name='EXP_YEAR'><sharedItems><n v='2023'/></sharedItems></cacheField>
        <cacheField name='POLICY_TYPE'><sharedItems><s v='Homeowners Multi-Peril'/><s v='Renters/Tenant'/></sharedItems></cacheField>
        <cacheField name='WP'><sharedItems/></cacheField>
        <cacheField name='WE'><sharedItems/></cacheField>
      </cacheFields>
    </pivotCacheDefinition>"""
    records = """<?xml version='1.0'?>
    <pivotCacheRecords xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' count='2'>
      <r><x v='0'/><x v='0'/><n v='3000'/><n v='2'/></r>
      <r><x v='0'/><x v='1'/><n v='1000'/><n v='5'/></r>
    </pivotCacheRecords>"""
    workbook = tmp_path / "doi.xlsx"
    with ZipFile(workbook, "w") as archive:
        archive.writestr("xl/pivotCache/pivotCacheDefinition1.xml", definition)
        archive.writestr("xl/pivotCache/pivotCacheRecords1.xml", records)
    output = tmp_path / "clean.parquet"
    result = clean_ca_residential_property_insurance(
        workbook,
        {"data_vintage": "2026-08-03", "retrieved_at": "2026-08-03T00:00:00Z"},
        output,
    )
    values = result.set_index("variable_id")["value"]
    assert values["ca_homeowners_written_premium"] == 3000
    assert values["ca_homeowners_written_exposure"] == 2
    assert values["ca_homeowners_avg_written_premium"] == 1500
    assert set(result["geo_id"]) == {"STATE_06"}
    assert pd.Timestamp(result["observation_date"].iloc[0]).year == 2023
