# Step 6B: Programme Reviews in Learning Plan Meetings

The existing Learning Plan GET now reads Reviews ID for native learners whose
Programme Suitability selection is confirmed. It verifies the saved programme
against the confirmed selection and uses the actual saved learner start/end
dates. No dates are inferred from a cohort or module. Existing Aptem and legacy
review flows are preserved.

During Onboarding, the Meetings forecast includes the programme's enabled
placement-applicable templates even if their execution status is Active-only.
Only an in-memory copy's status gate is relaxed for this forecast. Original LMS
templates, current learner status, actual review access and execution eligibility
are unchanged. Other statuses continue using their configured eligibility.
Each template remains one summary row with occurrence count, first/last dates
and the existing OTJ arithmetic. `plannedDates` retains every individual date
for the future detailed Training Plan schedule; that document is not changed.
`planningDuringOnboarding` and `executionEligibleNow` distinguish planning from
present execution eligibility.

The integration reads enabled live templates for exactly that programme, then
reuses `review_instances.review_applies_to_placement`, `learner_is_eligible`,
`resolve_learner_occurrences`, `review_schedule` and `review_otjh.planned_meetings`.
The existing hours gate `template_otjh_hours` is unchanged: applicable templates
not flagged as OTJ contribute zero. Module hours and the existing total formula
are unchanged. Templates are not identified by names; Monthly Coaching is an
ordinary template and no additional coaching system is created.

Skip decisions use the learner's verified `LearnerProfile.pk`, the LMS Review
Engine's learner namespace, not the Enrolment Created_users primary key.
Programme-wide skips and only that learner's skips are read. Cohort/group-specific
templates require verified saved placement IDs; ambiguity produces a blocker.

Each existing Meetings row carries `plannedDates`, `scheduleKind: projected` and
`bookedAppointmentsLoaded: false`. These are forecasts, not booked appointments;
bookings are neither created nor inferred. This step does not read or alter the
appointment system. The existing columns are retained. The individual dates are
available for a future document adapter; no PDF integration is included here.

Meetings remain a dynamic read, so no parallel plan storage, writes, migrations,
refresh duplication or overwrites of customised module plans are required.
The existing LP response adds `meetingsBlockers` with exact source errors. Missing
templates/permissions are never replaced by another programme's templates.

## DATABASE BLOCKERS — ACTION REQUIRED

The original missing SELECT permissions were granted by the user and verified
through the configured `enrolment_modules_reader` connection. Both tables and
all required columns below are readable; no remaining permission blocker was
found. The agent did not apply grants or change LMS records.

* `curriculum.review_templates`: `id`, `programme_id`, `name`, `recurrence_unit`,
  `enabled`, `recurrence_interval`, `occurrence_count`, `applicable_statuses`,
  `applicability`, `expected_otjh`, `counts_towards_otjh`, `deleted_at`.
* `curriculum.review_occurrence_overrides`: `review_id`, `programme_id`, `action`,
  `occurrence_date`, `learner_id`, `deleted_at`.

No additional write privileges or schema changes are needed. If access is later
revoked, the native integration fails closed and the Meetings section explains
the blockers; module rows remain available.

Learners also need their real saved start and end dates for a bounded lifetime
projection. Missing dates are a separate data blocker, not a reason to use cohort
dates. Native programme/profile conflicts similarly require correction outside
this read-only projection.

Verification uses isolated learners across ME/MM/APM/PCP, the production recurrence
engine, scoped skip fixtures, unchanged OTJ arithmetic, zero-hour templates,
eligibility, no writes, repeat reads, source permissions and the existing frontend
Meetings table. Production inspection is metadata-only; no appointments, plans,
statuses, templates or documents were changed.
