import { readLearnerJson, peekLearnerJson } from './learnerRead';
import type { LearnerKind } from '@/api/learnerDetail';

export type AttendanceSessionStatus = 'attended' | 'missed' | 'late' | 'present' | 'absent' | 'unmarked' | 'upcoming' | 'in_progress';

export interface AttendanceSessionRow {
  id: string;
  date: string;
  title: string;
  sessionType: string;
  status: AttendanceSessionStatus;
  rawStatus?: string;
  effectiveStatus?: string;
  startTime: string;
  endTime: string;
  module: string;
  coach: string;
}

export interface LearnerAttendance {
  learnerEmail: string;
  learnerId: number;
  learnerName: string;
  sessions: number;
  present: number;
  absent: number;
  late: number;
  catchup: number;
  risk: string;
  lastSessionDate: string | null;
  consecutiveMissed: number;
  updatedAt: string | null;
  attendanceRate: number;
  source?: 'kbc-attendance' | 'microsoft-teams' | 'combined';
  sessionHistory: AttendanceSessionRow[];
}

const attendanceUrl = (kind: LearnerKind, learnerId: string, source?: 'kbc') =>
  `/learner_api/attendance/${kind}/${learnerId}/${source ? `?source=${source}` : ''}`;

// The existing case-file display consumes attended/missed/late labels. Keep that
// adapter here so canonical wire statuses do not change unrelated Coach pages.
export function attendanceForDisplay(data: LearnerAttendance | null | undefined) {
  if (!data) return data;
  return { ...data, sessionHistory: (data.sessionHistory || []).map(row => ({ ...row,
    status: row.status === 'absent' ? 'missed' as const : row.status === 'present'
      ? row.rawStatus === 'late' ? 'late' as const : 'attended' as const : row.status,
  })) };
}

export function peekLearnerAttendance(kind: LearnerKind, learnerId: string): LearnerAttendance | null | undefined {
  return attendanceForDisplay(peekLearnerJson<{ attendance: LearnerAttendance | null }>(attendanceUrl(kind, learnerId))?.attendance);
}

export async function fetchLearnerAttendance(kind: LearnerKind, learnerId: string, signal?: AbortSignal, fresh = false, source?: 'kbc'): Promise<LearnerAttendance | null> {
  const data = await readLearnerJson<{ attendance: LearnerAttendance | null }>(attendanceUrl(kind, learnerId, source), { ttlMs: 30_000, signal, revalidate: fresh });
  return attendanceForDisplay(data.attendance) ?? null;
}
