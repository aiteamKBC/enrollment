import { coachFetch } from '@/lib/coachFetch';
import { clearCurriculumGetCache } from '@/lib/curriculumApi';
import { formatSystemTimestamp } from '@/lib/format';
import type { OffScheduleRun } from './offScheduleRuns';

/**
 * Calendar health: what each session's Teams meeting is and whether it is sound.
 *
 * Reading health and opening a session never call Microsoft -- the backend reads
 * the LMS rows, the last saved status check and the integrity log. Only a
 * confirmed resolution changes anything, and only the one the preview showed.
 */

export type MeetingType = 'main' | 'additional' | 'standalone';
export type LinkIntegrity = 'verified' | 'link_conflict' | 'missing' | 'verification_required' | 'verification_failed';
export type Membership = 'in_plan' | 'not_in_plan' | 'manually_cancelled';
export type HealthStatus = 'healthy' | 'attention_required' | 'verification_pending' | 'verification_failed';
export type HealthFilter = 'all' | 'main' | 'additional' | 'standalone' | 'link_conflict' | 'missing' | 'lifecycle' | 'audit' | 'not_in_plan';
export type ResolutionAction = 'keep_existing' | 'mark_intentional' | 'reassociate_main' | 'create_replacement' | 'reopen_completion' | 'recheck_occurrence';

export interface HealthIssue { code: string; severity: 'error' | 'warning' | 'notice'; confirmed?: boolean; message: string }

export interface MeetingRef { eventId: string; onlineMeetingId: string; joinUrl?: string; joinRef: string; organizer?: string }

export interface Attribution { known: boolean; label: string; origin: string; initiatedBy: string; trigger: string; jobId: string; at: string }

export interface HealthSession {
  occurrenceId: string;
  liveSessionId: string;
  sessionNumber: number;
  startDateTimeUtc: string;
  endDateTimeUtc: string;
  meetingType: MeetingType;
  integrity: LinkIntegrity | null;
  integrityBasis: string;
  verificationStale: boolean;
  membership: Membership;
  lifecycle: string;
  lifecycleIssues: HealthIssue[];
  auditIssues: HealthIssue[];
  attribution: Attribution | null;
  resolution: { type: string; at: string; by: string } | null;
  meeting: MeetingRef;
  title?: string;
  /** Calls on this link that were not this session; ignored, and said so. */
  offScheduleRuns?: OffScheduleRun[];
}

export interface HealthWarning {
  category: 'link' | 'lifecycle' | 'audit';
  severity: 'error' | 'warning' | 'notice';
  title: string;
  module: string;
  sessions: number[];
  explanation: string;
  nextAction: string;
  lastVerifiedAt: string;
}

export interface CalendarHealth {
  liveSessionId: string;
  module: { moduleCatalogueId: string; title: string; programme: string; cohort: string; group: string };
  mainMeeting: MeetingRef;
  summary: {
    status: HealthStatus;
    counts: Record<'plannedSessions' | 'mainSeries' | 'additionalMeetings' | 'standaloneReplacements' | 'missingOccurrences'
      | 'linkConflicts' | 'lifecycleIssues' | 'auditIssues' | 'notInPlan', number>;
    lastVerifiedAt: string;
    verificationStale: boolean;
    neverVerified: boolean;
    lastVerificationFailure: { at: string; message: string } | null;
    auditLogAvailable: boolean;
  };
  warnings: HealthWarning[];
  sessions: { items: HealthSession[]; page: number; pageSize: number; pages: number; total: number; filter: HealthFilter };
  readOnly: true;
}

export interface TimelineEntry {
  id: string; type: string; at: string; sessionNumber: number | null; origin: string; by: string;
  executedBy: string; trigger: string; jobId: string; correlationId: string;
  before: Partial<MeetingRef>; after: Partial<MeetingRef>; outcome: string; detail: Record<string, unknown>;
}

