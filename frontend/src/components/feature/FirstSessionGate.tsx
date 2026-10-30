import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from "@/hooks/useAuth.helpers";
import {
  bookLearnerCalendarSession,
  fetchFirstSessionSlots,
  fetchLearnerFirstSession,
  ukOffsetForDate,
  type LearnerFirstSession,
  type SessionSlot,
} from '@/api/learnerCalendar';
import type { LearnerKind } from '@/api/learnerDetail';
import { SessionSlotPicker } from './SessionSlotPicker';
import { slotLabel } from "./SessionSlotPicker.helpers";
import { RouteLoadingSkeleton } from './RouteLoadingSkeleton';

// ============================================================================
// The learner's first session is their own first task.
//
// It used to be arranged by whoever enrolled them, on the create form. Now the
// learner signs in, books it with their case owner, and waits until the day —
// so nothing about their programme opens before the session that starts it.
//
// Two states, both decided by the server (`access`), never by this browser: a
// learner must not reach their programme early by changing their own clock.
//
//   'enrolling' -> nothing; an apprentice books only once every enrolment
//                  step is done, and until then their enrolment is the page
//                  they need (useOnboardingRedirect keeps them on it)
//   'book'      -> the booking screen below
//   'waiting'   -> the holding screen below
//   'open'      -> nothing; the programme renders normally
//
// Note this is NOT the cohort start-date lockout that LearnerProgrammeGate
// used to apply and that was deliberately removed. That held learners out of
// modules they had already been assigned. This is the one meeting that has to
// happen before there is a programme to be let into at all.
// ============================================================================

/** Paths a held learner can still reach. Support and their own profile stay
 *  open: somebody who cannot book, or who thinks the date is wrong, needs a
 *  way to say so that is not "email an address they do not have". */
const ALWAYS_OPEN = ['/messages', '/learner/messages', '/learner/support', '/profile'];

const exempt = (path: string) =>
  ALWAYS_OPEN.some(base => path === base || path.startsWith(`${base}/`));

/** Tomorrow in UK time — the earliest a first session can be booked.
 *
 *  The server refuses a past date anyway (booking_date_restriction); this just
 *  stops the date field offering one. */
function earliestBookable(): string {
  const day = new Date();
  day.setDate(day.getDate() + 1);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(day);
}

