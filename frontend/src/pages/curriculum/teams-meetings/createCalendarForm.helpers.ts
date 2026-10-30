import { formatCalendarDateTime, getCalendarTimeZone, parseUtcInstant, zonedNaiveToUtcIso, type TeamsMeetingInput } from '../module-builder/moduleAuthoringData';
import { emailList } from "../module-builder/EmailChipsInput.helpers";
import { cleanText } from '../shared/entities/model';
import { calendarInputError, normalizedClock, clockLabel } from './calendarTime';


// ============================================================================
// The one create form for a module's Teams calendar.
//
// A module can be sent to Teams from the Teams Meetings page or from a live
// session inside the Module Builder. Those are two doors onto the same record,
// not two features: the dates, the settings Graph needs and the invitation
// lists are identical, so they are written once here and both callers render
// it. Each caller keeps only what is genuinely its own -- the dialog it sits in
// and what it does after the meeting exists.
// ============================================================================

export const DEFAULT_START_TIME = '09:00';

export const DEFAULT_DURATION_MINUTES = 60;


/**
 * The minute a UTC instant falls on, for comparing two dates for equality.
 *
 * Instants, never wall clocks: the module's plan and the Teams calendar are
 * compared as the same absolute moment, so a reader in another zone can never
 * turn a matching date into "will be moved".
 */
export function minuteKey(value: unknown): string {
  const instant = parseUtcInstant(value);
  return Number.isNaN(instant.getTime()) ? '' : instant.toISOString().slice(0, 16);
}


/**
 * The little a session has to say for a calendar to be built on it.
 *
 * `CurriculumSession` satisfies this, so the Teams Meetings page passes its own
 * rows unchanged. It is stated separately because the Module Builder reads the
 * same dates off the structure it is already holding -- the backend stamps them
 * onto each live-session component from the same planner -- rather than reading
 * the whole curriculum's session collection back to find one module's.
 */
export interface TeamsPlannedSession {
  /** Existing bookings retain their exact instant, number and elapsed duration. */
  startDateTimeUtc?: string;
  sessionNumber?: number;
  durationMinutes?: number;
  timeZone?: string;
  /** `YYYY-MM-DD` in the business zone. */
  date: string;
  startTime: string;
  endTime: string;
  /** Dates a ticked cohort holiday lands on, named above the list. */
  skippedHolidays?: string[];
  /** The live-session component this date belongs to, when the caller knows it. */
  componentId?: string;
  /** Display-only title from the matched live-session component. */
  componentTitle?: string;
}


/** `YYYY-MM-DDTHH:mm` for a stored session — the wall clock the group meets on. */
export function sessionNaiveLocal(session: TeamsPlannedSession): string {
  // Keep incomplete legacy rows renderable. Creation validates the original
  // values before any request, so this display fallback can never be sent.
  let time = DEFAULT_START_TIME;
  try { time = normalizedClock(session.startTime); } catch { /* Shown by calendarInputError. */ }
  return `${cleanText(session.date)}T${time}`;
}


export function minutesBetween(startTime: string, endTime: string): number {
  if (!cleanText(startTime) || !cleanText(endTime)) return 0;
  let start: string, end: string;
  try { start = normalizedClock(startTime); end = normalizedClock(endTime); } catch { return 0; }
  const [startHour, startMinute] = start.split(':').map(Number);
  const [endHour, endMinute] = end.split(':').map(Number);
  if ([startHour, startMinute, endHour, endMinute].some(value => !Number.isFinite(value))) return 0;
  return (endHour * 60 + endMinute) - (startHour * 60 + startMinute);
}


