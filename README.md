# Enrolment Corner

Standalone React/Vite frontend and Django API extracted from `aiteamKBC/LMS_v2`.
Repository: https://github.com/aiteamKBC/enrollment (public).

The service owns application, eligibility, the complete enrolment wizard, staff review,
employer signatures, compliance documents, learning-plan agreement, onboarding meetings
and the first learning-session booking. Courses, lessons, submissions, marking,
attendance and ongoing study remain in LMS. Existing LMS source files were preserved.

## Local startup

Prerequisites: Python 3.14, Node 24/npm, and PostgreSQL 18. Existing pinned dependency
versions are retained. The prepared local `.env` files contain credentials and are ignored
by Git. `.env.example` files contain the shareable variable names.

From this repository:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

If checking out elsewhere, create `backend/.env` and `frontend/.env` from their examples,
then supply the actual credentials. The prepared database is Enrolment Corner; do not
point this service at the original LMS database.

In separate terminals:

```powershell
backend/.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:8000
npm --prefix frontend run dev
```

Open http://localhost:3000. The dev server requires port 3000 and stops if it is
already in use. Vite proxies API requests to `VITE_API_TARGET`, normally
http://127.0.0.1:8000. `/health/` is a liveness endpoint; it does not claim database or
Microsoft availability. Existing account passwords are copied; browser sessions and
password-reset sessions are independent. Production must use HTTPS and secure cookies.

## Environment

| File / variables | Purpose |
| --- | --- |
| `backend/.env`: `DATABASE_URL`, `ENROLLMENT_DATABASE_HOST` | Enrolment Corner PostgreSQL; both Django DB aliases use this target |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | Service identity and host configuration |
| `FRONTEND_URL`, `CSRF_TRUSTED_ORIGINS`, cookie settings | New frontend origin, invitation links and request protection |
| `AZURE_STORAGE_*`, storage-container names | Existing private documents and evidence references |
| `AZURE_MAIL_*` | Invitations/password resets; mail is disabled in the prepared local environment |
| `MICROSOFT_GRAPH_*` | First-session/onboarding booking and case-owner availability |
| `MICROSOFT_SSO_*` | Optional sign-in registration; register the new callback separately |
| `INCLUSION_SSO_*`, `SUPPORT_TICKETS_DATABASE_URL` | Optional support/inclusion integration |
| `LMS_URL`, `LMS_HANDOFF_URL`, `LMS_HANDOFF_SECRET` | Study destination and signed handoff |
| `frontend/.env`: `VITE_API_TARGET`, `VITE_LMS_URL` | Local API proxy and browser study destination; never put credentials in Vite variables |
| `integrations/.env` | Receiver credentials for the existing LMS database; load only into the receiver process |

The prepared handoff URL is deliberately empty until the receiver is deployed.
`backend/.env` and `integrations/.env` contain the matching generated signing secret.
An unconfigured/failed handoff displays an error and does not claim a successful transfer.

## Database ownership and import

`database/schema.sql` is a reproducible schema bootstrap with no learner rows.
`database/relations.json` lists its relations. The legacy SQL models are unmanaged:
ordinary Django `migrate` is not a replacement for this bootstrap.

For a new empty service database only:

```powershell
backend/.venv/Scripts/python.exe tools/bootstrap_database.py
backend/.venv/Scripts/python.exe tools/bootstrap_database.py --apply
```

Do not bootstrap again on the prepared Enrolment Corner database. The bootstrap refuses
existing service tables. Use reviewed incremental migrations for subsequent changes.

The initial copy includes the source enrolment schema and the login, learner identity,
catalogue, learning-plan and booking dependencies it needs. Source table names and
stable identities are retained; existing lowercase Enrolment Corner tables remain separate.
Only onboarding/first-session calendar rows are copied. Active login sessions, password
reset tokens, personal-calendar credentials and unrelated study history are not copied.
Referenced Azure blobs stay at their existing locations; their bytes were not duplicated.

The source trigger that propagates learner lifecycle changes into **study reviews** is
excluded from this service's schema; the original LMS trigger and data remain intact.
Module shifts that move study progress also stay in LMS; those endpoints return an explicit
`LMS_STUDY_REQUIRED` response here. Preparing the initial learning plan remains available.

