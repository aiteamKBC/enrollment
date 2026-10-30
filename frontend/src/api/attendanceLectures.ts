import type { LearnerKind } from './learnerDetail';
import type { LearnerAttendance } from './learnerAttendance';
import { peekLearnerJson, readLearnerJson } from './learnerRead';
import { invalidateWeeklyProgress } from './weeklyLearning';

export type LectureStatus = 'completed' | 'late' | 'absent' | 'cancelled' | 'upcoming' | 'in_progress' | 'pending' | 'unmarked' | 'awaiting_recording';
export interface LectureActivity {
  id: string; title: string; type: string; completed: boolean;
  href?: string; groupId?: number; activityId?: number;
}
export interface AttendanceLecture {
  id: string; sessionId: string; reportId: string; date: string; title: string;
  moduleId: string; module: string; source: 'kbc-attendance' | 'microsoft-teams';
  startTime: string; endTime: string; durationMinutes: number | null;
  startsAt?: string | null; endsAt?: string | null; joinUrl?: string;
  componentHref?: string;
  tutor?: string; coach?: string; attendanceConfirmed?: boolean; creditedMinutes?: number | null;
  contentSummary: string; ksbs: string[]; activities: LectureActivity[];
  ksbScope?: 'lecture' | 'activities' | 'module' | null;
  status: LectureStatus; catchupStatus: 'completed' | 'pending' | 'missed' | null;
  rawStatus?: string;
  effectiveStatus?: string;
  counted?: boolean;
  rawAttendanceStatus?: string;
  effectiveAttendanceStatus?: string;
  effectiveAttendance?: 0 | 1 | null;
  finalOutcome?: string;
  attendanceBasis?: 'recorded' | 'awaiting_recording' | null;
  updatedAt: string | null; canReportAbsence: boolean; excused?: boolean;
  absenceReport: { id: number; status: string } | null;
  recovery?: {
    method: string; date: string | null;
    /** Alternative session only: when and where the learner makes the lecture up. */
    startTime?: string; endTime?: string; group?: string; title?: string;
    calendarKey?: string; joinUrl?: string; ended?: boolean;
    /** Recording plan only: seconds of the lecture recording watched, and its length. */
    watchedSeconds?: number; recordingSeconds?: number;
  } | null;
  monthlyLog?: { month: string; sourceRef: string };
}
export interface AttendanceMode {
  available: boolean; mode: 'live' | 'lazy'; requestedMode: 'lazy' | null;
  status: string; emailSent: boolean; managerAvailable: boolean;
  remindersEnabled: boolean; updatedAt: string | null;
  plannedLiveHours?: number; plannedRecordedHours?: number;
  unmappedPlannedLectures?: number;
}
export interface AttendanceWorkspace {
  timeZone?: string;
  csrfToken?: string;
  lectures: AttendanceLecture[];
  totals: { total: number; attended: number; absent: number; covered: number; upcoming: number; attendanceRate: number | null };
  summary: LearnerAttendance | null;
  modules: { id: string; title: string }[];
  mode: AttendanceMode;
  recentActivity: { id: string; title: string; at: string; type: string }[];
}
const url = (kind: LearnerKind, id: string) => `/learner_api/attendance/${kind}/${id}/lectures/`;
/** Recovery is a recorded outcome, not merely a difference from raw attendance. */
export function isMadeUpLecture(row: AttendanceLecture): boolean {
  const outcome = row.effectiveStatus || row.effectiveAttendanceStatus || row.finalOutcome;
  return row.attendanceBasis !== 'recorded' && row.effectiveAttendance === 1
    && (outcome ? outcome === 'made_up' : row.catchupStatus === 'completed');
}

