"""Verified same-path installation of private, published database snapshots."""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import tempfile

from .common import (
    DATABASE_ASSET, MANIFEST_ASSET, MAX_MANIFEST_BYTES, GhClient, SnapshotConfig,
    SnapshotError, atomic_json, contained_path, ensure_compatible_code,
    exclusive_file, inspect_database, read_json, require_private_repository,
    replace_locked, sha256_stream, utc_now, validate_manifest, validate_tag,
)


def _wal_guard(path: Path) -> None:
    if Path(str(path) + ".wal").exists():
        raise SnapshotError("A database WAL is present. Close its writer normally before updating; do not delete the WAL.")


def _state(config: SnapshotConfig) -> dict | None:
    if not config.state_path.exists():
        return None
    state = read_json(config.state_path)
    validate_manifest(state.get("manifest", {}), config)
    return state


def _record(config: SnapshotConfig, manifest: dict) -> None:
    atomic_json(config.state_path, {"installed_at_utc": utc_now(), "manifest": manifest})


def _current_hash(config: SnapshotConfig) -> str | None:
    _wal_guard(config.database_path)
    if not config.database_path.exists():
        return None
    with exclusive_file(config.database_path) as handle:
        _wal_guard(config.database_path)
        return sha256_stream(handle)


def _recover(config: SnapshotConfig) -> None:
    """Complete state recording after a crash, never guess about different bytes."""
    pending_path = config.output_dir / "install-pending.json"
    if not pending_path.exists():
        return
    pending = read_json(pending_path)
    manifest = validate_manifest(pending.get("manifest", {}), config)
    _wal_guard(config.database_path)
    context = exclusive_file(config.database_path) if config.database_path.exists() else nullcontext(None)
    with context as handle:
        actual = sha256_stream(handle) if handle else None
        if actual == manifest["database"]["sha256"]:
            _record(config, manifest)
        elif "previous_sha256" not in pending or actual != pending["previous_sha256"]:
            raise SnapshotError("An interrupted installation has unexpected database bytes. Preserve the database and its backup before manual recovery.")
        pending_path.unlink()


def _check_inspection(config: SnapshotConfig, staged: Path, manifest: dict) -> None:
    inspection = inspect_database(staged, config.required_tables)
    for field in ("schema_signature", "table_counts", "coverage"):
        if inspection[field] != manifest["inspection"][field]:
            raise SnapshotError(f"Downloaded database {field} differs from its manifest")
    if inspection["restricted_data_present"] != manifest["access"]["restricted_data_present"]:
        raise SnapshotError("Downloaded database access classification differs from its manifest")


def install_snapshot(config: SnapshotConfig, staged_database: Path, manifest: dict) -> str:
    """Consume a verified staged file, retaining one previous unmodified baseline.

    Existing untracked or locally edited databases are never overwritten. A tiny
    journal allows the next installation to recover an interrupted state write.
    """
    validate_manifest(manifest, config)
    ensure_compatible_code(config, manifest)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    staged = contained_path(config.root, staged_database)
    destination = contained_path(config.root, config.database_path)
    if staged == destination:
        raise SnapshotError("The staged snapshot must be separate from the installed database")
    with exclusive_file(config.output_dir / "install.lock", create=True):
        _recover(config)
        state = _state(config)
        _wal_guard(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        current = exclusive_file(destination) if destination.exists() else nullcontext(None)
        with current as old_handle:
            _wal_guard(destination)
            old_hash = sha256_stream(old_handle) if old_handle else None
            wanted_hash = manifest["database"]["sha256"]
            if old_hash == wanted_hash:
                if state is not None and state["manifest"] == manifest:
                    return "unchanged"
                _record(config, manifest)
                return "adopted" if state is None else "unchanged"
            if old_handle and (state is None or old_hash != state["manifest"]["database"]["sha256"]):
                raise SnapshotError(
                    "The local database is untracked or has local changes. Preserve that file separately "
                    "and save permanent changes as Python/SQL before removing it from the active path and retrying."
                )
            if not staged.is_file() or staged.is_symlink():
                raise SnapshotError("The downloaded database is missing or is a symbolic link")
            _wal_guard(staged)
            if staged.stat().st_size != manifest["database"]["bytes"]:
                raise SnapshotError("Downloaded database size differs from its manifest")
            if staged.stat().st_dev != destination.parent.stat().st_dev:
                raise SnapshotError("Staging and database must be on the same filesystem for atomic replacement")
            # Inspection closes its own DuckDB connection before our native lock.
            # The checksum under that lock binds the inspected file to the manifest.
            _check_inspection(config, staged, manifest)
            ensure_compatible_code(config, manifest)
            with exclusive_file(staged) as staged_handle:
                _wal_guard(staged)
                if sha256_stream(staged_handle) != wanted_hash:
                    raise SnapshotError("Downloaded database SHA-256 checksum differs from its manifest")
                staged_handle.flush()
                os.fsync(staged_handle.fileno())
                if old_handle:
                    config.backup_path.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=".previous-", suffix=".duckdb", dir=config.backup_path.parent)
                    backup_temp = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as backup:
                            old_handle.seek(0)
                            shutil.copyfileobj(old_handle, backup, 1024 * 1024)
                            backup.flush()
                            os.fsync(backup.fileno())
                        os.replace(backup_temp, config.backup_path)
                    finally:
                        backup_temp.unlink(missing_ok=True)
                    atomic_json(config.backup_path.with_suffix(".snapshot.json"), state)
                pending_path = config.output_dir / "install-pending.json"
                atomic_json(pending_path, {"previous_sha256": old_hash, "manifest": manifest})
                try:
                    replace_locked(staged, destination, staged_handle, replace_existing=old_handle is not None)
                    _record(config, manifest)
                    pending_path.unlink()
                except OSError as exc:
                    raise SnapshotError(
                        "Installation was interrupted. The active file and previous backup were retained. "
                        "Retry update to recover its installation record; do not remove install-pending.json."
                    ) from exc
    return "installed"


