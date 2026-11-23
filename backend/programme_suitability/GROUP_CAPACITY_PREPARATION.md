## Step 3: explicit PCP cohort-module confirmation

The PCP learner action now calls the existing allocation service through
`GET/PUT <kind>/<learner_id>/cohort-module-confirmation/`. PUT accepts only
`expectedRevision`, requires the signed-in learner's own session and CSRF, and
checks PCP again inside the allocation transaction. It cannot accept a group,
offering or substitute cohort from the client. GET is read-only; an existing
confirmation reloads from the Enrolment-owned ledger even during an LMS outage.

`Confirm My Cohort Modules` appears only after valid active PCP academic choices.
Pending/failed saves or a different saved selection disable confirmation. The
button displays progress and blocks duplicate clicks. Successful confirmation
shows the permanent group, locks editing in that view and refreshes its schedule
read. Retry safety and permanent seat uniqueness remain in the shared service.
Other-cohort PCP deliveries stay informational. Fixed programmes have no button.

Only the existing capacity ledger receives a write. No existing learner/profile,
Learning Plan, Training Plan, PDF or LMS record is modified. No new model or
migration is needed. No live migration or confirmation has been executed.

Activation still requires the separately approved migration, authoritative
verified membership baseline/identity bridge, complete real home-group module
offerings and resolution of any saved cohort-ID conflict, listed below. Missing
storage/data produces a clear unavailable message and no partial confirmation.
These prerequisites have not been changed or bypassed by this step.

Step 3 tests cover successful six-module PCP confirmation; seats 1–20 in G1 and
21 in G2; capacity changing between preview and click; missing/incomplete G2;
invalid active-pathway choices; stale revisions; ownership/CSRF; request-field
restrictions; missing storage/rosters; unchanged learner records; retries and
refresh; and overlapping PCP confirmations through the same capacity service.
Browser screenshots and checks use disposable SQLite plus explicitly synthetic
LMS fixtures in `.cache/pcp-step3/`. No fixture data represents a live booking.

Files changed for Step 3:

- Backend: `cohort_confirmation.py`, `urls.py`, `group_capacity.py`,
  `test_cohort_confirmation.py`, `capacity_concurrency_fixture.py`, and this
  document. The pre-existing models and prepared migration are reused unchanged.
- Frontend: `modules/CohortModuleConfirmation.tsx`,
  `modules/ProgrammeModulesCard.tsx`, `modules/useModuleSchedules.ts`,
  `__tests__/cohortConfirmation.test.tsx`, and
  `__tests__/realScheduleCandidates.test.tsx`.

Verification: 86 focused backend tests and all 385 Programme Suitability frontend
tests across 37 files passed; TypeScript, full frontend ESLint and build passed.
The actual Step 6 browser test confirmed six synthetic HOME
offerings, exactly one explicit mutation, persisted Group 1 after refresh, no
browser errors and identical before/after source learner state. Separate UI
tests also verify confirmed Group 2 and timeline invalidation scoped to the
correct learner. Existing programme/cohort/academic selection service hashes
remain unchanged. PostgreSQL concurrency still requires an isolated rehearsal
before live activation; current concurrent tests use overlapping SQLite
connections and the unchanged shared locking/seat-constraint implementation.

## Steps 1–2: timeline display and prepared capacity ledger

Steps 1–2 did not include a Confirm button or endpoint; Step 3 above adds that
explicit PCP action only. No programme-confirmation hook, automatic allocation,
Learning Plan write, Training Plan generation or live migration is included.
The existing schedule GET uses
only `preview_home_group()`, which never creates a pool or reserves a seat.

### Timeline

Offerings are grouped by the stable academic module ID, with one sticky module
label and separately identified delivery lanes inside it. Actual offering IDs,
dates, cohort/group IDs and provisional title-mapping labels are retained.
There are no per-offering Select buttons or local selection state.

