import { useState } from 'react';
import { Modal } from '@/pages/users/components/Modal';
import { naiveLocalFromUtc } from "./createCalendarForm.helpers";
import { getCalendarTimeZone, zonedNaiveToUtcIso } from '../module-builder/moduleAuthoringData';
import { calendarAction, type ActionReview, type ActionResult } from './calendarActions';
import { submitChangeEmails, type ScheduleEmailStatus } from './scheduleEmail';

export interface CalendarActionTarget {
  liveId: string; moduleId: string; title: string; action: 'cancel' | 'reschedule'; scope: 'series' | 'occurrence';
  timeZone?: string;
  occurrences: Array<{ session_number: number; scheduled_start: string; scheduled_end: string; status?: string }>;
}

export function CalendarActionDialog({ target, onClose, onChanged }: {
  target: CalendarActionTarget; onClose: () => void; onChanged: () => Promise<void>;
}) {
  const timeZone = target.timeZone === 'GMT Standard Time' ? 'Europe/London' : target.timeZone || getCalendarTimeZone();
  const calendarLabel = (value: string) => new Intl.DateTimeFormat('en-GB', {
    timeZone: review?.timeZone || timeZone, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  }).format(new Date(value)).replace(/\b(am|pm)\b/g, match => match.toUpperCase());
  // Only a future, scheduled session can move; the rest are still listed, as
  // they stand, so the whole schedule is in view while editing.
  const movable = (row: CalendarActionTarget['occurrences'][number]) => row.status === 'scheduled' && Date.parse(row.scheduled_start) > Date.now();
  const [originals] = useState(() => target.occurrences.filter(row => target.action === 'cancel' || movable(row)).map(row => ({
    sessionNumber: row.session_number, localStart: naiveLocalFromUtc(row.scheduled_start, timeZone),
    durationMinutes: Math.round((Date.parse(row.scheduled_end) - Date.parse(row.scheduled_start)) / 60000),
  })));
  const [drafts, setDrafts] = useState(originals);
  // What goes to review is what was edited, and nothing else: an untouched
  // session is never sent, so an edit that changes nothing emails nobody.
  const changedDrafts = drafts.filter((draft, index) => draft.localStart !== originals[index].localStart
    || draft.durationMinutes !== originals[index].durationMinutes);
  const [review, setReview] = useState<ActionReview | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);
  const [comment, setComment] = useState('');
  const [includeComment, setIncludeComment] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [needsStatusCheck, setNeedsStatusCheck] = useState(false);
  const [error, setError] = useState('');
  const [email, setEmail] = useState<ScheduleEmailStatus | null>(null);
  const [emailError, setEmailError] = useState('');
  const [emailing, setEmailing] = useState(false);
  const notify = true;
  const cancelling = target.action === 'cancel';

  /**
   * The change email, once Microsoft has confirmed every change.
   * Learners each get their own copy -- what their sessions were and are now,
   * nobody else's details -- and the organiser, co-organisers and presenters a
   * copy that also lists the invited learners. Accepted messages are never sent twice.
   */
  const sendEmails = async (outcome: ActionResult, retryFailed = false) => {
    if (outcome.status !== 'done' || !outcome.changeNotice) return;
    // Cancellation notices are sent by the backend as part of the confirmed
    // action, so the browser can be closed without losing the LMS email. If
    // that server-side delivery failed, fall through to the existing endpoint
    // so the user can safely retry using the same ledger key.
    if (outcome.scheduleEmail && !outcome.scheduleEmail.error
      && Number.isInteger(outcome.scheduleEmail.total)
      && Number.isInteger(outcome.scheduleEmail.accepted)
      && Number.isInteger(outcome.scheduleEmail.queued)
      && Number.isInteger(outcome.scheduleEmail.failed)
      && Number.isInteger(outcome.scheduleEmail.uncertain)
      && outcome.scheduleEmail.status === 'complete'
      && outcome.scheduleEmail.queued === 0
      && outcome.scheduleEmail.failed === 0
      && outcome.scheduleEmail.uncertain === 0) {
      setEmail(outcome.scheduleEmail as ScheduleEmailStatus);
      return;
    }
    if (outcome.scheduleEmail?.error) setEmailError(outcome.scheduleEmail.error);
    setEmailing(true); setEmailError('');
    try {
      setEmail(await submitChangeEmails(target.liveId, outcome.changeNotice, { retryFailed, onProgress: setEmail }));
    } catch (failure) { setEmailError(failure instanceof Error ? failure.message : 'Change emails could not be confirmed.'); }
    finally { setEmailing(false); }
  };

  const prepare = async (recoverPrevious = true): Promise<void> => {
    setBusy(true); setError(''); setChecked(false); setNeedsStatusCheck(false);
    try {
      const value = await calendarAction<ActionReview>(target.liveId, {
        stage: 'review', action: target.action, scope: target.scope,
        sessionNumber: target.scope === 'occurrence' ? target.occurrences[0].session_number : undefined,
        comment: includeComment ? comment : '',
        changes: cancelling ? undefined : changedDrafts.map(draft => ({ sessionNumber: draft.sessionNumber,
          startDateTimeUtc: zonedNaiveToUtcIso(draft.localStart, timeZone), durationMinutes: draft.durationMinutes })),
      });
      setReview(value);
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : 'Review could not be loaded.';
      // Reconcile a previous request before opening a new review. The status
      // endpoint is read-only and never repeats the earlier calendar mutation.
      if (recoverPrevious && /previous action/i.test(message)) {
        try {
          const status = await calendarAction<ActionResult>(target.liveId, { stage: 'status' });
          await onChanged();
          if (status.status === 'done' || status.status === 'none') {
            await prepare(false);
            return;
          }
          setNeedsStatusCheck(true);
          setError(`${message} ${status.message}`);
        } catch (statusFailure) {
          setNeedsStatusCheck(true);
          setError(statusFailure instanceof Error ? statusFailure.message : message);
        }
      } else setError(message);
    }
    finally { setBusy(false); }
  };
  const checkPreviousAction = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const status = await calendarAction<ActionResult>(target.liveId, { stage: 'status' });
      await onChanged();
      if (status.status === 'done' || status.status === 'none') {
        setNeedsStatusCheck(false);
        await prepare(false);
      } else {
        setNeedsStatusCheck(true);
        setError(status.message);
      }
    } catch (failure) {
      setNeedsStatusCheck(true);
      setError(failure instanceof Error ? failure.message : 'The previous action still needs a status check.');
    } finally { setBusy(false); }
  };
  const execute = async (statusOnly = false) => {
    if (!statusOnly && (!checked || !review || attempted)) return;
    setBusy(true); setError('');
    if (!statusOnly) setAttempted(true);
    try {
      const value = await calendarAction<ActionResult>(target.liveId, statusOnly ? { stage: 'status' }
        : { stage: 'confirm', reviewToken: review!.reviewToken, acknowledgeNotifications: true });
      setResult(value);
      // Keep the mail side effect independent from the page refresh. A
      // transient refresh failure must not suppress a cancellation notice
      // after Microsoft has already accepted the action.
      await sendEmails(value);
      await onChanged();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The calendar action could not be confirmed.'); }
    finally { setBusy(false); }
  };
  const emailIncomplete = Boolean(emailError || email?.failed || email?.queued);
  const cancellationPending = cancelling && attempted && result?.status !== 'done';
  return (
    <Modal title={`${cancelling ? 'Cancel' : 'Edit'} ${target.scope === 'series' ? 'calendar series' : 'session'}`} onClose={onClose}
      dismissible={!busy && !emailing} size="max-w-4xl" scrollResetKey={review ? 'review' : 'edit'} footer={
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" disabled={busy || emailing} onClick={onClose} className="inline-flex h-10 items-center rounded-lg border border-background-200 bg-background-50 px-4 text-sm font-semibold text-foreground-700 transition-colors hover:bg-background-100">{result?.status === 'done' ? 'Done' : 'Close'}</button>
          {result?.status === 'done' && result.changeNotice && emailIncomplete && !emailing && <button type="button" onClick={() => void sendEmails(result, true)} className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-sm font-bold text-white shadow-sm transition-colors hover:bg-primary-700"><span aria-hidden="true">↻</span>Retry pending emails</button>}
          {needsStatusCheck && !attempted && !review ? <button type="button" disabled={busy} onClick={() => void checkPreviousAction()} className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-sm font-bold text-white shadow-sm transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40"><span aria-hidden="true">↻</span>{busy ? 'Checking previous action…' : 'Check previous action status'}</button>
          : attempted ? result?.status !== 'done' && <button type="button" disabled={busy} onClick={() => void execute(true)} className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-sm font-bold text-white shadow-sm transition-colors hover:bg-primary-700"><span aria-hidden="true">↻</span>Check action status</button>
            : review ? <>
              <button type="button" disabled={busy} onClick={() => { setReview(null); setChecked(false); }} className="inline-flex h-10 items-center rounded-lg border border-background-200 bg-background-50 px-4 text-sm font-semibold text-foreground-700 transition-colors hover:bg-background-100">Back to editing</button>
              <button type="button" disabled={busy || !checked} onClick={() => void execute()} className={`inline-flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-bold text-white shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${cancelling ? 'bg-red-700 hover:bg-red-800' : 'bg-primary-600 hover:bg-primary-700'}`}>{busy ? 'Applying...' : cancelling ? <><span aria-hidden="true">!</span>Confirm cancellation</> : 'Save and send update'}</button>
            </> : <button type="button" disabled={busy || !drafts.length || (!cancelling && !changedDrafts.length)} onClick={() => void prepare()} className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-sm font-bold text-white shadow-sm transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40">{busy ? 'Checking Microsoft...' : <><span aria-hidden="true">✓</span>Review changes</>}</button>}
        </div>
      }>
      <div className="space-y-4">
        <p className="font-bold text-foreground-900">{target.title}</p>
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          {cancelling ? 'Microsoft sends a cancellation notice to the affected invitees. Silent cancellation is not available.'
            : 'The new times will update Teams, the module timetable and learner training plans. Microsoft sends calendar updates to invitees, and the LMS sends the change email automatically.'}
        </p>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        {result && <p role="status" className={`rounded-lg p-3 text-sm ${result.status === 'done' ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-900'}`}>
          {result.message}{result.total ? ` (${result.completed}/${result.total} calendar changes confirmed)` : ''}
        </p>}
        {cancellationPending && <div role="status" aria-live="polite" className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <span aria-hidden="true" className={`mt-0.5 inline-block h-4 w-4 shrink-0 rounded-full border-2 border-current border-t-transparent ${busy ? 'animate-spin' : ''}`} />
          <span>
            <strong className="block">{busy ? 'Cancellation in progress' : 'Cancellation awaiting confirmation'}</strong>
            {busy ? 'Microsoft is processing the cancellation. Keep this window open while the request completes.'
              : 'Microsoft has not confirmed every calendar change yet. Use “Check action status” to refresh safely; the cancellation will not be sent again.'}
          </span>
        </div>}
        {cancelling && result?.status === 'done' && review && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          <strong>Cancelled in the LMS:</strong> session{review.sessions.length === 1 ? '' : 's'} {review.sessions.map(session => session.sessionNumber).join(', ')}. The affected date stays visible below for reference.
        </p>}
        {attempted && <p role="status" className={`rounded-lg p-3 text-sm ${emailIncomplete || email?.uncertain ? 'bg-amber-50 text-amber-900' : 'bg-background-50 text-foreground-700'}`}>
          <strong>{cancelling ? 'Cancellation emails: ' : 'Change emails: '}</strong>
          {!notify ? 'Not sent — you chose not to email.'
            : emailing ? `Sending… ${email ? `${email.accepted} of ${email.total} submitted to Microsoft` : ''}`
              : email && !email.total ? 'None to send — nobody is invited.'
              : email ? `${email.accepted} of ${email.total} submitted to Microsoft${email.failed ? ` · ${email.failed} failed` : ''}${email.uncertain ? ` · ${email.uncertain} awaiting verification (not sent again automatically)` : ''}`
                : result?.status === 'done' && !result.changeNotice ? 'Not sent — no session date changed.'
                  : result?.status === 'done' ? 'Not sent.'
                    : 'Sent once Microsoft confirms every change.'}
          {emailError && <span role="alert" className="block text-red-700">{emailError}</span>}
        </p>}
        {!review && !attempted && (cancelling ? <>
          <p>{target.scope === 'series' ? 'The entire calendar series will be cancelled.' : `Only session ${target.occurrences[0].session_number} will be cancelled.`}</p>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={includeComment} onChange={event => setIncludeComment(event.target.checked)} />Add a message to Microsoft's cancellation notice</label>
          {includeComment && <textarea aria-label="Cancellation message" value={comment} maxLength={1000} onChange={event => setComment(event.target.value)} className="min-h-24 w-full rounded-lg border p-3 text-sm" />}
        </> : <div className="max-h-[50vh] space-y-3 overflow-auto">
          <p className="text-sm text-foreground-600">Every session and its time. Change the ones you want to move; only those go to review, and the rest keep their current times.</p>
          <p className="text-sm text-foreground-600">Time zone: {timeZone}. 12 AM is midnight; 12 PM is noon.</p>
          {[...target.occurrences].sort((a, b) => a.session_number - b.session_number).map(row => {
            const index = drafts.findIndex(draft => draft.sessionNumber === row.session_number);
            const draft = drafts[index];
            if (!draft) {
              return <div key={row.session_number} className="grid items-center gap-2 rounded-lg border bg-background-50 p-3 text-sm text-foreground-500 sm:grid-cols-[80px_1fr_130px]">
                <strong>Session {row.session_number}</strong>
                <span>{calendarLabel(row.scheduled_start)} to {calendarLabel(row.scheduled_end)}</span>
                <span className="text-xs font-semibold">{row.status === 'cancelled' ? 'Cancelled' : row.status === 'superseded' ? 'Not in plan' : 'Already run'}, can’t be moved</span>
              </div>;
            }
            const changed = changedDrafts.includes(draft);
            return <div key={row.session_number} className={`grid gap-2 rounded-lg border p-3 sm:grid-cols-[80px_1fr_130px] ${changed ? 'border-primary-300 bg-primary-50' : ''}`}>
              <strong className="text-sm">Session {draft.sessionNumber}{changed && <span className="block text-[11px] font-bold text-primary-700">Changed</span>}</strong>
              <input aria-label={`Session ${draft.sessionNumber} date and time`} type="datetime-local" value={draft.localStart} onChange={event => setDrafts(current => current.map((item, i) => i === index ? { ...item, localStart: event.target.value } : item))} className="rounded border px-2 py-1 text-sm" />
              <label className="text-xs">Minutes<input aria-label={`Session ${draft.sessionNumber} duration`} type="number" min={15} max={1440} value={draft.durationMinutes} onChange={event => setDrafts(current => current.map((item, i) => i === index ? { ...item, durationMinutes: Number(event.target.value) } : item))} className="w-full rounded border px-2 py-1 text-sm" /></label>
            </div>;
          })}
          {!changedDrafts.length && <p role="status" className="text-sm font-semibold text-foreground-500">Nothing has changed yet, so nothing will be sent to Teams or emailed.</p>}
        </div>)}
        {review && <>
          <p className="text-sm">Organizer: {review.organizer} · Time zone: {review.timeZone}</p>
          {review.warnings?.map((warning, index) => <p key={index} className="text-sm text-amber-800">{warning}</p>)}
          <div className="max-h-[45vh] overflow-auto rounded-lg border">
            <table className="w-full text-left text-sm"><thead className="bg-background-100"><tr><th className="p-3">Session</th><th className="p-3">Current time</th>{!cancelling && <th className="p-3">New time</th>}<th className="p-3">Meeting</th></tr></thead>
              <tbody>{review.sessions.map(session => <tr key={session.sessionNumber} className="border-t"><td className="p-3">{session.sessionNumber}</td>
                <td className="p-3">{calendarLabel(session.startDateTimeUtc)}<br />to {calendarLabel(session.endDateTimeUtc)}
                  {session.calendarVerified === false && <span className="block text-xs text-amber-800">Stored LMS time; this occurrence could not be verified.</span>}</td>
                {!cancelling && <td className="p-3 font-semibold">{session.newStartDateTimeUtc ? <>{calendarLabel(session.newStartDateTimeUtc)}<br />to {calendarLabel(session.newEndDateTimeUtc!)}</> : 'Unchanged'}</td>}
                <td className="p-3">{cancelling && result?.status === 'done' ? <span className="inline-flex rounded-full border border-red-200 bg-red-50 px-2 py-1 text-xs font-bold text-red-700">Cancelled</span>
                  : session.joinUrl?.startsWith('https://') ? <a href={session.joinUrl} target="_blank" rel="noreferrer" className="font-semibold text-primary-700 underline">Current link</a> : 'Unavailable'}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <p className="text-xs text-foreground-500">{review.calendarRequests} calendar request{review.calendarRequests === 1 ? '' : 's'}. 12 AM is midnight; 12 PM is noon. Existing meeting links are preserved when moving sessions.</p>
          {!attempted && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />I checked these sessions and understand that Microsoft will notify the affected invitees.</label>}
        </>}
      </div>
    </Modal>
  );
}
