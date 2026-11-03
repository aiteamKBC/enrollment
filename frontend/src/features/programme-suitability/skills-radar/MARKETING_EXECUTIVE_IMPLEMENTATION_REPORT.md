# Marketing Executive Level 4 module-based Skills Radar

## Delivered behaviour

ME L4 now uses the existing PCP/MM Skills Radar master-detail interface: three fixed module cards on the left, one selected module's complete KSB questions on the right. First module is selected initially. Clicking another replaces only the details; navigation positions, answers and global progress remain stable. Mobile navigation appears above the details.

The source mapping is fixed and versioned as `source-workbook-2026-11-02`. All three modules and 33 KSBs are included automatically. There are no pathways, optional choices, credit calculations, redistribution or module-selection writes for ME.

## Exact mapping

| Existing module ID | Published title at verification | Sessions | KSBs | Exact assigned codes |
| --- | --- | --- | --- | --- |
| `marketing-impact` | Marketing Impact and Planning | 16 | 14 | K1 K2 K4 K5 K6 K7 K8 K9 K10 S2 S6 S9 B6 B8 |
| `social-media` | Social Media | 14 | 10 | K11 S1 S3 S4 S5 S7 S8 B1 B3 B7 |
| `martech` | Marketing Technology (MarTech) | 14 | 9 | K3 S10 S11 S12 S13 S14 B2 B4 B5 |

Total: **33 unique KSBs** — 11 Knowledge (K1–K11), 14 Skills (S1–S14), 8 Behaviours (B1–B8). The per-module category counts are respectively 9/3/2, 1/6/3 and 1/5/3.

`marketingExecutive.ts` binds immutable source groups to the existing published module IDs. Names and Sessions are read from the applicable published/pinned Step 6 modules configuration, not from reference titles or session metadata in the source JSON. Admin renaming, session edits and display reordering do not change ownership. The JSON is reference KSB data, not a second live module catalogue.

Validation checks expected codes/categories, non-empty complete descriptions, unique profile IDs, one owner per code, exact 14/10/9 counts and existence of all three published modules. Missing/duplicate/ambiguous codes or assignments identify affected codes. A missing published module reports its ID and affected codes. Invalid mapping never silently drops questions or falls back to code-only labels. Existing derived configuration-error handling is reused without changing wizard completion predicates or registering/reordering steps.

## Source and database verification

The checked-in `marketing_executive_l4_ksb_full_mapping.json` is byte-identical to the supplied JSON. All 33 descriptions, categories and owners match the available workbook `C:\Users\lenovo\Downloads\Marketing_Executive_Level_4_KSB_Mapping.xlsx`, sheets KSB source, Coverage and Module map. The available filename lacks `(1)` from the prompt, but all source records match.

- JSON SHA-256: `cfd4ef39834626147f43f556ce79db3da8dea1fa78b2e90a993b8c2237e3f9ff`
- Workbook SHA-256: `5b85d872094f88f8538f2477d99bf56c15e822b398a495ed5a5d35c446d93286`

Full wording, punctuation and spelling are preserved, including K1's `7P’S`, S2's complete SMART expansion and B8's equality/diversity text. Both rows and the existing Answer popup display the same canonical description.

Read-only inspection through the existing Django connection confirmed that the database already contains the same 33 complete descriptions under `standard:st0596-v1-0`, with existing IDs such as `st0596-v1-0-K1`. Published module version 11 already contains the required stable IDs and sessions. No duplicate records, schema change, migration, seed or correction of real learner data was needed. No environment variables were changed.

## Existing components and answers preserved

- Reused `ModuleMasterDetail` unchanged, including chevrons, purple selection, explicit LTR/grid placement and optional published Sessions display.
- Reused existing `SkillsRadar` question rows, Answer/Edit/View buttons, nine-point rating labels/scores, high-rating note validation, notes, evidence handling, Cancel, read-only staff review and Confirm & Next.
- Existing global sequence and owner selection now include ME. Confirming question 14 (B8) advances to question 15 (K11) and selects Social Media automatically. Global numbering does not restart per module.
- Existing WizardProvider cached profile loading, seeding and save serialization remain the persistence mechanism. Confirmed ME catalogue lookup also handles a stale board programme; no new learner choice or programme-confirmation rule was added.
- Original profile IDs remain answer keys. The grouping does not manufacture IDs or rewrite ratings, notes, evidence filenames, action plans or historical responses. A test verifies arbitrary existing IDs as well as the current database IDs.
- Global progress remains X of 33 and module progress X of 14/10/9. Existing completion checks still require all current responses, ignoring unrelated historical rows. No new answer-storage or step-completion architecture was introduced.

The existing evidence workflow stores filenames in the Skills Radar answer envelope. This task preserves that implementation; it does not add a new blob-upload/storage system.

## Files created or modified

Shared production files additionally edited in this task:

1. `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` — ME profile/grouping dispatch into the same renderer and popup.
2. `frontend/src/pages/users/wizard/WizardContext.tsx` — confirmed ME profile lookup and existing derived mapping validity handling.

Existing test updated:

- `frontend/src/features/programme-suitability/__tests__/mmRadarUI.test.tsx` — removed the former assertion that confirmed ME uses a flat list; ME now intentionally has its own grouped tests. The APM flat-renderer regression remains. MM production mapping/data were not changed.

New files:

