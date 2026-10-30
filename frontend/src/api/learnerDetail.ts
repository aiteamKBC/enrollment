import { createCachedResource } from './cachedRequest';
import { readLearnerJson, invalidateLearnerReads } from './learnerRead';
import { invalidateWeeklyLearning, type WeeklyProgressScope } from './weeklyLearning';
import type { LearnerAccessGate, LearnerLearningAccess } from '@/utils/learnerAccessGate';
// ============================================================================
// Learner-detail API client.
// Talks to the Django backend at /learner_api (proxied to :8000 by Vite in dev).
// Combines a CommercialUser/EnrolmentUser row with its "Learner"."Active_users"
// mirror (present only while the learner is Active) into one read-only shape.
// ============================================================================

const BASE = '/learner_api/learner-detail';
const detailResource = createCachedResource<LearnerDetail>('learner-detail', key =>
  readLearnerJson(`${BASE}/${key.replace(':', '/')}/?content=summary`));
const quizDetailResource = createCachedResource<LearnerDetail>('learner-quiz-detail', key =>
  readLearnerJson(`${BASE}/${key.replace(':', '/')}/?content=quiz`));
const componentResource = createCachedResource<LearnerDetail>('learner-component-detail', async key => {
  const [kind, id, componentId] = JSON.parse(key) as [LearnerKind, string, string];
  const detail = await readLearnerJson<LearnerDetail>(
    `${BASE}/${kind}/${id}/?content=activity&component_id=${encodeURIComponent(componentId)}`);
  if (String(detail.id) !== id || !detail.components?.some(item => item.componentId === componentId)) {
    throw new Error('The server returned a different activity. Please try again.');
  }
  return detail;
});
const summaryResource = createCachedResource<LearnerSummary>('learner-summary', key =>
  readLearnerJson(`/learner_api/learner-summary/${key.replace(':', '/')}/`));

export type LearnerKind = 'commercial' | 'apprenticeship';

export interface LearnerWeekEntry {
  module: string | null;
  week: string;
  moduleId?: string | null;
  weekId?: string | null;
}
/** A KSB authored against a component, with the weight it contributes. */
export interface ComponentKsbMapping {
  code: string;
  description: string | null;
  classification: string | null;   // main | secondary | possible
  weight: number;
}

export interface PreviousLearningCompletion {
  courseRef: string;
  activityId: string;
  sourceActivityId: number;
  catalogueKind: 'material' | 'quiz';
  completed: boolean;
}

