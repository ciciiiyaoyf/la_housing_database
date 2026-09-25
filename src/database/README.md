# DuckDB materialization

This module assembles the local DuckDB database from accepted Parquet outputs,
configuration, and metadata. It does not fetch new provider observations.

- [build.py](build.py): `build_database()` creates/refreshes the configured
  database; `sync_source_metadata()` updates source registries after acquisition.
- [housing_finance.py](housing_finance.py): registers completed Version B/C
  observation and lineage files and their convenience views.
- [variable_metadata.py](variable_metadata.py): registers labeled observation
  views using the current variable dictionary.

The general pipeline invokes this module at `build-database`. Inputs include
`data/clean/`, `data/final/`, metadata CSVs, and [SQL definitions](../../sql/).
The configured output is normally `database/housing.duckdb`, containing clean,
analytical, metadata, provenance-registry, and quality relations.

Only valid checksum-gated B/C observation/lineage pairs are registered.
Version C preserves information-cutoff vintages; a latest-vintage view is an
explicit view, not a rewrite of history. Restricted HUD USPS content can exist
in the local database and must not enter ordinary public exports.

See [build and refresh procedures](../../docs/database_build_and_refresh.md),
[finance versions](../../docs/housing_finance_versions.md), and
[query commands](../../docs/query_and_export_commands.md).
Return to the [source map](../README.md) or [project README](../../README.md).
