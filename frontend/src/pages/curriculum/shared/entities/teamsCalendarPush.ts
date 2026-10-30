// ============================================================================
// Sending a module's own session plan to the Teams calendar it drifted from.
//
// The Teams Meetings page sends the module's *stored* session dates. Once a
// series exists those dates and the tracked occurrences describe each other --
// the structure reader leaves a component's date alone when it carries a
// tracked occurrence, and the occurrence was written from that same date -- so
// neither side moves when the delivery days change, and re-sending them would
// only put the old dates back.
//
// The dates here come from the module's plan instead: the backend's own walk
// over the new delivery days and the cohort's ticked holidays, which is the
// only source that has actually moved. Asking the server for it rather than
// deriving it here is what keeps the pushed dates identical to the ones every
// other screen shows.
// ============================================================================

import { fetchModuleSessionPlan, loadModuleStructure, updateTeamsMeetingSchedule, zonedNaiveToUtcIso } from '../../module-builder/moduleAuthoringData';
import type { TeamsUpdateOutcome, UpdatedCalendar } from '../../teams-meetings/creationResult';
import { fetchCurriculumTeamsMeetingSummaries } from '@/lib/curriculumApi';
import { normalizedClock } from '../../teams-meetings/calendarTime';
import { liveSessionPlan } from './liveSessionMeetingScope';

/** The group's own hour, when a series has none of its own to keep. */
const FALLBACK_START_TIME = '09:00';
const FALLBACK_DURATION_MINUTES = 60;

function minutesBetween(startTime: string, endTime: string): number {
  if (!startTime || !endTime) return 0;
  const [startHour, startMinute] = normalizedClock(startTime).split(':').map(Number);
  const [endHour, endMinute] = normalizedClock(endTime).split(':').map(Number);
  if ([startHour, startMinute, endHour, endMinute].some(value => !Number.isFinite(value))) return 0;
  return (endHour * 60 + endMinute) - (startHour * 60 + startMinute);
}

export interface TeamsCalendarPushResult {
  /** How many dates the calendar was sent. */
  sessionCount: number;
  /** Graph's own complaint, when it took the series but not every instance. */
  warning: string;
  /** The update as the server returned it, with the review's email choice. */
  outcome: TeamsUpdateOutcome;
  /** What was sent, for the result dialog that may email about it. */
  sent: UpdatedCalendar;
}

/**
 * Put a module's Teams series onto the dates its plan now says.
 *
 * `weeks` is the authored week count when the caller knows it. The endpoint
 * multiplies it by the delivery days itself, and passing it keeps the plan
 * right for a module whose stored session count has not been re-derived.
 *
 * Throws when there is no tracked series, no plan, or Graph refuses the write:
 * a push that quietly did nothing would leave the reader believing the
 * invitations had moved.
 */
export async function pushModulePlanToTeams({
  moduleCatalogueId,
  moduleName,
  weeks,
  startTime,
  endTime,
}: {
  moduleCatalogueId: string;
  moduleName?: string;
  weeks?: number;
  startTime?: string;
  endTime?: string;
}): Promise<TeamsCalendarPushResult> {
  const catalogueId = String(moduleCatalogueId || '').trim();
  if (!catalogueId) throw new Error('This module has no catalogue id to plan from.');

  // The module's weeks are read with the plan, and a failure to read them
  // stops the push: they are what says which weeks the module calendar runs.
  // Without them every planned date would be sent -- including a week delivered
  // by its own additional meeting, whose link the series would then overwrite.
  const [plan, summaries, module] = await Promise.all([
    fetchModuleSessionPlan(catalogueId, weeks),
    fetchCurriculumTeamsMeetingSummaries(undefined, { moduleCatalogueIds: [catalogueId], skipCache: true }),
    loadModuleStructure(catalogueId, { skipCache: true }),
  ]);

  const planned = (plan ? liveSessionPlan(module, plan) : []).filter(session => String(session?.date || '').trim());
  if (!planned.length) throw new Error('This module has no planned session dates to send.');

  const summary = summaries.find(item => String(item.moduleCatalogueId || '').trim() === catalogueId);
  if (!summary?.liveSessionId) throw new Error('This module has no Teams meeting to update.');

  const time = normalizedClock(startTime || FALLBACK_START_TIME);
  const duration = Math.max(
    15,
    minutesBetween(time, String(endTime || '')) || summary.durationMinutes || FALLBACK_DURATION_MINUTES,
  );
  // Keep the planner's occurrence identity when it is unambiguous. In
  // particular, if week 2 is delivered by an additional event, week 3 must
  // stay session 3 on the module series; renumbering it to 2 makes the
  // occurrence writer move the later main session onto the removed week's
  // identity. Older planners sometimes repeat numbers, so those payloads use
  // the compact sequence the Teams API expects.
  const plannedNumbers = planned.map(session => session.sessionNumber).filter(
    (value): value is number => Number.isInteger(value) && value > 0,
  );
  const plannerNumbersAreUnique = plannedNumbers.length === planned.length
    && new Set(plannedNumbers).size === plannedNumbers.length;
  const occurrences = planned.map((session, index) => ({
    sessionNumber: plannerNumbersAreUnique ? session.sessionNumber : index + 1,
    startDateTimeUtc: zonedNaiveToUtcIso(`${session.date}T${normalizedClock(session.startTime || time)}`, summary.timeZone),
    durationMinutes: session.durationMinutes || minutesBetween(session.startTime || time, session.endTime || '') || duration,
  }));

  const title = String(moduleName || summary.moduleTitle || '').trim() || 'Live session';
  const result = await updateTeamsMeetingSchedule(summary.liveSessionId, {
    title,
    organizerEmail: summary.organizerEmail,
    eventId: summary.eventId,
    localStartDateTime: `${planned[0].date}T${normalizedClock(planned[0].startTime || time)}`,
    startDateTimeUtc: occurrences[0].startDateTimeUtc,
    durationMinutes: occurrences[0].durationMinutes,
    repeat: occurrences.length > 1 ? 'weekly' : 'none',
    repeatOccurrences: occurrences.length,
    scheduledOccurrences: occurrences,
  });

  return {
    sessionCount: occurrences.length,
    warning: String(result?.warnings?.[0]?.message || '').trim(),
    outcome: result,
    sent: { title, scheduledOccurrences: occurrences, scheduleTimeZone: summary.timeZone },
  };
}
