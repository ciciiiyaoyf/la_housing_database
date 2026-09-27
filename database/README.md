# Local DuckDB query database

`housing.duckdb` is the integrated local database for direct DuckDB SQL analysis.
It also supports the optional Data Browser and query/export tools. It exposes
metadata, raw-manifest, clean, analytics, and quality schemas. It is a static
snapshot, not a live connection to providers.

The graphical DuckDB workflow can create `sql_workspace.duckdb` in this folder
for notebook storage while attaching `housing.duckdb` read-only. That workspace
file is also local and excluded from Git; it contains UI work, not a second copy
of the housing observations. See the SQL guide's graphical-interface section.

The file is excluded from Git. A new user installs a permitted private release
with `run_snapshot.py update`, obtains a compatible manual snapshot, or builds
from the required sources; see the
[database setup](../README.md#3-get-a-local-database). The first release must be
published before installation can succeed. Start querying with the
[DuckDB SQL guide](../docs/duckdb_sql_guide.md), or use the optional
[CSV browser](../docs/query_and_export_commands.md#browser-downloads).
Close database readers before rebuilding. Use the ordinary export tools for shareable CSVs:
the full research database can contain licensed detail and must not be assumed
safe for redistribution. Preserve an existing database before intentionally
switching to another snapshot.

The [collaboration workflow](../README.md#collaborate-through-database-snapshots)
keeps the active file at `database/housing.duckdb`. `run_snapshot.py open` checks
for a compatible published update before launching a writable local DuckDB
session; `--read-only` is optional. Local writes are not sent to collaborators and
prevent the next replacement. Reproducible contributions belong in code, followed
by a publisher build and release. No raw source archives are included in a release.

Downloads use temporary staging and checksum/structure checks. Replacement
requires a closed database without a WAL, an unchanged recorded installation,
and a clean matching code revision. Unknown or changed local files are preserved;
there is no force overwrite option. One prior managed database and its record
remain under `output/archive/database_snapshots/`; the current installation record
is `output/snapshots/installed.json`. A manually supplied identical release can be
recognized; a different manual database requires intentional preservation and
removal from the managed path before installation. A remaining WAL must stay with
its database for recovery and must never be deleted to bypass the check.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
