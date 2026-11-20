# Step 6A: learner-specific module activities (read-only)

Implemented only activity content retrieval. No plan hydration/persistence, allocation,
module-total calculation, coaching/review integration or document generation is called.

## Entry points

- `learner_api.module_activities.learner_module_activities(enrolment_id)` is the reusable backend service.
- `learner_api.module_activities.project_module(entry, raw)` is a pure structured projection.
- `enrollment_service.lms_activity_readonly.get_module_activities(module_id, programme_id, cohort_id, group_id)` opens only the existing approved LMS reader, verifies its role and READ ONLY transaction, performs bounded parameterized SELECTs, then rolls back and closes.
- Authenticated GET `/enrolment_api/programme-suitability/apprenticeship/<enrolment_id>/module-activities/` uses the existing enrolment self/staff authorization. The route does not accept alternate module, cohort, group or pathway inputs. It returns 409 when prerequisites/content are unavailable; partial content has explicit per-module blockers and HTTP 200.

## Scope and identity

Only `stored_training_plan(source)` is read. There is no programme-wide catalogue scan,
legacy preset, unsaved preview, title matching, ID substitution or backfill. The saved
programme and home-cohort identity are verified from existing Enrolment records. Each
entry needs a distinct confirmed academic ID and LMS offering ID, and exact programme,
home cohort and group. An unscheduled/pending academic module produces an explicit
blocker and is not queried in LMS. Fixed programmes use only saved fixed academic IDs.

PCP additionally requires the existing persisted allocation to match the active saved
pathway and its complete validated selection. Every offering is cross-checked against
the allocation's academic ID, LMS ID, group, home cohort and dates. Inactive-pathway
choices and informational other-cohort offerings are never content sources. Profiles
are optional; the Enrolment learner ID and saved programme assignment remain authoritative.

## Source relationships

`curriculum.modules.module_catalogue_id` -> `weeks.module_catalogue_id` ->
`components.week_id`, with the component's own `module_catalogue_id` checked too.
Live module placement is checked before reading any children. Soft-deleted rows are
excluded. Weeks retain display_order/week_number/id; components retain display_order/id.
Orphans, duplicate/invalid identities and truncated results are reported rather than
joined by a similar title or silently treated as a complete response.

Assignments are existing assignment components, not new entities. Reuses the existing
pure assignment-topic parser for the actual `assignmentTopics` slots. No synthetic
Topic 1 fallback is created. Brief/content, instructions, learnerTitle and dueTiming
come from existing settings. Reading content/guidance remains attached to its component.
Timing is returned only as recorded (`dueTiming`, `recordingDate`); a phrase such as
"End of week" is not converted into an invented deadline. This step does not calculate
week dates or load calendars/coaching/review schedules.

`components.expected_otjh` is the parent activity's recorded hours. Zero stays zero;
missing/invalid hours stay NULL with a blocker. Nested assignment topics have NULL hours:
the existing topic schema has no independently authored OTJ-hour field. The parent
assignment's hours are never copied to children. `moduleTotalOtjHours` is copied from
the existing saved plan unchanged, together with its hoursPending flag. Activity hours
are never summed into that value and no existing OTJ calculation code is called/edited.

KSB coverage comes only from the module's own `ksb_mappings` rows and its verified
components' canonical KSB JSON. Week/component relationships are checked; module-level
mappings stay module-level. Codes are deduplicated while retaining all source references.
Descriptions may be enriched only through the module's explicit `ksb_profile_source_id`,
an exact profile ID, and a single unambiguous profile version. Definitions are selected
only for already mapped codes. Multiple versions are reported, never resolved by latest
name/date. `ksb_mappings.ksb_id` has no declared definition FK in the audited schema and
is deliberately not guessed to equal `ksb_definitions.id`. Unmapped profile KSBs are
not returned. Missing descriptions and relationships remain explicit.

## Verified database blockers (18 November 2026)

The configured **LMS / neondb / curriculum** metadata was inspected using only
`enrolment_modules_reader`, with transaction_read_only=on, verified TLS, local timeouts,
and rollback. No privileged LMS connection was used. All required tables and columns
exist with the expected types. Schema USAGE is available. The reader can read modules,
including description and total_otjh. It cannot SELECT any columns on weeks, components,
ksb_mappings, ksb_profiles, ksb_profile_versions or ksb_definitions.

Therefore module identities/descriptions can work after a valid persisted plan exists,
but real activity/assignment content, activity hours and KSB coverage cannot yet be
verified. No missing activity records or concrete per-module mappings are asserted:
those records are not readable. Source inspection evidence is in `.cache/step6a/source-audit.json`.

### DATABASE BLOCKERS — ACTION REQUIRED

The database owner needs to supply SELECT access for the existing reader to these
minimum columns (including the deletion predicates needed to avoid historical rows):

- `curriculum.weeks`: `id`, `module_catalogue_id`, `title`, `summary`, `week_number`, `display_order`, `learning_outcomes`, `deleted_at`, `is_programme_deleted`, `deleted_by`, `deleted_via_parent`.
- `curriculum.components`: `id`, `module_catalogue_id`, `week_id`, `title`, `type`, `description`, `display_order`, `settings_json`, `expected_otjh`, `ksb_mappings`, `deleted_at`, `is_programme_deleted`, `deleted_by`, `deleted_via_parent`.
- `curriculum.ksb_mappings`: `id`, `module_catalogue_id`, `week_id`, `component_id`, `ksb_code`, `ksb_description`, `classification`, `weight_class`, `weight`, `deleted_at`, `is_programme_deleted`, `deleted_by`, `deleted_via_parent`.
- `curriculum.ksb_profiles`: `id`, `ksb_profile_id`, `ksb_items`.
- `curriculum.ksb_profile_versions`: `id`, `source_profile_id`.
- `curriculum.ksb_definitions`: `id`, `profile_version_id`, `code`, `description`, `ksb_type`.

