# Compliance Documents Builder — implementation and verification

**Activation update:** after explicit user approval the five storage tables were
installed in the existing database. See [STORAGE_SETUP_FIX.md](STORAGE_SETUP_FIX.md)
for the current deployed status and PostgreSQL verification. Earlier rollout
limitations described below are historical.

For the subsequent full-page editor and learner-specific revision extension, see
[FULL_PAGE_REVISIONS.md](FULL_PAGE_REVISIONS.md), including current activation status,
the additional learner Documents visibility requirement and merge coordination.
The sections below describe the original builder implementation.

Implemented on the existing `compliance-documents` branch. No commit, push, merge,
rebase or branch switch was performed. The working tree was clean before this task.

The admin route is `/users/compliance-documents`, next to the existing form builders.
It supports searching the eight original documents, editing safe structured blocks,
controlled dynamic fields, programme variants, saved drafts, PDF previews,
publication approval notes, optimistic concurrency and immutable version history.

## Activation status

**The code is implemented and locally verified; the new database tables have NOT
been installed on the connected production database.** Until activation, the
builder lists/previews originals and explains why Save/Publish are unavailable.
Existing issuance and signing continue through their original implementation.

The configured database endpoint belongs to the Neon branch named `production`.
The connected Neon integration has `VIEWER` permission and cannot create a test
branch. No production schema, learner row, signature, uploaded PDF or storage
object was written during this work. `.env` was not modified.

The applicable [Neon PostgreSQL skill](C:/Users/lenovo/.codex/plugins/cache/openai-curated-remote/neon-postgres/2.1.0/skills/neon-postgres/SKILL.md)
requires: **“Test a migration on a branch of production, against production-like
data, before applying it to production.”** An isolated PostgreSQL branch and a
deployment owner with suitable permission are therefore still needed to verify
and install the additive SQL. SQLite integration tests do not substitute for that
PostgreSQL DDL/trigger/locking verification.

Deployment procedure, from `enrollment/backend`:

1. Review `schema.sql` and create an isolated database branch using the existing
   project. Supply that branch's **direct** connection URL through the environment
   variable `DOCUMENT_TEMPLATE_DATABASE_URL`, using the team's normal secret handling.
   Do not put credentials in the command line, source control or this report.
2. Run `python manage.py apply_document_templates --check` and then
   `python manage.py apply_document_templates --dry-run`. The latter runs DDL in
   a transaction and explicitly rolls it back.
3. Run `python manage.py apply_document_templates` on the isolated branch. Verify
   all four new tables, unique constraints, immutable version/history triggers,
   and concurrent draft publication/issue/sign behaviour on PostgreSQL.
4. Complete deployment review before targeting production with its direct URL.
   Deploy backend and frontend together; restart workers after schema activation.

The command rejects schema writes through a pooled endpoint, uses the existing
enrolment alias unless an explicit direct target is supplied, and has a five-second
DDL lock timeout. It only creates the feature's tables/functions/indexes/triggers.
It does not migrate, rewrite or backfill old learner/documents/answers.

## Original documents and data

| Category | Stable template identity | Existing source/workflow |
|---|---|---|
| Compliance | `apprenticeship-agreement` | `learner_api/apprenticeship_agreement.py` and `apprenticeshipAgreementPdf.ts` |
| Compliance | `ilr` | `learner_api/ilr_document.py` and `ilrDocumentPdf.ts` |
| Compliance | `training-plan` | `learner_api/training_plan_document.py` and `trainingPlanPdf.ts` |
| Compliance | `written-agreement` | `learner_api/written_agreement.py` and `writtenAgreementPdf.ts` |
| Review | `review-eligibility` | Existing `eligibility-review` form/issue/sign handlers |
| Review | `review-rpl` | Existing `workspace` form/issue/sign handlers |
| Review | `review-health-safety` | Existing `training-plan` review form/issue/sign handlers |
| Review | `review-uln` | Existing `uln-privacy` form/issue/sign handlers |

`ilr` means the **Individual Learner Record document**. Its original PDF contains
an Extended ILR snapshot, but this change does not alter the wizard's Extended ILR
step, its answers, persistence, progression or database table.

`tools/seed_document_templates.mjs` extracts existing wording from the repository
using the TypeScript AST and static string extraction, without evaluating template
code. It copies all AA notes, Training Plan commitments/appendices, Written
Agreement declarations/delivery notes/cost labels, ILR rows/declarations, review
questions and full ULN wording. Matching backend/frontend `seed.json` snapshots
include source provenance hashes. No new contractual clauses were authored.

An unchanged original publication deliberately uses the retained original PDF
generator. All eight unchanged documents have byte-for-byte PDF equivalence tests
after normalising only creation time and the random PDF file identifier.

