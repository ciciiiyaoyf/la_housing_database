"""Snapshot safety tests use tiny synthetic databases, never research inputs."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import pytest

from src.distribution.common import (
    SnapshotConfig,
    SnapshotError,
    inspect_database,
    sha256_file,
    validate_manifest,
)
from src.distribution.update import install_snapshot
from src.distribution import common as snapshot_common
from src.distribution import publish as snapshot_publish
from src.distribution import update as snapshot_update


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def snapshot_config(tmp_path: Path) -> SnapshotConfig:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "snapshot-test@example.invalid")
    _git(tmp_path, "config", "user.name", "Snapshot safety test")
    (tmp_path / ".gitignore").write_text("database/\noutput/\n", encoding="utf-8")
    (tmp_path / "source_marker.txt").write_text("fixture version 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "Create synthetic test project")
    return SnapshotConfig(
        root=tmp_path,
        repository="snapshot-fixture/research-project",
        tag_prefix="data-",
        database_path=tmp_path / "database/housing.duckdb",
        output_dir=tmp_path / "output/database_snapshots",
        backup_path=tmp_path / "database/housing.previous.duckdb",
        state_path=tmp_path / "database/housing.snapshot.json",
        required_tables={
            "metadata.variable_dictionary": ["variable_id", "source_id"],
            "analytics.fact_housing_observation": [
                "variable_id", "source_id", "observation_date", "value"
            ],
        },
        max_download_bytes=64 * 1024 * 1024,
    )


def _snapshot(config: SnapshotConfig, version: int) -> tuple[Path, dict]:
    path = config.output_dir / f"fixture-{version}" / "housing.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA analytics")
        connection.execute(
            "CREATE TABLE metadata.variable_dictionary "
            "(variable_id VARCHAR, source_id VARCHAR)"
        )
        connection.execute(
            "INSERT INTO metadata.variable_dictionary VALUES ('test_value', 'synthetic_fixture')"
        )
        connection.execute(
            "CREATE TABLE analytics.fact_housing_observation "
            "(variable_id VARCHAR, source_id VARCHAR, observation_date DATE, value DOUBLE)"
        )
        connection.execute(
            "INSERT INTO analytics.fact_housing_observation VALUES "
            "('test_value', 'synthetic_fixture', DATE '2026-01-01', ?)", [version]
        )
    inspection = inspect_database(path, config.required_tables)
    manifest = {
        "manifest_version": 1,
        "repository": config.repository,
        "tag": f"data-v{version}",
        "snapshot_created_at_utc": "2026-09-27T12:00:00+00:00",
        "validated_with_code_commit": _git(config.root, "rev-parse", "HEAD"),
        "database": {
            "filename": "housing.duckdb",
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        },
        "duckdb_version": duckdb.__version__,
        "inspection": {
            field: inspection[field]
            for field in ("schema_signature", "table_counts", "coverage")
        },
        "checks": {
            name: {"exit_code": 0, "completed_at_utc": "2026-09-27T12:00:00+00:00"}
            for name in ("pytest", "pipeline_validate")
        },
        "access": {
            "scope": "authorized_collaborators",
            "restricted_data_present": inspection["restricted_data_present"],
            "notice": "Synthetic fixtures contain no source observations.",
        },
    }
    return path, manifest


def _saved_state(config: SnapshotConfig) -> dict:
    return json.loads(config.state_path.read_text(encoding="utf-8"))


def _github_bytes_under_gbk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> None:
    """Run an actual child process, while emulating Windows' GBK text default."""
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    emitter = (
        "import sys; "
        f"sys.stdout.buffer.write({stdout!r}); "
        f"sys.stderr.buffer.write({stderr!r}); "
        f"sys.exit({returncode})"
    )

    def run(command, **kwargs):
        if command[0] == "gh":
            return real_run([sys.executable, "-c", emitter], **kwargs)
        return real_run(command, **kwargs)

    monkeypatch.setattr(snapshot_common.subprocess, "run", run)


