import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, CalendarCheck2, CalendarDays, CheckCircle2, ExternalLink, Send } from 'lucide-react';
import styles from '../coach-booking/booking.module.css';
import form from './lmsIntroduction.module.css';
import { SYSTEM_TIME_ZONE } from '@/lib/format';
import { getLmsIntroduction, getLmsIntroductionTimes, LmsIntroductionError, requestLmsIntroduction, slotLabel, type LmsIntroductionState } from '@/api/lmsIntroduction';

// Today in the business time zone, as the YYYY-MM-DD a date input expects.
const businessToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: SYSTEM_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export default function LmsIntroductionPage() {
  const token = useSearchParams()[0].get('token') ?? '';
  const [state, setState] = useState<LmsIntroductionState | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [note, setNote] = useState('');
  const [freeTimes, setFreeTimes] = useState<string[] | null>(null);
  const [timesLoading, setTimesLoading] = useState(false);
  const [timesError, setTimesError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError(''); setState(null);
    getLmsIntroduction(token, controller.signal).then(found => {
      setState(found);
      setDate(found.request?.date ?? ''); setTime(found.request?.time ?? ''); setNote(found.request?.note ?? '');
    }).catch(reason => { if (!controller.signal.aborted) setLoadError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [token, reload]);

  // Only the times the case owner is actually free are offered for a day.
  const booked = state?.request?.status === 'scheduled';
  useEffect(() => {
    setFreeTimes(null); setTimesError('');
    if (!state || booked || !date) return;
    const controller = new AbortController();
    setTimesLoading(true);
    getLmsIntroductionTimes(token, date, controller.signal).then(found => {
      setFreeTimes(found.times);
      setTime(current => (found.times.includes(current) ? current : ''));
    }).catch(reason => { if (!controller.signal.aborted) setTimesError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setTimesLoading(false); });
    return () => controller.abort();
  }, [token, date, state, booked]);

  const send = (body: { date: string; time: string; note: string }) => {
    if (saving) return;
    setSaving(true); setSaveError('');
    requestLmsIntroduction(token, body).then(setState)
      .catch(reason => {
        setSaveError(reason.message);
        // Already booked: show the booking instead of the form.
        if (reason instanceof LmsIntroductionError && reason.state) setState(reason.state);
      })
      .finally(() => setSaving(false));
  };
  const submit = (event: FormEvent) => { event.preventDefault(); send({ date, time, note }); };

  const request = state?.request;
  const owner = state?.caseOwner || 'your case owner';
  const legacyRequest = request?.status === 'requested';
  return <main className={styles.page}>
    <div className={styles.container}>
      <div className={styles.brandBar}><img src="/assets/kbc-logo.png" alt="Kent Business College" width="175" height="80" /><span><CalendarDays size={17} aria-hidden="true" />LMS introduction</span></div>
      <header className={styles.hero}>
        <div className={styles.heroCopy}><p className={styles.eyebrow}>WELCOME TO THE PLATFORM</p><h1>{state ? `Book your LMS introduction with ${owner}` : 'Book your LMS introduction'}</h1><p>A short one-to-one meeting on Microsoft Teams where your case owner shows you around the learning platform. Choose a time when they are free and the Teams invitation is sent to your email straight away.</p><span className={styles.accessNote}><CheckCircle2 size={16} aria-hidden="true" />No password needed to book</span></div>
        <div className={styles.heroIcon} aria-hidden="true"><CalendarCheck2 strokeWidth={1.3} /></div>
      </header>
      {loading && <p role="status" className={styles.message}>Loading your booking...</p>}
      {loadError && <div role="alert" className={styles.message}>{loadError}<button type="button" className={styles.retry} onClick={() => setReload(value => value + 1)}>Retry</button></div>}
      {state && <section className={styles.booking} aria-label="LMS introduction booking">
        {request && booked ? <div className={styles.empty}>
          <span className={styles.emptyIcon}>{request.inviteSent ? <CalendarCheck2 size={30} aria-hidden="true" /> : <AlertTriangle size={30} aria-hidden="true" />}</span>
          <h2>{request.inviteSent ? 'Your LMS introduction is booked' : 'Your booking is saved'}</h2>
          <p className={form.summary}>{slotLabel(request.date, request.time)}</p>
          {request.inviteSent
            ? <p>It is in {owner}'s Teams calendar, and the Microsoft Teams invitation has been sent to your email. To change it, reply to that invitation or contact {owner}.</p>
            : <>
              <p role="alert" className={form.error}>{state.warning || 'The Microsoft Teams invitation has not been sent yet.'}</p>
              <p><button type="button" className={form.secondary} disabled={saving} onClick={() => send({ date: request.date ?? '', time: request.time ?? '', note: request.note })}>{saving ? 'Sending...' : 'Send the Teams invitation again'}</button></p>
            </>}
          {request.meetingLink && <p><a className={styles.openBooking} href={request.meetingLink} target="_blank" rel="noopener noreferrer">Open the Teams meeting<ExternalLink size={17} aria-hidden="true" /></a></p>}
          {saveError && <p role="alert" className={form.error}>{saveError}</p>}
        </div> : <form className={form.form} onSubmit={submit}>
          <div className={styles.sectionHeading}><span className={styles.stepNumber}>01</span><div><h2>Choose a time</h2><p>{state.durationMinutes} minutes with {owner} on Microsoft Teams, Monday to Friday. Times are UK time.</p></div></div>
          {legacyRequest && <p className={form.hint}>You asked for {slotLabel(request?.date ?? null, request?.time ?? null)}. Choose a time below to book it now.</p>}
          <div className={form.fields}>
            <label className={form.field}>Date<input type="date" required min={businessToday()} value={date} onChange={event => setDate(event.target.value)} /></label>
            <label className={form.field}>Start time<select required value={time} disabled={!freeTimes || freeTimes.length === 0} onChange={event => setTime(event.target.value)}>
              <option value="">{!date ? 'Choose a date first' : timesLoading ? 'Checking availability...' : freeTimes && freeTimes.length === 0 ? 'No free times this day' : 'Choose a time'}</option>
              {(freeTimes ?? []).map(option => <option key={option} value={option}>{option}</option>)}
            </select></label>
          </div>
          {timesError && <p role="alert" className={form.error}>{timesError}</p>}
          {freeTimes && freeTimes.length === 0 && !timesError && <p className={form.hint}>{owner} has no free times on this day. Please choose another date.</p>}
          <label className={form.field}>Anything your case owner should know? (optional)<textarea maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label>
          <p className={form.hint}>Only times when {owner} is free are shown. Weekends and UK bank holidays are not available. Your email address is shared with {owner} for the Teams invitation.</p>
          {saveError && <p role="alert" className={form.error}>{saveError}</p>}
          <div className={form.actions}>
            <button type="submit" className={form.submit} disabled={saving || !date || !time}><Send size={17} aria-hidden="true" />{saving ? 'Booking...' : 'Book this time'}</button>
          </div>
        </form>}
      </section>}
      <footer className={styles.footer}><span>Kent Business College</span><span>Learning support, one conversation at a time.</span></footer>
    </div>
  </main>;
}
