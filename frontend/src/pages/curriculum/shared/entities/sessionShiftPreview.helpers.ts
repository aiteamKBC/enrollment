import { cleanText } from './model';


/** The minimum a generated session needs to carry to be placed on the timeline. */
export interface HolidayShiftSessionLike {
  date: string;
  skippedHolidays?: string[];
}


/**
 * The plan's own dates, with each session's holiday clashes named against them.
 *
 * A ticked holiday moves nothing and decides nothing: the session keeps its own
 * delivery day and the plan only NAMES the holidays that fall on it, in
 * `clashes`. That is the whole of it -- a warning, no shift, no replacement, no
 * effect on the run or on the module's end date.
 *
 * `moved`, `gapLabel`, `headline` and `detail` are the parked clash fields.
 * They are kept on the shape because every caller still renders it, and they
 * are now always inert (`false` / empty). See the parked block below.
 */
export interface SessionShift {
  sessionNumber: number;
  originalDate: string;
  actualDate: string;
  moved: boolean;
  gapLabel: string;
  /** The closed dates this session itself landed on, in the order it hit them. */
  clashes: Array<{ date: string; holiday: string }>;
  /** Unused now that a shift is read solely off the session's own clashes; kept for callers matching on the shape. */
  causeNames: string[];
  headline: string;
  detail: string;
}


export interface HolidayShiftPlan {
  shifts: SessionShift[];
  closures: Array<{ date: string; label: string }>;
  movedCount: number;
  movedRangeLabel: string;
  originalEndDate: string;
  shiftedEndDate: string;
}


// ---------------------------------------------------------------------------
// Holiday clash handling -- PARKED ON PURPOSE, kept below as comments.
//
// A ticked holiday landing on a session used to push that session to the next
// delivery day and drag every session behind it along with it. That rule is
// off. A holiday is a WARNING now and nothing else: the session keeps its own
// date, the week keeps its place in the run, the module keeps its end date, and
// whether the session runs, becomes a reading week or is dropped is the
// author's call, made on the week itself.
//
// The arithmetic is commented out rather than deleted, because the shape it
// filled in (`moved`, `gapLabel`, `headline`, `detail`, `movedCount`,
// `movedRangeLabel`) is still on the payload every caller renders, and the rule
// may be asked for again. Nothing reads it while it is parked.
//
// How much later a session runs, in the delivery pattern's own units:
//   function gapBetween(fromIso: string, toIso: string): string {
//     const from = Date.parse(`${fromIso}T00:00:00Z`);
//     const to = Date.parse(`${toIso}T00:00:00Z`);
//     if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return '';
//     const days = Math.round((to - from) / 86400000);
//     if (days % 7 === 0) {
//       const weeks = days / 7;
//       return `${weeks} week${weeks === 1 ? '' : 's'} later`;
//     }
//     return `${days} day${days === 1 ? '' : 's'} later`;
//   }
//
// `2026-09-10` shifted by whole weeks, forward or back:
//   function shiftByWeeks(iso: string, weeks: number): string {
//     const parsed = Date.parse(`${iso}T00:00:00Z`);
//     if (Number.isNaN(parsed) || !weeks) return iso;
//     return new Date(parsed + weeks * 7 * 86400000).toISOString().slice(0, 10);
//   }
//
// Inside the per-session walk below, where a clash used to become a move:
//   const originalDate = clashes.length ? clashes[0].date : actualDate;
//   const moved = Boolean(originalDate) && originalDate !== actualDate;
//   const clashNames = Array.from(new Set(clashes.map(c => c.holiday).filter(Boolean)));
//   gapLabel: moved ? gapBetween(originalDate, actualDate) : '',
//   headline: !moved ? '' : clashNames.length ? `Clashes with ${clashNames.join(', ')}` : 'Clashes with a holiday',
//   detail: !moved ? '' : [
//     `Was due ${formatDateLabel(originalDate)}`,
//     ...clashes.map(c => `${formatDateLabel(c.date)} closed${c.holiday ? `: ${c.holiday}` : ''}`),
//     `Runs ${formatDateLabel(actualDate)}`,
//   ].join(' · '),
//
// And the run-level summary of those moves:
//   const movedNumbers = shifts.filter(shift => shift.moved).map(shift => shift.sessionNumber);
//   const contiguous = movedNumbers.length > 1
//     && movedNumbers[movedNumbers.length - 1] - movedNumbers[0] === movedNumbers.length - 1;
//   movedCount: movedNumbers.length,
//   movedRangeLabel: !movedNumbers.length ? ''
//     : movedNumbers.length === 1 ? `Session ${movedNumbers[0]}`
//     : contiguous ? `Sessions ${movedNumbers[0]}-${movedNumbers[movedNumbers.length - 1]}`
//     : `Sessions ${movedNumbers.join(', ')}`,
//   originalEndDate: cleanText(plannedOriginalEndDate)
//     || (shiftedEndDate ? shiftByWeeks(shiftedEndDate, -closedDates.length) : ''),
// ---------------------------------------------------------------------------

