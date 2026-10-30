// ============================================================================
// Which calendar delivers a live session: the module's own Teams series, or an
// additional meeting of its own. Never both.
//
// A week delivered by both is the failure this guards: the series books the
// same date as the additional meeting, and the next Update writes the series'
// link over the additional meeting's. So each live session belongs to exactly
// one, chosen by its author and stored on the component (`teamsMeetingScope`),
// and every path that sends the module calendar's dates sends only 'main'.
//
// The backend reads the same rule in `live_session_meeting_scope` (views.py).
// ============================================================================

import type { ModuleCatalogueItem, ModuleWeekSessionPlan } from '../../module-builder/moduleAuthoringData';

export type LiveSessionMeetingScope = 'main' | 'additional' | 'pending';

export const LIVE_SESSION_MEETING_SCOPE_KEY = 'teamsMeetingScope';

type Settings = Record<string, unknown> | undefined | null;

const text = (value: unknown) => (value === null || value === undefined ? '' : String(value).trim());

/** A saved join link is still a calendar assignment even on older rows that
 * predate occurrence metadata. Additional meetings are checked first by the
 * scope reader, so their mirrored `liveSessionUrl` cannot be mistaken for the
 * module calendar here. */
function hasTeamsMeetingLink(settings: Settings): boolean {
  return Boolean(text(settings?.liveSessionLinkOverride) || text(settings?.liveSessionUrl) || text(settings?.teamsMeetingUrl));
}

/**
 * Whether the module's own series already holds a booked occurrence for it.
 *
 * `teamsOccurrenceId`/`teamsSessionNumber` identify newer rows whose real
 * Graph occurrence was paired to the component. Older rows can have only the
 * normal meeting link; `liveSessionMeetingScope` uses that link for display,
 * while this helper remains the confirmed-booking check.
 */
export function bookedOnModuleSeries(settings: Settings): boolean {
  return Boolean(text(settings?.teamsOccurrenceId) || Number(settings?.teamsSessionNumber || 0) > 0);
}

/**
 * Whether the module already has a main-calendar assignment. A pasted link
 * establishes that context too, so a newly inserted blank week must choose
 * which calendar to join. Only `bookedOnModuleSeries` confirms a booking.
 */
export function moduleHasBookedSeries(components: Array<{ settings?: Settings }>): boolean {
  return components.some(({ settings }) => {
    if (text(settings?.extraTeamsMeetingUrl)) return false;
    if (bookedOnModuleSeries(settings)) return true;
    if (text(settings?.teamsMeetingScope).toLowerCase() === 'additional') return false;
    return hasTeamsMeetingLink(settings);
  });
}

/**
 * Which calendar delivers this live session.
 *
 * A confirmed booking outranks the stored choice. Otherwise the author's
 * explicit choice decides, and a legacy normal meeting link fills in the
 * display when no choice was stored. With neither, a module with no calendar
 * yet delivers it on its own series (there is nothing to overwrite); a module
 * that already has one leaves it 'pending' -- a week added since -- until
 * someone chooses.
 */
export function liveSessionMeetingScope(settings: Settings, moduleHasSeries = true): LiveSessionMeetingScope {
  if (text(settings?.extraTeamsMeetingUrl)) return 'additional';
  if (bookedOnModuleSeries(settings)) return 'main';
  const chosen = text(settings?.[LIVE_SESSION_MEETING_SCOPE_KEY]).toLowerCase();
  if (chosen === 'main' || chosen === 'additional') return chosen;
  // Legacy rows can retain a normal join link without occurrence metadata.
  // The link settles what the row displays, while `meetingScopeIsOpen` keeps
  // the existing option to move an unconfirmed link to an additional meeting.
  if (hasTeamsMeetingLink(settings)) return 'main';
  return moduleHasSeries ? 'pending' : 'main';
}

/**
 * Whether the author has already given this live session to the module's own
 * calendar, before it was booked there.
 *
 * Just as final as a booking: the next Update books it, and an additional
 * meeting on the same week would then lose its link to the series'. Each week
 * is delivered by one calendar only, so the additional-meeting form refuses it
 * until the choice is changed under "Which calendar runs each week". The
 * server refuses it too (`reserved_for_module_calendar`).
 */