export interface SessionDetail extends HealthSession {
  module: CalendarHealth['module'];
  mainMeeting: MeetingRef;
  actualStartUtc: string;
  actualEndUtc: string;
  attendanceReportLinked: boolean;
  participantCount: number;
  createdAt: string;
  updatedAt: string;
  evidence: { attendanceRecords: number | null; recordings: number | null; transcripts: number | null };
  separateFromMain: boolean;
  compare: { main: MeetingRef & { membership: string }; session: MeetingRef & { membership: string; startDateTimeUtc: string; endDateTimeUtc: string }; implications: string[] };
  auditHistory: TimelineEntry[];
  statusHistory: TimelineEntry[];
  resolutions: { action: ResolutionAction; label: string; readOnly: boolean }[];
}

export interface ResolutionPreview {
  action: ResolutionAction;
  label: string;
  sessionNumber: number;
  changes: string[];
  unchanged: string[];
  followUp: string[];
  microsoft: { creates: boolean; updates: boolean; cancels: boolean; reads: boolean; mayEmail: boolean };
  lmsEmail: boolean;
  blocked: string;
  requiresConfirmation: boolean;
  previewToken: string;
}

const base = () => import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
const healthPath = (liveSessionId: string) => `${base()}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/calendar-health/`;

async function readJson(response: Response, fallback: string) {
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(result?.error || fallback) as Error & { code?: string; existing?: unknown };
    error.code = result?.code;
    error.existing = result?.existing;
    throw error;
  }
  return result;
}

/** Read-only. */
export async function fetchCalendarHealth(liveSessionId: string, filter: HealthFilter = 'all', page = 1, pageSize = 25): Promise<CalendarHealth> {
  const query = new URLSearchParams({ filter, page: String(page), pageSize: String(pageSize) });
  const result = await readJson(await coachFetch(`${healthPath(liveSessionId)}?${query}`, { method: 'GET' }),
    'Calendar health could not be loaded. Nothing was changed.');
  if (!result?.summary || !Array.isArray(result?.sessions?.items)) throw new Error('Calendar health returned an unreadable answer.');
  return result as CalendarHealth;
}

/** Read-only. */
export async function fetchSessionDetail(liveSessionId: string, occurrenceId: string): Promise<SessionDetail> {
  const result = await readJson(
    await coachFetch(`${healthPath(liveSessionId)}sessions/${encodeURIComponent(occurrenceId)}/`, { method: 'GET' }),
    'This session could not be loaded. Nothing was changed.');
  if (!result?.session) throw new Error('This session returned an unreadable answer.');
  return result.session as SessionDetail;
}

