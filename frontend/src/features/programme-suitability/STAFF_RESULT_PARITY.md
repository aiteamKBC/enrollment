# Shared learner and staff suitability results

The staff enrolment wizard previously branched inside CvSuitabilityPanel and rendered
ProgrammeComparison, including long programme descriptions and responsibilities.
The learner used RecommendationResult for the same saved assessment state.

## Implementation

- CvSuitabilityPanel now renders RecommendationResult for both roles using the same
  completion status and saved AssessmentState. recommendation.ts remains the only
  existing client-side interpretation of the server result. No scores are recalculated.
- The confirmed programme and any alternative-selection reason come from the saved
  snapshot, exactly as they do for the learner. A review override remains separate;
  it does not replace the automated recommendation or the saved learner selection.
- Admissions status, critical evidence, review requests and the existing authorised
  review form are in a separate staff-only section. Saved answers reuse SubmittedAnswers.
- Staff can open the existing AssessmentDialog through Preview Assessment. Its new
  optional readOnly flag hides submission, pathway and programme-selection actions,
  displays disabled saved answers and guards the persistence function. The flag
  defaults to false, preserving the learner dialog's existing behavior.
- WizardShell enables the existing LearnerStepContent presentation for the staff
  suitability step only. A named inline-size container enables its existing responsive
  result layout. No new CSS, colours, branding or learner presentation was introduced.
- ProgrammeSuitabilityStep, ProgrammeModulesCard, ProgrammeModulesView and the module
  scheduling implementation are unchanged. Both roles still load the same saved module
  configuration version, selected programme, pathway, module selections, home cohort
  and schedule endpoint. Staff module controls retain their existing read-only behavior.

## Scope and preservation

No backend files, database records, configuration shapes, programme guidance, scoring,
selection rules, APIs or publication code were changed. No migrations or writes against
live learner records were performed. Existing uncommitted work was preserved.
The builder's diagnostic Preview & Test is outside this staff-wizard change and retains
its existing comparison diagnostics.

## Files changed in this task

- CvSuitabilityPanel.tsx
- AssessmentDialog.tsx
- ../../pages/users/wizard/WizardShell.tsx
- __tests__/staffResultParity.test.tsx (new)
- __tests__/stepVisibility.test.tsx
- __tests__/suitability.test.tsx (existing admissions assertions now target shared result)
- STAFF_RESULT_PARITY.md (this report)

## Verification coverage

The parity tests compare identical RecommendationResult markup between roles for
Marketing Executive, Marketing Manager, a confirmed Marketing Executive alternative to
Marketing Manager and Project Controls Professional. They compare saved programme/module
text, cohort/timeline data and selected module IDs; verify Chartered selections, mandatory
modules and inactive-path isolation; and cover pending/completed admissions reviews,
manual recommendations, incomplete assessments and pending confirmation. Opening and
closing the staff preview or viewing saved answers must not call assessment/module saves.
An integration test uses the real WizardProvider and staff WizardShell to verify the
shared content presentation, existing module loading and absence of writes.

No authenticated live-browser check was performed; parity checks render the real React
components against synthetic saved snapshots and mocked API reads.

## Results

- Frontend suitability, modules/schedules, shared wizard and learner enrolment suite:
  644 tests passed across 67 files; 0 failures.
- TypeScript (`npm --prefix frontend run type-check`): passed.
- Production build (`npm --prefix frontend run build`): passed.
- `git diff --check`: passed.
