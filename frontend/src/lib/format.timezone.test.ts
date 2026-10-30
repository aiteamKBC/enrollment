import { describe, expect, it } from 'vitest';
import {
  formatSystemTimestamp,
  SYSTEM_TIME_ZONE,
  systemDateParts,
  systemTimeZoneName,
} from './format';

describe('system timezone', () => {
  it('uses Europe/London as the single business timezone', () => {
    expect(SYSTEM_TIME_ZONE).toBe('Europe/London');
  });

  it('applies GMT in winter and BST in summer', () => {
    expect(formatSystemTimestamp('2026-01-15T12:00:00Z', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    })).toBe('12:00');
    expect(systemTimeZoneName('2026-01-15T12:00:00Z')).toBe('GMT');

    expect(formatSystemTimestamp('2026-07-15T12:00:00Z', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    })).toBe('13:00');
    expect(systemTimeZoneName('2026-07-15T12:00:00Z')).toBe('BST');
  });

  it('derives the UK calendar day rather than the browser calendar day', () => {
    expect(systemDateParts('2026-07-15T23:30:00Z')).toEqual({
      year: 2026,
      month: 7,
      day: 16,
    });
  });

  it('treats a legacy offset-less API timestamp as UTC', () => {
    expect(formatSystemTimestamp('2026-07-15T12:00:00', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    })).toBe('13:00');
  });
});
