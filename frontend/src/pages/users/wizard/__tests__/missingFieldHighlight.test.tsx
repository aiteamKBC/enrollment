/**
 * Pressing Next with a step unfinished marks each outstanding box in red, with
 * what to do under it — driven by the same list that blocks the move.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const fetchExtendedIlr = vi.fn();
vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: vi.fn().mockResolvedValue({ meta: { updatedAt: '' } }),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/curriculum', () => ({
  fetchKsbProfile: vi.fn().mockResolvedValue({ results: [] }),
  peekKsbProfile: () => undefined,
}));

import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '../WizardContext';
import { WizardShell } from '../WizardShell';
import { requiredMessage } from "../stepErrors.helpers";
import type { EnrolmentBoard } from '../../types';

const BOARD = {
  user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
  contact: { email: '', phone: '', dob: '', groupMembership: '', hasMandate: false },
  programme: { name: 'Test Programme', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
} as unknown as EnrolmentBoard;

function renderPersonalDetails(mode: 'learner' | 'staff', onNavigateStep = vi.fn()) {
  render(
    <ToastProvider>
      <WizardProvider userId="20" board={BOARD}>
        <WizardShell currentIndex={2} mode={mode} onNavigateStep={onNavigateStep} onFinish={vi.fn()} />
      </WizardProvider>
    </ToastProvider>
  );
  return onNavigateStep;
}

const box = (label: string) => screen.getByText(label, { selector: 'div' }).parentElement!.querySelector('input, select')!;

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  vi.clearAllMocks();
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
});

describe('missing answers after Next', () => {
  it('shows nothing red before the learner tries to move on', async () => {
    renderPersonalDetails('learner');
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    expect(box('Address')).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Please enter address.')).not.toBeInTheDocument();
  });

  it('marks every unanswered box red with what to enter, once Next is pressed', async () => {
    const onNavigateStep = renderPersonalDetails('learner');
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(box('Address')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Please enter address.')).toBeInTheDocument();
    expect(screen.getByText('Please select sex.')).toBeInTheDocument();
    expect(screen.getByText('Please sign here.')).toBeInTheDocument();
    expect(onNavigateStep).not.toHaveBeenCalled();
  });

  it('clears a box’s red as soon as it is filled in', async () => {
    renderPersonalDetails('learner');
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    await userEvent.type(box('Address'), '1 High Street');

    expect(box('Address')).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Please enter address.')).not.toBeInTheDocument();
    // The rest stay marked.
    expect(screen.getByText('Please select sex.')).toBeInTheDocument();
  });

  it('says what is wrong with a filled but badly formatted box', async () => {
    renderPersonalDetails('learner');
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());
    await userEvent.type(box('Email'), 'w');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(box('Email')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a valid email address, e.g. name@example.com')).toBeInTheDocument();
  });

  it('never marks the staff view, which is not gated', async () => {
    const onNavigateStep = renderPersonalDetails('staff');
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(onNavigateStep).toHaveBeenCalledWith(3);
    expect(screen.queryByText('Please enter address.')).not.toBeInTheDocument();
  });
});

describe('requiredMessage', () => {
  it('names the box in the sentence', () => {
    expect(requiredMessage('Line Manager name', 'enter')).toBe('Please enter line manager name.');
    expect(requiredMessage('ULN', 'enter')).toBe('Please enter ULN.');
  });

  it('falls back to a plain prompt for questions and instructions', () => {
    expect(requiredMessage('Please state your nationality', 'select')).toBe('Please select an option.');
    expect(requiredMessage('Do you have any caring responsibilities?', 'enter')).toBe('Please answer this question.');
    expect(requiredMessage('Care leaver', 'choose')).toBe('Please choose Yes or No.');
  });
});
