import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const state = vi.hoisted(() => ({ save: vi.fn(), open: vi.fn(), error: vi.fn(), enabled: true,
  status: 'NOT_STARTED', confirmed: false, token: 0, qualification: 'BA', legacy: false, next: 'cv-job', previous: false, programme: 'MM_L6', modulesRequired: false, modulesComplete: false }));
vi.mock('@/hooks/useToast.helpers', () => ({ useToast: () => ({ error: state.error, success: vi.fn() }) }));
vi.mock('../ProgrammeSuitabilityStep', () => ({ default: () => <p>Assessment step</p> }));
vi.mock('@/pages/users/wizard/steps/CvJob', () => ({ default: () => <p>Existing CV fields</p> }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => {
  const steps = [
    ...(state.previous ? [{ slug: 'introduction', label: 'Welcome', builtin: true, items: [] }] : []),
    { slug: 'programme-suitability', label: 'Programme Suitability & Modules', builtin: true, items: [] },
    { slug: state.next, label: 'Future step', builtin: true, items: state.next === 'cv-job' ? [{ key: 'cv.highestQualification', builtin: true }] : [] },
    { slug: 'next-steps', label: 'Finish step', builtin: true, items: [] },
  ];
  return { userId: '41', isCommercial: false,
    draft: { cvJob: { experienceText: '', highestQualification: state.qualification },
      programmeSuitability: { required: state.enabled && !state.legacy, status: state.status, selectionConfirmed: state.confirmed, selectedProgramme: state.programme },
      programmeModulesCompletion: { required: state.modulesRequired, complete: state.modulesComplete } },
    hydrated: true, ready: true, saveIlr: state.save, ilrSaving: false, steps,
    suitability: { state: { selectedProgramme: state.programme }, start: state.open, continueToken: state.token, status: state.status, selectionConfirmed: state.confirmed },
    layout: { steps, ...(state.enabled ? { programmeSuitability: { enabled: true } } : {}) },
  };
} }));

import { WizardShell } from '@/pages/users/wizard/WizardShell';
import { maxReachableStep, missingForStep } from '@/pages/users/wizard/validation';
import type { WizardDraft } from '@/pages/users/types';
import type { WizardLayout } from '@/pages/users/wizard/layout/types';
import { configuration } from './fixtures';

beforeAll(() => { (globalThis as Record<string, unknown>).AppIcon = () => <i />; });
beforeEach(() => { vi.clearAllMocks(); state.enabled = true; state.status = 'NOT_STARTED'; state.confirmed = false; state.token = 0;
  state.programme = 'MM_L6'; state.modulesRequired = false; state.modulesComplete = false; state.qualification = 'BA'; state.legacy = false; state.next = 'cv-job'; state.previous = false; state.save.mockResolvedValue(undefined); });

it('blocks Next and offers the assessment without waiting for any CV save', async () => {
  const navigate = vi.fn(); state.save.mockImplementation(() => new Promise(() => {}));
  render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).not.toHaveBeenCalled(); expect(state.save).not.toHaveBeenCalled(); expect(state.open).toHaveBeenCalled();
  expect(state.error).toHaveBeenCalledWith('Assessment required', 'Please complete the Programme Suitability Assessment before continuing.');
});

it.each(['NOT_STARTED', 'SUBMITTED'])('blocks a future sidebar step with unconfirmed state %s', async (status) => {
  state.status = status;
  const navigate = vi.fn();
  render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getAllByRole('tab', { name: /Future step/ })[0]);
  expect(navigate).not.toHaveBeenCalled();
});

it.each(['NOT_STARTED', 'SUBMITTED'])('does not render a future direct route with unconfirmed state %s', (status) => {
  state.status = status;
  render(<WizardShell currentIndex={1} mode="learner" onNavigateStep={vi.fn()} onFinish={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Please complete the Programme Suitability Assessment');
  expect(screen.getByRole('button', { name: 'Return to Programme Suitability & Modules' })).toBeInTheDocument();
});

it.each(['IN_PROGRESS', 'NOT_STARTED', 'SUBMITTED'])('keeps unconfirmed assessment state %s blocked, including after closing the modal', async (status) => {
  state.status = status; const navigate = vi.fn();
  render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).not.toHaveBeenCalled();
});

it.each(['cv-job', 'policies'])('advances to the dynamic next step %s only after the result Continue action', async (next) => {
  state.next = next; const navigate = vi.fn();
  const go = () => navigate(state.next);
  const { rerender } = render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={go} onFinish={vi.fn()} />);
  expect(navigate).not.toHaveBeenCalled();
  state.status = 'SUBMITTED';
  rerender(<WizardShell currentIndex={0} mode="learner" onNavigateStep={go} onFinish={vi.fn()} />);
  expect(navigate).not.toHaveBeenCalled();
  state.token = 1; state.confirmed = true;
  rerender(<WizardShell currentIndex={0} mode="learner" onNavigateStep={go} onFinish={vi.fn()} />);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(next));
  expect(navigate).toHaveBeenCalledTimes(1);
});

