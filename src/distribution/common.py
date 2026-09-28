"""Small, cross-platform primitives for authenticated database snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import BinaryIO, Iterator

import duckdb
import yaml

DATABASE_ASSET = "housing.duckdb"
MANIFEST_ASSET = "housing.snapshot.json"
MAX_MANIFEST_BYTES = 1024 * 1024


class SnapshotError(RuntimeError):
    """A failed prerequisite leaves the installed database in place."""


@dataclass(frozen=True)
class SnapshotConfig:
    root: Path
    repository: str
    tag_prefix: str
    database_path: Path
    output_dir: Path
    backup_path: Path
    state_path: Path
    required_tables: dict[str, list[str]]
    max_download_bytes: int


def contained_path(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    path = root / value
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise SnapshotError(f"Snapshot path must stay inside the project: {value}")
    # Refuse even internal symbolic links: replacement must operate on the named file.
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise SnapshotError(f"Snapshot paths must not be symbolic links: {path}")
    return resolved


def load_snapshot_config(root: Path, config_path: Path | None = None) -> SnapshotConfig:
    root = root.resolve()
    path = config_path or root / "config" / "database_sharing.yaml"
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    expected = {"repository", "tag_prefix", "database_path", "output_dir", "backup_path",
                "state_path", "required_tables", "max_download_bytes"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise SnapshotError("database_sharing.yaml has missing or unknown configuration fields")
    if not isinstance(raw["repository"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", raw["repository"]
    ):
        raise SnapshotError("repository must be a GitHub OWNER/REPO name")
    prefix = raw["tag_prefix"]
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-", prefix):
        raise SnapshotError("tag_prefix must be a safe name ending in a hyphen")
    tables = raw["required_tables"]
    if not isinstance(tables, dict) or not tables or any(
        not isinstance(table, str) or not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", table)
        or not isinstance(columns, list) or not columns
        or any(not isinstance(col, str) or not re.fullmatch(r"[A-Za-z_]\w*", col) for col in columns)
        for table, columns in tables.items()
    ):
        raise SnapshotError("required_tables must map schema.table names to column lists")
    size = raw["max_download_bytes"]
    if type(size) is not int or not 0 < size < 2**31:
        raise SnapshotError("max_download_bytes must be positive and below 2 GiB")
    paths = {key: contained_path(root, raw[key]) for key in (
        "database_path", "output_dir", "backup_path", "state_path"
    )}
    if len(set(paths.values())) != len(paths):
        raise SnapshotError("Snapshot database, output, backup and state paths must differ")
    if not paths["output_dir"].is_relative_to(root / "output") or any(
        not paths[key].is_relative_to(root / "output") for key in ("backup_path", "state_path")
    ):
        raise SnapshotError("Snapshot staging, state and backups must be under output/")
    if paths["database_path"].suffix != ".duckdb":
        raise SnapshotError("database_path must name a .duckdb file")
    return SnapshotConfig(root=root, repository=raw["repository"], tag_prefix=prefix,
                          required_tables=tables, max_download_bytes=size, **paths)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_stream(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return sha256_stream(handle)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise SnapshotError(f"Snapshot metadata exceeds 1 MiB: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise SnapshotError(f"Invalid snapshot JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"Snapshot metadata must be an object: {path}")
    return value


def git_revision(root: Path, require_clean: bool = True) -> str:
    def git(*args: str) -> str:
        try:
            result = subprocess.run(["git", "-C", str(root), *args], check=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SnapshotError("Git checkout unavailable; install Git and use the cloned project") from exc
        return result.stdout.strip()
    revision = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise SnapshotError("Git HEAD must resolve to a complete commit")
    if require_clean and git("status", "--porcelain", "--untracked-files=normal"):
        raise SnapshotError("Local code has uncommitted files. Preserve and commit local work before snapshot operations.")
    return revision


def ensure_compatible_code(config: SnapshotConfig, manifest: dict) -> None:
    revision = git_revision(config.root, require_clean=True)
    wanted = manifest["validated_with_code_commit"]
    if revision != wanted:
        raise SnapshotError(
            f"Code version differs: snapshot was validated with {wanted}, local HEAD is {revision}. "
            "Save local work, fetch the repository, and select that release's code revision before updating."
        )


class GhClient:
    """GitHub CLI supplies its own authentication; credentials never enter manifests."""

    def __init__(self, config: SnapshotConfig):
        self.config = config

    def run(self, args: list[str]) -> str:
        environment = os.environ.copy()
        environment["GH_HOST"] = "github.com"
        environment["GH_PROMPT_DISABLED"] = "1"
        try:
            # GitHub CLI emits UTF-8 regardless of the Windows locale. Capture
            # bytes so decoding cannot fail silently in subprocess reader threads.
            result = subprocess.run(["gh", *args], cwd=self.config.root, env=environment,
                                    capture_output=True, timeout=1800, check=False)
        except FileNotFoundError as exc:
            raise SnapshotError("GitHub CLI is missing. Install gh and run gh auth login once.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError("GitHub request timed out; installed data was not replaced. Retry when online.") from exc
        if result.returncode:
            diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
            raise SnapshotError(f"GitHub command failed. Check gh auth status and repository access. {diagnostic}")
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotError("GitHub returned output that is not valid UTF-8") from exc

    def json(self, args: list[str]):
        try:
            return json.loads(self.run(args))
        except json.JSONDecodeError as exc:
            raise SnapshotError("GitHub returned invalid JSON") from exc


def require_private_repository(config: SnapshotConfig, client: GhClient) -> None:
    info = client.json(["repo", "view", config.repository, "--json", "nameWithOwner,isPrivate"])
    if not isinstance(info, dict) or str(info.get("nameWithOwner", "")).lower() != config.repository.lower():
        raise SnapshotError("GitHub repository identity does not match configuration")
    if info.get("isPrivate") is not True:
        raise SnapshotError("Full research snapshots require a private repository with an authorized audience.")


def validate_tag(tag: str, prefix: str) -> str:
    if not isinstance(tag, str) or not tag.startswith(prefix) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag
    ) or len(tag) <= len(prefix):
        raise SnapshotError(f"Snapshot tag must start with {prefix} and use only letters, digits, dots, hyphens or underscores")
    return tag


def _timestamp(value) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
    except (TypeError, AttributeError, ValueError) as exc:
        raise SnapshotError("Snapshot timestamps must be valid UTC ISO-8601 values") from exc


def validate_manifest(manifest: dict, config: SnapshotConfig, expected_tag: str | None = None) -> dict:
    try:
        if type(manifest["manifest_version"]) is not int or manifest["manifest_version"] != 1:
            raise SnapshotError("Unsupported snapshot manifest version")
        if manifest["repository"].lower() != config.repository.lower():
            raise SnapshotError("Snapshot repository does not match configuration")
        tag = validate_tag(manifest["tag"], config.tag_prefix)
        if expected_tag is not None and tag != expected_tag:
            raise SnapshotError("Snapshot tag does not match the requested release")
        _timestamp(manifest["snapshot_created_at_utc"])
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", manifest["validated_with_code_commit"]):
            raise SnapshotError("Snapshot code revision is not a full Git commit")
        asset = manifest["database"]
        if asset["filename"] != DATABASE_ASSET:
            raise SnapshotError("Snapshot database filename must be exactly housing.duckdb")
        if type(asset["bytes"]) is not int or not 0 < asset["bytes"] <= config.max_download_bytes:
            raise SnapshotError("Snapshot database size is invalid or exceeds the configured limit")
        if not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
            raise SnapshotError("Invalid snapshot SHA-256 checksum")
        if not isinstance(manifest["duckdb_version"], str) or not manifest["duckdb_version"]:
            raise SnapshotError("Snapshot DuckDB version is missing")
        inspection = manifest["inspection"]
        if not re.fullmatch(r"[0-9a-f]{64}", inspection["schema_signature"]):
            raise SnapshotError("Invalid snapshot schema signature")
        counts = inspection["table_counts"]
        if not isinstance(counts, dict) or any(type(count) is not int or count < 0 for count in counts.values()):
            raise SnapshotError("Invalid snapshot table counts")
        if not all(name in counts for name in config.required_tables):
            raise SnapshotError("Snapshot inventory lacks required tables")
        if not isinstance(inspection["coverage"], dict):
            raise SnapshotError("Snapshot coverage must be an object")
        for name in ("pytest", "pipeline_validate"):
            check = manifest["checks"][name]
            if type(check["exit_code"]) is not int or check["exit_code"] != 0:
                raise SnapshotError(f"Snapshot {name} check did not pass")
            _timestamp(check["completed_at_utc"])
        access = manifest["access"]
        if access["scope"] != "authorized_collaborators" or type(access["restricted_data_present"]) is not bool:
            raise SnapshotError("Snapshot access scope is invalid")
        if not isinstance(access["notice"], str) or not access["notice"].strip():
            raise SnapshotError("Snapshot access notice is missing")
    except (KeyError, TypeError, AttributeError) as exc:
        raise SnapshotError("Snapshot manifest has missing or malformed fields") from exc
    return manifest


def inspect_database(path: Path, required_tables: dict[str, list[str]]) -> dict:
    """Read schema/counts without loading external files or installing extensions."""
    if not path.is_file() or Path(str(path) + ".wal").exists():
        raise SnapshotError("Database is missing or has a WAL. Close database writers normally before sharing.")
    try:
        with duckdb.connect(str(path), read_only=True, config={"enable_external_access": "false",
                             "autoinstall_known_extensions": "false", "autoload_known_extensions": "false"}) as connection:
            rows = connection.execute(
                "SELECT table_schema, table_name, column_name, data_type, ordinal_position, is_nullable "
                "FROM information_schema.columns WHERE table_schema NOT IN ('information_schema','pg_catalog') "
                "ORDER BY table_schema, table_name, ordinal_position"
            ).fetchall()
            columns: dict[str, set[str]] = {}
            for schema, table, col, *_ in rows:
                columns.setdefault(f"{schema}.{table}", set()).add(col)
            for name, expected in required_tables.items():
                if not set(expected).issubset(columns.get(name, set())):
                    raise SnapshotError(f"Database lacks required table/columns: {name}: {expected}")
            tables = connection.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('information_schema','pg_catalog') "
                "ORDER BY table_schema,table_name"
            ).fetchall()
            counts = {}
            for schema, table in tables:
                qualified = '"' + schema.replace('"', '""') + '"."' + table.replace('"', '""') + '"'
                counts[f"{schema}.{table}"] = connection.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0]
            if any(name not in counts for name in required_tables):
                raise SnapshotError("Required snapshot tables must be stored tables, not external views")
            fact = columns.get("analytics.fact_housing_observation", set())
            coverage = {"observation_start": None, "observation_end": None,
                        "observation_count": counts.get("analytics.fact_housing_observation", 0)}
            if "observation_date" in fact:
                first, last = connection.execute(
                    "SELECT min(observation_date)::VARCHAR,max(observation_date)::VARCHAR "
                    "FROM analytics.fact_housing_observation"
                ).fetchone()
                coverage.update(observation_start=first, observation_end=last)
            restricted = any("hud_usps" in name or any(col.startswith("usps_") for col in cols)
                             for name, cols in columns.items())
            predicates = []
            if "source_id" in fact:
                predicates.append("source_id = 'hud_usps_ncwm_quarterly'")
            if "variable_id" in fact:
                predicates.append("starts_with(variable_id, 'usps_')")
            if predicates:
                restricted |= connection.execute("SELECT 1 FROM analytics.fact_housing_observation WHERE " +
                                                 " OR ".join(predicates) + " LIMIT 1").fetchone() is not None
            signature = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
            return {"schema_signature": signature, "table_counts": counts, "coverage": coverage,
                    "restricted_data_present": restricted}
    except duckdb.Error as exc:
        raise SnapshotError(f"Database cannot be inspected. Close open database sessions and check DuckDB compatibility: {exc}") from exc


@contextmanager
def exclusive_file(path: Path, create: bool = False) -> Iterator[BinaryIO]:
    """Exclude DuckDB readers AND writers without opening/modifying it via DuckDB.

    POSIX uses the same whole-file fcntl record lock as DuckDB. No other descriptor
    to this inode may be opened/closed in this process while the lock is held.
    Windows denies shared read/write access but permits the holder to rename it.
    """
    handle = None
    acquired = False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            opener = kernel.CreateFileW
            opener.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            opener.restype = wintypes.HANDLE
            native = opener(str(path), 0x80000000 | 0x40000000 | 0x00010000, 0x00000004, None,
                            4 if create else 3, 0x80, None)  # OPEN_ALWAYS / OPEN_EXISTING
            if native == ctypes.c_void_p(-1).value:
                raise OSError(ctypes.get_last_error(), "Cannot acquire exclusive database access")
            try:
                fd = msvcrt.open_osfhandle(native, os.O_RDWR | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(native)
                raise
            handle = os.fdopen(fd, "r+b")
        else:
            import fcntl
            handle = path.open("a+b" if create else "r+b")
            fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield handle
    except OSError as exc:
        if acquired:
            raise
        raise SnapshotError(f"File is busy or inaccessible: {path}. Close DuckDB, Python connections, the Data Browser and other snapshot commands, then retry.") from exc
    finally:
        if handle is not None:
            handle.close()


def replace_locked(staged: Path, destination: Path, staged_handle: BinaryIO, *, replace_existing: bool = True) -> None:
    """Rename while both old and new database handles remain exclusively held.

    Windows MoveFileEx (os.replace) refuses an open destination. FileRenameInfoEx
    with POSIX semantics supports replacement without releasing our locks. An
    unsupported filesystem fails closed rather than falling back to an unlock gap.
    https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
    """
    if os.name != "nt":
        if replace_existing:
            os.replace(staged, destination)
        else:
            # First install must not overwrite a file created by another program
            # during download/inspection. link fails atomically if it now exists.
            os.link(staged, destination)
            staged.unlink()
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class RenameInfo(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]

    encoded = str(destination.resolve()).encode("utf-16-le")
    buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + len(encoded))
    info = RenameInfo.from_buffer(buffer)
    info.Flags = (0x00000001 | 0x00000002) if replace_existing else 0
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    rename = kernel.SetFileInformationByHandle
    rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    rename.restype = wintypes.BOOL
    if not rename(msvcrt.get_osfhandle(staged_handle.fileno()), 22, buffer, len(buffer)):
        raise OSError(ctypes.get_last_error(), "Filesystem cannot atomically replace the locked database")
