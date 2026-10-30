// ============================================================================
// Employer portal API client
//
// The employer-facing view of their own learners: who they are, what each
// learner's progress looks like, and which documents still need the employer's
// signature.
//
// Signing reuses the existing endpoints rather than adding parallel ones:
//  - reviews  -> /learner_api/reviews/<kind>/<id>/<eventKey>/sign/ with
//                party="employer", the same call the learner and admin sides make
//  - PDFs     -> /enrolment_api/documents/<kind>/<id>/<docId>/sign/
// ============================================================================

import type { LearnerDetail, LearnerKind } from '@/api/learnerDetail';
import type { HomeProgress, OverviewWeek } from '@/api/learnerOverview';
import type { TrainingPlanContract, TrainingPlanDashboard } from '@/api/trainingPlanDashboard';
import type { MonthlyLogHours, LogSummary, LogDetail } from '@/features/monthly-logs/api';
import type { ActivityContent } from '@/features/old-otjh/api';
import type { ReviewFormResponse } from '@/api/reviewForm';
import type { LearnerCalendarEvent, LearnerReviewDefinition } from '@/api/learnerCalendar';
import type { CoachMeetingArtifactsResponse } from '@/pages/coach/shared/calendarEvents';
import type { ProgressMetric } from '@/api/learnerMetrics';
import type { DashboardOverviewPayload } from '@/api/dashboardTabs';
import type { DashboardTrainingPayload } from '@/api/dashboardTraining';

const BASE = '/learner_api/employer-portal';

export type EmployerDashboardMetrics = {
  asOf: string;
  learners: Record<string, {
    programme: Pick<ProgressMetric, 'completed' | 'total' | 'percent' | 'status'> | null;
    otjh: Pick<HomeProgress['otjh'], 'actual' | 'planned' | 'percent'> | null;
    attendance: EmployerLearnerAttendance | null;
    risk: 'on-track' | 'need-attention' | 'at-risk' | 'unavailable';
  }>;
};

export function fetchEmployerDashboardMetrics(employerId: string, signal?: AbortSignal, section?: 'all' | 'summary' | 'programme' | 'progress' | 'status') {
  return request<EmployerDashboardMetrics>(`${BASE}/${employerId}/dashboard-metrics/${section ? `?section=${section}` : ''}`, { signal });
}

export type EmployerProgressReviews = {
  events: LearnerCalendarEvent[];
  definitions: Record<string, LearnerReviewDefinition | null>;
};

export function fetchEmployerProgressReviews(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return request<EmployerProgressReviews>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/`, { signal });
}

export function fetchEmployerProgressReviewSummary(employerId: string, kind: LearnerKind, learnerId: string,
  month: string, signal?: AbortSignal) {
  return request<{ events: LearnerCalendarEvent[] }>(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/?summary_month=${encodeURIComponent(month)}`, { signal });
}

export function fetchEmployerProgressReview(employerId: string, kind: LearnerKind, learnerId: string, eventKey: string, signal?: AbortSignal) {
  return request<{ event: LearnerCalendarEvent; definition: LearnerReviewDefinition | null }>(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/${encodeURIComponent(eventKey)}/`, { signal });
}

export function fetchEmployerMeetingArtifacts(employerId: string, kind: LearnerKind, learnerId: string, eventKey: string, signal?: AbortSignal) {
  return request<CoachMeetingArtifactsResponse>(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/${encodeURIComponent(eventKey)}/artifacts/`, { signal });
}

export function employerMeetingArtifactContentUrl(employerId: string, kind: LearnerKind, learnerId: string, eventKey: string,
  artifactType: string, artifactId: string, options: { preview?: boolean } = {}): string {
  const base = `${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/${encodeURIComponent(eventKey)}`
    + `/artifacts/${encodeURIComponent(artifactType)}/${encodeURIComponent(artifactId)}/content/`;
  return options.preview ? `${base}?preview=1` : base;
}

