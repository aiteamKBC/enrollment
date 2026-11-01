# Automatic programme recommendation result

Implemented on `CV-and-Programme-Suitability`, preserving the earlier uncommitted feature
and flow fixes. No commit, push, main-branch merge, dependency upgrade or secret change
was made. This change supersedes the previous automatic navigation immediately after
assessment submission.

## Learner behaviour

The existing questions and backend scoring remain unchanged. **Submit Assessment**
validates required answers, calculates/persists the authoritative result, and shows
**Your Recommended Programme**. It does not close the modal or navigate away.

A confident result shows one card: the configured programme title/level, its actual
stored matching index, an accessible progress bar, and up to three strongest saved
answer texts. Only that programme's score is displayed. The card explains that this
is a responsibility matching index and preliminary guidance, not acceptance probability
or a final admissions decision. Long suitability summaries and responsibility lists
remain available in configuration/admin tools but are not rendered in the learner result.

The recommended programme is selected by default. An allowed Level 4 alternative can
be saved from a Level 6 result, with confirmation of the selected programme. The original
recommendation, matching indices and explanation remain unchanged. The learner may also
return to the recommended Level 6 programme before continuing. Allowed selections survive
refresh/reopening.

Level 4 results expose no Level 6 selection control. Learners can save a non-blocking
admissions review request with a reason. It appears in the existing staff CV-step review
workflow, and staff can still record a reasoned manual recommendation through that
authorised endpoint. This does not introduce an eligibility rejection or change the
learner's existing programme placement.

`FURTHER_REVIEW_REQUIRED` and `INSUFFICIENT_INFORMATION` show a review result without
inventing a programme or exposing either numeric index. All scores stay stored for
admissions. Such submissions are completed with an empty programme selection and can
continue immediately without waiting for staff.

**Continue to Next Step** verifies successful submission and the current persisted
selection. Only its successful response closes the modal and triggers the existing
dynamic wizard navigation (currently Personal Learning Record). Missing unrelated
mandatory fields still show validation and withhold navigation; the submitted assessment
is retained. Next, sidebar, direct-route and final backend enrolment-submit guards remain
effective for unsubmitted mandatory assessments. Duplicate submissions remain guarded.

## Authoritative selection policy

| System recommendation | Permitted new learner selections |
| --- | --- |
| `ME_L4` | `ME_L4` |
| `MM_L6` | `MM_L6`, `ME_L4` |
| `APM_L4` | `APM_L4` |
| `PCP_L6` | `PCP_L6`, `APM_L4` |
| Inconclusive | None; pending admissions review |

`selection.py` derives permitted IDs from the actual recommendation outcome and the
versioned programme definitions. It never picks the numeric maximum. Existing critical
indicators, coverage, match/difference thresholds and ambiguous-result rules remain in
the original Python scoring engine.

Modified API requests cannot grant a higher-level choice or use another pathway's IDs.
Scores, programme affinities and recommendations supplied by the client are ignored.
Selection-only saves validate against the persisted server result and never invoke the
scoring engine. Existing staff review permissions and CSRF/ownership checks are preserved.

## Persistence and API

The existing assessment endpoint remains:

```text
GET/PUT /enrolment_api/programme-suitability/<kind>/<learner_id>/
```

PUT now accepts these actions (existing draft/confirm requests remain supported):

- `draft`: save partial answers; changed answers invalidate submission.
- `submit`: validate/calculate/persist, select a confident recommendation by default,
  or preserve an empty pending-review selection. It returns the result rather than
  performing navigation. An identical retry is idempotent.
- `select`: save an allowed preference using the persisted result, without rescoring
  or clearing submission.
- `continue`: require confirmed submission and unchanged/saved responses and selection;
  return the persisted state without changing assessment data.
- `request_review`: save the learner's reason in the existing review state and audit
  history; do not block continuation or claim email delivery.

The fields remain logically separate:

| Meaning | Storage / response |
| --- | --- |
| System recommendation | `result.recommendedProgramme` / `recommendedProgramme` |
| Learner preference | `selected_programme` / `selectedProgramme` |
| Selection origin | `programme_selection_source` / `programmeSelectionSource` |
| Submission | `confirmed_at`, completion policy / `assessmentStatus`, `completionStatus` |
| Automated outcome | `result.outcome` / `recommendationStatus` |
| Staff review status | `review_status` / `admissionsReviewStatus`, `reviewStatus` |

