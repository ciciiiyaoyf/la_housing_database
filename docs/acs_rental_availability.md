# ACS annual rental-availability proxy

## Definition and scope

The active annual county proxy is:

```text
acs_rental_availability_rate
    = acs_for_rent_vacant_units
    / (acs_renter_occupied_units + acs_for_rent_vacant_units)
```

`acs_for_rent_vacant_units` and its 90% margin of error come from the U.S. Census Bureau's 2024 ACS 5-Year Detailed Table B25004, Vacancy Status (`B25004_002E` and `B25004_002M`). The matched renter-occupied denominator component and margin of error come from ACS Table B25003, Tenure (`B25003_003E` and `B25003_003M`). The current repository coverage is the 2024 ACS five-year vintage.

This ratio is an annual rental-availability proxy, not an observed stock of active rental listings. It must not be relabeled as monthly rental inventory or combined with the HUD/USPS all-residential vacancy rate.

Official sources:

- [ACS 2024 B25004 variable definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25004.html)
- [ACS 2024 B25003 variable definitions](https://api.census.gov/data/2024/acs/acs5/groups/B25003.html)
- [Official 2024 B25004 table-based data file](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25004.dat)

## Geography and stored measures

The component estimates and component margins of error are retained as direct ACS observations for Los Angeles County and the configured Los Angeles-area Census ZCTAs. The constructed rate is exposed in the annual county controls. A Census ZCTA is not a USPS postal ZIP; the identifiers and labels remain distinct throughout the repository.

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

## Remaining blockers

- **ACS history:** only the 2024 matched B25003/B25004 vintage is currently active. Historical coverage requires acquiring and archiving each official matched vintage with its source URL, retrieval timestamp, provider vintage, filename, geography, and checksum. If the Census API path is used, a valid API key may be required. No older values are inferred from 2024.
- **Monthly rental listings:** `rental_listing_inventory_monthly` remains unavailable at the source-acquisition and licensing step. Implementation requires a stable historical active-rental-listing universe, documented geography and revision behavior, and a license that permits storage and use. ZORI is a rent index, Redfin active listings are for-sale listings, ACS is annual, and HUD/USPS is all-residential; none is relabeled as the missing monthly listing stock.