export async function downloadEmployerProgressReview(employerId: string, kind: LearnerKind, learnerId: string, eventKey: string) {
  const { saveReviewPdfResponse } = await import('./reviewPdf');
  await saveReviewPdfResponse(await fetch(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/progress-reviews/${encodeURIComponent(eventKey)}/pdf/`,
    { credentials: 'include' }));
}

function monthlyLogPath(employerId: string, kind: LearnerKind, learnerId: string) {
  return `${BASE}/${employerId}/learner/${kind}/${learnerId}/monthly-logs/`;
}

export function fetchEmployerMonthlyLogSummary(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return request<LogSummary>(monthlyLogPath(employerId, kind, learnerId), { signal });
}

export function fetchEmployerMonthlyLog(employerId: string, kind: LearnerKind, learnerId: string, month: string, signal?: AbortSignal) {
  return request<LogDetail>(`${monthlyLogPath(employerId, kind, learnerId)}${month}/`, { signal });
}

export async function fetchEmployerMonthlyLogContent(employerId: string, kind: LearnerKind, learnerId: string, month: string, rowId: number) {
  const content = await request<ActivityContent>(`${monthlyLogPath(employerId, kind, learnerId)}${month}/activities/${rowId}/`);
  return { ...content, parts: content.parts.map(part => ({ ...part,
    url: part.url?.startsWith('/') && !part.url.startsWith('//') ? new URL(part.url, window.location.origin).href : part.url,
  })) };
}

export interface EmployerLearnerCard {
  id: string;
  kind: 'apprenticeship' | 'commercial';
  name: string;
  email: string;
  programme: string;
  cohort: string;
  programmeStatus: string;
  onboardingStatus: string;
  /** On programme — their card leads with performance rather than paperwork. */
  isActive: boolean;
  /** Documents this employer can sign right now but hasn't. */
  outstandingCount: number;
  documentsTotal: number;
  /** Student Home's canonical OTJ hours and percentage, included in the list response. */
  otjhProgress: { actual: number | null; planned: number | null; percent: number | null } | null;
}

/** A review awaiting (or carrying) the employer's signature. */
export interface EmployerReviewRow {
  kind: 'review';
  eventKey: string;
  reviewType: string;
  label: string;
  scheduledDate: string;
  /** False until the questionnaire itself is finished — nobody can sign before. */
  signable: boolean;
  completed: boolean;
  sectionsTotal: number;
  employerSignatureRequired: boolean;
  signed: boolean;
  signedName: string;
  signedAt: string | null;
  learnerSigned: boolean;
  adminSigned: boolean;
  reviewInstanceId?: string;
  migratedForm?: boolean;
}

/** A generated compliance PDF awaiting (or carrying) the employer's signature. */
export interface EmployerDocumentRow {
  /**
   * 'agreement' is the Apprenticeship Agreement, which has its own table and
   * signing endpoint but appears in the same signable list as everything else.
   */
  kind: 'document' | 'agreement' | 'training-plan' | 'written-agreement';
  id: string;
  docType: string;
  label: string;
  generatedAt: string | null;
  /** False for documents this type doesn't ask the employer to sign. */
  signable: boolean;
  signed: boolean;
  signedName: string;
  signedAt: string | null;
  /** Every party this document needs, e.g. ['learner', 'employer']. */
  parties?: string[];
  /** The learner's side, so the employer can see who else has signed. */
  learnerSigned?: boolean;
  learnerSignedName?: string;
  learnerSignedAt?: string | null;
  /** The provider's side, on tripartite documents like the Training Plan. */
  providerSigned?: boolean;
  providerSignedName?: string;
  providerSignedAt?: string | null;
}

export type SignableItem = EmployerReviewRow | EmployerDocumentRow;

export interface EmployerPortal {
  employer: {
    id: string;
    name: string;
    email: string;
    employerGroupNames: string[];
  };
  learners: EmployerLearnerCard[];
  outstandingTotal: number;
}

export interface EmployerPerformance {
  quizzesTaken: number;
  quizzesPassed: number;
  averageScore: number | null;
  componentsCompleted: number;
  ksbsEvidenced: number;
  completedHours: string | null;
  lastActivityAt: string | null;
}

export interface EmployerLearnerDetail {
  employer: { id: string; name: string };
  learner: {
    id: string;
    kind: 'apprenticeship' | 'commercial';
    name: string;
    email: string;
    phone: string;
    programme: string;
    cohort: string;
    /** The learner record's employer display name. */
    employer?: string;
    /** The learner record's organisation display name. */
    organization?: string;
    programmeStatus: string;
    onboardingStatus: string;
    startDate: string;
    endDate: string;
    /** The dates recorded against the learner, as their own dashboard header
     *  shows them. Distinct from startDate/endDate above, which are the
     *  enrolment's Start_date/End_date columns. */
    learnerStartDate?: string;
    learnerEndDate?: string;
    isActive: boolean;
  };
  performance: EmployerPerformance;
  reviews: EmployerReviewRow[];
  documents: EmployerDocumentRow[];
  outstandingCount: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      // The portal endpoints are gated on the session (employer_or_staff): an
      // employer may read only their own record, staff may read any. Without
      // this the HttpOnly kbc_session cookie is not sent and every call 401s.
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(res.ok
      ? 'The employer portal returned an unexpected response. Please try again.'
      : `Could not load employer portal data (${res.status}). Please try again.`);
  }
  if (!res.ok) {
    throw new Error((data && (data.error || data.detail)) || `Request failed (${res.status})`);
  }
  return data as T;
}

// The landing page is the heaviest employer read. A second caller while it is
// still loading (StrictMode's double effect, the progress-reviews page) shares
// the request on its way rather than asking the server to build it again.
const portalRequests = new Map<string, Promise<EmployerPortal>>();

export function fetchEmployerPortal(employerId: string): Promise<EmployerPortal> {
  const existing = portalRequests.get(employerId);
  if (existing) return existing;
  const pending = request<EmployerPortal>(`${BASE}/${employerId}/`);
  const clear = () => {
    if (portalRequests.get(employerId) === pending) portalRequests.delete(employerId);
  };
  pending.then(clear, clear);
  portalRequests.set(employerId, pending);
  return pending;
}

/** Whose document a row is, on the all-documents list. */
export interface EmployerDocumentOwner {
  id: string;
  kind: 'apprenticeship' | 'commercial';
  name: string;
  programme: string;
}

export type EmployerOwnedItem = SignableItem & { learner: EmployerDocumentOwner };

export interface EmployerDocuments {
  employer: { id: string; name: string };
  items: EmployerOwnedItem[];
  outstandingTotal: number;
}

/** One file the learner uploaded to an assignment. */
export type EmployerAssignmentFile =
  | { source: 'lms'; id: string; name: string }
  | { source: 'aptem'; id: string; activityId: string; name: string };

/** A handed-in assignment, as the employer sees it: no marks or tutor feedback. */
export interface EmployerAssignment {
  id: string;
  source: 'lms' | 'aptem';
  title: string;
  moduleTitle: string;
  weekTitle: string;
  status: string;
  submittedAt: string | null;
  files: EmployerAssignmentFile[];
}

/** Every assignment this learner has handed in, current and legacy. */
export function fetchEmployerLearnerAssignments(employerId: string, kind: string, learnerId: string) {
  return request<{ assignments: EmployerAssignment[] }>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/assignments/`);
}

/** A short-lived link to one uploaded assignment file. */
export async function fetchEmployerAssignmentFileUrl(employerId: string, kind: string, learnerId: string, file: EmployerAssignmentFile) {
  const base = `${BASE}/${employerId}/learner/${kind}/${learnerId}/assignments`;
  const url = file.source === 'lms'
    ? `${base}/files/${encodeURIComponent(file.id)}/`
    : `${base}/legacy/${encodeURIComponent(file.id)}/?activityId=${encodeURIComponent(file.activityId)}`;
  return (await request<{ url: string }>(url)).url;
}

/** Every signable item across this employer's learners. */
export function fetchEmployerDocuments(employerId: string): Promise<EmployerDocuments> {
  return request<EmployerDocuments>(`${BASE}/${employerId}/documents/`);
}

export function fetchEmployerLearner(
  employerId: string,
  kind: string,
  learnerId: string,
): Promise<EmployerLearnerDetail> {
  return request<EmployerLearnerDetail>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/`);
}

/**
 * The learner's own training plan, hours and KSBs.
 *
 * Deliberately the same payload the learner's workspace reads (LearnerDetail),
 * so the employer sees the identical weeks, components and KSB mappings instead
 * of a second summary that could drift. The employer UI renders it read-only.
 */
export function fetchEmployerLearnerPlan(
  employerId: string,
  kind: string,
  learnerId: string,
): Promise<LearnerDetail> {
  return request<LearnerDetail>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/plan/`);
}