The completed snapshot contains **486,091 rows across 73 relations**. All **12 pre-existing
target tables** were verified unchanged by row count and fingerprint. The filtered
onboarding-event sequence was aligned after import so new bookings cannot reuse copied IDs.
`database-copy-result.json` records the independent read-only verification. The native
transaction committed successfully; the old planning connection expired afterward, so
verification was repeated independently instead of rerunning the import.

`database/reference-schema.sql` adds a small Aptem status cache used to preserve withdrawn
learner status. It contains **423 linked IDs/statuses**, without the source study history.
The initial bootstrap includes its schema. From this original extraction workspace,
`python tools/refresh_aptem_reference.py --apply` refreshes the scoped reference rows;
the tool reads LMS without writing to it and verifies the selected target. Future Aptem
status changes need this refresh or an agreed integration; the snapshot is not live sync.

`tools/copy_database.py` is an initial extraction utility, requiring the original checkout
and private source/target configuration. It refuses relation-name conflicts, reads a
consistent source snapshot, takes a private target backup, and restores with native
PostgreSQL tools in one transaction. Row counts, foreign-key/unique constraints and
fingerprints of pre-existing target tables are checked before commit. It is not a
continuous synchronizer. Private dumps, SQL data and credentials must remain outside Git.
The non-sensitive copy plan/result files record scope and verification.

## Handoff to the existing LMS

The learner's **Continue to LMS** action uses the existing first-session readiness rule.
The backend derives the learner from the signed-in session; it does not accept a caller's
chosen learner ID. A timestamped HMAC request sends that learner's enrolment, documents,
initial learning plan, identity and onboarding bookings to a separate receiver.

Deploy the receiver beside LMS, using the same reviewed source artifact, without replacing
the current LMS application. Its process loads only `integrations/.env`. Apply the additive
`integrations/lms-receiver.sql` receipt ledger to the verified LMS database after the normal
backup/review gate, then run:

```bash
cd backend
daphne -b 127.0.0.1 -p 8002 enrollment_service.receiver_asgi:application
```

Put the receiver behind a private HTTPS route ending `/handoff`; set that URL in the
enrolment backend. Service runtime can use
`daphne -b 127.0.0.1 -p 8000 enrollment_service.asgi:application` behind the frontend proxy.
Supply environment files through the process manager; neither ASGI module loads the
receiver's environment automatically.

The receiver checks signatures, timestamps, table/role/learner ownership and database host.
Transfers are transactional and retryable. A receipt prevents a retry from overwriting
later study changes. Existing active learners are preserved. Conflicting stable IDs,
login identities or missing catalogue dependencies return a conflict for staff review;
they are never silently reassigned. LMS sign-in remains separate; this is not cookie SSO.

The receiver migration and live handoff have **not** been applied to the original LMS.
Production cutover also needs a final enrolment-data reconciliation and one agreed writer
for new enrolments. Source code preservation does not make two databases continuously sync.
Use the standard reviewed deployment from merged main. Publishing this extracted source
does not deploy the service or change the existing LMS installation. Credentials and
private database copies are excluded from the public repository.

## Checks

```powershell
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:teams
npm --prefix frontend test -- src/pages/users src/pages/apprenticeship-application src/pages/learner/onboarding src/pages/learner/welcome src/pages/learner/compliance src/pages/learner/first-session src/pages/continue-lms src/lib/enrollmentAccess.test.ts --maxWorkers=2
backend/.venv/Scripts/python.exe tools/run_backend_checks.py enrollment_service.tests enrolment_api
```

The backend unit runner loads no `.env`, uses memory-only databases and blocks sockets.
Local PostgreSQL integration runners require the private cluster's `data_directory` to
match exactly and use synthetic fixtures. They never target the configured live database.
See `VALIDATION.md` for commands, results and limits. Native PostgreSQL tools, dumps,
logs, browser screenshots and private test configuration are excluded from the repository.

Shared backend modules are retained where the original enrolment workflows import them;
the public URL allowlist, frontend routes and onboarding-only booking guard define the
service boundary. `source-manifest.json` records source provenance. Extraction helpers
are development tools; the application does not import files from the original checkout.
