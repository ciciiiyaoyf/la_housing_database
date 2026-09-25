from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from src.cleaning.ca_doi import (
    EXPECTED_EXPOSURE_YEARS,
    SOURCE_ID,
    clean_ca_doi_homeowners,
    read_ca_doi_pivot_records,
)
from src.cleaning.common import OBSERVATION_COLUMNS
from src.cleaning.runner import clean_sources
from src.common.config import ProjectConfig, SourceConfig
from src.harmonization.build import (
    _add_ca_doi_monthly_control,
    _state_monthly_control_panel,
)


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
RECORD_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "pivotCacheRecords"
)


def _write_fixture(path: Path, years: list[int]) -> None:
    shared_years = "".join(f'<n v="{year}"/>' for year in years)
    records: list[str] = []
    for index, _ in enumerate(years):
        # Two Homeowners Multi-Peril coverage ranges must be summed. The
        # Renters/Tenant row is deliberately much larger and must be excluded.
        records.extend(
            [
                (
                    '<r><s v="2024"/><x v="{year}"/><x v="0"/>'
                    '<x v="0"/><n v="{wp}"/><n v="10"/></r>'
                ).format(year=index, wp=1000 + index),
                (
                    '<r><s v="2024"/><x v="{year}"/><x v="0"/>'
                    '<x v="1"/><n v="{wp}"/><n v="20"/></r>'
                ).format(year=index, wp=2000 + index),
                (
                    '<r><s v="2024"/><x v="{year}"/><x v="1"/>'
                    '<x v="0"/><n v="999999"/><n v="1"/></r>'
                ).format(year=index),
            ]
        )
    definition = f'''<?xml version="1.0" encoding="UTF-8"?>
<pivotCacheDefinition xmlns="{MAIN_NS}" recordCount="{len(records)}">
  <cacheFields count="7">
    <cacheField name="RPT_YEAR"><sharedItems/></cacheField>
    <cacheField name="EXP_YEAR"><sharedItems>{shared_years}</sharedItems></cacheField>
    <cacheField name="POLICY_TYPE"><sharedItems><s v="Homeowners Multi-Peril"/><s v="Renters/Tenant"/></sharedItems></cacheField>
    <cacheField name="RANGE_OF_INS"><sharedItems><s v="Range A"/><s v="Range B"/></sharedItems></cacheField>
    <cacheField name="WP"><sharedItems/></cacheField>
    <cacheField name="WE"><sharedItems/></cacheField>
    <cacheField name="AvgPrem" formula="WP/WE" databaseField="0"/>
  </cacheFields>
</pivotCacheDefinition>'''
    relationship = f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL_NS}">
  <Relationship Id="rId1" Type="{RECORD_REL}" Target="pivotCacheRecords1.xml"/>
</Relationships>'''
    record_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<pivotCacheRecords xmlns="{MAIN_NS}" count="{len(records)}">{"".join(records)}</pivotCacheRecords>'''
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
        package.writestr(
            "xl/pivotCache/pivotCacheDefinition1.xml", definition
        )
        package.writestr(
            "xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels",
            relationship,
        )
        package.writestr(
            "xl/pivotCache/pivotCacheRecords1.xml", record_xml
        )


def _record() -> dict[str, str]:
    return {
        "source_id": SOURCE_ID,
        "data_vintage": "2026-08-07",
        "retrieved_at": "2026-08-07T12:00:00+00:00",
        "source_filename": "A-Part-I-Data-Summary.xlsx",
        "checksum_sha256": "a" * 64,
        "provider_last_updated": "2025-07-07",
    }


def _project_config(root: Path) -> ProjectConfig:
    source = SourceConfig(
        source_id=SOURCE_ID,
        provider="California Department of Insurance",
        dataset_name="Residential Property Insurance Report Part I",
        source_page_url="https://www.insurance.ca.gov/report",
        download_url="https://www.insurance.ca.gov/report.xlsx",
        native_frequency="annual",
        geography_levels=("state",),
        file_format="xlsx",
        raw_subdir="ca_doi",
    )
    return ProjectConfig(
        root=root,
        verified_at="2026-08-07",
        sources=(source,),
        pipeline={},
        variables=(),
        geographies=(),
        crosswalk_version="test",
    )


