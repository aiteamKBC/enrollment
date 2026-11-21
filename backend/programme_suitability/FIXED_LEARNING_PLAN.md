# Step 4: fixed academic modules in the existing Learning Plan

ME L4, MM L6 and APM L4 programme confirmation now seeds the learner's existing
Enrolment-owned `Learning_plan` column. The selected/confirmed programme and its
saved module configuration decide the academic bundle, not the recommendation.
If the module configuration was not pinned yet, the existing module pinning
service pins it during the same confirmation transaction. Existing saved fixed
definitions are never refreshed to a newer publication by this operation.

Previously confirmed learners who have no stored source/mirror plan now receive
a **read-only projection** from that same saved academic configuration and
home-cohort/group LMS resolver when opening the existing Learning Plan. They no
longer fall back to the legacy group's incomplete module list. Resolved titles,
offering IDs and dates come from the learner's actual LMS group, matching the
timeline; unscheduled academic modules remain present with scheduling pending.
The UI identifies this as `Confirmed programme modules — not saved yet` rather
than claiming it is a group preset or a persisted plan. GET never backfills or
writes a learner record, pins configuration, allocates a group or issues a PDF.
Stored/manual plans, deliberately cleared plans and preservation/conflict
outcomes are not replaced by this projection.

The reported `vv bb` case was checked against the existing databases using only
verified read-only transactions and the existing public LMS reader. It has a
confirmed ME L4 programme, saved module configuration v11, no source plan and no
profile plan. Its actual home-group offerings are Marketing Impact and Planning
(2027-02-01 to 2027-06-20), Social Media (2027-06-16 to 2027-09-21), and Martech
(2027-09-29 to 2028-01-04). No live data was changed during diagnosis. Regression
coverage reproduces the previous one-module `Impact& Planning` preset and checks
that all three modules replace that unsaved fallback, match timeline titles and
dates, and execute no INSERT/UPDATE/DELETE queries.

The standard `select` and `continue` confirmation paths call the seeder only
after the existing programme assignment succeeds. Confirmation retries also
use it; GET and assessment submission alone never create a plan. The assessment
and learner row are locked on the same Enrolment connection. A plan-write failure
rolls back the confirmation. Only the plan column is updated; no full learner
save, status advancement, profile/delivery mirror rebuild or document hook runs.

The existing source plan JSON and Learning Plan API/serializer remain canonical.
New entries use the usual `moduleId`, `moduleTitle`, programme/group, hours and
date fields, plus stable `academicModuleId`, `assignmentSource`, configuration
version and scheduling metadata. No separate Learning Plan models, schema,
migration, endpoints or capacity implementation were introduced.

The existing explicit Learning Plan save also accepts the verified historical
preview's own home-module references when they have not been copied into the
legacy catalogue. It rebuilds the accepted rows through the same resolver and
retains their verified snapshots instead of reintroducing the legacy title/date.
Unknown/unverified offering IDs are still refused. This is the existing user-
initiated save workflow, not a background write on GET.

Scheduling uses only a verified lookup of the saved home cohort and existing
profile/account group, or an already persisted allocation. No group is allocated
or changed here. Offering reads are restricted to that cohort and then rechecked
against both cohort and group IDs. Only a unique exact/normalized title or an
already confirmed alias can attach a real LMS offering ID and dates. Ambiguous,
missing, foreign-cohort, foreign-group and invalid-date offers are never used.
No external database writes, permission changes or production settings changes
are involved.

If scheduling cannot be verified, every fixed academic module is still saved
with `schedulingStatus: pending`, blank dates and no LMS offering reference.
Its `academic:<programme>:<academic-id>` key is a local academic reference, not
an invented LMS ID. Unknown groups remain blank. Available LMS session/week
counts are retained as distinct metadata; no session/week count is converted
into OTJ hours. Module hours now come only from the exact matched offering's
`curriculum.modules.total_otjh`. The existing read-only reader includes this
optional numeric column only when its current role already has access. Missing
permissions or an unsupported/missing column leave scheduling reads operational
and module hours explicitly pending, rather than inventing an hours value.
Unknown/invalid/negative hours use the existing numeric zero plus
`hoursPending: true`; the UI displays `Pending` and marks partial totals. A real
LMS zero remains a known zero. No grants or schemas are changed.

The `vv bb` home-group module-hour values were verified through the current LMS
read-only role: Marketing Impact and Planning 138h, Social Media 103h, Martech
105.75h (105h 45m), total 346.75h (346h 45m). Confirmed-plan creation and the
historical read-only preview share this same resolver. Fractional hours are
preserved, and the existing hour/minute formatter displays them in the modal.

The existing Learning Plan serializer preserves these saved academic snapshots
rather than substituting dates from an unrelated programme-wide catalogue or
inheriting additional group modules. The existing admin modal displays a saved
academic plan even when group/cohort scheduling is pending, with a
`Schedule pending` badge and the normal blank date display. Existing manual
plan/catalogue editing behavior is retained.

Any existing source plan, legacy CSV-derived plan or populated profile plan is
preserved. An explicitly stored `[]` is treated as an intentionally cleared
plan, matching the existing storage contract; it is not silently repopulated.
The confirmation response's existing `review` object records
`fixedLearningPlan.status = EXISTING_PLAN_PRESERVED` for that conflict. A later
administrator programme change is similarly preserved and reported as
`PLACEMENT_CONFLICT`. A small creation/preservation outcome on the assessment
prevents retries from restoring modules that someone subsequently edited or
removed. It does not store a competing copy of the plan.

PCP confirmation, its button, capacity models/services, timetable, programme
structure, cohort assignment and all PDF/document issuance paths are unchanged.
Step 5 PCP Learning Plan integration and later activities/PDF steps are not
implemented. No live learners or databases were changed during verification.

Files changed:

- `enrollment_service/lms_readonly.py`
- `enrollment_service/test_lms_readonly.py`
- `programme_suitability/fixed_learning_plan.py`
- `programme_suitability/views.py`
- `programme_suitability/test_fixed_learning_plan.py`
- `learner_api/learning_plan.py`
- `frontend/src/api/learningPlan.ts`
- `frontend/src/pages/users/components/LearningPlanModal.tsx`
- `frontend/src/pages/users/components/__tests__/learningPlanPlacement.test.tsx`
- This document.

Isolated verification covers the actual programme confirmation endpoints,
existing Learning Plan GET/serialization, all three fixed programmes, saved
configuration version retention, alternative confirmed programme, retry/edit
preservation, missing/foreign scheduling, existing allocation reads without
capacity consumption, pending rendering and rollback/status/document protection.

Results: 133 focused backend tests passed. All 395 frontend tests across the
Programme Suitability directory and existing Learning Plan modal tests passed
(38 files). TypeScript, full frontend ESLint, frontend build and whitespace
checks passed. All database tests used isolated SQLite fixtures with external
connections blocked; no live migrations or learner updates were performed.
