# Training Plan Activities corrections — 18 November 2026

## Live Session titles — 19 November 2026

Only Live Session activity display titles now use the exact linked `weeks.title` already verified by the learner-specific module projection (`components.week_id = weeks.id`, within the same module). Other activity titles continue using `components.title`. Component IDs, original component titles, hours, dates and totals are retained. Blank week titles retain the original component title rather than inventing a name. Existing unissued drafts use their captured week titles without rereading LMS; the nested signing snapshot is kept identical to the preview. Issued/signed snapshots are untouched. Changed implementation: `training_plan_activities.py`; regression coverage: `test_training_plan_activities.py` (20 passing isolated workflow tests).

## Learner-facing diagnostics removed

The subsequent visibility correction uses `activities-overview-v4` for new captures and the presentation of existing unissued drafts. Technical `warnings` and `blockers` remain in frozen/admin data but are excluded from both PDF content and the protected document HTML. The existing preview modal hides its technical diagnostics by default; admin builder, editor and admin revision-history previews explicitly retain them. Learner and employer previews do not opt in.

Unissued draft preview upgrades only the presentation marker in a copied payload, without rereading sources or changing custom wording. Explicit finalization persists that same marker into the signing copy after the existing readiness check. Critical blockers still prevent issuance. Already issued/signed revisions and their saved artifacts are never upgraded or rewritten.

Verification: 18 isolated backend document tests; 68 focused frontend tests across PDF/editor/preview/admin-preview suites; TypeScript, scoped ESLint and production build. Tests cover hidden PDF and HTML diagnostics, admin visibility, preservation of frozen diagnostic data/custom wording, existing unissued draft preview/final output, historical behavior and blocked issuance for critical missing data.

## Subsequent requested restoration

The later request restores the original complete Reviews Schedule for new captures (`activities-overview-v3`): Monthly Coaching, Progress Review, Gateway, support plans and other applicable programme reviews all appear, chronologically and once per occurrence, with the original **Participants / Format** header. Missing type access no longer empties this complete schedule or blocks issuance by itself; it still prevents guessing module coaching placement. Verified participant roles remain usable, with unavailable details explicitly labelled. Module/meeting totals are unchanged. Existing v1/v2 snapshots retain their saved presentation. Verification: 16 isolated backend workflow tests and 6 focused frontend tests, plus TypeScript/scoped ESLint. The v2-specific discussion below records the previous correction, superseded only for the restored complete schedule and heading.

The existing design and official document lifecycle are retained. New captures use `activities-overview-v2`; existing v1 drafts, issued snapshots and signed artifacts retain v1 rendering. Resuming a saved custom draft does not refresh or replace its frozen source data. Create a new authorised revision to capture these corrections.

## Before / after

| Section | Previous output | New draft/revision output |
| --- | --- | --- |
| Activity OTJ | Raw `components.expected_otjh`, including zeros | Same source and totals. Verified zero stays `0h`; NULL/missing stays `—`; decimal hours use existing hours/minutes formatter. Media running time is not substituted for OTJ. |
| Activity dates | Recording dates or raw `dueTiming` text could appear | Only actual assignment deadlines and Monthly Coaching dates. Recording dates, other component dates and module-end fallbacks are excluded. |
| Assignment deadline | `End of week` could be printed as text | Explicit ISO date in `settings_json.dueTiming`, or the existing end-of-week rule using the verified Builder week/delivery/holiday calendar. Unresolvable dates show `—` and a source warning. |
| Module meetings | Every applicable review type could be inserted | Only authoritative type `mcm`, in exactly one verified module/week; placed before a later assignment deadline. Ambiguous coaching remains explicitly reported, never randomly assigned. |
| Reviews Schedule | Coaching, Progress, Gateway and support plans mixed together | Only authoritative type `progress_review`, even if its editable name differs. No Monthly Coaching rows in this table. Other types remain unchanged in Learning Plan Meetings and OTJ totals. |
| Final column | Always unavailable | Required signature roles from that specific template's signature flags through the existing `required_signature_roles` resolver and official role labels. These roles do not imply completed signatures. Missing access is explicit. |

Review classification uses `review_type_id` and the existing stable system type constants (`REVT-MCM`, `REVT-PROGRESS_REVIEW`), or a permitted custom type catalogue code. It never infers the type or signer requirements from an editable name.

The calendar enrichment passes exact verified module/programme/cohort/group identities to the approved LMS reader. It calls existing `scheduling_holidays_from`, `module_week_delivery_slots`, the Builder dated-schedule helper and authored-week deadline rule. It does not implement a competing scheduling engine or query through the application's privileged/default Curriculum connection. Unreadable holidays are never treated as an empty calendar.

## Actual read-only OTJ audit

Learner 1601's confirmed three-module plan was checked using the existing verified replacement resolver (no persisted changes). Counts of recorded zero values: Marketing Impact and Planning **167**, Social Media **128**, Marketing Technology (MarTech) **147**. Total **442** genuine recorded zeros; **0 NULL** values in these inspected components. This finding applies to these three modules, not every LMS programme.

