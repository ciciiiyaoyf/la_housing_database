"""California statewide CPI-U annual-average cleaner.

The California Department of Industrial Relations publishes the complete
California Consumer Price Index history as a PDF.  The table includes annual
averages and observations at several subannual publication frequencies.  This
module deliberately retains only the provider-published ``Annual`` rows; it
does not calculate annual averages from the monthly or bimonthly rows.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from src.cleaning.common import OBSERVATION_COLUMNS, finalize, metadata_fields


SOURCE_ID = "ca_dir_california_cpi"
VARIABLE_ID = "ca_cpi_u_all_items_annual"
TRANSFORMATION_ID = "ca_dir_california_cpi_u_all_items_annual_v1"
PROVIDER = "California Department of Industrial Relations"
UNIT = "index_1982_1984_100"
FIRST_PUBLISHED_YEAR = 1955

_DASH = r"[-\u2010\u2011\u2012\u2013\u2014\u2212]"
_NUMBER = r"(?:R/)?(?:\d+(?:\.\d+)?|\.\d+)"
_ANNUAL_ROW = re.compile(
    rf"^(?P<year>\d{{4}})\s+Annual\s+"
    rf"(?P<cpi_u>{_NUMBER})\s+(?P<cpi_w>{_NUMBER})$"
)
_TITLE_RANGE = re.compile(
    rf"CALIFORNIA\s+CONSUMER\s+PRICE\s+INDEX\s*\(?\s*"
    rf"(?P<first>\d{{4}})\s*{_DASH}\s*(?P<last>\d{{4}})\s*\)?",
    re.IGNORECASE,
)
_BASE_SIGNATURE = re.compile(
    rf"ALL\s+ITEMS\s*\(\s*1982\s*{_DASH}\s*1984\s*=\s*100\s*\)",
    re.IGNORECASE,
)
_HEADER_SIGNATURE = re.compile(
    r"Year\s+Month\s+All\s+Urban\s+Consumers\s+Urban\s+Wage\s+Earners"
    r"\s+and\s+Clerical\s+Workers",
    re.IGNORECASE,
)


class CACPIFormatError(ValueError):
    """Raised when the official California CPI PDF contract has changed."""


def extract_ca_cpi_pdf_text(path: Path) -> str:
    """Extract every PDF page with pypdf, failing on missing/blank pages."""

    path = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "pypdf is required to clean the California CPI PDF"
        ) from exc

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise CACPIFormatError(
                f"{path.name}: California CPI PDF is unexpectedly encrypted"
            )
        if not reader.pages:
            raise CACPIFormatError(
                f"{path.name}: California CPI PDF contains no pages"
            )
        pages: list[str] = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text()
            if page_text is None or not page_text.strip():
                raise CACPIFormatError(
                    f"{path.name}: California CPI PDF page {page_number} "
                    "has no extractable text"
                )
            pages.append(page_text)
    except CACPIFormatError:
        raise
    except Exception as exc:
        raise CACPIFormatError(
            f"{path.name}: cannot read California CPI PDF"
        ) from exc
    return "\n".join(pages)


def _document_year_range(text: str) -> tuple[int, int]:
    ranges = {
        (int(match.group("first")), int(match.group("last")))
        for match in _TITLE_RANGE.finditer(text)
    }
    if len(ranges) != 1:
        raise CACPIFormatError(
            "California CPI title must expose exactly one consistent year range"
        )
    first_year, last_year = ranges.pop()
    if first_year != FIRST_PUBLISHED_YEAR or last_year <= first_year:
        raise CACPIFormatError(
            "California CPI title year range changed: "
            f"expected {FIRST_PUBLISHED_YEAR}-<latest>, found "
            f"{first_year}-{last_year}"
        )
    return first_year, last_year


def _annual_rows(text: str) -> pd.DataFrame:
    if not isinstance(text, str) or not text.strip():
        raise CACPIFormatError("California CPI extracted text is empty")
    if _BASE_SIGNATURE.search(text) is None:
        raise CACPIFormatError(
            "California CPI ALL ITEMS (1982-1984=100) signature is missing"
        )
    if _HEADER_SIGNATURE.search(text) is None:
        raise CACPIFormatError(
            "California CPI table header or column order changed"
        )
    first_year, title_last_year = _document_year_range(text)

    rows: list[dict[str, object]] = []
    for source_line in text.splitlines():
        line = re.sub(r"\s+", " ", source_line).strip()
        if re.match(r"^\d{4}\s+Annual\b", line) is None:
            continue
        match = _ANNUAL_ROW.fullmatch(line)
        if match is None:
            raise CACPIFormatError(
                f"unrecognized California CPI Annual row: {line!r}"
            )
        cpi_u_token = match.group("cpi_u")
        cpi_w_token = match.group("cpi_w")
        rows.append(
            {
                "year": int(match.group("year")),
                "value": float(cpi_u_token.removeprefix("R/")),
                "cpi_w": float(cpi_w_token.removeprefix("R/")),
                "is_revised": cpi_u_token.startswith("R/"),
            }
        )
    if not rows:
        raise CACPIFormatError("California CPI table contains no Annual rows")

    annual = pd.DataFrame(rows)
    duplicate_years = sorted(
        int(year)
        for year in annual.loc[
            annual["year"].duplicated(keep=False), "year"
        ].unique()
    )
    if duplicate_years:
        raise CACPIFormatError(
            f"California CPI table has duplicate Annual years: {duplicate_years}"
        )
    expected_years = set(range(first_year, title_last_year))
    actual_years = set(annual["year"])
    if actual_years != expected_years:
        raise CACPIFormatError(
            "California CPI Annual coverage does not match its title range: "
            f"missing={sorted(expected_years - actual_years)}, "
            f"extra={sorted(actual_years - expected_years)}"
        )
    if not annual[["value", "cpi_w"]].map(
        lambda value: math.isfinite(float(value)) and float(value) > 0
    ).all().all():
        raise CACPIFormatError(
            "California CPI Annual rows must contain positive finite index levels"
        )
    return annual.sort_values("year").reset_index(drop=True)


def parse_ca_cpi_annual_text(
    text: str,
    record: dict[str, str],
) -> pd.DataFrame:
    """Build shared-schema CPI-U observations from already-extracted PDF text."""

    if record.get("source_id") != SOURCE_ID:
        raise ValueError(
            "California CPI cleaner received unexpected "
            f"source_id={record.get('source_id')!r}"
        )
    annual = _annual_rows(text)
    year_text = annual["year"].astype(str)
    annual_end = pd.to_datetime(year_text + "-12-31")
    result = pd.DataFrame(
        {
            "observation_date": annual_end,
            "source_observation_date": annual_end,
            "period_start": pd.to_datetime(year_text + "-01-01"),
            "period_end": annual_end,
            "geo_id": "STATE_06",
            "state_fips": "06",
            "county_fips": None,
            "variable_id": VARIABLE_ID,
            "value": annual["value"],
            "reported_value": annual["value"],
            "unit": UNIT,
            "provider": PROVIDER,
            "native_frequency": "annual",
            "final_frequency": "annual",
            "transformation_id": TRANSFORMATION_ID,
            "is_imputed": False,
            "is_interpolated": False,
            "is_seasonally_adjusted": False,
            "quality_flag": annual["is_revised"].map(
                lambda revised: (
                    "observed_state_control;published_annual_average;"
                    "all_urban_consumers;all_items"
                    + (";source_revised" if revised else "")
                )
            ),
        }
    )
    for key, value in metadata_fields(record).items():
        result[key] = value
    return result[OBSERVATION_COLUMNS]


def clean_ca_cpi(
    path: Path,
    record: dict[str, str],
    output_path: Path,
) -> pd.DataFrame:
    """Clean official DIR PDF Annual CPI-U rows to the observation contract."""

    text = extract_ca_cpi_pdf_text(path)
    return finalize(parse_ca_cpi_annual_text(text, record), output_path)


__all__ = [
    "CACPIFormatError",
    "PROVIDER",
    "SOURCE_ID",
    "TRANSFORMATION_ID",
    "UNIT",
    "VARIABLE_ID",
    "clean_ca_cpi",
    "extract_ca_cpi_pdf_text",
    "parse_ca_cpi_annual_text",
]
