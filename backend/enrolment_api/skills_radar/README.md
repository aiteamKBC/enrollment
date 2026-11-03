# CSV Skills Radar catalogue updates

Implemented on the existing `skills-radar` branch. No commit, push, merge, rebase, branch switch, live publication or live learner-data write was performed. Existing uncommitted Skills Radar work was retained.

## Administrator workflow

Open **Enrolment Form Builder → Skills Radar**. The four programme cards show the published count, mapping hash and publication date. Download Current Mapping CSV or Blank CSV template, edit the complete file, then Upload CSV.

Upload validates and compares without writing a draft, changing learner answers or publishing. The comparison includes unchanged, added, removed, description, category and mapping changes with old/new values. PCP ownership is shown separately for each pathway. Missing codes are removals, not a partial update. At least 25% removal triggers an additional warning.

Confirm the complete snapshot, every listed removal and any category changes. **Save Skills Radar Draft** stores an actor-private draft and puts it into the current builder draft. Preview uses the existing module navigation; PCP test choices stay local. Use the existing **Publish changes → Publish** dialog to activate it. There is no separate publishing endpoint. A saved draft can be loaded after reopening the builder; a stale draft must be reimported against the latest publication. Cancel import leaves both publication and builder draft unchanged.

An identical import does not dirty the wizard or create a private draft. Repeated unchanged draft saves do not update their timestamp. Reimporting a published CSV can also explicitly revert an already-loaded private draft.

## CSV schemas

MM, ME and APM:

```csv
ksb_code,ksb_type,description,module_key
```

PCP:

```csv
ksb_code,ksb_type,description,operational_owner_key,strategic_owner_key,chartered_owner_key
```

Columns must use this exact order. Categories are `Knowledge`, `Skill`, `Behaviour`. Codes are programme-scoped `K1`, `S1`, `B1`, etc.; gaps and new codes are supported. A category change under the same code is permitted only after explicit review. Descriptions retain their supplied Unicode, punctuation, whitespace and line breaks. Standard CSV quoting supports commas, quotes and multiline text. Empty descriptions, duplicate codes, malformed rows, invalid UTF-8 and unavailable module/owner keys are rejected with row details; no partial import occurs.

Fixed programme keys reference existing published module IDs:

| Programme | Module keys |
| --- | --- |
| ME_L4 | `marketing-impact`, `social-media`, `martech` |
| MM_L6 | `marketing-strategy`, `customer-journey`, `commercial-intelligence`, `ai-marketing` |
| APM_L4 | `pmp`, `ai-project-management` |

PCP reuses the original ownership keys in the existing allocation engine. Operational and Strategic use `PMP`, `AI`, `RISK`, `PPC`, `FINANCE`, `SCHEDULE`; Chartered uses `CH_PLAN`, `CH_STAKE`, `CH_RISK`, `CH_PMO`, `AI`, `FINANCE`. Validation resolves them against active, selectable leaves in the existing module configuration. Parent groups cannot own questions. No duplicate module/pathway records are created. Module renaming keeps mappings because titles are not identifiers.

Exports include a UTF-8 BOM and this first metadata record, followed by the header:

```text
# skills-radar-v1;programme=ME_L4;escape=apostrophe
```

Keep this record when editing an export. It binds the programme and declares reversible apostrophe escaping for cells beginning with formula characters, apostrophes or whitespace. Plain hand-authored CSVs may start directly with the schema header; their descriptions are never silently unescaped. Export/import roundtrips preserve unchanged records, including formula-like text and genuine apostrophes. Blank templates intentionally contain no question rows and must be filled before importing.

Limits: 1 MiB per CSV, 2,000 records per programme, 12,000 characters per description. The builder/draft application limit is 4 MiB for the complete JSON document; deployment request/proxy limits can impose a lower limit for unusually large combined catalogues.

## Storage, identity and history

