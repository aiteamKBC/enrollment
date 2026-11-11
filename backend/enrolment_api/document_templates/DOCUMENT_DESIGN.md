# Shared light-purple document design

Implemented on `compliance-documents`, 5 November 2026. Earlier uncommitted work
was preserved. No commit, push, merge, rebase, branch switch or reset occurred.

## Use and publication safety

All eight Compliance/Review templates and their programme variants use the shared
design when the editable content contains `designTheme: "lavender-v1"`.
The global Builder prepares this design on its local editable copy when opening
an older template without an explicit design choice. It does **not** save or
publish automatically. Choose **Document design → Light purple — professional**,
then **Save Draft → Preview saved draft → Publish new version** after review.

The **Original layout** choice is saved as `designTheme: "classic"` and is respected
on reopening. Learner-specific draft editors have the same design choice; this
changes only the editable draft's presentation, retaining its original global
template assignment and historical signature rules. Existing drafts retain their
current design until the admin chooses a different one. New drafts based on a
newly published lavender template inherit it normally.

No live database record, issued document, signature, stored PDF or global version
was changed by this task. No database migration is needed: the optional design
identifier lives in the existing validated template-content JSON. No production
template was published and no learner was reissued a document.

Snapshots without the new identifier retain the previous renderer. In particular,
unchanged legacy templates continue using the original eight PDF entry points;
older edited structured snapshots keep the existing classic rendering branch.
The new design is opt-in and versioned: future visual redesigns should use a new
theme identifier instead of altering the meaning of `lavender-v1`.

## Shared implementation

- `documentTheme.ts` centralises the versioned palette, typography, spacing,
  margins, draft preparation and explicit-block override precedence. The editor
  receives the same colour/font tokens through scoped CSS variables.
- `documentTheme.css` styles only the paper surface. Lavender headings/labels,
  white value cells, fine borders, readable text, declarations and signature
  containers share consistent defaults. Scoped rules prevent the application's
  global `!important` dashboard table skin from recolouring document tables.
  Shared application CSS was not edited.
- `lavenderPdf.ts` paints those values through the existing jsPDF/AutoTable stack.
  The existing `render.ts` still resolves all native blocks, costs, learning plan,
  hours, review answers, conditional fields, signatures and controlled tokens.
  There are no separate document-specific redesigns or replacement PDF libraries.
- Headings have a clear hierarchy, body text and lists wrap by words, long tokens
  can wrap without clipping, label/value tables have padded cells and repeated
  headings, and AutoTable avoids splitting ordinary rows across pages. Very tall
  rows still flow across pages when necessary to retain all their content.
- Related signature panels stay together when they fit. Labels, names, positions
  and original dates remain present. Images use a proportional bounding box,
  preserving their original aspect ratio; historical signature provenance keeps
  its existing explicit statement that old marks do not approve revised wording.
- A4 page chrome has a subtle accent and optional existing header/footer values.
  Page numbering defaults on for new lavender content, while an explicitly saved
  page-number setting is respected. Long custom headers/footers receive enough
  content clearance; a header/footer that leaves no usable page area produces a
  clear preview error rather than clipped content or recursive pagination.

## Admin formatting and data protection

Shared defaults do not replace `pageLayout`, block styles or text-run overrides.
Custom font sizes, bold/italic/underline (including explicit false values),
alignment, spacing, custom header/footer and margins remain supported. The page
editor applies theme defaults with CSS rather than serialising them into every
text run. Existing explicit table/list block styles survive editing round trips.

All eight original seeds, legal clauses, titles, native mappings and signature
role definitions were retained. Programme variants keep their original IDs and
programme applicability; all four programme identifiers are covered by schema
tests. No answer, calculation, selection restriction, issuance rule, signing
permission, history/discard operation, Skills Radar or wizard step was changed.

## Files created or modified in this task

