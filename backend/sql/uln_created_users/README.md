# ULN canonical storage: proposed deployment, awaiting production approval

Prepared and rehearsed 25 November 2026. **No production migration or write test
has been run.** Production continues using `Wizard_Plr.ULN` because
`ENROLMENT_ULN_STORAGE` defaults to `legacy`. No new Django model field is mapped.

## Production preflight

Run `00_preflight.sql` against the verified Enrolment Corner target immediately
before any approved change. Record the exact host/branch, backup identifiers,
counts and fingerprints in the private deployment record, not this repository.
No live deployment results, learner identities, ULNs or documents are packaged
with this change. The test results below use synthetic data only.

Confirm the current column state, legacy value formats, duplicate/conflicting
ownership, learner-kind mappings and custom legacy ULN bindings. Preserve the
nine previously identified orphan PLR rows without inventing mappings. Stop if
any orphan has a populated ULN or an identity/mapping conflict is found. Earlier
findings do not replace these checks: shared production data can change.

`Created_users.id` is the integer primary key. The existing PLR scope continues
using `Wizard_Plr.Learner_id` / `Wizard_Plr_Records.Learner_id` with that key.
No FK is added: these scope columns currently have no enforced FK. The lowercase
`Created_users.learner_id` instead links to `Learner.learners.id`; unique partial
indexes exist on this link and on the reverse `learners.enrolment_id` link.
Neither link establishes the business rule for a person returning for another
enrolment. No orphan mapping is inferred, changed, created or deleted.

**Database ULN uniqueness is deferred.** Confirm whether one physical learner
always retains one Created_users row, including re-enrolments and type changes.
Unique UUID/email and one-to-one profile links do not prove that rule. The
existing application duplicate check remains; canonical assignments share the
same PostgreSQL transaction advisory lock across Admin edits and PDF imports.
Unresolved orphan ULNs are conservatively reserved without assigning an owner.
This does not prevent privileged SQL clients from creating duplicates before a
database uniqueness constraint is separately approved. No uniqueness index is
part of this deployment. The candidate partial unique index was tested locally
only; its eventual definition would be:

```sql
CREATE UNIQUE INDEX CONCURRENTLY created_users_uln_unique
ON enrolment."Created_users" ("ULN") WHERE "ULN" IS NOT NULL;
```

## Exact database changes

1. `01_expand.sql`: add just nullable `ULN VARCHAR(10)` and its format CHECK to
   `enrolment."Created_users"`. `NULL` means no ULN. The ASCII regex and character
   length reject empty strings, non-ASCII numerals, whitespace and bad lengths.
   The application converts its existing empty-string API value to SQL NULL.
   Leading zeros are preserved. No default, FK, index, trigger or new table.
2. `02_reconcile_once.sql`: while writers are paused, recheck format, scope,
   multiple values and duplicate ownership, then copy matched nonempty legacy
   ULNs using **Created_users.id**. Conflicts abort the entire transaction.
   An already-populated canonical column aborts instead of being overwritten.
   Nine orphan legacy rows are left untouched, even if they contain a value
   that cannot be mapped. All Wizard_Plr rows and columns remain intact.

These files are operator-run SQL, not auto-startup Django migrations. They have
bounded lock/statement timeouts. Expansion takes a brief exclusive table lock;
reconciliation locks the two affected tables against writers. A timeout is an
abort requiring a new window, not permission to increase timeouts blindly.

Only Created_users data/schema changes during deployment. Wizard_Plr is read
for backfill and later remains a transactionally maintained compatibility
mirror. Wizard_Plr_Records, its identifiers/evidence and Extended_ILR continue
using their current structures. No layouts, assignments, form builder, steps,
other schemas, source/evidence tables or revision/deletion/source columns change.

## Application paths and compatibility