- `Wizard_Form_Layouts.Layout.skillsRadarCatalogues` contains immutable catalogue snapshots, keyed by programme. Each has `programmeId`, SHA-256 `version`, `publishedAt`, and records with stable IDs, codes, categories, descriptions and ownership keys. Publishing appends to the existing wizard history with its existing `Created_by` audit field. Unchanged programmes keep their hash and publication date.
- Existing code identities retain their original profile IDs. Added codes get deterministic IDs such as `radar:ME_L4:K99`; K1 in another programme is a separate identity. Reimport does not create duplicates.
- Removed codes are absent from the next snapshot, while earlier publications and answers remain. Description/category/mapping updates produce a new snapshot; they do not overwrite historical text. Curriculum authoring tables and original source catalogues are not rewritten by imports.
- Private drafts reuse the existing `ModuleConfigurationDraft` JSON store with a separate `skills-radar:platform:<id>` / `skills-radar:django:<id>` actor namespace. This cannot overwrite the modules editor's draft. Import approvals are validated against the currently published hash and are excluded from published layouts.
- The first real answer pins a per-programme hash in the existing `Extended_ILR.Wizard_draft.skillsRadar.catalogueVersions`. A signed learner/programme/version token ensures the answer is saved against the version shown, including when an administrator publishes while a learner has the form open. Opening, previewing or switching modules does not write an ILR record.
- Already-started legacy assessments without metadata use the immutable initial snapshot. Unrated placeholders and answers belonging to a different programme do not count as a start. The first subsequent save records the pin. New unstarted assessments resolve the latest publication.
- Legacy answers held only in the per-step tables are recovered even if a partial JSON draft contains other sections. This read touches only the radar tables and does not write or replace the learner's other sections.
- Existing pins are server-owned: incoming client metadata cannot replace them. Answer saves serialise on the learner row inside the existing transaction, preserve historical assessment entries, and retain the radar document when another draft write omits it. Existing rating/note/evidence projections remain in use.
- Overall/module progress and completion use the applicable catalogue's actual question IDs and length. ME 33→34 affects new assessments; pinned old assessments still have 33. Staff read the same applicable version with disabled Answer/View controls as before.
- PCP uses the same redistribution loop: original mandatory ownership stays fixed, unselected optional ownership is distributed among selected optional modules, and Chartered Finance resolves to its one selected alternative. Published snapshots supply the variable question list instead of enforcing the initial 71-code source.

No new database migration or schema is needed. This relies on the already-existing `programme_suitability/0005_modules_configuration.py` migration for the private draft table. No historical migration was edited. Suitability scoring, module credits/sessions/selection rules and other wizard-step implementations were not changed.

## Security and verification

New admin endpoints use the existing enrolment authentication/access gate plus staff/admin checks. Upload and draft writes enforce CSRF. Catalogue publication through the existing builder additionally enforces CSRF and staff permission. The learner endpoint is scoped by the existing learner-ownership gate and is read-only. File extension, size, MIME, encoding, structure, identifiers and active ownership are checked server-side. Actor/programme/hash metadata is logged; CSV contents are not sent to external services.

Tests run without live credentials or external network access:

```powershell
cd backend
.venv\Scripts\python.exe manage.py test enrolment_api.skills_radar.test_catalogue programme_suitability --settings=enrolment_api.skills_radar.test_settings --noinput
```

Result: **163 passed** (30 catalogue/API/history tests and 133 existing suitability/modules regression tests). The isolated runner uses in-memory SQLite, real answer/projection and private-draft models, and a real historical JSON lookup. PostgreSQL publication/table-lock statements are exercised through recording mocks. Valid CSRF, hostile origins, anonymous/non-admin access, learner scoping, validation atomicity, unchanged saves, all four programme pins, recovering partial legacy drafts and preserving a removed added-question answer are covered.

Additional existing scale/ILR regression tests: **22 passed**, using `tools/run_backend_checks.py` with `enrolment_api.tests_skills_radar_scale`, `enrolment_api.tests_ilr_learner_details`, `enrolment_api.tests_ilr_employer_details`.

Frontend catalogue tests cover all four dynamic runtimes, all eight PCP optional combinations, import review/confirmations/cancel, row errors, idempotence, draft restoration/reversion, versioned completion, old/new counts, read-only View, existing nine-point Answer modal and saved notes/evidence. Focused catalogue plus builder run: **52 passed** (25 catalogue tests and 27 builder tests), recorded in `frontend/.cache/csv-radar-final-focused.json`.

Broader frontend run: **479 passed / 483 total**, with the same four pre-existing failures around expecting 12 wizard steps where the current repository has 13. These match the prior HEAD baseline; the CSV feature does not change the step registry or count. An additional backend layout run has four pre-existing failures/errors caused by tests indexing the CV step at position zero; running the same cases against `HEAD:backend/enrolment_api/wizard_layout.py` reproduced all four. No unrelated navigation/layout refactor was made to conceal those failures.

