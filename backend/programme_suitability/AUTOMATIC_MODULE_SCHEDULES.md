# Automatic learner-facing LMS schedule visibility

This supersedes the earlier display-approval gate. The normal learner Step 6
fetches real future LMS schedules automatically. No administrator approval,
review query or offering-specific code change is required to display or locally
choose a recognised delivery. There is no booking/session persistence endpoint.

## Matching and safety

- Scope comes from the confirmed programme and frozen academic configuration,
  using the four previously verified LMS programme IDs. Academic module IDs,
  active pathway and current selected optional IDs are kept distinct.
- No shared universal LMS academic-module ID is available in the existing reader.
  `module_catalogue_id` is retained as the delivery ID, never misused as an
  academic identity. Cohort/group references remain their actual recorded IDs.
- Exact-title matches allow Unicode/HTML entity, case, punctuation, whitespace
  and `&`/`and` normalization. Word order and qualification words such as APMG,
  APM and PMP are preserved. The UI says **Exact LMS title match**, not that a
  qualification or booking has been verified.
- Documented possible titles from the previous audit are scoped by programme,
  pathway, academic ID and the saved academic title. These are **Possible LMS
  match** candidates, not verified equivalences. There is no unrestricted fuzzy
  search, token deletion, cross-programme borrowing or automatic substitution.
- The historical `schedule_review_candidates.json` filename is retained, but its
  purpose is now possible-title evidence for automatic display. It contains no
  offering IDs, dates, groups or approvals. The old `APPROVED_BINDINGS` gate has
  been removed. New eligible deliveries with recognized titles appear from the
  next fresh fetch without editing this evidence file or frontend code.
- All remote data still passes through the existing dedicated
  `enrolment_modules_reader` and parameterised SELECT-only connection. No new
  aliases, credentials, roles, schemas, migrations or startup connections exist.
- Reader deletion/archive/composite-join/date checks, additional test-record
  exclusions and `start_date > current Europe/London date` apply. Pagination reads
  to exhaustion and distinct delivery/group IDs are never collapsed.
- PCP may use eligible deliveries across PCP cohorts. ME/MM/APM are restricted
  to their one saved home cohort and have no session-selection controls. Pending
  fixed-programme home assignments cannot borrow another cohort's dates.
- The home cohort is read from the existing persisted assignment and is always
  the first non-editable row. A delivery choice never writes or replaces it.
- One choice per PCP academic module stays in local React state. Tentative
  choices explicitly say **Provisional choice - not saved or booked**. All
  choices are unsaved/non-bookings and disappear after reload. No write request
  is issued by selecting a delivery.

## Refresh behaviour

The existing GET-only module-schedules endpoint runs on Step 6 open, active-path
or optional-module changes, page refresh and return to focus/visibility.
Academic rows update immediately; schedule requests coalesce rapid toggles over
120ms. A component-scoped 15-second response cache and in-flight deduplication
avoid duplicate reads. Focus/visibility refresh bypasses that cache, with a
one-second duplicate-event guard. There is no periodic polling or shared
cross-account cache. Explicit Retry makes a fresh read; errors do not alter
academic module selections, credits, completion or navigation.

## Chartered example — actual LMS data

The freshly read PCP scope contains 29 reader-eligible records, 15 future
deliveries and **4 future candidates for the six selected Chartered modules**:

| Academic module | Match confidence | Recorded future deliveries |
|---|---|---|
| Project Planning and Control | Possible (`PPC`; Chartered PMO equivalence is uncertain) | G1/G2: 10 Feb–31 May 2028 |
| Portfolio Management (APMG), selected optional | Possible (`Portfolio Management`; APMG qualifier not established) | G1: 7 Jun–20 Sep 2028; G2: 8 Jun–27 Sep 2028 |
| Stakeholder Engagement and Communication | No future delivery | Matching delivery began 30 Sep 2026 |
| Project Management Office | No future delivery | Matching delivery began 5 Nov 2026 |
| Risk, Quality and Issue Management | Unavailable | No reliable counterpart; Risk Management alone is not substituted |
| AI in Project Management | Unavailable | No reliable counterpart; AI in Project Control is not substituted |

Earned Value is absent because it is not selected. These four actual bars are
tentative matches; none is claimed as verified qualification equivalence. Their
delivery cohort is October 2026; the example learner's home remains Feb 2027.
The axis starts at the first real offering month in 2028 and includes all returned
dates. IDs and dates are fetched from LMS, not configured in frontend code.

## Open and verify

Use the existing learner page, with no special query:

`http://localhost:3000/learner/onboarding/programme-suitability`

Use your actual frontend port and normal learner sign-in. The existing API path
is `/enrolment_api/programme-suitability/apprenticeship/<id>/module-schedules/`.
The old staff-review option remains optional; it is not used for learner data.
Synthetic visual preview remains development-only and clearly marked SAMPLE.

Browser verification used the actual normal learner router and WizardShell,
synthetic SQLite `:memory:` learner records and a freshly SELECT-read real LMS
snapshot. No live learner data was copied or modified; the isolated server blocks
external connections and replays recorded LMS rows with their real IDs/dates.
Screenshots/results: `.cache/live-learner-schedules/`. Tests exercise automatic
matching of an additional future delivery on a subsequent read and refresh on
focus, without changing mapping code or writing to LMS.

Changed files: `module_schedules.py`, `schedule_review_candidates.json`,
`test_module_schedules.py`; frontend `useModuleSchedules.ts`,
`ModuleSchedulingPreview.tsx`, `ModuleSchedulingPreview.module.css`,
`realScheduleCandidates.test.tsx`, the home-row wording assertion in
`schedulingPreview.test.tsx`, and the scheduling documentation.
Programme Structure/v24, cohort assignment, assessment/persistence, PCP credits,
Training Plan, KSBs and Compliance Documents are unchanged.

Validation: 29 backend/reader tests passed without migrations or remote test
writes; 329 Programme Suitability frontend tests passed in 34 files. TypeScript,
ESLint and production build passed. The actual normal learner browser test
verified four real provisional bars, local one-per-module selection, focus
refresh, fixed Feb 2027 home cohort, no unselected Earned Value, cleared local
choices on reload, desktop/mobile scrolling and zero write requests. Separate
isolated tests prove a newly returned eligible delivery appears on a subsequent
fetch without editing code or mappings.