| Consumer | Legacy mode | After canonical cutover |
| --- | --- | --- |
| Admin ULN automatic save | Existing explicit expected/value permission check | Same API; canonical write + legacy mirror in one transaction |
| PDF preview | Read legacy; no writes | Read canonical; same identity/ULN checks; no writes |
| PDF import | Existing atomic import | Same transaction includes canonical ULN, mirror, qualifications and protected metadata |
| Ordinary enrolment saves / Student Add | Omit ULN unless explicit Admin edit | Same; stale draft ULNs never overwrite the canonical value |
| PLR read in Admin/Student bootstrap and ILR | Existing legacy value | PK lookup in Created_users; NULL returns empty API string with no legacy fallback |
| Live review PLR panel | Existing scope lookup | Shared canonical ULN accessor; qualification records unchanged |
| Newly issued ILR learner details | Existing historical RPL/PLR precedence | Canonical ULN, including deliberate NULL; signed issued snapshots remain unchanged |
| Board wizard bundle | Existing legacy PLR row | Retained mirror remains compatible (bundle currently does not consume its ULN) |
| Enrolment-to-LMS contract v1 | Existing Wizard_Plr payload | Exporter omits new Created_users.ULN key; retained mirror carries ULN in supported shape |
| Existing audit serializers | Explicit mapped fields; ULN redacted on PLR | Unchanged; new raw-access column is not included in Created_users serializers or audit field lists; mirror retains existing redacted audit path |
| Custom wizard layout mapping | Custom answer columns on Wizard_Plr | Unchanged; fresh inspection found no custom field mapped to ULN; built-in PLR projection uses the shared storage accessor |
| Paused older PLR service/views | Unused | Remain unused; never activate |

The new column is deliberately **not** added to EnrolmentUser's Django field
list. Ordinary Created_users queries/inserts remain compatible with both schemas.
Only `created_users` mode queries the optional column. SQL errors remain inside
the existing PLR savepoint: the section reports loadError, never a fake empty
table; unrelated sections remain available. Logs contain exception type and
SQLSTATE without learner values or SQL parameters. Existing required completion
validation, permissions, evidence preservation and stale-save protections remain.

After cutover, NULL is authoritative. Never COALESCE from legacy ULN, draft JSON
or old Review_RPL values. The legacy mirror is updated/cleared in the same
transaction as canonical writes. Review_RPL remains historical, not another
canonical store. No backfill runs from application startup, reads or saves.

## Colleague checklist: one coordinated maintenance window

No production SQL may run until the project owner explicitly confirms that
every developer is ready and all relevant writers have stopped. Keep backends
paused throughout expansion, legacy-mode verification, reconciliation and
canonical verification. Only the coordinator runs the two approved SQL files.

### 1. Integrate the agreed compatible backend before the window

The ULN compatibility change consists of these runtime files, relative to the
repository root. They build on the already completed PLR/ULN functionality;
integrate the tested change together, rather than copying a single helper.

| File | Compatibility change |
| --- | --- |
| backend/enrolment_api/plr_uln_storage.py | New legacy/canonical accessor; canonical NULL is authoritative; transactional legacy mirror |
| backend/enrolment_api/plr_uln.py | Same Admin/ASCII validation; duplicate/concurrent assignment protection checks canonical ownership after cutover |
| backend/enrolment_api/wizard_steps.py | PLR reads and explicit ULN projection use the shared accessor; ordinary saves preserve ULN |
| backend/enrolment_api/plr_pdf_preview.py | Preview reads ULN through the accessor |
| backend/enrolment_api/plr_pdf_import.py | Atomic import writes ULN through the accessor |
| backend/enrollment_service/settings.py | ENROLMENT_ULN_STORAGE switch, default legacy |
| backend/enrollment_service/handoff.py | Excludes the new Created_users.ULN key from the existing v1 transfer payload |
| backend/learner_api/review_form.py | Live PLR panel reads canonical ULN after cutover |
| backend/learner_api/ilr_document.py | Newly derived ILR details use canonical ULN after cutover without restoring cleared historical values |

Include backend/enrollment_service/test_uln_compatibility.py with the change as
its exporter regression test. Existing PLR tests and the isolated PostgreSQL
rehearsal remain part of validation. No new learner model field, frontend change,
dependency upgrade or Django migration is required by this ULN storage phase.

The exact expansion compatibility fix in handoff.py is:

```python
user = users[0]
user.pop('ULN', None)
```

It keeps the existing Wizard_Plr ULN payload; no receiver or other system is
changed. The complete file list above is still required for canonical cutover.

- [ ] Save local work on your own branch (commit it, or use your normal local
  backup/stash workflow). Keep your own .env file and database credentials.
- [ ] Integrate the coordinator's agreed commit/package and resolve conflicts
  with your own changes. Do not replace your whole working folder or use a hard
  reset. If your checkout lacks the completed PLR features, resolve that baseline
  mismatch with the coordinator before declaring it ready.
- [ ] Report the shared commit/package identifier. This checklist does not
  publish a commit or prove that colleagues already have the compatible code.

### 2. Keep legacy mode until the coordinator announces cutover

In each developer's backend/.env:

```dotenv
ENROLMENT_ULN_STORAGE=legacy
```

