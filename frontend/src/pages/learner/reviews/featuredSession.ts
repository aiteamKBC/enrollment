import type { LearnerCalendarEvent } from '@/api/learnerCalendar';

export function meetingStatus(session: LearnerCalendarEvent): string {
  return session.status.trim().toLowerCase().replace(/[ _]+/g, '-');
}

export function meetingIsBooked(session: LearnerCalendarEvent): boolean {
  return Boolean(session.scheduledDate) && ['scheduled', 'in-progress'].includes(meetingStatus(session));
}

/** A target date is a reminder to book, never evidence of a scheduled meeting. */
export function featuredSession(sessions: LearnerCalendarEvent[], today: string): LearnerCalendarEvent | null {
  const active = sessions.filter(session => ['scheduled', 'in-progress', 'not-scheduled', 'planned'].includes(meetingStatus(session)));
  const booked = active.filter(session => meetingIsBooked(session) && session.scheduledDate! >= today)
    .sort((a, b) => `${a.scheduledDate}T${a.scheduledTime || '23:59'}`.localeCompare(`${b.scheduledDate}T${b.scheduledTime || '23:59'}`) || a.id.localeCompare(b.id));
  if (booked.length) return booked[0];
  const planned = active.filter(session => !meetingIsBooked(session))
    .sort((a, b) => (a.targetDate || a.date || '9999').localeCompare(b.targetDate || b.date || '9999') || a.sequence - b.sequence);
  return planned[0] || null;
}

export function meetingJoinUrl(session: LearnerCalendarEvent): string | null {
  if (!session.meetingLink) return null;
  try {
    const url = new URL(session.meetingLink);
    return url.protocol === 'https:' ? url.href : null;
  } catch { return null; }
}

export function meetingTimeRange(session: LearnerCalendarEvent): string {
  const match = /^(\d{2}):(\d{2})/.exec(session.scheduledTime || '');
  if (!meetingIsBooked(session) || !match) return 'To be confirmed';
  const hours = Number(match[1]); const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return 'To be confirmed';
  const start = `${match[1]}:${match[2]}`;
  if (!Number.isFinite(session.durationMinutes) || session.durationMinutes <= 0) return start;
  const end = hours * 60 + minutes + session.durationMinutes;
  return `${start} – ${String(Math.floor(end / 60) % 24).padStart(2, '0')}:${String(end % 60).padStart(2, '0')}${end >= 1440 ? ' (next day)' : ''}`;
}
