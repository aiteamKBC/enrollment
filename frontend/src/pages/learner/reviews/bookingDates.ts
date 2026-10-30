import { ukOffsetForDate, type BookingCalendarRules } from '@/api/learnerCalendar';

export function bookingToday(): string {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('-');
}

/** Booking inputs are UK wall-clock values, regardless of the browser timezone. */
export function bookingInstant(day: string, time: string): number {
  const wallClock = Date.parse(`${day}T${time}:00Z`);
  if (!Number.isFinite(wallClock)) return NaN;
  let instant = wallClock + ukOffsetForDate(day) * 60_000;
  // Resolve the offset at the actual instant, including midnight on a DST
  // change day when the noon offset is different from the start of the day.
  const formatter = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  for (let attempt = 0; attempt < 2; attempt++) {
    const parts = formatter.formatToParts(new Date(instant));
    const value = (type: string) => Number(parts.find(part => part.type === type)!.value);
    const displayed = Date.UTC(value('year'), value('month') - 1, value('day'), value('hour'), value('minute'));
    instant += wallClock - displayed;
  }
  return instant;
}

export function isoDate(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

/** Local noon of a strict `YYYY-MM-DD` calendar day, or null. A date input can
 * report values such as `20261-10-05` (a five-digit year typed into Chrome's
 * year field), which `new Date()` cannot parse; callers must not turn those
 * into instants. */
export function parseBookingDay(value?: string | null): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  if (!match) return null;
  const [year, month, day] = match.slice(1).map(Number);
  const selected = new Date(year, month - 1, day, 12, 0, 0);
  return selected.getFullYear() === year && selected.getMonth() === month - 1 && selected.getDate() === day ? selected : null;
}

export function mcmBookingDateRestrictionMessage(value: string): string {
  const selected = parseBookingDay(value);
  if (!selected) return value ? 'Choose a valid booking date.' : '';
  return selected.getDate() > 5 && selected.getDate() < 20
    ? 'MCM appointments are available from the 20th of each month through the 5th of the following month.'
    : '';
}

export function firstAvailableBookingDate(candidate?: string | null, rules?: BookingCalendarRules | null, monthly = false): string {
  const today = bookingToday();
  const valid = parseBookingDay(candidate) ? candidate! : '';
  const selected = new Date(`${valid && valid >= today ? valid : today}T12:00:00`);
  // Search only the published calendar horizon. Without rules the caller is
  // still loading them; a bounded provisional search must not loop forever.
  const lastYear = rules ? Math.max(0, ...rules.coveredYears) : selected.getFullYear();
  for (let searched = 0; searched < 3660 && selected.getFullYear() <= lastYear; searched++) {
    const day = isoDate(selected);
    if (!bookingDateRestrictionMessage(day, rules || null)
      && !(monthly && mcmBookingDateRestrictionMessage(day))) return day;
    selected.setDate(selected.getDate() + 1);
  }
  return '';
}

export function bookingDateRestrictionMessage(value: string, rules: BookingCalendarRules | null): string {
  if (!value) return '';
  const selected = parseBookingDay(value);
  if (!selected) return 'Choose a valid booking date.';
  if (value < bookingToday()) return 'Sessions cannot be booked on a date that has already passed.';
  if (selected.getDay() === 0 || selected.getDay() === 6) return 'Sessions cannot be booked on Saturdays or Sundays.';
  const holiday = rules?.bankHolidays?.find(day => day.date === value);
  if (holiday) return `Sessions cannot be booked on UK bank holidays (${holiday.title}).`;
  if (rules && !(rules.coveredYears || []).includes(selected.getFullYear())) {
    return 'Sessions cannot be booked because the UK bank-holiday calendar is not available for this year.';
  }
  return '';
}