function longDate(date: string): string {
  const parsed = new Date(`${date}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString('en-GB', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });
}

export function FirstSessionGate({ children }: { children: ReactNode }) {
  const { auth } = useAuth();
  const account = auth.account;
  // Only the learner's own session holds them up. Staff and admins carry a
  // different role and never reach this; an account that is both a staff
  // member and a learner (login/learner_enrolment.py) is gated on their own
  // record and nowhere else, which the role check already gives us because
  // reviewing somebody else's page is done from a staff-role session.
  if (account?.role !== 'learner') return <>{children}</>;
  return <LearnerFirstSessionGate key={account.subjectId}>{children}</LearnerFirstSessionGate>;
}

function LearnerFirstSessionGate({ children }: { children: ReactNode }) {
  const { auth, logout } = useAuth();
  const { pathname } = useLocation();
  const account = auth.account;
  const kind: LearnerKind = account?.learnerType === 'commercial' ? 'commercial' : 'apprenticeship';
  const learnerId = String(account?.subjectId ?? '');

  const [state, setState] = useState<LearnerFirstSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setLoadError('');
    return fetchLearnerFirstSession(kind, learnerId, signal)
      .then(result => { if (!signal?.aborted) setState(result); })
      .catch((err: Error) => {
        if (signal?.aborted) return;
        setLoadError(err.message || 'Could not check your first session.');
      })
      .finally(() => { if (!signal?.aborted) setLoading(false); });
  }, [kind, learnerId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  if (exempt(pathname)) return <>{children}</>;
  if (loading && !state) return <RouteLoadingSkeleton />;

  // A gate that cannot reach the server must not become a lockout: the learner
  // has done nothing wrong, and holding them out on a network error would be
  // the same screen as holding them out on purpose.
  if (loadError && !state) return <>{children}</>;
  if (!state || state.access === 'open' || state.access === 'enrolling') return <>{children}</>;

  return (
    <Holding
      state={state}
      kind={kind}
      learnerId={learnerId}
      onBooked={() => void load()}
      onSignOut={logout}
    />
  );
}

function Holding({ state, kind, learnerId, onBooked, onSignOut }: {
  state: LearnerFirstSession;
  kind: LearnerKind;
  learnerId: string;
  onBooked: () => void;
  onSignOut: () => void;
}) {
  return (
    <div className="min-h-screen bg-background-200 px-4 py-10">
      <div className="mx-auto max-w-xl rounded-2xl border border-foreground-200 bg-background-50 p-6 sm:p-8">
        {state.access === 'waiting'
          ? <Waiting state={state} />
          : <Booking state={state} kind={kind} learnerId={learnerId} onBooked={onBooked} />}
        <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-foreground-100 pt-4 text-[13px]">
          <Link to="/learner/support" className="text-primary-600 hover:underline">Contact support</Link>
          <button type="button" onClick={onSignOut} className="text-foreground-500 hover:underline">
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}

/** Booked; shown until the session day (also on the learner's First Learning Session page). */
export function Waiting({ state }: { state: LearnerFirstSession }) {
  const time = (state.event?.scheduledTime || '').slice(0, 5);

  return (
    <>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-primary-600">
        You are all set
      </span>
      <h1 className="mt-2 text-xl font-semibold text-foreground-900">
        Your programme starts on {state.startsOn ? longDate(state.startsOn) : 'your first session'}
      </h1>
      <p className="mt-3 text-[14px] leading-relaxed text-foreground-600">
        Your first session with{' '}
        <span className="font-semibold text-foreground-800">
          {state.caseOwner?.name || 'your case owner'}
        </span>
        {time ? <> is at <span className="font-semibold text-foreground-800">{slotLabel(time)}</span> UK time</> : null}.
        Your learning opens here on the day — there is nothing else to do until then.
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-2">
        {state.event?.meetingLink && (
          <a
            href={state.event.meetingLink}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-xl bg-primary-500 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-primary-600"
          >
            <i className="ri-vidicon-line" aria-hidden="true" />
            Join the Teams meeting
          </a>
        )}
      </div>
      <p className="mt-4 text-[12px] text-foreground-400">
        The invitation is in your email.
      </p>
    </>
  );
}

/** The case owner's free hours for a chosen day.
 *
 *  Reloaded whenever the date changes, because availability is a property of
 *  the day, not of the form. A time already picked is cleared when the new
 *  day does not offer it — otherwise changing the date could silently keep a
 *  selection the case owner is busy for.
 */
function useSessionSlots(
  kind: LearnerKind,
  learnerId: string,
  date: string,
  onUnavailable: () => void,
) {
  const [slots, setSlots] = useState<SessionSlot[]>([]);
  const [unconfirmed, setUnconfirmed] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!date) {
      setSlots([]);
      setUnconfirmed('');
      setError('');
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchFirstSessionSlots(kind, learnerId, date, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setSlots(result.slots);
        setUnconfirmed(result.unconfirmed);
        onUnavailable();
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setSlots([]);
        setUnconfirmed('');
        setError(err.message || 'Could not check your case owner’s calendar.');
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // `onUnavailable` is a stable callback from the caller; re-running on a new
    // identity would refetch the same day on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, learnerId, date]);

  return { slots, unconfirmed, loading, error };
}

/** True when the picked time is one the case owner is actually free for.
 *
 *  Checked before booking as well as in the picker: the slots were read when
 *  the date was chosen, and somebody else may have taken the hour since. The
 *  backend checks again too — this only keeps the button honest. */
function bookable(slots: SessionSlot[], time: string): boolean {
  return slots.some(slot => slot.time === time && slot.available);
}

/** The booking form (also on the learner's First Learning Session page). */
export function Booking({ state, kind, learnerId, onBooked }: {
  state: LearnerFirstSession;
  kind: LearnerKind;
  learnerId: string;
  onBooked: () => void;
}) {
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Changing the day drops the hour: the new day has its own free hours, and
  // keeping a selection made against the old one would book a time the case
  // owner may well be busy for.
  const { slots, unconfirmed, loading, error: slotsError } = useSessionSlots(
    kind, learnerId, date, () => setTime(''),
  );

  const owner = state.caseOwner;

  // Without a case owner there is nobody to meet. The learner cannot fix that
  // themselves, so this says who can rather than showing a form that will fail.
  if (!owner) {
    return (
      <>
        <h1 className="text-xl font-semibold text-foreground-900">
          Your first session cannot be booked yet
        </h1>
        <p className="mt-3 text-[14px] leading-relaxed text-foreground-600">
          You do not have a case owner assigned, so there is nobody to book your first
          session with. Please contact support and we will get this sorted.
        </p>
      </>
    );
  }

  const book = async () => {
    if (saving || !date || !bookable(slots, time)) return;
    setSaving(true);
    setError('');
    try {
      await bookLearnerCalendarSession(kind, learnerId, {
        sessionType: 'first-session',
        scheduledDate: date,
        scheduledTime: time,
        durationMinutes: 60,
        timezoneOffsetMinutes: ukOffsetForDate(date),
      });
      onBooked();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not book your session. Please try again.');
      setSaving(false);
    }
  };

  return (
    <>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-primary-600">
        Welcome
      </span>
      <h1 className="mt-2 text-xl font-semibold text-foreground-900">
        Book your first learning session
      </h1>
      <p className="mt-3 text-[14px] leading-relaxed text-foreground-600">
        Before your programme starts, book a one-hour session with your case owner,{' '}
        <span className="font-semibold text-foreground-800">{owner.name}</span>. Your
        learning opens on the day of that session.
      </p>

      <div className="mt-5 flex flex-wrap items-start gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500">
            Date
          </span>
          <input
            type="date"
            value={date}
            min={earliestBookable()}
            disabled={saving}
            onChange={event => { setDate(event.target.value); setError(''); }}
            className="rounded-lg border border-foreground-200 bg-background-50 px-3 py-2 text-[13px] font-medium text-foreground-900 outline-none focus:border-primary-400 disabled:opacity-60"
          />
          <span className="text-[11px] text-foreground-400">
            Weekdays only — not weekends or UK bank holidays.
          </span>
        </label>
      </div>

      <div className="mt-4 flex flex-col gap-1.5">
        <span id="first-session-time-label" className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500">
          Time (UK)
        </span>
        {date ? (
          <SessionSlotPicker
            slots={slots}
            value={time}
            onChange={next => { setTime(next); setError(''); }}
            labelledBy="first-session-time-label"
            disabled={saving}
            loading={loading}
            error={slotsError}
            unconfirmed={unconfirmed}
          />
        ) : (
          <p className="text-[13px] text-foreground-500">
            Choose a date to see the times {owner.name} is free.
          </p>
        )}
      </div>

      {time && (
        <p className="mt-2 text-[12px] text-foreground-500">
          Session at <span className="font-semibold">{slotLabel(time)}</span> UK time.
        </p>
      )}

      <button
        type="button"
        onClick={() => void book()}
        disabled={saving || !date || !bookable(slots, time)}
        className="mt-5 inline-flex items-center gap-2 rounded-xl bg-primary-500 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-primary-600 disabled:opacity-60 disabled:cursor-not-allowed"
      >
        <i className={saving ? 'ri-loader-4-line animate-spin' : 'ri-calendar-check-line'} aria-hidden="true" />
        {saving ? 'Booking…' : 'Book my first session'}
      </button>

      {error && <p role="alert" className="mt-3 text-[13px] text-red-700">{error}</p>}
    </>
  );
}
