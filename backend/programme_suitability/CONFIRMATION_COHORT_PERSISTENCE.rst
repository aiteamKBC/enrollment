Programme confirmation and real cohort persistence
=================================================

Prepared locally on 13 November 2026; NOT activated. This phase supersedes the
profile prerequisite described in the Phase 3/4 audit documents. Those documents
describe the earlier implementation, before account-only persistence was added.

Existing transaction and storage
-------------------------------

Step 6's existing assessment confirmation calls
``sync_confirmed_programme_to_learner`` in ``programme_assignment.py`` inside
the same assessment transaction. It already saves the confirmed programme name
to ``enrolment.Created_users.Programme``, mirrors an existing compatible
``Learner.learners`` profile, and records the stable curriculum programme ID in
``Assessment.review.programmeAssignment``. The recommendation is not used as
the confirmed assignment. The existing catalogue validation remains unchanged.

For a future eligible first confirmation only, that same transaction now:

* Resolves the real published cohort for the confirmed programme and the UK-local
  ``selection_confirmed_at`` date (existing ``confirmed_at`` fallback retained).
* Saves ``cohortId``, ``cohortName``, ``cohortPublicationVersion`` and
  ``cohortAssignmentVersion: 1`` alongside the existing programme ID and cohort
  outcome in ``review.programmeAssignment`` and the existing history event.
* Sets only the previously blank account ``Cohort`` display label to the
  canonical published cohort name. The stable ID is authoritative, not text.
* Mirrors the same stable ID/name to an existing compatible linked profile,
  using the existing safe legacy-profile lookup/linking mechanism if needed.

No separate profile is required, created or initialized. The existing JSON
sync-outcome field already stores programme/cohort outcomes, so no schema,
column or migration is introduced. Account/profile and audit writes roll back
together if confirmation fails. An APPLIED outcome is returned on retry without
replaying assignment or undoing a later staff change.

Assignment prerequisites and failure behavior
--------------------------------------------

The existing automatic-assignment flag must be enabled in an explicitly approved
rollout. Its settings/defaults/environment are unchanged in this task. An
existing cohort ID, nonblank account/profile cohort text, or reconciliation
request excludes automatic assignment. Old APPLIED records are not upgraded.

The assignment consumer reads current and relevant pinned canonical snapshots
and reuses the existing automatic resolver. It verifies stable identity,
programme association, eligible published status and the exact assignment
period in both snapshots. It accepts only these UK-date windows:

* February: previous October 16 through February 15 inclusive.
* June: February 16 through June 15 inclusive.
* October: June 16 through October 15 inclusive.

The protected shared calendar/resolver are unchanged. The consumer rejects a
conflicting published period and rejects a previous-cohort fallback as a
substitute for the actual target intake. Cohort duration start/end dates are not
treated as confirmation-date windows. Modules, KSBs and PCP selection logic are
untouched. The existing resolver's configuration and real curriculum-row checks
still apply; merely having a cohort label in a draft is insufficient.

Missing/incompatible data leaves the programme confirmation valid and the cohort
pending; it never invents a cohort. Since APPLIED remains idempotent, a later
retry does not backfill a pending outcome. Such a learner requires a separately
approved reconciliation decision.

Shared display
--------------

Step 6's existing ``homeCohort`` field and the Admin board cohort lookup use
``read_home_cohort``. Existing stable profile assignments retain precedence.
New account-only assignments can resolve their recorded stable IDs directly;
opening a page never creates a profile or writes data.

For new recorded assignments, use the saved cohort publication reference. If an
explicit wizard/module pin conflicts, return pending instead of overriding it.
Where there is no pin, read the recorded publication even if the default has
subsequently advanced; do not create/change a wizard or module pin. If account
programme/cohort text has been changed by staff, do not resurrect old audit
data over it. Historical records without the new marker cannot acquire a
stable cohort identity through this read path.

Admin's shared resolver remains behind the existing cohort-learner flag. Its
fallback remains the account's existing cohort label. A successful future
assignment also saves that label, so account information receives the same
canonical cohort name. No frontend redesign or module-selection changes were
needed for this phase.

Read-only publication audit and activation blockers
--------------------------------------------------

The local read-only audit found apprenticeship publication v16 has no canonical
Programme Structure. The saved shared Admin draft has base version 23; this
differs from the active apprenticeship version and needs administrator review.
No automatic reconciliation or publication was attempted.

The saved draft contains these existing programme/cohort associations:

* ME_L4 / PROG-ME-L4: Feb 2027
  ``COHORT-20260917092025244736D2820D958B44``; June 2027
  ``COHORT-20261110195722888955DB66B4D8CC1C``.
* MM_L6 / PROG-MM-L6: Feb 2027
  ``COHORT-20261110185121808654D6E19983AD79``.
* APM_L4 / PROG-20260907141619067932: Feb 2027
  ``COHORT-20261110185125368519EE89CF7CF8DC``.
* PCP_L6 / PROG-PCP-L6: Feb 2027
  ``COHORT-20261110185128015800C4494CA47EEB``.

All five are planned and have null duration start/end dates. Their explicit
assignment periods match the agreed February/June cutoffs. No October cohort
is present in this draft. Do not invent one or infer its duration dates.

Subject to separate approval: review the saved draft at Admin > Programme
Structure, resolve its publication-base discrepancy through the existing
administrator workflow, and use its Publish / Publish Preview / Confirm Publish
controls to validate and publish a NEW compatible version. Publication must
preserve v16 and all historical pins. This task does not authorize changes to
Programme Structure, cohort data, modules, KSBs or its publisher.

The unchanged rollout prerequisites remain: publishing must be explicitly
enabled for publication; canonical programme/cohort data and real curriculum
rows must validate; cohort-learner integration must be reviewed/enabled before
automatic assignment is activated for new confirmations. Publishing alone does
not change existing learners or their pins. Do not enable assignment while
relevant snapshots remain incompatible or missing required target cohorts.

Safety and verification
-----------------------

All rollout flags remain disabled. Learner 1601 and other live learner records,
publications, pins, credentials and environment files were not changed. No live
migrations or LMS queries/writes were run. Programme Structure source, calendar,
publisher and other protected files match the pre-task byte hashes. Training
Plan, compliance, modules and KSB functionality were not changed.

The isolated backend run passed 92 tests, using settings_test and in-memory
SQLite with networking blocked. It covers translated IDs, confirmed versus
recommended programme, every cutoff, matching/conflicting/missing publications,
profile and account-only persistence, shared display, atomic rollback,
idempotency and legacy preservation. Existing frontend module/board regressions
passed 49 tests in four files. The local compatibility test runner supplies
deployed publication-schema fixture columns without editing production code.

Explicit approval is required before any flag/environment change, publication,
Programme Structure data change, historical reconciliation or learner update.
No scheduling board or LMS mapping approval is included.
