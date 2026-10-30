import type { LearnerKind } from './learnerDetail';
import type { TrainingPlanDashboard } from './trainingPlanDashboard';
import type { LearnerMetrics } from './learnerMetrics';
import { peekLearnerJson, readLearnerJson } from './learnerRead';

export type PlanActivitySummary = {
  id: string; componentId?: string | null; title: string; type: string; date: string;
  weekTitle?: string | null; expectedHours: number | null; completed: boolean; ksbCodes: string[];
};

export type PlanSubjectSummary = {
  id: string; title: string; source: 'legacy' | 'current'; completed: number; total: number;
  dates: string[]; moduleIds: string[]; sessionTitles: { date: string; title: string }[];
  activityCounts?: Record<string, number>; ksbCodes?: string[]; ksbMappingMissing?: boolean;
  ksbCodesByMonth?: Record<string, string[]>;
  ksbProgress?: { completed: number; total: number } | null;
  directHours?: number | null;
  monthlyActivities?: PlanActivitySummary[];
};

export type OverviewWeek = {
  metrics?: LearnerMetrics;
  planSubjects?: PlanSubjectSummary[];
  /** Monthly OTJH; assignment submissions use marking status, other activity types keep their existing semantics. */
  monthlyOtjh?: Record<string, { planned: number | null; submitted?: number; actual: number; includesHistorical?: boolean; missingPlannedActivities: number }>;
  weekStart: string; weekEnd: string; timezone: string;
  modules: { id: string; title: string; weekLabels: string[]; moduleIds?: string[]; completed: number; total: number;
    percent: number | null; ksbCodes: string[]; ksbMappingMissing: boolean }[];
  deadlines: { id: string; title: string; type: 'assignment' | 'checkpoint'; date: string; subjectId: string }[];
  undatedActivities: number; expectedHours: number | null; missingExpectedHours: number;
  otjh: { actual: number | null; historical: number | null; new: number; undatedHistoricalRows: number };
};

export type HomeProgressCount = { completed: number; total: number };
export type HomeProgress = {
  period: { start: string | null; end: string };
  otjh: { actual: number | null; submitted: number | null; planned: number | null; percent: number | null; includesHistorical?: boolean; missingPlannedActivities: number };
  activities: HomeProgressCount | null; assignments: HomeProgressCount | null;
  lectures: HomeProgressCount | null; modules: HomeProgressCount;
  undatedActivities: number;
};

function resource<T>(path: (kind: LearnerKind, id: string) => string, valid: (value: T) => boolean) {
  return {
    read: async (kind: LearnerKind, id: string, signal?: AbortSignal, fresh = false) => {
      const value = await readLearnerJson<T>(path(kind, id), { signal, revalidate: fresh, ttlMs: 30_000 });
      if (!value || !valid(value)) throw new Error('Could not load your overview. Please try again.');
      return value;
    },
    peek: (kind: LearnerKind, id: string) => {
      const value = peekLearnerJson<T>(path(kind, id));
      return value && valid(value) ? value : undefined;
    },
  };
}
const weekPath = (kind: LearnerKind, id: string) => `/learner_api/overview-week/${kind}/${encodeURIComponent(id)}/`;
export const overviewWeek = resource<OverviewWeek>(weekPath, value => !!value.weekStart && Array.isArray(value.modules) && Array.isArray(value.deadlines) && !!value.otjh);
export const overviewSchedule = resource<TrainingPlanDashboard>(
  (kind, id) => `/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=overview`,
  value => Array.isArray(value.sessions) && Array.isArray(value.reviews),
);

/** Student Home's compact week: current modules and the progress card. */
export type HomeSummary = Pick<OverviewWeek, 'weekStart' | 'weekEnd'> & {
  modules: Pick<OverviewWeek['modules'][number], 'id' | 'title' | 'weekLabels'>[];
  homeProgress: HomeProgress;
};
export const homeSummary = resource<HomeSummary>((kind, id) => `${weekPath(kind, id)}?section=home-summary`,
  value => !!value.weekStart && Array.isArray(value.modules)
    && !!value.homeProgress?.otjh && !!value.homeProgress.period && !!value.homeProgress.modules);

/**
 * One of Student Home's nearest events. ``date`` is an ISO instant, or a UK
 * calendar date (YYYY-MM-DD) for an assignment due date or an unbooked review.
 * ``booked`` is sent for reviews only.
 */
export type UpcomingEvent = { id: string; type: 'session' | 'assignment' | 'review'; title: string; date: string; booked?: boolean };
/** Student Home's nearest three sessions, assignment deadlines and progress reviews, earliest first. */
export type UpcomingSchedule = { events: UpcomingEvent[] };
export const upcomingSchedule = resource<UpcomingSchedule>(
  (kind, id) => `/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=upcoming`,
  value => Array.isArray(value.events),
);

export type LearningSchedule = Pick<TrainingPlanDashboard, 'modules' | 'moduleLinks' | 'generatedAt'>;
export const learningSchedule = resource<LearningSchedule>(
  (kind, id) => `/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=learning`,
  value => Array.isArray(value.modules) && !!value.moduleLinks,
);

/** My Learning cards and upcoming sessions share one module/ledger read. */
export type MyLearningSchedule = LearningSchedule & Pick<TrainingPlanDashboard, 'sessions' | 'moduleProgress'>;
export const myLearningSchedule = resource<MyLearningSchedule>(
  (kind, id) => `/learner_api/training-plan-dashboard/${kind}/${encodeURIComponent(id)}/?section=my-learning`,
  value => Array.isArray(value.modules) && !!value.moduleLinks && Array.isArray(value.sessions) && !!value.moduleProgress,
);
