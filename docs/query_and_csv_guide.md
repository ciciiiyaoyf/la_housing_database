# Find housing data and download a CSV

The **LA Housing Data Browser** lets you choose a dataset, filter it, preview
the results, and download a CSV. Routine retrieval needs no GitHub, SQL,
DuckDB, or command-line knowledge.

Its filters correspond to the **Variable Inventory** sheet in the
[reference overview workbook](../outputs/database_system_overview_20260824/LA_Housing_Database_Outsider_Overview.xlsx),
used in the discussion with Clement.
All 482 workbook IDs are in the current 558-variable dictionary. For shared
IDs, Domain, Layer, Proxy, Constructed, Geography, and frequency labels match.

## 1. Open the browser

The launcher needs the project's `.venv` environment and an existing compatible
`database/housing.duckdb`. A fresh GitHub clone includes code, documentation,
and metadata, but does not include this database or the archived source data.
On another computer, follow the [README setup and pipeline instructions](../README.md)
to build the database, or provide a compatible public database snapshot at
that path. The steps below apply once that setup is complete.

1. In File Explorer, open
   `C:\Users\yifan\Downloads\la_housing_database`.
2. Double-click **Open_Data_Browser.cmd**.
3. Your browser opens the **LA Housing Data Browser** page.
4. Keep the launcher window open while using the page.

The page reads the existing local database snapshot. It does not fetch new
provider releases or change observations. When finished, close the page and
press **Ctrl+C** in the launcher window.

If the page does not open automatically, copy the local address displayed in
the launcher window into your browser. If the port is already in use, an
earlier session may be running: open `http://127.0.0.1:8765/` or close that
session's launcher and try again.

## 2. Choose a dataset

Open **Dataset** and choose a dataset. Useful starting points are:

| Dataset | Use it for |
|---|---|
| LA County housing — Monthly | Monthly LA County housing measures |
| LA and comparable counties — Monthly | Separate rows for LA and comparison counties |
| Postal ZIP housing — Monthly | Postal-ZIP housing observations |
| Census ZCTA controls — Annual | Annual Census ZCTA measures |
| County controls — Annual | Annual demographic and other county controls |
| State controls — Monthly | State-level monthly controls |
| Macro and financial controls — Monthly | Macro measures at their actual geography |
| Housing finance — Version A benchmark | Benchmark finance observations |
| Housing finance — Version B monthly history | Retrospective monthly finance observations |
| Housing finance — Version C real-time vintages | Monthly finance with information cutoffs |
| Homebuying cost scenarios | Modeled costs and scenario assumptions |

Changing datasets resets filters. Changing geography updates the other
dropdowns, preserves valid selections, and identifies selections reset to All.
Choose a specific **Variable** by its display name and ID, or leave
**All variables** selected. Variable choices belong to the selected dataset
and geography. For example, `zori_smoothed_sa` selects the active Zillow rent
index. A single-variable download keeps its dates, geography, and supporting
quality and provenance fields.
Dropdown choices come from the current database. Ordinary tables have a separate column for each measure. The
General-source and Version A/B/C observation datasets instead have a
`variable_id` and a `value` column, with one observation per row. Both download
as CSVs.

## 3. Choose geography and frequency

1. Choose **Geography type**, such as `county` or `postal_zip`, or keep All.
2. Choose a **Place**, or keep All to retain separate rows for every place.
3. Choose **Frequency**: for example, monthly, quarterly, or annual.
4. Optionally set **From** and **Through** dates; both endpoints are included.

| Place identifier | Meaning |
|---|---|
| `COUNTY_06037` | Los Angeles County |
| `COUNTY_06059` | Orange County |
| `STATE_06` | California |
| `ZIP_90024` | Postal ZIP 90024 |
| `ZCTA_90024` | Census ZCTA 90024, a separate geography |
| `US` | United States |

The Place dropdown lists actual dataset geographies. County selection does
not fill rows with statewide or national controls. Postal ZIP codes and
Census ZCTAs remain distinct.

**Frequency** selects the output frequency. For an analysis table, this is the
table's cadence, including measures built from inputs at other frequencies.
For the variable catalog, it corresponds to the workbook's Final frequency.
**Native frequency**, under **Units and temporal types**, refers to
the source. A monthly output may contain a carried annual control or an
overlapping three-month Redfin observation. These retain their source timing;
filtering does not convert or aggregate them. Source observations use their
actual row frequency labels, which can be more specific than the workbook;
for example, ACS rows can use `annual_5_year_estimate`.

