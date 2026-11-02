import { modulesConfiguration } from './modulesFixtures';
vi.mock('../modules/api', () => ({ getModules: async () => ({ configuration: modulesConfiguration, configurationVersion: 3, selectedProgramme: 'PCP_L6' }) }));
import { useState } from 'react';
import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProgrammeSuitabilityStep from '../ProgrammeSuitabilityStep';
import CvJob from '@/pages/users/wizard/steps/CvJob';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import type { CvJobForm, EnrolmentBoard, WizardDraft } from '@/pages/users/types';
import { resolveLayout } from '@/pages/users/wizard/layout/resolve';
import { WizardTextsContext } from '@/pages/users/wizard/layout/textsContext';
import { useSuitability } from '../useSuitability';
import { configuration, fresh, result } from './fixtures';
import type { AssessmentState, ProgrammeId } from '../types';

vi.mock('../api', () => ({ getAssessment: vi.fn(), saveAssessment: vi.fn(), peekAssessment: vi.fn(),
  primeAssessment: vi.fn(), prefetchAssessmentSecurity: vi.fn() }));
vi.mock('@/api/cvJobDocuments', () => ({ fetchCvDocuments: vi.fn(), uploadCvDocument: vi.fn(),
  deleteCvDocument: vi.fn(), getCvDocumentUrl: vi.fn() }));
vi.mock('@/hooks/useToast.helpers', () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
import * as assessmentApi from '../api';
import * as documents from '@/api/cvJobDocuments';

const savedCv: CvJobForm = { pmQualifications: '', experienceText: 'Saved experience', highestQualification: 'BA (Hons)',
  highestQualificationField: 'Business', hasFieldQualification: true, highestFieldQualification: 'Saved field qualification',
  gcseEnglish: true, gcseMaths: true, functionalSkillsEnrol: 'No, I would prefer to opt out' };
const change = vi.fn();
function Harness({ enabled = true, legacy = false, staff = false, hasQualification = true, texts, assessment = false }: {
  enabled?: boolean; legacy?: boolean; staff?: boolean; hasQualification?: boolean; texts?: Record<string, string>; assessment?: boolean;
}) {
  const suitability = useSuitability('apprenticeship', '41', enabled, 3, legacy);
  const [cv, setCv] = useState<CvJobForm>({ ...savedCv, hasFieldQualification: hasQualification });
  const layout = resolveLayout({ steps: [], ...(enabled ? { programmeSuitability: configuration } : {}) });
  layout.steps.find((s) => s.slug === 'cv-job')!.items.push({ key: 'custom-cv-choice', builtin: false,
    type: 'dropdown', label: 'Custom CV choice', options: ['Saved choice', 'Another choice'], hidden: false });
  const value: WizardContextValue = { userId: '41', isCommercial: false, readOnly: staff, suitability,
    board: { programme: { name: 'Marketing Manager Level 6' } } as EnrolmentBoard,
    draft: { cvJob: cv, custom: { 'custom-cv-choice': 'Saved choice' } } as unknown as WizardDraft,
    setSection: (key, value) => { change(key, value); if (key === 'cvJob') setCv(value as CvJobForm); },
    setCustom: change, layout, steps: layout.steps, completed: [], markComplete: vi.fn(),
    saveIlr: vi.fn(), saveModules: vi.fn(), ilrSaving: false, ilrSavedAt: '', hydrated: true, ready: !suitability.loading,
    fileIlrDocument: vi.fn(), ilrFiling: false, inclusionCheck: { checking: false, error: null, refresh: vi.fn() },
  };
  return <WizardContext.Provider value={value}><WizardTextsContext.Provider value={texts}><div data-testid="cv-fields"><CvJob /></div>{assessment && <ProgrammeSuitabilityStep />}</WizardTextsContext.Provider></WizardContext.Provider>;
}
const fields = () => screen.getByTestId('cv-fields');
const lockMessage = 'Complete the Programme Suitability Assessment to unlock the remaining questions.';
const genericQualification = 'Do you have any qualifications in the field of your programme?';

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; });
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(assessmentApi.getAssessment).mockResolvedValue(fresh());
  vi.mocked(assessmentApi.prefetchAssessmentSecurity).mockResolvedValue({ csrfToken: 'synthetic' });
  vi.mocked(documents.fetchCvDocuments).mockResolvedValue([{ id: 'saved-cv', docKind: 'cv', filename: 'saved-cv.pdf',
    contentType: 'application/pdf', sizeBytes: 12, uploadedAt: null }]);
  vi.mocked(assessmentApi.saveAssessment).mockImplementation(async (_kind, _id, state, changes) => {
    const id = state.pathway === 'project-controls' ? 'PCP_L6' : 'MM_L6';
    return { ...state, ...changes, revision: state.revision + 1,
      ...(changes.action === 'submit' ? { result: { ...result, recommendedProgramme: id, outcome: `RECOMMENDED_${id}` },
        selectedProgramme: id, confirmedAt: '2026-11-01T12:00:00Z', completionStatus: 'SUBMITTED',
        selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION', selectionConfirmedAt: null } : {}),
    };
  });
});

