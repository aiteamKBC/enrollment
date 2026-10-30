import { Component, useEffect, useId, useRef, useState, type ErrorInfo, type FormEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import type { LearnerKind } from '@/api/learnerDetail';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import {
  bookLearnerCalendarSession, fetchCalendarConnections, fetchLearnerCalendarEvents, fetchPersonalCalendarAvailability,
  fetchReviewSlots, rescheduleLearnerCalendarSession, ukOffsetForDate, type BookingCalendarRules, type BookSessionResponse,
  type CalendarBusySlot, type LearnerCalendarEvent, type PersonalCalendarConnection,
} from '@/api/learnerCalendar';
import { bookingDateRestrictionMessage, bookingInstant, bookingToday, firstAvailableBookingDate, mcmBookingDateRestrictionMessage, parseBookingDay } from './bookingDates';
import { aptemBookingSnapshot, editableBookingNotes, isAptemReview, occupiesBookingSlot } from './meetingBooking';
import styles from './meetingBooking.module.css';

/** A list card supplies identity and optional initial values. The dialog
 * resolves the full authorized calendar event before allowing a booking. */
export type MeetingBookingSession = Pick<LearnerCalendarEvent, 'id' | 'eventKey' | 'source'> & Partial<LearnerCalendarEvent>;

type Props = {
  session: MeetingBookingSession;
  title: string;
  learner: { kind: LearnerKind; id: string };
  rules: BookingCalendarRules | null;
  attendance?: MeetingAttendance;
  onClose: () => void;
  onBooked: (response: BookSessionResponse) => void;
};

function bookingBounds(date: string, time: string, durationMinutes: number) {
  const start = parseBookingDay(date) ? bookingInstant(date, time) : NaN;
  return Number.isFinite(start) ? { start, end: start + durationMinutes * 60_000 } : null;
}

function overlaps(start: number, end: number, otherStart: number, otherEnd: number) {
  return start < otherEnd && end > otherStart;
}

function hasExistingBooking(session: MeetingBookingSession, attendance?: MeetingAttendance) {
  return occupiesBookingSlot(session)
    && Boolean(session.calendarEventKey || attendance?.calendarEventKey || !session.eventKey.startsWith('imported-review:'));
}

/** Keeps a failure inside the booking form from taking the whole page down
 * (RouteErrorBoundary would otherwise replace the page). Mirrors that
 * boundary's pattern; the fallback is an inline dialog the learner can close. */
class BookingFormBoundary extends Component<{ children: ReactNode; onClose: () => void }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled error in the meeting booking form', error, info.componentStack);
  }

  render() {
    return this.state.failed ? <BookingFormFailure onClose={this.props.onClose} /> : this.props.children;
  }
}

function BookingFormFailure({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current!;
    const previousOverflow = document.body.style.overflow;
    if (!element.open) element.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
    };
  }, []);
  return createPortal(<dialog ref={dialog} aria-labelledby={titleId} className={styles.dialog}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className={styles.header}>
      <div><p className={styles.eyebrow}>Meeting booking</p><h2 id={titleId}>Booking form unavailable</h2></div>
      <button type="button" aria-label="Close booking dialog" className={styles.close} onClick={onClose}><AppIcon className="ri-close-line" /></button>
    </header>
    <p role="alert" className={styles.error}>Something went wrong in the booking form. Close this window and refresh the page to check your meeting before trying again.</p>
    <footer className={styles.footer}><button type="button" onClick={onClose}>Close</button></footer>
  </dialog>, document.body);
}

/** The compact Book session form shared by the two meeting lists. */
export default function MeetingBookingDialog(props: Props) {
  return <BookingFormBoundary onClose={props.onClose}><MeetingBookingForm {...props} /></BookingFormBoundary>;
}

