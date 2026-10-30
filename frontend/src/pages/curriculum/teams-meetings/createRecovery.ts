import type { TeamsCreateStatus, TeamsMeetingResult } from '../module-builder/moduleAuthoringData';

/**
 * What the dialog shows while a Create's outcome is being recovered.
 *
 * `checking` and `stalled` mean the server may still finish; `uncertain` means
 * it stopped without saying whether Microsoft made the meeting. None of the
 * three may offer Create again -- only `failed`, a known outcome that saved no
 * calendar, leaves Create as it was.
 */
export type CreateRecovery =
  | { phase: 'checking' }
  | { phase: 'stalled'; message: string }
  | { phase: 'uncertain'; message: string }
  | { phase: 'failed'; message: string };

export type RecoveredCreate =
  | { kind: 'created'; result: TeamsMeetingResult }
  | { kind: 'stalled' | 'uncertain' | 'failed'; message: string };

export const STALLED_MESSAGE = 'Microsoft is still creating this calendar. Check again in a minute; do not create it again.';

/**
 * What the dialog knows about a Create that is under way, from the server's
 * saved status. `status` is the latest reading (null before the first one);
 * `failures` counts status reads in a row that did not answer; `slow` is set
 * once the create has run longer than it usually takes.
 */
export interface CreateProgress {
  status: TeamsCreateStatus | null;
  failures: number;
  slow: boolean;
}

export type ProgressStepState = 'done' | 'active' | 'pending';

export interface ProgressStep {
  key: 'calendar' | 'invitations' | 'emails';
  label: string;
  detail: string;
  state: ProgressStepState;
}

/**
 * The three things a Create does, in order, and how far each has got.
 *
 * Read from the saved calendar the server writes as it goes: the calendar row
 * exists once Microsoft has built the event; its pending warning clears once
 * every date is verified and Microsoft has sent the invitations; the email
 * ledger counts the LMS schedule emails as they are submitted. A reading from
 * before this create reached the server shows the first step as under way.
 */
export function progressSteps(status: TeamsCreateStatus | null, sessionCount: number): ProgressStep[] {
  const calendar = status?.calendar || null;
  const invited = Boolean(calendar && calendar.warnings.length === 0);
  const emails = status?.emails || null;
  const finished = status?.state === 'done';
  const sessions = `${sessionCount} session date${sessionCount === 1 ? '' : 's'}`;
  return [
    {
      key: 'calendar', label: 'Calendar created in Microsoft Teams',
      detail: calendar ? 'One meeting link for this module' : 'Creating the meeting series',
      state: calendar ? 'done' : 'active',
    },
    {
      key: 'invitations', label: 'Dates checked and Teams invitations sent',
      detail: invited ? `${sessions} confirmed by Microsoft` : calendar ? `Checking ${sessions} with Microsoft` : sessions,
      state: invited ? 'done' : calendar ? 'active' : 'pending',
    },
    {
      key: 'emails', label: 'LMS schedule emails sent',
      detail: emails && emails.total
        ? `${emails.accepted} of ${emails.total} submitted${emails.failed ? ` · ${emails.failed} failed` : ''}`
        : invited ? 'Preparing one email per person' : 'After the invitations',
      state: finished && invited ? 'done' : invited ? 'active' : 'pending',
    },
  ];
}
export const UNCERTAIN_MESSAGE = 'The earlier Create did not report back, so Microsoft may already hold this meeting. '
  + "Check the organizer's Outlook calendar. Create again only if the meeting is not there.";

/** A saved calendar in the shape the normal success path reads. */
function savedResult(calendar: NonNullable<TeamsCreateStatus['calendar']>, extraWarning = ''): TeamsMeetingResult {
  return {
    created: true,
    warnings: [...calendar.warnings, extraWarning].filter(Boolean),
    meeting: {
      liveSessionId: calendar.liveSessionId,
      joinUrl: calendar.joinUrl,
      organizerEmail: calendar.organizerEmail,
      settingsApplied: calendar.settingsApplied,
    },
  } as unknown as TeamsMeetingResult;
}

/**
 * One reading of the create status: a final answer, or `null` to keep waiting.
 *
 * `neverArrived` is true once the request has had time to reach the server:
 * a claim is the first thing the server writes, so still none means the
 * request was dropped before it -- nothing was created.
 */
export function readCreateStatus(status: TeamsCreateStatus, neverArrived: boolean): RecoveredCreate | null {
  if (status.state === 'creating') return null;
  if (status.state === 'uncertain') return { kind: 'uncertain', message: UNCERTAIN_MESSAGE };
  if (status.state === 'done') {
    const code = status.claim?.outcomeStatus ?? 0;
    // Both create paths answer a finished, verified calendar with 201 Created.
    // Reading only 200 as success held back the emails of every recovered create.
    if (status.calendar && code >= 200 && code < 300) return { kind: 'created', result: savedResult(status.calendar) };
    // A partial save (verification or links incomplete) is still the saved
    // calendar: finish as usual, with the warning that holds its emails back.
    if (status.calendar) {
      return { kind: 'created', result: savedResult(status.calendar, 'The calendar is saved, but it did not finish verification. Open its review before emailing anyone.') };
    }
    return { kind: 'failed', message: `The earlier Create ended without saving a calendar (HTTP ${code || 'error'}${status.claim?.outcomeCode ? `, ${status.claim.outcomeCode}` : ''}). You can create it again.` };
  }
  return neverArrived ? { kind: 'failed', message: 'The Create request never reached the server, so nothing was created. You can create it again.' } : null;
}

/**
 * Poll the saved status until it answers; never sends a Create of its own.
 *
 * The budget matches the server's claim lease (10 minutes): until then a
 * create still 'creating' is genuinely in progress, and past it the server
 * itself reports the claim as uncertain. Stopping sooner left the author on a
 * "check again" button while the server was about to answer. `onStatus`
 * reports every reading (or a failed one) so the dialog can show progress.
 */
export async function waitForSavedCreate(
  fetchStatus: () => Promise<TeamsCreateStatus>,
  { pollMs = 3000, budgetMs = 600000, arrivalMs = 30000, signal, onStatus }: {
    pollMs?: number; budgetMs?: number; arrivalMs?: number; signal?: AbortSignal;
    onStatus?: (status: TeamsCreateStatus | null) => void;
  } = {},
): Promise<RecoveredCreate | null> {
  const started = Date.now();
  while (!signal?.aborted) {
    try {
      const status = await fetchStatus();
      if (signal?.aborted) return null;
      onStatus?.(status);
      const answer = readCreateStatus(status, Date.now() - started >= arrivalMs);
      if (answer) return answer;
    } catch {
      // A status read that fails says nothing about the create; keep asking.
      onStatus?.(null);
    }
    if (Date.now() - started >= budgetMs) return { kind: 'stalled', message: STALLED_MESSAGE };
    await new Promise(resolve => window.setTimeout(resolve, pollMs));
  }
  return null;
}
