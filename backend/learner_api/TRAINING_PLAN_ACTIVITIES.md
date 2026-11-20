# Training Plan Activities Overview — Step 7

New Training Plan drafts and new Edit revisions capture a learner-specific `activities-overview-v1` snapshot through the existing document workflow. The shared browser/Node jsPDF renderer renders the protected native Learning Plan block from this snapshot. Preview and download use the existing PDF artifact. Finalization and signature recording use the existing revision/signing services.

## Source and lifecycle

- Start with the persisted Learning Plan and the verified Step 6A module content service. PCP eligibility remains governed by its confirmed active-pathway allocation. No other-cohort catalogue is introduced.
- Resolve programme reviews through Step 6B using actual saved learner start/end dates and its existing Onboarding forecast rules. Review rows are projected occurrences, not appointment bookings. Participant/format data is unavailable from that service and is explicitly labelled unavailable.
- Module OTJ totals and existing meeting totals remain authoritative. Displayed component hours do not change those totals; nested assignment topics have no independent hours unless a separate source activity records them.
- The single canonical review occurrence list feeds the Reviews Schedule. A meeting is also displayed inside a module only when exactly one verified module range contains its date; it is counted once in totals. Ambiguous dates stay in the Reviews Schedule.
- New drafts freeze current source data. Refresh/resume, preview, download, finalization and signing do not refresh the snapshot from LMS. An existing custom draft is resumed without replacement. New revisions can capture subsequent source changes; historical issued/signed sources and artifacts are retained.
- Critical missing source identities, content, hours, dates or a module outside the saved programme period block issuance/finalization. Draft previews retain explicit reasons. Optional missing descriptions/participant details show unavailable states.
- No Soft Month, Last Month, reading weeks, dates, activity hours, participants or booked statuses are fabricated.

## Changed files in Step 7

- `backend/learner_api/training_plan_activities.py` — read-only snapshot composition, source validation, new-content upgrade.
- `backend/learner_api/training_plan_document.py` — derive new snapshots and expose frozen issued data; explicit initial issue readiness check.
- `backend/enrolment_api/document_templates/learner_documents.py` — capture upgraded new draft template and snapshot.
- `backend/enrolment_api/document_templates/preview.py` — new learner Training Plan source integration.
- `backend/enrolment_api/document_templates/revisions.py` — new Create/Edit capture, finalization guard, signing envelope retains overview.
- `backend/enrolment_api/document_templates/service.py` — bind newly issued snapshot, authoritative dates and warnings.
- `frontend/src/features/compliance-documents/trainingPlanActivities.ts` — purple A4 activity/review tables and protected editor representation.
- `frontend/src/features/compliance-documents/render.ts` and `pageDocument.ts` — version-gated native block integration; unmarked historical snapshots retain existing rendering.
- Backend `test_training_plan_activities.py` and frontend `trainingPlanActivities.test.ts` — isolated workflow/render regression tests.

All other pre-existing working-tree changes were preserved.

## Verification (2026-11-18)

- New backend workflow tests: **10 passed**. Real isolated revision persistence and actual shared Node PDF rendering; includes fixed and PCP learners, preview token/finalize/download, provider signature, new Edit signing envelope, custom-draft preservation, historical bytes, permission blockers, unchanged learner plan/allocation, long tables, date validation and review deduplication.
- Steps 4–6 backend regression tests: **119 passed**, using `programme_suitability.test_settings`.
- Focused frontend PDF/editor tests: **44 passed** (new Activities, PDF, pageDocument suites).
- TypeScript `npx tsc --noEmit`: passed.
- Scoped ESLint for changed renderer/editor/test files: passed.
- Production frontend build and shared PDF runtime build: passed.
- `git diff --check`: passed (Git reports its existing LF/CRLF normalization notice for render.ts).
- Visual review: generated ME, PCP and 160-extra-activity PDFs; repeated table headings, final activity present, branded pages and review schedule. Samples are isolated synthetic fixtures, not issued real-learner documents.

Broader pre-existing suites are not entirely green:

- Compliance frontend run before the four new tests: 256 passed, 2 failed of 258; old `createDocument` and `profileEdit` failures, including `ReviewDocuments` `.map` of undefined in BoardPage. Replacing only the modified shared renderer/editor modules with original HEAD code in memory reproduced both failures (12 passed, 2 failed in the two affected files).
- Legacy backend document suites: 28 tests, 18 setup errors (missing isolated `enrolment.Organisations` fixture table), 4 failures (existing Onboarding authorization returns 403). The revisions suite alone reproduced its same four authorization failures with the five modified Python modules' original HEAD functions loaded in memory. No workspace files were reset or replaced for baseline checks.
- Document tests must use `document_templates.test_settings`; core tests must use `programme_suitability.test_settings`. Combining them with the document runner aliases default/enrolment connections and violates core classes' declared database scope. The separately rerun suites above pass.

Logs and samples: `.cache/step7/` at repository root. PDFs: `fixed.pdf` (8 pages), `pcp.pdf` (9 pages), `long-module.pdf` (13 pages). Screenshots: `fixed-page-4.png`, `fixed-page-5.png`, `pcp-page-4.png`, `pcp-page-6.png`, `long-module-page-5.png`. `pdf-verification.json` records page coverage.

## DATABASE BLOCKERS — ACTION REQUIRED

Read-only inspection of learner vv bb (1601) found an actual date conflict:

- Enrolment database, `enrolment.Created_users`, saved `Start_date` / `End_date`: **2026-09-15 / 2027-10-31**.
- Its confirmed own-cohort/group LMS Marketing Technology (MarTech) delivery ends **2028-01-04**.

An authorized administrator must reconcile the saved programme period or the genuine LMS delivery schedule. The implementation does not change either. A new draft can show the discrepancy; official finalization is blocked while it persists. Required Step 6A/6B reader access was available at inspection; no additional permission grants were made. Participant/format fields are not supplied by the verified Meetings service and remain explicitly unavailable, rather than inferred.

No production LMS writes, migrations, role/permission changes, live learner mutations, real document creation/issue/signing, Learning Plan writes, automatic issuance, feature-flag changes, commits or pushes were performed in Step 7. Tests write only isolated Enrolment document fixtures. Existing issued/signed Training Plans remain untouched.
