# Delete Draft from learner document History

Implemented on the existing `compliance-documents` branch, 5 November 2026.
Earlier uncommitted work was preserved; no commit, push, merge, rebase or branch
switch was performed.

## Behaviour

An authorised Admin sees a red trash **Delete** action beside **Continue editing**
for eligible drafts, both in the existing inline revision list and History.
Confirmation uses the requested **Delete Draft Revision?** title and wording.
Cancel/Escape leave the draft in place. While the request is pending, confirmation
controls and dismissal are disabled. Failure is shown in the dialog and does not
remove the row. Success reloads history, closes stale editor/comparison/view state
for that draft, and shows a success notification (and the existing toast when a
ToastProvider is available).

The operation is soft discard: `state='discarded'`, with the original UUID,
revision number, content, signatures/provenance and change metadata retained.
`changes.discardedDraft` adds the actor and timestamp; `updated_by`, `updated_at`
and `edit_version` are updated once. Retried deletion preserves that first audit
event. Discarded revisions disappear from active history and all ordinary
read/edit/preview/finalise routes reject them. New drafts use the latest original
or finalised base and a new revision number, including discarded numbers in the
allocation maximum: discarding R2 produces R3, never a recycled R2.

The backend validates the exact learner, document key, issued source ID and
revision relationship, then locks the issue before the revision inside one
transaction. It rechecks draft eligibility and the displayed edit version, so
concurrent saves/finalisation cannot bypass the restrictions. Existing auth and
CSRF protection remain in force.

Historical signature images inherited from the base are allowed in a discardable
draft; they are not new approval. A signing-source link, finalisation timestamp,
original record, invalid base, dependent revision, or signature/mark evidence that
differs from the inherited base makes a revision ineligible. Finalised, issued,
partly signed and fully signed copies cannot be discarded. Direct DELETE cannot
bypass this, and the existing database trigger continues to prohibit hard DELETE
and any modification of non-draft records (including discarded audit records).

Endpoint:

```text
DELETE /enrolment_api/document-revisions/learners/{learner}/{documentKey}/revisions/{revisionId}/
JSON: {"sourceId": "exact-issued-source-id", "editVersion": 2}
```

No PDF/blob/file deletion is performed. Drafts are JSON records; this architecture
does not create temporary shared files to clean up.

## Database extension and deployment

The original PostgreSQL CHECK constraints only permitted draft/original/finalized
states. A separate forward setup extends only the state/time checks on
`neondb` → `enrolment."Learner_Document_Revisions"`, adding a discarded-state audit
safety constraint. No column or table was added. The unmanaged model already uses
a string state; no Django migration or historical SQL edit was needed.

The existing one-draft partial index excludes discarded rows automatically.
Original/finalised timestamps remain required; discarded drafts retain NULL
`finalized_at` and cannot have a signing source or an original signature method.
All existing immutable triggers, source-document tables, published global
templates and signatures are preserved.

Deployment uses the existing direct-connection schema-command architecture:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py apply_document_revision_discard --plan
.\backend\.venv\Scripts\python.exe backend/manage.py apply_document_revision_discard --check
```

The update **has been applied to the application's existing database** using a
direct connection, with its existing credentials held only in process memory.
`.env` was not changed. Before applying, the command's dry run rolled back and
the new constraints plus actual revision APIs were exercised in a second
transaction that also rolled back. After applying, the read-only constraint check
passed. Checksums/counts of the four original source-document tables matched
before and after. No real learner draft was discarded by the implementation or
tests; all API test records were synthetic and rolled back.

For another environment, run the forward setup after base revision storage and
before relying on Delete Draft. Coordinate this small schema extension with the
backend/UI deployment. The base setup command now has small overridable plan and
verification hooks so the forward command reuses its connection, transaction,
pooler rejection and five-second lock-timeout handling. It does not replay the
global template setup or edit old migrations.

## Files changed in this task

- `backend/enrolment_api/document_templates/revisions.py`
- `backend/enrolment_api/document_templates/revision_views.py`
- `backend/enrolment_api/document_templates/revision_urls.py`
- `backend/enrolment_api/document_templates/discard_draft_schema.sql` (new)
- `backend/enrolment_api/document_templates/test_discard.py` (new)
- `backend/enrolment_api/management/commands/apply_document_templates.py`
- `backend/enrolment_api/management/commands/apply_document_revision_discard.py` (new)
- `frontend/src/features/compliance-documents/revisionApi.ts`
- `frontend/src/features/compliance-documents/LearnerDocumentRevisions.tsx`
- `frontend/src/features/compliance-documents/DeleteDraftDialog.tsx` (new)
- `frontend/src/features/compliance-documents/__tests__/discardDraft.test.tsx` (new)
- `frontend/src/features/compliance-documents/__tests__/api.test.ts`
- This report.

No wizard, Skills Radar, Programme Suitability, global template content/editor,
source document/signature writer, learner answer or historical PDF was changed.

## Verification

- **87 backend feature tests passed**, including ten new discard/deployment tests.
  Covers unchanged R1/template/signatures, other learners, idempotency/audit,
  no number reuse, stale confirmations, direct API auth/CSRF, wrong document/source,
  signing-source/new evidence protection, immutable copies and stale edit URLs.
- **179 frontend regression tests passed across 20 files**, covering document
  builder/history, actual learner profile Edit, learner Compliance/Review pages,
  notifications and wizard-builder integration. The focused four-file run also
  passed (23 tests).
- After the final nested-dialog dismissal guard, **11 draft UI tests passed**,
  including confirmation, Cancel/Escape, pending controls, rejection, refresh,
  original-only Delete exclusion and remaining View/Download/Compare/Edit.
- After the deployment-command output change, **7 command regression tests passed**.
- TypeScript, scoped ESLint, production build and `git diff --check` passed.
  Existing mixed static/dynamic import build notices remain.
- **Real PostgreSQL rehearsal passed** using the actual views and database
  constraints: admin/document scoping, soft discard/audit/retry, hidden history,
  stale read/save denial, R3 allocation from R1, finalised deletion rejection,
  unchanged original signatures, immutable discarded rows and hard-delete
  rejection. Both DDL and test writes were rolled back before the schema-only
  deployment. The final `apply_document_revision_discard --check` passed.

Local evidence: `.cache/discard-draft-rehearse.json` and
`.cache/discard-draft-apply.json`. These checks do not claim a new live browser
login or persisted deletion of an existing learner's document.