Edited versions use the same jsPDF download/export entry points, with paginated
A4 blocks and existing curriculum/cost/review data. They support editable titles,
headings, paragraphs, declarations, dates, custom tables, field substitutions,
signature placeholders, page breaks and bounded typography. Native data tables
keep their existing row links/calculations; custom tables allow adding/removing
rows and columns. Native column labels, cost labels and existing review question
wording are editable, while existing answer keys remain fixed. Dynamic values
are resolved only from the controlled field registry, never expressions or HTML.

Preserve the seed snapshots and original PDF generators after deployment. They
are the compatibility definitions for pre-builder documents, not a command to
regenerate contractual wording during every deployment. Future ordinary edits
should be new published versions through the builder.

## Version and signing safety

Four new unmanaged enrolment tables follow this repository's explicit schema
deployment convention:

- `Document_Template_Drafts`: one current draft per template/variant, revision,
  published-base token, editor and timestamp.
- `Document_Template_Versions`: append-only publication content, hash, number,
  programme applicability, author, timestamp and approval note.
- `Document_Template_Issues`: one binding to an **existing** document/review row,
  selected version, frozen template and resolved field values.
- `Document_Template_Render_History`: immutable, deduplicated rendered-data
  snapshots recorded through the existing signature/withdrawal handlers.

There are no edits to historical migrations and no changes to the old source
document tables. PostgreSQL triggers prohibit changing/deleting published versions
or render history, and protect issued template identity/content/field values.

Publishing requires the current saved revision, a matching signed preview token,
required original signing parties and explicit organisational review. Stale saves
and publications fail rather than replacing another administrator's changes.
Published programme overrides cannot overlap; other programmes retain the shared
template. The four existing document issuers bind the applicable version inside
their existing enrolment transactions.

Reviews created by either the existing Issue action or calendar booking are bound
when newly created. Rebooking keeps the original binding. For newly bound reviews,
the first existing signature freezes printable answers and context; answer edits
are then refused until the existing signatures are withdrawn. Earlier signed
render snapshots remain in history after withdrawal. Answer saves and signatures
coordinate on the existing review row to prevent a delayed save clearing a new
signature. Original signatory authorisation, mark capture and withdrawal remain.

Historical limitations are explicit:

- Pre-builder records have no historical template FK; they resolve to the original
  compatibility definition, independently of later publications. There is no
  speculative bulk backfill or re-issuance.
- Earlier review PDFs were generated from live answers/context and did not retain
  complete historical render snapshots. This implementation cannot reconstruct
  unknowable earlier wording/data. Existing stored/uploaded PDFs are retained;
  the new snapshot guarantee starts with newly bound reviews.
- Snapshots record the data/template/marks used by the existing PDF generators;
  they are not a replacement blob store or a new signature/evidence system.
- Native curriculum/cost/review calculations remain application logic. The editor
  does not invent funding rules, arbitrary expressions, extra signing roles or new
  answer fields. Programme assignments/cohorts are not changed by template edits.

## Every modified shared file

Paths below are relative to `enrollment/`. The feature implementation and tests
are otherwise isolated in `backend/enrolment_api/document_templates/` and
`frontend/src/features/compliance-documents/`.

| Shared file | Necessary integration |
|---|---|
| `backend/enrolment_api/urls.py` | One document-template URL include |
| `backend/learner_api/apprenticeship_agreement.py` | Bind existing issues; attach version metadata; archive existing signatures |
| `backend/learner_api/ilr_document.py` | Same integration for the Individual Learner Record PDF |
| `backend/learner_api/training_plan_document.py` | Same integration for Training Plan |
| `backend/learner_api/written_agreement.py` | Same integration for Written Agreement |
| `backend/learner_api/review_form.py` | Bind new reviews, expose versioned wording, freeze/archive newly bound signed reviews |
| `backend/learner_api/calendar.py` | Delegate existing review upsert through a helper that binds only newly created reviews |
| `frontend/src/api/apprenticeshipAgreement.ts` | Optional template metadata on the existing response type |
| `frontend/src/api/ilrDocument.ts` | Optional template metadata |
| `frontend/src/api/trainingPlanDocument.ts` | Optional template metadata |
| `frontend/src/api/writtenAgreement.ts` | Optional template metadata |
| `frontend/src/api/reviewForm.ts` | Optional template metadata |
| `frontend/src/components/feature/EnrollmentShell.tsx` | One existing staff-sidebar link |
| `frontend/src/router/enrollmentRoutes.tsx` | One lazy page and route inside the existing access gate |
| `frontend/src/lib/apprenticeshipAgreementPdf.ts` | Optional managed renderer before retained original renderer |
| `frontend/src/lib/ilrDocumentPdf.ts` | Same PDF adapter |
| `frontend/src/lib/trainingPlanPdf.ts` | Same PDF adapter |
| `frontend/src/lib/writtenAgreementPdf.ts` | Same PDF adapter |
| `frontend/src/pages/learner/onboarding/reviews/reviewDocument.ts` | Managed PDF adapter and section-scoped versioned question labels |
| `frontend/src/pages/learner/onboarding/reviews/form.tsx` | Issued wording preview and pinned labels/ULN wording; saved answer keys unchanged |

