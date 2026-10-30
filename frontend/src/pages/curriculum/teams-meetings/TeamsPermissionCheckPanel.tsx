import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { coachFetch } from '@/lib/coachFetch';
import { InlineError } from '../shared/entities/ui';

interface GraphErrorDetail { status: number | null; code: string; message: string; requestId: string; at: string }
interface PermissionCheckItem {
  key: string; status: 'pass' | 'fail' | 'unknown'; summary: string;
  graphError?: GraphErrorDetail; roles?: string[]; groups?: string[];
}
export interface TeamsPermissionReport {
  checkedAt: string; readOnly: true; verdict: 'pass' | 'fail' | 'unknown';
  app: { clientId: string; tenantId: string; permissionContext: string };
  organizer: { email: string; objectId: string; directoryObjectId: string };
  meeting: { onlineMeetingId: string; graphPath: string };
  checks: PermissionCheckItem[]; actions: string[]; adminCommands: string[];
}

/** Read-only: GET requests to Microsoft only. Grants, changes and sends nothing. */
async function checkTeamsPermissions(liveSessionId: string): Promise<TeamsPermissionReport> {
  const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
  const response = await coachFetch(`${base}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/permission-check/`, { method: 'GET' });
  const result = await response.json();
  if (!response.ok) throw new Error(result?.error || 'The permission check could not finish. Nothing was changed.');
  if (!Array.isArray(result?.checks) || typeof result?.verdict !== 'string') throw new Error('The permission check returned an unreadable report.');
  return result as TeamsPermissionReport;
}

const statusTone = { pass: 'text-emerald-700', fail: 'text-red-700', unknown: 'text-foreground-500' } as const;
const statusLabel = { pass: 'OK', fail: 'Problem', unknown: 'Not known' } as const;

export function TeamsPermissionCheckPanel({ liveSessionId, disabled }: { liveSessionId: string; disabled?: boolean }) {
  const [report, setReport] = useState<TeamsPermissionReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setReport(null); setError(''); }, [liveSessionId]);
  const run = async () => {
    setChecking(true); setError('');
    try { setReport(await checkTeamsPermissions(liveSessionId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The permission check could not finish.'); }
    finally { setChecking(false); }
  };
  return (
    <section aria-label="Microsoft permission check" className="space-y-3 rounded-xl border border-background-200 p-3">
      <div>
        <h4 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Microsoft permission check</h4>
        <p className="text-[11px] text-foreground-500">Reads whether the LMS app may manage this meeting for its organiser. It changes nothing, grants nothing and sends nothing.</p>
      </div>
      <button type="button" disabled={disabled || checking || !liveSessionId} onClick={() => void run()}
        className="inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50">
        <AppIcon className={checking ? 'ri-loader-4-line animate-spin' : 'ri-shield-keyhole-line'} />
        {checking ? 'Checking…' : 'Check Microsoft permissions'}
      </button>
      {error && <InlineError message={error} />}
      {report && <div className="space-y-2 text-[12px]">
        <p className="font-semibold">Result: <span className={statusTone[report.verdict]}>{statusLabel[report.verdict]}</span>
          <span className="font-normal text-foreground-500"> · checked {new Date(report.checkedAt).toLocaleString('en-GB')}</span></p>
        <dl className="grid gap-x-3 gap-y-1 text-[11px] sm:grid-cols-[auto_1fr]">
          <dt className="text-foreground-500">Organiser</dt><dd className="break-all">{report.organizer.email || '—'}</dd>
          <dt className="text-foreground-500">Organiser object ID</dt><dd className="break-all">{report.organizer.objectId || '—'}</dd>
          <dt className="text-foreground-500">App client ID</dt><dd className="break-all">{report.app.clientId || '—'}</dd>
          <dt className="text-foreground-500">Permission context</dt><dd>{report.app.permissionContext}</dd>
        </dl>
        <ul className="space-y-1.5">{report.checks.map(item => <li key={item.key} className="rounded-lg bg-background-50 px-2.5 py-1.5">
          <p><span className={`font-semibold ${statusTone[item.status]}`}>{statusLabel[item.status]}:</span> {item.summary}</p>
          {item.graphError && <p className="mt-0.5 break-all text-[11px] text-foreground-500">
            Microsoft said: HTTP {item.graphError.status ?? '—'} {item.graphError.code} {item.graphError.message}
            {item.graphError.requestId ? ` · request ID ${item.graphError.requestId}` : ''}{item.graphError.at ? ` · ${item.graphError.at}` : ''}</p>}
        </li>)}</ul>
        {!!report.actions.length && <div><h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">What to do</h5>
          <ul className="list-disc space-y-1 pl-5">{report.actions.map(action => <li key={action}>{action}</li>)}</ul></div>}
        <details><summary className="cursor-pointer text-[11px] font-semibold">Commands for a Microsoft 365 administrator</summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-lg bg-background-50 p-2 text-[11px]">{report.adminCommands.join('\n')}</pre></details>
      </div>}
    </section>
  );
}
