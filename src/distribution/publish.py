"""Validate and publish immutable, access-controlled database snapshots."""

from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import duckdb

from .common import (
    GhClient,
    SnapshotConfig,
    SnapshotError,
    atomic_json,
    exclusive_file,
    git_revision,
    inspect_database,
    sha256_file,
    sha256_stream,
    utc_now,
    validate_manifest,
)


DATABASE_ASSET = "housing.duckdb"
MANIFEST_ASSET = "housing.snapshot.json"
ACCESS_NOTICE = (
    "For authorized collaborators only. Repository access does not grant rights "
    "to redistribute source data. This snapshot preserves the source provenance "
    "and may contain licensed HUD-USPS vacancy observations; all applicable "
    "provider and institutional access restrictions continue to apply."
)


def _validate_tag(config: SnapshotConfig, tag: str) -> None:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", tag)
        or not tag.startswith(config.tag_prefix)
        or len(tag) <= len(config.tag_prefix)
        or tag.endswith(".")
    ):
        raise SnapshotError(
            f"Snapshot tag must start with {config.tag_prefix!r}, be at most "
            "120 characters, and contain only letters, digits, dots, underscores, "
            "and hyphens (with no final dot)."
        )


def _assert_no_wal(database_path: Path) -> None:
    if Path(f"{database_path}.wal").exists():
        raise SnapshotError(
            f"The database has a write-ahead log: {database_path}.wal. Close "
            "database applications normally and finish the build before publishing. "
            "Do not delete the log."
        )


def _run_checks(config: SnapshotConfig, tag: str) -> dict[str, dict[str, Any]]:
    """Run the actual required checks; retain failure logs outside a package."""
    log_dir = config.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    checks: dict[str, dict[str, Any]] = {}
    commands = (
        ("pytest", [sys.executable, "-m", "pytest", "-q", "tests"]),
        (
            "pipeline_validate",
            [sys.executable, "run_pipeline.py", "--stage", "validate"],
        ),
    )
    for name, command in commands:
        log_path = log_dir / f"{tag}-{run_id}-{name}.log"
        print(f"Running {name}; output: {log_path}", flush=True)
        try:
            with log_path.open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    command,
                    cwd=config.root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
        except OSError as exc:
            raise SnapshotError(f"Cannot run {name}: {exc}. Log: {log_path}") from exc
        if result.returncode != 0:
            raise SnapshotError(
                f"Required {name} check failed (exit {result.returncode}). "
                f"No snapshot was uploaded. Inspect {log_path}."
            )
        checks[name] = {"exit_code": 0, "completed_at_utc": utc_now()}
    return checks


def _assert_private_repository(client: GhClient, config: SnapshotConfig) -> None:
    repository = client.json(
        ["repo", "view", config.repository, "--json", "nameWithOwner,isPrivate"]
    )
    if (
        not isinstance(repository, dict)
        or str(repository.get("nameWithOwner", "")).casefold()
        != config.repository.casefold()
        or repository.get("isPrivate") is not True
    ):
        raise SnapshotError(
            "Full database snapshots can only be published to the configured "
            "private repository. This tool does not produce a public/redacted edition."
        )