async function postResolution(liveSessionId: string, body: Record<string, unknown>) {
  return readJson(await coachFetch(`${healthPath(liveSessionId)}resolution/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }), 'The resolution could not be completed. Nothing was changed.');
}

/** Describes the action. Changes nothing and reads nothing from Microsoft. */
export async function previewResolution(liveSessionId: string, occurrenceId: string, action: ResolutionAction): Promise<ResolutionPreview> {
  const result = await postResolution(liveSessionId, { occurrenceId, action });
  if (!result?.preview) throw new Error('The review returned an unreadable answer.');
  return result.preview as ResolutionPreview;
}

/** Read-only re-check of one missing occurrence. */
export async function recheckOccurrence(liveSessionId: string, occurrenceId: string): Promise<{ present: boolean; sessionNumber: number }> {
  return postResolution(liveSessionId, { occurrenceId, action: 'recheck_occurrence' });
}

/** Runs exactly the previewed action, against exactly the previewed state. */
export async function confirmResolution(liveSessionId: string, occurrenceId: string, preview: ResolutionPreview) {
  const result = await postResolution(liveSessionId, {
    occurrenceId, action: preview.action, confirm: true, previewToken: preview.previewToken,
  });
  clearCurriculumGetCache();
  return result as { resolved: boolean; action: ResolutionAction; warnings?: string[] };
}

export const STATUS_LABELS: Record<HealthStatus, string> = {
  healthy: 'Healthy',
  attention_required: 'Needs review',
  verification_pending: 'Verification pending',
  verification_failed: 'Verification failed',
};

export const STATUS_TONES: Record<HealthStatus, string> = {
  healthy: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  attention_required: 'border-red-200 bg-red-50 text-red-800',
  verification_pending: 'border-amber-200 bg-amber-50 text-amber-900',
  verification_failed: 'border-red-200 bg-red-50 text-red-800',
};

export const MEETING_TYPE_LABELS: Record<MeetingType, string> = {
  main: 'Main series',
  additional: 'Additional meeting',
  standalone: 'Standalone replacement',
};

export const MEETING_TYPE_EXPLANATIONS: Record<MeetingType, string> = {
  main: "This session uses the module's recurring Teams meeting.",
  additional: 'This is an intentionally separate Teams meeting for this week.',
  standalone: 'This session uses a separate meeting created to replace a missing occurrence.',
};

export const INTEGRITY_LABELS: Record<LinkIntegrity, string> = {
  verified: 'Verified',
  link_conflict: 'Link conflict',
  missing: 'Missing from Teams',
  verification_required: 'Verification required',
  verification_failed: 'Verification failed',
};

export const INTEGRITY_EXPLANATIONS: Record<LinkIntegrity, string> = {
  verified: 'Microsoft confirmed this session on the expected meeting at the last status check.',
  link_conflict: 'The LMS and Microsoft meeting associations require review. Participants may be using different meeting links.',
  missing: 'The LMS expects a session, but its Microsoft Teams occurrence could not be verified. Resolution required.',
  verification_required: 'Microsoft has not been checked for this session yet. Run the calendar status check.',
  verification_failed: 'The last status check could not match this session in Microsoft.',
};

export const INTEGRITY_TONES: Record<LinkIntegrity, string> = {
  verified: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  link_conflict: 'bg-red-50 text-red-800 border-red-200',
  missing: 'bg-red-50 text-red-800 border-red-200',
  verification_required: 'bg-amber-50 text-amber-900 border-amber-200',
  verification_failed: 'bg-red-50 text-red-800 border-red-200',
};

export const LIFECYCLE_LABELS: Record<string, string> = {
  scheduled: 'Scheduled',
  scheduled_date_passed: 'Scheduled (date passed, no attendance yet)',
  completed: 'Completed',
  not_in_plan: 'Not in plan — still on Teams',
  cancelled: 'Cancelled',
};

export const MEMBERSHIP_LABELS: Record<Membership, string> = {
  in_plan: 'In plan',
  not_in_plan: 'Not in plan — still on Teams',
  manually_cancelled: 'Manually cancelled',
};

export const FILTER_LABELS: Record<HealthFilter, string> = {
  all: 'All sessions',
  main: 'Main series',
  additional: 'Additional meetings',
  standalone: 'Standalone replacements',
  link_conflict: 'Link conflicts',
  missing: 'Missing occurrences',
  lifecycle: 'Lifecycle issues',
  audit: 'Audit issues',
  not_in_plan: 'Not in plan',
};

export const EVENT_LABELS: Record<string, string> = {
  calendar_reconciled: 'Calendar saved and compared with Teams',
  occurrence_missing_detected: 'Occurrence found missing from the recurring series',
  verification_succeeded: 'Calendar status checked',
  verification_failed: 'Calendar status check failed',
  replacement_created: 'Replacement meeting created',
  replacement_creation_uncertain: 'Replacement creation unconfirmed by Microsoft',
  replacement_creation_failed: 'Replacement creation refused by Microsoft',
  session_reassociated: 'Session reassociated with the main meeting',
  marked_intentional: 'Marked as intentional separate meeting',
  kept_existing: 'Reviewed and kept as it is',
  lifecycle_completed: 'Marked Completed by attendance sync',
  lifecycle_premature_run_ignored: 'Teams run before the session ignored',
  lifecycle_reopened: 'Returned to Scheduled',
};

export function formatWhen(value: string): string {
  if (!value) return 'Not recorded';
  return formatSystemTimestamp(value, { dateStyle: 'medium', timeStyle: 'short' }) || 'Not recorded';
}

export function shortId(value: string): string {
  if (!value) return 'Not recorded';
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
