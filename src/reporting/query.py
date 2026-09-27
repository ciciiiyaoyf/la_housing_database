"""Read-only catalog queries and labeled observation downloads.

The simplified unit is a metadata label: original units, values, dates, source
intervals, and provenance are retained without conversion or aggregation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sys
from typing import Sequence

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from src.common.config import load_config
from src.common.variable_metadata import TEMPORAL_TYPES


@dataclass(frozen=True)
class ObservationDataset:
    table: str
    date_column: str


DATASETS = {
    "main": ObservationDataset("analytics.fact_housing_observation", "observation_date"),
    "version-a": ObservationDataset("analytics.housing_finance_benchmark", "reference_date"),
    "version-b": ObservationDataset("analytics.housing_finance_monthly_hist", "month"),
    "version-c": ObservationDataset("analytics.housing_finance_monthly_rt", "month"),
}
LABEL_COLUMNS = (
    "unit_simplified",
    "temporal_type",
    "temporal_type_note",
    "monetary_basis",
)
DICTIONARY = "metadata.variable_dictionary"
TABLE_DATASETS = {
    "county-monthly": "county_monthly_panel",
    "zip-monthly": "zip_monthly_panel",
    "county-annual": "county_annual_controls",
    "zcta-annual": "zcta_annual_controls",
    "county-quarterly": "county_quarterly_controls",
    "state-monthly": "state_monthly_controls",
    "state-quarterly": "state_quarterly_panel",
    "state-annual": "state_annual_controls",
    "metro-monthly": "metro_monthly_controls",
    "metro-quarterly": "metro_quarterly_panel",
    "metro-quarterly-controls": "metro_quarterly_controls",
    "coc-annual": "coc_annual_controls",
    "macro-monthly": "macro_financial_monthly",
    "comparable-county-monthly": "comparable_county_monthly_analytics",
    "zip-regimes-quarterly": "zip_market_regime_quarterly",
    "zip-regime-summary-quarterly": "la_zip_market_regime_summary_quarterly",
    "zip-regime-transitions-quarterly": "la_zip_market_regime_transitions_quarterly",
    "homebuying-scenarios": "homebuying_cost_scenarios",
    "nine-constructions": "nine_constructions",
}
# These aliases are explicit display-column mappings, never value conversions.
PANEL_ALIASES = {"zori": "zori_smoothed_sa"}
ZIP_PANEL_ALIASES = {
    "homes_sold": "redfin_zip_homes_sold_rolling_3m",
    "median_sale_price": "redfin_zip_median_sale_price_nsa_rolling_3m",
    "median_sale_price_nsa": "redfin_zip_median_sale_price_nsa_rolling_3m",
    "median_days_on_market": "redfin_zip_median_days_on_market_rolling_3m",
    "new_listings": "redfin_zip_new_listings_rolling_3m",
    "active_listings": "redfin_zip_active_listings_rolling_3m",
    "pending_sales": "redfin_zip_pending_sales_rolling_3m",
}
PANEL_CONTEXT_COLUMNS = {
    "date", "month", "quarter_end", "year", "geo_id", "geography_type",
    "geography_level", "county_fips", "county_name", "state_fips", "state_name",
    "primary_county_fips", "postal_zip", "zcta", "metro_name",
    "fhfa_geography_id", "fhfa_geography_name", "hvs_geography_id", "hvs_geography_name",
    "source_coverage_count", "panel_quality_flag", "data_quality_flag", "quality_flag",
    "latest_source_vintage", "source_vintage", "retrieved_at", "source_filename",
    "checksum_sha256", "source_id", "transformation_id", "redfin_frequency",
    "redfin_window_months", "redfin_last_updated", "normalization_assumption_status",
    "frequency_treatment", "vintage_date", "scenario_name", "geo_name", "year_end",
    "complete_regime_inputs", "missing_regime_inputs", "classification_status",
    "transition_denominator_status", "homes_sold_months_reported",
    "coc_id", "coc_name", "metro_id", "metro_division_id", "cbsa_code", "cbsa_name",
    "geoid", "tract_geoid",
}
# These are row identities and denominator diagnostics for the mixed-period
# construction delivery. They are not silently promoted to economic measures.
NINE_CONSTRUCTIONS_CONTEXT_COLUMNS = PANEL_CONTEXT_COLUMNS | {
    "row_type", "frequency", "geography_scope", "applicable_variable_ids", "source_table", "source_file",
    "source_file_checksum_sha256", "regime_id", "regime_label", "from_regime_id",
    "from_regime_label", "to_regime_id", "to_regime_label", "previous_quarter_end",
    "regime_zip_count", "classified_zip_count", "universe_zip_count",
    "comparable_zip_count", "missing_previous_classification_count",
    "transition_zip_count", "changed_regime_zip_count", "entered_regime_zip_count",
    "exited_regime_zip_count", "regime_share_of_classified",
    "classification_coverage_share_of_universe", "transition_share_of_classified",
    "transition_share_of_comparable", "changed_regime_share_of_classified",
    "changed_regime_share_of_comparable", "entered_regime_share_of_classified",
    "entered_regime_share_of_comparable", "exited_regime_share_of_classified",
    "exited_regime_share_of_comparable",
}
LAYERS = ("General", "Version A", "Version B", "Version C")
GEOGRAPHY_ALIASES = {
    "country": "national", "metropolitan_division": "metro_division", "metro_area": "metro",
    "coc": "continuum_of_care", "coc_composite": "explicit_coc_composite",
}
GEOGRAPHIES = frozenset({
    "county", "state", "postal_zip", "zcta", "national", "metro", "metro_division",
    "continuum_of_care", "explicit_coc_composite", "tract_2020", "postal_zip_county_part", "puma", "unknown",
})
CATALOG_GEOGRAPHY_SCOPES = {
    "county comparison set": {"county"},
    "county with national rate": {"county"},
    "county calibrated to California": {"county"},
    "county with broader parameter geography": {"county"},
    "county with United States rate parameter": {"county"},
    "county with national deflator": {"county"},
    "Los Angeles County postal-ZIP aggregate": {"county"},
    "Los Angeles County reference on postal-ZIP panel": {"postal_zip"},
    "complete county PUMA pool or connected regional PUMA pool or California fallback": {"county", "puma", "state"},
}


def _layer(variable_id: str) -> str:
    for suffix, layer in (("_benchmark", "Version A"), ("_monthly_hist", "Version B"), ("_monthly_rt", "Version C")):
        if variable_id.endswith(suffix):
            return layer
    return "General"


def _canonical_geographies(values: Sequence[str]) -> list[str]:
    canonical = [GEOGRAPHY_ALIASES.get(value, value) for value in values]
    invalid = set(canonical) - GEOGRAPHIES
    if invalid:
        raise ValueError(f"Unknown geography {sorted(invalid)}; use options --field geography.")
    return canonical


def _geography_scope(value: object) -> set[str]:
    if pd.isna(value):
        return set()
    scope: set[str] = set()
    for token in str(value).split("|"):
        token = token.strip()
        scope.update(CATALOG_GEOGRAPHY_SCOPES.get(token, {GEOGRAPHY_ALIASES.get(token, token.lower() if token == "PUMA" else token)}))
    return scope


def _metadata_filters(
    frame: pd.DataFrame, *, geographies: Sequence[str] = (), frequencies: Sequence[str] = (),
    native_frequencies: Sequence[str] = (), domains: Sequence[str] = (), layers: Sequence[str] = (),
    proxy: bool | None = None, constructed: bool | None = None,
) -> pd.DataFrame:
    def require(column: str) -> None:
        if column not in frame:
            raise ValueError(f"Requested filter requires dictionary column {column!r}; rebuild the database.")
    mask = pd.Series(True, index=frame.index)
    for column, values in (("final_frequency", frequencies), ("native_frequency", native_frequencies), ("data_domain", domains)):
        if values:
            require(column)
            if column == "data_domain":
                mask &= frame[column].isin(values)
            else:
                mask &= frame[column].map(lambda value: False if pd.isna(value) else bool(set(str(value).split("|")) & set(values)))
    if geographies:
        require("geography")
        requested = set(_canonical_geographies(geographies))
        mask &= frame["geography"].map(lambda value: bool(_geography_scope(value) & requested))
    if layers:
        invalid = set(layers) - set(LAYERS)
        if invalid:
            raise ValueError(f"Unknown layer {sorted(invalid)}; choose from {', '.join(LAYERS)}.")
        mask &= frame["system_layer"].isin(layers)
    for column, value in (("is_proxy", proxy), ("is_constructed", constructed)):
        if value is not None:
            if not isinstance(value, bool):
                raise ValueError(f"{column} filter must be true or false.")
            require(column)
            # Missing and empty metadata are unknown, never implicitly false.
            normalized = frame[column].astype("string").str.lower().str.strip()
            mask &= normalized.eq(str(value).lower()).fillna(False)
    return frame.loc[mask].copy()


def _validate_dictionary(connection: duckdb.DuckDBPyConnection) -> None:
    try:
        columns = {item[0] for item in connection.execute(f"DESCRIBE {DICTIONARY}").fetchall()}
    except duckdb.Error as exc:
        raise ValueError("Variable dictionary is missing; rebuild the database first.") from exc
    missing = {"variable_id", *LABEL_COLUMNS} - columns
    if missing:
        raise ValueError(
            "Variable dictionary lacks classification columns "
            f"{sorted(missing)}; rebuild the database first."
        )
    invalid = connection.execute(
        f"SELECT variable_id FROM {DICTIONARY} "
        "GROUP BY variable_id HAVING count(*) != 1 OR variable_id IS NULL LIMIT 1"
    ).fetchone()
    if invalid is not None:
        raise ValueError("Variable dictionary must contain one non-null row per variable_id.")
    incomplete = connection.execute(
        f"SELECT variable_id FROM {DICTIONARY} "
        "WHERE nullif(trim(unit_simplified), '') IS NULL "
        "OR nullif(trim(temporal_type), '') IS NULL LIMIT 1"
    ).fetchone()
    if incomplete is not None:
        raise ValueError(f"Variable classification is missing for {incomplete[0]!r}.")


def _filters(
    *,
    variable_ids: Sequence[str] = (),
    units: Sequence[str] = (),
    temporal_types: Sequence[str] = (),
) -> tuple[list[str], list[object]]:
    invalid_types = set(temporal_types) - TEMPORAL_TYPES
    if invalid_types:
        raise ValueError(
            f"Unknown temporal_type {sorted(invalid_types)}; choose from {', '.join(sorted(TEMPORAL_TYPES))}."
        )
    clauses: list[str] = []
    parameters: list[object] = []
    for column, values in (
        ("variable_id", variable_ids),
        ("unit_simplified", units),
        ("temporal_type", temporal_types),
    ):
        if values:
            clauses.append(f"d.{column} IN ({', '.join('?' for _ in values)})")
            parameters.extend(values)
    return clauses, parameters


def query_catalog(
    connection: duckdb.DuckDBPyConnection,
    *,
    variable_ids: Sequence[str] = (),
    units: Sequence[str] = (),
    temporal_types: Sequence[str] = (),
    geographies: Sequence[str] = (),
    frequencies: Sequence[str] = (),
    native_frequencies: Sequence[str] = (),
    domains: Sequence[str] = (),
    layers: Sequence[str] = (),
    proxy: bool | None = None,
    constructed: bool | None = None,
) -> pd.DataFrame:
    """Return full dictionary rows matching exact metadata labels."""

    _validate_dictionary(connection)
    clauses, parameters = _filters(
        variable_ids=variable_ids, units=units, temporal_types=temporal_types
    )
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    frame = connection.execute(
        f"SELECT d.* FROM {DICTIONARY} d{where} ORDER BY d.variable_id", parameters
    ).fetchdf()
    frame["system_layer"] = frame["variable_id"].map(_layer)
    return _metadata_filters(
        frame, geographies=geographies, frequencies=frequencies, native_frequencies=native_frequencies,
        domains=domains, layers=layers, proxy=proxy, constructed=constructed,
    )


def _iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Date must use YYYY-MM-DD: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date: {value!r}") from exc


def _id_geography_expression(identifier: str) -> str:
    patterns = (
        ("^ZIPCOUNTYPART_[0-9]{5}_[0-9]{5}$", "postal_zip_county_part"),
        ("^COUNTY_[0-9]{5}$", "county"), ("^STATE_[0-9]{2}$", "state"),
        ("^ZIP_[0-9]{5}$", "postal_zip"), ("^ZCTA_[0-9]{5}$", "zcta"),
        ("^METRODIV_[0-9]{5}$", "metro_division"), ("^(METRO|CBSA)_[0-9]{5}$", "metro"),
        ("^COC_COMPOSITE_", "explicit_coc_composite"), ("^COC_", "continuum_of_care"),
        ("^TRACT_[0-9]{11}$", "tract_2020"), ("^PUMA_", "puma"),
    )
    return (
        f"CASE WHEN {identifier} IN ('US', 'USA', 'COUNTRY_US', 'NATIONAL_US') THEN 'national' "
        + " ".join(f"WHEN regexp_matches({identifier}, '{pattern}') THEN '{kind}'" for pattern, kind in patterns)
        + " ELSE 'unknown' END"
    )


def _row_geo_keys(
    connection: duckdb.DuckDBPyConnection, columns: Sequence[str], *, alias: str = "",
) -> list[tuple[str, str, str]]:
    """Return actual row geography type, query identifier, and untouched native key."""
    def col(name: str) -> str:
        return (alias + "." if alias else "") + _quote(name)
    keys: list[tuple[str, str, str]] = []
    if "geo_id" in columns:
        identifier = f"CAST({col('geo_id')} AS VARCHAR)"
        geo_type = _id_geography_expression(identifier)
        registry_columns = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='metadata' AND table_name='geography'"
        ).fetchall()}
        if alias and {"geo_id", "geo_type"} <= registry_columns:
            # Scalar lookup avoids multiplying observations for reference vintages.
            lookup = (
                "(SELECT CASE g.geo_type WHEN 'country' THEN 'national' "
                "WHEN 'metropolitan_division' THEN 'metro_division' WHEN 'metro_area' THEN 'metro' "
                f"ELSE g.geo_type END FROM metadata.geography g WHERE g.geo_id = {identifier} LIMIT 1)"
            )
            geo_type = f"COALESCE({lookup}, {geo_type})"
        keys.append((geo_type, identifier, identifier))
    # These tables retain provider geography keys alongside their broad panel key.
    for name, fallback in (("fhfa_geography_id", "metro_division"), ("hvs_geography_id", "metro"), ("ces_geography_id", "metro"), ("cpi_geography_id", "metro")):
        if name in columns:
            identifier = f"CAST({col(name)} AS VARCHAR)"
            inferred = _id_geography_expression(identifier)
            geo_type = f"CASE WHEN ({inferred}) = 'unknown' THEN '{fallback}' ELSE ({inferred}) END"
            keys.append((geo_type, identifier, identifier))
    if not keys:
        for name, prefix, kind in (
            ("postal_zip", "ZIP_", "postal_zip"), ("zcta", "ZCTA_", "zcta"),
            ("county_fips", "COUNTY_", "county"), ("state_fips", "STATE_", "state"),
            ("coc_id", "COC_", "continuum_of_care"),
        ):
            if name in columns:
                native = f"CAST({col(name)} AS VARCHAR)"
                keys.append((f"'{kind}'", f"'{prefix}' || {native}", native))
                break
    return keys


def _row_geography_filters(
    connection: duckdb.DuckDBPyConnection, columns: Sequence[str], *,
    geographies: Sequence[str] = (), geo_ids: Sequence[str] = (), alias: str = "",
) -> tuple[list[str], list[object]]:
    if not geographies and not geo_ids:
        return [], []
    types = _canonical_geographies(geographies)
    keys = _row_geo_keys(connection, columns, alias=alias)
    if not keys:
        raise ValueError("Selected dataset has no supported native geography identifier.")
    alternatives, parameters = [], []
    for geo_type, identifier, native in keys:
        conditions = [f"{identifier} IS NOT NULL"]
        if types:
            conditions.append(f"({geo_type}) IN ({', '.join('?' for _ in types)})")
            parameters.extend(types)
        if geo_ids:
            placeholders = ", ".join("?" for _ in geo_ids)
            conditions.append(f"({identifier} IN ({placeholders}) OR {native} IN ({placeholders}))")
            parameters.extend(geo_ids)
            parameters.extend(geo_ids)
        alternatives.append("(" + " AND ".join(conditions) + ")")
    return ["(" + " OR ".join(alternatives) + ")"], parameters


def _in_clause(expression: str, values: Sequence[object]) -> tuple[str, list[object]]:
    return (f"{expression} IN ({', '.join('?' for _ in values)})", list(values)) if values else ("FALSE", [])


def _column_geographies_for_ids(
    connection: duckdb.DuckDBPyConnection, table: str, columns: Sequence[str],
    geo_ids: Sequence[str], geographies: Sequence[str],
) -> list[str]:
    """Keep mixed-geography wide measures aligned with the requested native key."""
    if not geo_ids:
        return list(geographies)
    matched: set[str] = set()
    for geo_type, identifier, native in _row_geo_keys(connection, columns, alias="q"):
        id_clause, id_parameters = _in_clause(identifier, geo_ids)
        native_clause, native_parameters = _in_clause(native, geo_ids)
        matched.update(str(row[0]) for row in connection.execute(
            f"SELECT DISTINCT ({geo_type}) FROM {table} q WHERE ({id_clause} OR {native_clause})",
            [*id_parameters, *native_parameters],
        ).fetchall() if row[0] is not None and str(row[0]) != "unknown")
    if geographies:
        matched &= set(_canonical_geographies(geographies))
    # No matching rows remains an empty row slice, not a new metadata guess.
    return sorted(matched) if matched else list(geographies)


def _observation_query(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset: str = "main",
    variable_ids: Sequence[str] = (),
    units: Sequence[str] = (),
    temporal_types: Sequence[str] = (),
    geographies: Sequence[str] = (),
    frequencies: Sequence[str] = (),
    native_frequencies: Sequence[str] = (),
    domains: Sequence[str] = (),
    layers: Sequence[str] = (),
    proxy: bool | None = None,
    constructed: bool | None = None,
    geo_ids: Sequence[str] = (),
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, list[object]]:
    if dataset not in DATASETS:
        raise ValueError(f"Unknown dataset {dataset!r}; choose from {', '.join(DATASETS)}.")
    definition = DATASETS[dataset]
    start, end = _iso_date(start_date), _iso_date(end_date)
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before end_date.")
    _validate_dictionary(connection)
    try:
        columns = {
            item[0] for item in connection.execute(f"DESCRIBE {definition.table}").fetchall()
        }
    except duckdb.Error as exc:
        raise ValueError(f"Dataset {dataset!r} has not been built in this database.") from exc
    required = {"variable_id", "geo_id", definition.date_column}
    if required - columns:
        raise ValueError(f"Dataset {dataset!r} lacks columns {sorted(required - columns)}.")
    unmapped = connection.execute(
        f"SELECT f.variable_id FROM {definition.table} f "
        f"LEFT JOIN {DICTIONARY} d ON f.variable_id = d.variable_id "
        "WHERE d.variable_id IS NULL LIMIT 1"
    ).fetchone()
    if unmapped is not None:
        raise ValueError(f"Dataset variable {unmapped[0]!r} has no dictionary classification.")
    if variable_ids:
        present = {
            row[0] for row in connection.execute(
                f"SELECT DISTINCT variable_id FROM {definition.table} "
                f"WHERE variable_id IN ({', '.join('?' for _ in variable_ids)})", list(variable_ids)
            ).fetchall()
        }
        absent = set(variable_ids) - present
        if absent:
            raise ValueError(
                f"Requested variables have no observations in dataset {dataset!r}: {sorted(absent)}. "
                "Check the catalog and choose the appropriate observations dataset or table export."
            )
    clauses, parameters = _filters(
        variable_ids=variable_ids, units=units, temporal_types=temporal_types
    )
    metadata_native_frequencies = native_frequencies if dataset != "main" else ()
    if metadata_native_frequencies or domains or layers or proxy is not None or constructed is not None:
        selection = query_catalog(
            connection, native_frequencies=metadata_native_frequencies, domains=domains, layers=layers,
            proxy=proxy, constructed=constructed,
        )
        clause, values = _in_clause("d.variable_id", selection["variable_id"].tolist())
        clauses.append(clause)
        parameters.extend(values)
    row_clauses, row_values = _row_geography_filters(
        connection, columns, geographies=geographies, geo_ids=geo_ids, alias="f"
    )
    clauses.extend(row_clauses)
    parameters.extend(row_values)
    if frequencies:
        if dataset == "main":
            if "final_frequency" not in columns:
                raise ValueError("Frequency filter requires observation column 'final_frequency'.")
            expression = "f.final_frequency"
        elif dataset == "version-a":
            if "source_frequency" not in columns:
                raise ValueError("Version A frequency filter requires source_frequency.")
            expression = "f.source_frequency"
        else:
            expression = "'monthly'"
        clause, values = _in_clause(expression, frequencies)
        clauses.append(clause)
        parameters.extend(values)
    if native_frequencies and dataset == "main":
        if "native_frequency" not in columns:
            raise ValueError("Native-frequency filter requires observation column 'native_frequency'.")
        clause, values = _in_clause("f.native_frequency", native_frequencies)
        clauses.append(clause)
        parameters.extend(values)
    if start is not None:
        clauses.append(f"CAST(f.{definition.date_column} AS DATE) >= ?")
        parameters.append(start)
    if end is not None:
        clauses.append(f"CAST(f.{definition.date_column} AS DATE) <= ?")
        parameters.append(end)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    existing_labels = [column for column in LABEL_COLUMNS if column in columns]
    exclusion = f" EXCLUDE ({', '.join(existing_labels)})" if existing_labels else ""
    labels = ", ".join(f"d.{column}" for column in LABEL_COLUMNS)
    sql = (
        f"SELECT f.*{exclusion}, {labels} FROM {definition.table} f "
        f"JOIN {DICTIONARY} d ON f.variable_id = d.variable_id{where} "
        f"ORDER BY f.variable_id, f.geo_id, f.{definition.date_column}"
    )
    restricted_source = (
        " OR source_id = 'hud_usps_ncwm_quarterly'" if "source_id" in columns else ""
    )
    restricted = connection.execute(
        f"SELECT variable_id FROM ({sql}) selected "
        f"WHERE starts_with(variable_id, 'usps_'){restricted_source} LIMIT 1", parameters
    ).fetchone()
    if restricted is not None:
        raise ValueError("Refusing ordinary download of restricted HUD-USPS observations.")
    return sql, parameters


def query_observations(
    connection: duckdb.DuckDBPyConnection, **filters: object
) -> pd.DataFrame:
    """Return labeled long-table observations with native columns intact.

    Date bounds are inclusive. Main uses observation_date, Version A uses the
    explicit reference_date, and Versions B/C use month. Version C retains all
    vintages; this function never selects a latest vintage implicitly.
    """

    sql, parameters = _observation_query(connection, **filters)
    return connection.execute(sql, parameters).fetchdf()


def _quote(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def _table_frequency(dataset: str) -> str:
    if "quarterly" in dataset:
        return "quarterly"
    if "annual" in dataset:
        return "annual"
    return "monthly"


def list_datasets(connection: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """List supported selector names without requiring SQL or loading observations."""
    rows = [
        {"kind": "observations", "dataset": name, "table_name": definition.table, "date_column": definition.date_column}
        for name, definition in DATASETS.items()
    ]
    for name, table in TABLE_DATASETS.items():
        date_column = "quarter_end" if name in {"metro-quarterly", "zip-regimes-quarterly", "zip-regime-summary-quarterly", "zip-regime-transitions-quarterly"} else "date"
        rows.append({"kind": "table", "dataset": name, "table_name": "analytics." + table, "date_column": date_column})
    return pd.DataFrame(rows)


OPTION_FIELDS = ("domain", "layer", "frequency", "native-frequency", "geography", "geo-id", "unit", "temporal-type", "proxy", "constructed")


def list_options(
    connection: duckdb.DuckDBPyConnection, field: str, *, kind: str = "catalog",
    dataset: str | None = None, geographies: Sequence[str] = (),
) -> pd.DataFrame:
    """Return exact filter values as a single string-valued ``value`` column."""
    if field not in OPTION_FIELDS:
        raise ValueError(f"Unknown option field {field!r}; choose from {', '.join(OPTION_FIELDS)}.")
    if kind not in {"catalog", "observations", "table"}:
        raise ValueError("Options kind must be catalog, observations, or table.")
    frame = query_catalog(connection)
    relation: str | None = None
    if kind == "observations":
        dataset = dataset or "main"
        if dataset not in DATASETS:
            raise ValueError(f"Unknown observations dataset {dataset!r}.")
        relation = DATASETS[dataset].table
    elif kind == "table":
        if dataset not in TABLE_DATASETS:
            raise ValueError("Table options require a supported --dataset; use datasets to list choices.")
        relation = "analytics." + TABLE_DATASETS[dataset]
    elif field == "geo-id":
        raise ValueError("Geo-id options require --kind observations or --kind table and a dataset.")
    columns = []
    if relation is not None:
        try:
            columns = [row[0] for row in connection.execute(f"DESCRIBE {relation}").fetchall()]
        except duckdb.Error as exc:
            raise ValueError(f"Dataset {dataset!r} has not been built.") from exc
    if field in {"geography", "geo-id"} and relation is not None:
        keys = _row_geo_keys(connection, columns, alias="q")
        if not keys:
            raise ValueError("Dataset has no supported native geography key.")
        values: set[str] = set()
        types = _canonical_geographies(geographies)
        for geo_type, identifier, _ in keys:
            expression = geo_type if field == "geography" else identifier
            where = f"{identifier} IS NOT NULL"
            parameters = []
            if types:
                clause, parameters = _in_clause(f"({geo_type})", types)
                where += " AND " + clause
            values.update(str(row[0]) for row in connection.execute(
                f"SELECT DISTINCT {expression} FROM {relation} q WHERE {where}", parameters
            ).fetchall() if row[0] is not None)
        return pd.DataFrame({"value": sorted(values)})
    if relation is not None and (field == "frequency" or field == "native-frequency" and dataset == "main"):
        row_clauses, row_parameters = _row_geography_filters(connection, columns, geographies=geographies, alias="q")
        where = " WHERE " + " AND ".join(row_clauses) if row_clauses else ""
        if kind == "table" and dataset == "nine-constructions" and field == "frequency":
            values = connection.execute(
                f"SELECT DISTINCT frequency FROM {relation} q{where} ORDER BY frequency",
                row_parameters,
            ).fetchall()
            return pd.DataFrame({"value": [str(row[0]) for row in values if row[0] is not None]})
        if field == "frequency" and (kind == "table" or dataset in {"version-b", "version-c"}):
            exists = connection.execute(f"SELECT 1 FROM {relation} q{where} LIMIT 1", row_parameters).fetchone()
            return pd.DataFrame({"value": [_table_frequency(dataset)] if exists else []})
        column = "native_frequency" if field == "native-frequency" else "final_frequency" if dataset == "main" else "source_frequency"
        if column not in columns:
            raise ValueError(f"Frequency options require dataset column {column!r}.")
        row_clauses.append(f"{_quote(column)} IS NOT NULL")
        where = " WHERE " + " AND ".join(row_clauses)
        values = [str(row[0]) for row in connection.execute(
            f"SELECT DISTINCT {_quote(column)} FROM {relation} q{where} ORDER BY 1", row_parameters
        ).fetchall()]
        return pd.DataFrame({"value": values})
    if relation is not None:
        if kind == "observations":
            clauses, parameters = _row_geography_filters(connection, columns, geographies=geographies, alias="q")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            ids = {row[0] for row in connection.execute(f"SELECT DISTINCT variable_id FROM {relation} q{where}", parameters).fetchall()}
            frame = frame.loc[frame["variable_id"].isin(ids)]
        else:
            _, _, frame = _table_query(connection, dataset=dataset, geographies=geographies)
    elif geographies:
        frame = _metadata_filters(frame, geographies=geographies)
    field_columns = {
        "domain": "data_domain", "layer": "system_layer", "frequency": "final_frequency",
        "native-frequency": "native_frequency", "unit": "unit_simplified", "temporal-type": "temporal_type",
        "proxy": "is_proxy", "constructed": "is_constructed", "geography": "geography",
    }
    column = field_columns[field]
    if column not in frame:
        raise ValueError(f"Options for {field!r} require dictionary column {column!r}.")
    values = set()
    for value in frame[column].dropna():
        if field == "geography":
            values.update(_geography_scope(value))
        elif field in {"frequency", "native-frequency"}:
            values.update(token.strip() for token in str(value).split("|") if token.strip())
        elif field in {"proxy", "constructed"}:
            if str(value).strip().lower() in {"true", "false"}:
                values.add(str(value).strip().lower())
        elif str(value).strip():
            values.add(str(value))
    return pd.DataFrame({"value": sorted(values)})


def _table_query(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset: str,
    variable_ids: Sequence[str] = (),
    units: Sequence[str] = (),
    temporal_types: Sequence[str] = (),
    geographies: Sequence[str] = (),
    frequencies: Sequence[str] = (),
    native_frequencies: Sequence[str] = (),
    domains: Sequence[str] = (),
    layers: Sequence[str] = (),
    proxy: bool | None = None,
    constructed: bool | None = None,
    geo_ids: Sequence[str] = (),
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, list[object], pd.DataFrame]:
    if dataset not in TABLE_DATASETS:
        raise ValueError(f"Unknown table dataset {dataset!r}.")
    if dataset == "nine-constructions":
        return _nine_constructions_table_query(
            connection, variable_ids=variable_ids, units=units, temporal_types=temporal_types,
            geographies=geographies, frequencies=frequencies, native_frequencies=native_frequencies,
            domains=domains, layers=layers, proxy=proxy, constructed=constructed, geo_ids=geo_ids,
            start_date=start_date, end_date=end_date,
        )
    if dataset == "homebuying-scenarios":
        return _scenario_table_query(
            connection, variable_ids=variable_ids, units=units, temporal_types=temporal_types,
            geographies=geographies, frequencies=frequencies, native_frequencies=native_frequencies,
            domains=domains, layers=layers, proxy=proxy, constructed=constructed, geo_ids=geo_ids,
            start_date=start_date, end_date=end_date,
        )
    table = "analytics." + TABLE_DATASETS[dataset]
    dictionary = query_catalog(connection)
    try:
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
    except duckdb.Error as exc:
        raise ValueError(f"Table dataset {dataset!r} has not been built.") from exc
    column_geographies = _column_geographies_for_ids(connection, table, columns, geo_ids, geographies) if "geography" in dictionary else geographies
    selected = query_catalog(
        connection, variable_ids=variable_ids, units=units, temporal_types=temporal_types,
        geographies=column_geographies, native_frequencies=native_frequencies,
        domains=domains, layers=layers, proxy=proxy, constructed=constructed,
    ).set_index("variable_id", drop=False)
    aliases = {**PANEL_ALIASES, **(ZIP_PANEL_ALIASES if dataset == "zip-monthly" else {})}
    known = set(dictionary["variable_id"])
    mapping = {column: aliases.get(column, column) for column in columns}
    present = set(mapping.values()) & known
    absent = set(variable_ids) - present
    if absent:
        raise ValueError(f"Requested variables are absent from table {dataset!r}: {sorted(absent)}.")
    variable_columns = [column for column in columns if mapping[column] in selected.index]
    if not variable_columns:
        raise ValueError(f"No classified variable columns match the filters in table {dataset!r}.")
    restricted = [column for column in columns if column.startswith("usps_")]
    if restricted:
        raise ValueError("Refusing ordinary download from a table containing restricted HUD-USPS fields.")
    if "source_id" in columns and connection.execute(
        f"SELECT 1 FROM {table} WHERE source_id = ? LIMIT 1", ["hud_usps_ncwm_quarterly"]
    ).fetchone() is not None:
        raise ValueError("Refusing ordinary download from a table containing restricted HUD-USPS sources.")
    context = {
        column for column in columns
        if column in PANEL_CONTEXT_COLUMNS or column.endswith(
            ("_quality_flag", "_source_vintage", "_source_filename", "_checksum_sha256", "_retrieved_at",
             "_source_date", "_source_id", "_frequency", "_available", "_method",
             "_geography_id", "_geography_name", "_regime_id", "_regime_label", "_model_version",
             "_fingerprint_sha256", "_quarter_end", "_fips")
        )
        or column.startswith(("source_", "is_")) and mapping[column] not in known
        or column in {"regime_id", "regime_label"}
    }
    output_columns = [column for column in columns if column in context or column in variable_columns]
    rows = []
    for column in variable_columns:
        rows.append({"table_name": table, "table_column": column, **selected.loc[mapping[column]].to_dict()})
    metadata = pd.DataFrame(rows)
    metadata.attrs["unclassified_columns"] = [
        column for column in columns if column not in context and mapping[column] not in known
    ]
    date_column = next((column for column in ("date", "quarter_end", "month") if column in columns), None)
    start, end = _iso_date(start_date), _iso_date(end_date)
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before end_date.")
    if (start is not None or end is not None) and date_column is None:
        raise ValueError(f"Table {dataset!r} has no supported date column for date filtering.")
    clauses, parameters = [], []
    row_clauses, row_values = _row_geography_filters(
        connection, columns, geographies=geographies, geo_ids=geo_ids, alias="q"
    )
    clauses.extend(row_clauses)
    parameters.extend(row_values)
    if frequencies and _table_frequency(dataset) not in frequencies:
        clauses.append("FALSE")
    for operator, bound in ((">=", start), ("<=", end)):
        if bound is not None:
            clauses.append(f"CAST({_quote(date_column)} AS DATE) {operator} ?")
            parameters.append(bound)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_columns = [column for column in ("geo_id", "county_fips", "postal_zip", "zcta", date_column) if column in columns]
    order = " ORDER BY " + ", ".join(_quote(column) for column in order_columns) if order_columns else ""
    sql = f"SELECT {', '.join(_quote(column) for column in output_columns)} FROM {table} q{where}{order}"
    return sql, parameters, metadata


def _nine_constructions_table_query(
    connection: duckdb.DuckDBPyConnection, *, variable_ids: Sequence[str],
    units: Sequence[str], temporal_types: Sequence[str], geographies: Sequence[str],
    frequencies: Sequence[str], native_frequencies: Sequence[str], domains: Sequence[str],
    layers: Sequence[str], proxy: bool | None, constructed: bool | None,
    geo_ids: Sequence[str], start_date: str | None, end_date: str | None,
) -> tuple[str, list[object], pd.DataFrame]:
    """Filter a union by structural applicability, never by non-null values."""
    table = "analytics.nine_constructions"
    try:
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
    except duckdb.Error as exc:
        raise ValueError("Table dataset 'nine-constructions' has not been built.") from exc
    required = {"row_type", "date", "frequency", "geo_id", "source_table", "applicable_variable_ids"}
    if not required <= set(columns):
        raise ValueError(f"Nine-constructions table is missing required columns: {sorted(required - set(columns))}.")
    if any(column.startswith("usps_") for column in columns):
        raise ValueError("Refusing ordinary download from a table containing restricted HUD-USPS fields.")
    dictionary = query_catalog(connection)
    column_geographies = _column_geographies_for_ids(
        connection, table, columns, geo_ids, geographies
    ) if "geography" in dictionary else geographies
    selected = query_catalog(
        connection, variable_ids=variable_ids, units=units, temporal_types=temporal_types,
        geographies=column_geographies, native_frequencies=native_frequencies,
        domains=domains, layers=layers, proxy=proxy, constructed=constructed,
    ).set_index("variable_id", drop=False)
    known = set(dictionary["variable_id"])
    mapping = {column: PANEL_ALIASES.get(column, column) for column in columns}
    present = set(mapping.values()) & known
    absent = set(variable_ids) - present
    if absent:
        raise ValueError(f"Requested variables are absent from table 'nine-constructions': {sorted(absent)}.")
    candidates = [column for column in columns if mapping[column] in selected.index]
    if not candidates:
        raise ValueError("No classified variable columns match the filters in table 'nine-constructions'.")
    start, end = _iso_date(start_date), _iso_date(end_date)
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before end_date.")
    clauses, parameters = _row_geography_filters(
        connection, columns, geographies=geographies, geo_ids=geo_ids, alias="q"
    )
    if frequencies:
        clause, values = _in_clause('q."frequency"', frequencies)
        clauses.append(clause)
        parameters.extend(values)
    for operator, bound in ((">=", start), ("<=", end)):
        if bound is not None:
            clauses.append(f'CAST(q."date" AS DATE) {operator} ?')
            parameters.append(bound)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # A branch can legitimately contain entirely missing measurements. Its
    # schema-level applicability, not the observed values, determines inclusion.
    applicability: dict[str, dict[str, set[str]]] = {}
    for row_type, source_table, frequency, ids in connection.execute(
        f"SELECT DISTINCT row_type, source_table, frequency, applicable_variable_ids FROM {table} q{where}",
        parameters,
    ).fetchall():
        for variable_id in str(ids or "").split("|"):
            if not variable_id:
                continue
            entry = applicability.setdefault(
                variable_id, {"row_types": set(), "source_tables": set(), "frequencies": set()}
            )
            entry["row_types"].add(str(row_type))
            entry["source_tables"].add(str(source_table))
            entry["frequencies"].add(str(frequency))
    variable_columns = [column for column in candidates if mapping[column] in applicability]
    selected_ids = sorted({mapping[column] for column in variable_columns})
    if selected_ids:
        clauses.append("(" + " OR ".join(
            "list_contains(string_split(q.applicable_variable_ids, '|'), ?)" for _ in selected_ids
        ) + ")")
        parameters.extend(selected_ids)
    else:
        clauses.append("FALSE")
    context = {
        column for column in columns
        if mapping[column] not in known and (
            column in NINE_CONSTRUCTIONS_CONTEXT_COLUMNS
            or column.endswith((
                "_quality_flag", "_source_vintage", "_source_filename", "_checksum_sha256",
                "_retrieved_at", "_source_date", "_source_id", "_available", "_method",
                "_regime_id", "_regime_label", "_model_version", "_fingerprint_sha256",
            ))
            or column.startswith(("source_", "is_"))
        )
    }
    output_columns = [column for column in columns if column in context or column in variable_columns]
    rows = []
    for column in variable_columns:
        variable_id = mapping[column]
        entry = applicability[variable_id]
        rows.append({
            "table_name": table, "table_column": column, **selected.loc[variable_id].to_dict(),
            "applicable_row_types": "|".join(sorted(entry["row_types"])),
            "applicable_source_tables": "|".join(sorted(entry["source_tables"])),
            "delivery_frequencies": "|".join(sorted(entry["frequencies"])),
        })
    metadata = pd.DataFrame(rows, columns=[
        "table_name", "table_column", *selected.columns,
        "applicable_row_types", "applicable_source_tables", "delivery_frequencies",
    ])
    family_table = connection.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'metadata' AND table_name = 'nine_constructions_dictionary'"
    ).fetchone()
    if family_table is not None:
        families = connection.execute("SELECT * FROM metadata.nine_constructions_dictionary").fetchdf()
        extra_columns = [column for column in (
            "construction_number", "construction_name", "applicable_geo_id", "packaging_note"
        ) if column in families]
        if families["variable_id"].duplicated().any():
            raise ValueError("Nine-constructions dictionary requires one row per variable_id.")
        if extra_columns:
            metadata = metadata.merge(
                families[["variable_id", *extra_columns]], on="variable_id", how="left", validate="many_to_one"
            )
    metadata.attrs["unclassified_columns"] = [
        column for column in columns if column not in context and mapping[column] not in known
    ]
    where = " WHERE " + " AND ".join(clauses)
    order_columns = [column for column in (
        "row_type", "geo_id", "frequency", "date", "regime_id", "from_regime_id", "to_regime_id"
    ) if column in columns]
    sql = (f"SELECT {', '.join(_quote(column) for column in output_columns)} FROM {table} q{where}"
           + " ORDER BY " + ", ".join(_quote(column) for column in order_columns))
    return sql, parameters, metadata


def _scenario_table_query(
    connection: duckdb.DuckDBPyConnection,
    *,
    variable_ids: Sequence[str],
    units: Sequence[str],
    temporal_types: Sequence[str],
    geographies: Sequence[str] = (),
    frequencies: Sequence[str] = (),
    native_frequencies: Sequence[str] = (),
    domains: Sequence[str] = (),
    layers: Sequence[str] = (),
    proxy: bool | None = None,
    constructed: bool | None = None,
    geo_ids: Sequence[str] = (),
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, list[object], pd.DataFrame]:
    """Select native scenario measures with explicit row-key classifications.

    Filters within a row-key group are alternatives; selected groups intersect.
    For example, a 10% down-payment ID plus a 3% loan closing-cost ID selects
    their shared scenario rows, preserving both amounts and all assumptions.
    """

    table = "analytics.homebuying_cost_scenarios"
    dictionary = query_catalog(connection).set_index("variable_id", drop=False)
    try:
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
    except duckdb.Error as exc:
        raise ValueError("Table dataset 'homebuying-scenarios' has not been built.") from exc
    column_geographies = _column_geographies_for_ids(connection, table, columns, geo_ids, geographies) if "geography" in dictionary else geographies
    selected = query_catalog(
        connection, variable_ids=variable_ids, units=units, temporal_types=temporal_types,
        geographies=column_geographies, native_frequencies=native_frequencies,
        domains=domains, layers=layers, proxy=proxy, constructed=constructed,
    ).set_index("variable_id", drop=False)
    if any(column.startswith("usps_") for column in columns):
        raise ValueError("Refusing ordinary download from a scenario table containing restricted HUD-USPS fields.")
    if "source_id" in columns and connection.execute(
        f"SELECT 1 FROM {table} WHERE source_id = ? LIMIT 1", ["hud_usps_ncwm_quarterly"]
    ).fetchone() is not None:
        raise ValueError("Refusing ordinary download from a scenario table containing restricted HUD-USPS sources.")
    dynamic_measures = {
        "median_sale_price": "price_source_variable_id",
        "down_payment_amount": "down_payment_variable_id",
        "closing_cost_amount": "closing_cost_variable_id",
    }
    direct_measures = ("estimated_mortgage_amount_scenario", "total_cash_to_close_scenario")
    required = {"date", *dynamic_measures, *dynamic_measures.values(), *direct_measures}
    if required - set(columns):
        raise ValueError(f"Scenario table lacks columns {sorted(required - set(columns))}.")
    roles: dict[str, list[str]] = {}
    present = set(direct_measures)
    for measure, row_key in dynamic_measures.items():
        ids = [row[0] for row in connection.execute(f"SELECT DISTINCT {_quote(row_key)} FROM {table}").fetchall()]
        unmapped = [variable for variable in ids if variable not in dictionary.index]
        if unmapped:
            raise ValueError(f"Scenario row keys lack dictionary classifications: {unmapped}.")
        if any(variable.startswith("usps_") for variable in ids):
            raise ValueError("Refusing ordinary download of restricted HUD-USPS scenario observations.")
        roles[measure] = ids
        present.update(ids)
    absent = set(variable_ids) - present
    if absent:
        raise ValueError(f"Requested variables are absent from homebuying scenarios: {sorted(absent)}.")
    clauses: list[str] = []
    parameters: list[object] = []
    row_clauses, row_values = _row_geography_filters(
        connection, columns, geographies=geographies, geo_ids=geo_ids, alias="q"
    )
    clauses.extend(row_clauses)
    parameters.extend(row_values)
    if frequencies and "monthly" not in frequencies:
        clauses.append("FALSE")
    output_measures: list[str] = []
    metadata_rows = []
    for measure, ids in roles.items():
        matched = sorted(set(ids) & set(selected.index))
        if not matched:
            continue
        row_key = dynamic_measures[measure]
        clauses.append(f"{_quote(row_key)} IN ({', '.join('?' for _ in matched)})")
        parameters.extend(matched)
        output_measures.append(measure)
        for variable in matched:
            metadata_rows.append({
                "table_name": table, "table_column": measure,
                "row_identifier_column": row_key, "row_identifier_value": variable,
                **selected.loc[variable].to_dict(),
            })
    for measure in direct_measures:
        if measure in selected.index:
            output_measures.append(measure)
            metadata_rows.append({
                "table_name": table, "table_column": measure,
                "row_identifier_column": "", "row_identifier_value": "",
                **selected.loc[measure].to_dict(),
            })
    if not output_measures:
        raise ValueError("No classified variable columns match the filters in homebuying scenarios.")
    start, end = _iso_date(start_date), _iso_date(end_date)
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before end_date.")
    for operator, bound in ((">=", start), ("<=", end)):
        if bound is not None:
            clauses.append(f"CAST(date AS DATE) {operator} ?")
            parameters.append(bound)
    # Other native fields identify the scenario and its assumptions; every
    # monetary observation belongs to one of the five measure roles above.
    measures = set(dynamic_measures) | set(direct_measures)
    output_columns = [column for column in columns if column not in measures or column in output_measures]
    sql = f"SELECT {', '.join(_quote(column) for column in output_columns)} FROM {table} q"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    order_columns = [column for column in ("geo_id", "date", "scenario_name") if column in columns]
    sql += " ORDER BY " + ", ".join(_quote(column) for column in order_columns)
    metadata = pd.DataFrame(metadata_rows)
    metadata.attrs["unclassified_columns"] = []
    return sql, parameters, metadata


def query_table(
    connection: duckdb.DuckDBPyConnection, **filters: object
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return selected native wide columns and an explicit column-to-variable dictionary."""

    sql, parameters, metadata = _table_query(connection, **filters)
    return connection.execute(sql, parameters).fetchdf(), metadata


