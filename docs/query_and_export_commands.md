# CSV downloads and metadata exports

This guide provides two alternatives for downloading CSVs and variable definitions:
[browser controls](#browser-downloads) or [saved export commands](#1-open-the-project-in-powershell).
For interactive SQL queries, start with the [DuckDB SQL guide](duckdb_sql_guide.md).
Complete the [README setup](../README.md#1-install-the-tools-and-clone-the-project),
including [a compatible local database](../README.md#3-get-a-local-database), first.
**GitHub does not include the database or raw source archives, and no ready-made
snapshot download is supplied.** Obtain a permitted compatible snapshot from the
maintainer, or follow the separate [build guide](database_build_and_refresh.md).
Offline operation needs data already on your computer; opening the browser or
running an export does not fetch new provider releases.

Filters use the [current variable dictionary](../metadata/data_dictionary.csv),
originally aligned with the **Variable Inventory** sheet of the August 24 overview
workbook. The historical workbook contains 482 variable IDs. The current dictionary
contains 559, including all 482 workbook IDs. For those shared IDs, Domain, Layer,
Proxy, Constructed, Geography, Native frequency, and Final frequency agree with the
workbook. The same filters also work for the 77 newer variables. A definition does
not guarantee observations in every snapshot, dataset, place, or date range.

## Browser downloads

Choose this route for dropdowns, variable checkboxes, and CSV download buttons. The following Windows
steps start after the README setup; the browser runs on your own computer.

### Open the Data Browser

Complete [README setup steps 1–3](../README.md#1-install-the-tools-and-clone-the-project)
first. On later visits, you can start here; you do not need to reinstall the
packages every time.

#### Open it with the launcher

1. Open **File Explorer** with **Windows key + E**.
2. Open **Downloads → la_housing_database**, or the location where you cloned it.
3. Find **Open_Data_Browser.cmd** and double-click it there, on your computer.
   Windows may hide `.cmd` and show **Open_Data_Browser** as a Windows Command Script.
   Clicking its filename on GitHub or in a document preview does not run it.
4. A terminal window should open and stay open. A browser tab titled
   **LA Housing Data Browser** should appear, with the page heading
   **Find and download housing data**. Continue to
   [Download your first dataset](#download-your-first-dataset) in that browser tab.

#### If double-clicking does not open the page

Open **Windows PowerShell** from Start and
[return to the project folder](../README.md#return-to-the-project-folder-in-powershell).
Paste this command into PowerShell and press Enter:

```powershell
.\.venv\Scripts\python.exe run_data_browser.py --open-browser
```

On macOS/Linux, open **Terminal**, enter the project folder, and run this instead:

```bash
.venv/bin/python run_data_browser.py --open-browser
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
prompt if the double-click launcher shows one. Next time, repeat
[Open the Data Browser](#open-the-data-browser).

The page reads a local snapshot. Opening it does not fetch new provider releases
or update observations.

### After a database refresh

**The Data Browser automatically uses the database updated by the refresh workflow.**
It queries the local DuckDB file for each preview, CSV download, and definition
download; it does not use a separate saved copy of the data. Stop the browser
before refreshing, then reopen it after the refresh completes and its results have
been checked. Use the new page, or reload an old tab, to reload dataset and filter
choices. The [refresh guide](database_build_and_refresh.md#use-the-refreshed-data-in-the-csv-browser)
provides the complete sequence with Windows and macOS commands.

Click **Preview data** again and download a new CSV. Files already saved in your
Downloads folder do not change automatically. To include newly available months,
leave **Through** blank or extend it; a previously selected end date still applies.
A failed refresh may have updated only part of the database, so check the refresh
result before treating browser downloads as current.

### Download your first dataset

**Where to work:** in the **Data Browser tab** opened above. The next actions
are clicks on that page; there are no PowerShell commands to enter.

Try this example to download one rent series for postal ZIP 90024. It requires
that your database contain the postal-ZIP housing panel.

1. Under **Select your data**, open the **Dataset** dropdown and choose
   **Postal ZIP housing — Monthly**. Wait for the filters to finish loading.
2. Open **Geography type** and choose **postal_zip**. Wait for the choices to update.
3. Open **Place** and choose **ZIP_90024**.
4. Expand **Variables**, search for `zori_smoothed_sa`, and check the Zillow
   Observed Rent Index entry ending in **`(zori_smoothed_sa)`**. Checking it
   switches from **All variables** to that selection. For the whole dataset,
   keep **All variables** checked instead.
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

### Select several variables

Expand **Variables** to open the searchable checkbox list. Each entry shows a
readable name and its exact variable ID in parentheses. You can select several
entries for one download without holding Ctrl or Shift.

Every dataset organizes these choices under category headings. Most use the
existing subject domains with readable names; finance, homebuying, and market
regime datasets use more specific families of related measures. The nine
constructions retain their nine named indicator families. Only categories with
available choices for the selected dataset and geography appear.

| Dataset | Examples of category headings |
|---|---|
| County or postal-ZIP housing | **Home sales and market conditions**, **Rents and rental market**. |
| Housing finance — Versions A, B, C | **Mortgage payments and payment parameters**, **Down-payment rates and borrower shares**, **Mortgage debt and sensitivities**, **Home equity and loan-to-value**. |
| Homebuying cost scenarios | **Sale-price inputs**, **Down-payment scenarios**, **Closing-cost scenarios**, **Mortgage amount and cash to close**. |
| ZIP market classifications — Quarterly | **Regime classification and diagnostics**, **Model inputs**, **Standardized model inputs**. |
| ZIP market summary — Quarterly | **Regime shares and changes**, **Classification coverage**. |

Category headings organize the variable list. They do not change a variable's
definition or its **Domain**, **Layer**, **Proxy**, or **Constructed** classification,
and they do not alter the data or companion definitions you download. Use the
checkbox beside a variable to select it; a category is not an additional data
filter. You can select variables from different categories in the same download.

- **All variables** is checked initially. Individual boxes are unchecked in this
  mode; that means no variable restriction is applied, not that the download is empty.
- Check an individual variable to leave All mode. Check further variables to
  add them to the same selection, or uncheck one to remove it.
- Use the search box to find a name or ID. Searching narrows the displayed list;
  it does not change already checked variables. Clear the search to inspect the
  full list again.
- Check **All variables** to restore the unrestricted selection. If you uncheck
  every individual variable while in selected-variable mode, choose at least
  one variable or restore **All variables** before previewing or downloading.

For ordinary tables and observations, selected variables are alternatives:
choosing price momentum and rent momentum includes both. In **Homebuying cost
scenarios**, alternatives within a scenario group are combined with selections
from the other groups: for example, a 10% down payment and a 3% loan-based
closing cost select their shared scenario rows. Geography, dates, frequency, and the
other filters still apply. For wide tables, selected measures appear as separate
columns with their retained row context; long observation datasets retain their
`variable_id` and `value` columns. Download the matching definitions with the data.

### Download all nine housing constructions together

For one table containing the nine constructions described in the
[county and ZIP indicator guide](county_and_zip_housing_indicators.md), choose
**Nine housing constructions — All geographies and periods** in **Dataset**.
Here, **nine** means nine indicator families. Together they contain **41 existing
variables**, including different measurement horizons and supporting diagnostics:

| Indicator family | Variables | What the choices include |
|---|---:|---|
| 1. Home price momentum | 2 | Year-over-year and three-month annualized momentum. |
| 2. Rent momentum | 2 | The same two horizons for rents. |
| 3. County market tightness | 5 | Tightness index, input count, and three standardized components. |
| 4. Zillow price-to-rent ratio | 3 | Ratio level and one-month/year-over-year changes. |
| 5. LA postal-ZIP relative valuation pressure | 2 | Relative pressure and its matched LA County reference. |
| 6. Local economic momentum | 2 | Resident-employment momentum and unemployment-rate context. |
| 7. Los Angeles relative performance | 7 | Six comparisons with the peer counties and the required peer count. |
| 8. LA postal-ZIP market regimes | 13 | Classification, distance, input diagnostics, regime shares, transitions, and coverage. |
| 9. County housing market heat | 5 | Heat index, rank, coverage count, and two standardized components. |
| **Total** | **41** | Existing variable IDs grouped for selection. |

These counts come from [the mapping dictionary](../metadata/nine_constructions_dictionary.csv).
Dates, geographic identifiers, source information, and other retained context
columns are additional fields, not additional indicator families. A variable
may appear on several applicable row types without becoming a new variable.

In **Variables**, choices for this dataset are grouped under their construction
names. The search and checkboxes work across those groups. For the complete table,
leave every filter at **All** and both dates blank, click **Preview data**, then
**Download CSV** and **Download variable definitions**. Keep the resulting
`nine-constructions.csv` and `nine-constructions.variables.csv` together.
The download contains all matching records, including those beyond the preview.

For example, to compare two measures in the same LA County monthly download:

1. Select **county** in **Geography type**, **COUNTY_06037** in **Place**, and
   **monthly** in **Frequency**.
2. Expand **Variables**. Under **Home price momentum**, check
   **ZHVI year-over-year log momentum (`zhvi_momentum_yoy_log_pct`)**.
3. Under **Rent momentum**, also check
   **ZORI year-over-year log momentum (`zori_momentum_yoy_log_pct`)**.
4. Click **Preview data**. Confirm both measure columns, the LA County identifier,
   and monthly rows, then download the CSV and definitions.

Keep the other filters at **All** for this example because price and rent
momentum belong to different subject domains. Selecting both variables includes
applicable rows even when one or both values are missing.

This is one wide table with different kinds of rows. Start by reading
`row_type`, `date`, `frequency`, `geography_type`, and `geo_id`:

| `row_type` | One row represents |
|---|---|
| `county_monthly` | One of the eight comparison counties in one month. |
| `zip_monthly` | One LA postal ZIP in one month. |
| `zip_regime_quarterly` | One LA postal ZIP's classification and inputs at a quarter end. |
| `zip_regime_summary_quarterly` | One regime's LA ZIP summary in one quarter. |
| `zip_regime_transition_quarterly` | One from-regime/to-regime pair in one quarter. |

County and ZIP records retain their own measures. Quarterly classifications,
summary shares, and transition shares are not repeated into months or copied
onto each ZIP. Summary and transition rows describe the LA postal-ZIP universe;
they are not individual ZIP observations. The existing
`la_county_price_to_rent_ratio_zillow` field remains the explicitly named county
reference used in ZIP relative valuation.

A blank can mean either **this measure does not apply to this row** or
**this measure applies but its value is unavailable**. The pipe-separated
`applicable_variable_ids` field and companion definitions distinguish these
cases: match a column to its variable ID in the definitions, then check whether
that ID applies to the row. Do not replace either kind of blank with zero.
Retain classification, denominator, coverage, and quality diagnostics when
interpreting missing values.

Geography, frequency, place, and variable filters restrict applicable records.
For example, **postal_zip** plus **monthly** selects ZIP monthly records;
**quarterly** includes classifications and aggregate summaries/transitions.
Choosing a variable does not manufacture values where it is missing. Counts or
shares repeated as context on summary/transition rows must not be summed across
those rows. Filter `row_type` in SQL or Excel before treating a selection as one
uniform dataset.

For the same complete download from PowerShell after the setup in section 1:

```powershell
& $HousingPython run_query.py table --dataset nine-constructions --output output/exports/my_nine_constructions.csv
```

The prepared product is `data/final/nine_constructions.parquet`, queried as
`analytics.nine_constructions`; its standard export is
`output/exports/la_nine_constructions.csv`. Its columns reuse existing variable
IDs; [the mapping dictionary](../metadata/nine_constructions_dictionary.csv)
records the construction and applicable row types.

If an older local database lacks this table but the five accepted final source
tables are present, a maintainer can build and register only this product.
Close DuckDB readers and stop the Data Browser first, then run from the project
folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage nine-constructions
```

This reads accepted local products; it does not download providers or resolve
source review holds. Ordinary harmonization/database builds also include it.
Reopen the browser after the command succeeds.

### Find other data and use the filters

| Dataset choice | Typical use |
|---|---|
| Nine housing constructions — All geographies and periods | Nine indicator families containing 41 variables, with explicit county/ZIP monthly and quarterly row types. |
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
| Variables | All measures, or one or more checked measures identified by name and ID. |
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

Filters use the [current variable dictionary](../metadata/data_dictionary.csv).
Different filters combine with **AND**: each filter must be satisfied. Multiple
checked **Variables** are alternatives within that filter; a record need not
contain every selected measure. **All** leaves a condition unrestricted. Proxy
and Constructed are independent; an unknown flag is not the same as No.

For example, to find LA County monthly construction proxies:

1. Choose **LA County housing — Monthly** in Dataset and wait for the filters.
2. Choose **county** in Geography type, then **COUNTY_06037** in Place.
3. Keep **All variables** checked under **Variables** and choose **monthly** in Frequency.
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
[the command walkthrough below](#1-open-the-project-in-powershell)
for multiple selected places, saved commands, and Parquet output. For SQL, use
the [DuckDB SQL guide](duckdb_sql_guide.md).

### Open the CSV in Excel

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

## 1. Open the project in PowerShell

**Check the window first.** If your prompt looks like
`C:\Users\yourname\Downloads\la_housing_database>` without `PS` at the start,
you are in **Command Prompt**. The commands below will not work there.
Type this in that existing window and press Enter:

```text
powershell
```

Wait for `PS C:\Users\yourname\Downloads\la_housing_database>` (with your own
path). You have switched to PowerShell and kept the same folder. If you are
already in the project folder, skip the navigation command below; do not append
another `la_housing_database` folder. Continue with the `Get-Location` checks.

Complete the [README setup](../README.md#2-set-up-the-project), including
[a compatible local database](../README.md#3-get-a-local-database), first.
These instructions use **Windows PowerShell**
on your computer. Type commands there, not into Excel, a GitHub page, the browser
address bar, or a Python window showing `>>>`.

1. Open the Windows **Start** menu, type **PowerShell**, and open **Windows
   PowerShell**. Administrator mode is not needed.
2. Find the prompt, which looks like `PS C:\Users\yourname>`. Do not copy the
   prompt itself. For every `powershell` block below, copy **one complete line**,
   paste it at this prompt, and press **Enter**. A long command may wrap visually;
   it is still one command. Do not copy the surrounding backticks.
3. Wait for the command to finish and for the `PS ...>` prompt to return before
   entering the next line. If an error appears, stop and use
   [Common messages](#8-common-messages). Do not continue as though it succeeded.

Enter this first command if you followed the README and saved the project in
your Downloads folder:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
```

If you saved the project elsewhere, use `Set-Location -LiteralPath` followed by
the full project-folder path in quotes instead. Choose the folder containing
`run_query.py`, `requirements.txt`, and `README.md`. In File Explorer, click its
address bar to see and copy that path. Successful navigation normally prints
nothing; the next PowerShell prompt shows the new folder.

Now enter these lines **one at a time** in the same PowerShell window:

```powershell
Get-Location
Test-Path -LiteralPath '.\run_query.py'
Test-Path -LiteralPath '.\.venv\Scripts\python.exe'
Test-Path -LiteralPath '.\database\housing.duckdb'
$HousingPython = '.\.venv\Scripts\python.exe'
```

**Check the results:** `Get-Location` should show your project folder. Each
`Test-Path` should print `True`. If the first is `False`, navigate to the correct
folder; if the second is `False`, finish Python setup in the README; if the third
is `False`, follow the README's database step. The `$HousingPython = ...` line
normally prints nothing; it stores the interpreter path for this session.

Check that the query tool starts, then that it can read your database:

```powershell
& $HousingPython run_query.py --help
& $HousingPython run_query.py catalog --variable zori_smoothed_sa
```

The first command prints help beginning with `usage:`. The second prints CSV
headings and the rent variable's definition to the terminal; it does not save
a file or prove that observations are installed. If these checks succeed, go
to section 3 to inspect available choices, or section 5 to try a data export.

Keep this PowerShell window open and stay in the project folder for **all command
examples in this guide**. The `&` runs the interpreter stored in `$HousingPython`.
If you close PowerShell, repeat this section when you reopen it. The examples
below are alternatives: choose the ones you need; you do not have to run every
download. Commands never fetch new provider releases.

## 2. Translate workbook columns into query filters

This section is a reference. The option fragments in the table, such as
`--domain rental_market`, are **not complete commands**. Add them to a full
command like those in sections 4–6; do not enter them alone in PowerShell.

| Workbook column / choice | Command option | Example |
|---|---|---|
| Domain | `--domain` | `--domain rental_market` |
| Layer | `--layer` | `--layer "Version B"` |
| Proxy | `--proxy` | `--proxy true` |
| Constructed | `--constructed` | `--constructed false` |
| Final frequency | `--frequency` | `--frequency monthly` |
| Native frequency | `--native-frequency` | `--native-frequency rolling_3_months` |
| Geography type | `--geography` | `--geography county` |
| A particular place | `--geo-id` | `--geo-id COUNTY_06037` |
| Variable ID | `--variable` | `--variable zori_smoothed_sa` |
| Simplified unit | `--unit` | `--unit "USD/month"` |
| Temporal type | `--temporal-type` | `--temporal-type stock` |

Use the exact Domain values, including underscores. The following is a list of
allowed words to read or copy into a command, **not a block to run**:

```text
construction                  eviction
finance                       for_sale_market
home_values                   homelessness
housing_finance               housing_stock
labor_market                  macro
migration                     population
prices                        property_insurance
rental_market                 residential_address_availability
structural                    uncertainty
```

Layer choices are `General`, `Version A`, `Version B`, and `Version C`.
Put the version names in quotes because they contain spaces. Proxy and
Constructed accept `true` or `false`; leaving an option out includes either
value. These are independent classifications: a constructed variable is not
necessarily a proxy.

**Frequency matters:** `--frequency` selects the output frequency. Catalog
queries use the workbook's Final frequency; table queries use the table's
actual cadence and keep measures derived from inputs at other frequencies.
`--native-frequency` selects the source
frequency. A monthly output may use annual source information or an overlapping
three-month window. Filtering does not turn annual values into monthly data or
make overlapping windows additive.

For `main` observations, frequency filters use actual row labels. For example,
ACS observations can use `annual_5_year_estimate` even when the variable
inventory labels the native frequency `annual`. Discover dataset-specific
choices instead of assuming the catalog labels cover every row label:

```powershell
& $HousingPython run_query.py options --field native-frequency --kind observations --dataset main --geography zcta
& $HousingPython run_query.py options --field geo-id --kind table --dataset comparable-county-monthly --geography county
```

Run either line in the PowerShell window from section 1. It prints a heading
named `value`, followed by available choices; it does not create a download.
Use a printed value unchanged in a later filter. A heading with no choices means
the selected dataset/geography has no available choices in your local snapshot.

**Geography matters:** `county` means the county geography type;
`COUNTY_06037` identifies Los Angeles County. Postal ZIP and Census ZCTA are
different geography choices. Geography selection restricts actual data rows
when exporting observations or tables. It does not substitute national or
statewide controls for missing county observations.

## 3. List the available choices and datasets

In the same project PowerShell window, run these lines one at a time to list
current choices directly from the database:

```powershell
& $HousingPython run_query.py options --field domain
& $HousingPython run_query.py options --field layer
& $HousingPython run_query.py options --field frequency
& $HousingPython run_query.py options --field geography
```

After each command, expect a `value` heading and a list in the terminal.
For example, the Domain list includes names such as `construction`; the Layer
list uses `General`, `Version A`, `Version B`, and `Version C`. Copy the exact
choice you need into a later command. These commands do not save files.

List supported dataset selectors (this works without a database and does not
confirm that their observations are installed):

```powershell
& $HousingPython run_query.py datasets
```

This prints a table in CSV form, beginning with
`kind,dataset,table_name,date_column`. Use the word in the `dataset` column, such
as `zip-monthly`, after `--dataset`; do not paste the entire result as a command.
Next choose whether you need definitions or observations:

| Command | What you receive | Typical use |
|---|---|---|
| `catalog` | One row per variable, with definitions and classifications | Find and select variables |
| `observations` | One row per variable, geography, observation date, and applicable vintage | Download data with labels on every row |
| `table` | One row per native table key, with separate measure columns | Download a ready-to-use analysis table |

`catalog` downloads metadata, not housing observations. A catalog entry can
exist even when values are unavailable. Explicit missing values remain missing.

## 4. Find variables using the workbook choices

For example, find General-layer construction proxies delivered monthly at the
county level:

```powershell
& $HousingPython run_query.py catalog --geography county --frequency monthly --domain construction --layer General --proxy true --constructed true --output output/exports/my_construction_variables.csv
```

Run that complete line in the project PowerShell window. On success it prints
`Exported ... rows to output/exports/my_construction_variables.csv`; the number
depends on the matching dictionary entries. Open your project folder in File
Explorer, then **output → exports**, and find **my_construction_variables.csv**.
Section 7 explains how to import it into Excel. It contains selected variable
IDs, names, definitions, original and simplified units, temporal types, and
classifications. This example finds the Central and Slow construction proxies
in the maintained dictionary. It saves definitions, not their observed values.

For a second example, find monthly postal-ZIP rental-market variables:

```powershell
& $HousingPython run_query.py catalog --geography postal_zip --frequency monthly --domain rental_market --output output/exports/my_zip_rental_variables.csv
```

This writes **my_zip_rental_variables.csv** in the same **output → exports**
folder. Check the success message before opening the file.

Different options combine with **AND**. Repeating a multiple-choice option
selects alternatives within it. For example:

```powershell
& $HousingPython run_query.py catalog --domain rental_market --domain for_sale_market --frequency monthly --output output/exports/my_monthly_market_variables.csv
```

This selects monthly variables from either of those two domains. If an
intersection has no results, remove one condition or check the available choices.
All three examples write different filenames. To rerun an example, change the
filename after `--output`, for example to `my_construction_variables_v2.csv`;
existing exports are never replaced. After identifying an ID, use section 5
to download a table or section 6 to download labeled observations.

## 5. Download a table as a CSV

### A. Choose a table

Read this table to choose a dataset. The values below are option values, not
commands. The examples in parts B and C show where to use them:

| `--dataset` value | Contents |
|---|---|
| `nine-constructions` | All nine county/ZIP constructions with explicit monthly/quarterly row types |
| `county-monthly` | LA County monthly housing panel |
| `comparable-county-monthly` | Monthly housing measures for LA and comparable counties |
| `zip-monthly` | Postal-ZIP monthly housing panel |
| `county-annual` | Annual county controls |
| `zcta-annual` | Annual Census ZCTA controls |
| `county-quarterly` | Quarterly county controls |
| `state-monthly` / `state-quarterly` / `state-annual` | State controls at the stated output frequency |
| `metro-monthly` / `metro-quarterly` / `metro-quarterly-controls` | Metro controls and panels |
| `macro-monthly` | Monthly macro variables, retaining their own geography |
| `coc-annual` | Annual Continuum of Care controls |
| `zip-regimes-quarterly` | Quarterly postal-ZIP market classifications |
| `zip-regime-summary-quarterly` | LA ZIP market-regime summary |
| `zip-regime-transitions-quarterly` | Transitions between market regimes |
| `homebuying-scenarios` | Homebuying cost scenarios and their assumptions |

### B. Download the table

In the same project PowerShell window, run this complete line to download all
classified measures in the LA County monthly panel:

```powershell
& $HousingPython run_query.py table --dataset county-monthly --output output/exports/my_la_county_monthly.csv
```

Wait for the prompt to return. A successful run prints `Column classifications:`
with a filename, then `Exported ... rows to ...`. Both files are inside your
project's **output → exports** folder:

- `my_la_county_monthly.csv`: the data table.
- `my_la_county_monthly.variables.csv`: the companion dictionary linking each
  selected data column to its variable ID, unit, temporal type, and definition.

Confirm that both files exist, then follow section 7 to open them in Excel.
Keep the dictionary with its data file. This command does not open Excel for you.

Date, geography, quality, and provenance fields remain in the table. Unregistered
measure columns, if any, are named in a message and omitted from this classified
export. The existing underlying database tables remain unchanged.

### C. Add geography, dates, or variable filters

The following are separate examples. Run the one you need in the same project
PowerShell window; each prints its saved paths and row count. The name after
`--output` is the file to look for under **output → exports**.

Download the county construction proxies from 2020 onward:

```powershell
& $HousingPython run_query.py table --dataset county-monthly --geo-id COUNTY_06037 --frequency monthly --domain construction --layer General --proxy true --constructed true --start-date 2020-01-01 --output output/exports/my_la_construction_proxies.csv
```

Download one ZIP's rent index:

```powershell
& $HousingPython run_query.py table --dataset zip-monthly --geo-id ZIP_90024 --variable zori_smoothed_sa --start-date 2020-01-01 --output output/exports/my_90024_rent.csv
```

For the ZIP example, open **my_90024_rent.csv** and
**my_90024_rent.variables.csv** after success. Expect ZIP `90024`, dates from
2020 onward where available, and a `zori` data column. The dictionary maps that
column to `zori_smoothed_sa`. A zero-row result does not establish coverage;
check the snapshot's available geography and dates before continuing.

Dates use `YYYY-MM-DD`. Both start and end dates are inclusive; add
`--end-date 2025-12-31` to the complete export command when you want a fixed
endpoint. That fragment is not a command to run on its own.

To keep two comparable counties, repeat the geography ID:

```powershell
& $HousingPython run_query.py table --dataset comparable-county-monthly --geo-id COUNTY_06037 --geo-id COUNTY_06059 --domain for_sale_market --start-date 2020-01-01 --output output/exports/my_la_orange_market.csv
```

These IDs mean Los Angeles County and Orange County. Exporting several counties
retains their separate rows; it does not aggregate them.

## 6. Download observations with labels on every row

Use `observations` when you want a long table with columns such as
`variable_id`, `geo_id`, `value`, `unit_simplified`, and `temporal_type`.
Source dates, native frequency, and provenance are retained where supplied.

Run this line in the project PowerShell window:

```powershell
& $HousingPython run_query.py observations --dataset main --geography county --geo-id COUNTY_06037 --frequency monthly --domain for_sale_market --start-date 2020-01-01 --output output/exports/my_la_monthly_market_observations.csv
```

On success, find **my_la_monthly_market_observations.csv** under
**output → exports**. Unlike `table`, this command writes one data file with
variable IDs and classifications on every row; it does not create a
`.variables.csv` companion. Follow section 7 to import the CSV into Excel.

Choose the observation dataset matching the variable's Layer:

| Layer | Observation `--dataset` | Date filter applies to |
|---|---|---|
| General | `main` | `observation_date` |
| Version A | `version-a` | `reference_date`; original source interval remains available |
| Version B | `version-b` | `month` |
| Version C | `version-c` | `month`; all information cutoffs remain present |

Some General-layer derived variables occur in wide tables only. Use `table`
for those; a selected variable missing from a long dataset raises a message
rather than creating observations.

For example, list Version B finance variables, then download their monthly LA
observations. Run the first line, check its success message, then run the second:

```powershell
& $HousingPython run_query.py catalog --layer "Version B" --domain finance --frequency monthly --output output/exports/my_version_b_variables.csv
& $HousingPython run_query.py observations --dataset version-b --layer "Version B" --domain finance --frequency monthly --geo-id COUNTY_06037 --output output/exports/my_version_b_la.csv
```

Look for **my_version_b_variables.csv** (definitions) and
**my_version_b_la.csv** (observations) under **output → exports**. These require
a snapshot containing the Version B layer; they do not build it if absent.

Layer filtering does not move data between datasets. For example, Version B
requires `--dataset version-b`; `--dataset main --layer "Version B"` does not
combine the two products. Version C exports retain all cutoffs, so the same
variable/date may appear for more than one information vintage.

For modeled costs, this table example selects the 3%-of-loan closing-cost
scenario:

```powershell
& $HousingPython run_query.py table --dataset homebuying-scenarios --geo-id COUNTY_06037 --variable closing_cost_loan_3pct --output output/exports/my_la_closing_cost_3pct.csv
```

Its companion dictionary records both the value-column name and the scenario
row identifier. Scenario assumptions remain in the data file. Multiple choices
within a price/down-payment/closing-cost role are alternatives; choices across
roles intersect, selecting the scenario with that combination.
After success, the files are **my_la_closing_cost_3pct.csv** and
**my_la_closing_cost_3pct.variables.csv** under **output → exports**.

## 7. Locate and open the downloaded CSVs

Command-line exports in these examples are saved under the following location.
This is a path description, **not a command**; replace the words in angle
brackets mentally with the project folder you opened in section 1:

```text
<your project folder>\output\exports
```

To open that folder, enter this command in the same project PowerShell window
and press Enter:

```powershell
Invoke-Item -LiteralPath '.\output\exports'
```

File Explorer should open **output\exports**. Match the filename to the
successful command's `--output` value. If the folder or file is absent, return
to PowerShell and check the last error and your current folder with `Get-Location`.
Command-line exports stay here; the webpage's **Download CSV** button instead
uses your web browser's configured download location, often Downloads.

Follow [Open the CSV in Excel](#open-the-csv-in-excel) above.
It explains how to disable automatic type detection or remove an inferred type
step before setting identifier columns to **Text**. For a `table` export, import
its `.variables.csv` companion the same way. A catalog or `observations` export
has no automatic companion.

An identifier such as county FIPS `06037` must retain its leading zero. The CSV
contains the correct identifier; Excel's automatic numeric conversion can change
its display. The table and its companion dictionary are ordinary CSV files that
you can share together.

The tool does not overwrite existing exports. If a filename already exists,
choose another name, such as `my_la_county_monthly_v2.csv`. To create typed
Parquet instead, change `.csv` to `.parquet`; the companion dictionary remains CSV.
Parquet is an optional analysis format; use CSV for the Excel steps above.

## 8. Common messages

| Message or result | What to do |
|---|---|
| `'Set-Location'`, `'Get-Location'`, `'Test-Path'`, or `'$HousingPython'` is not recognized as an internal or external command | This is Command Prompt. Enter `powershell`, press Enter, and wait for a prompt beginning with `PS`. Then continue section 1 from the current project folder. |
| `Set-Location` cannot find the path | Open the project in File Explorer, copy its actual folder path, and repeat section 1 with that path in quotes. |
| Python or `run_query.py` cannot be found | Check the project folder and the `Test-Path` results in section 1; finish the README setup if needed. |
| The expression after `&` is not valid | Your PowerShell session may have lost `$HousingPython`; repeat the assignment in section 1. |
| Database does not exist / table has not been built | Follow the README database step; a clone or variable dictionary alone does not contain observations. |
| Output already exists | Use a new filename. |
| No matching variables/columns | Check exact choices; loosen the filter intersection. |
| Variable has no observations in this dataset | Check its Layer and try the appropriate long dataset or wide table. |
| Empty date/geography slice | Check that the selected place and dates occur in that dataset. Missing data are not filled. |
| Unclassified columns omitted | The message identifies additional table fields outside the configured variable inventory. |
| Restricted HUD-USPS download refused | These licensed observations remain outside ordinary CSV exports; select public variables instead. |

For more options, run either of these in the project PowerShell window:

```powershell
& $HousingPython run_query.py table --help
& $HousingPython run_query.py observations --help
```

They print instructions and return to the prompt; they do not export data.
The following reference explains the classifications and their aggregation limits.

## 9. Units and temporal types

The variable dictionary covers all 559 configured variables. `unit_simplified`
and `temporal_type` are stable query fields; the existing `unit` remains the
original unit. Labels never rescale values or change observation dates,
frequency, geography, source vintage, or aggregation rules.

`config/variable_metadata.csv` is the explicit per-variable registry.
`config/variable_metadata_provenance.json` records the supplied workbook's
filename, SHA-256, import timestamp, definitions, additions, and corrections.
The workbook covers 482 IDs; 77 later additions are classified from the current
configuration and transformation definitions. The source workbook remains intact.

| Temporal type | Meaning |
|---|---|
| `stock` | Outstanding level; do not sum through time. |
| `flow` | Quantity during a period; additive only across compatible, nonoverlapping periods. |
| `period_average` | An explicitly defined average; inspect its averaging basis. |
| `period_statistic` | A non-additive index, median, modeled level, or cross-sectional statistic. |
| `derived_ratio` | Ratio, rate, share, or yield; recompute from components where appropriate. |
| `growth_change` | Change between observations; recompute for a different horizon. |
| `rolling_window` | Overlapping-window observation; do not sum adjacent observations. |
| `cumulative` | Cumulative-to-date amount; do not sum successive totals. Currently unused. |
| `metadata_or_assumption` | Uncertainty, flags, diagnostics, or assumptions with no general economic aggregation rule. |

These categories describe observations; they do not select frequency-conversion
algorithms. In particular, Redfin ZIP observations remain overlapping rolling
three-month windows, and repeated annual controls retain their annual source
meaning. Annualized GDP and income rates are not unadjusted quarterly flows.

Unit simplifications include `current_dollars` to `USD`, `housing_units` to
`units`, and `fraction`/`proportion` to `share`. `percent`,
`percentage_points`, dollar scaling, annualization, constant-dollar bases, and
index bases remain distinct. `monetary_basis` preserves inflation information
when the simplified label is less specific. A growth fraction labeled `share`
can be negative or exceed one; the label does not impose a zero-to-one bound.

The generated `la_version_a_benchmark.csv` retains the frozen benchmark's
original unit labels. For 30 explicitly reviewed variables, older labels such
as `dollars`, `rate`, and `share` have equivalent current dictionary labels.
The export includes `unit_simplified` and explains each equivalence in
`unit_simplification_note`; it does not rescale values or rewrite frozen files.
Unreviewed unit mismatches still stop the export.

Five reviewed classification corrections are recorded in `metadata_review_note`,
with the original supplied type retained in `workbook_temporal_type`: three
average-premium variables are derived ratios; annual total BEA personal income
is a flow; the cross-sectional implied average mortgage balance is a period
statistic. No numerical data were changed.

## 10. Query with SQL

Use the [DuckDB SQL guide](duckdb_sql_guide.md) to open the database read-only,
find tables and columns, write SQL queries, and inspect results. It explains where
to type each command and how to distinguish PowerShell from the SQL prompt.
No Python script is needed for that workflow.

For shareable public CSVs, the browser and commands in this guide enforce the
ordinary export rules and include the selected variables' classifications.
Direct SQL is a research interface and does not apply those export restrictions.

For wide datasets, the companion dictionary gives the exact `table_column` to
`variable_id` mapping. For example, ZIP `homes_sold` maps to the Redfin
rolling-window identifier. Each column retains its own temporal classification;
a mixed table does not receive one collective stock/flow type. Scenario mappings
also contain `row_identifier_column` and `row_identifier_value`.

Use `--temporal-type stock --unit units` with `catalog` to find stock quantities,
or add the filters to an export command above. These are option fragments, not
standalone commands. `--unit` aliases `--unit-simplified`; neither option converts
numerical values. See
[database build and refresh](database_build_and_refresh.md#change-a-source-or-method)
for updating the explicit classification registry and SQL views.
