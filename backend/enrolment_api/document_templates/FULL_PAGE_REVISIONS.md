# Full-page editing and learner document revisions

**Activation update:** the five feature tables are now installed after explicit
user approval. PostgreSQL draft/save/finalise checks passed for all four Compliance
documents; originals were unchanged. [STORAGE_SETUP_FIX.md](STORAGE_SETUP_FIX.md)
records the current status and additional PostgreSQL locking fix. The original
pre-deployment status below is retained as historical verification context.

Implemented on `compliance-documents`, 4 November 2026. Earlier uncommitted builder work was preserved. No branch switch, commit, push, merge, rebase, production data write or document/blob replacement was performed.

## Current activation status

**The implementation is locally verified but is not activated on the configured database.** A read-only `apply_document_templates --with-revisions --check` confirmed all five feature tables are missing: `Document_Template_Versions`, `Document_Template_Drafts`, `Document_Template_Issues`, `Document_Template_Render_History`, `Learner_Document_Revisions`.

Neon metadata identifies the connected project as Enrolment Corner, with only the `production` branch available and effective permission `VIEWER`. The connector cannot create a development branch or even obtain SQL credentials with that permission. A normal read-only application connection was used solely for the table-existence check. No production schema command was applied.

The applicable [Neon Postgres skill](C:/Users/lenovo/.codex/plugins/cache/openai-curated-remote/neon-postgres/2.1.0/skills/neon-postgres/SKILL.md) requires: “Test a migration on a branch of production, against production-like data, before applying it to production.” A temporary branch and Editor access (or a teammate providing an isolated branch/direct connection) are needed to complete that PostgreSQL verification and rollout. This is an actual access limitation, not an automatic approval-review rejection.

Until setup, revision history reads return an explicit setup state; revision writes return 503. Existing issuance/signing remains available through its existing fallback. The global builder continues listing/previewing the original templates, with Save/Publish disabled while storage is unavailable.

## Delivered behavior

- Page navigation replaces the paragraph/block navigation. Each selected A4 page has one coherent editable surface, not a stack of independent textareas.
- Formatting supports bold, italic, underline, sizes, heading/paragraph styles, alignment, lists, tables and row/column operations, registered field/date tokens, required signature placeholders, caret page breaks, page reordering and undo/redo.
- Storage remains controlled JSON: known text runs, lists, tables, explicit breaks and page layout. Pasted text is plain; dropped external HTML/files are rejected. Dynamic field chips serialize back to the allowlisted registry identifiers. Linked tables/questions/signatures keep their identities and existing data sources; double-click opens their existing settings UI.
- Explicit pages, margin/header/footer/page-number settings and overflow warnings are supported. The PDF renderer flows long content onto further pages; the PDF preview is the final pagination reference. This is not automatic Word-style pagination in the editing surface.
- All eight original legal/content seeds remain unchanged. Unchanged global templates and original R1 documents continue using their retained original PDF generators. Edited copies use the existing jsPDF workflow with safe rich formatting and historical signature provenance.
- Admin Documents rows offer Edit and History. Editing creates/resumes a draft for that learner, starts from the historical issued binding, and initially displays the existing signature appearances, names and dates.
- A reason and exactly one signature method are required before finalisation; saving uses an edit-version check and finalisation requires a preview token for that exact saved version.
- **Keep Existing Signatures:** the finalized copy is `Updated Copy`, retains historical images/names/dates, identifies the source revision per signature, and expressly does not record approval of changed wording. It creates no fresh signature event and is not independently Signed.
- **Sign Again:** the new appearance starts blank, with previous appearances available separately as historical references. Fresh capture uses the existing SignaturePad and existing source signature writers/party rules. All required fresh signatures produce the actual `Signed` status; further signing/withdrawal is refused once complete.
- Finalized R2 is read-only. Editing it creates R3. The global source template ID and number are retained across R1/R2/R3 even after a later global publication. Original source rows, signatures and blob paths remain unchanged by learner revision creation/finalisation.
- Learner Documents displays R1 then R2 underneath the original document, with working View/Download and neutral `Updated Copy`/`Available` labels. It does not display “Not Re-signed”. Genuinely re-signed revisions display `Signed`. Drafts and Edit actions are hidden from learners. Own-learner access is enforced by the backend.
- History refreshes on entry, browser focus, after local edits/signatures and every 60 seconds while mounted. Employer document queues expose applicable revision signing actions; ILRs and reviews that do not request employer signatures are excluded by backend permissions.
- The new immutable revision record captures ancestry, actor/time, reason, signature method, provenance, change summary and checksum. Fresh marks stay on dedicated non-active rows of the existing document/review models, bound to the same original template. They use existing render-history auditing; no alternate signature storage system was introduced.

