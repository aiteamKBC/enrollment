import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { fetchInitialAssessments, autoLoginBksbAccount, launchInitialAssessment } = vi.hoisted(() => ({
  fetchInitialAssessments: vi.fn(),
  autoLoginBksbAccount: vi.fn(),
  launchInitialAssessment: vi.fn(),
}));

vi.mock('@/api/bksb', () => ({
  fetchInitialAssessments,
  autoLoginBksbAccount,
  launchInitialAssessment,
}));
vi.mock('../WizardContext.helpers', () => ({
  useWizard: () => ({ userId: '1601', isCommercial: false }),
}));
vi.mock('../layout/textsContext', () => ({
  useText: () => (key: string) => ({
    'block.initialAssessments.title': 'Initial Assessments',
    'block.initialAssessments.what': 'What is an Initial Assessment?',
    'block.initialAssessments.whatBody': 'Assessment information',
    'block.initialAssessments.why': 'Why Is This Important?',
    'block.initialAssessments.whyBody': 'Results information',
    'block.initialAssessments.completing': 'Completing Your Initial Assessment',
    'block.initialAssessments.completingBody': 'BKSB information',
  }[key] || key),
}));

import InitialAssessments from '../steps/InitialAssessments';

function response() {
  return {
    enabled: true,
    assessments: [
      { subject: 'english', status: 'error', error: 'BKSB returned HTTP 404.', directLaunchEnabled: false },
      { subject: 'maths', status: 'not_started', directLaunchEnabled: true },
    ],
  };
}

