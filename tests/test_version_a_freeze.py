from __future__ import annotations

import csv
import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FREEZE_MANIFEST = PROJECT_ROOT / "metadata" / "version_a_freeze_manifest.csv"


def test_version_a_files_and_output_remain_byte_identical() -> None:
    """Versions B/C must never mutate the accepted Version A implementation."""

    with FREEZE_MANIFEST.open(encoding="utf-8", newline="") as handle:
        frozen = list(csv.DictReader(handle))

    assert frozen
    for row in frozen:
        path = PROJECT_ROOT / row["relative_path"]
        assert path.is_file(), f"Frozen Version A path is missing: {path}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == row["sha256"], (
            f"Version A changed at {row['relative_path']}: "
            f"expected {row['sha256']}, got {actual}"
        )
