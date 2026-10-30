import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { CoachMeetingAttendanceSummary, CoachMeetingAttendanceTrackerRow } from './calendarEvents';
import { reviewAttendanceLinks, type AttendanceLinkContext, type AttendanceLinkChange } from './reviewAttendanceLinks';

const button = 'rounded-md border border-primary-200 bg-white px-3 py-1.5 text-xs font-semibold text-primary-700 hover:bg-primary-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 disabled:opacity-50';
const auditTime = (value: string) => new Date(value).toLocaleString('en-GB', { timeZone: 'Europe/London' }) + ' (UK)';

export function ReviewAttendanceEditor({ eventKey, attendance, children }: {
  eventKey: string;
  attendance: CoachMeetingAttendanceSummary | null;
  children: (attendance: CoachMeetingAttendanceSummary | null, controls: (row: CoachMeetingAttendanceTrackerRow) => ReactNode) => ReactNode;
}) {
  const [context, setContext] = useState<AttendanceLinkContext | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState<string | null>(null);
  const [targetKey, setTargetKey] = useState('');
  const [reason, setReason] = useState('');
  const [undoId, setUndoId] = useState<number | null>(null);
  const generation = useRef(0);
  const saving = useRef(false);
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    setLoading(true); setContext(null); setError(''); setSelection(null); setUndoId(null); setBusy(false);
    reviewAttendanceLinks(eventKey, undefined, controller.signal).then(value => {
      if (generation.current === current) setContext(value);
    }).catch(e => {
      if (!controller.signal.aborted && generation.current === current) setError(e instanceof Error ? e.message : 'Could not load attendance corrections.');
    }).finally(() => { if (generation.current === current) setLoading(false); });
    return () => { generation.current += 1; controller.abort(); };
  }, [eventKey, attendance, revision]);

  async function save(change: AttendanceLinkChange) {
    if (saving.current || loading) return;
    saving.current = true; setBusy(true); setError('');
    const current = generation.current;
    try {
      const result = await reviewAttendanceLinks(eventKey, change);
      if (generation.current === current) {
        setContext(result); setSelection(null); setUndoId(null); setReason('');
      }
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : 'Attendance correction failed.');
    } finally {
      saving.current = false;
      if (generation.current === current) setBusy(false);
    }
  }

  const validReason = reason.trim().length >= 3 && reason.trim().length <= 500;
  const controls = (row: CoachMeetingAttendanceTrackerRow) => {
    const candidate = context?.candidates.find(c => c.rowId === row.id);
    if (!candidate || !context?.targets?.length || loading) return null;
    return <div className="col-span-full">
      {selection !== candidate.key ? <div className="flex justify-end"><button type="button" className={button} disabled={busy}
        aria-label={`Link ${row.displayName || row.name} to expected participant`}
        onClick={() => { setSelection(candidate.key); setTargetKey(''); setReason('Participant joined using a different email account.'); setUndoId(null); }}>Link participant</button></div>
        : <div className="mt-2 space-y-2 rounded-lg bg-primary-50 p-3" aria-label="Confirm attendance link">
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-0 basis-full text-xs font-semibold sm:flex-1 sm:basis-auto">This participant is
              <select aria-label="Expected participant" className="mt-1 block w-full rounded-md border border-primary-200 bg-white p-2" disabled={busy} value={targetKey} onChange={e => setTargetKey(e.target.value)}>
                <option value="">Select learner or employer</option>
                {context.targets.map(target => <option key={target.key} value={target.key}>{target.role === 'employer' ? 'Employer' : 'Learner'} — {target.name}</option>)}
              </select>
            </label>
            <button type="button" className={`${button} !bg-primary-700 !text-white`} disabled={busy || !validReason || !context.targets.some(target => target.key === targetKey)}
              onClick={() => void save({ action: 'link', version: context.version, participantKey: candidate.key, targetKey, reason: reason.trim() })}>{busy ? 'Saving…' : 'Confirm'}</button>
            <button type="button" className={button} disabled={busy} onClick={() => setSelection(null)}>Cancel</button>
          </div>
          <label className="block text-xs">Reason<textarea aria-label="Attendance link reason" className="mt-1 block w-full rounded-md border border-primary-200 bg-white p-2" maxLength={500} disabled={busy} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <p className="text-xs text-foreground-600">Confirm only if this participant is the selected person who joined. Applies to this meeting only. Original Teams evidence is retained.</p>
        </div>}
    </div>;
  };
  return <>
    {children(context?.attendance || attendance, controls)}
    {loading && <p role="status" className="mt-2 text-xs text-foreground-500">Loading attendance corrections…</p>}
    {error && <div role="alert" className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900">{error} <button type="button" className={button} disabled={busy} onClick={() => setRevision(n => n + 1)}>Reload attendance</button></div>}
    {context?.activeLinks.map(link => <div key={link.id} className="mt-2 rounded-lg border border-primary-100 p-3 text-xs">
      <p className="font-semibold">{link.targetRole === 'employer' ? 'Employer' : 'Learner'}{link.targetName ? ` — ${link.targetName}` : ''}</p>
      <p>Attendance link confirmed by {link.confirmedBy} · {auditTime(link.confirmedAt)}</p>
      {undoId !== link.id ? <button type="button" className={`${button} mt-2`} disabled={busy} onClick={() => { setUndoId(link.id); setSelection(null); setReason(''); }}>Undo link</button>
        : <div className="mt-2 space-y-2">
          <p>Undo this identity link? The original Teams attendance will be shown again.</p>
          <label className="block">Reason<textarea aria-label="Undo reason" className="mt-1 block w-full rounded-md border p-2" value={reason} maxLength={500} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
          <button type="button" className={button} disabled={busy || !validReason} onClick={() => void save({ action: 'undo', linkId: link.id, version: context.version, reason: reason.trim() })}>Confirm undo</button>
          <button type="button" className={`${button} ml-2`} disabled={busy} onClick={() => setUndoId(null)}>Cancel</button>
        </div>}
    </div>)}
    {Boolean(context?.history.length) && <details className="mt-2 text-xs"><summary className="cursor-pointer font-semibold text-primary-700">Attendance correction history</summary>
      <ul className="mt-2 space-y-2">{context?.history.map(link => <li key={link.id}>
        <span className="font-semibold">{link.targetRole === 'employer' ? 'Employer' : 'Learner'}{link.targetName ? ` — ${link.targetName}` : ''}: </span>
        {link.confirmedBy} · {auditTime(link.confirmedAt)} · {link.reason}
        {link.revokedAt && <p>Undone by {link.revokedBy} · {auditTime(link.revokedAt)} · {link.undoReason}</p>}
      </li>)}</ul>
    </details>}
  </>;
}
