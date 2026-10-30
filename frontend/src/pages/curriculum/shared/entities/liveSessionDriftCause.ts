/**
 * Why a Teams-booked live session no longer sits on its week's date, said from
 * the two dates alone.
 *
 * Nothing records which edit moved the plan, so this reads the shape of the
 * move instead: a different weekday means the delivery day changed; the same
 * weekday on another date means the run itself moved -- a new start date, or a
 * week added, removed or reordered. It never names a person or a time it does
 * not know.
 */

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAY_MS = 86_400_000;

/** A YYYY-MM-DD calendar date as a UTC midnight, so no machine time zone shifts its day. */
function calendarDay(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
  if (!match) return null;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function weekdayOf(value: string): string {
  const day = calendarDay(value);
  return day === null ? '' : WEEKDAYS[new Date(day).getUTCDay()];
}

export interface LiveSessionDriftCause {
  /** Short, for a badge: "Day changed Friday → Sunday". */
  short: string;
  /** A full sentence, for a tooltip or a notice. */
  sentence: string;
}

export function liveSessionDriftCause(drift: { storedDate: string; weekDates: string[] }): LiveSessionDriftCause | null {
  const planned = drift.weekDates[0] || '';
  const fromDay = weekdayOf(drift.storedDate);
  const toDay = weekdayOf(planned);
  if (!fromDay || !toDay) return null;
  const plannedDays = drift.weekDates.map(weekdayOf);
  if (!plannedDays.includes(fromDay)) {
    // A new day more than a week away is not the same week on another day:
    // the run moved too (a new start date), and saying only "day changed"
    // hid the bigger half of the move.
    const gap = Math.abs((calendarDay(planned) ?? 0) - (calendarDay(drift.storedDate) ?? 0)) / DAY_MS;
    if (gap > 6) {
      return {
        short: `Date and day changed ${fromDay} → ${toDay}`,
        sentence: `The module's dates moved and its delivery day changed from ${fromDay} to ${toDay} (a new start date, and the group's or the module's delivery days were edited), so this week now runs on ${toDay}.`,
      };
    }
    return {
      short: `Day changed ${fromDay} → ${toDay}`,
      sentence: `The delivery day changed from ${fromDay} to ${toDay} (the group's or the module's delivery days were edited), so this week now runs on ${toDay}.`,
    };
  }
  // Measure against the planned date on the same weekday: a Mon+Fri week's
  // Friday session moved by however far that Friday moved, not to the Monday.
  const sameDay = drift.weekDates[plannedDays.indexOf(fromDay)];
  const days = Math.round(((calendarDay(sameDay) ?? 0) - (calendarDay(drift.storedDate) ?? 0)) / DAY_MS);
  const weeks = Math.abs(days) / 7;
  const distance = Number.isInteger(weeks)
    ? `${weeks} week${weeks === 1 ? '' : 's'}`
    : `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`;
  const direction = days > 0 ? 'later' : 'earlier';
  return {
    short: `Week moved ${distance} ${direction}`,
    sentence: `The delivery day is still ${fromDay}, but this week moved ${distance} ${direction} — the module's start date changed, or a week was added, removed or reordered before it.`,
  };
}
