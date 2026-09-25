# Validation results

Local structural QA, warning, raw-completeness, and finance validation reports.
`qa_summary.csv` summarizes checks; `qa_failures.csv` records details when present.
The pipeline and tests produce these files for a particular local build.

Structural failures stop validation. Plausible economic outliers and expected
source gaps remain warnings for review. A prior successful report does not prove
a later build passed. Keep the reports used to substantiate a published result;
re-run validation after a relevant change.

[Parent folder](../README.md) · [Start here](../../README.md) · [Build and refresh guide](../../docs/database_build_and_refresh.md)
