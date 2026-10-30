import { coachFetch } from '@/lib/coachFetch';
import type { CoachMeetingAttendanceSummary } from './calendarEvents';

export interface AttendanceLinkAudit {
  id: number;
  confirmedBy: string;
  confirmedAt: string;
  reason: string;
  revokedBy: string | null;
  revokedAt: string | null;
  undoReason: string | null;
  targetRole?: 'learner' | 'employer';
  targetName?: string;
}

export interface AttendanceLinkContext {
  attendance: CoachMeetingAttendanceSummary;
  version: string;
  learner: { id: number; name: string } | null;
  targets: { key: string; role: 'learner' | 'employer'; name: string }[];
  candidates: { rowId: string; key: string }[];
  activeLinks: AttendanceLinkAudit[];
  history: AttendanceLinkAudit[];
}

export type AttendanceLinkChange = { version: string; reason: string } & (
  { action: 'link'; participantKey: string; targetKey: string }
  | { action: 'undo'; linkId: number }
);

export async function reviewAttendanceLinks(eventKey: string, change?: AttendanceLinkChange, signal?: AbortSignal): Promise<AttendanceLinkContext> {
  const response = await coachFetch(`/coach_api/coach/review-attendance/${encodeURIComponent(eventKey)}/links`, {
    method: change ? 'POST' : 'GET', signal,
    ...(change ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Attendance links could not be loaded.');
  return result;
}
