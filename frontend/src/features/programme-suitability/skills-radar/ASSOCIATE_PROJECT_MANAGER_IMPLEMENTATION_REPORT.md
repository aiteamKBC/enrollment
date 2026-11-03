# Associate Project Manager Level 4 module-based Skills Radar

## Delivered behaviour and reused components

Follow-up: staff review before Step 6 confirmation is covered by `APM_STAFF_REVIEW_FIX_REPORT.md`. It fixes the formerly uncovered assigned-programme/unconfirmed admin case; the original implementation results below are retained as a record of that pass.

APM L4 now uses the same existing SkillsRadar and ModuleMasterDetail components as PCP, MM and ME: two vertically stacked module cards physically on the left and one large selected-module question panel on the right. First module is selected initially. Clicking another replaces only the details, with stable card positions and no answer writes or catalogue reloads. Mobile navigation sits above details; inherited RTL and a row-reverse parent cannot mirror the desktop columns.

The existing question row renderer, purple/white styling, Answer/Edit/View controls, Modal, nine-point rating scale/labels/scores, high-rating note validation, notes, evidence handling, Cancel, read-only review and Confirm & Next are reused. No APM-only assessment component, second modal, scoring algorithm or answer-storage mechanism was created.

APM has no pathways, optional selection, module checkboxes, credits or redistribution. Both modules and all 62 questions appear automatically from the fixed source mapping and applicable published configuration.

## Exact APM mapping verified

| Source reference key | Existing APM module ID | Published title at verification | Sessions | K/S/B | Total |
| --- | --- | --- | --- | --- | --- |
| `apm_l4_pmp` | `pmp` | Project Management Professional | 28 | 24 / 15 / 3 | 42 |
| `apm_l4_ai` | `ai-project-management` | AI in Project Management | 14 | 7 / 11 / 2 | 20 |

The source names the first group Project Management Professional (PMP). Its existing Admin title currently omits `(PMP)`; the UI deliberately reads the actual published title without modifying configuration or hardcoding a replacement. Session counts also come from configuration, not source defaults.

PMP exact codes:

- Knowledge: K1 K2 K3 K4 K5 K6 K7 K8 K9 K10 K11 K12 K13 K14 K16 K17 K18 K21 K22 K23 K24 K25 K28 K29.
- Skills: S2 S3 S4 S5 S6 S9 S10 S11 S12 S14 S15 S17 S18 S21 S26.
- Behaviours: B1 B2 B3.

AI in Project Management exact codes:

- Knowledge: K15 K19 K20 K26 K27 K30 K31.
- Skills: S1 S7 S8 S13 S16 S19 S20 S22 S23 S24 S25.
- Behaviours: B4 B5.

Total: **62 unique KSBs** — K1–K31 (31 Knowledge), S1–S26 (26 Skills), B1–B5 (5 Behaviours). Every code belongs to exactly one module. Ownership follows the supplied Revised workbook, including AI assignments whose wording does not explicitly mention AI; no questions were generated or reclassified by subject matter.

The dedicated `associateProjectManager.ts` data adapter resolves explicit source mapping keys to existing APM module IDs. Runtime configuration lookup never matches editable names or module positions. Renames, session changes and source/configuration display reordering preserve ownership. The applicable published/pinned modules snapshot remains the existing Step 6 source; no duplicate live modules catalogue or Admin KSB editor was introduced.

## Authoritative wording and existing IDs

The checked-in `associate_project_manager_l4_ksb_full_mapping.json` is byte-identical to the supplied JSON. All 62 types, full original descriptions, mapping keys and owners were verified against KSB source, Coverage and Module map in the available **Revised** workbook:

`C:\Users\lenovo\Downloads\Associate_Project_Manager_Level_4_KSB_Mapping_Revised.xlsx`

Its filename lacks `(1)` from the prompt, but all records match the supplied JSON. The older non-Revised workbook was not used for ownership.

- JSON SHA-256: `19a842d3925fae1818bfed4805b027bf04db0c05da90c182d42d73a649be2b77`
- Revised workbook SHA-256: `5c07a681bad67a495ad246685164bf57338836de7c5fd67d88bce8bdbd10e774`

Original wording, punctuation and spelling are unchanged, including the complete K4 PESTLE/SWOT/VUCA description. Both rows and the existing popup display the same canonical full text.

