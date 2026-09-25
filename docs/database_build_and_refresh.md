# Database build and refresh

This guide is for the person maintaining the source archives and local database.
For first-time installation, complete the [README setup](../README.md#2-set-up-the-project).
For everyday CSV downloads, use the [Data Browser instructions](../README.md#4-open-the-data-browser).
The commands here rebuild or update local research products; they are not a
complete database installation recipe for a fresh clone.

## Open the working folder and run commands

1. Open **Windows PowerShell** from the Start menu. Type commands there, not in
   the Data Browser, Excel, or a Python window.
2. Open the project folder. If you cloned into Downloads as in the README, run:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
Get-Location
```

If your copy is elsewhere, replace the first line with `Set-Location -LiteralPath`
followed by your actual project path in quotes. `Get-Location` should display the
folder containing `run_pipeline.py`, `requirements.txt`, and `config`. Every
relative path in this guide starts at that folder.

3. Check that the project's environment is installed:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --help
```

You should see the available stages and options. If PowerShell cannot find this
interpreter or Python reports a missing package, return to the README setup
before continuing. All examples below call `.venv` directly; activation is not
required. On macOS/Linux, use `.venv/bin/python` in place of the Windows path.

4. Run **one command at a time**, without the surrounding code fences. Wait for
   the PowerShell prompt, such as `PS C:\…\la_housing_database>`, to return before
   typing the next command. The prompt itself is not a command. A build can take
   time and may be quiet while reading a large file.
5. Immediately after each Python command, check its exit code:

```powershell
$LASTEXITCODE
```

`0` means that command ended without a fatal error; any other number means stop
and investigate. It does **not** prove that all sources or expected rows are
present. Use the stage checks below as well. Pipeline runs append to
`metadata/pipeline_run_log.csv`; check the latest row's `requested_stage`,
`completed_at`, `status`, and `error`. Detailed pipeline messages are appended to
`output/pipeline.log`, so match their timestamps to this run. A setup failure
before a run starts may have only a terminal error and no new log row.

If the prompt is `>>>`, you are inside Python; type `exit()` to return to
PowerShell. If PowerShell shows `>>`, press **Ctrl+C** and re-enter the complete
command. Ctrl+C also interrupts a running build; an interrupted build is not a
successful result. Read the error and logs before retrying or starting another
stage. Do not run two builders at once.

## What a rebuild requires

A Git clone includes configuration, code, metadata, and reference documents.
It excludes observation files, raw archives, `.venv`, and `database/housing.duckdb`.
The published inventory describes the maintained research snapshot, not data
automatically installed by cloning.

To reproduce an existing snapshot, obtain its permitted raw archives and matching
manifests from the maintainer, preserving relative paths and checksums. A query-only
database snapshot is sufficient for browsing, but is not sufficient for rebuilding.
There is no ready-made database download supplied by this repository; see the
[README data requirement](../README.md#3-get-a-local-database).
Do not copy licensed HUD-USPS detail into an ordinary shared snapshot. Its access
and export rules are in [source methodology](source_definitions_and_methods.md#hud-usps-vacancy).

Never edit, overwrite, or delete an archived raw vintage to make a build pass.
Keep its source filename, geography, provider vintage, retrieval time, and checksum.
Use the source's importer for a newly obtained official release so it receives its
own archive and provenance record. Keep frozen finance inputs and their matching
manifests intact; the [finance guide](housing_finance_versions.md) explains which
products may be rebuilt.

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

The `--help` command above lists the choices. The stage names and options in the
tables below are reference information, not commands to paste on their own.

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
`SOURCE_ID` is a placeholder: use an exact configured ID, such as `bls_laus_la_county`.
It does **not** isolate harmonization, database building, validation, or exports.
`--start-date` and `--end-date` constrain supported downloads or harmonized dates.
`--vintage` selects archived retrieval dates where implemented; it is not allowed
with strict refresh. `--offline` reuses local raw archives without network retrieval.
`--force-download` requests a new payload instead of reusing the day's archive.
`--local-file` imports an official manually acquired file. For troubleshooting,
use `--log-level DEBUG`. These options must follow a complete command with a stage;
do not paste an option such as `--offline` by itself.

| Stage or group | Prerequisite and result to check before continuing |
|---|---|
| `discover` | Requires source configuration and online access unless offline. Review `metadata/source_discovery.csv` immediately after this stage; a reachable endpoint does not confirm a usable or current payload. The later catalog stage rewrites this report. |
| `download` | Requires provider access or the supported official local file. Confirm the selected source's new or reused archive and checksum in `metadata/raw_file_manifest.csv`; review terminal errors and any `metadata/manual_downloads.csv` entries, including their recorded dates. |
| `clean` | Requires matching archives/manifests and the source-specific parser. Check the selected source's observation count in the log, its cleaned output, and the current transformation record. A bulk run may continue after a source fails. |
| `harmonize` | Requires the complete set of cleaned inputs for the intended panels. Inspect regenerated products in `data/final/`; run validation to check their structure and coverage. |
| `build-database` | Requires the intended source, final, and metadata products. Look for the `Built` database message and table counts; confirm the database is readable after the command exits. This stage alone does not validate every product. |
| `validate` | Requires the local products being checked. Expect `QA completed: 0 errors`; review warnings and the QA/coverage files described below. |
| `catalog` | Requires the local products whose availability it reports. Inspect updated inventories and `output/qa/raw_repository_completeness.csv`; generating a catalog does not fill missing data. |
| `export` | Requires validated final products. Check the `Exported CSV outputs` counts and the expected files in `output/exports/`; absent inputs may be skipped, and an old CSV's presence is not proof it was regenerated. |
| `variable-metadata` | Requires complete, consistent variable definitions and the registry. Check `metadata/data_dictionary.csv`, `output/exports/variable_dictionary.csv`, and the new transformation-log entry. It updates SQL labels only when a database already exists. |

Open reports in File Explorer relative to the project folder. CSV reports can be
viewed in Excel, but close them without saving changes to identifiers or dates.
Close generated workbooks before regenerating them so Windows does not lock them.

## Rebuild from existing archives

First confirm the required archives, manifests, supporting inputs, and installed
environment are present. Stop the Data Browser with **Ctrl+C in its PowerShell
window**; closing its browser tab alone does not stop the server. Close any other
application holding the database open. In the project PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage all --offline
```

Wait for the prompt, check `$LASTEXITCODE`, then review the run log, QA, coverage,
and export counts. This is not an initial-download command: offline mode does not
supply missing sources or complete the finance layers. If a stage fails, later
stages in this run do not execute; existing downstream files may still be from
an earlier build.

The following is a separate workflow after adding a source contract and successfully
acquiring and cleaning its inputs. It starts from cleaned data, not raw files.
Register the catalog before validating its new metadata. Run each line separately,
checking its exit code and the relevant result above; export only after validation
succeeds:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage harmonize
.\.venv\Scripts\python.exe run_pipeline.py --stage build-database
.\.venv\Scripts\python.exe run_pipeline.py --stage catalog
.\.venv\Scripts\python.exe run_pipeline.py --stage variable-metadata
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
.\.venv\Scripts\python.exe run_pipeline.py --stage export
```

## Retrieve source updates

The database is a static snapshot. Opening it does not contact providers.
On an already provisioned workspace, stop database readers as above and confirm
provider access and the manual-source requirements. The strict general-source
workflow, run from the same project PowerShell window, is:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh
```

Automatic acquisition and strict cleaning failures stop the workflow. Manual or
licensed sources appear as intervention items in `metadata/source_refresh_status.csv`;
retaining checksummed existing archives does not make them newest releases.
After the prompt returns, check the exit code and run log, then inspect the current
refresh report's `refresh_status`, `is_blocking`, `action_required`, and
`clean_refresh_status` for every requested source. A retained archive or manual
intervention item needs review even when the process exits successfully. Check QA
and exports only after the downstream stages have completed. The
[Redfin compatibility hold](county_and_zip_housing_indicators.md#current-redfin-source-limitation)
needs a reviewed source/method decision, not repeated blind refresh attempts.

For a configured source, the following are **independent runnable examples** of
archiving a fresh official release. Choose only the source or sources needed for
your update; this block is not a required sequence or a complete database build:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bls_laus_la_county --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bls_ces_la_metro --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bls_cpi_la_metro --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source fhfa_hpi_state_purchase_only --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source fhfa_hpi_metro_purchase_only --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source fhfa_hpi_county_annual --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bea_state_personal_income --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bea_county_personal_income --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source census_hvs_state_rental_vacancy --force-download
```

Check the exit code and selected source's manifest row after each command.
Downloading alone does not update analytical values. For example, after a
successful LAUS download, clean it with:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage clean --source bls_laus_la_county
```

Check its cleaned observations and transformation record, then run the downstream
sequence from `harmonize` through `export` above. Alternatively, run the offline
rebuild once all required archives are present; that includes cleaning. Every
retrieval preserves endpoint/request metadata, original
filename, source vintage, exact retrieval time, size, and SHA-256 checksum.

For LA County LAUS, the official BLS Public Data API requests series
`LAUCN060370000000003` (unemployment rate), `...004` (unemployment), `...005`
(employment), and `...006` (labor force). HTTP/API errors, absent configured series,
invalid JSON, or an empty numeric response stop that download. Inspect the newest
manifest row rather than relying on a date copied into a document.

For manual Redfin ZIP import, first obtain the official provider CSV and review
the [ZIP source rules](county_and_zip_housing_indicators.md). This command is a
**template**: replace the quoted example path with the real CSV path before running:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source redfin_zip_monthly --local-file "C:\path\to\official_redfin.csv"
```

Confirm the import's exit code and new manifest record before using the offline
rebuild above, which still requires all other archives. The national file stays
raw-only; cleaning filters to the versioned LA County postal-ZIP reference.
Observation windows overlap across three months.

## Finance layers and integrated refresh

Version A is frozen separately. Versions B and C require its exact benchmark,
matching freeze manifest, and eligible supporting archives. A fresh general build
alone does not create them. Follow the [finance guide](housing_finance_versions.md)
for preflight, build, validation, lineage, and cutoff rules.

On a fully provisioned research workspace, complete the finance guide's preflight
and stop database readers. The integrated runner refreshes general sources,
rebuilds retrospective B, appends a C cutoff, then registers and validates.
The following is a **template**: replace `YYYY-MM-DD` with the intended information
cutoff in year-month-day form, following the finance guide's availability rules.
Do not paste the placeholder literally:

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py --information-cutoff YYYY-MM-DD
```

After the prompt returns, check `$LASTEXITCODE` and the newest row of
`metadata/system_refresh_run_log.csv`: match the cutoff, require `status=succeeded`,
and review `error`. Review the source refresh and QA reports too; the log alone
does not establish current releases for manual sources. A failed run can leave
partially updated products, so do not describe it as a completed integrated refresh.
The runner verifies, but does not rebuild, frozen Version A. Do not change its
benchmark or freeze manifest to bypass a verification failure. The runner does
not install a recurring schedule.
The macro-only acquisition helper `scripts/refresh_macro_request.py` acquires and
strictly cleans nine macro sources; it does not publish a final database by itself.

## Files, provenance, and validation

| Location | Role |
|---|---|
| `data/raw/` | Immutable permitted provider payloads. |
| `data/benchmark/` | Benchmark archives and prepared inputs; preserve raw vintages and the frozen version's manifest-protected files. |
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

Before publishing an implementation or describing a build as validated, run these
two commands separately from the project PowerShell window. They assume the
environment and required local products already exist. Wait for the prompt and
check `$LASTEXITCODE` after each:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
```

The first should finish with no failed tests; review skips because they are not
evidence that an unavailable fixture was tested. The second should report
`QA completed: 0 errors` and exit with `0`. Integration tests require the
corresponding local data products; a code-only clone does not contain their fixtures.
Structural failures stop validation. Plausible economic outliers, freshness, and
expected gaps remain review warnings. Check
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

After editing `config/variable_metadata.csv`, stop database readers and run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage variable-metadata
```

Check the exit code and metadata-stage results in the stage table, then run the
validation command above. This refreshes the dictionary, SQL labels in an existing
database, export dictionary, and transformation entry without changing observation
values. It does not build a missing database. After running `catalog`, repeat this
metadata stage to enrich generated estimates.

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
