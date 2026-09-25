from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from scripts import normalize_workbook_theme as normalization


def test_theme_names_change_without_touching_other_xml_or_zip_members(tmp_path: Path) -> None:
    workbook = tmp_path / "report.xlsx"
    theme = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<d:theme xmlns:d="http://schemas.openxmlformats.org/drawingml/2006/main" name="Vendor theme">'
        b'<!-- Preserve formatting and comments. -->'
        b'<d:themeElements><d:clrScheme name = \'Vendor palette\'><d:accent1><d:srgbClr val="123456"/></d:accent1></d:clrScheme>'
        b'<d:fontScheme name="Keep this font name"/>'
        b'<d:fmtScheme name="Vendor format"/></d:themeElements></d:theme>'
    )
    expected_theme = theme.replace(b"Vendor theme", b"Housing Database").replace(b"Vendor palette", b"Housing Database").replace(b"Vendor format", b"Housing Database")
    unchanged = {
        "xl/worksheets/sheet1.xml": b'<worksheet><cell value="00123" formula="SUM(A1:A3)"/></worksheet>',
        "xl/styles.xml": b"unchanged cell styles",
        "xl/media/image1.bin": bytes(range(256)),
    }
    with ZipFile(workbook, "w") as archive:
        archive.comment = b"preserve archive comment"
        for filename, payload in {"xl/theme/theme1.xml": theme, **unchanged}.items():
            info = ZipInfo(filename, date_time=(2025, 1, 2, 3, 4, 6))
            info.compress_type = ZIP_DEFLATED
            info.comment = b"member comment"
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    with ZipFile(workbook) as archive:
        original_metadata = [(info.filename, info.date_time, info.compress_type, info.comment, info.external_attr) for info in archive.infolist()]
    assert normalization.normalize_workbook_theme(workbook) == 3
    with ZipFile(workbook) as archive:
        assert archive.comment == b"preserve archive comment"
        assert [(info.filename, info.date_time, info.compress_type, info.comment, info.external_attr) for info in archive.infolist()] == original_metadata
        assert archive.read("xl/theme/theme1.xml") == expected_theme
        for filename, payload in unchanged.items():
            assert archive.read(filename) == payload
    normalized_bytes = workbook.read_bytes()
    assert normalization.normalize_workbook_theme(workbook) == 0
    assert workbook.read_bytes() == normalized_bytes


def test_raw_workbook_is_never_modified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(normalization, "PROJECT_ROOT", tmp_path)
    raw = tmp_path / "data" / "raw" / "source.xlsx"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"immutable source bytes")
    with pytest.raises(ValueError, match="immutable"):
        normalization.normalize_workbook_theme(raw)
    assert raw.read_bytes() == b"immutable source bytes"