it('advances after confirmation independently of CV validation, then validates CV on leaving it', async () => {
  state.qualification = ''; const navigate = vi.fn();
  const { rerender } = render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  state.status = 'SUBMITTED'; state.confirmed = true; state.token = 1;
  rerender(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(1));
  navigate.mockClear();
  rerender(<WizardShell currentIndex={1} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).not.toHaveBeenCalled();
  expect(screen.getAllByText('Highest-level qualification').length).toBeGreaterThan(0);
  state.qualification = 'BA';
  rerender(<WizardShell currentIndex={1} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).toHaveBeenCalledWith(2);
  expect(state.open).not.toHaveBeenCalled();
});

it('restores submitted completion without forcing a repeat assessment', async () => {
  state.status = 'SUBMITTED'; state.confirmed = true; const navigate = vi.fn();
  render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).toHaveBeenCalledWith(1); expect(state.open).not.toHaveBeenCalled();
});

it('leaves previous steps accessible before assessment completion', async () => {
  state.previous = true; const navigate = vi.fn();
  render(<WizardShell currentIndex={1} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(navigate).toHaveBeenCalledWith(0);
});

it('does not newly require assessment for already submitted/completed applications', async () => {
  state.legacy = true; const navigate = vi.fn();
  render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' })); expect(navigate).toHaveBeenCalledWith(1);
});

it('includes assessment in the existing completion and direct-route guard while keeping CV text optional', () => {
  const steps = [{ slug: 'programme-suitability', label: 'Programme Suitability & Modules', builtin: true, hidden: false, items: [] }, { slug: 'cv-job', label: 'CV', builtin: true, hidden: false, items: [
    { key: 'cv.experience', builtin: true, hidden: false }, { key: 'cv.highestQualification', builtin: true, hidden: false },
  ] }, { slug: 'plr', label: 'PLR', builtin: true, hidden: false, items: [] }];
  const layout: WizardLayout = { steps, programmeSuitability: configuration };
  const draft = { cvJob: { experienceText: '', highestQualification: 'BA' }, programmeSuitability: { required: true, status: 'NOT_STARTED' } } as WizardDraft;
  expect(missingForStep(0, draft, layout)).toEqual(['Programme Suitability Assessment']);
  expect(maxReachableStep(draft, 3, layout)).toBe(0);
  draft.programmeSuitability!.status = 'SUBMITTED';
  expect(missingForStep(0, draft, layout)).toEqual(['Programme Suitability Assessment']);
  expect(maxReachableStep(draft, 3, layout)).toBe(0);
  draft.programmeSuitability!.selectionConfirmed = true;
  expect(missingForStep(0, draft, layout)).toEqual([]); expect(maxReachableStep(draft, 3, layout)).toBe(2);
  expect(missingForStep(1, draft, layout)).toEqual([]);
});


it('keeps confirmed PCP on the modules step, blocking Next and direct routes until valid choices are saved', async () => {
  state.status = 'SUBMITTED'; state.confirmed = true; state.programme = 'PCP_L6'; state.modulesRequired = true; state.token = 1;
  const navigate = vi.fn();
  const { rerender } = render(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  expect(navigate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).not.toHaveBeenCalled();
  expect(state.error).toHaveBeenCalledWith('Module choices required', expect.stringContaining('complete and save'));
  rerender(<WizardShell currentIndex={1} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('PCP pathway and module choices');
  state.modulesComplete = true;
  rerender(<WizardShell currentIndex={0} mode="learner" onNavigateStep={navigate} onFinish={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(navigate).toHaveBeenCalledWith(1);
});
