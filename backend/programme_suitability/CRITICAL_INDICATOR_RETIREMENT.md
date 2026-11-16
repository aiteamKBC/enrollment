# Critical indicator retirement

## Result presentation and builder

Admin and learner results continue to use the same RecommendationResult, saved
assessment and programme selection. The dedicated Critical Level 6 evidence block
was removed from the staff panel, including its counts and names. It was not moved
to another section. Admissions status, saved answers and the authorised review form
remain separate from the shared result. No learner presentation or CSS was changed.

The question builder no longer exposes critical flags or targets. Scoring Rules no
longer exposes minimum critical counts or critical answer strength. Mixed-evidence
controls, question weights, multiplicity and answer affinities are retained. Existing
critical fields stay in draft JSON when other controls are edited. Preview results
no longer render critical counts, raw critical mappings or legacy critical reason codes.

## Scoring and activation

The existing code already chose the learner recommendation from the final weighted
indices. Critical indicators still affected the separate admissions diagnostics.
New-policy calculations now bypass both critical question-strength accumulation and
the minimum-count review trigger. They retain the weighted formula, per-question
caps, denominator, score thresholds, coverage, mixed-evidence diagnostics, required
answers, zero-evidence result and conservative Level 4 tie behavior.

The optional configuration marker `scoringPolicy: GENERAL_WEIGHTED_V1` selects the
updated behavior. defaults() returns it for new drafts. The existing wizard publication
normalizer stamps it onto the newly published configuration, including configuration
carried forward from a previous version. This creates a new wizard version through
the existing Publish changes workflow; it does not edit earlier publications.
The draft preview endpoint uses the same preparation helper and scoring engine.
No configuration was automatically published during implementation.

validate() never upgrades an unmarked saved configuration. Assessment calculations
continue using their own configuration snapshot. An unmarked snapshot, including an
assessment started before publication, keeps legacy critical diagnostics. Saved
historical recommendations, review decisions, answers and JSON are not recalculated
or cleared. New snapshots taken after the next publication use the new marker.

Submitting an assessment still follows the existing pending admissions review workflow; no learner is automatically approved.

Critical-specific fields may be omitted under the new policy and do not constrain
new calculations; existing JSON values are preserved. Legacy snapshots retain their
original validation. The result API keeps `criticalIndicators` for compatibility,
with empty per-programme lists under the new policy. Historic counts and flags can
remain in stored results but are not displayed. No database schema change is needed.

## Preservation

No changes were made to selection authorization, admissions save/notifications,
programme assignment, module/version APIs, PCP pathways, credits, mandatory/optional
choices, cohort relationships, schedules or Skills Radar. Existing uncommitted work
was preserved. No migrations against a live database, branch changes, commits or
pushes were performed.

## Files changed in this task

Production:
- backend/programme_suitability/configuration.py
- backend/programme_suitability/scoring.py
- backend/programme_suitability/views.py
- frontend/src/features/programme-suitability/CvSuitabilityPanel.tsx
- frontend/src/features/programme-suitability/SuitabilityAdmin.tsx
- frontend/src/features/programme-suitability/ProgrammeComparison.tsx
- frontend/src/features/programme-suitability/types.ts

Tests and documentation:
- backend/programme_suitability/tests.py
- backend/programme_suitability/test_modules_configuration.py
- backend/programme_suitability/test_runner.py
- frontend/src/features/programme-suitability/__tests__/suitability.test.tsx
- frontend/src/features/programme-suitability/__tests__/staffResultParity.test.tsx
- backend/programme_suitability/README.md
- backend/programme_suitability/CRITICAL_INDICATOR_RETIREMENT.md

The isolated test setup now supplies real synthetic onboarding learner records and
emulates PostgreSQL's column metadata from SQLite's actual fixture schema. Permissions
and publication lookups remain exercised. No authorization bypass or live connection
was introduced. The test runner's existing ephemeral in-memory schema setup is retained.

## Verification

Backend: 134 tests passed using programme_suitability.test_settings. Coverage includes
legacy and new scoring, identical weighted scores/coverage/general flags, missing or
changed retired fields, both pathways, required-answer validation, draft previews,
publication stamping, unchanged historical results, pinned in-progress assessments,
admissions overrides, selection/assignment, PCP rules, module persistence and schedules.

Frontend: 645 tests passed across 67 files, with 0 failures. TypeScript, production build and git diff --check passed. Tests render
real components with synthetic snapshots and mocked API reads; no authenticated live
browser or real learner publication was performed.
