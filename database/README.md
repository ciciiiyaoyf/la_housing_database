# Local DuckDB query database

`housing.duckdb` is the integrated local database for direct DuckDB SQL analysis.
It also supports the optional Data Browser and query/export tools. It exposes
metadata, raw-manifest, clean, analytics, and quality schemas. It is a static
snapshot, not a live connection to providers.

The graphical DuckDB workflow can create `sql_workspace.duckdb` in this folder
for notebook storage while attaching `housing.duckdb` read-only. That workspace
file is also local and excluded from Git; it contains UI work, not a second copy
of the housing observations. See the SQL guide's graphical-interface section.

The file is excluded from Git. A new user must obtain a compatible permitted
snapshot or build from the required sources; see the
[database setup](../README.md#3-get-a-local-database). Start querying with the
[DuckDB SQL guide](../docs/duckdb_sql_guide.md), or use the optional
[CSV browser](../docs/query_and_export_commands.md#browser-downloads).
Close database readers before rebuilding. Use the ordinary export tools for shareable CSVs:
the full research database can contain licensed detail and must not be assumed
safe for redistribution. Preserve an existing database before intentionally
switching to another snapshot.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
