import { useEffect, useState, type MutableRefObject } from 'react';
import { bookLearnerCalendarSession, fetchCatchupSlots, fetchLearnerCalendarEvents, ukOffsetForDate, type BookingCalendarRules, type LearnerCalendarEvent } from '@/api/learnerCalendar';
import type { MissedAttendanceSession } from '@/api/absenceReports';
import { useMyLearner } from '@/hooks/useMyLearner';
import styles from '../attendance.module.css';

/** Books the chosen time for a parent that submits the booking with its own button.
 * `linkReportId` links the new booking to that absence report in the same server request. */
export type CatchupBookAction = (linkReportId?: number) => Promise<LearnerCalendarEvent | null>;

export default function CatchupBooking({ lecture, selectedKey, onSelect, onBooked, onBusyChange, disabled = false, standalone = false,
  bookRef, onDraftChange, reportId, onLinked }: {
  lecture: MissedAttendanceSession;
  selectedKey: string;
  onSelect: (event: LearnerCalendarEvent | null) => void;
  onBooked?: (event: LearnerCalendarEvent) => Promise<boolean>;
  onBusyChange: (busy: boolean) => void;
  disabled?: boolean;
  standalone?: boolean;
  /** Set when a parent form books and submits in one step; hides this component's own Book button. */
  bookRef?: MutableRefObject<CatchupBookAction | null>;
  /** Whether a bookable time is chosen, so the parent can enable its button. */
  onDraftChange?: (ready: boolean) => void;
  /** The absence report being made up; a catch-up linked to another report is not offered. */
  reportId?: number;
  /** With `reportId`: a new booking is linked to that report by the server in the same request. */
  onLinked?: (event: LearnerCalendarEvent) => void;
}) {
  const learner = useMyLearner();
  const [events, setEvents] = useState<LearnerCalendarEvent[]>([]);
  const [rules, setRules] = useState<BookingCalendarRules>();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [duration, setDuration] = useState('30');
  const [slots, setSlots] = useState<string[]>([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsError, setSlotsError] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const today = rules?.today || new Date().toLocaleDateString('en-CA');
  const earliestDate = lecture.dateIso > today ? lecture.dateIso : today;
  const selectedDay = date ? new Date(`${date}T12:00:00`) : null;
  const selectedHoliday = rules?.bankHolidays.find(day => day.date === date);
  const dateRestriction = !selectedDay || !Number.isFinite(selectedDay.getTime()) ? ''
    : date < earliestDate ? 'Choose a date on or after the lecture date.'
      : [0, 6].includes(selectedDay.getDay()) ? 'Catch-up sessions cannot be booked on Saturdays or Sundays.'
        : selectedHoliday ? `Catch-up sessions cannot be booked on ${selectedHoliday.title}.`
          : rules && !rules.coveredYears.includes(selectedDay.getFullYear()) ? 'Booking is not available for this year.' : '';
  const available = events.filter(event => event.source === 'catch-up'
    // One catch-up makes up one lecture: hide bookings already linked to another absence.
    && (!event.linkedReportId || event.linkedReportId === reportId)
    && ['scheduled', 'not-scheduled', 'in-progress'].includes(event.status)
    && event.scheduledDate && event.scheduledDate >= earliestDate && event.scheduledTime
    && new Date(`${event.scheduledDate}T${event.scheduledTime}`).getTime() > Date.now());
  const selected = available.find(event => event.eventKey === selectedKey);
  const draftReady = Boolean(!selected && date && time && !dateRestriction && slots.includes(time));

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError('');
    fetchLearnerCalendarEvents(learner.kind, learner.id, { revalidate: true }).then(result => {
      if (cancelled) return;
      setEvents(result.events);
      setRules(result.bookingCalendar);
    }).catch(reason => {
      if (!cancelled) setLoadError(reason instanceof Error ? reason.message : 'Could not load catch-up bookings.');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [learner.kind, learner.id, revision]);

  useEffect(() => {
    if (!loading && !loadError && selectedKey && !selected) onSelect(null);
  }, [loading, loadError, selectedKey, selected, onSelect]);

  // Only times the coach is free: their Outlook working hours and calendar, and their other bookings.
  useEffect(() => {
    setTime('');
    setSlots([]);
    setSlotsError('');
    if (!date || dateRestriction) return;
    const controller = new AbortController();
    setSlotsLoading(true);
    fetchCatchupSlots(learner.kind, learner.id, date, Number(duration), controller.signal)
      .then(times => setSlots(times))
      .catch(reason => { if (!controller.signal.aborted) setSlotsError(reason instanceof Error ? reason.message : 'Could not check your coach’s calendar.'); })
      .finally(() => { if (!controller.signal.aborted) setSlotsLoading(false); });
    return () => controller.abort();
  }, [learner.kind, learner.id, date, duration, dateRestriction, revision]);

  useEffect(() => { onDraftChange?.(draftReady); }, [draftReady, onDraftChange]);

  const book: CatchupBookAction = async (linkReportId) => {
    const linkTo = linkReportId ?? (onLinked ? reportId : undefined);
    // A parent booking for the report it just saved calls this from the same submit,
    // while its own "saving" state still disables the fields; that call goes ahead.
    if (busy || (disabled && linkReportId === undefined)) return null;
    setError('');
    setNotice('');
    if (!draftReady) { setError('Choose a date and one of your coach’s available times.'); return null; }
    setBusy(true); onBusyChange(true);
    try {
      const result = await bookLearnerCalendarSession(learner.kind, learner.id, {
        sessionType: 'catch-up', scheduledDate: date, scheduledTime: time, durationMinutes: Number(duration),
        // Coach bookings are stored in UK time, which is what the available times are listed in.
        timezoneOffsetMinutes: ukOffsetForDate(date),
        ...(linkTo ? { absenceReportId: linkTo } : {}),
        notes: `Catch-up for lecture: ${lecture.title}\nLecture date: ${lecture.dateIso}`,
      });
      if (!result.event?.eventKey || result.event.source !== 'catch-up'
        || !['scheduled', 'not-scheduled', 'in-progress'].includes(result.event.status)
        || !result.event.scheduledDate || !result.event.scheduledTime) throw new Error('The session was not booked. Please refresh your bookings before retrying.');
      setEvents(current => [...current.filter(event => event.eventKey !== result.event.eventKey), result.event]);
      onSelect(result.event);
      if (linkTo) {
        if (result.linkedReportId !== linkTo) throw new Error('The catch-up was not linked to your absence report. Please refresh before retrying.');
        setNotice('Catch-up session booked and linked to your absence report.');
        onLinked?.(result.event);
        return result.event;
      }
      const reportLinked = onBooked ? await onBooked(result.event) : false;
      setNotice(result.warning || (reportLinked
        ? 'Catch-up session booked and linked to your absence report.'
        : `Catch-up session booked.${standalone || bookRef ? '' : ' Submit your absence report to link it to this lecture.'}`));
      return result.event;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not book the catch-up session.');
      // A time taken meanwhile: show the coach's current free times.
      setRevision(value => value + 1);
      return null;
    } finally { setBusy(false); onBusyChange(false); }
  };
  if (bookRef) bookRef.current = book;

  return <div className={styles.catchupBooking} aria-busy={busy || loading}>
    <div className={styles.bookingHeading}><strong>Your catch-up session</strong><button type="button" disabled={busy || loading || disabled} onClick={() => setRevision(value => value + 1)}>Refresh bookings</button></div>
    {loading && <p role="status">Loading your bookings…</p>}
    {loadError && <p role="alert">{loadError}</p>}
    {!loading && available.length > 0 && <label>Use an existing catch-up booking
      <select aria-label="Catch-up booking" disabled={busy || disabled} value={selected?.eventKey || ''} onChange={event => { setNotice(''); onSelect(available.find(item => item.eventKey === event.target.value) || null); }}>
        <option value="">Choose a session or book below</option>
        {available.map(event => <option key={event.eventKey} value={event.eventKey}>{event.scheduledDate} at {event.scheduledTime} · {event.coachName || 'Your coach'}{event.status === 'not-scheduled' ? ' (Legacy pending request)' : ''}</option>)}
      </select>
    </label>}
    {loading ? null : selected ? <p className={styles.bookingConfirmation}>{selected.scheduledDate} at {selected.scheduledTime} · {selected.durationMinutes} minutes{selected.status === 'not-scheduled' ? ' · Existing pending request' : ' · Booked'}</p> : <>
      <p>Choose a date and one of your coach’s available times. The booking is confirmed immediately.</p>
      <div className={styles.bookingFields}>
        <label>Date<input aria-label="Catch-up date" aria-invalid={Boolean(dateRestriction)} type="date" min={earliestDate} value={date} disabled={busy || disabled} onChange={event => { setDate(event.target.value); setError(''); }} /></label>
        <label>Time (UK)<select aria-label="Catch-up time" value={time} disabled={busy || disabled || !date || Boolean(dateRestriction) || slotsLoading || !slots.length} onChange={event => { setTime(event.target.value); setError(''); }}>
          <option value="">{!date ? 'Choose a date first' : slotsLoading ? 'Checking…' : slots.length ? 'Choose a time' : 'No free times'}</option>
          {slots.map(slot => <option key={slot} value={slot}>{slot}</option>)}
        </select></label>
        <label>Duration<select aria-label="Catch-up duration" value={duration} disabled={busy || disabled} onChange={event => setDuration(event.target.value)}><option value="30">30 minutes</option><option value="60">60 minutes</option></select></label>
      </div>
      {dateRestriction && <p role="alert">{dateRestriction}</p>}
      {slotsLoading && <p role="status">Checking your coach’s calendar…</p>}
      {slotsError && <p role="alert">{slotsError}</p>}
      {date && !dateRestriction && !slotsLoading && !slotsError && !slots.length && <p role="status">Your coach has no free time on this day. Choose another date.</p>}
      {!bookRef && <button type="button" className={`primary-action ${styles.bookCatchupButton}`} disabled={busy || loading || disabled || !draftReady} onClick={() => { void book(); }}>{busy ? 'Booking…' : 'Book Catch-up Session'}</button>}
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
