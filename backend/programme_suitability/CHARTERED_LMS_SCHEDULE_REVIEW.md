# Chartered module schedules — owner approval required

**Historical report:** automatic learner-facing candidate visibility now
supersedes this display-approval gate. See `AUTOMATIC_MODULE_SCHEDULES.md`.
The dated offering evidence below remains historical evidence, not a booking
approval or a prerequisite to displaying real candidate schedules.

All mappings remain **UNAPPROVED**. Real LMS schedules were refreshed on
14 November 2026 through `enrollment_service.lms_readonly.get_module_offerings`.
The Phase 2 audit, Phase 8 proposal and exact-title review evidence were reused;
no fuzzy matching or academic substitutions were introduced.

PCP scope: `PROG-PCP-L6`. The reader returned **29** valid delivery records;
**15** begin strictly after the UK review date. For the active Chartered selection
of five mandatory modules plus Portfolio, **4 distinct future offerings** match
the explicit review title list, covering **2 academic modules**. All four are
read-only review candidates; **0 approved/selectable learner offerings** exist.

| Enrolment academic module (Chartered) | Exact LMS candidate | Future rectangles | Owner decision / missing-schedule reason |
|---|---|---:|---|
| `pmo-planning-control` — Project Planning and Control | `PPC` | 2 | Confirm that PPC satisfies this Chartered PMO component independently of operational `planning-control` and its APMG qualification. |
| `pmo-stakeholders` — Stakeholder Engagement and Communication | `Stakeholder Management & Reporting` | 0 | Existing candidate started 30 Sep 2026; no eligible future delivery. Full academic equivalence also requires review. |
| `pmo-risk-quality` — Risk, Quality and Issue Management | None verified | 0 | Provide a verified equivalent and future delivery. Risk Management alone must not be substituted. |
| `pmo-office` — Project Management Office | `Ray-Project Management Office(PMO)` | 0 | Existing candidate started 5 Nov 2026. No eligible future delivery; confirm this component separately from PMI PMO. |
| `ai-project-management` — AI in Project Management | None verified | 0 | Provide a verified equivalent and future delivery. Do not substitute AI in Project Control. |
| `portfolio` — Portfolio Management (APMG), selected optional | `Portfolio Management` | 2 | Confirm the APMG academic/qualification equivalence. |

Earned Value Management is not selected in this example and is excluded.

The four distinct delivery records are:

| Academic ID | LMS offering ID | Group | Recorded start → end |
|---|---|---|---|
| `pmo-planning-control` | `MOD-20260912132052479676` | G1 | 10 Feb 2028 → 31 May 2028 |
| `pmo-planning-control` | `MOD-2026100111101527614080126D27D623` | G2 | 10 Feb 2028 → 31 May 2028 |
| `portfolio` | `MOD-20260912125821447490` | G1 | 7 Jun 2028 → 20 Sep 2028 |
| `portfolio` | `MOD-202610011111188096444893DEB489B6` | G2 | 8 Jun 2028 → 27 Sep 2028 |

All four use delivery cohort **October 2026**,
`COHORT-20260912111441822802`. G1 references
`GROUP-20260912111638967115`; G2 references
`GROUP-2026100110425434687520181B1A2448`.
The learner's saved **February 2027 home cohort remains unchanged**. Delivery
cohort membership is schedule metadata, never a reassignment operation.

## Review access

Sign in as admissions staff with Enrolment access or an administrator. Open:

`http://localhost:3000/users/<learnerId>/wizard/programme-suitability?moduleScheduleReview=1`

Use your existing frontend port and actual learner ID. No special environment
flag is required. The backend GET endpoint is:

`/enrolment_api/programme-suitability/apprenticeship/<learnerId>/module-schedules/?review=1`

Staff review is checked by the backend regardless of the frontend query or the
legacy development auth bypass. Learners receive 403 for review access and see
no unapproved offering data in normal mode. No approval or session-saving API
exists. The endpoint reads the saved academic snapshot without invoking the
configuration pinning/refresh writer. The normal existing academic save workflow
is unchanged; current UI choices are projected via a validated GET query.

## Data and selection boundary

- All remote reads go through the existing dedicated, parameterised SELECT-only
  LMS reader; React calls only the authenticated Django endpoint.
- Reads page to exhaustion (200 per request); there is no silent total cap.
  Duplicate pages/concurrent inconsistencies fail closed. Pagination is live,
  not a cross-page snapshot; the current PCP data fits in one page.
- Exact programme/cohort joins, reader deletion/archival/date filters, UK future
  cutoff and additional test-label exclusions apply. G1/G2 IDs remain separate.
- PCP reads all eligible PCP delivery cohorts. Fixed programmes read only the
  saved home cohort; unknown home assignments cannot borrow another cohort.
- `schedule_review_candidates.json` contains staff-review title candidates only.
  It does not approve any academic identity. `APPROVED_BINDINGS` is empty and is
  separate from all unapproved review documents. No approvals were applied.
- Unverified bars show both academic and exact LMS titles, actual dates and
  `UNVERIFIED LMS MATCH — REVIEW ONLY`, with no Select control. Only an explicitly
  approved mapping can enable a local UI choice; no scheduling persistence exists.
- The visible date axis follows returned offerings (February–September 2028 for
  this selection), rather than making the reviewer scroll through empty 2027.
  A Jump control scrolls to the first available offering. Missing modules retain
  academic rows and specific reasons.

## Verification evidence

Actual staff and learner React routes were tested against isolated SQLite
`:memory:` aliases with synthetic learner data. The LMS reader was replaced only
inside that isolated test server with the freshly captured real SELECT results;
candidate dates and IDs were not fabricated. Live learner/LMS writes were blocked.
The test verified six modules, four real candidate bars, home February 2027,
distinct groups, 2028 date range, learner 403, zero write requests and desktop/mobile
scrolling. Screenshots and machine-readable evidence: `.cache/real-lms-review/`.

Validation: 10 schedule service/security tests and 17 existing isolated reader
tests passed without migrations, database writes or network access. The focused
Programme Suitability frontend group passed 328 tests in 34 files. TypeScript,
ESLint and the production build passed. The unrelated full frontend suite was
not repeated; its previously reported failures remain outside this task.

Changed backend files: `module_schedules.py`, `urls.py`,
`schedule_review_candidates.json`, `test_module_schedules.py`, this report.
Changed frontend files: `ProgrammeModulesCard.tsx`, `ModuleSchedulingPreview.tsx`,
`ModuleSchedulingPreview.module.css`, `schedulingTimeline.ts`,
`schedulingPreviewMode.ts`, `useModuleSchedules.ts`,
`realScheduleCandidates.test.tsx`, the academic persistence test's schedule GET
fixture, and `SCHEDULING_PREVIEW.md`. Existing unrelated work was preserved.
