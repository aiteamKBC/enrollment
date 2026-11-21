# Compact weekly Training Plan — 19 November 2026

The existing official Training Plan renderer now supports `activities-overview-v5` for new drafts and future authorised revisions. It retains the existing KBC/DfE branding, protected native Learning Plan block, document preview/download, mandatory sections, revision/signature services and immutable issued artifacts. No competing generator or storage was introduced.

## Presentation

- Dark purple numbered module headings, recorded description and covered KSBs.
- Every actual Week record is captured in its original source order with stable ID, authored week number/title/order, including empty weeks. No week is derived from a component's name or from a made-up calendar date.
- Each week has a lavender heading and a compact three-column table: Learning Activity, OTJ Hours, Planned Date / Month. Small portable vector icons identify activity types.
- Video, PowerPoint/PPT/presentation and Podcast components with verified zero or missing OTJ move into that same week's lavender Supporting Resources box. Full names are wrapped, duplicate titles are retained as distinct source IDs, and lists have no length cap.
- Positive-hour resources remain individual main-table activities. A box says `(0h each)` only if every resource has recorded zero hours. Mixed/missing boxes show individual `0h` or `OTJ unavailable` labels. No media duration is substituted for authored OTJ.
- Live-session display names still use their verified linked Week title; other activities keep Component titles. Original IDs, component titles, hours and metadata remain in the frozen snapshot.
- Reading, quiz, live sessions, assignments, other components and existing assignment subtopics remain visible individually. Assignment subtopics retain their parent and Week identities and have no invented independent hours.
- Only verified assignment deadlines and projected Monthly Coaching dates populate weekly activity date cells. Existing Step 6A/6B services and existing calendar/deadline rules remain unchanged.
- Monthly Coaching occurs only in its uniquely verified module/week, once. Ambiguous or unclassified occurrences remain visible in a separate programme-meetings table instead of being silently dropped or inserted into an arbitrary week.
- The final Reviews Schedule now contains **Progress Reviews only**, as explicitly requested in the new compact-design brief. It shows verified required signatory roles, not assertions of completed signatures. Other applicable review types remain unchanged in underlying Learning Plan Meetings and the existing meeting totals.
- Week/table headings repeat on continuation pages. Resource-box headings stay with content and repeat for long lists. Font sizes remain readable (9pt activities, 8.8pt supporting resources). A tested 220-resource list reaches its final full name across pages.
- No technical diagnostics are printed in the learner PDF or document HTML. Existing admin diagnostics and critical finalization checks remain active.

## Snapshot safety

New captures include the complete Week records needed for v5. Existing custom/unissued flat drafts are **not** silently recaptured or reconstructed: their frozen data/custom wording is preserved, with existing v4 clean presentation. Create a new authorised draft/revision for the weekly layout. Issued/signed v1–v4 snapshots keep their original renderer and stored PDF bytes. No real learner documents were issued, finalized, signed, replaced or refreshed during testing.

## Reconciliation and visual verification

Isolated fixture uses three confirmed modules, one with 20 real-shaped Week records and two with three Weeks each, plus source Components and configured learner-specific review occurrences. The final Week of each fixture module is deliberately empty, and several resources deliberately share names. These are synthetic records, not a production learner export or the example values in the design image.

The exact same captured programme/module/component data was rendered by the previous v4 flat presentation and the new v5 weekly presentation:

| Check | Result |
| --- | --- |
| Full document before | 25 pages |
| Full document after | 18 pages (7 fewer, 28% reduction) |
| Confirmed modules | 3 of 3 |
| Week records | 26 of 26, including all empty weeks |
| Source Components | 460 of 460, each represented once |
| Main-table Components | 115 |
| Supporting Resources | 345 |
| Source component ID multiset vs snapshot | Identical |
| Source ID multiset vs actual `weeklySections` grouping | Identical, no duplicates |
| Original Week relationships | Preserved for every component |
| Full component names extracted from actual PDF | Every expected title present, including the expected multiplicity of duplicate titles |
| Module / meeting / plan hours before and after | 299h 15m / 12h / 311h 15m, unchanged |

The source IDs, rendered IDs and per-module reconciliation are in `.cache/weekly-training-plan/reconciliation.json`. The companion local verifier invokes the actual frontend grouping function against the generated snapshot rather than mirroring grouping logic in a test. PDF text extraction verifies full names and the last actual Week; screenshots were inspected for wrapping, column alignment, continued tables and orphaned resource headings.

Artifacts at repository root:

- `.cache/weekly-training-plan/before-flat.pdf`
- `.cache/weekly-training-plan/after-weekly.pdf`
- `.cache/weekly-training-plan/weekly-page-4.png` — module and weekly activities/resources.
- `.cache/weekly-training-plan/weekly-page-5.png` — continuation, missing-OTJ resource and subsequent weeks.
- `.cache/weekly-training-plan/weekly-page-15.png` — final review section.
- `.cache/weekly-training-plan/snapshot.json`, `weekly-content.html`, `reconciliation.json`.