export function isManuallyConfirmedLecture(row: AttendanceLecture): boolean {
  const raw = row.rawStatus || row.rawAttendanceStatus;
  const outcome = row.effectiveStatus || row.effectiveAttendanceStatus || row.finalOutcome;
  return row.attendanceBasis !== 'recorded' && row.effectiveAttendance === 1
    && outcome === 'present' && ['absent', 'unmarked', 'pending'].includes(raw || '');
}

/** Preserve the established lecture display labels while the wire status is canonical. */
export function attendanceWorkspaceForDisplay(data: AttendanceWorkspace): AttendanceWorkspace {
  return { ...data, lectures: data.lectures.map(row => {
    if ((row.status as string) !== 'present') return row;
    const raw = row.rawStatus || row.rawAttendanceStatus;
    const status: LectureStatus = row.attendanceBasis === 'recorded' ? 'completed'
      : isMadeUpLecture(row) ? 'absent'
      : raw === 'late' ? 'late' : 'completed';
    return { ...row, status };
  }) };
}
// One display object per cached payload. A page peeks on every render until its
// read settles; a new object each time looked like new data and re-rendered in a loop.
const displayed = new WeakMap<AttendanceWorkspace, AttendanceWorkspace>();
function displayWorkspace(data: AttendanceWorkspace): AttendanceWorkspace {
  let display = displayed.get(data);
  if (!display) {
    display = attendanceWorkspaceForDisplay(data);
    displayed.set(data, display);
  }
  return display;
}
export const peekAttendanceWorkspace = (kind: LearnerKind, id: string) => {
  const data = peekLearnerJson<AttendanceWorkspace>(url(kind, id));
  return data ? displayWorkspace(data) : data;
};
export async function fetchAttendanceWorkspace(kind: LearnerKind, id: string, signal?: AbortSignal, fresh = false) {
  const data = await readLearnerJson<AttendanceWorkspace>(url(kind, id), { ttlMs: 30_000, signal, revalidate: fresh });
  return displayWorkspace(data);
}
export async function confirmAttendance(kind: LearnerKind, id: string, lectureId: string): Promise<{
  lectureId: string; status: 'completed'; creditedMinutes: number; creditedHours: number; alreadyRecorded: boolean;
}> {
  const workspace = peekAttendanceWorkspace(kind, id) || await fetchAttendanceWorkspace(kind, id);
  if (!workspace.csrfToken) throw new Error('Please refresh the page before recording attendance.');
  const response = await fetch(`/learner_api/attendance/${kind}/${id}/attend/`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': workspace.csrfToken },
    body: JSON.stringify({ lectureId }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(result?.error || 'Could not save attendance. Refresh the page and try again.');
  if (result?.lectureId !== lectureId || result?.status !== 'completed' ||
      !Number.isFinite(result?.creditedMinutes) || result.creditedMinutes <= 0 || !Number.isFinite(result?.creditedHours)) {
    throw new Error('Attendance could not be confirmed. Please refresh the page and try again.');
  }
  invalidateWeeklyProgress(kind, id);
  return result;
}
export type SavedAttendanceMode = Pick<AttendanceMode, 'mode' | 'requestedMode'>;
/** Administrators only: every saved mode keyed by learner id. A learner with no entry is on Live Sessions. */
export async function fetchAttendanceModes(signal?: AbortSignal): Promise<{ available: boolean; modes: Record<string, SavedAttendanceMode> }> {
  const response = await fetch('/learner_api/attendance-mode/', { credentials: 'include', signal });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result) throw new Error(result?.error || 'Could not load attendance modes.');
  return result;
}
/** `direct` (administrators only) applies the mode at once, without manager approval or email. */
export async function updateAttendanceMode(kind: LearnerKind, id: string, mode: 'live' | 'lazy', options: { direct?: boolean } = {}): Promise<AttendanceMode> {
  const response = await fetch(`/learner_api/attendance/${kind}/${id}/mode/`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify(options.direct ? { mode, direct: true } : { mode }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not update attendance mode.');
  invalidateWeeklyProgress(kind, id);
  return result;
}