| Actual activity | Source component ID | Raw `expected_otjh` (hours) | PDF display | Finding |
| --- | --- | --- | --- | --- |
| P1 - VID - Marketing Definitions | `COMP-202611171020325665VOELI` | `0.00` | `0h` | Verified zero. `durationMinutes=10` is media duration, not authored OTJ. |
| PPT: Marketing Definitions | `COMP-20261117102032566E6SNXD` | `0.00` | `0h` | Verified zero. |
| Podcast P1 | `COMP-202611171020325667Q98VM` | `0.00` | `0h` | Verified zero. `durationMinutes=20` is separate. |
| AI Assisted Learning P1 | `COMP-202611171020325664M9HZT` | `0.50` | `30m` | Recorded nonzero hours; existing formatter. |
| Impact&Planning Assignment 3 | `COMP-20261117102032578XK0NWA` | `8.00` | `8h` | Recorded nonzero hours. Due rule is `End of week`, not an explicit date. |

No real NULL example was found in that scope; isolated tests explicitly cover NULL → `—`, zero → `0h`, and media duration not replacing OTJ. Detailed safe audit output: repository `.cache/step7-corrections/source-audit.json`.

## DATABASE BLOCKERS — ACTION REQUIRED

Production **LMS**, schema **curriculum**, role **enrolment_modules_reader** currently lacks column SELECT access to:

| Table | Missing columns | Effect |
| --- | --- | --- |
| `review_templates` | `review_type_id` | Cannot verify Monthly Coaching versus Progress Review. New draft reports this; finalization is blocked rather than mixing review types. |
| `review_templates` | `signature_advisor`, `signature_employer`, `signature_participant`, `signature_referrer` | Cannot supply verified required signatory roles. Explicit unavailable state/warning, no guessed roles. |
| `groups` | `session_week_day`, `session_start_time`, `session_end_time` | Cannot verify the group's delivery pattern for authored week scheduling. |
| `holidays` | `id`, `label`, `start_date`, `end_date`, `notes`, `is_archived` | Cannot apply the actual organisation closure calendar. |
| `england_holidays` | `id`, `title`, `holiday_date` | Cannot apply the actual bank-holiday calendar. |

`review_types.id, code` also lack SELECT in the audit; these are needed only when a template references a custom type rather than one of the two existing system IDs. Module schedule inputs and cohort start/excluded-holiday fields inspected were readable. No grant, schema change, privileged credential fallback or permission bypass was attempted.

The inspected assignments have `dueTiming='End of week'`. Without the verified group/calendar inputs, a numerical week ordinal or module end date is insufficient to claim a submission date. The new draft reports this relationship blocker and displays `—`. Coaching projected dates still come exclusively from Step 6B's existing learner-start-date recurrence engine; placement waits for verified module/week ranges.

Existing source-data blockers, including the previously identified learner programme-end/module-end mismatch, remain subject to the existing finalization checks. No learner dates or source schedules were corrected by this work.

## Files changed by these corrections

- `backend/learner_api/training_plan_activities.py`
- `backend/enrollment_service/lms_activity_readonly.py` (document-only calendar enrichment)
- `backend/enrollment_service/lms_reviews_readonly.py` (document-only type/signature enrichment)
- `backend/enrollment_service/test_training_plan_sources.py` (new)
- `backend/enrolment_api/document_templates/test_training_plan_activities.py`
- `frontend/src/features/compliance-documents/trainingPlanActivities.ts`
- `frontend/src/features/compliance-documents/render.ts`
- `frontend/src/features/compliance-documents/pageDocument.ts`
- `frontend/src/features/compliance-documents/__tests__/trainingPlanActivities.test.ts`
- This report.

All other pre-existing uncommitted work is preserved.

## Verification and artifacts

- **15** isolated official document workflow tests passed: zero/NULL, exact due date, verified end-of-week deadline, unresolved dates, MCM chronological placement/deduplication, renamed typed Progress Review, per-programme roles, missing permissions, fixed/PCP plans, official preview/download/finalization/signature paths and historical protection.
- **53** source-reader/Step 6A/Step 6B regression tests passed, including **5 new** read-only enrichment tests. They verify scoped SELECT-only queries, reader guard, denied columns not queried, rollback/close, custom type code, and reuse of the existing calendar engine without hidden Django queries.
- **45** focused frontend PDF/editor tests passed. v1 snapshot behavior remains covered; v2 dates, missing hours and required-role labelling are tested.
- TypeScript, scoped ESLint, production frontend/shared PDF build and `git diff --check` passed.
- A valid isolated new revision was finalized/rendered for the corrected example. `.cache/step7-corrections/corrected.pdf` contains 8 pages, 12 distinct MCM occurrences inside their verified module weeks and 2 Progress Review rows at the end. Screenshots: `corrected-page-4.png` and `corrected-page-5.png`. The fixture's roles/data demonstrate successful integration when the required source access is available; they are not claims that production permission blockers have been resolved.

Module/meeting/plan OTJ calculations, Learning Plan storage, Programme Suitability, cohort/group allocation, timeline, document signature/history logic, live learner records, issued/signed PDFs, LMS data, permissions and feature flags were not modified. No migrations, commit or push were run.
