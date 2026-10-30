// ============================================================================
// First sessions — enrolment workspace
//
// Every learner's first learning session booking: when they booked it, when the
// session is, who it is with and its Teams join link. Read only — nothing here
// books, moves or cancels a meeting.
//
// All bookings are listed, cancelled and failed-to-sync ones too; the status
// column says which, so a booking Microsoft never accepted is not mistaken for
// a working meeting. Filters live in the URL so a filtered view can be shared.
// ============================================================================
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AdminPage, DataPanel, StatusBadge } from '@/pages/admin/_shared/AdminPage';
import { AppIcon } from '@/components/feature/AppIcon';
import { formatSystemTimestamp } from '@/lib/format';
import {
  FILTER_KEYS,
  caseOwnerOptions,
  fetchFirstSessionBookings,
  filterFirstSessionBookings,
  programmeOptions,
  type FirstSessionBooking,
  type FirstSessionBookingFilters,
} from '@/api/firstSessionBookings';

type Tone = 'ok' | 'bad' | 'warn' | 'neutral';

const STATE_LABELS: Record<string, { label: string; tone: Tone }> = {
  scheduled: { label: 'Scheduled', tone: 'ok' },
  'in-progress': { label: 'In progress', tone: 'ok' },
  requested: { label: 'Awaiting approval', tone: 'warn' },
  'awaiting-signature': { label: 'Awaiting signature', tone: 'warn' },
  completed: { label: 'Completed', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'bad' },
  'sync-pending': { label: 'Teams sync pending', tone: 'warn' },
  'sync-failed': { label: 'Teams sync failed', tone: 'bad' },
  'needs-reconciliation': { label: 'Needs reconciliation', tone: 'warn' },
};

function stateBadge(state: string) {
  return STATE_LABELS[state] ?? { label: state.replace(/-/g, ' ') || 'Unknown', tone: 'neutral' as Tone };
}

/** A UK calendar day, shown as the day it is — no time-zone conversion. */
function formatDay(value: string | null): string {
  if (!value) return '—';
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }).format(date);
}

function formatBookedAt(value: string | null): string {
  if (!value) return '—';
  return formatSystemTimestamp(value, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) || '—';
}

const fieldClass = 'px-3 py-2 rounded-xl border border-foreground-200/60 bg-background-50 text-[13px] text-foreground-700 focus:outline-none focus:ring-2 focus:ring-primary-200 min-w-0 max-w-full';
const labelClass = 'flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wider text-foreground-400';
const th = 'text-left px-4 py-2.5 text-foreground-400 font-medium text-[10px] uppercase tracking-wider whitespace-nowrap';

