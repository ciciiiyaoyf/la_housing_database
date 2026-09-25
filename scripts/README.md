# Maintenance and audit utilities

Helpers for specific acquisition, verification, and presentation tasks. Run them
from the project root with the project Python environment; everyday browsing
starts with `Open_Data_Browser.cmd` in the root folder.

| File or folder | Purpose |
|---|---|
| `refresh_county_analytics.py` | Acquire and strictly clean the county/ZIP indicator inputs. |
| `refresh_macro_request.py` | Acquire and strictly clean the macro source contracts. |
| `report_macro_request.py` | Generate per-variable macro coverage and frequency-delivery evidence. |
| `audit_county_exact_sep22.py`, `audit_regimes_exact_sep22.py` | Independent formula, source, null-mask, and regime checks. |
| `audit_redfin_successor_csv_sep22.py` | Check successor Redfin files without accepting them into active history. |
| `data_browser.html` | The running browser interface; served by the reporting module. |
| `normalize_workbook_theme.py` | Normalize workbook theme display names without changing cell values. |
| `report_authoring/` | Preserved document-building and QA helpers, described below. |

Refresh helpers do not by themselves publish a complete database. Audit outputs
are evidence, not replacement observations. Preserve these scripts even when they
are invoked manually instead of by the main runner.

## Optional document tools

The former metro report's findings are in the
[data guide](../docs/data_inventory_and_limitations.md#metro-variables-and-finer-geography-alternatives).
Its building tools remain in `report_authoring/`:

- [build_msa_report.py](report_authoring/build_msa_report.py) recreates the historical
  report from its external, checksum-matched template. It takes `REFERENCE_DOCX
  OUTPUT_DOCX`, replaces the output path, and requires optional `python-docx`.
- [inspect_docx_template.py](report_authoring/inspect_docx_template.py) records
  structure/style evidence. [compare_docx_packages.py](report_authoring/compare_docx_packages.py)
  checks package parts and font/footer/theme preservation. They take document paths.
- [SofficeWordBridge.cs](report_authoring/SofficeWordBridge.cs) is a Windows Word-to-PDF
  bridge requiring installed Microsoft Word. Its compiled `soffice.exe` is retained
  locally and excluded from Git; it is not a LibreOffice installation.
- [Template design reference](report_authoring/template_design_reference.md)
  preserves authoring requirements. Old QA evidence remains in the ignored local
  `tmp/report_qa_archive/`; recorded paths describe the layout at that time.

These helpers are not part of data refresh. Use a new output filename to preserve
an earlier report. Historical workbook authoring scripts, when locally present,
also remain preserved; new reports belong under `output/`.

[Project tutorial](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
