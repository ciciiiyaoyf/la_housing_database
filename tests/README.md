# Verification tests

Checks acquisition, source cleaning, identifiers, formulas, missingness, geography,
frequency, provenance, finance vintages, query/export behavior, and structural QA.
Combined nine-construction checks also cover source-row reconciliation, retained
nulls and diagnostics, row-type keys, variable applicability, and filtered exports
without geographic copying or quarterly-to-monthly expansion.
Browser category checks cover subject labels, finance/scenario/regime families,
and unchanged variable choices and metadata when the picker is grouped.
Snapshot distribution checks cover manifest validation, private-release selection,
checksum and schema verification, code-revision compatibility, preservation of
unmanaged or modified local databases, active-file/WAL refusal, and a single
backup during protected same-path installation. Release transport tests use
controlled fixtures rather than uploading production data.

Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
```

Before a database release, these checks run before the final metadata review and
commit because validation regenerates tracked `metadata/source_coverage.csv`.
`run_snapshot.py publish` reruns both checks and refuses publication if the
checkout is dirty or the checks change tracked files. The publisher reviews and
commits intended metadata updates rather than reverting them to bypass that gate.

Some tests use temporary fixtures; integration tests require the corresponding
local database and data products. A clone without observations is not a fully
provisioned integration-test workspace. Tests must not substitute invented data
for missing production observations. Keep failures visible and investigate them.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
