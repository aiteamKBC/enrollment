/**
 * A call on a session's Teams link that was not that session: the link tried
 * before the first session, a day's use long after it, or one person checking
 * the room. Teams lists it as the meeting having happened that day; the LMS
 * ignores it, so it gives the session no attendance and no recording. Every
 * screen that shows the session says so, with both dates.
 */
export interface OffScheduleRun {
  runStartUtc: string;
  runEndUtc: string;
  scheduledStartUtc: string;
  reason: 'before_session' | 'after_session' | 'test_open' | string;
}

/** The sentence a person reads, with every date written by the screen's own formatter. */
export function offScheduleRunSentence(run: OffScheduleRun, when: (iso: string) => string): string {
  const session = `this session (${when(run.scheduledStartUtc)})`;
  const call = `${when(run.runStartUtc)} to ${when(run.runEndUtc)}`;
  const ignored = 'It is not counted as this session: no attendance or recording is attached to it.';
  if (run.reason === 'after_session') {
    return `Teams was used on this link from ${call}, more than 12 hours after ${session}. ${ignored}`;
  }
  if (run.reason === 'test_open') {
    return `One person opened this link for under five minutes, from ${call}, which is a check of the room, not ${session}. ${ignored}`;
  }
  return `Teams was opened on this link from ${call}, before ${session}. Teams shows the meeting as held then. ${ignored}`;
}

/** A short label for a table cell, with the full sentence kept for its title. */
export function offScheduleRunLabel(runs: OffScheduleRun[] = []): string {
  if (!runs.length) return '';
  return runs.length === 1 ? 'A Teams call on another day was ignored' : `${runs.length} Teams calls on other days were ignored`;
}
