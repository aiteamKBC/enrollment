import { LearnerReadError } from './learnerRead';
import type { PlanSlotHoliday } from './trainingPlanDashboard';

export type WeeklySessionState = 'attended' | 'missed' | 'upcoming' | 'live' | 'unscheduled' | 'unmarked' | 'cancelled';
export type WeeklyAttendance = 'present' | 'absent' | 'upcoming' | 'unmarked';
export type CanonicalWeek = {
  id: string; moduleId: string; weekId?: string; weekNumber: number; slotNumber: number;
  kind: 'live-session' | 'reading-week'; title: string; startDate: string; endDate: string;
  status: string; progress: number; completedActivities: number; totalActivities: number;
  attendance: WeeklyAttendance; sessionState: WeeklySessionState; holidayNote: string;
  sessionStart?: string; sessionDurationMinutes?: number | null; holidays: PlanSlotHoliday[];
};
export type CanonicalSelectedWeek = {
  weekId: string;
  summary: { ksbCount: number; achievedKsbCount: number; otjhHours: number | null;
    plannedOtjhHours: number; untimedActivities: number };
  liveSession: { id: string; title: string; date: string; startTime: string; start: string; durationMinutes: number | null;
    endTime: string | null; status: string; sessionState: WeeklySessionState; attendance: WeeklyAttendance; joinUrl: string | null } | null;
  activities: { id: string; title: string; type: string; isQuiz: boolean;
    status: 'completed' | 'in-progress' | 'not-started'; expectedHours: number | null; actualHours: number | null;
    durationMinutes: number | null; quizDuration: number | null; quizTimeUnit: string | null;
    ksbCodes: string[]; quizId?: number | null; contentAvailable?: boolean; videoUrl?: string | null;
    sessionDate?: string | null; sessionDateTimeUtc?: string | null; teamsLiveSessionId?: string | null }[];
};
export type WeeklyLearningResponse = {
  modules: { id: string; title: string }[];
  weeks: CanonicalWeek[];
  selectedWeek: CanonicalSelectedWeek | null;
};

/** Existing learner route/cache IDs address the canonical delivery-slot ID. */
export const studentWeekId = (week: CanonicalWeek) => `${week.kind === 'reading-week' ? 'reading' : 'slot'}:${week.moduleId}:${week.slotNumber}`;
export const attendedEvidence = (value: WeeklyAttendance) => value === 'present' ? true : value === 'absent' ? false : null;
export const selectedNavigation = (response: WeeklyLearningResponse) => response.weeks.find(week => week.id === response.selectedWeek?.weekId);

/** Validate the single resource at the network boundary; return the same object. */
export function validateWeeklyLearning(response: WeeklyLearningResponse, actions = false): WeeklyLearningResponse {
  const invalid = () => { throw new LearnerReadError('The weekly response was incomplete. Please try again.', 200, 'invalid_week'); };
  if (!response || !Array.isArray(response.modules) || !Array.isArray(response.weeks) || response.selectedWeek === undefined) invalid();
  const moduleIds = new Set(response.modules.map(module => module.id));
  for (const week of response.weeks) {
    if (typeof week.id !== 'string' || !moduleIds.has(week.moduleId) || !Number.isInteger(week.slotNumber)
      || !Array.isArray(week.holidays) || !['present', 'absent', 'upcoming', 'unmarked'].includes(week.attendance)
      || !Number.isFinite(week.progress) || !Number.isInteger(week.totalActivities) || !Number.isInteger(week.completedActivities)) invalid();
  }
  const selected = response.selectedWeek;
  if (selected && (!selectedNavigation(response) || !selected.summary || selected.liveSession === undefined || !Array.isArray(selected.activities)
    || selected.activities.some(activity => typeof activity.id !== 'string' || !['completed', 'in-progress', 'not-started'].includes(activity.status)
      || (actions && (typeof activity.contentAvailable !== 'boolean' || (activity.isQuiz && typeof activity.quizId !== 'number')))))) invalid();
  return response;
}