| File | Purpose |
|---|---|
| `backend/enrolment_api/document_templates/registry.py` | Validate/persist the optional design ID and distinguish themed content from legacy-equivalent content. |
| `backend/enrolment_api/document_templates/test_design_theme.py` (new) | Eight-document/four-programme compatibility, custom formatting, save-without-publish and old-version/issued-binding preservation tests. |
| `frontend/src/features/compliance-documents/types.ts` | Optional typed design ID. |
| `frontend/src/features/compliance-documents/documentTheme.ts` (new) | Shared, versioned design tokens/defaults. |
| `frontend/src/features/compliance-documents/documentTheme.css` (new) | Scoped editor paper styling and dashboard-style isolation. |
| `frontend/src/features/compliance-documents/lavenderPdf.ts` (new) | Shared styled jsPDF text, tables, signature panels and page chrome. |
| `frontend/src/features/compliance-documents/render.ts` | Opt-in painter dispatch within the existing content renderer; classic paths retained. |
| `frontend/src/features/compliance-documents/pageDocument.ts` | Themed HTML, compact question tables, preserved explicit rich-text/list/table formatting. |
| `frontend/src/features/compliance-documents/FullPageEditor.tsx` | Apply scoped tokens/defaults and repaint when design changes. |
| `frontend/src/features/compliance-documents/DocumentDesignControl.tsx` (new) | Reused design selector. |
| `frontend/src/features/compliance-documents/page.tsx` | Prepare the shared design on the editable global draft and explain Save/Preview/Publish. |
| `frontend/src/features/compliance-documents/LearnerRevisionEditor.tsx` | Same presentation choice for editable learner drafts. |
| `frontend/src/features/compliance-documents/__tests__/documentTheme.test.ts` (new) | Shared design, historical/classic compatibility, formatting, provenance and optional PDF review artifacts. |
| `frontend/src/features/compliance-documents/__tests__/builder.test.tsx` | Actual Builder design selection/save behaviour with no automatic publication. |
| `backend/enrolment_api/document_templates/DOCUMENT_DESIGN.md` (new) | This report. |

The original source PDF generators, review-question definitions, eight seeded templates,
document/signature writers, database schema and shared wizard files were not
modified in this task. Their earlier uncommitted changes remain preserved.

## PDF and browser verification

Sixteen synthetic previews were generated through the actual existing PDF entry
points: short and long values for each document. The long cases include wrapped
names/addresses, longer review answers, multi-page learning-plan modules,
delivery rows and long signatory names.

| Document | Short PDF pages | Long PDF pages |
|---|---:|---:|
| Apprenticeship Agreement | 4 | 4 |
| Individual Learner Record | 6 | 6 |
| Training Plan | 6 | 9 |
| Written Agreement | 6 | 6 |
| Eligibility Review & FS Discussion | 4 | 5 |
| RPL and Experience | 3 | 4 |
| Workplace Health & Safety Declaration | 2 | 4 |
| ULN Privacy Notice & Learner Acknowledgement | 3 | 3 |

The 75 pages were rasterised with PyMuPDF. Contact sheets and individual
representative pages were visually inspected for all eight types, including
signature sections, notes and long learning-plan content. Automated checks found
no text outside the page, no missing substantive word/number tokens relative to
the corresponding classic structured PDFs, lavender drawing primitives in every
sample, and correct proportions for all 44 signature images. Differences in
counts of repeated table header words (`Field`, `Value`, `Question`, `Answer`) are
recorded separately because wrapping changes how often a table header repeats;
they are not removed document content. More pages may be needed for the new
spacing; existing explicit breaks and every content block are retained.

Headless Chrome also exercised the real global Builder, full-page editor,
DocumentPreview and download for **all eight** documents. It checked computed
heading/header/label-cell colours, local Save Draft, actual PDF iframe creation
and download. There were zero uncaught page errors and no publication attempt.
Browser API responses used isolated synthetic fixtures, not a production login
or production template writes. The backend's real save/preview/publish logic was
separately exercised by isolated integration tests.

Local review artifacts are under `.cache/document-design/`: sixteen lavender PDFs,
matching classic PDFs, page PNGs, contact sheets, `inspection.json`,
`browser-results.json`, `admin-editor.png` and `admin-preview.png`. All contain
synthetic data. The temporary browser harness was removed after verification.

## Test commands and results

- `python backend/manage.py test enrolment_api.document_templates --settings=enrolment_api.document_templates.test_settings --noinput`: **91 passed**. This runner uses in-memory SQLite and blocks outbound connections; it does not load production credentials.
- Frontend document/notification/builder/learner/admin regression selection: **199 passed across 21 files**. After the final local document refinements, `npm --prefix frontend test -- src/features/compliance-documents/__tests__ --maxWorkers=2`: **111 passed across 12 files**.
- Final focused theme + Builder run with optional PDF export enabled: **31 passed**. Artifact generation is opt-in via `DOCUMENT_DESIGN_ARTIFACTS`; ordinary tests do not depend on local artifact files.
- `npm --prefix frontend run type-check`: passed.
- Scoped ESLint for `src/features/compliance-documents --max-warnings 0`: passed.
- `npm --prefix frontend run build`: passed. Existing mixed static/dynamic Review imports and plugin-timing notices remain.
- `git diff --check`: passed.

Historical compatibility tests cover the original eight generators and explicit
classic structured output. PDF identity comparisons normalise creation timestamps
and random PDF file IDs. Existing stored PDFs/uploads were never rewritten or
restyled. Existing A4 and built-in PDF font support remain unchanged.

No implementation/deployment prerequisite remains for the design controls. Using
the new design for future issuance requires the Admin's normal explicit review
and publication of a new template version; publication was intentionally not
performed automatically.
