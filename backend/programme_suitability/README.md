# CV & Programme Suitability

Implemented on `CV-and-Programme-Suitability`. The feature uses deterministic Python rules;
no AI, NLP, CV parsing, new package dependencies or catalogue records are introduced.
The four stable suitability identifiers are `ME_L4`, `MM_L6`, `APM_L4`, and `PCP_L6`.

## Administration and publishing

Open **Enrolment form builder → CV/Job Description → Programme Suitability Assessment**.
On first use, select **Add the initial 18-question assessment to this draft**. This loads
the supplied 18 questions, 72 answers and four editable programme guidance definitions.
It does not publish them.

The six tabs support enabling/disabling the assessment; question/answer editing,
addition, soft removal and ordering; multiple or single selection; required questions;
weights; per-programme answer affinities; critical L6 indicators; programme descriptions,
summaries, responsibilities and order; scoring thresholds; and server-side previews.
The four seeded answers are defaults, not a fixed limit. The validation limits are
100 questions and 30 answers per question to bound request size.

All configuration CRUD edits the existing wizard's local draft. Use its existing
**Publish changes** button to activate the configuration. Publication validates the
configuration and stores it in `Wizard_Form_Layouts.Layout.programmeSuitability`, in
the same transaction and version as the rest of the wizard. The existing optimistic
`baseVersion` conflict check and actor/timestamp audit apply. Draft edits follow the
existing in-memory workflow: leaving/reloading without publishing can discard them.
No second server-side draft store or separate publish endpoint was added.

The preview calculates against the draft on the backend, shows capped per-question
points, scores, coverage, critical evidence, outcome and reason codes, and never
creates a learner assessment.

## Learner and admissions workflow

The existing CV uploads and general form save API remain in place. **Start Assessment**
opens the assessment independently of CV upload, experience text, or wizard save state.
CV upload/experience text are optional for this flow; other mandatory step fields remain
validated. The questionnaire and security token are prefetched independently, with one
shared request and an in-memory cache scoped to learner and wizard publication version.

For an enabled published assessment, learners cannot proceed beyond CV until the backend
reports `SELECTION_CONFIRMED`. Submission alone remains `SUBMITTED_PENDING_CONFIRMATION`.
Draft answers and merely opening the modal do not complete the step.
The same completion summary drives the progress rail, Next, sidebar and existing direct-URL
guard. The existing backend enrolment-submit endpoint also verifies the real assessment.
Already `Submitted` or `Completed` applications are exempt from the new requirement.

Each successful answer save survives navigation/refresh. None-apply is an explicit empty
array, distinct from an unanswered question. **Submit Assessment** validates and persists
answers, scores and the provisional recommendation, then shows **Your Recommended Programme**.
It does not navigate. The learner sees one programme card with its actual matching index
and a short responsibility-based caption, without comparison cards, explanation paragraphs
or suitability lists. Administration retains the full suitability content and preview.

The programme with the greater final weighted index is recommended and selected by default.
An exact positive tie conservatively recommends Level 4 and is flagged internally. Critical
indicators, mixed evidence and threshold failures remain admissions diagnostics; they do not
replace a valid score-based recommendation with a learner-facing review card. When both
indices are zero, no programme is assigned and a short non-blocking message is shown.

The learner result contains one programme card, its actual index/progress bar and the short
line "Based on your answers and current job responsibilities." Long explanations, suitability
lists and the Request Admissions Review button are omitted. A Level 6 result has an enabled
Level 4 alternative button that opens a small mandatory reason dialog. Cancel returns to the
result without saving; Submit Reason & Continue atomically confirms the alternative with its
reason before requesting the next step.
A Level 4 result displays its Level 6 counterpart as disabled and marked Not Available. The
backend enforces those restrictions; choosing an alternative never changes the recommendation
or indices. **Continue to Next Step** confirms the recommended programme. Both actions persist
`selection_confirmed_at` before using the dynamic wizard order. Other mandatory fields still gate navigation. Neither
path makes a final admissions decision. Successful confirmation now synchronizes the selected
programme to the existing learner assignment; submission/preview alone never changes it.