/**
 * The learner-dashboard reads behind the Overview tab, served through the portal.
 *
 * The learner's own dashboard endpoints admit only the learner and staff, so an
 * employer signed in to the portal gets these same payloads from here instead,
 * behind the portal's employer-owns-this-learner check. Meeting and booking
 * links come back empty: the employer's view is read-only.
 */
function overviewPart<T>(employerId: string, kind: string, learnerId: string, part: string, signal?: AbortSignal) {
  return request<T>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/overview/${part}/`, { signal });
}

/** Existing learner projections, authorized through the employer's own routes. */
export async function fetchEmployerLearningOverview(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  const data = await overviewPart<DashboardOverviewPayload>(employerId, kind, learnerId, 'dashboard', signal);
  if (!data?.metrics?.otjh || !data.metrics.ksb || !Array.isArray(data.programme)
    || !Array.isArray(data.modules) || !Array.isArray(data.hours?.months)) {
    throw new Error('Could not load learner progress. Please try again.');
  }
  return data;
}

export async function fetchEmployerTraining(employerId: string, kind: LearnerKind, learnerId: string,
  subject = '', month = '', signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (subject) query.set('subject', subject);
  if (month) query.set('month', month);
  const data = await request<DashboardTrainingPayload>(`${BASE}/${employerId}/learner/${kind}/${learnerId}/overview/training/${query.size ? `?${query}` : ''}`, { signal });
  if (!Array.isArray(data?.timeline?.modules) || !Array.isArray(data.timeline.reviews)
    || (data.selectedModuleId && data.selectedModule?.id !== data.selectedModuleId)) {
    throw new Error('Could not load the training plan. Please try again.');
  }
  return data;
}

export function fetchEmployerLearnerWeek(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return overviewPart<OverviewWeek>(employerId, kind, learnerId, 'week', signal);
}

/** Attendance-register totals: the figure the learner's dashboard and the coach case file show. */
export interface EmployerLearnerAttendance {
  sessions: number;
  present: number;
  absent: number;
  late: number;
  attendanceRate: number;
}

export function fetchEmployerLearnerAttendance(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return overviewPart<{ attendance: EmployerLearnerAttendance | null }>(employerId, kind, learnerId, 'attendance', signal);
}

export function fetchEmployerLearnerSchedule(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return overviewPart<TrainingPlanDashboard>(employerId, kind, learnerId, 'schedule', signal);
}

export function fetchEmployerLearnerContract(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return overviewPart<TrainingPlanContract>(employerId, kind, learnerId, 'contract', signal);
}

/** Per-month hour totals only — no activity rows or signatures. */
export function fetchEmployerLearnerHours(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return overviewPart<MonthlyLogHours>(employerId, kind, learnerId, 'hours', signal);
}

/** The learner's profile photo, or null when they have not added one. */
export async function fetchEmployerLearnerPhoto(employerId: string, kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  const res = await fetch(`${BASE}/${employerId}/learner/${kind}/${learnerId}/overview/photo/`, { credentials: 'include', signal });
  if (res.status === 204) return null;
  if (!res.ok || !res.headers.get('Content-Type')?.startsWith('image/jpeg')) throw new Error('The photo could not be loaded.');
  return res.blob();
}


/**
 * Sign a review as the employer — the same endpoint the learner and admin use,
 * with party="employer". An empty signature withdraws the sign-off.
 */
export function fetchEmployerReviewInstance(
  employerId: string,
  kind: string,
  learnerId: string,
  eventKey: string,
): Promise<unknown> {
  return request(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/`,
  );
}

