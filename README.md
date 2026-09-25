# Los Angeles Housing Database

Find housing data for Los Angeles and comparison areas, select a whole dataset
or one variable, and download a CSV for Excel or analysis. Once set up, the local
**Data Browser** provides dropdowns and download buttons; everyday use requires
no SQL or database knowledge.

**Start here:** [clone](#1-install-the-tools-and-clone-the-project) →
[set up Python](#2-set-up-the-project) → [get data](#3-get-a-local-database) →
[open the browser](#4-open-the-data-browser) → [download CSV](#5-download-your-first-dataset).
Already set up? Go straight to step 4.

## Before you start

These instructions use **Windows PowerShell**. A *terminal* is the window where
you paste commands; *cloning* downloads a working copy of this repository. Run
commands one at a time, without the code fences. Wait for each to finish and
resolve any error before continuing.

| Setup step | What you receive |
|---|---|
| Clone from GitHub | Programs, configuration, variable definitions, and reference guides. |
| Supply or build a database | Actual observations queried by the browser. |

**GitHub does not include `database/housing.duckdb` or the raw source archives.**
Cloning and installing Python alone will not provide housing observations.
Step 3 explains this requirement. You can inspect the published
[variable dictionary](metadata/data_dictionary.csv) immediately without setup.

## 1. Install the tools and clone the project

1. Install [Git for Windows](https://git-scm.com/downloads/win) and
   [Python for Windows](https://www.python.org/downloads/windows/).
   The project requires Python **3.11 or newer**; these examples use 3.11.
2. Open **Windows PowerShell** from the Start menu. If it was open during
   installation, close it and open a new window.
3. Check the tools:

```powershell
git --version
py -3.11 --version
```

Both should print a version. If you installed another supported version, replace
`-3.11` with that version below, for example `-3.12`.

4. Clone into your Downloads folder:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads')
git clone https://github.com/ciciiiyaoyf/la_housing_database.git
Set-Location -LiteralPath '.\la_housing_database'
```

GitHub may ask you to sign in. This repository requires access granted by its
owner; “repository not found” can mean your account lacks access. See GitHub's
[cloning instructions](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
for help. If you already have this project folder, use it instead of cloning a
second copy; see [updating](#8-update-an-existing-copy).

You should now be in the folder containing `README.md`, `requirements.txt`, and
`Open_Data_Browser.cmd`. Keep this PowerShell window open for the next steps.

## 2. Set up the project

Create the project's Python environment, named `.venv`, and install its packages.
Do this once for each new local copy:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_query.py --help
```

The last command should display query-tool help. These commands use the
interpreter directly: no activation or PowerShell execution-policy change is
needed. On macOS/Linux, use `python3 -m venv .venv` and `.venv/bin/python` instead
of the Windows interpreter path; launch with Python in step 4, not the `.cmd` file.

## 3. Get a local database

To use the existing research dataset, obtain a **compatible, permitted database
snapshot** from the project maintainer. **No ready-made database download is
currently supplied by this repository.** Request its build date, coverage, and
access restrictions with the file. A public copy must exclude licensed HUD-USPS
detail; the maintainer's full research database is not automatically suitable for
redistribution.

1. In File Explorer, open your `la_housing_database` project folder.
2. Open its `database` subfolder, creating it if necessary.
3. Place the supplied file there with the exact name **`housing.duckdb`**. Extract
   it first if supplied in a ZIP archive. Preserve an existing research database
   if you are not intentionally switching snapshots.
4. In the project PowerShell window, check that the file exists and its variable
   catalog can be read:

```powershell
Test-Path -LiteralPath '.\database\housing.duckdb'
.\.venv\Scripts\python.exe run_query.py catalog --variable zori_smoothed_sa
```

The first command should print `True`; the second should show the rent variable's
metadata. This checks the file and catalog, not complete observation coverage.
The browser preview in step 5 checks the data you selected. Missing-table or
metadata errors mean you need a snapshot compatible with this checkout.

**To build from provider files instead**, follow
[Database build and refresh](docs/database_build_and_refresh.md). This is a
maintainer workflow with source archives, manual files, access requirements,
and validation; it is not a one-command installation of the full snapshot.
`--offline` needs existing archives, and the finance layers need frozen benchmark
inputs. A one-source download does not build a complete working database.

## 4. Open the Data Browser

In **File Explorer on your computer**, open the project folder and double-click
**`Open_Data_Browser.cmd`**. Clicking its name on GitHub or in a document preview
does not run the local program.

A terminal window should stay open and your web browser should show **LA Housing
Data Browser**. You can do the same thing from PowerShell in the project folder:

```powershell
.\.venv\Scripts\python.exe run_data_browser.py --open-browser
```

If no tab appears, copy the address printed in the terminal into your browser,
normally `http://127.0.0.1:8765/`. This address refers to your own computer.
Keep the terminal open while using the page. Press **Ctrl+C** in that terminal
to stop it when finished.

The page reads a local snapshot. Opening it does not fetch new provider releases
or update observations.

## 5. Download your first dataset

Try this example if your database contains the postal-ZIP housing panel:

1. **Dataset:** choose **Postal ZIP housing — Monthly**.
2. **Geography type:** choose **postal_zip**.
3. **Place:** choose **ZIP_90024**.
4. **Variable:** choose the Zillow Observed Rent Index entry ending in
   **`(zori_smoothed_sa)`**. For the whole dataset, keep **All variables**.
5. **Frequency:** choose **monthly**. Leave the other filters at **All**.
6. Optionally set **From** to `2020-01-01`; leave **Through** blank for all later
   available dates. Both date endpoints are included.
7. Click **Preview data**. Check the place, dates, and columns. The rent column
   is called `zori`; the definitions file identifies it as `zori_smoothed_sa`.
8. Click **Download CSV**, then **Download variable definitions**.

The browser saves to its usual Downloads folder or asks where to save the files.
The data CSV includes **all matching rows**, not just the 20 preview rows. The
second CSV gives variable IDs, units, temporal types, and descriptions; keep
it with the data. Dates, geography, quality, and provenance fields remain in a
single-variable export. Missing values stay missing.

For every place, leave **Place** at **All places**. Changing **Dataset** resets
earlier filters. CSV recipients do not need to install this project.

## 6. Find other data and use the filters

| Dataset choice | Typical use |
|---|---|
| LA County housing — Monthly | County prices, rents, housing activity, and derived measures. |
| LA and comparable counties — Monthly | Separate observations for LA and seven comparison counties. |
| Postal ZIP housing — Monthly | Housing measures for individual postal ZIPs. |
| County controls — Annual / Census ZCTA controls — Annual | Annual demographic and housing controls. |
| State / Metro / Macro controls | Broader indicators at their actual geography and frequency. |
| Source observations — General layer | Source records with `variable_id` and `value` columns. |
| Housing finance — Versions A, B, C | Benchmark, retrospective monthly, or information-cutoff finance data. |
| Homebuying cost scenarios | Modeled costs and assumptions, not observed transactions. |

Choices depend on the local database. A dictionary entry does not guarantee
populated observations for every dataset or place.

| Filter | What it selects |
|---|---|
| Variable | One measure and its identifier. |
| Geography type / Place | A geography class and location. `COUNTY_06037` is LA County. |
| Frequency | Output cadence, such as monthly or annual. |
| Domain | Subject group, such as `rental_market`, `construction`, or `labor_market`. |
| Layer | General, Version A, Version B, or Version C; choose a compatible dataset. |
| Proxy variable | Yes/No classification indicating a proxy for another concept. |
| Constructed variable | Yes/No classification indicating a calculation or model. |
| Units and temporal types | Simplified units, stock/flow and other temporal labels, and native frequency. |

Filters match the reference workbook's **Variable Inventory** labels and combine
with **AND**. **All** imposes no restriction. Proxy and Constructed are independent;
unknown flags are not No. For example, LA monthly construction proxies use
**LA County housing — Monthly → county → COUNTY_06037 → Domain construction →
Layer General → Proxy Yes → Constructed Yes**.

Postal ZIPs and Census ZCTAs are distinct. County selection does not fill missing
values from state or national data. Output frequency does not change source
meaning: Redfin ZIP windows overlap across three months; ACS five-year estimates
remain survey-period estimates. Stock/flow labels do not automatically resample
values.

For an empty preview, remove a filter or choose a compatible dataset. Some derived
General variables exist only in analysis tables; finance Version C retains multiple
information cutoffs. Use [Query and export commands](docs/query_and_export_commands.md)
for multiple selected places, saved commands, Parquet output, and SQL.

## 7. Open the CSV in Excel

1. In Excel choose **Data → From Text/CSV** and select the data file.
2. Choose **Transform Data** if identifier columns are interpreted as numbers.
3. Set FIPS, postal ZIP, ZCTA, and similar identifiers to **Text**.
4. Load the table and open the definitions CSV the same way.

This preserves leading zeros, such as FIPS `06037`. Double-clicking a CSV can
cause Excel to convert those identifiers to numbers.

## 8. Update an existing copy

Close the browser's launcher first. In PowerShell, open your existing project
folder and run:

```powershell
git status --short
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If Git reports local edits or refuses the pull, preserve those edits and ask the
maintainer to help reconcile them; do not discard them to force an update.
Reopen the launcher after a successful update. **Pulling code does not refresh
the data**: obtain an updated compatible snapshot or use the maintainer workflow.

## Troubleshooting

| What you see | What to do |
|---|---|
| `git` or `py` is not recognized | Install the tool, reopen PowerShell, and repeat the version check. |
| Clone destination already exists | Use the existing project folder and step 8. |
| Python environment is missing | Run step 2 inside the project folder. |
| Database not found | Complete step 3; cloning alone does not provide the database. |
| Launcher closes / nothing appears | Run the Python launch command in step 4 to read the error. |
| Port already in use | Use the earlier launcher's page or stop that launcher before reopening. |
| Browser cannot reach the database | Keep the launcher running; reopen it if closed. |
| No matching rows or variables | Reset filters, check coverage, and widen the date range. |
| Restricted HUD-USPS download refused | Licensed detail is excluded from ordinary exports; select public measures. |

## Where to find things

This README is the beginner tutorial. Each of the six reference guides below
has one purpose. Replaced guides and dated narrative reports remain in Git history.
Each top-level module folder has a short `README.md` explaining its role,
key files, and inputs/outputs. Nested modules are described in their parent guide.

| Guide | Read it for… |
|---|---|
| [Data inventory and limitations](docs/data_inventory_and_limitations.md) | Available layers, coverage records, missing targets, and limitations. |
| [Query and export commands](docs/query_and_export_commands.md) | Repeatable filters, CSV/Parquet, SQL, and unit/temporal definitions. |
| [Database build and refresh](docs/database_build_and_refresh.md) | Acquisition, rebuilds, updates, provenance, and QA. |
| [Source definitions and methods](docs/source_definitions_and_methods.md) | Rental availability, insurance, debt, construction, and eviction methods. |
| [Housing finance versions](docs/housing_finance_versions.md) | Benchmark A, retrospective B, and real-time C. |
| [County and ZIP housing indicators](docs/county_and_zip_housing_indicators.md) | Momentum, tightness, valuation, heat, and market regimes. |

| Folder or file | Contents |
|---|---|
| `Open_Data_Browser.cmd` | Windows launcher for everyday downloading. |
| [docs/](docs/README.md) | Index of the six detailed guides above. |
| `metadata/data_dictionary.csv` | Variable names, IDs, definitions, units, and classifications. |
| [metadata/](metadata/README.md) | Inventories, coverage, provenance, audit evidence, and frozen model parameters. |
| [config/](config/README.md) | Sources, variables, geography references, and method settings. |
| [database/](database/README.md) | Local query database, not included in Git. |
| [data/](data/README.md) | Storage-layer map for source archives, clean inputs, and final analytical products. |
| [output/](output/README.md) | Generated CSVs, coverage, QA reports, and local historical report archives. |
| [src/](src/README.md) | Pipeline modules and their responsibilities. |
| [scripts/](scripts/README.md) | Source refresh helpers, audits, browser page, and preserved document tools. |
| [sql/](sql/README.md) | Database schemas, optional views, and manual structural checks. |
| [tests/](tests/README.md) | Verification and required local fixtures. |

Metro variables and finer-geography alternatives are covered in the
[data guide](docs/data_inventory_and_limitations.md#metro-variables-and-finer-geography-alternatives).
The standalone progress report has been absorbed there. Its reusable building and
QA code is preserved in [report authoring](scripts/README.md#optional-document-tools).

For current variable definitions, use the [data dictionary](metadata/data_dictionary.csv),
which contains 558 IDs. The generated Excel catalog is `metadata/data_catalog.xlsx`
in a local build.
[Source coverage](metadata/source_coverage.csv) and the
[dataset inventory](metadata/analytical_dataset_inventory.csv) describe the
maintained build. Check your local database for the data you actually have.

The [housing finance guide](docs/housing_finance_versions.md) compares Versions A/B/C.
Historical workbooks and previews from the former `outputs/` folder are retained
locally under `output/archive/legacy_reports/`. The six previously published
workbooks also remain in Git history. Their useful explanations are consolidated
in the data and finance guides; current inventories take precedence over those snapshots.
