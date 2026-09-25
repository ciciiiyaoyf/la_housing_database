# Local DuckDB query database

`housing.duckdb` is the integrated local database used by the Data Browser and
query/export tools. It exposes metadata, raw-manifest, clean, analytics, and
quality schemas. It is a static snapshot, not a live connection to providers.

The file is excluded from Git. A new user must obtain a compatible permitted
snapshot or build from the required sources; see step 3 of the project README.
Close readers before rebuilding. Use the ordinary export tools for shareable CSVs:
the full research database can contain licensed detail and must not be assumed
safe for redistribution. Preserve an existing database before intentionally
switching to another snapshot.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