Read-only inspection through the application's existing Django connection found the same 62 complete descriptions under `standard:st0310-v1-5`, with existing profile IDs such as `st0310-v1-5-K1`. The published modules version was 11. No real learner, KSB, module, schema, environment variable, backend model or migration was changed. There was no need to create duplicate records or rewrite historical data.

## Persistence, identity, progress and validation

Answers continue using existing programme-specific profile IDs. APM `st0310-v1-5-K1` and PCP `st0845-v1-1-K1` remain different answers even though both have code K1. Tests verify that editing the APM answer neither loads nor overwrites the historical PCP rating, note, evidence or action plan. Arbitrary existing database IDs are supported; no IDs are manufactured from code or module position.

Existing WizardProvider profile caching, answer seeding, save serialization and completion predicates are reused. The selected confirmed APM programme determines the curriculum lookup even if the board still shows a stale programme. No new programme-confirmation requirement or wizard step-gating architecture was introduced.

Global progress remains X of 62 and module progress X of 42/20, counting current unique profile IDs only. Existing saved notes, evidence filenames, action plans, ratings and historical responses remain in `skillsRadar.assessments`. Derived grouping/configuration metadata is excluded from saved answers as before.

Confirm & Next follows the resolved global assessment sequence; confirming PMP's last question B3 at 42/62 advances to K15 at 43/62 and automatically selects the AI module. It does not restart numbering per module.

Validation rejects missing/duplicate/unknown codes, incorrect types, empty/code-only descriptions, inconsistent source keys/owners, incorrect 42/20 sizes, ambiguous profile IDs, invalid programme configuration and missing published module IDs. Errors identify affected codes and do not silently render partial questions. Existing derived configurationError handling is used without modifying registry completion logic or the 13-step registration.

The existing evidence implementation records filenames in the Skills Radar answer envelope. Its workflow was preserved; this task does not add a new blob-storage/upload service.

## Files created or modified

Shared production files additionally edited:

1. `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` — adds APM lookup/grouping dispatch into the unchanged shared row/modal/navigation logic.
2. `frontend/src/pages/users/wizard/WizardContext.tsx` — adds confirmed APM profile lookup and existing derived mapping-validity handling.

Existing tests updated:

- `frontend/src/features/programme-suitability/__tests__/mmRadarUI.test.tsx`
- `frontend/src/features/programme-suitability/__tests__/meRadarUI.test.tsx`

Their old assertions that confirmed APM uses a flat list are obsolete under this explicitly requested feature. They now assert that MM/ME profile data cannot be reused as APM just because codes overlap. New dedicated APM tests verify the actual two-module UI, all 62 records and persistence; the updates do not suppress a genuine implementation failure.

New files:

- `frontend/src/features/programme-suitability/skills-radar/associateProjectManager.ts`
- `frontend/src/features/programme-suitability/skills-radar/associate_project_manager_l4_ksb_full_mapping.json`
- `frontend/src/features/programme-suitability/__tests__/apmRadarFixtures.ts`
- `frontend/src/features/programme-suitability/__tests__/apmRadarAllocation.test.ts`
- `frontend/src/features/programme-suitability/__tests__/apmRadarUI.test.tsx`
- `frontend/src/features/programme-suitability/__tests__/apmRadarIntegration.test.tsx`
- `tools/verify_apm_radar_sources.py`
- This report.

Hash comparisons against task start confirm that PCP `allocation.ts`, MM `marketingManager.ts`, ME `marketingExecutive.ts`, `ModuleMasterDetail.tsx`, `types.ts` and `layout/registry.ts` are unchanged. Earlier uncommitted work was preserved. No other developer's step, shared stylesheet, Admin editor, backend source, model or migration was edited.

## Tests executed and actual results

- APM allocation: **11 passed** — exact 42/20 code mapping, full source wording, 31/26/5 types, 24/15/3 and 7/11/2 ownership, arbitrary existing IDs, stable keys and Admin edits/reordering, missing/duplicate/invalid mappings/profiles/modules and programme identity.
- APM UI: **7 passed** — two fixed modules, all descriptions/counts, one panel, no selectors/accordions/navigation writes, all nine ratings, notes/evidence, 42-to-43 boundary, old flat answers, Admin title/session display, read-only review and overlapping programme-scoped K1 answers.
- APM real-provider integration: **4 passed** — stale-board confirmed programme resolution, unchanged save envelope, missing-K6 completion error and 61-to-62 completion preserving historical rows.
- Other programme radar regressions: **73 passed** — PCP 29, MM 22, ME 22, preserving their allocations, counts, UI and persistence.
- Combined focused frontend: **95 passed** across 12 files.
- Broader programme suitability/users/onboarding frontend: **578 passed, 4 failed, 582 total**. The exact four failures match the previously recorded HEAD baseline listed below.
- Frontend lint, type-check, production build and Git whitespace check: passed.
- Backend programme suitability: **133 passed**.
- Focused backend Skills Radar scale, ILR learner/employer and service checks: **36 passed**.
- Source verifier: **62 exact records**, counts 42/20, zero wording/type/ownership mismatches, JSON byte-identical.

