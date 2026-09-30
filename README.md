# Los Angeles Housing Database

Use the local **DuckDB database** to explore Los Angeles housing data, retrieve
tables or individual series with SQL, and load query results directly into Python
for modeling and forecasting research. The database includes observations,
analytical panels, variable definitions, and provenance.

**First-time collaborator setup: steps 1–4, in order.** Each step contains its
Windows and macOS/Linux commands; the collaborator uses only the matching
platform's block. GitHub sign-in, Python setup, release selection, database
installation, and the first query are included in that sequence.

| Stage | When it is needed |
|---|---|
| **1–4: setup and first query** | Once for a new collaborator's local copy. |
| **5: explore tables and variables** | After setup, when choosing data. |
| **6: modeling** | Optional Python analysis; source-refresh commands are for a provisioned publisher. |
| **7: CSV downloads** | Optional alternative to SQL/Python. |
| **8: later updates** | When another database release becomes available. |

The collaboration section after step 8 is a reference for publishing and
contributing changes, not an additional prerequisite. An existing publisher keeps
the working branch and built database and uses the publisher instructions; the
collaborator's release-selection/download steps do not apply to that build workspace.

For readers who prefer spreadsheet files, the
[CSV browser and export tutorial](docs/query_and_export_commands.md#browser-downloads)
is an alternative. CSV export is not required for SQL analysis or modeling.

For the nine county and ZIP housing constructions in one download, choose
**Nine housing constructions — All geographies and periods** in the Data Browser,
or query `analytics.nine_constructions`. Here, **nine** means nine indicator
families containing **41 existing variables**, including alternate measures and
supporting diagnostics. Expand **Variables** to search and check several measures,
or keep **All variables** checked for the full selection. The table keeps county months, ZIP months,
ZIP-quarter classifications, quarterly summaries, and quarterly transitions as
separate row types. Follow the [combined-table instructions](docs/query_and_export_commands.md#download-all-nine-housing-constructions-together)
before interpreting its blanks or combining rows.

Every Data Browser dataset groups its variable choices by subject or an existing
indicator family. Finance, homebuying scenarios, and regime datasets use more
specific groups so related measures are easier to find. These headings organize
the picker; variable definitions, Domain filters, and exported values stay the same.

## Before you start

A collaborator needs repository access, an internet connection, and enough local
space for the database, a temporary download, and one previous-version backup.
The Git clone contains programs and documentation. The published database is a
separate download installed in step 3; source archives are not included.

| Window or prompt | Purpose |
|---|---|
| **Windows PowerShell** (`PS ... >`) | Windows installation and project commands. |
| **macOS/Linux Terminal** | macOS/Linux installation and project commands. |
| **Web browser** | GitHub sign-in and this guide. |
| **DuckDB** (`D` prompt inside the terminal) | SQL, after step 4 starts the database client. |

Each command runs separately. The collaborator copies one command line into the
terminal, presses **Enter**, waits for the prompt to return, and stops if the
command fails. Pasting uses **Ctrl+V** on Windows and **Command+V** on macOS.
The `PS ... >` or `D` prompt itself is not
part of a command. Commands are not entered in GitHub's webpage, a browser address
bar, or a Python prompt (`>>>`). A Python prompt closes with `exit()`.

On Windows, the Start menu's **Windows PowerShell** opens the correct shell.
A window showing only `C:\...>` is Command Prompt; entering `powershell` there
starts PowerShell. Administrator mode is not required for the project commands.

### macOS and Linux

On macOS, **Command+Space → Terminal** opens the correct window. Linux uses its
Terminal application. Both platforms follow the same numbered steps below using
the macOS/Linux blocks. There is no separate setup walkthrough to complete later.

## 1. Install the tools and clone the project

### Install the tools

The required tools are **Git**, **Python 3.11 or newer**, and **GitHub CLI (`gh`)**.
An already installed tool can be kept if its version check succeeds.

**Windows:** the collaborator installs Git from
[Git for Windows](https://git-scm.com/downloads/win) and Python from
[Python for Windows](https://www.python.org/downloads/windows/). A new installation
can use the Python install manager; after installation, a new PowerShell window
runs the following command to install Python 3.11:

```powershell
pymanager install 3.11
```

GitHub CLI is installed separately in PowerShell:

```powershell
winget install --id GitHub.cli --exact
```

The collaborator closes and reopens PowerShell after installation, then checks:

```powershell
git --version
py -3.11 --version
gh --version
```

Each line runs separately. A different supported Python version can replace
`-3.11` consistently in the Windows commands. If `gh` is installed in its normal
location but an existing PowerShell window cannot find it, that window runs:

```powershell
$env:Path = "$env:ProgramFiles\GitHub CLI;$env:Path"
gh --version
```

**macOS:** with [Homebrew](https://brew.sh/) installed, Terminal runs:

```bash
brew install git python gh
git --version
python3 --version
gh --version
```

**Linux:** the collaborator installs Git and Python 3.11+ using the distribution's
package manager and GitHub CLI using its
[official installation instructions](https://github.com/cli/cli#installation),
then runs the same three version checks as macOS. Some distributions also require
their Python `venv` package.

### Sign in to GitHub

In the same terminal, on either platform:

```text
gh auth login
```

The collaborator chooses **GitHub.com**, **HTTPS**, and **browser sign-in**, using
the account granted access to this repository. If asked to authenticate Git with
the GitHub credentials, the collaborator chooses **Yes**.

The terminal displays a one-time code. The collaborator copies that code into the
browser's **Authorize your device** page, approves **GitHub CLI**, and waits for
the terminal's authentication-success message. The code comes from the terminal,
not email; GitHub Desktop is not required. An expired code requires another
`gh auth login` attempt. Sign-in is checked with:

```text
gh auth status
```

### Download the project

**For a new local copy**, the collaborator runs the matching block, one line at
a time. The clone command runs only when the destination folder does not exist.

**Windows PowerShell:**

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads')
git clone https://github.com/ciciiiyaoyf/la_housing_database.git
Set-Location -LiteralPath 'la_housing_database'
```

**macOS/Linux Terminal:**

```bash
cd "$HOME/Downloads"
git clone https://github.com/ciciiiyaoyf/la_housing_database.git
cd la_housing_database
```

### Return to the project folder in PowerShell

**For an existing copy**, or after reopening a terminal, the collaborator enters
the existing project instead of cloning again. A different clone location
replaces the example path.

**Windows PowerShell:**

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
Get-Location
Get-ChildItem -Name
```

**macOS/Linux Terminal:**

```bash
cd "$HOME/Downloads/la_housing_database"
pwd
ls
```

The folder listing must include `README.md`, `requirements.txt`, and
`run_snapshot.py`. An older clone without `run_snapshot.py` needs the current
project code. On its normal working branch, the collaborator runs:

```text
git status --short
git pull --ff-only
```

The pull runs only if `git status --short` prints nothing. Local work is preserved
and reconciled before proceeding; a failed pull is not bypassed by discarding
files. A copy already checked out at a published release uses step 8 for later
updates. The same terminal stays open for step 2.

## 2. Set up the project

The collaborator remains in `la_housing_database` in the terminal from step 1.
A Python environment named `.venv` holds this project's packages. It is created
once for each local copy; an existing working `.venv` can be reused.

**Windows PowerShell:**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_query.py --help
.\.venv\Scripts\duckdb.exe --version
```

**macOS/Linux Terminal:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_query.py --help
.venv/bin/duckdb --version
```

Each line runs separately and must finish successfully before the next line.
Environment creation may be silent. The final two commands should show query-tool
help and a DuckDB version. `requirements.txt` installs both DuckDB's Python library
and its command-line client. These checks do not install housing observations.
The terminal stays in the same folder for step 3; environment activation is not
required because the commands name the environment's Python directly.

## 3. Get a local database

This step receives a completed, validated snapshot. It does not run provider
downloads or rebuild the database. GitHub authentication and Python setup were
completed in steps 1–2, so no later section is a prerequisite.

**A. Check the available release.** All database sessions are closed. In the
project folder, the collaborator runs the matching command:

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe run_snapshot.py check
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python run_snapshot.py check
```

The result includes `latest_tag` and `validated_with_code_commit`. The first
published release is **`housing-db-20260927-01`**. The commands below use that tag;
if `check` reports a newer tag, the collaborator substitutes the reported tag in
both the Git switch and snapshot download commands. If no release exists or
authentication fails, the collaborator stops and resolves that error first.

**B. Select the release's matching code.** The README may be newer than the code
used to validate the database. A successful `git pull` alone does not establish
compatibility. The collaborator keeps this guide open on GitHub and runs:

```text
git status --short
git fetch origin --tags
git switch --detach housing-db-20260927-01
```

The fetch and switch run only after `git status --short` prints nothing. Existing
uncommitted work is preserved and reconciled first. The switch selects the
release's exact code in the same project folder. Git may describe this as a
**detached HEAD**; this is expected for a selected release, and saved branches
remain available. It does not make the database read-only. A contributor creates
a named working branch before making code changes.

**C. Install the selected code's packages and download its database.** Each line
runs separately, using the same tag selected above.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_snapshot.py update --tag housing-db-20260927-01
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_snapshot.py update --tag housing-db-20260927-01
```

The updater downloads the database, verifies its checksum and structure, and
installs it at **`database/housing.duckdb`**. A result of `installed`, `unchanged`,
or `adopted` means this step succeeded. The active path stays the same on later
updates. Raw source archives are not included. The collaborator proceeds directly
to step 4 after success.

An unknown or locally changed existing database is preserved and causes the
updater to stop. After normal closure of its applications, that local work must
be preserved separately before an intentional switch to the managed snapshot.
A remaining `.wal` file stays with its database for recovery; it is never deleted
to bypass the check. A failed update is not followed by opening a missing file.

<details>
<summary>Alternative: a maintainer supplies a database file manually</summary>

This alternative is used instead of the automatic download when the maintainer
has supplied a compatible, permitted file with its build information, coverage,
and access restrictions. The full research database can contain licensed detail.

### Place the supplied file using File Explorer

The collaborator extracts any ZIP archive and uses Windows File Explorer,
macOS Finder, or Linux Files to place the file directly at
`la_housing_database/database/housing.duckdb`, with no extra containing folder.
An existing database is preserved before replacement. The file must be named
exactly `housing.duckdb`.

### Check the database in PowerShell

On Windows, `Test-Path -LiteralPath '.\database\housing.duckdb'` should return
`True`. On macOS/Linux, `ls -lh database/housing.duckdb` should list the supplied
file. A successful file check permits the direct local opening command in step 4.
The maintainer confirms the file's compatible code version. Manual files do not
automatically enroll in snapshot updates.

</details>

## 4. Open DuckDB and run your first query

Step 3 must have succeeded. In the same project folder, the collaborator opens
the installed database using the matching terminal command:

**Windows PowerShell:**

```powershell
.\.venv\Scripts\duckdb.exe .\database\housing.duckdb
```

**macOS/Linux Terminal:**

```bash
.venv/bin/duckdb database/housing.duckdb
```

DuckDB displays a **`D` prompt** inside that terminal. SQL goes at this prompt;
PowerShell and shell commands do not. The local session permits writes. An
optional `-readonly` flag before the file path provides a viewing-only session.
Local database edits are not sent to collaborators and will block a later
automatic replacement; permanent shared changes belong in reproducible Python
or SQL contributions.

At **`D`**, the collaborator runs:

```sql
SELECT current_database() AS database_name;
```

The result should contain `housing`. The first data query is:

```sql
SELECT date::DATE AS month, county_fips, zori
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
ORDER BY date DESC
LIMIT 10;
```

The result contains up to ten LA County rows, most recent first. `zori` is the
Zillow Observed Rent Index in USD/month; a blank/null cell remains missing. The
panel column corresponds to variable ID `zori_smoothed_sa`. Results depend on the
selected snapshot; the query does not export a CSV.

SQL statements finish with a semicolon. **Ctrl+C** cancels an incomplete statement.
The session stays open for exploration in step 5. A collaborator stopping here
can close the session and return to the terminal with:

```text
.quit
```

**Initial setup is complete.** Step 5 covers exploration. Steps 6–7 are optional
uses of the installed database. Step 8 is for later releases, not an additional
first-time requirement.

## 5. Find tables, datasets, and variables

The collaborator continues in the DuckDB session from step 4. At the **DuckDB
`D` prompt**, the following display command runs **without a semicolon**:

```text
.mode table
```

This prints the complete result instead of DuckDB's shortened preview. The next
SQL block runs at the same prompt, including its final semicolon:

```sql
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_catalog = current_database()
ORDER BY table_schema, table_name;
```

This lists **every table and view in every schema of the open database**. The
output can extend beyond one screen; scrolling up reveals the beginning of the
list, and a wider terminal helps with long names. The following command restores
the usual compact display at **`D`**, without a semicolon:

```text
.mode duckbox
```

A *schema* is a group of related
tables; `analytics.county_monthly_panel` means the `county_monthly_panel` table
inside the `analytics` schema. A *view* is a saved query presented like a table.
A table's presence does not guarantee non-missing values for every variable/date.

The [DuckDB SQL guide](docs/duckdb_sql_guide.md) is the main usage tutorial. It
explains, in order, how to:

1. Inspect column names, data types, sample rows, and variable definitions.
2. Select geography, frequency, Domain, Layer, Proxy, Constructed, units, and temporal type.
3. Retrieve a whole dataset or one series, with explicit date and location filters.
4. Check missingness and duplicate keys before joining tables or modeling.
5. Reuse saved SQL and load its results directly into a Python DataFrame.

The [data inventory](docs/data_inventory_and_limitations.md) describes product
choices and links to source/finance methods for interpreting modeled values.
After exploration, `.quit` closes DuckDB and returns to the terminal.

## 6. Use query results for modeling

This section is optional after initial setup. The first example reads the
installed snapshot. Source refresh is a separate publisher workflow afterward;
its inputs are not supplied by a database download.

### Load model inputs from DuckDB

The collaborator can load the installed snapshot into Python without a CSV.
The repository includes a small, reproducible example:

- [sql/model_input.sql](sql/model_input.sql) selects LA County monthly rent data.
- [scripts/load_model_data.py](scripts/load_model_data.py) runs that query against
  the local database and returns a pandas DataFrame, a table in Python memory.

The collaborator closes DuckDB with `.quit` and runs the matching command in
the project folder. **Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe scripts/load_model_data.py
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python scripts/load_model_data.py
```

The program prints the result's size and a preview; it does not fit a forecasting
model, fill missing values, or modify the database. The
[modeling section of the SQL guide](docs/duckdb_sql_guide.md#8-use-the-query-result-in-python)
explains how to use the function in an analysis and change the saved query.

For forecasting, historical observation dates alone do not establish what was
known at a past forecast date. Provider revisions, release dates, finance cutoffs,
missing months, and model-training periods matter. The
[forecasting guidance](docs/duckdb_sql_guide.md#9-prepare-for-forecasting-without-using-future-information)
describes these considerations before a training sample is created.

### Refresh the data before modeling

**Publisher maintenance only.** This source-acquisition workflow requires the
source archives and permissions described below. It is not a collaborator setup
step; a collaborator needing newer inputs receives a newly published snapshot
through step 8 before choosing the analysis sample.

**The whole-project update command is `run_system_refresh.py`.**
It refreshes the configured general sources and tables, rebuilds finance B,
builds or validates the finance C cutoff, and validates the integrated database.
`run_pipeline.py --stage refresh` is the narrower choice for general data only;
it does not recompute finance B/C. The publisher chooses one workflow.

The whole-project command requires a **fully provisioned research workspace**:
installed packages, existing source archives, permitted
source access, and the frozen finance A files. A supplied DuckDB snapshot by itself
does not include these prerequisites. The
[complete refresh guide](docs/database_build_and_refresh.md#refresh-everything-before-modeling)
describes acquisition setup for a new publisher workspace.

First, the publisher closes **every DuckDB session** with `.quit`, including the
terminal running DuckDB's graphical interface, stops the CSV Data Browser with
**Ctrl+C** in its terminal, and closes Python database connections. Closing a
web-browser tab alone does not stop either server.

**Where the command runs:** in **Windows PowerShell** or **macOS/Linux Terminal**,
inside the existing `la_housing_database` project folder. The publisher runs only
the command for the matching operating system and waits for the prompt to return.

**Windows PowerShell — whole-project update:**

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py
```

**macOS Terminal — whole-project update:**

```bash
.venv/bin/python run_system_refresh.py
```

The default finance C information cutoff is **today's UTC date**. To choose a
specific cutoff, the publisher uses the guide's `--information-cutoff` instructions. Existing
C vintages and frozen A remain preserved. Fixed source editions are checked within
their configured scope; this command does not silently replace them with newer
products or make unavailable manual inputs current.

Immediately after the command finishes, the publisher checks its exit code in
the same window.

**Windows PowerShell:**

```powershell
$LASTEXITCODE
```

**macOS Terminal:**

```bash
echo $?
```

| Result | What to do before modeling |
|---|---|
| `0` | Refresh completed for the configured scope. Review coverage, QA, and fixed-edition limitations. |
| `2` | Rebuild completed, but manual source checks or other reported actions remain. Resolve or document those limitations before choosing model inputs. |
| `1` | Refresh failed and may have updated only some products. Read the reported error and refresh reports; do not treat this as a completed update. |

**The CSV Data Browser uses this refreshed database too.** After a successful
refresh, reopening the browser and using the new page, or reloading an existing
tab, loads current dataset and filter choices. Previews and CSV downloads read
the database at the time of the request; there is no separate browser update
command. Previously downloaded CSVs remain saved copies. The
[Windows/macOS reopening steps](docs/database_build_and_refresh.md#use-the-refreshed-data-in-the-csv-browser)
explain how to download updated data.

The complete [dataset update schedule](docs/database_build_and_refresh.md#dataset-update-schedule)
is directly in the refresh guide. It lists all **73 configured datasets, finance
inputs, and products**, distinguishes observation frequency from provider release
cadence, and gives each update command or required action. The same guide explains
refresh logs and actions to resolve before modeling.

Redfin ZIP acquisition uses the provider's public file index and downloads its
national CSV directly (about 1.3 GB); no browser automation or API key is needed.
The cleaner keeps the newer days-on-market YoY change in days separate from the
legacy percentage measure. **The county Redfin source still has a methodology
review hold. A whole-project, all-general-source, or combined Redfin refresh cannot
finish successfully while that hold remains.** The command reports the blocker;
successful ZIP acquisition does not clear it. See
[Redfin downloads](docs/database_build_and_refresh.md#redfin-downloads) for the
current source status and the independent ZIP refresh.

For **only the general sources and tables**, the publisher can choose the
following alternative. It includes cleaning, rebuilding, validation, and exports,
but leaves finance B/C unrecomputed. It is an alternative to the whole-project
command, not an additional setup step.

**Windows PowerShell — general data only:**

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh
```

**macOS Terminal — general data only:**

```bash
.venv/bin/python run_pipeline.py --stage refresh
```

To select **both Redfin county and ZIP sources**, the smaller alternative below
acquires and cleans those sources, then rebuilds shared outputs once. It remains
subject to the county hold and does not recompute finance B/C.

**Windows PowerShell — both Redfin sources:**

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin
```

**macOS Terminal — both Redfin sources:**

```bash
.venv/bin/python run_pipeline.py --stage refresh --source redfin
```

The guide's [command selector](docs/database_build_and_refresh.md#choose-the-right-refresh-command)
also covers individual-source updates. A successful complete refresh already
includes cleaning; separate cleaning commands are for the documented maintenance
workflow.

## 7. CSV downloads as an alternative

For inspecting data in Excel or sharing permitted tables as files, the
[step-by-step CSV browser tutorial](docs/query_and_export_commands.md#browser-downloads)
provides Windows launcher and macOS/Linux Terminal instructions. It explains
how to choose a whole dataset or selected variables, where files are saved, and
how to import them into Excel without losing leading zeros. The same guide covers
saved command-line exports and their companion variable definitions.

This is an alternative interface to the local database. SQL queries and Python
modeling do not require a CSV export first. The browser does not fetch new provider
releases or install a missing database.

## 8. Update an existing copy

This step is used later, when the collaborator wants a newer published database.
The existing folder and `.venv` are reused. An existing publisher instead keeps
the build workspace on its working branch and follows the publisher sequence
below; downloading over that built database is not a refresh procedure.

**A. Close active sessions and enter the project folder.** DuckDB closes with
`.quit`; the Data Browser stops with **Ctrl+C** in its terminal; Python database
connections close normally. Closing a browser tab alone does not stop a server.

**Windows PowerShell:**

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
git status --short
.\.venv\Scripts\python.exe run_snapshot.py check
```

**macOS/Linux Terminal:**

```bash
cd "$HOME/Downloads/la_housing_database"
git status --short
.venv/bin/python run_snapshot.py check
```

The collaborator preserves and reconciles any code changes before proceeding.
The check reports the newest `latest_tag`. If the installed file already matches
the desired release, a new download is unnecessary. Local database edits must be
preserved separately before replacement; they are not listed by `git status`.

**B. Fetch and select the reported release.** These commands are the same on both
platforms. The example tag is replaced with the `latest_tag` just reported:

```text
git fetch origin --tags
git switch --detach housing-db-20260927-01
```

A release checkout uses fetch/select rather than `git pull`, because it names a
specific published version. Existing contribution branches remain available.

**C. Update packages and open the matching snapshot.** The same reported tag is
used in the command below, with each line run separately.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_snapshot.py open --tag housing-db-20260927-01
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_snapshot.py open --tag housing-db-20260927-01
```

`open` verifies and installs the requested release before starting a writable
DuckDB session. Replacing `open` with `update` installs without starting a session.
The active file stays at `database/housing.duckdb`; the previous managed version
is retained as one backup. Changes appear after a publisher releases a completed
build and the collaborator runs this update sequence.

## Collaborate through database snapshots

GitHub collaborators can contribute Python, SQL, configuration, and documentation.
One **publisher** initially runs accepted changes on a computer with the required
source archives and build inputs. A **collaborator** downloads each completed
database and works locally. Either person can become a publisher after obtaining
the required inputs and permissions. Downloading a snapshot does not provide the
raw archives needed to rebuild it.

The workflow is: **contribute code → integrate changes → build and validate →
publish a release → update local copies**. Editing a local table alone does not
send changes to another computer. This workflow does not synchronize two open
databases or merge independent database edits.

### One-time setup for publishers and collaborators

Required tool installation and GitHub authentication are already included in
step 1, before cloning or downloading. Python setup is step 2. There are no
additional collaborator setup commands hidden in this reference section.

The repository owner keeps the configured repository private and confirms that
every repository reader may receive the included data. Repository membership does
not grant redistribution rights. `config/database_sharing.yaml` selects the
repository and snapshot paths. Credentials remain in GitHub CLI authentication
storage, outside the project. The publisher additionally needs release-writing
permission and a provisioned source/build workspace.

### Publisher: first release and every later update

This sequence uses the publisher's `main` branch when direct pushes are permitted.
If a change requires a pull request, the publisher starts with the contribution
branch sequence below **before editing or building**, keeping local `main`
unchanged until that request is merged.

**A. Pull the accepted code, then build.** The publisher closes database
applications and enters the existing build workspace. GitHub CLI sign-in from
step 1 must already be complete. The following Git commands work in both Windows
PowerShell and macOS/Linux Terminal, one line at a time:

```text
git status --short
```

The status must be empty before switching branches or pulling. Existing local
work is preserved and reconciled first. Once reviewed contributions have been
merged into GitHub's `main`, the publisher runs:

```text
git switch main
git pull --ff-only origin main
```

If the pull fails, the publisher stops and resolves the reported issue before
building. `git pull` retrieves code and tracked metadata; it does not download
provider observations or replace `database/housing.duckdb`.

The publisher installs the pulled code's requirements. **Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python -m pip install -r requirements.txt
```

The publisher then builds the intended database using the
[build and refresh guide](docs/database_build_and_refresh.md). The command
depends on the change and available source inputs. The existing Redfin county
refresh hold remains in force; a blocked all-source refresh is not a successful
complete refresh. A release of an existing validated snapshot must describe that
snapshot's actual coverage rather than claim new ingestion.

**B. Validate before the final review and commit.** The publisher closes DuckDB
with `.quit`, stops the Data Browser with **Ctrl+C** in its terminal, and closes
Python database connections. The following commands run in the project folder,
one at a time, stopping on any failed check.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
git status --short
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python run_pipeline.py --stage validate
git status --short
```

Pipeline validation regenerates tracked `metadata/source_coverage.csv` and may
update the database's QA tables. The resulting metadata changes belong in the
review; they must not be reverted merely to obtain a clean checkout.

**C. Review, commit, and push.** After both checks pass, the publisher reviews
the resulting files. These commands work on both platforms:

```text
git status --short
git diff
```

The example below stages `metadata/source_coverage.csv`. The publisher replaces
or extends that path list with the actual reviewed code, configuration, and
metadata files reported above. New files also require inspection before staging.
Raw archives, credentials, and `.duckdb` files stay outside the Git commit.

```text
git add -- metadata/source_coverage.csv
git diff --cached
```

The staged diff must contain only the intended changes. The publisher then
commits them with a message describing the actual update:

```text
git commit -m "Update database build and source coverage"
```

If there are no changes to commit, the publisher skips staging and committing.
For a publisher authorized to push directly to `main`, the next commands are:

```text
git push origin main
git status --short
```

The push must succeed and the final status must be empty before publication.
An `Everything up-to-date` response is normal when that commit is already on
GitHub. A rejected push stops publication. The existing local commit is preserved
and reconciled with the maintainer before retrying; a force push is not used.
The new-contribution recipe below starts from remote `main` and does not carry
over an unpublished commit already made on local `main`. Unrelated local work is
preserved and resolved. Database applications remain closed.

**D. Publish a new release.** From the same project folder, the publisher runs
the matching command below. `housing-db-20260927-02` is an example; each release
needs a new tag using the current date and an incremented suffix for additional
releases that day. The already published `housing-db-20260927-01` is not reused.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe run_snapshot.py publish --tag housing-db-20260927-02 --acknowledge-authorized-sharing
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python run_snapshot.py publish --tag housing-db-20260927-02 --acknowledge-authorized-sharing
```

`publish` runs `pytest` and `run_pipeline.py --stage validate` against the existing
build, stops on failure, packages the database, and uploads the release. It does
**not** rebuild the database or download provider updates. Its manifest records
the exact code commit used for validation, SHA-256 checksum, file size, database
structure, and packaging time. Packaging time is not an inferred original build
date; source vintages and provenance remain in the database. Validation may update
the database's QA metadata; the package is made after those checks succeed.
Existing tags are not reused to replace an earlier release.
Publication reruns both verification commands and refuses the package if those
checks introduce tracked file changes. In that case, the publisher reviews the
new metadata, commits and pushes the intended result, and retries after the
checkout is clean. The tool never discards those changes automatically.
Publisher packages remain under `output/snapshots/publish/<tag>/`, and check logs
remain under `output/snapshots/logs/`. These publisher records are separate from
the collaborator's temporary download staging. An upload failure can leave a draft
release for inspection; it must not be reported as a successful publication.

For local review without an upload, the publisher can replace `publish` and its
acknowledgement with `prepare --tag housing-db-20260927-02`. This still runs the
validation gates and requires a clean committed checkout. Preparation alone does
not make a release available to collaborators. It retains a package for that tag;
a later publish command uses a new unused tag rather than overwriting the prepared
package, for example a suffix of `-03` after a preparation ending in `-02`.

### Collaborator: first download and every later update

The complete first-time sequence is steps 1–4 above. Step 8 contains the complete
later-update sequence. This reference section adds no required actions after
either sequence.

The clean code checkout must match the selected release's recorded revision.
Documentation on `main` can be newer than the latest database; selecting the
reported release tag before installation handles that difference without another
project folder or a forced reset. There is no background scheduler or live
database merge. A local writable session permits experiments; shared changes
enter the next build through reproducible code contributions.

### Contributing changes to the shared database

A **pull request** proposes merging a branch's changes into `main`. It is
different from `git pull`, which updates a local branch from GitHub. This sequence
is for contributing code or documentation; downloading a database remains the
step 8 workflow. All Git and `gh` commands below work in both Windows PowerShell
and macOS/Linux Terminal, inside the project folder, after GitHub sign-in.

**A. Start a new contribution before editing.** The collaborator closes database
applications and checks for local code changes:

```text
git status --short
```

The status must be empty. Existing work is preserved first; an existing proposal
continues on its own branch. For a new proposal, the collaborator runs:

```text
git fetch origin
git switch -c codex/update-model-query origin/main
```

`codex/update-model-query` is an example new branch name. The collaborator chooses
a distinct descriptive name for each proposal and uses the same name in the push
and pull-request commands below. This works from a clean release checkout too;
it starts the proposal from current remote `main` without deleting saved branches.

**B. Install requirements, edit, and check the change.** The collaborator runs
the matching package command. **Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Reusable SQL belongs in `sql/`; Python changes belong in the appropriate existing
module under `src/`. The collaborator makes the proposed edits, runs applicable
checks, and updates tests, variable definitions, and the transformation log when
methods change. The pull request states the checks actually run and any missing
build inputs. A provisioned publisher performs the required build and database
validation before releasing the result. Database experiments use a separate local
copy; edits typed only into DuckDB are not captured by Git.

**C. Commit and push the proposal branch.** The collaborator reviews the changes:

```text
git status --short
git diff
```

The next example assumes `sql/model_input.sql` is the edited file. The collaborator
substitutes the actual reviewed paths and includes the relevant tests and metadata.

```text
git add -- sql/model_input.sql
git diff --cached
```

After confirming the staged diff and choosing an accurate commit message:

```text
git commit -m "Update model input query"
git push --set-upstream origin codex/update-model-query
```

**D. Open the pull request after the push succeeds.**

```text
gh pr create --base main --head codex/update-model-query
```

The terminal prompts for a title and description. The collaborator describes the
change, its purpose, actual validation, and remaining limitations, then submits
the request. The command prints its GitHub URL. For requested revisions, the
collaborator edits on the same branch, repeats review/staging/commit, and runs
`git push`. Those commits update the existing pull request; a second request is
unnecessary.

### Review a pull request and receive the merged code

**A. The maintainer locates and reviews the request.** These commands run in the
project folder on either platform:

```text
gh pr list --base main --state open
```

The list includes each request's number. `123` below is only an example and is
replaced with the actual number:

```text
gh pr view 123 --web
```

The maintainer reviews the diff, validation evidence, and any required checks or
approvals on GitHub. Opening the request does not merge it. After review is
complete, an authorized maintainer can merge from the terminal:

```text
gh pr merge 123
```

The command prompts for an allowed merge method. A blocked or queued merge must
finish before the publisher proceeds; the GitHub request must show **Merged**.

**B. The publisher retrieves the merged code.** After closing database
applications and confirming `git status --short` is empty:

```text
git switch main
git pull --ff-only origin main
```

The publisher then follows the publisher sequence above: install requirements,
build, validate, review/commit/push generated metadata, and publish a new database
tag. A merged pull request alone does not update any collaborator's DuckDB file.
Collaborators receive the resulting release through step 8. A contributor saves
work on the named branch before returning to a clean published release checkout;
the snapshot installer requires that release's exact code revision.

### Existing databases and local changes

The active path stays **`database/housing.duckdb`** on every update. A new snapshot
downloads to temporary staging, is verified, and replaces the closed database only
when the installed file still matches its recorded baseline. The previous managed
version is retained at
`output/archive/database_snapshots/housing.previous.duckdb`; only one previous
managed version is retained, with its `housing.previous.snapshot.json` record.
Temporary staging is removed after the command.
Each changed snapshot downloads in full; this is not an incremental transfer.

An existing identical snapshot can be recognized without replacing it. An unknown
existing database, a locally modified database, an active connection, or a
remaining `housing.duckdb.wal` causes installation to stop. There is no force
overwrite switch. For an intentional switch, the collaborator closes applications
normally and preserves the local database in a separate working or archive folder
before moving it away from the managed path. A remaining WAL must stay with its
database and must not be deleted: recovery requires the matching pair. After safe
recovery and closure, `update` can install into the vacant managed path.

Experiments can use a separate database copy so the managed path remains available
for releases. A code contribution is shared through Git, reviewed, integrated, and
built by a publisher; uploading a collaborator's independently edited file does
not merge it with the published database. Installation records live in
`output/snapshots/installed.json`. A lost or changed record is treated
conservatively rather than authorizing replacement.

If the updater process stops during installation, the next update uses
`output/snapshots/install-pending.json` to reconcile the recorded snapshot with
the actual file. Unexpected bytes cause recovery to stop. The pending record
remains in place until recovery succeeds; it is not a substitute for backups.

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `'Set-Location'`, `'Get-Location'`, or `'Test-Path'` is not recognized as a command | Command Prompt is open. The collaborator enters `powershell` and confirms the prompt starts with `PS` before retrying the guide's PowerShell commands. |
| `git` or `py` is not recognized | Install the tool, reopen PowerShell, and repeat the version check. |
| `pymanager` is not recognized | This command requires the Python install manager. Complete its installation and reopen PowerShell; if Python 3.11 is already installed, use the version check in step 1 instead. |
| A prompt showing `>>>` | Python is open. The collaborator enters `exit()` and then opens Windows PowerShell from Start or Terminal on macOS/Linux. |
| A prompt showing `>>` after pasting | PowerShell is waiting for the rest of an incomplete command. Press Ctrl+C, then copy the complete command again, including both closing quotes and parentheses. |
| Cannot find `requirements.txt` or `run_query.py` | The terminal is probably in the wrong folder. The collaborator follows [Return to the project folder](#return-to-the-project-folder-in-powershell), then retries the failed command. |
| Clone destination already exists | The collaborator enters the existing folder and continues with step 2. Step 8 applies only after initial setup is complete. |
| Python environment is missing | Run step 2 inside the project folder. |
| Package installation reports an error | Stop before the next step. Check the internet connection and Python version; give the complete error to the maintainer if retrying does not resolve it. |
| Database not found | Complete step 3; cloning alone does not provide the database. |
| `duckdb.exe` cannot be found | Return to the project folder and rerun step 2's `pip install -r requirements.txt` command. |
| SQL produces a PowerShell error | Start DuckDB first; paste SQL at its `D` prompt. |
| DuckDB says a table or column is missing | Use the SQL guide's table/column discovery; confirm the snapshot contains the product. |
| DuckDB reports a file lock | Close other database sessions and any active build before reconnecting. |
| CSV launcher closes / nothing appears | Use the PowerShell launch instructions in the [CSV guide](docs/query_and_export_commands.md#browser-downloads). |
| CSV browser port already in use | Use the earlier launcher's page or stop that launcher before reopening. |
| Browser cannot reach the database | Keep the launcher running; reopen it if closed. |
| No matching rows or variables | Reset filters, check coverage, and widen the date range. |
| Restricted HUD-USPS download refused | Licensed detail is excluded from ordinary exports; select public measures. |
| Snapshot tool cannot find `gh` or authenticate | The collaborator completes the one-time GitHub CLI setup and checks `gh auth status` with the repository-authorized account. |
| Snapshot reports a GBK `UnicodeDecodeError` followed by a `NoneType` JSON error | The collaborator updates the project code to obtain the UTF-8 decoding fix. GitHub CLI responses use UTF-8 independently of the Windows display language; changing the system language is unnecessary. |
| No published database snapshot found | The publisher must complete the first `run_snapshot.py publish` release. A Git clone alone does not include the database. |
| Snapshot code revision mismatch | The collaborator preserves local code work, updates Git, and uses the exact clean revision recorded in the selected release. No forced reset is required. |
| macOS `git status --short` lists only untracked `.DS_Store` files | These are Finder display settings. In Terminal inside the project, the collaborator runs `printf '\n.DS_Store\n' >> .git/info/exclude`, then repeats `git status --short`. This ignores those filenames throughout the local checkout without deleting files or changing the shared project. |
| `git commit` reports `Author identity unknown` | The contributor sets a repository-local identity with `git config user.name "Contributor Name"` and `git config user.email "GITHUB_EMAIL_ADDRESS"`, replacing both example values with the contributor's name and GitHub-associated email or GitHub-provided no-reply address, then retries the commit. |
| Snapshot refuses an unmanaged or locally changed database | The local file is preserved. The collaborator follows the existing-database instructions before intentionally switching, or keeps using that working copy. |
| Snapshot reports an active database or WAL | Applications close normally before retrying. A remaining WAL is retained with its database for recovery, never deleted to bypass the check. |

When asking for help, the collaborator includes the step number, the command
entered, and the full error text. A screenshot can help for a browser or File
Explorer problem. The original message helps the maintainer identify the cause.

## Where to find things

This README covers setup and the first SQL query. Each of the seven guides below
has one purpose. Replaced guides and dated narrative reports remain in Git history.
Each top-level module folder has a short `README.md` explaining its role,
key files, and inputs/outputs. Nested modules are described in their parent guide.

| Guide | Read it for… |
|---|---|
| [DuckDB SQL guide](docs/duckdb_sql_guide.md) | Primary workflow: view tables, query data, and prepare model inputs. |
| [Data inventory and limitations](docs/data_inventory_and_limitations.md) | Available layers, coverage records, missing targets, and limitations. |
| [CSV browser and export commands](docs/query_and_export_commands.md) | Alternative downloads, Excel import, repeatable export filters, and metadata meanings. |
| [Database build and refresh](docs/database_build_and_refresh.md) | Acquisition, rebuilds, updates, provenance, and QA. |
| [Source definitions and methods](docs/source_definitions_and_methods.md) | Rental availability, insurance, debt, construction, and eviction methods. |
| [Housing finance versions](docs/housing_finance_versions.md) | Benchmark A, retrospective B, and real-time C. |
| [County and ZIP housing indicators](docs/county_and_zip_housing_indicators.md) | Momentum, tightness, valuation, heat, and market regimes. |

| Folder or file | Contents |
|---|---|
| `Open_Data_Browser.cmd` | Windows launcher for the alternative CSV browser. |
| `run_system_refresh.py` | One-command data refresh and rebuild; see the update schedule in the build and refresh guide. |
| `run_snapshot.py` | Publisher packaging and private release uploads; collaborator checks, protected installation, and update-before-open. |
| [docs/](docs/README.md) | Index of the seven guides above. |
| `metadata/data_dictionary.csv` | Variable names, IDs, definitions, units, and classifications. |
| [metadata/](metadata/README.md) | Inventories, coverage, provenance, audit evidence, and frozen model parameters. |
| [config/](config/README.md) | Sources, variables, geography references, and method settings. |
| [database/](database/README.md) | Local DuckDB file used for SQL and analysis; not included in Git. |
| [data/](data/README.md) | Storage-layer map for source archives, clean inputs, and final analytical products. |
| [output/](output/README.md) | Generated CSVs, coverage, QA reports, and local historical report archives. |
| [src/](src/README.md) | Pipeline modules and their responsibilities. |
| [scripts/](scripts/README.md) | Source refresh helpers, audits, browser page, and preserved document tools. |
| [sql/](sql/README.md) | Saved model-input query, database schemas, optional views, and checks. |
| [tests/](tests/README.md) | Verification and required local fixtures. |

Metro variables and finer-geography alternatives are covered in the
[data guide](docs/data_inventory_and_limitations.md#metro-variables-and-finer-geography-alternatives).
The standalone progress report has been absorbed there. Its reusable building and
QA code is preserved in [report authoring](scripts/README.md#optional-document-tools).

For current variable definitions, use the [data dictionary](metadata/data_dictionary.csv),
which contains 559 IDs. The generated Excel catalog is `metadata/data_catalog.xlsx`
in a local build.
[Source coverage](metadata/source_coverage.csv) and the
[dataset inventory](metadata/analytical_dataset_inventory.csv) describe the
maintained build. The local database's contents determine which data are available.

The [housing finance guide](docs/housing_finance_versions.md) compares Versions A/B/C.
Historical workbooks and previews from the former `outputs/` folder are retained
locally under `output/archive/legacy_reports/`. The six previously published
workbooks also remain in Git history. Their useful explanations are consolidated
in the data and finance guides; current inventories take precedence over those snapshots.
