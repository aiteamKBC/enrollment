import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import { LearnerEnrolmentVisualContext } from '@/features/learner-enrolment/enrolmentPresentation';
import { LearnerStepContent } from '@/features/learner-enrolment/LearnerStepContent';
import ProgrammeSuitabilityStep from '../ProgrammeSuitabilityStep';
import { AssessmentDialog } from '../AssessmentDialog';
import { completion } from '../useSuitability';
import type { AssessmentState, ProgrammeId } from '../types';
import { fresh, result } from './fixtures';
import { pcpConfiguration } from './pcpRadarFixtures';
import { mandatoryIds, type ProgrammeModuleSelection } from '../modules/configuration';

vi.mock('../api', () => ({ request: vi.fn(), saveAssessment: vi.fn(), saveReview: vi.fn() }));
import { request, saveAssessment, saveReview } from '../api';
const saveModules = vi.fn(), setSection = vi.fn();
function saved(recommended: ProgrammeId, selected = recommended): AssessmentState {
  const pathway = recommended.startsWith('M') ? 'marketing' : 'project-controls';
  return { ...fresh(), pathway, responses: { [pathway]: [`${pathway}_D`] },
    result: { ...result, outcome: `RECOMMENDED_${recommended}`, recommendedProgramme: recommended },
    selectedProgramme: selected, originalPreference: selected,
    completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED',
    confirmedAt: '2026-11-01T12:00:00Z', selectionConfirmedAt: '2026-11-01T12:01:00Z',
    alternativeSelectionReason: selected !== recommended ? 'My current role focuses on delivery.' : null,
    reviewStatus: 'PENDING_ADMISSIONS_REVIEW' };
}
function Harness({ initial, staff = false }: { initial: AssessmentState; staff?: boolean }) {
  const [state, accept] = useState(initial);
  const [open, setOpen] = useState(false);
  const path = pcpConfiguration.programmes[3].pathways.find(p => p.id === 'chartered')!;
  const selection: ProgrammeModuleSelection | undefined = state.selectedProgramme === 'PCP_L6'
    ? { programme: 'PCP_L6', pathway: path.id, configurationVersion: 11,
      modulesByPathway: { [path.id]: [...mandatoryIds(path), 'portfolio'], operational: ['pmp'] } } : undefined;
  const value = { userId: 'synthetic-parity', isCommercial: false, readOnly: staff, hydrated: true,
    draft: { programmeModules: selection }, saveModules, setSection,
    layout: { steps: [], programmeSuitability: state.configuration },
    suitability: { state, accept, enabled: true, status: completion(state), open, loading: false, error: '',
      start: () => setOpen(true), close: () => setOpen(false), refresh: vi.fn(), submitted: vi.fn(), continued: vi.fn() },
    modules: { data: { configuration: pcpConfiguration, configurationVersion: 11, selectedProgramme: state.selectedProgramme,
      homeCohort: { status: 'resolved', programmeId: state.selectedProgramme, cohortId: 'saved-cohort', name: 'Saved home cohort', layoutVersion: 11 } }, error: '', retry: vi.fn() },
  } as unknown as WizardContextValue;
  return <WizardContext.Provider value={value}><LearnerEnrolmentVisualContext.Provider value><LearnerStepContent slug="programme-suitability">
    <ProgrammeSuitabilityStep />
  </LearnerStepContent></LearnerEnrolmentVisualContext.Provider></WizardContext.Provider>;
}
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  vi.mocked(request).mockResolvedValue({ programmeId: 'PCP_L6', pathway: 'chartered', review: false, asOfDate: '2026-11-15', offeringCount: 0, modules: [] });
});

it.each([
  ['ME_L4', 'ME_L4'], ['MM_L6', 'MM_L6'], ['MM_L6', 'ME_L4'], ['PCP_L6', 'PCP_L6'],
] as [ProgrammeId, ProgrammeId][])('shares the exact saved result and module schedule for recommended %s / selected %s', async (recommended, selected) => {
  const state = saved(recommended, selected), before = structuredClone(state);
  const learner = render(<Harness initial={state} />);
  const resultHtml = screen.getByRole('region', { name: 'Assessment recommendation result' }).outerHTML;
  const timeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  await screen.findByText('Saved home cohort');
  const moduleText = screen.getByRole('region', { name: 'Programme Modules' }).textContent;
  const timelineText = timeline.textContent;
  const rows = Array.from(timeline.querySelectorAll('[data-academic-module-id]')).map(node => node.getAttribute('data-academic-module-id'));
  learner.unmount();
  render(<Harness initial={state} staff />);
  expect(screen.getByRole('region', { name: 'Assessment recommendation result' }).outerHTML).toBe(resultHtml);
  // Staff module controls are disabled; all saved programme/module text is identical.
  expect(screen.getByRole('region', { name: 'Programme Modules' }).textContent).toBe(moduleText);
  const staffTimeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  expect(staffTimeline.textContent).toBe(timelineText);
  expect(Array.from(staffTimeline.querySelectorAll('[data-academic-module-id]')).map(node => node.getAttribute('data-academic-module-id'))).toEqual(rows);
  expect(screen.queryByRole('region', { name: 'Programme comparison' })).toBeNull();
  expect(screen.queryByText('Editable suitability guidance')).toBeNull();
  expect(screen.queryByText('Relevant responsibility')).toBeNull();
  expect(screen.queryByText('Critical Level 6 evidence')).toBeNull();
  expect(screen.queryByText(/distinct indicators|no qualifying indicators/)).toBeNull();
  if (selected === 'PCP_L6') {
    expect(screen.getByRole('radio', { name: 'Chartered Pathway' })).toBeChecked();
    const chartered = within(screen.getByRole('region', { name: 'Chartered Pathway' }));
    expect(chartered.getByRole('checkbox', { name: 'Portfolio Management (APMG)' })).toBeChecked();
    expect(chartered.getByRole('checkbox', { name: 'Portfolio Management (APMG)' })).toBeDisabled();
    expect(staffTimeline.querySelector('[data-academic-module-id="portfolio"]')).not.toBeNull();
    expect(staffTimeline.querySelector('[data-academic-module-id="pmp"]')).toBeNull();
  }
  expect(state).toEqual(before);
  expect(saveAssessment).not.toHaveBeenCalled(); expect(saveReview).not.toHaveBeenCalled(); expect(saveModules).not.toHaveBeenCalled();
});

