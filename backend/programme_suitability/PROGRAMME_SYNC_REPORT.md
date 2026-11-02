# Confirmed programme selection integration

Implemented on `CV-and-Programme-Suitability`, 2026-11-01. Existing uncommitted work was
preserved. No commit, push, merge or branch switch was performed.

## Canonical relationship and actual catalogue

The existing account/application is `learner_api.EnrolmentUser`, backed by
`enrolment."Created_users"`. Its `programme` field maps to the existing `Programme` text
column and stores the catalogue's programme name. This is the value the Users directory,
programme filters, Edit User prefill and enrolment board already read. Authentication uses
`login.LoginAccount.subject_id`; no new account or login field is needed.

An existing permanent `learner_api.LearnerProfile` (`"Learner".learners`) is linked through
`enrolment_id`. It stores `programme` and the stable catalogue key `programme_id`. Both are
updated when that profile exists. Normal onboarding records without a permanent profile
continue to use the enrolment source; the established activation flow creates their profile
later. This enhancement does not create a premature profile or duplicate learner account.

Read-only inspection of the configured live catalogue verified these existing active records:

| Suitability code | Catalogue `programme_id` | Existing catalogue name |
| --- | --- | --- |
| `ME_L4` | `PROG-ME-L4` | Marketing Executive Level 4 |
| `MM_L6` | `PROG-MM-L6` | Marketing Manager Level 6 |
| `APM_L4` | `PROG-20260907141619067932` | Associate Project Manager Level 4 |
| `PCP_L6` | `PROG-PCP-L6` | Project Controls Professional Level 6 |

The resolver queries existing records, rather than assuming suitability codes are database
primary keys. An optional saved `catalogueId` is validated when configured; otherwise it
resolves the canonical name. Pathway/level must agree with the stable assessment definition
and the actual record's name/level. Missing, ambiguous, inactive, archived, deleted or wrong
mappings fail with an actionable review error. Archived Marketing Manager duplicates found
in the catalogue are not mistaken for the active Level 6 record. No programme was created.

## Confirmation and atomicity

`sync_confirmed_programme_to_learner` runs after the assessment confirmation save, within
the enclosing confirmation transaction, for Continue and permitted alternative selections.
It reads the server's saved `selected_programme`, never a client-supplied catalogue id. It
preserves the original recommendation, scores, answers and alternative reason. Draft saves,
submission, opening/closing results and incomplete alternative reasons do not assign anything.
The existing zero-evidence continuation remains supported without changing a programme.

The feature's `default` and legacy `enrolment` aliases refer to the same configured PostgreSQL
database. All assignment ORM operations and catalogue queries explicitly use the assessment's
connection so confirmation, source assignment, profile assignment and audit commit together.
The aliases are checked first; an omitted PostgreSQL port is treated as its default 5432.
Different databases are rejected instead of making a falsely atomic two-connection write.
A profile save failure rolls back the learner update, alternative reason and confirmation.

Only programme name/id and, for a safely matched legacy profile, its existing enrolment link
are written. Profile timestamps update normally. The broad placement-mirroring helper is
deliberately not called: it also stamps cohort dates and swallows database errors, contrary
to this task's consistency and preservation requirements.

Existing cohort/group relationships must resolve within the selected programme; incompatible
ones return 409 `PROGRAMME_ASSIGNMENT_REVIEW_REQUIRED` and remain unchanged. Finalised enrolment,
delivery/active status or active/staff-approved profile placement cannot be silently moved.
No cohort/group assignment, date stamping, status progression, employer change or training-plan
change is performed. Administrators retain their existing authorised manual edit workflow.

Assignment metadata is stored in the existing `review.programmeAssignment` JSON and a
`PROGRAMME_ASSIGNMENT_SYNCED` history entry, including old/new programme and actor/time.
Existing staff review writes preserve this metadata. Repeated confirmations are idempotent;
later authorised staff programme changes are not undone by a stale confirmation retry.

## Existing interface integration

- Users table and programme filters: the existing directory API reads the updated source name.
- Edit User: the existing fields API returns the canonical name matching the curriculum dropdown.
- Enrolment board/wizard bootstrap: the original programme summary reads the same source.
- Learner Profile/Overview: the existing detail/summary projections read the source programme;
  permanent profile consumers also receive the updated name and catalogue id.
