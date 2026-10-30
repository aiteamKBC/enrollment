import { coachFetch } from '@/lib/coachFetch';
import type { LogLearner, LogSummary, LogDetail } from '@/features/monthly-logs/api';
import type { MonthlyActivityResponse } from '@/pages/coach/monthly-cycle/types';

export type CoachLogYear = {
  learner: LogLearner & { initials: string }; year: number; years: number[];
  summary: { coachSignatures: { completed: number; total: number }; acceptedOtjhHours: number; trainingPlanHours: number | null };
  months: Array<{ month: string; activities: number; acceptedOtjhHours: number; targetHours: number | null;
    isOpen: boolean; learnerSigned: boolean; coachSigned: boolean }>;
};
export type CoachLogDetail = { summary: LogSummary; detail: LogDetail };

async function read<T>(path: string): Promise<T> {
  const response = await coachFetch(`/coach_api/coach/monthly-logs/${path}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.detail || `Request failed with ${response.status}`);
  return data as T;
}

export const getCoachMonthlyLogLearners = () => read<{ learners: LogLearner[] }>('learners');
export const getCoachLogYear = (id: string, year: number) => read<CoachLogYear>(`${id}?year=${year}`);
export const getCoachLogDetail = (id: string, month: string) => read<CoachLogDetail>(`${id}/${month}`);

// React Query owns freshness and in-flight deduplication. Query functions do
// not consume its abort signal: StrictMode remounts share the pending request.
export const coachLogQueryOptions = { staleTime: 60_000, retry: false, refetchOnWindowFocus: false } as const;

export function monthlyCycleEndpoint(month: string) {
  return `/coach_api/coach/monthly-activity?${new URLSearchParams({ month })}`;
}

export async function getCoachMonthlyCycle(month: string, signal?: AbortSignal): Promise<MonthlyActivityResponse> {
  const response = await coachFetch(monthlyCycleEndpoint(month), { signal });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : `Request failed with ${response.status}`;
    throw new Error(detail);
  }
  return data as MonthlyActivityResponse;
}