it('renders the assessment only in its dedicated step and leaves CV fields interactive', async () => {
  const { rerender } = render(<Harness />);
  await screen.findByRole('button', { name: 'saved-cv.pdf' });
  expect(screen.queryByRole('region', { name: 'Programme suitability assessment' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Start Assessment' })).not.toBeInTheDocument();
  expect(screen.queryByText(lockMessage)).not.toBeInTheDocument();
  expect(fields().querySelector('[inert], fieldset[disabled]')).toBeNull();
  expect(screen.getByDisplayValue('Saved experience')).toBeEnabled();
  expect(screen.getByDisplayValue('Saved choice')).toBeEnabled();
  rerender(<Harness assessment />);
  expect(screen.getByRole('heading', { name: 'Programme Suitability & Modules' })).toBeInTheDocument();
  expect(screen.getAllByRole('region', { name: 'Programme suitability assessment' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Start Assessment' })).toBeEnabled();
});

it.each(['Marketing', 'Project Controls'])('updates qualifications immediately after %s submission from the separate step', async (pathway) => {
  render(<Harness assessment />);
  await userEvent.click(await screen.findByRole('button', { name: 'Start Assessment' }));
  await userEvent.click(screen.getByRole('button', { name: pathway }));
  await userEvent.click(await screen.findByLabelText(`${pathway === 'Marketing' ? 'marketing' : 'project-controls'} answer D`));
  await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
  await screen.findByRole('button', { name: 'Continue to Next Step' });
  const label = pathway === 'Marketing' ? 'Do you have any marketing qualifications?' : 'Do you have any project management qualifications?';
  expect(within(screen.getByRole('group', { name: label })).getByRole('radio', { name: 'Yes' })).toBeChecked();
  expect(change).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Close assessment' }));
  expect(screen.getByDisplayValue('Saved experience')).toBeEnabled();
  expect(screen.queryByText(lockMessage)).not.toBeInTheDocument();
});

it('keeps the generic qualification label without metadata and does not lock CV fields', async () => {
  vi.mocked(assessmentApi.getAssessment).mockRejectedValue(new Error('Assessment unavailable.'));
  render(<Harness assessment />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Assessment unavailable');
  expect(screen.getByRole('group', { name: genericQualification })).not.toBeDisabled();
  expect(screen.getByDisplayValue('Saved experience')).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
});

it('restores the read-only preview in the new step without adding a CV assessment card', async () => {
  vi.mocked(assessmentApi.getAssessment).mockResolvedValue(submittedAssessment('MM_L6'));
  render(<Harness assessment />);
  await userEvent.click(await screen.findByRole('button', { name: 'Preview Assessment' }));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(within(screen.getByRole('dialog')).queryByRole('button', { name: 'Continue to Next Step' })).toBeNull();
  expect(within(fields()).queryByRole('button', { name: 'Preview Assessment' })).toBeNull();
  expect(change).not.toHaveBeenCalled();
});

function submittedAssessment(recommended: ProgrammeId, selected: ProgrammeId = recommended): AssessmentState {
  const programme = configuration.programmes.find((p) => p.id === recommended)!;
  return { ...fresh(), pathway: programme.pathway, revision: 2,
    result: { ...result, recommendedProgramme: recommended, outcome: `RECOMMENDED_${recommended}` },
    selectedProgramme: selected, completionStatus: 'SUBMITTED', confirmedAt: '2026-11-01T12:00:00Z',
    selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:01:00Z' };
}

it.each([
  ['ME_L4', 'ME_L4', 'marketing'], ['MM_L6', 'MM_L6', 'marketing'],
  ['APM_L4', 'APM_L4', 'project management'], ['PCP_L6', 'PCP_L6', 'project management'],
  ['MM_L6', 'ME_L4', 'marketing'], ['PCP_L6', 'APM_L4', 'project management'],
] as [ProgrammeId, ProgrammeId, string][])('restores the %s → %s qualification label from the saved pathway without changing answers', async (recommended, selected, field) => {
  vi.mocked(assessmentApi.getAssessment).mockResolvedValue(submittedAssessment(recommended, selected));
  const label = `Do you have any ${field} qualifications?`;
  const { unmount } = render(<Harness />);
  const question = await screen.findByRole('group', { name: label });
  expect(within(question).getByRole('radio', { name: 'Yes' })).toBeChecked();
  expect(within(question).getByRole('radio', { name: 'No' })).not.toBeChecked();
  expect(screen.queryByRole('group', { name: genericQualification })).not.toBeInTheDocument();
  expect(within(fields()).getByDisplayValue('Saved field qualification')).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Do you have GCSE in English' })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Do you have GCSE in Maths' })).toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
  unmount(); render(<Harness />);
  const restored = await screen.findByRole('group', { name: label });
  expect(within(restored).getByRole('radio', { name: 'Yes' })).toBeChecked();
  expect(change).not.toHaveBeenCalled();
});

it.each([true, false])('keeps the saved qualification answer %s while applying only the question label override', async (answer) => {
  vi.mocked(assessmentApi.getAssessment).mockResolvedValue(submittedAssessment('PCP_L6', 'APM_L4'));
  const texts = { 'cv.fieldQualification.label': 'Previously configured qualification question',
    'cv.fieldQualification.named': 'Existing follow-up wording', 'cv.highestQualification.label': 'Existing highest qualification wording' };
  render(<Harness hasQualification={answer} texts={texts} />);
  const question = await screen.findByRole('group', { name: 'Do you have any project management qualifications?' });
  expect(within(question).getByRole('radio', { name: answer ? 'Yes' : 'No' })).toBeChecked();
  expect(screen.getByText('Existing highest qualification wording')).toBeInTheDocument();
  if (answer) expect(screen.getByText('Existing follow-up wording')).toBeInTheDocument();
  expect(texts['cv.fieldQualification.label']).toBe('Previously configured qualification question');
  expect(change).not.toHaveBeenCalled();
});
