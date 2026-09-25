# Database build and refresh

For installation and everyday CSV downloads, follow the [README](../README.md).
This guide is for the person maintaining the source archives and local database.
Commands run from the project folder. The examples below use `python` as shorthand
for the project's interpreter; in Windows PowerShell you can use
`.\.venv\Scripts\python.exe` in its place without activating the environment.
Run one command at a time and stop if it reports an error.

## What a rebuild requires

A Git clone includes configuration, code, metadata, and reference documents.
It excludes observation files, raw archives, `.venv`, and `database/housing.duckdb`.
The published inventory describes the maintained research snapshot, not data
automatically installed by cloning.

To reproduce an existing snapshot, obtain its permitted raw archives and matching
manifests from the maintainer, preserving relative paths and checksums. A query-only
database snapshot is sufficient for browsing, but is not sufficient for rebuilding.
Do not copy licensed HUD-USPS detail into an ordinary shared snapshot. Its access
and export rules are in [source methodology](source_definitions_and_methods.md#hud-usps-vacancy).

A new online build depends on current provider availability, manual files, and
source-specific access. See the [source inventory](../metadata/system_source_inventory.csv)
and [data limitations](data_inventory_and_limitations.md). It need not reproduce the older snapshot's
values or coverage because providers revise releases. Never treat skipped sources
or a successful download as proof of a complete database.

Ordinary bulk acquisition and cleaning can log some source failures and continue;
read their source-level reports as well as the process exit code. Sparse one-source
builds are not a supported starter workflow: downstream builders expect inputs
across panels, and structural QA requires the complete macro contract. Use a
compatible permitted snapshot for query-only setup or provision the full inputs
before rebuilding. Strict refresh adds stronger failure gates but retains the
documented manual-source and Redfin review requirements.

## Pipeline stages

```powershell
python run_pipeline.py --help
```

| Stage | Purpose |
|---|---|
| `discover` | Check configured source endpoints, unless offline; record discovery metadata. |
| `download` | Archive provider payloads and their provenance. |
| `clean` | Normalize selected archived vintages using source-specific rules. |
| `harmonize` | Build facts, derived measures, and geography/frequency-specific panels. |
| `build-database` | Register local metadata, source tables, and analytical products in DuckDB. |
| `validate` | Run structural checks and write QA/coverage reports. |
| `catalog` | Generate source, variable, gap, and storage inventories and workbooks. |
| `export` | Write permitted CSV exports from final analytical files. |
| `variable-metadata` | Refresh unit/temporal labels and labeled views without changing observations. |
| `all` | Run discovery through export in the order above, excluding `variable-metadata`. |
| `refresh` | Verify newest eligible releases and strictly clean sources before rebuilding. |

`--source SOURCE_ID` filters source-aware discovery, download, and clean stages.
It does **not** isolate harmonization, database building, validation, or exports.
`--start-date` and `--end-date` constrain supported downloads or harmonized dates.
`--vintage` selects archived retrieval dates where implemented; it is not allowed
with strict refresh. `--offline` reuses local raw archives without network retrieval.
`--force-download` requests a new payload instead of reusing the day's archive.
`--local-file` imports an official manually acquired file. For troubleshooting,
use `--log-level DEBUG`. Run records go to `metadata/pipeline_run_log.csv`.

## Rebuild from existing archives

Close the data browser and any application holding the database open first.
With the required archives and manifests already present:

```powershell
python run_pipeline.py --stage all --offline
```

This is not an initial-download command. Review QA and coverage after the run;
offline mode does not supply missing sources or complete the finance layers.

To rebuild after adding a source contract, register the catalog before validating
its new metadata, then export only after validation succeeds:

```powershell
python run_pipeline.py --stage harmonize
python run_pipeline.py --stage build-database
python run_pipeline.py --stage catalog
python run_pipeline.py --stage variable-metadata
python run_pipeline.py --stage validate
python run_pipeline.py --stage export
```

## Retrieve source updates

The database is a static snapshot. Opening it does not contact providers.
The strict general-source workflow is:

```powershell
python run_pipeline.py --stage refresh
```

Automatic acquisition and strict cleaning failures stop the workflow. Manual or
licensed sources appear as intervention items in `metadata/source_refresh_status.csv`;
retaining checksummed existing archives does not make them newest releases. The
[Redfin compatibility hold](county_and_zip_housing_indicators.md#current-redfin-source-limitation)
needs a reviewed source/method decision, not repeated blind refresh attempts.

For a configured source, archive a fresh official release with its source ID:

```powershell
python run_pipeline.py --stage download --source bls_laus_la_county --force-download
python run_pipeline.py --stage download --source bls_ces_la_metro --force-download
python run_pipeline.py --stage download --source bls_cpi_la_metro --force-download
python run_pipeline.py --stage download --source fhfa_hpi_state_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_metro_purchase_only --force-download
python run_pipeline.py --stage download --source fhfa_hpi_county_annual --force-download
python run_pipeline.py --stage download --source bea_state_personal_income --force-download
python run_pipeline.py --stage download --source bea_county_personal_income --force-download
python run_pipeline.py --stage download --source census_hvs_state_rental_vacancy --force-download
```

These are alternative source examples; run the ones needed for your update.
Downloading alone does not update analytical values. Clean the changed sources
and rebuild the downstream stages, or run the offline rebuild once all required
archives are present. Every retrieval preserves endpoint/request metadata, original
filename, source vintage, exact retrieval time, size, and SHA-256 checksum.

For LA County LAUS, the official BLS Public Data API requests series
`LAUCN060370000000003` (unemployment rate), `...004` (unemployment), `...005`
(employment), and `...006` (labor force). HTTP/API errors, absent configured series,
invalid JSON, or an empty numeric response stop that download. Inspect the newest
manifest row rather than relying on a date copied into a document.

Manual Redfin ZIP import, after obtaining the official provider CSV:

```powershell
python run_pipeline.py --stage download --source redfin_zip_monthly --local-file "C:\path\to\official_redfin.csv"
python run_pipeline.py --stage all --offline
```

Replace the example path. The national file stays raw-only; cleaning filters to
the versioned LA County postal-ZIP reference. Windows overlap across three months.

## Finance layers and integrated refresh

Version A is frozen separately. Versions B and C require its exact benchmark,
matching freeze manifest, and eligible supporting archives. A fresh general build
alone does not create them. Follow the [finance guide](housing_finance_versions.md)
for preflight, build, validation, lineage, and cutoff rules.

On a fully provisioned research workspace, the integrated runner refreshes general
sources, rebuilds retrospective B, appends a C cutoff, then registers and validates:

```powershell
python run_system_refresh.py --information-cutoff YYYY-MM-DD
```

Replace `YYYY-MM-DD` with the intended information cutoff. The runner verifies,
but does not rebuild, frozen Version A. It does not install a recurring schedule.
The macro-only acquisition helper `scripts/refresh_macro_request.py` acquires and
strictly cleans nine macro sources; it does not publish a final database by itself.

## Files, provenance, and validation

| Location | Role |
|---|---|
| `data/raw/`, `data/benchmark/` | Immutable permitted provider payloads and benchmark inputs. |
| `data/restricted/hud_usps/` | Licensed archive and detailed derivatives; outside public exports and Git. |
| `data/archive/deprecated_zori_nsa/` | Historical NSA ZORI archive; excluded from active ingestion. |
| `data/interim/`, `data/clean/`, `data/final/` | Rebuildable intermediate, normalized, and analytical files. |
| `database/housing.duckdb` | Local query database with metadata, raw, clean, analytics, and quality schemas. |
| `metadata/raw_file_manifest.csv` | Local raw-file lineage, endpoints, requests, timestamps, and checksums. |
| `metadata/transformation_log.csv` | Execution record linking inputs and outputs. |
| `output/exports/` | Generated data CSVs and definitions. |
| `output/qa/`, `output/coverage/` | Structural failures, review warnings, missingness, vintages, and coverage. |

The `raw` schema stores manifest/provenance records, not copies of provider payloads.
The `clean` schema registers active sources; old files on disk do not necessarily
belong to active tables. Current generated inventories are linked from the
[data reference](data_inventory_and_limitations.md).

Before publishing an implementation or validated build:

```powershell
python -m pytest -q tests
python run_pipeline.py --stage validate
```

Integration tests require the corresponding local data products; a code-only clone
does not contain their fixtures. Structural failures stop validation. Plausible
economic outliers, freshness, and expected gaps remain review warnings. Check
`output/qa/qa_summary.csv`, `qa_failures.csv`, `raw_repository_completeness.csv`,
and the source/time/ZIP/missingness reports under `output/coverage/`.

## Change a source or method

1. Use an official download, documented API, or provider-published file.
2. Update source configuration, variable definitions, and explicit unit/temporal labels.
3. Preserve raw archives, original identifiers, vintage, retrieval time, and checksum.
4. Implement source-specific geography, frequency, missingness, and uncertainty rules.
5. Update the dictionary, transformation log, methodology, and relevant QA/tests.
6. Run the checks above. Never silently interpolate, fill another geography, or
   replace an unavailable canonical target with a differently defined proxy.

After editing `config/variable_metadata.csv`, use `--stage variable-metadata` to
refresh the dictionary, SQL labels, export dictionary, and transformation entry.
After `--stage catalog`, run that metadata stage again to enrich generated estimates.

## Historical records

Superseded narrative audits remain available through Git history rather than
parallel current guides. The [August 3 HUD-USPS summary](../metadata/history/hud_usps_official_rebuild_summary_20260803.csv)
describes an earlier build, not the active database. The
[quarantine manifest](../metadata/hud_usps_quarantine_manifest.csv) likewise records
superseded provenance; do not rewrite historical entries to describe current data.
The dated workbooks and previews from the former `outputs/` folder are preserved
locally under `output/archive/legacy_reports/`. Previously published workbooks also
remain in Git history. Their useful explanations are consolidated in the data and
finance guides; current CSV/JSON inventories take precedence for present availability.
Use `output/` for all new exports, QA, and reports.
