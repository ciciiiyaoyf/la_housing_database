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
| [report_authoring/](report_authoring/README.md) | Preserved document-building and QA helpers, separate from data computation. |

Refresh helpers do not by themselves publish a complete database. Audit outputs
are evidence, not replacement observations. Preserve these scripts even when they
are invoked manually instead of by the main runner.

[Parent folder](../README.md) · [Start here](../README.md) · [Build and refresh guide](../docs/database_build_and_refresh.md)
