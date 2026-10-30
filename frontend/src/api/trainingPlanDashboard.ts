import type { LearnerKind } from './learnerDetail';
import type { LearnerCalendarEvent } from './learnerCalendar';
import { subjectRequest } from './studentActivity';
import { readLearnerJson } from './learnerRead';

export type PlanMonth = { label: string; topics: string[]; planned: number | null; source: string;
  activities?: { date: string; title: string; method: string; hours: number }[]; weeklyTarget?: number | null };
export type PlanSession = { id: string; moduleId: string; title: string; start: string; end: string | null;
  minutes: number; joinUrl: string | null; status: string; attended: boolean | null };
/** A holiday, exactly as the curriculum stores and the scheduler serves it. */
export type PlanSlotHoliday = { id?: string; label: string; startDate: string; endDate: string; type?: string; notes?: string };
/**
 * One position in the module's curriculum, open or closed.
 *
 * The wire shape of the curriculum scheduler's own slot spine — the same
 * `slots[]` the Module Builder reads, produced by `build_module_session_plan`
 * and passed straight through by the learner dashboard. A `reading-week` slot
 * is a delivery day a cohort holiday closed: it holds no session (`sessionNumber`
 * is null) and carries the holidays that closed it.
 *
 * Reading Weeks come from here and nowhere else. Inferring them from gaps
 * between session dates cannot work: a gap is equally what a term break, an
 * unauthored week, or a module that simply does not deliver looks like.
 */
export type PlanCurriculumSlot = { slotNumber: number; date: string; day: string;
  type: 'live-session' | 'reading-week'; cause?: string; sessionNumber: number | null; holidays: PlanSlotHoliday[];
  /** The authored week's own id/title/outcomes (curriculum.weeks), matched by sessionNumber. Absent when no week was authored at that number. */
  weekId?: string; weekTitle?: string; learningOutcomes?: string[];
  /**
   * The curriculum team's hint for this holiday week, when they published one.
   *
   * Served only for a slot a holiday actually lands on, and only when the
   * author turned the hint on -- an unpublished note never leaves the server,
   * so there is nothing here for a learner screen to decide about.
   */
  holidayNote?: string };
export type PlanModule = { id: string; title: string; description: string; start_date: string | null; end_date: string | null; tutor_name: string; coach_name: string;
  programme_name?: string; cohort_name?: string; group_name?: string; total_otjh?: number | null;
  weeks_number?: number | null; sessions_number?: number | null; educational_session_count?: number;
  session_week_day?: string; session_start_time?: string; session_end_time?: string; learning_outcomes?: string[];
  /** The curriculum spine for this module. Empty when the module has no plannable schedule. */
  curriculumSlots?: PlanCurriculumSlot[];
  /**
   * The scheduler's own delivery end — the last DELIVERED session, holiday
   * shifts included. Distinct from `end_date`, which is the stored date a human
   * may have typed and which stops describing the run once a closure moves it.
   */
  effectiveEndDate?: string;
  /** Where the run would have ended with nothing closed. */
  originalEndDate?: string };
export type PlanReview = Pick<LearnerCalendarEvent, 'id' | 'eventKey' | 'title' | 'source' | 'sequence' | 'status' | 'date' | 'targetDate' | 'scheduledDate' | 'scheduledTime' | 'durationMinutes' | 'coachName' | 'invited' | 'reviewTemplateId' | 'reviewInstanceId'> & { meetingLink?: string | null };
export type PlanModuleSummary = {
  id: string;
  moduleIds: string[];
  completed: number;
  total: number;
  directHours?: number | null;
  ksbProgress?: { completed: number; total: number } | null;
  dates: string[];
  title: string;
  source: string;
  sessionTitles: Array<{ date: string; title: string }>;
  activityCounts?: Record<string, number>;
  ksbCodes?: string[];
  ksbMappingMissing?: boolean;
  monthlyActivities?: Array<{
    id: string;
    componentId?: string | null;
    title: string;
    type: string;
    date: string;
    weekTitle?: string | null;
    expectedHours: number | null;
    completed: boolean;
    ksbCodes: string[];
  }>;
};
export type PlanModuleProgress = {
  attendance: { attended: number; total: number };
  hours: { actual: number };
  ksb: { completed: number; total: number; achievedCodes?: string[] };
};
export type TrainingPlanDashboard = {
  /** Accepted ledger hours, scoped to the module and activity rather than completion. */
  moduleActivityHours?: Record<string, { components: Record<string, number>; quizzes: Record<string, number> }> | null;
  /** Per-module activity/hour/KSB summaries already loaded by the overview read. */
  planSubjects?: PlanModuleSummary[];
  months: Record<string, PlanMonth>;
  /** Programme dates recorded on the selected Aptem Training Plan contract. */
  programmeStartDate?: string | null;
  programmeEndDate?: string | null;
  /** Monthly OTJH; assignment submissions use marking status, other activity types keep their existing semantics. */
  monthlyOtjh?: Record<string, { planned: number | null; submitted?: number; actual: number; includesHistorical?: boolean; missingPlannedActivities: number }>;
  /** Authoritative SSOT Monthly Logs totals. */
  monthlyLogOtjh?: Record<string, { target: number | null; submitted: number; completed: number }>;
  /** Whole-programme OTJH requirement from the shared dashboard metrics. */
  requiredOtjh?: number | null;
  actual: { month: string; groupId: string | null; hours: number; count: number }[];
  actualAvailable: boolean;
  /** Learner-progress-entry metrics keyed by the assigned curriculum module id. */
  moduleProgress?: Record<string, PlanModuleProgress>;
  modules: PlanModule[];
  moduleLinks: Record<string, { id: string; title: string }>;
  sessions: PlanSession[];
  reviews: PlanReview[];
  coach: { name: string; email?: string; phone?: string; bookingUrl: string | null };
  journalTargets?: Record<string, number>;
  contractStatus: string;
  generatedAt: string;
};

export function fetchTrainingPlanDashboard(kind: LearnerKind, id: string, signal?: AbortSignal) {
  // Share the same in-flight schedule read as This week / Upcoming on Dashboard.
  return readLearnerJson<TrainingPlanDashboard>(`/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=overview`, { signal, ttlMs: 30_000, revalidate: true });
}

export type TrainingPlanContract = Pick<TrainingPlanDashboard,
  'months' | 'contractStatus' | 'programmeStartDate' | 'programmeEndDate' | 'journalTargets'>;
export function fetchTrainingPlanContract(kind: LearnerKind, id: string, signal?: AbortSignal) {
  return subjectRequest<TrainingPlanContract>(`/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=contract`, { signal });
}
