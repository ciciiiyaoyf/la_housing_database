# HUD-USPS residential vacancy proxy

## Status

As of 2026-08-03, `hud_usps_ncwm_quarterly` and its eight `usps_*` variables are active. The active build comes from a verified official raw archive, not from the earlier derived package. The archive contains 75 immutable artifacts: 73 contiguous HUD NCWM standardized-2020-tract JSON responses for 2008 Q1 through 2026 Q1, the official 2026 Q1 HUD tract-to-postal-ZIP crosswalk JSON response, and the official 58-row 2020 Census California county-code TXT reference.

The former 2026-08-02 derived package file was deleted after the official rebuild was verified. Only its superseded quarantine manifest remains; it is not an active input and does not override the newer official-raw rebuild.

HUD-USPS tract information and its detailed derivatives are licensed data. Raw files and detailed county, tract, and modeled postal-ZIP-part outputs remain access-controlled under `data/restricted/hud_usps/`, outside Git history and public CSV exports. The required sublicense notice travels with every copy.

## Variable contract

The primary proxy is:

`usps_residential_vacancy_rate = (short_term_vacant_residential_addresses + long_term_vacant_residential_addresses) / total_residential_addresses`

`no_stat_residential_addresses / total_residential_addresses` remains a separate diagnostic. No-stat addresses are never added to the vacancy numerator.

The active quarterly variables are:

- `usps_total_residential_addresses`
- `usps_short_term_vacant_residential_addresses`
- `usps_long_term_vacant_residential_addresses`
- `usps_vacant_residential_addresses`
- `usps_no_stat_residential_addresses`
- `usps_active_residential_addresses`
- `usps_residential_vacancy_rate`
- `usps_residential_no_stat_rate`

This is an all-residential administrative address-vacancy proxy. It does not measure rental advertisements, asking rents, renter tenure, or whether a vacant address is offered for rent. It is not `rental_listing_inventory_monthly` and must never be relabeled as true rental-listing inventory.

## Official archive and provenance

Each of the 75 official artifacts is recorded with its artifact role, source URL and request query, source filename, exact UTC retrieval timestamp, provider vintage or release date when supplied, geography, byte size, and SHA-256 checksum. Quarter-end dates are observation periods, not inferred provider release dates. The official source inventory is in `metadata/hud_usps_source_manifest.csv`; the common immutable-file records are in `metadata/raw_file_manifest.csv`.

The roles are:

- `ncwm_standardized_2020`: 73 quarterly responses, contiguous from 2008 Q1 through 2026 Q1;
- `tract_zip_crosswalk`: one official 2026 Q1 response used for every modeled Los Angeles County postal-ZIP-part series; and
- `county_fips_reference_2020`: one official 58-row Census file used only to resolve California county names without changing FIPS identifiers.

Archive completeness and checksum validation stop the rebuild when any expected artifact is missing, duplicated, malformed, or altered.

## Geography, construction, and frequency

The verified rebuild contains 1,768,936 long-form variable observations across 3,029 geographies and 73 calendar-quarter ends:

- 4,234 county-quarter rows (33,872 variable observations): all 58 California counties;
- 182,062 tract-quarter rows (1,456,496 variable observations): 2,494 standardized 2020 Census tracts in Los Angeles County; and
- 34,821 modeled ZIP-quarter rows (278,568 variable observations): 477 Los Angeles County portions of USPS postal ZIP codes.

The source component fields are direct provider values at standardized 2020-tract geography. California county component counts are constructed by summing their tract values, with null propagation when any required constituent value is missing. Rates are calculated only after aggregation; tract percentages are never averaged. The common `reported_value` field is populated only for direct tract component fields, not for constructed county values, modeled ZIP values, or calculated rates.

The ZIP estimates allocate tract counts with the fixed 2026 Q1 residential-address crosswalk. They are modeled Los Angeles County portions of postal ZIPs—not whole postal ZIPs and not Census ZCTAs—and use `postal_zip_county_part` geography with `is_modeled = true`. County sums are constructed rather than directly reported but are not crosswalk-modeled; tract source components are direct. Row-level lineage preserves the source response and crosswalk filenames, checksums, vintages, and retrieval timestamps.

The native and analytical frequency is quarterly. The repository does not expand these observations to months or insert them into monthly panels. Any future monthly carry-forward would have to be a separate modeled variable with `is_interpolated = true`; it must not be described as observed monthly inventory.

## Structural validation and review warnings

`src.cleaning.hud_usps` stops on:

- missing expected official artifacts, checksum mismatches, invalid response schemas, or mismatched response periods;
- missing required columns, invalid quarter ends, duplicate geography-quarter keys, or malformed identifiers;
- incorrect county/tract relationships, incorrect modeled flags, or a ZIP scope other than the Los Angeles County portion;
- negative counts, invalid rate bounds, or noninteger direct tract counts;
- `vacant != short-term + long-term`;
- `total != active + vacant + no-stat`;
- a rate that does not equal its documented numerator divided by total residential addresses; or
- excessive loss when tract counts are allocated through the ZIP crosswalk.

Missing components remain missing. Formula checks run only where all required inputs are present. In the verified rebuild, 219 county-quarter rows are null across all dependent components and rates because at least one constituent tract component is incomplete; the earlier partial-sum behavior is not retained. Zero-address tract and ZIP rates also remain null.

Plausible high rates, large quarter-to-quarter changes, low-address denominators, and USPS address-management discontinuities are warnings for review rather than automatic deletions, smoothing, or imputation. ZIP allocation residuals remain below the structural threshold and are reported separately.

The active rebuild gives Los Angeles County a 2026 Q1 vacancy proxy of `0.0121697746` (1.21698%), based on 48,211 short- plus long-term vacant residential addresses and 3,961,536 total residential addresses. This is an all-residential proxy value, not a rental-listing inventory estimate.

## Rebuild and storage

`src.ingestion.hud_usps` downloads and verifies the official authenticated responses, preserves immutable artifacts under `data/restricted/hud_usps/raw/`, and writes the source manifest. `src.cleaning.hud_usps` rebuilds the quarterly county, tract, modeled postal-ZIP-part, common-fact, and extended-lineage tables from those files. Licensed source-specific outputs remain under `data/restricted/hud_usps/`; only configuration, methods, tests, and nonconfidential metadata belong in Git.

The superseded package inventory and hashes remain in `metadata/hud_usps_quarantine_manifest.csv` for historical audit. They are not an alternative active source. Current code or schema changes must still pass the full test suite and `python run_pipeline.py --stage validate` before the repository build is declared complete.

## Official references

- Dataset overview: https://www.huduser.gov/portal/datasets/usps.html
- NCWM API documentation: https://www.huduser.gov/portal/dataset/uspsncwm-api.html
- HUD-USPS ZIP crosswalk documentation: https://www.huduser.gov/portal/dataset/uspszip-api.html
- Census-tract sublicense: https://www.huduser.gov/portal/usps/sublicense_agreement.html
- 2020 Census California county codes: https://www2.census.gov/geo/docs/reference/codes2020/cou/st06_ca_cou2020.txt
