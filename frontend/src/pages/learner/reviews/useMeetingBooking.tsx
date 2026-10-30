import { useState, type Dispatch, type SetStateAction } from 'react';
import type { LearnerKind } from '@/api/learnerDetail';
import type { BookingCalendarRules, LearnerCalendarEvent } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import MeetingBookingDialog from './MeetingBookingDialog';
import { meetingBookingWarning } from './meetingBooking';
import { BookingErrorBoundary } from '@/components/feature/BookingErrorBoundary';

export function useMeetingBooking({ learner, rules, attendance, titleOf, setEvents, refresh }: {
  learner: { kind: LearnerKind; id: string };
  rules: BookingCalendarRules | null;
  attendance: MeetingAttendance[];
  titleOf: (session: LearnerCalendarEvent) => string;
  setEvents: Dispatch<SetStateAction<LearnerCalendarEvent[]>>;
  refresh: () => void;
}) {
  const [session, setSession] = useState<LearnerCalendarEvent | null>(null);
  const [notice, setNotice] = useState('');
  const [warning, setWarning] = useState('');
  const dialog = session && <BookingErrorBoundary resetKey={session.id} onClose={() => setSession(null)}>
    <MeetingBookingDialog key={session.id} session={session} title={titleOf(session)} learner={learner}
    rules={rules} attendance={attendance.find(item => item.id === session.id || (session.reviewId && item.id === `imported-review:${session.reviewId}`))
      || attendance.find(item => Boolean(item.calendarEventKey) && item.calendarEventKey === (session.calendarEventKey || session.eventKey))} onClose={() => setSession(null)}
    onBooked={response => {
      const saved = response.event;
      setEvents(current => [...current.filter(item => item.id !== session.id && item.eventKey !== saved.eventKey),
        session.reviewSource === 'aptem' || session.importedReview
          ? { ...session, ...saved, id: session.id, eventKey: session.eventKey, calendarEventKey: saved.calendarEventKey || saved.eventKey,
              reviewSource: 'aptem', reviewId: session.reviewId, aptemReviewId: session.aptemReviewId,
              sequence: session.sequence, importedReview: session.importedReview }
          : saved]);
      setWarning(meetingBookingWarning(saved, response.warning));
      setNotice(response.approvalRequired ? 'Your booking request has been sent for approval.' : 'Your meeting time has been saved.');
      setSession(null);
      refresh();
    }} />
  </BookingErrorBoundary>;
  return { openBooking: (selected: LearnerCalendarEvent) => { setNotice(''); setWarning(''); setSession(selected); }, dialog,
    notice: (notice || warning) && <div role="status" className={`rounded-xl border p-3 text-sm ${warning ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{warning || notice}</div> };
}
