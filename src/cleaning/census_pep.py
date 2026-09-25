"""Clean official Census PEP county population and housing-unit vintages.

The Population Estimates Program publishes July 1 point estimates.  The
project stores them on the annual December 31 observation grid while retaining
the July 1 provider date in ``source_observation_date``.  Vintage 2019 and
Vintage 2025 are deliberately separate, non-overlapping source segments; this
module does not splice, interpolate, or otherwise revise either segment.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields


LA_STATE_FIPS = "06"
LA_COUNTY_CODE = "037"
LA_COUNTY_FIPS = "06037"
LA_COUNTY_NAME = "Los Angeles County, California"


@dataclass(frozen=True)
class PepSourceSpec:
    """Documented layout and output semantics for one immutable PEP source."""

    source_id: str
    file_kind: str
    years: tuple[int, ...]
    variable_id: str
    unit: str
    vintage_label: str
    vintage_segment: str

    @property
    def transformation_id(self) -> str:
        measure = "population" if self.unit == "persons" else "housing_units"
        return f"census_pep_county_{measure}_july1_to_annual_dec31_v1"


SOURCE_SPECS = {
    "census_pep_county_population_v2019": PepSourceSpec(
        source_id="census_pep_county_population_v2019",
        file_kind="csv_population",
        years=tuple(range(2010, 2020)),
        variable_id="census_pep_county_population_annual",
        unit="persons",
        vintage_label="2019",
        vintage_segment="2010_2019",
    ),
    "census_pep_county_population_v2025": PepSourceSpec(
        source_id="census_pep_county_population_v2025",
        file_kind="csv_population",
        years=tuple(range(2020, 2026)),
        variable_id="census_pep_county_population_annual",
        unit="persons",
        vintage_label="2025",
        vintage_segment="2020_2025",
    ),
    "census_pep_county_housing_units_v2019": PepSourceSpec(
        source_id="census_pep_county_housing_units_v2019",
        file_kind="xlsx_housing_units",
        years=tuple(range(2010, 2020)),
        variable_id="census_pep_county_housing_units_annual",
        unit="housing_units",
        vintage_label="2019",
        vintage_segment="2010_2019",
    ),
    "census_pep_county_housing_units_v2025": PepSourceSpec(
        source_id="census_pep_county_housing_units_v2025",
        file_kind="xlsx_housing_units",
        years=tuple(range(2020, 2026)),
        variable_id="census_pep_county_housing_units_annual",
        unit="housing_units",
        vintage_label="2025",
        vintage_segment="2020_2025",
    ),
}
SOURCE_IDS = frozenset(SOURCE_SPECS)

_POPULATION_COLUMN = re.compile(r"POPESTIMATE((?:19|20)\d{2})")
_FOUR_DIGIT_YEAR = re.compile(r"(?:19|20)\d{2}")


def _schema_error(path: Path, detail: str) -> ValueError:
    return ValueError(f"Census PEP schema drift in {path.name}: {detail}")


def _read_population_csv(path: Path) -> pd.DataFrame:
    """Read a Census CSV without losing FIPS leading zeros.

    Census population files have historically included Latin-1 geography
    names, while synthetic and newer files may be UTF-8.  The encoding fallback
    changes no source bytes and all columns remain strings.
    """

    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                header = next(csv.reader(handle), None)
            if not header:
                raise _schema_error(path, "CSV has no header row")
            columns = [str(column).strip().lstrip("\ufeff") for column in header]
            if any(not column for column in columns):
                raise _schema_error(path, "CSV contains an unnamed column")
            duplicates = sorted(
                {column for column in columns if columns.count(column) > 1}
            )
            if duplicates:
                raise _schema_error(
                    path, f"CSV contains duplicate columns {duplicates}"
                )
            frame = pd.read_csv(path, dtype=str, encoding=encoding)
            if frame.shape[1] != len(columns):
                raise _schema_error(
                    path,
                    "CSV parsed column count does not match its header "
                    f"({frame.shape[1]} != {len(columns)})",
                )
            frame.columns = columns
            return frame
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:  # pragma: no cover - Latin-1 decodes every byte
        raise _schema_error(path, f"CSV encoding is unreadable: {last_error}")
    raise _schema_error(path, "CSV could not be read")  # pragma: no cover


def _normalized_code(
    values: pd.Series,
    *,
    width: int,
    column: str,
    path: Path,
) -> pd.Series:
    text = values.astype("string").str.strip()
    valid = text.notna() & text.str.fullmatch(r"\d+")
    valid &= text.str.len().le(width)
    if not valid.all():
        examples = text.loc[~valid].astype(str).drop_duplicates().head(5).tolist()
        raise _schema_error(
            path,
            f"{column} contains missing or invalid codes {examples}",
        )
    return text.str.zfill(width)


def _reported_count(value: object, *, label: str, path: Path) -> int:
    if pd.isna(value) or isinstance(value, bool):
        raise _schema_error(path, f"{label} is missing or nonnumeric")
    text = str(value).strip().replace(",", "")
    number = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    if pd.isna(number) or not math.isfinite(float(number)):
        raise _schema_error(path, f"{label} is missing or nonnumeric")
    if float(number) < 0 or not float(number).is_integer():
        raise _schema_error(path, f"{label} is not a nonnegative integer")
    return int(number)


def _population_values(path: Path, spec: PepSourceSpec) -> dict[int, int]:
    if path.suffix.casefold() != ".csv":
        raise _schema_error(path, "population source must be an archived CSV")
    source = _read_population_csv(path)
    required = {"SUMLEV", "STATE", "COUNTY"}
    missing_identifiers = sorted(required - set(source.columns))
    if missing_identifiers:
        raise _schema_error(
            path, f"missing required columns {missing_identifiers}"
        )

    expected_columns = {f"POPESTIMATE{year}" for year in spec.years}
    actual_columns = {
        column for column in source.columns if _POPULATION_COLUMN.fullmatch(column)
    }
    if actual_columns != expected_columns:
        missing = sorted(expected_columns - actual_columns)
        extra = sorted(actual_columns - expected_columns)
        raise _schema_error(
            path,
            "population estimate columns changed: "
            f"missing={missing}, extra={extra}",
        )

    summary_level = _normalized_code(
        source["SUMLEV"], width=3, column="SUMLEV", path=path
    )
    state = _normalized_code(
        source["STATE"], width=2, column="STATE", path=path
    )
    county = _normalized_code(
        source["COUNTY"], width=3, column="COUNTY", path=path
    )
    match = source.loc[
        summary_level.eq("050")
        & state.eq(LA_STATE_FIPS)
        & county.eq(LA_COUNTY_CODE)
    ]
    if len(match) != 1:
        raise _schema_error(
            path,
            "expected exactly one SUMLEV 050 Los Angeles County FIPS 06037 "
            f"row; found {len(match)}",
        )
    row = match.iloc[0]
    return {
        year: _reported_count(
            row[f"POPESTIMATE{year}"],
            label=f"POPESTIMATE{year}",
            path=path,
        )
        for year in spec.years
    }


def _year_token(value: object) -> int | None:
    if pd.isna(value) or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and 1900 <= value <= 2100 else None
    text = str(value).strip()
    return int(text) if _FOUR_DIGIT_YEAR.fullmatch(text) else None


def _housing_year_columns(
    raw: pd.DataFrame, spec: PepSourceSpec, path: Path
) -> tuple[int, dict[int, int]]:
    expected = set(spec.years)
    candidates: list[tuple[int, dict[int, int], list[int]]] = []
    for row_number in range(min(len(raw), 20)):
        positions: dict[int, int] = {}
        duplicates: list[int] = []
        for column_number, value in enumerate(raw.iloc[row_number].tolist()):
            year = _year_token(value)
            if year is None:
                continue
            if year in positions:
                duplicates.append(year)
            else:
                positions[year] = column_number
        if expected.intersection(positions):
            candidates.append((row_number, positions, duplicates))

    exact = [
        candidate
        for candidate in candidates
        if set(candidate[1]) == expected and not candidate[2]
    ]
    if len(exact) == 1:
        return exact[0][0], exact[0][1]

    if candidates:
        closest = max(
            candidates,
            key=lambda candidate: len(expected.intersection(candidate[1])),
        )
        actual = set(closest[1])
        detail = (
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}, "
            f"duplicates={sorted(set(closest[2]))}"
        )
    else:
        detail = f"missing={sorted(expected)}, extra=[], duplicates=[]"
    raise _schema_error(
        path,
        "housing-unit year header changed or is ambiguous: " + detail,
    )


def _normalized_geography(value: object) -> str:
    if pd.isna(value):
        return ""
    text = " ".join(str(value).replace("\n", " ").split()).strip()
    text = text.lstrip(".").strip()
    return text.casefold()


def _housing_values(path: Path, spec: PepSourceSpec) -> dict[int, int]:
    if path.suffix.casefold() != ".xlsx":
        raise _schema_error(path, "housing-unit source must be an archived XLSX")
    try:
        raw = pd.read_excel(path, sheet_name=0, header=None, dtype=object)
    except Exception as exc:
        raise _schema_error(path, f"workbook is unreadable: {exc}") from exc
    if raw.empty:
        raise _schema_error(path, "workbook has no cells")

    header_row, year_columns = _housing_year_columns(raw, spec, path)
    first_year_column = min(year_columns.values())
    if first_year_column < 1:
        raise _schema_error(path, "workbook has no geographic-area column")

    expected_name = LA_COUNTY_NAME.casefold()
    matching_rows: list[int] = []
    for row_number in range(header_row + 1, len(raw)):
        geography_cells = raw.iloc[row_number, :first_year_column]
        if any(
            _normalized_geography(value) == expected_name
            for value in geography_cells
        ):
            matching_rows.append(row_number)
    if len(matching_rows) != 1:
        raise _schema_error(
            path,
            f"expected exactly one {LA_COUNTY_NAME!r} row; "
            f"found {len(matching_rows)}",
        )

    row_number = matching_rows[0]
    return {
        year: _reported_count(
            raw.iat[row_number, year_columns[year]],
            label=f"housing-unit estimate {year}",
            path=path,
        )
        for year in spec.years
    }


def parse_census_pep(path: Path, record: dict[str, str]) -> pd.DataFrame:
    """Parse one documented PEP source into observation-contract fields."""

    path = Path(path)
    source_id = str(record.get("source_id") or "")
    try:
        spec = SOURCE_SPECS[source_id]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported Census PEP source_id={source_id!r}; "
            f"expected one of {sorted(SOURCE_IDS)}"
        ) from exc

    if spec.file_kind == "csv_population":
        values = _population_values(path, spec)
    elif spec.file_kind == "xlsx_housing_units":
        values = _housing_values(path, spec)
    else:  # pragma: no cover - every frozen specification is covered above
        raise AssertionError(f"Unhandled Census PEP file kind {spec.file_kind!r}")

    base_metadata = metadata_fields(record)
    rows: list[dict[str, object]] = []
    for year in spec.years:
        observation_date = pd.Timestamp(year=year, month=12, day=31)
        source_observation_date = pd.Timestamp(year=year, month=7, day=1)
        value = values[year]
        rows.append(
            {
                "observation_date": observation_date,
                "source_observation_date": source_observation_date,
                "period_start": pd.Timestamp(year=year, month=1, day=1),
                "period_end": observation_date,
                "geo_id": f"COUNTY_{LA_COUNTY_FIPS}",
                "state_fips": LA_STATE_FIPS,
                "county_fips": LA_COUNTY_FIPS,
                "variable_id": spec.variable_id,
                "value": value,
                "reported_value": value,
                "unit": spec.unit,
                "provider": "U.S. Census Bureau",
                "native_frequency": "annual",
                "final_frequency": "annual",
                **base_metadata,
                "transformation_id": spec.transformation_id,
                "is_imputed": False,
                "is_interpolated": False,
                "is_seasonally_adjusted": False,
                "quality_flag": (
                    "observed;reference_date_july_1;"
                    f"pep_vintage_{spec.vintage_label};"
                    f"vintage_segment_{spec.vintage_segment}"
                ),
            }
        )
    return pd.DataFrame(rows)


def clean_census_pep(
    path: Path,
    record: dict[str, str],
    output_path: Path,
) -> pd.DataFrame:
    """Clean one official PEP county source and write the shared schema."""

    return finalize(parse_census_pep(path, record), output_path)
