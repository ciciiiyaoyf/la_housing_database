# Version A benchmarks

This module constructs native-frequency housing-finance benchmarks from
official ACS, PUMS, HMDA, NMDB, mortgage-rate, and CPI inputs. It is separate
from the modeled monthly Version B/C layers.

- [pipeline.py](pipeline.py): source acquisition, cleaning, building, and QA;
  exposed through [`run_version_a.py`](../../run_version_a.py).
- [acquisition.py](acquisition.py): immutable files, vintage/release selection,
  checksums, and source manifests.
- [acs.py](acs.py), [pums.py](pums.py), [hmda.py](hmda.py),
  [nmdb.py](nmdb.py), and [cpi.py](cpi.py): source-specific contracts.
- [finance.py](finance.py) and [build.py](build.py): finance calculations and assembly.
- [schema.py](schema.py) and [qa.py](qa.py): observation schema and benchmark checks.

Inputs are governed by `config/version_a_benchmark.yaml` and
`config/version_a_variables.yaml`. Local outputs include `data/benchmark/clean/`,
`data/final/housing_finance_benchmark.parquet`, and Version A metadata/QA records.

Accepted Version A files are checksum-frozen. Routine refresh must not rebuild
or replace them; Versions B/C consume them as read-only anchors. Missing
compatible calibration inputs leave canonical targets null. Scenario outputs
must not be relabeled as calibrated benchmarks or given a different universe.

See [housing-finance definitions](../../docs/housing_finance_versions.md) and
[build procedures](../../docs/database_build_and_refresh.md).
Return to the [source map](../README.md) or [project README](../../README.md).
