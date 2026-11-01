# Mandatory reason for choosing an alternative programme

Implemented on `CV-and-Programme-Suitability`, 2026-11-01. Existing uncommitted changes were
preserved. No commit, push, merge or branch switch was performed.

## Behaviour and persistence

Choosing an enabled L4 alternative now changes the existing native assessment dialog to a
small reason dialog, without any save or programme confirmation. The title, dynamic question,
textarea label/placeholder and Cancel / Submit Reason & Continue labels match the request.
Cancel and Escape return to the result without changing the saved selection. The disabled
L6 alternatives remain disabled and cannot open the reason dialog.

Submit Reason & Continue validates non-whitespace text and a 1000-character maximum, then
sends `action: select`, `selectedProgramme` and `alternativeSelectionReason` together in the
existing authenticated/CSRF-protected PUT. The backend validates the allowed programme and
reason, then saves the selection, exact reason, confirmation timestamp and revision under
the existing transaction and row lock. The original responses, recommendation and scores are
preserved. No scoring/recommendation/configuration logic changed. The matching length limit
counts UTF-16 units, as HTML textarea maxLength does, including supplementary Unicode text.

The new nullable `alternative_selection_reason` field is optional for historical records and
recommended-programme confirmations. Additive migration
`0004_assessment_alternative_selection_reason.py` was applied to the configured service DB;
all four feature migrations are applied. Nothing backfills or invents historical reasons.
GET/PUT responses expose `alternativeSelectionReason`, and existing history snapshots now
include it. Attempts to change a confirmed reason through any normal assessment action are
rejected with 403. Identical retry requests remain idempotent. A standalone reason save is
rejected: a reason cannot be persisted separately from the alternative confirmation.

The existing in-flight guard and disabled controls prevent duplicate submissions. Save
failures keep the dialog and written reason available for retry. Navigation is requested
only after the saved backend response confirms the selection. Existing wizard validation
still blocks other incomplete mandatory CV fields without losing the saved selection/reason.
Continuing with the recommended programme needs no reason and keeps the existing flow.

## Display

The shared purple result card preserves the original programme and matching score. Beneath
them it displays Your Confirmed Programme Selection, the prominent alternative-choice message,
and Your Reason, only for a confirmed different selection. Both the inline CV card and
Preview Assessment reuse this component. The original Preview Assessment label remains.
Confirmed preview has no choice controls, reason textarea or Continue action; Show Answers
stays read-only. Refresh/reopening reads the saved reason, not temporary form state.

The exact reason is rendered as React text, without HTML interpretation, with preserved line
breaks and safe wrapping for long content. Legacy confirmed alternatives without reasons
show the selection and omit the empty reason section. Recommended-programme confirmations
do not show an alternative/reason section. The existing staff assessment view displays the
learner-provided reason beside the preference; existing audited admissions reviews preserve
it. No new admin page or reason-editing workflow was introduced.

## Files changed in this enhancement

Backend, all under `backend/programme_suitability/`:

- `models.py`, `views.py`, `tests.py`
- `migrations/0004_assessment_alternative_selection_reason.py` (new)
- `README.md`, `ALTERNATIVE_REASON_REPORT.md` (new)

Frontend, all under `frontend/src/features/programme-suitability/`:

- `types.ts`, `AssessmentDialog.tsx`, `RecommendationResult.tsx`, `CvSuitabilityPanel.tsx`
- `__tests__/recommendation.test.tsx`, `__tests__/suitability.test.tsx`, `__tests__/api.test.ts`

No shared wizard, navigation, admin editor, CV upload or unrelated source file was changed
for this enhancement. Earlier shared-file changes remain in the working tree. The feature
directories remain untracked as part of the earlier implementation; include the full feature
and migration chain when preparing a PR and coordinate existing shared patches with colleagues.

## Verification

- Backend feature suite: **70 passed**, using the migration-backed, network-blocked memory
  database. Covers both pathways, missing/blank/non-string/over-limit reasons, Unicode limit,
  unchanged results, exact text persistence, idempotent retries, immutable confirmed reasons,
  legacy records, staff visibility/audit and rejection of separate reason writes.
- Existing backend suites (`enrolment_api`, `enrollment_service.tests`): **217 passed**.
- Frontend suitability, wizard, builder and learner onboarding regression suites:
  **272 passed across 33 files**. Full frontend type-check and lint passed.
- Production build passed. Migration drift check reported no changes; migration 0004 applied.
- Chrome synthetic-fixture checks passed for all four programmes, including mobile: valid
  alternatives open the reason dialog, empty reasons are blocked, Cancel makes no write,
  confirmed reasons persist across refresh and appear in read-only preview, recommended
  confirmations require no reason, L6 alternatives remain disabled, saved answers stay disabled.
  Both alternative flows also confirmed that another missing mandatory CV field blocks
  navigation while preserving the saved reason; completing it permits dynamic Next navigation.
  No real learner record, publication, upload or email was created. Browser fixtures were
  intercepted locally; real API protections/persistence were checked in the backend suite.
  Temporary harness files were removed. Ignored mobile screenshots remain in `frontend/.cache/`.

Apply the new schema in other environments before serving the updated API:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --noinput
```

Existing API consumers confirming an alternative must now include `alternativeSelectionReason`;
the recommended-programme flow and already-confirmed historical records remain compatible.
No historical migration was edited. There are no known outstanding implementation failures.
