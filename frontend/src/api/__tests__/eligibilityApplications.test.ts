import { afterEach, expect, it, vi } from 'vitest';
import { fetchApplicationReview, fetchEligibilityApplication, fetchEligibilityApplications, recordApplicationReview } from '../eligibilityApplications';

afterEach(() => vi.unstubAllGlobals());

it('uses staff-only GET endpoints with credentials and no caching for list and details', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ applications: [] }) });
  vi.stubGlobal('fetch', fetch);
  await fetchEligibilityApplications({ search: "Sam & Co", status: 'pending_assessment', page: 2 });
  await fetchEligibilityApplication('42');
  const [url] = fetch.mock.calls[0];
  expect(url).toContain('/enrolment_api/eligibility-applications/?');
  expect(new URL(url, 'https://example.test').searchParams.get('search')).toBe('Sam & Co');
  expect(new URL(url, 'https://example.test').searchParams.get('page')).toBe('2');
  expect(fetch.mock.calls[1][0]).toBe('/enrolment_api/eligibility-applications/42/');
  for (const [, options] of fetch.mock.calls) {
    expect(options).toEqual({ method: 'GET', credentials: 'include', cache: 'no-store', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
  }
});

it('reads review history and posts only a versioned decision with no reviewer or eligibility metadata', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ history: [] }) });
  vi.stubGlobal('fetch', fetch);
  await fetchApplicationReview('42');
  const action = { action: 'rejected' as const, notes: 'Evidence not sufficient.', expectedVersion: 1, requestId: 'review-token' };
  await recordApplicationReview('42', action);
  expect(fetch.mock.calls[0][0]).toBe('/enrolment_api/eligibility-applications/42/reviews/');
  const options = fetch.mock.calls[1][1];
  expect(options.method).toBe('POST');
  expect(options.credentials).toBe('include');
  expect(options.headers['X-Requested-With']).toBe('XMLHttpRequest');
  expect(JSON.parse(options.body)).toEqual(action);
});

it.each([401, 403, 404, 503])('surfaces an API refusal without fallback applicant data: %s', async status => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, json: async () => ({ error: 'Access or record unavailable.' }) }));
  await expect(fetchEligibilityApplication('42')).rejects.toMatchObject({ status, message: 'Access or record unavailable.' });
});