def status_snapshot(config: SnapshotConfig) -> dict:
    """Inspect local state without contacting GitHub or changing database bytes."""
    state = _state(config)
    actual = _current_hash(config)
    if actual is None:
        status = "missing"
    elif state is None:
        status = "untracked"
    elif actual != state["manifest"]["database"]["sha256"]:
        status = "locally_modified"
    else:
        status = "installed"
    return {"status": status, "database_path": str(config.database_path),
            "tag": state["manifest"]["tag"] if state else None,
            "sha256": actual,
            "recovery_pending": (config.output_dir / "install-pending.json").exists()}


def _release(config: SnapshotConfig, client: GhClient, tag: str | None) -> dict:
    if tag is None:
        # API pagination avoids a newer unrelated release hiding database snapshots.
        pages = client.json(["api", "--paginate", "--slurp", f"repos/{config.repository}/releases?per_page=100"])
        releases = [release for page in pages for release in page]
        candidates = [release for release in releases if not release.get("draft")
                      and not release.get("prerelease")
                      and str(release.get("tag_name", "")).startswith(config.tag_prefix)]
        if not candidates:
            raise SnapshotError("No published database snapshot exists. The publisher must create the first release.")
        tag = max(candidates, key=lambda release: release.get("published_at") or "")["tag_name"]
    validate_tag(tag, config.tag_prefix)
    release = client.json(["release", "view", tag, "--repo", config.repository, "--json",
                           "tagName,isDraft,isPrerelease,assets,publishedAt,url"])
    if release.get("tagName") != tag or release.get("isDraft") is not False or release.get("isPrerelease") is not False:
        raise SnapshotError("Only completed, published database releases may be installed")
    assets = release.get("assets", [])
    for name, limit in ((MANIFEST_ASSET, MAX_MANIFEST_BYTES), (DATABASE_ASSET, config.max_download_bytes)):
        matches = [asset for asset in assets if asset.get("name") == name]
        if len(matches) != 1 or type(matches[0].get("size")) is not int or not 0 < matches[0]["size"] <= limit:
            raise SnapshotError(f"Release must contain one valid {name} asset within the size limit")
    return release


def update_snapshot(config: SnapshotConfig, *, tag: str | None = None, check_only: bool = False) -> dict:
    """Download only when bytes differ; checks alone never install a database."""
    client = GhClient(config)
    require_private_repository(config, client)
    release = _release(config, client, tag)
    tag = release["tagName"]
    config.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=config.output_dir) as directory:
        staging = Path(directory)
        client.run(["release", "download", tag, "--repo", config.repository,
                    "--pattern", MANIFEST_ASSET, "--dir", str(staging)])
        manifest = validate_manifest(read_json(staging / MANIFEST_ASSET), config, expected_tag=tag)
        db_asset = next(asset for asset in release["assets"] if asset["name"] == DATABASE_ASSET)
        if db_asset["size"] != manifest["database"]["bytes"]:
            raise SnapshotError("Release database size differs from its manifest")
        # A release tag must identify the exact code revision used for validation.
        commit = client.json(["api", f"repos/{config.repository}/commits/{tag}"])
        if commit.get("sha") != manifest["validated_with_code_commit"]:
            raise SnapshotError("Release tag points to different code than its database manifest")
        local = status_snapshot(config)
        if check_only:
            return {"status": "checked", "latest_tag": tag,
                    "validated_with_code_commit": manifest["validated_with_code_commit"],
                    "update_available": local["sha256"] != manifest["database"]["sha256"],
                    "local": local}
        ensure_compatible_code(config, manifest)
        # Resolve an interrupted metadata write before classifying local modifications.
        with exclusive_file(config.output_dir / "install.lock", create=True):
            _recover(config)
        local = status_snapshot(config)
        if local["sha256"] == manifest["database"]["sha256"]:
            # No 383 MB transfer when the local bytes are already the requested snapshot.
            result = install_snapshot(config, staging / DATABASE_ASSET, manifest)
        else:
            if local["status"] in ("untracked", "locally_modified"):
                raise SnapshotError("Local database contains untracked or modified data. Preserve it separately before updating.")
            client.run(["release", "download", tag, "--repo", config.repository,
                        "--pattern", DATABASE_ASSET, "--dir", str(staging)])
            result = install_snapshot(config, staging / DATABASE_ASSET, manifest)
    return {"status": result, "tag": tag, "database_path": str(config.database_path),
            "validated_with_code_commit": manifest["validated_with_code_commit"]}
