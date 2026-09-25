# LA housing database: reproducible command reference

For dropdowns and CSV download buttons, start with the
[point-and-click guide](query_and_csv_guide.md). This optional command reference
supports saved, repeatable queries. It starts with the **Variable Inventory** sheet in
`LA_Housing_Database_Overview.xlsx`, the workbook used with Clement. It covers
both finding variables and downloading their data. All examples use the local
database snapshot; they do not download new releases from providers.

The workbook contains 482 variable IDs. The current database dictionary contains
558, including all 482 workbook IDs. For those shared IDs, Domain, Layer, Proxy,
Constructed, Geography, Native frequency, and Final frequency agree with the
workbook. The same filters also work for the 76 newer variables.

## 1. Open the project in PowerShell

Open **Windows PowerShell** and paste these two lines:

```powershell
Set-Location -LiteralPath 'C:\Users\yifan\Downloads\la_housing_database'
$HousingPython = '.\.venv\Scripts\python.exe'
```

If the project is saved elsewhere, change the folder on the first line.
Keep this PowerShell window open for the following steps. The `&` at the start
of each command runs the project's Python environment.

Check that the query tool starts:

```powershell
& $HousingPython run_query.py --help
```

You should see the available commands. If PowerShell cannot find Python,
check that the project folder contains `.venv\Scripts\python.exe`.
If it cannot find the database, check for `database\housing.duckdb`.

## 2. Translate workbook columns into query filters

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

Use the exact Domain values, including underscores. Domain values are:

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

**Geography matters:** `county` means the county geography type;
`COUNTY_06037` identifies Los Angeles County. Postal ZIP and Census ZCTA are
different geography choices. Geography selection restricts actual data rows
when exporting observations or tables. It does not substitute national or
statewide controls for missing county observations.

## 3. List the available choices and datasets

List the current choices directly from the database:

```powershell
& $HousingPython run_query.py options --field domain
& $HousingPython run_query.py options --field layer
& $HousingPython run_query.py options --field frequency
& $HousingPython run_query.py options --field geography
```

List the datasets you can download:

```powershell
& $HousingPython run_query.py datasets
```

The tool offers two data shapes:

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

Open `output\exports\my_construction_variables.csv` to see the selected variable
IDs, names, definitions, original and simplified units, temporal types, and
classifications. This example finds the Central and Slow construction proxies.

For a second example, find monthly postal-ZIP rental-market variables:

```powershell
& $HousingPython run_query.py catalog --geography postal_zip --frequency monthly --domain rental_market --output output/exports/my_zip_rental_variables.csv
```

Different options combine with **AND**. Repeating a multiple-choice option
selects alternatives within it. For example:

```powershell
& $HousingPython run_query.py catalog --domain rental_market --domain for_sale_market --frequency monthly --output output/exports/my_monthly_market_variables.csv
```

This selects monthly variables from either of those two domains. If an
intersection has no results, remove one condition or check the available choices.

## 5. Download a table as a CSV

### A. Choose a table

Common table choices are:

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

Download all classified measures in the LA County monthly panel:

```powershell
& $HousingPython run_query.py table --dataset county-monthly --output output/exports/my_la_county_monthly.csv
```

The command prints the number of rows and the saved file paths. It produces:

- `my_la_county_monthly.csv`: the data table.
- `my_la_county_monthly.variables.csv`: the companion dictionary linking each
  selected data column to its variable ID, unit, temporal type, and definition.

Date, geography, quality, and provenance fields remain in the table. Unregistered
measure columns, if any, are named in a message and omitted from this classified
export. The existing underlying database tables remain unchanged.

### C. Add geography, dates, or variable filters

Download the county construction proxies from 2020 onward:

```powershell
& $HousingPython run_query.py table --dataset county-monthly --geo-id COUNTY_06037 --frequency monthly --domain construction --layer General --proxy true --constructed true --start-date 2020-01-01 --output output/exports/my_la_construction_proxies.csv
```

Download one ZIP's rent index:

```powershell
& $HousingPython run_query.py table --dataset zip-monthly --geo-id ZIP_90024 --variable zori_smoothed_sa --start-date 2020-01-01 --output output/exports/my_90024_rent.csv
```

Dates use `YYYY-MM-DD`. Both start and end dates are inclusive; add
`--end-date 2025-12-31` when you want a fixed endpoint.

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

```powershell
& $HousingPython run_query.py observations --dataset main --geography county --geo-id COUNTY_06037 --frequency monthly --domain for_sale_market --start-date 2020-01-01 --output output/exports/my_la_monthly_market_observations.csv
```

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
observations:

```powershell
& $HousingPython run_query.py catalog --layer "Version B" --domain finance --frequency monthly --output output/exports/my_version_b_variables.csv
& $HousingPython run_query.py observations --dataset version-b --layer "Version B" --domain finance --frequency monthly --geo-id COUNTY_06037 --output output/exports/my_version_b_la.csv
```

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

## 7. Locate and open the downloaded CSVs

Files in these examples are saved under:

```text
C:\Users\yifan\Downloads\la_housing_database\output\exports
```

To open a CSV in Excel while preserving identifiers:

1. Open Excel and choose **Data → From Text/CSV**.
2. Select the CSV and choose **Transform Data** if Excel proposes numeric
   types for identifier fields.
3. Set FIPS, postal ZIP, ZCTA, and similar identifier columns to **Text**.
4. Load the table, then open its `.variables.csv` companion the same way.

An identifier such as county FIPS `06037` must retain its leading zero. The CSV
contains the correct identifier; Excel's automatic numeric conversion can change
its display. The table and its companion dictionary are ordinary CSV files that
you can share together.

The tool does not overwrite existing exports. If a filename already exists,
choose another name, such as `my_la_county_monthly_v2.csv`. To create typed
Parquet instead, change `.csv` to `.parquet`; the companion dictionary remains CSV.

## 8. Common messages

| Message or result | What to do |
|---|---|
| Output already exists | Use a new filename. |
| No matching variables/columns | Check exact choices; loosen the filter intersection. |
| Variable has no observations in this dataset | Check its Layer and try the appropriate long dataset or wide table. |
| Empty date/geography slice | Check that the selected place and dates occur in that dataset. Missing data are not filled. |
| Unclassified columns omitted | The message identifies additional table fields outside the configured variable inventory. |
| Restricted HUD-USPS download refused | These licensed observations remain outside ordinary CSV exports; select public variables instead. |

Use `& $HousingPython run_query.py table --help` or
`& $HousingPython run_query.py observations --help` to see all options.
The [unit and temporal-type reference](variable_units_temporal_types.md) explains
the classifications and their aggregation limits.