def _assert_remote_revision(
    client: GhClient, config: SnapshotConfig, revision: str, tag: str
) -> None:
    """Require pushed code and reject an existing tag pointing to other code."""
    remote = client.json(["api", f"repos/{config.repository}/commits/{revision}"])
    if not isinstance(remote, dict) or remote.get("sha") != revision:
        raise SnapshotError(
            "The validated code commit is not available in the configured GitHub "
            "repository. Push that commit before publishing."
        )
    # Matching refs returns an empty list when a proposed release tag is new.
    # A draft release need not have created its tag yet.
    refs = client.json(["api", f"repos/{config.repository}/git/matching-refs/tags/{tag}"])
    if not isinstance(refs, list):
        raise SnapshotError("GitHub returned an invalid tag reference response.")
    if any(isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{tag}" for ref in refs):
        tagged = client.json(["api", f"repos/{config.repository}/commits/{tag}"])
        if not isinstance(tagged, dict) or tagged.get("sha") != revision:
            raise SnapshotError(
                f"Existing tag {tag!r} points to different code. Choose a new snapshot tag."
            )


def _verify_release(
    client: GhClient,
    config: SnapshotConfig,
    tag: str,
    revision: str,
    package_dir: Path,
    *,
    draft: bool,
) -> dict[str, Any]:
    release = client.json(
        [
            "release", "view", tag, "--repo", config.repository, "--json",
            "tagName,isDraft,targetCommitish,assets,url",
        ]
    )
    if (
        not isinstance(release, dict)
        or release.get("tagName") != tag
        or release.get("isDraft") is not draft
        or release.get("targetCommitish") != revision
    ):
        raise SnapshotError("GitHub release state or target commit did not match the snapshot.")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SnapshotError("GitHub did not return the uploaded release assets.")
    names = [asset.get("name") for asset in assets if isinstance(asset, dict)]
    if sorted(names) != sorted([DATABASE_ASSET, MANIFEST_ASSET]):
        raise SnapshotError("Release must contain exactly the database and its manifest.")
    for asset in assets:
        if asset.get("size") != (package_dir / asset["name"]).stat().st_size:
            raise SnapshotError(f"Uploaded size mismatch for {asset['name']}.")
    return release


def _prepare_package(
    config: SnapshotConfig,
    tag: str,
    revision: str,
    checks: dict[str, dict[str, Any]],
    package_dir: Path,
) -> dict[str, Any]:
    source = config.database_path
    _assert_no_wal(source)
    if not source.is_file():
        raise SnapshotError(f"No built database exists at {source}.")
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    if package_dir.exists():
        raise SnapshotError(f"Prepared snapshot already exists at {package_dir}; use a new tag.")
    with tempfile.TemporaryDirectory(prefix=f".{tag}-", dir=package_dir.parent) as temporary:
        temporary_dir = Path(temporary)
        target = temporary_dir / DATABASE_ASSET
        try:
            # Native locking excludes database readers/writers during copying.
            # Never reopen the source while this POSIX fcntl lock is held: closing
            # any descriptor for the inode would release its process-wide locks.
            # Validation precedes this lock because existing QA writes QA tables.
            with exclusive_file(source) as source_handle:
                _assert_no_wal(source)
                before = sha256_stream(source_handle)
                with target.open("wb") as copied_handle:
                    shutil.copyfileobj(source_handle, copied_handle, length=1024 * 1024)
                    copied_handle.flush()
                    os.fsync(copied_handle.fileno())
                copied = sha256_file(target)
                after = sha256_stream(source_handle)
                _assert_no_wal(source)
                if before != copied or copied != after:
                    raise SnapshotError("Database changed while copying; no snapshot was published.")
            # Inspect the exact bytes that will be published, using the common
            # read-only connection with external access disabled.
            inspection = inspect_database(target, config.required_tables)
        except OSError as exc:
            raise SnapshotError(
                f"Cannot obtain a stable database copy: {exc}. "
                "Close database applications and finish all writers before retrying."
            ) from exc
        if git_revision(config.root, require_clean=True) != revision:
            raise SnapshotError("Code changed while preparing the snapshot; rerun with a new tag.")
        restricted = inspection.get("restricted_data_present")
        if not isinstance(restricted, bool):
            raise SnapshotError("Database inspection did not determine restricted-data presence.")
        manifest = {
            "manifest_version": 1,
            "repository": config.repository,
            "tag": tag,
            "snapshot_created_at_utc": utc_now(),
            "validated_with_code_commit": revision,
            "database": {
                "filename": DATABASE_ASSET,
                "bytes": target.stat().st_size,
                "sha256": copied,
            },
            "duckdb_version": duckdb.__version__,
            "inspection": {
                field: inspection[field]
                for field in ("schema_signature", "table_counts", "coverage")
            },
            "checks": checks,
            "access": {
                "scope": "authorized_collaborators",
                "restricted_data_present": restricted,
                "notice": ACCESS_NOTICE,
            },
        }
        validate_manifest(manifest, config, expected_tag=tag)
        atomic_json(temporary_dir / MANIFEST_ASSET, manifest)
        # Reserve the persistent package without replacing any prior tag. The
        # files are already complete and checked before they enter this folder.
        try:
            package_dir.mkdir()
        except FileExistsError as exc:
            raise SnapshotError(f"Prepared snapshot already exists at {package_dir}.") from exc
        try:
            for name in (DATABASE_ASSET, MANIFEST_ASSET):
                (temporary_dir / name).replace(package_dir / name)
        except OSError as exc:
            raise SnapshotError(
                f"Could not finalize prepared snapshot at {package_dir}: {exc}. "
                "No files were uploaded; inspect the partial package before retrying."
            ) from exc
    return manifest


def _publish_snapshot(
    config: SnapshotConfig,
    *,
    tag: str,
    acknowledge_authorized_sharing: bool = False,
    prepare_only: bool = False,
) -> dict[str, Any]:
    """Check a prebuilt database, package it, and optionally publish a new release.

    This never refreshes sources or rebuilds observations. The required existing
    validation command can update QA tables in the source database. Preparation
    itself locks the source without changing it and preserves its bytes and provenance.
    """
    _validate_tag(config, tag)
    revision = git_revision(config.root, require_clean=True)
    if not prepare_only and not acknowledge_authorized_sharing:
        raise SnapshotError(
            "Publishing requires --acknowledge-authorized-sharing after confirming "
            "that every repository reader is permitted to receive this database. "
            "Use the prepare command to run checks and package locally without uploading."
        )
    package_dir = config.output_dir / "publish" / tag
    if package_dir.exists():
        raise SnapshotError(f"Prepared snapshot already exists at {package_dir}; use a new tag.")
    if not config.database_path.is_file():
        raise SnapshotError(f"Build the database first; missing {config.database_path}.")
    _assert_no_wal(config.database_path)
    client: GhClient | None = None
    if not prepare_only:
        client = GhClient(config)
        _assert_private_repository(client, config)
        _assert_remote_revision(client, config, revision, tag)
    checks = _run_checks(config, tag)
    if git_revision(config.root, require_clean=True) != revision:
        raise SnapshotError("Code changed during the required checks; no snapshot was uploaded.")
    manifest = _prepare_package(config, tag, revision, checks, package_dir)
    result: dict[str, Any] = {
        "status": "prepared",
        "tag": tag,
        "package_directory": str(package_dir),
        "manifest": manifest,
    }
    if prepare_only:
        return result
    assert client is not None
    notes_path = package_dir / "release-notes.md"
    notes_path.write_text(
        f"Validated database snapshot `{tag}`.\n\n"
        f"Snapshot copied at: {manifest['snapshot_created_at_utc']}\n\n"
        f"Validated with code commit: `{revision}`. This identifies the code used "
        "for these checks; it does not assert when or with which revision the "
        "source observations were originally built.\n\n"
        f"Database SHA-256: `{manifest['database']['sha256']}`\n\n"
        "Required pytest and pipeline validation checks passed. Source vintages, "
        "retrieval timestamps, filenames, checksums, and geography remain in the "
        "database. Coverage and table counts are recorded in the attached manifest.\n\n"
        f"Restricted HUD-USPS data detected: {manifest['access']['restricted_data_present']}.\n\n"
        f"{ACCESS_NOTICE}\n",
        encoding="utf-8",
    )
    try:
        if git_revision(config.root, require_clean=True) != revision:
            raise SnapshotError("Code changed before upload; rerun the checks.")
        _assert_private_repository(client, config)
        client.run(
            [
                "release", "create", tag, str(package_dir / DATABASE_ASSET),
                str(package_dir / MANIFEST_ASSET), "--repo", config.repository,
                "--target", revision, "--title", tag, "--notes-file", str(notes_path),
                "--draft",
            ]
        )
        _verify_release(client, config, tag, revision, package_dir, draft=True)
    except (SnapshotError, OSError) as exc:
        raise SnapshotError(
            f"Snapshot was not confirmed published: {exc}. Package retained at "
            f"{package_dir}. Inspect the GitHub release {tag!r}; if a draft was "
            "created, it has been retained for review, not deleted or overwritten."
        ) from exc
    try:
        client.run(["release", "edit", tag, "--repo", config.repository, "--draft=false", "--latest"])
        release = _verify_release(client, config, tag, revision, package_dir, draft=False)
        tagged = client.json(["api", f"repos/{config.repository}/commits/{tag}"])
        if not isinstance(tagged, dict) or tagged.get("sha") != revision:
            raise SnapshotError("Published release tag did not resolve to the validated code commit.")
    except (SnapshotError, OSError) as exc:
        raise SnapshotError(
            f"Publication could not be fully confirmed: {exc}. Inspect release {tag!r} "
            "on GitHub before retrying; it may already be published. "
            f"The verified local package remains at {package_dir}."
        ) from exc
    result.update(status="published", url=release.get("url"))
    return result


def publish_snapshot(
    config: SnapshotConfig,
    *,
    tag: str,
    acknowledge_authorized_sharing: bool = False,
    prepare_only: bool = False,
) -> dict[str, Any]:
    """Serialize preparation/publication against installation in this checkout."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_file(config.output_dir / "install.lock", create=True):
        return _publish_snapshot(
            config,
            tag=tag,
            acknowledge_authorized_sharing=acknowledge_authorized_sharing,
            prepare_only=prepare_only,
        )
