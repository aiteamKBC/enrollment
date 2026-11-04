# Marketing Manager Level 6 module-based Skills Radar

## Delivered behaviour

MM L6 now uses the existing PCP master-detail component: four fixed module cards on the left and one full-description KSB panel on the right. First module is selected initially; clicking another replaces only the panel. Mobile navigation sits above details. The same purple/white styling, progress indicators and Answer/Edit/View rows are reused.

MM has exactly 42 unique KSBs, with no pathways, optional choices, redistribution, credits or module-selection writes. Existing programme confirmation and wizard navigation rules are unchanged. The selected confirmed programme, rather than an assessment recommendation alone or stale board programme, determines the MM profile.

## Mapping and configuration

| Existing module ID | Published title at verification | Sessions | KSBs | Exact assigned codes |
| --- | --- | --- | --- | --- |
| `marketing-strategy` | Marketing Strategy and Planning | 16 | 16 | K1 K2 K5 K6 K7 K9 K13 S1 S3 S4 S5 S7 S10 S17 B2 B6 |
| `customer-journey` | Customer Journey Optimisation | 14 | 9 | K3 K4 K14 S2 S8 S15 B5 B7 B8 |
| `commercial-intelligence` | Commercial Intelligence | 14 | 11 | K8 K10 K11 S6 S9 S11 S12 S13 S14 S16 B4 |
| `ai-marketing` | AI in Marketing | 14 | 6 | K12 S18 S19 B1 B3 B9 |

Total: **42**, comprising K1–K14 (14 Knowledge), S1–S19 (19 Skills), B1–B9 (9 Behaviour). Each code has exactly one owner.

The supplied immutable source groups are bound to these existing IDs in `marketingManager.ts`. Published module lookup uses IDs, never editable configuration titles or positions. Source module names are checked only for internal source-data consistency. Admin renames, session changes and display reordering do not change KSB ownership.

Display titles and sessions come from the existing modules controller's applicable published/pinned configuration, just as in Step 6. The supplied JSON's title/session metadata is reference material, not a second live modules catalogue. No Admin editor, Draft/Publish workflow or module configuration was changed.

Validation identifies missing/duplicate/unknown codes, ambiguous profile IDs, missing descriptions, inconsistent assignments, incorrect group sizes and missing published module IDs. Missing assignment errors name the omitted code. Invalid MM mapping does not render a partial list or silently fall back to bare-code questions. Existing derived configurationError handling is reused; wizard completion predicates and step registration are unchanged.

## Authoritative text and existing records

The checked-in `marketing_manager_l6_ksb_full_mapping.json` is byte-identical to the supplied file. All 42 category/description/owner records match the available workbook `C:\Users\lenovo\Downloads\Marketing_Manager_Level6_KSB_Mapping.xlsx`, sheets KSB source, Coverage and Module map. The available filename lacks `(1)` from the prompt, but its entire mapping and text match the supplied JSON.

Original punctuation/spelling remain unchanged, including S14: `Construct a robust buness case in support of marketing activities/plans`.

- JSON SHA-256: `58556b8f5cc586c91b9216de507079c0851903256102c4aa73a8735b3c72a616`
- Workbook SHA-256: `81a506e337f32be17d39c19bcf1a790835b21224880f152840b7fbad68e55346`

Read-only inspection using the application's existing Django connection confirmed that the database already has these same 42 full descriptions under `standard:st0612-v1-0`, with IDs such as `st0612-v1-0-K1`. The published modules snapshot was version 11. No duplicate KSB records, migration, seed, data correction or real learner write was necessary. Inspection followed the installed Neon/Postgres skill and did not provision infrastructure or change environment variables.

The frontend binds source wording to the existing returned profile IDs. It does not manufacture IDs from codes, positions or module membership; a test also verifies arbitrary existing database IDs. Ratings, notes, evidence filenames, action plans and historical responses remain in the existing `skillsRadar.assessments` envelope.

## Reused components and persistence

- `ModuleMasterDetail` provides the same navigation/detail layout as PCP. Its only extension is optional published Sessions metadata, supplied for MM; PCP does not receive it.
- `SkillsRadar` retains the existing nine-point scale, rating labels/scores, high-rating note validation, evidence workflow, Cancel, editing and read-only staff review.
- Existing `open()` and Confirm & Next logic use the resolved global question sequence; crossing question 16/17 automatically selects Customer Journey Optimisation. Global numbering does not restart per module.
- Existing WizardProvider profile loading, answer seeding, cached requests and save serialization are reused. MM confirmed catalogue lookup also handles a stale board programme.
- Existing completion checks still require every current KSB response and ignore unrelated historical IDs. MM global progress remains X of 42; module counts are 16/9/11/6. Derived grouping/configuration metadata is never stored as an answer.
- Switching modules performs no answer writes, module-selection writes or catalogue reloads.

Existing evidence handling stores filenames in the answer envelope. This task preserves that workflow; it does not introduce a new blob-storage/upload implementation.

## Files changed in this task

Shared files additionally edited:

1. `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` — add MM resolution and feed the existing grouped row/modal renderer.
2. `frontend/src/pages/users/wizard/WizardContext.tsx` — confirmed MM profile lookup and reuse existing derived configuration validity handling.
3. `frontend/src/features/programme-suitability/skills-radar/ModuleMasterDetail.tsx` — optional session count display; PCP data/rendering contract remains compatible.

New files:

