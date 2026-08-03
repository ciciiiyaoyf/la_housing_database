# HUD-USPS residential vacancy proxy

## Status

The eight HUD-USPS variables are active from an authenticated official rebuild completed on 2026-08-03. All 73 California quarterly NCWM responses from 2008 Q1 through 2026 Q1, the 2026 Q1 tract-to-ZIP crosswalk, and the official Census 2020 county-FIPS reference were archived under the Git-ignored restricted directory with byte sizes and SHA-256 checksums. The reproducible acquisition and construction code is `scripts/rebuild_hud_usps_official.py`.

The former 2026-08-02 derived snapshot remains quarantined as historical evidence and is not an input to the active series. Licensed raw responses and tract/ZIP derivatives remain access-controlled and outside Git history.

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

## Official rebuild findings

The official rebuild contains 4,234 county-quarter rows (58 counties), 182,062 LA tract-quarter rows (2,494 tracts), and 31,828 modeled LA ZIP-county-part rows (436 ZIP parts). The normalized clean table contains 1,744,992 observations for eight variables and 2,988 geography identifiers. Zero-address rates remain null, and address-management discontinuities remain visible as warnings rather than being smoothed away.

Los Angeles County's active 2026 Q1 vacancy-proxy rate is `0.0121697746` (1.21698%). This reproduces the former snapshot value from newly archived official inputs.

## Refresh checklist

For each refresh, rerun the authenticated rebuild, archive new responses and checksums, retain the sublicense notice, resolve names through the versioned Census reference, and rerun source-specific and full-pipeline validation before replacing active outputs.

The protected package inventory and hashes are recorded in `metadata/hud_usps_quarantine_manifest.csv`.

## Official references

- Dataset overview: https://www.huduser.gov/portal/datasets/usps.html
- NCWM API documentation: https://www.huduser.gov/portal/dataset/uspsncwm-api.html
- HUD-USPS ZIP crosswalk documentation: https://www.huduser.gov/portal/dataset/uspszip-api.html
- Census-tract sublicense: https://www.huduser.gov/portal/usps/sublicense_agreement.html