Other additions are `backend/enrolment_api/management/commands/apply_document_templates.py`
and `tools/seed_document_templates.mjs`. No dependencies or package locks changed.

## Integration points to coordinate before merging

- Sidebar/router registration can textually conflict with other new admin pages.
  Retain all independently added routes and links under the existing access gate.
- Document serializers/issuers/signers and the calendar review mirror can conflict
  with another developer's document or booking changes. Retain atomic enrolment
  writes, existing permissions/notifications and template bindings together.
- Review form question wording must remain scoped to its section and stable answer
  key. Do not copy builder wording into shared wizard/Extended ILR configuration.
- Keep the PDF adapters at every existing export entry point, and retain the old
  renderer for original/legacy snapshots. A textual merge that drops an adapter
  would silently print the wrong version.
- There is no Django migration-number dependency. Coordinate one reviewed run of
  the additive schema command and deploy matching frontend/backend code. PostgreSQL
  branch DDL/constraint/locking verification is still required before production.
- Skills Radar, KSB CSV/maps, suitability scoring/modules and wizard progression
  files have no changes in this task. The learner Documents tab categories,
  uploaded document APIs/storage and programme/cohort controls are unchanged.

## Verification results

| Check | Result |
|---|---|
| New isolated backend suite | **29 passed**; all eight seeds, authorisation, CSRF, draft CAS, preview tokens, missing values, publication/history, programme variants, deployment-command guards |
| Existing issuers/signers | All four exercised with synthetic learners and real in-memory document rows; v1 signed record retained after v2 publication/reissue |
| Review workflows | All four Issue paths plus all four calendar creation/rebooking paths exercised; signed snapshots and withdrawal history preserved |
| Existing Programme Suitability suite | **133 passed** |
| Targeted existing backend regressions | **34 passed**: document permissions/schema/policies/CV, Skills Radar scale, progression and Training Plan hydration |
| Frontend new + related existing suites | **77 passed in 12 files**: builder, API/CSRF, navigation, review wording, PDF output, learner documents/signing, Board page, ULN/review downloads, CV and Skills Radar |
| TypeScript | Passed |
| ESLint on feature and modified frontend components | Passed with zero warnings |
| Vite production build | Passed |
| Git whitespace/scope check | Passed; same branch, no unrelated protected feature changes |
| Chrome browser check | Eight documents visible; saved draft survives reload; actual five-page, 50,633-byte PDF displays resolved sample name; preview/publish flow and mobile navigation pass; zero page errors and zero Issue/Sign requests |

Browser verification used the real React page, existing jsPDF generators and
Chrome PDF viewer, with isolated synthetic API responses. Real issue/sign/version
binding was verified separately through the existing Django views using in-memory
SQLite. No claim is made that this exercised the live production database/Azure
storage, PostgreSQL triggers, or a production deployment.

The wider calendar/schema regression run has **five pre-existing unsuccessful
tests**, reproduced using the original calendar source read directly from `HEAD`
inside an isolated Python process, without altering the working tree:

- `BookingEndpointRestrictionTests.test_every_coach_session_type_is_rejected_on_a_weekend`
  expects the weekend error before the existing missing-case-owner error.
- `RescheduleEndpointTests.test_reschedule_returns_conflict_for_any_other_overlapping_session`
  returns 503 rather than the expected 409 in the existing test setup.
- `RescheduleEndpointTests.test_reschedule_updates_the_same_booking_record`
  returns 503 rather than 200 in the existing test setup.
- Both `EnrolmentDdlAliasTests` command-inspection tests fail because the existing
  `learner_api.management.commands.apply_review_detail_tables` module is absent.

The 46-test wider subset reports the same three failures/two errors with and
without this feature's calendar integration. These unrelated issues were not changed.

Reproduce principal checks from `enrollment/`:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test enrolment_api.document_templates.test_templates --settings=enrolment_api.document_templates.test_settings --noinput
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
npm --prefix frontend test -- src/features/compliance-documents/__tests__ src/pages/learner/compliance src/pages/learner/onboarding/__tests__/reviewDownload.test.tsx src/pages/learner/onboarding/__tests__/ulnPrivacyReview.test.tsx src/pages/users/__tests__/boardPage.test.tsx src/pages/users/wizard/__tests__/skillsRadarScale.test.tsx src/pages/users/wizard/__tests__/cvJobStep.test.tsx --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run build
```
