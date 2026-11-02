import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { clearAllCachedResources } from '@/api/cachedRequest';
import { getAssessment, prefetchAssessmentSecurity, saveAssessment } from '../api';
import { fresh } from './fixtures';
import * as wizardCache from '@/api/extendedIlr';
import * as learnerCache from '@/api/learnerDetail';

beforeEach(() => clearAllCachedResources());
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

it('shares concurrent questionnaire reads and caches by learner and published version', async () => {
  const fetcher = vi.fn(async () => json(fresh())); vi.stubGlobal('fetch', fetcher);
  await Promise.all([getAssessment('apprenticeship', '41', 3), getAssessment('apprenticeship', '41', 3)]);
  await getAssessment('apprenticeship', '41', 3);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await getAssessment('apprenticeship', '41', 4);
  await getAssessment('apprenticeship', '42', 4);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('uses one prefetched CSRF token for pathway and answer saves without refetching questions', async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => { void init; return json(url.includes('enrollment-csrf') ? { csrfToken: 'masked-test-token' } : fresh()); });
  vi.stubGlobal('fetch', fetcher);
  await Promise.all([prefetchAssessmentSecurity(), prefetchAssessmentSecurity()]);
  await saveAssessment('apprenticeship', '41', fresh(), { pathway: 'marketing' });
  await saveAssessment('apprenticeship', '41', { ...fresh(), pathway: 'marketing', revision: 1 }, { responses: { marketing: ['marketing_A'] } });
  expect(fetcher).toHaveBeenCalledTimes(3);
  const init = fetcher.mock.calls[1][1] as RequestInit;
  expect(init.credentials).toBe('include'); expect(init.method).toBe('PUT');
  expect(init.headers).toMatchObject({ 'X-CSRFToken': 'masked-test-token' });
  expect(JSON.parse(init.body as string)).toMatchObject({ pathway: 'marketing', configurationVersion: 3 });
});

it('refreshes a rejected CSRF token once and preserves an actionable origin error if still forbidden', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf')
    ? json({ csrfToken: 'masked-test-token' }) : new Response('CSRF verification failed. Origin checking failed.', { status: 403 }));
  vi.stubGlobal('fetch', fetcher);
  await expect(saveAssessment('apprenticeship', '41', fresh(), { pathway: 'marketing' })).rejects.toThrow('security token or page origin');
  expect(fetcher).toHaveBeenCalledTimes(4);
});

it('does not retry a genuine permission rejection as a CSRF failure', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf') ? json({ csrfToken: 'test' }) : json({ error: 'Not authorised for this learner.' }, 403));
  vi.stubGlobal('fetch', fetcher);
  await expect(saveAssessment('apprenticeship', '42', fresh(), { pathway: 'marketing' })).rejects.toThrow('Not authorised');
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('does not cache failed loads and permits retry', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json({ error: 'Temporarily unavailable' }, 503)).mockResolvedValueOnce(json(fresh()));
  vi.stubGlobal('fetch', fetcher);
  await expect(getAssessment('apprenticeship', '41', 3)).rejects.toThrow('Temporarily unavailable');
  expect((await getAssessment('apprenticeship', '41', 3)).available).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('drops cached learner state when the application clears account-scoped resources', async () => {
  const fetcher = vi.fn(async () => json(fresh())); vi.stubGlobal('fetch', fetcher);
  await getAssessment('apprenticeship', '41', 3); clearAllCachedResources();
  await getAssessment('apprenticeship', '41', 3); expect(fetcher).toHaveBeenCalledTimes(2);
});

it('sends the alternative and exact reason in one confirmation request and returns the persisted reason', async () => {
  const reason = '  My job focuses on delivery.\nI want to build that foundation.  ';
  const saved = { ...fresh(), selectedProgramme: 'ME_L4', alternativeSelectionReason: reason,
    selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' };
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf') ? json({ csrfToken: 'test' }) : json(saved));
  vi.stubGlobal('fetch', fetcher);
  const response = await saveAssessment('apprenticeship', '41', fresh(), { action: 'select', selectedProgramme: 'ME_L4', alternativeSelectionReason: reason });
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  const writes = calls.filter(([, options]) => options?.method === 'PUT');
  expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0][1].body))).toMatchObject({ action: 'select', selectedProgramme: 'ME_L4', alternativeSelectionReason: reason });
  expect(response.alternativeSelectionReason).toBe(reason);
});

it.each(['continue', 'select'] as const)('invalidates the existing account/profile reads only after successful %s confirmation', async (action) => {
  const wizard = vi.spyOn(wizardCache, 'invalidateWizardCacheById').mockImplementation(() => {});
  const learner = vi.spyOn(learnerCache, 'invalidateLearnerDetailCache').mockImplementation(() => {});
  const saved = { ...fresh(), selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' };
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf') ? json({ csrfToken: 'test' }) : json(saved));
  vi.stubGlobal('fetch', fetcher);
  await saveAssessment('apprenticeship', '41', fresh(), { action });
  expect(wizard).toHaveBeenCalledWith('41'); expect(learner).toHaveBeenCalledTimes(1);
});

it('does not invalidate account/profile reads on submission alone or a failed assignment confirmation', async () => {
  const wizard = vi.spyOn(wizardCache, 'invalidateWizardCacheById').mockImplementation(() => {});
  const learner = vi.spyOn(learnerCache, 'invalidateLearnerDetailCache').mockImplementation(() => {});
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('enrollment-csrf')) return json({ csrfToken: 'test' });
    return JSON.parse(String(options?.body)).action === 'submit' ? json({ ...fresh(), completionStatus: 'SUBMITTED', selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION' })
      : json({ error: 'Admissions must review the programme mapping.' }, 409);
  });
  vi.stubGlobal('fetch', fetcher);
  await saveAssessment('apprenticeship', '41', fresh(), { action: 'submit' });
  await expect(saveAssessment('apprenticeship', '41', fresh(), { action: 'continue' })).rejects.toThrow('Admissions must review');
  expect(wizard).not.toHaveBeenCalled(); expect(learner).not.toHaveBeenCalled();
});

it('rejects an HTML or empty success response instead of leaving the modal stuck loading', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Unexpected proxy response</html>', { status: 200 })));
  await expect(getAssessment('apprenticeship', '41', 3)).rejects.toThrow('invalid assessment response');
});