def test_github_json_preserves_utf8_under_gbk_default(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"commit": {"message": "Snapshot \u2014 \u66f4\u65b0\u6570\u636e"}}
    _github_bytes_under_gbk(
        monkeypatch, stdout=json.dumps(expected, ensure_ascii=False).encode("utf-8"),
    )

    assert snapshot_common.GhClient(snapshot_config).json(["api", "fixture"]) == expected


def test_github_failed_command_decodes_utf8_diagnostics_under_gbk_default(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = "Access denied \u2014 \u8bf7\u91cd\u65b0\u767b\u5f55"
    _github_bytes_under_gbk(
        monkeypatch, stderr=diagnostic.encode("utf-8") + b" \xff", returncode=1,
    )

    with pytest.raises(SnapshotError, match="GitHub command failed") as error:
        snapshot_common.GhClient(snapshot_config).run(["api", "fixture"])
    assert diagnostic in str(error.value)
    assert "\ufffd" in str(error.value)


def test_github_malformed_utf8_stops_with_controlled_error(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github_bytes_under_gbk(monkeypatch, stdout=b'{"message":"\xff"}')

    with pytest.raises(SnapshotError, match="not valid UTF-8"):
        snapshot_common.GhClient(snapshot_config).json(["api", "fixture"])


def test_first_install_records_verified_snapshot(snapshot_config: SnapshotConfig) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    result = install_snapshot(snapshot_config, staged, manifest)
    assert result == "installed"
    assert sha256_file(snapshot_config.database_path) == manifest["database"]["sha256"]
    assert _saved_state(snapshot_config)["manifest"] == manifest
    assert not snapshot_config.backup_path.exists()


def test_same_snapshot_is_unchanged_without_replacing_database(snapshot_config: SnapshotConfig) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, staged, manifest)
    original_stat = snapshot_config.database_path.stat()
    assert install_snapshot(snapshot_config, staged, manifest) == "unchanged"
    assert snapshot_config.database_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert not snapshot_config.backup_path.exists()


def test_identical_untracked_database_can_be_adopted(snapshot_config: SnapshotConfig) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    snapshot_config.database_path.parent.mkdir(parents=True)
    shutil.copy2(staged, snapshot_config.database_path)
    original_stat = snapshot_config.database_path.stat()
    assert install_snapshot(snapshot_config, staged, manifest) == "adopted"
    assert snapshot_config.database_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert _saved_state(snapshot_config)["manifest"] == manifest


def test_different_untracked_database_is_never_overwritten(snapshot_config: SnapshotConfig) -> None:
    existing, _ = _snapshot(snapshot_config, 1)
    staged, manifest = _snapshot(snapshot_config, 2)
    snapshot_config.database_path.parent.mkdir(parents=True)
    shutil.copy2(existing, snapshot_config.database_path)
    original_hash = sha256_file(snapshot_config.database_path)
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == original_hash
    assert not snapshot_config.state_path.exists()


def test_local_database_edits_are_preserved(snapshot_config: SnapshotConfig) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, first_manifest)
    with duckdb.connect(str(snapshot_config.database_path)) as connection:
        connection.execute("UPDATE analytics.fact_housing_observation SET value = 99")
    local_hash = sha256_file(snapshot_config.database_path)
    staged, manifest = _snapshot(snapshot_config, 2)
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == local_hash
    assert _saved_state(snapshot_config)["manifest"] == first_manifest
    assert not snapshot_config.backup_path.exists()


def test_only_previous_snapshot_is_retained_as_backup(snapshot_config: SnapshotConfig) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    second, second_manifest = _snapshot(snapshot_config, 2)
    third, third_manifest = _snapshot(snapshot_config, 3)
    install_snapshot(snapshot_config, first, first_manifest)
    install_snapshot(snapshot_config, second, second_manifest)
    assert sha256_file(snapshot_config.backup_path) == first_manifest["database"]["sha256"]
    install_snapshot(snapshot_config, third, third_manifest)
    assert sha256_file(snapshot_config.backup_path) == second_manifest["database"]["sha256"]
    assert sha256_file(snapshot_config.database_path) == third_manifest["database"]["sha256"]