HOME shows just the existing saved group, or the one eligible group returned by
the read-only capacity preview. An already saved group remains visible when the
capacity tables have not been deployed, protecting existing Marketing Impact
and Planning deliveries. If an unassigned learner's prerequisites cannot be
verified, HOME shows an unavailable explanation; no group is guessed.
For PCP L6 only, OTHER cohorts retain their valid future offerings as information
only. They cannot fill missing HOME schedules or alter the saved home assignment.
ME L4, MM L6 and APM L4 query and render only their assigned HOME cohort and
saved/allocated group. They never receive or display other-cohort offerings,
including when HOME or its group is unavailable. Capacity eligibility for an
unassigned group does not substitute for a fixed-programme group assignment.

PCP academic rows still come from the active pathway's mandatory and selected
optional modules. No pathway, credit, programme-confirmation or cohort-assignment
rule was changed. Scroll, sticky labels, month/year scale and zoom are preserved.

### Prepared service

`group_capacity.allocate(kind, learner_id, expected_revision)` is the shared
confirmation integration point, now reached only by the explicit PCP PUT. It writes only
the Enrolment-owned allocation ledger. The assessment, existing learner account
and profile, capacity pool and ledger all use the same `default` connection;
the account and profile are read and locked, never updated.

The service locks the saved assessment/placement and the existing capacity pool
within one transaction. It validates the pinned academic configuration and PCP
completion rules, exact saved programme/home identity, real LMS groups and the
entire selected home-group module bundle. Only exact normalized titles or
explicitly approved aliases can establish allocation equivalence; tentative
mapping candidates remain informational. Missing, duplicate, undated, deleted,
wrong-programme or other-cohort deliveries cannot authorize an allocation.

An owner-verified roster reserves existing memberships. Capacity is the union of
verified member IDs and committed ledger learner IDs, avoiding double counting.
G1 fills to 20, then real G2 is eligible only with its complete schedule. An
incomplete G1 is not skipped to G2. Existing verified members keep their group.
An idempotent retry returns the original allocation, and placement/academic
conflicts block replacement. Database constraints enforce a unique learner,
unique programme/cohort/group seat and seat numbers 1–20. Failed transactions
consume no places. No read endpoint calls the allocation writer.

### Prepared migration — NOT applied to live databases

`migrations/0012_group_capacity_preparation.py` creates:

- `cohort_group_pools`: unique programme/cohort and a nullable, verified roster.
- `cohort_group_allocations`: immutable service-created learner/group/seat and
  academic-module/delivery snapshots, with uniqueness and capacity constraints.

No pools are automatically created or certified. Applying this migration alone
does not satisfy the roster and schedule prerequisites. The Step 3 action must
retain the same-connection transaction for the entire ledger snapshot. Existing
allocation/account groups are never backfilled on viewing.

### Exact outstanding activation prerequisites

1. Separately approve deployment of the Enrolment-owned migration. Confirm that
   `default` is the Enrolment database and can read/lock its existing account and
   profile schemas. Do not split atomic allocation writes between aliases.
2. Obtain an authoritative read-only existing-membership source/export and a
   verified bridge to Enrolment `Created_users.id`. Current reader permissions
   on group records do not prove roster counts. Do not treat inaccessible
   membership data as empty. Reconcile both LMS and existing Enrolment/profile
   memberships and establish which membership writers can change capacity.
3. Provision an owner-reviewed pool only after reconciliation.
   `verified_members` maps **every actual home-cohort group ID** to its unique
   positive Enrolment learner IDs; a verified empty group is explicitly `[]`.
   `verified_at` and `verification_evidence` must identify the authoritative
   source, identity bridge and reconciled counts. There is no public endpoint
   that can certify these values. Freeze the baseline after cutover; later
   reconciliation must respect the same capacity lock and permanent seats.
