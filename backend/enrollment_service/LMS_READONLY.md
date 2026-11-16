LMS schedule reader
===================

`lms_readonly.py` uses the existing Psycopg 3 dependency and the backend-only
`LMS_READONLY_DATABASE_URL` variable loaded by the existing Django settings.
It does not load dotenv itself, install a database alias/router/model, expose an
HTTP endpoint, or connect at import/application startup. Existing database and
migration configuration is untouched. Never put this variable in frontend
environment files or log its value.

Public operations (call explicitly after Django settings have loaded):

- `verify_connection()`: verifies the reader role, read-only transaction and
  required schema; returns only two sample counts capped at three.
- `get_module_offerings(programme_id, cohort_id=None, limit=100, offset=0)`:
  pass an independently verified **LMS** programme ID. A page is capped at 200
  records. There is no ME/MM/APM/PCP mapping or programme discovery operation.
  Each result retains `module_catalogue_id`, programme/cohort/group identities,
  module start/end dates and session count. Different G1/G2 offerings remain
  separate. Pagination uses a deterministic date/identity ordering; reads are
  live rather than a historical snapshot across multiple requests.

Safety boundary
---------------

- Uses only the dedicated `enrolment_modules_reader` account on a Neon host.
- Enforces certificate/hostname verification (`verify-full`, the existing pinned
  Certifi CA bundle, including on Windows)
  and SCRAM channel binding. No downgrade on connection failure. URL query
  parameters cannot inject libpq session options or override account/host.
- Sets Psycopg `read_only=True` before the first SELECT, producing a read-only
  transaction, and checks the effective transaction mode and current role.
  PostgreSQL role privileges remain an independent protection. Application code
  must never expose the underlying driver connection or add a generic SQL API.
- Fixed SELECT allowlist only. Programme, cohort and paging values are bound
  parameters. No ORM, DDL, DML, migrations, role changes or write probes.
- A fixed SELECT sets transaction-local statement/lock timeouts (5s/1s);
  these settings and transaction control do not modify database records/schema.
  Connection timeout is 5s; connections are rolled back and closed, never
  committed, cached or pooled. Driver prepared statements are disabled for
  pooler compatibility.
- Required names/types are documented in `database/schema.sql` and validated
  against `information_schema.columns` on each connection before schedule reads.
  Missing columns or unexpected types fail closed.
- Excludes deleted modules/cohorts, parent-deletion markers, archived/unknown
  cohort statuses, missing programme identities/names and absent/reversed/
  infinite module dates. Any nonblank parent-deletion marker is conservatively
  treated as deleted. A planned cohort needs a valid module offering to appear.
  Joins use both programme ID and cohort ID, never names. Optional cohort dates
  must be finite/ordered when present. It does not invent additional rules about
  historical dates or require module dates to fit within cohort dates.
- Missing configuration raises `LmsReadUnavailable('not_configured')` only when
  called. Other failures expose fixed codes, not server errors/DSNs. Callers
  should present an unavailable state and must not echo credentials or raw
  configuration. There are no caller/UI integrations in this change.

Verification
------------

From `backend`, run the hermetic tests (no dotenv/Django setup or remote access):

    .venv/Scripts/python.exe -m unittest enrollment_service.test_lms_readonly -v

Most tests mock Psycopg. Filter tests execute the actual SELECT with adapted
placeholder/cast syntax on disposable SQLite in-memory fixtures, never the LMS.
They verify composite joins, deletion/date filters and distinct group offerings.
Never test role enforcement by attempting a real database write.

Before UI work, verify the four programme identity mappings, exact cohort/group
offerings and publication/history rules with the project owner. Decide how
unavailable/empty/truncated pages are shown. This reader does not change
suitability results, learner assignment, cohorts, pathways, training plans,
Skills Radar or documents.
