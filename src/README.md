# Python source

This package acquires official data, normalizes observations, builds analytical
tables, materializes DuckDB, validates results, and serves queries. Run the root
entry points from the project directory. A fresh clone contains code and metadata,
not a ready-to-query database.

## Entry points and data flow

- [run_pipeline.py](../run_pipeline.py) calls [pipeline.py](pipeline.py) for
  discovery, download, clean, harmonize, database, validation, catalog, and export stages.
- [run_system_refresh.py](../run_system_refresh.py) calls [system_refresh.py](system_refresh.py)
  for strict source refresh and eligible monthly finance builds, preserving frozen Version A.
- [run_version_a.py](../run_version_a.py), [run_version_b.py](../run_version_b.py), and
  [run_version_c.py](../run_version_c.py) expose the separate finance pipelines.
- [run_query.py](../run_query.py) and [run_data_browser.py](../run_data_browser.py)
  query the existing database; they do not fetch new provider releases.

The general path is official files → immutable `data/raw/` → `data/clean/`
observations → `data/final/` tables → local DuckDB → filtered exports. Configuration
defines contracts; `metadata/` records provenance, variables, models, and transformations.

## Module map

| Module | Main files and responsibility | Inputs and outputs |
|---|---|---|
| [ingestion](ingestion/) | [download.py](ingestion/download.py), [refresh.py](ingestion/refresh.py): official acquisition, immutable archives, strict freshness checks; provider-specific adapters handle special sources. | Configured endpoints or official local files → archives, manifests, refresh reports. |
| [cleaning](cleaning/) | [runner.py](cleaning/runner.py) dispatches source parsers; [common.py](cleaning/common.py) defines the observation contract. | Archived vintages + provenance → source-specific clean Parquet and transformation records. |
| [geography](geography/) | [build.py](geography/build.py), [zcta_reference.py](geography/zcta_reference.py), [comparable_counties.py](geography/comparable_counties.py): identifiers and explicit geographic universes. | Configured references + observed keys → geography/crosswalk tables and validated scopes. |
| [features](features/) | [derived.py](features/derived.py), [comparable_counties.py](features/comparable_counties.py), [market_regimes.py](features/market_regimes.py), [macro_financial.py](features/macro_financial.py): formulas, scenarios, fixed models, sparse macro controls. | Clean frames + explicit parameters → derived frames and versioned model/reference parameters. |
| [harmonization](harmonization/) | [build.py](harmonization/build.py): `build_analytical_outputs()` assembles the fact, panels, controls, regimes, and scenarios. | Clean observations + feature/geography contracts → final Parquet and transformation log. |
| [benchmark](benchmark/) | [pipeline.py](benchmark/pipeline.py), [build.py](benchmark/build.py), [finance.py](benchmark/finance.py): native-frequency Version A; source adapters, schema, and QA remain separate. | Official finance inputs → benchmark clean files, benchmark observations, manifests, and QA. |
| [housing_finance_monthly](housing_finance_monthly/) | [pipeline.py](housing_finance_monthly/pipeline.py), [retrospective.py](housing_finance_monthly/retrospective.py), [realtime.py](housing_finance_monthly/realtime.py): monthly Versions B/C with source, schema, QA, and metadata helpers. | Frozen A anchors + eligible inputs → paired observations/lineage, manifests, completion markers. |
| [database](database/) | [build.py](database/build.py), [housing_finance.py](database/housing_finance.py), [variable_metadata.py](database/variable_metadata.py): DuckDB materialization and labeled views. | Accepted Parquet + metadata + SQL definitions → clean, analytical, metadata, provenance, and quality relations. |
| [reporting](reporting/) | [query.py](reporting/query.py), [data_browser.py](reporting/data_browser.py), [export.py](reporting/export.py), [catalog.py](reporting/catalog.py), [system_inventory.py](reporting/system_inventory.py): filtering, downloads, catalogs, and inventories. | Local database/configuration → metadata workbooks/CSVs, user exports, and browser responses. |
| [validation](validation/) | [qa.py](validation/qa.py), [macro_financial.py](validation/macro_financial.py): structural, arithmetic, geography, and coverage checks; finance modules add dedicated QA. | Materialized data + contracts → local QA/coverage reports and DuckDB quality tables. |
| [common](common/) | [config.py](common/config.py), [io.py](common/io.py), [logging.py](common/logging.py), [variable_metadata.py](common/variable_metadata.py): shared configuration, file/provenance helpers, logging, and labels. | Configuration/manifests/frames → validated objects, registry enrichment, and explicit metadata updates. |

## Rules that cross module boundaries

- Raw files are immutable; retain source filenames, retrieval times, vintages, checksums, and geography. Credentials stay outside recorded configuration and URLs.
- Preserve missing values and exact calendar lags. ZIPs, ZCTAs, county portions, and broader geographies are distinct; never substitute or broadcast observations silently.
- Frequency conversion is source-specific. Redfin ZIP windows overlap; annual controls stay labeled; GDP remains quarterly; active rent ingestion uses smoothed, seasonally adjusted ZORI only.
- Ordinary refresh does not refit frozen models or rewrite frozen Version A. Version B publishes a complete retrospective pair atomically; Version C appends cutoff vintages excluding future information.
- Missing compatible finance inputs leave canonical targets null. Scenarios stay labeled, and panel features need not also be fact-table rows.
- Unit/temporal enrichment does not rescale values or select aggregation rules. Structural failures stop validation; plausible economic outliers remain review warnings.
- Licensed HUD USPS archives/derivatives stay access-controlled under `data/restricted/`. The local database may contain restricted records; ordinary public exports reject them.

See the [project README](../README.md), [build and refresh guide](../docs/database_build_and_refresh.md),
[query/export reference](../docs/query_and_export_commands.md), [data inventory](../docs/data_inventory_and_limitations.md),
[source methods](../docs/source_definitions_and_methods.md), [finance versions](../docs/housing_finance_versions.md),
and [county/ZIP indicator methods](../docs/county_and_zip_housing_indicators.md).
