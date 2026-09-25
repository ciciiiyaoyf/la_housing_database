# Analytical data products

Canonical local Parquet facts, geography/frequency-specific panels, modeled
scenarios, and finance products. Database registration and CSV export read these
files; they are not optional report attachments.

Version A is checksum-frozen. Version B has paired observations/lineage and a
completion record. Version C uses append-only information-cutoff partitions;
existing vintages must not be overwritten with changed results. Follow the
finance and maintenance guides before rebuilding. Local files are not included
in Git, and a missing product is not evidence of a zero-valued series.

[Parent folder](../README.md) · [Start here](../../README.md) · [Build and refresh guide](../../docs/database_build_and_refresh.md)
