Step 6 cohort display and mapping review (Phase 3)
================================================

Scope and current state
-----------------------

The read-only check on 13 November 2026 found apprenticeship publication v16
still has no canonical ``programmeStructure`` and no custom ``intakeCalendar``.
The automatic-assignment, cohort-learner and publishing flags are absent from
the loaded backend/process environment, so settings.py's default-false rollout
safeguards apply. This describes this local runtime, not other deployments.

No flag, environment file, database row, schema, publication or learner pin was
changed. This phase does not query LMS; its mapping review uses Phase 2 evidence.

The existing assignment algorithm is unchanged. It uses the confirmed selected
programme and UK-local ``selection_confirmed_at`` (falling back to
``confirmed_at``). It only assigns when the existing feature flag permits it and
there is no persisted cohort ID. Existing assignments must not be overwritten.
The existing default intake boundaries are Feb: Oct 15-Feb 15, Jun: Feb 16-Jun 15,
and Oct: Jun 16-Oct 14. December targets February of the following year. The
October 14/15 boundary is deliberate existing code; this phase does not revise
it to match a general description of "the 15th". Existing configured assignment
periods and previous-eligible-cohort fallback remain authoritative.

Additional activation gates verified after Phase 3
--------------------------------------------------

Automatic assignment requires an existing unambiguous LearnerProfile. The
programme-confirmation service deliberately does not create profiles. Enabling
its flag alone cannot assign a cohort to an application without a profile.
An existing APPLIED programme synchronization is idempotent: confirmation
retries do not backfill missing assignments. Do not reset confirmation or remove
the APPLIED audit entry to force it to run again.

A narrow safety correction now also excludes existing nonblank legacy cohort
names on either Created_users or LearnerProfile from automatic assignment, even
when cohort_id is missing. Such text-only placements need an explicit reviewed
identity resolution, not replacement with a date-derived intake.

Enrolment Details currently reads board.programme.cohort from
Created_users.Cohort (learner_api/mappers.py). Automatic assignment persists the
stable cohort on LearnerProfile. These are separate sources: a profile assignment
does not guarantee the board's legacy text field changes. Before activation,
approve and verify consistent read-side cohort metadata for Enrolment Details,
or an explicitly approved local reconciliation of its legacy text. Do not
silently copy names or update existing records as part of displaying Step 6.

For existing learners, preserving wizard/module pins takes precedence over
making a label appear. If their pinned publication lacks canonical structure,
new publication alone cannot resolve it. Keep the pending state. A possible
follow-up is a separately approved, versioned cohort-display publication binding
that leaves the wizard/module pins unchanged; this would need its own data-model,
schema and authorization review before implementation or migration. No such
binding or automatic fallback has been added here.

Additive display contract
-------------------------

The existing learner modules GET response now includes ``homeCohort``. Its
resolution is independent of enabling cohort-scoped module replacement:

* Confirmed programme is the existing Assessment.selected_programme, with
  selection_confirmed_at required. The recommendation is not a display source.
* Persisted home cohort is LearnerProfile.cohort_id with programme_id.
* Canonical/curriculum programme identifiers are translated using the four
  already verified identities. Conflicting or duplicate bindings fail closed.
* ``learner_published_layout`` keeps its existing precedence: administrator
  override, submitted pin, checkpoint pin, modules pin, then current only if no
  pin exists. A missing pinned publication is never replaced with the latest.
* The exact cohort ID must exist in that layout's actual canonical structure
  under the confirmed/persisted programme. The name is returned verbatim.
  Legacy compatibility-generated February 2027 cohorts are never used here.
* Missing assignment, publication, name or matching identity produces pending
  metadata. Metadata failures do not change or prevent existing module controls.
* Step 6 adds the resolved name or "Cohort pending" beside the selected programme
  title in Programme Modules, not beside a different recommended programme.
  Shared administrator previews retain their previous layout.

The feature flag controlling cohort-specific module replacement remains off
unless independently activated. Its lookup now accepts verified curriculum IDs
without changing saved profile IDs. The fixed module tables, PCP active pathway,
mandatory/optional choices, six-credit rules, saving and history are unchanged.

Safe activation steps requiring an administrator
------------------------------------------------

No Enrolment database migration is required for this label/identifier adapter.
There is no LMS schema requirement or permission change for this phase.