export interface LearnerComponentEntry {
  /** Exact owned source activity in a verified course replacement; no new attempt or hours. */
  previousLearning?: PreviousLearningCompletion;
  module: string | null;
  week: string | null;
  component: string;
  expectedOtjh: number | null;
  moduleId?: string | null;             // curriculum.modules.module_catalogue_id (null for legacy id-less modules)
  weekId?: string | null;               // curriculum.weeks.id
  ksbWeightTotal?: number | null;       // summed weight of this component's KSB mappings
  ksbMappingCount?: number | null;      // 0 => component is not gated by the completion criteria
  ksbMappings?: ComponentKsbMapping[];  // the KSBs auto-credited when this component is completed
  componentId?: string | null;
  /** Display-only grouping within the authored week; child IDs remain canonical. */
  pointNumber?: number | null;
  type?: string | null;                 // master component type, e.g. 'video', 'live_session'
  description?: string | null;
  assignmentTopics?: import('@/lib/assignmentTopics').AssignmentTopic[];
  /** Author flag: the monthly (eight-step) assignment. See isMonthlyAssignment. */
  monthlyAssignment?: boolean;
  assignmentBrief?: string | null;      // assignment brief authored as plain text (Module Builder)
  assignmentBriefHtml?: string | null;  // assignment brief authored as rich text (Week Builder)
  videoUrl?: string | null;             // present on video components authored with a URL
  alternativeYouTubeUrl?: string | null;
  audioUrl?: string | null;             // podcast / reading voice-over
  contentHtml?: string | null;          // reading rich-text content
  lessonContentHtml?: string | null;    // video lesson content; activity read only
  hasReadingContent?: boolean;         // list response; HTML is loaded when opened
  fileName?: string | null;             // powerpoint / document file name
  /** Every file attached to the component, in the author's order. Entry 0
   *  is the same file `resourceUrl` points at; the rest are the extra
   *  attachments a reading / deck / podcast can now carry. */
  files?: import('@/lib/componentFiles').ComponentFile[];
  downloadAllowed?: boolean;            // powerpoint download flag
  reflectionPrompt?: string | null;     // authored reflection prompt / learner guidance
  /** Completion derived from verified Teams attendance; adds no recorded hours. */
  attendanceCompleted?: boolean;
  attendanceCompletedAt?: string | null;
  reflectionRequired?: boolean;         // false completes the activity without the reflection flow
  /** Authored on the component: this activity must be validated by a tutor or
   *  coach. The reflection is what creates the marking record, so an activity
   *  with this set always goes through the reflection flow even when
   *  reflectionRequired is false — otherwise it completes without ever
   *  reaching the marking queue. */
  tutorValidationRequired?: boolean;
  reflectionQuestion?: string | null;   // custom Apply-tab question; null uses the default copy
  resourceUrl?: string | null;          // generic external/download URL
  liveSessionUrl?: string | null;       // Microsoft Teams join URL for live sessions
  teamsSessionNumber?: number | null;
  teamsLiveSessionId?: string | null;   // curriculum.live_sessions.id for attendance/artifact sync
  recordingSeriesId?: string | null;    // saved series owning a dated recorded event
  recordingSessionNumber?: number | null;
  recordingDate?: string | null;        // recording day in Europe/London
  sessionDate?: string | null;
  sessionTime?: string | null;
  sessionDateTimeUtc?: string | null;
  durationMinutes?: number | null;
  isQuiz?: boolean;
  quizMeta?: { quizId: number; questions: number | null; duration: number | null; timeUnit: string | null };
  /** A passed quiz whose slot an author has since removed from the module. */
  retired?: boolean;
  /** Compact Weekly Learning rows only: the server's hasComponentContent
   *  verdict, sent instead of the descriptions and briefs it reads. */
  contentAvailable?: boolean;
}
export interface LearnerKsbItem {
  code: string;
  type: string;
  number: string;
  description: string;
}

// New results include a text/key snapshot; older attempts may only have IDs.
export interface LearnerQuizQuestionResult {
  questionId: number;
  questionText?: string;
  type?: string;
  possible?: number;
  imageUrl?: string;
  earned: number;
  correct: boolean;
  chosenAnswerId?: number | number[] | null;
  correctAnswerId?: number[] | null;
  chosenText?: string | null;
  correctText?: string | null;
}

export interface LearnerQuizAttempt {
  /**
   * The quiz component's off-the-job hours, when it is component-linked — what
   * this attempt put towards the learner's total.
   */
  expectedOtjh?: number | null;
  kind?: 'quiz';
  moduleId?: string | null;
  moduleTitle?: string | null;
  weekId?: string | null;
  weekTitle?: string | null;
  componentId?: string | null;
  componentTitle?: string | null;
  componentType?: string | null;
  attempt?: number;           // 1-based attempt number for this quiz
  grade: number;              // 0-1 decimal, e.g. 0.9
  achievedScore?: number;     // questions correct
  totalScore?: number;        // questions total
  passed: boolean;
  quizId: number;
  ksbs?: string[];            // KSB codes the learner marked fulfilled
  feedback?: string;
  reportedTime?: string;      // learner's chosen time (planned label or free text)
  questions?: LearnerQuizQuestionResult[];
  startedAt: string;
  submittedAt: string;
  timeTaken?: string;         // "MM:SS", e.g. "00:26" (auto-tracked)
  timeTrackingSource?: string | null;
  claimedSeconds?: number | null;   // learner/browser supplied duration
  verifiedSeconds?: number | null;  // fallback for the OTJ total when reportedTime is blank
}

/** A coach's verdict on one submitted activity. */
export interface ComponentMarking {
  /** '' when nothing has been handed in; 'submitted_for_tutor_review' while it
   *  waits; 'accepted' | 'partial' once validated; 'referred' | 'rejected'
   *  when sent back for more work. */
  status: string;
  /** The coach's written feedback. Empty until they have reviewed it. */
  feedback: string;
  reviewedBy: string;
  reviewedAt: string | null;
}

