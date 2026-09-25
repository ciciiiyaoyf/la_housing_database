# Monthly housing finance: Versions B and C

This module builds modeled monthly finance layers from source-faithful,
read-only Version A anchors and eligible indicator/calibration inputs.

- [pipeline.py](pipeline.py): preflight, builds, QA, paired output publication,
  and freeze verification; root entry points are
  [`run_version_b.py`](../../run_version_b.py) and [`run_version_c.py`](../../run_version_c.py).
- [retrospective.py](retrospective.py): Version B monthly history using
  information available at rebuild time.
- [realtime.py](realtime.py): Version C availability/cutoff logic and construction.
- [sources.py](sources.py): source-specific indicator and NMDB input readers.
- [schema.py](schema.py) and [qa.py](qa.py): observation/lineage contracts and checks.
- [metadata.py](metadata.py): global dictionaries, catalogs, and executed transformations.

Inputs follow the separate Version B/C configuration files and frozen benchmark
Parquet. Local outputs include monthly observations, matching lineage, build
manifests, QA results, and checksum-gated completion markers in the configured paths.

Version B replaces a complete retrospective output pair atomically. Version C
is append-only by information cutoff and must exclude future information.
Both verify Version A's freeze before/after building. Missing compatible anchors
or calibration controls leave targets null; scenario substitutes stay labeled.

See [finance versions](../../docs/housing_finance_versions.md) and
[build and refresh procedures](../../docs/database_build_and_refresh.md).
Return to the [source map](../README.md) or [project README](../../README.md).
