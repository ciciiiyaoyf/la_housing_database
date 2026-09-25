# Shared configuration and file helpers

This module supplies infrastructure used across pipeline stages; it contains
no provider downloader or analytical model.

- [config.py](config.py): validated YAML loading and the `ProjectConfig` /
  `SourceConfig` contracts.
- [io.py](io.py): directory setup, CSV metadata updates, SHA-256 checksums,
  and archived-file/vintage lookup.
- [logging.py](logging.py): consistent console and local file logging.
- [variable_metadata.py](variable_metadata.py): explicit registry loading,
  label enrichment, coverage checks, and source-unit compatibility checks.

Inputs include the project `config/` files, raw-file manifests, and supplied
data frames. Helpers return validated objects/frames or update the specific
local metadata/log files requested by their caller.

`config/variable_metadata.csv` is the per-variable unit/temporal registry.
Its labels never rescale observations or choose frequency-conversion rules.
Original units and monetary bases remain distinct; missing or contradictory
required metadata is a structural failure. Archive helpers must preserve
immutable raw files and their provenance.

See [query and export definitions](../../docs/query_and_export_commands.md) and
[build procedures](../../docs/database_build_and_refresh.md).
Return to the [source map](../README.md) or [project README](../../README.md).
