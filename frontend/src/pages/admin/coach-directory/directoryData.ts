import { bookingTypes, type DirectoryCoach } from '@/api/coachBookingDirectory';

export type CoachFilter = 'all' | 'with-links' | 'without-links';

export function hasBookingLinks(coach: DirectoryCoach) {
  return bookingTypes.some(type => Boolean(coach.links[type.key]));
}

export function matchesCoachFilter(coach: DirectoryCoach, filter: CoachFilter) {
  return filter === 'all' || (filter === 'with-links' ? hasBookingLinks(coach) : !hasBookingLinks(coach));
}

/** The directory API returns the complete public directory, not a paged result.
 * Links describe enabled options, not a count of sessions or calendar slots. */
export function directoryStatistics(coaches: DirectoryCoach[] | null) {
  return {
    totalCoaches: coaches ? coaches.length : null,
    publicPages: coaches ? coaches.length : null,
    activeBookingOptions: coaches ? coaches.reduce((total, coach) => total + bookingTypes.filter(type => Boolean(coach.links[type.key])).length, 0) : null,
    sessionsOffered: null,
  };
}

