# View and query the DuckDB database

This is the primary guide for using the housing database. You will open the local
DuckDB file, inspect its tables, select data with SQL, and pass query results
directly to Python for modeling. A CSV download is not required.

**Follow the tutorial:** [open DuckDB](#1-open-the-database-and-recognize-the-two-prompts) →
[view tables](#2-see-which-tables-and-datasets-are-installed) →
[choose variables](#4-find-variables-by-the-inventory-classifications) →
[retrieve data](#5-retrieve-a-dataset-or-one-series) →
[use Python](#8-use-the-query-result-in-python).
For a visual SQL editor, see the [graphical interface](#10-view-tables-in-duckdbs-graphical-interface).

Complete [README steps 1–3](../README.md#1-install-the-tools-and-clone-the-project)
first. They explain installation and obtaining a compatible, permitted
`database/housing.duckdb` file. A GitHub clone contains no housing observations.
For the alternative spreadsheet workflow, use
[CSV downloads](query_and_export_commands.md#browser-downloads).

## 1. Open the database and recognize the two prompts

On Windows, open **Start → Windows PowerShell**. Paste this command and press Enter
to enter the project folder used in the README:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
```

If your existing terminal prompt starts with `C:\...>` instead of `PS C:\...>`,
it is Command Prompt. Type **`powershell`**, press Enter, and wait for `PS` before
using PowerShell commands. If it already shows the correct project folder, skip
the navigation command; do not add a second `la_housing_database` folder.

If you saved it elsewhere, follow the README's
[folder-location instructions](../README.md#return-to-the-project-folder-in-powershell).
All paths below start from the project folder. Check that the database exists:

```powershell
Test-Path -LiteralPath '.\database\housing.duckdb'
```

Continue only if this prints `True`. In the same **PowerShell** window, start DuckDB:

```powershell
.\.venv\Scripts\duckdb.exe -readonly .\database\housing.duckdb
```

If the executable is missing, rerun the README package-install command:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then retry opening DuckDB. The requirements include **duckdb-cli**, the official
SQL command-line client, separately from the Python `duckdb` library. There is no
`python -m duckdb` command in the installed Python library. See the
[official CLI documentation](https://duckdb.org/docs/current/clients/cli/overview).

On macOS/Linux, open Terminal, enter the project folder as described in the
[README](../README.md#macos-and-linux), and use:

```bash
.venv/bin/duckdb -readonly database/housing.duckdb
```

Once DuckDB opens, the window displays a **`D` prompt**. Keep it open and use the
SQL blocks in sections 2–7 there. Each SQL block is one complete statement:
copy the whole block, including its final `;`, paste at `D`, and press Enter.
Lines can wrap on screen without changing the query.

| What the window shows | What to enter |
|---|---|
| `PS ... >` | PowerShell commands that start programs or move between folders. |
| `D` | SQL statements ending in `;`, or DuckDB dot commands described below. |
| A continuation prompt after unfinished SQL | Finish the statement with `;`, or press Ctrl+C and paste the complete statement again. |

The `-readonly` connection protects the supplied database from changes and fails
if the file does not exist. It is appropriate for exploration and model-input
queries. Close database readers before rebuilding the database.

At **`D`**, run:

```sql
SELECT current_database() AS database_name;
```

Expect `housing`. When finished with a session, enter **`.quit`** at `D`, without
a semicolon, and press Enter. This returns you to PowerShell or Terminal.
Reopen the database with the same launch command on your next visit.

## 2. See which tables and datasets are installed

At the **DuckDB `D` prompt**, first enter this display command and press Enter,
**without a semicolon**:

```text
.mode table
```

DuckDB's usual `duckbox` display shows only 40 rows by default and can hide the
middle of a long result. `table` mode prints every result row and the complete
table names. Now paste this whole SQL statement at **`D`** and press Enter:

```sql
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_catalog = current_database()
ORDER BY table_schema, table_name;
```

This lists **all tables and views across every schema in the open database**,
including schemas added in later builds. It has no SQL row limit. Scroll up in
the terminal to read the beginning of the list; more than one screen of output
is normal. Maximize the window if long names wrap onto another line.

After reading the complete list, enter this at **`D`**, without a semicolon:

```text
.mode duckbox
```

This restores the compact display for later data previews. Display commands
change only how results appear; they do not alter the database. See DuckDB's
[output formats](https://duckdb.org/docs/current/clients/cli/output_formats).

A *schema* groups related tables. A name such as
`analytics.county_monthly_panel` means table `county_monthly_panel` in schema
`analytics`. A *view* is a saved query that you can read like a table.

| Schema | What to look for |
|---|---|
| `analytics` | Prepared panels, source observations, finance products, and labeled views. Start here for analysis. |
| `metadata` | Variable definitions, geography, and finance lineage. |
| `quality` | Recorded checks on a particular build. |
| `clean` | Source-specific normalized observations. Inspect their source contracts before combining them. |
| `raw` | Manifest/provenance records; archived provider files themselves remain outside DuckDB. |

Useful starting products, when present in your snapshot:

| SQL table/view | Shape and purpose |
|---|---|
| `analytics.county_monthly_panel` | One row per county/month, with measure columns such as `zori`. |
| `analytics.zip_monthly_panel` | One row per postal ZIP/month. |
| `analytics.fact_housing_observation_labeled` | Long observations with `geo_id`, `variable_id`, `value`, units, and temporal labels. |
| `analytics.housing_finance_benchmark_labeled` | Version A benchmark observations at their reference periods. |
| `analytics.housing_finance_monthly_hist_labeled` | Version B retrospective monthly estimates; may use later information. |
| `analytics.housing_finance_monthly_rt_labeled` | Version C estimates by explicit information cutoff. |
| `metadata.variable_dictionary` | Definitions and classifications, not observed values. |

The listing reflects your actual database. A table can exist with no rows or
explicit missing values. Use the checks in section 7 to establish usable coverage.
The [data inventory](data_inventory_and_limitations.md) explains other products.

For any later result that needs a complete display, enter `.mode table` at `D`
and rerun the query, then restore `.mode duckbox` when finished. For a wide result,
`.mode line` prints each row vertically. These settings do not remove a SQL
`LIMIT`, which restricts the query result itself. A display setting such as
`.maxrows 60` still caps the preview and does not guarantee a complete list.
These are [DuckDB display commands](https://duckdb.org/docs/current/clients/cli/dot_commands).

## 3. Inspect columns and preview a table

Before guessing a column name, inspect the table:

```sql
DESCRIBE analytics.county_monthly_panel;
```

The result lists column names, data types, and other structural information.
To inspect just the three columns used in the first example:

```sql
DESCRIBE SELECT date, county_fips, zori
FROM analytics.county_monthly_panel;
```

Now preview their values:

```sql
SELECT date::DATE AS month, county_fips, zori
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
ORDER BY date DESC
LIMIT 10;
```

This selects up to ten recent LA County rows. Read the statement as follows:

| SQL part | Meaning |
|---|---|
| `SELECT ...` | Choose the columns to return. `date::DATE AS month` displays the date under the heading `month`. |
| `FROM ...` | Choose the table. |
| `WHERE ...` | Keep rows satisfying a condition. |
| `ORDER BY date DESC` | Put the newest dates first. Use ascending `ORDER BY date` for a time series. |
| `LIMIT 10` | Return at most ten rows for inspection. |

Use single quotes around text values, including identifiers such as `'06037'`.
The leading zero is meaningful. The project keeps postal ZIPs and Census ZCTAs
distinct. A column's name alone does not establish its definition or units;
look up `zori_smoothed_sa` in the dictionary next.

## 4. Find variables by the inventory classifications

Inspect the dictionary's available fields:

```sql
DESCRIBE metadata.variable_dictionary;
```

Look up the rent variable:

```sql
SELECT variable_id, display_name, definition, unit_simplified,
       temporal_type, native_frequency, final_frequency
FROM metadata.variable_dictionary
WHERE variable_id = 'zori_smoothed_sa';
```

It is a smoothed, seasonally adjusted rent index, in USD/month, classified as
`period_statistic`. Its wide-panel column is `zori`; its long-table identifier is
`zori_smoothed_sa`. Never assume every table uses the same column naming.

The inventory filters have these SQL equivalents:

| Inventory choice | SQL field or expression |
|---|---|
| Domain | `data_domain` |
| Geography scope | `geography`, the recorded scope text. Many entries use pipe-separated tokens; some have qualified descriptions. |
| Final/output frequency | `final_frequency` in the dictionary; inspect the selected dataset's actual period/row labels too. |
| Native frequency | `native_frequency` |
| Proxy / Constructed | `is_proxy` / `is_constructed` |
| Simplified unit / Temporal type | `unit_simplified` / `temporal_type` |
| Layer | Derived from the current versioned variable-ID suffixes, as shown below; the SQL dictionary does not store a `layer` column. |

For example, find observed General-layer county rent variables delivered monthly:

```sql
WITH catalog AS (
    SELECT *,
           CASE
               WHEN ends_with(variable_id, '_benchmark') THEN 'Version A'
               WHEN ends_with(variable_id, '_monthly_hist') THEN 'Version B'
               WHEN ends_with(variable_id, '_monthly_rt') THEN 'Version C'
               ELSE 'General'
           END AS layer
    FROM metadata.variable_dictionary
)
SELECT variable_id, display_name, unit_simplified, temporal_type
FROM catalog
WHERE list_contains(string_split(geography, '|'), 'county')
  AND final_frequency = 'monthly'
  AND data_domain = 'rental_market'
  AND layer = 'General'
  AND is_proxy = FALSE
  AND is_constructed = FALSE
  AND temporal_type = 'period_statistic'
ORDER BY variable_id;
```

`WITH catalog AS (...)` gives a name to an intermediate query; it does not create
or change a stored table. `AND` requires every condition to match. In the maintained
dictionary this selection includes `zori_smoothed_sa`. A match identifies a variable;
it does not prove non-null data exists for every place/date.

The geography condition matches an **exact `county` token**. Some variables instead
have qualified scopes such as `county with national rate` or
`Los Angeles County postal-ZIP aggregate`. Inspect their full `geography` value
and source definition before adding them to a selection; those descriptions must
not be silently treated as interchangeable county observations.

To discover exact Domain values before adapting the query:

```sql
SELECT DISTINCT data_domain
FROM metadata.variable_dictionary
WHERE data_domain IS NOT NULL
ORDER BY data_domain;
```

Change `'rental_market'` to a returned value, such as `'construction'`, when appropriate.
Remove a whole `AND` condition to leave that filter unrestricted. `FALSE` is not
the same as an unknown/null flag. The Layer expression follows the current variable
contracts; it does not move values between General/A/B/C observation tables.

## 5. Retrieve a dataset or one series

For the complete LA County monthly table, the query is:

```sql
SELECT *
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
ORDER BY date;
```

`*` selects all physical columns, including any not classified by the CSV tools.
Inspect the dictionary and source methods before using an unfamiliar column.
This result may be wide; the display commands in section 2 affect how it appears.
Section 8 shows how to load a SQL result into Python memory for analysis.

For one county rent series and a fixed date range:

```sql
SELECT date::DATE AS month, county_fips, zori
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
  AND date >= DATE '2020-01-01'
  AND date < DATE '2026-01-01'
ORDER BY date;
```

This includes January 2020 through December 2025 where rows exist. It preserves
missing rent values. For a postal-ZIP series with definitions/provenance attached:

```sql
SELECT observation_date::DATE AS month, geo_id, variable_id, value,
       unit_simplified, temporal_type, source_vintage, quality_flag
FROM analytics.fact_housing_observation_labeled
WHERE geo_id = 'ZIP_90024'
  AND variable_id = 'zori_smoothed_sa'
  AND observation_date >= DATE '2020-01-01'
ORDER BY observation_date;
```

The **wide** panel has separate columns for measures. The **long** view keeps each
measure in `value` and identifies it with `variable_id`. Choose the shape suited
to your analysis; neither requires exporting a CSV.

To look up geographic identifiers rather than guessing them:

```sql
SELECT geo_id, geo_type, geo_name, county_fips, postal_zip, zcta
FROM metadata.geography
WHERE geo_id IN ('COUNTY_06037', 'ZIP_90024');
```

In wide panels, filter `county_fips = '06037'` or `postal_zip = '90024'`. In long
observations, use `geo_id = 'COUNTY_06037'` or `'ZIP_90024'`. Changing the filter
does not substitute broader-geography data for missing local observations.

## 6. Join observations to definitions

Use stable identifiers, not display names, to combine related records. For example:

```sql
SELECT f.observation_date::DATE AS month, f.geo_id,
       f.variable_id, d.display_name, f.value,
       d.unit_simplified, d.temporal_type, f.source_vintage
FROM analytics.fact_housing_observation AS f
LEFT JOIN metadata.variable_dictionary AS d
  ON f.variable_id = d.variable_id
WHERE f.geo_id = 'COUNTY_06037'
  AND f.variable_id = 'zori_smoothed_sa'
ORDER BY f.observation_date;
```

`f` and `d` are short names for the two tables in this query. The left join keeps
observation rows even if a definition is missing, making the missing metadata
visible. The dictionary must have one row per variable ID. Check that assumption:

```sql
SELECT variable_id, count(*) AS rows_per_id
FROM metadata.variable_dictionary
GROUP BY variable_id
HAVING count(*) > 1;
```

Expect no rows for the maintained dictionary. If duplicates appear, investigate
before joining: otherwise a join can multiply observations. For two data tables,
first identify their full keys—geography, period, variable, variant, and cutoff
where applicable. Do not join different frequencies on a year alone and silently
replicate values into monthly observations.

## 7. Check coverage, missing values, and row keys

Count usable values separately from the number of rows:

```sql
SELECT county_fips,
       min(date)::DATE AS first_month,
       max(date)::DATE AS last_month,
       count(*) AS rows,
       count(zori) AS rent_values,
       count(*) - count(zori) AS missing_rent_values,
       min(date) FILTER (WHERE zori IS NOT NULL)::DATE AS first_rent_month
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
GROUP BY county_fips;
```

`count(*)` counts rows; `count(zori)` counts only non-null rent values. The panel
can start before the rent series does. A quality flag marked `ok` does not mean
every measure in that row is populated.

Check the county/month key before constructing lags or merging predictors:

```sql
SELECT county_fips, date, count(*) AS rows_per_key
FROM analytics.county_monthly_panel
GROUP BY county_fips, date
HAVING count(*) > 1;
```

No returned rows means no duplicates for that key. For a ZIP panel, use
`postal_zip, date`. For finance tables, inspect `DESCRIBE` and the
[finance contracts](housing_finance_versions.md) for variants and cutoffs.

Missing values are information about coverage. Do not replace them with zero,
interpolate them, or fill them from another geography without a separately
justified and documented analysis method. Stock/flow labels do not authorize
a universal aggregation rule; Redfin rolling-three-month windows overlap.

## 8. Use the query result in Python

The repository supplies a small reproducible example:

- [sql/model_input.sql](../sql/model_input.sql) selects LA County monthly `zori`
  from 2020 onward, keeping dates, identifiers, missing values, and source units.
- [scripts/load_model_data.py](../scripts/load_model_data.py) reads that SQL,
  connects to `housing.duckdb` in read-only mode, and returns a pandas DataFrame.
  A DataFrame is a table in Python memory; no CSV is involved.

First, you can run the saved query at DuckDB's **`D` prompt**:

```text
.read sql/model_input.sql
```

This dot command has no semicolon. Its path is relative to the project folder
where you started DuckDB. To save your own SQL, open Notepad, paste a complete
SQL statement, and choose **File → Save As** inside the project's `sql` folder.
Choose **All files**, a name such as **my_analysis.sql**, and UTF-8 encoding.
Use `.read sql/my_analysis.sql` to run it; ensure it was not saved as `.sql.txt`.

Now enter **`.quit`** to return to **PowerShell**, then run:

```powershell
.\.venv\Scripts\python.exe scripts/load_model_data.py
```

On macOS/Linux, the equivalent Terminal command is:

```bash
.venv/bin/python scripts/load_model_data.py
```

Expect a row count, date range, null counts, and the first ten rows. Exact values
depend on your snapshot. An empty result remains empty. The helper reads the
database and SQL relative to its own project location and refuses a missing
database rather than creating one.

To use the returned table in your own Python analysis:

1. Open **Notepad** and paste the following **Python code**, not at a `D` or `PS`
   prompt.
2. Save it in the **project root**, beside `README.md`, as **model_example.py**,
   choosing **All files** and UTF-8 encoding.

```python
from scripts.load_model_data import load_model_data

data = load_model_data()
print(data.head())
print(data.dtypes)
print(data.isna().sum())
```

3. In project **PowerShell**, run:

```powershell
.\.venv\Scripts\python.exe model_example.py
```

The `data` variable is available to later code in that script for plotting,
feature design, training, and evaluation. The example performs no model fitting
or forecasting. Adapt the saved SQL selection deliberately; keep a `date` column
if using the supplied helper's command-line summary. Record the SQL, database
build/vintage, and chosen sample with your analysis. DuckDB's
[Python conversion reference](https://duckdb.org/docs/current/clients/python/conversion)
documents the `.df()` operation used by the helper.

## 9. Prepare for forecasting without using future information

If you followed the Python example, your window is back in PowerShell. Reopen
DuckDB with the command from section 1 and confirm the **`D` prompt** before
running this section's SQL.

The general panels are useful for retrospective analysis, but filtering an
observation date to the past does **not** reconstruct what was known at that time.
Provider revisions and later-retrieved source files can still enter those rows.
Version B is explicitly retrospective. A chronological train/test split alone
does not remove this kind of future-information leakage.

For Version C, first list the information sets actually stored:

```sql
SELECT information_cutoff_date::DATE AS cutoff,
       count(*) AS rows,
       min(month)::DATE AS first_month,
       max(month)::DATE AS last_month
FROM analytics.housing_finance_monthly_rt
GROUP BY information_cutoff_date
ORDER BY information_cutoff_date;
```

Choose **one explicit cutoff** from those results for a reproducible extraction.
The reviewed workspace contains cutoff `2026-08-17`; a different snapshot may
contain different cutoffs. The example below is a modeled mortgage-payment
estimate, not an observed loan-payment series:

```sql
SELECT month::DATE AS month, county_fips, variable_id, estimate_variant,
       value, unit, temporal_type, information_cutoff_date,
       availability_date, availability_date_basis,
       is_modeled, is_nowcast, is_carried_forward, is_scenario,
       lookahead_safe, quality_flag, lineage_id
FROM analytics.housing_finance_monthly_rt_labeled
WHERE county_fips = '06037'
  AND information_cutoff_date = DATE '2026-08-17'
  AND variable_id = 'acs_estimated_aggregate_monthly_first_mortgage_pi_monthly_rt'
  AND estimate_variant = 'central'
  AND lookahead_safe = TRUE
ORDER BY month;
```

Keep the availability, vintage/lineage, and status fields alongside the values.
An August 31 month label can be a modeled nowcast at an August 17 cutoff; the
label is not evidence that later observed inputs were available. The pipeline's
cutoff and source-availability rules determine eligibility. Do not add an arbitrary
`month <= cutoff` filter as a substitute for those rules.

Before training or claiming forecast performance:

- Define the forecast date, horizon, target, geography, and units. Separate
  observed targets from modeled estimates, sensitivities, and proxies.
- Use only inputs available at the forecast date and retain the applicable source
  vintages/cutoffs. One stored Version C cutoff cannot support a historical
  rolling-vintage backtest; absent earlier information sets remain unavailable.
- Check exact monthly spacing before constructing lags. The previous row is not
  necessarily the previous calendar month. Preserve missing observations.
- Fit preprocessing and model parameters using training data only, and evaluate
  on later periods. Full-history normalization, revised inputs, or a later cutoff
  can otherwise leak future information into the test period.
- Keep ZIP/ZCTA/geography definitions and source-specific frequencies intact.
  Overlapping Redfin windows and ACS multi-year estimates are not independent
  monthly flows. Do not sum indices, stock levels, or interest rates across time.
- Keep licensed source restrictions with direct SQL results. Read-only access
  protects database contents; it does not impose the public-export filters.

See [finance versions](housing_finance_versions.md),
[source methods](source_definitions_and_methods.md), and
[county/ZIP model definitions](county_and_zip_housing_indicators.md) for the
implemented availability and transformation rules. This guide prepares inputs;
it does not validate a particular forecasting model.

## 10. View tables in DuckDB's graphical interface

The SQL client above works locally without a browser. DuckDB also provides a
graphical SQL interface through its `ui` extension. This is a DuckDB table viewer
and SQL editor, distinct from the project's CSV download page.

The UI needs a writable database for its notebook storage, so do not open
`housing.duckdb` directly with `-ui -readonly`. Instead, keep the housing database
read-only and use a separate local notebook file, following DuckDB's
[documented read-only UI setup](https://duckdb.org/docs/current/core_extensions/ui).

1. At an existing DuckDB **`D` prompt**, enter `.quit` to return to PowerShell.
   Stay in the project folder.
2. In **PowerShell**, start a separate UI workspace:

```powershell
.\.venv\Scripts\duckdb.exe .\database\sql_workspace.duckdb
```

On macOS/Linux, use `.venv/bin/duckdb database/sql_workspace.duckdb` in Terminal.
This creates the notebook file if absent. It is excluded from Git and is not a
second copy of the housing observations.

3. At its **`D` prompt**, enter these SQL statements separately:

```sql
ATTACH 'database/housing.duckdb' AS housing (READ_ONLY);
```

```sql
USE housing;
```

```sql
CALL start_ui();
```

The first launch may download the official extension and interface assets, so it
needs working network access. A browser tab should open at the local UI address,
normally `http://localhost:4213`. Keep the DuckDB terminal running.

4. In the UI's database list, expand **housing → analytics**, select a table such
   as **county_monthly_panel**, and use **Preview data** to view rows. Open a SQL
   notebook/cell to run the queries from this guide against `housing`. DuckDB's
   [UI overview](https://duckdb.org/2025/03/12/duckdb-ui) illustrates these controls.
5. For a new session, reopen the same workspace file and attach `housing` again;
   attachments are session-specific. When finished, return to the **`D` prompt**,
   run `CALL stop_ui_server();`, then `.quit`.

If the web interface returns HTTP 500 and reports a TLS verification error for
`ui.duckdb.org`, use the SQL-client route in section 1 while resolving the
network/certificate issue. Do not disable
certificate verification to load the UI. Windows ARM is also not supported by the
UI extension according to its current documentation.

## Troubleshooting

| What happens | What to do |
|---|---|
| `duckdb.exe` is not found | Use project PowerShell and install `requirements.txt`; the Python `duckdb` package alone is insufficient. |
| The database cannot be opened / does not exist | Check the working folder and exact `database/housing.duckdb` path. Keep `-readonly` so a typo cannot create an empty database. |
| PowerShell rejects `SELECT` | Open DuckDB first; SQL belongs at `D`. |
| DuckDB waits instead of showing results | Finish SQL with `;`, or cancel with Ctrl+C and paste the complete statement. |
| Table or column does not exist | Run sections 2–3; check that the snapshot has the product and matches the checkout. |
| No rows | Inspect geography, date range, variable ID, and cutoff; metadata definitions alone do not prove observations exist. |
| The table list or another result omits rows/names | At `D`, enter `.mode table` without a semicolon and rerun the query, as in section 2. Scroll up for earlier rows. Use `.mode line` for wide results and `.mode duckbox` to restore previews. A SQL `LIMIT` still restricts the result. |
| Database is locked | Close other applications/builders using it before reopening. Stop all readers before a rebuild. |
| Cannot create or change a table | The supplied research database is opened read-only. Keep your model results separately from it. |
| Python cannot import `scripts` | Save and run `model_example.py` from the project root, as described in section 8. |

[Project setup](../README.md) · [Data inventory](data_inventory_and_limitations.md) ·
[CSV alternative](query_and_export_commands.md#browser-downloads) ·
[Build and refresh](database_build_and_refresh.md)
