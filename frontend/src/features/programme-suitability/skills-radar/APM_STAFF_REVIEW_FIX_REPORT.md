# APM admin Skills Radar parity fix

The reported admin screenshot showed an assigned APM learner whose Programme Suitability step was Not started. The old implementation activated grouped APM rendering only after programme confirmation. Staff can open later wizard steps before confirmation, so that case fell back to the old flat list even though all 62 descriptions existed. Earlier tests covered confirmed APM staff review, not this unconfirmed assigned-programme case.

## Fix

- Unconfirmed **read-only staff** review now recognizes the learner's assigned APM programme and reads its two modules from the existing published wizard configuration.
- It reuses the same 42/20 allocation, question rows, ModuleMasterDetail and read-only View modal as confirmed APM review.
- It does not fabricate/confirm an assessment, mark Step 6 complete, change learner gating or write answers/module choices.
- It avoids the confirmation-dependent learner modules endpoint, which can pin a snapshot. Published metadata is read from the existing layout response instead.
- Confirmation loading settles before choosing published fallback metadata. A confirmed programme choice overrides a stale APM board; confirmed APM continues using its pinned snapshot.
- Missing published module metadata or assessment load errors are surfaced, rather than silently rendering a flat/partial review.
- Ratings, notes, evidence filenames, action plans and historical answer IDs remain untouched. Staff cannot edit ratings or confirm answers.

The learner flow and other programme mappings remain unchanged. No backend, database record, migration, Admin configuration, shared stylesheet or step-registration change was needed.

## Files changed

- `frontend/src/features/programme-suitability/skills-radar/associateProjectManager.ts` — assigned APM identification for staff review only; allocation and canonical text unchanged.
- `frontend/src/pages/users/wizard/WizardContext.tsx` — published read-only review snapshot, profile loading and existing validation handling.
- `frontend/src/pages/users/wizard/steps/SkillsRadar.tsx` — enable the existing grouped renderer for that staff case.
- `frontend/src/features/programme-suitability/__tests__/apmStaffRadar.test.tsx` — six real-provider regression tests.
- This report and a reference from the earlier APM implementation report.

All previous uncommitted work was preserved on `skills-radar`; no commit, push, merge, rebase, reset or branch switch was performed. Potential merge conflicts are in WizardContext.tsx, SkillsRadar.tsx and the APM helper. Preserve the staff-only fallback alongside the existing confirmed programme/pinned snapshot logic when merging.

## Verification

Six regression scenarios passed: unconfirmed assigned APM staff review with saved read-only answers and zero writes; no fallback for learners; delayed confirmation using the pinned APM snapshot; assigned review when no assessment is configured; a confirmed alternative overriding a stale APM board; clear failure for missing published module metadata.

- Combined radar suites: **101 passed** across 13 files, including the existing 95 APM/PCP/MM/ME tests.
- Frontend type-check, lint and production build: passed.
- Git whitespace check: passed.
- Broader frontend regression: **584 passed, 4 failed, 588 total**; the failures match the recorded HEAD baseline.

Chrome exercised the actual **staff WizardShell**, WizardProvider and Skills Radar with an assigned APM board, a Not started assessment and the existing version-11 published configuration/62-item profile. It showed the two modules physically left and one 42/20 question panel right while Step 6 remained unconfirmed. All nine rating controls were disabled; the note was read-only and existing notes/evidence reloaded correctly. Observed **zero writes**, **zero learner-modules endpoint requests**, one profile request per load, no page errors and no unexpected requests.

Browser APIs were intercepted to protect real learner data; this is not a fully authenticated test against the specific account in the screenshot. The isolated browser harness/server was removed/stopped after verification.

Ignored local evidence: `frontend/.cache/apm-staff-browser-results.json`, `apm-staff-fixed-desktop.png`, `apm-staff-radar-regression.json`.

The four unchanged baseline failures retain old 12-step assumptions on a branch with 13 steps:

- `Next Steps in the step rail does not count as complete just by being opened while earlier steps are unfinished`
- `Next Steps in the step rail is locked and not counted as complete before the earlier steps are done`
- `Next Steps in the step rail shows its number rather than a tick for staff, who are not gated`
- `registers twelve default steps with suitability sixth and CV seventh`