## 4. Choose a variable or narrow its category

Leave a filter at **All** to avoid restricting it.

| Filter | How to use it |
|---|---|
| Variable | Choose a display name and variable ID for one measure, or keep All variables. |
| Domain | Choose the workbook's subject grouping, such as `rental_market`, `for_sale_market`, `construction`, `labor_market`, or `finance`. |
| Layer | Choose General, Version A, Version B, or Version C, with a compatible dataset. |
| Proxy variable | Yes means Proxy=True; No means Proxy=False. |
| Constructed variable | Yes means Constructed=True; No means Constructed=False. |

Proxy and Constructed are independent. A constructed measure need not be a
proxy, and unknown flags are not treated as No. Additional filters offer
simplified units, temporal type, and native frequency. Stock/flow labels do
not automatically choose a frequency-conversion rule.

**Example: LA monthly construction proxies**

1. Dataset: **LA County housing — Monthly**; Variable: **All variables**.
2. Geography type: **county**; Place: **COUNTY_06037**.
3. Frequency: **monthly**.
4. Domain: **construction**; Layer: **General**.
5. Proxy variable: **Yes**; Constructed variable: **Yes**.
6. From: **2020-01-01**; leave Through blank.

This selects the Central and Slow completion and under-construction proxies.
Filters combine with AND: adding a filter narrows the selection.

**Example: monthly rent for postal ZIP 90024**

1. Dataset: **Postal ZIP housing — Monthly**. This resets earlier filters.
2. Geography type: **postal_zip**; Place: **ZIP_90024**.
3. Variable: choose the Zillow Observed Rent Index entry ending in
   **(zori_smoothed_sa)**.
4. Frequency: **monthly**. Leave Domain, Layer, Proxy, Constructed, units,
   and temporal type on **All**.
5. Optionally set From and Through, or leave both blank for all available dates.
6. Click **Preview data** and confirm the place and dates. The rent measure may
   appear under the table column `zori`; its definitions file identifies it
   as `zori_smoothed_sa`, measured in USD/month.
7. Click **Download CSV**, then **Download variable definitions**.

This produces a single ZIP's rent series with its supporting fields. It does
not substitute county rent for missing ZIP observations.

## 5. Preview the results

Click **Preview data**. It shows up to 20 matching rows; scroll horizontally to see
more columns. Check geography, dates, and selected measure columns. A message
indicates when further rows exist.

An empty result means the selection has no matching rows or variables. Clear
one filter or try a compatible dataset. Missing values remain missing.
Some General-layer derived variables occur in analysis tables only; use the
corresponding table rather than the General-source observations dataset.
Version C retains all information cutoffs, so a variable/date may have multiple
vintages.

## 6. Download the table as a CSV

1. Click **Download CSV**.
2. Your browser saves the file in its usual download folder or asks where to
   save it, according to its settings.
3. Click **Download variable definitions** to save the matching dictionary.

The CSV contains **all matching rows**, not just the preview. For tables, the
definitions file maps selected columns to their variable IDs, units, temporal
types, and descriptions. Scenario definitions also identify applicable row
identifiers. Keep both CSVs together when sharing them.

Date, geography, quality, and provenance fields remain in exports. Any
unregistered additional measure columns are reported and omitted from
classified table exports. Licensed HUD-USPS observations remain outside
ordinary public downloads.

Repeated downloads follow your browser's filename behavior; the database is
never overwritten. Choose another dataset or change filters to download a
different table.

## 7. Open the CSV in Excel

1. In Excel choose **Data → From Text/CSV** and select the downloaded file.
2. If Excel proposes numbers for identifiers, choose **Transform Data**.
3. Set FIPS, postal ZIP, ZCTA, and similar identifier columns to **Text**.
4. Load the table. Open the definitions CSV in the same way.

This preserves leading zeros, such as the zero in FIPS `06037`. Double-clicking
a CSV can make Excel interpret identifiers as numbers even though the file
contains correct text.

## Repeatable queries and setup

The browser and command-line tool share the same query and export code.
For saved commands, multiple selected places, or Parquet output, use the
[command reference](query_command_reference.md). The
[temporal-type reference](variable_units_temporal_types.md) explains unit and
temporal classifications.

The launcher uses this project's existing `.venv` environment and database;
CSV recipients need neither. A fresh clone is not a ready-to-query database.
On another computer, complete the dependency setup in README and build the
database through the documented pipeline, or provide a compatible public
snapshot. The launcher does not install software, build the database, or
refresh source data.