def test_download_checksum_mismatch_preserves_installed_database(snapshot_config: SnapshotConfig) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, first_manifest)
    staged, manifest = _snapshot(snapshot_config, 2)
    with staged.open("ab") as stream:
        stream.write(b"deliberately damaged test transfer")
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == first_manifest["database"]["sha256"]
    assert _saved_state(snapshot_config)["manifest"] == first_manifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "other-owner/other-project"),
        ("tag", "../data-v1"),
        ("validated_with_code_commit", "not-a-git-revision"),
        ("manifest_version", 999),
    ],
)
def test_manifest_identity_is_validated(snapshot_config: SnapshotConfig, field: str, value: object) -> None:
    _, manifest = _snapshot(snapshot_config, 1)
    manifest[field] = value
    with pytest.raises(SnapshotError):
        validate_manifest(manifest, snapshot_config, expected_tag="data-v1")


@pytest.mark.parametrize("filename", ["../housing.duckdb", "/tmp/housing.duckdb", "other.duckdb"])
def test_manifest_cannot_select_another_destination(snapshot_config: SnapshotConfig, filename: str) -> None:
    _, manifest = _snapshot(snapshot_config, 1)
    manifest["database"]["filename"] = filename
    with pytest.raises(SnapshotError):
        validate_manifest(manifest, snapshot_config)


@pytest.mark.parametrize("check", ["pytest", "pipeline_validate"])
def test_manifest_rejects_failed_validation(snapshot_config: SnapshotConfig, check: str) -> None:
    _, manifest = _snapshot(snapshot_config, 1)
    manifest["checks"][check]["exit_code"] = 1
    with pytest.raises(SnapshotError):
        validate_manifest(manifest, snapshot_config)


def test_manifest_schema_mismatch_is_rejected(snapshot_config: SnapshotConfig) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    manifest["inspection"]["schema_signature"] = "0" * 64
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert not snapshot_config.database_path.exists()


def test_missing_required_columns_prevent_snapshot_inspection(snapshot_config: SnapshotConfig) -> None:
    staged, _ = _snapshot(snapshot_config, 1)
    with duckdb.connect(str(staged)) as connection:
        connection.execute("ALTER TABLE analytics.fact_housing_observation DROP COLUMN value")
    with pytest.raises(SnapshotError, match="required"):
        inspect_database(staged, snapshot_config.required_tables)


def test_required_tables_cannot_be_views(snapshot_config: SnapshotConfig) -> None:
    staged, _ = _snapshot(snapshot_config, 1)
    with duckdb.connect(str(staged)) as connection:
        connection.execute("ALTER TABLE analytics.fact_housing_observation RENAME TO underlying_fixture")
        connection.execute(
            "CREATE VIEW analytics.fact_housing_observation AS SELECT * FROM analytics.underlying_fixture"
        )
    with pytest.raises(SnapshotError, match="stored tables"):
        inspect_database(staged, snapshot_config.required_tables)


def test_restricted_rows_are_detected_in_shared_fact_table(snapshot_config: SnapshotConfig) -> None:
    staged, _ = _snapshot(snapshot_config, 1)
    with duckdb.connect(str(staged)) as connection:
        connection.execute(
            "UPDATE analytics.fact_housing_observation SET variable_id = 'usps_synthetic_test'"
        )
    assert inspect_database(staged, snapshot_config.required_tables)["restricted_data_present"] is True


def test_pending_wal_prevents_replacement(snapshot_config: SnapshotConfig) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, first_manifest)
    wal = Path(str(snapshot_config.database_path) + ".wal")
    wal.write_bytes(b"preserve this pending WAL fixture")
    staged, manifest = _snapshot(snapshot_config, 2)
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert wal.read_bytes() == b"preserve this pending WAL fixture"
    assert sha256_file(snapshot_config.database_path) == first_manifest["database"]["sha256"]


