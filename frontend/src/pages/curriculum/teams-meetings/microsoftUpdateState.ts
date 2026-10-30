import { coachFetch } from '@/lib/coachFetch';
import { clearCurriculumGetCache } from '@/lib/curriculumApi';

// Where a Teams meeting stands with Microsoft, apart from the invitation.
// A save writes who is invited and the dates (the calendar event) separately
// from how the meeting runs (the onlineMeeting: lobby, recording, transcription,
// language, presenter and co-organiser roles). Microsoft can refuse the second
// while accepting the first, so the page says which, and never calls a refused
// or unconfirmed update "Saved".

export type MicrosoftUpdateState = 'saved' | 'pending' | 'failed';
/**
 * How far a "Saved" goes: `read_back` -- Microsoft's read-back showed the
 * values (only Retry reads back); `accepted` -- Microsoft accepted the last
 * write and nothing is pending; `not_applied` -- something is still waiting.
 */
export type MicrosoftUpdateVerification = 'read_back' | 'accepted' | 'not_applied';

export interface MicrosoftGraphErrorSummary {
  status: number | null;
  code: string;
  message: string;
  requestId: string;
  at: string;
}

export interface MicrosoftUpdateSummary {
  state: MicrosoftUpdateState;
  /** 'settings' and/or 'roles': what Microsoft has not applied yet. */
  pendingGroups: string[];
  lastError: MicrosoftGraphErrorSummary | null;
  /** Retry re-sends only the pending settings/roles; it never moves a date or emails anyone. */
  retryable: boolean;
  /** Absent from an older backend; the page then claims no read-back. */
  verification?: MicrosoftUpdateVerification;
  message: string;
}

export const MICROSOFT_VERIFICATION_NOTES: Record<MicrosoftUpdateVerification, string> = {
  read_back: 'Verified: Microsoft’s read-back shows these settings.',
  accepted: 'Accepted by Microsoft. Not read back since the last save.',
  not_applied: 'Saved in the LMS only. Microsoft has not applied this yet.',
};

export interface MicrosoftOptionsRetryResult {
  retried: string[];
  confirmed?: string[];
  remaining?: string[];
  superseded?: boolean;
  requestedChangeUnknown?: boolean;
  microsoftUpdate: MicrosoftUpdateSummary;
  message: string;
}

export const MICROSOFT_UPDATE_LABELS: Record<MicrosoftUpdateState, string> = {
  saved: 'Saved',
  pending: 'Pending Microsoft update',
  failed: 'Failed',
};

export const MICROSOFT_UPDATE_TONES: Record<MicrosoftUpdateState, string> = {
  saved: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  pending: 'border-amber-200 bg-amber-50 text-amber-800',
  failed: 'border-red-200 bg-red-50 text-red-700',
};

export const OPTION_GROUP_NAMES: Record<string, string> = {
  settings: 'Lobby, recording, transcription and language',
  roles: 'Presenter and co-organiser roles',
};

const NOT_APPLIED = 'teams_meeting_options_not_applied';
const STATES: MicrosoftUpdateState[] = ['saved', 'pending', 'failed'];

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function graphError(value: unknown): MicrosoftGraphErrorSummary | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const status = typeof item.status === 'number' ? item.status : null;
  const error = { status, code: text(item.code), message: text(item.message), requestId: text(item.requestId), at: text(item.at) };
  return error.status || error.code || error.message || error.requestId ? error : null;
}

function groupList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item in OPTION_GROUP_NAMES) : [];
}

/** The server's own summary (a meeting row or a retry result), or null when it sent none. */
export function readMicrosoftUpdate(value: unknown): MicrosoftUpdateSummary | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  if (!STATES.includes(item.state as MicrosoftUpdateState)) return null;
  return {
    state: item.state as MicrosoftUpdateState,
    pendingGroups: groupList(item.pendingGroups),
    lastError: graphError(item.lastError),
    retryable: item.retryable === true,
    verification: (['read_back', 'accepted', 'not_applied'] as const).find(value => value === item.verification),
    message: text(item.message),
  };
}

/**
 * What a save's response says about the meeting options. Null when the server
 * did not report them (an older backend): the page then shows no state rather
 * than guessing "Saved".
 */
export function microsoftUpdateFromSave(result: unknown): MicrosoftUpdateSummary | null {
  if (!result || typeof result !== 'object') return null;
  const item = result as Record<string, unknown>;
  if (typeof item.optionsApplied !== 'boolean') return null;
  const pendingGroups = groupList(item.optionsPending);
  const refusal = (Array.isArray(item.warnings) ? item.warnings : [])
    .find(warning => warning && typeof warning === 'object' && (warning as Record<string, unknown>).code === NOT_APPLIED) as
    Record<string, unknown> | undefined;
  const lastError = graphError(refusal?.graphError);
  if (!item.optionsApplied) {
    return {
      state: 'failed', pendingGroups, lastError, retryable: pendingGroups.length > 0, verification: 'not_applied',
      message: text(refusal?.message) || 'Invitations and dates were saved, but Microsoft did not apply the meeting settings.',
    };
  }
  if (pendingGroups.length) {
    return {
      state: 'pending', pendingGroups, lastError, retryable: true, verification: 'not_applied',
      message: 'Invitations and dates were saved. Meeting settings from an earlier save are still waiting for Microsoft.',
    };
  }
  return {
    state: 'saved', pendingGroups: [], lastError: null, retryable: false, verification: 'accepted',
    message: 'Microsoft accepted this save.',
  };
}

/** Re-sends only the pending settings/roles. No date moves, nobody is emailed, nothing is cancelled. */
export async function retryMicrosoftMeetingOptions(liveSessionId: string): Promise<MicrosoftOptionsRetryResult> {
  const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
  const response = await coachFetch(
    `${base}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/retry-options/`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(text(result?.error) || 'The retry could not finish. Invitations and dates are unchanged.');
  const summary = readMicrosoftUpdate(result?.microsoftUpdate);
  if (!summary || !Array.isArray(result?.retried)) throw new Error('Microsoft’s answer could not be read. Check the meeting again.');
  clearCurriculumGetCache();
  return { ...result, microsoftUpdate: summary, message: text(result.message) };
}
