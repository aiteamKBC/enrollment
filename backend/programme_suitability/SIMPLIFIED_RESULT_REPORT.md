# Simplified weighted programme recommendation

Implemented on `CV-and-Programme-Suitability`. Earlier uncommitted work was preserved.
No commit, push, branch change, merge, dependency upgrade, secret edit or schema migration
was made for this request. This policy supersedes the earlier automatic-review vetoes and
the previous result screen described in `RECOMMENDATION_RESULT_REPORT.md`.

## Recommendation behaviour

The existing question weights, answer affinities, multi-selection caps, active-question
denominator and score normalisation are unchanged. The final calculated matching indices
now determine the learner-facing recommendation:

- Marketing: MM L6 when its index is strictly greater than ME L4; otherwise ME L4.
- Project Controls: PCP L6 when its index is strictly greater than APM L4; otherwise APM L4.
- An exact positive tie uses L4 and records `SCORE_TIE_LEVEL_4_DEFAULT` internally.
- Both-zero/no-evidence assessments do not assign a programme; they remain submitted and
  show one short, non-blocking message.

The recommendation remains preliminary. Critical indicators, minimum index/difference,
coverage and mixed-evidence rules still run as admissions diagnostics. Their original outcome
and flags are retained as `admissionsReviewOutcome` and `admissionsFlags`, together with the
existing critical evidence and breakdown. They no longer veto a valid weighted recommendation.
`recommendationPolicy` identifies the interpretation as `WEIGHTED_SCORE_V2`.

The regression case uses real configured affinities to produce **ME 52.96 / MM 80.85**,
with an unmet critical-indicator requirement. MM L6 is recommended at 80.85; the critical
failure is retained internally instead of becoming the learner's result screen.

Existing stored results also receive this interpretation from their saved indices. GET
does not rewrite their scores, answers or choices. An explicit selection/continue operation
audits and persists the new policy metadata when needed. If an old submitted positive-score
review result has no selection, Continue saves the score-based default without recalculating
answers. Previously saved historical preferences are retained rather than silently replaced.

## Result and continuation

The learner result displays Assessment Completed, one purple-bordered recommendation
card, the configured programme title/level, that programme's actual index/progress bar and
"Based on your answers and current job responsibilities." A short preliminary-matching
note avoids implying eligibility. The long review card, request-review button, comparison,
second score, explanation paragraphs and responsibility lists are absent.

There is one alternative option below the card:

| Recommendation | Alternative control |
| --- | --- |
| ME L4 | Disabled Marketing Manager L6 — Not Available |
| MM L6 | Enabled Choose Marketing Executive L4 |
| APM L4 | Disabled Project Controls Professional L6 — Not Available |
| PCP L6 | Enabled Choose Associate Project Manager L4 |

The labels use the configured programme definitions, not hardcoded display names. The
existing backend policy enforces the same permitted IDs. Forged higher-level or different-
pathway choices are rejected. Staff overrides remain available in the authorised review API.

Selecting the allowed L4 alternative persists it first, preserves the original recommendation
and matching indices, then invokes the existing dynamic next-step handler. This works both
in the modal and in the saved-result panel. Failed saves remain on CV and expose Retry.
Continue to Next Step verifies saved submission/selection and advances normally. Both routes
still validate remaining mandatory fields; a saved assessment/choice is retained if another
field needs correction. Submission itself shows the result and does not navigate.

The old Request Admissions Review action remains supported in the backend for compatibility;
its learner-facing button is removed. Admin configuration, suitability content, CV uploads,
other wizard steps, authentication/CSRF and mandatory-assessment guards were preserved.

## Files changed for this request

Backend, under `backend/programme_suitability/`:

- `scoring.py`: return the weighted recommendation after calculating the unchanged indices
  and internal admissions diagnostics.
- `selection.py`: shared score-based interpretation and policy metadata; permitted selections
  use that same authoritative result.
- `views.py`: adapt stored-score results and persist/audit the default on Continue where needed.
- `tests.py`: updated superseded review-veto assertions; added exact-score, tie, stored-result
  and internal-threshold regression coverage.
- `README.md`, `SIMPLIFIED_RESULT_REPORT.md`.

Frontend, under `frontend/src/features/programme-suitability/`:

- `RecommendationResult.tsx`: compact card, one alternative and disabled L6 control.
- `recommendation.ts`: configured alternative lookup; removed long explanation rendering.
- `AssessmentDialog.tsx`: allowed alternative saves and advances without another Continue click.
- `CvSuitabilityPanel.tsx`: same saved-result alternative action with persistence/error state.
- `types.ts`: optional internal policy/diagnostic metadata.
- `__tests__/recommendation.test.tsx`, `__tests__/suitability.test.tsx`.

No additional shared wizard/admin/settings files were edited for this request. Earlier shared
integration hooks remain part of the uncommitted feature and should still be coordinated with
colleagues when merging the complete branch. No new migration or configuration publication
is needed for this change.

## Verification

- Backend feature: **57 tests passed**.
- Existing backend regressions: **217 tests passed**.
- Wizard, builder, onboarding and frontend feature suites: **241 tests passed across 33 files**.
- Full TypeScript, ESLint and production build: **passed**; migration drift check reported no changes.
- TypeScript and ESLint checks passed.
- Chrome fixture run used results calculated by the actual Python engine: all four recommendation
  scenarios, the 80.85/52.96 case, disabled L6 choices, alternatives that save then advance,
  failed-save Retry without early navigation, and non-blocking zero evidence passed.
- Desktop/mobile screenshots were inspected; no horizontal overflow or runtime errors occurred.

The browser used isolated HTTP fixture responses, not a real learner's credentials. Backend
policy, scoring and persistence were independently tested through Django views with the
memory-only database. No real learner assessment, programme choice or email was changed
during verification.

Relevant commands from `enrollment/`:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api enrollment_service.tests
npm --prefix frontend test -- src/features/programme-suitability src/pages/users/wizard src/pages/users/wizard-builder src/pages/learner/onboarding --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
```