export default function FirstSessionBookingsPage() {
  const [params, setParams] = useSearchParams();
  const [bookings, setBookings] = useState<FirstSessionBooking[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchFirstSessionBookings(controller.signal)
      .then(data => setBookings(data.results))
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load first-session bookings.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [version]);

  const filters = useMemo(
    () => Object.fromEntries(FILTER_KEYS.map(key => [key, params.get(key) || ''])) as unknown as FirstSessionBookingFilters,
    [params],
  );
  const filtering = FILTER_KEYS.some(key => filters[key]);
  const shown = useMemo(() => filterFirstSessionBookings(bookings, filters), [bookings, filters]);
  const owners = useMemo(() => caseOwnerOptions(bookings), [bookings]);
  const programmes = useMemo(() => programmeOptions(bookings), [bookings]);

  const setFilter = useCallback((key: keyof FirstSessionBookingFilters, value: string) => {
    setParams(current => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    }, { replace: true });
  }, [setParams]);

  const clearFilters = () => setParams(current => {
    const next = new URLSearchParams(current);
    FILTER_KEYS.forEach(key => next.delete(key));
    return next;
  }, { replace: true });

  return (
    <AdminPage
      workspaceRole="compliance"
      title="First sessions"
      subtitle="Learners who have booked their first learning session"
      icon="ri-calendar-check-line"
      heroTitle="First learning sessions"
      heroBlurb="Every first-session booking — when the learner booked it, when the session is, and its Teams link. Cancelled and past bookings are included."
      stats={[
        { label: 'Bookings', value: loading ? '—' : bookings.length },
        { label: 'Shown', value: loading ? '—' : shown.length },
      ]}
    >
      <form
        aria-label="Filter first-session bookings"
        onSubmit={event => event.preventDefault()}
        className="bg-[var(--kbc-surface)] rounded-2xl border border-[var(--kbc-border)] p-3 md:p-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1.4fr)_auto] xl:items-end"
      >
        <label className={labelClass}>
          Case owner
          <select value={filters.caseOwner} onChange={event => setFilter('caseOwner', event.target.value)} className={`${fieldClass} cursor-pointer`}>
            <option value="">All case owners</option>
            {owners.map(owner => <option key={owner.value} value={owner.value}>{owner.label}</option>)}
          </select>
        </label>
        <label className={labelClass}>
          Programme
          <select value={filters.programme} onChange={event => setFilter('programme', event.target.value)} className={`${fieldClass} cursor-pointer`}>
            <option value="">All programmes</option>
            {programmes.map(programme => <option key={programme} value={programme}>{programme}</option>)}
          </select>
        </label>
        <fieldset className="grid grid-cols-2 gap-2 min-w-0">
          <legend className="sr-only">Session date</legend>
          <label className={labelClass}>
            Session from
            <input type="date" value={filters.sessionFrom} max={filters.sessionTo || undefined} onChange={event => setFilter('sessionFrom', event.target.value)} className={fieldClass} />
          </label>
          <label className={labelClass}>
            Session to
            <input type="date" value={filters.sessionTo} min={filters.sessionFrom || undefined} onChange={event => setFilter('sessionTo', event.target.value)} className={fieldClass} />
          </label>
        </fieldset>
        <fieldset className="grid grid-cols-2 gap-2 min-w-0">
          <legend className="sr-only">Booked-on date</legend>
          <label className={labelClass}>
            Booked from
            <input type="date" value={filters.bookedFrom} max={filters.bookedTo || undefined} onChange={event => setFilter('bookedFrom', event.target.value)} className={fieldClass} />
          </label>
          <label className={labelClass}>
            Booked to
            <input type="date" value={filters.bookedTo} min={filters.bookedFrom || undefined} onChange={event => setFilter('bookedTo', event.target.value)} className={fieldClass} />
          </label>
        </fieldset>
        <button
          type="button"
          onClick={clearFilters}
          disabled={!filtering}
          className="px-4 py-2 rounded-xl border border-foreground-200/60 text-[13px] font-semibold text-foreground-700 hover:bg-primary-50/60 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-200"
        >
          Clear filters
        </button>
      </form>

      <DataPanel
        loading={loading}
        error={error}
        empty={shown.length === 0}
        emptyMessage={filtering ? 'No first-session bookings match these filters.' : 'No first-session bookings yet.'}
        emptyIcon="ri-calendar-line"
        onRetry={() => setVersion(value => value + 1)}
      >
        <div className="bg-[var(--kbc-surface)] rounded-2xl border border-[var(--kbc-border)] overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <caption className="sr-only">First-session bookings</caption>
              <thead>
                <tr className="border-b border-foreground-400/50">
                  <th scope="col" className={th}>Learner</th>
                  <th scope="col" className={th}>Programme</th>
                  <th scope="col" className={th}>Case owner</th>
                  <th scope="col" className={th}>Booked on</th>
                  <th scope="col" className={th}>Session</th>
                  <th scope="col" className={th}>Status</th>
                  <th scope="col" className={th}>Meeting</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(booking => {
                  const badge = stateBadge(booking.state);
                  return (
                    <tr key={booking.id} className="border-b border-background-100/50 hover:bg-primary-50/40 transition-smooth align-top">
                      <td className="px-4 py-2.5">
                        {booking.learnerId !== null ? (
                          <Link
                            to={`/users/${booking.learnerId}${booking.source === 'commercial' ? '?source=commercial' : ''}`}
                            className="font-medium text-foreground-800 hover:text-primary-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-200 rounded"
                          >
                            {booking.learnerName || booking.learnerEmail || 'Unnamed learner'}
                          </Link>
                        ) : (
                          <span className="font-medium text-foreground-800">{booking.learnerName || booking.learnerEmail || 'Unknown learner'}</span>
                        )}
                        {booking.learnerEmail && <div className="text-[11px] text-foreground-500">{booking.learnerEmail}</div>}
                      </td>
                      <td className="px-4 py-2.5 text-foreground-700">{booking.programme || '—'}</td>
                      <td className="px-4 py-2.5 text-foreground-700">
                        {booking.caseOwner.name || booking.caseOwner.email || '—'}
                      </td>
                      <td className="px-4 py-2.5 whitespace-nowrap text-foreground-700">{formatBookedAt(booking.bookedAt)}</td>
                      <td className="px-4 py-2.5 whitespace-nowrap text-foreground-700">
                        <span>{formatDay(booking.sessionDate)}</span>
                        {booking.sessionTime && <div className="text-[11px] text-foreground-500">{booking.sessionTime} UK time{booking.durationMinutes ? ` · ${booking.durationMinutes} min` : ''}</div>}
                      </td>
                      <td className="px-4 py-2.5"><StatusBadge status={badge.label} tone={badge.tone} /></td>
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        {booking.meetingLink ? (
                          <a
                            href={booking.meetingLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Open Teams meeting for ${booking.learnerName || booking.learnerEmail || 'learner'}`}
                            className="inline-flex items-center gap-1 font-semibold text-primary-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-200 rounded"
                          >
                            <AppIcon name="ri-vidicon-line" aria-hidden="true" />
                            Join link
                          </a>
                        ) : (
                          <span className="text-foreground-400">No link</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </DataPanel>
    </AdminPage>
  );
}
