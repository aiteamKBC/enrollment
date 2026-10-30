import { readCaseFileCache } from '../cache/caseFileCache';
import { coachFetch } from '@/lib/coachFetch';

export type CaseFileReadOptions = { signal?: AbortSignal; refresh?: boolean };

export async function readCoachJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await coachFetch(url, { signal });
  const payload = await response.json() as T & { detail?: string; error?: string; message?: string };
  if (!response.ok) throw new Error(payload.message || payload.detail || payload.error || `Request failed (${response.status}).`);
  return payload;
}

export function caseFileRead<T>(scope: string, learnerId: string, section: string, params: Record<string, string> = {}, options: CaseFileReadOptions = {}) {
  const { code, moduleId, weekId, ...queryParams } = params;
  const journeyDetail = section === 'learning-plan-module' || section === 'learning-plan-week';
  const path = section === 'ksb-detail' ? `ksbs/${encodeURIComponent(code)}` : journeyDetail
    ? `learning-plan/module/${encodeURIComponent(moduleId)}${weekId ? `/week/${encodeURIComponent(weekId)}` : ''}` : section;
  const query = new URLSearchParams(section === 'ksb-detail' || journeyDetail ? queryParams : params).toString();
  const url = `/coach_api/coach/case-file/${encodeURIComponent(learnerId)}/${path}${query ? `?${query}` : ''}`;
  return readCaseFileCache<T>(scope, learnerId, section, url, options);
}

/** Project consumers from one cached tab response; resources never become HTTP requests. */
export async function caseFileSectionRead<T>(scope: string, learnerId: string, section: string, params: Record<string, string> = {}, options: CaseFileReadOptions = {}): Promise<T> {
  if (section === 'ksb-detail' || section === 'ksb-search' || section === 'learning-plan-module' || section === 'learning-plan-week' || params.resource === 'submission') {
    return caseFileRead<T>(scope, learnerId, section, params, options);
  }
  const { resource, ...selection } = params;
  const query: Record<string, string> = {};
  if (section === 'overview' && selection.moduleId) query.moduleId = selection.moduleId;
  if (section === 'attendance') {
    for (const [key, value] of Object.entries(selection)) {
      if (value && !(['status', 'month'].includes(key) && value === 'all')
        && !(key === 'page' && value === '1') && !(key === 'pageSize' && value === '20')) query[key] = value;
    }
  }
  if (section === 'weekly-learning') {
    if (selection.week) query.week = selection.week;
  }
  if (section === 'monthly-focus') {
    const requested = new URLSearchParams(window.location.search).get('month');
    query.month = selection.month || (requested && /^\d{4}-(0[1-9]|1[0-2])$/.test(requested) ? requested
      : new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit' }).format(new Date()).slice(0, 7));
  }
  const payload = await caseFileRead<Record<string, unknown>>(scope, learnerId, section, query, options);
  if (resource === 'module') {
    const schedule = payload.schedule as { modules: { id: string }[] };
    const module = schedule?.modules.find(row => row.id === selection.moduleId);
    if (!module) throw new Error('Module not found in this learner plan.');
    return { module } as T;
  }
  if (!resource || resource === 'rows' || section === 'attendance') return payload as T;
  const error = (payload.errors as Record<string, string> | undefined)?.[resource];
  if (error) throw new Error(error);
  if (!(resource in payload)) throw new Error(`The ${section} response is missing ${resource}.`);
  return payload[resource] as T;
}
