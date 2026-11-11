# Learner pending-signature notifications

Implemented on `compliance-documents`, 4 November 2026. The existing uncommitted compliance builder/revision work was preserved. No commit, push, merge, rebase, branch switch, `.env` edit, production data write or schema migration was performed.

## Result

Learners have a bell beside their name throughout the existing Enrolment Corner shell. The bell and the Compliance documents navigation badge use the **same backend-confirmed pending-signature snapshot**. The number means outstanding learner actions, not unread items, all documents or employer/provider signatures.

The dropdown contains actual document titles, original/revision indicators, template versions when available, issued timestamps and Review & Sign links. At zero, both numbers disappear; the bell remains and shows “No signatures required.” Opening, viewing and downloading never resolve an obligation.

The admin/employer header gets no notification bell and makes no notification request. Existing names and Sign out controls remain intact. The dropdown supports outside dismissal, Escape with focus restoration, normal Tab navigation, Arrow Up/Down, Home/End, and bounded mobile positioning.

## Backend source of truth

`GET /enrolment_api/pending-signatures/` is read-only and has **no learner identifier parameter**. It requires an active platform learner identity and derives the learner ID from the authenticated session. Staff/employer/Django-admin-only requests are refused. Changing query parameters does not select another learner. Responses are private and `no-store`.

It reads the existing four statutory models:

| Document | Learner party |
| --- | --- |
| Apprenticeship Agreement | apprentice |
| Individual Learner Record | learner |
| Training Plan | apprentice |
| Written Agreement | learner |

It also supports the four onboarding review types through their existing `learner` signature field. A review must be the current non-cancelled instance, have a completed form, and be accessible under the portal's existing Submitted/Completed enrolment gate. An unfinished form has no usable Sign action, so it does not create a premature signing notification. The wizard gate itself was not changed.

Original statutory requests come only from the latest active source row. Cancelled/voided/superseded sources are excluded. Existing unsigned rows are included immediately; no reissue or notification backfill is needed. SQL calculates whether the learner's signature is empty without returning signature images, answer snapshots or PDF content. Overall `fullySigned` is deliberately not the pending condition.

Generic generated/uploaded files are not guessed to require signatures. They do not have the existing learner-facing statutory/review signing cards used here; employer-only paperwork must not produce an unusable learner action.

## Revisions and duplicate prevention

- A finalized Sign Again copy can contribute one obligation, identified by its stable revision UUID. Its dedicated existing source signing row supplies the actual learner signature state.
- A Keep Existing Signatures copy creates no new signing request. Historical marks are neither fresh approvals nor pending-signature records.
- Original R1 snapshots frozen by the existing revision workflow are not offered as actionable signing requests. Their existing backend locking remains unchanged.
- Within a current original document's revision chain, the newest finalized revision is the applicable copy. An unfinalized draft does not replace that request. A later finalized copy or normal source reissue makes the previous request historical for notification purposes.
- Revision signing rows deliberately have non-active source statuses in the existing implementation: compliance clones are `superseded`, review clones are `cancelled`. These structural statuses must not be confused with cancellation of the original review. Revision finalisation and the current original source determine applicability.
- Cancellation/voiding of the current original source removes its request/chain. No new cancellation workflow or mutable field on immutable revisions was introduced.
- Stable document IDs include both document key and source UUID/ID; revision IDs use their UUID. Repeated reads, retries, reopening or viewing do not insert notification rows and cannot duplicate a count.

## Navigation and state updates

Ordinary statutory items link to `/learner/compliance-documents?signature=...`. Review items use their existing `/learner/onboarding/reviews?signature=...` destination, where their existing Sign review action is available. Revision links identify the exact R2/R3 card, rather than the original.

The target query only selects an already-authorised rendered card. It does not fetch an arbitrary learner/document, open an editor or sign automatically. The selected card is scrolled into view, highlighted and focused. A notification navigation refreshes source documents/revision history so newly issued copies can be located even when the destination page was already open.

The existing cached-resource convention provides an in-memory cache keyed by authenticated account and learner. One hook in `EnrollmentShell` supplies both badges. Current auth logout already clears these caches. Old in-flight responses are ignored on account changes; counts/document metadata are not stored in browser storage.

After a **successful** existing issue/sign/review-completion/revision-finalisation API write, a small revalidation event invalidates this resource. A storage pulse allows other tabs to revalidate; it contains only a timestamp/random pulse, no document IDs, signatures or personal data. Failed/cancelled signing attempts do not emit a successful-write event or optimistically decrement counts.

Visible portals also revalidate every **30 seconds**, on focus and when returning to a visible tab. This detects issuance from another admin/browser without WebSockets or new infrastructure. Hidden tabs defer reads until visible. Refresh/login always returns to backend state. A failed refresh reports Retry and retains the last confirmed snapshot; it does not falsely report that obligations disappeared.

## Files created

- `backend/enrolment_api/pending_signatures.py`
- `backend/enrolment_api/document_templates/test_pending_signatures.py`
- `frontend/src/features/pending-signatures/api.ts`
- `frontend/src/features/pending-signatures/events.ts`
- `frontend/src/features/pending-signatures/usePendingSignatures.ts`
- `frontend/src/features/pending-signatures/SignatureNotifications.tsx`
- `frontend/src/features/pending-signatures/DocumentSignatureTarget.tsx`
- `frontend/src/features/pending-signatures/targets.ts`
- `frontend/src/features/pending-signatures/__tests__/notifications.test.tsx`
- `frontend/src/features/pending-signatures/__tests__/writeInvalidation.test.ts`
- This report.

## Existing files modified by this task

