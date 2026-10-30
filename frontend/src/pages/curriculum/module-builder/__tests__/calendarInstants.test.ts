import { describe, expect, it } from 'vitest';
import { formatCalendarDateTime, parseUtcInstant, viewerZoneOffset } from '../moduleAuthoringData';

/**
 * A meeting is one absolute instant. Everything that reads a stored timestamp has
 * to agree on which instant that is, whatever zone the reader's machine is in --
 * otherwise the same calendar is "in sync" in London and two hours out in Cairo,
 * which is what a reader in Egypt actually saw.
 */
describe('calendar instants', () => {
  it('reads a timestamp that names no offset as UTC, not as the reader’s own time', () => {
    // What `timestamp without time zone` columns hand back: UTC, unstamped.
    // `new Date()` would read this as the reader's local time and shift it.
    expect(parseUtcInstant('2026-12-12T09:00:00').toISOString()).toBe('2026-12-12T09:00:00.000Z');
    expect(parseUtcInstant('2026-12-12T09:00').toISOString()).toBe('2026-12-12T09:00:00.000Z');
    // Postgres' space separator is the same instant as the ISO 'T' form.
    expect(parseUtcInstant('2026-12-12 09:00:00').toISOString()).toBe('2026-12-12T09:00:00.000Z');
  });

  it('leaves a timestamp that does name its offset exactly where it is', () => {
    expect(parseUtcInstant('2026-12-12T09:00:00Z').toISOString()).toBe('2026-12-12T09:00:00.000Z');
    expect(parseUtcInstant('2026-12-12T09:00:00+00:00').toISOString()).toBe('2026-12-12T09:00:00.000Z');
    // +02:00 is 07:00 UTC -- the offset is honoured rather than dropped.
    expect(parseUtcInstant('2026-12-12T09:00:00+02:00').toISOString()).toBe('2026-12-12T07:00:00.000Z');
  });

  it('reports nothing for a value that is not a time at all', () => {
    expect(Number.isNaN(parseUtcInstant('').getTime())).toBe(true);
    expect(Number.isNaN(parseUtcInstant(null).getTime())).toBe(true);
    expect(Number.isNaN(parseUtcInstant('not a date').getTime())).toBe(true);
  });

  it('shows an unstamped time on the calendar’s clock rather than the reader’s', () => {
    // Europe/London in December is UTC+0, so the stamped and unstamped forms of
    // the same instant have to print identically no matter where this runs.
    expect(formatCalendarDateTime('2026-12-12T09:00:00')).toBe('12 Dec 2026, 09:00');
    expect(formatCalendarDateTime('2026-12-12T09:00:00Z')).toBe('12 Dec 2026, 09:00');
  });

  it('measures the reader’s own zone against the calendar’s', () => {
    const { viewerZone, differenceMinutes, calendarZoneLabel } = viewerZoneOffset(new Date('2026-12-12T09:00:00Z'));
    expect(viewerZone).toBeTruthy();
    expect(calendarZoneLabel).toBe('London');
    // Zero when both zones show the same clock, so a page can stay quiet; the
    // sign says which way the reader's own Teams will differ.
    expect(Number.isFinite(differenceMinutes)).toBe(true);
  });
});
