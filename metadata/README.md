# Metadata and audit evidence

Describes the data and preserves the evidence needed to interpret or reproduce it.
Some files are generated inventories; others are required model parameters or
provenance records. Do not treat this folder as disposable progress reports.

| Start with | Question answered |
|---|---|
| [data_dictionary.csv](data_dictionary.csv) | What does each variable mean? |
| [system_variable_inventory.csv](system_variable_inventory.csv) | Which variables are materialized? |
| [analytical_dataset_inventory.csv](analytical_dataset_inventory.csv) | What tables, keys, and coverage exist? |
| [system_source_inventory.csv](system_source_inventory.csv) | Which providers and acquisition contracts supply them? |
| [source_coverage.csv](source_coverage.csv) | What periods have non-null data? |
| [data_gap_analysis.csv](data_gap_analysis.csv), [paused_variables_decision_register.csv](paused_variables_decision_register.csv) | What is absent, partial, or intentionally deferred? |
| [transformation_log.csv](transformation_log.csv) | Which transformations produced outputs? |

Frozen reference parameters, regime-model JSON files, Version A freeze checksums,
B/C build manifests, and source lineage support computation or reproducibility.
Generated CSV/XLSX inventories describe their recorded build, not a live provider
connection. Local raw manifests and some run logs are excluded from Git.
`history/` holds superseded evidence, including the
[August 3 HUD-USPS build summary](history/hud_usps_official_rebuild_summary_20260803.csv).
These historical values must not be rewritten to describe a later build.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