4. PCP February 2027 needs real Group 1 offerings for every selected active
   pathway module. For the inspected Chartered selection this is Project
   Planning and Control; Stakeholder Engagement and Communication; Risk, Quality
   and Issue Management; Project Management Office; AI in Project Management;
   Portfolio Management (APMG). Each needs its own real LMS module ID, exact
   programme/cohort/group associations, valid dates and verified academic
   equivalence. Current Group 1 has no scheduled offerings. Do not borrow
   October-cohort deliveries. Actual Group 2 and its full bundle are required
   for learners beyond the first 20; Group 2 currently does not exist there.
5. Resolve the inspected learner's historical cohort-ID mismatch through an
   owner-approved identity correction/bridge while preserving the saved HOME
   assignment. Equal labels do not silently override a conflicting saved ID.
   The prior audit recorded saved ID
   `COHORT-20261110185128015800C4494CA47EEB`, LMS PCP Feb 2027 ID
   `COHORT-2026111713280402271425DC10D3EFF0`, and real Group 1 ID
   `GROUP-20261117133758865624AE0B56C3FF0A`. These are audit evidence to reconcile,
   not an instruction to rewrite the learner's cohort or substitute IDs.
6. Before eventual PostgreSQL rollout, rehearse concurrent confirmations in an
   isolated PostgreSQL database. This environment has no local PostgreSQL
   server; current concurrency verification uses two genuinely overlapping
   SQLite connections against a disposable file, with a lock loser retrying.
   PostgreSQL row locking is implemented via `select_for_update`, but its
   behaviour has not been claimed as tested on a live PostgreSQL database.

No LMS grants, schemas or data were changed. All LMS access still uses the
existing guarded read-only reader. No environment file or production feature
flag changed, and no live learner records or signed documents were written.

### Verification

Isolated tests cover unassigned preview without a reservation, seat 20 / learner
21, missing or incomplete G2, complete-bundle validation, existing G1 retention,
idempotency, database uniqueness, rollback, stale revisions, PCP active-pathway
selection, overlapping confirmations and preservation of learner/plan fields.
Timeline tests cover one academic label, stacked lanes, HOME filtering, PCP-only
OTHER groups, fixed-programme foreign-cohort rejection, provisional labels, no
Select controls, Marketing Impact display, actual dates, unchanged home cohort,
scrolling and zoom.

Actual Step 6 browser verification uses the real authenticated PCP learner route
with disposable in-memory databases and the previously captured LMS SELECT
snapshot. No new live schedules were invented. Browser mutations and external
requests are blocked. Before/after learner state is compared, and desktop/mobile
screenshots are saved under `.cache/timeline-group-step12/`.

Latest results: 75 focused backend tests passed, including the two-connection
concurrency fixture; all 372 Programme Suitability frontend tests passed across
36 files. TypeScript, full frontend ESLint and build passed. Migration-state dry
run and whitespace checks passed. Browser checks passed for the actual Step 6
route, six active Chartered academic rows, four captured offering identities,
one PPC/Portfolio sticky label each, no Select controls, unchanged learner state,
zoom, sticky horizontal scrolling and responsive layout. These results do not
claim a PostgreSQL concurrency rehearsal or a live learner allocation.

Files changed for this step:

- Backend: `group_capacity.py`, `models.py`,
  `migrations/0012_group_capacity_preparation.py`, `module_schedules.py`,
  `test_group_capacity.py`, `capacity_concurrency_fixture.py`,
  `test_module_schedules.py`, and this document.
- Frontend Programme Suitability: `modules/ModuleSchedulingPreview.tsx`,
  `modules/ModuleSchedulingPreview.module.css`, `modules/useModuleSchedules.ts`,
  `__tests__/realScheduleCandidates.test.tsx`,
  `__tests__/schedulingPreview.test.tsx`.

Existing uncommitted work was preserved. Protected-file hashes verify unchanged
programme structure/assignment/confirmation code, routes, academic-choice
persistence, home-cohort assignment and the Programme Modules card. No Confirm
or availability-check component was introduced.
