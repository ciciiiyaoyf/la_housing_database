# Catalogs, queries, browser, and exports

This module makes the existing database and its metadata accessible to users.
Query/export functions read accepted data; they do not retrieve new releases.

- [query.py](query.py): shared dataset discovery, metadata/observation filters,
  native table selection, and CSV/Parquet downloads; entry point
  [`run_query.py`](../../run_query.py).
- [data_browser.py](data_browser.py): local read-only HTTP interface using the
  same query functions and [browser page](../../scripts/data_browser.html);
  entry point [`run_data_browser.py`](../../run_data_browser.py).
- [catalog.py](catalog.py): source catalogs, requirement audits, and workbooks.
- [system_inventory.py](system_inventory.py): source, variable, analytical,
  estimate, and storage inventories.
- [export.py](export.py): standard public CSV outputs.
- [variable_metadata.py](variable_metadata.py): metadata-only registry refresh.

Inputs are the local DuckDB database, accepted tables, configuration, and metadata.
Outputs include catalogs/inventories in `metadata/`, standard `output/exports/`
files, or user-selected export paths. A fresh clone requires a database build
or a compatible public snapshot before the browser can query observations.

Keep native values, dates, geography, and provenance. Label enrichment does not
rescale values. Licensed HUD USPS observations are rejected by ordinary exports;
variable definitions and scenario mappings accompany selected table measures.

See the [browser walkthrough](../../README.md) and
[query/export reference](../../docs/query_and_export_commands.md).
Return to the [source map](../README.md).