Relevant commands:

```powershell
npm --prefix frontend test -- src/features/programme-suitability/__tests__/apmRadar src/features/programme-suitability/__tests__/meRadar src/features/programme-suitability/__tests__/mmRadar src/features/programme-suitability/__tests__/pcpRadar --maxWorkers=2
npm --prefix frontend test -- src/features/programme-suitability src/pages/users src/pages/learner/onboarding --maxWorkers=2 --reporter=json --outputFile=.cache/apm-radar-regression.json
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api.tests_skills_radar_scale enrolment_api.tests_ilr_learner_details enrolment_api.tests_ilr_employer_details enrollment_service.tests
.\backend\.venv\Scripts\python.exe tools/verify_apm_radar_sources.py --reference-json "C:\Users\lenovo\Downloads\associate_project_manager_l4_ksb_full_mapping.json" --workbook "C:\Users\lenovo\Downloads\Associate_Project_Manager_Level_4_KSB_Mapping_Revised.xlsx"
```

## Chrome verification

Tested the actual WizardProvider, SkillsRadar and unchanged ModuleMasterDetail with a read-only export of the real published APM module configuration and actual 62-item profile. Intercepted API responses prevented writes to real learner records.

Verified desktop 1440px physical left/right positions, two vertically stable navigation cards/right-facing chevrons, RTL document/body plus row-reverse parent, single selected module and panel, all 62 full descriptions without duplication, existing APM K1 rating/note/evidence restoration independent of historical PCP K1, all nine rating options, required high-rating note, evidence filename selection, B3-to-K15 global question 42/43 transition, synchronized module highlight and global/module progress, explicit answer save/reload, historical answers/action plans preserved, Admin rename/Sessions edits retaining PMP's 42 KSBs, and mobile 390px navigation above details with complete long K4 wrapping/no horizontal overflow.

A separate completed learner fixture with all 62 existing profile IDs rated was reopened. It displayed 62/62 globally, 42/42 PMP and 20/20 AI, retained the original saved K1 note, and showed no Start/Continue assessment button. Loading it performed no answer writes.

Observed one profile request per page load, zero writes/profile/module fetches from module clicks, no browser page errors and no unexpected API requests. Persistence was verified with controlled responses, not a fully authenticated production endpoint end-to-end test.

Ignored evidence: `frontend/.cache/apm-radar-browser-results.json`, `apm-radar-regression.json`, `apm-radar-desktop.png`, `apm-radar-rtl.png`, `apm-radar-mobile.png`. Temporary browser harness files were removed and the isolated verification server was stopped.

## Remaining issues and merge coordination

No APM/PCP/MM/ME feature test fails. Four broader frontend baseline failures still expect the former 12-step wizard while the current branch already has 13 steps:

- `registers twelve default steps with suitability sixth and CV seventh`
- `Next Steps in the step rail is locked and not counted as complete before the earlier steps are done`
- `Next Steps in the step rail shows its number rather than a tick for staff, who are not gated`
- `Next Steps in the step rail does not count as complete just by being opened while earlier steps are unfinished`

They exactly match the recorded HEAD baseline and remain for coordination with their step owners. This task did not change registration/navigation to conceal them. An authenticated deployment smoke test remains outside the controlled browser test.

Potential merge-conflict files are SkillsRadar.tsx and WizardContext.tsx, plus the two updated radar UI test files. Preserve all four programme dispatches, profile IDs/caching, saved answer serialization and the revised APM grouping expectations when merging. The shared navigation/detail component itself was not modified.

Keep module/profile IDs stable and use the applicable configuration snapshot. Unknown custom module IDs require an explicit reviewed mapping rather than display-name guesses. No migration dependency or cross-programme data correction needs coordination.

Work stayed on the existing `skills-radar` branch. No commit, push, merge, rebase, reset or branch switch was performed. Existing uncommitted work and real learner records were preserved.
