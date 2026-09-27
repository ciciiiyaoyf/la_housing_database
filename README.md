# Los Angeles Housing Database

Use the local **DuckDB database** to explore Los Angeles housing data, retrieve
tables or individual series with SQL, and load query results directly into Python
for modeling and forecasting research. The database includes observations,
analytical panels, variable definitions, and provenance.

**Start here:** [clone](#1-install-the-tools-and-clone-the-project) →
[install the tools](#2-set-up-the-project) → [get the database](#3-get-a-local-database) →
[open DuckDB](#4-open-duckdb-and-run-your-first-query) →
[learn SQL](docs/duckdb_sql_guide.md) → [use results in Python](#6-use-query-results-for-modeling).
Already set up? Start at step 4 to explore the database, or
[refresh the data before modeling](#refresh-the-data-before-modeling).

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

The numbered walkthrough below is for **Windows**. You will use these interfaces:

| Window | What you do there |
|---|---|
| **Windows PowerShell** (`PS ... >`) | Paste setup commands and start DuckDB or Python programs. |
| **DuckDB** (`D` prompt, in the same terminal window) | Type SQL to inspect tables and retrieve data after step 4. |
| **File Explorer** | Find the downloaded project and place the database file. Open it with **Windows key + E**. |
| **Your web browser**, such as Edge or Chrome | Read this guide; use the separate CSV browser only if you want downloads. |

A *command* is a line of text that tells the computer to do something. A
*terminal* is the window that accepts commands; this guide uses PowerShell.
*Cloning* means downloading a working copy of the project from GitHub.
SQL is the language used to ask DuckDB for data. This guide explains where to
enter it and includes queries you can copy before adapting them.

**How to enter a PowerShell command:**

1. Click the Windows **Start** button, type **Windows PowerShell**, and open it.
   A window or terminal tab opens with a line starting with `PS` and ending in `>`.
   This line is the *prompt*: it means PowerShell is ready for a command.
2. Copy **one command line** from a code box in this guide. Click after the `>`
   in PowerShell, paste with **Ctrl+V** (or right-click), and press **Enter**.
   Paste only the command, without the surrounding explanation or code-box label.
   Do not combine several commands into one line.
3. Wait until the `PS ... >` prompt returns before entering the next command.
   Installation can print many lines and take several minutes. If it reports an
   error, stop at that step and use [Troubleshooting](#troubleshooting).

**Already have a terminal open?** A prompt such as
`C:\Users\yourname\Downloads\la_housing_database>` without the initial `PS`
is **Command Prompt**, a different program. In that window, type `powershell`
and press Enter. Wait for a prompt starting with `PS`; then use this guide's
PowerShell commands. The folder stays the same. If you are already inside
`la_housing_database`, you do not need to enter a second folder with that name.

Enter the `powershell` commands in **PowerShell**, not in GitHub's page, your browser's
address bar, a Python window showing `>>>`, or an Excel cell. If you accidentally
opened Python, type `exit()` and press Enter, then open PowerShell as above.
Opening PowerShell as administrator is not needed for this walkthrough.
Keep this guide open in your browser while following the steps. After starting
DuckDB in step 4, enter `sql` blocks at its `D` prompt instead; the guide marks
that change explicitly.

For macOS/Linux, use the separate [Terminal commands](#macos-and-linux) below.

| Setup step | What you receive |
|---|---|
| Clone from GitHub | Programs, configuration, variable definitions, and reference guides. |
| Supply or build a database | Actual observations queried directly in DuckDB. |

**The Git clone does not include `database/housing.duckdb` or raw source archives.**
Cloning and installing Python alone will not provide housing observations. The
[collaboration workflow](#collaborate-through-database-snapshots) distributes the
database separately through private GitHub Releases after the first publisher
release. Step 3 explains installation. The published
[variable dictionary](metadata/data_dictionary.csv) is available immediately without setup.

## 1. Install the tools and clone the project

### Install the tools

Git downloads and updates the project. Python runs its programs. If Git and
Python 3.11 are already installed, go to the version checks below.

1. In your web browser, open [Git for Windows](https://git-scm.com/downloads/win),
   download the installer for your computer, open the downloaded file, and complete
   the installer. The default choices are sufficient for this walkthrough.
2. Open [Python for Windows](https://www.python.org/downloads/windows/). For a new
   installation, choose the **Python install manager**, open the downloaded file,
   and complete installation. Close any existing PowerShell window and open a new
   one using the Start menu.
3. **Only if you just installed the Python install manager**, enter this command
   in the new PowerShell window to install the version used in this guide:

```powershell
pymanager install 3.11
```

Wait for installation to finish. The manager is described in the official
[Python Windows installation guide](https://docs.python.org/3/using/windows.html#python-install-manager).
The project supports Python **3.11 or newer**; these examples explicitly use 3.11.

### Check the tools

In **PowerShell**, enter the following commands separately, pressing Enter after
each. The current folder does not matter for these two checks.

```powershell
git --version
```

Expect a line starting with `git version`. Then run:

```powershell
py -3.11 --version
```

Expect `Python 3.11` followed by a patch version. If either command fails, complete
its installation before continuing. If you already use another supported Python
version, replace `-3.11` consistently in this guide with that version, for example
`-3.12`; first verify that `py -3.12 --version` works.

### Download the project

Keep using the **same PowerShell window**. First, move into your Downloads folder:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads')
```

`$HOME` means your Windows user folder; leave it exactly as written. This command
changes the folder where subsequent commands run. The prompt should now end in
`\Downloads>`. Next, download the project:

```powershell
git clone https://github.com/ciciiiyaoyf/la_housing_database.git
```

Wait for the download to finish and the PowerShell prompt to return. GitHub may
open a sign-in window; sign in with the account granted access to this repository.
A “repository not found” message can mean your account lacks permission. Ask the
project owner for access before retrying. GitHub's
[cloning guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
and [sign-in guidance](https://docs.github.com/en/get-started/git-basics/caching-your-github-credentials-in-git)
explain that process.

After a successful download, enter the new project folder:

```powershell
Set-Location -LiteralPath '.\la_housing_database'
```

The prompt should end in `\Downloads\la_housing_database>`. This is the
**project folder** used throughout this guide. In File Explorer, you can find
the same folder by opening **Downloads → la_housing_database**.

If the folder already exists, use that copy instead of repeating `git clone`.
Return to it as described below, then continue setup or follow
[step 8](#8-update-an-existing-copy) to update it.

### Return to the project folder in PowerShell

**Use this whenever you reopen PowerShell after cloning.** Opening File Explorer
does not change PowerShell's current folder. For the Downloads location used above,
paste this into PowerShell and press Enter:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
```

If you saved the project somewhere else, open that folder in File Explorer,
click its address bar, and copy its full path. In PowerShell, type
`Set-Location -LiteralPath '`, paste the path, type the closing `'`, and press
Enter. Keep the single quotes, especially if the path contains spaces.

To check your location, enter:

```powershell
Get-Location
```

The result must be your `la_housing_database` folder. Then enter:

```powershell
Get-ChildItem -Name
```

You should see `README.md`, `requirements.txt`, and `Open_Data_Browser.cmd`
among the names. If they are missing, correct your location before step 2.
Keep this PowerShell window open.

## 2. Set up the project

**Where to type:** in the **PowerShell window from step 1**, with its prompt ending
in `\la_housing_database>`. If that window is closed, follow
[Return to the project folder](#return-to-the-project-folder-in-powershell) first.

A Python *environment* is a folder containing the Python program and packages used
by this project, including DuckDB's Python library and its separate SQL command-line
client. Its name here is `.venv`. Set it up once for each new local copy.
Follow the next four steps in order, entering each command **separately**.

**A. Create the environment.** Copy this one line into PowerShell and press Enter:

```powershell
py -3.11 -m venv .venv
```

Wait for the prompt to return; this command may finish without printing a message.
It creates `.venv` inside the project folder. Do not open or edit files inside it.

**B. Update the package installer.** In the same PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Wait for it to finish without an error. “Successfully installed” or
“Requirement already satisfied” messages are normal.

**C. Install the project's packages.** Still in the same window, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This step needs an internet connection and can take several minutes. It reads
`requirements.txt`; you do not need to open that file or install its entries by
hand. Wait for installation to finish before continuing.

**D. Check that setup worked.** Run:

```powershell
.\.venv\Scripts\python.exe run_query.py --help
```

You should see help text starting with `usage:` and listing commands such as
`catalog`, `observations`, and `table`. This checks the project's Python packages.
Also check the DuckDB SQL program in the same PowerShell window:

```powershell
.\.venv\Scripts\duckdb.exe --version
```

Expect a DuckDB version number. `requirements.txt` installs the official
`duckdb-cli` package as well as the Python library; installing `duckdb` alone does
not provide this executable. Neither check installs housing observations.
Continue to step 3.

The longer commands select Python from `.venv` directly. No separate environment
activation or PowerShell execution-policy change is needed. Keep this window open
for the database check in step 3.

## 3. Get a local database

The project uses **database snapshots**: complete database files published after
validation. No snapshot is bundled with the Git clone. A publisher must create
the first private release before automatic installation can succeed; adding the
snapshot tools alone does not publish any data. Raw source archives are not
included in a database release.

### Install a published snapshot

After steps 1–2, a collaborator completes the
[one-time GitHub CLI setup](#one-time-setup-for-publishers-and-collaborators). From
the project folder in **PowerShell**, the collaborator runs:

```powershell
.\.venv\Scripts\python.exe run_snapshot.py update
```

The command installs the newest published `housing-db-` release at
`database/housing.duckdb`, verifies its checksum and database structure, and
requires the clean local Git revision to match the release's recorded revision.
The [collaboration workflow](#collaborate-through-database-snapshots) explains
later updates, local edits, and publisher responsibilities. macOS/Linux commands
appear in the [Terminal walkthrough](#macos-and-linux).

An existing database is preserved when its origin is unknown or its contents have
changed locally. The tool does not overwrite that work. The
[existing-database instructions](#existing-databases-and-local-changes) explain the
next step. A missing release requires the publisher to complete a first release;
recloning will not create one.

### Manually supplied snapshot alternative

A maintainer may instead supply a compatible permitted database file. Its build
information, coverage, and access restrictions must accompany it. The full
research database may contain licensed HUD-USPS detail; private repository access
does not itself confer redistribution permission. An existing manually supplied
database can continue to use the direct DuckDB commands below without enrolling
in automatic updates.

### Place the supplied file using File Explorer

1. The collaborator opens **File Explorer** with **Windows key + E**, then opens
   **Downloads → la_housing_database → database**, or the actual clone location.
2. The collaborator locates the supplied file. For a ZIP archive, **Extract All**
   exposes the actual `.duckdb` file. Its name must be exactly **`housing.duckdb`**.
3. The collaborator copies the supplied file into the project's **database** folder. The result is
   `la_housing_database\database\housing.duckdb`. The file stays directly in `database`,
   without an extra containing folder. Any existing database must be preserved
   before an intentional replacement; the maintainer identifies the correct snapshot.

### Check the database in PowerShell

Switch back to **PowerShell**, in the project folder. If necessary, use
[Return to the project folder](#return-to-the-project-folder-in-powershell).
First, paste this command and press Enter:

```powershell
Test-Path -LiteralPath '.\database\housing.duckdb'
```

Expect `True`. If it says `False`, check the folder and filename in File Explorer;
do not continue until this check says `True`. Step 4 opens this exact database,
and step 5 shows how to inspect its contents. If queries report missing tables or
metadata, give that error to the maintainer and request a compatible snapshot.

**To build from provider files instead**, follow
[Database build and refresh](docs/database_build_and_refresh.md). This is a
maintainer workflow with source archives, manual files, access requirements,
and validation; it is not a one-command installation of the full snapshot.
`--offline` needs existing archives, and the finance layers need frozen benchmark
inputs. A one-source download does not build a complete working database.

## 4. Open DuckDB and run your first query

**Where to start:** in **Windows PowerShell**, in the project folder. If you
closed it, follow [Return to the project folder](#return-to-the-project-folder-in-powershell).
Complete steps 1–3 first. For an older checkout, rerun step 2's package-install
command once to add the DuckDB command-line client.

Paste this **PowerShell command** and press Enter:

```powershell
.\.venv\Scripts\duckdb.exe -readonly .\database\housing.duckdb
```

DuckDB opens inside that window and displays a **`D` prompt**. You are now talking
to DuckDB, so enter SQL rather than PowerShell commands. `-readonly` lets you
inspect the supplied database without altering its tables. A missing file causes
an error rather than creating an empty database.

At the **DuckDB `D` prompt**, copy this entire SQL statement, including the final
semicolon, and press Enter:

```sql
SELECT current_database() AS database_name;
```

Expect a small table containing `housing`. Now paste this whole SQL block at `D`:

```sql
SELECT date::DATE AS month, county_fips, zori
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
ORDER BY date DESC
LIMIT 10;
```

Expect up to ten LA County rows, with the most recent month first. `zori` is the
Zillow Observed Rent Index in USD/month; a blank/null cell means the value is
missing. The panel's `zori` column corresponds to variable ID `zori_smoothed_sa`.
Your results depend on the installed snapshot. This query displays data directly
from DuckDB; it does not create a CSV.

SQL can span several lines. DuckDB waits until you finish the statement with `;`.
After the result, another `D` prompt means it is ready for the next query. If a
paste is incomplete, press **Ctrl+C** to cancel it and paste the complete query.
Do not paste another launch command at `D`.

When you want to return to PowerShell, enter this **DuckDB command**, with no
semicolon, and press Enter:

```text
.quit
```

Continue with the full [DuckDB SQL guide](docs/duckdb_sql_guide.md) to explore the
database, choose variables, check data quality, save queries, and use Python.
It also explains [DuckDB's graphical table viewer and SQL editor](docs/duckdb_sql_guide.md#10-view-tables-in-duckdbs-graphical-interface).

## 5. Find tables, datasets, and variables

Reopen DuckDB using step 4 if you have exited it. At the **DuckDB `D` prompt**,
first enter this display command and press Enter, **without a semicolon**:

```text
.mode table
```

This prints the complete result instead of DuckDB's shortened preview. Now paste
the whole SQL block below, including its final semicolon, and press Enter:

```sql
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_catalog = current_database()
ORDER BY table_schema, table_name;
```

This lists **every table and view in every schema of the open database**. Scroll
up in the terminal to see the beginning of the list; the output can extend beyond
one screen. Maximize the window if long names wrap. After reading it, restore the
usual compact display by entering this at **`D`**, without a semicolon:

```text
.mode duckbox
```

A *schema* is a group of related
tables; `analytics.county_monthly_panel` means the `county_monthly_panel` table
inside the `analytics` schema. A *view* is a saved query presented like a table.
A table's presence does not guarantee non-missing values for every variable/date.

The [DuckDB SQL guide](docs/duckdb_sql_guide.md) is the main usage tutorial. Follow it
in order to learn how to:

1. Inspect column names, data types, sample rows, and variable definitions.
2. Select geography, frequency, Domain, Layer, Proxy, Constructed, units, and temporal type.
3. Retrieve a whole dataset or one series, with explicit date and location filters.
4. Check missingness and duplicate keys before joining tables or modeling.
5. Reuse saved SQL and load its results directly into a Python DataFrame.

Read the [data inventory](docs/data_inventory_and_limitations.md) when choosing a
product, and its source/finance methods before interpreting modeled values.

## 6. Use query results for modeling

### Refresh the data before modeling

**For a whole-project update before modeling, use `run_system_refresh.py`.**
It refreshes the configured general sources and tables, rebuilds finance B,
builds or validates the finance C cutoff, and validates the integrated database.
`run_pipeline.py --stage refresh` is the narrower choice for general data only;
it does not recompute finance B/C. Choose one workflow, rather than running both.

The whole-project command requires a **fully provisioned research workspace**:
installed packages, existing source archives, permitted
source access, and the frozen finance A files. A supplied DuckDB snapshot by itself
does not include these prerequisites; follow the
[complete refresh guide](docs/database_build_and_refresh.md#refresh-everything-before-modeling)
if you are setting up acquisition for the first time.

First exit **every DuckDB session** with `.quit`, including the terminal running
DuckDB's graphical interface. Stop the CSV Data Browser with **Ctrl+C** in its
terminal, and close Python connections to the database. Closing a web-browser tab
alone does not stop either server.

**Where to type:** in **Windows PowerShell** or **macOS Terminal**, inside the
`la_housing_database` project folder. On Windows, use
[Return to the project folder](#return-to-the-project-folder-in-powershell) if needed;
on macOS, use the [Terminal walkthrough](#macos-and-linux). Run only the command
for your operating system, then wait for the prompt to return.

**Windows PowerShell — whole-project update:**

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py
```

**macOS Terminal — whole-project update:**

```bash
.venv/bin/python run_system_refresh.py
```

The default finance C information cutoff is **today's UTC date**. To choose a
specific cutoff, follow the guide's `--information-cutoff` instructions. Existing
C vintages and frozen A remain preserved. Fixed source editions are checked within
their configured scope; this command does not silently replace them with newer
products or make unavailable manual inputs current.

Immediately after the command finishes, check its exit code in the same window.

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
refresh, reopen it and use the new page, or reload an existing tab, to load current
dataset and filter choices. Previews and CSV downloads read the database at the
time you request them; there is no separate browser update command. Previously
downloaded CSVs remain saved copies. Follow the
[Windows/macOS reopening steps](docs/database_build_and_refresh.md#use-the-refreshed-data-in-the-csv-browser)
to download updated data.

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

If you deliberately want **only the general sources and tables**, use the
following alternative. It includes cleaning, rebuilding, validation, and exports,
but leaves finance B/C unrecomputed. Do not run it before or after the
whole-project command as an additional setup step.

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

### Load model inputs from DuckDB

You can move data directly from DuckDB into Python, without downloading a CSV.
The repository includes a small, reproducible example:

- [sql/model_input.sql](sql/model_input.sql) selects LA County monthly rent data.
- [scripts/load_model_data.py](scripts/load_model_data.py) runs that query against
  the local database and returns a pandas DataFrame, a table in Python memory.

**Where to run it:** in **PowerShell**, in the project folder. If the window shows
DuckDB's `D` prompt, enter `.quit` first. Then run:

```powershell
.\.venv\Scripts\python.exe scripts/load_model_data.py
```

The program prints the result's size and a preview; it does not fit a forecasting
model, fill missing values, or modify the database. The
[modeling section of the SQL guide](docs/duckdb_sql_guide.md#8-use-the-query-result-in-python)
explains how to use the function in your own analysis and change the saved query.

For forecasting, historical observation dates alone do not establish what was
known at a past forecast date. Provider revisions, release dates, finance cutoffs,
missing months, and model-training periods matter. Read the
[forecasting guidance](docs/duckdb_sql_guide.md#9-prepare-for-forecasting-without-using-future-information)
before creating a training sample.

## 7. CSV downloads as an alternative

If you prefer to inspect data in Excel or share permitted tables as files, use the
[step-by-step CSV browser tutorial](docs/query_and_export_commands.md#browser-downloads).
It explains where to double-click `Open_Data_Browser.cmd`, how to choose a whole
dataset or selected variables, where the files are saved, and how to import them into
Excel without losing leading zeros. The same guide covers saved command-line
exports and their companion variable definitions.

This is an alternative interface to the local database. SQL queries and Python
modeling do not require a CSV export first. The browser does not fetch new provider
releases or install a missing database.

## 8. Update an existing copy

Use this when you already have the project and want the latest programs and
documentation. You do not need to clone a second copy.

1. Close any DuckDB SQL session with `.quit`. If you also have the CSV browser
   running, click its terminal window and press **Ctrl+C**; closing only its browser
   tab does not stop the program. Close Python sessions connected to the database.
2. Open **Windows PowerShell** from Start and
   [return to the project folder](#return-to-the-project-folder-in-powershell).
3. In that PowerShell window, check for local file changes:

```powershell
git status --short
```

If it prints nothing and returns to the prompt, there are no local Git changes.
If it lists files, stop and ask the maintainer to help preserve and reconcile those
changes before updating. Local databases and generated exports are excluded from
Git, so they normally do not appear in this list.

4. If that check is clear, download the latest project changes:

```powershell
git pull --ff-only
```

Wait for a successful update summary or `Already up to date.` If Git refuses the
update, stop and retain its error message for the maintainer; do not discard local
work to force it through.

5. After a successful update, update the installed packages in the same window:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

After package installation succeeds, **`git pull` has updated code and
documentation, but has not refreshed housing observations**. A collaborator using
published releases installs the matching snapshot with:

```powershell
.\.venv\Scripts\python.exe run_snapshot.py update
```

A provisioned publisher workspace instead follows
[Refresh the data before modeling](#refresh-the-data-before-modeling) before
publishing a completed build. A manually supplied database can continue through
the manual file handoff. Database sessions reopen only after the chosen update
finishes. The workflow below distinguishes these roles.

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

The repository owner keeps the configured repository private and confirms that
every repository reader is authorized to receive the full database, including any
licensed detail. [config/database_sharing.yaml](config/database_sharing.yaml)
selects the repository, release prefix, and local paths. Publication refuses a
public repository. The sharing acknowledgement confirms the operator's audience
review; it does not grant data rights or sanitize the database.

Both roles complete the Python setup above and install **GitHub CLI** in addition
to Git. On **Windows PowerShell**, the installation command is:

```powershell
winget install --id GitHub.cli --exact
```

On **macOS Terminal**, with Homebrew installed:

```bash
brew install gh
```

Linux installations and alternatives are listed in the official
[GitHub CLI installation guide](https://github.com/cli/cli#installation). A newly
opened terminal then runs these commands, separately, on either platform:

```text
gh --version
gh auth login
gh auth status
```

Each person signs in to GitHub.com using that person's own account with repository
access; the publisher additionally needs release-writing permission. Browser
authentication is supported by [GitHub CLI](https://cli.github.com/manual/gh_auth_login).
Credentials remain in the CLI's credential storage, outside project files.

### Publisher: first release and every later update

1. The publisher integrates reviewed code contributions and builds the intended
   database using the [build and refresh guide](docs/database_build_and_refresh.md).
   The command depends on the change and available source inputs. The existing
   Redfin county refresh hold remains in force; a blocked all-source refresh is
   not a successful complete refresh. A release of an existing validated snapshot
   must describe that snapshot's actual coverage rather than claim new ingestion.
2. The publisher closes database applications and runs the verification commands
   below **before the final review and commit**. Pipeline validation regenerates
   tracked `metadata/source_coverage.csv` and may update the database's QA tables.
   The resulting metadata changes belong in the review; they must not be reverted
   merely to obtain a clean checkout.
3. The publisher reviews and commits the relevant code, configuration, and
   metadata changes, then pushes the commit to the configured repository. The
   working tree must be clean. Unrelated local work must be preserved and resolved
   before publishing. The publisher closes DuckDB, Python connections, and the
   Data Browser before packaging.
4. From the project folder, the publisher runs the platform's publish command below.
   `housing-db-20260927-01` is an example; each release needs a new tag using the
   current date and an incremented suffix for additional releases that day.

**Verification before the final commit — Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
git status --short
```

**Verification before the final commit — macOS/Linux Terminal:**

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python run_pipeline.py --stage validate
git status --short
```

Each command runs separately and the publisher stops on any failed check. After
reviewing, committing, and pushing the changes, the publisher proceeds with the
appropriate command below.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe run_snapshot.py publish --tag housing-db-20260927-01 --acknowledge-authorized-sharing
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python run_snapshot.py publish --tag housing-db-20260927-01 --acknowledge-authorized-sharing
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
acknowledgement with `prepare --tag housing-db-20260927-01`. This still runs the
validation gates and requires a clean committed checkout. Preparation alone does
not make a release available to collaborators. It retains a package for that tag;
a later publish command uses a new unused tag rather than overwriting the prepared
package, for example a suffix of `-02` after a preparation ending in `-01`.

### Collaborator: first download and every later update

1. The collaborator closes all sessions using `database/housing.duckdb`, including
   Python, DuckDB, and the Data Browser. Local code work is saved, committed, and
   reconciled through Git before updating; generated database edits are handled
   separately as described below.
2. From the project folder, the collaborator checks `git status --short`. Once the
   working tree is clean, the collaborator runs `git pull --ff-only`, then the
   platform's package-install command from step 2. A divergent branch requires
   normal Git reconciliation rather than discarding local work. The snapshot
   installer never changes Git branches or discards files to force a match.
3. The collaborator runs one of the following commands. The first run downloads
   the published database; later runs reuse the local file when its checksum is
   unchanged. `open` checks for a new
   snapshot, installs it when needed, and starts DuckDB in the same terminal.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe run_snapshot.py open
```

**macOS/Linux Terminal:**

```bash
.venv/bin/python run_snapshot.py open
```

At DuckDB's `D` prompt, SQL statements run as in step 4; `.quit` returns to the
terminal. The default local session permits edits. `open --read-only` is an
optional viewing mode that prevents accidental local changes; it does not alter
GitHub collaborator permissions. Direct local changes stay on that computer and
cause later replacement to be refused. Durable shared changes belong in reviewed
Python or SQL scripts so a publisher can reproduce them in the next build.
The `open` command needs the DuckDB CLI included in `requirements.txt`. If it is
missing, the collaborator reruns the platform's package-install command; the
snapshot command stops before installation rather than starting without a client.

Replacing `open` with `update` installs the snapshot without launching DuckDB.
`check` reads the remote manifest without downloading or installing the database;
it reports the required code revision even when local code does not match.
`status` reports local installation state without contacting GitHub. An optional
`--tag housing-db-20260927-01` on `check`, `update`, or `open` selects a particular
release. The clean local Git commit must equal that release's
`validated_with_code_commit`; a newer unrelated code revision is not automatically
compatible. A mismatch reports the needed revision. A collaborator retaining an
older release can use a separate checkout at its recorded commit after preserving
local work. If the main branch has advanced since the most recent snapshot, a
successful `git pull` alone may still leave a mismatch. The publisher can publish
a snapshot validated with the new code; otherwise the collaborator selects the
existing release's code. For example, a separate Git worktree avoids switching or
discarding the working branch:

```text
git fetch origin --tags
git worktree add --detach ../la_housing_database_snapshot housing-db-20260927-01
cd ../la_housing_database_snapshot
```

The example tag must be replaced by the tag reported by `check`, and the new
folder name must be unused. The collaborator then enters that folder, repeats
the Python environment and package setup there, and runs `run_snapshot.py open`
with that environment's Python. This is an optional compatibility checkout, not
a new download folder created on every snapshot update. Normal contributions
continue in the collaborator's working branch; a detached checkout is for using
the matching published snapshot.

There is no background scheduler. An update check happens when `check`, `update`,
or `open` runs. `open` checks before starting the session; it does not interrupt an
existing session. A collaborator who launches DuckDB directly continues using the
currently installed file until an update command runs.

### Contributing changes to the shared database

1. The collaborator makes a Git branch for the proposed change and keeps database
   experiments in a separate local copy. Reusable SQL belongs in `sql/`; Python
   changes belong in the appropriate existing module under `src/`.
2. The collaborator commits the scripts and any required tests, variable dictionary,
   and transformation-log updates, then pushes the branch and opens a pull request.
   Changes typed only into a DuckDB session are not captured by Git or the updater.
3. The publisher reviews and integrates the contribution, runs the relevant build
   with the required source inputs, validates the result, and publishes a new tag.
4. Collaborators obtain the integrated code and run `open` or `update` to receive
   the resulting shared database. Independent database files are never merged.

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

| What you see | What to do |
|---|---|
| `'Set-Location'`, `'Get-Location'`, `'Test-Path'`, or `'$HousingPython'` is not recognized as a command | You are using Command Prompt. Enter `powershell`, press Enter, and confirm the prompt starts with `PS` before retrying the guide's PowerShell commands. |
| `git` or `py` is not recognized | Install the tool, reopen PowerShell, and repeat the version check. |
| `pymanager` is not recognized | This command requires the Python install manager. Complete its installation and reopen PowerShell; if Python 3.11 is already installed, use the version check in step 1 instead. |
| A prompt showing `>>>` | You opened Python. Enter `exit()` and press Enter, then open Windows PowerShell from Start. |
| A prompt showing `>>` after pasting | PowerShell is waiting for the rest of an incomplete command. Press Ctrl+C, then copy the complete command again, including both closing quotes and parentheses. |
| Cannot find `requirements.txt` or `run_query.py` | You are probably in the wrong folder. Follow [Return to the project folder](#return-to-the-project-folder-in-powershell), then retry the failed command. |
| Clone destination already exists | Use the existing project folder and step 8. |
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
| No published database snapshot found | The publisher must complete the first `run_snapshot.py publish` release. A Git clone alone does not include the database. |
| Snapshot code revision mismatch | The collaborator preserves local code work, updates Git, and uses the exact clean revision recorded in the selected release. No forced reset is required. |
| Snapshot refuses an unmanaged or locally changed database | The local file is preserved. The collaborator follows the existing-database instructions before intentionally switching, or keeps using that working copy. |
| Snapshot reports an active database or WAL | Applications close normally before retrying. A remaining WAL is retained with its database for recovery, never deleted to bypass the check. |

When asking for help, include the step number, the command you entered, and the
full error text. A screenshot can help for a browser or File Explorer problem.
Keep the original message so the maintainer can identify the cause.

## macOS and Linux

<details>
<summary>Open the Terminal walkthrough for macOS/Linux</summary>

Use this section instead of the Windows setup and update commands. The SQL
statements in step 4 and the DuckDB SQL guide are the same on these platforms.

**Where to type:** on macOS, press **Command+Space**, type **Terminal**, and press
Return. On Linux, open **Terminal** from your applications menu. Use that window
for the commands below, one line at a time, pressing Return/Enter and waiting for
the prompt after each. Do not enter the Windows `Set-Location`, `py`, or `.exe`
commands in this Terminal.

Install Git and Python 3.11 or newer using the official
[Git downloads](https://git-scm.com/downloads) and
[Python downloads](https://www.python.org/downloads/), or your Linux distribution's
package manager. Check them in Terminal:

```bash
git --version
```

```bash
python3 --version
```

Continue only if both work and the second shows Python 3.11 or newer. Some Linux
distributions provide the Python `venv` component separately; install that
component using the distribution's package manager if environment creation below
reports that it is missing.

For a new copy, move into Downloads, clone the project, then enter its folder.
Before cloning this private repository, configure **Git Credential Manager** or
**GitHub CLI** by following [GitHub's sign-in setup for your platform](https://docs.github.com/en/get-started/git-basics/caching-your-github-credentials-in-git).
Git alone on macOS/Linux may not include a browser sign-in helper. Use the account
granted repository access. Then run these three commands separately:

```bash
cd "$HOME/Downloads"
```

```bash
git clone https://github.com/ciciiiyaoyf/la_housing_database.git
```

```bash
cd la_housing_database
```

For an existing copy or a newly opened Terminal, enter this instead of cloning
again. Replace the path if you saved the project somewhere else:

```bash
cd "$HOME/Downloads/la_housing_database"
```

Type `pwd` and press Enter to check the current folder. Type `ls` and press Enter;
you should see `README.md`, `requirements.txt`, and `run_query.py`. In this project
folder, run each setup command separately:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/python -m pip install --upgrade pip
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

```bash
.venv/bin/python run_query.py --help
```

Environment creation may be silent; package installation prints progress. Wait
for completion without errors. The last command should show query-tool help.

For published releases, the collaborator completes the
[one-time GitHub CLI setup](#one-time-setup-for-publishers-and-collaborators), then
runs the following from the project folder in **Terminal**:

```bash
.venv/bin/python run_snapshot.py update
```

The publisher must have created the first release, and the collaborator's clean
code revision must match it. The file is installed directly at
`database/housing.duckdb`. The
[collaboration workflow](#collaborate-through-database-snapshots) covers later
updates and preservation of local edits. For the manual alternative, the
collaborator uses **Finder** or the Linux **Files** application to place a permitted
snapshot at that same path, following step 3's requirements.

The catalog check in Terminal is:

```bash
.venv/bin/python run_query.py catalog --variable zori_smoothed_sa
```

Expect the rent variable's metadata. Once that works, open DuckDB from the same
Terminal:

```bash
.venv/bin/duckdb -readonly database/housing.duckdb
```

At DuckDB's `D` prompt, use the SQL in step 4 and the
[DuckDB SQL guide](docs/duckdb_sql_guide.md). Enter `.quit` to return to Terminal.
Before modeling on a provisioned workspace, close all database connections and
follow [Refresh the data before modeling](#refresh-the-data-before-modeling).
That section provides paired Windows/macOS commands, recommends the whole-project
workflow, and explains exit codes and the current county-source hold. Use its
**macOS Terminal** blocks here. See the
[dataset update schedule](docs/database_build_and_refresh.md#dataset-update-schedule)
for the full list and source-specific actions. Once you have reviewed the refresh
result, run the modeling example:

```bash
.venv/bin/python scripts/load_model_data.py
```

The CSV browser is an alternative described in the
[CSV guide](docs/query_and_export_commands.md#browser-downloads). Its Windows
`.cmd` launcher is not used on macOS/Linux; that guide supplies a Python command.

For later updates, exit DuckDB with `.quit`, return to the project folder with `cd`, then
run `git status --short`. Continue only if it lists no local changes. Run these
two commands separately, stopping if any fails:

```bash
git pull --ff-only
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

These commands update programs and packages. A collaborator using private
database releases then runs:

```bash
.venv/bin/python run_snapshot.py open
```

This checks for and installs the matching snapshot before starting a local
writable DuckDB session. `open --read-only` is available for viewing, and `update`
installs without opening a session. A provisioned publisher instead follows the
documented refresh/build steps, then publishes the validated result. A manually
supplied file can continue through the manual handoff alternative.

</details>

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
maintained build. Check your local database for the data you actually have.

The [housing finance guide](docs/housing_finance_versions.md) compares Versions A/B/C.
Historical workbooks and previews from the former `outputs/` folder are retained
locally under `output/archive/legacy_reports/`. The six previously published
workbooks also remain in Git history. Their useful explanations are consolidated
in the data and finance guides; current inventories take precedence over those snapshots.