If the terminal, IDE launch configuration or another environment source also
sets this variable, it must agree: an existing process environment value takes
precedence over .env. Do not change DATABASE_URL, ENROLLMENT_DATABASE_HOST,
credentials, or point the application at the recovery branch.

### 3. Stop your own writers at the agreed start time

- [ ] Save pending enrolment work while the backend is still running, then
  close Wizard tabs so autosaves and retries cannot continue.
- [ ] Stop every copy of your Enrolment Corner Django backend with Ctrl+C,
  including any IDE/container instance you started.
- [ ] Pause management commands, scripts, agents, test runners and SQL editor
  actions that could write learner, ULN, PLR or enrolment draft data in shared
  Neon. Finish or roll back your own open transactions normally.
- [ ] Keep source files/work intact. Code editors and the frontend dev server
  may remain open. Do not terminate another developer's sessions/processes.
- [ ] Send the project owner this acknowledgement:

```text
Ready: compatible backend version <commit/package ID> integrated.
Storage mode is legacy. All my Enrolment Corner backends and shared-database
writers are stopped. I will not restart them until the coordinator releases us.
```

### 4. Remain paused throughout the coordinator's work

After the project owner's explicit all-ready/all-stopped confirmation, the
coordinator rechecks recovery/preflight and runs 01_expand.sql. Only the
coordinator temporarily runs the compatible backend in legacy mode to verify
Admin and Student enrolment reads, then stops it again and verifies the drain.
Everyone else remains paused. The coordinator repeats preflight, runs
02_reconcile_once.sql once, and verifies data/backfill before enabling canonical
mode for the coordinator's backend and checking both Wizards again. Any failure
means stop, report it and keep everyone paused. No synthetic writes go to Neon.

### 5. Restart only after successful cutover and release

- [ ] Wait for the coordinator to announce successful verification and release.
- [ ] In backend/.env, and in any overriding terminal/IDE environment, set:

```dotenv
ENROLMENT_ULN_STORAGE=created_users
```

- [ ] Start a fresh backend process from the repository root; do not rely on an
  already-running autoreloader to pick up a changed environment:

