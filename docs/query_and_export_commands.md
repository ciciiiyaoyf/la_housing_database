# Query and export commands

For dropdowns and CSV download buttons, start with the
[beginner tutorial in the README](../README.md). This optional command reference
supports saved, repeatable queries. Filters use the classifications in the
[current variable dictionary](../metadata/data_dictionary.csv), originally aligned
with the **Variable Inventory** sheet of the August 24 overview workbook. This guide
covers both finding variables and downloading their data. All examples use the local
database snapshot; they do not download new releases from providers.

The historical workbook contains 482 variable IDs. The current database dictionary contains
558, including all 482 workbook IDs. For those shared IDs, Domain, Layer, Proxy,
Constructed, Geography, Native frequency, and Final frequency agree with the
workbook. The same filters also work for the 76 newer variables.

## 1. Open the project in PowerShell

Complete the [README setup](../README.md#2-set-up-the-project), including a
compatible local database, first. These instructions use **Windows PowerShell**
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

Follow the README's [step-by-step Excel import](../README.md#7-open-the-csv-in-excel).
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

The variable dictionary covers all 558 configured variables. `unit_simplified`
and `temporal_type` are stable query fields; the existing `unit` remains the
original unit. Labels never rescale values or change observation dates,
frequency, geography, source vintage, or aggregation rules.

`config/variable_metadata.csv` is the explicit per-variable registry.
`config/variable_metadata_provenance.json` records the supplied workbook's
filename, SHA-256, import timestamp, definitions, additions, and corrections.
The workbook covers 482 IDs; 76 later additions are classified from the current
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

Five reviewed classification corrections are recorded in `metadata_review_note`,
with the original supplied type retained in `workbook_temporal_type`: three
average-premium variables are derived ratios; annual total BEA personal income
is a flow; the cross-sectional implied average mortgage balance is a period
statistic. No numerical data were changed.

## 10. Optional Python/SQL example for analysts

You can skip this section for normal CSV downloads. The block below is
**Python code containing a SQL query**, not PowerShell commands. Do not paste
its lines directly at a `PS ...>` prompt. To try it as a saved script:

1. Open **Notepad** from the Windows Start menu and paste the entire Python block.
2. Choose **File → Save As**. Navigate to your project folder containing
   `run_query.py`. Set **Save as type** to **All files**, filename to
   **query_example.py**, and encoding to **UTF-8**, then save. Ensure the filename
   is not `query_example.py.txt`.
3. Return to the project PowerShell window from section 1. Run the command
   shown after the code block.

```python
import duckdb

with duckdb.connect("database/housing.duckdb", read_only=True) as connection:
    data = connection.sql("""
        SELECT observation_date, geo_id, variable_id, value, unit,
               unit_simplified, temporal_type, monetary_basis, source_vintage
        FROM analytics.fact_housing_observation_labeled
        WHERE variable_id = 'permits_total' AND geo_id = 'COUNTY_06037'
        ORDER BY observation_date, variable_id
        LIMIT 20
    """).df()
    print(data.to_string(index=False))
```

Run the saved file with this **PowerShell command**:

```powershell
& $HousingPython .\query_example.py
```

Expect up to 20 permit observations printed in the terminal, followed by the
PowerShell prompt. `Empty DataFrame` means the selected observations are absent
from this snapshot. This example only reads the database and prints a preview;
it does not save a CSV. Use sections 5–6 for the checked download workflow.

The other labeled views are `analytics.housing_finance_benchmark_labeled`,
`analytics.housing_finance_monthly_hist_labeled`, and
`analytics.housing_finance_monthly_rt_labeled`. Direct SQL access is a research
interface: the ordinary download tool enforces export restrictions and should be
used for shareable public CSVs.

For wide datasets, the companion dictionary gives the exact `table_column` to
`variable_id` mapping. For example, ZIP `homes_sold` maps to the Redfin
rolling-window identifier. Each column retains its own temporal classification;
a mixed table does not receive one collective stock/flow type. Scenario mappings
also contain `row_identifier_column` and `row_identifier_value`.

Use `--temporal-type stock --unit units` with `catalog` to find stock quantities,
or add the filters to an export command above. `--unit` aliases
`--unit-simplified`; neither option converts numerical values. See
[database build and refresh](database_build_and_refresh.md#change-a-source-or-method)
for updating the explicit classification registry and SQL views.
