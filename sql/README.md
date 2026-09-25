# Saved queries, SQL definitions, and checks

Small SQL files supporting or illustrating the DuckDB database contract.
For the primary analysis workflow, start with the
[DuckDB SQL guide](../docs/duckdb_sql_guide.md). These schema and view definitions
are supporting files; opening an existing snapshot does not require running them.

| File | Role |
|---|---|
| [model_input.sql](model_input.sql) | Read-only LA County rent selection used by `scripts/load_model_data.py` as the SQL-to-modeling example. |
| [create_schema.sql](create_schema.sql) | Creates the five schemas; used by database construction and metadata synchronization. |
| [create_county_panel.sql](create_county_panel.sql) | Optional county modeling view over the already-built county table. |
| [create_zip_panel.sql](create_zip_panel.sql) | Optional ZIP modeling view over the already-built ZIP table. |
| [quality_checks.sql](quality_checks.sql) | Manual duplicate and missing-key queries corresponding to structural QA. |

The Python pipeline builds the analytical tables. The optional view files are not
a replacement for acquisition, harmonization, or validation and are not run by the
normal build. Keep them as usable SQL definitions.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