The canonical enrolment field is `EnrolmentUser.programme` (`enrolment.Created_users.Programme`).
Confirmation resolves the saved selection to an existing active `curriculum.programmes` record
and updates that field plus `programme` / `programme_id` on an already-existing learner profile
in the same database transaction. It never creates another account, profile or catalogue row,
and does not change dates, status, cohort, group or training plans. Incompatible placements or
finalised assignments return an admissions review requirement without confirming the selection.
Existing Users/Edit User/board/profile APIs read these same fields. See
`PROGRAMME_SYNC_REPORT.md` for actual catalogue mappings, transaction guarantees and verification.

Historical confirmed selections can be reconciled with
`manage.py reconcile_suitability_programmes --dry-run` (the default); `--apply` writes only
safe missing assignments. A different existing programme is reported for review, never
blindly overwritten. Later authorised staff programme edits survive confirmation retries.

Submitted answers are immutable, including while programme confirmation is pending. Closing
the result preserves that pending state. **Show Answers** displays the saved version's pathway,
question titles/text, every original option and checked selections using disabled checkboxes;
**Back to Result** returns without saving. After confirmation, the inline result preserves the
original recommendation and score, shows a different selected programme separately, and hides
all alternatives. **Review Assessment** opens that result with **Show Answers** and **Close**
only. Learners cannot restart, replace answers or change a confirmed selection through the API.
Staff retain the existing separate, reasoned and audited admissions recommendation override.

New alternative confirmations require a non-whitespace reason, limited to 1000 characters
in both the UI and API. The nullable `alternative_selection_reason` field stores the exact
submitted text, including line breaks, alongside the selection and confirmation timestamp.
Learners cannot edit it after confirmation. The purple result card and Preview Assessment
show Your Confirmed Programme Selection and Your Reason from saved data. Admissions can
read the learner-provided reason beside their existing assessment review. Text is escaped
by React, preserves line breaks and wraps long content. Historical confirmed alternatives
without a reason stay readable; no reason is invented and no re-confirmation is required.
See `ALTERNATIVE_REASON_REPORT.md` for implementation and verification details.

`confirmed_at` records assessment submission; `selection_confirmed_at` independently records
programme confirmation. Migration `0003` adds the nullable timestamp without guessing whether
older submissions were confirmed. Existing unfinished applications without it reopen the saved
result for one explicit confirmation; previously Submitted/Completed applications stay exempt.

Staff open the learner's existing wizard CV step to inspect uploaded CV/job information,
answers, programme indices, critical evidence through the scored result, preliminary
recommendation and preference. **Admissions review** records a review status and an
optional manual programme recommendation with a required reason. It preserves the
original preference, automated result and previous revisions.

When staff suggest a different programme, an after-commit callback reuses
`login.email_azure.send_mail` if that existing transport is configured. No new mail
infrastructure is created. Delivery is recorded as `SENT` only when the transport
reports success; failures are recorded separately. With mail disabled/unconfigured,
the review stays visible in the learner's own assessment and no delivery is claimed.
`notifications.deliver_review_notification(id, revision)` is the retry/integration entry point.
Verification used mocks: no actual review email was sent.

## Scoring

For each programme and active question:

```text
answer points = question weight × programme affinity
question points = min(question weight, sum(selected answer points))
programme index = 100 × sum(question points) / sum(all active pathway question weights)
```

The cap is independent for each programme. Decimal arithmetic is used before final
display rounding. Unanswered, optional unanswered and none-apply questions stay in
the denominator. Coverage is the proportion of active question weight explicitly
answered; none-apply counts as answered, but gives zero evidence or points. Separate
evidence coverage is retained. Missing required answers prevent confirmation but not
partial saves or previews. No responsibility evidence yields `INSUFFICIENT_INFORMATION`.

