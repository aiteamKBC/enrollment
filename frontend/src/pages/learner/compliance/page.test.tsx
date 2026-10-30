import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Page from './page';

const state = vi.hoisted(() => ({ signed: false, remaining: false, fail: false, role: 'learner', invalidate: vi.fn() }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '125' }) }));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ auth: { account: { role: state.role } } }) }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/api/learnerDetail', () => ({ invalidateLearnerDetailCache: state.invalidate }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('./DocumentCard', () => ({ Field: () => null, DocumentCard: ({ title, onSign }: { title: string; onSign: (mark: string) => void }) => <button onClick={() => onSign('Signature')}>{title}</button> }));
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
  particulars: {}, signatures: { learner: { signed: state.signed } },
} }), signWrittenAgreement: async () => { if (state.fail) throw new Error('Offline'); state.signed = true; } }));

beforeEach(() => { state.signed = false; state.remaining = false; state.fail = false; state.role = 'learner'; state.invalidate.mockClear(); });
afterEach(cleanup);
function setup() {
  render(<MemoryRouter initialEntries={['/learner/compliance-documents']}><Routes>
    <Route path="/learner/compliance-documents" element={<Page />} />
    <Route path="/learner/first-session" element={<h1>First session destination</h1>} />
  </Routes></MemoryRouter>);
}
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
