# Alternative programme controls restricted to confirmation popup

Implemented on `CV-and-Programme-Suitability`, 2026-11-01. Existing uncommitted work was
preserved. No commit, push, branch switch or merge was performed.

## Root cause and fix

`RecommendationResult` previously gated alternatives on the saved confirmation alone,
without distinguishing the inline summary from the confirmation popup. `CvSuitabilityPanel`
also supplied a direct programme-selection save handler, allowing pending selections to be
made from the inline page. Pending and confirmed submissions both used Review Assessment.

The result component now defaults to a summary with no selection callback. Its typed
confirmation mode requires an explicit callback and renders alternatives only for a
submitted, unconfirmed assessment. Only the confirmation popup opts into this mode.
The inline component no longer has any programme-selection request handler.

Closing a submitted, pending popup leaves Resume Programme Selection on the CV page.
That action reopens the saved pending result for confirmation. Confirmed records show
Review Assessment and a read-only result with Show Answers and Close. The disabled saved
answer view and existing dynamic wizard navigation remain unchanged.

The existing Django `selection_confirmed_at`, API `selectionStatus`/`selectionConfirmedAt`,
confirmation actions and post-confirmation API protections were inspected and reused.
No backend or schema change was necessary. Confirmation is persisted before navigation;
submitted status alone is not treated as selection confirmation. Recommendation, scores,
questions, weights, alternative rules, admin/publishing and CV uploads were not changed.

## Files modified in this fix

All changes are inside `frontend/src/features/programme-suitability/`:

- `RecommendationResult.tsx`: explicit confirmation mode, default read-only summary.
- `CvSuitabilityPanel.tsx`: remove inline selection handler; pending Resume action.
- `AssessmentDialog.tsx`: supply confirmation controls only while selection is pending;
  share the guarded confirmation handler between Continue and the permitted alternative.
- `__tests__/recommendation.test.tsx`: summary/confirmation rendering regression tests.
- `__tests__/suitability.test.tsx`: all four programme journeys, pending resume, persisted
  confirmation across fresh mounts, read-only review/answers and no extra selection writes.
- `ALTERNATIVE_BUTTON_FIX_REPORT.md`: this report.

No shared wizard files were modified in this fix. Existing shared-file changes from the
earlier feature work remain in the working tree and still need normal merge coordination.

## Verification

- Frontend suitability, wizard, builder and learner onboarding suites: **260 passed across
  33 files**, including nine additional regression cases for this fix.
- Backend programme suitability suite: **64 passed**, including persisted confirmations,
  permitted alternatives, rejected post-confirmation changes and immutable submitted answers.
- Frontend type-check, lint and production build verified separately.
- `git diff --check` passed.

Frontend refresh/login restoration was verified using fresh component mounts with persisted
API responses; backend persistence/protections used the isolated migration-backed test DB.
No real learner record, publication, file upload or email was created during these checks.
No known outstanding implementation issue remains.