describe('Initial Assessments Auto-login fallback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchInitialAssessments.mockResolvedValue(response());
    vi.spyOn(window, 'open').mockReturnValue({
      closed: false,
      opener: null,
      close: vi.fn(),
      location: { replace: vi.fn(), href: '' },
    } as unknown as Window);
  });

  it('shows the account action and keeps ENG_NEW direct launch hidden', async () => {
    render(<InitialAssessments />);
    expect(await screen.findByRole('button', { name: 'Start English Assessment' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open BKSB Account' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Maths Assessment' })).toBeInTheDocument();
    expect(screen.getByText('Open BKSB to access your English course and complete your Initial Assessment.')).toBeInTheDocument();
    expect(screen.getByText('Open BKSB to access your Maths course and complete your Initial Assessment.')).toBeInTheDocument();
    expect(screen.queryByText(/BKSB access:/)).not.toBeInTheDocument();
  });

  it('navigates the reserved tab on a successful Auto-login response', async () => {
    const tab = { closed: false, opener: null, close: vi.fn(), location: { replace: vi.fn(), href: '' } };
    vi.mocked(window.open).mockReturnValue(tab as unknown as Window);
    autoLoginBksbAccount.mockResolvedValue({ url: 'https://login.example.test/one-time', expiresAt: 'soon' });
    render(<InitialAssessments />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start English Assessment' }));
    await waitFor(() => expect(autoLoginBksbAccount).toHaveBeenCalledWith('apprenticeship', '1601', 'english'));
    expect(tab.location.replace).toHaveBeenCalledWith('https://login.example.test/one-time');
    expect(tab.close).not.toHaveBeenCalled();
  });

  it('shows an opening placeholder instead of a blank about:blank tab', async () => {
    let resolveLogin!: (value: { url: string; expiresAt: string }) => void;
    const pendingLogin = new Promise<{ url: string; expiresAt: string }>((resolve) => {
      resolveLogin = resolve;
    });
    const tab = {
      closed: false,
      opener: null,
      close: vi.fn(),
      document: { title: '', body: { innerHTML: '' } },
      location: { replace: vi.fn(), href: '' },
    };
    vi.mocked(window.open).mockReturnValue(tab as unknown as Window);
    autoLoginBksbAccount.mockReturnValue(pendingLogin);
    render(<InitialAssessments />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start English Assessment' }));
    await waitFor(() => {
      expect(tab.document.title).toBe('Opening BKSB');
      expect(tab.document.body.innerHTML).toContain('Opening BKSB');
    });
    resolveLogin({ url: 'https://login.example.test/one-time', expiresAt: 'soon' });
    await waitFor(() => expect(tab.location.replace).toHaveBeenCalled());
  });

  it('closes the reserved tab when Auto-login fails', async () => {
    const tab = { closed: false, opener: null, close: vi.fn(), location: { replace: vi.fn(), href: '' } };
    vi.mocked(window.open).mockReturnValue(tab as unknown as Window);
    autoLoginBksbAccount.mockRejectedValue(new Error('BKSB is temporarily unavailable.'));
    render(<InitialAssessments />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start English Assessment' }));
    await waitFor(() => expect(tab.close).toHaveBeenCalled());
    expect(screen.getByRole('alert')).toHaveTextContent('BKSB is temporarily unavailable.');
  });

  it('keeps an unstarted assessment as Start even when the BKSB account is ready', async () => {
    fetchInitialAssessments.mockResolvedValueOnce({
      enabled: true,
      assessments: [
        { subject: 'english', status: 'not_started', bksbAccountReady: true, accessStatus: 'ready', directLaunchEnabled: false },
        { subject: 'maths', status: 'not_started', directLaunchEnabled: true },
      ],
    });
    render(<InitialAssessments />);
    expect(await screen.findByRole('button', { name: 'Start English Assessment' })).toBeInTheDocument();
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  });

  it('opens an unstarted Maths assessment through Auto-login with the Maths subject', async () => {
    autoLoginBksbAccount.mockResolvedValue({ url: 'https://login.example.test/maths', expiresAt: 'soon' });
    render(<InitialAssessments />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start Maths Assessment' }));
    await waitFor(() => expect(autoLoginBksbAccount).toHaveBeenCalledWith('apprenticeship', '1601', 'maths'));
    expect(launchInitialAssessment).not.toHaveBeenCalled();
  });

  it('shows Continue for an in-progress assessment', async () => {
    fetchInitialAssessments.mockResolvedValueOnce({
      enabled: true,
      assessments: [
        { subject: 'english', status: 'in_progress', bksbAccountReady: true, accessStatus: 'ready', directLaunchEnabled: false },
        { subject: 'maths', status: 'not_started', directLaunchEnabled: true },
      ],
    });
    render(<InitialAssessments />);
    expect(await screen.findByRole('button', { name: 'Continue English Assessment' })).toBeInTheDocument();
  });

  it('shows Review and only a verified categorical level for a completed assessment', async () => {
    fetchInitialAssessments.mockResolvedValueOnce({
      enabled: true,
      assessments: [
        { subject: 'english', status: 'completed', result: '2.6', level: 'Level 2', assessmentDate: '2026-11-01T10:00:00Z', bksbAccountReady: true, accessStatus: 'ready', directLaunchEnabled: false },
        { subject: 'maths', status: 'not_started', directLaunchEnabled: true },
      ],
    });
    autoLoginBksbAccount.mockResolvedValue({ url: 'https://login.example.test/review', expiresAt: 'soon' });
    render(<InitialAssessments />);
    expect(await screen.findByRole('button', { name: 'Review English Assessment' })).toBeInTheDocument();
    expect(screen.getByText('Review your completed assessment results and access your English course in BKSB.')).toBeInTheDocument();
    expect(screen.getByText('Level 2')).toBeInTheDocument();
    expect(screen.queryByText('2.6')).not.toBeInTheDocument();
    expect(screen.queryByText('Result')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review English Assessment' })).toHaveClass('primary-action');
    fireEvent.click(screen.getByRole('button', { name: 'Review English Assessment' }));
    await waitFor(() => expect(autoLoginBksbAccount).toHaveBeenCalledWith('apprenticeship', '1601', 'english'));
    expect(screen.queryByRole('button', { name: 'Continue English Assessment' })).not.toBeInTheDocument();
  });

  it('uses dashboard Auto-login for a completed Maths review instead of starting another attempt', async () => {
    fetchInitialAssessments.mockResolvedValueOnce({
      enabled: true,
      assessments: [
        { subject: 'english', status: 'not_started', directLaunchEnabled: false },
        { subject: 'maths', status: 'completed', level: 'Entry 3', bksbAccountReady: true, accessStatus: 'ready', directLaunchEnabled: true },
      ],
    });
    autoLoginBksbAccount.mockResolvedValue({ url: 'https://login.example.test/review', expiresAt: 'soon' });
    render(<InitialAssessments />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review Maths Assessment' }));
    expect(screen.getByText('Review your completed assessment results and access your Maths course in BKSB.')).toBeInTheDocument();
    await waitFor(() => expect(autoLoginBksbAccount).toHaveBeenCalledWith('apprenticeship', '1601', 'maths'));
    expect(launchInitialAssessment).not.toHaveBeenCalled();
  });

  it('does not classify an unrecognised provider measurement', async () => {
    fetchInitialAssessments.mockResolvedValueOnce({
      enabled: true,
      assessments: [
        { subject: 'english', status: 'completed', result: '2.6', level: '2.6', bksbAccountReady: true, accessStatus: 'ready', directLaunchEnabled: false },
        { subject: 'maths', status: 'not_started', directLaunchEnabled: true },
      ],
    });
    render(<InitialAssessments />);
    expect(await screen.findByText('Level pending verification')).toBeInTheDocument();
    expect(screen.queryByText('2.6')).not.toBeInTheDocument();
  });

  it('does not present the legacy English deep-link 404 as an assessment failure', async () => {
    render(<InitialAssessments />);
    expect(screen.getByText('Open BKSB to access your English course and complete your Initial Assessment.')).toBeInTheDocument();
    expect(screen.queryByText('BKSB returned HTTP 404.')).not.toBeInTheDocument();
    expect(screen.getAllByText('Not Started')).toHaveLength(2);
  });

  it('keeps the existing cards and hides transient errors on a return-to-tab refresh', async () => {
    render(<InitialAssessments />);
    expect(await screen.findByRole('button', { name: 'Start Maths Assessment' })).toBeInTheDocument();
    await waitFor(() => expect(fetchInitialAssessments).toHaveBeenCalledTimes(1));
    fetchInitialAssessments.mockRejectedValueOnce(new Error('temporary refocus failure'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    await waitFor(() => expect(fetchInitialAssessments).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: 'Start Maths Assessment' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
