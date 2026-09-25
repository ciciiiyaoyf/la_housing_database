# Data storage layers

Stores source archives and the local products constructed from them. Most payloads
are excluded from Git; cloning the repository does not populate these folders.

| Folder | Role |
|---|---|
| [raw/](raw/README.md) | Immutable provider responses and downloads. |
| [interim/](interim/README.md) | Reusable intermediate transformations. |
| [clean/](clean/README.md) | Source-normalized observations. |
| [final/](final/README.md) | Analytical facts, panels, scenarios, and finance products. |
| [benchmark/](benchmark/README.md) | Version A benchmark inputs and intermediate artifacts. |
| [restricted/](restricted/README.md) | Access-controlled source and derived data. |
| [archive/](archive/README.md) | Superseded vintages retained for reproducibility. |

Provider and vintage subfolders inherit the rules of their storage layer. Use the
raw manifests and transformation log to identify selected inputs. Never delete or
edit archived raw vintages, interpolate missing observations, or relabel geography
to make an output appear complete.

[Parent folder](../README.md) · [Start here](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
