# Python source

This package implements acquisition, normalization, analytical construction,
database materialization, validation, and user-facing queries. Run the root
entry points from the project directory; these modules are not standalone datasets.

[pipeline.py](pipeline.py) dispatches the general pipeline stages through
[`run_pipeline.py`](../run_pipeline.py). [system_refresh.py](system_refresh.py)
coordinates strict source refresh and eligible monthly finance builds through
[`run_system_refresh.py`](../run_system_refresh.py), preserving frozen Version A.

| Module | Responsibility |
|---|---|
| [ingestion](ingestion/README.md) | Official source discovery and immutable acquisition |
| [cleaning](cleaning/README.md) | Source-specific parsers and common observation contract |
| [geography](geography/README.md) | Geographic identifiers, references, and crosswalk metadata |
| [features](features/README.md) | Derived formulas, scenarios, and fixed model parameters |
| [harmonization](harmonization/README.md) | Long observations and analytical panels |
| [benchmark](benchmark/README.md) | Native-frequency Version A finance benchmarks |
| [housing_finance_monthly](housing_finance_monthly/README.md) | Monthly finance Versions B and C |
| [database](database/README.md) | DuckDB tables, metadata, and views |
| [reporting](reporting/README.md) | Catalogs, inventory, browser, and filtered exports |
| [validation](validation/README.md) | Structural checks, arithmetic checks, and review warnings |
| [common](common/README.md) | Configuration, files, provenance helpers, and variable labels |

Inputs come from `config/`, archived provider files, and intermediate tables.
Generated observations and databases remain local; source code does not include
a ready-to-query database. Never edit raw vintages, silently fill missing data,
or treat postal ZIP codes and Census ZCTAs as interchangeable.

See the [project README](../README.md), [build and refresh guide](../docs/database_build_and_refresh.md),
and [data inventory](../docs/data_inventory_and_limitations.md).