/** The same wall clock a UTC instant shows on the Microsoft calendar. */
export function naiveLocalFromUtc(value: unknown, timeZone = getCalendarTimeZone()): string {
  const instant = parseUtcInstant(value);
  if (Number.isNaN(instant.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(instant).reduce<Record<string, string>>((accumulator, part) => {
    if (part.type !== 'literal') accumulator[part.type] = part.value;
    return accumulator;
  }, {});
  if (!parts.year || !parts.day) return '';
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour === '24' ? '00' : parts.hour}:${parts.minute}`;
}


/** A date and time as the Microsoft calendar shows it, not as this reader's PC does. */
export const calendarLabel = (value: string, timeZone = getCalendarTimeZone()) => {
  const [date, time] = formatCalendarDateTime(value, timeZone).split(', ');
  return time ? `${date}, ${clockLabel(time)}` : date;
};


export function teamsGapNote(plannedUtc: string, teamsUtc: string, hasCalendar: boolean): { matches: boolean; note: string } {
  if (!plannedUtc) return { matches: true, note: '' };
  // Before the calendar exists there is nothing to RECONCILE — every date is
  // new — but each row still says where it stands, and names the button that
  // will put it there. `matches` stays true: nothing has drifted from anything,
  // so the row must not be styled as a difference.
  if (!hasCalendar) {
    return { matches: true, note: 'Not on the Teams calendar yet — Create Teams calendar adds it.' };
  }
  if (!teamsUtc) return { matches: false, note: 'Not on the Teams calendar yet — Update Teams calendar adds it.' };
  if (minuteKey(plannedUtc) === minuteKey(teamsUtc)) return { matches: true, note: '' };
  const planned = calendarLabel(plannedUtc);
  const held = calendarLabel(teamsUtc);
  const [plannedDay] = planned.split(', ');
  const [heldDay, heldTime] = held.split(', ');
  return {
    matches: false,
    note: plannedDay === heldDay
      ? `Right day, wrong time — Teams still holds ${heldTime || held}; sending moves it here.`
      : `Teams still holds ${held}; sending moves it here.`,
  };
}


/**
 * What the preview needs to know about a module. Deliberately narrower than the
 * Teams Meetings page's own row: the Module Builder has a module and its dates,
 * not a table row, and asking it for one would be asking it to become a second
 * copy of that page.
 */
export interface TeamsSchedulePreviewRow {
  timeZone?: string;
  name: string;
  sessions: TeamsPlannedSession[];
  /** The module's own session dates, as the UTC instants Teams would hold. */
  plannedStarts: string[];
  /** What Teams holds today, in order, when it has been asked for. */
  teamsStarts: string[];
  durationMinutes: number;
  /** Present once the module has a Teams calendar; only its existence matters here. */
  summary?: unknown;
  /**
   * The weekly slot the module's group runs to, when the caller knows it.
   *
   * The length of a session is the group's to state -- "Friday 12:00 PM to 2:00
   * PM" is two hours -- and a module inherits it. It is shown here so the reader
   * can see where 60 or 120 minutes came from, and so a session whose stored
   * clock no longer agrees with its group is visible before the invitations go
   * out. Optional: the Module Builder door has the group's name but not its
   * times, and an absent pattern says nothing rather than guessing one.
   */
  groupPattern?: GroupDeliveryPattern;
}


/** A group's weekly slot: the day it meets on and the clock it occupies. */
export interface GroupDeliveryPattern {
  /** The group's own name, so the line can say who it is about. */
  name: string;
  /** "Thursday" or "Monday, Wednesday" -- empty when the group names no day. */
  days: string;
  /** `HH:MM`, normalised. Empty when the group has no readable time. */
  startTime: string;
  endTime: string;
  /** The span between them, or 0 when either is missing. */
  durationMinutes: number;
}


/**
 * How long Create will book every session for, or 0 when each keeps its own.
 *
 * The Duration field is an override, not a display: leaving it on "Use
 * scheduled duration for each session" sends each session's own length. Both
 * the preview and the payload read it from here so the dates on screen are the
 * dates that get booked.
 */
export function overrideDurationMinutes(form: TeamsCalendarForm, row: { durationMinutes: number }): number {
  if (!form.durationMinutes) return 0;
  return Math.max(15, Number(form.durationMinutes) || row.durationMinutes || DEFAULT_DURATION_MINUTES);
}


/** Everything the create form holds, and nothing a caller has to reproduce. */
export interface TeamsCalendarForm {
  scheduleTimeZone?: 'Africa/Cairo' | 'Europe/London';
  seriesMode?: 'auto' | 'shared' | 'per_day';
  title: string;
  organizerEmail: string;
  attendees: string;
  presenters: string;
  coOrganizers: string;
  details: string;
  durationMinutes: string;
  lobbyBypass: string;
  recording: string;
  spokenLanguage: string;
  meetingType: string;
}


export function emptyTeamsCalendarForm(): TeamsCalendarForm {
  return {
    // England, because the learners and presenters a new calendar invites are
    // in England: the hour they read in their invitation is the hour they turn
    // up for. Defaulting to Egypt put an author's 9 AM into their calendars as
    // 7 AM, one clock change apart from the time the module says it runs at.
    // Egypt stays in the picker for a calendar that really is delivered there.
    scheduleTimeZone: 'Europe/London',
    title: '',
    organizerEmail: '',
    attendees: '',
    presenters: '',
    coOrganizers: '',
    details: '',
    durationMinutes: '',
    seriesMode: 'auto',
    lobbyBypass: 'everyone',
    recording: 'record-transcribe',
    spokenLanguage: 'en-GB',
    meetingType: 'live-session',
  };
}


/** The module a calendar is being created for: its identity and its dates. */
export interface TeamsCalendarTarget extends TeamsSchedulePreviewRow {
  catalogueId: string;
}


/** The module's own session dates, in the shape the Teams endpoints take. */
export function teamsCalendarOccurrences(row: TeamsCalendarTarget, timeZone?: string) {
  return row.sessions.map((session, index) => ({
    sessionNumber: session.sessionNumber ?? index + 1,
    startDateTimeUtc: session.startDateTimeUtc || zonedNaiveToUtcIso(sessionNaiveLocal(session), timeZone || session.timeZone || row.timeZone),
    durationMinutes: session.durationMinutes || Math.max(15, minutesBetween(session.startTime, session.endTime) || row.durationMinutes),
  }));
}


/**
 * The create payload, built the same way from either door.
 *
 * Durations follow each session unless explicitly overridden. The dates are the
 * module's own — a holiday-shifted plan included, which is what the backend
 * moves each Graph instance onto.
 */
export function buildTeamsCalendarInput(row: TeamsCalendarTarget, form: TeamsCalendarForm): TeamsMeetingInput {
  const invalid = calendarInputError(row);
  if (invalid) throw new Error(invalid);
  const meetingTitle = cleanText(row.name) || 'Live session';
  const duration = Math.max(15, Number(form.durationMinutes) || row.durationMinutes || DEFAULT_DURATION_MINUTES);
  // Read through the same helper the preview reads, so the lengths on screen
  // are the lengths that get booked.
  const override = overrideDurationMinutes(form, row);
  const occurrences = teamsCalendarOccurrences(row, form.scheduleTimeZone).map(occurrence => (override ? { ...occurrence, durationMinutes: override } : occurrence));
  return {
    ...(form.scheduleTimeZone ? { scheduleTimeZone: form.scheduleTimeZone } : {}),
    title: meetingTitle,
    organizerEmail: form.organizerEmail.trim(),
    attendees: emailList(form.attendees),
    presenters: emailList(form.presenters),
    coOrganizers: emailList(form.coOrganizers),
    moduleCatalogueId: row.catalogueId,
    moduleTitle: meetingTitle,
    localStartDateTime: sessionNaiveLocal(row.sessions[0]),
    startDateTimeUtc: occurrences[0].startDateTimeUtc,
    durationMinutes: occurrences[0]?.durationMinutes || duration,
    seriesMode: form.seriesMode || 'auto',
    repeat: occurrences.length > 1 ? 'weekly' : 'none',
    repeatOccurrences: occurrences.length,
    scheduledOccurrences: occurrences,
    lobbyBypass: form.lobbyBypass,
    recording: form.recording,
    spokenLanguage: form.spokenLanguage,
    meetingType: form.meetingType,
    details: form.details,
    requestResponses: true,
    allowNewTimeProposals: true,
    // A cohort mixes learners with staff from unrelated employers, and Graph
    // puts the full attendee list in the invitation it sends each of them.
    // Hiding it means every invitee sees only themselves, so one module's
    // invitation stops disclosing everyone's address to everyone else.
    // Organizer and co-organizers still see the full list.
    hideAttendees: true,
    // Keeps retries and double-clicks for the same module idempotent at Graph
    // as well as at our own API and database boundary.
    transactionId: `TEAMS-${row.catalogueId}`,
  };
}


export type TeamsUpdateFields = Partial<Pick<TeamsMeetingInput, 'attendees' | 'presenters' | 'coOrganizers' | 'recording' | 'lobbyBypass' | 'spokenLanguage'>>;


/**
 * What an update form changes on an existing calendar, and who it newly invites.
 *
 * Only changed fields are returned, so an untouched list or option keeps the
 * value the calendar has saved -- the server treats an omitted key as "keep".
 * `addedPeople` is everyone invited now, in any role, who was not invited
 * before; moving someone between roles does not make them new.
 */
export function teamsUpdateChanges(form: TeamsCalendarForm, before: TeamsCalendarForm): { fields: TeamsUpdateFields; addedPeople: string[] } {
  const fields: TeamsUpdateFields = {};
  const lower = (value: string) => emailList(value).map(email => email.toLowerCase());
  const sameList = (left: string[], right: string[]) => left.length === right.length && left.every(value => right.includes(value));
  for (const key of ['attendees', 'presenters', 'coOrganizers'] as const) {
    if (!sameList(lower(form[key]), lower(before[key]))) fields[key] = emailList(form[key]);
  }
  for (const key of ['recording', 'lobbyBypass', 'spokenLanguage'] as const) {
    if (form[key] !== before[key]) fields[key] = form[key];
  }
  const invited = (value: TeamsCalendarForm) => new Set([value.organizerEmail.trim().toLowerCase(),
    ...lower(value.attendees), ...lower(value.presenters), ...lower(value.coOrganizers)]);
  const was = invited(before);
  return { fields, addedPeople: [...invited(form)].filter(email => email && !was.has(email)) };
}


/** Who may skip the lobby. The values the backend accepts, in the order offered. */
export const TEAMS_LOBBY_OPTIONS = [
  { value: 'invited', label: 'People invited to this meeting' },
  { value: 'organization', label: 'People in my organization' },
  { value: 'organization-excluding-guests', label: 'Organization, excluding guests' },
  { value: 'everyone', label: 'Everyone' },
  { value: 'organizer', label: 'Only organizers' },
];


export const TEAMS_RECORDING_OPTIONS = [
  { value: 'none', label: 'Do not start automatically' },
  { value: 'record', label: 'Record automatically' },
  { value: 'record-transcribe', label: 'Record and transcribe' },
];


export const TEAMS_LANGUAGE_OPTIONS = [
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'en-US', label: 'English (US)' },
  { value: 'ar-EG', label: 'Arabic (Egypt)' },
  { value: 'fr-FR', label: 'French' },
];
