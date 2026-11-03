# PCP Level 6 Skills Radar: completion and verification

## Scope and Git safety

Completed on the existing `skills-radar` branch. The earlier module allocation implementation and its uncommitted work were already present and have been preserved. No commit, push, merge, rebase, reset or branch switch was performed.

Only the missing master-detail presentation, selection integration and relevant tests/documentation were changed in this pass. No backend production file, database record, saved module configuration, migration, other programme mapping or other wizard step was changed. No migration is required.

## Audit against every request section

| Section | State at audit | Action / verification |
| --- | --- | --- |
| 1. Master-detail layout | Missing: previous UI used accordions | Replaced with vertical module navigation physically left and one white question panel right. Explicit grid placement and local LTR prevent mirroring. On mobile, navigation precedes details. |
| 2. Selection interaction | Incomplete: no single active module | First module selected initially; clicks replace only details, retain navigation positions and never save/refetch answers. |
| 3. Saved Step 6 modules | Already implemented correctly | Retained confirmed PCP, savedSelection, stable module IDs, pinned version and complete-selection validation. Unsaved draft choices and recommendation alone do not select modules. |
| 4. Authoritative catalogue | Already implemented correctly | Reverified canonical JSON byte for byte and all descriptions/types/owners against the workbook. |
| 5. Operational allocation | Already implemented correctly | All three optional combinations pass deterministic redistribution. |
| 6. Strategic allocation | Already implemented correctly | All three optional combinations pass the same redistribution rule. |
| 7. Chartered allocation | Already implemented correctly | Four PMO children, AI and exactly the selected optional; parent excluded. Both optionals own the same 11 records. |
| 8. Complete descriptions | Already implemented correctly | Same original text in rows and popup; all 71 texts verified, including long K6, without truncation. |
| 9. Answer popup | Incomplete only at module boundary | Preserved nine scores/labels, note validation, evidence list, Cancel and global sequence. Opening/advancing a KSB now selects its owning module. |
| 10. Persistence | Already implemented correctly | Reused profile IDs and answer envelope; ratings, notes, evidence filenames, action plans and historical responses survive saves/regrouping. |
| 11. Progress/completion | Already implemented correctly | Counts retained; added explicit 70-to-71 completion test proving historical ratings cannot substitute for current responses. |
| 12. Mapping validation | Already implemented correctly | Eight combinations each contain 71 unique codes and IDs; invalid catalogue/profile/module/version errors remain visible and block completion. |
| 13. Performance | Already implemented correctly | Navigation stays local state: zero writes/fetches on module clicks, one shared profile request per load. |
| 14. Other programmes | Already implemented correctly | Non-PCP flat renderer, popup and completion retained. Existing programme/wizard regressions exercised. |
| 15. Tests | Incomplete for new layout | Updated accordion assertions and added boundary, continue and completion tests; verified browser geometry, RTL, mobile and refresh. |
| 16. Git safety | Already satisfied | Branch/status inspected; preserved uncommitted work; shared-file risks listed below. |
| 17. Reporting | Pending | This report records the audit, changes, coverage, checks and limitations. |

## Changes made

`ModuleMasterDetail.tsx` replaces the feature's former `ModuleAccordions.tsx`. Navigation shows module name, answered/assigned count, right-facing chevron and purple selection. The label fills the button width so shared icon-button CSS cannot pull the chevron toward the centre. One detail panel reuses the existing KSB row renderer.

`SkillsRadar.tsx` adds local selected-module state and an ID-to-owner lookup. Existing `open()` selects the question's owner, so Answer, Continue assessment and Confirm & Next select the correct module. The popup remains associated with the existing profile ID if saved grouping changes.

No ownership, scoring, saving, Step 6 confirmation, recommendation, navigation registration or shared stylesheet changes were needed in this pass.

## KSB coverage and redistribution

Each combination has exactly 71 unique existing profile IDs and codes with complete canonical descriptions. Source data is never mutated.

| Pathway | Selected optionals | Counts in module display order |
| --- | --- | --- |
| Operational | Planning + EVM | PMP 16; AI 12; Risk 9; Planning 20; EVM 14 |
| Operational | Planning + PMI-SP | PMP 16; AI 12; Risk 9; Planning 23; PMI-SP 11 |
| Operational | EVM + PMI-SP | PMP 16; AI 12; Risk 9; EVM 20; PMI-SP 14 |
| Strategic | PMO + Portfolio | PMP 16; AI 12; Risk 9; PMO 20; Portfolio 14 |
| Strategic | PMO + MSP | PMP 16; AI 12; Risk 9; PMO 23; MSP 11 |
| Strategic | Portfolio + MSP | PMP 16; AI 12; Risk 9; Portfolio 20; MSP 14 |
| Chartered | Portfolio | Planning 21; Stakeholders 7; Risk/Quality 11; PMO Office 9; AI 12; Portfolio 11 |
| Chartered | EVM | Planning 21; Stakeholders 7; Risk/Quality 11; PMO Office 9; AI 12; EVM 11 |

Redistribution retains original ownership in the JSON and assigns excluded optional KSBs round-robin in canonical K/S/B order. Recipient priority PPC, FINANCE, SCHEDULE is independent of selection-array order, editable titles or display order. Chartered does not redistribute.

