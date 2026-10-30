import { validateWeeklyLearning } from '@/api/weeklyLearningContract';
import type { WeeklyLearningResponse } from '@/api/weeklyLearningContract';
import { createCachedResource } from '@/api/cachedRequest';
import { withCallerSignal } from '@/api/learnerRead';
import { coachFetch } from '@/lib/coachFetch';

/** Memory only. The auth lifecycle clears this registered resource on logout. */
const fetchSection = async (key: string) => {
  const [, , section, url] = JSON.parse(key) as string[];
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 180_000);
  try {
    const response = await coachFetch(url, { signal: controller.signal });
    const payload = await response.json() as { detail?: string; error?: string; message?: string };
    if (!response.ok) throw new Error(payload.message || payload.detail || payload.error || `Request failed (${response.status}).`);
    return section === 'weekly-learning' ? validateWeeklyLearning(payload as unknown as WeeklyLearningResponse) : payload;
  } finally {
    window.clearTimeout(timer);
  }
};
const resource = createCachedResource<unknown>('coach-case-file-session', fetchSection, Infinity, Infinity);
export const OVERVIEW_TTL_MS = 60_000;
const overview = createCachedResource<unknown>('coach-case-file-session', fetchSection, OVERVIEW_TTL_MS, Infinity);
const dynamicSections = new Set(['reviews', 'overview', 'weekly-learning', 'monthly-focus', 'otjh-ksb', 'ksb-detail', 'ksb-search', 'attendance', 'learning-plan', 'learning-plan-module', 'learning-plan-week']);

export function peekCaseFileCache<T>(scope: string, learnerId: string, section: string, url: string): T | undefined {
  const key = JSON.stringify([scope, learnerId, section, url]);
  const cached = (dynamicSections.has(section) ? overview : resource).peek(key);
  if (cached) return cached as T;
  if (section === 'weekly-learning') {
    const requested = new URL(url, window.location.origin);
    const week = requested.searchParams.get('week');
    requested.searchParams.delete('week');
    const initial = overview.peek(JSON.stringify([scope, learnerId, section, requested.pathname + requested.search])) as { selectedWeek?: { weekId: string } } | undefined;
    if (week && initial?.selectedWeek?.weekId === week) return initial as T;
  }
}

export function readCaseFileCache<T>(scope: string, learnerId: string, section: string, url: string, options: { signal?: AbortSignal; refresh?: boolean } = {}) {
  if (options.signal?.aborted) return Promise.reject(new DOMException('The operation was aborted.', 'AbortError'));
  const key = JSON.stringify([scope, learnerId, section, url]);
  if (section === 'weekly-learning') {
    const requested = new URL(url, window.location.origin);
    const week = requested.searchParams.get('week');
    requested.searchParams.delete('week');
    const defaultUrl = requested.pathname + requested.search;
    const defaultKey = JSON.stringify([scope, learnerId, section, defaultUrl]);
    const initial = overview.peek(defaultKey) as { selectedWeek?: { weekId: string } } | undefined;
    if (week && initial?.selectedWeek?.weekId === week) {
      if (options.refresh) overview.invalidate(defaultKey);
      else if (!overview.peek(key)) return withCallerSignal(Promise.resolve(initial as T), options.signal);
    }
  }
  return withCallerSignal((dynamicSections.has(section) ? overview : resource).read(key, { revalidate: options.refresh }) as Promise<T>, options.signal);
}

export function clearCaseFileCache() { resource.invalidate(); overview.invalidate(); }

