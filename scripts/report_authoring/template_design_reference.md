# Reference

Historical template notes for the retired metro-geography report. Authoring tools
now live in this folder; previews and style evidence were preserved locally under
`tmp/report_qa_archive/`. The original paths below record the former layout.

- Absolute path: `[local template path redacted]`
- SHA-256: `d3006f9074e46823d103fe283be19901f55f75c19811558455d7a16c61797355`
- Render: `C:\Users\yifan\Downloads\la_housing_database\outputs\mentor_reports\template_reference_qa\render`
- Style evidence: `C:\Users\yifan\Downloads\la_housing_database\outputs\mentor_reports\template_reference_qa\template-style-evidence.json`
- Page count: 4
- Section count: 1

# Page system

- Letter portrait, 8.5 by 11 inches.
- One section with 1 inch margins on every side.
- Header and footer distances are 0.5 inch.
- The same centered header and right-aligned page-number footer recur on all pages.
- No first-page, odd-page, or even-page variation.

# Typography and components

- Body and metadata use Garamond, predominantly 12 point, black.
- The centered title uses bold Garamond at 24 point.
- Section headings are centered bold underlined Garamond and are kept with the following content.
- Body paragraphs are justified with 1.5 line spacing and a one-half-inch first-line indent or a leading tab.
- The first page contains a centered title, four metadata lines, a thin horizontal rule, and the first major heading.
- The header is centered, light gray, uppercase, and visually secondary.
- The footer contains a right-aligned PAGE field.
- Existing numbered-list definitions use a one-inch left indent and a one-quarter-inch hanging indent.
- The reference has no tables or images.

# Content flow

- Title and metadata establish recipient, author, date, and subject.
- Background explains scope, sources, and facts.
- Analysis interprets evidence and compares options.
- Conclusion states the recommendation and next actions.

# Slot map

- `word/header1.xml`, first header paragraph: replace the confidentiality placeholder with the project identifier.
- `word/document.xml`, first nonempty centered title paragraph: replace with a specific report title and apply the built-in Title style while retaining the template's black Garamond title appearance.
- `word/document.xml`, four metadata paragraphs: rewrite as Prepared for, Prepared by, Inventory date, and Subject.
- `word/document.xml`, first horizontal-rule paragraph: preserve as page-one furniture.
- `word/document.xml`, body paragraphs after the horizontal rule: replace all placeholder and sample text with the technical report. Reuse the centered bold underlined heading pattern, justified prose pattern, and numbered-list pattern.
- The report requires compact comparison tables that the template does not contain. Add them only where repeated records require row-by-row comparison, using Garamond, light gray borders, a restrained gray header, and no decorative color so they remain visually derived from the template.
- `word/footer1.xml`: preserve the page-number field and alignment unchanged.

# Package preservation

- Editable: `word/document.xml`, `word/header1.xml`, and the document relationships only if hyperlinks are added.
- Preserve-only: font files and font relationships, `word/footer1.xml`, `word/theme/theme1.xml`, `word/fontTable.xml`, content types, root relationships, and all embedded-font parts.
- Styles, numbering, settings, and section geometry should remain unchanged except for necessary document-content references produced by the authoring library.

# Fidelity gates

- Retained reference must still match the recorded SHA-256 before and after authoring.
- Letter size, portrait orientation, margins, header distance, footer distance, Garamond typography, centered title, metadata block, horizontal rule, restrained section headings, header, and page-number footer must remain recognizably source-derived.
- Every final page must render without clipping, overlap, broken tables, excessive blank space, or orphaned headings.
- Placeholder text, lorem ipsum, and confidentiality language must not remain.
- Package comparison must confirm that embedded fonts and the page-number footer are preserved.
