/** A recording belongs to the day it was captured in the LMS business zone. */
export function recordingDay(value?: string | null): string | null {
  if (!value) return null;
  // Older archive rows serialize a UTC timestamp without a timezone suffix.
  const instant = new Date(/(?:[zZ]|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(instant.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(instant);
  const part = (type: string) => parts.find(item => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}
