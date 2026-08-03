# HUD-USPS residential vacancy proxy

## Status

The eight HUD-USPS variables are registered in configuration and the variable dictionary, and their source-specific validator is implemented. The supplied 2026-08-02 derived snapshot is preserved byte-for-byte under the Git-ignored restricted project directory, but it is **quarantined and excluded from active clean tables, facts, panels, and exports**.

Activation is blocked because the construction session discarded the official quarterly API responses and the tract-to-ZIP crosswalk response. The surviving package does not contain the exact UTC retrieval timestamp, source release date, official source filenames, or raw-file checksums needed by this repository's provenance standard. Registering the variables and preserving the package does not claim successful official ingestion.

## Variable contract

The primary proxy is:

`usps_residential_vacancy_rate = (short_term_vacant_residential_addresses + long_term_vacant_residential_addresses) / total_residential_addresses`

`no_stat_residential_addresses / total_residential_addresses` remains a separate diagnostic. No-stat addresses are never added to the vacancy numerator.

The registered quarterly variables are:

- `usps_total_residential_addresses`
- `usps_short_term_vacant_residential_addresses`
- `usps_long_term_vacant_residential_addresses`
- `usps_vacant_residential_addresses`
- `usps_no_stat_residential_addresses`
- `usps_active_residential_addresses`
- `usps_residential_vacancy_rate`
- `usps_residential_no_stat_rate`

This is an all-residential administrative address-vacancy proxy. It does not measure rental advertisements, asking rents, renter tenure, or whether a vacant address is for rent. It is not `rental_listing_inventory_monthly` and must never be relabeled as true rental-listing inventory.

## Geography and frequency

The quarantined package reports 73 calendar-quarter ends from 2008 Q1 through 2026 Q1:

- 4,234 county-quarter rows: all 58 California counties.
- 182,062 tract-quarter rows: 2,494 standardized 2020 Census tracts in Los Angeles County.
- 34,821 modeled ZIP-quarter rows: 477 Los Angeles County portions of USPS postal ZIP codes.

The ZIP estimates use a fixed 2026 Q1 tract-to-ZIP residential-address ratio. They are modeled Los Angeles County portions of postal ZIPs—not whole postal ZIPs and not Census ZCTAs—and use `postal_zip_county_part` geography with `is_modeled = true`. County and tract rows use `is_modeled = false`.

The native and analytical frequency is quarterly. The repository does not expand these observations to months. Any future monthly carry-forward would have to be a separate modeled variable with `is_interpolated = true`; it must not be described as observed monthly inventory.

## Structural validation

`src.cleaning.hud_usps` stops on:

- missing required columns, invalid quarter ends, duplicate geography-quarter keys, or malformed identifiers;
- incorrect county/tract relationships, incorrect modeled flags, or a ZIP scope other than the Los Angeles County portion;
- negative counts, invalid rate bounds, or noninteger observed county/tract counts;
- `vacant != short-term + long-term`;
- `total != active + vacant + no-stat`; or
- a rate that does not equal its documented numerator divided by total residential addresses.

Missing components remain missing. Formula checks run only where all required inputs are present. Plausible high rates, large quarter-to-quarter changes, and low-address denominators are warnings for review rather than automatic deletions or smoothing.

## Quarantined snapshot findings

Internal arithmetic checks passed, but the package has activation blockers beyond missing raw provenance:

- 219 county rates are blank because constituent source tracts are incomplete.
- 935 `county_name` cells are blank across 13 county FIPS codes; activation requires an official versioned FIPS-name reference.
- zero-address tract and ZIP rates remain null.
- statewide series contain large USPS address-management discontinuities that must remain visible as warnings.
- modeled ZIP outliers and low-address rows must be reviewed, not removed or smoothed.

The package reported the Los Angeles County 2026 Q1 rate as `0.0121697746` (1.21698%), based on 48,211 short- plus long-term vacant residential addresses and 3,961,536 total residential addresses. This value is a quarantined package assertion, not an active database observation.

## Activation checklist

Before any HUD-USPS value can enter active repository outputs:

1. Reacquire every required quarterly NCWM response and the exact crosswalk from the official authenticated HUD APIs.
2. Archive each raw response immutably under `data/raw/` with source URL/query, source filename, UTC retrieval timestamp, provider/source vintage, file size, SHA-256 checksum, and geography.
3. Preserve the applicable sublicense term and required notice in access-controlled storage.
4. Rebuild the derived tables from the archived raw responses with Python 3.11-compatible repository code.
5. Resolve county names through an official versioned FIPS reference without changing identifiers.
6. Pass the source-specific structural checks, full `pytest`, and `python run_pipeline.py --stage validate`.
7. Only then change the source and variable statuses from quarantined to active.

The protected package inventory and hashes are recorded in `metadata/hud_usps_quarantine_manifest.csv`.

## Official references

- Dataset overview: https://www.huduser.gov/portal/datasets/usps.html
- NCWM API documentation: https://www.huduser.gov/portal/dataset/uspsncwm-api.html
- HUD-USPS ZIP crosswalk documentation: https://www.huduser.gov/portal/dataset/uspszip-api.html
- Census-tract sublicense: https://www.huduser.gov/portal/usps/sublicense_agreement.html
