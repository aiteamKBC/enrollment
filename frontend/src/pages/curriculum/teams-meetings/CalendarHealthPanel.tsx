import { useCallback, useEffect, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { DetailRow, InlineError } from '../shared/entities/ui';
import { offScheduleRunLabel, offScheduleRunSentence } from './offScheduleRuns';
import {
  EVENT_LABELS, FILTER_LABELS, INTEGRITY_EXPLANATIONS, INTEGRITY_LABELS, INTEGRITY_TONES, LIFECYCLE_LABELS,
  MEETING_TYPE_EXPLANATIONS, MEETING_TYPE_LABELS, MEMBERSHIP_LABELS, STATUS_LABELS, STATUS_TONES,
  confirmResolution, fetchCalendarHealth, fetchSessionDetail, formatWhen, previewResolution, recheckOccurrence, shortId,
  type CalendarHealth, type HealthFilter, type HealthSession, type ResolutionAction, type ResolutionPreview,
  type SessionDetail, type TimelineEntry,
} from './calendarHealth';

/**
 * Calendar health for one module's Teams calendar.
 *
 * Opening it, filtering, paging and opening a session only read the LMS: no
 * Microsoft call, no email, no change. A correction happens only through a
 * resolution review that shows what will and will not change, and only after
 * the person ticks that they have read it and confirms.
 */
export function CalendarHealthPanel({ liveSessionId, disabled }: { liveSessionId: string; disabled?: boolean }) {
  const [health, setHealth] = useState<CalendarHealth | null>(null);
  const [filter, setFilter] = useState<HealthFilter>('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openSession, setOpenSession] = useState<HealthSession | null>(null);

  const load = useCallback(async (nextFilter: HealthFilter, nextPage: number) => {
    if (!liveSessionId) return;
    setLoading(true); setError('');
    try { setHealth(await fetchCalendarHealth(liveSessionId, nextFilter, nextPage)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Calendar health could not be loaded.'); }
    finally { setLoading(false); }
  }, [liveSessionId]);

  useEffect(() => { setHealth(null); setFilter('all'); setPage(1); setOpenSession(null); void load('all', 1); }, [liveSessionId, load]);

  const choose = (next: HealthFilter) => { setFilter(next); setPage(1); void load(next, 1); };
  const go = (next: number) => { setPage(next); void load(filter, next); };

  if (!liveSessionId) return null;
  const summary = health?.summary;
  return (
    <section aria-labelledby="calendar-health-heading" className="space-y-3 rounded-xl border border-background-200 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 id="calendar-health-heading" className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">
            {health?.module.title ? `${health.module.title} — Calendar health` : 'Calendar health'}
          </h4>
          <p className="text-[11px] text-foreground-500">
            Reads what the LMS holds and the last Microsoft status check. Opening it changes nothing and sends nothing.
          </p>
        </div>
        <button type="button" onClick={() => void load(filter, page)} disabled={loading || disabled}
          className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50">
          <AppIcon className={loading ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'} />
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
      {error && <InlineError message={error} onRetry={() => void load(filter, page)} />}
      {!health && loading && <p className="text-[12px] text-foreground-500">Loading calendar health…</p>}
      {health && summary && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span data-testid="calendar-health-status"
              className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[12px] font-bold ${STATUS_TONES[summary.status]}`}>
              Status: {STATUS_LABELS[summary.status]}
            </span>
            <span className="text-[11px] text-foreground-500">
              {summary.neverVerified ? 'Microsoft has never been checked for this calendar.'
                : `Last verified ${formatWhen(summary.lastVerifiedAt)}${summary.verificationStale ? ' — status check stale, run it again' : ''}`}
            </span>
          </div>
          {summary.lastVerificationFailure && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-2 text-[11px] font-semibold text-red-800">
              The last status check failed ({formatWhen(summary.lastVerificationFailure.at)}): {summary.lastVerificationFailure.message}
            </p>
          )}
          {!summary.auditLogAvailable && (
            <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
              The integrity log is not set up on this server yet, so audit history and saved resolutions are unavailable.
            </p>
          )}
          <dl className="grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-5">
            {([
              ['Planned sessions', summary.counts.plannedSessions],
              ['Main series', summary.counts.mainSeries],
              ['Additional meetings', summary.counts.additionalMeetings],
              ['Standalone replacements', summary.counts.standaloneReplacements],
              ['Missing occurrences', summary.counts.missingOccurrences],
              ['Link conflicts', summary.counts.linkConflicts],
              ['Lifecycle issues', summary.counts.lifecycleIssues],
              ['Audit attribution issues', summary.counts.auditIssues],
              ['Not in plan', summary.counts.notInPlan],
            ] as const).map(([label, value]) => (
              <div key={label} className="rounded-lg bg-background-50 px-2 py-1.5">
                <dt className="text-foreground-500">{label}</dt>
                <dd className="text-[14px] font-bold text-foreground-900">{value}</dd>
              </div>
            ))}
          </dl>
          {health.warnings.map(warning => (
            <div key={warning.category} role={warning.severity === 'error' ? 'alert' : 'note'}
              className={`rounded-lg border p-3 text-[12px] ${warning.severity === 'notice' ? 'border-slate-200 bg-slate-50 text-slate-800'
                : warning.severity === 'error' ? 'border-red-200 bg-red-50 text-red-900' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
              <p className="font-bold">{warning.title}</p>
              <p className="mt-1">{warning.explanation}</p>
              <p className="mt-1 text-[11px]"><span className="font-semibold">Next step:</span> {warning.nextAction}</p>
              {warning.lastVerifiedAt && <p className="mt-0.5 text-[11px]">Last verified {formatWhen(warning.lastVerifiedAt)}</p>}
            </div>
          ))}
          <div role="group" aria-label="Filter sessions" className="flex flex-wrap gap-1.5">
            {(Object.keys(FILTER_LABELS) as HealthFilter[]).map(key => (
              <button key={key} type="button" aria-pressed={filter === key} onClick={() => choose(key)} disabled={loading}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${filter === key ? 'border-primary-300 bg-primary-50 text-primary-800' : 'border-background-200'}`}>
                {FILTER_LABELS[key]}
              </button>
            ))}
          </div>
          <SessionTable sessions={health.sessions.items} onView={setOpenSession} />
          {health.sessions.pages > 1 && (
            <nav aria-label="Session pages" className="flex items-center justify-between text-[11px]">
              <button type="button" onClick={() => go(page - 1)} disabled={loading || health.sessions.page <= 1}
                className="rounded-lg border px-2.5 py-1 font-semibold disabled:opacity-50">Previous</button>
              <span>Page {health.sessions.page} of {health.sessions.pages} · {health.sessions.total} sessions</span>
              <button type="button" onClick={() => go(page + 1)} disabled={loading || health.sessions.page >= health.sessions.pages}
                className="rounded-lg border px-2.5 py-1 font-semibold disabled:opacity-50">Next</button>
            </nav>
          )}
        </>
      )}
      {openSession && (
        <SessionDrawer
          liveSessionId={liveSessionId}
          session={openSession}
          lastVerifiedAt={health?.summary.lastVerifiedAt || ''}
          disabled={disabled}
          onClose={() => setOpenSession(null)}
          onResolved={() => void load(filter, page)}
        />
      )}
    </section>
  );
}

function SessionTable({ sessions, onView }: { sessions: HealthSession[]; onView: (session: HealthSession) => void }) {
  if (!sessions.length) return <p className="text-[12px] text-foreground-500">No sessions match this filter.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[11px]">
        <thead className="text-foreground-500">
          <tr>
            <th scope="col" className="py-1 pr-2">Week</th>
            <th scope="col" className="py-1 pr-2">Date &amp; time</th>
            <th scope="col" className="py-1 pr-2">Meeting type</th>
            <th scope="col" className="py-1 pr-2">Link integrity</th>
            <th scope="col" className="py-1 pr-2">Lifecycle</th>
            <th scope="col" className="py-1 pr-2">Audit</th>
            <th scope="col" className="py-1">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map(session => (
            <tr key={session.occurrenceId} className="border-t border-background-200 align-top">
              <td className="py-1.5 pr-2 font-semibold">
                {session.meetingType === 'additional' ? (session.title || 'Additional') : session.sessionNumber > 0 ? session.sessionNumber : 'Former'}
              </td>
              <td className="py-1.5 pr-2">{formatWhen(session.startDateTimeUtc)}</td>
              <td className="py-1.5 pr-2">{MEETING_TYPE_LABELS[session.meetingType]}</td>
              <td className="py-1.5 pr-2">
                {session.integrity ? (
                  <span className={`inline-flex rounded-full border px-2 py-0.5 font-semibold ${INTEGRITY_TONES[session.integrity]}`}>
                    {INTEGRITY_LABELS[session.integrity]}{session.verificationStale ? ' (status check stale)' : ''}
                  </span>
                ) : MEMBERSHIP_LABELS[session.membership]}
                {session.resolution && <span className="block text-foreground-500">Reviewed</span>}
              </td>
              <td className="py-1.5 pr-2">
                {LIFECYCLE_LABELS[session.lifecycle] || session.lifecycle}
                {session.lifecycleIssues.some(issue => issue.confirmed)
                  ? <span className="block font-semibold text-red-700">Issue</span>
                  : session.lifecycleIssues.length > 0 && <span className="block text-amber-800">Check</span>}
                {Boolean(session.offScheduleRuns?.length) && (
                  <span className="block text-amber-800"
                    title={(session.offScheduleRuns || []).map(run => offScheduleRunSentence(run, formatWhen)).join(' ')}>
                    {offScheduleRunLabel(session.offScheduleRuns)}
                  </span>
                )}
              </td>
              <td className="py-1.5 pr-2">{session.auditIssues.length ? 'Unknown attribution' : 'Known or not required'}</td>
              <td className="py-1.5">
                <button type="button" onClick={() => onView(session)}
                  className="rounded-lg border px-2 py-0.5 font-semibold">
                  View
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SessionDrawer({ liveSessionId, session, lastVerifiedAt, disabled, onClose, onResolved }: {
  liveSessionId: string; session: HealthSession; lastVerifiedAt: string; disabled?: boolean;
  onClose: () => void; onResolved: () => void;
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState('');
  const [view, setView] = useState<'details' | 'compare' | 'audit' | 'status'>('details');
  const [reviewing, setReviewing] = useState<ResolutionAction | null>(null);
  const [notice, setNotice] = useState('');
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setError('');
    try { setDetail(await fetchSessionDetail(liveSessionId, session.occurrenceId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'This session could not be loaded.'); }
  }, [liveSessionId, session.occurrenceId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { panel.current?.focus(); }, []);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !reviewing) onClose(); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose, reviewing]);

  const title = session.meetingType === 'additional' ? (session.title || 'Additional meeting') : `Week ${session.sessionNumber}`;
  return (
    <div className="fixed inset-0 z-[70] flex justify-end bg-slate-900/40" onClick={onClose}>
      <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="session-drawer-title"
        className="h-full w-full max-w-xl overflow-y-auto bg-white p-4 shadow-2xl outline-none" onClick={event => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 id="session-drawer-title" className="text-[15px] font-bold text-foreground-900">{title}</h3>
            <p className="text-[11px] text-foreground-500">{MEETING_TYPE_EXPLANATIONS[session.meetingType]}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border px-2.5 py-1 text-[12px] font-semibold">Close</button>
        </div>
        {error && <div className="mt-3"><InlineError message={error} onRetry={() => void load()} /></div>}
        {notice && <p role="status" className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-[12px] text-emerald-900">{notice}</p>}
        {!detail && !error && <p className="mt-3 text-[12px] text-foreground-500">Loading session…</p>}
        {detail && (
          <div className="mt-3 space-y-3">
            {detail.separateFromMain && detail.meetingType === 'standalone' && (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-red-900">
                <p className="font-bold">Different Teams Meeting Detected</p>
                <p className="mt-1">This session uses a separate Teams meeting from the module&apos;s main recurring calendar.
                  Learners using previously saved links or Teams chats may enter a different meeting room.</p>
                <p className="mt-1 text-[11px]">Main meeting {shortId(detail.mainMeeting.eventId)} · This session {shortId(detail.meeting.eventId)}
                  {' · '}{lastVerifiedAt ? `Calendar last verified ${formatWhen(lastVerifiedAt)}` : 'Microsoft not yet checked'}
                </p>
                <p className="mt-1 text-[11px] font-semibold">Recommended: compare the meetings, then choose a resolution below.</p>
              </div>
            )}
            {detail.integrity === 'missing' && (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-red-900">
                <p className="font-bold">Missing from Teams — Resolution Required</p>
                <p className="mt-1">{INTEGRITY_EXPLANATIONS.missing} No separate meeting was created automatically.</p>
              </div>
            )}
            {detail.lifecycleIssues.filter(issue => issue.confirmed).map(issue => (
              <div key={issue.code} role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-red-900">
                <p className="font-bold">Invalid Session Completion</p>
                <p className="mt-1">{issue.code === 'completed_before_start'
                  ? 'This session was marked Completed before its scheduled start.' : issue.message}</p>
                <p className="mt-1 text-[11px]">Scheduled {formatWhen(detail.startDateTimeUtc)} – {formatWhen(detail.endDateTimeUtc)} ·
                  Recorded run {formatWhen(detail.actualStartUtc)} – {formatWhen(detail.actualEndUtc)} ·
                  Attendance records {detail.evidence.attendanceRecords ?? 'unknown'}</p>
                <button type="button" onClick={() => setView('status')} className="mt-1.5 rounded-lg border border-red-200 bg-white px-2 py-0.5 text-[11px] font-semibold">
                  View status history
                </button>
              </div>
            ))}
            <div role="tablist" aria-label="Session views" className="flex flex-wrap gap-1.5">
              {([['details', 'Details'], ['compare', 'Compare meetings'], ['audit', 'Audit history'], ['status', 'Status history']] as const).map(([key, label]) => (
                <button key={key} type="button" role="tab" aria-selected={view === key} onClick={() => setView(key)}
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${view === key ? 'border-primary-300 bg-primary-50 text-primary-800' : 'border-background-200'}`}>
                  {label}
                </button>
              ))}
            </div>
            {view === 'details' && <SessionDetails detail={detail} />}
            {view === 'compare' && <CompareMeetings detail={detail} />}
            {view === 'audit' && <Timeline entries={detail.auditHistory} empty="No recorded audit events for this session. Earlier actions are shown as unknown, not inferred." />}
            {view === 'status' && <Timeline entries={detail.statusHistory} empty="No recorded status changes. A completion made before the integrity log existed has no recorded source." />}
            {detail.resolutions.length > 0 && (
              <section aria-labelledby="resolution-heading" className="space-y-1.5 rounded-lg border border-background-200 p-3">
                <h4 id="resolution-heading" className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Review resolution</h4>
                <p className="text-[11px] text-foreground-500">Each option shows what it will change before anything happens.</p>
                <div className="flex flex-wrap gap-1.5">
                  {detail.resolutions.map(option => (
                    <button key={option.action} type="button" disabled={disabled}
                      onClick={() => setReviewing(option.action)}
                      className="rounded-lg border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-50">
                      {option.label}
                    </button>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
        {reviewing && detail && (
          <ResolutionReview
            liveSessionId={liveSessionId}
            occurrenceId={detail.occurrenceId}
            action={reviewing}
            onClose={() => setReviewing(null)}
            onDone={(message) => { setReviewing(null); setNotice(message); void load(); onResolved(); }}
          />
        )}
      </div>
    </div>
  );
}

function SessionDetails({ detail }: { detail: SessionDetail }) {
  const attribution = detail.attribution;
  return (
    <div className="space-y-3">
      <div>
        <h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Session</h5>
        <DetailRow label="Programme" value={detail.module.programme || 'Not recorded'} />
        <DetailRow label="Cohort" value={detail.module.cohort || 'Not recorded'} />
        <DetailRow label="Group" value={detail.module.group || 'Not recorded'} />
        <DetailRow label="Module" value={detail.module.title || 'Not recorded'} />
        <DetailRow label="Week" value={detail.sessionNumber > 0 ? detail.sessionNumber : 'Not in plan'} />
        <DetailRow label="Scheduled" value={`${formatWhen(detail.startDateTimeUtc)} – ${formatWhen(detail.endDateTimeUtc)}`} />
        <DetailRow label="Lifecycle" value={LIFECYCLE_LABELS[detail.lifecycle] || detail.lifecycle} />
        {detail.lifecycleIssues.filter(issue => !issue.confirmed).map(issue => (
          <p key={issue.code} role="note" className="mt-1 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
            {issue.message}
          </p>
        ))}
        {(detail.offScheduleRuns || []).map(run => (
          <p key={`${run.runStartUtc}-${run.runEndUtc}`} role="note"
            className="mt-1 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
            {offScheduleRunSentence(run, formatWhen)}
          </p>
        ))}
        <DetailRow label="Calendar membership" value={MEMBERSHIP_LABELS[detail.membership]} />
      </div>
      <div>
        <h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Microsoft Teams</h5>
        <DetailRow label="Meeting type" value={MEETING_TYPE_LABELS[detail.meetingType]} />
        <DetailRow label="Organiser" value={detail.mainMeeting.organizer || 'Not recorded'} />
        <DetailRow label="Event ID" value={<span className="break-all font-mono text-[11px]">{detail.meeting.eventId || 'Not recorded'}</span>} />
        <DetailRow label="Online meeting ID" value={<span className="break-all font-mono text-[11px]">{detail.meeting.onlineMeetingId || 'Uses the series meeting'}</span>} />
        <DetailRow label="Recurring series ID" value={<span className="break-all font-mono text-[11px]">{detail.mainMeeting.eventId || 'Not recorded'}</span>} />
        <DetailRow label="Join link" value={detail.meeting.joinUrl
          ? <a className="break-all text-primary-700 underline" href={detail.meeting.joinUrl} target="_blank" rel="noreferrer">Open this session&apos;s link</a>
          : 'Not recorded'} />
        <DetailRow label="Expected association" value={detail.meetingType === 'main' ? 'Main recurring series' : detail.meetingType === 'additional' ? 'Its own additional meeting' : 'Main recurring series (this session differs)'} />
        <DetailRow label="Verification" value={detail.integrity
          ? `${INTEGRITY_LABELS[detail.integrity]}${detail.verificationStale ? ' (status check stale)' : ''}` : 'Not applicable'} />
        {detail.integrity && <p className="text-[11px] text-foreground-500">{INTEGRITY_EXPLANATIONS[detail.integrity]}</p>}
      </div>
      <div>
        <h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Creation and modification</h5>
        <DetailRow label="LMS record created" value={formatWhen(detail.createdAt)} />
        <DetailRow label="LMS record last changed" value={formatWhen(detail.updatedAt)} />
        {attribution && <>
          <DetailRow label="Separate meeting created by" value={attribution.label} />
          <DetailRow label="Trigger" value={attribution.trigger || 'Not recorded'} />
          {attribution.jobId && <DetailRow label="Job" value={attribution.jobId} />}
        </>}
        {detail.auditIssues.map(issue => (
          <p key={issue.code} role="note" className="mt-1 rounded-lg border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-800">
            <span className="font-semibold">Audit Attribution Incomplete:</span> {issue.message}
          </p>
        ))}
      </div>
      <div>
        <h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Session evidence</h5>
        <DetailRow label="Attendance run linked" value={detail.attendanceReportLinked ? 'Yes' : 'No'} />
        <DetailRow label="Attendance records" value={detail.evidence.attendanceRecords ?? 'Unknown'} />
        <DetailRow label="Recordings" value={detail.evidence.recordings ?? 'Unknown'} />
        <DetailRow label="Transcripts" value={detail.evidence.transcripts ?? 'Unknown'} />
      </div>
    </div>
  );
}

function CompareMeetings({ detail }: { detail: SessionDetail }) {
  const { main, session, implications } = detail.compare;
  const same = main.joinRef === session.joinRef;
  const rows: [string, string, string][] = [
    ['Relationship', main.membership, session.membership],
    ['Event ID', main.eventId, session.eventId],
    ['Online meeting ID', main.onlineMeetingId, session.onlineMeetingId || 'Series meeting'],
    ['Organiser', main.organizer || '', main.organizer || ''],
    ['Join link reference', main.joinRef, session.joinRef],
    ['Scheduled', 'Recurring', `${formatWhen(session.startDateTimeUtc)}`],
  ];
  return (
    <div className="space-y-2">
      <p className={`rounded-lg border p-2 text-[12px] font-semibold ${same ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
        {same ? 'This session uses the same Teams meeting as the main series.' : 'This session uses a different Teams meeting from the main series.'}
      </p>
      <table className="w-full text-left text-[11px]">
        <thead className="text-foreground-500"><tr><th scope="col" className="py-1 pr-2"> </th><th scope="col" className="py-1 pr-2">Main series</th><th scope="col" className="py-1">This session</th></tr></thead>
        <tbody>
          {rows.map(([label, left, right]) => (
            <tr key={label} className="border-t border-background-200 align-top">
              <th scope="row" className="py-1 pr-2 font-semibold text-foreground-600">{label}</th>
              <td className="break-all py-1 pr-2 font-mono">{left || 'Not recorded'}</td>
              <td className={`break-all py-1 font-mono ${left !== right ? 'font-bold text-red-800' : ''}`}>{right || 'Not recorded'}</td>
            </tr>
          ))}
          <tr className="border-t border-background-200 align-top">
            <th scope="row" className="py-1 pr-2 font-semibold text-foreground-600">Evidence on this session</th>
            <td className="py-1 pr-2">—</td>
            <td className="py-1">{detail.evidence.attendanceRecords ?? '?'} attendance · {detail.evidence.recordings ?? '?'} recordings · {detail.evidence.transcripts ?? '?'} transcripts</td>
          </tr>
        </tbody>
      </table>
      {implications.length > 0 && (
        <div className="rounded-lg border border-background-200 p-2 text-[11px]">
          <p className="font-semibold">What this can mean in practice</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{implications.map(line => <li key={line}>{line}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function Timeline({ entries, empty }: { entries: TimelineEntry[]; empty: string }) {
  if (!entries.length) return <p className="text-[12px] text-foreground-500">{empty}</p>;
  return (
    <ol className="space-y-2">
      {entries.map(entry => (
        <li key={entry.id} className="rounded-lg border border-background-200 p-2 text-[11px]">
          <p className="font-semibold text-foreground-900">{EVENT_LABELS[entry.type] || entry.type}</p>
          <p className="text-foreground-600">{formatWhen(entry.at)} · {entry.by}{entry.trigger ? ` · ${entry.trigger}` : ''}</p>
          <p className="text-foreground-500">Executed by {entry.executedBy || 'LMS'} · Outcome: {entry.outcome}
            {entry.correlationId ? ` · ref ${entry.correlationId.slice(0, 8)}` : ''}</p>
          {(entry.before?.eventId || entry.after?.eventId) && (
            <p className="break-all text-foreground-500">
              Meeting {shortId(entry.before?.eventId || '')} → {shortId(entry.after?.eventId || '')}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

function ResolutionReview({ liveSessionId, occurrenceId, action, onClose, onDone }: {
  liveSessionId: string; occurrenceId: string; action: ResolutionAction; onClose: () => void; onDone: (message: string) => void;
}) {
  const [preview, setPreview] = useState<ResolutionPreview | null>(null);
  const [error, setError] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    let live = true;
    if (action === 'recheck_occurrence') return () => { live = false; };
    previewResolution(liveSessionId, occurrenceId, action)
      .then(result => { if (live) setPreview(result); })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'The review could not be prepared.'); });
    return () => { live = false; };
  }, [liveSessionId, occurrenceId, action]);

  const recheck = async () => {
    setWorking(true); setError('');
    try {
      const result = await recheckOccurrence(liveSessionId, occurrenceId);
      onDone(result.present ? 'Microsoft now shows this occurrence in the recurring series.' : 'Microsoft still has no occurrence of the series on this date.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'The re-check could not finish.'); }
    finally { setWorking(false); }
  };

  const confirm = async () => {
    if (!preview) return;
    setWorking(true); setError('');
    try {
      const result = await confirmResolution(liveSessionId, occurrenceId, preview);
      onDone(`${preview.label}: done.${result.warnings?.length ? ` ${result.warnings.join(' ')}` : ''}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Nothing was changed.'); }
    finally { setWorking(false); }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-900/50 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="resolution-review-title" className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-4 shadow-2xl">
        <h3 id="resolution-review-title" className="text-[15px] font-bold">
          {action === 'recheck_occurrence' ? 'Re-check Microsoft' : `Review: ${preview?.label || '…'}`}
        </h3>
        {error && <div className="mt-2"><InlineError message={error} /></div>}
        {action === 'recheck_occurrence' ? (
          <div className="mt-2 space-y-2 text-[12px]">
            <p>Reads the recurring series from Microsoft and reports whether this occurrence is there now. It changes nothing and sends nothing.</p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="rounded-lg border px-3 py-1.5 font-semibold">Close</button>
              <button type="button" onClick={() => void recheck()} disabled={working} className="rounded-lg border border-primary-300 bg-primary-50 px-3 py-1.5 font-semibold disabled:opacity-50">
                {working ? 'Checking…' : 'Re-check now'}
              </button>
            </div>
          </div>
        ) : !preview ? (!error && <p className="mt-2 text-[12px] text-foreground-500">Preparing the review…</p>) : (
          <div className="mt-2 space-y-2 text-[12px]">
            <Section title="What will change" items={preview.changes} />
            <Section title="What stays the same" items={preview.unchanged} />
            <ul className="space-y-0.5 rounded-lg bg-background-50 p-2 text-[11px]">
              <li>Microsoft meetings created: <strong>{preview.microsoft.creates ? 'One' : 'None'}</strong></li>
              <li>Microsoft meetings updated: <strong>{preview.microsoft.updates ? 'Yes' : 'None'}</strong></li>
              <li>Microsoft meetings cancelled: <strong>None</strong></li>
              <li>Microsoft emails: <strong>{preview.microsoft.mayEmail ? 'Microsoft may email the people on the new meeting' : 'None expected from this action'}</strong></li>
              <li>LMS emails: <strong>{preview.lmsEmail ? 'Yes' : 'None'}</strong></li>
            </ul>
            {preview.followUp.length > 0 && <Section title="Manual follow-up" items={preview.followUp} />}
            {preview.blocked ? (
              <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-2 font-semibold text-red-800">Blocked: {preview.blocked}</p>
            ) : (
              <label className="flex items-start gap-2 text-[12px]">
                <input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} className="mt-0.5" />
                <span>I have read what this will and will not change.</span>
              </label>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="rounded-lg border px-3 py-1.5 font-semibold">Cancel</button>
              <button type="button" onClick={() => void confirm()} disabled={working || !acknowledged || Boolean(preview.blocked)}
                className="rounded-lg border border-red-300 bg-red-50 px-3 py-1.5 font-semibold text-red-800 disabled:opacity-50">
                {working ? 'Working…' : `Confirm: ${preview.label}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="font-semibold">{title}</p>
      <ul className="list-disc space-y-0.5 pl-5">{items.map(item => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}
