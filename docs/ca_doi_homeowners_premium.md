# California DOI annual homeowners premium control

## Source and scope

The sole source is the California Department of Insurance (CDI)
[Residential Property Insurance Report](https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/),
Part I Premium and Exposure Summary Data. The configured download is CDI's
official `A-Part-I-Data-Summary.xlsx` workbook. It is public and requires no
login, API key, or paid subscription.

The active series is a California annual state control for exposure years
2008-2023. It is not an estimate of Los Angeles County premiums, not a premium
quote for a particular property, and not a monthly observation.

## Construction

The workbook's rendered worksheets are pivot-table views. The cleaner reads
the OOXML pivot-cache definition and records directly, then:

1. Requires source fields `EXP_YEAR`, `POLICY_TYPE`, `WP`, and `WE`.
2. Filters `POLICY_TYPE` exactly to `Homeowners Multi-Peril`.
3. Sums every insurance-coverage range by `EXP_YEAR`.
4. Calculates average written premium as `sum(WP) / sum(WE)`.
5. Emits three annual California observations for each year:
   `ca_homeowners_written_premium`,
   `ca_homeowners_written_exposures`, and
   `ca_homeowners_avg_written_premium`.

The three direct annual outputs are nominal. The cleaner fails if the expected 2008-2023
coverage changes, a required field/value is missing, a shared-item reference
is invalid, record counts disagree, or annual exposures are nonpositive.
Every raw workbook vintage is archived unchanged with retrieval timestamp,
source filename, byte size, and SHA-256 checksum in the raw-file manifest.

The approved deflator is the California Department of Industrial Relations
California CPI-U All Items annual average, `ca_cpi_u_all_items_annual`
(`1982-1984=100`). Its cleaner reads the official `EntireCCPI.PDF`, retains
only rows labeled `Annual`, and never calculates annual averages from
subannual rows. The active archived vintage contains 71 annual observations
for 1955-2025. It is a California state index, not the separate Los Angeles
metro CPI series.

## Pipeline commands

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source ca_doi_residential_property_part_i --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage clean --source ca_doi_residential_property_part_i
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source ca_dir_california_cpi --force-download
.\.venv\Scripts\python.exe run_pipeline.py --stage clean --source ca_dir_california_cpi
```

Neither official source requires a login, API key, paid subscription, or
browser session. The generic XLSX downloader handles the CDI workbook, and the
DIR PDF downloader archives the static official PDF URL as a new immutable
vintage whenever its bytes change.

## Downstream controls

The analytical build creates three explicitly documented transformations:

- `ca_homeowners_avg_written_premium_yoy` is the exact annual change for
  consecutive source years. It has 15 values for 2009-2023.
- `ca_homeowners_avg_written_premium_monthly_control` repeats each annual
  exposure-year value across that calendar year's 12 month ends. The 192 rows
  remain `STATE_06`, retain the annual source observation and raw provenance,
  use `native_frequency=annual` and `final_frequency=monthly`, and set
  `quality_flag=carried_annual_control;mixed_frequency_control` and
  `is_interpolated=false`. They are constructed carried-annual controls, not
  observed monthly insurance data or interpolation. They are available in the long fact and
  `analytics.state_monthly_controls`.
- `ca_homeowners_avg_written_premium_real` is the annual statewide average in
  constant 2023 dollars:
  `nominal_premium_year * California_CPI_U_2023 / California_CPI_U_year`.
  Nominal premium and CPI must be positive and matched on the exact calendar
  year. The 2023 real value therefore equals the 2023 nominal value. Missing
  inputs remain null; CPI is never interpolated and no other geography is
  substituted.

The calendar-year step function follows the governing document literally; it
is not release-date-aware and is never linearly interpolated or relabeled as
LA County data. The canonical real-dollar series remains annual and is not
expanded to monthly observations. If a model needs an annual value carried
within year, that operation belongs in the analysis layer with an explicit
mixed-frequency/carried-annual-control flag.
