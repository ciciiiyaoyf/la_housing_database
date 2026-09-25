"""IRS SOI county migration cleaners."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields

LA_COUNTY_FIPS = ("06", "037")
TOTAL_MIGRATION_COUNTERPART = ("96", "000")
IRS_ENHANCED_MATCHING_BREAK_PERIOD = "2022-2023"
IRS_ENHANCED_MATCHING_BREAK_FLAG = (
    "provider_methodology_break_2022_2023_enhanced_matching_new_series"
)

VARIABLES = {
    "in_returns": ("irs_soi_in_migration_returns", "tax_returns"),
    "out_returns": ("irs_soi_out_migration_returns", "tax_returns"),
    "net_returns": ("irs_soi_net_migration_returns", "tax_returns"),
    "in_exemptions": ("irs_soi_in_migration_exemptions", "exemptions"),
    "out_exemptions": ("irs_soi_out_migration_exemptions", "exemptions"),
    "net_exemptions": ("irs_soi_net_migration_exemptions", "exemptions"),
    "in_agi": ("irs_soi_in_migration_agi", "thousands_of_current_dollars"),
    "out_agi": ("irs_soi_out_migration_agi", "thousands_of_current_dollars"),
    "net_agi": ("irs_soi_net_migration_agi", "thousands_of_current_dollars"),
}


def _zfill3(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(3)


def _zfill2(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(2)


def _quality_flag(period: str) -> str:
    flags = [f"filing_year_pair_{period}"]
    if period == IRS_ENHANCED_MATCHING_BREAK_PERIOD:
        flags.append(IRS_ENHANCED_MATCHING_BREAK_FLAG)
    return ";".join(flags)


def _standardize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = {
        column: column.strip().lower().replace("\ufeff", "")
        for column in frame.columns
    }
    frame = frame.rename(columns=normalized).copy()
    rename_map = {
        "state_code_dest": "dest_statefips",
        "county_code_dest": "dest_countyfips",
        "state_code_origin": "origin_statefips",
        "county_code_origin": "origin_countyfips",
        "return_num": "n1",
        "exmpt_num": "n2",
        "aggr_agi": "agi",
        "y2_statefips": "dest_statefips",
        "y2_countyfips": "dest_countyfips",
        "y1_statefips": "origin_statefips",
        "y1_countyfips": "origin_countyfips",
    }
    return frame.rename(columns={key: value for key, value in rename_map.items() if key in frame})


def _extract_total_row(path: Path, record: dict[str, str]) -> dict[str, object]:
    direction = record.get("migration_direction")
    if direction not in {"inflow", "outflow"}:
        raise ValueError(f"IRS migration raw record missing direction: {path.name}")
    source = pd.read_csv(path, dtype=str, encoding="latin1")
    source = _standardize_columns(source)
    required = {
        "dest_statefips",
        "dest_countyfips",
        "origin_statefips",
        "origin_countyfips",
        "n1",
        "n2",
        "agi",
    }
    missing = required - set(source.columns)
    if missing:
        raise ValueError(f"IRS migration schema change in {path.name}: {sorted(missing)}")
    source["dest_statefips"] = _zfill2(source["dest_statefips"])
    source["origin_statefips"] = _zfill2(source["origin_statefips"])
    source["dest_countyfips"] = _zfill3(source["dest_countyfips"])
    source["origin_countyfips"] = _zfill3(source["origin_countyfips"])
    if direction == "inflow":
        mask = (
            source["dest_statefips"].eq(LA_COUNTY_FIPS[0])
            & source["dest_countyfips"].eq(LA_COUNTY_FIPS[1])
            & source["origin_statefips"].eq(TOTAL_MIGRATION_COUNTERPART[0])
            & source["origin_countyfips"].eq(TOTAL_MIGRATION_COUNTERPART[1])
        )
    else:
        mask = (
            source["origin_statefips"].eq(LA_COUNTY_FIPS[0])
            & source["origin_countyfips"].eq(LA_COUNTY_FIPS[1])
            & source["dest_statefips"].eq(TOTAL_MIGRATION_COUNTERPART[0])
            & source["dest_countyfips"].eq(TOTAL_MIGRATION_COUNTERPART[1])
        )
    match = source.loc[mask].copy()
    if len(match) != 1:
        raise ValueError(
            f"Expected one LA County total {direction} row in {path.name}; found {len(match)}"
        )
    row = match.iloc[0]
    period = str(record["archive_period"])
    end_year = int(period.split("-")[1])
    return {
        "archive_period": period,
        "year": end_year,
        "direction": direction,
        "returns": pd.to_numeric(row["n1"], errors="coerce"),
        "exemptions": pd.to_numeric(row["n2"], errors="coerce"),
        "agi": pd.to_numeric(row["agi"], errors="coerce"),
        "record": record,
    }


def clean_irs_soi_county_migration(
    archive_files: list[tuple[Path, dict[str, str]]],
    source_id: str,
    output_path: Path,
    coverage_path: Path,
) -> pd.DataFrame:
    rows = [_extract_total_row(path, record) for path, record in archive_files]
    source = pd.DataFrame(rows)
    if source.empty:
        raise ValueError(f"No IRS SOI migration archives available for {source_id}")
    wide = source.pivot_table(
        index=["archive_period", "year"],
        columns="direction",
        values=["returns", "exemptions", "agi"],
        aggfunc="first",
    )
    wide.columns = [f"{direction}_{measure}" for measure, direction in wide.columns]
    wide = wide.reset_index()
    required = {
        "inflow_returns",
        "outflow_returns",
        "inflow_exemptions",
        "outflow_exemptions",
        "inflow_agi",
        "outflow_agi",
    }
    missing_periods = []
    for _, row in wide.iterrows():
        missing = [column for column in required if pd.isna(row.get(column))]
        if missing:
            missing_periods.append(
                {
                    "archive_period": row["archive_period"],
                    "missing_columns": "|".join(missing),
                }
            )
    if missing_periods:
        pd.DataFrame(missing_periods).to_csv(coverage_path, index=False)
        raise ValueError(
            "IRS SOI migration cleaner requires both inflow and outflow files "
            f"for every period; missing periods written to {coverage_path}"
        )
    wide["net_returns"] = wide["inflow_returns"] - wide["outflow_returns"]
    wide["net_exemptions"] = wide["inflow_exemptions"] - wide["outflow_exemptions"]
    wide["net_agi"] = wide["inflow_agi"] - wide["outflow_agi"]
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    wide[
        [
            "archive_period",
            "year",
            "inflow_returns",
            "outflow_returns",
            "net_returns",
            "inflow_exemptions",
            "outflow_exemptions",
            "net_exemptions",
            "inflow_agi",
            "outflow_agi",
            "net_agi",
        ]
    ].to_csv(coverage_path, index=False)
    # Use the latest retrieved archive metadata for each filing-year pair as
    # source vintage metadata for the derived net observations.
    metadata_by_period: dict[str, dict[str, str]] = {}
    for item in rows:
        period = str(item["archive_period"])
        current = metadata_by_period.get(period)
        candidate = item["record"]
        if current is None or str(candidate.get("retrieved_at", "")) > str(
            current.get("retrieved_at", "")
        ):
            metadata_by_period[period] = candidate
    long_rows: list[dict[str, object]] = []
    for _, row in wide.iterrows():
        year = int(row["year"])
        period = str(row["archive_period"])
        observation_date = pd.Timestamp(year=year, month=12, day=31)
        period_start = pd.Timestamp(year=year - 1, month=1, day=1)
        base = {
            "observation_date": observation_date,
            "source_observation_date": observation_date,
            "period_start": period_start,
            "period_end": observation_date,
            "geo_id": "COUNTY_06037",
            "state_fips": "06",
            "county_fips": "06037",
            "provider": "Internal Revenue Service",
            "native_frequency": "annual",
            "final_frequency": "annual",
            "transformation_id": "irs_soi_county_migration_net_v2",
            "is_imputed": False,
            "is_interpolated": False,
            "is_seasonally_adjusted": False,
            "quality_flag": _quality_flag(period),
        }
        base.update(metadata_fields(metadata_by_period[period]))
        values = {
            "in_returns": row["inflow_returns"],
            "out_returns": row["outflow_returns"],
            "net_returns": row["net_returns"],
            "in_exemptions": row["inflow_exemptions"],
            "out_exemptions": row["outflow_exemptions"],
            "net_exemptions": row["net_exemptions"],
            "in_agi": row["inflow_agi"],
            "out_agi": row["outflow_agi"],
            "net_agi": row["net_agi"],
        }
        for key, value in values.items():
            variable_id, unit = VARIABLES[key]
            long_rows.append(
                {
                    **base,
                    "variable_id": variable_id,
                    "value": value,
                    "reported_value": value,
                    "unit": unit,
                }
            )
    return finalize(pd.DataFrame(long_rows), output_path)
