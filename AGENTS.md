# Instructions for coding agents

- Raw files are immutable. Never edit, replace, or delete an archived raw vintage.
- Never fabricate observations, endpoints, source metadata, or successful ingestion.
- Use only official public downloads, documented APIs, and provider-published files.
- Preserve source vintage, retrieval timestamp, checksum, source filename, and geography.
- Keep postal ZIP codes distinct from Census ZCTAs. Preserve leading zeros in all identifiers.
- Do not interpolate housing data or fill ZIP observations with county observations silently.
- Treat Redfin ZIP Key Metrics as overlapping rolling-three-month observations. Keep the national CSV raw-only, filter with the versioned LA County ZIP reference, and never relabel active listings as inventory.
- The sole active rent index is Zillow ZORI smoothed and seasonally adjusted, all homes plus multifamily. Keep archived NSA vintages out of active ingestion, clean tables, facts, and panels.
- Every transformation must be represented in code, the variable dictionary, and the transformation log.
- Source-specific frequency conversion rules must be explicit; never apply one aggregation rule to all variables.
- Structural QA failures must stop validation. Plausible economic outliers should be warnings for review.
- Run `pytest` and `python run_pipeline.py --stage validate` before declaring work complete.
- Update `README.md`, configuration, tests, and the data dictionary whenever schemas or methods change.
- Keep the project Python 3.11+ compatible and avoid unnecessary frameworks.
