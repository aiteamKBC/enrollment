import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { AuthUser } from '@/api/auth';
import { clearAllCachedResources } from '@/api/cachedRequest';
import type { PendingSignature } from '../api';
import { pendingSignaturesChanged, PENDING_SIGNATURES_STORAGE_KEY } from '../events';
import { SIGNATURE_POLL_MS } from '../usePendingSignatures';
import { DocumentSignatureTarget } from '../DocumentSignatureTarget';
import { signatureTargetId } from '../targets';

const state = vi.hoisted(() => ({ account: null as AuthUser | null, logout: vi.fn() }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: state.account }, logout: state.logout }) }));
import { WorkspaceShell } from '@/components/feature/EnrollmentShell';
import { signAgreement } from '@/api/apprenticeshipAgreement';

const learner = (id = 7): AuthUser => ({ id, role: 'learner', subjectId: id, subjectType: 'learner', displayName: 'Synthetic Learner', learnerType: 'apprenticeship', email: 'synthetic@example.test', hasPassword: true, lastLoginAt: null, permissions: [] });
const item = (id: string, key = 'apprenticeship-agreement', revisionId: string | null = null): PendingSignature => ({ id: revisionId ? `revision:${revisionId}` : `document:${key}:${id}`, documentKey: key, documentId: id, revisionId, revisionNumber: revisionId ? 2 : 1, title: key === 'training-plan' ? 'Training Plan' : 'Apprenticeship Agreement', category: 'Compliance', templateVersion: 1, issuedAt: '2030-01-01T12:00:00Z', status: 'pending', requiresLearnerSignature: true, href: '/learner/compliance-documents?' + new URLSearchParams({ signature: signatureTargetId(key, id, revisionId || undefined)! }) });
let items: PendingSignature[], failSign: boolean;
const fetcher = vi.fn();
function Host() {
  const location = useLocation();
  return <WorkspaceShell pageTitle="Learner portal" role="learner" roleLabel="Learner" navItems={[]}>
    <p data-testid="location">{location.pathname}{location.search}</p>
    <DocumentSignatureTarget documentKey="apprenticeship-agreement" sourceId="aa-1"><button onClick={() => { void signAgreement(7, 'apprentice', 'Synthetic Learner', 'data:image/png;synthetic').catch(() => {}); }}>Existing Sign action</button><button>Preview original</button></DocumentSignatureTarget>
    <DocumentSignatureTarget documentKey="apprenticeship-agreement" sourceId="aa-1" revisionId="r2"><button>Sign revised document</button></DocumentSignatureTarget>
  </WorkspaceShell>;
}
const view = () => <MemoryRouter initialEntries={['/learner/first-session']}><Host /></MemoryRouter>;
beforeEach(() => {
  clearAllCachedResources(); state.account = learner(); state.logout.mockClear(); failSign = false;
  items = [item('aa-1'), item('tp-1', 'training-plan')];
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  fetcher.mockReset(); fetcher.mockImplementation(async (url: string) => {
    if (url.includes('/sign/')) {
      if (failSign) return new Response(JSON.stringify({ error: 'Signature failed' }), { status: 503 });
      items = items.filter((i) => i.documentId !== 'aa-1'); return new Response(JSON.stringify({ agreement: { signatures: { apprentice: { signed: true } } } }));
    }
    return new Response(JSON.stringify({ count: items.length, results: items }));
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); clearAllCachedResources(); });

const badges = async (count: number) => {
  expect(await screen.findByLabelText(`${count} pending learner signatures`)).toHaveTextContent(String(count));
  expect(screen.getByLabelText(`${count} documents requiring your signature`)).toHaveTextContent(String(count));
};
describe('learner signature notifications', () => {
  it('shows the bell next to the learner, with matching sidebar count on any learner page', async () => {
    render(view()); await badges(2);
    const header = screen.getByRole('banner'); expect(within(header).getByText('Synthetic Learner')).toBeInTheDocument();
    expect(within(header).getByRole('button', { name: 'Signature notifications' })).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/learner/first-session');
  });
  it('opening/closing notifications changes neither pending count nor read status', async () => {
    render(view()); await badges(2); fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    const dialog = screen.getByRole('dialog', { name: 'Notifications' }); expect(within(dialog).getByText('Training Plan')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Your signature is required.')).toHaveLength(2);
    fireEvent.pointerDown(document.body); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await badges(2); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('navigates to and highlights the exact document without signing or removing it', async () => {
    render(view()); await badges(2); fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    fireEvent.click(screen.getByRole('link', { name: 'Review & Sign — Apprenticeship Agreement' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/learner/compliance-documents?signature=signature-apprenticeship-agreement-aa-1');
    const target = document.getElementById('signature-apprenticeship-agreement-aa-1'); expect(target).toHaveFocus();
    expect(target).toHaveAttribute('data-signature-target', 'true'); expect(within(target!).getByRole('button', { name: 'Existing Sign action' })).toBeInTheDocument();
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/sign/'))).toBe(true); await badges(2);
  });
  it('uses the exact revision target instead of the original', async () => {
    items = [item('aa-1', 'apprenticeship-agreement', 'r2')]; render(view()); await badges(1);
    fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    fireEvent.click(screen.getByRole('link', { name: 'Review & Sign — Apprenticeship Agreement R2' }));
    expect(document.getElementById('signature-revision-r2')).toHaveFocus();
    expect(document.getElementById('signature-apprenticeship-agreement-aa-1')).not.toHaveAttribute('data-signature-target');
    await badges(1);
  });
  it('revalidates both counts only after the existing successful Sign action', async () => {
    render(view()); await badges(2); fireEvent.click(screen.getByRole('button', { name: 'Existing Sign action' }));
    await badges(1); fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    expect(within(screen.getByRole('dialog')).queryByText('Apprenticeship Agreement')).not.toBeInTheDocument();
    expect(screen.getByText('Training Plan')).toBeInTheDocument();
  });
  it('a failed signature leaves both badges unchanged', async () => {
    failSign = true; render(view()); await badges(2); fireEvent.click(screen.getByRole('button', { name: 'Existing Sign action' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2)); await badges(2);
  });
  it('hides both numbers at zero while keeping the bell and empty dropdown', async () => {
    items = []; render(view()); await waitFor(() => expect(fetcher).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    expect(await screen.findByText('No signatures required.')).toBeInTheDocument();
    expect(screen.queryByLabelText(/pending learner signatures/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/documents requiring your signature/)).not.toBeInTheDocument();
  });
  it('refreshes after issuance in another tab and on focus', async () => {
    render(view()); await badges(2); items.push(item('wa-1', 'written-agreement'));
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: PENDING_SIGNATURES_STORAGE_KEY, newValue: 'revalidate' })));
    await badges(3); items = [items[0]]; act(() => window.dispatchEvent(new Event('focus'))); await badges(1);
  });
  it('polls for admin issuance without a manual page refresh', async () => {
    vi.useFakeTimers(); render(view()); await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByLabelText('2 pending learner signatures')).toBeInTheDocument();
    items.push(item('new', 'written-agreement')); await act(async () => { await vi.advanceTimersByTimeAsync(SIGNATURE_POLL_MS); });
    expect(screen.getByLabelText('3 pending learner signatures')).toBeInTheDocument();
    expect(screen.getByLabelText('3 documents requiring your signature')).toBeInTheDocument();
  });
  it('Escape closes and restores keyboard focus; Tab/outside also dismiss', async () => {
    render(view()); await badges(2); const bell = screen.getByRole('button', { name: 'Signature notifications' });
    fireEvent.keyDown(bell, { key: 'ArrowDown' });
    await waitFor(() => expect(screen.getByRole('link', { name: 'Review & Sign — Apprenticeship Agreement' })).toHaveFocus());
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' }); await waitFor(() => expect(screen.getByRole('link', { name: 'Review & Sign — Training Plan' })).toHaveFocus());
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' }); expect(bell).toHaveFocus(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('does not add notifications to the admin header or make an admin request', async () => {
    state.account = { ...learner(), role: 'admin', subjectType: 'staff' }; render(view());
    expect(screen.queryByRole('button', { name: 'Signature notifications' })).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('clears on logout and never shows the previous learner on account change', async () => {
    const ui = render(view()); await badges(2);
    state.account = null; ui.rerender(view()); expect(screen.queryByRole('button', { name: 'Signature notifications' })).not.toBeInTheDocument();
    items = []; state.account = learner(99); ui.rerender(view());
    expect(screen.queryByLabelText('2 pending learner signatures')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' })); expect(await screen.findByText('No signatures required.')).toBeInTheDocument();
  });
  it('failed refresh retains the last confirmed obligation and shows Retry', async () => {
    render(view()); await badges(2); fetcher.mockRejectedValueOnce(new Error('Offline'));
    act(() => pendingSignaturesChanged());
    fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' })); expect(await screen.findByRole('alert')).toHaveTextContent('Offline');
    await badges(2); fireEvent.click(screen.getByRole('button', { name: 'Retry' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });
  it('ignores an in-flight response from a previous learner session', async () => {
    let finish!: (value: Response) => void;
    const previous = [...items]; fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const ui = render(view()); state.account = learner(99); items = [item('new-person-plan', 'training-plan')]; ui.rerender(view());
    await badges(1);
    await act(async () => { finish(new Response(JSON.stringify({ count: previous.length, results: previous }))); });
    await badges(1); fireEvent.click(screen.getByRole('button', { name: 'Signature notifications' }));
    expect(screen.queryByText('Apprenticeship Agreement')).not.toBeInTheDocument();
  });
});
