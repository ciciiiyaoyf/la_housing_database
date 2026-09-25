from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.cleaning.census_pep import SOURCE_SPECS, clean_census_pep
from src.cleaning.common import OBSERVATION_COLUMNS


def _record(source_id: str, filename: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "data_vintage": "2026-08-15",
        "retrieved_at": "2026-08-15T19:30:00+00:00",
        "source_filename": filename,
        "checksum_sha256": "b" * 64,
        "provider_last_updated": "2026-03-26",
    }


def _population_fixture(path: Path, years: tuple[int, ...]) -> None:
    rows: list[dict[str, object]] = []
    for sumlev, state, county, name, offset in (
        ("040", "06", "000", "California", 900_000),
        ("050", "6", "37", "Los Angeles County, California", 10_000),
        ("050", "06", "059", "Orange County, California", 20_000),
        ("050", "32", "003", "Clark County, Nevada", 30_000),
    ):
        row: dict[str, object] = {
            "SUMLEV": sumlev,
            "REGION": "4",
            "DIVISION": "9",
            "STATE": state,
            "COUNTY": county,
            "STNAME": "California" if state in {"6", "06"} else "Nevada",
            "CTYNAME": name,
        }
        row.update(
            {
                f"POPESTIMATE{year}": offset + index
                for index, year in enumerate(years)
            }
        )
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def _housing_fixture(path: Path, years: tuple[int, ...]) -> None:
    base_columns = 3 if years[0] == 2010 else 2
    width = base_columns + len(years)
    rows: list[list[object]] = [
        [
            "Annual Estimates of Housing Units for Counties in California",
            *([None] * (width - 1)),
        ],
        [None] * width,
        [
            "Geographic Area",
            "April 1 estimates",
            *([None] * (width - 2)),
        ],
        [
            None,
            *( ["Census", "Estimates Base"] if base_columns == 3 else ["Estimates Base"] ),
            *years,
        ],
        [
            "California",
            *([999_000] * (width - 1)),
        ],
        [
            ".Los Angeles County, California",
            *([4_000_000] * (base_columns - 1)),
            *[4_100_000 + index for index, _ in enumerate(years)],
        ],
        [
            ".Orange County, California",
            *([1_000_000] * (base_columns - 1)),
            *[1_100_000 + index for index, _ in enumerate(years)],
        ],
        ["Note: July 1 estimates", *([None] * (width - 1))],
    ]
    pd.DataFrame(rows).to_excel(path, header=False, index=False)


@pytest.mark.parametrize(
    ("source_id", "filename"),
    [
        (
            "census_pep_county_population_v2019",
            "co-est2019-alldata.csv",
        ),
        (
            "census_pep_county_population_v2025",
            "co-est2025-alldata.csv",
        ),
    ],
)
def test_population_csv_retains_only_la_and_preserves_vintage_semantics(
    tmp_path: Path,
    source_id: str,
    filename: str,
) -> None:
    spec = SOURCE_SPECS[source_id]
    source = tmp_path / filename
    output = tmp_path / f"{source_id}.parquet"
    _population_fixture(source, spec.years)

    result = clean_census_pep(source, _record(source_id, filename), output)

    assert output.is_file()
    assert list(result.columns) == OBSERVATION_COLUMNS
    assert len(result) == len(spec.years)
    assert set(result["geo_id"]) == {"COUNTY_06037"}
    assert set(result["state_fips"]) == {"06"}
    assert set(result["county_fips"]) == {"06037"}
    assert set(result["variable_id"]) == {
        "census_pep_county_population_annual"
    }
    assert set(result["unit"]) == {"persons"}
    assert result["value"].tolist() == [
        10_000 + index for index, _ in enumerate(spec.years)
    ]
    _assert_shared_semantics(result, spec.years, spec.vintage_segment)


