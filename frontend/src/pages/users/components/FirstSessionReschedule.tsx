import { useEffect, useState } from 'react';
import {
  fetchLearnerCalendarEvents,
  rescheduleLearnerCalendarSession,
  type LearnerCalendarEvent,
} from '@/api/learnerCalendar';
import type { LearnerKind } from '@/api/learnerDetail';

// ============================================================================
// Move a learner's first session, from the enrolment header.
//
// The session is booked during creation (learner_api/first_session.py) and was
// otherwise only changeable from the learner's own calendar — which is the
// wrong place for an enrolment officer who is already looking at the record.
//
// Only *before* the day: once the session has happened, moving it would
// rewrite history rather than rearrange a plan, and the programme start date
// it anchors has already taken effect. A past session is shown, read-only,
// so the header still says when the learner started.
// ============================================================================

/** The hours a first session may start, matching the create form and
 *  COLLEGE_DAY_START/COLLEGE_LAST_SLOT_START in coach_availability.py. */
const HOURS = Array.from({ length: 8 }, (_, i) => i + 9);
const MINUTES = Array.from({ length: 60 }, (_, i) => i);

/** Today in UK time — the college's day, not the viewer's.
 *
 *  Staff working from another country must not be able to move a session that
 *  has already started in Kent, nor be blocked from one that has not. */
