import { describe, expect, it } from 'vitest';
import { emptyLinks, type DirectoryCoach } from '@/api/coachBookingDirectory';
import { directoryStatistics, matchesCoachFilter } from './directoryData';

const coaches: DirectoryCoach[] = [
  { id: 1, version: 1, name: 'First Coach', slug: 'first', links: { ...emptyLinks(), support_session: 'https://example.test/support', one_to_one: 'https://example.test/private' } },
  { id: 2, version: 1, name: 'Second Coach', slug: 'second', links: emptyLinks() },
];

describe('directory presentation data', () => {
  it('counts the full public directory and enabled options, not appointment slots', () => {
    expect(directoryStatistics(coaches)).toEqual({ totalCoaches: 2, publicPages: 2, activeBookingOptions: 2, sessionsOffered: null });
  });
  it('distinguishes unavailable data from an empty directory', () => {
    expect(directoryStatistics(null)).toEqual({ totalCoaches: null, publicPages: null, activeBookingOptions: null, sessionsOffered: null });
    expect(directoryStatistics([])).toEqual({ totalCoaches: 0, publicPages: 0, activeBookingOptions: 0, sessionsOffered: null });
  });
  it('supports older records without a one-to-one link', () => {
    const { one_to_one: _unused, ...olderLinks } = coaches[0].links;
    expect(directoryStatistics([{ ...coaches[0], links: olderLinks as DirectoryCoach['links'] }]).activeBookingOptions).toBe(1);
  });
  it('filters by existing links without modifying or dropping option types', () => {
    const before = structuredClone(coaches);
    expect(coaches.filter(coach => matchesCoachFilter(coach, 'all'))).toEqual(coaches);
    expect(coaches.filter(coach => matchesCoachFilter(coach, 'with-links'))).toEqual([coaches[0]]);
    expect(coaches.filter(coach => matchesCoachFilter(coach, 'without-links'))).toEqual([coaches[1]]);
    expect(coaches).toEqual(before);
  });
});
