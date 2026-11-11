# Configurable document question cards — implementation report

Implemented on `compliance-documents`, preserving the pre-existing uncommitted
document-builder work. No branch switch, commit, push, merge, rebase or reset.

## What the administrator can edit

Existing Review wording maps, including all six Extended ILR review questions,
now open as individual field cards. Existing bound scalar document fields and
two-column document-data tables also use field cards. Their original labels,
answer keys and data bindings are retained. The evidence question loads as
multi-line text; Yes/No questions load as radio fields rather than new text inputs.

Cards have expand/collapse, move up/down, Duplicate and Delete actions. Expanded
cards contain a label with dynamic-field picker, the shared Field type control,
help text, Mandatory, options/defaults where applicable, and conditional display.
Disabled settings explain their actual data/workflow restriction.

| Configuration | Custom document fields | Existing system-linked fields |
|---|---|---|
| Label and dynamic placeholders | Editable | Editable |
| Help text and dynamic placeholders | Editable | Editable |
| Type | Editable among supported document types | Fixed to existing source type |
| Mandatory | Requires configured value/default before publication | Original workflow setting retained |
| Options/default selections | Editable with stable option IDs | Original source choices retained |
| Conditions | Preceding choice fields in the same section | Original source behaviour retained |
| Reordering | Supported within the section | Presentation order only |
| Duplicate | New field ID; copy configuration | Disabled; cannot create another answer relationship |
| Delete | Confirmed draft removal; dangling conditions must be corrected | Disabled; enclosing protected sections cannot be removed through the API either |

Add Field uses exactly the same field-card component as editing existing fields.
It can add custom fields inside a question section or create a new document-field
section. Contiguous legacy scalar fields become a section only when edited;
their individual field IDs and bindings do not change during grouping.

Supported types use the existing wizard vocabulary: `single_line`, `multi_line`,
`number`, `dropdown`, `heading`, `description`, `divider`, `radio`, `checkbox`.
The existing legacy `text` spelling remains compatible.

**Document context limitation:** these are document presentation/configuration
fields. They print an administrator-configured value or a permitted existing
data placeholder; this upgrade does not create a new learner-answer collection
or database. Choice fields print their configured selections and choices.
Mandatory validates the configured value/default before publication, and prints
the required indicator. It does not add a new wizard progression requirement.

File upload is explicitly disabled with an explanation and rejected server-side.
Existing signed PDF uploads and secure storage remain the upload workflow.
Numeric limits validate configured constants; existing numeric source values
retain their own workflow validation. Dynamic numeric placeholders must come
from the existing numeric document field registry.

Runtime-generated curriculum activities, calculated delivery/cost data and
complex PLR/Skills Radar review tables retain their original generation logic.
They are not converted into independent answer inputs. Static cost row labels
are configurable cards, while amounts/totals remain linked to their original
issued values and are not recalculated by the editor. Existing review input
controls/answer saving and the separate Extended ILR wizard step are unchanged.

## Reuse and persistence

The wizard's existing `CustomFieldType`, `LayoutCondition`, type options and
content-type helpers are reused. Its existing type selector was extracted into
`FieldTypeControl`; the existing New field editor now uses that same component,
retaining its original value/change/disabled behaviour. Backend field/content/
choice type sets come from `enrolment_api.wizard_layout` without changing that
module or its schema/answer logic. Dynamic text controls and the existing
allowlisted placeholder registry are reused across document fields.

Document-specific validation is separate from wizard answer-column creation:
it must not run wizard DDL or create answer columns merely to edit a PDF template.

Question configuration is an optional `fields` array in the existing versioned
template JSON. Custom field sections use block type `questions`. Each item has:

```json
{
  "id": "question-stable-id",
  "label": "Supporting information for {{learner.fullName}}",
  "type": "multi_line",
  "helpText": "Existing document placeholder support is retained.",
  "required": false,
  "condition": {"field": "preceding-question-id", "values": ["option-no-id"]},
  "options": [],
  "defaultOptionIds": [],
  "value": "Configured document content",
  "systemLinked": false,
  "binding": null
}
```

