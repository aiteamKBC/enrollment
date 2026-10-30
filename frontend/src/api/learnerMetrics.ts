import { readLearnerJson, peekLearnerJson } from './learnerRead';
import type { LearnerKind } from './learnerDetail';

export interface KsbActivityPoint {
  activityId: string;
  code: string;
  ksbDefinitionId?: string | null;
  definitionCode?: string | null;
  completed: boolean;
  title: string | null;
  type: string | null;
  module: string | null;
  status: string | null;
  source: string | null;
  completedAt: string | null;
  componentId: string | null;
}
export interface ProgressMetric {
  completed: number | null;
  total: number | null;
  percent: number | null;
  status: 'ready' | 'empty' | 'unavailable';
  reason?: string;
  mappedCompleted?: number;
  mappedTotal?: number;
  unmappedActivities?: number;
  codes?: { code: string; completed: number; total: number; percent: number | null }[];
  points?: KsbActivityPoint[];
}
export interface LearnerMetrics {
  migrated: boolean;
  aptem_planned_total?: number | null;
  programme: ProgressMetric;
  ksb: ProgressMetric;
  otjh: { historical: number | null; new: number; actual: number | null; planned: number | null; targetToDate?: number | null; completed_actual?: number | null };
}
const url = (kind: LearnerKind, id: string) => `/learner_api/metrics/${kind}/${id}/`;
function valid(data: LearnerMetrics | undefined): data is LearnerMetrics {
  return !!(data?.programme?.status && data?.ksb?.status && data?.otjh);
}
export const peekLearnerMetrics = (kind: LearnerKind, id: string, perspective?: 'learner-overview') => {
  const data = peekLearnerJson<LearnerMetrics>(url(kind, id) + (perspective ? `?view=${perspective}` : ''));
  return valid(data) ? data : undefined;
};
export async function fetchLearnerMetrics(kind: LearnerKind, id: string, signal?: AbortSignal, force = false, perspective?: 'learner-overview') {
  const data = await readLearnerJson<LearnerMetrics>(url(kind, id) + (perspective ? `?view=${perspective}` : ''), { signal, revalidate: force, ttlMs: 30_000 });
  if (!valid(data)) {
    throw new Error('The server returned invalid programme totals. Please try again.');
  }
  return data;
}