TypeScript, targeted ESLint with zero warnings, and production Vite build passed. Headless Chrome exercised the real builder and learner components with every API request intercepted: four uploads, cancellation, CSV response content/filename, complete-snapshot review, private draft, existing Publish dialog, 390px responsive width, new 34-question assessment, existing answer modal/note/evidence filenames, save/reload, and an old read-only 33-question assessment. No unexpected API requests or browser errors occurred. Native disk download could not be verified in the intercepted Chrome environment (it cancelled before interception); backend export bytes/headers and browser fetching the CSV were verified.

Limitations: live PostgreSQL lock contention was not stress-tested and no live catalogue was published. There is no automatic migration of started learners to a new version. The existing evidence storage mechanism is unchanged; tests preserve the filenames/references already stored by Skills Radar. Historical wizard publications and the initial defaults must be retained for version resolution.

## Files and merge coordination

New backend files in `backend/enrolment_api/skills_radar/`: `__init__.py`, `defaults.json`, `catalogue.py`, `csv_io.py`, `service.py`, `views.py`, `urls.py`, `test_catalogue.py`, `test_runner.py`, `test_settings.py`, this report.

New frontend files in `frontend/src/features/programme-suitability/skills-radar/catalogue/`: `types.ts`, `api.ts`, `runtime.ts`, `useCatalogue.ts`, `CatalogueAdmin.tsx`, `ImportComparison.tsx`, `CataloguePreview.tsx`.

New tests in `frontend/src/features/programme-suitability/__tests__/`: `catalogueFixtures.ts`, `catalogueRuntime.test.ts`, `catalogueAdmin.test.tsx`, `catalogueIntegration.test.tsx`. The existing feature-local `skills-radar/allocation.ts` was extended to accept approved dynamic catalogues without replacing the existing redistribution algorithm.

Every shared file changed for this CSV task:

| File | Integration |
| --- | --- |
| `backend/enrolment_api/urls.py` | Includes dedicated radar URLs. |
| `backend/enrolment_api/wizard_layout.py` | Adds catalogue payload/validation and secure publishing; retains existing publisher/history. |
| `backend/enrolment_api/extended_ilr.py` | Retains/pins radar metadata and historical answers within the existing save transaction. |
| `backend/enrolment_api/tests_ilr_learner_details.py` | Adds the mock for the new preservation read; original ILR separation assertions remain. |
| `backend/programme_suitability/test_modules_persistence.py` | Mocks the legacy radar recovery read in the existing database-free persistence test. |
| `frontend/src/api/wizardLayout.ts` | Sends CSRF for catalogue-bearing publications. |
| `frontend/src/pages/users/types.ts` | Types the stored radar pins and signed first-save token. |
| `frontend/src/pages/users/wizard/layout/types.ts` | Types catalogue snapshots and transient import approvals. |
| `frontend/src/pages/users/wizard/layout/resolve.ts` | Preserves those fields during layout resolution. |
| `frontend/src/pages/users/wizard/WizardContext.helpers.ts` | Exposes optional managed-catalogue state. |
| `frontend/src/pages/users/wizard/WizardContext.tsx` | Loads applicable versions, derives question IDs/completion and forwards the signed save token. |
| `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` | Uses managed questions/grouping while retaining existing answer rows/modal. |
| `frontend/src/pages/users/wizard-builder/page.tsx` | Mounts the dedicated editor at `block.skillsRadar` and clarifies version retention in Publish. |

Highest textual conflict risks are `WizardContext.tsx`, `SkillsRadar.tsx`, `wizard_layout.py`, `extended_ilr.py`, and the builder page. Coordinate preservation of the two layout fields in all serializers, catalogue validation after module normalization, the signed version/preservation save path, and stable module IDs. Module deactivation/removal must be reviewed with its mappings; module title changes do not require remapping. Do not prune historical layouts or edit the immutable initial catalogue to apply a future content change—publish a CSV version instead.

The pre-existing uncommitted PCP/MM/ME/APM feature files and tests are dependencies and were left intact. `wizard/layout/registry.ts` was already modified before this CSV task; this task adds no step, renumbering or unrelated navigation changes. Merge the required feature files together with the small shared integration edits and coordinate any teammates' changes to the save/layout contracts before merging into main.
