// ============================================================================
// Monthly Cycle — shared types.
//
// Split out of page.tsx so lib/ and components/ do not need to import from the
// page module itself. Nothing here changes the API contract.
// ============================================================================

export type MonthlyStatus = 'on-track' | 'need-attention' | 'at-risk';
export type ActivityTone = 'primary' | 'emerald' | 'amber' | 'red';
export type InlineActivityFilter = 'all' | 'assignment' | 'attendance' | 'quiz' | 'video' | 'audio' | 'reading' | 'meeting';
export type CoachingDeliveryKind = 'mcr' | 'pr' | 'catch-up' | 'support';
export type CoachingDeliveryStatus = 'booked' | 'completed' | 'cancelled' | 'needs-schedule';

export interface MonthlyActivityItem {
  id: string;
  date: string;
  type: string;
  title: string;
  detail: string;
  tone: ActivityTone;
  source: string;
  status?: string;
  timeLabel?: string;
}

export interface MonthlyLearnerActivity {
  id: string;
  name: string;
  initials: string;
  /** 'commercial' | 'apprenticeship' — which learner_detail table this id resolves against. */
  learnerType?: 'commercial' | 'apprenticeship' | null;
  /** enrolment."Created_users".id -- a different, disjoint pk space from `id`
   *  above. /learner-detail/ needs this one, not the LearnerProfile id. */
  enrolmentId?: string | null;
  /** Which module/week otjhTarget's cumulative-to-date figure currently falls in. */
  currentModule?: string | null;
  currentWeek?: string | null;
  componentsTargetToDate?: number | null;
  email?: string | null;
  cohortName: string;
  group: string;
  programme: string;
  status: MonthlyStatus;
  otjhStatus: string;
  lastActivityDate?: string | null;
  lastActivityLabel: string;
  learning: {
    total: number;
    quizzes: number;
    videos: number;
    components: number;
    reflections: number;
  };
  coaching: {
    total: number;
    booked: number;
    needsSchedule: number;
    mcm: number;
    progressReviews: number;
    catchups: number;
  };
  evidence: {
    submitted: number;
    latestDate?: string | null;
  };
  ksb: {
    touched: number;
    codes: string[];
  };
  otjh: {
    monthlyHours: number;
    monthlyHoursLabel: string;
    monthlyTarget: number;
    progress: number;
    completed: number;
    target: number;
  };
  needsAction: string[];
  activities: MonthlyActivityItem[];
}

export interface MonthlySummary {
  activeLearners: number;
  timelineItems: number;
  learningActivities: number;
  quizzes: number;
  videos: number;
  components: number;
  coachingSessions: number;
  bookedSessions: number;
  needsSchedule: number;
  evidence: number;
  ksbTouched: number;
  otjhHours: number;
  needsAction: number;
  onTrack: number;
  needAttention: number;
  atRisk: number;
}

export interface MonthlyActivityResponse {
  owner?: {
    name?: string;
    email?: string;
  };
  month: string;
  monthLabel: string;
  summary: MonthlySummary;
  learners: MonthlyLearnerActivity[];
}

export interface CoachingDeliveryItem {
  id: string;
  eventKey?: string;
  learnerId: string;
  learnerName: string;
  learnerStatus: MonthlyStatus;
  programme: string;
  cohort: string;
  group: string;
  kind: CoachingDeliveryKind;
  label: string;
  title: string;
  detail: string;
  date: string;
  status: CoachingDeliveryStatus;
  timeLabel: string;
}

export type CoachingDeliveryScheduleSource = 'mcr' | 'progress-review' | 'catch-up' | 'student-support';
export type CoachingDeliveryFocusSource = 'mcr' | 'progress-review' | 'catch-up' | 'student-support';

export interface CoachingDeliverySummary {
  byKind: Record<CoachingDeliveryKind, {
    items: CoachingDeliveryItem[];
    counts: Record<CoachingDeliveryStatus, number>;
  }>;
}
