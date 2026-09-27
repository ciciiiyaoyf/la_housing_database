"""Package the nine constructions at their existing native row granularity.

This module projects and stacks accepted products. It does not calculate any
economic measure, fill missing values, or join county observations onto ZIPs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import yaml

from src.common.config import ProjectConfig
from src.common.io import append_csv, sha256_file


CONTEXT_COLUMNS = (
    "row_type", "date", "frequency", "geo_id", "geography_type",
    "geography_scope", "source_table", "source_file",
    "source_file_checksum_sha256", "source_row_key", "applicable_variable_ids",
)
ROW_TYPES = (
    "county_monthly", "zip_monthly", "zip_regime_quarterly",
    "zip_regime_summary_quarterly", "zip_regime_transition_quarterly",
)


def _local_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Nine-constructions path escapes the project: {relative}")
    return path


def read_nine_constructions_contract(root: Path) -> dict:
    path = root / "config" / "nine_constructions.yaml"
    with path.open(encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    if not isinstance(contract, dict):
        raise ValueError("Nine-constructions contract must be a mapping")
    families = contract.get("families", {})
    if set(families) != set(range(1, 10)):
        raise ValueError("Nine-constructions contract must enumerate families 1 through 9")
    sources = contract.get("sources", [])
    if [source.get("row_type") for source in sources] != list(ROW_TYPES):
        raise ValueError("Nine-constructions contract has unexpected row types or order")
    variables = [variable for family in families.values() for variable in family["variables"]]
    if len(variables) != len(set(variables)):
        raise ValueError("Each variable must have one nine-constructions family")
    represented = set()
    for source in sources:
        if source["frequency"] not in {"monthly", "quarterly"}:
            raise ValueError("Unsupported nine-constructions row frequency")
        if source["geography_type"] not in {"county", "postal_zip"}:
            raise ValueError("Unsupported nine-constructions geography")
        projected = source["variable_columns"]
        if len(projected) != len(set(projected)) or set(projected) - set(variables):
            raise ValueError(f"Invalid variable projection for {source['row_type']}")
        represented.update(projected)
        _local_path(root, source["source_file"])
    if represented != set(variables):
        raise ValueError("Every nine-constructions variable must be projected")
    return contract


def _dictionary(root: Path, contract: dict) -> pd.DataFrame:
    dictionary = pd.read_csv(root / "metadata" / "data_dictionary.csv", dtype=str)
    if dictionary["variable_id"].isna().any() or dictionary["variable_id"].duplicated().any():
        raise ValueError("Variable dictionary must contain unique nonmissing variable IDs")
    dictionary = dictionary.set_index("variable_id", drop=False)
    rows = []
    for number, family in contract["families"].items():
        for variable_id in family["variables"]:
            if variable_id not in dictionary.index:
                raise ValueError(f"Nine-constructions variable lacks a dictionary definition: {variable_id}")
            sources = [source for source in contract["sources"] if variable_id in source["variable_columns"]]
            rows.append({
                **dictionary.loc[variable_id].to_dict(),
                "construction_number": number,
                "construction_name": family["name"],
                "table_name": "analytics.nine_constructions",
                "table_column": variable_id,
                "applicable_row_types": "|".join(source["row_type"] for source in sources),
                "row_frequencies": "|".join(dict.fromkeys(source["frequency"] for source in sources)),
                "source_tables": "|".join(source["source_table"] for source in sources),
                "applicable_geo_id": contract.get("variable_geography_restrictions", {}).get(variable_id, ""),
                "packaging_note": "Existing values and source nulls retained; applicability follows the source schema, not nonmissing values. Quarterly model inputs remain quarter-end samples of their documented monthly measures.",
            })
    return pd.DataFrame(rows)


def _project_source(root: Path, source: dict) -> pd.DataFrame:
    path = _local_path(root, source["source_file"])
    if not path.is_file():
        raise FileNotFoundError(f"Required nine-constructions input is missing: {path}")
    frame = pd.read_parquet(path)
    columns = list(dict.fromkeys([
        source["date_column"], source["id_column"], *source["key_columns"],
        *source["variable_columns"], *source["context_columns"],
    ]))
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{source['row_type']} lacks required columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"Required nine-constructions input is empty: {source['row_type']}")
    if frame[source["key_columns"]].isna().any().any():
        raise ValueError(f"Missing source key in {source['row_type']}")
    if frame.duplicated(source["key_columns"]).any():
        raise ValueError(f"Duplicate source key in {source['row_type']}")
    identifiers = frame[source["id_column"]].astype("string")
    if not identifiers.str.fullmatch(r"\d{5}").all():
        raise ValueError(f"{source['row_type']} identifiers must retain exactly five digits")
    for column in ("county_fips", "primary_county_fips", "state_fips"):
        if column in columns:
            width = 2 if column == "state_fips" else 5
            valid = frame[column].astype("string").str.fullmatch(rf"\d{{{width}}}").fillna(False)
            if not valid.all():
                raise ValueError(f"Malformed {column} in {source['row_type']}")
    if source["row_type"] != "county_monthly":
        for column in ("county_fips", "primary_county_fips"):
            if column in columns and not frame[column].astype("string").eq("06037").all():
                raise ValueError(f"Non-LA scope in {source['row_type']}")
    dates = pd.to_datetime(frame[source["date_column"]], errors="raise")
    if dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError(f"Dates must be date-only period ends in {source['row_type']}")
    valid_dates = dates.dt.is_month_end
    if source["frequency"] == "quarterly":
        valid_dates &= dates.dt.month.isin([3, 6, 9, 12])
    if not valid_dates.all():
        raise ValueError(f"Invalid {source['frequency']} period end in {source['row_type']}")
    output = frame[columns].copy()
    expected_geo = ("COUNTY_" if source["geography_type"] == "county" else "ZIP_") + identifiers
    if "geo_id" in output and not output["geo_id"].astype("string").eq(expected_geo).all():
        raise ValueError(f"Source geography key mismatch in {source['row_type']}")
    output["date"] = dates
    output["geo_id"] = expected_geo
    for column in ("row_type", "frequency", "geography_type", "geography_scope", "source_table", "source_file"):
        output[column] = source[column]
    output["source_file_checksum_sha256"] = sha256_file(path)
    key_parts = frame[source["key_columns"]].astype("string")
    key_parts[source["date_column"]] = dates.dt.strftime("%Y-%m-%d")
    output["source_row_key"] = key_parts.agg("|".join, axis=1)
    output["applicable_variable_ids"] = "|".join(source["variable_columns"])
    return output


def build_nine_constructions(config: ProjectConfig, *, write_outputs: bool = True) -> pd.DataFrame:
    """Stack five explicit source projections and optionally publish the package.

    ``write_outputs=False`` performs the same input and dictionary checks without
    changing files, allowing validation against the native products.
    """
    root = config.root
    contract = read_nine_constructions_contract(root)
    dictionary = _dictionary(root, contract)
    frames = [_project_source(root, source) for source in contract["sources"]]
    restrictions = contract.get("variable_geography_restrictions", {})
    for source, frame in zip(contract["sources"], frames):
        for geo_id in frame["geo_id"].unique():
            applicable = [variable for variable in source["variable_columns"] if variable not in restrictions or restrictions[variable] == geo_id]
            frame.loc[frame["geo_id"].eq(geo_id), "applicable_variable_ids"] = "|".join(applicable)
        for variable, geo_id in restrictions.items():
            if variable in source["variable_columns"] and frame.loc[frame["geo_id"].ne(geo_id), variable].notna().any():
                raise ValueError(f"Geographically inapplicable values in {source['row_type']}: {variable}")
    output = pd.concat(frames, ignore_index=True, sort=False)
    output = output[[*CONTEXT_COLUMNS, *(column for column in output if column not in CONTEXT_COLUMNS)]]
    output = output.convert_dtypes()
    for column in output:
        if output[column].dtype == object and output[column].isna().all():
            output[column] = output[column].astype("string")
    # Nullable integer identifiers avoid decimal labels after stacking unrelated
    # row types; these casts preserve existing integer values and missingness.
    for column in ("regime_id", "from_regime_id", "to_regime_id", "zip_market_regime_id"):
        if column in output:
            output[column] = output[column].astype("Int64")
    for column in ("postal_zip", "county_fips", "primary_county_fips", "state_fips"):
        if column in output:
            output[column] = output[column].astype("string")
    if output.duplicated(["row_type", "source_row_key"]).any():
        raise ValueError("Combined nine-constructions row key is not unique")
    if not write_outputs:
        return output
    output_path = _local_path(root, contract["output_path"])
    dictionary_path = _local_path(root, contract["dictionary_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dictionary_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f".{uuid4().hex}.tmp"
    parquet_temp = output_path.with_name(output_path.name + suffix)
    dictionary_temp = dictionary_path.with_name(dictionary_path.name + suffix)
    try:
        output.to_parquet(parquet_temp, index=False)
        dictionary.to_csv(dictionary_temp, index=False)
        parquet_temp.replace(output_path)
        dictionary_temp.replace(dictionary_path)
    finally:
        for temporary in (parquet_temp, dictionary_temp):
            temporary.unlink(missing_ok=True)
    append_csv(root / "metadata" / "transformation_log.csv", [{
        "transformation_id": contract["methodology_id"],
        "source_id": "multiple",
        "input_file": "|".join(source["source_file"] for source in contract["sources"]),
        "output_file": f"{contract['output_path']}|{contract['dictionary_path']}",
        "input_rows_or_shape": "|".join(f"{source['row_type']}={len(frame)}" for source, frame in zip(contract["sources"], frames)),
        "output_rows": len(output),
        "description": "Project the explicit nine-construction measures and diagnostics from five accepted native products, then stack rows with their original geography, monthly/quarterly frequency, source nulls, and lineage. No interpolation, aggregation, geography broadcast, model refit, or numeric formula is applied. Record schema applicability independently of missing values.",
        "code_module": "src.harmonization.nine_constructions",
        "source_vintage": "|".join(sorted({str(value) for frame in frames for column in ("source_vintage", "latest_source_vintage") if column in frame for value in frame[column].dropna().unique()})),
        "source_filename": "|".join(Path(source["source_file"]).name for source in contract["sources"]),
        "checksum_sha256": "|".join(frame["source_file_checksum_sha256"].iloc[0] for frame in frames),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }])
    return output
