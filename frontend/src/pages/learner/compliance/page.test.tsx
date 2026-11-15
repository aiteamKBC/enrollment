import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Page from './page';

const state = vi.hoisted(() => ({ signed: false, remaining: false, fail: false, role: 'learner', invalidate: vi.fn() }));
const revisions = vi.hoisted(() => ({ revisionHistory: vi.fn(), readRevision: vi.fn(), signRevision: vi.fn() }));
vi.mock('@/features/compliance-documents/revisionApi', () => revisions);
vi.mock('@/features/compliance-documents/LearnerDocumentRevisions', () => ({ LearnerDocumentRevisions: () => <p>Version history retained</p> }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '125' }) }));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ auth: { account: { role: state.role } } }) }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/api/learnerDetail', () => ({ invalidateLearnerDetailCache: state.invalidate }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('./DocumentCard', () => ({ Field: () => null, FieldGroup: ({ children }: { children: React.ReactNode }) => <>{children}</>, DocumentCard: ({ title, onSign, learner, currentVersion }: { title: string; onSign: (mark: string) => void; learner?: { signed: boolean }; currentVersion?: number }) => <article><p>{title}: {learner?.signed ? 'Signed' : 'Pending'}{currentVersion ? ` (V${currentVersion})` : ''}</p><button onClick={() => onSign('Signature')}>{title}</button></article> }));
vi.mock('@/api/apprenticeshipAgreement', () => ({ fmtAgreementDate: () => '', fetchAgreement: async () => ({
  agreement: { particulars: {}, signatures: { apprentice: { signed: true } } }, particulars: { apprenticeName: 'Learner' }, meta: {},
}) }));
vi.mock('@/api/ilrDocument', () => ({ fetchIlrDocument: async () => ({ learner: { name: 'Learner' }, document: {
  learnerDetails: {}, signatures: { learner: { signed: !state.remaining } },
} }) }));
vi.mock('@/api/trainingPlanDocument', () => ({ fetchTrainingPlanDocument: async () => ({ learner: { name: 'Learner' }, document: {
  programme: {}, learningPlan: [], signatures: { apprentice: { signed: true } },
} }) }));
vi.mock('@/api/writtenAgreement', () => ({ fetchWrittenAgreement: async () => ({ learner: { name: 'Learner' }, meta: {}, document: {
  id: 'written-root', particulars: {}, signatures: { learner: { signed: state.signed } },
} }), signWrittenAgreement: async () => { if (state.fail) throw new Error('Offline'); state.signed = true; } }));

beforeEach(() => { state.signed = false; state.remaining = false; state.fail = false; state.role = 'learner'; state.invalidate.mockClear(); revisions.revisionHistory.mockResolvedValue({ results: [] }); revisions.signRevision.mockReset(); });
afterEach(cleanup);
function setup() {
  render(<MemoryRouter initialEntries={['/learner/compliance-documents']}><Routes>
    <Route path="/learner/compliance-documents" element={<Page />} />
    <Route path="/learner/first-session" element={<h1>First session destination</h1>} />
  </Routes></MemoryRouter>);
}
it('shows signed V3 even when the original is unsigned, and signs only V3 when it is pending', async () => {
  revisions.revisionHistory.mockResolvedValue({ results: [{ id: 'v3', sourceId: 'written-root', number: 3, state: 'finalized' }] });
  const detail = (signed: boolean) => ({ revision: { id: 'v3', number: 3, signatureMethod: 'sign-again' }, preview: { data: { signatures: { learner: { signed } } } } });
  revisions.readRevision.mockResolvedValue(detail(true));
  setup();
  expect(await screen.findByText('Written Agreement: Signed (V3)')).toBeInTheDocument();
  expect(state.signed).toBe(false);
  cleanup();
  revisions.readRevision.mockResolvedValue(detail(false));
  revisions.signRevision.mockImplementation(async () => { revisions.readRevision.mockResolvedValue(detail(true)); return detail(true); });
  setup();
  const button = await screen.findByRole('button', { name: 'Written Agreement' });
  await act(async () => { fireEvent.click(button); });
  expect(revisions.signRevision).toHaveBeenCalledWith('125', 'v3', 'learner', 'Learner', 'Signature');
  expect(state.signed).toBe(false); // Original source signer was never called.
  expect(await screen.findByText('First session destination')).toBeInTheDocument();
});
it('goes to the first learning session booking after the last learner signature, while other parties can still be unsigned', async () => {
  setup(); const button = await screen.findByRole('button', { name: 'Written Agreement' });
  await act(async () => fireEvent.click(button));
  expect(await screen.findByRole('heading', { name: 'First session destination' })).toBeVisible();
  expect(state.invalidate).toHaveBeenCalledWith('apprenticeship', '125');
});
it.each(['remaining', 'fail', 'staff'])('keeps the documents open for %s', async reason => {
  state.remaining = reason === 'remaining'; state.fail = reason === 'fail'; state.role = reason === 'staff' ? 'admin' : 'learner';
  setup(); const button = await screen.findByRole('button', { name: 'Written Agreement' });
  await act(async () => fireEvent.click(button));
  await screen.findByRole('button', { name: 'Written Agreement' });
  expect(screen.queryByRole('heading', { name: 'First session destination' })).not.toBeInTheDocument();
});
