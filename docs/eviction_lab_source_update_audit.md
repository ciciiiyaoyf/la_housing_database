# Eviction Lab source-update audit

Issue 12 requires a periodic check for a genuinely high-frequency Los Angeles
County eviction product. The check must not turn the retained annual series
into monthly observations, interpolate it, or substitute another geography.

The authoritative discovery source is Eviction Lab's public
[Eviction Tracking System Get the Data registry](https://evictionlab.org/eviction-tracking/get-the-data/).
The audit archives that page unchanged, validates the complete table identified
by `ets-get-data`, and records every listed site's official weekly and monthly
download links. A high-frequency product is considered available only when a
registry row naming Los Angeles or California exposes a weekly or monthly
official Eviction Lab download. `All Cities` and `All States` are required
sentinel rows but are not silently treated as Los Angeles County products.

The machine-readable outputs are:

- `metadata/eviction_lab_ets_source_check.csv`: dated audit result, source-page
  checksum and archive path, target match counts, decision, and the unchanged
  annual-series contract.
- `metadata/eviction_lab_ets_registry_snapshot.csv`: the complete parsed ETS
  registry with official download URLs and target-match flags.
- `data/raw/eviction_lab/ets_coverage_registry/`: immutable official HTML
  snapshots used by the audit.

As checked on 2026-08-17, the official ETS Version 3.0 page was last updated
August 8, 2026. It lists no Los Angeles or California product, so there is no
approved weekly or monthly LA County series to add. No login, credential, or
paywall blocked the check. The existing `eviction_lab_county_estimates` series
therefore remains annual for 2000-2018, and no annual value is interpolated or
expanded to a higher frequency.

Rerun the public-source check with:

```powershell
python -m src.ingestion.eviction_lab_ets_audit --root .
```

If the registry schema is incomplete or changes unexpectedly, the audit fails
instead of recording a negative result. If a Los Angeles or California row is
added, the audit records `available_review_before_integration`; it does not
automatically ingest the new product because its geography, definitions,
coverage, and compatibility must first be reviewed.
