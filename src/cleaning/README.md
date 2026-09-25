# Source cleaning

These parsers convert archived provider files into explicit, provenance-bearing
observations without modifying raw files. The general pipeline calls
`clean_sources()` in [runner.py](runner.py) during its `clean` stage.

- [common.py](common.py): shared observation columns and normalization helpers.
- [runner.py](runner.py): source dispatch, accepted vintage selection, and
  transformation logging.
- [acs.py](acs.py), [bls.py](bls.py), [zillow.py](zillow.py),
  [redfin.py](redfin.py), and [redfin_zip.py](redfin_zip.py): major source parsers.
- Other named files implement their own provider contracts, including AHS,
  SIPP, permits, migration, insurance, macro series, and HUD USPS.

Inputs are immutable archives plus source/configuration metadata. Most outputs
are source-specific Parquet files in `data/clean/`; licensed HUD USPS derivatives
remain under `data/restricted/hud_usps/`. The shared contract retains dates,
geography, units, raw filename/checksum, retrieval/vintage information, and flags.

Frequency conversion is source-specific. Redfin ZIP values remain overlapping
rolling-three-month observations; their national CSV is raw-only. Active rent
ingestion uses only smoothed, seasonally adjusted ZORI. Schema drift and missing
required provenance must fail rather than manufacture successful ingestion.

See [source definitions](../../docs/source_definitions_and_methods.md) and
[build and refresh procedures](../../docs/database_build_and_refresh.md).
Return to the [source map](../README.md) or [project README](../../README.md).
