# Learner-flow fixes

The existing scoring engine, 18 questions/72 answers, programme affinities, editable
admin weights, publication workflow and immutable assessment snapshots were preserved.
Work remains on `CV-and-Programme-Suitability`; previous uncommitted changes were kept.
No commits, pushes, branch merges, dependency upgrades, secret changes or new migrations
were made for these fixes.

## Exact 403 diagnosis

The failing pathway-selection operation is:

```text
PUT /enrolment_api/programme-suitability/<kind>/<learner_id>/
```

The client already sent the session cookie (`credentials: include`) and a masked
`X-CSRFToken` obtained from `GET /learner_api/enrollment-csrf/`. The API prefixes allow
authenticated learners and the assessment decorator allows only their own learner ID.
The request did not require an admin identity.

The effective service settings had a `CSRF_TRUSTED_ORIGINS` environment override which
replaced the local development defaults. `http://localhost:3000`, the reported browser
origin, was absent. Replaying a PUT with the correct cookie/token and the proxy's backend
host reproduced Django's exact failure:

```text
Origin checking failed - http://localhost:3000 does not match any trusted origins.
```

Django returned its HTML CSRF failure page, which the old JSON-only error parser discarded,
leaving the unhelpful `Suitability request failed (403)` message.

`enrollment_service/settings.py` now appends only the explicit localhost and 127.0.0.1
Vite origins when `DEBUG` is true, even if a deployment-origin override exists. Production
origin settings remain explicit. No authentication/ownership checks were removed, no
wildcard trusted origin was added, and feature writes retain `csrf_protect`. Same-origin
Vite proxy requests do not need a CORS permissions change.

Runtime replay after the fix accepted both local origins with a valid token; an
untrusted origin still returned 403. Middleware-level Django tests also bootstrap the
real CSRF cookie/token and successfully save both pathways, while missing-token and
untrusted-origin requests remain forbidden. The frontend refreshes a rejected CSRF
token once, never retries a genuine JSON permission rejection as CSRF, and provides
actionable errors plus Retry.

## CV independence and loading

Previously both the CV panel and Next awaited `saveIlr()` before fetching/opening the
assessment. That save writes the whole wizard and projects multiple step tables, so
unrelated database work delayed the assessment and failures could prevent access.

The assessment now has one controller owned by the existing wizard context. It prefetches
its questions and security token independently, shares concurrent reads, and caches only
in memory by learner and current wizard publication version. Existing account-change
cache invalidation clears these resources. Each new question/answer save does not fetch
the questionnaire or bootstrap CSRF again. Opening, closing, reopening and switching
pathways do not duplicate the configuration read.

**Start Assessment**, **Resume Assessment** and **Review Assessment** do not call or await
CV saving. The modal can open immediately with its own loading state, error and Retry.
It retains saved partial answers on close/reopen and restores confirmed preferences on
refresh. CV uploads remain intact; experience text is optional when the assessment is
enabled, while qualifications and other applicable mandatory fields retain validation.

## Mandatory completion and automatic navigation

The API reports `NOT_STARTED`, `IN_PROGRESS` or `SUBMITTED`. Only a real confirmed timestamp
and saved programme selection produce `SUBMITTED`. Editing submitted answers/preferences
reopens completion while retaining audit history and the original preference.

The wizard attaches a server-derived completion summary to its runtime draft; it is never
saved as an editable wizard answer. Existing completion calculations and the direct-route
guard now include it. Next and future sidebar navigation remain blocked until submission,
and an attempted direct future route cannot render its step body while assessment is
outstanding. Previous steps remain accessible. A required assessment failure blocks
advancement instead of silently falling through to the next step.

`learner_api.views.enrolment_user_detail` additionally checks the real database assessment
before a learner can set onboarding status to Submitted/Completed. A forged draft completion
marker cannot bypass this check. Staff workflows are preserved. Published configurations
without an enabled assessment, a hidden CV step, and already Submitted/Completed applications
remain compatible. Older unsubmitted applications following a newly enabled assessment
must complete it. The exemption is based on persisted onboarding status, not creation date.

**Submit & Continue** waits for backend scoring, validation and confirmation. Successful
submission updates the shared state and triggers one navigation to the next visible step
in the current wizard order (currently Personal Learning Record). If another required
field is missing, navigation is withheld and field errors are shown, while the assessment
remains submitted. The same mechanism handles CV reordered to the final step. In-flight
submission is guarded synchronously, and an identical repeated confirmation is idempotent
on the backend.

## Measurements