Responses include `allowedSelections`. Selection origins include `SYSTEM_RECOMMENDATION`,
`LEARNER_ALTERNATIVE`, `PENDING_REVIEW`, `NOT_SELECTED`, and `LEGACY`. Original preferences,
configuration snapshots and earlier revisions remain in their existing fields/history.
Staff review preserves the learner's review request when adding its decision.

Historical confirmed selections are not silently rewritten, even if they differ from
the new self-service policy. They are labelled `LEGACY`, remain visible as a previously
saved preference, and can continue. Any new explicit selection must follow the new
policy; an authorised staff review remains the route for exceptions. Historical results
are not silently recalculated when revisited or continued.

## Migration

Added and applied:

```text
0002_assessment_programme_selection_source
```

It only adds the selection-source column with `LEGACY` as the default for existing rows.
Existing choices, answers, scores and configuration snapshots were not rewritten. No
historical migration file was edited. The feature's isolated test runner now applies
all feature migration leaves rather than hardcoding the initial migration.

For another environment, use the usual additive feature migration:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --plan
.\backend\.venv\Scripts\python.exe backend/manage.py migrate programme_suitability --noinput
```

## Files changed for this request

Backend, all within `backend/programme_suitability/`:

- New `selection.py`: self-service policy based on the authoritative result.
- `views.py`: actions, default selection, result/source metadata, review request,
  preservation of historical choices and no selection-only rescoring.
- `completion.py`: submitted inconclusive results can complete without a selection.
- `models.py`: separate programme-selection source.
- New `migrations/0002_assessment_programme_selection_source.py`.
- `test_runner.py`: test the complete migration chain.
- `tests.py`: API policy, critical indicators, review completion, history and overrides.
- `README.md` and this report.

Frontend, all within `frontend/src/features/programme-suitability/`:

- New `RecommendationResult.tsx`: one compact result card, optional lower-level choice,
  and admissions review request.
- New `recommendation.ts`: presentation from backend outcome/evidence; no scoring.
- `AssessmentDialog.tsx`: Submit → Result → Continue, independent persistence/retry states.
- `CvSuitabilityPanel.tsx`: compact learner result; full existing comparison for staff.
- `useSuitability.ts`: submission and explicit continuation are separate events.
- `types.ts`, `api.ts`: action and result/source metadata contracts.
- New `__tests__/recommendation.test.tsx`.
- `__tests__/suitability.test.tsx`, `__tests__/continuation.test.tsx`.

No additional shared wizard, other-step or admin-editor files were edited for this request.
The earlier uncommitted shared integration hooks remain in the branch and still require
team review when the entire feature is merged. In particular, coordinate the existing
WizardContext, WizardShell, validation, URL/settings and learner-submit hooks from prior
work. Merge the feature with its additive migration and run the regressions below.

## Validation

Confirmed during implementation:

- **53 backend feature tests passed**, including all four defaults, rejected forged L6
  selections, both allowed L4 alternatives without rescoring, critical-evidence failures,
  inconclusive completion/continuation, raw submission guards, historical preservation,
  ownership/CSRF and staff review permissions.
- **217 existing backend regression tests passed**.
- **239 frontend tests passed across 33 files**, covering result display for all four
  programmes, backend-supplied indices/evidence, review outcomes, explicit continuation,
  persistence, mandatory guards, CV uploads and existing admin configuration.
- Full TypeScript, ESLint and production-build checks passed.
- Both feature migrations are marked `[X]` in the configured environment.
- Chrome fixture verification used results generated by the actual Python scoring
  engine: Submit stayed on CV and showed the result, an allowed alternative persisted,
  Continue advanced to PLR, review-required results continued without a selected programme,
  and mobile layouts had no horizontal overflow or runtime errors.

The browser fixture run did not submit a real learner application or send mail. Scoring,
permissions, policy and persistence were separately exercised against the real Django
views using the isolated in-memory test database. No new configuration publication is
needed for this flow change. Existing guidance/thresholds still require admissions review
before being treated as operational admissions policy.

Commands from `enrollment/`:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api enrollment_service.tests
npm --prefix frontend test -- src/features/programme-suitability src/pages/users/wizard src/pages/users/wizard-builder src/pages/learner/onboarding --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
```
