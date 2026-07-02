"""DuckDB materialization."""

from src.database.build import build_database, sync_source_metadata

__all__ = ["build_database", "sync_source_metadata"]
