# Verification tests

Checks acquisition, source cleaning, identifiers, formulas, missingness, geography,
frequency, provenance, finance vintages, query/export behavior, and structural QA.

Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
```

Some tests use temporary fixtures; integration tests require the corresponding
local database and data products. A clone without observations is not a fully
provisioned integration-test workspace. Tests must not substitute invented data
for missing production observations. Keep failures visible and investigate them.

[Parent folder](../README.md) · [Start here](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
