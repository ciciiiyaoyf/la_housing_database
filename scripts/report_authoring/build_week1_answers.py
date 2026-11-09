"""Build the complete Week 1 answer document from its generated Markdown.

Run with the bundled document-authoring Python. The optional rendering command
uses the canonical render_docx.py and can use the repository's Microsoft Word
bridge on Windows. SVG charts are rendered with the supplied Node/Sharp runtime.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


INLINE = re.compile(r"(\*\*.*?\*\*|`[^`]+`|\[[^\]]+\]\([^\)]+\))")
TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")
SUBSCRIPT = re.compile(r"\b([A-Za-z]+)_(t|\(t[−-][0-9pj]+\))(?![A-Za-z0-9_])")


def math_text(paragraph, text):
    """Keep the elementary time subscripts as editable native Word equations."""
    cursor = 0
    for match in SUBSCRIPT.finditer(text):
        paragraph.add_run(text[cursor:match.start()])
        equation = OxmlElement("m:oMath")
        subscript = OxmlElement("m:sSub")
        for tag, value in (("e", match.group(1)), ("sub", match.group(2).strip("()"))):
            element = OxmlElement(f"m:{tag}")
            run = OxmlElement("m:r")
            content = OxmlElement("m:t")
            content.text = value
            run.append(content)
            element.append(run)
            subscript.append(element)
        equation.append(subscript)
        paragraph._p.append(equation)
        cursor = match.end()
    paragraph.add_run(text[cursor:])


def inline(paragraph, text):
    for piece in INLINE.split(text):
        bold = piece.startswith("**") and piece.endswith("**")
        code = piece.startswith("`") and piece.endswith("`")
        link = re.fullmatch(r"\[([^\]]+)\]\(([^\)]+)\)", piece)
        value = piece[2:-2] if bold else piece[1:-1] if code else piece
        if link:
            if link.group(2).startswith(("https://", "http://")):
                relationship = paragraph.part.relate_to(
                    link.group(2),
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                    is_external=True,
                )
                hyperlink = OxmlElement("w:hyperlink")
                hyperlink.set(qn("r:id"), relationship)
                run = OxmlElement("w:r")
                properties = OxmlElement("w:rPr")
                color = OxmlElement("w:color")
                color.set(qn("w:val"), "0563C1")
                properties.append(color)
                run.append(properties)
                content = OxmlElement("w:t")
                content.text = link.group(1)
                run.append(content)
                hyperlink.append(run)
                paragraph._p.append(hyperlink)
                continue
            # Evidence filenames remain meaningful in a downloaded Word/PDF copy.
            value = link.group(1)
        if not bold and not code and SUBSCRIPT.search(value):
            math_text(paragraph, value)
            continue
        run = paragraph.add_run(value)
        if bold:
            run.bold = True
        if code:
            run.font.name = "Consolas"
            run.font.size = Pt(9)


def format_document(document):
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin, section.bottom_margin = Inches(.65), Inches(.65)
    section.left_margin, section.right_margin = Inches(.65), Inches(.65)
    section.header_distance, section.footer_distance = Inches(.28), Inches(.28)
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[name]
        style.font.name = "Arial"
        style.font.color.rgb = RGBColor.from_string("000000")
        style.paragraph_format.widow_control = True
        for border in list(style.element.xpath("./w:pPr/w:pBdr")):
            border.getparent().remove(border)
    normal = document.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.08
    title = document.styles["Title"]
    title.font.size = Pt(24)
    title.font.bold = True
    title.paragraph_format.space_after = Pt(14)
    for name, size in (("Heading 1", 17), ("Heading 2", 12), ("Heading 3", 11)):
        style = document.styles[name]
        style.font.size = Pt(size)
        style.font.bold = True
        style.paragraph_format.space_before = Pt(12)
        style.paragraph_format.space_after = Pt(7)
        style.paragraph_format.keep_with_next = True
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.paragraph_format.space_after = Pt(0)
    r = footer.add_run("Week 1 housing forecasting   |   ")
    r.font.name, r.font.size = "Arial", Pt(8)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.core_properties.title = "Week 1 housing forecasting exercise answers"
    document.core_properties.subject = "Completed exercises and interpretation from the historical backtests"
    document.core_properties.author = "Los Angeles Housing Database"


def table_widths(headers):
    if len(headers) == 4 and headers[0] == "Horizon":
        return [.7, 2.05, 2.15, 2.3]
    if len(headers) == 4 and headers[0] == "Outcome":
        return [1.15, .7, 2.6, 2.75]
    if len(headers) == 4 and headers[0] in {"County", "Outcome and model", "Growth specification"}:
        return [2.1, 1.7, 1.7, 1.7]
    if len(headers) == 5 and headers[0] == "Raw start":
        return [1.2, .75, 1.65, 1.8, 1.8]
    if headers == ["Model", "Raw start", "Term", "Coefficient"]:
        return [1.35, 1, 3.3, 1.55]
    if headers[:2] == ["Model", "Raw start"] and len(headers) == 7:
        return [1.28, .87, 1.03, 1.03, .42, 1.3, 1.27]
    if headers[0] == "Specification" and len(headers) == 7:
        return [1.68, .92, .92, .92, .92, .92, .92]
    if headers[:2] == ["County", "Model"]:
        return [1.15, 1.25, .8, .8, .8, .8, .8, .8]
    if headers[0] == "Geography":
        return [1.35, 1.4, 1, 1, .75, 1.7]
    if headers[0] == "Horizon" and len(headers) == 7:
        return [.58, 1.5, .82, 1.1, 1, 1, 1.2]
    if headers[0] == "County" and len(headers) == 6:
        return [1.15, 1.3, 1.3, 1.3, 1.15, 1]
    if len(headers) == 4:
        return [1.6, 1.1, 2.25, 2.25]
    return [7.2 / len(headers)] * len(headers)


def add_table(document, records):
    headers, *rows = records
    widths = table_widths(headers)
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment, table.autofit = WD_TABLE_ALIGNMENT.CENTER, False
    for column, width in zip(table.columns, widths, strict=True):
        column.width = Inches(width)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        for attr, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
            element.set(qn(f"w:{attr}"), value)
        borders.append(element)
    table._tbl.tblPr.append(borders)
    for row_number, record in enumerate([headers, *rows]):
        row = table.rows[0] if row_number == 0 else table.add_row()
        properties = row._tr.get_or_add_trPr()
        properties.append(OxmlElement("w:cantSplit"))
        if row_number == 0:
            properties.append(OxmlElement("w:tblHeader"))
        for index, (cell, value, width) in enumerate(zip(row.cells, record, widths, strict=True)):
            cell.width = Inches(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_pr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement("w:tcMar")
            for side, amount in (("top", 70), ("bottom", 70), ("start", 85), ("end", 85)):
                element = OxmlElement(f"w:{side}")
                element.set(qn("w:w"), str(amount))
                element.set(qn("w:type"), "dxa")
                margins.append(element)
            tc_pr.append(margins)
            fill = "244A59" if row_number == 0 else "F2F6F7" if row_number % 2 == 0 else "FFFFFF"
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), fill)
            tc_pr.append(shade)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            paragraph.paragraph_format.keep_with_next = (
                row_number == 0 or row_number == len(rows) - 1
                or (len(rows) <= 4 and row_number < len(rows))
            )
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT if index == 0 else WD_ALIGN_PARAGRAPH.CENTER
            inline(paragraph, value)
            for run in paragraph.runs:
                run.font.size = Pt(8.6 if len(headers) > 6 else 9.2)
                run.font.bold = row_number == 0
                run.font.color.rgb = RGBColor.from_string("FFFFFF" if row_number == 0 else "000000")
    after = document.add_paragraph()
    after.paragraph_format.space_after = Pt(2)
    after.paragraph_format.line_spacing = 0.25
    after.add_run().font.size = Pt(2)


def raster_chart(svg, output, node, modules):
    script = (
        "const sharp=require(process.argv[1]);"
        "sharp(process.argv[2],{density:288}).png().toFile(process.argv[3])"
        ".catch(error=>{console.error(error);process.exit(1)});"
    )
    subprocess.run([str(node), "-e", script, str(modules / "sharp"), str(svg), str(output)], check=True)


def build(markdown, output, assets, node, modules):
    lines = markdown.read_text(encoding="utf-8").splitlines()
    assets.mkdir(parents=True, exist_ok=True)
    document = Document()
    format_document(document)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        picture = re.fullmatch(r"!\[([^\]]*)\]\(([^\)]+)\)", line)
        if heading:
            depth, title = len(heading.group(1)), heading.group(2)
            paragraph = document.add_paragraph(style="Title" if depth == 1 else f"Heading {depth - 1}")
            if depth == 2 and (title.startswith("Exercise 1 ") or title == "Final interpretation questions"):
                paragraph.paragraph_format.page_break_before = True
            inline(paragraph, title)
        elif line.startswith("|"):
            records = []
            while True:
                if not TABLE_RULE.fullmatch(line):
                    records.append([cell.strip() for cell in line.strip("|").split("|")])
                if i >= len(lines) or not lines[i].strip().startswith("|"):
                    break
                line = lines[i].strip()
                i += 1
            add_table(document, records)
        elif picture:
            svg = markdown.parent / picture.group(2)
            png = assets / f"{svg.stem}.png"
            raster_chart(svg, png, node, modules)
            caption = document.add_paragraph(picture.group(1))
            caption.paragraph_format.keep_with_next = True
            caption.paragraph_format.space_before = Pt(10)
            caption.paragraph_format.space_after = Pt(4)
            for run in caption.runs:
                run.font.size, run.bold = Pt(10), True
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(6)
            paragraph.add_run().add_picture(str(png), width=Inches(7.2))
        else:
            paragraph = document.add_paragraph()
            if line.startswith("- "):
                paragraph.style = "List Bullet"
                line = line[2:]
            inline(paragraph, line)
            if line.startswith("**") and line.endswith("**"):
                paragraph.paragraph_format.keep_with_next = True
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, default=Path("output/forecasting/week1/week1_exercises.md"))
    parser.add_argument("--output", type=Path, default=Path("output/forecasting/week1/week1_exercises.docx"))
    parser.add_argument("--assets", type=Path, default=Path("tmp/pdfs/week1_answers/assets"))
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-modules", type=Path, required=True)
    parser.add_argument("--render-script", type=Path,
                        help="Bundled documents skill render_docx.py, to create PDF and page PNGs.")
    parser.add_argument("--renderer-bin", type=Path,
                        help="Directory containing the chosen renderer, such as the existing Word bridge.")
    parser.add_argument("--render-dir", type=Path, default=Path("tmp/pdfs/week1_answers/render"))
    args = parser.parse_args()
    build(args.markdown.resolve(), args.output.resolve(), args.assets.resolve(),
          args.node.resolve(), args.node_modules.resolve())
    if args.render_script:
        environment = os.environ.copy()
        if args.renderer_bin:
            environment["PATH"] = str(args.renderer_bin.resolve()) + os.pathsep + environment.get("PATH", "")
        subprocess.run([
            sys.executable, str(args.render_script.resolve()), str(args.output.resolve()),
            "--output_dir", str(args.render_dir.resolve()), "--emit_pdf", "--dpi", "110",
        ], check=True, env=environment)
        shutil.copyfile(args.render_dir / args.output.with_suffix(".pdf").name, args.output.with_suffix(".pdf"))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