def test_ca_doi_reads_pivot_cache_and_aggregates_exact_policy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "doi.xlsx"
    output = tmp_path / "clean.parquet"
    years = sorted(EXPECTED_EXPOSURE_YEARS)
    _write_fixture(source, years)

    records = read_ca_doi_pivot_records(source)
    assert list(records.columns) == ["EXP_YEAR", "POLICY_TYPE", "WP", "WE"]
    assert len(records) == 3 * len(years)

    clean = clean_ca_doi_homeowners(source, _record(), output)
    assert output.is_file()
    assert len(clean) == 3 * len(years)
    assert set(clean["variable_id"]) == {
        "ca_homeowners_written_premium",
        "ca_homeowners_written_exposures",
        "ca_homeowners_avg_written_premium",
    }
    assert set(clean["geo_id"]) == {"STATE_06"}
    assert set(clean["state_fips"]) == {"06"}
    assert clean["county_fips"].isna().all()
    assert set(clean["native_frequency"]) == {"annual"}
    assert not clean["is_interpolated"].any()
    assert set(clean["checksum_sha256"].dropna()) == {"a" * 64}

    first_year = clean.loc[clean["observation_date"].eq(pd.Timestamp("2008-12-31"))]
    values = first_year.set_index("variable_id")["value"]
    assert values["ca_homeowners_written_premium"] == pytest.approx(3000.0)
    assert values["ca_homeowners_written_exposures"] == pytest.approx(30.0)
    assert values["ca_homeowners_avg_written_premium"] == pytest.approx(100.0)
    assert first_year["quality_flag"].str.contains(
        "policy_type_homeowners_multi_peril", regex=False
    ).all()
    assert first_year["quality_flag"].str.contains(
        "coverage_range_rows_2", regex=False
    ).all()


def test_ca_doi_fails_when_required_exposure_year_is_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "doi_missing_year.xlsx"
    _write_fixture(source, sorted(EXPECTED_EXPOSURE_YEARS - {2023}))

    with pytest.raises(
        ValueError,
        match=r"exposure-year coverage changed: missing=\[2023\]",
    ):
        clean_ca_doi_homeowners(
            source, _record(), tmp_path / "should_not_exist.parquet"
        )


def test_explicit_ca_doi_clean_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw.xlsx"
    raw.write_bytes(b"not-an-xlsx")
    monkeypatch.setattr(
        "src.cleaning.runner.raw_path_for_vintage",
        lambda *_args, **_kwargs: (raw, _record()),
    )

    def fail_clean(*_args, **_kwargs):
        raise ValueError("pivot cache changed")

    monkeypatch.setattr(
        "src.cleaning.runner.clean_ca_doi_homeowners", fail_clean
    )
    with pytest.raises(ValueError, match="pivot cache changed"):
        clean_sources(_project_config(tmp_path), source_filter=SOURCE_ID)


def test_ca_doi_monthly_control_is_flagged_calendar_year_step(
    tmp_path: Path,
) -> None:
    row = {column: pd.NA for column in OBSERVATION_COLUMNS}
    row.update(
        {
            "observation_date": pd.Timestamp("2023-12-31"),
            "source_observation_date": pd.Timestamp("2023-12-31"),
            "period_start": pd.Timestamp("2023-01-01"),
            "period_end": pd.Timestamp("2023-12-31"),
            "geo_id": "STATE_06",
            "state_fips": "06",
            "variable_id": "ca_homeowners_avg_written_premium",
            "value": 1787.912186,
            "reported_value": 1787.912186,
            "unit": "current_dollars_per_exposure",
            "provider": "California Department of Insurance",
            "native_frequency": "annual",
            "final_frequency": "annual",
            "source_id": SOURCE_ID,
            "source_vintage": "2026-08-07",
            "retrieved_at": "2026-08-07T12:00:00+00:00",
            "source_filename": "A-Part-I-Data-Summary.xlsx",
            "checksum_sha256": "a" * 64,
            "provider_last_updated": "2025-07-07",
            "transformation_id": "ca_doi_homeowners_multi_peril_annual_v1",
            "is_imputed": False,
            "is_interpolated": False,
            "is_seasonally_adjusted": False,
            "quality_flag": "observed_state_control;nominal",
        }
    )
    fact = pd.DataFrame([row], columns=OBSERVATION_COLUMNS)

    expanded = _add_ca_doi_monthly_control(fact)
    monthly = expanded.loc[
        expanded["variable_id"].eq(
            "ca_homeowners_avg_written_premium_monthly_control"
        )
    ]
    assert len(monthly) == 12
    assert monthly["observation_date"].min() == pd.Timestamp("2023-01-31")
    assert monthly["observation_date"].max() == pd.Timestamp("2023-12-31")
    assert monthly["value"].eq(1787.912186).all()
    assert monthly["reported_value"].isna().all()
    assert monthly["source_observation_date"].eq(
        pd.Timestamp("2023-12-31")
    ).all()
    assert monthly["native_frequency"].eq("annual").all()
    assert monthly["final_frequency"].eq("monthly").all()
    assert monthly["is_interpolated"].eq(False).all()
    assert monthly["is_imputed"].eq(False).all()
    assert monthly["quality_flag"].str.contains(
        "carried_annual_control", regex=False
    ).all()

    panel = _state_monthly_control_panel(expanded, _project_config(tmp_path))
    assert len(panel) == 12
    assert panel["geo_id"].eq("STATE_06").all()
    assert panel["state_fips"].eq("06").all()
    assert panel["is_observed"].eq(False).all()
    assert panel["is_constructed"].eq(True).all()
    assert panel["is_proxy"].eq(False).all()
    assert panel["is_interpolated"].eq(False).all()
    assert panel["is_mixed_frequency_control"].eq(True).all()
    assert panel["is_carried_annual_control"].eq(True).all()
    assert panel["source_url"].eq(
        "https://www.insurance.ca.gov/report"
    ).all()
    assert panel["scenario_name"].isna().all()
