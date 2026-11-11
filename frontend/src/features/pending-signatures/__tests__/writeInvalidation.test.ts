import { afterEach, describe, expect, it, vi } from 'vitest';
import { signAgreement, issueAgreement } from '@/api/apprenticeshipAgreement';
import { signIlrDocument, issueIlrDocument } from '@/api/ilrDocument';
import { signTrainingPlanDocument, issueTrainingPlanDocument } from '@/api/trainingPlanDocument';
import { signWrittenAgreement, issueWrittenAgreement } from '@/api/writtenAgreement';
import { signReviewForm, issueOnboardingReview, saveReviewForm } from '@/api/reviewForm';
import { PENDING_SIGNATURES_CHANGED, PENDING_SIGNATURES_STORAGE_KEY } from '../events';
import { finalizeRevision, signRevision } from '@/features/compliance-documents/revisionApi';
const writes = [
  () => signAgreement(7, 'apprentice', 'Learner', 'mark'), () => issueAgreement(7),
  () => signIlrDocument(7, 'learner', 'Learner', 'mark'), () => issueIlrDocument(7),
  () => signTrainingPlanDocument(7, 'apprentice', 'Learner', 'mark'), () => issueTrainingPlanDocument(7),
  () => signWrittenAgreement(7, 'learner', 'Learner', 'mark'), () => issueWrittenAgreement(7),
  () => signReviewForm('apprenticeship', '7', 'event', { party: 'learner', name: 'Learner', signature: 'mark' }),
  () => issueOnboardingReview('apprenticeship', '7', 'workspace'),
  () => saveReviewForm('apprenticeship', '7', 'event', { finish: true }),
  () => finalizeRevision('7', 'r2', 'reviewed-token'),
  () => signRevision('7', 'r2', 'apprentice', 'Learner', 'mark'),
];
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
describe('existing document writer notifications', () => {
  it.each(writes.map((write, i) => ({ write, i })))('revalidates successful document/review write $i without persisting content', async ({ write }) => {
    const changed = vi.fn(); window.addEventListener(PENDING_SIGNATURES_CHANGED, changed);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ agreement: {}, document: {}, csrfToken: 'synthetic-token' }))));
    const stored = vi.fn(); vi.stubGlobal('localStorage', { setItem: stored });
    try { await write(); expect(changed).toHaveBeenCalledTimes(1); expect(stored).toHaveBeenCalledWith(PENDING_SIGNATURES_STORAGE_KEY, expect.any(String)); expect(stored.mock.calls[0][1]).not.toContain('mark'); }
    finally { window.removeEventListener(PENDING_SIGNATURES_CHANGED, changed); }
  });
  it.each(writes.map((write, i) => ({ write, i })))('does not signal failed write $i', async ({ write }) => {
    const changed = vi.fn(); window.addEventListener(PENDING_SIGNATURES_CHANGED, changed);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'Failed' }), { status: 503 })));
    try { await expect(write()).rejects.toThrow(); expect(changed).not.toHaveBeenCalled(); }
    finally { window.removeEventListener(PENDING_SIGNATURES_CHANGED, changed); }
  });
});
