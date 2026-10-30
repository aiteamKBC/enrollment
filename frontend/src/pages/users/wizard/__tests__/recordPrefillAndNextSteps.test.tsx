/**
 * What an apprentice already gave before the wizard opened is offered back, and
 * Next Steps is not ticked off before the learner has reached it.
 *
 * A new apprentice enters their phone, address, date of birth and a reusable
 * signature on their first sign-in (learner_api/first_login_details.py). The
 * board carries those, and Personal Details fills its *blanks* from them —
 * never overwriting an answer already given in the wizard — and the filled-in
 * values are written on the next save rather than only shown.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';

const fetchExtendedIlr = vi.fn();
const saveExtendedIlr = vi.fn();

vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: (...args: unknown[]) => saveExtendedIlr(...args),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
// The Inclusion step's status comes from the Safeguarding database; not answered yet.
vi.mock('@/api/wizardInclusion', () => ({
  fetchInclusionStatus: vi.fn(async () => ({ applicable: true, completed: false, respondedAt: null })),
  SAFEGUARDING_URL: 'https://safeguarding.example/',
}));
const fetchKsbProfile = vi.fn();
vi.mock('@/api/curriculum', () => ({
  fetchKsbProfile: (...args: unknown[]) => fetchKsbProfile(...args),
  peekKsbProfile: () => undefined,
}));

import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '../WizardContext';
import { useWizard } from "../WizardContext.helpers";
import { WizardShell } from '../WizardShell';
import type { EnrolmentBoard } from '../../types';

const SIGNATURE = 'data:image/png;base64,SAVED';

function board(contact: Partial<EnrolmentBoard['contact']> = {}): EnrolmentBoard {
  return {
    user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
    contact: { email: 'test@example.com', phone: '', dob: '', groupMembership: '', hasMandate: false, ...contact },
    programme: { name: 'Test Programme', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
  } as unknown as EnrolmentBoard;
}

const FROM_FIRST_SIGN_IN = {
  phone: '07123 456789',
  address: '1 High Street, Canterbury, Kent, CT1 1AA',
  dob: '15/03/1990',
  savedSignature: SIGNATURE,
  savedSignatureDate: '2026-09-20',
};

let latest: ReturnType<typeof useWizard> | null = null;
function Probe() {
  latest = useWizard();
  return null;
}

function renderWizard(b: EnrolmentBoard, { isCommercial = false, currentIndex = 2, mode = 'learner' as 'learner' | 'staff' } = {}) {
  render(
    <ToastProvider>
      <WizardProvider userId="20" board={b} isCommercial={isCommercial}>
        <WizardShell currentIndex={currentIndex} mode={mode} onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        <Probe />
      </WizardProvider>
    </ToastProvider>
  );
}

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  vi.clearAllMocks();
  latest = null;
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  saveExtendedIlr.mockResolvedValue({ meta: { updatedAt: '2026-09-27T00:00:00Z' } });
  fetchKsbProfile.mockResolvedValue({ results: [] });
});

describe('Personal Details pre-fill from the learner record', () => {
  it('fills a new apprentice’s phone, address, date of birth and signature', async () => {
    renderWizard(board(FROM_FIRST_SIGN_IN));
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    const pd = latest!.draft.personalDetails;
    expect(pd.phone).toBe('07123 456789');
    expect(pd.address).toBe('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(pd.dob).toBe('1990-03-15');
    expect(pd.age).toBeGreaterThan(30);
    expect(pd.signature).toBe(SIGNATURE);
    expect(pd.signatureDate).toBe('2026-09-20');
  });

  it('fills only the blanks of a saved draft, keeping what was answered in the wizard', async () => {
    fetchExtendedIlr.mockResolvedValue({
      answers: null,
      draft: {
        personalDetails: {
          firstName: 'Test', lastName: 'Learner', email: 'test@example.com',
          phone: '07999 000111', address: '', dob: '', sex: 'Female',
        },
      },
      meta: { updatedAt: '2026-09-26T10:00:00Z' },
    });
    renderWizard(board(FROM_FIRST_SIGN_IN));

    await waitFor(() => expect(latest!.draft.personalDetails.sex).toBe('Female'));
    const pd = latest!.draft.personalDetails;
    expect(pd.phone).toBe('07999 000111');
    expect(pd.address).toBe('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(pd.dob).toBe('1990-03-15');
    expect(pd.signature).toBe(SIGNATURE);
  });

  it('writes the filled-in values on the next save instead of treating them as already saved', async () => {
    fetchExtendedIlr.mockResolvedValue({
      answers: null,
      draft: { personalDetails: { firstName: 'Test', lastName: 'Learner', email: 'test@example.com', phone: '', address: '', dob: '', sex: 'Female' } },
      meta: { updatedAt: '2026-09-26T10:00:00Z' },
    });
    renderWizard(board(FROM_FIRST_SIGN_IN));
    await waitFor(() => expect(latest!.draft.personalDetails.address).not.toBe(''));

    await latest!.saveIlr();

    expect(saveExtendedIlr).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(saveExtendedIlr.mock.calls[0])).toContain('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(JSON.stringify(saveExtendedIlr.mock.calls[0])).toContain(SIGNATURE);
  });

  it('leaves a commercial learner’s form alone', async () => {
    renderWizard(board({ address: '1 High Street', savedSignature: SIGNATURE }), { isCommercial: true });
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    expect(latest!.draft.personalDetails.address).toBe('');
    expect(latest!.draft.personalDetails.signature).toBeUndefined();
  });
});

describe('Next Steps in the step rail', () => {
  it('is locked and not counted as complete before the earlier steps are done', async () => {
    renderWizard(board());
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('Locked')).toBeInTheDocument());
    expect(within(tab).queryByText('Complete')).toBeNull();
    expect(tab.querySelector('.ri-check-line')).toBeNull();
    // Only the Introduction, Before You Begin, the Personal Learning Record
    // (nothing required) and the Skills Radar (no competencies on this
    // programme) count so far — Next Steps would make it 5. The Inclusion
    // assessment is not answered.
    expect(screen.getByText('4 of 11 steps complete')).toBeInTheDocument();
  });

  it('shows its number rather than a tick for staff, who are not gated', async () => {
    renderWizard(board(), { mode: 'staff' });
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('Not started')).toBeInTheDocument());
    expect(within(tab).getByText('11')).toBeInTheDocument();
    expect(tab.querySelector('.ri-check-line')).toBeNull();
  });

  it('does not count as complete just by being opened while earlier steps are unfinished', async () => {
    renderWizard(board(), { mode: 'staff', currentIndex: 10 });
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('In progress')).toBeInTheDocument());
    expect(tab.querySelector('.ri-check-line')).toBeNull();
  });
});