- `frontend/src/features/programme-suitability/skills-radar/marketingManager.ts`
- `frontend/src/features/programme-suitability/skills-radar/marketing_manager_l6_ksb_full_mapping.json`
- `frontend/src/features/programme-suitability/__tests__/mmRadarFixtures.ts`
- `frontend/src/features/programme-suitability/__tests__/mmRadarAllocation.test.ts`
- `frontend/src/features/programme-suitability/__tests__/mmRadarUI.test.tsx`
- `frontend/src/features/programme-suitability/__tests__/mmRadarIntegration.test.tsx`
- `tools/verify_mm_radar_sources.py`
- This report.

Earlier uncommitted PCP files, tests, type additions and registry validation were preserved. In particular, PCP `allocation.ts` is byte-identical to its contents at the start of this task. No backend source/model/migration, other developer's wizard step, stylesheet, admin component or navigation registration was edited.

## Tests and actual results

- MM allocation: **11 passed** — exact code assignments and complete wording, arbitrary existing IDs, published renames/sessions/reordering, no-selection operation, missing/duplicate/invalid mapping/profile validation and confirmed programme identity.
- MM UI: **8 passed** — all four modules/counts/text, single active detail panel, no selectors, zero navigation saves/fetches, all nine original rating options, note/evidence and boundary navigation, old flat answers, published rename/session display, read-only review, ME/APM flat renderer preservation.
- MM real-provider integration: **4 passed** — stale board lookup, preserved answer envelope, missing-K6 completion error and 41-to-42 completion ignoring historical rows.
- Existing PCP radar suites: **29 passed** — all eight pathway combinations, 71 unique KSBs, original allocation/redistribution, popup/persistence/progress behaviour.
- Final combined focused run: **52 passed** across six test files.
- Broader programme-suitability/users/onboarding frontend regression: **535 passed, 4 failed, 539 total**. The exact four failures match the previously recorded HEAD baseline, listed below. The later precise missing-assignment-message refinement was rechecked in the focused suites.
- Frontend type-check, lint, production build and Git whitespace check: passed.
- Backend programme suitability suite: **133 passed**.
- Backend Skills Radar scale, ILR learner/employer and service checks: **36 passed**.
- Source verifier: **42 exact records**, counts 16/9/11/6, zero text/category/assignment mismatches; canonical JSON byte-identical.

Relevant commands:

```powershell
npm --prefix frontend test -- src/features/programme-suitability/__tests__/mmRadar src/features/programme-suitability/__tests__/pcpRadar --maxWorkers=2
npm --prefix frontend test -- src/features/programme-suitability src/pages/users src/pages/learner/onboarding --maxWorkers=2 --reporter=json --outputFile=.cache/mm-radar-regression.json
npm --prefix frontend run type-check
npm --prefix frontend run lint
npm --prefix frontend run build
.\backend\.venv\Scripts\python.exe backend/manage.py test programme_suitability --settings=programme_suitability.test_settings --noinput
.\backend\.venv\Scripts\python.exe tools/run_backend_checks.py enrolment_api.tests_skills_radar_scale enrolment_api.tests_ilr_learner_details enrolment_api.tests_ilr_employer_details enrollment_service.tests
.\backend\.venv\Scripts\python.exe tools/verify_mm_radar_sources.py --reference-json "C:\Users\lenovo\Downloads\marketing_manager_l6_ksb_full_mapping.json" --workbook "C:\Users\lenovo\Downloads\Marketing_Manager_Level6_KSB_Mapping.xlsx"
```

## Browser verification

Chrome exercised the actual WizardProvider, shared master-detail component and SkillsRadar using the read-only export of the real published MM modules and actual 42-item profile. Controlled API responses intercepted learner writes to avoid changing live records.

Passed: four vertically stacked modules; physical left navigation/right details; RTL plus a row-reverse parent does not mirror; stable navigation coordinates; only one active module/panel; all 42 complete descriptions across the four panels; original S14 wording; no module/pathway selectors; right-side Answer buttons; nine rating options and required high-rating note; evidence filename selection; question 16/17 boundary; global/module progress; old flat-layout rating/note/evidence/action-plan restoration; save/reload of a new answer; historical rows retained; admin rename/session changes retain all 16 Strategy KSBs; mobile navigation above content and full wrapping without horizontal overflow.

Observed one profile request per load, zero profile/module fetches or writes from module clicks, and no page errors/unexpected requests. Browser persistence uses controlled API fixtures, not a fully authenticated production endpoint test.

Ignored evidence: `frontend/.cache/mm-radar-browser-results.json`, `mm-radar-regression.json`, `mm-radar-desktop.png`, `mm-radar-rtl.png`, `mm-radar-mobile.png`. Temporary browser harness files were removed and the isolated port-3001 verification server was stopped.

## Remaining issues and merge coordination

No MM or PCP feature test failure remains. Four broader frontend baseline failures concern the old 12-step assumptions on a branch that already has 13 steps:

- `registers twelve default steps with suitability sixth and CV seventh`
- `Next Steps in the step rail is locked and not counted as complete before the earlier steps are done`
- `Next Steps in the step rail shows its number rather than a tick for staff, who are not gated`
- `Next Steps in the step rail does not count as complete just by being opened while earlier steps are unfinished`

They were left unchanged for coordination with their owners. An authenticated deployment smoke test remains outside the intercepted browser test.

Potential merge conflicts are in SkillsRadar.tsx, WizardContext.tsx and the reused ModuleMasterDetail.tsx. Preserve both MM resolution and existing PCP resolution when merging, including confirmed profile lookup and exclusion of derived metadata from saved answers. Keep the existing stable module/profile IDs and applicable configuration snapshot. The earlier uncommitted types.ts/registry.ts changes belong to the PCP work and should also be preserved, though neither was additionally edited here.

Work remained on the existing `skills-radar` branch. No commit, push, merge, rebase, reset or branch switch was performed. No migration dependency or database coordination is needed for this change.
