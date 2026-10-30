import { useCallback, useEffect, useRef, useState } from 'react';
import { loadModuleSessions, requestSessionSync, type SessionResult } from '@/api/sessionResults';
import { SessionResults } from '@/components/feature/SessionResults';
import { SessionSyncStatus } from '@/components/feature/SessionSyncStatus';
import { formatSystemTimestamp } from '@/lib/format';
import { useSavedSessionData } from '@/hooks/useSavedSessionData';

export function SessionResultsDialog({ moduleId, onClose }: { moduleId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => previous?.focus();
  }, []);
  return <div className="fixed inset-0 z-[75] flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-label="Module session results" tabIndex={-1}
      onClick={event => event.stopPropagation()} onKeyDown={event => {
        if (event.key === 'Escape') onClose();
        if (event.key === 'Tab') {
          const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input,[tabindex="0"]') || []);
          const first = controls[0], last = controls[controls.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }} className="max-h-[90vh] w-full max-w-6xl overflow-auto rounded-2xl bg-background-50 p-5">
      <button type="button" onClick={onClose} className="mb-4 rounded-lg border bg-white px-3 py-2 text-sm">Close sessions</button>
      <ModuleSessions moduleId={moduleId} />
    </div>
  </div>;
}

export function ModuleSessions({ moduleId }: { moduleId: string }) {
  const [selected, setSelected] = useState<SessionResult>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ seriesId: string; message: string } | null>(null);
  const [busy, setBusy] = useState('');
  const load = useCallback((signal: AbortSignal) => loadModuleSessions(moduleId, signal), [moduleId]);
  const saved = useSavedSessionData(load, !selected, data => Boolean(data?.jobs.some(job => ['queued', 'running'].includes(job.state))));
  const { data, loading } = saved;
  useEffect(() => { setSelected(undefined); setNotice(null); setError(''); }, [moduleId]);
  const sync = async (id: string) => {
    setBusy(id); setError(''); setNotice(null);
    try { const result = await requestSessionSync(id); setNotice({ seriesId: id, message: result.message }); saved.refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not request synchronization.'); }
    finally { setBusy(''); }
  };
  const otherSaved = data?.series.flatMap(series => series.sessions).filter(session =>
    session.id !== selected?.id && (session.fileCount || 0) > 0) || [];
  if (selected) return <div className="space-y-4"><button type="button" className="text-sm font-semibold text-primary-700" onClick={() => setSelected(undefined)}>← All sessions</button>
    {otherSaved.length > 0 && <aside className="rounded-xl border bg-primary-50/40 p-3 text-sm" aria-label="Other sessions with saved files">
      <p className="font-semibold">Other sessions with saved files</p><div className="mt-2 flex flex-wrap gap-2">{otherSaved.map(session =>
        <button key={session.id} type="button" onClick={() => setSelected(session)} className="rounded-lg border bg-white px-3 py-2 text-primary-700">
          {session.itemTitle || `Session ${session.sessionNumber}`} · {formatSystemTimestamp(session.startsAt)} · {session.fileCount} files
        </button>)}</div></aside>}
    <SessionResults seriesId={selected.seriesId} sessionNumber={selected.sessionNumber} moduleId={moduleId} /></div>;
  return <section className="space-y-4" aria-label="Sessions and recordings">
    <header className="flex items-center justify-between gap-4"><div><h2 className="text-lg font-bold">Sessions & Recordings</h2><p className="mt-1 text-sm text-foreground-500">Open a session to review its recording, transcript and attendance.</p></div>
      <button disabled={loading || saved.refreshing} aria-busy={saved.refreshing} onClick={saved.refresh} className="rounded-lg border bg-white px-3 py-2 text-sm disabled:opacity-60">{saved.refreshing && !loading ? 'Refreshing…' : 'Refresh'}</button></header>
    {(error || saved.error) && <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-800">{error || saved.error}</p>}
    {data?.warning && <p role="status" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">{data.warning}</p>}
    {notice && !data?.jobs.some(job => job.live_session_id === notice.seriesId) && <p role="status" className="rounded-lg bg-primary-50 p-4 text-sm">{notice.message}</p>}
    {loading ? <p role="status">Loading saved sessions…</p> : !data?.series.length ? !error && !saved.error && <p className="rounded-xl border bg-white p-6 text-sm">No Teams sessions are linked to this module yet.</p> : data.series.map(series => <div key={series.id} className="overflow-hidden rounded-xl border bg-white">
      <div className="flex items-center justify-between gap-3 border-b bg-primary-50/40 p-4"><strong>{series.title}</strong><button disabled={Boolean(busy) || data.syncAvailable === false} onClick={() => void sync(series.id)} className="rounded-lg bg-primary-600 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{busy === series.id ? 'Requesting…' : 'Sync attendance & files'}</button></div>
      {data.jobs.filter(job => job.live_session_id === series.id).map(job => <SessionSyncStatus key={job.live_session_id} job={job} />)}
      <ul className="divide-y">{series.sessions.map(session => <li key={session.id}><button onClick={() => setSelected(session)} className="flex w-full items-center justify-between gap-4 p-4 text-left hover:bg-background-50"><span><strong className="block text-sm">{session.itemTitle || `Session ${session.sessionNumber}`}</strong><span className="text-xs text-foreground-500">{session.itemTitle ? `Session ${session.sessionNumber} · ` : ''}{formatSystemTimestamp(session.startsAt)}</span></span><span className="text-right text-xs"><span className="block font-semibold">{session.reportReady ? 'Attendance report saved' : 'Awaiting attendance report'}</span><span className="text-foreground-500">{session.fileCount || 0} files · Open session →</span></span></button></li>)}</ul>
    </div>)}
  </section>;
}
