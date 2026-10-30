/** Normalize stored clocks without losing AM/PM from older session records. */
export function normalizedClock(value: unknown): string {
  const match = String(value ?? '').trim().match(/^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$/i);
  if (!match) throw new Error('Choose a valid session time, including AM or PM when using a 12-hour clock.');
  let hour = Number(match[1]);
  const minute = Number(match[2]);
  const period = match[3]?.toUpperCase();
  if (minute > 59 || (period ? hour < 1 || hour > 12 : hour > 23)) throw new Error('Choose a valid session time.');
  if (period) hour = hour % 12 + (period === 'PM' ? 12 : 0);
  return `${String(hour).padStart(2, '0')}:${match[2]}`;
}

export function clockLabel(value: unknown): string {
  if (!String(value ?? '').trim()) return 'Choose a time';
  try {
    const [hour, minute] = normalizedClock(value).split(':');
    return `${Number(hour) % 12 || 12}:${minute} ${Number(hour) < 12 ? 'AM' : 'PM'}`;
  } catch { return 'Check this time'; }
}

/** "2 hours", "90 min" -- the length a reader would say out loud. */
export function durationLabel(minutes: number): string {
  if (minutes <= 0) return '';
  if (minutes % 60) return `${minutes} min`;
  const hours = minutes / 60;
  return `${hours} hour${hours === 1 ? '' : 's'}`;
}

export function calendarInputError(row: { sessions: { startTime: unknown; endTime: unknown; startDateTimeUtc?: string; durationMinutes?: number }[] }): string {
  for (const session of row.sessions) {
    if (session.startDateTimeUtc && Number.isFinite(Date.parse(session.startDateTimeUtc)) && Number.isFinite(session.durationMinutes) && Number(session.durationMinutes) > 0) continue;
    try {
      const start = normalizedClock(session.startTime);
      const end = normalizedClock(session.endTime);
      if (end <= start) return 'Each session must end after it starts. Check its AM/PM values.';
    } catch { return 'A session has a missing or invalid time. Correct its schedule before sending invitations.'; }
  }
  return '';
}

const minuteOf = (value: string) => {
  const instant = Date.parse(value);
  return Number.isFinite(instant) ? Math.floor(instant / 60000) : NaN;
};

/**
 * Which date Teams holds for each planned session, paired by date before position.
 *
 * `planned` and `held` are UTC instants. A planned session is paired with the
 * held one on its exact minute, then with one on the same day (`dayOf`, the
 * calendar day in the business zone), and only then, in order, with whatever
 * held dates are still unclaimed -- a genuine move. Pairing by position alone
 * turned one session missing from Teams into "still holds next week's date" on
 * every row after it, and a last row "not on Teams" that Teams did hold.
 *
 * `paired[i]` is the held instant for `planned[i]`, or '' when Teams holds
 * nothing for it. `extra` is what Teams holds that no planned session claims.
 */
export function pairHeldDates(
  planned: readonly string[],
  held: readonly string[],
  dayOf: (value: string) => string,
): { paired: string[]; extra: string[] } {
  const remaining = held.filter(value => Number.isFinite(minuteOf(value)))
    .sort((left, right) => Date.parse(left) - Date.parse(right));
  const paired = planned.map(() => '');
  const claim = (index: number, match: string | undefined) => {
    if (match === undefined) return;
    paired[index] = match;
    remaining.splice(remaining.indexOf(match), 1);
  };
  planned.forEach((value, index) => {
    if (Number.isFinite(minuteOf(value))) claim(index, remaining.find(item => minuteOf(item) === minuteOf(value)));
  });
  planned.forEach((value, index) => {
    if (!paired[index] && Number.isFinite(minuteOf(value))) claim(index, remaining.find(item => dayOf(item) === dayOf(value)));
  });
  const open = planned.flatMap((value, index) => (!paired[index] && Number.isFinite(minuteOf(value)) ? [index] : []));
  const leftovers = [...remaining];
  open.forEach((index, order) => claim(index, leftovers[order]));
  return { paired, extra: remaining };
}

export function reviewDateLabel(value: string, timeZone: string): string {
  if (!Number.isFinite(Date.parse(value))) return 'Check this date and time';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  }).format(new Date(value)).replace(/\bam\b/, 'AM').replace(/\bpm\b/, 'PM');
}
