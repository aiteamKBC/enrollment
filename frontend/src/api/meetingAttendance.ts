import { readLearnerJson } from './learnerRead';
import type { LearnerKind } from './learnerDetail';
import { invalidateWeeklyProgress } from './weeklyLearning';

export interface MeetingAttendance {
  id: string; title: string; status?: string; date: string | null; startTime: string | null; durationMinutes: number | null;
  meetingLink: string; meetingProvider: string; canAttend: boolean; attendanceConfirmed: boolean;
  syncWarning?: string; invited?: boolean | null; calendarEventKey?: string | null;
  creditedMinutes: number | null; canReportAbsence: boolean; absenceReported: boolean;
  absenceSessionId: string | null; missed: boolean;
}
export interface MeetingAttendanceResponse {
  sessions: MeetingAttendance[]; today: string; timeZone: string; csrfToken: string;
}
const base = '/learner_api/meeting-attendance';
export function fetchMeetingAttendance(kind: LearnerKind, id: string) {
  return readLearnerJson<MeetingAttendanceResponse>(`${base}/${kind}/${id}/`, { ttlMs: 0 });
}
export async function confirmMeetingAttendance(kind: LearnerKind, id: string, meetingId: string, csrfToken: string): Promise<{ creditedMinutes: number; alreadyRecorded: boolean }> {
  const response = await fetch(`${base}/${kind}/${id}/attend/`, { method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken }, body: JSON.stringify({ meetingId }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Could not record attendance. Please try again.');
  invalidateWeeklyProgress(kind, id);
  return data;
}