- `frontend/src/features/programme-suitability/skills-radar/marketingExecutive.ts`
- `frontend/src/features/programme-suitability/skills-radar/marketing_executive_l4_ksb_full_mapping.json`
- `frontend/src/features/programme-suitability/__tests__/meRadarFixtures.ts`
- `frontend/src/features/programme-suitability/__tests__/meRadarAllocation.test.ts`
- `frontend/src/features/programme-suitability/__tests__/meRadarUI.test.tsx`
- `frontend/src/features/programme-suitability/__tests__/meRadarIntegration.test.tsx`
- `tools/verify_me_radar_sources.py`
- This report.

Pre-existing uncommitted work was preserved. Hash comparison against task start confirms no changes to PCP `allocation.ts`, MM `marketingManager.ts`, shared `ModuleMasterDetail.tsx`, `types.ts` or `layout/registry.ts`. No backend model/service/migration, Admin editor, stylesheet, other developer's wizard step or navigation registration was edited.

## Tests and actual results

- ME allocation: **11 passed** — exact ownership, 33 unique IDs/codes/full texts, 11/14/8 categories, arbitrary existing IDs, published rename/session/reorder handling, invalid/missing/duplicate source/profile/module data, no selection or redistribution.
- ME UI: **7 passed** — three fixed modules and complete 14/10/9 panels, one active module, initial selection, no selectors/accordions, zero navigation writes/fetches, all nine rating options, notes/evidence, boundary navigation, old flat answers, Admin names/sessions, read-only review and APM flat renderer.
- ME real-provider integration: **4 passed** — stale-board confirmed programme resolution, existing answer envelope, missing-K6 configuration failure and 32-to-33 completion preserving historical responses.
- MM regression: **22 passed** — unchanged fixed mapping/42 questions, existing UI and persistence.
- PCP regression: **29 passed** — unchanged 71 questions, all eight optional/pathway combinations, deterministic redistribution, UI and persistence.
- Combined focused frontend: **73 passed** across nine test files.
- Broader frontend regression: **556 passed, 4 failed, 560 total**. The failures exactly match the previously recorded HEAD baseline below.
- Frontend lint, type-check, production build and Git whitespace check: passed.
- Backend programme suitability suite: **133 passed**.
- Backend Skills Radar scale, ILR learner/employer and service regressions: **36 passed**.
- Source verifier: **33 exact records**, counts 14/10/9, no wording/category/owner mismatch, JSON byte-identical.

Relevant commands:

```powershell
npm --prefix frontend test -- src/features/programme-suitability/__tests__/meRadar src/features/programme-suitability/__tests__/mmRadar src/features/programme-suitability/__tests__/pcpRadar --maxWorkers=2
npm --prefix frontend test -- src/features/programme-suitability src/pages/users src/pages/learner/onboarding --maxWorkers=2 --reporter=json --outputFile=.cache/me-radar-regression.json
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api.tests_skills_radar_scale enrolment_api.tests_ilr_learner_details enrolment_api.tests_ilr_employer_details enrollment_service.tests
.\backend\.venv\Scripts\python.exe tools/verify_me_radar_sources.py --reference-json "C:\Users\lenovo\Downloads\marketing_executive_l4_ksb_full_mapping.json" --workbook "C:\Users\lenovo\Downloads\Marketing_Executive_Level_4_KSB_Mapping.xlsx"
```

## Browser verification

Chrome tested the actual WizardProvider, SkillsRadar and shared master-detail component with a read-only export of the real version-11 ME module configuration and actual 33-item profile. Controlled API responses intercepted writes to avoid modifying real learner records.

Verified desktop 1440px physical left navigation/right details, vertical card positions/chevron alignment, RTL document/body plus a flex row-reverse parent, one active panel, all 33 unique complete texts, original S14 answer/notes/evidence, nine rating choices, required high-rating note, B8-to-K11 question 14/15 transition and matching module highlight, global/module progress, explicit save/reload of a new answer, preserved historical responses/action plan, Admin rename/Sessions edits retaining all 14 Impact KSBs, and mobile 390px navigation above details with full K1 wrapping/no horizontal overflow.

Observed one profile request per page load, zero writes or profile/module reloads from module clicks, no page errors and no unexpected API requests. This verifies frontend rendering and persistence with controlled responses, not a fully authenticated production API end-to-end test.

Ignored local evidence: `frontend/.cache/me-radar-browser-results.json`, `me-radar-regression.json`, `me-radar-desktop.png`, `me-radar-rtl.png`, `me-radar-mobile.png`. Temporary browser harness files were removed and the isolated port-3001 verification server was stopped.

## Remaining issues and merge coordination

Four broader frontend tests retain old 12-step assumptions while the current branch already has 13 steps. Their exact names match the recorded HEAD baseline:

- `Next Steps in the step rail does not count as complete just by being opened while earlier steps are unfinished`
- `Next Steps in the step rail is locked and not counted as complete before the earlier steps are done`
- `Next Steps in the step rail shows its number rather than a tick for staff, who are not gated`
- `registers twelve default steps with suitability sixth and CV seventh`

These existing failures remain for coordination with the step owners; this task did not alter their navigation or step registration.

Potential merge-conflict files are SkillsRadar.tsx and WizardContext.tsx. Preserve the ME dispatch alongside MM/PCP dispatch, existing profile cache/ID handling and exclusion of derived metadata from saved answers. No step order or completion-predicate edits need merging. The shared master-detail component itself was not changed.

Keep stable module/profile IDs and the applicable configuration snapshot when integrating with teammates. Custom IDs require an explicit reviewed ownership mapping; missing published owners produce errors rather than guessing from display names. No migration dependency or database coordination is required.

Work remained on the existing `skills-radar` branch. No commit, push, merge, rebase, reset or branch switch was performed. No failing ME/MM/PCP feature test remains. An authenticated deployment smoke test remains outside the controlled browser verification.