@pytest.mark.parametrize(
    ("source_id", "filename"),
    [
        (
            "census_pep_county_housing_units_v2019",
            "CO-EST2019-ANNHU-06.xlsx",
        ),
        (
            "census_pep_county_housing_units_v2025",
            "CO-EST2025-HU-06.xlsx",
        ),
    ],
)
def test_housing_workbook_uses_exact_year_headers_and_la_geography(
    tmp_path: Path,
    source_id: str,
    filename: str,
) -> None:
    spec = SOURCE_SPECS[source_id]
    source = tmp_path / filename
    output = tmp_path / f"{source_id}.parquet"
    _housing_fixture(source, spec.years)

    result = clean_census_pep(source, _record(source_id, filename), output)

    assert list(result.columns) == OBSERVATION_COLUMNS
    assert len(result) == len(spec.years)
    assert set(result["geo_id"]) == {"COUNTY_06037"}
    assert set(result["state_fips"]) == {"06"}
    assert set(result["county_fips"]) == {"06037"}
    assert set(result["variable_id"]) == {
        "census_pep_county_housing_units_annual"
    }
    assert set(result["unit"]) == {"housing_units"}
    assert result["value"].tolist() == [
        4_100_000 + index for index, _ in enumerate(spec.years)
    ]
    _assert_shared_semantics(result, spec.years, spec.vintage_segment)


def _assert_shared_semantics(
    result: pd.DataFrame,
    years: tuple[int, ...],
    vintage_segment: str,
) -> None:
    expected_observations = pd.Series(
        [pd.Timestamp(year=year, month=12, day=31) for year in years],
        name="observation_date",
    )
    expected_source_dates = pd.Series(
        [pd.Timestamp(year=year, month=7, day=1) for year in years],
        name="source_observation_date",
    )
    pd.testing.assert_series_equal(
        result["observation_date"].reset_index(drop=True),
        expected_observations,
    )
    pd.testing.assert_series_equal(
        result["source_observation_date"].reset_index(drop=True),
        expected_source_dates,
    )
    assert result["reported_value"].equals(result["value"])
    assert result["period_start"].dt.strftime("%m-%d").eq("01-01").all()
    assert result["period_end"].equals(result["observation_date"])
    assert result["native_frequency"].eq("annual").all()
    assert result["final_frequency"].eq("annual").all()
    assert result["is_imputed"].eq(False).all()
    assert result["is_interpolated"].eq(False).all()
    assert result["is_seasonally_adjusted"].eq(False).all()
    assert result["quality_flag"].str.contains("reference_date_july_1").all()
    assert result["quality_flag"].str.contains(
        f"vintage_segment_{vintage_segment}"
    ).all()
    assert result["source_vintage"].eq("2026-08-15").all()
    assert result["retrieved_at"].eq("2026-08-15T19:30:00+00:00").all()
    assert result["checksum_sha256"].eq("b" * 64).all()
    assert result["provider_last_updated"].eq("2026-03-26").all()


def test_population_schema_drift_missing_planned_year_raises(
    tmp_path: Path,
) -> None:
    source_id = "census_pep_county_population_v2025"
    spec = SOURCE_SPECS[source_id]
    source = tmp_path / "co-est2025-alldata.csv"
    _population_fixture(source, spec.years[:-1])

    with pytest.raises(
        ValueError,
        match=r"population estimate columns changed: .*POPESTIMATE2025",
    ):
        clean_census_pep(
            source,
            _record(source_id, source.name),
            tmp_path / "should_not_exist.parquet",
        )


def test_housing_schema_drift_extra_year_raises(tmp_path: Path) -> None:
    source_id = "census_pep_county_housing_units_v2025"
    source = tmp_path / "CO-EST2025-HU-06.xlsx"
    _housing_fixture(source, (*SOURCE_SPECS[source_id].years, 2026))

    with pytest.raises(
        ValueError,
        match=r"housing-unit year header changed.*extra=\[2026\]",
    ):
        clean_census_pep(
            source,
            _record(source_id, source.name),
            tmp_path / "should_not_exist.parquet",
        )


def test_housing_schema_drift_duplicate_la_row_raises(tmp_path: Path) -> None:
    source_id = "census_pep_county_housing_units_v2019"
    source = tmp_path / "CO-EST2019-ANNHU-06.xlsx"
    years = SOURCE_SPECS[source_id].years
    _housing_fixture(source, years)
    raw = pd.read_excel(source, header=None)
    duplicate = raw.loc[raw[0].eq(".Los Angeles County, California")]
    pd.concat([raw, duplicate], ignore_index=True).to_excel(
        source, header=False, index=False
    )

    with pytest.raises(ValueError, match=r"Los Angeles County.*found 2"):
        clean_census_pep(
            source,
            _record(source_id, source.name),
            tmp_path / "should_not_exist.parquet",
        )


def test_unknown_source_id_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unknown.csv"
    source.write_text("SUMLEV,STATE,COUNTY\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported Census PEP source_id"):
        clean_census_pep(
            source,
            _record("census_pep_unknown", source.name),
            tmp_path / "should_not_exist.parquet",
        )
