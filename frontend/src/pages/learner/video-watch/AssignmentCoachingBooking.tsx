import { occupiesBookingSlot } from '@/pages/learner/reviews/meetingBooking';
import { loadAssignmentTopicStates, type AssignmentTopicState } from '@/api/assignmentTopics';
import { BookingDatePicker } from './BookingDatePicker';
import { CoachMeetingArtifactsPanel } from '@/pages/coach/shared/CoachMeetingArtifactsPanel';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { LearnerKind } from '@/api/learnerDetail';
import { bookLearnerCalendarSession, fetchLearnerCalendarEvents, fetchLearnerMeetingArtifacts, fetchReviewSlots, learnerMeetingArtifactContentUrl, type LearnerCalendarResponse } from '@/api/learnerCalendar';
import { fetchReviewHistory, type ImportedReview } from '@/api/reviewHistory';
import { mcmBookingDateRestrictionMessage } from '../reviews/bookingDates';

const CLOSED_REVIEW_STATUSES = ['completed', 'cancelled', 'awaiting-signature'];

export function AssignmentCoachingBooking({ kind, learnerId, month, title, meetingKey, assignmentId, topicId, disabled, onSave, onSelect }: {
  kind: LearnerKind; learnerId: string; month: string; title: string; meetingKey: string; disabled: boolean;
  assignmentId?: string; topicId?: string;
  onSave: () => Promise<boolean>; onSelect: (key: string) => void;
}) {
  const loadArtifacts = useCallback((key: string, signal?: AbortSignal) => fetchLearnerMeetingArtifacts(kind, learnerId, key, signal), [kind, learnerId]);
  const contentUrl = useCallback((key: string, type: string, id: string, options: { preview?: boolean } = {}) => learnerMeetingArtifactContentUrl(kind, learnerId, key, type, id, options), [kind, learnerId]);
  const [calendar, setCalendar] = useState<LearnerCalendarResponse | null>(null);
  const [importedMcms, setImportedMcms] = useState<ImportedReview[]>([]);
  const [topicStates, setTopicStates] = useState<AssignmentTopicState[]>([]);
  const [bookedHere, setBookedHere] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [slotKey, setSlotKey] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [duration, setDuration] = useState(60);
  const [availableTimes, setAvailableTimes] = useState<string[]>([]);
  const [availabilityLoading, setAvailabilityLoading] = useState(false);
  const [availabilityError, setAvailabilityError] = useState('');
  const [busy, setBusy] = useState(false);
  const booking = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const latest = useRef({ kind, learnerId, month, assignmentId, topicId, onSelect });
  latest.current = { kind, learnerId, month, assignmentId, topicId, onSelect };
  const [reload, setReload] = useState(0);
  const [availabilityRetry, setAvailabilityRetry] = useState(0);
  // MCM appointments run from the 20th through the following 5th, subject to
  // the server's weekday, UK bank-holiday and past-date rules.
  const today = calendar?.bookingCalendar?.today || new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/London' });
  const lastYear = Math.max(Number(today.slice(0, 4)), ...(calendar?.bookingCalendar?.coveredYears || []));
  const selectableDates: string[] = [];
  for (const day = new Date(`${today}T12:00:00Z`); day.getUTCFullYear() <= lastYear; day.setUTCDate(day.getUTCDate() + 1)) {
    const value = day.toISOString().slice(0, 10);
    if (mcmBookingDateRestrictionMessage(value)) continue;
    if ([0, 6].includes(day.getUTCDay())) continue;
    if (calendar?.bookingCalendar?.bankHolidays.some(holiday => holiday.date === value)) continue;
    if (calendar?.bookingCalendar && !calendar.bookingCalendar.coveredYears.includes(day.getUTCFullYear())) continue;
    selectableDates.push(value);
  }
  const events = calendar?.events || [];
  const booked = events.filter(e => e.source === 'mcr' && (occupiesBookingSlot(e)
    || (['scheduled', 'in-progress', 'completed', 'awaiting-signature'].includes(e.status) && e.scheduledDate)));
  // Learners with imported Aptem MCMs get no Curriculum MCMs on their coach's
  // timetable, so only their Aptem MCMs can be booked here.
  const aptemSourced = importedMcms.some(review => review.aptemReviewId);
  const slots: { key: string; label: string; eventKey?: string; reviewId?: string }[] = aptemSourced
    ? importedMcms.filter(review => review.aptemReviewId && !review.completedDate && !CLOSED_REVIEW_STATUSES.includes(review.status)
        // Every imported MCM is also a calendar event; only a linked booking takes it.
        && !events.some(e => e.reviewId === review.id && e.calendarEventKey && occupiesBookingSlot(e)))
      .sort((a, b) => (a.plannedDate || '').localeCompare(b.plannedDate || ''))
      .map(review => ({ key: `imported-review:${review.id}`, reviewId: review.id,
        label: `MCM ${(review.plannedDate || '').slice(0, 7)} | Aptem plan ${review.plannedDate}` }))
    : events.filter(e => e.source === 'mcr' && e.status === 'not-scheduled' && !occupiesBookingSlot(e))
      .sort((a, b) => (a.targetDate || a.date || '').localeCompare(b.targetDate || b.date || ''))
      .map(e => ({ key: e.eventKey, eventKey: e.eventKey, label: `MCM ${(e.targetDate || e.date || '').slice(0, 7)} | ${e.coachName}` }));
  const slot = slots.find(e => e.key === slotKey);
  const needsSlot = slots.length > 0 || aptemSourced;
  const shared = topicId && !disabled ? topicStates
    .filter(state => state.topicId && state.topicId !== topicId && state.month === month)
    .map(state => booked.find(event => event.eventKey === state.meetingKey)).find(Boolean) : undefined;
  const selected = shared || booked.find(e => e.eventKey === (topicId ? bookedHere || meetingKey : meetingKey));
  const topicBooked = Boolean(topicId && selected);
  useEffect(() => {
    if (shared && shared.eventKey !== meetingKey) latest.current.onSelect(shared.eventKey);
  }, [shared, meetingKey]);
  useEffect(() => {
    let active = true;
    setLoading(true); setTopicStates([]); setBookedHere(''); setCalendar(null); setImportedMcms([]); setSlotKey(''); setDate(''); setDuration(60); setError(''); setNotice('');
    Promise.all([
      fetchLearnerCalendarEvents(kind, learnerId, { force: true }),
      fetchReviewHistory(kind, learnerId, 'monthly-coaching'),
      topicId && assignmentId ? loadAssignmentTopicStates({ learnerKind: kind, learnerId, activityId: assignmentId }) : Promise.resolve([]),
    ]).then(([result, history, topics]) => {
      // Without the imported list the right MCM source is unknown: stop rather
      // than offer a Curriculum slot an Aptem learner's coach cannot see.
      if (!Array.isArray(history?.reviews)) throw new Error('Could not load your Monthly Coaching Meetings.');
      if (active) { setCalendar(result); setImportedMcms(history.reviews); setTopicStates(topics); setLoading(false); }
    }).catch(e => { if (active) { setError(e.message || 'Could not load coaching meetings.'); setLoading(false); } });
    return () => { active = false; };
  }, [kind, learnerId, month, assignmentId, topicId, reload]);
  const restriction = () => {
    if (!date) return '';
    const parsed = new Date(`${date}T12:00:00`);
    if (!Number.isFinite(parsed.getTime())) return 'Choose a valid date.';
    if (date < today) return 'Sessions cannot be booked on a date that has already passed.';
    if ([0, 6].includes(parsed.getDay())) return 'Sessions cannot be booked on Saturdays or Sundays.';
    const holiday = calendar?.bookingCalendar?.bankHolidays.find(h => h.date === date);
    if (holiday) return `Sessions cannot be booked on UK bank holidays (${holiday.title}).`;
    if (calendar?.bookingCalendar && !calendar.bookingCalendar.coveredYears.includes(parsed.getFullYear())) return 'The bank-holiday calendar is not available for this year.';
    return mcmBookingDateRestrictionMessage(date);
  };
  const dateError = restriction();
  const bookingOffset = date ? (12 - Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', hourCycle: 'h23' }).format(new Date(`${date}T12:00:00Z`)))) * 60 : 0;
  useEffect(() => {
    setTime(''); setAvailableTimes([]); setAvailabilityError('');
    if (!date || dateError) { setAvailabilityLoading(false); return; }
    const controller = new AbortController();
    setAvailabilityLoading(true);
    fetchReviewSlots(kind, learnerId, date, duration, { signal: controller.signal })
      .then(times => { if (!controller.signal.aborted) setAvailableTimes(times); })
      .catch(error => { if (!controller.signal.aborted) setAvailabilityError(error.message || 'Could not load coach availability.'); })
      .finally(() => { if (!controller.signal.aborted) setAvailabilityLoading(false); });
    return () => controller.abort();
  }, [date, dateError, bookingOffset, kind, learnerId, duration, reload, availabilityRetry]);
  const book = async () => {
    if (disabled || topicBooked || booking.current || (needsSlot && !slot) || !date || !time || dateError || availabilityLoading || !availableTimes.includes(time)) return;
    booking.current = true; setBusy(true); setError(''); setNotice('');
    const identity = { kind, learnerId, month, assignmentId, topicId };
    try {
      if (!await onSave()) { setError('Save your assignment draft before booking.'); return; }
      if (!mounted.current || Object.keys(identity).some(k => identity[k as keyof typeof identity] !== latest.current[k as keyof typeof identity])) return;
      const result = await bookLearnerCalendarSession(kind, learnerId, {
        sessionType: 'mcr', bookingContext: 'monthly-assignment', eventKey: slot?.eventKey, ...(slot?.reviewId ? { reviewId: slot.reviewId } : {}), assignmentMonth: month, scheduledDate: date, scheduledTime: time,
        durationMinutes: duration, timezoneOffsetMinutes: bookingOffset, notes: `Monthly assignment: ${title}`,
      });
      if (!mounted.current || Object.keys(identity).some(k => identity[k as keyof typeof identity] !== latest.current[k as keyof typeof identity])) return;
      setCalendar(current => current ? { ...current, events: [...current.events.filter(e => e.eventKey !== result.event.eventKey), result.event] } : current);
      setBookedHere(result.event.eventKey);
      latest.current.onSelect(result.event.eventKey); setSlotKey(''); setDate(''); setTime(''); setAvailableTimes([]);
      setNotice(result.warning ? `Booking saved. ${result.warning}` : result.event.invited === false ? 'Booking saved, but the calendar invitation has not been sent. Contact your coach.' : 'Your Monthly Coaching Meeting is booked and linked to this assignment.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not book your meeting.'); }
    finally { booking.current = false; setBusy(false); }
  };
  const input = 'mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm disabled:bg-slate-50';
  return <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6">
    <div><h4 className="text-base font-semibold">Arrange your Monthly Coaching Meeting</h4><p className="mt-2 text-sm leading-6 text-slate-600">Use a booked MCM or book a monthly session with your assigned coach below. The same booking appears in your calendar and your coach's calendar.</p></div>
    {loading && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />Loading coaching meetings...</p>}
    {!loading && <>
      {!topicBooked && <label className="block">Use an existing coaching booking<select className={input} value={meetingKey} disabled={disabled || busy} onChange={e => { setBookedHere(e.target.value); onSelect(e.target.value); }}><option value="">Select a booked meeting</option>{booked.map(e => <option key={e.eventKey} value={e.eventKey}>{e.scheduledDate} {e.scheduledTime} ? {e.coachName}</option>)}</select></label>}
      {!booked.length && <p className="text-sm text-slate-500">You have no booked MCM yet.</p>}
      {selected && <div className="rounded-xl bg-blue-50 p-4 text-sm"><p>{topicBooked ? "MCM already booked" : "Linked meeting"}: {selected.scheduledDate} at {selected.scheduledTime} with {selected.coachName}.</p>{topicBooked && <p className="mt-2">This meeting is shared across all three topics. You only need to book once.</p>}{selected.invited === false && <p className="mt-2 text-amber-800">The meeting is saved, but the calendar invitation has not been sent. Contact your coach.</p>}</div>}
      {selected && <div className="space-y-3">
        <p className="text-sm leading-6 text-slate-600">Recording and transcription are enabled automatically using the live-session settings. The recording, transcript and attendance appear here once Teams has processed them after the meeting.</p>
        <CoachMeetingArtifactsPanel event={selected} storedOnly={selected.reviewSource === 'aptem'} fetchArtifacts={loadArtifacts} contentUrl={contentUrl} showAttendance />
      </div>}
      {!topicBooked && <fieldset disabled={disabled || busy || !calendar} className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:p-5">
        <legend className="px-2 text-sm font-semibold">Schedule an official MCM</legend>
        <>
          {slots.length > 0 && <label className="block">Monthly Coaching Meeting slot<select className={input} value={slotKey} onChange={e => setSlotKey(e.target.value)}><option value="">Select your programme MCM</option>{slots.map(e => <option key={e.key} value={e.key}>{e.label}</option>)}</select></label>}
          {aptemSourced && !slots.length && <p role="status" className="text-sm text-slate-600">No open Monthly Coaching Meeting is left in your programme plan. Contact your coach to arrange it.</p>}
          <p className="text-sm leading-6 text-slate-600">Choose an upcoming weekday from the 20th of a month through the 5th of the following month. Available times come from your coach's calendar and working hours, shown in UK time (Europe/London). Availability is checked again when you book.</p>
          <div className="grid gap-4 sm:grid-cols-2"><BookingDatePicker value={date} onChange={setDate} dates={selectableDates} /><label>Time<select className={input} value={time} disabled={availabilityLoading || !availableTimes.length} onChange={e => setTime(e.target.value)}><option value="">{availabilityLoading ? 'Loading available times...' : 'Select an available time'}</option>{availableTimes.map(value => <option key={value} value={value}>{value}</option>)}</select></label></div>
          <label className="block">Duration<select className={input} value={duration} onChange={e => { setDuration(Number(e.target.value)); setTime(''); }}>{[30, 45, 60, 90, 120].map(minutes => <option key={minutes} value={minutes}>{minutes === 60 ? '1 hour' : `${minutes} minutes`}</option>)}</select></label>
          {availabilityError && <p role="alert" className="text-sm text-red-700">{availabilityError} <button type="button" onClick={() => setAvailabilityRetry(value => value + 1)} className="underline">Retry availability</button></p>}
          {date && !dateError && !availabilityLoading && !availabilityError && !availableTimes.length && <p className="text-sm text-slate-600">Your coach has no available {duration}-minute appointments on this date. Choose another date or duration.</p>}
          {dateError && <p role="alert" className="text-sm text-red-700">{dateError}</p>}
          <button type="button" className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white disabled:opacity-40" disabled={(needsSlot && !slot) || !date || !time || Boolean(dateError) || availabilityLoading || !availableTimes.includes(time)} onClick={() => void book()}>{busy && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}{busy ? 'Booking MCM...' : `Book ${duration}-minute MCM`}</button>
        </>
      </fieldset>}
      <button type="button" disabled={busy} className="text-sm font-medium text-blue-700 underline" onClick={() => setReload(n => n + 1)}>Refresh meetings</button>
    </>}
    {notice && <p role="status" className="rounded-xl bg-blue-50 p-4 text-sm">{notice}</p>}
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
  </section>;
}