## Historical-data limits

Pre-builder compliance records use the preserved original application template; they are labelled accordingly, rather than inventing a published V1. Reviews require an existing frozen historical render binding before learner-specific editing is permitted. An older review without that evidence is refused with an explanatory error; current live learner data is not presented as an invented historical signed snapshot. Existing original views/downloads remain available.

PDFs retain the application's existing font/character support. PDF layout is verified through the existing generators; the native editing surface is not a pixel-identical PDF renderer.

## Files changed for this extension

Feature implementation:

- Backend `document_templates/models.py`, `registry.py`, `service.py`, `test_runner.py`; new `page_content.py`, `revisions.py`, `revision_views.py`, `revision_urls.py`, `revision_schema.sql`, `test_revisions.py`.
- Frontend `features/compliance-documents/page.tsx`, `api.ts`, `types.ts`, `render.ts`, `BlockEditor.tsx`; new `FullPageEditor.tsx`, `pageDocument.ts`, `revisionApi.ts`, `LearnerRevisionEditor.tsx`, `LearnerDocumentRevisions.tsx`.
- Feature tests: updated `__tests__/builder.test.tsx`; new `__tests__/pageDocument.test.ts`, `__tests__/learnerRevisions.test.tsx`.
- Feature documentation: this report and its README link.

Every shared integration file modified by this extension:

| File | Targeted change |
| --- | --- |
| `backend/enrolment_api/urls.py` | Add revision API include. |
| `backend/enrolment_api/management/commands/apply_document_templates.py` | Optional `--with-revisions` additive setup/check. |
| `backend/learner_api/apprenticeship_agreement.py` | Trusted internal revision source selection; historical/completed revision lock guard. |
| `backend/learner_api/ilr_document.py` | Same scoped source selection and locking. |
| `backend/learner_api/training_plan_document.py` | Same scoped source selection and locking. |
| `backend/learner_api/written_agreement.py` | Same scoped source selection and locking. |
| `backend/learner_api/review_form.py` | Prevent answer/start-time changes to revision snapshots; return a lock conflict for locked signature changes. |
| `frontend/src/pages/users/BoardPage.tsx` | Revision actions/rows beneath the four compliance originals and issued review rows. |
| `frontend/src/pages/learner/compliance/page.tsx` | Revision rows beneath each original document card. |
| `frontend/src/pages/learner/onboarding/reviews/page.tsx` | Shared revision rows for issued reviews. |
| `frontend/src/pages/employer/EmployerDocumentsPage.tsx` | Applicable revision access/signing beneath the existing queue. |
| `frontend/src/pages/learner/onboarding/reviews/reviewDocument.ts` | Export the existing row builder for linked review content in the page editor. |
| `frontend/src/pages/learner/onboarding/__tests__/reviewDownload.test.tsx` | Isolate the existing review download tests from the separately tested revision component. |

Earlier pending changes in calendar integration, PDF wrappers, API types, sidebar/routing and wizard FieldEditor remain preserved. They are described in README/FIELD_BUILDER_UPGRADE; they were not refactored for this extension. No wizard step registration, ordering, navigation, completion or progress calculation was changed by this extension. No historical migration was edited.

## Verification