The final rounded indices determine the learner-facing recommendation: Level 6 only when
its index is strictly greater than Level 4; otherwise Level 4, with a positive tie recorded
in `recommendationFlags`. Both-zero/no-evidence results remain `INSUFFICIENT_INFORMATION`.
The same interpretation applies to existing stored scores without recalculating answers.
A confirmed save/continue audits a policy upgrade rather than silently replacing old choices.

The existing editable thresholds (minimum index 60, difference 15, coverage 80%, two distinct
critical indicators) and mixed-evidence rules still calculate internal diagnostics. Their
outcome/flags are preserved in `admissionsReviewOutcome` and `admissionsFlags`. They do not
veto the weighted learner recommendation. The assessment still requires its required answers
before submission. A programme matching index is not eligibility or acceptance probability.

## Storage, migration and security

`migrations/0001_initial.py` creates only `programme_suitability_assessments` on the
default database, which this standalone service configures from its enrolment database
URL. No historical migration was edited. The existing database router excludes the
`enrolment` alias from Django migrations; do not migrate this feature on that alias.

Each learner has one current assessment with an immutable configuration snapshot and
wizard publication version, selected pathway, responses, server-computed result,
preference, original confirmed preference, review, revision and timestamps. Before
edits, the prior assessment/review is appended to its history. Configuration changes
do not rescore historical assessments. Started assessments remain pinned to their
original configuration, including when the published assessment is later disabled.

