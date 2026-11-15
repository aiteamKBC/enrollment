# Programme Suitability builder simplification

This changes admin presentation only. Existing colours, admin UI classes, API services,
configuration shapes, save handlers and publication logic are reused.

## Tabs

- General Settings: enabled state and learner introduction. A collapsed Advanced
  programme associations section retains editing of existing catalogue references.
  These references are consumed by programme assignment on confirmation.
- Marketing Assessment and Project Controls Assessment: real questions appear as
  numbered compact rows with text previews, status, Edit, reorder and soft removal.
  Only one question editor opens at a time. Title, text, activity, requirement and
  answers come first. Weights, multiplicity, critical indicator settings, stable IDs
  and per-answer affinities remain in collapsed Advanced Settings. New questions
  open for editing; existing IDs and defaults are retained.
- Programme Modules: the existing ModulesAdmin appears only in this tab. Its mounted
  state survives tab switches, including programme, preview choices and messages.
  Module/group rows start collapsed and show sessions, credits, activity and Edit.
  Existing module fields, ordering, submodules, parent associations and PCP pathway
  editing are retained. IDs appear in a collapsed Advanced Settings area.
- Scoring Rules: match, difference and weighted coverage are grouped together.
  Critical evidence and mixed-evidence parameters are in Advanced Scoring Settings.
  Help text reflects the existing backend calculation and internal review flags.
- Preview & Test: the existing backend preview, assessment questions and programme
  comparison remain. A note explains that saved preferences, confirmed selection,
  admissions review and cohort schedules require an actual learner record. Module
  and pathway test choices remain available through Programme Modules / Preview Changes.

## Data and compatibility

The Programme Suitability Content tab was removed only from rendering. Programme
records, titles, descriptions, responsibilities, codes, activity, ordering and catalogue
references remain in configuration.programmes and are carried through unchanged by
all other edits. No schema, API, backend, learner component or stored data was changed.
Catalogue references have a minimal editing location under General Settings because
programme_assignment.py consumes them; the full programme content editor is not recreated.

Question editing still patches the existing local wizard draft. Module editing still
uses ModulesAdmin's existing draft APIs and version. Save Draft, Load saved modules
draft, older-version warnings, Preview Changes and the existing wizard publication
mechanism are retained. Nothing automatically publishes, migrates learners, changes
snapshots or rewrites historical results.

PCP Operational, Strategic and Chartered pathways, mandatory and optional choices,
credit requirements, selection limits, grouping and versioned learner selections keep
their existing implementation. Fixed module structures remain fixed.

## Files

Production changes:
- SuitabilityAdmin.tsx
- modules/ModuleRowEditor.tsx
- ../../pages/users/wizard-builder/page.tsx

Regression tests updated or added:
- __tests__/suitability.test.tsx
- __tests__/modulesAdmin.test.tsx
- __tests__/moduleGroups.test.tsx
- __tests__/moduleHierarchy.test.tsx
- __tests__/pcpRules.test.tsx
- __tests__/adminTestHelpers.ts (opens actual collapsed editors for existing assertions)
- ../../pages/users/wizard-builder/__tests__/wizardBuilder.test.tsx

Unrelated pre-existing working-tree changes were preserved. No migrations, publication,
branch switch, commit or push was performed.

## Verification

- Frontend broad regression run (suitability, modules, wizard builder, wizard and
  learner enrolment): 630 passed; three version-picker tests failed because they
  expected native select options inside the existing custom dropdown trigger.
- Those three tests now open the existing dropdown and choose its real options.
  Rename, archive visibility and stale-request assertions are retained. The focused
  rerun of the complete wizard-builder file and module hierarchy file passed all
  31 tests. No application version-picker behavior was changed.
- Backend ScoringTests and PublishingTests: all 20 passed, without database setup
  or migrations.
- Frontend TypeScript check and production build passed.
- No authenticated browser or live learner/database publication was performed.