1. Prepare/reconcile the shared Programme Structure draft against the latest
   published base and revision, using the existing Admin workflow. Do not amend
   v16 or any other historical publication in place.
2. Review the four explicit curriculum programme IDs and all real cohort IDs.
   Populate canonical cohort names verbatim from Enrolment administration.
   Ensure each cohort belongs to its programme, has valid published status and
   assignment periods/year where needed, and has compatible module/KSB mappings.
   Do not insert a fabricated February cohort or use LMS names as home labels.
   Review PCP routes, selectable children, optional counts and credits unchanged.
3. Run the existing validation/preflight and inspect changes, including any
   Enrolment-owned curriculum cohort-status synchronization publication performs.
   Verify no path points to or writes the external LMS connection.
4. Only after approval, an operator may enable
   PROGRAMME_STRUCTURE_PUBLISHING_ENABLED in the controlled backend runtime and
   restart it. An authorized administrator then explicitly publishes through
   the existing append-only publisher. This creates a new version; its number
   must come from the publisher, not be assumed to be v17.
5. Verify the new publication and existing historical pins. New publication does
   not repair older pinned snapshots lacking a structure: those learners remain
   "Cohort pending" until an explicitly approved administrator action resolves
   their relevant publication/assignment. Do not bulk repin, rescore, or backfill.
6. Enable PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED only after verifying intended
   cohort-specific module/KSB behaviour, historical versions, ID translation,
   real assignments, missing-cohort errors and rollback in a non-production
   rehearsal. It is not needed just to display safely resolved home metadata.
7. Enable PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED separately only
   after testing real published periods, December/year boundaries and the
   existing confirmation flow. It must only handle eligible future confirmation
   events without overwriting existing assignments. No backfill is authorized.

The exact Admin location is Programme Structure in the admin sidebar, route
/users/programme-structure. Review the programme/cohorts in Structure Editor,
Save Changes, then use Draft Changes -> Publish Preview -> Publish -> Confirm
Publish. Reconcile a stale shared draft through the existing workflow first;
review all proposed changes. Save/reconcile/publish actions change Enrolment
data and require explicit authorization; they are not inspection actions.

Draft module mapping report
---------------------------

``module_mapping_review.json`` is an approval-ready report, not runtime mapping
configuration. It contains all 28 pathway-specific academic module definitions
(23 distinct programme/module keys), stable IDs, classification, candidate LMS
offering IDs with separate cohort/group/date records, ambiguity explanations,
future-candidate flags and historical definition versions where observed.

There are 0 verified, 8 candidate, 16 ambiguous and 4 unavailable definitions.
No entry is approved. APM's two fixed academic modules have no verified
counterpart. Portfolio/APMG, EVM qualification variants, PPC components, PMI PMO
versus PMO, stakeholder/reporting and AI management/control need explicit review.
Future flags are dated to the Phase 2 UK audit date; they are not live choices.

Future PCP eligibility must use only
``modules_selection.modulesByPathway[modules_selection.pathway]`` against its
saved modules_configuration/version. Inactive pathway arrays are not combined.
No mappings, schedule choices or new persistence tables are applied in Phase 3.
A future Enrolment-owned mapping/selection schema requires separate review and
approval before any migration; the external LMS remains strictly read-only.

Verification
------------

Pure checks require no Django setup or environment/database access:

  .venv/Scripts/python.exe -m unittest programme_suitability.test_home_cohort enrollment_service.test_lms_readonly

The Phase 3 authenticated HTTP/calendar/assignment regressions additionally ran
with the existing SQLite-only SuitabilityRunner and the full service URLconf.
An ignored compatibility runner in .cache/phase3_test_runner.py inspects SQLite
fixture columns instead of PostgreSQL pg_attribute and adds Learner_kind to the
disposable publication fixtures to match the deployed schema. Production lookup,
pin precedence, assessment/assignment logic and HTTP authorization are exercised
unchanged. Networking is blocked by the existing isolated runner; no PostgreSQL
test databases or production migrations are used. This compatibility harness is
needed because the older repository fixtures predate the publisher's kind column.

The scheduling board and Training Plan integration are not implemented. Approve
the academic mappings and publication/rollout prerequisites before starting them.
