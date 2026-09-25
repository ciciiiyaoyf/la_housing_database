# Version A benchmark workspace

Local source inputs and intermediate artifacts used by the separate Version A
benchmark workflow. The benchmark preserves native source periods and supports
monthly Versions B/C without turning their modeled outputs into observed data.

[clean/](clean/README.md) contains normalized benchmark inputs. Acquisition lineage
is recorded in the Version A raw manifest under metadata. The completed benchmark
is under `data/final/` and governed by the freeze manifest. Existing source archives
and frozen artifacts must be preserved; use `run_version_a.py` only within the
benchmark's documented acquisition/build/freeze workflow.

[Parent folder](../README.md) · [Start here](../../README.md) · [Build and refresh guide](../../docs/database_build_and_refresh.md)
