/**
 * First Learning Session: a Delivery apprentice's sidebar tab, locked until they
 * have signed all four compliance documents, then the page to book the session.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { LearnerFirstSession } from '@/api/learnerCalendar';
import { FIRST_SESSION_ROUTE, navItemsForStatus } from '@/hooks/useOnboardingRedirect';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '670' }) }));
const api = vi.hoisted(() => ({ fetchLearnerFirstSession: vi.fn() }));
vi.mock('@/api/learnerCalendar', () => api);
const documents = vi.hoisted(() => ({ agreement: vi.fn(), ilr: vi.fn(), plan: vi.fn(), written: vi.fn() }));
vi.mock('@/api/apprenticeshipAgreement', () => ({ fetchAgreement: documents.agreement }));
vi.mock('@/api/ilrDocument', () => ({ fetchIlrDocument: documents.ilr }));
vi.mock('@/api/trainingPlanDocument', () => ({ fetchTrainingPlanDocument: documents.plan }));
vi.mock('@/api/writtenAgreement', () => ({ fetchWrittenAgreement: documents.written }));
const history = vi.hoisted(() => vi.fn());
vi.mock('@/features/compliance-documents/revisionApi', () => ({ revisionHistory: history }));
vi.mock('@/components/feature/FirstSessionGate', () => ({
  Booking: () => <p>booking form</p>,
  Waiting: () => <p>booked session</p>,
}));

import LearnerFirstSessionPage from './page';

const tab = (items: { href?: string; locked?: boolean }[]) => items.find((i) => i.href === FIRST_SESSION_ROUTE);
const session = (overrides: Partial<LearnerFirstSession>): LearnerFirstSession =>
  ({ caseOwner: { name: 'Case Owner', email: 'owner@example.test' }, booked: false, event: null, startsOn: null, access: 'enrolling', canBook: false, ...overrides });

beforeEach(() => {
  vi.clearAllMocks();
  history.mockResolvedValue({ results: [], setupRequired: false });
  documents.agreement.mockResolvedValue({ agreement: null });
  documents.ilr.mockResolvedValue({ document: null });
  documents.plan.mockResolvedValue({ document: null });
  documents.written.mockResolvedValue({ document: null });
});

describe('the sidebar tab', () => {
  it('is locked for a Delivery apprentice until their documents are signed', () => {
    expect(tab(navItemsForStatus('Delivery', [], 'apprenticeship', false, false, true, false))).toMatchObject({ locked: true });
    expect(tab(navItemsForStatus('Delivery', [], 'apprenticeship', false, false, true, true))?.locked).toBeUndefined();
  });

  it('is only offered to Delivery apprentices', () => {
    expect(tab(navItemsForStatus('Onboarding', [], 'apprenticeship'))).toBeUndefined();
    expect(tab(navItemsForStatus('Active', [], 'apprenticeship'))).toBeUndefined();
    expect(tab(navItemsForStatus('Delivery', [], 'commercial'))).toBeUndefined();
  });
});

describe('the page', () => {
  const renderPage = () => render(<MemoryRouter><LearnerFirstSessionPage /></MemoryRouter>);

  it('stays locked until the learner has signed their documents', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    renderPage();

    expect(await screen.findByText('Sign your compliance documents first')).toBeInTheDocument();
    expect(await screen.findAllByRole('listitem', { name: /Not signed by you/ })).toHaveLength(4);
    expect(screen.queryByRole('listitem', { name: /: Signed by you/ })).not.toBeInTheDocument();
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
  });

  it('shows only confirmed learner signatures and keeps partial completion locked', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    documents.agreement.mockResolvedValue({ agreement: { signatures: { apprentice: { signed: true }, employer: { signed: false } } } });
    documents.ilr.mockResolvedValue({ document: { signatures: { learner: { signed: false }, provider: { signed: true } }, fullySigned: false } });
    documents.plan.mockResolvedValue({ document: { signatures: { apprentice: { signed: true } } } });
    renderPage();
    expect(await screen.findAllByRole('listitem', { name: /: Signed by you/ })).toHaveLength(2);
    expect(screen.getAllByRole('listitem', { name: /Not signed by you/ })).toHaveLength(2);
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
    for (const read of Object.values(documents)) expect(read).toHaveBeenCalledWith('670');
  });

  it('keeps other confirmed statuses visible when one document read fails', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    documents.agreement.mockResolvedValue({ agreement: { signatures: { apprentice: { signed: true } } } });
    documents.ilr.mockRejectedValue(new Error('Document read failed'));
    renderPage();
    expect(await screen.findByRole('listitem', { name: 'Individual Learner Record: Signature status unavailable' })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: 'Apprenticeship Agreement: Signed by you' })).toBeInTheDocument();
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
  });

  it('keeps the server restriction even when all learner signatures are present', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    documents.agreement.mockResolvedValue({ agreement: { signatures: { apprentice: { signed: true } } } });
    documents.ilr.mockResolvedValue({ document: { signatures: { learner: { signed: true } } } });
    documents.plan.mockResolvedValue({ document: { signatures: { apprentice: { signed: true } } } });
    documents.written.mockResolvedValue({ document: { signatures: { learner: { signed: true } } } });
    renderPage();
    expect(await screen.findAllByRole('listitem', { name: /: Signed by you/ })).toHaveLength(4);
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
  });

  it('reflects the latest independent revised document rather than the original or retained historical marks', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    documents.plan.mockResolvedValue({ document: { id: 'plan-source', signatures: { apprentice: { signed: false } } } });
    history.mockImplementation(async (_id, key) => ({ results: key === 'training-plan' ? [
      { sourceId: 'plan-source', number: 1, state: 'original', signatureMethod: 'original', signatures: { apprentice: { signed: false } } },
      { sourceId: 'plan-source', number: 3, state: 'finalized', signatureMethod: 'sign-again', signatures: { apprentice: { signed: true } } },
      { sourceId: 'different-source', number: 9, state: 'finalized', signatureMethod: 'sign-again', signatures: { apprentice: { signed: false } } },
      { sourceId: 'plan-source', number: 4, state: 'draft', signatureMethod: 'sign-again', signatures: { apprentice: { signed: false } } },
      { sourceId: 'plan-source', number: 2, state: 'finalized', signatureMethod: 'keep', signatures: { apprentice: { signed: false } } },
    ] : [], setupRequired: false }));
    renderPage();
    expect(await screen.findByRole('listitem', { name: 'Training Plan: Signed by you' })).toBeInTheDocument();
    expect(history).toHaveBeenCalledWith('670', 'training-plan');
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
    // A newly finalized fresh-signature revision becomes pending, even though
    // the preceding document was signed. No historical mark approves it.
    history.mockResolvedValue({ results: [{ sourceId: 'plan-source', number: 6, state: 'finalized', signatureMethod: 'sign-again', signatures: { apprentice: { signed: false } } }], setupRequired: false });
    fireEvent(window, new Event('learner-pending-signatures-changed'));
    expect(await screen.findByRole('listitem', { name: 'Training Plan: Not signed by you' })).toBeInTheDocument();
  });

  it('refreshes confirmed signatures on focus and does not show completion while a read is unknown', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    documents.agreement.mockResolvedValue({ agreement: { id: 'aa', signatures: { apprentice: { signed: false } } } });
    renderPage();
    await screen.findByRole('listitem', { name: 'Apprenticeship Agreement: Not signed by you' });
    documents.agreement.mockResolvedValue({ agreement: { id: 'aa', signatures: { apprentice: { signed: true } } } });
    fireEvent(window, new Event('focus'));
    expect(await screen.findByRole('listitem', { name: 'Apprenticeship Agreement: Signed by you' })).toBeInTheDocument();
    history.mockRejectedValue(new Error('History unavailable'));
    fireEvent(window, new Event('focus'));
    await waitFor(() => expect(screen.getByRole('listitem', { name: 'Apprenticeship Agreement: Signature status unavailable' })).toBeInTheDocument());
    expect(screen.queryByRole('listitem', { name: 'Apprenticeship Agreement: Signed by you' })).not.toBeInTheDocument();
  });

  it('preserves the compliance navigation action', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    render(<MemoryRouter initialEntries={['/learner/first-session']}><Routes>
      <Route path="/learner/first-session" element={<LearnerFirstSessionPage />} />
      <Route path="/learner/compliance-documents" element={<p>Compliance destination</p>} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Go to Compliance documents' }));
    expect(await screen.findByText('Compliance destination')).toBeInTheDocument();
  });

  it('offers the booking once they have', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: true }));
    renderPage();

    expect(await screen.findByText('booking form')).toBeInTheDocument();
    expect(api.fetchLearnerFirstSession).toHaveBeenCalledWith('apprenticeship', '670');
    expect(documents.agreement).not.toHaveBeenCalled();
  });

  it('shows the booked session once it is booked', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: true, booked: true, startsOn: '2026-10-05' }));
    renderPage();

    expect(await screen.findByText('booked session')).toBeInTheDocument();
  });
});