| Measurement | Before | After |
| --- | --- | --- |
| Read-only assessment GET against configured DB, first sample | 1,837 ms | 1,924 ms |
| Same GET, warm connection | 531 ms | 532 ms |
| Prefetched modal opening in Chrome with isolated local HTTP fixtures | Not instrumented | 395 ms |
| Questionnaire GETs per page in StrictMode browser flow | Duplicate panel/navigation reads possible | 1 |
| CSRF bootstraps during the browser flow | Previously once per write | 1 per page |
| CV saves required to open the assessment | Whole-wizard save awaited | 0 |

The database itself did not become faster; the improvement removes CV-save time and
repeat network work from the opening path. The browser measurement used actual feature
components and transport with test HTTP responses, not a real learner's credentials.
The DB measurements were read-only; no live assessments were created or changed. These
are local samples, not a guaranteed latency bound or a statistically controlled benchmark.

The Chrome flow verified direct-step blocking, empty CV/experience access, Next blocking,
a failed submission with Retry, successful automatic advancement to PLR, separate learner
preference/recommendation, refresh restoration and a mobile review modal without horizontal
overflow or runtime errors. Real scoring and CSRF enforcement were validated separately
through the Django tests. No mail was sent.

## Fix-specific files

Feature backend:

- `backend/programme_suitability/completion.py` (new authoritative completion policy)
- `backend/programme_suitability/views.py` (completion metadata, lean learner lookup, idempotent confirmation)
- `backend/programme_suitability/tests.py` (middleware/CSRF, completion, legacy, submission-backstop tests)
- `backend/programme_suitability/README.md` and this report

Feature frontend:

- `frontend/src/features/programme-suitability/useSuitability.ts` (new shared assessment controller)
- `frontend/src/features/programme-suitability/api.ts` (version-scoped cache, CSRF sharing/retry, useful errors)
- `frontend/src/features/programme-suitability/types.ts` (completion metadata)
- `frontend/src/features/programme-suitability/AssessmentDialog.tsx` (independent loading, Retry, submission guard)
- `frontend/src/features/programme-suitability/CvSuitabilityPanel.tsx` (independent entry point and shared state)
- `frontend/src/features/programme-suitability/__tests__/api.test.ts` (new transport/cache regression tests)
- `frontend/src/features/programme-suitability/__tests__/fixtures.ts` (extracted existing test fixtures)
- `frontend/src/features/programme-suitability/__tests__/continuation.test.tsx`
- `frontend/src/features/programme-suitability/__tests__/suitability.test.tsx`

Shared files needing coordination before merging:

| File | Small necessary hook |
| --- | --- |
| `backend/enrollment_service/settings.py` | Trust explicit local Vite origins only in DEBUG |
| `backend/learner_api/views.py` | Server-side assessment check on learner enrolment submission |
| `frontend/src/pages/users/types.ts` | Optional transient server completion summary |
| `frontend/src/pages/users/wizard/WizardContext.helpers.ts` | Optional typed feature controller |
| `frontend/src/pages/users/wizard/WizardContext.tsx` | Prefetch/controller integration and exclude completion marker from saved answers |
| `frontend/src/pages/users/wizard/WizardShell.tsx` | Mandatory navigation guard and dynamic advance after confirmed submission |
| `frontend/src/pages/users/wizard/validation.ts` | Include assessment in CV completeness, make CV text optional for this flow |
| `frontend/src/pages/users/wizard/steps/CvJob.tsx` | Display CV experience as optional with enabled assessment |

The existing learner route page was not changed: its `maxReachableStep`/whole-wizard
validation now receive the same completion summary through their existing hooks. Unrelated
steps, admin editor, programme mappings, scoring rules, authentication, global styles and
package dependencies were not changed by this fix.

## Validation commands

Executed successfully on 2026-10-31:

- Backend feature tests: **43 passed**, including enforced CSRF/session middleware,
  both pathways, learner ownership, server submission backstop, legacy exemption and
  idempotent confirmations.
- Existing backend regression tests: **217 passed**.
- Wizard/builder/onboarding/frontend feature suite: **225 passed across 32 files**.
- Final focused frontend rerun after response-error hardening: **40 passed**.
- Full TypeScript check, full ESLint check and production build: **passed**.
- Migration drift check: **no changes detected**; no new migration is required.
- Chrome fixture flow: **passed**, including mobile width, failed-submit Retry,
  automatic navigation and no CV save on assessment opening.

From `enrollment/`:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api enrollment_service.tests
npm --prefix frontend test -- src/features/programme-suitability src/pages/users/wizard src/pages/users/wizard-builder src/pages/learner/onboarding --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
```

No new database migration is required. Configuration/scoring snapshots and existing
assessment records were not rewritten. After merging shared hooks with colleagues, run
these checks again; restart Django if settings changes were not picked up automatically.
No configuration publication is needed merely to apply the fix to an already enabled
assessment. Production deployments must retain their own explicitly trusted frontend origin.
