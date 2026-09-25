# Noncontrolled rental exposure: ACS structural bounds

## Current status

The repository implements the public-data portion of the methodology for rental
exposure outside rent-control coverage. It keeps 52 direct ACS estimate and
margin-of-error variables from Detailed Tables B25032 and B25127 and constructs
eight annual structure/age-share variables. The two final structural exposure
bounds are published only for ACS observations ending in 2020 or later.

This is a **partial implementation**. The implemented outputs are broad
construction-age and one-unit structural proxy bounds. The exact point variable
`noncontrolled_exposure_share` is intentionally absent because the repository
does not yet have a complete, dated, property-level policy and exemption
crosswalk for every relevant Los Angeles County jurisdiction. Consequently, an
exposure-adjusted noncontrolled-rent-growth series is also not available.

No RentCast data or other unlisted provider is used. A personalized Census URL
or API key is never stored in the repository.

## Authorized official sources

- Census ACS five-year Detailed Table
  [B25032, Tenure by Units in Structure](https://api.census.gov/data/2024/acs/acs5/groups/B25032.html)
- Census ACS five-year Detailed Table
  [B25127, Tenure by Year Structure Built by Units in Structure](https://api.census.gov/data/2024/acs/acs5/groups/B25127.html)
- [ACS five-year API documentation](https://www.census.gov/data/developers/data-sets/acs-5year.html)

The source IDs are `census_acs_5year_b25032` and
`census_acs_5year_b25127`. They use the official Census API base URL. The
downloader reads `CENSUS_API_KEY` from the environment only when making a
request; the key and the user's personalized URL are excluded from source
configuration, raw provenance, logs, and committed files.

## Geography, frequency, and coverage

- Native and final frequency: annual ACS five-year estimates.
- Los Angeles County: 15 ACS ending vintages from 2010 through 2024.
- Project Census ZCTAs: 14 ending vintages from 2011 through 2024 for each of
  274 configured ZCTAs. The
  official 2010 ACS API does not expose ZCTA geography.
- A Census ZCTA is not a USPS postal ZIP. These outputs remain in
  `analytics.zcta_annual_controls` under identifiers such as `ZCTA_90001`; they
  are never merged into or relabeled as postal-ZIP monthly observations.
- The annual estimates are not interpolated, forward-filled, or expanded to
  monthly observations.

Each archived response retains its Census vintage, retrieval timestamp, source
filename, checksum, and geography. Provider missing-value sentinels remain
null. Estimate and 90-percent margin-of-error cells are separate variables.
The current clean B25032 table has 84,722 direct observations and the current
B25127 table has 115,530.

## B25032 structure shares

Let `T32` be `acs_b25032_renter_total`. The four same-vintage descriptive
shares are:

```text
acs_renter_occupied_1_unit_share
  = (renter_1_detached + renter_1_attached) / T32

acs_renter_occupied_2_to_4_units_share
  = (renter_2_units + renter_3_to_4_units) / T32

acs_renter_occupied_5_plus_units_share
  = (renter_5_to_9_units + renter_10_to_19_units
     + renter_20_to_49_units + renter_50_plus_units) / T32

acs_renter_occupied_mobile_other_share
  = (renter_mobile_home + renter_boat_rv_other) / T32
```

These describe renter-occupied units by structure type. They are not themselves
rent-control classifications.

## B25127 vintage normalization

B25127 does not retain one fixed year-built layout across all vintages. The
cleaner resolves cells by explicit vintage regime and published label, then
normalizes them into a definitely-recent band and a broader band that crosses
the target cutoff:

| ACS ending vintage | Definitely-recent lower-bound band | Cutoff-mixed band |
|---|---|---|
| 2010-2014 | Built 2000 or later | Built 1980-1999 |
| 2015-2020 | Built 2010 or later | Built 2000-2009 |
| 2021-2024 | Built 2020 or later | Built 2000-2019 |

The normalized inputs are retained for total renter units and six mutually
exclusive structure groups: one unit, two-to-four units, five-to-nineteen
units, twenty-to-forty-nine units, fifty-plus units, and mobile/other. Every
estimate has a matching published margin-of-error variable.

Let `T27` be `acs_b25127_renter_total`, `R` the normalized
`acs_b25127_renter_recent_lower_bound_total`, and `M` the normalized
`acs_b25127_renter_cutoff_mixed_total`:

```text
acs_renter_recent_construction_share_lower_bound = R / T27
acs_renter_recent_construction_share_upper_bound = (R + M) / T27
```

The lower bound omits potentially qualifying units hidden inside the mixed bin.
The upper bound assigns the entire mixed bin to recent construction. The bounds
are necessarily wide in vintages whose published bins are coarse; neither is an
exact rolling 15-year share.

## Structural noncontrolled-exposure proxies

The repository publishes these fields only for observations ending in 2020 or
later, corresponding to the AB 1482 effective period. This publication floor is
not evidence that a particular unit is legally exempt.

Let `O` be total B25032 one-unit renter units, `R1` the B25127 one-unit renter
count already in the definitely-recent band, and `M1` the one-unit renter count
already in the cutoff-mixed band:

```text
noncontrolled_exposure_share_structural_proxy_lower_bound = R / T27

noncontrolled_exposure_share_structural_proxy_upper_bound
  = min(1, (R + M + O - R1 - M1) / T27)
```

The subtraction prevents the one-unit units already included in the age bands
from being counted twice. The upper proxy deliberately treats all remaining
one-unit rentals as potentially exempt. ACS does not identify the ownership and
tenant-notice conditions needed for that legal exemption, so this is an upper
structural bound rather than a legal determination.

Both proxy fields remain null before 2020. Ratios also remain null when a
required source estimate is missing or invalid or when the denominator is not
positive.

As a reproducibility diagnostic, the official clean inputs for the 2024 Los
Angeles County ACS vintage produce 26.9194% one-unit, 13.6184% two-to-four-unit,
58.4807% five-plus, and 0.9815% mobile/other renter structure shares. The recent
construction interval is 0.9888% to 12.6978%, and the broader noncontrolled
structural upper proxy is 37.5339%. These values validate the formulas; they do
not convert either bound into a legal coverage estimate.

## Exposure-scaled ZORI structural sensitivities

The repository also publishes two Los Angeles County annual sensitivity fields:

```text
exposure_adjusted_noncontrolled_rent_growth_structural_proxy_lower_bound
  = December ZORI YoY growth * structural exposure lower bound

exposure_adjusted_noncontrolled_rent_growth_structural_proxy_upper_bound
  = December ZORI YoY growth * structural exposure upper bound
```

Only an exact same-year December 31 match is allowed. The ACS bounds are not
carried into other months, and the Census-ZCTA bounds are not joined to postal
ZIP ZORI observations. The resulting 2020-2024 fields are exposure-scaled
aggregate sensitivity indices. They do **not** estimate rent growth within
legally noncontrolled units: aggregate ZORI does not separately observe the
controlled and noncontrolled segments. When ZORI growth is negative, the
numeric order of the products reverses even though the exposure inputs retain
their lower/upper-bound meanings. The exact
`exposure_adjusted_noncontrolled_rent_growth` target remains absent.

## Why the exact point estimate remains blocked

Construction age and units-in-structure are not enough to determine legal
coverage. A defensible point estimate also needs, at minimum:

- dated state-law versions and all enumerated exclusions;
- incorporated-versus-unincorporated jurisdiction at the property level;
- every applicable local ordinance and amendment;
- property-level construction/certificate-of-occupancy status;
- ownership form and required tenant-notice status for relevant exemptions;
- affordable-housing, dormitory, mobilehome, and other statutory categories;
- a documented aggregation rule from covered properties or units to the target
  county or Census ZCTA.

`config/rent_control_policy_crosswalk.csv` is an official-source-only scaffold.
It records the statewide law, City of Los Angeles RSO, and unincorporated Los
Angeles County RSTPO, but every row is marked `scaffold_only_not_applied`. It is
not complete across incorporated municipalities and cannot be joined to the
current annual ACS aggregates. In particular, `COUNTY_06037` includes both
incorporated and unincorporated areas and must not be used as if the County
RSTPO applied county-wide.

Official policy references in the scaffold include:

- [California Civil Code section 1947.12](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=1947.12.)
- [Los Angeles Housing Department RSO dashboard](https://housing.lacity.gov/rso)
- [Los Angeles County DCBA RSTPO overview](https://dcba.lacounty.gov/portfolio/rent-increases/)

These links support provenance, not automated legal classification. Policy
rules must be reverified by date before a future implementation uses them.

## Refresh and validation

With `CENSUS_API_KEY` already available in the environment:

```powershell
python run_pipeline.py --stage download --source census_acs_5year_b25032 --force-download
python run_pipeline.py --stage download --source census_acs_5year_b25127 --force-download
python run_pipeline.py --stage clean
python run_pipeline.py --stage harmonize
python run_pipeline.py --stage database
python run_pipeline.py --stage catalog
python run_pipeline.py --stage validate
pytest
```

Raw archives are immutable. A failed or partial API response must not replace an
existing archive, and no observation may be fabricated from another geography,
table, or vintage.