```powershell
.\backend\.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

- [ ] Reload old browser tabs and verify your Admin/Student view as applicable.
  Report any error; do not independently restart an old backend or switch back
  to legacy mode. Rollback must be coordinated for everyone and retains the
  legacy mirror/column without dropping data or database structures.

## Shared developer database: technical coordination reference

Enrolment Corner is currently run on developers' individual computers against
one shared Neon production database. There is no known hosted application,
separate worker or scheduled job. A check of one workstation cannot establish
that all database writers have stopped. Do not terminate another developer's
connections/processes, change their credentials, or replace their local work.

Expansion and canonical cutover have different technical prerequisites. For the
current execution, the one-window checklist above requires everyone to stay
paused between both stages:

| Stage | Can developers keep their backends running? | Required condition |
| --- | --- | --- |
| 01_expand.sql only | Yes, with compatible application versions and a coordinated quiet DDL moment | Every backend remains in legacy mode; whole-row consumers are compatible; no long transaction holds the target table |
| 02_reconcile_once.sql and storage-mode switch | No relevant writer may run until released by the coordinator | Every developer acknowledges the pause and has the same tested compatible backend version available |

The added column is nullable, has no default or uniqueness index, and is not
mapped onto the general learner Django model. Existing ORM inserts/updates use
their explicit mapped columns; existing PLR reads/writes still use Wizard_Plr.
The CHECK accepts NULL, so adding it does not create a mandatory ULN requirement.
However, this is not an unconditional guarantee for unknown colleague changes:
positional SQL, SELECT * consumers and whole-row exporters must be checked.
The known handoff exporter previously shipped every Created_users key, while
the receiver rejects unsupported keys. Every developer should integrate the
prepared exporter compatibility patch before expansion; the receiver is not
changed. The full canonical backend must be available to everyone before
reconciliation, but its storage flag must remain legacy until cutover.

ALTER TABLE takes an ACCESS EXCLUSIVE lock, so this is not a guarantee of zero
interruption. Agree on a short quiet moment for expansion, let existing requests
and transactions finish, and temporarily avoid learner/enrolment operations and
long read transactions against Created_users. Backend processes may stay up in
legacy mode. The exact reviewed SQL has a 3-second lock timeout and a 30-second
statement timeout. If either fails, stop and report it; do not kill sessions,
increase timeouts or automatically retry. PostgreSQL reference:
https://www.postgresql.org/docs/18/sql-altertable.html

The following stage details support the checklist above. The earlier option of
resuming ordinary development after expansion is not being used for this window.
Use one named migration coordinator:

1. **Distribute and identify the compatible version.** Each developer saves
   local code edits and integrates the agreed backend commit/package without a
   reset or discarding unrelated work. Confirm that the exporter patch is
   present and ENROLMENT_ULN_STORAGE is legacy in each backend's effective
   environment. Pin the same tested backend commit/package identifier for the
   eventual cutover. Compatibility code in this workspace alone does not prove
   colleagues have received or loaded it. Restart their own backend normally
   if needed to load the compatible code, still in legacy mode.
2. **Expansion only.** Recheck the approved snapshot/recovery point and current
   production preflight. Agree on the quiet DDL moment above. The coordinator
   runs the unchanged 01_expand.sql once, verifies its exact column/CHECK, then
   verifies existing Admin and Student enrolment reads and PLR failure isolation
   in legacy mode. Confirm unchanged learner data, PLR/evidence, orphan rows,
   progress, layouts and assignments. Everyone remains paused for the same-window
   reconciliation; expansion itself does not activate canonical storage.
3. **Agree on the cutover window.** Obtain a fresh recovery point if appropriate
   for the elapsed time and any intervening changes. Before the window, each
   developer finishes or saves outstanding enrolment work while the old backend
   is still available. Close wizard/browser tabs that can autosave or retry
   queued requests; keep source files and local work intact.
4. **Every relevant writer stops voluntarily.** Each developer stops their own
   Enrolment Corner Django server (normally Ctrl+C), any other copy/container of
   that backend, and any management command, script, test runner, agent or SQL
   editor action that could write ULN/PLR/enrolment drafts or learner rows in
   the shared database. They finish or roll back their own open transactions
   normally. Do not run tests against shared Neon. Editors/source work, unrelated
   projects and read-only console browsing can remain available, but no long
   transactions may obstruct migration locks. Stop the whole relevant backend
   rather than relying on people avoiding the PLR page: ordinary wizard saves
   and automatic retries can also write legacy PLR values.
5. **Collect acknowledgements and verify the drain.** Each developer reports
   their identity, compatible backend version, stopped backend instances and
   paused write commands/SQL sessions, and agrees not to restart until released.
   The coordinator checks all developers have acknowledged, verifies no relevant
   in-flight write transaction remains using read-only database diagnostics,
   and repeats preflight. No active sessions at one instant is insufficient:
   an idle old backend can reconnect later. If a developer is unconfirmed, a
   writer is still running, an orphan ULN is populated, a mapping conflicts, or
   verification fails, stop. Never infer a team-wide pause from this computer.
6. **Reconcile once while paused.** Only after those checks, the coordinator
   sets the reviewed session acknowledgement and runs the unchanged
   02_reconcile_once.sql once, before canonical activation. Verify the backfill
   against current matched legacy values, preservation of the nine orphan rows,
   all existing enrolment data and unchanged layout/assignment fingerprints.
   Keep every other writer stopped. Do not rerun backfill after cutover.
7. **Activate only the compatible version, verify, then release.** Every
   developer prepares ENROLMENT_ULN_STORAGE=created_users for the agreed backend
   version while their backend is stopped. The coordinator starts the compatible
   local backend with canonical storage and verifies Admin and Student enrolment
   reads, permissions, existing PLR visibility and section failure isolation.
   Use isolated tests for import/edit/delete/concurrency write checks, never
   synthetic shared-Neon writes. After successful verification, release the
   other developers to start that version in canonical mode and reload old
   browser tabs. No legacy-mode backend or old write script may restart.
8. **Rollback if needed.** Keep everyone paused if verification fails and report
   the failure. Use the retained legacy mirror and reviewed rollback procedure:
   verify canonical/mirror agreement including intentional NULL clears, then
   coordinate a switch of all backends to legacy mode. Do not drop the new
   column or constraint, restore over newer progress, or remove the exporter
   compatibility patch. A subsequent cutover requires a reviewed reconciliation;
   it is not permission to rerun 02_reconcile_once.sql.

The nine focused exporter/ULN compatibility tests were rerun successfully on
25 November 2026 using isolated synthetic test data. Neither approved SQL file
was changed or executed during this coordination review. The previously approved
scope and existing application duplicate/concurrency protections are unchanged.

## Deployment sequence (only after explicit approval)

1. Verify the intended Enrolment Corner production branch/database and use its
   **direct** connection for DDL/backups. Recheck metadata with preflight SQL.
   If any custom ULN binding appears, stop and review that writer before
   cutover; do not edit a published layout or the Form Builder as a workaround.
2. Take and verify the approved backup described below. Keep Enrolment Corner
   on `ENROLMENT_ULN_STORAGE=legacy`.
3. Deploy the compatible code to all Enrolment Corner instances, including the
   handoff exporter which strips the new key. Old ULN logic and the current
   schema continue to work. **Retire/drain any older exporter that serializes
   Created_users wholesale before expansion**: its v1 LMS receiver would reject
   the new key even if NULL. Do not change the receiver or the LMS schema.
4. Apply `01_expand.sql` with `psql -X --set ON_ERROR_STOP=1` against the verified
   direct target. Check exact type, nullability and CHECK definition. Remain in
   legacy mode: older compatible application releases can still read/write
   legacy ULNs during the rollout. Do not assume any initial copy stays current.
   Before proceeding to reconciliation or changing the storage setting, verify
   both Admin and Student enrolment/bootstrap reads against their existing saved
   data, Admin-only permissions, and PLR section failure isolation in legacy
   mode. Keep PDF Import and Edit/Delete enabled through their existing paths;
   verify their write behavior using the isolated tests, not synthetic Neon
   writes. Stop if this pre-switch verification fails.
5. Schedule a short Enrolment Corner write maintenance window. At the existing
   deployment/proxy layer pause all enrolment write endpoints and background
   writers/commands (ordinary draft saves can also write PLR); let in-flight
   transactions finish. Keep reads available. Verify no old writer remains.
   This is an operational gate, not a new enrolment completion requirement.
6. Recheck preflight, then set the session-only acknowledgement
   `SET enrolment.uln_legacy_writers_drained = 'yes';` and run
   `02_reconcile_once.sql` in that same session. Verify every matched nonempty
   legacy value equals its canonical value, counts/evidence/progress unchanged,
   and all orphan rows equal the protected backup. Investigate any conflict;
   do not trim, invent identities, fix orphans or force the backfill.
7. While writes remain paused, set **every instance** to
   `ENROLMENT_ULN_STORAGE=created_users`, restart/replace the service processes,
   and verify reads for Admin and Student, NULLs and existing values. Verify
   there are no legacy-mode writers before resuming writes. Do not mix legacy
   writers with canonical writers after this point. That would reintroduce a
   second source of truth; bidirectional triggers are intentionally avoided.
8. Resume writes and monitor section errors, conflict counts and approved
   manual acceptance checks. Do not run synthetic write/load tests on Neon.
   Keep legacy storage for the compatibility/rollback period.

No production setting has been changed, no application service restarted, and
no migration applied as part of this preparation. Expansion alone never enables
canonical mode. Do not run backfill again after cutover, even if every ULN is
currently NULL; it is not an ongoing reconciliation job.

## Backup, rollback and recovery

Before production approval is acted upon, verify the Neon restore window and
record a recovery point/branch identifier in the approved infrastructure.
Using a direct connection and approved encrypted storage, obtain a custom-format
pg_dump of the schema/data for Created_users, Wizard_Plr, Wizard_Plr_Records and
Extended_ILR, including their sequences/constraints. For example (credentials
come from the approved operator environment, never this document):

```text
pg_dump --dbname=<approved-direct-connection> --format=custom
  --table=<literal-pattern: enrolment."Created_users">
  --table=<literal-pattern: enrolment."Wizard_Plr">
  --table=<literal-pattern: enrolment."Wizard_Plr_Records">
  --table=<literal-pattern: enrolment."Extended_ILR">
  --file=<approved-encrypted-backup-location>