function MeetingBookingForm({ session, title, learner, rules, attendance, onClose, onBooked }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  // Background list refreshes must not reset a day/time the learner is editing.
  const [initialBooking] = useState({ session, rules, attendance });
  const [target, setTarget] = useState<LearnerCalendarEvent | null>(null);
  const [calendarRules, setCalendarRules] = useState(rules);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  const [date, setDate] = useState(hasExistingBooking(session, attendance) ? session.scheduledDate! : firstAvailableBookingDate(session.scheduledDate || session.targetDate || session.date, rules, session.source === 'mcr'));
  const [time, setTime] = useState(session.scheduledTime || '09:00');
  const [duration, setDuration] = useState(String(hasExistingBooking(session, attendance) ? session.durationMinutes || 60 : 60));
  const [notes, setNotes] = useState(editableBookingNotes(session));
  const [calendarEvents, setCalendarEvents] = useState<LearnerCalendarEvent[]>([]);
  const [connections, setConnections] = useState<PersonalCalendarConnection[]>([]);
  const [busySlots, setBusySlots] = useState<CalendarBusySlot[]>([]);
  const [availabilityLoading, setAvailabilityLoading] = useState(false);
  const [availabilityError, setAvailabilityError] = useState('');
  const [coachSlots, setCoachSlots] = useState<string[]>([]);
  const [coachAvailabilityLoading, setCoachAvailabilityLoading] = useState(false);
  const [coachAvailabilityError, setCoachAvailabilityError] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);
  const edited = useRef({ date: false, time: false, duration: false, notes: false });
  const mounted = useRef(false);
  const monthly = session.source === 'mcr';
  const curriculumReview = session.source === 'review';
  const reviewBooking = monthly || curriculumReview || session.source === 'progress-review';
  const meetingType = monthly ? 'monthly coaching' : curriculumReview ? 'review' : 'progress review';
  const rescheduling = Boolean(target && hasExistingBooking(target, attendance));
  const isExisting = target ? rescheduling : hasExistingBooking(session, attendance);
  const dateRestriction = (!date && !loading ? 'No permitted date is available in the supported booking calendar. Choose a supported date or contact your programme team.' : '') || bookingDateRestrictionMessage(date, calendarRules) || (monthly ? mcmBookingDateRestrictionMessage(date) : '');

  useEffect(() => {
    mounted.current = true;
    const element = dialog.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      mounted.current = false;
      element.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  useEffect(() => {
    const { session, rules, attendance } = initialBooking;
    let cancelled = false;
    setLoading(true);
    setLoadError('');
    void Promise.all([
      fetchLearnerCalendarEvents(learner.kind, learner.id, { revalidate: true }),
      fetchCalendarConnections(learner.kind, learner.id).catch(() => ({ connections: [] })),
    ]).then(([calendar, calendarConnections]) => {
      if (cancelled) return;
      const reviewId = session.importedReview?.id || session.reviewId;
      const allowedTypes = session.source === 'mcr' ? ['monthly coaching meeting', 'monthly coaching', 'mcm'] : ['progress review', 'progress review (+ skills radar)'];
      if (session.importedReview && !allowedTypes.includes(session.importedReview.type.trim().toLowerCase())) {
        throw new Error('Choose a Monthly Coaching Meeting or Progress Review to schedule.');
      }
      const durableKey = session.calendarEventKey || attendance?.calendarEventKey || session.eventKey;
      // Resolve only this learner's exact booking. Dates are not identities.
      const candidates = calendar.events.filter(event => event.source === session.source);
      const saved = candidates.find(event => event.eventKey === durableKey || event.calendarEventKey === durableKey)
        || candidates.find(event => Boolean(reviewId && event.reviewId === reviewId));
      if (!saved) throw new Error('This meeting is no longer available. Close this window and refresh the page.');
      if (saved && !['scheduled', 'not-scheduled'].includes(saved.status)) {
        throw new Error('This meeting can no longer be scheduled. Close this window and refresh the page.');
      }
      const resolved = saved;
      const nextRules = calendar.bookingCalendar || rules;
      setTarget(resolved);
      setCalendarRules(nextRules);
      setCalendarEvents(calendar.events);
      setConnections(Array.isArray(calendarConnections?.connections) ? calendarConnections.connections : []);
      if (!edited.current.date) setDate(hasExistingBooking(resolved, attendance) ? resolved.scheduledDate! : firstAvailableBookingDate(resolved.scheduledDate || resolved.targetDate || resolved.date, nextRules, resolved.source === 'mcr'));
      if (!edited.current.time) setTime(resolved.scheduledTime || '09:00');
      if (!edited.current.duration) setDuration(String(hasExistingBooking(resolved, attendance) ? resolved.durationMinutes || 60 : 60));
      if (!edited.current.notes) setNotes(editableBookingNotes(resolved));
    }).catch(reason => {
      if (!cancelled) setLoadError(reason instanceof Error ? reason.message : 'Could not load this booking. Please try again.');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [learner.kind, learner.id, initialBooking, revision]);

  useEffect(() => {
    // An unparseable day (e.g. a five-digit year typed into the date field)
    // has no instant; `toISOString()` would throw and blank the page. The
    // inline date restriction already tells the learner to fix it.
    const day = parseBookingDay(date);
    if (!connections.length || !day) {
      setBusySlots([]);
      setAvailabilityError('');
      return;
    }
    const nextDay = new Date(Date.parse(`${date}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    const start = new Date(bookingInstant(date, '00:00')).toISOString();
    const end = new Date(bookingInstant(nextDay, '00:00')).toISOString();
    let cancelled = false;
    setAvailabilityLoading(true);
    setAvailabilityError('');
    void fetchPersonalCalendarAvailability(learner.kind, learner.id, start, end)
      .then(result => {
        if (cancelled) return;
        setBusySlots(Array.isArray(result?.busy) ? result.busy : []);
        if (!Array.isArray(result?.busy) || result.errors?.length) setAvailabilityError('Some connected calendars could not be checked. The server will check again before booking.');
      })
      .catch(() => {
        if (!cancelled) {
          setBusySlots([]);
          setAvailabilityError('Calendar availability could not be refreshed. The server will check again before booking.');
        }
      })
      .finally(() => { if (!cancelled) setAvailabilityLoading(false); });
    return () => { cancelled = true; };
  }, [connections.length, date, learner.kind, learner.id]);

  useEffect(() => {
    let cancelled = false;
    setCoachSlots([]);
    setCoachAvailabilityError('');
    if (!reviewBooking || !target || !parseBookingDay(date) || dateRestriction) {
      setCoachAvailabilityLoading(false);
      return;
    }
    const requestedDuration = Number(duration);
    const eventKey = rescheduling
      ? target.calendarEventKey || attendance?.calendarEventKey || target.eventKey
      : undefined;
    const controller = new AbortController();
    setCoachAvailabilityLoading(true);
    void fetchReviewSlots(learner.kind, learner.id, date, requestedDuration, {
      excludeEventKey: eventKey, signal: controller.signal,
    }).then(times => {
      if (!cancelled) {
        setCoachSlots(times);
        setTime(current => times.includes(current) ? current : '');
      }
    }).catch(reason => {
      if (!cancelled) setCoachAvailabilityError(reason instanceof Error
        ? reason.message : 'Coach availability could not be confirmed. Retry before booking.');
    }).finally(() => {
      if (!cancelled) setCoachAvailabilityLoading(false);
    });
    return () => { cancelled = true; controller.abort(); };
  }, [reviewBooking, target, date, dateRestriction, duration, rescheduling, session, attendance, monthly, learner.kind, learner.id, revision]);

  const requested = bookingBounds(date, time, Number(duration));
  const lmsConflict = requested ? calendarEvents.find(event => {
    if (event.eventKey === target?.eventKey || event.id === target?.id || !occupiesBookingSlot(event) || !event.scheduledDate || !event.scheduledTime) return false;
    const existing = bookingBounds(event.scheduledDate, event.scheduledTime, event.durationMinutes || 60);
    return existing ? overlaps(requested.start, requested.end, existing.start, existing.end) : false;
  }) : null;
  // Match the Calendar page/server exclusion: moving this appointment must
  // not conflict with its existing personal-calendar copy.
  const currentBooking = rescheduling && target?.scheduledDate && target.scheduledTime
    ? bookingBounds(target.scheduledDate, target.scheduledTime, target.durationMinutes || 60) : null;
  const personalConflict = requested ? busySlots.find(slot => {
    const start = Date.parse(slot.start), end = Date.parse(slot.end);
    if (currentBooking && start === currentBooking.start && end === currentBooking.end) return false;
    return Number.isFinite(start) && Number.isFinite(end) && overlaps(requested.start, requested.end, start, end);
  }) : null;
  const bookingConflict = lmsConflict || personalConflict;
  const reviewAvailabilityTimes = coachSlots.filter(slot => {
    const interval = bookingBounds(date, slot, Number(duration));
    if (!interval) return false;
    const learnerCalendarConflict = calendarEvents.some(event => {
      if (event.eventKey === target?.eventKey || event.id === target?.id || !occupiesBookingSlot(event) || !event.scheduledDate || !event.scheduledTime) return false;
      const existing = bookingBounds(event.scheduledDate, event.scheduledTime, event.durationMinutes || 60);
      return existing ? overlaps(interval.start, interval.end, existing.start, existing.end) : false;
    });
    const connectedCalendarConflict = busySlots.some(other => {
      const start = Date.parse(other.start), end = Date.parse(other.end);
      if (currentBooking && start === currentBooking.start && end === currentBooking.end) return false;
      return Number.isFinite(start) && Number.isFinite(end) && overlaps(interval.start, interval.end, start, end);
    });
    return !learnerCalendarConflict && !connectedCalendarConflict;
  });

  function close() { if (!submitting.current) onClose(); }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || loading || availabilityLoading || coachAvailabilityLoading || loadError
      || (reviewBooking && (coachAvailabilityError || !reviewAvailabilityTimes.includes(time)))
      || (reviewBooking && connections.length > 0 && availabilityError)
      || bookingConflict || !target || !date || !time) return;
    if (dateRestriction) { setError(dateRestriction); return; }
    submitting.current = true;
    setSaving(true);
    setError('');
    try {
      const reviewId = session.importedReview?.id || target.reviewId;
      const eventKey = target.calendarEventKey || attendance?.calendarEventKey || target.eventKey;
      // An imported review may have a scheduled date before its first LMS
      // booking. Only a durable calendar key can be sent to reschedule.
      const update = rescheduling;
      const imported = isAptemReview(target);
      const input = {
        scheduledDate: date,
        scheduledTime: time,
        durationMinutes: Number(duration) || 60,
        notes: update && imported ? notes.trim() : notes.trim() || undefined,
        timezoneOffsetMinutes: ukOffsetForDate(date),
      };
      const response = update
        ? await rescheduleLearnerCalendarSession(learner.kind, learner.id, {
          ...input, eventKey, reviewId,
          ...(imported ? { expectedBooking: aptemBookingSnapshot(target) } : {}),
        })
        : await bookLearnerCalendarSession(learner.kind, learner.id, {
          ...input, sessionType: monthly ? 'mcr' : curriculumReview ? 'review' : 'progress-review',
          eventKey: reviewId ? undefined : eventKey, reviewId,
          assignmentMonth: reviewId ? target.assignmentMonth || (session.targetDate || session.date || date).slice(0, 7) : undefined,
        });
      if (mounted.current) onBooked(response);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not save this booking. Please try again.');
    } finally {
      submitting.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  const plannedDate = session.targetDate || session.date;
  const sessionLabel = `${monthly ? 'Monthly Coaching Meeting' : curriculumReview ? (session.reviewTypeName || session.title || 'Review') : 'Progress Review'}${plannedDate ? ` · ${new Date(`${plannedDate}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : ''}`;
  const calendarHref = `/learner/calendar?kind=${encodeURIComponent(learner.kind)}&learner=${encodeURIComponent(learner.id)}&connect=calendar`;
  const canSubmit = !loading && !saving && !availabilityLoading && !coachAvailabilityLoading && !loadError
    && (!reviewBooking || (!coachAvailabilityError && reviewAvailabilityTimes.includes(time)
      && !(connections.length > 0 && availabilityError)))
    && !dateRestriction && !bookingConflict && Boolean(target && date && time);
  return createPortal(<dialog ref={dialog} aria-labelledby={titleId} aria-describedby={descriptionId} className={styles.dialog}
    onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <form onSubmit={submit} aria-busy={loading || saving}>
      <header className={styles.header}>
        <div><p className={styles.eyebrow}>{monthly ? 'Monthly coaching booking' : curriculumReview ? 'Curriculum review booking' : 'Progress review booking'}</p>
          <h2 id={titleId}>{isExisting ? 'Reschedule' : 'Book'} {meetingType}</h2>
          <p id={descriptionId} className={styles.description}>Choose the day and time for {title}.</p></div>
        <button type="button" aria-label="Close booking dialog" className={styles.close} onClick={close} disabled={saving}><AppIcon className="ri-close-line" /></button>
      </header>
      <label className={styles.session}>Session<select aria-label="Session" value={session.id} onChange={() => undefined} disabled={saving}>
        <option value={session.id}>{sessionLabel}</option>
      </select></label>
      {rescheduling && target && <p role="note" className={styles.description}>Current booking: {target.scheduledDate} at {target.scheduledTime} UK time · {target.durationMinutes || 60} minutes. Select an available replacement below.</p>}
      <div className={styles.fields}>
        <label>Date<input aria-label="Day" type="date" required min={bookingToday()} value={date} disabled={saving || Boolean(loadError)} onChange={event => { edited.current.date = true; setDate(event.target.value); setTime(''); setError(''); }} /></label>
        {reviewBooking ? <label>Time (UK time · Europe/London)<select aria-label="Time" required value={time} disabled={saving || Boolean(loadError) || coachAvailabilityLoading || Boolean(coachAvailabilityError) || Boolean(connections.length && availabilityError) || !reviewAvailabilityTimes.length} onChange={event => { edited.current.time = true; setTime(event.target.value); setError(''); }}>
          <option value="">{coachAvailabilityLoading ? 'Checking coach availability…' : 'Select an available time'}</option>
          {reviewAvailabilityTimes.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label> : <label>Time<input type="time" required value={time} disabled={saving || Boolean(loadError)} onChange={event => { edited.current.time = true; setTime(event.target.value); setError(''); }} /></label>}
      </div>
      <p className={styles.description}>All booking times are UK time (Europe/London), including daylight saving time.</p>
      <div className={`${styles.calendarCheck} ${bookingConflict ? styles.calendarConflict : ''}`}>
        <AppIcon className="ri-calendar-line" />
        <div>
          {bookingConflict
            ? <p>This time conflicts with {lmsConflict ? `“${lmsConflict.title}”` : 'an event in your personal calendar'}.</p>
            : !connections.length
            ? <Link to={calendarHref}>Connect your personal calendar to prevent booking conflicts</Link>
            : availabilityLoading
              ? <p role="status">Checking your personal calendar…</p>
              : availabilityError && isAptemReview(target || session)
                ? <p>Personal calendar availability could not be confirmed.</p>
                : <p>This time is available in your calendar.</p>}
          {connections.length > 0 && <small>{connections.length} personal calendar{connections.length === 1 ? '' : 's'} connected</small>}
        </div>
      </div>
      <label className={styles.duration}>Duration
        <select aria-label="Duration" value={duration} disabled={saving || Boolean(loadError)} onChange={event => { edited.current.duration = true; setDuration(event.target.value); setTime(''); setError(''); }}>
          {[30, 45, 60, 90, 120].map(minutes => <option key={minutes} value={minutes}>{minutes < 60 ? `${minutes} minutes` : minutes === 60 ? '1 hour' : `${minutes / 60} hours`}</option>)}
          {![30, 45, 60, 90, 120].includes(Number(duration)) && <option value={duration}>{duration} minutes</option>}
        </select>
      </label>
      <label className={styles.notes}>What would you like to cover? <span>(optional)</span>
        <textarea maxLength={500} value={notes} disabled={saving || Boolean(loadError)} onChange={event => { edited.current.notes = true; setNotes(event.target.value); }} placeholder="Add anything your coach should know before the session…" />
        <small>{notes.length}/500</small>
      </label>
      {loadError && <p role="alert" className={styles.error}>{loadError} <button type="button" onClick={() => setRevision(value => value + 1)}>Try again</button></p>}
      {coachAvailabilityLoading && <p role="status">Checking coach availability for this date and duration…</p>}
      {coachAvailabilityError && <p role="alert" className={styles.error}>{coachAvailabilityError} <button type="button" onClick={() => setRevision(value => value + 1)}>Retry availability</button></p>}
      {!coachAvailabilityLoading && !coachAvailabilityError && reviewBooking && target && date && !dateRestriction && !reviewAvailabilityTimes.length && <p role="status">No available Review times remain on this date. Choose another date.</p>}
      {availabilityError && connections.length > 0 && reviewBooking && <p role="alert" className={styles.error}>{availabilityError} <button type="button" onClick={() => setRevision(value => value + 1)}>Retry calendar check</button></p>}
      {!loading && !loadError && (error || dateRestriction || availabilityError) && <p role="alert" className={styles.error}>{error || dateRestriction || availabilityError}</p>}
      {error.toLowerCase().includes('refresh') && isAptemReview(target || session) && <button type="button" disabled={saving || loading}
        onClick={() => { setError(''); setRevision(value => value + 1); }}>Refresh booking</button>}
      <footer className={styles.footer}>
        <button type="button" onClick={close} disabled={saving}>Cancel</button>
        <button type="submit" aria-label={saving ? 'Saving' : isExisting ? 'Save new time' : 'Book session'} className={styles.primary} disabled={!canSubmit}>{saving ? 'Saving…' : isExisting ? 'Save new time' : 'Send request'}</button>
      </footer>
    </form>
  </dialog>, document.body);
}
