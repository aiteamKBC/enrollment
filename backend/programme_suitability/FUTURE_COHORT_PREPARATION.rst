Phase 4: future-confirmation cohort preparation only
===================================================

No activation or live data changes are performed by this task. All Programme
Structure implementation, definitions, periods, shared calendar defaults,
publication logic, snapshots and rollout settings remain exactly as they were at
the start of Phase 4, including prior uncommitted work.

Assignment-only corrections
---------------------------

``programme_assignment.automatic_cohort_window`` now treats October 15 as October
when calculating the default future assignment intake. October 16 onward still
targets February of the next year. Explicit published calendars are not changed
or reinterpreted; the shared Programme Structure ``intake_calendar`` is untouched.
The confirmation date is explicitly converted to Europe/London, regardless of
the deployment's default timezone. selection_confirmed_at takes precedence over
the documented confirmed_at fallback. Neither timestamp is rewritten.

The existing first-confirmation transaction still persists the selected programme
and uses its existing profile lookup/linking, validation, audit and APPLIED
idempotency guards. Existing cohort IDs and nonblank legacy cohort names are
preserved. Historical reconciliation cannot enter the future cohort-assignment
branch, even if the automatic-assignment flag is enabled later.

The new ``future_cohort`` consumer verifies actual canonical current and relevant
pinned publications before reusing ``resolve_automatic_cohort``. Its returned
cohort must exist for the confirmed programme in both publications. Missing pins,
canonical data, actual cohort IDs, valid periods or eligible status produce the
existing pending result. Legacy name-based/derived compatibility cohorts cannot
satisfy these checks. The existing resolver and its previous-eligible fallback
are not replaced or modified.

Important protected-data blocker
--------------------------------

The previously observed publication data uses October 14/15 boundaries. Phase 4
does not alter those periods or publish a replacement. On October 15/16, an
explicit calendar or selected published period that conflicts with the intended
intake blocks automatic assignment instead of being rewritten. Therefore a
correct default calculation alone cannot fix conflicting canonical data.
Any Programme Structure review/change is outside this phase and requires a
separate instruction and approval. Publications v12 and v16 are not repaired.

Profile creation remains a prerequisite
---------------------------------------

The existing assignment workflow only uses an already linked profile or the
existing unambiguous legacy-profile linking path. It does not create profiles.
The available profile-creation/delivery workflow also initializes placement,
training-plan/KSB and lifecycle data. Calling it from programme confirmation
would change protected functionality, so no such workaround is introduced.

Consequently the prepared path works for a future first confirmation with an
existing compatible profile and valid canonical data. A future learner without
a profile remains pending. An approved lifecycle/profile-workflow design is
still needed before claiming readiness for all new learners. Existing APPLIED
records, including learner 1601, are never reset, relinked or backfilled.

Display consistency without record synchronization
--------------------------------------------------

Admin Enrolment Details' board mapper can read the same read-only home-cohort
resolver as Step 6 when the existing COHORT_LEARNER rollout flag is enabled.
For a safely resolved assignment it displays the verbatim pinned-structure name.
It does not copy or change Created_users.Cohort, profile fields or learner pins.
With the flag disabled or a pending/failed lookup, the board retains its existing
legacy label. Step 6 keeps its existing pending behaviour. No frontend redesign,
module-selection change, scheduling board or Training Plan integration is added.

Verification and required approvals
------------------------------------

The tests use mocked publications and disposable in-memory SQLite fixtures with
networking blocked. They cover all default boundaries, UK date conversion, a
first confirmation selecting ME instead of recommended MM, cohort persistence,
shared display resolution, idempotency, missing profile/publication handling,
legacy cohort preservation, historical reconciliation exclusion and old pins.
Synthetic fixture publications are not writes to a real Enrolment database.

Before activation, separate approval is required for any feature-flag change,
profile-lifecycle integration, Programme Structure data review/change/publication,
or historical learner reconciliation. None is performed or automatically queued.
Existing approved real cohorts and compatible relevant pins must already be
available. No new schemas/migrations, approved LMS mappings or LMS access are
required or introduced by this code preparation.
