/**
 * The Inclusion step, between the Skills Radar and the Policies: apprenticeship
 * learners take the inclusion assessment on the Safeguarding site in a new tab,
 * and the Policies stay locked until the Safeguarding database holds a response
 * for one of their emails. Commercial learners never see the step; staff see
 * its status only.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ToastProvider } from '@/hooks/useToast';

const fetchExtendedIlr = vi.fn();
const saveExtendedIlr = vi.fn();
vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: (...args: unknown[]) => saveExtendedIlr(...args),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: vi.fn(async () => ({ results: [] })), peekKsbProfile: () => undefined }));
const fetchInclusionStatus = vi.fn();
vi.mock('@/api/wizardInclusion', () => ({
  fetchInclusionStatus: (...args: unknown[]) => fetchInclusionStatus(...args),
  SAFEGUARDING_URL: 'https://safeguarding.example/',
}));

import { WizardProvider } from '../WizardContext';
import { useWizard } from "../WizardContext.helpers";
import { WizardShell } from '../WizardShell';
import { layoutForLearner, resolveLayout, defaultLayout, visibleSteps } from '../layout/resolve';
import type { EnrolmentBoard } from '../../types';

const BOARD = {
  user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
  contact: { email: '', phone: '', dob: '', groupMembership: '', hasMandate: false },
  programme: { name: 'Test Programme', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
} as unknown as EnrolmentBoard;

const NOT_DONE = { applicable: true, completed: false, respondedAt: null };
const DONE = { applicable: true, completed: true, respondedAt: '2026-10-15T09:30:00Z' };

let latest: ReturnType<typeof useWizard> | null = null;
function Spy() {
  latest = useWizard();
  return null;
}

function renderWizard({ mode = 'learner', isCommercial = false, currentIndex = 8 }: { mode?: 'learner' | 'staff'; isCommercial?: boolean; currentIndex?: number } = {}) {
  const onNavigateStep = vi.fn();
  render(
    <ToastProvider>
      <WizardProvider userId="20" board={BOARD} isCommercial={isCommercial} readOnlyLearnerSteps={mode === 'staff'}>
        <WizardShell currentIndex={currentIndex} mode={mode} onNavigateStep={onNavigateStep} onFinish={vi.fn()} />
        <Spy />
      </WizardProvider>
    </ToastProvider>,
  );
  return onNavigateStep;
}

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  latest = null;
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  saveExtendedIlr.mockResolvedValue({ meta: { updatedAt: '2026-10-15T00:00:00Z' } });
  fetchInclusionStatus.mockResolvedValue(NOT_DONE);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('the Inclusion step', () => {
  it('sits between the Skills Radar and the Policies, and keeps the Policies locked until it is done', async () => {
    renderWizard();
    const policies = await screen.findByRole('tab', { name: 'Policies' });
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent ?? '');
    const radar = tabs.findIndex((t) => t.includes('Skills Radar'));
    expect(tabs[radar + 1]).toContain('Inclusion');
    expect(tabs[radar + 2]).toContain('Policies');
    await waitFor(() => expect(policies).toHaveAttribute('aria-disabled', 'true'));
    expect(fetchInclusionStatus).toHaveBeenCalledWith('apprenticeship', '20');
  });

  it('opens the Safeguarding site in a new tab, with no email in the link', async () => {
    renderWizard();
    const start = await screen.findByRole('link', { name: /Start inclusion assessment/ });
    expect(start).toHaveAttribute('href', 'https://safeguarding.example/');
    expect(start).toHaveAttribute('target', '_blank');
    expect(start).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(start.getAttribute('href')).not.toMatch(/@|email/i);
    expect(await screen.findByText('You have not completed the inclusion assessment yet.')).toBeInTheDocument();
  });

  it('counts as complete once a check finds the response', async () => {
    renderWizard();
    await screen.findByText('You have not completed the inclusion assessment yet.');
    const inclusionTab = screen.getByRole('tab', { name: 'Inclusion' });
    expect(within(inclusionTab).queryByText('Complete')).toBeNull();
    fetchInclusionStatus.mockResolvedValue(DONE);
    fireEvent.click(screen.getByRole('button', { name: /I have finished — check again/ }));

    expect(await screen.findByText('Thank you, we have received your inclusion assessment.')).toBeInTheDocument();
    expect(screen.getByText('Received 15 Oct 2026')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Start inclusion assessment/ })).not.toBeInTheDocument();
    // Reaching the Policies also needs the earlier steps: see learnerStepGating.test.tsx.
    await waitFor(() => expect(within(inclusionTab).getByText('Complete')).toBeInTheDocument());
  });

  it('checks again when the learner comes back to the tab', async () => {
    renderWizard();
    await screen.findByText('You have not completed the inclusion assessment yet.');
    expect(fetchInclusionStatus).toHaveBeenCalledTimes(1);
    fetchInclusionStatus.mockResolvedValue(DONE);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(await screen.findByText('Thank you, we have received your inclusion assessment.')).toBeInTheDocument();
    expect(fetchInclusionStatus).toHaveBeenCalledTimes(2);
  });

  it('shows a failed check and does not count the step as done', async () => {
    fetchInclusionStatus.mockRejectedValue(new Error('We could not check the inclusion assessment just now.'));
    renderWizard();
    expect(await screen.findByText('We could not check the inclusion assessment just now.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Policies' })).toHaveAttribute('aria-disabled', 'true'));
    expect(screen.getByRole('button', { name: /check again/ })).toBeEnabled();
  });

  it('shows staff the status only — the site would sign in the member of staff', async () => {
    fetchInclusionStatus.mockResolvedValue(DONE);
    renderWizard({ mode: 'staff' });
    expect(await screen.findByText('The learner has completed the inclusion assessment.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Start inclusion assessment/ })).not.toBeInTheDocument();
  });

  it('is skipped by commercial learners', async () => {
    fetchInclusionStatus.mockResolvedValue({ applicable: false, completed: false, respondedAt: null });
    renderWizard({ isCommercial: true, currentIndex: 2 });
    await screen.findByRole('tab', { name: 'Policies' });
    await waitFor(() => expect(fetchInclusionStatus).toHaveBeenCalledWith('commercial', '20'));
    expect(screen.queryByRole('tab', { name: 'Inclusion' })).not.toBeInTheDocument();
  });

  it('is never written with the saved answers, and a saved status is not trusted', async () => {
    fetchInclusionStatus.mockRejectedValue(new Error('unreachable'));
    // A draft written by hand through the API, claiming the assessment is done.
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { inclusion: DONE }, meta: { updatedAt: '' } });
    renderWizard();
    await screen.findByText('unreachable');
    expect(latest!.draft.inclusion).toBeUndefined();
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Policies' })).toHaveAttribute('aria-disabled', 'true'));

    fetchInclusionStatus.mockResolvedValue(DONE);
    fireEvent.click(screen.getByRole('button', { name: /check again/ }));
    await screen.findByText('Thank you, we have received your inclusion assessment.');
    // An answer of the learner's, so there is something to write.
    act(() => latest!.setSection('policies', { ...latest!.draft.policies, acknowledged: { 'kbc-1': true } }));
    await act(() => latest!.saveIlr());
    expect(saveExtendedIlr).toHaveBeenCalled();
    const [, , , draft] = saveExtendedIlr.mock.calls.at(-1)!;
    expect(draft).not.toHaveProperty('inclusion');
  });
});

describe('the Inclusion step in a layout', () => {
  it('goes after the Skills Radar in a layout published before it existed', () => {
    const published = defaultLayout();
    published.steps = published.steps.filter((s) => s.slug !== 'inclusion');
    const slugs = visibleSteps(resolveLayout(published)).map((s) => s.slug);
    expect(slugs.slice(slugs.indexOf('skills-radar'), slugs.indexOf('skills-radar') + 3)).toEqual(['skills-radar', 'inclusion', 'policies']);
  });

  it('is hidden for commercial learners wherever its block sits', () => {
    const layout = defaultLayout();
    // An admin moved the block onto the Policies step.
    const block = layout.steps.find((s) => s.slug === 'inclusion')!.items.pop()!;
    layout.steps.find((s) => s.slug === 'policies')!.items.push(block);
    const commercial = layoutForLearner(layout, true);
    expect(visibleSteps(commercial).map((s) => s.slug)).not.toContain('inclusion');
    expect(commercial.steps.find((s) => s.slug === 'policies')!.items.find((i) => i.key === 'block.inclusion')!.hidden).toBe(true);
    expect(layoutForLearner(layout, false)).toBe(layout);
  });
});

