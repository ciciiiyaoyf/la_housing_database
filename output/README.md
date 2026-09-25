# Exports, reports, and archives

Products written by the pipeline and query tools for data delivery or verification.
They are generated locally and mostly excluded from Git.

| Folder | Purpose |
|---|---|
| `exports/` | Data CSVs, Parquet query exports, and matching definitions. The [variable dictionary](exports/variable_dictionary.csv) is published. |
| `coverage/` | Date, geography, source, missingness, and vintage coverage for the recorded build. |
| `qa/` | Structural checks, review warnings, and completeness evidence. |
| `archive/legacy_reports/` | Local historical workbooks, previews, and supporting files retired from the former `outputs/` folder. Excluded from Git. |

These files can be consumed by tests, analysts, or reproducibility checks. Do not
remove them simply because they are generated. Use this single `output/` location
for generated exports, checks, and reports. Archived reports are not pipeline inputs
or the current data inventory; use [metadata](../metadata/README.md) and the
[data guide](../docs/data_inventory_and_limitations.md).

Wide query exports include a `<name>.variables.csv` mapping columns and scenario
rows to variable IDs, units, and temporal types. Keep it with the data. Browser
downloads use the browser's selected Downloads folder. Licensed HUD-USPS detail
remains excluded from ordinary exports. QA reports record a particular run;
structural failures stop validation, while review warnings require interpretation.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
