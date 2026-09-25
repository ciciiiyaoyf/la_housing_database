# Official source acquisition

This module discovers source metadata and archives official downloads/API
responses. Acquisition records provenance; it does not by itself update
analytical tables or prove that a source is current and compatible.

- [discover.py](discover.py): maintained source discovery records.
- [download.py](download.py): configured downloads and official local-file imports.
- [refresh.py](refresh.py): strict newest-eligible verification, intervention
  reports, and verification that cleaned outputs use refreshed archives.
- [bps_archive.py](bps_archive.py), [bps_annual.py](bps_annual.py),
  [irs_migration.py](irs_migration.py), [macro_claims.py](macro_claims.py), and
  [hud_usps.py](hud_usps.py): source-specific acquisition contracts.
- [eviction_lab_ets_audit.py](eviction_lab_ets_audit.py): dated high-frequency
  product discovery; a new listing requires review before ingestion.

Inputs are official configured endpoints or unchanged official local files.
Outputs are immutable `data/raw/` vintages and manifests/status records;
licensed HUD USPS archives remain in `data/restricted/hud_usps/`.

Preserve filename, vintage, retrieval timestamp, geography, and checksum.
Credentials stay outside configuration and recorded URLs. Offline reuse is
not a current-release verification; failed downloads, schema problems, or
review holds must not be reported as successful refreshes.

See [build/refresh operations](../../docs/database_build_and_refresh.md) and
[source-specific access requirements](../../docs/source_definitions_and_methods.md).
Return to the [source map](../README.md) or [project README](../../README.md).