| Shared file | Targeted change |
| --- | --- |
| `backend/enrolment_api/urls.py` | Register the read-only self endpoint. |
| `frontend/src/components/feature/EnrollmentShell.tsx` | Single shared state, learner-only bell and matching sidebar badge. |
| `frontend/src/api/apprenticeshipAgreement.ts` | Revalidation signal after successful issue/sign. |
| `frontend/src/api/ilrDocument.ts` | Same successful-write signal. |
| `frontend/src/api/trainingPlanDocument.ts` | Same successful-write signal. |
| `frontend/src/api/writtenAgreement.ts` | Same successful-write signal. |
| `frontend/src/api/reviewForm.ts` | Signal after successful review writes, including completion/signing/issuance. |
| `frontend/src/pages/learner/compliance/page.tsx` | Card targets and source refresh for notification navigation. |
| `frontend/src/pages/learner/onboarding/reviews/page.tsx` | Review targets and refresh for notification navigation. |
| `frontend/src/features/compliance-documents/revisionApi.ts` | Signal after successful finalisation/signing. |
| `frontend/src/features/compliance-documents/LearnerDocumentRevisions.tsx` | Exact revision targets and navigation-triggered history refresh. |
| `frontend/src/features/compliance-documents/__tests__/learnerRevisions.test.tsx` | Router context for the added navigation target. |

Other dirty files shown by Git predate this task. This task does not edit backend signature writers, database models, existing signature records, original PDFs, template content/global versioning, assessment/Skills Radar/KSB logic or wizard step/navigation/completion logic.

## Tests and actual results

- **69 backend document tests passed**, including the new 14 pending-signature integration tests. They cover existing records; all four statutory learner parties; all four review revision types; completed/current/submitted review access; personal versus overall signatures; draft/publication exclusions; superseded/voided/cancelled sources; exact revision links; Sign Again and Keep; latest applicable revision/draft handling; missing optional template storage; session ownership; inactive/non-learner rejection; duplicate prevention; private metadata; read failures and commercial exclusions.
- **175 frontend tests passed across 20 files**, including **40 new notification/writer tests**. Coverage includes one shared count, empty state, opening without decrementing, exact original/revision targets, successful/failed signatures, polling, cross-tab revalidation events, keyboard behavior, logout/account changes, stale in-flight response isolation, retry behavior, all statutory/review writers and revision finalisation/signature invalidation. Existing builder, documents, reviews, admin board, CV and Skills Radar selections passed together.
- **34 additional existing backend regressions passed** for employer signing, document schema/policies, CV documents, Skills Radar scale, learner progression and training-plan hydration.
- TypeScript, targeted ESLint with `--max-warnings 0`, production build and `git diff --check` passed. The build retains the previously observed mixed static/dynamic review-import and plugin-timing notices; it exits successfully.
- **Real Chrome with real local API handlers and a disposable in-memory database passed**: unsigned original count; admin issuing another document while the learner stays open; automatic polling; matching badges; a second learner tab observing the same count and subsequent signing update; keyboard dismissal; 390px mobile dropdown; exact original navigation; existing Preview/Download without count changes; the existing SignaturePad signing both originals; employer/provider-pending documents no longer counted after the learner signs; refresh preserving zero; newly finalized Sign Again R2; exact R2 highlight and existing revision SignaturePad; count clearing after the learner signature while employer remains pending. The browser used a synthetic authenticated account context; no real account, production database or external service was changed.
- A separate real-browser **360px mobile empty-state** check passed without horizontal overflow.

Ignored local browser artifacts under `.cache/`: `pending-signatures-browser-results.json`, `pending-signatures-desktop.png`, `pending-signatures-mobile.png`, `pending-signatures-mobile-empty.png`, `pending-signatures-revision-target.png`, `pending-original-preview.pdf`. Temporary fixture source files and servers are removed/stopped after verification.

Reproduce backend tests with:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test enrolment_api.document_templates --settings=enrolment_api.document_templates.test_settings --noinput
```

The feature-only frontend tests are:

```powershell
npm --prefix frontend test -- src/features/pending-signatures/__tests__ --maxWorkers=2
```

## Database/deployment and limits

**No new migration, notification table, backfill or production write is needed for this feature.** Original pending documents work even if the optional template/revision tables are not yet installed. New learner-specific revisions still depend on the prior revision feature's additive table rollout; this task does not deploy or change that rollout. See `backend/enrolment_api/document_templates/FULL_PAGE_REVISIONS.md` for that existing activation requirement.

Issuance from a separate admin session is eventually visible within the 30-second visible-tab polling window, or sooner on focus. This is not server-push delivery. Review notifications wait for the existing review accessibility/completion rules. The current system has no independent mutable cancellation field on finalized revisions; applicability uses the current source and newest finalized revision as described above.

These are relevant selected regressions, not a claim that every test in the repository was run. New notification reads were tested with isolated source rows; no production learner documents were used as test fixtures.

## Merge coordination

Highest textual-conflict risks are the shared `EnrollmentShell`, learner document/review pages, API writer return paths and the common API URL list. Keep teammates' existing sidebar destinations, signing response handling, permission checks and signature/revision behavior when resolving these hunks.

Behavioral integration matters as well: header and sidebar must consume one hook; emit revalidation only after successful writes; preserve the auth/learner cache key and stale-response guard; keep the backend target encoding aligned with `targets.ts`; keep current-source/revision filtering and review accessibility rules together. Dropping a target wrapper would leave a valid notification unable to locate its Sign action.

If teammates add a new document/signatory type, integrate its authoritative learner signature source and actual secure learner Sign UI before counting it. Do not infer signing requirements from wording, titles, historical images or overall status. There are no migration dependencies to coordinate for this notification addition itself.
