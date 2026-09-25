# Document authoring and QA helpers

Preserved tools used to build and check the retired metro-geography report.
They construct documents and verify formatting; they do not acquire observations
or build the housing database. The report's useful findings are maintained in
[the data guide](../../docs/data_inventory_and_limitations.md#metro-variables-and-finer-geography-alternatives).

| File | Role |
|---|---|
| [build_msa_report.py](build_msa_report.py) | Recreates the historical report from its original, checksum-matched DOCX template; content is not a live data inventory. |
| [inspect_docx_template.py](inspect_docx_template.py) | Reads a document's structure and formatting as JSON evidence. |
| [compare_docx_packages.py](compare_docx_packages.py) | Compares package parts and preserved fonts/footer/theme. |
| [SofficeWordBridge.cs](SofficeWordBridge.cs) | Windows Microsoft Word document-to-PDF bridge with command-line conversion arguments. |
| [template_design_reference.md](template_design_reference.md) | Retained template design and preservation requirements. |

The Python document builders require `python-docx`, an optional authoring dependency
outside the database's requirements. The original template is external; its expected
SHA-256 is embedded in the builder. The Word bridge requires Windows and installed
Microsoft Word. Its existing compiled `soffice.exe` is preserved locally and excluded
from Git; it is a custom bridge, not a bundled LibreOffice installation.

The builder accepts `REFERENCE_DOCX OUTPUT_DOCX` and replaces its output path.
Use a new output filename when preserving an earlier report. Inspectors take the
document paths as arguments. These helpers are not part of the normal refresh.

Historical preview, comparison, and style evidence was preserved locally under
`tmp/report_qa_archive/`. Paths inside those old records describe their original
locations; they are not current pipeline dependencies. The standalone report was
removed after its content was consolidated, without removing these building tools.

[Scripts folder](../README.md) · [Project tutorial](../../README.md)