export function signReviewAsEmployer(
  employerId: string,
  kind: string,
  learnerId: string,
  eventKey: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/`,
    { method: 'POST', body: JSON.stringify({ party: 'employer', ...input }) },
  );
}

/**
 * Read a legacy Enrolment_Reviews document as the employer, for "Show
 * document". The learner-side read is closed to employers.
 */
export function fetchEmployerEnrolmentReview(
  employerId: string,
  kind: string,
  learnerId: string,
  eventKey: string,
): Promise<ReviewFormResponse> {
  return request<ReviewFormResponse>(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/reviews/${encodeURIComponent(eventKey)}/`,
  );
}

/**
 * Sign a legacy Enrolment_Reviews row (one with no Curriculum review instance)
 * as the employer. The review-instance endpoint above 404s for these, so they
 * keep the original enrolment-review sign route. An empty signature withdraws.
 */
export function signEnrolmentReviewAsEmployer(
  kind: string,
  learnerId: string,
  eventKey: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `/learner_api/reviews/${kind}/${learnerId}/${encodeURIComponent(eventKey)}/sign/`,
    { method: 'POST', body: JSON.stringify({ party: 'employer', ...input }) },
  );
}

/** Save the employer's own answers to whichever fields the Curriculum
 * template opted the Employer into answering -- every other field id is
 * rejected server-side, so only the fields this form actually unlocked
 * should ever be posted here. A distinct payload shape (`answers`, not
 * `party`/`signature`) from signReviewAsEmployer above, so it never touches
 * the sign-off flow. */