export interface LearnerDetail {
  id: string;
  /** Pilot feature flag; Aptem identity itself remains server-side. */
  studentActivityAvailable?: boolean;
  name: string;
  email: string;
  phone: string;
  programme: string;
  programmeStatus: string;
  /** The enrolment wizard: 'Submitted' by the learner, 'Completed' by staff; older payloads omit it. */
  onboardingStatus?: string;
  /** A Delivery apprentice who has signed all four compliance documents may book their first session. */
  firstSessionUnlocked?: boolean;
  learnerType?: LearnerKind;
  /** Staff-owned Created_users.Attendance_type delivery classification. */
  attendanceType?: string | null;
  programmeStartDate?: string;
  /** The learner's own recorded start, from Created_users.Learner_start_date.
   *  programmeStartDate falls back to the cohort when this is unset, so it can
   *  differ; the dashboard header states this one. */
  learnerStartDate?: string;
  /** The learner's own recorded end, from Created_users.Learner_end_date.
   *  programmeEndDate resolves through the delivery's own end columns, which
   *  stay empty for a learner whose end date is recorded only against
   *  themselves; the dashboard header states this one. */
  learnerEndDate?: string;
  programmeEndDate?: string;
  cohort: string;
  /** The learner's cohort schedule, from curriculum.cohorts. Gateway is a date
   *  the cohort reaches, not something finishing the modules early unlocks. */
  cohortStartDate?: string;
  /** Last day of the practical period — Gateway opens once it has passed. */
  gatewayStartDate?: string;
  epaEndDate?: string;
  epaMonths?: number | null;
  group: string;
  employer: string;
  employerId?: number | null;
  organization?: string;
  lineManager: string;
  isActive: boolean;
  modules: string[];
  week: LearnerWeekEntry[];
  components: LearnerComponentEntry[];
  /** Passed quizzes whose slot was later removed; kept so the module still
   *  shows the completed work. Never part of `components`. */
  retiredQuizComponents?: LearnerComponentEntry[];
  ksbs: LearnerKsbItem[];
  progressKsbCodes?: string[];
  quizAttempts: LearnerQuizAttempt[];
  videoProgress?: LearnerVideoProgress[];
  componentProgress?: LearnerComponentProgress[];  // non-quiz, non-video completions
  /** The coach's decision per component id, for activities that need
   *  validating. An activity whose component sets tutorValidationRequired is
   *  not finished until `status` reads 'accepted' — finishing only hands it in. */
  componentMarkingStatus?: Record<string, ComponentMarking>;
  activityFeed?: LearnerActivityEntry[];   // newest first
  totalExpectedOtjh: number;
  plannedHours?: string;      // planned OTJ hours (also stored in Active_users.planned_hours)
  completedHours?: string;    // completed OTJ hours, all activities (Active_users.Completed_hours)
  targetHours?: string;       // cumulative planned hours up to the current week (Target_hours)
  progressHours?: string;     // completed - target (Progress_Hours)
  progressVariance?: string;  // (completed - target) / target, decimal (Progress_variance); '' if target=0
  otjhStatus?: string;        // "On track" | "Need attention" | "At risk" (OTJHoursStatus)
  currentModule?: string | null;  // module targetHours' cumulative-target week currently falls in
  currentWeek?: string | null;    // that week's own label, e.g. "Week 4"
  componentsTargetToDate?: number | null;  // components expected by now, same pacing as targetHours
  /**
   * Why this learner cannot start yet, from the same conditions that would
   * activate them (learner_progression.access_gate). Absent on older responses;
   * `blocked: false` when nothing is holding them back.
   */
  accessGate?: LearnerAccessGate;
  learningAccess?: LearnerLearningAccess;
}

export type LearnerSummary = Pick<LearnerDetail,
  'id' | 'name' | 'email' | 'phone' | 'programme' | 'programmeStatus' | 'onboardingStatus' | 'firstSessionUnlocked' |
  'cohort' | 'group' | 'employer' | 'employerId' | 'organization' | 'learnerType' | 'isActive'
> & Pick<LearnerDetail, 'studentActivityAvailable' | 'programmeStartDate' | 'learnerStartDate' | 'learnerEndDate' | 'programmeEndDate' | 'accessGate' | 'learningAccess'>;