function ukToday(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/London',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

/** "2:37 PM" — the 24-hour value is what gets booked, this is for reading. */
function clockLabel(time: string): string {
  const [hour, minute] = time.split(':').map(Number);
  const suffix = hour < 12 ? 'AM' : 'PM';
  return `${hour % 12 === 0 ? 12 : hour % 12}:${String(minute).padStart(2, '0')} ${suffix}`;
}

function dayLabel(date: string): string {
  const parsed = new Date(`${date}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** The learner's booked first session, or null. Cancelled rows are ignored:
 *  they are a session that is no longer happening, not one to move. */
function firstSessionOf(events: LearnerCalendarEvent[]): LearnerCalendarEvent | null {
  return (
    events.find(
      event =>
        event.source === 'first-session' &&
        event.status === 'scheduled' &&
        Boolean(event.scheduledDate),
    ) ?? null
  );
}

export function FirstSessionReschedule({ kind, learnerId, onMoved }: {
  kind: LearnerKind;
  learnerId: string;
  /** The programme start date follows the session, so the header reloads. */
  onMoved?: () => void;
}) {
  const [session, setSession] = useState<LearnerCalendarEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSession(null);
    setOpen(false);
    setError('');
    setMessage('');
    fetchLearnerCalendarEvents(kind, learnerId)
      .then(result => { if (!cancelled) setSession(firstSessionOf(result.events)); })
      // A calendar that cannot be read is not an error worth shouting about in
      // the header: the rest of the enrolment record is still usable, and the
      // session can still be moved from the learner's own calendar.
      .catch(() => { if (!cancelled) setSession(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [kind, learnerId]);

  if (loading || !session || !session.scheduledDate) return null;

  const scheduledDate = session.scheduledDate;
  const scheduledTime = (session.scheduledTime || '').slice(0, 5);
  // Compared as ISO strings in UK time: the session is in the past only once
  // its whole day has gone, so a session later today can still be moved.
  const past = scheduledDate < ukToday();
  const when = `${dayLabel(scheduledDate)}${scheduledTime ? ` at ${clockLabel(scheduledTime)}` : ''}`;

  const start = () => {
    setDate(scheduledDate);
    setTime(scheduledTime || '09:00');
    setError('');
    setMessage('');
    setOpen(true);
  };

  const [hour, minute] = time ? time.split(':').map(Number) : [null, null];
  const setPart = (h: number | null, m: number | null) => {
    if (h === null) return;
    setTime(`${String(h).padStart(2, '0')}:${String(m ?? 0).padStart(2, '0')}`);
  };

  const unchanged = date === scheduledDate && time === scheduledTime;

  const save = async () => {
    if (saving || !date || !time || unchanged) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      // Offset pinned to Europe/London, not the browser's: the backend reads
      // the time as UK wall clock, the same convention the booking used.
      const offset =
        (12 -
          Number(
            new Intl.DateTimeFormat('en-GB', {
              timeZone: 'Europe/London',
              hour: '2-digit',
              hourCycle: 'h23',
            }).format(new Date(`${date}T12:00:00Z`)),
          )) *
        60;
      const result = await rescheduleLearnerCalendarSession(kind, learnerId, {
        eventKey: session.eventKey,
        scheduledDate: date,
        scheduledTime: time,
        durationMinutes: session.durationMinutes || 60,
        timezoneOffsetMinutes: offset,
      });
      setSession(result.event);
      setOpen(false);
      // A Graph warning means the row moved but Microsoft has not confirmed
      // it — saying "moved" alone would imply an invite nobody has received.
      setMessage(
        result.warning
          ? `Session moved. ${result.warning} Check the learner's calendar before telling them it is confirmed.`
          : 'Session moved. The case owner and the learner have been sent the new invitation.',
      );
      onMoved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not move the session. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const cell = (selected: boolean) =>
    `w-full px-3 py-1 text-[12px] tabular-nums text-left transition-smooth cursor-pointer ${
      selected ? 'bg-violet-600 text-white font-semibold' : 'text-slate-600 hover:bg-slate-100'
    }`;

  return (
    <div className="min-w-0">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        First session
      </span>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-slate-800">{when}</span>
        {past ? (
          // Not an error — just why there is no button. The date is still shown
          // because it is when the learner's programme started.
          <span className="text-[11px] text-slate-500">already held</span>
        ) : (
          !open && (
            <button
              type="button"
              onClick={start}
              className="inline-flex items-center gap-1.5 rounded-lg bg-violet-100 px-3 py-1.5 text-[12px] font-semibold text-violet-800 hover:bg-violet-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
            >
              <i className="ri-calendar-event-line" aria-hidden="true" />
              Reschedule
            </button>
          )
        )}
      </div>

      {open && (
        <div className="mt-2 rounded-xl border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-start gap-3">
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                New date
              </span>
              <input
                type="date"
                value={date}
                // The college's today, so a session cannot be moved into the
                // past from a machine whose clock is on another continent.
                min={ukToday()}
                disabled={saving}
                onChange={event => { setDate(event.target.value); setError(''); }}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-[13px] font-medium text-slate-800 outline-none hover:border-violet-400 focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-60"
              />
            </label>
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                New time (UK)
              </span>
              <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 bg-white divide-x divide-slate-200">
                <ul className="max-h-32 w-14 overflow-y-auto" aria-label="Hour">
                  {HOURS.map(h => (
                    <li key={h}>
                      <button type="button" aria-pressed={hour === h} disabled={saving}
                        onClick={() => setPart(h, minute)} className={cell(hour === h)}>
                        {String(h).padStart(2, '0')}
                      </button>
                    </li>
                  ))}
                </ul>
                <ul className="max-h-32 w-14 overflow-y-auto" aria-label="Minute">
                  {MINUTES.map(m => (
                    <li key={m}>
                      <button type="button" aria-pressed={minute === m} disabled={saving || hour === null}
                        onClick={() => setPart(hour, m)}
                        className={`${cell(minute === m)} disabled:opacity-40 disabled:cursor-not-allowed`}>
                        {String(m).padStart(2, '0')}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>

          <p className="mt-2 text-[11px] text-slate-500">
            Moves the existing Teams meeting and re-invites the case owner and the learner.
            The learner’s programme start date follows this session.
          </p>

          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || unchanged || !date || !time}
              className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-[12px] font-semibold text-white hover:bg-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <i className={saving ? 'ri-loader-4-line animate-spin' : 'ri-check-line'} aria-hidden="true" />
              {saving ? 'Moving…' : 'Move session'}
            </button>
            <button
              type="button"
              onClick={() => { setOpen(false); setError(''); }}
              disabled={saving}
              className="rounded-lg px-3 py-2 text-[12px] font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && <p role="alert" className="mt-2 max-w-sm text-xs text-red-700">{error}</p>}
      {message && <p role="status" className="mt-2 max-w-sm text-xs text-emerald-700">{message}</p>}
    </div>
  );
}