Array order is the display order, matching the existing builder architecture;
there is no competing index-based identity or redundant `order` property.
Choice IDs remain stable when labels are edited or options are reordered.
Deleting a controller/option or moving a dependent before its controller leaves
an explicit validation warning and requires correction, rather than silently
rewiring the condition. Conditions cannot refer forward, to another section,
to absent options, to themselves, or form a cycle.

Draft Save/reopen, saved PDF Preview and versioned Publish use the same APIs and
JSON storage already implemented for documents. Existing optimistic revision
checks, preview tokens, approval, authorisation and CSRF protection remain.
**No new database tables or migrations are required by this upgrade.** The initial
document-builder storage installation, if not already deployed, remains a
prerequisite; see the original [deployment guide](README.md). No production
database/schema/storage writes were performed for this upgrade.

## Backward compatibility and immutable documents

- Legacy `labels`, scalar `field` and bound two-column `table` structures adapt
  to cards without modifying their stored JSON merely by opening the editor.
- Original wording/order remain the default. Persisted fields are introduced
  only by an actual edit; the original label maps remain available to existing
  review wording consumers.
- Legacy scalar binding edits and existing presentation repeats remain readable
  and receive stable, non-colliding card IDs. A new save cannot use compatibility
  adaptation to change a published system binding.
- Server validation protects IDs, bindings, source types, choices and workflow
  settings. Save and Publish additionally compare system bindings against the
  actual published base; deleting an enclosing protected block cannot bypass
  field-card restrictions.
- Previously published JSON, source answers, source document rows, signature
  marks/timestamps and uploaded evidence are not migrated or rewritten.
- The existing issued-version pinning, signature handling and immutable history
  remain. Both partially and fully signed old documents were tested after new
  publications. Existing original PDFs still use their unchanged legacy renderer.

## Files changed for this upgrade

These are the incremental changes for the field-card upgrade, distinct from the
pre-existing uncommitted implementation listed in `README.md`.

| File relative to `enrollment/` | Change |
|---|---|
| `frontend/src/features/compliance-documents/QuestionCards.tsx` | New shared create/edit card UI, options, conditions and field management |
| `frontend/src/features/compliance-documents/questionFields.ts` | Legacy adaptation, stable identity, source values, condition evaluation and client validation |
| `frontend/src/features/compliance-documents/TokenText.tsx` | Existing dynamic text editor extracted for reuse |
| `frontend/src/features/compliance-documents/question_types.json` | Existing review question type compatibility catalogue |
| `frontend/src/features/compliance-documents/BlockEditor.tsx` | Replace wording textareas/bound table rows with cards |
| `frontend/src/features/compliance-documents/page.tsx` | Add Field, scalar section grouping, protected block deletion and save validation |
| `frontend/src/features/compliance-documents/types.ts` | Structured field JSON using existing type/condition definitions |
| `frontend/src/features/compliance-documents/render.ts` | Existing jsPDF renderer consumes saved order/types/choices/help/conditions and original source values |
| `frontend/src/features/compliance-documents/__tests__/questionCards.test.tsx` | Card editor interaction and safety coverage |
| `frontend/src/features/compliance-documents/__tests__/questionRendering.test.ts` | Adaptation, preserved repeats, real PDF output, answer identity, conditions and costs |
| `frontend/src/pages/users/wizard-builder/FieldTypeControl.tsx` | Extracted shared field-type selector |
| `frontend/src/pages/users/wizard-builder/FieldEditor.tsx` | Existing New field editor uses that selector; no wizard persistence changes |
| `backend/enrolment_api/document_templates/question_fields.py` | Field schema validation, compatibility adaptation and binding protection |
| `backend/enrolment_api/document_templates/question_types.json` | Matching controlled review question types |
| `backend/enrolment_api/document_templates/registry.py` | Optional structured fields while retaining legacy JSON compatibility |
| `backend/enrolment_api/document_templates/views.py` | Save/Publish enforce unchanged published system bindings |
| `backend/enrolment_api/document_templates/test_question_fields.py` | Field schema, type/choice/condition validation and compatibility tests |
| `backend/enrolment_api/document_templates/test_templates.py` | Persistent field workflow plus partial/full signed-document regression |
| `backend/enrolment_api/document_templates/FIELD_BUILDER_UPGRADE.md` | This report |

