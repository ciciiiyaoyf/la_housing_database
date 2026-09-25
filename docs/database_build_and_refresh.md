# Database build and refresh

This guide is for the person maintaining the source archives and local database.
For first-time installation, complete the [README setup](../README.md#2-set-up-the-project).
For analysis, start with the [DuckDB SQL guide](duckdb_sql_guide.md).
For optional CSV downloads, use the [Data Browser instructions](query_and_export_commands.md#browser-downloads).
The commands here rebuild or update local research products; they are not a
complete database installation recipe for a fresh clone.

## Choose the right refresh command

Run commands in **Windows PowerShell inside the project folder**, after closing
database readers as explained below. Choose **one** workflow from this table;
the rows are alternatives, not a sequence to run from top to bottom.

| What you want to update | Command |
|---|---|
| All configured general sources and general database products | `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh` |
| The whole project, including rebuilding finance B and building/validating finance C | `.\.venv\Scripts\python.exe run_system_refresh.py` |
| One general source, for example LAUS | `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_laus_la_county` |
| Both Redfin county and ZIP sources together | `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin` |

Every `--stage refresh` command includes acquisition, cleaning, harmonization,
DuckDB rebuilding, validation, catalog updates, and CSV exports. **You do not
run a separate `clean` command after a successful refresh.** The integrated
`run_system_refresh.py` command includes that general refresh and the additional
finance work; running both all-source commands would repeat acquisition.

A source filter limits which provider inputs are downloaded and cleaned. Shared
general tables are then rebuilt using those inputs and the other sources already
stored locally. It does not make the other sources current or rebuild finance B/C.
For example, the combined ZIP panel still needs current Zillow ZIP-month keys to
include new Redfin months; the full accepted Redfin subset remains in its clean
and fact tables.

**Current Redfin limitation:** the county source has a provider-method review hold.
Selecting `redfin` includes that source and preserves the hold; it does not make
county acquisition compatible. A combined or all-source run cannot finish
successfully while that hold remains. See [Redfin downloads](#redfin-downloads).

## Refresh everything before modeling

For a provisioned research workspace, this is the normal command for updating the
configured general sources and rebuilding the database and monthly finance products.
The [dataset schedule below](#dataset-update-schedule) lists every source, its release
cadence, and the fixed-edition or manual exceptions. A provider's newest available
release can still describe an earlier month, quarter, or year.

**Where to run it:** in **Windows PowerShell**, inside `la_housing_database`, as
explained in [Open the working folder](#open-the-working-folder-and-run-commands).
Exit DuckDB with `.quit`, stop its graphical UI, stop the Data Browser with Ctrl+C,
and close Python sessions connected to the database. The runner checks database
access before downloading. It also requires the existing source archives and frozen
finance inputs described under [What a rebuild requires](#what-a-rebuild-requires).

For a whole-project update including finance B/C, enter **this one command**
and press Enter:

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py
```

It attempts current provider acquisition, checks and cleans sources, rebuilds
general analytical products and retrospective finance B, builds or validates the
finance C cutoff, registers the integrated database and unit/temporal metadata,
and runs QA and exports. The complete national Redfin ZIP CSV is about 1.3 GB and can take several minutes to download.
Automatic acquisition or structural failures stop the workflow and identify the
source or stage needing attention.

The default C cutoff is **today's UTC date**. If that cutoff already exists, its
immutable observations and lineage are validated and reused. Later downloads on
the same day can update general/B products, but cannot overwrite that C vintage.
Frozen Version A and its finance input vintages remain unchanged. For an explicit
historical cutoff, follow [Finance layers and integrated refresh](#finance-layers-and-integrated-refresh).

When the PowerShell prompt returns, immediately enter:

```powershell
$LASTEXITCODE
```

| Exit code | Meaning and next step |
|---|---|
| `0` | The configured refresh and integrated checks completed. Review coverage and the fixed-edition/finance limitations before selecting model inputs. |
| `2` | The rebuild completed, but manual provider-release checks remain. Read each `action_required` in the source report; the command has not verified all sources as current. |
| `1` | A source, preflight, or build failed. Products may be partly updated; use the recorded failure to resolve the issue before treating this as a completed refresh. |

Read `metadata/source_refresh_status.csv` for each source's acquisition result,
latest clean observation, and required action. Read the newest
`metadata/system_refresh_run_log.csv` row for `general_completed`,
`version_b_rebuilt`, `version_c_action`, `integrated_completed`, `status`, and `error`.
These fields record completed work, including whether C was newly built or reused.
The QA and coverage reports are under `output/qa/` and `output/coverage/`.

To diagnose database access, the frozen benchmark, and known source actions without
starting downloads or rebuilding, use:

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py --check
```

This preflight does not contact every provider or certify current releases.
Current Redfin limitations are explained under [Redfin downloads](#redfin-downloads).
The command runs when you invoke it; it does not install a recurring schedule.

## Dataset update schedule

This is the complete update list: **61 configured general source datasets, 8 separately managed finance inputs, and 4 database products**. The same source can support several tables; these entries describe acquisition and rebuild responsibilities, not 73 unrelated provider downloads.

**Observation frequency** describes the periods represented by the values. **Provider release cadence** describes when files or new vintages are published. For example, DOL claims observations are weekly but its bulk file is updated daily. The linked dataset names lead to official provider evidence; release calendars can change.

For a normal update, use [Refresh everything before modeling](#refresh-everything-before-modeling). The source-specific commands below are for refreshing or troubleshooting one source. They acquire and rebuild that source through the general pipeline; they do not replace an integrated finance B/C refresh.

**Where to enter commands:** first follow [Open the working folder and run commands](#open-the-working-folder-and-run-commands), then paste one complete command into **PowerShell in the project folder**. Close database readers before updating. On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`. Paths such as `C:\path\to\official_ahs.csv` are examples to replace with the actual downloaded file path.

| Refresh mode | Meaning |
|---|---|
| Automatic | The pipeline can request the configured official source; access errors and provider review holds still matter. |
| Manual | An official export or source-specific action is required; a routine command cannot certify a newer release. |
| Fixed edition | The command checks a named year, vintage, or file edition. Extending coverage requires a verified source/configuration review. |
| Frozen | Preserve the historical edition or benchmark and its immutable inputs. It is not supposed to advance during a routine refresh. |
| Derived | Recompute from eligible inputs; there is no separate provider download. |

A recent successful download is not proof that a provider published new observations. Read the action and notes, then check the refresh and coverage reports before selecting model inputs. In particular, the integrated runner preserves frozen A and existing C vintages.

### Interest rates

National series. Monthly modeling aggregates retain source-specific rules and exclude incomplete months where required.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [Effective Federal Funds Rate [FEDFUNDS]](https://fred.stlouisfed.org/series/FEDFUNDS)<br>`fred_fedfunds` | Monthly | Monthly observations added after month end; FRED may revise history | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_fedfunds` | See group note above. |
| [2-Year Treasury constant-maturity yield [DGS2]](https://www.federalreserve.gov/releases/h15/about.htm)<br>`fred_dgs2` | Daily | Business days; FRED may post later than the originating provider | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_dgs2` | See group note above. |
| [10-Year Treasury constant-maturity yield [DGS10]](https://www.federalreserve.gov/releases/h15/about.htm)<br>`fred_dgs10` | Daily | Business days; FRED may post later than the originating provider | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_dgs10` | See group note above. |
| [Baa corporate spread over 10-Year Treasury [BAA10Y]](https://fred.stlouisfed.org/series/BAA10Y)<br>`fred_baa10y` | Daily | Business days; FRED may post later than the originating provider | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_baa10y` | See group note above. |
| [Bank Prime Loan Rate [DPRIME]](https://www.federalreserve.gov/releases/h15/about.htm)<br>`fred_dprime` | Daily | Business days; FRED may post later than the originating provider | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_dprime` | See group note above. |
| [Secured Overnight Financing Rate [SOFR]](https://www.newyorkfed.org/markets/reference-rates/sofr)<br>`fred_sofr` | Daily | Business days; FRED may post later than the originating provider | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fred_sofr` | See group note above. |
| [Primary Mortgage Market Survey, weekly rates since 1971](https://www.freddiemac.com/pmms/docs/PMMS_Publication_Calendar.pdf)<br>`freddie_mac_pmms` | Weekly | Weekly on Thursday; Wednesday when Thursday is a US holiday | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source freddie_mac_pmms` | Also supplies finance sensitivity calculations. Weekly observations remain distinguishable from monthly summaries. |

### Employment and prices

Use the provider calendar for actual release dates; observations and publication dates differ.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [California and Los Angeles division CES industry employment and total-private average hourly earnings](https://www.bls.gov/schedule/news_release/metro.htm)<br>`bls_ces_ca_la_industry_earnings` | Monthly | Monthly; annual benchmark or seasonal revisions may also occur | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_ces_ca_la_industry_earnings` | See group note above. |
| [ETA 539 daily-updated bulk file: California weekly initial and continued claims](https://oui.doleta.gov/unemploy/DataDownloads.asp)<br>`dol_ca_ui_claims` | Weekly | Daily bulk-file updates; new claims observations are weekly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source dol_ca_ui_claims` | ETA539 revisions appear in the next daily file. California only; completed-month means require all component weeks. |
| [Local Area Unemployment Statistics, Los Angeles and seven comparable California counties](https://www.bls.gov/schedule/news_release/metro.htm)<br>`bls_laus_la_county` | Monthly | Monthly; annual benchmark or seasonal revisions may also occur | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_laus_la_county` | See group note above. |
| [California Consumer Price Index, All Urban Consumers, annual average](https://www.dir.ca.gov/oprl/CAPriceIndex.htm)<br>`ca_dir_california_cpi` | Annual | Bimonthly publication; annual-average series adds completed calendar years | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source ca_dir_california_cpi` | DIR states updates in even-numbered months. The database uses the published annual-average series. |
| [Current Employment Statistics, Los Angeles-Long Beach-Anaheim MSA](https://www.bls.gov/schedule/news_release/metro.htm)<br>`bls_ces_la_metro` | Monthly | Monthly; annual benchmark or seasonal revisions may also occur | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_ces_la_metro` | See group note above. |
| [CPI-U All Items, Los Angeles-Long Beach-Anaheim published area](https://www.bls.gov/cpi/news.htm)<br>`bls_cpi_la_metro` | Monthly | Monthly; annual benchmark or seasonal revisions may also occur | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_cpi_la_metro` | See group note above. |

### Income and output

Retain published vintage and units; state GDP and personal income do not become new monthly observations.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [SQGDP9 California real GDP all industries](https://www.bea.gov/news/schedule)<br>`bea_ca_real_gdp` | Quarterly | Quarterly with annual revisions | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bea_ca_real_gdp` | See group note above. |
| [SQINC1 Quarterly Personal Income by State](https://www.bea.gov/news/schedule)<br>`bea_state_personal_income` | Quarterly | Quarterly with annual revisions | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bea_state_personal_income` | See group note above. |
| [CAINC1 Annual Personal Income by County](https://www.bea.gov/news/schedule)<br>`bea_county_personal_income` | Annual | Annual with revisions | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bea_county_personal_income` | See group note above. |

### Home values and rents

For Zillow, fetch the current complete CSV, including provider history revisions. Only smoothed seasonally adjusted ZORI is active.

For FHFA, selected state/metro series are quarterly; the county developmental index remains annual.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [County ZHVI, all homes, middle tier, smoothed and seasonally adjusted](https://www.zillow.com/research/data/)<br>`zillow_county_zhvi` | Monthly | Monthly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source zillow_county_zhvi` | See group note above. |
| [ZIP ZHVI, all homes, middle tier, smoothed and seasonally adjusted](https://www.zillow.com/research/data/)<br>`zillow_zip_zhvi` | Monthly | Monthly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source zillow_zip_zhvi` | See group note above. |
| [County ZORI, all homes plus multifamily, smoothed and seasonally adjusted](https://www.zillow.com/research/data/)<br>`zillow_county_zori_smoothed_sa` | Monthly | Monthly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source zillow_county_zori_smoothed_sa` | See group note above. |
| [ZIP ZORI, all homes plus multifamily, smoothed and seasonally adjusted](https://www.zillow.com/research/data/)<br>`zillow_zip_zori_smoothed_sa` | Monthly | Monthly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source zillow_zip_zori_smoothed_sa` | See group note above. |
| [Purchase-Only House Price Index by State](https://www.fhfa.gov/data/hpi)<br>`fhfa_hpi_state_purchase_only` | Quarterly | Quarterly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fhfa_hpi_state_purchase_only` | See group note above. |
| [Purchase-Only House Price Index for the 100 Largest Metropolitan Areas](https://www.fhfa.gov/data/hpi)<br>`fhfa_hpi_metro_purchase_only` | Quarterly | Quarterly | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fhfa_hpi_metro_purchase_only` | See group note above. |
| [Developmental All-Transactions House Price Index by County](https://www.fhfa.gov/data/hpi)<br>`fhfa_hpi_county_annual` | Annual | Annual observations; publication/revision dates require provider review | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source fhfa_hpi_county_annual` | See group note above. |

### Home sales and listings

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [Housing Market Tracker monthly county data](https://www.redfin.com/news/data-center/downloads/)<br>`redfin_county_monthly` | Monthly | Monthly product; provider may revise history | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin_county_monthly` | Legacy county endpoint has a configured provider-release review hold. Download success cannot clear that hold. |
| [Housing Market Tracker ZIP measures, rolling three months](https://www.redfin.com/news/data-center/downloads/)<br>`redfin_zip_monthly` | Overlapping 3-month windows | Monthly product; provider may revise history | **Automatic public-file download:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin_zip_monthly` | Resolves the official CSV from the provider index; see [Redfin downloads](#redfin-downloads) for versioned units and raw-only fields. National CSV stays raw-only; cleaning uses the versioned LA County ZIP reference. |

### Construction

Discover official period files, recheck the newest eligible period, and retain checksum-valid historical archives. Missing required files or download errors stop refresh.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [Building Permits Survey county monthly archive](https://www.census.gov/construction/bps/schedule.html)<br>`census_bps_county` | Monthly | Monthly; county/state data follow the revised-permits release | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_bps_county` | See group note above. |
| [Building Permits Survey final annual county files, 1990-2025](https://www.census.gov/construction/bps/schedule.html)<br>`census_bps_county_annual_final` | Annual | Annual final release, usually in May | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_bps_county_annual_final` | See group note above. |
| [Building Permits Survey state monthly archive](https://www.census.gov/construction/bps/schedule.html)<br>`census_bps_state` | Monthly | Monthly; county/state data follow the revised-permits release | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_bps_state` | See group note above. |

### Population and housing stock

The Vintage 2019 sources are historical baselines. Retain them; do not replace their archived files with a newer vintage.

For the Vintage 2025 sources, re-download checks that named edition only. A newer vintage requires an explicit source/configuration and comparability review.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [Vintage 2019 annual county resident population estimates](https://www.census.gov/programs-surveys/popest/about/schedule.html)<br>`census_pep_county_population_v2019` | Annual | Annual vintage releases; this source ID selects a fixed vintage | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_pep_county_population_v2019` | See group note above. |
| [Vintage 2025 annual county resident population estimates](https://www.census.gov/programs-surveys/popest/about/schedule.html)<br>`census_pep_county_population_v2025` | Annual | Annual vintage releases; this source ID selects a fixed vintage | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_pep_county_population_v2025` | See group note above. |
| [Vintage 2019 annual county housing-unit estimates for California](https://www.census.gov/programs-surveys/popest/about/schedule.html)<br>`census_pep_county_housing_units_v2019` | Annual | Annual vintage releases; this source ID selects a fixed vintage | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_pep_county_housing_units_v2019` | See group note above. |
| [Vintage 2025 annual county housing-unit estimates for California](https://www.census.gov/programs-surveys/popest/about/schedule.html)<br>`census_pep_county_housing_units_v2025` | Annual | Annual vintage releases; this source ID selects a fixed vintage | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_pep_county_housing_units_v2025` | See group note above. |

### Insurance

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [Residential Property Insurance Report Part I Premium and Exposure Summary Data](https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/)<br>`ca_doi_residential_property_part_i` | Annual | Report releases containing annual observations; fixed release date not verified | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source ca_doi_residential_property_part_i` | Refresh the provider workbook and inspect its last data year; report release timing need not match the insured year. |

### Wealth and debt surveys

Re-download checks this edition only. A later reference year needs a verified workbook and schema/configuration update.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [2025 Survey of Income and Program Participation state debt tables, 2024 reference period](https://www.census.gov/topics/income-poverty/wealth/data/tables.html)<br>`census_sipp_2025_state_debt_2024` | Annual reference-year table | Annual survey/table releases; this source selects 2025 SIPP / 2024 reference year | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_sipp_2025_state_debt_2024` | See group note above. |
| [2025 Survey of Income and Program Participation state wealth tables, 2024 reference period](https://www.census.gov/topics/income-poverty/wealth/data/tables.html)<br>`census_sipp_2025_state_wealth_2024` | Annual reference-year table | Annual survey/table releases; this source selects 2025 SIPP / 2024 reference year | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_sipp_2025_state_wealth_2024` | See group note above. |
| [2023 American Housing Survey Table Creator Table 14A Mortgage Characteristics, California](https://www.census.gov/programs-surveys/ahs/about.html)<br>`census_ahs_2023_ca_table14a` | Biennial survey year | Biennial survey program; this source selects the 2023 California table | **Manual:** Export official AHS California Table 14A CSV with margins of error, then run `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_ahs_2023_ca_table14a --local-file "C:\path\to\official_ahs.csv"` | Interactive export required. Review coverage and configuration before adding a new survey year; do not overwrite the old archive. |

### Population and migration

Manual source-release check required. Preserve suppression markers, geography and source definition; do not replace missing releases with provisional data.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [SOI Tax Stats County-to-County Migration Data, Los Angeles County](https://www.irs.gov/statistics/soi-tax-stats-migration-data)<br>`irs_soi_county_migration` | Annual | Annual migration-year releases with publication lag | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source irs_soi_county_migration` | Discover complete official inflow/outflow pairs and recheck the newest published pair. Do not treat a migration year as its release year. |
| [Natality, 2007-2024, year by county](https://wonder.cdc.gov/natality-current.html)<br>`cdc_wonder_natality_county` | Annual | Final annual vintages when released; no fixed publication day verified | **Manual:** Use the official CDC WONDER web export and the source-specific provenance/archiving workflow. No generic --local-file importer is implemented. | See group note above. |
| [Underlying Cause of Death, 1999-2020, Los Angeles County annual deaths](https://wonder.cdc.gov/ucd-icd10.html)<br>`cdc_wonder_ucd_1999_2020_la_county` | Annual | Closed historical edition (1999-2020); no new observation years expected | **Frozen:** Retain the official historical export and provenance; this fixed edition has no routine forward update. | Historical mortality baseline; the newer single-race edition is a separate source with an explicit overlap policy. |
| [Underlying Cause of Death, 2018-2024, year by county](https://wonder.cdc.gov/ucd-icd10-expanded.html)<br>`cdc_wonder_ucd_2018_2024_county` | Annual | Final annual vintages when released; no fixed publication day verified | **Manual:** Use the official CDC WONDER web export and the source-specific provenance/archiving workflow. No generic --local-file importer is implemented. | See group note above. |

### Housing insecurity

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [County eviction estimates, 2000-2018](https://evictionlab.org/methods/)<br>`eviction_lab_county_estimates` | Annual | Historical research release covering 2000-2018; no rolling update schedule | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source eviction_lab_county_estimates` | Re-downloading cannot add recent eviction counts. Any successor needs a separate coverage/method review. |
| [2007-2025 Point-in-Time Estimates by Continuum of Care](https://www.huduser.gov/portal/datasets/ahar.html)<br>`hud_pit_coc_2007_2025` | Annual point-in-time count | Annual PIT/AHAR release; this source selects the 2007-2025 workbook | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source hud_pit_coc_2007_2025` | Review the next official edition and update configuration. CoC geography is not county geography. |

### Vacancy

Refresh checks this filename only. Review the rates page and configure the next edition when published; do not splice definitions silently.

Retain the historical edition; newer years belong to a separately configured current edition.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [HVS Rental Vacancy Rates by State, 2005-present](https://www.census.gov/housing/hvs/data/faq.html)<br>`census_hvs_state_rental_vacancy` | Quarterly | Quarterly releases; this source selects a named historical/current file edition | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_hvs_state_rental_vacancy` | See group note above. |
| [HVS Rental Vacancy Rates for 75 Largest MSAs, 2005-2014](https://www.census.gov/housing/hvs/data/faq.html)<br>`census_hvs_metro_rental_vacancy_2005_2014` | Quarterly | Quarterly releases; this source selects a named historical/current file edition | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_hvs_metro_rental_vacancy_2005_2014` | See group note above. |
| [HVS Rental Vacancy Rates for 75 Largest MSAs, 2015-2025](https://www.census.gov/housing/hvs/data/faq.html)<br>`census_hvs_metro_rental_vacancy_2015_2025` | Quarterly | Quarterly releases; this source selects a named historical/current file edition | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_hvs_metro_rental_vacancy_2015_2025` | See group note above. |
| [HVS Rental Vacancy Rates for 75 Largest MSAs, 2026](https://www.census.gov/housing/hvs/data/faq.html)<br>`census_hvs_metro_rental_vacancy_2026` | Quarterly | Quarterly releases; this source selects a named historical/current file edition | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_hvs_metro_rental_vacancy_2026` | See group note above. |
| [HUD aggregated USPS administrative data on residential address vacancies](https://www.huduser.gov/portal/datasets/usps.html)<br>`hud_usps_ncwm_quarterly` | Quarterly | Quarterly | **Fixed edition:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source hud_usps_ncwm_quarterly` | Strict refresh requires HUDUSER_API_TOKEN even when archives exist, to verify the latest provider period. A release newer than the approved 2026Q1 geography/crosswalk scope blocks refresh until reviewed. Restricted data stay outside public exports. |

### ACS demographic and housing tables

Official group-endpoint discovery checks newest published vintage. Census API access rules apply.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [ACS 5-Year Detailed Table B05002 Place of Birth by Nativity and Citizenship Status, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b05002` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b05002` | See group note above. |
| [ACS 5-Year Detailed Table B07001 Geographical Mobility in the Past Year by Age for Current Residence, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b07001` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b07001` | See group note above. |
| [ACS 5-Year Detailed Table B11001 Household Type, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b11001` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b11001` | See group note above. |
| [ACS 5-Year Detailed Table B19013 Median Household Income, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b19013` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b19013` | See group note above. |
| [ACS 5-Year Detailed Table B25001 Housing Units, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25001` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25001` | See group note above. |
| [ACS 5-Year Detailed Table B25002 Occupancy Status, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25002` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25002` | See group note above. |
| [ACS 5-Year Detailed Table B25003 Tenure, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25003` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25003` | See group note above. |
| [ACS 5-Year Detailed Table B25004 Vacancy Status, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25004` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25004` | See group note above. |
| [ACS 5-Year Detailed Table B25064 Median Gross Rent, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25064` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25064` | See group note above. |
| [ACS 5-Year Detailed Table B25077 Median Home Value, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25077` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25077` | See group note above. |
| [ACS 5-Year Detailed Table B25081 Mortgage Status, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25081` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25081` | See group note above. |
| [ACS 5-Year Detailed Table B25032 Tenure by Units in Structure, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25032` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25032` | See group note above. |
| [ACS 5-Year Detailed Table B25127 Tenure by Year Structure Built by Units in Structure, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25127` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25127` | See group note above. |
| [ACS 5-Year Detailed Table B25106 Tenure by Housing Costs as a Percentage of Household Income, 2010-2024 vintages](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)<br>`census_acs_5year_b25106` | Annual vintage / 5-year estimate | Annual release of overlapping five-year estimates | **Automatic:** `.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source census_acs_5year_b25106` | See group note above. |

### Separate finance inputs

Version A file hashes are verified, not rebuilt, by run_system_refresh.py.

Retain the relationship vintage compatible with its PUMS geography.

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [ACS five-year county finance benchmark tables](https://www.census.gov/data/developers/data-sets/acs-5year.html)<br>`census_acs5_finance` | 5-year estimate | Annual release of overlapping five-year estimates | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | See group note above. |
| [ACS five-year California housing PUMS](https://www.census.gov/programs-surveys/acs/microdata/access.html)<br>`census_acs5_pums_housing_ca` | 5-year estimate | Annual release of overlapping five-year estimates | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | See group note above. |
| [2020 Census tract-to-PUMA relationship file](https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt)<br>`census_tract_to_puma_2020` | Decennial geography | Decennial relationship-file edition; corrections only within the named edition | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | See group note above. |
| [2010 Census tract-to-PUMA relationship file](https://www2.census.gov/geo/docs/maps-data/data/rel/2010_Census_Tract_to_2010_PUMA.txt)<br>`census_tract_to_puma_2010` | Decennial geography | Decennial relationship-file edition; corrections only within the named edition | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | See group note above. |
| [HMDA combined Modified Loan Application Register](https://ffiec.cfpb.gov/data-publication/modified-lar)<br>`cfpb_hmda_modified_lar` | Annual | Annual activity-year publication; provider corrections may follow | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | Official combined ZIP is a manual finance import. Do not silently replace frozen benchmark loan records. |
| [NMDB joint outstanding-rate and remaining-term parameter](https://www.fhfa.gov/programs/nmdb)<br>`fhfa_nmdb_joint_distribution` | Quarterly / published aggregate | Quarterly NMDB program; required exported parameter may be unavailable | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | Do not construct a joint distribution from separate marginals. Frozen benchmark inputs change only in a separately reviewed revision. |
| [NMDB California unpaid principal balance](https://www.fhfa.gov/programs/nmdb)<br>`fhfa_nmdb_ca_upb` | Quarterly / published aggregate | Quarterly NMDB program; required exported parameter may be unavailable | **Manual:** Follow the [finance guide](housing_finance_versions.md) to import the official NMDB file with explicit `--start-year`, `--release-date`, and `--local-file` values; then run `.\.venv\Scripts\python.exe run_system_refresh.py`. | Latest archived all-mortgage UPB supports B as a growth indicator only. It is not a compatible calibration level; unified refresh does not download it. |
| [CPI-U US city average, published annual average for finance deflation](https://www.bls.gov/cpi/news.htm)<br>`bls_cpi_u_us_city_average` | Monthly plus published annual average | Monthly CPI releases; published M13 annual average added after year end | **Frozen:** Preserve the frozen Version A inputs. A separate benchmark revision requires explicit vintage/release dates and the finance guide; the unified refresh does not reacquire this input. | Frozen benchmark uses published M13 and its explicit release date; do not recompute the annual average. |

### Database products

| Dataset and source ID | Observation frequency | Provider release cadence | Update command or required action | Additional notes |
|---|---|---|---|---|
| [General clean tables, analytical panels and derived features](database_build_and_refresh.md)<br>`general_analytical_products` | Source-specific frequencies | On each successful project refresh; no independent provider release | **Derived:** `.\.venv\Scripts\python.exe run_system_refresh.py` | Rebuilt from available official inputs using the configured geography/frequency rules. Missing values are preserved. |
| [Version A frozen housing-finance benchmark](housing_finance_versions.md)<br>`version_a_housing_finance_benchmark` | Mixed native frequencies | On each successful project refresh; no independent provider release | **Frozen:** `.\.venv\Scripts\python.exe run_system_refresh.py` | Preserved with exact hash verification. A routine update must not overwrite the benchmark or its freeze manifest. |
| [Version B retrospective monthly finance estimates](housing_finance_versions.md)<br>`version_b_housing_finance_monthly_hist` | Monthly | On each successful project refresh; no independent provider release | **Derived:** `.\.venv\Scripts\python.exe run_system_refresh.py` | Rebuilt using frozen A and latest eligible archived supporting inputs; modeled history may use later benchmark information. |
| [Version C monthly finance information-cutoff vintages](housing_finance_versions.md)<br>`version_c_housing_finance_monthly_rt` | Monthly estimates by cutoff vintage | On each successful project refresh; no independent provider release | **Derived:** `.\.venv\Scripts\python.exe run_system_refresh.py` | Appends/reuses the chosen cutoff; default is today UTC. Only inputs available by the cutoff are eligible; prior vintages remain immutable. |

## Open the working folder and run commands

1. Open **Windows PowerShell** from the Start menu. Type commands there, not in
   the Data Browser, Excel, or a Python window.
2. Open the project folder. If you cloned into Downloads as in the README, run:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Downloads\la_housing_database')
Get-Location
```

If your copy is elsewhere, replace the first line with `Set-Location -LiteralPath`
followed by your actual project path in quotes. `Get-Location` should display the
folder containing `run_pipeline.py`, `requirements.txt`, and `config`. Every
relative path in this guide starts at that folder.

3. Check that the project's environment is installed:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --help
```

You should see the available stages and options. If PowerShell cannot find this
interpreter or Python reports a missing package, return to the README setup
before continuing. All examples below call `.venv` directly; activation is not
required. On macOS/Linux, use `.venv/bin/python` in place of the Windows path.

4. Run **one command at a time**, without the surrounding code fences. Wait for
   the PowerShell prompt, such as `PS C:\…\la_housing_database>`, to return before
   typing the next command. The prompt itself is not a command. A build can take
   time and may be quiet while reading a large file.
5. Immediately after each Python command, check its exit code:

```powershell
$LASTEXITCODE
```

`0` means that command ended without a fatal error; any other number means stop
and investigate. It does **not** prove that all sources or expected rows are
present. Use the stage checks below as well. Pipeline runs append to
`metadata/pipeline_run_log.csv`; check the latest row's `requested_stage`,
`completed_at`, `status`, and `error`. Detailed pipeline messages are appended to
`output/pipeline.log`, so match their timestamps to this run. A setup failure
before a run starts may have only a terminal error and no new log row.

If the prompt is `>>>`, you are inside Python; type `exit()` to return to
PowerShell. If PowerShell shows `>>`, press **Ctrl+C** and re-enter the complete
command. Ctrl+C also interrupts a running build; an interrupted build is not a
successful result. Read the error and logs before retrying or starting another
stage. Do not run two builders at once.

## What a rebuild requires

A Git clone includes configuration, code, metadata, and reference documents.
It excludes observation files, raw archives, `.venv`, and `database/housing.duckdb`.
The published inventory describes the maintained research snapshot, not data
automatically installed by cloning.

To reproduce an existing snapshot, obtain its permitted raw archives and matching
manifests from the maintainer, preserving relative paths and checksums. A query-only
database snapshot is sufficient for browsing, but is not sufficient for rebuilding.
There is no ready-made database download supplied by this repository; see the
[README data requirement](../README.md#3-get-a-local-database).
Do not copy licensed HUD-USPS detail into an ordinary shared snapshot. Its access
and export rules are in [source methodology](source_definitions_and_methods.md#hud-usps-vacancy).

Never edit, overwrite, or delete an archived raw vintage to make a build pass.
Keep its source filename, geography, provider vintage, retrieval time, and checksum.
Use the source's importer for a newly obtained official release so it receives its
own archive and provenance record. Keep frozen finance inputs and their matching
manifests intact; the [finance guide](housing_finance_versions.md) explains which
products may be rebuilt.

A new online build depends on current provider availability, manual files, and
source-specific access. See the [source inventory](../metadata/system_source_inventory.csv)
and [data limitations](data_inventory_and_limitations.md). It need not reproduce the older snapshot's
values or coverage because providers revise releases. Never treat skipped sources
or a successful download as proof of a complete database.

Ordinary bulk acquisition and cleaning can log some source failures and continue;
read their source-level reports as well as the process exit code. Sparse one-source
builds are not a supported starter workflow: downstream builders expect inputs
across panels, and structural QA requires the complete macro contract. Use a
compatible permitted snapshot for query-only setup or provision the full inputs
before rebuilding. Strict refresh adds stronger failure gates but retains the
documented manual-source and Redfin review requirements.

## Pipeline stages

The `--help` command above lists the choices. The stage names and options in the
tables below are reference information, not commands to paste on their own.

| Stage | Purpose |
|---|---|
| `discover` | Check configured source endpoints, unless offline; record discovery metadata. |
| `download` | Archive provider payloads and their provenance. |
| `clean` | Normalize selected archived vintages using source-specific rules. |
| `harmonize` | Build facts, derived measures, and geography/frequency-specific panels. |
| `build-database` | Register local metadata, source tables, and analytical products in DuckDB. |
| `validate` | Run structural checks and write QA/coverage reports. |
| `catalog` | Generate source, variable, gap, and storage inventories and workbooks. |
| `export` | Write permitted CSV exports from final analytical files. |
| `variable-metadata` | Refresh unit/temporal labels and labeled views without changing observations. |
| `all` | Run discovery through export in the order above, excluding `variable-metadata`. |
| `refresh` | Verify newest eligible releases and strictly clean sources before rebuilding. |

`--source SOURCE_ID` filters source-aware discovery, download, and clean stages.
`SOURCE_ID` is a placeholder: use an exact configured ID, such as `bls_laus_la_county`.
It does **not** isolate harmonization, database building, validation, or exports.
`--start-date` and `--end-date` constrain supported downloads or harmonized dates.
`--vintage` selects archived retrieval dates where implemented; it is not allowed
with strict refresh. `--offline` reuses local raw archives without network retrieval.
`--force-download` requests a new payload instead of reusing the day's archive.
`--local-file` imports an official manually acquired file. For troubleshooting,
use `--log-level DEBUG`. These options must follow a complete command with a stage;
do not paste an option such as `--offline` by itself.

| Stage or group | Prerequisite and result to check before continuing |
|---|---|
| `discover` | Requires source configuration and online access unless offline. Review `metadata/source_discovery.csv` immediately after this stage; a reachable endpoint does not confirm a usable or current payload. The later catalog stage rewrites this report. |
| `download` | Requires provider access or the supported official local file. Confirm the selected source's new or reused archive and checksum in `metadata/raw_file_manifest.csv`; review terminal errors and any `metadata/manual_downloads.csv` entries, including their recorded dates. |
| `clean` | Requires matching archives/manifests and the source-specific parser. Check the selected source's observation count in the log, its cleaned output, and the current transformation record. A bulk run may continue after a source fails. |
| `harmonize` | Requires the complete set of cleaned inputs for the intended panels. Inspect regenerated products in `data/final/`; run validation to check their structure and coverage. |
| `build-database` | Requires the intended source, final, and metadata products. Look for the `Built` database message and table counts; confirm the database is readable after the command exits. This stage alone does not validate every product. |
| `validate` | Requires the local products being checked. Expect `QA completed: 0 errors`; review warnings and the QA/coverage files described below. |
| `catalog` | Requires the local products whose availability it reports. Inspect updated inventories and `output/qa/raw_repository_completeness.csv`; generating a catalog does not fill missing data. |
| `export` | Requires validated final products. Check the `Exported CSV outputs` counts and the expected files in `output/exports/`; absent inputs may be skipped, and an old CSV's presence is not proof it was regenerated. |
| `variable-metadata` | Requires complete, consistent variable definitions and the registry. Check `metadata/data_dictionary.csv`, `output/exports/variable_dictionary.csv`, and the new transformation-log entry. It updates SQL labels only when a database already exists. |

Open reports in File Explorer relative to the project folder. CSV reports can be
viewed in Excel, but close them without saving changes to identifiers or dates.
Close generated workbooks before regenerating them so Windows does not lock them.

## Rebuild from existing archives

First confirm the required archives, manifests, supporting inputs, and installed
environment are present. Stop the Data Browser with **Ctrl+C in its PowerShell
window**; closing its browser tab alone does not stop the server. Close any other
application holding the database open. In the project PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage all --offline
```

Wait for the prompt, check `$LASTEXITCODE`, then review the run log, QA, coverage,
and export counts. This is not an initial-download command: offline mode does not
supply missing sources or complete the finance layers. If a stage fails, later
stages in this run do not execute; existing downstream files may still be from
an earlier build.

The following is a separate workflow after adding a source contract and successfully
acquiring and cleaning its inputs. It starts from cleaned data, not raw files.
Register the catalog before validating its new metadata. Run each line separately,
checking its exit code and the relevant result above; export only after validation
succeeds:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage harmonize
.\.venv\Scripts\python.exe run_pipeline.py --stage build-database
.\.venv\Scripts\python.exe run_pipeline.py --stage catalog
.\.venv\Scripts\python.exe run_pipeline.py --stage variable-metadata
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
.\.venv\Scripts\python.exe run_pipeline.py --stage export
```

## Retrieve source updates

The database is a static snapshot. Opening it does not contact providers.
On an already provisioned workspace, stop database readers as above and confirm
provider access and the manual-source requirements. The commands in this section
are **alternative scopes or maintenance stages**, not a checklist to run together.

### Refresh all general sources

In the project PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh
```

Omitting `--source` selects all enabled general sources. This command
downloads, cleans, rebuilds, validates, and exports in one run. It does not
recompute finance B/C; use the [whole-project command](#refresh-everything-before-modeling)
when those products also need updating. Fixed-edition and manual sources retain
the exceptions in the dataset schedule.

Automatic acquisition and strict cleaning failures stop the workflow. Manual or
licensed sources appear as intervention items in `metadata/source_refresh_status.csv`;
retaining checksummed existing archives does not make them newest releases.
After the prompt returns, check the exit code and run log, then inspect the current
refresh report's `refresh_status`, `is_blocking`, `action_required`, and
`clean_refresh_status` for every requested source. A retained archive or manual
intervention item needs review even when the process exits successfully. Check QA
and exports only after the downstream stages have completed. The
[Redfin compatibility hold](county_and_zip_housing_indicators.md#current-redfin-source-limitation)
needs a reviewed source/method decision, not repeated blind refresh attempts.

### Refresh one source

For example, to update LAUS and carry its new observations into DuckDB, run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source bls_laus_la_county
```

This is a complete individual-source refresh. Do not follow it with another
download or clean step. Use the exact source ID and command in the
[dataset schedule](#dataset-update-schedule) for another source, or the
[combined Redfin command](#redfin-downloads) for both Redfin levels.

### Download and clean separately for maintenance

Use separate stages when inspecting a provider file before processing it or
diagnosing a failed stage. They are not additional steps in the normal refresh
workflow. For example, acquisition alone is:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source bls_laus_la_county --force-download
```

`download` preserves the provider's original response in the raw archive and
records its provenance. It does not convert that response into analytical
observations. After a successful download, the next maintenance stage is:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage clean --source bls_laus_la_county
```

`clean` reads the archived response, parses source dates and values, preserves
geographic identifiers, applies the source's documented transformations, and
writes standardized observations with lineage. **Cleaning alone still does not
update the analytical tables in DuckDB.** Check its observations and transformation
record, then follow the downstream sequence from `harmonize` through `export` in
[Rebuild from existing archives](#rebuild-from-existing-archives). That section
also gives an offline rebuild which includes cleaning without downloading again.

Check `$LASTEXITCODE` immediately after each maintenance command and stop on
failure. The complete `refresh` workflow already performs these stages in order.
Every retrieval preserves endpoint/request metadata, original filename, source
vintage, exact retrieval time, size, and SHA-256 checksum.

For LA County LAUS, the official BLS Public Data API requests series
`LAUCN060370000000003` (unemployment rate), `...004` (unemployment), `...005`
(employment), and `...006` (labor force). HTTP/API errors, absent configured series,
invalid JSON, or an empty numeric response stop that download. Inspect the newest
manifest row rather than relying on a date copied into a document.

## Redfin downloads

### Refresh county and ZIP data together

In **Windows PowerShell inside the project folder**, after closing DuckDB and
other database readers, the combined command is:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin
```

`redfin` is a source group defined in `config/pipeline.yaml`: it selects
`redfin_county_monthly` and `redfin_zip_monthly`. It acquires and cleans both
sources, then runs the shared harmonization, database build, validation, catalog,
and export stages once. Other providers are not downloaded by this command.

**Currently blocked by the county review hold:** the accepted county series
ends in May 2026; newer county files require the documented methodology migration.
The combined command reports that blocker and cannot claim a completed update
just because ZIP acquisition succeeds. Downloads may have been archived before
the run stops. The source group does not remove or bypass the hold. Review the
[county-source limitation](county_and_zip_housing_indicators.md#current-redfin-source-limitation)
before attempting it. To list known requirements without downloading, use
`.\.venv\Scripts\python.exe run_system_refresh.py --check --source redfin`.

For an individual Redfin level, choose **one** of these commands:

**County only** (subject to the same current hold):

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin_county_monthly
```

**ZIP only:**

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage refresh --source redfin_zip_monthly
```

Use `run_system_refresh.py` without a source filter for all configured sources
and finance products. You do not need to run the separate Redfin commands first.

### ZIP download method and source formats

The ZIP downloader uses Redfin's official public files directly. It reads the
[provider file index](https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_data_center/index.json)
and follows its `housing_market.monthly.zip.all` entry. That index and file path
are published in the [official download page](https://www.redfin.com/news/data-center/downloads/).
No browser, API key, or manual download is needed for this route. Install the
project requirements as described in the README; a separate Chromium installation
is no longer required.

For acquisition only, use this maintenance command in the project PowerShell
window. The refresh commands above already include this work:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source redfin_zip_monthly --force-download
```

The downloader streams the full national CSV (about **1.3 GB** for the September
2026 publication). Wait for the command to finish. It archives the CSV unchanged,
checks its complete schema, postal ZIP identifiers, overlapping three-month windows,
duplicate keys, and date coverage, and records its checksum and source metadata.
The exact provider index is also archived so the URL selection can be reproduced.

The September 25, 2026 verification downloaded 4,960,669 national rows, with
provider update date September 3 and observations through August 31. Applying
the configured January 2019 window-start bound and LA County ZIP reference
produced 27,538 ZIP-period rows across 330 ZIPs, ending March 2019 through August
2026. These are dated verification results; later releases can revise them.

Downloading alone does not update analytical observations. Use one of the
refresh workflows above to complete cleaning, rebuilding, validation, and exports.

**Versioned formats and units:** the cleaner accepts the exact legacy 20-column
Key Metrics export, the successor 22-column export, and the official full
50-column CSV. The national file stays raw-only. Only the reviewed measures,
selected dates, and ZIPs in the versioned LA County ZIP reference enter clean data.
Other columns in the full provider file remain raw-only, including its separate
`INVENTORY` field. Active listings are never renamed inventory.

Query `clean.redfin_zip_monthly` or filter `analytics.fact_housing_observation`
by `source_id = 'redfin_zip_monthly'` for the complete accepted LA subset. The
combined `analytics.zip_monthly_panel` retains its existing Zillow ZIP-month
keys; Redfin rows outside those keys remain available in the source and fact
tables and appear as a coverage warning in QA.

The newer files publish median days-on-market YoY as a change in **days**. This is
stored under the separate variable
`redfin_zip_median_days_on_market_yoy_days_provider_rolling_3m`. The existing
provider percentage variable retains its definition and is missing in vintages
that do not supply it. Its old values remain in the immutable historical archives.
The ZIP panel's calculated calendar-year percentage change is another independent
measure; it is not relabeled as the provider's days change.

**If you saw HTTP 429:** that error came from the earlier browser-page method.
Update the project code using the README's update instructions, then use the
ZIP refresh command above. `--force-download` bypasses the local cache; it does not override
provider access limits. If the direct file itself refuses access, stop repeated
attempts and read the reported failure. Existing database observations remain
available for queries; a failed acquisition is never reported as a data refresh.

Manual import is still available for an unchanged official file matching a
supported versioned format. Replace the example path with the actual path:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage download --source redfin_zip_monthly --local-file "C:\path\to\official_redfin.csv"
```

The same structural checks apply to direct and manual acquisition. Do not rename
columns or edit the provider file to force a match. Source releases with a new,
unrecognized format stop ingestion until their definitions are reviewed.

## Finance layers and integrated refresh

Version A is frozen separately. Versions B and C require its exact benchmark,
matching freeze manifest, and eligible supporting archives. A fresh general build
alone does not create them. Follow the [finance guide](housing_finance_versions.md)
for preflight, build, validation, lineage, and cutoff rules.

On a fully provisioned research workspace, complete the finance guide's preflight
and stop database readers. The integrated runner refreshes general sources,
rebuilds retrospective B, appends a new C cutoff or validates an existing one,
then registers and validates. For the usual current update, use the no-argument
command under [Refresh everything before modeling](#refresh-everything-before-modeling).
The following is a **template**: replace `YYYY-MM-DD` with the intended information
cutoff in year-month-day form, following the finance guide's availability rules.
Do not paste the placeholder literally:

```powershell
.\.venv\Scripts\python.exe run_system_refresh.py --information-cutoff YYYY-MM-DD
```

After the prompt returns, check `$LASTEXITCODE` and the newest row of
`metadata/system_refresh_run_log.csv`: match the cutoff and inspect `status`,
`integrated_completed`, `version_c_action`, and `error`. A
`completed_with_action_required` result exits with code 2 and retains the pending
manual release checks. Review the source refresh and QA reports too; the log alone
does not establish current releases for manual sources. A failed run can leave
partially updated products, so do not describe it as a completed integrated refresh.
The runner verifies, but does not rebuild, frozen Version A. Do not change its
benchmark or freeze manifest to bypass a verification failure. The runner does
not install a recurring schedule.
The macro-only acquisition helper `scripts/refresh_macro_request.py` acquires and
strictly cleans nine macro sources; it does not publish a final database by itself.

## Files, provenance, and validation

| Location | Role |
|---|---|
| `data/raw/` | Immutable permitted provider payloads. |
| `data/benchmark/` | Benchmark archives and prepared inputs; preserve raw vintages and the frozen version's manifest-protected files. |
| `data/restricted/hud_usps/` | Licensed archive and detailed derivatives; outside public exports and Git. |
| `data/archive/deprecated_zori_nsa/` | Historical NSA ZORI archive; excluded from active ingestion. |
| `data/interim/`, `data/clean/`, `data/final/` | Rebuildable intermediate, normalized, and analytical files. |
| `database/housing.duckdb` | Local query database with metadata, raw, clean, analytics, and quality schemas. |
| `metadata/raw_file_manifest.csv` | Local raw-file lineage, endpoints, requests, timestamps, and checksums. |
| `metadata/transformation_log.csv` | Execution record linking inputs and outputs. |
| `output/exports/` | Generated data CSVs and definitions. |
| `output/qa/`, `output/coverage/` | Structural failures, review warnings, missingness, vintages, and coverage. |

The `raw` schema stores manifest/provenance records, not copies of provider payloads.
The `clean` schema registers active sources; old files on disk do not necessarily
belong to active tables. Current generated inventories are linked from the
[data reference](data_inventory_and_limitations.md).

Before publishing an implementation or describing a build as validated, run these
two commands separately from the project PowerShell window. They assume the
environment and required local products already exist. Wait for the prompt and
check `$LASTEXITCODE` after each:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe run_pipeline.py --stage validate
```

The first should finish with no failed tests; review skips because they are not
evidence that an unavailable fixture was tested. The second should report
`QA completed: 0 errors` and exit with `0`. Integration tests require the
corresponding local data products; a code-only clone does not contain their fixtures.
Structural failures stop validation. Plausible economic outliers, freshness, and
expected gaps remain review warnings. Check
`output/qa/qa_summary.csv`, `qa_failures.csv`, `raw_repository_completeness.csv`,
and the source/time/ZIP/missingness reports under `output/coverage/`.

## Change a source or method

1. Use an official download, documented API, or provider-published file.
2. Update source configuration, variable definitions, and explicit unit/temporal labels.
3. Preserve raw archives, original identifiers, vintage, retrieval time, and checksum.
4. Implement source-specific geography, frequency, missingness, and uncertainty rules.
5. Update the dictionary, transformation log, methodology, and relevant QA/tests.
6. Run the checks above. Never silently interpolate, fill another geography, or
   replace an unavailable canonical target with a differently defined proxy.

After editing `config/variable_metadata.csv`, stop database readers and run:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --stage variable-metadata
```

Check the exit code and metadata-stage results in the stage table, then run the
validation command above. This refreshes the dictionary, SQL labels in an existing
database, export dictionary, and transformation entry without changing observation
values. It does not build a missing database. After running `catalog`, repeat this
metadata stage to enrich generated estimates.

## Historical records

Superseded narrative audits remain available through Git history rather than
parallel current guides. The [August 3 HUD-USPS summary](../metadata/history/hud_usps_official_rebuild_summary_20260803.csv)
describes an earlier build, not the active database. The
[quarantine manifest](../metadata/hud_usps_quarantine_manifest.csv) likewise records
superseded provenance; do not rewrite historical entries to describe current data.
The dated workbooks and previews from the former `outputs/` folder are preserved
locally under `output/archive/legacy_reports/`. Previously published workbooks also
remain in Git history. Their useful explanations are consolidated in the data and
finance guides; current CSV/JSON inventories take precedence for present availability.
Use `output/` for all new exports, QA, and reports.
