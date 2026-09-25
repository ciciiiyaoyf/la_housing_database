# Structural and analytical validation

This module checks materialized data and metadata, records diagnostics, and
stops validation on structural errors. It does not repair missing observations.

- [qa.py](qa.py): `run_qa()` coordinates the general `validate` stage, including
  identifiers, schemas, lineage, frequencies, units, missingness, and derived formulas.
- [macro_financial.py](macro_financial.py): independent macro/rate contracts,
  arithmetic checks, and geography-preserving monthly-panel checks.

Inputs include configuration, final/clean tables, source metadata, and the
local DuckDB database. Outputs include `output/qa/qa_summary.csv`,
`output/qa/qa_failures.csv`, coverage reports, `metadata/source_coverage.csv`,
and DuckDB `quality.qa_summary` / `quality.qa_failures` tables.

Structural inconsistencies fail the build. Plausible economic outliers,
stale observations, and other review conditions remain warnings where specified;
they are not silently deleted, smoothed, or filled. A passing validation run
does not claim that unavailable data or unresolved source-review holds vanished.

Version A and Version B/C have additional dedicated checks in
[benchmark/qa.py](../benchmark/qa.py) and
[housing_finance_monthly/qa.py](../housing_finance_monthly/qa.py).
See [validation procedures](../../docs/database_build_and_refresh.md) and
[current limitations](../../docs/data_inventory_and_limitations.md).
Return to the [source map](../README.md) or [project README](../../README.md).
