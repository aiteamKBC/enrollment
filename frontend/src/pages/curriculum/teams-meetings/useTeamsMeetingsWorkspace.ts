import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { isTeamsReviewCancelled } from './calendarReview';
import { finishTeamsCreation, finishTeamsUpdate } from './creationResult';
import { newResendKey, submitResendEmails } from './scheduleEmail';
import { UNCERTAIN_MESSAGE, waitForSavedCreate, type CreateProgress, type CreateRecovery } from './createRecovery';
import { type UpdateProgress } from './updateProgress';
import { syncTeamsCalendarState } from './calendarState';
import { compareTeamsAttendees, type AttendeeComparison } from './attendeeComparison';
import { type CalendarActionTarget } from './CalendarActionDialog';
import { calendarAction, type ActionResult, type ActionReview } from './calendarActions';
import { leftoverSlotLabel } from './leftoverSlots';
import { normalizedClock } from './calendarTime';
import { calendarOwnershipKnown, externalCalendarFor, EXTERNAL_CALENDAR_MESSAGE } from './externalCalendar';
import { showCurriculumAlert, showCurriculumConfirm } from '@/components/feature/CurriculumSweetAlert';
import { useCurriculumEntities } from '@/hooks/useCurriculumEntities';
import {
  fetchCurriculumSessions,
  fetchCurriculumTeamsMeetingSummaries,
  type CurriculumGroup,
  type CurriculumModule,
  type CurriculumSession,
  type CurriculumTeamsMeetingSummary,
} from '@/lib/curriculumApi';
import {
  ApiError,
  CurriculumRequestTimeout,
  createTeamsMeeting,
  fetchTeamsCreateStatus,
  fetchTeamsCreateDraft,
  saveTeamsCreateDraft,
  fetchModuleMeetingInvitees,
  fetchModuleSessionPlan,
  getCalendarTimeZone,
  loadTeamsMeetingArtifacts,
  loadTeamsMeetingConfiguration,
  loadModuleStructure,
  parseUtcInstant,
  probeModuleTeamsAttachment,
  restoreModuleTeamsMeeting,
  setLiveSessionMeetingScope,
  syncTeamsMeetingArtifacts,
  updateTeamsMeetingSchedule,
  zonedNaiveToUtcIso,
  type TeamsMeetingArtifact,
  type TeamsMeetingArtifactsResult,
  type TeamsMeetingInput,
  type TeamsCreateStatus,
  type TeamsCreateDraft,
  type TeamsLeftoverSlot,
  type TeamsMeetingResult,
  type ModuleWeekSessionPlan,
  type ModuleCatalogueItem,
  type ModuleComponent,
} from '../module-builder/moduleAuthoringData';
import { emailList } from "../module-builder/EmailChipsInput.helpers";
import {
  liveSessionMeetingScope,
  liveSessionPlan,
  meetingScopeIsOpen,
  moduleHasBookedSeries,
  type LiveSessionMeetingScope,
} from '../shared/entities/liveSessionMeetingScope';
import {
  cleanText,
  moduleIdentity,
  normaliseKey,
  resolveModuleContext,
} from '../shared/entities/model';
import { type GroupDeliveryPattern, type TeamsCalendarForm } from './createCalendarForm';
import { buildTeamsCalendarInput, DEFAULT_DURATION_MINUTES, emptyTeamsCalendarForm, minuteKey, minutesBetween, naiveLocalFromUtc, sessionNaiveLocal, teamsCalendarOccurrences, teamsUpdateChanges } from "./createCalendarForm.helpers";
import { useDrawerState } from '../shared/entities/useDrawerState';


// The Teams Meetings workspace: the reads, the writes and the dialog that
// the Teams Meetings page and a module's own screen share.

const AUTO_SYNC_RETRY_MS = 5 * 60 * 1000;
// How often the create dialog asks the server how far a Create has got, and
// how long a Create usually takes before the dialog says it is running long.
const CREATE_PROGRESS_POLL_MS = 3000;
const CREATE_USUAL_MS = 60 * 1000;
const AUTO_SYNC_RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;

export type CalendarState = 'in-sync' | 'out-of-sync' | 'not-created' | 'no-sessions' | 'unverified';

export const STATE_LABELS: Record<CalendarState, string> = {
  'in-sync': 'In sync',
  'out-of-sync': 'Dates differ',
  'not-created': 'Not created',
  'no-sessions': 'No sessions',
  // Never shown as agreement: the backend could not compare the calendar
  // against the module's plan (a read failed, or nothing is tracked to
  // compare against yet), so the state says that plainly rather than guess.
  unverified: 'Unable to verify',
};

export const STATE_TONES: Record<CalendarState, string> = {
  'in-sync': 'border-emerald-200 bg-emerald-50 text-emerald-700',
  'out-of-sync': 'border-amber-200 bg-amber-50 text-amber-800',
  'not-created': 'border-background-200 bg-background-100 text-foreground-500',
  'no-sessions': 'border-background-200 bg-background-100 text-foreground-500',
  unverified: 'border-background-200 bg-background-100 text-foreground-500',
};

export interface MeetingRow {
  module: CurriculumModule;
  catalogueId: string;
  name: string;
  programmeName: string;
  cohortName: string;
  groupName: string;
  summary?: CurriculumTeamsMeetingSummary;
  sessions: CurriculumSession[];
  /** The module's own session dates, as the UTC instants Teams would hold. */
  plannedStarts: string[];
  /** What Teams holds today, in order, when it has been asked for. */
  teamsStarts: string[];
  durationMinutes: number;
  /** The weekly slot this module's group runs to — where a session's length comes from. */
  groupPattern?: GroupDeliveryPattern;
  state: CalendarState;
  differingSessions: number;
}

/**
 * The weekly slot a group runs to, ready to be shown.
 *
 * The group is where a session's length actually comes from — "Friday, 12:00 PM
 * to 2:00 PM" is a two-hour session — so the create dialog states it rather than
 * leaving a list of mixed lengths unexplained. Times are normalised rather than
 * trusted: rows written before the group schedule was split into its own columns
 * kept a 12-hour clock. One that cannot be read comes back empty and is reported
 * as missing, never as midnight.
 */
function groupDeliveryPattern(group: CurriculumGroup | undefined, name: string): GroupDeliveryPattern | undefined {
  if (!group) return undefined;
  const readClock = (value: unknown) => {
    try { return normalizedClock(value); } catch { return ''; }
  };
  const startTime = readClock(group.startTime);
  const endTime = readClock(group.endTime);
  const days = cleanText(group.weekDays);
  if (!days && !startTime && !endTime) return undefined;
  return {
    name: cleanText(group.name) || name || 'This group',
    days,
    startTime,
    endTime,
    durationMinutes: Math.max(0, minutesBetween(startTime, endTime)),
  };
}

/**
 * The module's live-session components, in the order their weeks run.
 *
 * These name the dates in `liveSessionPlan`, and `namedSessions` pairs the two
 * BY POSITION — so a live session that plan drops (one delivered by its own
 * additional meeting) has to be dropped here too, or every session after it
 * would be labelled with the previous one's title.
 */
/** Every live-session component of a module, in the order its weeks run. */
function allLiveSessionComponents(module: ModuleCatalogueItem | null): ModuleComponent[] {
  return (module?.weekStructure || []).flatMap(week => (week.components || []).filter(
    component => component.type === 'live-session',
  ));
}

/**
 * Drop the sessions delivered by their own additional meeting.
 *
 * `liveSessionPlan` already excludes them from the planned list, but a module
 * with NO calendar yet never builds that list -- its plan effect returns early
 * when there is no summary -- so the create form reads the module's stored
 * sessions straight through. This is where those are filtered, which is what
 * keeps a week's private meeting off the module calendar's preview AND out of
 * what Create sends.
 *
 * Matched on the session's own `componentId`, and by position against the live
 * sessions in week order when a row does not carry one -- the same pairing
 * `namedSessions` uses, so the two can never disagree about which row is which.
 */
function withoutAdditionalMeetings(
  sessions: CurriculumSession[], components: ModuleComponent[], excluded: Set<string>,
): CurriculumSession[] {
  if (!excluded.size) return sessions;
  return sessions.filter((session, index) => {
    const componentId = cleanText(session.componentId) || cleanText(components[index]?.id);
    return !componentId || !excluded.has(componentId);
  });
}

function liveSessionComponents(module: ModuleCatalogueItem | null): ModuleComponent[] {
  const every = allLiveSessionComponents(module);
  const hasSeries = moduleHasBookedSeries(every);
  return every.filter(component => liveSessionMeetingScope(component.settings, hasSeries) === 'main');
}

/**
 * Every live session of a module with the calendar that delivers it.
 *
 * What the Teams dialog lists under "Which calendar runs each week", so a week
 * added after the module calendar exists is put to the author rather than
 * quietly booked on either one.
 */
export interface LiveSessionScopeRow {
  componentId: string;
  weekNumber: number;
  title: string;
  date: string;
  scope: LiveSessionMeetingScope;
  /** False once either calendar has booked it: a booking is changed by cancelling it, never here. */
  open: boolean;
}

function liveSessionScopeRows(module: ModuleCatalogueItem | null): LiveSessionScopeRow[] {
  const hasSeries = moduleHasBookedSeries(allLiveSessionComponents(module));
  return (module?.weekStructure || []).flatMap(week => (week.components || [])
    .filter(component => component.type === 'live-session' && component.id)
    .map(component => ({
      componentId: String(component.id),
      weekNumber: Number(week.weekNumber) || 0,
      title: cleanText(component.title) || cleanText(week.title) || `Week ${week.weekNumber}`,
      // The component's own date and nothing else. The backend has already
      // stamped the week's date onto its PRIMARY live session, so that one
      // reads it here; falling back to `week.sessionDate` only ever answered
      // for an ADDITIONAL session, which would then show the primary's date
      // and claim a day nobody gave it. Undated is the truth for that one.
      date: cleanText(component.settings?.sessionDate).slice(0, 10),
      scope: liveSessionMeetingScope(component.settings, hasSeries),
      open: meetingScopeIsOpen(component.settings),
    })));
}

/**
 * Each date carries the name of the live session that runs on it.
 *
 * A date and a number say when a session runs; the component's own title says
 * which session it is. Matched on the component the session already names, and
 * otherwise by position — the same order the calendar itself was built in. A
 * module whose weeks have not been read keeps its rows exactly as they were.
 */
function namedSessions(sessions: CurriculumSession[], components: ModuleComponent[]): CurriculumSession[] {
  if (!components.length) return sessions;
  return sessions.map((session, index) => {
    const match = components.find(component => component.id && component.id === session.componentId) || components[index];
    const componentTitle = cleanText(match?.title);
    return componentTitle ? { ...session, componentTitle, componentId: session.componentId || match?.id } : session;
  });
}

function sessionRowsFromPlan(row: MeetingRow, plan: ModuleWeekSessionPlan, module?: ModuleCatalogueItem | null): CurriculumSession[] {
  return liveSessionPlan(module || null, plan).map((planned, index) => {
    const previous = row.sessions[index] || row.sessions[row.sessions.length - 1];
    return {
      ...(previous || {}),
      id: previous?.id || `${row.catalogueId}-${planned.sessionNumber || index + 1}`,
      moduleCatalogueId: row.catalogueId,
      moduleId: row.catalogueId,
      title: previous?.title || row.name,
      type: previous?.type || 'live-session',
      date: planned.date,
      day: planned.day || previous?.day || '',
      startTime: planned.startTime || previous?.startTime || '',
      endTime: planned.endTime || previous?.endTime || '',
      skippedHolidays: planned.skippedHolidays || [],
      status: previous?.status || 'scheduled',
      ksbCodes: previous?.ksbCodes || [],
    } as CurriculumSession;
  });
}

/**
 * Where one meeting sits against the clock.
 *
 * A join link is only worth offering while the meeting can still be joined, and
 * a row for a meeting that already happened should say so rather than invite a
 * click that lands in an empty lobby. The window opens a quarter of an hour
 * early because people join before the hour, and closes when the session's own
 * end time passes.
 */
export type MeetingRunState = 'unknown' | 'upcoming' | 'live' | 'ended';

export function meetingRunState(startIso: string, durationMinutes: number, now: number): MeetingRunState {
  const start = parseUtcInstant(startIso).getTime();
  if (Number.isNaN(start)) return 'unknown';
  const end = start + Math.max(15, durationMinutes) * 60000;
  if (now >= end) return 'ended';
  if (now >= start - 15 * 60000) return 'live';
  return 'upcoming';
}

