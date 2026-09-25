# Generated exports and quality reports

Products written by the pipeline and query tools for data delivery or verification.
They are generated locally and mostly excluded from Git.

| Folder | Purpose |
|---|---|
| [exports/](exports/README.md) | Data CSVs, Parquet query exports, and matching definitions. |
| [coverage/](coverage/README.md) | Date, geography, source, missingness, and vintage coverage. |
| [qa/](qa/README.md) | Structural checks, review warnings, and completeness evidence. |

These files can be consumed by tests, analysts, or reproducibility checks. Do not
remove them simply because they are generated. Published reference workbooks live
in the separate `outputs/reference_workbooks/` folder. Local previews and temporary
review folders, when present, are not additional pipeline modules.

[Parent folder](../README.md) · [Start here](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
