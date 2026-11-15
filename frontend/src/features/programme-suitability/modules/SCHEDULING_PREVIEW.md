# Step 6 module timeline and local design preview

## Current behaviour: automatic live learner schedules

Normal learner Step 6 displays dynamically read future LMS offerings without an
administrator approval dependency or a special query URL. Exact/safely normalized
titles and documented possible-title candidates are matched within the confirmed
programme only. Tentative bars say **Possible LMS match**; their local choices
say **Provisional choice - not saved or booked**. No booking or session choice is
persisted. Fixed programmes remain confined to the saved home cohort and have
no selectable delivery controls.

The timeline fetches on open and selection/pathway changes, coalesces rapid edits,
uses a private 15-second per-component cache, and bypasses it when the browser
returns to focus. No polling is used. See
`backend/programme_suitability/AUTOMATIC_MODULE_SCHEDULES.md` for matching rules,
real Chartered dates, unavailable reasons and current verification evidence.

The notes below describe earlier preview/review phases; their former approval
restriction no longer applies to automatic candidate display.

## Real LMS candidate review

Staff with Enrolment access can now open the actual wizard at
`/users/<learnerId>/wizard/programme-suitability?moduleScheduleReview=1`.
This calls the authenticated Django GET-only `module-schedules/` endpoint and
uses actual LMS dates/IDs through the existing isolated SELECT-only reader.
The backend enforces staff review permission; a learner changing the URL is
refused. Do not use `moduleSchedulePreview=1` for real-data review; the real
review option takes precedence if both parameters are present.

Normal learners receive only explicit approvals. No mappings are currently
approved, so their rows show the approval reason without unverified dates.
Review shows exact academic and candidate LMS titles together, with
`UNVERIFIED LMS MATCH — REVIEW ONLY` and no Select control. A future explicit
approval can permit a local component-state choice, never a scheduling save.
The axis uses the returned date range and includes Jump to next available session.

See `backend/programme_suitability/CHARTERED_LMS_SCHEDULE_REVIEW.md` for the six
Chartered decisions, four actual 2028 offering IDs/dates, permissions, pagination
and verification evidence. Read failures have a separate Retry action and do
not alter academic module choices or completion rules.

The normal learner Step 6 now automatically shows the existing timeline below
the academic module-selection area after programme confirmation. No query
parameter, navigation or refresh is required. It reads the same live React
selection as the existing academic editor, so optional toggles and pathway
changes immediately add/remove the corresponding academic rows. The home row
uses only the existing persisted home-cohort metadata.

All LMS mappings remain UNAPPROVED. Normal rendering has no offering bars,
session-selection controls, synthetic dates or scheduling writes: each academic
module shows `Schedule not available yet`. ME/MM/APM retain fixed modules and
the same safe home-cohort-only empty schedule. The existing academic save queue
and validation are unchanged; rendering the timeline does not enqueue saves.

The clearly labelled synthetic offering preview remains separately opt-in for
local development only. No backend endpoint, LMS request, persistence,
localStorage or sessionStorage is used for sample session choices.

1. Start the existing React development server with `npm run dev` from `frontend`.
2. Open a learner's existing Step 6 page, after their programme is confirmed.
3. Append `?moduleSchedulePreview=1` to the URL, for example:
   `http://localhost:3000/learner/onboarding/programme-suitability?moduleSchedulePreview=1`
   (use the port printed by your existing dev server).
4. Remove that query parameter to return to normal module selection with the
   automatically displayed safe, empty-offering timeline beneath it.

The opt-in only works in Vite development mode on localhost, 127.0.0.1 or ::1.
Production builds include the safe timeline, while synthetic offering generation
and selection remain guarded by `import.meta.env.DEV`. No backend feature flag
or step navigation is changed.

For this review session a separate localhost-only Vite server also serves an
ignored, synthetic provider fixture (no sign-in or backend needed):

`http://127.0.0.1:3018/.cache/module-scheduling-preview.html?moduleSchedulePreview=1&programme=PCP_L6`

Change only `programme=ME_L4`, `MM_L6` or `APM_L4` to inspect fixed programmes;
`pathway=operational`, `strategic` or `chartered` selects synthetic test context.
These URL parameters belong to the ignored fixture page, not the real learner
workflow. They never alter a learner or programme/cohort assignment.

Desktop (1450px) and mobile (390px) captures are in
`.cache/scheduling-timeline/PCP_L6-1450.png`, `PCP_L6-390.png`, `ME_L4-1450.png`
and `ME_L4-390.png`. They show the actual module card and enrolment hero inside
a synthetic visual wrapper, not screenshots of a real learner account.

The programme title and first timeline row use the existing confirmed programme and resolved
`homeCohort` response; missing/unresolved assignments remain `Cohort pending`.
There is no cohort input. ME/MM/APM keep their fixed academic module names and
session counts and show `Schedule not available yet`, because no mappings are
approved. There are no invented fixed-programme dates or selectors.