export function reservedForModuleCalendar(settings: Settings): boolean {
  return !bookedOnModuleSeries(settings) && text(settings?.[LIVE_SESSION_MEETING_SCOPE_KEY]).toLowerCase() === 'main';
}

/** Whether the choice can still change: no confirmed occurrence or extra meeting exists. */
export function meetingScopeIsOpen(settings: Settings): boolean {
  return !text(settings?.extraTeamsMeetingUrl) && !bookedOnModuleSeries(settings);
}

/**
 * A booked occurrence keeps its historical date in `curriculum.sessions`.
 * The detail modal must still show the current plan that will be sent when a
 * group day/time changes, so overlay the fresh planner result without mutating
 * historical session rows or their Teams identity.
 *
 * Only the dates of the module's own calendar: the Teams Meetings page and the
 * shared plan push (`teamsCalendarPush`) both send what this returns, so a week
 * that is not 'main' can reach Microsoft through neither.
 */
export function liveSessionPlan(module: ModuleCatalogueItem | null, plan: ModuleWeekSessionPlan): ModuleWeekSessionPlan['sessions'] {
  if (!module?.weekStructure?.length) return plan.sessions || [];
  const selected: ModuleWeekSessionPlan['sessions'] = [];
  // The Teams occurrence each booked session already is. Sent under its own
  // number, so a week placed elsewhere never moves another week's meeting.
  const bookedNumbers: Array<number | null> = [];
  const hasSeries = moduleHasBookedSeries((module.weekStructure || []).flatMap(
    week => (week.components || []).filter(component => component.type === 'live-session'),
  ));
  // Planned dates already given to a week. A date is sent once: when a module
  // starts after its opening week's delivery day, week 1 has no calendar week
  // of its own and falls back to the first date -- which week 2 then also
  // matched by calendar week, sending two sessions on one day ("Session 2
  // overlaps the previous session") and never the last date at all.
  const used = new Set<ModuleWeekSessionPlan['sessions'][number]>();
  module.weekStructure.forEach(week => {
    const liveSessions = (week.components || []).filter(component => component.type === 'live-session');
    const liveCount = liveSessions.length;
    if (!liveCount) return;
    const byWeek = (plan.sessions || []).filter(session => Number(session.weekNumber) === Number(week.weekNumber) && !used.has(session));
    const candidates = byWeek.length
      ? byWeek.slice(0, liveCount)
      : (plan.sessions || []).filter(session => !used.has(session)).slice(0, liveCount);
    candidates.forEach(candidate => used.add(candidate));
    // A live session delivered by its own additional meeting -- or not yet
    // assigned to either calendar -- is not part of this calendar: it is not
    // shown among these dates, not counted among them, and not sent when they
    // are. Its slot is still CONSUMED rather than skipped — the dates are taken
    // in order, so dropping one from the middle without consuming it would pull
    // every later session a slot early.
    candidates.forEach((candidate, index) => {
      const settings = liveSessions[index]?.settings;
      if (liveSessionMeetingScope(settings, hasSeries) !== 'main') return;
      selected.push(candidate);
      const booked = Number(settings?.teamsSessionNumber || 0);
      bookedNumbers.push(bookedOnModuleSeries(settings) && booked > 0 ? booked : null);
    });
  });
  const taken = new Set(bookedNumbers.filter((value): value is number => value !== null));
  if (!taken.size) return selected;
  // A session not booked yet keeps the planner's number unless a booked one
  // already owns it; then it takes the next number no session holds.
  let next = Math.max(0, ...taken, ...selected.map(session => Number(session.sessionNumber) || 0));
  return selected.map((session, index) => {
    const booked = bookedNumbers[index];
    if (booked !== null) return booked === session.sessionNumber ? session : { ...session, sessionNumber: booked };
    const planned = Number(session.sessionNumber) || 0;
    if (planned > 0 && !taken.has(planned)) {
      taken.add(planned);
      return session;
    }
    next += 1;
    taken.add(next);
    return { ...session, sessionNumber: next };
  });
}
