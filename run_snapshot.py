"""Publish and receive completed private database snapshots without a server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from src.distribution.common import SnapshotError, load_snapshot_config
from src.distribution.publish import publish_snapshot
from src.distribution.update import status_snapshot, update_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show local installed version and detect local edits")
    for command in ("check", "update", "open"):
        subparser = commands.add_parser(command, help={
            "check": "Check the private release without installing it",
            "update": "Install a verified release at database/housing.duckdb",
            "open": "Update first, then open the local database in the DuckDB CLI",
        }[command])
        subparser.add_argument("--tag", help="Select a specific housing-db- release; default is newest published")
        if command == "open":
            subparser.add_argument("--read-only", action="store_true", help="Open a read-only SQL session")
    for command in ("prepare", "publish"):
        subparser = commands.add_parser(command, help="Validate and package" if command == "prepare" else "Validate and publish a private release")
        subparser.add_argument("--tag", required=True, help="Unique housing-db- release name")
        if command == "publish":
            subparser.add_argument("--acknowledge-authorized-sharing", action="store_true",
                                   help="Confirm every repository reader may receive all included data")
    args = parser.parse_args(argv)
    try:
        config = load_snapshot_config(Path(__file__).resolve().parent)
        executable = None
        if args.command == "open":
            local_cli = Path(sys.executable).parent / ("duckdb.exe" if sys.platform == "win32" else "duckdb")
            executable = str(local_cli) if local_cli.is_file() else shutil.which("duckdb")
            if executable is None:
                raise SnapshotError("DuckDB CLI is missing. Install the project's requirements in the active virtual environment.")
        if args.command == "status":
            result = status_snapshot(config)
        elif args.command in ("prepare", "publish"):
            result = publish_snapshot(config, tag=args.tag, prepare_only=args.command == "prepare",
                                      acknowledge_authorized_sharing=getattr(args, "acknowledge_authorized_sharing", False))
        else:
            result = update_snapshot(config, tag=args.tag, check_only=args.command == "check")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.command == "open":
            if not args.read_only:
                print("Writable local session. Save permanent changes as Python/SQL; direct database edits will block the next update.", flush=True)
            command = [executable]
            if args.read_only:
                command.append("-readonly")
            command.append(str(config.database_path))
            return subprocess.run(command, cwd=config.root, check=False).returncode
        return 0
    except (SnapshotError, OSError) as exc:
        print(f"Snapshot operation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