- Frontend confirmation invalidates the existing wizard bootstrap/ILR and learner detail/summary
  caches only after successful confirmation. Failed confirmation/submission alone does not
  invalidate account reads. Existing learner-read subscribers are notified by the normal
  invalidator. No User Management/Profile component, button, result card or navigation changed.

An unrelated authorised phone edit was tested against the real existing edit endpoint; it
retains the confirmed programme. Original assessment recommendation and score remain distinct
from account placement when the learner chose a lower-level alternative.

## Historical reconciliation and actual verification

The command defaults to dry-run and accepts repeatable `--learner-id` filters. `--apply`
checks each row in its own transaction and reports success only after commit. It fills a
missing source assignment or consistent missing profile link; a different nonempty source
or profile programme is reported for review. Its ordinary ID snapshot works behind Neon's
transaction pooler, without relying on a server-side cursor across per-row transactions.

The configured database dry-run identified **3 safe missing assignments**, with **0 review
conflicts**. Those three were applied, preserving the selections already confirmed by learners:

| Learner id | Confirmed selection | Programme saved |
| --- | --- | --- |
| 1601 | `ME_L4` | Marketing Executive Level 4 |
| 1606 | `PCP_L6` | Project Controls Professional Level 6 |
| 1607 | `PCP_L6` | Project Controls Professional Level 6 |

All three had no permanent profile yet; none was created. Fresh database reads verified the
saved source relationship and exact Edit User projection. Using each existing learner's
actual login-account identity, the existing board and learner-summary view responses returned
the same programme for all three. The existing learner-detail API's read-only quiz projection
was additionally verified for learner 1606 and returned Project Controls Professional Level 6.
An additional dry-run reported **ALREADY_SYNCED=3, REVIEW=0**, with no further writes.

The separate isolated integration suite creates real source/profile/catalogue tables and
exercises all six recommended/alternative cases, including actual directory, Edit User and
learner-summary endpoint projections. Only unrelated signature/document/progression reads are
stubbed there; saved programme fields and programme outputs are not mocked. Missing/ambiguous
mapping, forbidden ownership/selection, atomic rollback/retry, compatible/incompatible placement,
finalised protection, legacy links, existing staff edits and command safety are covered.

## Files changed for this task

All backend changes are under `backend/programme_suitability/`:

- `programme_assignment.py` (new shared service within the feature)
- `views.py` (confirmation integration and response/error metadata)
- `management/__init__.py`, `management/commands/__init__.py` (new)
- `management/commands/reconcile_suitability_programmes.py` (new)
- `test_programme_assignment.py` (new isolated database/API integration tests)
- `test_runner.py`, `tests.py` (test schema support and service isolation for older policy tests)
- `README.md`, `PROGRAMME_SYNC_REPORT.md` (this report)

Frontend changes, under `frontend/src/features/programme-suitability/`:

- `api.ts` (existing cache invalidation after confirmed selection)
- `__tests__/api.test.ts` (successful/failed/submission cache behaviour)

**No new database migration is needed.** Existing fields/relationships and review/history JSON
are reused; migration drift check reported no model changes. No shared User Management,
Profile, global cache or wizard source file was changed in this task. Prior uncommitted shared
patches remain. The feature directories themselves are still untracked as part of the earlier
work; include the complete feature and its existing migration chain when preparing a PR.

## Commands and actual results

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
# 86 passed; migration-backed memory SQLite, network blocked
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api enrollment_service.tests
# 217 passed
npm --prefix frontend test -- src/features/programme-suitability src/pages/users src/pages/learner/onboarding --maxWorkers=2
# 413 passed across 51 files
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
# All passed
.\backend\.venv\Scripts\python.exe backend/manage.py makemigrations programme_suitability --check --dry-run
# No changes detected
.\backend\.venv\Scripts\python.exe backend/manage.py reconcile_suitability_programmes --dry-run
# Final: ALREADY_SYNCED=3, REVIEW=0
```

No unresolved live catalogue mappings or historical review conflicts were found. In another
environment, mappings and protected placements are checked afresh; failures require existing
admissions/admin review rather than guessing or overwriting an approved programme. No email,
invitation, duplicate programme, new learner account or premature permanent profile was created.