## Verification

| Check | Actual result |
|---|---|
| Document backend suites | **45 passed** |
| Frontend document/wizard-builder and related regressions | **120 passed in 16 files** |
| Existing targeted backend document/progression/CV/Skills Radar tests | **34 passed** |
| Programme Suitability suite | **133 passed** |
| TypeScript | Passed |
| ESLint on document feature and shared field editor/control | Passed, zero warnings |
| Vite production build | Passed |
| Whitespace/scope review | Passed; current branch and uncommitted work preserved |

A real Chrome workflow ran against the actual Django views and temporary
in-memory SQLite, with synthetic users/documents and no `.env` or production
database connection:

1. Opened an existing ILR template and its bound Family name field.
2. Edited its label/help and moved it ahead of ULN, preserving the original data
   path `document.learnerDetails.familyName`.
3. Added a required custom field with a configured value.
4. Saved the draft, reloaded the page and verified both values and order.
5. Rendered and inspected the real five-page PDF in Chrome; sample placeholders,
   help and configured values appeared in the saved order (50,645 bytes).
6. Published version 2 through the real preview-token/approval flow.
7. Issued a new synthetic learner document through the existing issue view and
   verified it used version 2 and the new field configuration.
8. Verified the pre-existing fully signed version 1 document retained its template,
   marks and signed state; no Issue/Sign call came from merely editing/previewing.

The backend suite separately covers partial signatures and subsequent full
signing, original PDF compatibility, permissions, invalid/missing choices,
source tampering, removed controllers, numeric validation, immutable publication
history, cost bindings and missing dynamic field warnings. Browser tests use an
injected synthetic admin identity, while real permission checks are covered by
the backend tests. No claim is made that PostgreSQL DDL/locking or live Azure
storage was tested by the SQLite/browser workflow; this upgrade changes neither.

Reproduce principal checks:

```powershell
.\backend\.venv\Scripts\python.exe backend/manage.py test enrolment_api.document_templates.test_templates enrolment_api.document_templates.test_question_fields --settings=enrolment_api.document_templates.test_settings --noinput
npm --prefix frontend test -- src/features/compliance-documents/__tests__ src/pages/users/wizard-builder/__tests__ src/pages/learner/compliance src/pages/learner/onboarding/__tests__/reviewDownload.test.tsx src/pages/learner/onboarding/__tests__/ulnPrivacyReview.test.tsx src/pages/users/__tests__/boardPage.test.tsx src/pages/users/wizard/__tests__/skillsRadarScale.test.tsx src/pages/users/wizard/__tests__/cvJobStep.test.tsx --maxWorkers=2
npm --prefix frontend run type-check
npm --prefix frontend run build
```

## Merge coordination

The only additional shared wizard-builder modification is the selector extraction
in `FieldEditor.tsx`. Preserve that integration when merging another developer's
New field UI changes; existing wizard semantics and tests must remain intact.
Document feature files may conflict with concurrent template/rendering work.
Keep optional legacy adaptation, API binding protection and PDF field rendering
together: resolving a textual conflict by dropping one would cause behavioural
data/version problems.

The compatibility question-type catalogues must match. Changes to underlying
system question definitions require coordination with their source/answer
workflow; ordinary document edits must not rewrite those compatibility types.
Skills Radar, KSB CSV/mappings, Programme Suitability, wizard progression,
Extended ILR calculations, source learner models, storage and existing issuer/
signature APIs received no further changes in this upgrade.
