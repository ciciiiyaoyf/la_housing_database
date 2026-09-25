"""HUD Point-in-Time count cleaner.

The official HUD workbook is an XLSB file with one sheet per PIT year.  This
module preserves the native Continuum of Care (CoC) geography.  It does not
relabel a CoC, or a collection of CoCs, as a county.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields

EXPECTED_YEARS = tuple(range(2007, 2026))
LA_FOUR_COC_IDS = ("CA-600", "CA-606", "CA-607", "CA-612")
LA_FOUR_COC_COMPOSITE_ID = "LA_FOUR_COC_COMPOSITE"

REQUIRED_SOURCE_COLUMNS = (
    "CoC Number",
    "Overall Homeless",
    "Sheltered Total Homeless",
    "Unsheltered Homeless",
)
OPTIONAL_COUNT_TYPE_COLUMN = "Count Types"

VARIABLES = {
    "Overall Homeless": "hud_pit_overall_homeless_annual",
    "Sheltered Total Homeless": "hud_pit_sheltered_homeless_annual",
    "Unsheltered Homeless": "hud_pit_unsheltered_homeless_annual",
}

FULL_COUNT_TYPE = "sheltered and unsheltered count"
SHELTERED_ONLY_COUNT_TYPE = "sheltered-only count"
PARTIAL_UNSHELTERED_COUNT_TYPE = "sheltered and partial unsheltered count"
NO_COUNT_TYPE = "no 2010 pit count"
COUNT_TYPE_ALIASES = {
    FULL_COUNT_TYPE: FULL_COUNT_TYPE,
    "sheltered and full unsheltered count": FULL_COUNT_TYPE,
    SHELTERED_ONLY_COUNT_TYPE: SHELTERED_ONLY_COUNT_TYPE,
    "sheltered-only count*": SHELTERED_ONLY_COUNT_TYPE,
    PARTIAL_UNSHELTERED_COUNT_TYPE: PARTIAL_UNSHELTERED_COUNT_TYPE,
    NO_COUNT_TYPE: NO_COUNT_TYPE,
}

_COC_ID_PATTERN = re.compile(r"^[A-Z]{2}-\d{3}[A-Z]?$")
_COMPOSITE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def _normalized_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).replace("\n", " ").split()).strip()


def _normalized_key(value: object) -> str:
    return _normalized_text(value).casefold()


def _canonical_count_type(value: object) -> str:
    normalized = _normalized_key(value)
    if not normalized:
        return ""
    try:
        return COUNT_TYPE_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported HUD PIT Count Type: {normalized!r}") from exc


def _excel_engine(path: Path) -> str | None:
    if path.suffix.casefold() != ".xlsb":
        return None
    if importlib.util.find_spec("pyxlsb") is None:
        raise ModuleNotFoundError(
            "Reading the official HUD PIT .xlsb workbook requires the "
            "optional 'pyxlsb' package. Install pyxlsb before cleaning this "
            "source; do not convert or replace the archived raw workbook."
        )
    return "pyxlsb"


def _sheet_year(sheet_name: object) -> int | None:
    matches = _YEAR_PATTERN.findall(str(sheet_name))
    if len(matches) != 1:
        return None
    return int(matches[0])


def _year_sheet_map(
    sheet_names: Sequence[str], expected_years: Sequence[int]
) -> dict[int, str]:
    expected = set(expected_years)
    result: dict[int, str] = {}
    for sheet_name in sheet_names:
        year = _sheet_year(sheet_name)
        if year not in expected:
            continue
        if year in result:
            raise ValueError(
                f"HUD PIT workbook has multiple sheets for {year}: "
                f"{result[year]!r} and {sheet_name!r}"
            )
        result[year] = sheet_name
    missing = sorted(expected - set(result))
    if missing:
        raise ValueError(f"HUD PIT workbook is missing yearly sheets: {missing}")
    return result


def _locate_header(raw: pd.DataFrame, sheet_name: str) -> tuple[int, dict[str, int]]:
    required_keys = {_normalized_key(column) for column in REQUIRED_SOURCE_COLUMNS}
    for row_number in range(min(len(raw), 30)):
        keys = [_normalized_key(value) for value in raw.iloc[row_number].tolist()]
        locations = {key: index for index, key in enumerate(keys) if key}
        if required_keys.issubset(locations):
            return row_number, locations
    raise ValueError(
        f"HUD PIT sheet {sheet_name!r} does not contain the required columns: "
        f"{list(REQUIRED_SOURCE_COLUMNS)}"
    )


def _read_year_sheet(workbook: pd.ExcelFile, sheet_name: str, year: int) -> pd.DataFrame:
    raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=object)
    header_row, locations = _locate_header(raw, sheet_name)
    selected_columns = list(REQUIRED_SOURCE_COLUMNS)
    count_type_key = _normalized_key(OPTIONAL_COUNT_TYPE_COLUMN)
    if count_type_key in locations:
        selected_columns.append(OPTIONAL_COUNT_TYPE_COLUMN)
    selected = pd.DataFrame(
        {
            column: raw.iloc[header_row + 1 :, locations[_normalized_key(column)]].values
            for column in selected_columns
        }
    )
    selected["CoC Number"] = selected["CoC Number"].map(_normalized_text).str.upper()
    if OPTIONAL_COUNT_TYPE_COLUMN in selected:
        selected[OPTIONAL_COUNT_TYPE_COLUMN] = selected[
            OPTIONAL_COUNT_TYPE_COLUMN
        ].map(_normalized_text)
        count_types = set(
            selected[OPTIONAL_COUNT_TYPE_COLUMN]
            .map(_normalized_key)
            .loc[lambda values: values.ne("")]
        )
        unsupported = sorted(count_types - set(COUNT_TYPE_ALIASES))
        if unsupported:
            raise ValueError(
                f"HUD PIT sheet {sheet_name!r} has unsupported Count Types: "
                f"{unsupported}"
            )
        selected[OPTIONAL_COUNT_TYPE_COLUMN] = selected[
            OPTIONAL_COUNT_TYPE_COLUMN
        ].map(_canonical_count_type)
    else:
        # HUD's 2007 and 2008 worksheets are already one row per CoC and do
        # not include Count Types. Preserve that documented provider layout.
        selected = selected.loc[selected["CoC Number"].ne("")].copy()
        selected[OPTIONAL_COUNT_TYPE_COLUMN] = ""
    if selected.empty:
        raise ValueError(
            f"HUD PIT sheet {sheet_name!r} has no 'Overall Homeless' count rows"
        )

    # Older HUD sheets include blank national-total rows and prose footnotes
    # beneath the CoC table. Exclude only those non-observation rows. A
    # nonblank unrecognized identifier carrying metric values is structural
    # drift and must still stop ingestion. MO-604A is a published 2007 CoC ID.
    selected = selected.loc[selected["CoC Number"].ne("")].copy()
    invalid_mask = ~selected["CoC Number"].str.fullmatch(_COC_ID_PATTERN)
    invalid_with_values = invalid_mask & selected[list(VARIABLES)].apply(
        lambda row: any(_normalized_text(value) for value in row), axis=1
    )
    invalid_ids = sorted(selected.loc[invalid_with_values, "CoC Number"].unique())
    if invalid_ids:
        raise ValueError(
            f"HUD PIT sheet {sheet_name!r} has invalid CoC identifiers: "
            f"{invalid_ids[:10]}"
        )
    selected = selected.loc[~invalid_mask].copy()
    duplicates = selected["CoC Number"].duplicated(keep=False)
    if duplicates.any():
        duplicate_ids = sorted(selected.loc[duplicates, "CoC Number"].unique())
        raise ValueError(
            f"HUD PIT sheet {sheet_name!r} has duplicate overall rows for CoCs: "
            f"{duplicate_ids[:10]}"
        )

    for column in VARIABLES:
        selected[column] = pd.to_numeric(
            selected[column]
            .map(_normalized_text)
            .str.replace(",", "", regex=False),
            errors="coerce",
        )
        if selected[column].dropna().lt(0).any():
            raise ValueError(
                f"HUD PIT sheet {sheet_name!r} has negative values in {column!r}"
            )
    selected["year"] = year
    return selected


def _validate_composites(
    composite_mapping: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if composite_mapping is None:
        return {}
    validated: dict[str, tuple[str, ...]] = {}
    expected_members = set(LA_FOUR_COC_IDS)
    for composite_id, members in composite_mapping.items():
        if not _COMPOSITE_ID_PATTERN.fullmatch(str(composite_id)):
            raise ValueError(f"Invalid HUD PIT composite identifier: {composite_id!r}")
        normalized_members = tuple(
            _normalized_text(member).upper() for member in members
        )
        if len(normalized_members) != len(set(normalized_members)):
            raise ValueError(
                f"HUD PIT composite {composite_id!r} contains duplicate CoC IDs"
            )
        if set(normalized_members) != expected_members:
            raise ValueError(
                "The only supported HUD PIT derived geography is the explicit "
                f"Los Angeles four-CoC set {list(LA_FOUR_COC_IDS)}; received "
                f"{list(normalized_members)} for {composite_id!r}"
            )
        validated[str(composite_id)] = tuple(
            coc_id for coc_id in LA_FOUR_COC_IDS if coc_id in normalized_members
        )
    return validated


def _state_fips(coc_id: str) -> str | None:
    # The active project scope is California.  Preserve every native CoC ID,
    # while only assigning a FIPS code where the CoC prefix is unambiguous for
    # the in-scope state.  County FIPS is intentionally never assigned.
    return "06" if coc_id.startswith("CA-") else None


def _count_type_slug(count_type: object) -> str:
    canonical = _canonical_count_type(count_type)
    if not canonical:
        return "legacy_count_type_not_supplied"
    return canonical.replace(" ", "_").replace("-", "_")


def _native_quality_flag(
    source_column: str,
    reported: float,
    count_type: object,
) -> str:
    metric = {
        "Overall Homeless": "overall",
        "Sheltered Total Homeless": "sheltered",
        "Unsheltered Homeless": "unsheltered",
    }[source_column]
    canonical = _canonical_count_type(count_type)
    status = _count_type_slug(count_type)
    observed = "observed" if pd.notna(reported) else "source_missing"
    if canonical == NO_COUNT_TYPE:
        return f"native_coc;{status};{metric}_not_collected"
    if canonical in {
        SHELTERED_ONLY_COUNT_TYPE,
        PARTIAL_UNSHELTERED_COUNT_TYPE,
    } and source_column != "Sheltered Total Homeless":
        return f"native_coc;{status};{metric}_incomplete"
    return f"native_coc;{status};{metric}_{observed}"


def _analytical_value(
    source_column: str,
    reported: float,
    count_type: object,
) -> float:
    canonical = _canonical_count_type(count_type)
    if canonical == NO_COUNT_TYPE:
        return float("nan")
    if (
        canonical
        in {SHELTERED_ONLY_COUNT_TYPE, PARTIAL_UNSHELTERED_COUNT_TYPE}
        and source_column != "Sheltered Total Homeless"
    ):
        return float("nan")
    return reported


def clean_hud_pit(
    path: Path,
    record: dict[str, str],
    output_path: Path,
    *,
    composite_mapping: Mapping[str, Sequence[str]] | None = None,
    expected_years: Iterable[int] = EXPECTED_YEARS,
) -> pd.DataFrame:
    """Normalize annual HUD PIT counts at their native CoC geography.

    A Los Angeles four-CoC composite is derived only when ``composite_mapping``
    is explicitly supplied with exactly CA-600, CA-606, CA-607, and CA-612.
    The composite remains a CoC composite: ``county_fips`` is always missing.

    HUD's combined workbook supplies row-level Count Types, including mixed
    full, partial-unsheltered, and sheltered-only records in 2021. Reported
    values are preserved in ``reported_value``; incomplete overall and
    unsheltered values are missing in the analytical ``value`` field and
    explicitly flagged.
    """

    years = tuple(sorted({int(year) for year in expected_years}))
    if not years:
        raise ValueError("HUD PIT expected_years cannot be empty")
    composites = _validate_composites(composite_mapping)
    engine = _excel_engine(path)
    with pd.ExcelFile(path, engine=engine) as workbook:
        sheet_map = _year_sheet_map(workbook.sheet_names, years)
        source = pd.concat(
            [
                _read_year_sheet(workbook, sheet_map[year], year)
                for year in years
            ],
            ignore_index=True,
        )

    base_metadata = metadata_fields(record)
    rows: list[dict[str, object]] = []
    for _, source_row in source.sort_values(["year", "CoC Number"]).iterrows():
        year = int(source_row["year"])
        coc_id = str(source_row["CoC Number"])
        count_type = source_row[OPTIONAL_COUNT_TYPE_COLUMN]
        observation_date = pd.Timestamp(year=year, month=12, day=31)
        for source_column, variable_id in VARIABLES.items():
            reported = source_row[source_column]
            rows.append(
                {
                    "observation_date": observation_date,
                    "source_observation_date": None,
                    "period_start": pd.Timestamp(year=year, month=1, day=1),
                    "period_end": observation_date,
                    "geo_id": f"COC_{coc_id}",
                    "state_fips": _state_fips(coc_id),
                    "county_fips": None,
                    "variable_id": variable_id,
                    "value": _analytical_value(
                        source_column, reported, count_type
                    ),
                    "reported_value": reported,
                    "unit": "people",
                    "provider": "U.S. Department of Housing and Urban Development",
                    "native_frequency": "annual_point_in_time",
                    "final_frequency": "annual",
                    **base_metadata,
                    "transformation_id": "hud_pit_native_coc_v2",
                    "is_imputed": False,
                    "is_interpolated": False,
                    "is_seasonally_adjusted": False,
                    "quality_flag": _native_quality_flag(
                        source_column, reported, count_type
                    ),
                }
            )

    native = pd.DataFrame(rows)
    for composite_id, members in composites.items():
        member_geo_ids = {f"COC_{coc_id}" for coc_id in members}
        for year in years:
            observation_date = pd.Timestamp(year=year, month=12, day=31)
            for variable_id in VARIABLES.values():
                member_rows = native.loc[
                    native["observation_date"].eq(observation_date)
                    & native["variable_id"].eq(variable_id)
                    & native["geo_id"].isin(member_geo_ids)
                ]
                complete = (
                    set(member_rows["geo_id"]) == member_geo_ids
                    and member_rows["value"].notna().all()
                )
                value = member_rows["value"].sum() if complete else float("nan")
                quality = (
                    "complete_sum" if complete else "incomplete_members_or_values"
                )
                rows.append(
                    {
                        "observation_date": observation_date,
                        "source_observation_date": None,
                        "period_start": pd.Timestamp(year=year, month=1, day=1),
                        "period_end": observation_date,
                        "geo_id": f"COC_COMPOSITE_{composite_id}",
                        "state_fips": "06",
                        "county_fips": None,
                        "variable_id": variable_id,
                        "value": value,
                        "reported_value": None,
                        "unit": "people",
                        "provider": (
                            "U.S. Department of Housing and Urban Development"
                        ),
                        "native_frequency": "annual_point_in_time",
                        "final_frequency": "annual",
                        **base_metadata,
                        "transformation_id": "hud_pit_explicit_coc_composite_v2",
                        "is_imputed": False,
                        "is_interpolated": False,
                        "is_seasonally_adjusted": False,
                        "quality_flag": (
                            "derived_explicit_four_coc_composite;not_county;"
                            f"members={'|'.join(members)};{quality}"
                        ),
                    }
                )

    return finalize(pd.DataFrame(rows), output_path)
