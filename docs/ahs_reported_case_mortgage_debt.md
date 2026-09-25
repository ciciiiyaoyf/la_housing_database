# AHS reported-case mortgage-debt proxy

## Purpose and authorization

The Four Variable Data Status Report names the 2023 American Housing Survey
(AHS) as the primary public calibration proxy for the California-wide
owner-occupied mortgage-debt control. It also states that AHS "primary
mortgage" may not equal a legal first lien. The AHS construction is therefore
kept separate from the exact, still-null FHFA/NMDB calibrated Version A target.

This source is not assigned to the finance variables by
`LA_CA_Housing_Additional_Variables_Methodology.docx`; that later document does
not redefine the finance construction. The catalog records the AHS variables as
authorized by the separate finance-status document, not by the Additional
Variables document.

## Official source and preserved context

- Provider: U.S. Census Bureau and HUD
- Product: 2023 AHS Table Creator, Table 14A, Mortgage Characteristics
- Selected area: California (`areas=00006`)
- Selected filter: owner-occupied (`filtergroup1=01`)
- Source ID: `census_ahs_2023_ca_table14a`
- Native frequency: 2023 biennial survey-year estimate
- Analytical geography: `STATE_06`

The exported CSV does not embed the selected year or geography. The source
configuration preserves the official query URL, and the cleaner fails unless
the caller explicitly supplies survey year 2023 and geography California. The
unchanged CSV, original filename, retrieval timestamp, provider vintage, byte
size, and SHA-256 checksum are preserved in the immutable raw archive and raw
manifest.

## Construction

The cleaner validates the complete Table 14A section order and the exact
three-column export contract. It retains the published estimate and margin of
error for:

- households with at least one regular mortgage;
- each of the fourteen outstanding-principal balance bins;
- balance not reported;
- median outstanding principal; and
- mean outstanding principal.

Displayed household estimates are in thousands and are normalized to
households. The reported-bin total must reconcile to total regular-mortgage
households minus balance-not-reported households within the explicit 0.2
thousand-household tolerance needed for independently rounded table cells.

The separately labeled proxy is:

`sum(14 reported-bin household estimates) * 1,000 * published conditional mean`

For the archived 2023 California export:

- regular-mortgage households: 4,585,200;
- reported-bin households: 3,381,200;
- balance-not-reported households: 1,203,800 (26.2540%);
- published conditional mean: $418,100; and
- reported-case proxy: $1,413,679,720,000.

## Required limitations

The proxy deliberately excludes households whose balance was not reported. It
does not assign the reported-case mean to those households because doing so
would add an unsupported missing-at-random assumption. No all-household
mean-scaled extrapolation is emitted.

Published margins of error remain attached to their provider-published cells.
The repository does not construct an aggregate margin of error, standard error,
or confidence interval because the necessary covariance between the reported
household count and conditional mean is unavailable.

The result is a 2023 California statewide sensitivity. It is not a 2024
exact-date control, a county observation, or an exact owner-occupied closed-end
first-lien total. It must not be allocated to counties or substituted into the
twelve null calibrated Version A finance target IDs.

## Refresh and QA

Refreshes require a new official Table Creator export with margins of error
enabled. Import the unchanged file with:

`python run_pipeline.py --stage download --source census_ahs_2023_ca_table14a --local-file PATH_TO_OFFICIAL_CSV`

Cleaning stops on a changed header, reordered section, missing or suppressed
required value, nonnumeric cell, duplicate observation key, missing provenance,
or failed bin reconciliation. Every accepted vintage remains immutable.
