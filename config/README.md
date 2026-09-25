# Configuration

Defines which sources, variables, geographies, and methods the pipeline uses.

| Files | Role |
|---|---|
| `sources.yaml`, `pipeline.yaml` | Provider acquisition contracts, output paths, method settings, and refresh holds. |
| `variables.yaml`, `variable_metadata.csv` | General variable definitions and explicit unit/temporal classifications. |
| `variable_metadata_provenance.json` | Origin and review history of those classifications. |
| `geographies.yaml`, `la_zcta_reference.json` | Geography definitions; postal ZIP and Census ZCTA stay distinct. |
| `rent_control_policy_crosswalk.csv` | Policy scaffold; incomplete coverage does not authorize exact exposure estimates. |
| `version_a_*`, `version_b_*`, `version_c_*` | Separate benchmark and monthly finance contracts. |

These files affect computation, not just display. Version A files are checksum-frozen;
method changes require a documented version update and validation.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
