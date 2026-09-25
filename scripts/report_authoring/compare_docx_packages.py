from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path


def inventory(path: Path):
    with zipfile.ZipFile(path) as archive:
        return {
            item.filename: (len(data := archive.read(item.filename)), hashlib.sha256(data).hexdigest())
            for item in archive.infolist()
        }


reference = inventory(Path(sys.argv[1]))
final = inventory(Path(sys.argv[2]))

all_parts = sorted(set(reference) | set(final))
changed = [name for name in all_parts if reference.get(name) != final.get(name)]
unchanged = [name for name in all_parts if reference.get(name) == final.get(name)]

print("CHANGED OR ADDED OR REMOVED")
for name in changed:
    status = "changed"
    if name not in reference:
        status = "added"
    elif name not in final:
        status = "removed"
    print(f"- {status}: {name}")

print("\nPRESERVATION CHECKS")
for name in [
    "word/footer1.xml",
    "word/fontTable.xml",
    "word/_rels/fontTable.xml.rels",
    "word/fonts/Garamond-bold.ttf",
    "word/fonts/Garamond-boldItalic.ttf",
    "word/fonts/Garamond-italic.ttf",
    "word/fonts/Garamond-regular.ttf",
    "word/theme/theme1.xml",
]:
    print(f"- {name}: {'preserved' if name in unchanged else 'changed or missing'}")
