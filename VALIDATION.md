# Extraction validation

All original LMS tracked files were preserved. Work is under the independent `enrollment/`
repository. No deployment or live Microsoft operations were performed. Publication covers
the extracted source and configuration examples; credentials and private data are excluded.

Publication review verified that all 1,278 staged files exclude private runtime files and
the actual configured secrets. `git diff --cached --check` reports 41 whitespace issues,
all verified present in the original copied source; they were preserved without a broad
formatting change. The original LMS working tree remains unchanged.

Commands below are relative to `enrollment/`. On this workstation, Python checks reused
the existing parent project's Python 3.14 virtual environment. Frontend checks reused the
installed dependencies through an ignored local junction; new checkouts use `npm ci`.

| Check | Command / result |
| --- | --- |
| Frontend types | `npm --prefix frontend run type-check` — PASS |
| Frontend lint | `npm --prefix frontend run lint` — PASS, zero warnings |
| Frontend bundle/routes | `npm --prefix frontend run build` — PASS |
| Enrolment frontend | `npm --prefix frontend test -- src/pages/users src/pages/apprenticeship-application src/pages/learner/onboarding src/pages/learner/welcome src/pages/learner/compliance src/pages/learner/first-session src/pages/continue-lms src/lib/enrollmentAccess.test.ts --maxWorkers=2` — **345 passed, 53 files** |
| Teams frontend baseline | `npm --prefix frontend run test:teams` — **136 passed, 9 files** |
| Enrolment/backend boundary | `python tools/run_backend_checks.py enrollment_service.tests enrolment_api` — **217 passed**; memory-only settings and external sockets blocked |
| Teams PostgreSQL integration | `python tools/run_teams_postgres_checks.py` — **48 passed**; booking races, curriculum meetings/sync verdicts, forbidden cross-coach booking; verified private local PostgreSQL, mocked Graph/mail |
| Handoff PostgreSQL | `python tools/run_handoff_postgres_checks.py` — **8 passed**; atomic import, retry, ID/login conflicts, profile/plan/signed-document transfer, preservation of active learners and linked staff accounts |
| Runtime settings/middleware | `python tools/check_runtime.py` — PASS; actual settings on verified local PostgreSQL; health, authenticated administration, wizard, documents, learning-plan and first-session reads for both apprenticeship/commercial learners returned 200; protected anonymous requests 401; outbound network blocked |
| Browser smoke | Start Vite on `127.0.0.1:4179`, then from `frontend` run `node scripts/mock-smoke.mjs` — PASS using installed headless Edge; login, enrolment shell and mobile navigation; all APIs mocked |
| Schema rehearsal | `python tools/copy_database.py --schema-only --apply --target private/enrollment_rehearsal.json` — PASS, 73 relations, no learner rows copied locally |
| Copied database | `python tools/verify_database_copy.py` — PASS, 73 relations / 486,091 rows; all 12 pre-existing target tables unchanged |
| Aptem status reference | `python tools/refresh_aptem_reference.py --apply` — PASS, 423 linked IDs/statuses copied to the new target; source read-only |
| Live Teams/email/storage mutation | **NOT RUN** |

The initial combined frontend run had one focus-timing failure in
`apprenticeship-application/page.test.tsx`. Its isolated rerun passed 17/17 without an
implementation/test change. The final combined run including the role-boundary tests passed 345/345.

## Additional shared-module failures

This wider database-free run produced **259 passed, 6 failed (265 total)**:

```text
python tools/run_backend_checks.py enrolment_api learner_api.tests_booking_calendar curriculum_api.tests.TeamsMultiDayRecurrenceTests curriculum_api.tests.TeamsAttendanceRosterTests learner_api.tests.TeamsAttendanceEligibilityTests learner_api.tests.TeamsAttendanceSyncTests coach_api.tests_errors coach_api.tests.CoachTimetableWindowTests coach_api.tests.CoachTimetableBookingConflictTests coach_api.tests.CoachMeetingArtifactTests
```

All six failures were reproduced against the original, unchanged LMS modules using
the same isolated configuration:

```text
python tools/run_backend_checks.py --source learner_api.tests_booking_calendar coach_api.tests_errors coach_api.tests.CoachMeetingArtifactTests
```

That source comparison produced **31 passed, 6 failed (37 total)**. The failures are:

- Weekend booking expectation reaches missing case-owner validation first.
- Two rescheduling tests return 503 where the legacy mocks expect 200/409.
- The batch error test receives the enforced authentication gate's 401.
- Two artifact tests access database-backed attendance links from `SimpleTestCase`.

No assertions, gates or test expectations were weakened to hide these results. The
separate real-PostgreSQL Teams suites passed. This does not make the entire legacy
backend test collection green.

## Limits and release work

The browser smoke uses synthetic responses, not real learner data. PostgreSQL integration
fixtures contain no copied learner records. Passing mocked tests does not validate live
Microsoft credentials, invitations, Azure availability or end-to-end live handoff.

The receiver's additive migration has not been applied to the original LMS. Its deployment,
HTTPS URL, service callback registrations, mail enablement and final data reconciliation
remain release steps. Successful database verification is recorded in
`database-copy-result.json`; private backup/data files are excluded from Git.
The native import committed; an idle planning connection then expired before its Python
wrapper could report success. Independent read-only verification confirmed every copied
count and every pre-existing table fingerprint. No duplicate import was attempted.
