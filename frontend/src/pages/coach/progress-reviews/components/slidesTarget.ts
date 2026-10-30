import { type CoachCalendarEvent, eventDisplayDate, eventPeriodLabel } from '../../shared/calendarEvents';

/** The meeting a deck belongs to — plain fields, so learner and coach pages
 * with different event shapes open the same modal. */
export interface SlidesDeckTarget {
  /** The enrolment id: the slides API keys on the enrolment record. */
  learnerId: string;
  /** The day the meeting is held; the deck's window ends on it. */
  meetingDate: string;
  learnerName?: string | null;
  programme?: string | null;
  completed?: boolean;
  /** Shown before the pack loads, e.g. "Review 3". */
  periodLabel?: string;
}

/** A coach calendar row as a deck target. enrolmentId, never learnerId: an
 * event's learnerId is the profile id, and the two id sequences overlap. The
 * scheduled date wins over the planned target date, which can be months away. */
export function slidesTargetFromEvent(event: CoachCalendarEvent): SlidesDeckTarget {
  return {
    learnerId: event.enrolmentId || '',
    meetingDate: eventDisplayDate(event),
    learnerName: event.learner,
    programme: event.programme,
    completed: event.status === 'completed',
    periodLabel: eventPeriodLabel(event),
  };
}
