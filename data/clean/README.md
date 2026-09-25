# Normalized source observations

Source-specific Parquet files with standardized identifiers, dates, values,
frequency, units, quality flags, and provenance. Source cleaners construct them
from selected immutable raw vintages; harmonization and database building consume them.

Only configured active sources belong to the active database. A superseded file
can remain on disk without being an active input. Preserve native geography,
source windows, nulls, and vintage fields. Use the pipeline to rebuild files;
these local observation products are excluded from Git.

[Parent folder](../README.md) · [Start here](../../README.md) · [Build and refresh guide](../../docs/database_build_and_refresh.md)