def test_update_requires_matching_clean_code(snapshot_config: SnapshotConfig) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    (snapshot_config.root / "source_marker.txt").write_text("local change\n", encoding="utf-8")
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert not snapshot_config.database_path.exists()
    _git(snapshot_config.root, "add", "source_marker.txt")
    _git(snapshot_config.root, "commit", "--quiet", "-m", "Different code revision")
    with pytest.raises(SnapshotError):
        install_snapshot(snapshot_config, staged, manifest)
    assert not snapshot_config.database_path.exists()


@contextmanager
def _holding_process(program: str, *arguments: str):
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", program, *arguments],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        yield process
    finally:
        try:
            process.communicate("close\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)
        assert process.returncode == 0


def _other_duckdb_process(path: Path, read_only: bool):
    return _holding_process(
        "import duckdb, sys\n"
        "connection = duckdb.connect(sys.argv[1], read_only=sys.argv[2] == 'True')\n"
        "print('READY', flush=True)\n"
        "sys.stdin.readline()\n"
        "connection.close()\n",
        str(path), str(read_only),
    )


@pytest.mark.parametrize("read_only", [True, False], ids=["reader", "writer"])
def test_real_duckdb_process_blocks_database_replacement(
    snapshot_config: SnapshotConfig, read_only: bool
) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, first_manifest)
    staged, manifest = _snapshot(snapshot_config, 2)
    old_state = deepcopy(_saved_state(snapshot_config))
    with _other_duckdb_process(snapshot_config.database_path, read_only):
        with pytest.raises(SnapshotError):
            install_snapshot(snapshot_config, staged, manifest)
        assert _saved_state(snapshot_config) == old_state
        assert not snapshot_config.backup_path.exists()
    assert sha256_file(snapshot_config.database_path) == first_manifest["database"]["sha256"]


def test_independent_updaters_cannot_install_concurrently(snapshot_config: SnapshotConfig) -> None:
    first, first_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, first_manifest)
    staged, manifest = _snapshot(snapshot_config, 2)
    lock_path = snapshot_config.output_dir / "install.lock"
    with _holding_process(
        "from pathlib import Path\n"
        "import sys\n"
        "from src.distribution.common import exclusive_file\n"
        "with exclusive_file(Path(sys.argv[1]), create=True):\n"
        "    print('READY', flush=True)\n"
        "    sys.stdin.readline()\n",
        str(lock_path),
    ):
        with pytest.raises(SnapshotError):
            install_snapshot(snapshot_config, staged, manifest)
        assert sha256_file(snapshot_config.database_path) == first_manifest["database"]["sha256"]
        assert _saved_state(snapshot_config)["manifest"] == first_manifest


def _built_database(config: SnapshotConfig) -> dict:
    staged, manifest = _snapshot(config, 1)
    config.database_path.parent.mkdir(parents=True)
    shutil.copy2(staged, config.database_path)
    return manifest