Apply only the feature migration from the repository directory:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --plan
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --noinput
```

This was applied successfully in the current configured environment and verified with
`showmigrations programme_suitability` (through `[X] 0004_assessment_alternative_selection_reason`). No suitability configuration
was published and no real learner assessment was created during implementation.

The APIs reuse the platform's session/ownership and enrolment access-grant checks.
Configuration changes and reviews additionally require staff; ordinary Django users
without staff privileges are rejected. Feature write endpoints use Django CSRF and the
existing `/learner_api/enrollment-csrf/` bootstrap. Answer/question IDs, activity,
pathway, multiplicity, programme relationships, ranges and revisions are validated on
the backend. Client-supplied scores/weights are ignored. Concurrent revisions return
409. Unpublished/unconfigured installations keep the existing flow; suitability storage
errors show Retry and block advancement when an assessment is required, while keeping CV saving available.

## API

All new paths are relative to `/enrolment_api/programme-suitability/`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `defaults/` | Staff-only seed configuration for the existing draft |
| POST | `preview/` | Staff-only validation and draft scoring; accepts `validateOnly: true` |
| GET | `<kind>/<learner_id>/` | Own assessment or current published questions when not started |
| PUT | `<kind>/<learner_id>/` | Start/resume, save answers, calculate results, select/confirm preference |
| PUT | `<kind>/<learner_id>/review/` | Staff review and reasoned manual recommendation |

The existing `GET /enrolment_api/wizard-layout/` and
`PUT /enrolment_api/wizard-layout/publish/` read and publish the configuration together
with the wizard. There is no competing form builder or programme catalogue.

## Files and merge considerations

New backend files in `backend/programme_suitability/`:

- `__init__.py`, `apps.py`, `models.py`
- `configuration.py`, `scoring.py`, `seed.json`
- `permissions.py`, `views.py`, `urls.py`, `notifications.py`
- `migrations/__init__.py`, `migrations/0001_initial.py`
- `tests.py`, `test_settings.py`, `test_runner.py`, `README.md`

New frontend files in `frontend/src/features/programme-suitability/`:

- `types.ts`, `api.ts`
- `AssessmentQuestions.tsx`, `ProgrammeComparison.tsx`, `AssessmentDialog.tsx`
- `SuitabilityGate.tsx`, `CvSuitabilityPanel.tsx`, `SuitabilityAdmin.tsx`
- `__tests__/suitability.test.tsx`, `__tests__/continuation.test.tsx`

Existing files modified (the likely merge-conflict points):

| File | Integration change |
| --- | --- |
| `backend/enrollment_service/settings.py` | Register feature app |
| `backend/enrollment_service/settings_test.py` | Register feature in existing isolated regression settings |
| `backend/enrollment_service/urls.py` | Include feature routes |
| `backend/enrolment_api/wizard_layout.py` | Validate/preserve suitability on publication and require staff for its configuration |
| `frontend/src/pages/users/wizard-builder/page.tsx` | Render feature editor only inside CV step |
| `frontend/src/pages/users/wizard-builder/builderOps.ts` | Preserve top-level draft content when editing steps/text |
| `frontend/src/pages/users/wizard/layout/types.ts` | Optional typed suitability configuration |
| `frontend/src/pages/users/wizard/layout/resolve.ts` | Preserve configuration while resolving existing layouts |
| `frontend/src/pages/users/wizard/steps/CvJob.tsx` | Mount feature assessment/review panel |
| `frontend/src/pages/users/wizard/WizardShell.tsx` | Save-before-assessment continuation hook scoped to CV |

For merging: review these small shared-file patches together with colleagues; merge
through the normal feature PR; resolve shared layout/context hooks deliberately; run
the checks below after integrating each developer's work; apply only the additive feature
migration to the intended service database; then review defaults with admissions and
publish from the existing builder. A Git branch does not isolate database configuration.
No commit, push, automatic branch merge or main-branch change was made.

## Verification

For the current confirmation/read-only refinement, see `FINALISATION_REPORT.md` for the
changed files, migration, state contract and final verification. The results below describe
the original implementation before subsequent refinements.

Verified on 2026-10-31:

- Backend feature: **37 passed**, including migration-backed persistence, scoring,
  permissions, immutable snapshots and mocked notification delivery/failure.
- Existing backend regressions: **217 passed**.
- Wizard, builder and suitability frontend suites: **193 passed across 28 files**.
- Final focused frontend rerun after display refinements: **16 passed**.
- Full frontend type-check, full lint and production build: **passed**.
- Feature migration applied and marked `[X]`; migration drift check reported no changes.
- Existing backend health returned 200; anonymous suitability-default requests returned 401.
- Desktop/mobile component browser preview passed; no real learner submissions,
  configuration publications or real email deliveries were performed.

Run from `enrollment/`:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api enrollment_service.tests
npm --prefix frontend test -- src/features/programme-suitability src/pages/users/wizard src/pages/users/wizard-builder --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
```

Feature backend tests apply the actual feature migration to in-memory SQLite with
network sockets blocked. They do not load `.env` or touch the configured database.
Existing backend regressions also run with sockets blocked. Mail transport tests are mocked.
Browser preview checks use local fixture data to inspect the admin editor at desktop
size and the native learner dialog at 390px; no browser runtime errors or horizontal
overflow were observed. This is component-level browser verification, not a real
learner's full authenticated production journey.

The programme guidance/thresholds still require admissions validation. Optional
catalogue references can be set to existing IDs; this feature never creates duplicates
or automatically changes an enrolled learner's programme placement.

## Learner-flow fixes

See [FIX_REPORT.md](FIX_REPORT.md) for the CSRF root cause, mandatory-completion policy,
loading measurements, fix-specific file list and follow-up validation.

## Automatic recommendation result

See [RECOMMENDATION_RESULT_REPORT.md](RECOMMENDATION_RESULT_REPORT.md) for the current
Submit ? Result ? Continue flow, selection restrictions, migration and verification.

## Simplified weighted result

See [SIMPLIFIED_RESULT_REPORT.md](SIMPLIFIED_RESULT_REPORT.md) for the current score-based
recommendation policy and compact result/alternative-button behaviour.
