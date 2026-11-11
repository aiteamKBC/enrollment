# Learner document Edit: storage prerequisite fix

## Current deployment status

Implementation and isolated verification completed on `compliance-documents`.
**ACTIVATED after the user's explicit approval: all five feature tables now exist.**
Installed in the application's existing Neon database `neondb`, schema `enrolment`,
through the direct endpoint `ep-delicate-lab-aqejxa89.c-8.us-east-1.aws.neon.tech`.
The application's pooled endpoint and `.env` were not changed. The application
database role `neondb_owner` has CREATE permission in this schema; the old Neon
connector's separate Viewer permission did not prevent this approved SQL rollout.

The exact existing additive command was first rehearsed with transaction rollback.
Eleven PostgreSQL protection checks passed in a second rolled-back transaction.
The command was then applied and its `--with-revisions --check` passed. All four
immutability triggers and thirteen indexes are present. Both application readiness
checks return true. No isolated Neon branch was provisioned: the rehearsal was on
the approved database inside transactions that were rolled back, not on a branch.

Subsequent API checks on PostgreSQL passed for Apprenticeship Agreement, ILR,
Training Plan and Written Agreement: create/resume the same draft, save/reload,
preview/finalise Keep, history, read-only finalised content, and unsigned Sign Again
clone creation. All verification writes were rolled back; feature issue/revision
row counts were zero before and after. No new signatures or PDFs were uploaded.
Checksums and counts of all **19 original source documents** matched exactly:
7 Agreements, 4 ILRs, 5 Training Plans and 3 Written Agreements.

Those checks exposed a PostgreSQL-specific save query error:
`FOR UPDATE must specify unqualified relation names`. The schema-qualified model
name used with `select_for_update(of=('self',))` produced invalid PostgreSQL SQL.
`revisions.get_revision` now scopes access through an issue subquery and locks only
the revision table with plain `FOR UPDATE`, without nullable base joins or `OF`.
The existing 76 isolated feature tests passed after this fix, and all 8 storage
tests passed including the new offline PostgreSQL SQL-compilation regression.

Evidence: `.cache/compliance-storage-rehearse.json`,
`.cache/compliance-storage-apply.json`, `.cache/compliance-storage-workflow.json`.
The active frontend responds on `http://localhost:3000/`; the existing backend
responds on port 8000. Its protected endpoints still require normal authentication.
Refresh the Documents tab and click Edit to create the real learner draft.

## Verified original cause (before activation)

The actual learner profile Documents tab already calls
`LearnerDocumentRevisions.startRevision` with the learner and exact source ID.
The revision routes, authorisation, draft service and editor exist. The readiness
gate correctly refuses to use storage that has never been installed.

Read-only `apply_document_templates --with-revisions --check` on 4 November 2026
reported each of these absent, and now exits with status **1** for that condition:

1. `enrolment."Document_Template_Versions"`
2. `enrolment."Document_Template_Drafts"`
3. `enrolment."Document_Template_Issues"`
4. `enrolment."Document_Template_Render_History"`
5. `enrolment."Learner_Document_Revisions"`

The feature models have `managed=False`. There is no enrolment_api Django
migration chain that creates them; ordinary `manage.py migrate` will not install
them. This project uses explicit additive schema management commands for its
unmanaged enrolment tables. The existing `schema.sql` must run before
`revision_schema.sql` because revisions reference template issues. Those SQL files
and model definitions were not changed by this fix.

The connected Neon project is Enrolment Corner (`frosty-hall-78301843`), with only
the default `production` branch (`br-wild-rain-aqcd99bk`) available and a connected
integration permission of `VIEWER`. That permission prevents provisioning an
isolated branch through the integration; the application's separate connection
can perform the read-only table inspection. No credentials are included here.

## Changes in this task

Paths below are relative to the repository root. Earlier uncommitted work in
other files has been preserved.

| File | Change |
|---|---|
| `backend/enrolment_api/document_templates/storage.py` | Read-only prerequisite diagnostics: missing tables, disabled settings, check/plan/setup commands; no runtime DDL. |
| `backend/enrolment_api/document_templates/service.py` | Explicit readiness recheck bypasses the negative cache after deployment. |
| `backend/enrolment_api/document_templates/revisions.py` | Forced recheck reaches both storage layers; explicit revision disable flag is respected. Post-deployment verification also fixed schema-qualified PostgreSQL row locking during draft save/finalisation. |
| `backend/enrolment_api/document_templates/revision_views.py` | Edit forces a fresh check; only authorised admins receive schema diagnostics. Technical failures are logged; unavailable writes remain 503. |
| `backend/enrolment_api/management/commands/apply_document_templates.py` | Added offline `--plan` with dependency order and SQL hash; `--check` returns a failing exit status if storage is missing. |
| `backend/enrolment_api/document_templates/test_storage.py` | Eight tests for diagnostics, flags, retries, admin-only setup information, deployment safety and schema-qualified PostgreSQL lock compilation. |
| `frontend/src/features/compliance-documents/api.ts` | Preserve structured setup errors for the existing revision client. |
| `frontend/src/features/compliance-documents/revisionApi.ts` | Typed setup metadata on revision history responses. |
| `frontend/src/features/compliance-documents/LearnerDocumentRevisions.tsx` | Actionable admin notice, distinct configuration/setup instructions, and explicit Edit retry against the backend; no fake availability. |
| `frontend/src/features/compliance-documents/__tests__/api.test.ts` | Verify setup errors survive the API boundary without automatic write retries. |
| `frontend/src/features/compliance-documents/__tests__/profileEdit.test.tsx` | Actual BoardPage Documents tab and real revision editor/client integration, rather than only an isolated button/service test. |
| `backend/enrolment_api/document_templates/STORAGE_SETUP_FIX.md` | This report and reviewed deployment handover. |