def test_prepare_only_copies_database_without_network_or_source_changes(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = _built_database(snapshot_config)
    monkeypatch.setattr(snapshot_publish, "_run_checks", lambda *args: initial["checks"])

    def network_forbidden(*args):
        pytest.fail("Preparing a snapshot must not contact GitHub")

    monkeypatch.setattr(snapshot_publish, "GhClient", network_forbidden)
    result = snapshot_publish.publish_snapshot(snapshot_config, tag="data-v1", prepare_only=True)
    assert result["status"] == "prepared"
    package = Path(result["package_directory"])
    assert sha256_file(package / "housing.duckdb") == initial["database"]["sha256"]
    assert sha256_file(snapshot_config.database_path) == initial["database"]["sha256"]
    assert json.loads((package / "housing.snapshot.json").read_text(encoding="utf-8")) == result["manifest"]
    with pytest.raises(SnapshotError):
        snapshot_publish.publish_snapshot(snapshot_config, tag="data-v1", prepare_only=True)
    assert sha256_file(package / "housing.duckdb") == initial["database"]["sha256"]


def test_publishing_requires_explicit_access_acknowledgement(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _built_database(snapshot_config)

    def forbidden(*args):
        pytest.fail("Publication must stop before checks and network without acknowledgement")

    monkeypatch.setattr(snapshot_publish, "_run_checks", forbidden)
    monkeypatch.setattr(snapshot_publish, "GhClient", forbidden)
    with pytest.raises(SnapshotError, match="acknowledge"):
        snapshot_publish.publish_snapshot(snapshot_config, tag="data-v1")


def test_publishing_rejects_dirty_code_before_checks(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _built_database(snapshot_config)
    (snapshot_config.root / "source_marker.txt").write_text("uncommitted", encoding="utf-8")

    def checks_forbidden(*args):
        pytest.fail("Dirty code must be refused before validation")

    monkeypatch.setattr(snapshot_publish, "_run_checks", checks_forbidden)
    with pytest.raises(SnapshotError):
        snapshot_publish.publish_snapshot(snapshot_config, tag="data-v1", prepare_only=True)


@pytest.mark.parametrize("failing_check", ["pytest", "pipeline_validate"])
def test_required_check_failure_stops_packaging_and_upload(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch, failing_check: str
) -> None:
    commands = []

    def simulated_check(command, **kwargs):
        commands.append(command)
        name = "pytest" if "pytest" in command else "pipeline_validate"
        return subprocess.CompletedProcess(command, 2 if name == failing_check else 0)

    monkeypatch.setattr(snapshot_publish.subprocess, "run", simulated_check)
    with pytest.raises(SnapshotError, match=failing_check):
        snapshot_publish._run_checks(snapshot_config, "data-v1")
    assert len(commands) == (1 if failing_check == "pytest" else 2)
    assert not (snapshot_config.output_dir / "publish").exists()


def test_public_repository_cannot_receive_full_snapshot(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _built_database(snapshot_config)

    class PublicRepository:
        def __init__(self, config):
            pass

        def json(self, command):
            assert command[:2] == ["repo", "view"]
            return {"nameWithOwner": snapshot_config.repository, "isPrivate": False}

        def run(self, *args, **kwargs):
            pytest.fail("No upload is allowed to a public repository")

    def checks_forbidden(*args):
        pytest.fail("The public repository must be refused before expensive checks")

    monkeypatch.setattr(snapshot_publish, "GhClient", PublicRepository)
    monkeypatch.setattr(snapshot_publish, "_run_checks", checks_forbidden)
    with pytest.raises(SnapshotError, match="private repository"):
        snapshot_publish.publish_snapshot(
            snapshot_config, tag="data-v1", acknowledge_authorized_sharing=True
        )


def test_failed_checks_never_create_remote_release(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _built_database(snapshot_config)

    class PrivateRepository:
        def __init__(self, config):
            pass

        def json(self, command):
            if command[:2] == ["repo", "view"]:
                return {"nameWithOwner": snapshot_config.repository, "isPrivate": True}
            if "matching-refs" in command[-1]:
                return []
            return {"sha": manifest["validated_with_code_commit"]}

        def run(self, *args, **kwargs):
            pytest.fail("A failed check must prevent all upload commands")

    def failed_checks(*args):
        raise SnapshotError("Required pytest check failed")

    monkeypatch.setattr(snapshot_publish, "GhClient", PrivateRepository)
    monkeypatch.setattr(snapshot_publish, "_run_checks", failed_checks)
    with pytest.raises(SnapshotError, match="pytest"):
        snapshot_publish.publish_snapshot(
            snapshot_config, tag="data-v1", acknowledge_authorized_sharing=True
        )
    assert not (snapshot_config.output_dir / "publish/data-v1").exists()


def _mock_release(
    monkeypatch: pytest.MonkeyPatch,
    config: SnapshotConfig,
    database: Path,
    manifest: dict,
    *,
    fail_database: bool = False,
    tag_revision: str | None = None,
) -> list[str]:
    """GitHub-shaped responses only; no credentials, live APIs, or uploads."""
    downloads: list[str] = []
    encoded_manifest = json.dumps(manifest).encode("utf-8")
    tag = manifest["tag"]

    class FixtureGitHub:
        def __init__(self, provided_config):
            assert provided_config == config

        def json(self, command):
            if command[:2] == ["repo", "view"]:
                return {"nameWithOwner": config.repository, "isPrivate": True}
            if command[:3] == ["api", "--paginate", "--slurp"]:
                return [[
                    {"tag_name": "code-v999", "draft": False, "prerelease": False,
                     "published_at": "2026-09-29T00:00:00Z"},
                    {"tag_name": "data-unfinished", "draft": True, "prerelease": False,
                     "published_at": "2026-09-29T00:00:00Z"},
                ], [
                    {"tag_name": tag, "draft": False, "prerelease": False,
                     "published_at": "2026-09-27T00:00:00Z"},
                ]]
            if command[:2] == ["release", "view"]:
                assert command[2] == tag
                return {
                    "tagName": tag, "isDraft": False, "isPrerelease": False,
                    "assets": [
                        {"name": "housing.duckdb", "size": manifest["database"]["bytes"]},
                        {"name": "housing.snapshot.json", "size": len(encoded_manifest)},
                    ],
                }
            if command == ["api", f"repos/{config.repository}/commits/{tag}"]:
                return {"sha": tag_revision or manifest["validated_with_code_commit"]}
            pytest.fail(f"Unexpected fixture GitHub query: {command}")

        def run(self, command):
            assert command[:3] == ["release", "download", tag]
            name = command[command.index("--pattern") + 1]
            destination = Path(command[command.index("--dir") + 1]) / name
            downloads.append(name)
            if name == "housing.snapshot.json":
                destination.write_bytes(encoded_manifest)
            else:
                assert name == "housing.duckdb"
                if fail_database:
                    destination.write_bytes(b"incomplete transfer fixture")
                    raise SnapshotError("Simulated network interruption")
                shutil.copyfile(database, destination)
            return ""

    monkeypatch.setattr(snapshot_update, "GhClient", FixtureGitHub)
    return downloads


def test_remote_update_uses_same_path_and_skips_redundant_database_download(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, manifest = _snapshot(snapshot_config, 1)
    downloads = _mock_release(monkeypatch, snapshot_config, remote, manifest)
    first = snapshot_update.update_snapshot(snapshot_config)
    assert first["status"] == "installed"
    assert Path(first["database_path"]) == snapshot_config.database_path
    assert downloads == ["housing.snapshot.json", "housing.duckdb"]
    downloads.clear()
    second = snapshot_update.update_snapshot(snapshot_config)
    assert second["status"] == "unchanged"
    assert downloads == ["housing.snapshot.json"]
    assert not list(snapshot_config.output_dir.glob("download-*"))


def test_remote_check_reports_needed_code_without_downloading_database(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, manifest = _snapshot(snapshot_config, 1)
    (snapshot_config.root / "source_marker.txt").write_text("different code revision", encoding="utf-8")
    _git(snapshot_config.root, "add", "source_marker.txt")
    _git(snapshot_config.root, "commit", "--quiet", "-m", "Advance local fixture code")
    downloads = _mock_release(monkeypatch, snapshot_config, remote, manifest)
    checked = snapshot_update.update_snapshot(snapshot_config, check_only=True)
    assert checked["status"] == "checked"
    assert checked["update_available"] is True
    assert checked["validated_with_code_commit"] == manifest["validated_with_code_commit"]
    assert downloads == ["housing.snapshot.json"]
    with pytest.raises(SnapshotError, match="Code version differs"):
        snapshot_update.update_snapshot(snapshot_config)
    assert "housing.duckdb" not in downloads
    assert not snapshot_config.database_path.exists()


def test_remote_download_failure_preserves_installed_database_and_state(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, old_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, old_manifest)
    old_state = _saved_state(snapshot_config)
    remote, manifest = _snapshot(snapshot_config, 2)
    downloads = _mock_release(monkeypatch, snapshot_config, remote, manifest, fail_database=True)
    with pytest.raises(SnapshotError, match="network interruption"):
        snapshot_update.update_snapshot(snapshot_config)
    assert downloads == ["housing.snapshot.json", "housing.duckdb"]
    assert sha256_file(snapshot_config.database_path) == old_manifest["database"]["sha256"]
    assert _saved_state(snapshot_config) == old_state
    assert not snapshot_config.backup_path.exists()
    assert not list(snapshot_config.output_dir.glob("download-*"))


def test_remote_update_refuses_local_edits_before_large_download(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, old_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, old_manifest)
    with duckdb.connect(str(snapshot_config.database_path)) as connection:
        connection.execute("UPDATE analytics.fact_housing_observation SET value = 123")
    local_hash = sha256_file(snapshot_config.database_path)
    remote, manifest = _snapshot(snapshot_config, 2)
    downloads = _mock_release(monkeypatch, snapshot_config, remote, manifest)
    with pytest.raises(SnapshotError, match="modified"):
        snapshot_update.update_snapshot(snapshot_config)
    assert downloads == ["housing.snapshot.json"]
    assert sha256_file(snapshot_config.database_path) == local_hash


def test_remote_tag_must_resolve_to_manifest_code_revision(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, manifest = _snapshot(snapshot_config, 1)
    downloads = _mock_release(monkeypatch, snapshot_config, remote, manifest, tag_revision="0" * 40)
    with pytest.raises(SnapshotError, match="different code"):
        snapshot_update.update_snapshot(snapshot_config)
    assert downloads == ["housing.snapshot.json"]
    assert not snapshot_config.database_path.exists()


def test_interrupted_state_write_recovers_without_replacing_verified_bytes(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    record = snapshot_update._record

    def failed_record(*args):
        raise OSError("Simulated interrupted installation record write")

    monkeypatch.setattr(snapshot_update, "_record", failed_record)
    with pytest.raises(SnapshotError, match="interrupted"):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == manifest["database"]["sha256"]
    assert not snapshot_config.state_path.exists()
    assert (snapshot_config.output_dir / "install-pending.json").exists()
    original_stat = snapshot_config.database_path.stat()
    monkeypatch.setattr(snapshot_update, "_record", record)
    assert install_snapshot(snapshot_config, staged, manifest) == "unchanged"
    assert _saved_state(snapshot_config)["manifest"] == manifest
    assert snapshot_config.database_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert not (snapshot_config.output_dir / "install-pending.json").exists()


def test_failed_replacement_preserves_old_database_and_can_retry(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, old_manifest = _snapshot(snapshot_config, 1)
    install_snapshot(snapshot_config, first, old_manifest)
    old_state = _saved_state(snapshot_config)
    staged, manifest = _snapshot(snapshot_config, 2)
    replace = snapshot_update.replace_locked

    def failed_replace(*args, **kwargs):
        raise OSError("Simulated filesystem replacement failure")

    monkeypatch.setattr(snapshot_update, "replace_locked", failed_replace)
    with pytest.raises(SnapshotError, match="interrupted"):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == old_manifest["database"]["sha256"]
    assert _saved_state(snapshot_config) == old_state
    assert sha256_file(snapshot_config.backup_path) == old_manifest["database"]["sha256"]
    assert (snapshot_config.output_dir / "install-pending.json").exists()
    monkeypatch.setattr(snapshot_update, "replace_locked", replace)
    assert install_snapshot(snapshot_config, staged, manifest) == "installed"
    assert _saved_state(snapshot_config)["manifest"] == manifest
    assert sha256_file(snapshot_config.database_path) == manifest["database"]["sha256"]
    assert not (snapshot_config.output_dir / "install-pending.json").exists()


def test_first_install_never_overwrites_database_created_during_staging(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    appeared, appeared_manifest = _snapshot(snapshot_config, 2)
    replace = snapshot_update.replace_locked

    def create_database_before_rename(source, destination, source_handle, *, replace_existing):
        assert replace_existing is False
        assert not destination.exists()
        shutil.copy2(appeared, destination)
        return replace(source, destination, source_handle, replace_existing=replace_existing)

    monkeypatch.setattr(snapshot_update, "replace_locked", create_database_before_rename)
    with pytest.raises(SnapshotError, match="interrupted"):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == appeared_manifest["database"]["sha256"]
    assert staged.exists()
    assert not snapshot_config.state_path.exists()
    assert not snapshot_config.backup_path.exists()


def test_interrupted_installation_never_adopts_subsequent_local_edits(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, manifest = _snapshot(snapshot_config, 1)
    record = snapshot_update._record

    def failed_record(*args):
        raise OSError("Simulated installation interruption")

    monkeypatch.setattr(snapshot_update, "_record", failed_record)
    with pytest.raises(SnapshotError, match="interrupted"):
        install_snapshot(snapshot_config, staged, manifest)
    with duckdb.connect(str(snapshot_config.database_path)) as connection:
        connection.execute("UPDATE analytics.fact_housing_observation SET value = 321")
    local_hash = sha256_file(snapshot_config.database_path)
    monkeypatch.setattr(snapshot_update, "_record", record)
    with pytest.raises(SnapshotError, match="unexpected database bytes"):
        install_snapshot(snapshot_config, staged, manifest)
    assert sha256_file(snapshot_config.database_path) == local_hash
    assert not snapshot_config.state_path.exists()
    assert (snapshot_config.output_dir / "install-pending.json").exists()


@pytest.mark.parametrize("asset_size_mismatch", [False, True])
def test_publication_verifies_draft_before_making_release_available(
    snapshot_config: SnapshotConfig, monkeypatch: pytest.MonkeyPatch, asset_size_mismatch: bool
) -> None:
    original = _built_database(snapshot_config)
    commands: list[list[str]] = []
    state = {"draft": True}
    package = snapshot_config.output_dir / "publish/data-v1"

    class PublicationFixture:
        def __init__(self, config):
            assert config == snapshot_config

        def json(self, command):
            if command[:2] == ["repo", "view"]:
                return {"nameWithOwner": snapshot_config.repository, "isPrivate": True}
            if command[:2] == ["release", "view"]:
                return {
                    "tagName": "data-v1", "isDraft": state["draft"],
                    "targetCommitish": original["validated_with_code_commit"],
                    "assets": [
                        {"name": name, "size": (package / name).stat().st_size + int(asset_size_mismatch)}
                        for name in ("housing.duckdb", "housing.snapshot.json")
                    ],
                    "url": "https://example.invalid/synthetic-release",
                }
            if "matching-refs" in command[-1]:
                return []
            assert command[0] == "api" and "/commits/" in command[-1]
            return {"sha": original["validated_with_code_commit"]}

        def run(self, command):
            commands.append(command)
            if command[:2] == ["release", "create"]:
                assert "--draft" in command
                assert "--clobber" not in command
            elif command[:2] == ["release", "edit"]:
                assert "--draft=false" in command
                state["draft"] = False
            else:
                pytest.fail(f"Unexpected publication command: {command}")
            return ""

    monkeypatch.setattr(snapshot_publish, "GhClient", PublicationFixture)
    monkeypatch.setattr(snapshot_publish, "_run_checks", lambda *args: original["checks"])
    if asset_size_mismatch:
        with pytest.raises(SnapshotError, match="not confirmed published"):
            snapshot_publish.publish_snapshot(
                snapshot_config, tag="data-v1", acknowledge_authorized_sharing=True
            )
        assert state["draft"] is True
        assert len(commands) == 1
    else:
        result = snapshot_publish.publish_snapshot(
            snapshot_config, tag="data-v1", acknowledge_authorized_sharing=True
        )
        assert result["status"] == "published"
        assert result["url"] == "https://example.invalid/synthetic-release"
        assert len(commands) == 2
    assert sha256_file(snapshot_config.database_path) == original["database"]["sha256"]
    assert sha256_file(package / "housing.duckdb") == original["database"]["sha256"]