it.each(['PENDING_ADMISSIONS_REVIEW', 'REVIEW_COMPLETED'])('keeps %s and manual review separate from the shared saved recommendation', async reviewStatus => {
  const state = { ...saved('MM_L6', 'ME_L4'), reviewStatus,
    review: { status: reviewStatus, recommendedProgramme: 'ME_L4' as const, reason: 'Reviewed practical responsibilities.' } };
  const learner = render(<Harness initial={state} />);
  const html = screen.getByRole('region', { name: 'Assessment recommendation result' }).outerHTML;
  expect(screen.queryByRole('group', { name: 'Admissions review' })).toBeNull();
  learner.unmount(); render(<Harness initial={state} staff />);
  expect(screen.getByRole('region', { name: 'Assessment recommendation result' }).outerHTML).toBe(html);
  const details = within(screen.getByRole('region', { name: 'Admissions assessment details' }));
  expect(details.getByText(`Review status: ${reviewStatus.replaceAll('_', ' ')}`)).toBeVisible();
  expect(details.getByLabelText('Manual programme recommendation')).toHaveValue('ME_L4');
  expect(details.getByLabelText('Reason for review / override')).toHaveValue('Reviewed practical responsibilities.');
  expect(saveReview).not.toHaveBeenCalled();
});

it.each([false, true])('does not invent a result or allow staff writes for an incomplete assessment with started=%s', async started => {
  const state: AssessmentState = { ...fresh(), revision: started ? 1 : 0, pathway: started ? 'marketing' : '',
    responses: started ? { marketing: ['marketing_A'] } : {} };
  const learner = render(<Harness initial={state} />);
  expect(screen.queryByRole('region', { name: 'Assessment recommendation result' })).toBeNull();
  learner.unmount(); render(<Harness initial={state} staff />);
  expect(screen.queryByText('Assessment Completed')).toBeNull();
  expect(screen.getByText(started ? 'Assessment in progress' : 'Assessment not started')).toBeVisible();
  expect(screen.queryByRole('button', { name: /Start Assessment|Resume Assessment|Submit Assessment/ })).toBeNull();
  expect(screen.queryByRole('region', { name: 'Programme Modules' })).toBeNull();
  expect(saveAssessment).not.toHaveBeenCalled(); expect(saveReview).not.toHaveBeenCalled(); expect(saveModules).not.toHaveBeenCalled();
});

it('lets staff inspect a pending-confirmation result and its saved answers without selection or save actions', async () => {
  const state: AssessmentState = { ...saved('MM_L6'), selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION', selectionConfirmedAt: null };
  render(<Harness initial={state} staff />);
  await userEvent.click(screen.getByRole('button', { name: 'Preview Assessment' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByRole('region', { name: 'Assessment recommendation result' })).toBeVisible();
  expect(dialog.queryByRole('button', { name: /Choose|Continue to Next Step|Submit/ })).toBeNull();
  await userEvent.click(dialog.getByRole('button', { name: 'Show Answers' }));
  expect(dialog.getByLabelText('marketing answer D')).toBeChecked();
  expect(dialog.getByLabelText('marketing answer D')).toBeDisabled();
  await userEvent.click(dialog.getByRole('button', { name: 'Back to Result' }));
  await userEvent.click(dialog.getByRole('button', { name: 'Close assessment' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(saveAssessment).not.toHaveBeenCalled(); expect(saveReview).not.toHaveBeenCalled(); expect(saveModules).not.toHaveBeenCalled();
});

it('also prevents editing when the read-only dialog is given an incomplete saved snapshot directly', () => {
  render(<AssessmentDialog readOnly initial={{ ...fresh(), revision: 1, pathway: 'marketing', responses: { marketing: ['marketing_A'] } }}
    kind="apprenticeship" learnerId="synthetic-parity" onClose={vi.fn()} onConfirmed={vi.fn()} />);
  expect(screen.getByLabelText('marketing answer A')).toBeChecked();
  for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).toBeDisabled();
  expect(screen.queryByRole('button', { name: /Submit|Back to pathways/ })).toBeNull();
  expect(saveAssessment).not.toHaveBeenCalled();
});
