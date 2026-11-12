import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import { WizardShell } from '@/pages/users/wizard/WizardShell';
import { defaultLayout, resolveLayout } from '@/pages/users/wizard/layout/resolve';
import type { WizardLayout } from '@/pages/users/wizard/layout/types';
import type { EnrolmentBoard } from '@/pages/users/types';
import { confirmedPcp, pcpModules } from './pcpRadarFixtures';

const api = vi.hoisted(() => ({ layout: vi.fn(), read: vi.fn(), write: vi.fn(), assessment: vi.fn(), modules: vi.fn(), saveModules: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: api.read, peekExtendedIlr: () => undefined, saveExtendedIlr: api.write }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: vi.fn(async () => ({ results: [] })), peekKsbProfile: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: false, completed: false })) }));
vi.mock('@/api/wizardLayout', () => ({ fetchWizardLayout: api.layout, peekWizardLayout: () => undefined }));
vi.mock('../api', () => ({ getAssessment: api.assessment, peekAssessment: () => undefined, primeAssessment: vi.fn(),
  prefetchAssessmentSecurity: vi.fn(async () => ({ csrfToken: 'synthetic' })), saveAssessment: vi.fn(), saveReview: vi.fn() }));
vi.mock('../modules/api', () => ({ getModules: api.modules, saveModuleSelection: api.saveModules }));

const board = { user: { id: '41', name: 'Synthetic learner' }, contact: { email: '', phone: '', dob: '' },
  programme: { name: 'Project Controls Professional Level 6', onboardingStatus: 'In progress' } } as EnrolmentBoard;
let latest: ReturnType<typeof useWizard>;
function Step() {
  latest = useWizard();
  return <WizardShell currentIndex={latest.steps.findIndex(step => step.slug === 'programme-suitability')}
    mode="learner" onNavigateStep={vi.fn()} onFinish={vi.fn()} />;
}
function page(options: { layout?: WizardLayout; staff?: boolean; portal?: boolean } = {}) {
  return <ToastProvider><WizardProvider userId="41" board={board} layout={options.layout} readOnlyLearnerSteps={options.staff}
    recoverSavedAssessment={!options.staff && options.portal !== false}>
    <Step />
  </WizardProvider></ToastProvider>;
}
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  vi.stubGlobal('AppIcon', () => <i />);
  api.layout.mockResolvedValue({ layout: defaultLayout(), version: 14 });
  api.read.mockResolvedValue({ answers: null, draft: { cvJob: { experienceText: 'Keep saved CV answer' } }, meta: { updatedAt: 'saved' } });
  api.assessment.mockResolvedValue(structuredClone(confirmedPcp));
  api.modules.mockResolvedValue(pcpModules());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function savedResultIsVisible() {
  expect(await screen.findByText('Your Recommended Programme')).toBeVisible();
  expect(await screen.findByRole('region', { name: 'Programme Modules' })).toBeVisible();
  expect(latest.suitability?.state).toEqual(confirmedPcp);
  await waitFor(() => expect(latest.draft.programmeModules).toEqual(pcpModules().savedSelection));
  expect(latest.draft.cvJob.experienceText).toBe('Keep saved CV answer');
  expect(api.write).not.toHaveBeenCalled();
  expect(api.saveModules).not.toHaveBeenCalled();
}

it.each([false, undefined])('restores result and confirmed modules when a legacy step has builtin=%s', async builtin => {
  const layout = defaultLayout(), step = layout.steps.find(item => item.slug === 'programme-suitability')!;
  Object.assign(step, { builtin });
  api.layout.mockResolvedValue({ layout, version: 14 });
  render(page());
  await savedResultIsVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Preview Assessment' }));
  expect(await screen.findByRole('dialog')).toBeVisible();
});

it.each(['missing', 'failed'])('loads the saved assessment snapshot when the wizard layout is %s', async status => {
  if (status === 'failed') api.layout.mockRejectedValue(new Error('Layout read failed'));
  else api.layout.mockResolvedValue({ layout: null, version: null });
  render(page());
  await savedResultIsVisible();
  expect(api.assessment).toHaveBeenCalled();
});

it('shows assessment read errors and a working Retry instead of an empty step', async () => {
  api.layout.mockResolvedValue({ layout: null, version: null });
  api.assessment.mockRejectedValueOnce(new Error('Assessment read failed'));
  render(page());
  expect(await screen.findByRole('alert')).toHaveTextContent('Assessment read failed');
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await savedResultIsVisible();
});

it('keeps a local Builder preview without assessment configuration offline', async () => {
  render(page({ layout: defaultLayout() }));
  await waitFor(() => expect(latest.ready).toBe(true));
  expect(api.layout).not.toHaveBeenCalled();
  expect(api.assessment).not.toHaveBeenCalled();
  expect(api.modules).not.toHaveBeenCalled();
});

it('does not activate learner snapshot fallback for an unconfigured staff review', async () => {
  api.layout.mockResolvedValue({ layout: null, version: null });
  render(page({ staff: true }));
  await waitFor(() => expect(latest.ready).toBe(true));
  expect(api.assessment).not.toHaveBeenCalled();
  expect(api.modules).not.toHaveBeenCalled();
});

it('leaves unconfigured standalone wizard forms unchanged outside the learner portal', async () => {
  api.layout.mockResolvedValue({ layout: null, version: null });
  render(page({ portal: false }));
  await waitFor(() => expect(latest.ready).toBe(true));
  expect(api.assessment).not.toHaveBeenCalled();
  expect(api.modules).not.toHaveBeenCalled();
});

it('normalises only the reserved suitability identity, preserving layout text, items, order and custom steps', () => {
  const saved: WizardLayout = { steps: [
    { slug: 'custom-team', label: 'Team questions', builtin: false, hidden: false, items: [] },
    { slug: 'programme-suitability', label: 'Published programme heading', builtin: false, hidden: false,
      items: [{ key: 'cf_old', builtin: false, hidden: false, type: 'description', label: 'Retained configured guidance' }] },
  ], texts: { 'block.introduction.title': 'Published welcome' } };
  const before = structuredClone(saved), resolved = resolveLayout(saved);
  expect(resolved.steps.find(step => step.slug === 'programme-suitability')).toEqual({ ...saved.steps[1], builtin: true });
  expect(resolved.steps.find(step => step.slug === 'custom-team')).toEqual(saved.steps[0]);
  expect(resolved.steps.indexOf(resolved.steps.find(step => step.slug === 'custom-team')!))
    .toBeLessThan(resolved.steps.indexOf(resolved.steps.find(step => step.slug === 'programme-suitability')!));
  expect(resolved.texts).toEqual(saved.texts);
  expect(saved).toEqual(before);
});
