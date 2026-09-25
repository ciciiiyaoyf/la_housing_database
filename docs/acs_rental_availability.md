# ACS annual rental-availability proxy

## Definition and scope

The active annual county proxy is:

```text
acs_rental_availability_rate
    = acs_for_rent_vacant_units
    / (acs_renter_occupied_units + acs_for_rent_vacant_units)
```

`acs_for_rent_vacant_units` and its 90% margin of error come from U.S. Census Bureau ACS 5-Year Detailed Table B25004, Vacancy Status (`B25004_002E` and `B25004_002M`). The matched renter-occupied denominator component and margin of error come from ACS Table B25003, Tenure (`B25003_003E` and `B25003_003M`). The current repository has 15 matched Los Angeles County five-year vintages for 2010-2024 and 14 vintages for each of 274 configured Census ZCTAs for 2011-2024.

This ratio is an annual rental-availability proxy, not an observed stock of active rental listings. It must not be relabeled as monthly rental inventory or combined with the HUD/USPS all-residential vacancy rate.

Official sources:

- [ACS 2024 B25004 variable definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25004.html)
- [ACS 2024 B25003 variable definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25003.html)
- [Official 2024 B25004 table-based data file](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25004.dat)
- [Official ACS five-year API availability by vintage](https://www.census.gov/data/developers/data-sets/acs-5year.2010.html)

## Geography and stored measures

The component estimates and component margins of error are retained as direct ACS observations for Los Angeles County and the configured Los Angeles-area Census ZCTAs. County coverage is 2010-2024. ZCTA coverage is 2011-2024 because the official 2010 ACS API does not expose ZCTA geography; this provider gap remains explicit rather than being filled. The constructed rate is exposed in the annual county controls. A Census ZCTA is not a USPS postal ZIP; the identifiers and labels remain distinct throughout the repository.

The relevant active variables are:

- `acs_for_rent_vacant_units`
- `acs_for_rent_vacant_units_moe`
- `acs_renter_occupied_units`
- `acs_renter_occupied_units_moe`
- `acs_rental_availability_rate`

## Reliability and frequency rules

The county rate is calculated only when both component estimates and both 90% margins of error are present and nonnegative, and when the denominator is positive. The rate is suppressed when either component margin of error is greater than or equal to its estimate; a zero estimate with a positive margin of error is also treated as unreliable. Component estimates and margins of error remain available even when the constructed rate is suppressed.

The ACS measure remains at its native annual five-year-estimate frequency. It is not interpolated, forward-filled, or expanded into monthly or quarterly observations. Suppressed or unavailable values remain missing.

## Relationship to HUD/USPS

HUD/USPS is a separate native-quarterly administrative proxy covering all residential addresses. Its vacancy rate is `(short-term vacant + long-term vacant) / total residential addresses`, and no-stat addresses remain a separate diagnostic. It is not renter-only and is not active rental-listing inventory. The ACS and HUD/USPS measures have different universes, frequencies, geographies, and reliability metadata, so they must remain separate variables.

## Coverage and remaining blocker

- **ACS history:** complete for the active contract. Immutable official API archives cover 2010-2024 at Los Angeles County and 2011-2024 at the configured ZCTAs. Each archive preserves request years, variables, redacted source URLs, retrieval timestamp, source vintage, filename, geography contract, and checksum. The API key is stored outside the repository and is needed only for a future refresh.
- **Monthly rental listings:** `rental_listing_inventory_monthly` remains unavailable because the governing methodology does not name an approved, integrable historical active-listing source. The stuck step is methodology/source identification, not credentials, API activation, or coding. Any future approved source would still need a stable listing universe, documented geography/revision behavior, and usable storage terms. ZORI is a rent index, Redfin active listings are for-sale listings, ACS is annual, and HUD/USPS is all-residential; none is relabeled as the missing monthly listing stock.
