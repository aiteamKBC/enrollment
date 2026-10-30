import { SYSTEM_TIME_ZONE } from './format';
import type { WorkingHoursHoliday } from './componentAccessWindow';

// ============================================================================
// The working rules, as the browser sees them.
//
// This is UX only. The server decides whether a Finish click needs the
// correction dialog (learner_api.working_rules). This copy lets the dialog warn
// about a selection outside the rules; it never blocks Final Submit.
//
// Learning itself is never restricted by any of this.
// ============================================================================

export type WorkingRuleReason = 'weekend' | 'outside_working_hours' | 'holiday';

export interface WorkingRuleFailure {
  reason: WorkingRuleReason;
  holidayName: string;
  message: string;
}

/** Mirrors backend working_rules: Monday-Friday, 07:00 up to (not including) 19:00. */
export const WORKING_DAY_START_HOUR = 7;
export const WORKING_DAY_END_HOUR = 19;

/** A completion the server refused, with the reason it gave. */
export class CompletionValidationError extends Error {
  reason: WorkingRuleReason | '';
  holidayName: string;

  constructor(message: string, reason: WorkingRuleReason | '' = '', holidayName = '') {
    super(message);
    this.name = 'CompletionValidationError';
    this.reason = reason;
    this.holidayName = holidayName;
  }
}

/** `YYYY-MM-DD` for an instant, read in the business time zone. */
export function ukDateKey(at: Date): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: SYSTEM_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(at).map(part => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function holidayOn(day: string, holidays: WorkingHoursHoliday[]): WorkingHoursHoliday | null {
  return holidays.find(range => range.start <= day && day <= range.end) ?? null;
}

/**
 * Why this calendar day + clock time is not a working instant, or null.
 *
 * Takes the day and time as the learner typed them — the pickers are already in
 * UK terms, so there is no zone conversion to get wrong here. A learner in
 * another time zone still declares a UK working instant, which is the rule the
 * server applies too.
 */
export function workingRuleFailure(
  day: string,
  time: string,
  holidays: WorkingHoursHoliday[] = [],
): WorkingRuleFailure | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
    return { reason: 'outside_working_hours', holidayName: '', message: 'Select a completion date.' };
  }
  if (!/^\d{2}:\d{2}$/.test(time)) {
    return { reason: 'outside_working_hours', holidayName: '', message: 'Select a completion time.' };
  }

  const holiday = holidayOn(day, holidays);
  if (holiday) {
    const name = (holiday.label || '').trim();
    return {
      reason: 'holiday',
      holidayName: name,
      message: name ? `Bank holiday: ${name}.` : 'This date is a bank holiday.',
    };
  }

  // Midday avoids any chance of a DST boundary moving the weekday.
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay();
  if (weekday === 0 || weekday === 6) {
    const label = weekday === 0 ? 'Sunday' : 'Saturday';
    return { reason: 'weekend', holidayName: '', message: `${label} is not a working day.` };
  }

  const hour = Number(time.slice(0, 2));
  if (hour < WORKING_DAY_START_HOUR || hour >= WORKING_DAY_END_HOUR) {
    return {
      reason: 'outside_working_hours',
      holidayName: '',
      message: 'Outside official working hours (Monday to Friday, 07:00-19:00 UK time).',
    };
  }

  return null;
}

/** The instant a picker pair stands for, as the server parses it (UK local). */
export function declaredCompletedAt(day: string, time: string): string {
  return `${day}T${time}:00`;
}

/** Plain-English heading for why the learner is being asked to correct. */
export function reasonHeadline(reason: WorkingRuleReason | '', holidayName = ''): string {
  if (reason === 'holiday') {
    return holidayName ? `Bank holiday: ${holidayName}` : 'Bank holiday';
  }
  if (reason === 'weekend') return 'Outside official working days';
  if (reason === 'outside_working_hours') return 'Outside official working hours';
  return 'Outside official working rules';
}