/** Small identity response for pages that only need the learner heading. */
export function fetchLearnerSummary(kind: LearnerKind, id: string, force = false): Promise<LearnerSummary> {
  if (force) return summaryResource.read(`${kind}:${id}`, { revalidate: true });
  const detail = peekLearnerDetail(kind, id);
  return detail ? Promise.resolve(detail) : summaryResource.read(`${kind}:${id}`);
}

/** Render a revisit immediately, without a loading-frame flash. */
export function peekLearnerDetail(kind: LearnerKind, id: string, whileRefreshing = false): LearnerDetail | undefined {
  return detailResource.peek(`${kind}:${id}`, whileRefreshing ? 5 * 60_000 : 0);
}

/** A completed non-quiz, non-video component (podcast/reading/slides/reflection/…). */
export interface LearnerComponentProgress {
  /**
   * The component's off-the-job hours — what this completion put towards the
   * learner's total (see active_users.completed_hours_from_progress). Absent on
   * records written before components carried hours.
   */
  expectedOtjh?: number | null;
  kind: 'component' | 'quiz_reading';
  componentType: string;      // 'podcast' | 'reading' | 'powerpoint' | 'reflection' | …
  componentId: string;
  componentTitle?: string;
  attempt?: number;
  ksbs?: string[];
  feedback?: string;
  reportedTime?: string;
  startedAt: string | null;
  submittedAt: string;
  timeTaken: string | null;
  timeTrackingSource?: string | null;
  claimedSeconds?: number | null;   // learner/browser supplied duration
  verifiedSeconds?: number | null;  // fallback for the OTJ total when reportedTime is blank
  // Ungraded completions leave this absent — the row itself is the completion.
  // An explicit false is a recorded failure and never counts as achievement.
  passed?: boolean | null;
}

export interface LearnerActivityEntry {
  kind: 'quiz' | 'video' | 'component';
  componentType?: string;
  action: string;             // e.g. "Completed quiz", "Watched video"
  title: string;
  detail?: string;            // e.g. "Scored 90% · 18/20"
  passed?: boolean;
  quizId?: number;
  componentId?: string;
  week?: string | null;
  module?: string | null;
  at: string;                 // ISO timestamp
}

export interface LearnerVideoProgress {
  /**
   * The component's off-the-job hours — what this completion put towards the
   * learner's total (see active_users.completed_hours_from_progress). Absent on
   * records written before components carried hours.
   */
  expectedOtjh?: number | null;
  kind: 'video';
  componentId: string;
  /** Saved title of this completion, including historical videos no longer in the current plan. */
  componentTitle?: string;
  attempt?: number;
  ksbs?: string[];
  feedback?: string;
  reportedTime?: string;
  startedAt: string | null;
  submittedAt: string;
  timeTaken: string | null;
  timeTrackingSource?: string | null;
  claimedSeconds?: number | null;   // learner/browser supplied duration
  verifiedSeconds?: number | null;  // fallback for the OTJ total when reportedTime is blank
  // See LearnerComponentProgress.passed.
  passed?: boolean | null;
}

/** Remove cached learner data after a progress-changing action. */
export function invalidateLearnerDetailCache(kind?: LearnerKind, id?: string, scope?: WeeklyProgressScope): void {
  const key = kind && id ? `${kind}:${id}` : undefined;
  detailResource.invalidate(key);
  quizDetailResource.invalidate(key);
  componentResource.invalidate();
  summaryResource.invalidate(key);
  invalidateWeeklyLearning(key, scope);
  invalidateLearnerReads();
}

/** Share the expensive workspace payload between learner pages. */
export function fetchLearnerDetail(kind: LearnerKind, id: string, options: { force?: boolean; revalidate?: boolean; componentId?: string; quizView?: boolean } = {}): Promise<LearnerDetail> {
  if (options.force) invalidateLearnerDetailCache(kind, id);
  if (options.quizView) return quizDetailResource.read(`${kind}:${id}`, { revalidate: options.revalidate });
  if (options.componentId) return componentResource.read(JSON.stringify([kind, id, options.componentId]));
  return detailResource.read(`${kind}:${id}`, { revalidate: options.revalidate });
}

export function peekLearnerSummary(kind: LearnerKind, id: string): LearnerSummary | undefined {
  return summaryResource.peek(`${kind}:${id}`) ?? peekLearnerDetail(kind, id);
}
