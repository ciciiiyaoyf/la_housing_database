# Analytical table construction

[build.py](build.py) combines accepted clean observations into the long fact
table and analytical panels. Its `build_analytical_outputs()` function is the
general pipeline's `harmonize` stage.

The build selects eligible source vintages, preserves source timing and
geography, and calls the [feature modules](../features/README.md) for derived
measures. It assembles county/ZIP market panels, native-frequency controls,
comparable-county analytics, quarterly ZIP regimes, macro controls, and
homebuying scenarios.

Inputs are clean Parquet files, configuration, geographic references, and
versioned reference/model parameters. Outputs include the local
`data/final/fact_housing_observation.parquet`, other `data/final/` analytical
tables, geography tables, and recorded transformations in metadata.

Native-frequency observations and derived panel columns are different
products; not every panel feature is a fact-table row. Annual controls,
overlapping Redfin ZIP windows, and quarterly GDP retain their stated source
semantics. Missingness, ZIP/ZCTA separation, and original geographies must
survive joins; ordinary rebuilds must not refit frozen models.

See [build procedures](../../docs/database_build_and_refresh.md),
[county/ZIP indicators](../../docs/county_and_zip_housing_indicators.md), and
[source methods](../../docs/source_definitions_and_methods.md).
Return to the [source map](../README.md) or [project README](../../README.md).