Existing official mandatory sections remain present, which is why the full PDF contains more than the Activities Overview pages. No arbitrary “remaining weeks follow” placeholder is used.

## Tests

- 21 isolated document workflow tests passed after final PDF pagination changes; an additional isolated known Gateway/support-type exclusion test passed (22 document cases total). Covers ME and confirmed active-pathway PCP, correct source identities, activity zero/NULL, deadlines, MCM placement, Progress-only schedule, unchanged OTJ, frozen revisions, preview/download/finalization/signature routes and historical PDF protection.
- 53 source-reader/Step 6A/6B regression tests passed.
- 52 focused frontend tests passed across weekly grouping/PDF, previous Activities versions, shared PDF and protected page-document suites. The 5 new weekly cases also passed after the final pagination adjustment.
- TypeScript, scoped ESLint, production frontend/shared PDF runtime build and `git diff --check` passed. Vite emitted its existing plugin timing advisory; no build failure.

## DATABASE BLOCKERS — ACTION REQUIRED

The final read-only audit of the existing approved role `enrolment_modules_reader` confirmed missing SELECT access in production LMS schema `curriculum`:

| Table | Unreadable columns | Affected output |
| --- | --- | --- |
| `review_templates` | `review_type_id` | Cannot distinguish Progress Review from Monthly Coaching by its authoritative type. New v5 preserves unclassified occurrences separately and blocks finalization rather than guessing the type from a name. |
| `review_templates` | `signature_advisor`, `signature_employer`, `signature_participant`, `signature_referrer` | Required roles unavailable; no names or signatures invented. |
| `groups` | `session_week_day`, `session_start_time`, `session_end_time` | Cannot verify the group's weekly delivery calendar. |
| `holidays` | `id`, `label`, `start_date`, `end_date`, `notes`, `is_archived` | Cannot verify organisation closures when resolving relative deadlines. |
| `england_holidays` | `id`, `title`, `holiday_date` | Cannot verify bank-holiday effects on delivery/week dates. |

Custom Review Type IDs additionally require readable `review_types.id, code`. The existing fixed system IDs can be resolved through the application's stable constants once `review_type_id` is readable.

These do **not** block implementing/rendering the weekly layout from real Week/Component records. They do block claiming fully verified Progress-only classification, signatory details or unresolved relative assignment/coaching week dates for the affected production data. Technical details remain in admin data, not learner documents. Audit evidence is `.cache/weekly-training-plan/source-audit.json`. No permissions, roles or source records were changed, and no privileged connection was substituted.

## Files changed for this task

- `backend/learner_api/training_plan_activities.py` — v5 capture of full Week metadata, explicit topic Week links, Progress-only final schedule, unresolved occurrences retained, previous snapshots preserved.
- `backend/enrolment_api/document_templates/test_training_plan_activities.py` — new real-shaped weekly fixture/reconciliation/PDF comparison and updated v5 expectations.
- `frontend/src/features/compliance-documents/weeklyTrainingPlan.ts` — new versioned presentation helper inside the existing renderer, plus matching document HTML.
- `frontend/src/features/compliance-documents/trainingPlanActivities.ts` — shared snapshot types and v5 dispatch, previous version behavior retained.
- `frontend/src/features/compliance-documents/render.ts` and `pageDocument.ts` — recognise v5 in existing native block workflow.
- `frontend/src/features/compliance-documents/__tests__/weeklyTrainingPlan.test.ts` — five focused weekly rendering/reconciliation tests.
- This report.

All pre-existing uncommitted work is preserved. Programme Structure, Programme Suitability, cohorts/groups/allocation, PCP selections/confirmation, module timeline, Learning Plan persistence, OTJ calculation services, LMS records/schema/roles, live learners, production flags and issued/signed history were not changed. No migrations, commit or push were run.

## Reference appearance correction — 19 November 2026

The generic branded editor heading decorator and `!important` table skin were
overriding the weekly HTML appearance. The compact native block now has its own
scoped styling: dark gradient module strips, lavender week cards and column
headings, activity-specific SVG icons, inset lavender supporting-resource boxes,
and dark review/total table heads. Native role headings avoid generic h2/h3
decoration. The shared PDF painter uses matching vector gradients, compact
tables, small activity icons and enclosing week frames with page continuation.

This correction changes presentation only in `weeklyTrainingPlan.ts`,
`documentTheme.css` and the focused weekly frontend regression test. No source
capture, schedule, hour, grouping, Learning Plan or database logic changed.
The standard document branding is retained. Frozen issued/signed PDF bytes
continue through the existing historical document route.

Verification: 53 focused frontend tests, TypeScript, scoped ESLint, production
build and 22 isolated backend document workflow tests. A Chromium capture of
the generated editor HTML verifies lavender activity heads (`#e5cef3`), dark
review heads (`#70268b`), module/resource gradients and all 26 week cards.
The isolated 460-component fixture preserves every source ID exactly once and
all OTJ totals. Visual artifacts are under `.cache/weekly-training-plan/`:
`compact-editor-design.png`, `weekly-page-4.png`, `after-weekly.pdf` and
`design-check.json`. These are test fixtures, not live learner documents.
