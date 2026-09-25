# Output module

This README belongs to `output/` and describes the whole module.

**On GitHub and in a fresh clone**, this module contains only:

```text
output/
  README.md
  exports/
    variable_dictionary.csv
```

The [variable dictionary](exports/variable_dictionary.csv) contains definitions,
not housing observations. Other exports and reports are generated locally and
excluded from Git, so their folders do not appear on GitHub.

| Local folder | Purpose and when it appears |
|---|---|
| `exports/` | Holds the published dictionary; query and pipeline exports add data CSVs, Parquet files, and matching definitions here. |
| `coverage/` | Created by pipeline checks for date, geography, source, missingness, and vintage coverage. |
| `qa/` | Created by validation for structural checks, review warnings, and completeness evidence. |
| `archive/legacy_reports/` | Preserved only in the existing maintainer workspace: historical workbooks, previews, and supporting files retired from `outputs/`. A fresh clone does not recreate this archive. Previously published workbooks remain in Git history. |

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
