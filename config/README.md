# Configuration

Defines which sources, variables, geographies, and methods the pipeline uses.

| Files | Role |
|---|---|
| `sources.yaml`, `pipeline.yaml` | Provider acquisition contracts, named source groups, output paths, method settings, and refresh holds. |
| `variables.yaml`, `variable_metadata.csv` | General variable definitions and explicit unit/temporal classifications. |
| `variable_metadata_provenance.json` | Origin and review history of those classifications. |
| `geographies.yaml`, `la_zcta_reference.json` | Geography definitions; postal ZIP and Census ZCTA stay distinct. |
| `rent_control_policy_crosswalk.csv` | Policy scaffold; incomplete coverage does not authorize exact exposure estimates. |
| `nine_constructions.yaml` | Explicit source-table projections and row types for the combined nine-construction table; existing variable IDs and frequencies remain unchanged. |
| `database_sharing.yaml` | Private GitHub repository, snapshot release naming, required database relations, managed database path, staging, installation record, and single-backup locations. This distributes completed snapshots without changing source ingestion or variable methods. |
| `version_a_*`, `version_b_*`, `version_c_*` | Separate benchmark and monthly finance contracts. |

These files affect computation, not just display. Version A files are checksum-frozen;
method changes require a documented version update and validation.

`pipeline.yaml` defines the `redfin` source group for `--source redfin`: it selects
both county and ZIP sources. Group selection preserves each source's validation
and refresh holds; it does not change observations or source definitions.

`database_sharing.yaml` is used by `run_snapshot.py`. Publication requires a private
repository and an explicit operator acknowledgement that all repository readers
may receive the database. Repository privacy does not itself confer redistribution
rights. Credentials belong in GitHub CLI authentication storage, not YAML.
The [collaboration workflow](../README.md#collaborate-through-database-snapshots)
describes publisher and collaborator setup. A configured release location alone
does not create or publish the first database.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