function endedOccurrenceCount(row: MeetingRow, now: number): number {
  return row.teamsStarts.reduce((count, startIso, index) => {
    const session = row.sessions[index];
    const duration = Math.max(
      15,
      minutesBetween(session?.startTime || '', session?.endTime || '') || row.durationMinutes,
    );
    return count + (meetingRunState(startIso, duration, now) === 'ended' ? 1 : 0);
  }, 0);
}

function mostRecentOccurrenceEnd(row: MeetingRow): number {
  return row.teamsStarts.reduce((latest, startIso, index) => {
    const start = parseUtcInstant(startIso).getTime();
    if (Number.isNaN(start)) return latest;
    const session = row.sessions[index];
    const duration = Math.max(
      15,
      minutesBetween(session?.startTime || '', session?.endTime || '') || row.durationMinutes,
    );
    return Math.max(latest, start + duration * 60000);
  }, 0);
}

/**
 * A saved email list, whichever way it arrived: a list, JSON text of one (raw
 * SQL JSON columns may come back serialized), or plain text. `fallback` is used
 * when the first holds nobody -- the summary the dialog displays.
 */
export function savedEmails(value: unknown, fallback?: unknown): string[] {
  const decode = (raw: unknown): string[] => {
    let decoded = raw;
    if (typeof raw === 'string') {
      try { decoded = JSON.parse(raw); } catch { return emailList(raw); }
    }
    return Array.isArray(decoded) ? decoded.map(item => String(item ?? '').trim()).filter(Boolean) : [];
  };
  const first = decode(value);
  return first.length ? first : decode(fallback);
}

/** What "Edit meeting settings" edits: how the meeting runs and who runs it. */
export interface MeetingSettingsForm {
  recording: string;
  lobbyBypass: string;
  spokenLanguage: string;
  presenters: string;
  coOrganizers: string;
}

export function emptyMeetingSettingsForm(): MeetingSettingsForm {
  return { recording: 'none', lobbyBypass: 'invited', spokenLanguage: 'en-GB', presenters: '', coOrganizers: '' };
}

export interface TeamsMeetingsWorkspaceOptions {
  /** The module whose dialog opens first (the page's `?module=`). */
  initialSelectedId?: string;
  /**
   * Limit the workspace to one module. A module's own screen opens exactly the
   * dialog the Teams Meetings page shows, for that module alone.
   */
  scopeModuleId?: string;
  /**
   * Why writes are refused right now, if they are -- a module screen holding
   * unsaved work. Reading, syncing and viewing stay available.
   */
  blockedReason?: string;
  /** Called after any write to a module's calendar, so its screen can re-read it. */
  onCalendarChanged?: (catalogueId: string) => void;
}

/** A single module's summary read, or every module's. */
function summaryScope(scopeModuleId: string) {
  return scopeModuleId ? { moduleCatalogueIds: [scopeModuleId] } : {};
}

/**
 * Everything the Teams Meetings dialog knows and does, for every module or one.
 *
 * The Teams Meetings page and a module's own screen are two doors onto the same
 * calendar, so they share this one implementation -- the same reads, the same
 * writes, the same dialog -- rather than each keeping a copy that drifts.
 */
