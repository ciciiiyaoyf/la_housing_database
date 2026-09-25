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

The numbered walkthrough below is for **Windows**. You will use three windows:

| Window | What you do there |
|---|---|
| **Windows PowerShell** | Paste the setup and update commands shown below. |
| **File Explorer** | Find the downloaded project, place the database file, and double-click the launcher. Open it with **Windows key + E**. |
| **Your web browser**, such as Edge or Chrome | Read this guide, then use the local Data Browser's dropdowns and download buttons. |

A *command* is a line of text that tells the computer to do something. A
*terminal* is the window that accepts commands; this guide uses PowerShell.
*Cloning* means downloading a working copy of the project from GitHub.
You do not need to write Python code to use the Data Browser.

**How to enter a command:**

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

Enter these commands in **PowerShell**, not in GitHub's page, your browser's
address bar, a Python window showing `>>>`, or an Excel cell. If you accidentally
opened Python, type `exit()` and press Enter, then open PowerShell as above.
Opening PowerShell as administrator is not needed for this walkthrough.
Keep this guide open in your browser while following the steps.

For macOS/Linux, use the separate [Terminal commands](#macos-and-linux) below.

| Setup step | What you receive |
|---|---|
| Clone from GitHub | Programs, configuration, variable definitions, and reference guides. |
| Supply or build a database | Actual observations queried by the browser. |

**GitHub does not include `database/housing.duckdb` or the raw source archives.**
Cloning and installing Python alone will not provide housing observations.
Step 3 explains this requirement. You can inspect the published
[variable dictionary](metadata/data_dictionary.csv) immediately without setup.

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
by this project. Its name here is `.venv`. Set it up once for each new local copy.
Enter the next four commands **separately**, in order.

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
`catalog`, `observations`, and `table`. This confirms that the program starts;
it does not mean housing data has been installed. Continue to step 3.

The longer commands select Python from `.venv` directly. No separate environment
activation or PowerShell execution-policy change is needed. Keep this window open
for the database check in step 3.

## 3. Get a local database

To use the existing research dataset, obtain a **compatible, permitted database
snapshot** from the project maintainer. **No ready-made database download is
currently supplied by this repository.** Request its build date, coverage, and
access restrictions with the file. A public copy must exclude licensed HUD-USPS
detail; the maintainer's full research database is not automatically suitable for
redistribution.

Here, a *snapshot* means a database file containing the data available at a
particular build date. It is a separate file from the programs downloaded in step 1.
If the maintainer already set up this computer and `database/housing.duckdb` exists,
keep that file and go straight to the checks below.

### Place the supplied file using File Explorer

1. Press **Windows key + E** to open **File Explorer**. Open **Downloads**, then
   double-click **la_housing_database**, then **database**. If you cloned elsewhere,
   open that location instead.
2. Locate the file supplied by the maintainer. If it came in a ZIP archive,
   right-click the ZIP, choose **Extract All**, and locate the extracted `.duckdb`
   file. The file must be named exactly **`housing.duckdb`**.
3. Copy the supplied file into the project's **database** folder. The result is
   `la_housing_database\database\housing.duckdb`. Keep it directly in `database`,
   without an extra containing folder. If Windows asks to replace an existing file,
   preserve that existing database and confirm which snapshot you should use with
   the maintainer before replacing it.

### Check the database in PowerShell

Switch back to **PowerShell**, in the project folder. If necessary, use
[Return to the project folder](#return-to-the-project-folder-in-powershell).
First, paste this command and press Enter:

```powershell
Test-Path -LiteralPath '.\database\housing.duckdb'
```

Expect `True`. If it says `False`, check the folder and filename in File Explorer;
do not continue until this check says `True`. Next, run:

```powershell
.\.venv\Scripts\python.exe run_query.py catalog --variable zori_smoothed_sa
```

Expect comma-separated column headings followed by a definition row containing
`zori_smoothed_sa`, printed in PowerShell. This is normal; the command does not
open Excel or save a file. It checks that the program can read the variable catalog;
it does not establish complete
observation coverage. The browser preview in step 5 checks the data you selected.
If the command reports missing tables or metadata, give that error to the
maintainer and request a snapshot compatible with your project copy.

**To build from provider files instead**, follow
[Database build and refresh](docs/database_build_and_refresh.md). This is a
maintainer workflow with source archives, manual files, access requirements,
and validation; it is not a one-command installation of the full snapshot.
`--offline` needs existing archives, and the finance layers need frozen benchmark
inputs. A one-source download does not build a complete working database.

## 4. Open the Data Browser

Complete steps 1–3 first. On later visits, you can start here; you do not need to
reinstall the packages every time.

### Open it with the launcher

1. Open **File Explorer** with **Windows key + E**.
2. Open **Downloads → la_housing_database**, or the location where you cloned it.
3. Find **Open_Data_Browser.cmd** and double-click it there, on your computer.
   Windows may hide `.cmd` and show **Open_Data_Browser** as a Windows Command Script.
   Clicking its filename on GitHub or in a document preview does not run it.
4. A terminal window should open and stay open. A browser tab titled
   **LA Housing Data Browser** should appear, with the page heading
   **Find and download housing data**. Continue to step 5 in that browser tab.

### If double-clicking does not open the page

Open **Windows PowerShell** from Start and
[return to the project folder](#return-to-the-project-folder-in-powershell).
Paste this command into PowerShell and press Enter:

```powershell
.\.venv\Scripts\python.exe run_data_browser.py --open-browser
```

This starts the same browser program as double-clicking the launcher. If it fails,
the error remains visible in PowerShell so you can read it or copy it for help.
If it starts but no browser tab appears, copy the printed address, normally
`http://127.0.0.1:8765/`. Open Edge or Chrome, paste it into the **address bar at
the top**, and press Enter. This address refers to your own computer.

**Keep the terminal window open while using the page.** It is normal for this
command to keep running without returning to a `PS ... >` prompt. You can minimize
the window. When finished, close the browser tab, click inside the terminal that
started the program, and press **Ctrl+C** to stop it. Accept a batch-termination
prompt if the double-click launcher shows one. Next time, repeat step 4.

The page reads a local snapshot. Opening it does not fetch new provider releases
or update observations.

## 5. Download your first dataset

**Where to work:** in the **Data Browser tab** opened in step 4. The next actions
are clicks on that page; there are no PowerShell commands to enter.

Try this example to download one rent series for postal ZIP 90024. It requires
that your database contain the postal-ZIP housing panel.

1. Under **Select your data**, open the **Dataset** dropdown and choose
   **Postal ZIP housing — Monthly**. Wait for the filters to finish loading.
2. Open **Geography type** and choose **postal_zip**. Wait for the choices to update.
3. Open **Place** and choose **ZIP_90024**.
4. Open **Variable** and choose the Zillow Observed Rent Index entry ending in
   **`(zori_smoothed_sa)`**. For the whole dataset, keep **All variables**.
5. Open **Frequency** and choose **monthly**. Leave the other filters at their
   **All** choices.
6. Under **Date range**, optionally set **From** to January 1, 2020, using the date
   picker. Its displayed format depends on your browser. Leave **Through** blank
   for all later available dates. Both endpoints are included when provided.
7. Click **Preview data** and wait for rows to appear under **Data preview**.
   Check the place, dates, and columns; scroll sideways to see additional columns.
   The rent column is called `zori`; the definitions file links it to
   `zori_smoothed_sa`. Read any notes below the preview. If the page says no rows
   match, clear the date restriction or use **Reset filters** and try again.
8. Click **Download CSV** and wait for your browser to finish saving the file.
   Then click **Download variable definitions** to save the matching explanation.

The browser saves to its usual Downloads folder or asks you to choose a location.
In Edge or Chrome, press **Ctrl+J** to open the download list and use **Show in
folder** (or its folder icon) to locate the files. This example downloads
`zip-monthly.csv` and `zip-monthly.variables.csv`; repeated downloads may have a
number added by your browser. Keep both files from the same selection together.

A CSV is a plain-text table that Excel and other analysis programs can read.
The data CSV includes **all matching rows**, not just the 20 preview rows. The
definitions CSV gives variable IDs, units, temporal types, and descriptions.
Dates, geography, quality, and provenance fields remain in a single-variable
export. Missing values stay missing.

**All variables** includes the measures registered in the variable dictionary,
plus the dataset's retained context fields. Additional unregistered measure
columns are omitted and named in the preview notes; this is not a raw copy of
every column in the underlying database table.

For all available places, leave **Place** at **All places**. To start a different
selection, click **Reset filters**; this clears the filters and dates but keeps
the dataset. Changing **Dataset** also resets the earlier filters. CSV recipients
do not need to install this project.

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

Make these choices in the **same Data Browser page**. Choose **Dataset** first,
wait for its filters to load, and then select the location, variable, and other
filters you need. After each change, click **Preview data** again before downloading.

The Dataset menu lists supported products; seeing a name there does not establish
that its table or observations are installed. The filter choices are read from
your local database. A dictionary entry does not guarantee populated observations
for every dataset or place. If selecting a dataset reports a missing table, choose
another dataset or ask the maintainer for a compatible snapshot containing it.

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

For unit or temporal filtering, click the expandable **Units and temporal types**
heading below the main filters. This reveals **Simplified unit**, **Temporal type**,
and **Native frequency**. For example, selecting **flow** under Temporal type
keeps measures classified as flows; it does not calculate new values.

Filters use the [current variable dictionary](metadata/data_dictionary.csv) and
combine with **AND**: every selected condition must be satisfied. **All** leaves
that condition unrestricted. Proxy and Constructed are independent; an unknown
flag is not the same as No.

For example, to find LA County monthly construction proxies:

1. Choose **LA County housing — Monthly** in Dataset and wait for the filters.
2. Choose **county** in Geography type, then **COUNTY_06037** in Place.
3. Leave Variable at **All variables** and choose **monthly** in Frequency.
4. Choose **construction** in Domain and **General** in Layer.
5. Choose **Yes** in both Proxy variable and Constructed variable.
6. Click **Preview data**, review the rows and notes, then download both CSVs.

Postal ZIPs and Census ZCTAs are distinct. County selection does not fill missing
values from state or national data. Output frequency does not change source
meaning: Redfin ZIP windows overlap across three months; ACS five-year estimates
remain survey-period estimates. Stock/flow labels do not automatically resample
values.

For an empty preview, remove a filter or choose a compatible dataset. Some derived
General variables exist only in analysis tables; finance Version C retains multiple
information cutoffs. For **Source observations — General layer**, selecting all
variables may include licensed HUD-USPS records and be refused; choose a public
variable such as `zori_smoothed_sa` instead. Use
[Query and export commands](docs/query_and_export_commands.md)
for multiple selected places, saved commands, Parquet output, and SQL.

## 7. Open the CSV in Excel

**Where to work:** in the **desktop Excel application**, after the two files finish
downloading. The following menu names apply to recent Excel for Windows versions.

1. Open Excel and choose **Blank workbook**.
2. Click **Data** on the ribbon, then **From Text/CSV**. Some versions place it
   under **Get Data → From File → From Text/CSV**.
3. In the file picker, open the folder used for the download, select the data CSV
   (for the example, `zip-monthly.csv`), and click **Import**.
4. In the import preview, select **Do not detect data types** under **Data Type
   Detection**, if offered. Then choose **Transform Data** to open Power Query.
5. In Power Query's **Applied Steps** list on the right, remove an automatically
   added **Changed Type** step if present, using its **X**. This avoids retaining
   numeric conversions that have already removed leading zeros.
6. Find identifier columns such as `geo_id`, FIPS, postal ZIP, or ZCTA. Select each
   column, click the data-type icon beside its heading, and choose **Text**. If a
   replacement prompt appears, choose **Replace current**. Leave identifiers as
   text; set date or measurement columns to date/numeric types only as needed.
7. Click **Home → Close & Load**. The table should appear in the Excel workbook.
8. Repeat the import for `zip-monthly.variables.csv` to read the variable
   definitions. Keep both original CSVs; use **File → Save As** to save your Excel
   work separately as an `.xlsx` workbook.

This preserves leading zeros, such as FIPS `06037`. Double-clicking a CSV can
cause Excel to convert those identifiers to numbers.
See Microsoft's [CSV import instructions](https://support.microsoft.com/en-us/excel/import-data-from-data-sources-power-query)
and [type-detection options](https://learn.microsoft.com/en-us/power-query/connectors/text-csv)
for menu variations. If the file exceeds Excel's worksheet capacity, select fewer
places or a shorter date range in the Data Browser and download a smaller CSV.

## 8. Update an existing copy

Use this when you already have the project and want the latest programs and
documentation. You do not need to clone a second copy.

1. Stop the running Data Browser: click its terminal window and press **Ctrl+C**.
   Closing only the browser tab does not stop the program.
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

Wait for installation to finish without errors, then reopen the launcher using
step 4. **Updating code does not refresh housing observations.** To update the data,
obtain a newer compatible snapshot or follow the maintainer build workflow.

## Troubleshooting

| What you see | What to do |
|---|---|
| `git` or `py` is not recognized | Install the tool, reopen PowerShell, and repeat the version check. |
| `pymanager` is not recognized | This command requires the Python install manager. Complete its installation and reopen PowerShell; if Python 3.11 is already installed, use the version check in step 1 instead. |
| A prompt showing `>>>` | You opened Python. Enter `exit()` and press Enter, then open Windows PowerShell from Start. |
| A prompt showing `>>` after pasting | PowerShell is waiting for the rest of an incomplete command. Press Ctrl+C, then copy the complete command again, including both closing quotes and parentheses. |
| Cannot find `requirements.txt` or `run_query.py` | You are probably in the wrong folder. Follow [Return to the project folder](#return-to-the-project-folder-in-powershell), then retry the failed command. |
| Clone destination already exists | Use the existing project folder and step 8. |
| Python environment is missing | Run step 2 inside the project folder. |
| Package installation reports an error | Stop before the next step. Check the internet connection and Python version; give the complete error to the maintainer if retrying does not resolve it. |
| Database not found | Complete step 3; cloning alone does not provide the database. |
| Launcher closes / nothing appears | Run the Python launch command in step 4 to read the error. |
| Port already in use | Use the earlier launcher's page or stop that launcher before reopening. |
| Browser cannot reach the database | Keep the launcher running; reopen it if closed. |
| No matching rows or variables | Reset filters, check coverage, and widen the date range. |
| Restricted HUD-USPS download refused | Licensed detail is excluded from ordinary exports; select public measures. |

When asking for help, include the step number, the command you entered, and the
full error text. A screenshot can help for a browser or File Explorer problem.
Keep the original message so the maintainer can identify the cause.

## macOS and Linux

<details>
<summary>Open the Terminal walkthrough for macOS/Linux</summary>

Use this section instead of the Windows setup, launcher, and update commands.
The browser's filters and downloads work the same way as steps 5–6 above.

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

Use **Finder** on macOS or your Linux **Files** application to place the permitted
snapshot directly at `la_housing_database/database/housing.duckdb`, following
step 3's requirements. Back in Terminal, check it:

```bash
.venv/bin/python run_query.py catalog --variable zori_smoothed_sa
```

Expect the rent variable's metadata. Once that works, launch the browser from
the same Terminal:

```bash
.venv/bin/python run_data_browser.py --open-browser
```

Use the browser page described in steps 5–6. If it does not open, paste the printed
local address into the browser's address bar. Keep Terminal open while using the
page; press **Ctrl+C** there to stop the program. The Windows `.cmd` launcher is
not used on macOS/Linux.

For later updates, stop the browser, return to the project folder with `cd`, then
run `git status --short`. Continue only if it lists no local changes. Run these
two commands separately, stopping if either fails:

```bash
git pull --ff-only
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Launch the browser again after a successful update. This updates programs and
packages; a newer data snapshot must be obtained or built separately.

</details>

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
