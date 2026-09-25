# Data storage layers

Stores source archives and the local products constructed from them. Most payloads
are excluded from Git; cloning the repository does not populate these folders.

| Folder | Role |
|---|---|
| `raw/` | Immutable provider responses and downloads, with source vintages and checksums in the raw manifest. |
| `interim/` | Reusable intermediate transformations consumed by later stages. |
| `clean/` | Source-normalized observations from active configured inputs. |
| `final/` | Analytical facts, panels, scenarios, and finance products used by database building and export. |
| `benchmark/` | Version A inputs and normalized intermediates in `benchmark/clean/`. |
| `restricted/` | Access-controlled source and derived data, including licensed HUD-USPS detail. |
| `archive/` | Superseded vintages retained for reproducibility, including deprecated NSA ZORI. |

Provider and vintage subfolders inherit the rules of their storage layer. Use the
raw manifests and transformation log to identify selected inputs. Never delete or
edit archived raw vintages, interpolate missing observations, or relabel geography
to make an output appear complete.

Version A's completed benchmark is checksum-frozen. Version B observations and
lineage are paired; Version C retains append-only cutoff partitions. Follow the
[finance guide](../docs/housing_finance_versions.md) before rebuilding them.
Detailed restricted inputs and derivatives stay outside Git and public exports.
Deprecated NSA ZORI remains archived and inactive; the sole active rent product
is smoothed, seasonally adjusted ZORI. See the
[source methods](../docs/source_definitions_and_methods.md) for geography and
source-specific rules.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
