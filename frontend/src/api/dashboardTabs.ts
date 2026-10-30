import type { LearnerKind } from './learnerDetail';
import { peekLearnerJson, readLearnerJson } from './learnerRead';

type Count = { completed: number | null; total: number | null; percent: number | null };
export type OverviewModule = {
  id: string; title: string; start: string; end: string;
  activities: { completed: number; total: number };
  attendance: { attended: number; total: number } | null;
  hours: { actual: number | null; planned: number | null };
  ksb: { completed: number; total: number } | null;
  deliveryEnd?: string;
  duplicates?: Array<{ key: string; completed: boolean; date: string }>;
  dateBounds?: { startFixed: boolean; endFixed: boolean; first: string; last: string };
};
export type DashboardOverviewPayload = {
  metrics: { ksb: Count; otjh: { actual: number | null; targetToDate: number | null } };
  attendance: { present: number; sessions: number; attendanceRate?: number } | null;
  reviews: Count;
  modules: OverviewModule[];
  programme: Array<{ id: string; title: string; percent: number | null }>;
  hours: { months: Array<{ key: string; target: number | null; submitted: number | null; completed: number | null }>;
    planned: number | null; end: string };
  programmeStartDate: string | null; programmeEndDate: string | null;
};
const path = (kind: LearnerKind, id: string) => `/learner_api/dashboard/${kind}/${encodeURIComponent(id)}/overview/`;
const nullableNumber = (v: unknown) => v === null || (typeof v === 'number' && Number.isFinite(v));
const validCount = (value: Count | undefined) => !!value && nullableNumber(value.completed) && nullableNumber(value.total) && nullableNumber(value.percent);
function valid(data: DashboardOverviewPayload | undefined): data is DashboardOverviewPayload {
  return !!data?.metrics?.otjh && validCount(data.metrics.ksb) && validCount(data.reviews)
    && Array.isArray(data.modules) && Array.isArray(data.programme) && Array.isArray(data.hours?.months)
    && ['actual', 'targetToDate'].every(key => nullableNumber(data.metrics.otjh[key as keyof typeof data.metrics.otjh]))
    && (data.attendance === null || (!!data.attendance && typeof data.attendance.present === 'number' && typeof data.attendance.sessions === 'number'))
    && data.hours.months.every(row => typeof row?.key === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(row.key)
      && nullableNumber(row.target) && nullableNumber(row.submitted) && nullableNumber(row.completed));
}
export const dashboardOverview = {
  read: async (kind: LearnerKind, id: string, signal?: AbortSignal, fresh = false) => {
    const data = await readLearnerJson<DashboardOverviewPayload>(path(kind, id), { signal, revalidate: fresh, ttlMs: 30_000 });
    if (!valid(data)) throw new Error('Could not load your overview dashboard. Please try again.');
    return data;
  },
  peek: (kind: LearnerKind, id: string) => {
    const data = peekLearnerJson<DashboardOverviewPayload>(path(kind, id));
    return valid(data) ? data : undefined;
  },
};