export function buildHolidayShiftPlan<S extends HolidayShiftSessionLike>(
  sessions: S[],
  holidayLabelFor?: (date: string) => string,
  plannedOriginalEndDate?: string,
): HolidayShiftPlan {
  const shifts: SessionShift[] = sessions.map((session, index) => {
    const actualDate = cleanText(session.date);
    // The one fact still read off a holiday: which closed days land on THIS
    // session's own date, named so a screen can warn about them. It stops here
    // -- naming a clash is not acting on one.
    const clashes = (session.skippedHolidays || [])
      .map(date => cleanText(date))
      .filter(Boolean)
      .map(date => ({ date, holiday: cleanText(holidayLabelFor?.(date)) }));
    return {
      sessionNumber: index + 1,
      // A session is never displaced, so where it was due and where it runs are
      // the same day, and every clash field stays inert.
      originalDate: actualDate,
      actualDate,
      moved: false,
      gapLabel: '',
      clashes,
      causeNames: [],
      headline: '',
      detail: '',
    };
  });

  const closedDates = Array.from(new Set(
    sessions.flatMap(session => (session.skippedHolidays || []).map(date => cleanText(date))).filter(Boolean),
  )).sort();
  const shiftedEndDate = cleanText(sessions[sessions.length - 1]?.date);
  return {
    shifts,
    closures: closedDates.map(date => ({ date, label: cleanText(holidayLabelFor?.(date)) })),
    // Nothing moves, so nothing is counted as moved.
    movedCount: 0,
    movedRangeLabel: '',
    // The same day either way: the run a holiday never touched ends where this
    // one ends. Stated rather than dropped, because callers render the pair.
    originalEndDate: cleanText(plannedOriginalEndDate) || shiftedEndDate,
    shiftedEndDate,
  };
}


export function monthKeyOf(value: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(String(value || '').trim());
  return match ? `${match[1]}-${match[2]}` : '';
}


export function monthLabelOf(key: string): string {
  const parsed = new Date(`${key}-01T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return key;
  return parsed.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
}


/** `2026-09-16` -> `(Wednesday)`. Empty for anything that is not a date. */
export function weekdayLabel(value: string): string {
  const parsed = new Date(`${String(value || '').trim().slice(0, 10)}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return '';
  return `(${parsed.toLocaleDateString('en-GB', { weekday: 'long' })})`;
}


/** Which holiday(s) land on a session, in the words its warning uses. */
export function holidayCausePhrase(shift: SessionShift): string {
  if (shift.clashes.length) {
    const names = Array.from(new Set(shift.clashes.map(clash => clash.holiday).filter(Boolean)));
    return names.length ? names.join(', ') : 'a holiday';
  }
  return shift.causeNames.length ? shift.causeNames.join(', ') : 'a holiday';
}