export function saveEmployerReviewAnswers(
  employerId: string,
  kind: string,
  learnerId: string,
  eventKey: string,
  answers: Record<string, unknown>,
): Promise<unknown> {
  return request(
    `${BASE}/${employerId}/learner/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/`,
    { method: 'POST', body: JSON.stringify({ answers }) },
  );
}

/** Sign a generated compliance PDF. An empty signature withdraws the sign-off. */
export function signDocumentAsEmployer(
  kind: string,
  learnerId: string,
  docId: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `/enrolment_api/documents/${kind}/${learnerId}/${docId}/sign/`,
    { method: 'POST', body: JSON.stringify(input) },
  );
}

/**
 * The Apprenticeship Agreement has its own table and signing endpoint, so it
 * does not go through the generic document route above.
 */
export function signAgreementAsEmployer(
  learnerId: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `/learner_api/apprenticeship-agreement/${learnerId}/sign/`,
    { method: 'POST', body: JSON.stringify({ ...input, party: 'employer' }) },
  );
}

/** The Training Plan is tripartite and has its own signing endpoint. */
export function signTrainingPlanAsEmployer(
  learnerId: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `/learner_api/training-plan-document/${learnerId}/sign/`,
    { method: 'POST', body: JSON.stringify({ ...input, party: 'employer' }) },
  );
}

/** The Written Agreement is tripartite and has its own signing endpoint. */
export function signWrittenAgreementAsEmployer(
  learnerId: string,
  input: { name: string; signature: string },
): Promise<unknown> {
  return request(
    `/learner_api/written-agreement/${learnerId}/sign/`,
    { method: 'POST', body: JSON.stringify({ ...input, party: 'employer' }) },
  );
}