No additional module grant is currently required. No role, permission, schema, record,
credential or environment setting was changed. These are requirements, not executed grants.
After access is supplied, actual weeks/component links, assignment JSON, recorded
expected_otjh and profile/version relationships still need verification. Missing records,
NULL hours or ambiguous versions continue to return explicit blockers. Independent topic
hours and absolute assignment deadlines are not present in the verified topic schema;
they cannot be fabricated from the reference image or distributed from module totals.

A separate read-only prerequisite check found that Enrolment learner **1601 (ME)** and
**1609 (PCP)** have confirmed programmes but no persisted Learning_plan/Training_plan.
Consequently Step 6A correctly returns persisted_learning_plan_required for them. The
existing Step 4/5 save/confirmation workflow must supply their real plan before this
service may retrieve learner-specific activities. PCP must also have its successful
home-group confirmation. This step did not update either learner or infer modules from
the timeline. Evidence: `.cache/step6a/learner-plan-audit.json` (counts/IDs only, no names/emails).

## Isolated example and verification

`.cache/step6a/example-output.json` contains synthetic output produced by the tests:
a saved module total of 99.75 is unchanged; an assignment has 8 recorded component
hours; each of its two authored child topics has NULL activity hours; K1 coverage is
deduplicated with original mapping/component references. These are fixture values,
not real LMS data or values copied from the reference image.

Run with memory-only settings (external networking blocked):

```
backend/.venv/Scripts/python.exe backend/manage.py test programme_suitability.test_module_activities enrollment_service.test_lms_activity_readonly --settings=programme_suitability.test_settings
```

Tests cover all four programmes and all active PCP pathways, exact learner/home-group
scope, no early PCP reads, missing saved plans, unchanged source rows/module totals,
ordered components/topics, parent/child hour separation, scoped KSB descriptions,
permission/connection/schema failures, orphan records and SELECT-only driver calls.
No frontend or PDF modifications are needed or included in Step 6A.

## Verification results for this change

- 28 focused Step 6A tests passed.
- The broader Steps 1-6A run executed 171 tests: 170 passed; the unchanged
  `test_overlapping_confirmations_use_isolated_file_database_and_cannot_overbook`
  failed in its disposable SQLite subprocess with Windows file-lock/thread-timeout
  errors. The same test passed when rerun alone. No allocation code, concurrency
  fixture or earlier tests were modified to mask that failure.
- Python compilation, Django system checks and `git diff --check` passed.
- No frontend files were changed; frontend/PDF builds were not needed for this step.
- Production activity content remains unverified until the SELECT blockers above are
  resolved. No real activity payloads were returned or guessed during this inspection.

## Update: SELECT grants verified after owner action (18 November 2026)

The owner applied the six column-level SELECT grants in Neon. A fresh connection
through the application's existing `enrolment_modules_reader` on `neondb` verified
all required columns on all seven source tables (including modules). Schema/type
checks and zero-row SELECT statements succeeded in a verified read-only transaction.
The SELECT blockers described in the earlier audit above are now resolved.
Evidence: `.cache/step6a/post-grant-verification.json`.

No unscoped activity content was fetched. The follow-up read-only prerequisite check
still found no persisted Learning Plan for learners 1601 and 1609. Their confirmed
Learning Plans, and the successful home-group confirmation for PCP, remain prerequisites
before actual learner-specific content can be retrieved and its data quality verified.
No learner data, source data, roles or credentials were changed by this verification.

## Update: real saved ME plan content verified (18 November 2026)

After the administrator used the explicit Save button, learner 1601 now has a
persisted ME Learning Plan containing the three exact home-group LMS offering IDs.
The existing approved reader and the current pure activity-projection functions
were exercised against these saved references, without Django startup hooks or
live writes. Content retrieval succeeded with no reader permission blockers and
no orphan component/week links:

| Confirmed module | Weeks | Components | Assignments | Covered KSB codes |
| --- | ---: | ---: | ---: | ---: |
| Marketing Impact and Planning | 20 | 321 | 4 | 21 |
| Social Media | 14 | 244 | 3 | 0 |
| Martech | 14 | 241 | 3 | 0 |

The module hours remain the saved 138, 103 and 105.75, respectively. No component
hours were summed into those totals. The first module has four explicitly authored
assignment-topic slots with content. The other modules have assignment components
but no configured assignmentTopics with content; no nested topics were fabricated.

Real content is available, but its projection is partial because:
- All three module description columns are blank.
- Marketing Impact and Planning resolves 21 definition codes, but the separate
  ksb_profiles lookup is missing or ambiguous and is explicitly reported.
- Social Media and Martech have no covered KSB mappings in either the mapping
  table or their component KSB JSON.

These are source-data limitations, not missing SELECT permissions. The source owner
must review them before claiming a complete future Training Plan activity overview.
No title-based replacement modules, guessed KSBs, invented hours/dates, new document,
issued-document change or PDF-generation change was made. The future document-create
integration should consume the reusable learner_module_activities service and handle
its explicit availability/blockers, preserving the existing document snapshot rules.
Evidence: `.cache/step6a/saved-plan-content-verification.json` (summary only).
