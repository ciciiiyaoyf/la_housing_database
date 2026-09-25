"""Set neutral display names on exported workbook themes without editing cells."""

from __future__ import annotations

import argparse
from copy import copy
import os
from pathlib import Path
import tempfile
from xml.parsers import expat
from zipfile import ZipFile


THEME_NAME = "Housing Database"
DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
TARGET_TAGS = {f"{DRAWING_NAMESPACE}}}{name}" for name in ("theme", "clrScheme", "fmtScheme")}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WHITESPACE = b" \t\r\n"


def _normalized_theme(xml: bytes) -> tuple[bytes, int]:
    """Patch only target attribute values, retaining XML bytes outside them."""
    if b"\x00" in xml[:100] or xml.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("Theme normalization requires UTF-8 XML.")
    changes: list[tuple[int, int, bytes]] = []
    parser = expat.ParserCreate(namespace_separator="}")

    def start_element(tag: str, attributes: dict[str, str]) -> None:
        if tag not in TARGET_TAGS or attributes.get("name") == THEME_NAME:
            return
        position = parser.CurrentByteIndex + 1
        while xml[position] not in WHITESPACE + b"/>":
            position += 1
        while True:
            while xml[position] in WHITESPACE:
                position += 1
            if xml[position] in b"/>":
                changes.append((position, position, f' name="{THEME_NAME}"'.encode("utf-8")))
                return
            attribute_start = position
            while xml[position] not in WHITESPACE + b"=":
                position += 1
            attribute_name = xml[attribute_start:position]
            while xml[position] in WHITESPACE + b"=":
                position += 1
            quote = xml[position:position + 1]
            value_start = position + 1
            value_end = xml.index(quote, value_start)
            if attribute_name == b"name":
                changes.append((value_start, value_end, THEME_NAME.encode("utf-8")))
                return
            position = value_end + 1

    parser.StartElementHandler = start_element
    parser.Parse(xml, True)
    for start, end, replacement in reversed(changes):
        xml = xml[:start] + replacement + xml[end:]
    return xml, len(changes)


def normalize_workbook_theme(path: str | Path) -> int:
    """Normalize generated XLSX theme labels atomically; preserve other ZIP members."""
    workbook = Path(path).resolve(strict=True)
    if workbook.suffix.lower() != ".xlsx":
        raise ValueError("Expected an exported .xlsx workbook.")
    if workbook.is_relative_to((PROJECT_ROOT / "data" / "raw").resolve()):
        raise ValueError("Archived raw files are immutable.")
    replacements: dict[str, bytes] = {}
    count = 0
    temporary: Path | None = None
    try:
        with ZipFile(workbook) as original:
            members = original.infolist()
            if len({member.filename for member in members}) != len(members):
                raise ValueError("Workbook archive contains duplicate ZIP member names.")
            for member in members:
                if member.filename.startswith("xl/theme/") and member.filename.endswith(".xml"):
                    payload, changed = _normalized_theme(original.read(member))
                    if changed:
                        replacements[member.filename] = payload
                        count += changed
            if not replacements:
                return 0
            with tempfile.NamedTemporaryFile(dir=workbook.parent, prefix=workbook.stem + ".", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
            with ZipFile(temporary, "w") as normalized:
                normalized.comment = original.comment
                for member in members:
                    payload = replacements.get(member.filename)
                    if payload is None:
                        payload = original.read(member)
                    normalized.writestr(copy(member), payload)
        os.replace(temporary, workbook)
        temporary = None
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    arguments = parser.parse_args()
    count = normalize_workbook_theme(arguments.workbook)
    print(f"Normalized {count} workbook theme labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
