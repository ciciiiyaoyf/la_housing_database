# Geography contracts

This module maintains explicit geography identifiers, names, and reference
universes used by acquisition, cleaning, panel construction, and validation.

- [build.py](build.py): `build_geography()` combines configured geographies
  with recognized observation geographies and writes geography/crosswalk tables.
- [zcta_reference.py](zcta_reference.py): loads and validates the versioned
  Census ZCTA scope in `config/la_zcta_reference.json`.
- [comparable_counties.py](comparable_counties.py): defines LA and seven peer
  counties, their names/FIPS, and relevant source-series identifiers.

Inputs include `config/geographies.yaml`, reference files, and the fact frame.
Generated geography and crosswalk Parquet files live in `data/clean/` and are
subsequently materialized in DuckDB.

Keep identifiers as strings, including leading zeros. Postal ZIPs, Census
ZCTAs, counties, tracts, metro divisions, and county portions of postal ZIPs
have different scopes. Reference membership does not authorize substituting
county observations for missing ZIP data or allocating a state value locally.

See [source definitions](../../docs/source_definitions_and_methods.md),
[county/ZIP indicators](../../docs/county_and_zip_housing_indicators.md), and
[data inventory](../../docs/data_inventory_and_limitations.md).
Return to the [source map](../README.md) or [project README](../../README.md).