PCP uses the existing active-path helper and saved module configuration: mandatory
modules plus only the confirmed optional IDs from
`modulesByPathway[selection.pathway]`. Inactive pathways and group headings are
excluded. This mode does not offer academic module/pathway editing.

The existing preview is now a horizontal Gantt-style timeline with a sticky left
label column, aligned year/month header and a sticky first home-cohort row.
The home row always exists, including pending assignments, and contains no
fabricated offering bars. Its name comes exclusively from the existing persisted
home-cohort response, never a delivery offering.

PCP synthetic delivery A/B rows contain separate G1/G2 offering rectangles. The
last academic module deliberately has one sample offering to illustrate the
single-offering state. Dates span the following UK calendar year and beyond;
bar offsets and widths use their exact start/end days, with inclusive end dates.
Only samples starting strictly after the current Europe/London date are shown.
Every sample is labelled as not live LMS data. A top-right Select button updates
one component-state choice per academic module; selecting another delivery
visually deselects the first without changing the home assignment. Reset, reload,
leaving the page or changing the academic context discards sample choices.

Use the horizontal scrollbar, touch scrolling, keyboard on the focused timeline,
or earlier/later arrow buttons to browse. The home row and month header also
stay visible during vertical scrolling inside the board. Mobile screenshots show
a slight horizontal scroll so the first sample bar is visible.

The explicit sample preview card bypasses the existing module-save-queue component entirely.
Normal programme confirmation, cohort assignment, academic save/credit rules and
Next/Back remain unchanged. Live eligibility/date validation and preserving
previously saved schedules are future integration work; this preview implements
no real scheduling selections or persistence.

Normal Step 6 integration verification:

- 48 focused tests passed across five files, including normal-wizard live-state
  optional changes, active-path isolation, saved remount and production empty
  schedules, plus existing module/pathway/credit rules.
- Browser verification used the real React router and WizardShell at
  `/learner/onboarding/programme-suitability`, without a preview query.
- Synthetic learner only; both Django aliases were SQLite `:memory:`, dotenv
  was not loaded, external connections were blocked and no live aliases existed.
- Real existing module API/saving workflow was exercised only in that disposable
  database. Five Chartered mandatory modules plus Portfolio appeared; toggling
  Portfolio updated immediately; unselected Earned Value was absent; pathway
  switching and reload retained the correct selections and fixed home cohort.
- Rendering made no module-save request. No scheduling requests or real LMS
  offerings were introduced. Desktop/mobile horizontal scrolling passed.
- TypeScript, ESLint and production build passed.
- Actual-wizard screenshots/results: `.cache/normal-step6-timeline/`.
- Live-connected backend, learners, pins, publications and feature flags were
  untouched. Test services were stopped after verification.

Files for this change:

- `ProgrammeModulesCard.tsx`: development-only lazy preview integration.
- `schedulingPreviewMode.ts`: explicit localhost/query/build guard.
- `ModuleSchedulingPreview.tsx`: sticky-home timeline and local session state.
- `schedulingTimeline.ts`: presentation-only sample dates and day-based positioning.
- `ModuleSchedulingPreview.module.css`: responsive purple timetable styling.
- `../__tests__/schedulingPreview.test.tsx`: scope, isolation and selection tests.
- `SCHEDULING_PREVIEW.md`: usage, scope and review instructions.

Verification for the previous card preview (historical):

- Programme Suitability/module regression suites: 32 files, 318 tests passed,
  including seven new preview tests.
- Browser: four programmes at 1450px and 390px passed; G1/G2 selection, keyboard
  navigation, fixed home label, pending label, preview-off normal table, no
  horizontal overflow and no API/write requests verified. External fonts were
  blocked during the isolated browser run.
- TypeScript, ESLint and production build passed. Production assets were checked
  to exclude the preview component and synthetic session text.
- Complete frontend suite: 147 files passed / 7 failed; 1396 tests passed /
  88 failed. Failures occurred in login, apprenticeship application, LMS
  introduction, compliance document creation, learner welcome, learner header
  dates and Wizard Builder tests. These areas were not changed for this task;
  the complete suite is therefore not green. Full output is preserved in
  `frontend/.cache/scheduling-full-tests.log`. No unrelated fixes were attempted.

Earlier standalone timeline redesign verification (historical):

- Focused scheduling, fixed-module and PCP rule tests: 44 passed (four files),
  including ten preview tests. UK midnight, future-only filtering, single-offering
  counts, date geometry, home-row identity and no-save behaviour are covered.
- Browser: all four programmes at desktop 1450px and mobile 390px passed;
  horizontal/vertical sticky-home positioning, Select replacement, keyboard
  interaction, unchanged synthetic home assignment, pending state and normal
  preview-off rendering verified. API/write requests were blocked.
- Desktop/mobile captures and browser results: `.cache/scheduling-timeline/`.
- TypeScript, ESLint and production build passed. The development-only component
  remains excluded from production assets. No new backend or live LMS access.
- The full frontend suite was not repeated for this visual-only redesign; its
  previously reported unrelated failures above remain outside this task.
