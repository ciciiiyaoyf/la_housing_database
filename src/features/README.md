# Analytical formulas and models

These modules construct derived measures used by analytical panels. Callers
provide cleaned, correctly keyed inputs; harmonization handles most storage
and transformation-log integration.

- [derived.py](derived.py): panel changes, ratios, homebuying scenarios, and
  Central/Slow apartment completion and under-construction proxies.
- [comparable_counties.py](comparable_counties.py): exact-calendar momentum,
  fixed-reference normalization, market tightness/heat, and LA-relative measures.
- [market_regimes.py](market_regimes.py): quarterly ZIP features, frozen
  four-cluster classification, summaries/transitions, and model-file helpers.
- [macro_financial.py](macro_financial.py): sparse monthly macro controls at
  their actual national, state, or metro-division geography.

Inputs are data frames plus explicit parameters. Outputs are derived frames
and, where applicable, versioned reference/model parameters stored in metadata.
They feed `data/final/` products through the harmonization build.

Missing calendar lags remain missing; broader geographies are not broadcast
into local observations. Permit proxies are modeled scenarios. Normalization
parameters and regime centers remain frozen during ordinary refresh. Quarterly
GDP does not become a fabricated monthly macro series.

See [county and ZIP indicators](../../docs/county_and_zip_housing_indicators.md)
and [source methods](../../docs/source_definitions_and_methods.md).
Return to the [source map](../README.md) or [project README](../../README.md).