```

Quote case-sensitive table patterns appropriately for the operator's shell
(the complete `enrolment."Created_users"` string must reach pg_dump as one
argument, including its double quotes). Validate archive contents
with pg_restore --list and rehearse a restore within approved private
infrastructure. Record backup checksum and restore results. This task did not
export real learner data or create a production backup.

A targeted --table archive does not include the containing schema or every
referenced dependency. Restore only into an isolated recovery target with the
enrolment schema and the existing referenced tables, roles and extensions
available (prefer an approved recovery clone). Do not treat a four-table dump
as a self-contained full-database backup. Do not create missing dependencies or
disable their constraints in production to make a restore succeed.

On 25 November 2026, the exact four-table pg_dump/pg_restore procedure was
rehearsed locally using synthetic PostgreSQL data. The empty local restore target
first needed its enrolment schema created; after adding that recovery
prerequisite, restore passed. Full row contents, the ULN type/nullability,
Created_users constraints and linked sequence values matched the source. The
synthetic archive SHA256 was
963b37321b761c704e59b3b4126af6765df1ac4fb96c8d2f8894204845e5825d.
This verifies the synthetic restore procedure, not a live Neon backup, its
foreign-key dependency completeness or its point-in-time recovery window. Those
remain mandatory production execution gates.

If expansion/backfill fails, transaction rollback preserves data. Leave legacy
mode enabled and investigate. Before cutover, application rollback requires no
database rollback. After cutover, pause/drain writers again, verify canonical
and legacy mirrors match (including NULL/empty equivalence and deleted refs),
then switch every instance back to legacy mode before resuming writes. Keep the
handoff compatibility patch even on a rollback while the new column exists.
Do not deploy an older wholesale exporter. **Do not drop the new column, CHECK,
table or data.** The retained mirror supports a rollback without recreating the
database. A future re-cutover after legacy writes needs its own reviewed
reconciliation; never reuse the one-time backfill against an active canonical
store. For unexpected corruption, restore to an isolated approved recovery
branch, compare affected rows and agree on selective recovery; never overwrite
production or its newer enrolment progress with a blanket restore.

## Verification performed

Isolated PostgreSQL **18.6**, new database `enrollment_uln_5c4d2a95ed5a`, 16 tests
passed. The runner refuses anything except the private 127.0.0.1:55439 cluster,
checks server data_directory before writes, never imports production settings
or .env, and blocks external socket connections. Only synthetic identities,
dates, ULNs, qualifications and generated PDFs were used.

Tests cover migration guards/backfill, nine unchanged orphans, id versus linked
learner_id, leading zeros, NULL/ASCII constraints, conflicting/duplicate legacy
owners, legacy writes between expansion/cutover, an isolated candidate unique
index, two concurrent assignments, explicit Admin edits/clears, stale drafts,
legacy rollback reads, atomic rollback, synthetic PDF preview/import/reimport,
Student visibility/Add, Admin record edit/delete, permissions, live review/ILR
consumers, missing optional column, genuine PostgreSQL section-query failures,
other-section saving and both Admin/Student bootstrap responses.

Commands (from enrollment; backend command from backend):

```text
backend/.venv/Scripts/python.exe tools/rehearse_uln_postgres.py
```

```text
.venv/Scripts/python.exe manage.py test enrollment_service.test_uln_compatibility enrolment_api.tests_plr_uln enrolment_api.tests_plr_pdf_import enrolment_api.tests_plr_pdf_preview enrolment_api.tests_plr_legacy_regression enrolment_api.tests_plr_record_changes --settings=enrolment_api.plr_test_settings --verbosity=1
```

48 targeted backend tests passed in isolated SQLite, including v1 handoff
compatibility with absent, NULL and populated optional columns. Four existing
frontend suites passed, **46 tests**: plrStep, plrPdfPreview,
recordPrefillAndNextSteps and API plrPdfPreview. No frontend code changes.

Read-only production view verification also passed: Admin enrolment board,
Admin and Student wizard-bootstrap and extended-ilr returned 200 with existing
saved data and correct Admin-only ULN permission. Simulated PLR failures kept
both view responses at 200 with a section error and other saved sections intact.
Preview security GET returned Admin 200 / Student 403. Transactions were READ
ONLY with a non-read SQL guard; no production write endpoints were invoked.

Limits: no production canonical-column activation, real LRS PDF compatibility
test, signed-in browser acceptance session, production backup/restore or load
test was performed. UI verification uses existing component/provider tests and
real read-only backend view calls; unrelated dependencies in the isolated
PostgreSQL bootstrap tests are mocked. This is a rehearsed change awaiting
approval, not a claim of production deployment.

## Files affected by this phase

Application: enrolment_api/plr_uln_storage.py (new), plr_uln.py, wizard_steps.py,
plr_pdf_preview.py, plr_pdf_import.py; enrollment_service/settings.py,
handoff.py; learner_api/review_form.py, ilr_document.py.

Specification/tests: this directory's 00_preflight.sql, 01_expand.sql,
02_reconcile_once.sql and README.md; tools/rehearse_uln_postgres.py;
enrollment_service/test_uln_compatibility.py. Other existing working-tree changes
predate this phase and were preserved. No models, layouts or frontend files were
changed by this phase.