- **55 backend document tests passed**, including all eight document types, both signature methods, original/source isolation, global V1/V2 pinning, R3 creation, all required fresh signers, fresh-signature history, CSRF, reason/choice requirements, draft concurrency, preview tokens, own-learner and employer permissions, setup failure and finalized-content locking.
- **134 frontend tests passed across 18 files**: the document feature, field builder, learner compliance/reviews, admin board, CV/Job Description and Skills Radar regression selections.
- After the final title-compatibility fix, the complete document feature selection passed **74 tests across 9 files**, including one additional regression for document-title overrides after rich editing. TypeScript and targeted ESLint were repeated successfully.
- **133 Programme Suitability tests passed.**
- **34 targeted existing backend regressions passed**: employer signing, document schema/policies, CV documents, Skills Radar scale, learner progression and training-plan hydration.
- TypeScript, targeted ESLint (`--max-warnings 0`), production build and `git diff --check` passed. Build output notes existing mixed static/dynamic review imports and plugin timings; it exits successfully.
- Real Chrome against a disposable local in-memory Django backend passed: signed original V1, later global V2, admin full-page R2 edit with both historical marks visible, Save → PDF Preview → Finalise Keep, learner R1/R2 ordering and V1 labels, learner View/Download, refresh persistence and other-learner denial. R3 Sign Again was captured through the existing SignaturePad; completion by the employer produced Signed; a subsequent overwrite attempt returned 409. R2 remained Updated Copy.
- Separate real-browser formatting checks passed for Bold, page breaking at the caret while retaining following content, and undo/redo.
- Downloaded R2 was parsed and rasterized with PyMuPDF: full amendment text, original signer names/dates, historical R1 provenance and signature image resources were present. Original artifacts were not overwritten.

Browser artifacts are ignored local files under `.cache/`: `revision-browser-results.json`, `revision-toolbar-results.json`, `revision-fresh-signing-results.json`, `revision-R2.pdf`, `revision-learner.png`, `revision-fresh-signed.png`, `revision-full-page-editor.png`, `revision-R2-signatures.png`. Temporary source harnesses/servers are removed/stopped after testing.

The broader calendar regression baseline recorded in the earlier README is unchanged; its unrelated pre-existing failures were not fixed or included as passing here. PostgreSQL DDL triggers/constraints/concurrent locks still require the isolated branch verification below; SQLite tests do not prove those PostgreSQL behaviors.

## Rollout and merge coordination

1. Obtain an isolated Neon branch with production-like source schema/data and a direct connection. Keep its URL in `DOCUMENT_TEMPLATE_DATABASE_URL`; do not put credentials in command arguments or replace application `.env`.
2. Run `python backend/manage.py apply_document_templates --with-revisions --dry-run`, then apply on that isolated branch with `python backend/manage.py apply_document_templates --with-revisions`.
3. Verify immutable published templates/render history/R1/finalized R2 at the SQL level, unique one-draft and issue/number constraints, cross-connection draft/finalisation/signing races, source-table constraints and all eight document flows against that branch. The application connection should target the isolated branch during these checks.
4. Review the additive SQL before production activation; then use the approved direct production connection for the same setup command and repeat `--with-revisions --check`. The four base tables precede the new revision table within one transaction. No source document/signature/learner tables are migrated by this command.
5. Coordinate shared URL includes, the four existing signature writers, review locking, admin board/document rows and PDF row-builder export with teammates before merging. Keep API access checks, immutable snapshots, schema and UI revision integration together. Resolving only textual conflicts while dropping a locking or filtering guard would break historical integrity or expose documents.
6. Do not move fresh signing source clones into the ordinary active-document/current-review lists: their exclusion preserves existing document issuance and calendar behavior. Preserve employer-required filtering and ILR privacy.

The pending SQL addition is feature-specific setup, not a numbered Django migration competing with another developer's historical migration. It depends on the existing source document/review tables and the four base template tables. Coordinate its installation with the original builder rollout.
