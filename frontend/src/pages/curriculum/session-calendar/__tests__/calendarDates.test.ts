import { describe, expect, it, vi } from 'vitest';
import { formatDate } from "../page.helpers";

describe('session calendar date keys', () => {
  it('keeps a calendar cell on its local day instead of shifting it through UTC', () => {
    const localThursday = new Date(2026, 7, 6);

    // Cairo midnight is the previous UTC date. The calendar must not use that
    // UTC value as the key that it compares with the API's date-only string.
    vi.spyOn(localThursday, 'toISOString').mockReturnValue('2026-08-05T21:00:00.000Z');

    expect(formatDate(localThursday)).toBe('2026-08-06');
  });
});