Source verification: 31 Knowledge, 29 Skill, 11 Behaviour; all descriptions and owners match workbook sheets `KSB source` and `Coverage`. Canonical JSON SHA-256: `66ab531129d68292e9d0cd443a2949cffd982897031f4d398e34d4e873b80b40`. The separately named `Pasted text(20261102-112959).txt` was unavailable; the JSON and workbook independently matched every record, so no wording was invented.

## Test and browser results

- Final focused Vitest: **29 passed** — 16 allocation, 9 UI, 4 real-provider integration tests.
- Broader frontend regression: **511 passed / 515 collected**, with four known baseline failures below. The final additional completion test passed in the focused run.
- Frontend type-check, lint, production build and Git whitespace check: passed.
- Django programme suitability: **133 passed**.
- Focused backend Skills Radar scale, ILR learner/employer and service regressions: **36 passed**.
- Source verifier: all 71 exact descriptions/types/owners match; JSON is byte-identical to the supplied file.

Chrome tested the actual WizardProvider and SkillsRadar with an existing read-only export of published configuration version 11 and the actual 71-item profile. API interception prevented writes to real learner records. Persistence/serialization were verified with controlled responses; this was not a complete authenticated production API test.

Browser checks passed:

1. At 1440px, navigation physically left and the larger detail panel right.
2. RTL document/body and a flex row-reverse parent do not mirror the layout.
3. Navigation retains identical document coordinates while switching panels; chevrons align right.
4. One active module/panel, first module initially selected, no accordions or questions beneath navigation cards.
5. All 71 complete descriptions visited in Operational and both Chartered optional combinations, without duplicates/missing codes.
6. Zero writes, profile reloads or module reloads on navigation clicks; one profile request per load.
7. Existing nine-option popup, notes and evidence filename workflow work. Confirm & Next crosses question 16 to 17 and highlights AI.
8. Global/module progress update together. Notes, evidence filenames and historical responses survive explicit save, reload and Operational-to-Chartered regrouping.
9. At 390px navigation sits above details, K6 wraps fully, Answer remains usable, no horizontal overflow.
10. No browser page errors or unexpected API requests.

The 26 requested test areas are covered by allocation tests (selection, descriptions, all combinations and uniqueness), UI/provider tests (popup, notes/evidence, boundary/continue, progress and completion), browser checks (physical layout, RTL, stable navigation and refresh), broader wizard regression and lint/type-check/build.

## Files and merge coordination

Changed in this completion pass:

- `frontend/src/features/programme-suitability/skills-radar/ModuleMasterDetail.tsx` — replaces the untracked accordion component.
- `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` — module selection and popup-owner integration; the only shared production file additionally edited this pass.
- `frontend/src/features/programme-suitability/__tests__/pcpRadarUI.test.tsx` — layout, boundary and continue checks.
- `frontend/src/features/programme-suitability/__tests__/pcpRadarIntegration.test.tsx` — panel switching and 71-answer completion checks.
- This report and the supersession notice in `skills-radar/IMPLEMENTATION_REPORT.txt`.

Preserved earlier uncommitted production changes relative to HEAD:

- `frontend/src/pages/users/types.ts` — optional derived `skillsRadar.configurationError`.
- `frontend/src/pages/users/wizard/WizardContext.tsx` — confirmed programme lookup, allocation validity and exclusion of derived metadata from saved answers.
- `frontend/src/pages/users/wizard/layout/registry.ts` — existing Skills Radar completion surfaces configuration errors. No steps added, removed or reordered.
- `SkillsRadar.tsx` — previous grouping, complete wording, stable answer identity and responsive rows.
- Feature-specific `allocation.ts`, canonical JSON, fixtures, allocation tests and `tools/verify_pcp_radar_sources.py`.

Coordinate merges in the four shared files, especially SkillsRadar open/render handling and WizardContext profile lookup/serialization. Preserve other developers' step registration and the current 13-step order. Do not persist allocation/module UI state, configurationError or programmeKsbIds, replace profile IDs, or drop historical answers. Feature files isolate the rest. There are no migration dependencies.

Published version 11 contains Strategic Risk mandatory/PMO optional. Earlier backend seed defaults differ and were not rewritten. Custom/historical snapshots with missing/unknown owner modules require configuration review; existing errors are surfaced instead of inventing assignments.

## Remaining issues and evidence

No failing PCP feature test remains. Four broader frontend failures exactly match the previously recorded HEAD baseline:

- `registers twelve default steps with suitability sixth and CV seventh`
- `Next Steps in the step rail is locked and not counted as complete before the earlier steps are done`
- `Next Steps in the step rail shows its number rather than a tick for staff, who are not gated`
- `Next Steps in the step rail does not count as complete just by being opened while earlier steps are unfinished`

They expect the former 12-step layout/progress while this branch has 13 steps. They remain for coordination with step owners. The earlier implementation report also records unrelated broad-backend baseline failures; the relevant 169 backend tests above were rerun and passed this pass.

Existing evidence storage records filenames in the Skills Radar answer envelope. That workflow was preserved, not replaced with blob-upload storage. An authenticated deployment smoke test remains outside controlled browser verification.

Ignored evidence: `frontend/.cache/pcp-radar-master-detail-regression.json`, `pcp-radar-head-baseline.json`, `pcp-radar-master-detail-browser-results.json` and desktop/RTL/mobile `pcp-radar-master-detail-*.png` screenshots. Temporary browser harness files were removed and the isolated verification server was stopped.
