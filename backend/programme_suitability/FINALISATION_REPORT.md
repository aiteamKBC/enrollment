# Programme selection confirmation and submitted assessment review

Implemented on `CV-and-Programme-Suitability`, 2026-11-01. No commit, push, merge or branch
switch was performed. Existing uncommitted changes were preserved.

## Persisted state and API protections

- `confirmed_at` continues to mean assessment submission. Submission freezes the answers,
  pathway and configuration snapshot, and reports `SUBMITTED_PENDING_CONFIRMATION`.
- Additive migration `0003_assessment_selection_confirmed_at` records programme confirmation
  separately in `selection_confirmed_at`. It was applied successfully to the configured
  local service database. All three feature migrations are marked applied.
- `continue` confirms the original recommended programme; `select` confirms an allowed
  alternative. Both save the confirmation before requesting wizard navigation. Zero-evidence
  assessments can explicitly continue without assigning a programme.
- `selectionStatus: SELECTION_CONFIRMED` and `selectionConfirmedAt` come from the saved row,
  not browser state or client-supplied fields. Recommendation and selected programme remain
  independent. Existing weighted scoring, questions, weights and recommendation rules are
  unchanged. Selection actions never call the scoring function again.
- Submitted answers/pathway cannot be replaced through draft, submit, select, continue or
  review-request writes. Draft/restart attempts after submission return 403. Confirmed
  programme changes return 403. Identical submission/selection retries remain idempotent;
  conflicting revisions remain protected by the row lock and revision check.
- The existing staff-only admissions review endpoint still supports a reasoned, audited
  manual recommendation independently of the learner's original result and selection.
  Existing permissions, CSRF, admin editor/publishing and CV uploads remain in place.

## Learner flow and completion

Before confirmation, the result offers Show Answers, the existing permitted/disabled
alternative, and Continue to Next Step. Closing/reopening retains this pending result.
Submission alone does not advance or complete CV. Next, sidebar and existing direct-route
validation all require the confirmation summary, and raw final enrolment submission checks
the actual saved confirmation on the backend. Other mandatory fields still gate navigation.

After confirmation, the inline purple result preserves the original recommendation/score,
shows a different selected programme separately and offers only Review Assessment below it.
Alternatives and programme-changing controls disappear, including after refresh/new session.
Review opens the saved result with Show Answers and Close; no Continue, Edit or restart.

Show Answers renders the saved configuration version's pathway, question titles/descriptions,
every original answer option, selected answers and explicit none-apply answers. Disabled
checkboxes prevent changes; Back to Result returns without any write or recalculation.

Older assessments without the new timestamp deliberately remain pending until one explicit
confirmation; submission alone is not evidence that selection was confirmed. Existing
Submitted/Completed enrolments remain exempt from the new completion requirement.

## Files changed for this refinement

Backend feature directory `backend/programme_suitability/`:

- `models.py`, `completion.py`, `views.py`
- `migrations/0003_assessment_selection_confirmed_at.py` (new)
- `tests.py`, `README.md`, `FINALISATION_REPORT.md` (new)

Frontend feature directory `frontend/src/features/programme-suitability/`:

- `types.ts`, `useSuitability.ts`, `AssessmentDialog.tsx`, `RecommendationResult.tsx`
- `SubmittedAnswers.tsx` (new), `CvSuitabilityPanel.tsx`, `SuitabilityGate.tsx`
- `__tests__/recommendation.test.tsx`, `__tests__/suitability.test.tsx`, `__tests__/continuation.test.tsx`

Shared files changed **in this refinement**:

| Shared file | Necessary change |
| --- | --- |
| `frontend/src/pages/users/wizard/WizardContext.tsx` | Add persisted selection confirmation to the transient completion summary |
| `frontend/src/pages/users/wizard/WizardShell.tsx` | Gate Next/sidebar/future-step rendering and continuation on confirmation |
| `frontend/src/pages/users/wizard/validation.ts` | Require confirmation in existing completion/direct-route validation |

Other shared changes already present in the working tree belong to the preceding feature
implementation and were preserved. Coordinate these three context/navigation/validation
patches with colleagues. The feature directories are still untracked together with that
earlier implementation; include them and the additive migrations when preparing your PR.
Historical migrations must not be edited or reordered during merge.

## Verification

- Backend feature: **64 passed**, using migration-backed in-memory SQLite with sockets
  blocked. Includes all four confirmations, both allowed L4 alternatives, disallowed L6
  choices, persisted snapshots, immutable answers before/after confirmation, forged state,
  raw final enrolment bypass prevention, idempotent retries and authorised staff review.
- Existing backend suites (`enrolment_api`, `enrollment_service.tests`): **217 passed**.
- Frontend wizard, builder, learner onboarding and suitability regression suites:
  **251 passed across 33 files**.
- Full frontend type-check, full lint and production build passed.
- Migration drift check: no changes detected. Feature migration `0003` applied successfully.
- Chrome browser checks passed for ME L4, MM L6, APM L4 and PCP L6, including a mobile
  viewport: submit without navigation, read-only selected/unselected answers, close/reopen,
  pending Next guard, allowed/disabled alternative, confirmation, dynamic next, return to CV,
  refresh and read-only review. These browser checks used synthetic intercepted API fixtures;
  actual Django persistence and protections were tested separately above. No real learner
  assessment, publication, upload or email was created. Temporary browser harness files were
  removed; ignored screenshots remain in `frontend/.cache/confirmed-*.png`.

To apply this schema change in another checkout/database:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --noinput
```

The Node test environment emits existing localStorage/canvas notices; they do not fail the
suites. Browser verification used fixtures rather than an authenticated production session.
There are no known outstanding implementation failures.
