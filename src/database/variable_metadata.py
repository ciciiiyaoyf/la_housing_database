"""Metadata-only SQL views; observation values and source artifacts stay intact."""

from __future__ import annotations

import duckdb


LABELED_TABLES = (
    "fact_housing_observation",
    "housing_finance_benchmark",
    "housing_finance_monthly_hist",
    "housing_finance_monthly_rt",
)
LABEL_COLUMNS = ("unit_simplified", "temporal_type", "temporal_type_note", "monetary_basis")


def register_variable_metadata_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Expose labels on existing long tables without rewriting any observations."""
    columns = {
        row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='metadata' AND table_name='variable_dictionary'"
        ).fetchall()
    }
    if not set(LABEL_COLUMNS).issubset(columns):
        return  # Minimal projects without the optional metadata contract.
    duplicates = connection.execute(
        "SELECT variable_id FROM metadata.variable_dictionary "
        "GROUP BY variable_id HAVING count(*) > 1"
    ).fetchall()
    if duplicates:
        raise ValueError("Variable metadata join requires unique variable IDs")
    for table in LABELED_TABLES:
        existing = {
            row[0] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='analytics' AND table_name=?", [table]
            ).fetchall()
        }
        if "variable_id" not in existing:
            continue
        labels = ", ".join(f"d.{column}" for column in LABEL_COLUMNS if column not in existing)
        select = "f.*" + (", " + labels if labels else "")
        connection.execute(
            f"CREATE OR REPLACE VIEW analytics.{table}_labeled AS "
            f"SELECT {select} FROM analytics.{table} f "
            "LEFT JOIN metadata.variable_dictionary d USING (variable_id)"
        )
