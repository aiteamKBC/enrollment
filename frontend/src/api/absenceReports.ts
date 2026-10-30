import { readLearnerJson } from './learnerRead';
import { invalidateWeeklyProgress } from './weeklyLearning';
import type { LearnerKind } from '@/api/learnerDetail';

const BASE = '/learner_api/absence-reports';

export interface LearnerAbsenceReport {
  id: number;
  attendanceId?: string;
  reference: string;
  sessionTitle: string;
  sessionDate: string;
  sessionTime: string;
  reasonCategory: string;
  reason: string;
  status: string;
  evidenceProvided: boolean;
  evidenceKind: string;
  evidenceUrl: string;
  evidenceText: string;
  coachNote: string;
  recoveryMethod?: 'recorded' | 'catch-up' | 'alternative' | '';
  catchupEventKey?: string | null;
  alternativeSession?: AlternativeAttendanceSession | null;
  attendanceRate: number | null;
  previousAbsences: number;
  createdAt: string;
  updatedAt: string;
}

export interface AlternativeAttendanceSession {
  id: string;
  sessionId?: string;
  title: string;
  dateIso: string;
  startTime: string;
  endTime: string;
  groupId: string;
  group: string;
  cohortId: string;
  cohort: string;
  module?: string;
  status?: string;
  joinUrl?: string;
}

export interface MissedAttendanceSession {
  id: string;
  reportId?: string;
  status?: 'absent' | 'unmarked' | 'upcoming' | 'in_progress' | 'pending';
  sessionId: string;
  title: string;
  sessionType: string;
  dateIso: string;
  startTime: string;
  endTime: string;
  coach: string;
  module: string;
  alternativeSessions?: AlternativeAttendanceSession[];
}

export interface AbsenceReportData {
  count: number;
  results: LearnerAbsenceReport[];
  missedSessions: MissedAttendanceSession[];
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // A gateway timeout page rather than the API's JSON: the report may still have been saved.
    throw new Error([502, 504].includes(response.status)
      ? 'The server took too long to respond. Please check your reports before trying again.'
      : 'Something went wrong on our side. Please try again in a moment.');
  }
  if (!response.ok) {
    const message = data && typeof data === 'object' && 'error' in data
      ? String((data as { error?: string }).error)
      : `Request failed (${response.status}).`;
    const reference = data && typeof data === 'object' && 'reference' in data ? data.reference : null;
    const support = typeof reference === 'string' && /^ABS-[A-Z]+-[A-Z0-9]{3,5}-[A-F0-9]{12}$/.test(reference)
      ? ` Support reference: ${reference}` : '';
    throw new Error(message + support);
  }
  return data as T;
}

export async function fetchAbsenceReports(kind: LearnerKind, learnerId: string, scope?: 'meetings'): Promise<AbsenceReportData> {
  return readLearnerJson<AbsenceReportData>(`${BASE}/${kind}/${learnerId}/${scope ? '?scope=meetings' : ''}`);
}

export async function submitAbsenceReport(
  kind: LearnerKind,
  learnerId: string,
  form: FormData,
): Promise<LearnerAbsenceReport> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/${kind}/${learnerId}/`, { method: 'POST', body: form });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const data = await parseResponse<LearnerAbsenceReport>(response);
  invalidateWeeklyProgress(kind, learnerId);
  return data;
}