export function useTeamsMeetingsWorkspace(options: TeamsMeetingsWorkspaceOptions = {}) {
  const scopeModuleId = cleanText(options.scopeModuleId);
  const blockedReason = cleanText(options.blockedReason);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const notifyChanged = useCallback((catalogueId: string) => {
    optionsRef.current.onCalendarChanged?.(catalogueId);
  }, []);

  const { programmes, cohorts, groups, modules, holidays, loading, loaded, refreshing, error, reload } = useCurriculumEntities({ includeHolidays: true, includeStaff: true });
  const reloadEntitiesRef = useRef(reload);
  reloadEntitiesRef.current = reload;

  const [summaries, setSummaries] = useState<CurriculumTeamsMeetingSummary[]>([]);
  const [sessions, setSessions] = useState<CurriculumSession[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(true);
  // Whether the Teams state has ever landed. Every action on this page re-reads
  // it, and a re-read is not a first load: without this the table swapped itself
  // for six skeleton blocks every time somebody pressed Update Teams calendar,
  // so the row they had just acted on vanished and came back seconds later. A
  // refresh belongs over the rows already on screen, not in place of them.
  const [teamsLoaded, setTeamsLoaded] = useState(false);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  // Three states, not two. 'unconfigured' is the backend ANSWERING that the
  // five Graph environment variables are not all set -- the one fact that makes
  // every action here certain to fail. 'unknown' is the check itself not
  // completing: a 401, a 500, a timeout. Those two used to share one `false`,
  // so a request that never arrived locked the author out of creating,
  // updating and booking additional meetings while the credentials were fine.
  // Only a definitive 'unconfigured' disables anything now; an unknown check
  // says so and lets the server answer, which it does properly (503
  // "Microsoft Graph credentials are not configured").
  const [graphStatus, setGraphStatus] = useState<'checking' | 'configured' | 'unconfigured' | 'unknown'>('checking');
  const graphConfigured = graphStatus !== 'unconfigured';
  const [defaultOrganizer, setDefaultOrganizer] = useState('');
  const [timeZoneLabel, setTimeZoneLabel] = useState('');

  const [selectedId, setSelectedId] = useState(options.initialSelectedId || '');
  const [calendarActionTarget, setCalendarActionTarget] = useState<CalendarActionTarget | null>(null);

  const [busy, setBusy] = useState('');
  // `moduleId` is the module the message is about, when it is about one in
  // particular. Background checks run across every calendar on the page, so a
  // message with no owner cannot be told apart from one about the module the
  // reader has open.
  const [notice, setNotice] = useState<{
    tone: 'info' | 'warning' | 'error'; text: string; moduleId?: string;
    /** Cancelled in Microsoft but not here: the sessions (or whole series) the author may record a cancellation for. */
    recordCancel?: { series: boolean; sessions: number[] };
  } | null>(null);
  const [detail, setDetail] = useState<TeamsMeetingArtifactsResult | null>(null);
  const [plannedSessions, setPlannedSessions] = useState<Record<string, CurriculumSession[]>>({});
  // The selected module's live-session components, so each date can be shown
  // under the name of the session that runs on it.
  const [liveComponents, setLiveComponents] = useState<Record<string, ModuleComponent[]>>({});
  // Per module: every live session in week order, and the ids of the ones
  // delivered by their own additional meeting. Kept beside `liveComponents`
  // (which is already filtered) because excluding a session by position needs
  // the unfiltered order to count against.
  const [additionalMeetings, setAdditionalMeetings] = useState<Record<string, { all: ModuleComponent[]; excluded: string[] }>>({});
  // Per module: which calendar runs each live session, for the dialog to ask about.
  const [scopeRows, setScopeRows] = useState<Record<string, LiveSessionScopeRow[]>>({});
  const [linkReadStatus, setLinkReadStatus] = useState<Record<string, 'loading' | 'ready' | 'error'>>({});
  // Bumped after a choice is saved, so the structure is read again.
  const [scopeEpoch, setScopeEpoch] = useState(0);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRead, setDetailRead] = useState<{ liveSessionId: string; status: 'loading' | 'ready' | 'error' } | null>(null);
  const detailRequestId = useRef(0);
  const [artifactSyncing, setArtifactSyncing] = useState<Set<string>>(() => new Set());
  const [autoSyncEnabled, setAutoSyncEnabled] = useState(() => {
    if (typeof window === 'undefined') return true;
    return window.localStorage.getItem('curriculumTeamsAutoSync') !== 'off';
  });
  const artifactSyncInFlight = useRef<Set<string>>(new Set());
  const artifactSyncAttempts = useRef<Map<string, number>>(new Map());
  const calendarSyncInFlight = useRef<Set<string>>(new Set());
  const calendarSyncAttempts = useRef<Map<string, number>>(new Map());
  const calendarSyncStatuses = useRef<Map<string, string>>(new Map());
  const [calendarSyncing, setCalendarSyncing] = useState<Set<string>>(() => new Set());
  const [resultsModule, setResultsModule] = useState('');
  const [preview, setPreview] = useState<{ liveSessionId: string; artifact: TeamsMeetingArtifact; title: string } | null>(null);
  /**
   * Weeks of the selected module still waiting for a live-session component,
   * per the server's dry run of the re-attach walk. `null` while unknown, which
   * keeps the button hidden rather than flickering it in and straight back out.
   */
  const [pendingComponents, setPendingComponents] = useState<number | null>(null);
  const [transcriptPreview, setTranscriptPreview] = useState<{ liveSessionId: string; artifact: TeamsMeetingArtifact; title: string } | null>(null);
  // Whether a session is joinable or over is a fact about right now, so the
  // page keeps its own minute hand rather than freezing at first render.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    window.localStorage.setItem('curriculumTeamsAutoSync', autoSyncEnabled ? 'on' : 'off');
  }, [autoSyncEnabled]);

  const settingsDrawer = useDrawerState<MeetingSettingsForm>(emptyMeetingSettingsForm());
  // What the settings drawer opened on, so a presenter or co-organizer added
  // there is recognised as someone new rather than as a changed setting.
  const settingsBaseline = useRef<MeetingSettingsForm | null>(null);
  // The shared form's own empty state, rather than a second copy of its fields
  // that has to be kept in step by hand.
  const createDrawer = useDrawerState<TeamsCalendarForm>(emptyTeamsCalendarForm());
  // The Teams slot whose explicit Cancel is under way, by Microsoft event id.
  const [leftoverCancelling, setLeftoverCancelling] = useState('');
  // The module's saved create form (Save draft), and whether one is being saved.
  // A draft is only the form's values: nothing in Teams, nobody emailed.
  const [createDraft, setCreateDraft] = useState<(TeamsCreateDraft & { catalogueId: string }) | null>(null);
  const [createDraftSaving, setCreateDraftSaving] = useState(false);
  const createDraftFor = useRef('');
  // Read by the draft's late-arriving load, which must not overwrite typing.
  const createDirty = useRef(false);
  createDirty.current = createDrawer.dirty;
  // The same form for an existing calendar, and what it held when opened.
  const updateDrawer = useDrawerState<TeamsCalendarForm>(emptyTeamsCalendarForm());
  const updateBaseline = useRef<TeamsCalendarForm | null>(null);
  // The calendar the invitation fields were filled from.
  const invitationsFor = useRef('');
  // The last reading of Microsoft's attendee list for the open calendar, and
  // whether one is in flight. Kept beside the drawer rather than in it: the
  // drawer's form is what the author is editing, and a comparison must never
  // be mistaken for -- or write into -- that.
  const [comparison, setComparison] = useState<AttendeeComparison | null>(null);
  const [comparing, setComparing] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const comparisonFor = useRef('');
  const [drawerTarget, setDrawerTarget] = useState<MeetingRow | null>(null);
  const [invitedPrefilling, setInvitedPrefilling] = useState(false);
  // What the last prefill did, said beside its own button: until now a
  // request that failed and one that found nobody both looked like nothing.
  const [prefillNotice, setPrefillNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  // Guards a fetchModuleMeetingInvitees() response against landing after the
  // caller has since opened a different module's drawer (or closed it).
  const inviteesRequestId = useRef(0);
  // How a Create the browser stopped waiting for ended; null when none is pending.
  const [createRecovery, setCreateRecovery] = useState<CreateRecovery | null>(null);
  const recoveryAbort = useRef<AbortController | null>(null);
  // A Create under way, as the server reports it; null when none is running.
  const [createProgress, setCreateProgress] = useState<CreateProgress | null>(null);
  const progressWatch = useRef<{ controller: AbortController; slowTimer: number } | null>(null);
  // An existing-calendar update has no polling endpoint, so this follows the
  // confirmed request and its notification phase locally.
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);

  // ------------------------------------------------------------------ loads

  const loadTeamsState = useCallback(async (signal?: AbortSignal) => {
    setTeamsLoading(true);
    try {
      const [nextSummaries, nextSessions] = await Promise.all([
        // `revalidate`, not `skipCache`: read past this tab's cache, but let the
        // server answer from its own. The summaries with `occurrence_dates` are
        // the most expensive read this dialog makes -- forcing a rebuild of them
        // alongside the sessions, the overview, the module structure and the
        // Graph configuration, all the moment the dialog opens and all on top of
        // the four-second epoch poll, took this one past fifty seconds to a 503
        // while every request beside it hit its client timeout and cancelled.
        // The dialog then sat on "Loading this module's calendar and session
        // dates" until the author gave up, and only the second attempt was quick
        // because the first had warmed the caches. A write still invalidates the
        // server's copy through the shared epoch, so this stays current.
        fetchCurriculumTeamsMeetingSummaries(signal, { ...summaryScope(scopeModuleId), occurrenceDates: true, revalidate: true }),
        // Create and sync must use the latest module dates, just like the summaries.
        // Scoped to one module (the Module Builder's door), only that module's
        // sessions are read, fresh from its rows -- not a forced rebuild of the
        // whole curriculum's, which held this dialog on "loading" for seconds.
        // This one keeps `skipCache`: a calendar built from a stale session list
        // is a calendar missing sessions, which is what the sixteen-versus-three
        // regression test in teamsMeetingsPage.test.tsx exists to prevent.
        fetchCurriculumSessions(signal, { skipCache: true, moduleCatalogueId: scopeModuleId || undefined }),
      ]);
      if (signal?.aborted) return;
      setSummaries(nextSummaries);
      setSessions(nextSessions);
      setTeamsError(null);
      setTeamsLoaded(true);
    } catch (err) {
      if (signal?.aborted) return;
      setTeamsError(err instanceof Error ? err.message : 'Unable to load the tracked Teams meetings.');
    } finally {
      if (!signal?.aborted) setTeamsLoading(false);
    }
  }, [scopeModuleId]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      // A group edit can happen in the Group drawer while this module builder
      // stays mounted. Re-read the overview so its delivery day, clock and coach
      // are the values the Teams planner will use, rather than the snapshot that
      // opened the page. `revalidate`, not `skipCache`: a write already
      // invalidates the server's copy through the shared epoch, and forcing a
      // whole-curriculum rebuild (plus tutors, coaches and holidays) here held
      // the dialog on "Loading this module's calendar" for its full duration.
      // Neither read depends on the other, so they run side by side; the dialog
      // still waits for both (`loaded` and `teamsLoaded`) before it shows a row.
      await Promise.all([
        scopeModuleId ? reloadEntitiesRef.current({ silent: true, skipCache: false, revalidate: true }) : null,
        loadTeamsState(controller.signal),
      ]);
    })();
    return () => controller.abort();
  }, [loadTeamsState, scopeModuleId]);

  // The organizer default and the calendar's timezone both come from the
  // backend's Graph configuration — never from this browser's own zone.
  //
  // `alive` lets a retry started by the banner be abandoned the same way the
  // first attempt is when the page unmounts.
  const graphCheckAlive = useRef(true);
  useEffect(() => () => { graphCheckAlive.current = false; }, []);
  const checkGraphConfiguration = useCallback(() => {
    setGraphStatus('checking');
    return loadTeamsMeetingConfiguration()
      .then(configuration => {
        if (!graphCheckAlive.current) return;
        setGraphStatus(configuration.configured ? 'configured' : 'unconfigured');
        setDefaultOrganizer(configuration.defaultOrganizer || '');
        setTimeZoneLabel(configuration.timeZone || configuration.timeZoneIana || '');
      })
      // The check did not complete. That says nothing about the credentials, so
      // nothing is disabled on the strength of it.
      .catch(() => { if (graphCheckAlive.current) setGraphStatus('unknown'); });
  }, []);
  useEffect(() => { void checkGraphConfiguration(); }, [checkGraphConfiguration]);

  // -------------------------------------------------------------- row model

  /**
   * The holiday that closed one skipped date.
   *
   * A session's `skippedHolidays` are bare ISO dates, and a date alone does not
   * explain itself. The dates only ever come from a cohort's own ticked
   * selection, so any stored holiday covering one is the holiday that moved it.
   */
  const holidayLabelFor = useCallback((date: string) => {
    const day = cleanText(date);
    if (!day) return '';
    const match = holidays.find(holiday => (
      String(holiday.startDate) <= day && day <= String(holiday.endDate || holiday.startDate)
    ));
    return cleanText(match?.label);
  }, [holidays]);

  const summaryByModule = useMemo(() => {
    const map = new Map<string, CurriculumTeamsMeetingSummary>();
    summaries.forEach(summary => map.set(normaliseKey(summary.moduleCatalogueId), summary));
    return map;
  }, [summaries]);

  const sessionsByModule = useMemo(() => {
    const map = new Map<string, CurriculumSession[]>();
    sessions.forEach(session => {
      const key = normaliseKey(session.moduleCatalogueId) || normaliseKey(session.moduleId);
      if (!key) return;
      const list = map.get(key);
      if (list) list.push(session); else map.set(key, [session]);
    });
    map.forEach(list => list.sort((left, right) => (
      `${left.date}T${left.startTime}`.localeCompare(`${right.date}T${right.startTime}`)
    )));
    return map;
  }, [sessions]);

  // One module when a single module's own screen opened this, every module on
  // the Teams Meetings page. The rows are built the same way either way.
  const scopedModules = useMemo(() => (scopeModuleId
    ? modules.filter(module => normaliseKey(moduleIdentity(module)) === normaliseKey(scopeModuleId))
    : modules), [modules, scopeModuleId]);

  const rows = useMemo<MeetingRow[]>(() => {
    return scopedModules.map(module => {
      const catalogueId = moduleIdentity(module);
      const key = normaliseKey(catalogueId);
      const context = resolveModuleContext(module, groups, cohorts, programmes);
      const summary = summaryByModule.get(key);
      const moduleSessions = sessionsByModule.get(key) || [];
      const plannedStarts = moduleSessions.map(session => zonedNaiveToUtcIso(sessionNaiveLocal(session), session.timeZone || summary?.timeZone));
      const teamsStarts = summary?.occurrenceDates || [];
      const firstSession = moduleSessions[0];
      const groupPattern = groupDeliveryPattern(context.group, context.groupName);
      const durationMinutes = Math.max(
        15,
        minutesBetween(firstSession?.startTime || '', firstSession?.endTime || '')
          || summary?.durationMinutes
          // The group's weekly slot before a hardcoded hour: a module with no
          // usable session clock still belongs to a group that states one, and
          // 60 minutes was a guess that read as a fact.
          || groupPattern?.durationMinutes
          || DEFAULT_DURATION_MINUTES,
      );

      // The verdict itself is the backend's: it re-derives the module's plan
      // from the authoring rows (holidays, delivery days, dates) rather than
      // comparing against this page's own — possibly cached — session list, so
      // a plan changed a moment ago is never read as "in sync" against the old
      // one. `syncState` only travels when `occurrenceDates` was asked for,
      // which this page always does, so its absence means the read failed.
      let state: CalendarState = 'not-created';
      let differingSessions = summary?.differingOccurrenceCount || 0;
      if (summary) {
        state = summary.syncState || 'unverified';
      } else if (!moduleSessions.length) {
        state = 'no-sessions';
      }

      return {
        module,
        catalogueId,
        name: cleanText(module.name, 'Untitled module'),
        programmeName: context.programmeName,
        cohortName: context.cohortName,
        groupName: context.groupName,
        summary,
        sessions: moduleSessions,
        plannedStarts,
        teamsStarts,
        durationMinutes,
        groupPattern,
        state,
        differingSessions,
      };
    });
  }, [cohorts, groups, programmes, scopedModules, sessionsByModule, summaryByModule]);

  // A background status check runs for every calendar this page can see, so its
  // verdict has to be able to name the module it belongs to. Unnamed, a warning
  // about one module reads as a verdict on whichever dialog happens to be open.
  const moduleByLiveSession = useMemo(() => {
    const map = new Map<string, { catalogueId: string; name: string }>();
    rows.forEach(row => {
      if (row.summary) map.set(row.summary.liveSessionId, { catalogueId: row.catalogueId, name: row.name });
    });
    return map;
  }, [rows]);

  const selected = useMemo(
    () => rows.find(row => normaliseKey(row.catalogueId) === normaliseKey(selectedId)) || null,
    [rows, selectedId],
  );
  // A recovery belongs to the module it started on: closing the dialog or
  // opening another module stops its polling. The server keeps the outcome,
  // so reopening the module and pressing Create reads it again safely.
  useEffect(() => {
    recoveryAbort.current?.abort();
    setCreateRecovery(null);
    endCreateProgress();
  }, [selectedId]);
  const selectedForDisplay = useMemo(() => {
    if (!selected) return null;
    const key = normaliseKey(selected.catalogueId);
    const components = liveComponents[key] || [];
    const extra = additionalMeetings[key];
    const excluded = new Set(extra?.excluded || []);
    // The STORED branch only. A module with no calendar yet never builds a
    // planned list, so its stored sessions arrive one per live-session
    // component, unfiltered, and this is what keeps a week's private meeting
    // off the module calendar's preview.
    //
    // The planned branch must NOT be filtered again. `liveSessionPlan` has
    // already dropped the weeks another calendar delivers, and it drops them
    // from a list `extra.all` still holds in full -- so the positional pairing
    // inside this filter lines its result up against the wrong components and
    // removes the session AFTER each dropped week as well. That is what hid a
    // week moved onto the module calendar from "Module dates sent to Teams"
    // while Update Teams calendar, which reads `liveSessionPlan` directly,
    // listed it on the review page.
    const drop = (list: CurriculumSession[]) => withoutAdditionalMeetings(list, extra?.all || [], excluded);
    const startsFor = (list: CurriculumSession[]) => list.map(
      session => zonedNaiveToUtcIso(sessionNaiveLocal(session), session.timeZone || selected.summary?.timeZone),
    );
    const sessionsForPlan = plannedSessions[key];
    const sessions = sessionsForPlan || drop(selected.sessions);
    return { ...selected, sessions: namedSessions(sessions, components), plannedStarts: startsFor(sessions) };
  }, [additionalMeetings, liveComponents, plannedSessions, selected]);
  const selectedLiveId = useRef('');
  selectedLiveId.current = selected?.summary?.liveSessionId || '';
  const selectedLinkRead = selected ? linkReadStatus[normaliseKey(selected.catalogueId)] : undefined;
  const externalCalendar = selected
    ? externalCalendarFor(selected.summary, additionalMeetings[normaliseKey(selected.catalogueId)]?.all, detail?.series) : null;
  const ownershipUnknown = Boolean(selected?.summary && !externalCalendar
    && !calendarOwnershipKnown(selected.summary, detail?.series));
  const ownershipReadFinished = detailRead?.liveSessionId === selected?.summary?.liveSessionId
    && detailRead?.status !== 'loading';
  const calendarLinksLoading = Boolean(selected && (!selectedLinkRead || selectedLinkRead === 'loading'
    || (ownershipUnknown && !ownershipReadFinished)));
  const calendarLinksError = selectedLinkRead === 'error'
    ? 'The saved meeting links could not be checked. Retry before creating or changing this module calendar.'
    : ownershipUnknown && ownershipReadFinished
      ? 'The original meeting\'s calendar ownership could not be checked. Retry before changing this module calendar.' : '';
  const needsSavedCalendarPlan = Boolean(selected?.summary || externalCalendar);

  const protectedCalendar = (row: MeetingRow) => externalCalendarFor(
    row.summary, additionalMeetings[normaliseKey(row.catalogueId)]?.all, detail?.series,
  );
  const calendarWriteBlockReason = (row: MeetingRow) => protectedCalendar(row) ? EXTERNAL_CALENDAR_MESSAGE
    : linkReadStatus[normaliseKey(row.catalogueId)] !== 'ready' || !calendarOwnershipKnown(row.summary, detail?.series)
      ? 'The saved meeting links and calendar ownership must be checked before changing this module calendar.' : '';
  // The cancellation-status endpoint requires an LMS-owned organiser event.
  // Read this ref again after await: detail can identify an imported series
  // while an older status request is still in flight.
  const calendarSyncEligibility = useRef(new Map<string, { allowed: boolean; imported: boolean; moduleId: string }>());
  calendarSyncEligibility.current = new Map(rows.flatMap(row => row.summary ? [[row.summary.liveSessionId, {
    allowed: !protectedCalendar(row) && calendarOwnershipKnown(row.summary, detail?.series),
    imported: Boolean(protectedCalendar(row)), moduleId: row.catalogueId,
  }] as const] : []));

  const stats = useMemo(() => {
    const tracked = rows.filter(row => row.summary);
    return {
      tracked: tracked.length,
      inSync: tracked.filter(row => row.state === 'in-sync').length,
      drifted: tracked.filter(row => row.state === 'out-of-sync').length,
      toCreate: rows.filter(row => !row.summary && row.sessions.length).length,
    };
  }, [rows]);

  // ---------------------------------------------------------- detail loader

  /**
   * The tracked occurrence for one session of the plan.
   *
   * By the Teams date the schedule list paired with that row (`teamsUtc`), so a
   * row's Edit time, Cancel session, join link, attendance and recordings are
   * its own meeting's. Read by session number, one session missing from Teams
   * handed every later row the following week's meeting -- Cancel session on
   * 1 Oct cancelled 8 Oct. A row Teams holds nothing for has no occurrence,
   * unless the one on its own date was cancelled, which it then says.
   */
  const detailOccurrenceFor = useCallback((index: number, teamsUtc: string, plannedUtc = '') => {
    const occurrences = detail?.occurrences || [];
    if (occurrences.length && occurrences.every(item => !item.scheduled_start)) return occurrences[index];
    const minute = (value: unknown) => {
      const instant = Date.parse(String(value || ''));
      return Number.isFinite(instant) ? Math.floor(instant / 60000) : NaN;
    };
    const at = (value: string) => occurrences.filter(item => minute(item.scheduled_start) === minute(value));
    const off = (item: { status?: string }) => item.status === 'cancelled' || item.status === 'superseded';
    if (teamsUtc) return at(teamsUtc).find(item => !off(item)) || at(teamsUtc)[0];
    return plannedUtc ? at(plannedUtc).find(off) : undefined;
  }, [detail]);

  const loadDetail = useCallback(async (liveSessionId: string) => {
    const requestId = ++detailRequestId.current;
    if (!liveSessionId) {
      setDetail(null); setDetailError(null); setDetailRead(null); setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    setDetailRead({ liveSessionId, status: 'loading' });
    try {
      const loadedDetail = await loadTeamsMeetingArtifacts(liveSessionId);
      if (requestId !== detailRequestId.current) return;
      setDetail(loadedDetail);
      setDetailRead({ liveSessionId, status: 'ready' });
    } catch (err) {
      if (requestId !== detailRequestId.current) return;
      setDetail(null);
      setDetailError(err instanceof Error ? err.message : 'Unable to load this meeting from Teams.');
      setDetailRead({ liveSessionId, status: 'error' });
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }, []);

  const openCalendarAction = (action: CalendarActionTarget['action'], sessionNumber?: number) => {
    if (blockedReason || !selected?.summary || detailLoading || detail?.series.id !== selected.summary.liveSessionId) return;
    const refusal = calendarWriteBlockReason(selected);
    if (refusal) {
      setNotice({ tone: 'warning', text: refusal, moduleId: selected.catalogueId });
      return;
    }
    const occurrences = sessionNumber === undefined ? detail.occurrences
      : detail.occurrences.filter(row => row.session_number === sessionNumber);
    if (!occurrences.length) return;
    setCalendarActionTarget({ liveId: selected.summary.liveSessionId, moduleId: selected.catalogueId,
      title: `${selected.name} · ${selected.groupName}`, action, scope: sessionNumber === undefined ? 'series' : 'occurrence',
      timeZone: detail.series.timeZoneIana || getCalendarTimeZone(), occurrences });
    setSelectedId('');
  };

  const checkCalendarAction = async () => {
    const liveId = selected?.summary?.liveSessionId;
    if (!liveId || busy) return;
    setBusy(`${liveId}:action-status`);
    try {
      const result = await calendarAction<ActionResult>(liveId, { stage: 'status' });
      setNotice({ tone: result.status === 'done' || result.status === 'none' ? 'info' : 'warning', text: result.message });
      await loadTeamsState();
      await loadDetail(liveId);
      if (selected) notifyChanged(selected.catalogueId);
    } catch (error) { setNotice({ tone: 'error', text: error instanceof Error ? error.message : 'Action status could not be checked.' }); }
    finally { setBusy(''); }
  };

  useEffect(() => {
    const liveSessionId = selected?.summary?.liveSessionId || '';
    void loadDetail(liveSessionId);
  }, [loadDetail, selected?.summary?.liveSessionId, scopeEpoch]);

  // Every module's dates are named after its own live sessions, whether or not
  // its calendar is in sync -- the name is what the row is, not a symptom of
  // drift. Read once per module; `loadModuleStructure` serves its own cache.
  useEffect(() => {
    const catalogueId = selected?.catalogueId;
    if (!catalogueId) return undefined;
    let cancelled = false;
    const key = normaliseKey(catalogueId);
    setLinkReadStatus(previous => ({ ...previous, [key]: 'loading' }));
    void loadModuleStructure(catalogueId, { skipCache: true })
      .then(module => {
        if (cancelled) return;
        if (!module) throw new Error('Module links unavailable');
        setLiveComponents(previous => ({ ...previous, [normaliseKey(catalogueId)]: liveSessionComponents(module) }));
        const everyLiveSession = allLiveSessionComponents(module);
        const hasSeries = moduleHasBookedSeries(everyLiveSession);
        setAdditionalMeetings(previous => ({
          ...previous,
          [normaliseKey(catalogueId)]: {
            all: everyLiveSession,
            excluded: everyLiveSession
              .filter(component => liveSessionMeetingScope(component.settings, hasSeries) !== 'main')
              .map(component => component.id),
          },
        }));
        setScopeRows(previous => ({ ...previous, [normaliseKey(catalogueId)]: liveSessionScopeRows(module) }));
        setLinkReadStatus(previous => ({ ...previous, [key]: 'ready' }));
      })
      .catch(() => {
        if (!cancelled) setLinkReadStatus(previous => ({ ...previous, [key]: 'error' }));
      });
    return () => { cancelled = true; };
  }, [selected?.catalogueId, scopeEpoch]);

  // A booked session row intentionally keeps the historical Teams date. The
  // modal must nevertheless preview the current group plan, because that is
  // what the send action will use after a day/time edit. Do this for every
  // selected calendar, not only rows whose summary verdict is already marked
  // out-of-sync: the summary can be read before the group edit's cache epoch is
  // observed, while the planner endpoint is authoritative right now.
  useEffect(() => {
    if (!selected || !needsSavedCalendarPlan) return undefined;
    let cancelled = false;
    void Promise.all([fetchModuleSessionPlan(selected.catalogueId), loadModuleStructure(selected.catalogueId, { skipCache: true })])
      .then(([plan, module]) => {
        if (cancelled || !plan?.sessions?.length) return;
        setPlannedSessions(previous => ({
          ...previous,
          [normaliseKey(selected.catalogueId)]: sessionRowsFromPlan(selected, plan, module),
        }));
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [selected, scopeEpoch, needsSavedCalendarPlan]);

  const runArtifactSync = useCallback(async (
    liveSessionId: string,
    source: 'manual' | 'automatic',
  ) => {
    if (!liveSessionId || artifactSyncInFlight.current.has(liveSessionId)) return;
    const attemptedAt = artifactSyncAttempts.current.get(liveSessionId) || 0;
    if (source === 'automatic' && Date.now() - attemptedAt < AUTO_SYNC_RETRY_MS) return;

    artifactSyncAttempts.current.set(liveSessionId, Date.now());
    artifactSyncInFlight.current.add(liveSessionId);
    setArtifactSyncing(current => new Set(current).add(liveSessionId));
    if (source === 'manual') setNotice(null);

    try {
      const result = await syncTeamsMeetingArtifacts(liveSessionId);
      if ('state' in result) {
        if (source === 'manual') setNotice({ tone: 'info', text: result.message });
        return;
      }
      const nextSummaries = await fetchCurriculumTeamsMeetingSummaries(undefined, {
        ...summaryScope(scopeModuleId),
        occurrenceDates: true,
        skipCache: true,
      });
      setSummaries(nextSummaries);
      if (selected?.summary?.liveSessionId === liveSessionId) {
        await loadDetail(liveSessionId);
      }

      if (source === 'manual') {
        const counts = result.synced;
        const summary = [
          `${counts.attendanceRecords} attendance record${counts.attendanceRecords === 1 ? '' : 's'}`,
          `${counts.transcripts} transcript${counts.transcripts === 1 ? '' : 's'}`,
          `${counts.recordings} recording${counts.recordings === 1 ? '' : 's'}`,
        ].join(', ');
        setNotice({
          tone: result.partial ? 'warning' : 'info',
          text: result.partial
            ? `Teams sync finished partially: ${summary}. ${result.errors.join(' ')}`
            : `Teams sync complete: ${summary}.`,
        });
      }
    } catch (error) {
      if (source === 'manual') {
        setNotice({
          tone: 'error',
          text: error instanceof Error ? error.message : 'Attendance, transcripts and recordings could not be synced.',
        });
      }
    } finally {
      artifactSyncInFlight.current.delete(liveSessionId);
      setArtifactSyncing(current => {
        const next = new Set(current);
        next.delete(liveSessionId);
        return next;
      });
    }
  }, [loadDetail, scopeModuleId, selected?.summary?.liveSessionId]);

  const runCalendarSync = useCallback(async (liveSessionId: string, source: 'manual' | 'automatic') => {
    const eligibility = calendarSyncEligibility.current.get(liveSessionId);
    if (!eligibility?.allowed) {
      if (source === 'manual') setNotice({ tone: 'warning', moduleId: eligibility?.moduleId,
        text: eligibility?.imported
          ? 'This imported meeting uses its original Teams calendar. Ask the organiser to check dates and cancellations in Teams; checking saved LMS data does not update that calendar.'
          : 'The calendar owner has not been confirmed. Reopen this module and retry loading its existing meeting before checking calendar status.',
      });
      return undefined;
    }
    if (calendarSyncInFlight.current.has(liveSessionId)) return undefined;
    const attemptedAt = calendarSyncAttempts.current.get(liveSessionId);
    if (source === 'automatic' && attemptedAt !== undefined && Date.now() - attemptedAt < AUTO_SYNC_RETRY_MS) {
      return calendarSyncStatuses.current.get(liveSessionId);
    }
    calendarSyncInFlight.current.add(liveSessionId);
    calendarSyncAttempts.current.set(liveSessionId, Date.now());
    setCalendarSyncing(current => new Set(current).add(liveSessionId));
    try {
      const result = await syncTeamsCalendarState(liveSessionId);
      if (!calendarSyncEligibility.current.get(liveSessionId)?.allowed) return undefined;
      calendarSyncStatuses.current.set(liveSessionId, result.seriesStatus);
      if (result.changed || result.seriesStatus === 'cancelled') {
        if (selectedLiveId.current === liveSessionId) {
          if (result.seriesStatus === 'cancelled') {
            setSelectedId('');
            setDetail(null);
          } else await loadDetail(liveSessionId);
        }
        await loadTeamsState();
      } else if (selectedLiveId.current === liveSessionId && result.leftovers) {
        // The check found (or stopped finding) slots outside the plan; they are
        // shown from the calendar's own read, so refresh just that.
        setDetail(current => (current && current.series.id === liveSessionId
          ? { ...current, leftoverSlots: result.leftovers } : current));
      }
      // A verified cancellation is applied to eligible local occurrences. Any
      // historical or otherwise ineligible session remains visible for review.
      const outsideSessions = result.cancelledInMicrosoft || [];
      const outsideSeries = Boolean(result.seriesCancelledInMicrosoft);
      if (source === 'manual' || result.changed || result.errors.length || outsideSeries || outsideSessions.length) {
        const owner = moduleByLiveSession.get(liveSessionId);
        const body = result.errors.length ? result.errors.join(' ')
          : outsideSeries ? 'Microsoft shows this whole calendar as cancelled, but nobody cancelled it in the LMS, so nothing was changed here. If the cancellation should stand, record it with the button below.'
            : outsideSessions.length ? `Microsoft shows session${outsideSessions.length === 1 ? '' : 's'} ${outsideSessions.join(', ')} as cancelled, but nobody cancelled ${outsideSessions.length === 1 ? 'it' : 'them'} in the LMS, so nothing was changed here. If the cancellation should stand, record it with the button below.`
              : result.seriesStatus === 'cancelled' ? 'This calendar is cancelled.'
              : result.leftovers?.length
                ? `Calendar status is up to date. Teams also shows ${result.leftovers.length === 1 ? 'a slot' : `${result.leftovers.length} slots`} that ${result.leftovers.length === 1 ? 'is' : 'are'} not part of this module; nothing was cancelled. Review them in the calendar.`
                : 'Calendar status is up to date.';
        setNotice({
          tone: result.errors.length || outsideSeries || outsideSessions.length ? 'warning' : 'info',
          moduleId: owner?.catalogueId,
          ...(!result.errors.length && (outsideSeries || outsideSessions.length)
            ? { recordCancel: { series: outsideSeries, sessions: outsideSessions } } : {}),
          // Named, because this check reads Microsoft for one calendar and the
          // automatic sweep can land its verdict while a different module is
          // open. "Session 11 could not be matched" says nothing about whose
          // session 11 it means.
          text: owner?.name ? `${owner.name} — ${body}` : body,
        });
      }
      return result.seriesStatus;
    } catch (error) {
      if (!calendarSyncEligibility.current.get(liveSessionId)?.allowed) return undefined;
      const owner = moduleByLiveSession.get(liveSessionId);
      const body = error instanceof Error ? error.message : 'Calendar status could not be checked.';
      calendarSyncStatuses.current.delete(liveSessionId);
      setNotice({
        tone: 'warning',
        moduleId: owner?.catalogueId,
        text: owner?.name ? `${owner.name} — ${body}` : body,
      });
      return undefined;
    } finally {
      calendarSyncInFlight.current.delete(liveSessionId);
      setCalendarSyncing(current => {
        const next = new Set(current);
        next.delete(liveSessionId);
        return next;
      });
    }
  }, [loadDetail, loadTeamsState, moduleByLiveSession]);

  // Calendar checks remain independent of the server's automatic evidence
  // worker, which keeps processing after this page closes. Manual Sync wakes
  // that same durable queue immediately without transferring videos here.
  useEffect(() => {
    if (!autoSyncEnabled || !graphConfigured || teamsLoading) return undefined;
    const candidates = rows.filter(row => row.summary && calendarSyncEligibility.current.get(row.summary.liveSessionId)?.allowed);
    let cancelled = false;
    void (async () => {
      // Keep Graph pressure predictable: one series at a time rather than a
      // burst across every module whose meeting ended on the same minute.
      for (const row of candidates) {
        if (cancelled || !row.summary) return;
        const status = await runCalendarSync(row.summary.liveSessionId, 'automatic');
        if (cancelled) return;
        if (status !== 'active') continue;
        // The background worker owns artifact synchronization.
        // Opening this page does not trigger recording transfers.
      }
    })();
    return () => { cancelled = true; };
  }, [autoSyncEnabled, graphConfigured, now, rows, detail, additionalMeetings, runArtifactSync, runCalendarSync, teamsLoading]);

  // --------------------------------------------------------------- actions

  /** The module's own session dates, in the shape the Teams endpoints take. */
  const scheduledOccurrences = (row: MeetingRow) => teamsCalendarOccurrences(row);

  /**
   * The invitation fields as the calendar holds them now: who is invited, in
   * which role, and how the meeting runs. The loaded series is preferred when
   * it is this one; its list columns may arrive as JSON text, so they are
   * decoded rather than read as empty. The summary the dialog displays is the
   * fallback, so the fields never show less than the dialog above them.
   */
  const invitationForm = (row: MeetingRow): TeamsCalendarForm => {
    const summary = row.summary;
    const series = detail?.series.id === summary?.liveSessionId ? detail?.series : undefined;
    const presenters = savedEmails(series?.presenters, summary?.presenters);
    const coOrganizers = savedEmails(series?.co_organizers, summary?.coOrganizers);
    const elevated = new Set([...presenters, ...coOrganizers].map(email => email.toLowerCase()));
    return {
      ...emptyTeamsCalendarForm(),
      organizerEmail: cleanText(series?.organizer_email || summary?.organizerEmail),
      recording: cleanText(series?.recording, 'none'),
      lobbyBypass: cleanText(series?.lobby_bypass, 'invited'),
      spokenLanguage: cleanText(series?.spoken_language, 'en-GB'),
      presenters: presenters.join('\n'),
      coOrganizers: coOrganizers.join('\n'),
      // Older create requests persisted the complete Graph invitee roster in
      // `attendees`. Keep those legacy rows readable as the LMS's plain
      // attendee list; elevated roles remain visible in their own fields.
      attendees: savedEmails(series?.attendees, summary?.attendees)
        .filter(email => !elevated.has(email.toLowerCase()))
        .join('\n'),
    };
  };

  /** Fill the dialog's invitation fields from the calendar, as their untouched starting point. */
  const seedInvitations = (row: MeetingRow) => {
    if (!row.summary) return;
    const form = invitationForm(row);
    updateBaseline.current = form;
    invitationsFor.current = row.summary.liveSessionId;
    updateDrawer.openWith(form);
    // Another calendar's reading says nothing about this one.
    if (comparisonFor.current !== row.summary.liveSessionId) {
      comparisonFor.current = row.summary.liveSessionId;
      setComparison(null);
      setComparisonError(null);
    }
  };

  /** The fields and what they started from, when they belong to this calendar. */
  const invitationEdits = (row: MeetingRow) => (row.summary && invitationsFor.current === row.summary.liveSessionId
    ? { form: updateDrawer.form, baseline: updateBaseline.current || updateDrawer.form }
    : undefined);

  /** Who the open invitation fields add to this calendar, compared with what it holds. */
  const addedInvitees = (row: MeetingRow) => {
    const update = invitationEdits(row);
    return update ? teamsUpdateChanges(update.form, update.baseline).addedPeople : [];
  };

  /** After a save, what was sent is the new starting point: nothing is left "unsaved". */
  const settleInvitations = (form: TeamsCalendarForm) => {
    updateBaseline.current = form;
    updateDrawer.openWith(form);
  };

  /**
   * The people and settings alone, every date left where Teams holds it.
   *
   * "Save without notifying" (the default) sends nothing to anybody, not even
   * the people it adds. "Save and invite added people" (`invite`) is the same
   * save, after which the people it added -- and only they -- are sent the
   * Teams invitation and their LMS schedule. Neither tells anyone already
   * invited, and neither ever moves a date.
   */
  const saveInvitations = async (row: MeetingRow, { invite = false }: { invite?: boolean } = {}) => {
    const update = invitationEdits(row);
    if (!update) return;
    if (blockedReason) { updateDrawer.setError(blockedReason); return; }
    const refusal = calendarWriteBlockReason(row);
    if (refusal) { updateDrawer.setError(refusal); return; }
    updateDrawer.setSaving(true);
    updateDrawer.setError(null);
    try {
      await savePeople(row, update, { invite });
    } finally {
      updateDrawer.setSaving(false);
      setUpdateProgress(null);
    }
  };

  /**
   * Everyone the LMS has actually published for the open calendar.
   *
   * The drawer's *baseline*, never its form: the form is what the author is
   * editing now, and Microsoft was never asked about anybody added to it since
   * the last save.
   */
  const publishedInvitees = (row: MeetingRow): string[] => {
    const baseline = row.summary && invitationsFor.current === row.summary.liveSessionId ? updateBaseline.current : null;
    if (!baseline) return [];
    return [...new Set([...emailList(baseline.attendees), ...emailList(baseline.presenters),
      ...emailList(baseline.coOrganizers)].map(value => value.trim().toLowerCase()).filter(Boolean))];
  };

  /**
   * "Compare with Teams": read Microsoft's attendee list and show the difference.
   *
   * Deliberately independent of Save. It does not look at `dirty`, does not
   * send the form, and does not touch it afterwards -- an author half-way
   * through adding somebody can press it and still have their edit when the
   * answer arrives. Every press is a fresh read, because the question is what
   * Microsoft holds *now*; the previous answer stays on screen until the new
   * one lands so the panel does not blink empty.
   */
  const compareAttendees = async (row: MeetingRow) => {
    const summary = row.summary;
    if (!summary?.liveSessionId || comparing) return;
    comparisonFor.current = summary.liveSessionId;
    setComparing(true);
    setComparisonError(null);
    try {
      const result = await compareTeamsAttendees(summary.liveSessionId);
      if (comparisonFor.current !== summary.liveSessionId) return;
      setComparison(result);
    } catch (err) {
      if (comparisonFor.current !== summary.liveSessionId) return;
      setComparisonError(err instanceof Error ? err.message : 'Unable to read the current attendee list from Microsoft.');
    } finally {
      setComparing(false);
    }
  };

  /**
   * Send the schedule email again, as a creation email, to every learner the
   * saved calendar invites, the organiser, every co-organiser and every
   * presenter -- not the "was ... now ..." update notice.
   *
   * Its own action, before any review: it never touches the Teams calendar and
   * never asks Microsoft to announce anything. It re-reads the calendar the
   * server has already verified and posts that same full timetable, so a
   * learner who lost the first email, or who was added and removed and added
   * again, holds the dates and the join links as they now stand. Each press is
   * its own ledger round, so it reaches the people the last one reached too.
   */
  const resendSchedule = async (row: MeetingRow) => {
    const summary = row.summary;
    if (blockedReason || !summary?.liveSessionId) return;
    const address = (value: unknown) => String(value || '').trim().toLowerCase();
    // The server emails attendees who do not also run the meeting, plus the
    // organiser, co-organisers and presenters. Count the same people.
    const organisers = new Set([summary.organizerEmail || '', ...(summary.coOrganizers || []), ...(summary.presenters || [])].map(address).filter(Boolean));
    const learners = new Set((summary.attendees || []).map(address).filter(value => value && !organisers.has(value)));
    const count = learners.size;
    const organiserCount = organisers.size;
    setNotice(null);
    await showCurriculumConfirm({
      title: 'Email the full schedule to everyone?',
      text: `${count || 'Every'} invited ${count === 1 ? 'learner' : 'learners'}${organiserCount ? ` and ${organiserCount} ${organiserCount === 1 ? 'organiser or presenter' : 'organisers and presenters'}` : ''} on ${row.name} will be sent the complete timetable and join links again, as a new schedule email rather than a change notice. Anyone who already received it will receive it a second time. The Teams calendar itself is not changed.`,
      icon: 'warning',
      confirmButtonText: 'Send schedule emails',
      onConfirm: async () => {
        setBusy(`${row.catalogueId}:resend`);
        try {
          const email = await submitResendEmails(summary.liveSessionId, newResendKey());
          const unfinished = email.failed + email.uncertain;
          setNotice({
            tone: unfinished ? 'warning' : 'info',
            text: unfinished
              ? `${row.name}: Microsoft accepted ${email.accepted} of ${email.total} schedule emails. ${unfinished} could not be confirmed — press the button again to send a fresh round.`
              : email.total
                ? `${row.name}: the full schedule was submitted to all ${email.accepted} recipient${email.accepted === 1 ? '' : 's'}.`
                : `${row.name}: nobody is invited, so no schedule email was sent.`,
          });
        } finally {
          setBusy('');
        }
      },
      successTitle: 'Schedule emails submitted',
      successText: 'Microsoft has accepted the messages for delivery.',
    });
  };

  const pushDates = async (row: MeetingRow) => {
    const summary = row.summary;
    if (blockedReason || !summary || !row.sessions.length) return;
    const refusal = calendarWriteBlockReason(row);
    if (refusal) {
      setNotice({ tone: 'warning', text: refusal, moduleId: row.catalogueId });
      return;
    }
    setBusy(`${row.catalogueId}:dates`);
    setNotice({
      tone: 'warning',
      text: 'Heads up: if the session dates changed, Microsoft sends one update to everyone invited and the LMS emails them what changed.',
    });
    // Only what the author changed in the invitation fields is sent: an
    // untouched field keeps what the calendar has saved.
    const update = invitationEdits(row);
    const changes = update ? teamsUpdateChanges(update.form, update.baseline) : { fields: {}, addedPeople: [] };
    try {
      // Do not send `row.sessions` here: booked rows intentionally retain the
      // old Teams occurrence dates. The server-side planner is authoritative
      // after a group day/time edit and is fetched again immediately before the
      // write, so the modal cannot send stale dates from a cached row.
      const [plan, module] = await Promise.all([
        fetchModuleSessionPlan(row.catalogueId),
        loadModuleStructure(row.catalogueId, { skipCache: true }),
      ]);
      const planned = liveSessionPlan(module, plan).filter(session => String(session.date || '').trim());
      if (!planned.length) throw new Error('This module has no planned session dates to send.');
      const firstStart = planned[0].startTime || row.groupPattern?.startTime || '09:00';
      const fallbackDuration = Math.max(15, row.groupPattern?.durationMinutes || summary.durationMinutes || DEFAULT_DURATION_MINUTES);
      // Some planner responses number sessions per week, so two different
      // weeks can both arrive as "session 1". The Teams calendar contract
      // requires one unique occurrence number for the whole module. Preserve
      // the planner numbers when they are already unique; otherwise use the
      // ordered module sequence without changing the dates or durations.
      const plannedNumbers = planned.map(session => session.sessionNumber).filter((value): value is number => Number.isInteger(value) && value > 0);
      const plannerNumbersRepeat = new Set(plannedNumbers).size !== plannedNumbers.length || plannedNumbers.length !== planned.length;
      const occurrences = planned.map((session, index) => ({
        sessionNumber: plannerNumbersRepeat ? index + 1 : (session.sessionNumber || index + 1),
        startDateTimeUtc: zonedNaiveToUtcIso(`${session.date}T${session.startTime || firstStart}`, summary.timeZone),
        durationMinutes: session.durationMinutes || Math.max(15, minutesBetween(session.startTime || firstStart, session.endTime || '') || fallbackDuration),
      }));
      // The top-level Update button also carries the invitation fields from
      // the open drawer. If the authored dates and lengths are already what
      // Teams holds, an attendee/setting edit is a people-only save even
      // though this button normally reconciles the calendar dates. Mark it as
      // such so Microsoft is asked for a silent roster patch; the newly added
      // people are forwarded separately after verification.
      const heldKeys = (summary.occurrenceDates || []).map(minuteKey).sort();
      const plannedKeys = occurrences.map(item => minuteKey(item.startDateTimeUtc)).sort();
      const datesUnchanged = heldKeys.length === plannedKeys.length
        && heldKeys.every((value, index) => value && value === plannedKeys[index]);
      const savedDuration = Number(summary.durationMinutes);
      const durationsUnchanged = Number.isFinite(savedDuration)
        && occurrences.every(item => item.durationMinutes === savedDuration);
      const invitationsChanged = Boolean(update && Object.keys(changes.fields).length);
      if (datesUnchanged && durationsUnchanged && !invitationsChanged) {
        await showCurriculumAlert({
          title: 'No changes to save',
          text: 'The Teams calendar is already up to date. No emails were sent.',
          timer: 2400,
        });
        return;
      }
      const peopleOnly = Boolean(invitationsChanged
        && datesUnchanged && durationsUnchanged);
      const result = await updateTeamsMeetingSchedule(summary.liveSessionId, {
        title: row.name,
        organizerEmail: summary.organizerEmail,
        eventId: summary.eventId,
        localStartDateTime: `${planned[0].date}T${planned[0].startTime || firstStart}`,
        startDateTimeUtc: occurrences[0].startDateTimeUtc,
        durationMinutes: occurrences[0].durationMinutes,
        repeat: occurrences.length > 1 ? 'weekly' : 'none',
        repeatOccurrences: occurrences.length,
        scheduledOccurrences: occurrences,
        ...changes.fields,
        ...(peopleOnly ? { peopleOnly: true } : {}),
        // A date change is always announced, by Microsoft and by the LMS
        // together, so nobody's Outlook keeps dates the LMS email has replaced.
        // The server announces only what really moved: a retry of an update
        // that already landed is told to nobody twice.
        ...(!peopleOnly ? { notifyAttendees: true } : {}),
      }, { onSubmitted: () => setUpdateProgress({ stage: 'calendar' }) });
      setUpdateProgress(previous => previous ? { ...previous, stage: 'emails' } : previous);
      if (update) settleInvitations(update.form);
      // Not awaited, the same as the create flow: the table refresh is a round
      // trip and the confirmation must not queue behind it. The rows stay on
      // screen while it runs, so the table catches up under the alert.
      void loadTeamsState();
      // The occurrence rows this drawer reads its join links and attendance
      // from have just been rewritten, so the drawer's own read is stale until
      // it is taken again. This one IS awaited: the drawer is open in front of
      // the reader, and showing it the dates it just replaced would be wrong.
      if (summary.liveSessionId) await loadDetail(summary.liveSessionId);
      // Slots this update left on Teams rather than cancel -- shown at once,
      // without waiting for the next status check to notice them.
      if (summary.liveSessionId && result.leftoverSlots?.length) {
        const liveId = summary.liveSessionId;
        const found = result.leftoverSlots;
        setDetail(current => (current && current.series.id === liveId ? {
          ...current,
          leftoverSlots: [...(current.leftoverSlots || []).filter(item => !found.some(slot => slot.eventId === item.eventId)), ...found],
        } : current));
      }
      notifyChanged(row.catalogueId);
      const warning = result.warnings?.[0]?.message || '';
      if (warning) {
        setNotice({
          tone: 'warning',
          text: result.warnings?.[0]?.code === 'teams_meeting_options_not_applied'
            // Dates and invitations went ahead; only the meeting settings were refused.
            ? `${row.name}: ${warning}`
            : `${row.name}: the session dates are saved here, but Microsoft Teams did not accept every shifted meeting. ${warning}`,
        });
      } else if (result.microsoftUpdate === 'already_attempted') {
        setNotice({
          tone: 'warning',
          text: `${row.name}: an earlier attempt at this same update had already asked Microsoft to tell everyone invited, so it was not sent again. If someone still sees the old dates in Outlook, check the calendar before updating again.`,
        });
      }
      if (result.notifyAttendees || changes.addedPeople.length || result.addedPeople?.length) {
        // There is email to send -- the change email the author ticked, or the
        // schedule for people just added -- so the result stays open to send it
        // and say how it went, instead of timing out.
        await finishTeamsUpdate(result, { title: row.name, scheduledOccurrences: occurrences, scheduleTimeZone: summary.timeZone,
          liveSessionId: summary.liveSessionId, addedPeople: changes.addedPeople });
        return;
      }
      await showCurriculumAlert({
        title: warning ? 'Sent with warnings' : 'Teams calendar updated',
        text: !peopleOnly && result.microsoftUpdate === 'not_needed'
          ? `The Teams calendar for ${row.name} already had these dates. Nobody was emailed.`
          : `${occurrences.length} session date${occurrences.length === 1 ? '' : 's'} sent to the Teams calendar for ${row.name}.`,
        timer: warning ? undefined : 2000,
      });
    } catch (err) {
      if (isTeamsReviewCancelled(err)) return;
      setNotice({ tone: 'error', text: err instanceof Error ? err.message : 'The session dates could not be sent to Teams.' });
    } finally {
      setBusy('');
      setUpdateProgress(null);
    }
  };

  /**
   * Give every week of the module a live-session component holding this meeting.
   *
   * A created series is only really in front of the learner once each week has
   * a session to open, on that week's own date. So this does not just rewrite
   * the components that already exist: a week with none is given one, which is
   * what makes this the one action that finishes the job for a whole module.
   */
  const reattach = async (row: MeetingRow) => {
    if (blockedReason) return;
    const imported = protectedCalendar(row);
    if (!imported && calendarWriteBlockReason(row)) {
      setNotice({ tone: 'warning', text: calendarWriteBlockReason(row), moduleId: row.catalogueId });
      return;
    }
    if (imported && !imported.canReattach) {
      setNotice({ tone: 'warning', text: 'Connect the original calendar before reattaching its saved dates. The pasted links have been kept.', moduleId: row.catalogueId });
      return;
    }
    setBusy(`${row.catalogueId}:reattach`);
    setNotice(null);
    try {
      const result = await restoreModuleTeamsMeeting(row.catalogueId, { createMissingComponents: !imported });
      const created = result.createdComponents || 0;
      const updated = result.updatedComponents || 0;
      const parts = [
        created ? `${created} live-session component${created === 1 ? '' : 's'} created` : '',
        updated ? `${updated} updated` : '',
      ].filter(Boolean);
      await showCurriculumAlert({
        title: imported ? 'Saved Teams data reattached' : created ? 'Live sessions added to the module' : 'Meeting re-attached',
        text: imported
          ? `${updated} live-session component${updated === 1 ? '' : 's'} updated from saved calendar data. Dates without a match remain awaiting confirmation. The original Teams calendar and join link were not changed.`
          : parts.length
          ? `${parts.join(', ')} — each week now opens its own session, on its own date.`
          : 'This module has no weeks to hold a live session yet.',
        timer: 2600,
      });
      await loadTeamsState();
      setScopeEpoch(value => value + 1);
      notifyChanged(row.catalogueId);
    } catch (err) {
      setNotice({ tone: 'error', text: err instanceof Error ? err.message : 'The meeting could not be re-attached.' });
    } finally {
      setBusy('');
    }
  };

  /**
   * Presenters/Attendees derived from the module itself: its assigned tutor as
   * presenter, every learner whose training plan carries this module as
   * attendee. Only ever a starting point — it patches whichever drawer form is
   * open, never sends anything on its own, and a response arriving after the
   * caller has since switched modules or closed the drawer is dropped rather
   * than applied to the wrong one.
   */
  const prefillInvitees = async (
    row: MeetingRow,
    patch: (value: { attendees: string; presenters: string }) => void,
  ) => {
    const requestId = ++inviteesRequestId.current;
    setInvitedPrefilling(true);
    setPrefillNotice(null);
    try {
      const result = await fetchModuleMeetingInvitees(row.catalogueId);
      if (inviteesRequestId.current !== requestId) return;
      patch({
        attendees: result.attendees.join('\n'),
        presenters: result.presenters.join('\n'),
      });
      // A prefill that found nobody used to look exactly like one that failed:
      // the fields stayed as they were and the button said nothing either way.
      setPrefillNotice(result.attendees.length || result.presenters.length
        ? {
          tone: 'ok',
          text: `Filled in ${result.attendees.length} learner${result.attendees.length === 1 ? '' : 's'} who ${result.attendees.length === 1 ? 'has' : 'have'} this module on their training plan`
            + (result.presenters.length
              ? " and the module's tutor as presenter."
              : '. This module has no tutor assigned, so Presenters was cleared.'),
        }
        : {
          tone: 'error',
          text: 'No learner has this module on their training plan yet, and it has no tutor. Assign the module to the learners who should attend, then prefill again.',
        });
    } catch (err) {
      // The form stays usable with addresses typed in by hand -- but the
      // failure is said out loud rather than swallowed.
      if (inviteesRequestId.current !== requestId) return;
      setPrefillNotice({
        tone: 'error',
        text: err instanceof Error ? err.message : 'The learners who have this module on their plan could not be read.',
      });
    } finally {
      if (inviteesRequestId.current === requestId) setInvitedPrefilling(false);
    }
  };

  /**
   * The calendar as it is saved now, sent back unchanged. Invitations and
   * meeting settings change who is on the meeting and how it runs, never when:
   * with the held dates unknown, saving would shorten the series to whatever
   * this form could name, so the callers refuse rather than guess.
   */
  const heldSchedule = (row: MeetingRow) => {
    const summary = row.summary;
    const held = (summary?.occurrenceDates || []).filter(Boolean);
    if (!summary || !held.length) return null;
    const occurrences = held.map((value, index) => ({
      sessionNumber: index + 1,
      startDateTimeUtc: parseUtcInstant(value).toISOString(),
      durationMinutes: summary.durationMinutes || row.durationMinutes,
    }));
    return {
      title: row.name,
      organizerEmail: summary.organizerEmail,
      eventId: summary.eventId,
      localStartDateTime: naiveLocalFromUtc(occurrences[0].startDateTimeUtc),
      startDateTimeUtc: occurrences[0].startDateTimeUtc,
      durationMinutes: summary.durationMinutes || row.durationMinutes,
      repeat: (occurrences.length > 1 ? 'weekly' : 'none') as TeamsMeetingInput['repeat'],
      repeatOccurrences: occurrences.length,
      scheduledOccurrences: occurrences,
      peopleOnly: true,
    };
  };

  /**
   * Recording, lobby, language and who presents or co-organises, as the
   * calendar holds them now. Read from the loaded series when it is this one,
   * so the drawer opens on what Microsoft was last told rather than defaults.
   */
  const openSettings = (row: MeetingRow) => {
    if (blockedReason || !row.summary) return;
    const refusal = calendarWriteBlockReason(row);
    if (refusal) { setNotice({ tone: 'warning', text: refusal, moduleId: row.catalogueId }); return; }
    const series = detail?.series.id === row.summary.liveSessionId ? detail.series : undefined;
    // Closing the main dialog clears its detail. Retain the ownership that was
    // verified for this exact series while its settings drawer stays open.
    setDrawerTarget({ ...row, summary: { ...row.summary, meetingType: row.summary.meetingType || series?.meeting_type } });
    const opened = {
      recording: cleanText(series?.recording, 'none'),
      lobbyBypass: cleanText(series?.lobby_bypass, 'invited'),
      spokenLanguage: cleanText(series?.spoken_language, 'en-GB'),
      presenters: savedEmails(series?.presenters, row.summary.presenters).join('\n'),
      coOrganizers: savedEmails(series?.co_organizers, row.summary.coOrganizers).join('\n'),
    };
    settingsBaseline.current = opened;
    settingsDrawer.openWith(opened);
    setSelectedId('');
  };

  /**
   * Apply the meeting settings to the existing meeting.
   *
   * Through the same update the dates use, in its people-only mode: the held
   * dates go back unchanged, the occurrences are not rewritten, and the backend
   * refuses outright if Microsoft's join link differs from the saved one. So
   * the meeting keeps its identity, its link and every session where it is --
   * only its onlineMeeting options and roles change.
   */
  const saveSettings = async () => {
    const row = drawerTarget;
    const summary = row?.summary;
    if (!row || !summary) return;
    if (blockedReason) { settingsDrawer.setError(blockedReason); return; }
    const refusal = calendarWriteBlockReason(row);
    if (refusal) { settingsDrawer.setError(refusal); return; }
    const held = heldSchedule(row);
    if (!held) {
      settingsDrawer.setError('The meeting dates for this series have not loaded, so saving now could shorten it. Reload and try again.');
      return;
    }
    const form = settingsDrawer.form;
    // Naming a presenter or co-organizer here invites someone the meeting did
    // not have, exactly as the invitations drawer does. They are the only people
    // this save reaches: Microsoft forwards them the meeting and the LMS sends
    // them the schedule, while nobody already invited hears about it.
    const invitedBefore = new Set([
      summary.organizerEmail.trim().toLowerCase(),
      ...(summary.attendees || []).map(value => value.trim().toLowerCase()),
      ...emailList(settingsBaseline.current?.presenters || '').map(value => value.toLowerCase()),
      ...emailList(settingsBaseline.current?.coOrganizers || '').map(value => value.toLowerCase()),
    ]);
    const added = [...new Set([...emailList(form.presenters), ...emailList(form.coOrganizers)]
      .map(value => value.toLowerCase()))].filter(value => value && !invitedBefore.has(value));
    settingsDrawer.setSaving(true);
    settingsDrawer.setError(null);
    try {
      const result = await updateTeamsMeetingSchedule(summary.liveSessionId, {
        ...held,
        settingsOnly: true,
        recording: form.recording,
        lobbyBypass: form.lobbyBypass,
        spokenLanguage: form.spokenLanguage,
        presenters: emailList(form.presenters),
        coOrganizers: emailList(form.coOrganizers),
      }, { onSubmitted: () => setUpdateProgress({ stage: 'calendar' }) });
      setUpdateProgress(previous => previous ? { ...previous, stage: 'emails' } : previous);
      settingsDrawer.close();
      await loadTeamsState();
      if (summary.liveSessionId) await loadDetail(summary.liveSessionId);
      notifyChanged(row.catalogueId);
      await finishTeamsUpdate(result, { title: row.name, scheduledOccurrences: held.scheduledOccurrences,
        scheduleTimeZone: summary.timeZone, liveSessionId: summary.liveSessionId, addedPeople: added });
    } catch (err) {
      if (isTeamsReviewCancelled(err)) return;
      settingsDrawer.setError(err instanceof Error ? err.message : 'The meeting settings could not be saved.');
    } finally {
      settingsDrawer.setSaving(false);
      setUpdateProgress(null);
    }
  };

  /**
   * Save the invitations form: who is invited in every role, and how the
   * meeting runs. The dates sent back are the ones Teams already holds, so no
   * session moves; only the fields the author changed are sent.
   *
   * Without `invite` it only saves. Nobody is emailed -- not by Microsoft, not
   * by the LMS, not even the people it adds -- so it skips the send review and
   * never opens the email result. With `invite` the people it adds, and only
   * they, are forwarded the Teams invitation and sent their LMS schedule.
   *
   * Either way the server refuses, before writing anything, when Teams no
   * longer holds the dates saved here: a people save never repairs dates.
   */
  const savePeople = async (row: MeetingRow, update: { form: TeamsCalendarForm; baseline: TeamsCalendarForm }, { invite = false }: { invite?: boolean } = {}) => {
    const summary = row.summary;
    if (!summary) return;
    const changes = teamsUpdateChanges(update.form, update.baseline);
    if (!Object.keys(changes.fields).length) {
      updateDrawer.setError('Nothing has changed yet. Add or remove someone, or change a setting, then save.');
      return;
    }
    if (!emailList(update.form.attendees).length) {
      updateDrawer.setError('Name at least one attendee.');
      return;
    }
    const held = heldSchedule(row);
    if (!held) {
      updateDrawer.setError('The meeting dates for this series have not loaded, so saving now could shorten it. Reload the page and try again.');
      return;
    }
    try {
      const inviting = invite && changes.addedPeople.length > 0;
      const result = await updateTeamsMeetingSchedule(summary.liveSessionId, { ...held, ...changes.fields, ...(inviting ? {} : { invitationsOnly: true }) }, {
        onSubmitted: () => setUpdateProgress({ stage: 'calendar' }),
      });
      settleInvitations(update.form);
      await loadTeamsState();
      if (summary.liveSessionId) await loadDetail(summary.liveSessionId);
      notifyChanged(row.catalogueId);
      if (inviting) {
        // The people added, and only they, are sent the invitation and their
        // schedule; the result stays open to say how that went.
        await finishTeamsUpdate(result, { title: row.name, scheduledOccurrences: held.scheduledOccurrences,
          scheduleTimeZone: summary.timeZone, liveSessionId: summary.liveSessionId, addedPeople: changes.addedPeople });
        return;
      }
      const warning = (result.warnings || [])[0];
      const added = changes.addedPeople.length;
      await showCurriculumAlert({
        title: warning ? 'Saved with a warning' : 'Saved without notifying',
        text: warning
          ? warning.message
          : `Invitations and meeting settings are saved for ${row.name}. Nobody was emailed${added
            ? `, including the ${added} ${added === 1 ? 'person' : 'people'} you added`
            : ''}. The join link and session dates are unchanged.`,
        timer: warning ? undefined : 3200,
      });
    } catch (err) {
      if (isTeamsReviewCancelled(err)) return;
      updateDrawer.setError(err instanceof Error ? err.message : 'The invitations could not be saved.');
    }
  };

  /**
   * Seed the create form for a module with no calendar. The dialog that shows
   * it is the create form, so this runs when that dialog opens — from a row
   * click or from a `?module=` link — rather than from a button of its own.
   */
  const seedCreateForm = (row: MeetingRow) => {
    setDrawerTarget(row);
    const seed: TeamsCalendarForm = {
      ...emptyTeamsCalendarForm(),
      // The meeting is named after the module itself. The module's stored notes
      // are deliberately *not* offered as the description: they carry the
      // hidden `__key:value` lines the API appends (programme, cohort, group and
      // catalogue ids), and those would end up in the calendar invitation.
      title: cleanText(row.module.name, row.name),
      organizerEmail: defaultOrganizer,
      // Invitees remain empty until the author explicitly asks for the module's
      // tutor and learner-plan prefill below. Opening Create must not silently
      // assign a presenter the author did not choose.
      attendees: '',
      presenters: '',
      coOrganizers: '',
      details: '',
      durationMinutes: '',
    };
    createDrawer.openWith(seed);
    // A saved draft, when the module has one, replaces the blank answers -- but
    // only while the author has not started typing over them.
    createDraftFor.current = row.catalogueId;
    setCreateDraft(null);
    void fetchTeamsCreateDraft(row.catalogueId).then(({ draft }) => {
      if (!draft || createDraftFor.current !== row.catalogueId || createDirty.current) return;
      setCreateDraft({ ...draft, catalogueId: row.catalogueId });
      createDrawer.openWith({ ...seed, ...draftFormValues(draft.form) });
    }).catch(() => undefined);
  };

  /**
   * Cancel one Teams slot that is not a session of this module -- by hand.
   *
   * The one way such a slot leaves Microsoft. Update, Create and the status
   * check only ever report it, because removing it makes Microsoft email
   * everyone invited. Here the author has pressed Cancel and confirmed: the
   * server re-proves the slot is still outside the plan, then sends exactly one
   * cancellation and verifies it.
   */
  const cancelLeftover = async (slot: TeamsLeftoverSlot) => {
    const liveId = selected?.summary?.liveSessionId;
    if (!liveId || leftoverCancelling) return;
    const refusal = selected ? calendarWriteBlockReason(selected) : '';
    if (refusal) { setNotice({ tone: 'warning', text: refusal, moduleId: selected?.catalogueId }); return; }
    const what = leftoverSlotLabel(slot);
    setLeftoverCancelling(slot.eventId);
    try {
      await showCurriculumConfirm({
        title: 'Cancel this Teams slot?',
        text: `${what} is on Teams but is not a session of this module. Cancelling it removes it from Teams, and Microsoft emails everyone invited that it is cancelled. This cannot be undone.`,
        icon: 'warning',
        confirmButtonText: 'Cancel it and email everyone',
        cancelButtonText: 'Keep it',
        onConfirm: async () => {
          const review = await calendarAction<ActionReview>(liveId, { stage: 'review', action: 'cancel', scope: 'leftover', eventId: slot.eventId });
          const result = await calendarAction<ActionResult>(liveId, { stage: 'confirm', reviewToken: review.reviewToken, acknowledgeNotifications: true });
          if (result.status !== 'done') throw new Error(result.message);
          await loadDetail(liveId);
        },
        successTitle: 'Slot cancelled',
        successText: `${what} was cancelled on Teams. Module sessions are unchanged.`,
      });
    } finally {
      setLeftoverCancelling('');
    }
  };

  /**
   * Keep the create form for this module without creating anything.
   *
   * Saved on the server against the module, so the dialog can be closed and
   * finished later, by anyone. Microsoft is not called and nobody is emailed;
   * only Create does that.
   */
  const saveCreateDraft = async (target?: MeetingRow) => {
    const row = target || drawerTarget;
    if (!row || createDraftSaving || createDrawer.saving) return;
    const saved = createDrawer.form;
    // The title is always the module's own name, so it is not kept.
    const { title: _title, ...values } = saved;
    setCreateDraftSaving(true);
    createDrawer.setError(null);
    try {
      const { draft } = await saveTeamsCreateDraft(row.catalogueId, values as Record<string, string>);
      if (draft) setCreateDraft({ ...draft, catalogueId: row.catalogueId });
      createDrawer.markSaved(saved);
    } catch (err) {
      createDrawer.setError(err instanceof Error ? err.message : 'The draft could not be saved.');
    } finally {
      setCreateDraftSaving(false);
    }
  };

  /**
   * Find out how a Create the browser lost track of actually ended.
   *
   * Reads the server's saved status until it answers; it never sends Create.
   * A calendar that was saved comes back as the result the normal success path
   * reads. Anything else is shown in the dialog, with Create kept locked unless
   * the server says the attempt ended without saving anything.
   */
  const recoverCreate = async (row: MeetingRow): Promise<TeamsMeetingResult | null> => {
    recoveryAbort.current?.abort();
    const controller = new AbortController();
    recoveryAbort.current = controller;
    setCreateRecovery({ phase: 'checking' });
    // The same progress panel carries on: the create is still the create, the
    // browser has only stopped waiting for its reply.
    stopProgressPolling();
    setCreateProgress(previous => previous || { status: null, failures: 0, slow: true });
    const answer = await waitForSavedCreate(() => fetchTeamsCreateStatus(row.catalogueId), {
      signal: controller.signal, onStatus: recordCreateStatus,
    });
    if (!answer || controller.signal.aborted) return null;
    if (answer.kind === 'created') { setCreateRecovery(null); return answer.result; }
    // A create that saved nothing hands the form back; stalled and uncertain
    // keep the last known steps on screen beside their message.
    if (answer.kind === 'failed') endCreateProgress();
    setCreateRecovery({ phase: answer.kind, message: answer.message });
    return null;
  };
  useEffect(() => () => { recoveryAbort.current?.abort(); endCreateProgress(); },
    []);

  /** One status reading into the progress panel; `null` is a read that failed. */
  const recordCreateStatus = (status: TeamsCreateStatus | null) => {
    setCreateProgress(previous => (!previous ? previous
      : status ? { ...previous, status, failures: 0 } : { ...previous, failures: previous.failures + 1 }));
  };
  function stopProgressPolling() {
    progressWatch.current?.controller.abort();
  }
  function endCreateProgress() {
    const watch = progressWatch.current;
    if (watch) {
      watch.controller.abort();
      window.clearTimeout(watch.slowTimer);
    }
    progressWatch.current = null;
    setCreateProgress(null);
  }
  /**
   * Show the create's progress from the moment the author confirms the review.
   *
   * Display only: the create's own response (or, if the browser stops waiting,
   * `recoverCreate`) decides the outcome. The status reads are cheap and
   * read-only, so asking every few seconds costs the create nothing.
   */
  const startCreateProgress = (row: MeetingRow) => {
    endCreateProgress();
    const controller = new AbortController();
    const slowTimer = window.setTimeout(() => {
      setCreateProgress(previous => (previous ? { ...previous, slow: true } : previous));
    }, CREATE_USUAL_MS);
    progressWatch.current = { controller, slowTimer };
    setCreateProgress({ status: null, failures: 0, slow: false });
    void (async () => {
      while (!controller.signal.aborted) {
        await new Promise<void>(resolve => {
          const timer = window.setTimeout(resolve, CREATE_PROGRESS_POLL_MS);
          controller.signal.addEventListener('abort', () => { window.clearTimeout(timer); resolve(); }, { once: true });
        });
        if (controller.signal.aborted) return;
        try {
          const status = await fetchTeamsCreateStatus(row.catalogueId);
          if (!controller.signal.aborted) recordCreateStatus(status);
        } catch {
          if (!controller.signal.aborted) recordCreateStatus(null);
        }
      }
    })();
  };

  const createCalendar = async (target?: MeetingRow, options: { confirmUncertain?: boolean; checkOnly?: boolean } = {}) => {
    const row = target || drawerTarget;
    if (!row) return;
    if (blockedReason) { createDrawer.setError(blockedReason); return; }
    if (!options.checkOnly && (protectedCalendar(row) || row.summary)) {
      createDrawer.setError(protectedCalendar(row) ? EXTERNAL_CALENDAR_MESSAGE : 'This module already has a Teams calendar. Open its existing meeting instead of creating another.');
      return;
    }
    if (!options.checkOnly && linkReadStatus[normaliseKey(row.catalogueId)] !== 'ready') {
      createDrawer.setError('Wait until the saved meeting links have been checked. If the check failed, retry it before creating a calendar.');
      return;
    }
    const form = createDrawer.form;
    const organizer = form.organizerEmail.trim();
    if (!organizer) {
      createDrawer.setError('Enter the Microsoft 365 organizer email.');
      return;
    }
    if (!row.sessions.length) { createDrawer.setError('This module has no live-session components yet. Add a Live session to the weeks that should meet.'); return; }
    createDrawer.setSaving(true);
    createDrawer.setError(null);
    try {
      const input = buildTeamsCalendarInput(row, form);
      let result: TeamsMeetingResult | null;
      if (options.checkOnly) {
        result = await recoverCreate(row);
      } else {
        setCreateRecovery(null);
        try {
          const onSubmitted = () => startCreateProgress(row);
          result = await (options.confirmUncertain
            ? createTeamsMeeting(input, { confirmUncertain: true, onSubmitted })
            : createTeamsMeeting(input, { onSubmitted }));
        } catch (err) {
          // A timeout is not a failure: the server may still finish, or already
          // has. Another request already creating this module is the same
          // question. Either way, ask the server -- never send Create again.
          const code = err instanceof ApiError ? String(err.data?.code || '') : '';
          const savedLiveSessionId = err instanceof ApiError ? String(err.data?.liveSessionId || '') : '';
          if (code === 'teams_calendar_create_uncertain') {
            endCreateProgress();
            setCreateRecovery({ phase: 'uncertain', message: UNCERTAIN_MESSAGE });
            return;
          }
          // The create endpoint can answer 502 after the event and LMS row are
          // already saved, when Graph has not exposed the onlineMeeting options
          // yet. Read that saved result instead of showing the raw 403 or ever
          // sending Create again. A response without a live-session id is not
          // treated this way: it has no saved calendar we can safely recover.
          const savedPartial = err instanceof ApiError
            && err.data?.meetingCreated === true
            && Boolean(savedLiveSessionId);
          if (!(err instanceof CurriculumRequestTimeout)
            && code !== 'teams_calendar_create_in_progress'
            && !savedPartial) throw err;
          result = await recoverCreate(row);
        }
      }
      if (!result) return;
      endCreateProgress();
      // The create itself writes the join link into the live-session
      // components the author already placed before it answers. It must not
      // author new components in content-only weeks; Restore/Re-attach is the
      // explicit action for that.
      createDrawer.close();
      // The dialog was the create form and the create has happened, so it has
      // nothing left to show: close it rather than re-selecting the module into
      // its summary view, and let the confirmation land on the table behind.
      setSelectedId('');
      // Not awaited. The refresh is a round trip and the confirmation must not
      // queue behind it -- the table catches up while the alert is on screen.
      void loadTeamsState();
      notifyChanged(row.catalogueId);
      await finishTeamsCreation(result, input);
    } catch (err) {
      endCreateProgress();
      if (isTeamsReviewCancelled(err)) return;
      createDrawer.setError(err instanceof Error ? err.message : 'Microsoft Teams could not create the meeting.');
    } finally {
      createDrawer.setSaving(false);
      stopProgressPolling();
    }
  };

  /**
   * Ask whether re-attaching would actually do anything for the open module.
   *
   * Only for a module that has a calendar, and only while its dialog is open:
   * the answer costs a read of that module's weeks and components, which is far
   * too much to pay per row on the table behind it.
   */
  useEffect(() => {
    if (!selected?.summary) { setPendingComponents(null); return; }
    let cancelled = false;
    const catalogueId = selected.catalogueId;
    setPendingComponents(null);
    void probeModuleTeamsAttachment(catalogueId)
      .then(count => { if (!cancelled) setPendingComponents(count); })
      // A probe that fails leaves the button hidden. It only ever adds an
      // action, so failing closed costs nothing the user can see.
      .catch(() => { if (!cancelled) setPendingComponents(0); });
    return () => { cancelled = true; };
  }, [selected?.catalogueId, selected?.summary]);

  // Opening the dialog for a module with no calendar opens a blank invitee form.
  // The optional prefill button is the only path that assigns the module tutor
  // or learner-plan addresses.
  const seededCreateRef = useRef('');
  useEffect(() => {
    if (!selected || selected.summary || !selected.sessions.length || externalCalendar || calendarLinksLoading || calendarLinksError) {
      seededCreateRef.current = '';
      return;
    }
    const key = `${selected.catalogueId}:${defaultOrganizer}`;
    if (seededCreateRef.current === key) return;
    seededCreateRef.current = key;
    seedCreateForm(selected);
    // seedCreateForm is re-created every render; the ref above is what keeps
    // this from running twice for the same module.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultOrganizer, selected, externalCalendar, calendarLinksLoading, calendarLinksError]);

  // Opening the dialog for a module with a calendar fills its invitation fields
  // from that calendar. Filled again when the calendar's own read lands or the
  // saved calendar changes -- but never over edits not yet sent.
  const seededInvitationsRef = useRef('');
  const detailSeries = detail?.series;
  useEffect(() => {
    const summary = selected?.summary;
    if (!selected || !summary) {
      seededInvitationsRef.current = '';
      return;
    }
    const loaded = detailSeries?.id === summary.liveSessionId;
    const key = `${summary.liveSessionId}:${loaded}:${summary.updatedAt || ''}:${loaded ? JSON.stringify(detailSeries) : ''}`;
    if (seededInvitationsRef.current === key) return;
    if (invitationsFor.current === summary.liveSessionId && updateDrawer.dirty) return;
    seededInvitationsRef.current = key;
    seedInvitations(selected);
    // seedInvitations is re-created every render; the key is what keeps this
    // from running twice for the same calendar state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, detailSeries]);

  // True while the discard dialog is up. Escape reaches both this dialog and
  // SweetAlert's own handler in the same event, so without the flag dismissing
  // the dialog with the keyboard immediately raises a second one.
  const confirmingDiscard = useRef(false);

  /**
   * Every way out of the dialog -- the title-bar cross, the backdrop and
   * Escape -- comes through here. When the dialog is the create form and it has
   * been filled in, nothing is on Teams yet, so closing asks before it throws
   * the answers away.
   */
  const requestCloseSelected = () => {
    if (createDrawer.saving || updateDrawer.saving || updateProgress) return;
    // The invitation fields only count for the calendar on screen.
    const invitationsDirty = Boolean(selected?.summary) && updateDrawer.dirty;
    if (!createDrawer.dirty && !invitationsDirty) { setSelectedId(''); return; }
    if (confirmingDiscard.current) return;
    confirmingDiscard.current = true;
    void showCurriculumConfirm({
      title: 'Discard unsaved changes?',
      text: invitationsDirty
        ? 'Your changes to the invitations have not been sent to Teams. Closing now throws them away.'
        : createDraft?.catalogueId === selected?.catalogueId
          ? 'This Teams calendar has not been created yet. Closing now throws away what you changed since the saved draft.'
          : 'This Teams calendar has not been created yet. Closing now throws away what you filled in. Save draft keeps it for later.',
      icon: 'warning',
      confirmButtonText: 'Discard changes',
      cancelButtonText: 'Keep editing',
      onConfirm: () => {
        createDrawer.close();
        // Back to the calendar's saved values, so reopening shows what Teams holds.
        if (invitationsDirty && updateBaseline.current) updateDrawer.openWith(updateBaseline.current);
        setSelectedId('');
      },
    }).finally(() => { confirmingDiscard.current = false; });
  };


  // A single module's screen has no table to fall back to: closing a drawer
  // opened from its dialog returns to that dialog rather than to nothing.
  // ------------------------------------------------- main or additional, per week

  const selectedScopeRows = useMemo(
    () => (selected ? scopeRows[normaliseKey(selected.catalogueId)] || [] : []),
    [scopeRows, selected],
  );

  /**
   * Give one live session to the module calendar or to an additional meeting.
   *
   * The choice inherits the main link and matches imported calendar dates.
   * Nothing reaches Microsoft. Each side then refuses
   * it: the module calendar stops sending its date, or the additional-meeting
   * form stops offering the week. Read again afterwards rather than patched in
   * place, so what the dialog shows is what the server now holds.
   */
  const chooseMeetingScope = async (componentId: string, scope: 'main' | 'additional') => {
    if (!selected) return;
    if (blockedReason) { setNotice({ tone: 'warning', text: blockedReason, moduleId: selected.catalogueId }); return; }
    const catalogueId = selected.catalogueId;
    setBusy(`${catalogueId}:scope:${componentId}:${scope}`);
    setNotice(null);
    try {
      const result = await setLiveSessionMeetingScope(catalogueId, componentId, scope);
      if (result.message) {
        setNotice({ tone: result.calendarStatus === 'matched' ? 'info' : 'warning', text: result.message, moduleId: catalogueId });
      }
      setScopeEpoch(value => value + 1);
      notifyChanged(catalogueId);
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'The choice could not be saved. Nothing was changed.',
        moduleId: catalogueId,
      });
    } finally {
      setBusy('');
    }
  };

  /** Read the weeks again, after something outside this hook (the additional-meeting tab) booked or cancelled one. */
  const refreshMeetingScopes = useCallback(() => setScopeEpoch(value => value + 1), []);

  const drawerOpen = settingsDrawer.open;
  const wasDrawerOpen = useRef(false);
  useEffect(() => {
    if (wasDrawerOpen.current && !drawerOpen && scopeModuleId && !calendarActionTarget) setSelectedId(scopeModuleId);
    wasDrawerOpen.current = drawerOpen;
  }, [calendarActionTarget, drawerOpen, scopeModuleId]);

  return {
    programmes, cohorts, groups, modules, loading, loaded, refreshing, error, reload,
    teamsLoading, teamsLoaded, teamsError, graphConfigured, graphStatus, checkGraphConfiguration, timeZoneLabel,
    selectedId, setSelectedId, calendarActionTarget, setCalendarActionTarget,
    busy, notice, setNotice, detail, detailLoading, detailError, loadDetail, detailOccurrenceFor,
    artifactSyncing, autoSyncEnabled, setAutoSyncEnabled, calendarSyncing, resultsModule, setResultsModule,
    preview, setPreview, transcriptPreview, setTranscriptPreview, pendingComponents, now,
    settingsDrawer, createDrawer, createDraft, createDraftSaving, saveCreateDraft, leftoverCancelling, cancelLeftover, updateDrawer, drawerTarget, invitedPrefilling, prefillNotice,
    loadTeamsState, holidayLabelFor, rows, selected, selectedForDisplay, stats,
    openCalendarAction, checkCalendarAction, runArtifactSync, runCalendarSync,
    pushDates, resendSchedule, saveInvitations, addedInvitees, reattach, prefillInvitees, openSettings, saveSettings,
    comparison, comparing, comparisonError, compareAttendees, publishedInvitees,
    selectedScopeRows, chooseMeetingScope, refreshMeetingScopes,
    externalCalendar, calendarLinksLoading, calendarLinksError,
    createCalendar, createRecovery, createProgress, updateProgress, requestCloseSelected, notifyChanged, blockedReason, drawerOpen,
  };
}

export type TeamsMeetingsWorkspace = ReturnType<typeof useTeamsMeetingsWorkspace>;

/** The draft's saved values that the create form holds, and nothing else. */
function draftFormValues(saved: Record<string, string>): Partial<TeamsCalendarForm> {
  const values: Partial<TeamsCalendarForm> = {};
  const text = ['organizerEmail', 'attendees', 'presenters', 'coOrganizers', 'details', 'durationMinutes',
    'lobbyBypass', 'recording', 'spokenLanguage', 'meetingType'] as const;
  for (const key of text) if (typeof saved[key] === 'string') values[key] = saved[key];
  if (saved.scheduleTimeZone === 'Africa/Cairo' || saved.scheduleTimeZone === 'Europe/London') values.scheduleTimeZone = saved.scheduleTimeZone;
  if (saved.seriesMode === 'auto' || saved.seriesMode === 'shared' || saved.seriesMode === 'per_day') values.seriesMode = saved.seriesMode;
  return values;
}