No shared wizard registration, progression, navigation, learner answers, Extended
ILR calculations, Skills Radar, KSBs, or source document/signature writers were
changed in this task. `BoardPage.tsx` itself did not require a further change.

## Reviewed deployment procedure (now applied as described above)

From the repository root, with the existing backend virtual environment:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py apply_document_templates --with-revisions --plan
.\backend\.venv\Scripts\python.exe backend/manage.py apply_document_templates --with-revisions --check
```

The plan itself opens no connection in the command. The check issues only
`SELECT to_regclass(...)`; its expected exit status before installation is 1.
Combined SQL SHA256:
`0716af51b90369325e8df2a26028705d59b7c6cc13035755f8b52ede3746ddd4`.

1. Supply an isolated PostgreSQL branch and a direct, non-pooled connection through
   `DOCUMENT_TEMPLATE_DATABASE_URL` using the team's secret handling. Do not put
   the URL/password in a command argument, chat, report or source control.
2. Review the two existing SQL files and run the same command with
   `--with-revisions --dry-run` on that isolated branch. This runs DDL inside a
   transaction and rolls it back; it is not a read-only production check.
3. Install with `--with-revisions` on the isolated branch, rerun `--check`, and
   verify PostgreSQL foreign keys, the one-draft unique index, all immutable
   version/issue/render/revision triggers, and concurrent draft retries. Test
   with the application's actual database role, not only the deployment owner.
4. Only after isolated PostgreSQL verification and explicit deployment approval,
   point the command at the approved shared/production **direct** connection and
   run the reviewed setup, then its read-only check. Deploy the backend and UI
   together. If either feature flag is explicitly disabled, enable it in backend
   deployment configuration and restart workers; no flag was changed here.
5. Click Edit again. Its forced check bypasses both negative readiness caches,
   so installed storage can be used without waiting for the history polling cycle.

The command's existing transaction and five-second DDL lock timeout remain. The
SQL creates five feature tables, their indexes/functions and immutable triggers;
reruns replace only the feature's own trigger definitions. It does not alter or
recreate source document tables, rewrite source rows, change template bindings,
delete signatures/PDFs, backfill guessed historic versions, or fake Django
migration history. No new historical migration or migration dependency was added.
Coordinate the **base four tables before revision storage**, direct connection,
deployment role permissions, application flags and combined backend/UI deployment
with the team's database owner before merging/deployment.

The user's attachment explicitly says: “Do not automatically apply migrations to
production or a shared database without explicit deployment approval.” That
approval was subsequently given and repeated. The earlier handover proposed an
isolated Neon branch; no such branch was provisioned. The actual approved rollout
instead used the existing direct application connection, transactional rollback
rehearsal and PostgreSQL trigger/API verification described at the top of this
report. SQLite test success alone was not treated as deployment verification.

## Verification results

- **76 backend feature tests passed**, using the existing isolated in-memory
  SQLite runner, which blocks outbound sockets and never loads `.env`. Covers
  AA/ILR/source/review workflows, pinned historical template, unchanged originals,
  original signatures/PDF paths, draft resume/idempotency, Keep provenance,
  existing Sign Again writers, preview tokens/CAS, immutable finalised API content,
  CSRF, permissions and learner isolation, plus seven new storage tests.
- **34 existing backend regression tests passed**: employer document signing,
  document schema, policy/CV documents, Skills Radar scale, learner progression and
  training-plan hydration.
- **178 frontend tests passed across 21 files**, including the three new actual
  profile Edit tests and existing builder, learner compliance/review, wizard,
  BoardPage and notification coverage. After the final setup notice/API error test
  change, **12 focused tests passed across three files**. The focused run includes
  the additional structured-error test introduced after the 178-test run.
- TypeScript check, scoped ESLint and production Vite build passed. Build retains
  existing mixed static/dynamic import and plugin timing warnings. jsdom emits its
  existing canvas/localStorage notices; they are not test failures.
- **Headless Chrome actual profile check passed** on an isolated localhost
  frontend and synthetic in-memory Django revision API. It first verified the
  explicit disabled-config error without opening a blank editor, then retried Edit
  immediately after enabling the isolated fixture. Both issued AA and signed ILR
  opened R2 with the original content/snapshots/signatures and Template V1 while
  global V2 existed. Text edits, Save Draft, browser reload, reopening the same R2,
  actual PDF preview/download, Keep finalisation and R1/R2 History passed. Original
  source rows, signature values and original PDF paths were identical afterwards.
- **Headless Chrome learner Compliance page check passed** for both documents:
  R1 Signed and R2 Updated Copy, same V1, real PDF View/Download, no Edit action or
  “Not Re-signed”. Revision APIs ran with the own-learner fixture identity.
  Unrelated source-page response metadata and admin identity are synthetic test
  fixtures; these checks do not claim a live production login or PostgreSQL run.
  Both successful browser runs had zero uncaught page errors.

Local, synthetic browser evidence is retained under `.cache/`:
`storage-fix-browser-results.json`, `storage-fix-learner-results.json`,
`storage-fix-profile.png`, `storage-fix-learner.png` and both `storage-fix-*-R2.pdf`
downloads. Temporary test servers and fixture source files are removed after the
checks. No real learner documents were used or changed.

## Remaining prerequisite

None for database activation. Refresh the current Documents tab; a valid admin
session is required as before. The setup is installed in the shared database,
independently of the current Git branch. Source document/signature records were
preserved and no global template publication was invented or backfilled. No
commit, push, merge, rebase, branch switch or reset was performed.