def _output_path(path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Output path must end in .csv or .parquet.")
    if path.exists():
        raise FileExistsError(f"Output already exists; choose a new filename: {path}")
    return path


def export_catalog(frame: pd.DataFrame, output: str | Path) -> int:
    """Write a catalog without replacing any existing file."""

    path = _output_path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        with path.open("x", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
    else:
        with path.open("xb") as handle:
            frame.to_parquet(handle, index=False)
    return len(frame)


def export_observations(
    connection: duckdb.DuckDBPyConnection,
    output: str | Path,
    **filters: object,
) -> int:
    """Stream CSV or typed Parquet downloads without changing observations."""

    path = _output_path(output)
    sql, parameters = _observation_query(connection, **filters)
    return _stream_query(connection, path, sql, parameters)


def _stream_query(
    connection: duckdb.DuckDBPyConnection, path: Path, sql: str, parameters: list[object]
) -> int:
    cursor = connection.execute(sql, parameters)
    # DuckDB 1.5 renamed this method; keep the declared 1.4 minimum working.
    reader = (
        cursor.to_arrow_reader(batch_size=100_000) if hasattr(cursor, "to_arrow_reader")
        else cursor.fetch_record_batch(rows_per_batch=100_000)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    if path.suffix.lower() == ".parquet":
        with path.open("xb") as handle, pq.ParquetWriter(handle, reader.schema) as writer:
            for batch in reader:
                writer.write_batch(batch)
                row_count += batch.num_rows
    else:
        with path.open("x", encoding="utf-8", newline="") as handle:
            pd.DataFrame(columns=reader.schema.names).to_csv(handle, index=False)
            for batch in reader:
                batch.to_pandas().to_csv(handle, index=False, header=False)
                row_count += batch.num_rows
    return row_count


def export_table(
    connection: duckdb.DuckDBPyConnection, output: str | Path, **filters: object
) -> tuple[int, Path, list[str]]:
    """Download a wide table plus `<stem>.variables.csv` classification metadata."""

    path = _output_path(output)
    metadata_path = _output_path(path.with_name(path.stem + ".variables.csv"))
    sql, parameters, metadata = _table_query(connection, **filters)
    count = _stream_query(connection, path, sql, parameters)
    export_catalog(metadata, metadata_path)
    return count, metadata_path, metadata.attrs["unclassified_columns"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="DuckDB path; defaults to configured database.")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="Query variable identifiers and classifications.")
    observations = commands.add_parser(
        "observations", help="Download labeled observations from a supported long table."
    )
    table = commands.add_parser(
        "table", help="Download selected wide-panel columns plus a .variables.csv dictionary.",
        description="Select classified columns with native keys. For homebuying scenarios, "
        "selected price/down-payment/closing-cost variable groups intersect on scenario rows; "
        "the companion dictionary records each row-key mapping.",
    )
    datasets_command = commands.add_parser("datasets", help="List available dataset selectors and native table names.")
    datasets_command.add_argument("--output", type=Path)
    options = commands.add_parser("options", help="List exact filter values, including actual geography identifiers.")
    options.add_argument("--field", choices=OPTION_FIELDS, required=True)
    options.add_argument("--kind", choices=("catalog", "observations", "table"), default="catalog")
    options.add_argument("--dataset")
    options.add_argument("--geography", action="append", default=[])
    options.add_argument("--output", type=Path)
    for command in (catalog, observations, table):
        command.add_argument("--variable", action="append", default=[], help="Exact variable_id; repeatable.")
        command.add_argument(
            "--unit", "--unit-simplified", dest="units", action="append", default=[],
            help="Exact unit_simplified label; repeatable; values are not converted.",
        )
        command.add_argument("--temporal-type", action="append", default=[], help="Exact temporal_type; repeatable.")
        for option, help_text in (
            ("geography", "Canonical geography type; actual observation rows are filtered."),
            ("frequency", "Output frequency; main rows use final_frequency, A uses source_frequency, B/C use monthly."),
            ("native-frequency", "Main observations use actual row native_frequency; catalog/tables and A/B/C use inventory native labels."),
            ("domain", "Exact inventory Domain (data_domain)."),
            ("layer", "General, Version A, Version B, or Version C."),
        ):
            command.add_argument("--" + option, action="append", default=[], help=help_text + " Repeatable.")
        command.add_argument("--proxy", choices=("true", "false"), help="Explicit is_proxy flag; unknown values are excluded.")
        command.add_argument("--constructed", choices=("true", "false"), help="Explicit is_constructed flag; unknown values are excluded.")
        command.add_argument(
            "--output", type=Path, required=command is not catalog,
            help="New .csv or .parquet file. Existing files are never replaced.",
        )
    observations.add_argument("--dataset", choices=tuple(DATASETS), default="main")
    table.add_argument("--dataset", choices=tuple(TABLE_DATASETS), required=True)
    observations.add_argument("--geo-id", action="append", default=[], help="Exact native geo_id; repeatable.")
    table.add_argument("--geo-id", action="append", default=[], help="Canonical geo ID or unchanged native table key; repeatable.")
    for bound in ("start", "end"):
        observations.add_argument(
            f"--{bound}-date", help="Inclusive YYYY-MM-DD bound: main observation_date, A reference_date, B/C month."
        )
        table.add_argument(f"--{bound}-date", help="Inclusive YYYY-MM-DD bound on the native panel date.")
    args = parser.parse_args(argv)
    try:
        if args.command == "datasets":
            frame = list_datasets()
            if args.output is None:
                print(frame.to_csv(index=False), end="")
            else:
                count = export_catalog(frame, args.output)
                print(f"Exported {count:,} rows to {args.output}")
            return 0
        filters = {} if args.command == "options" else {
            "variable_ids": args.variable, "units": args.units, "temporal_types": args.temporal_type,
            "geographies": args.geography, "frequencies": args.frequency,
            "native_frequencies": args.native_frequency, "domains": args.domain, "layers": args.layer,
            "proxy": None if args.proxy is None else args.proxy == "true",
            "constructed": None if args.constructed is None else args.constructed == "true",
        }
        database = args.database
        if database is None:
            config = load_config()
            database = config.root / config.pipeline["database_path"]
        if not database.is_file():
            raise FileNotFoundError(f"Database does not exist: {database}")
        with duckdb.connect(str(database), read_only=True) as connection:
            if args.command in {"catalog", "options"}:
                frame = query_catalog(connection, **filters) if args.command == "catalog" else list_options(
                    connection, args.field, kind=args.kind, dataset=args.dataset, geographies=args.geography
                )
                if args.output is None:
                    print(frame.to_csv(index=False), end="")
                    return 0
                count = export_catalog(frame, args.output)
            elif args.command == "observations":
                count = export_observations(
                    connection, args.output, dataset=args.dataset, geo_ids=args.geo_id,
                    start_date=args.start_date, end_date=args.end_date, **filters,
                )
            else:
                count, metadata_path, excluded = export_table(
                    connection, args.output, dataset=args.dataset, geo_ids=args.geo_id,
                    start_date=args.start_date, end_date=args.end_date, **filters,
                )
                print(f"Column classifications: {metadata_path}")
                if excluded:
                    print(
                        "Unclassified non-context columns omitted: " + ", ".join(excluded), file=sys.stderr
                    )
        print(f"Exported {count:,} rows to {args.output}")
        return 0
    except (ValueError, OSError, duckdb.Error) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
