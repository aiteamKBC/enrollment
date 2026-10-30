import { useEffect, useState } from 'react';
import type { SessionSyncJob } from '@/api/sessionResults';
import { SessionSyncIssues } from './SessionSyncIssues';

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function SessionSyncStatus({ job }: { job?: SessionSyncJob | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (job?.state !== 'running') return;
    const timer = window.setInterval(() => setNow(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, [job?.state]);
  if (!job) return null;
  const failed = job.state === 'failed';
  const running = job.state === 'running';
  const complete = job.state === 'complete';
  const progress = job.progress;
  const transfer = running || failed ? progress?.transfer : null;
  const phase = transfer?.phase;
  const transferring = phase === 'downloading' || phase === 'uploading';
  const knownTotal = transferring && transfer && transfer.totalBytes !== null && transfer.totalBytes > 0
    && transfer.bytesTransferred <= transfer.totalBytes;
  const percent = knownTotal && transfer ? Math.floor(100 * transfer.bytesTransferred / transfer.totalBytes!) : undefined;
  const stale = running && transfer && now - Date.parse(transfer.updatedAt) > 60_000;
  const stage = phase === 'downloading' ? `Downloading ${transfer?.type}`
    : phase === 'uploading' ? `Uploading ${transfer?.type} to Azure`
      : phase === 'finalizing' ? `Finishing ${transfer?.type}`
        : phase === 'preparing' ? `Preparing ${transfer?.type}`
          : running ? progress && progress.totalFiles > 0 && progress.filesReady === progress.totalFiles
            ? 'Finishing sync' : 'Waiting for a transfer progress update'
            : complete ? 'Sync pass complete' : failed ? 'Sync needs retry' : 'Waiting to start';
  const size = transferring && transfer
    ? knownTotal ? `${formatBytes(transfer.bytesTransferred)} of ${formatBytes(transfer.totalBytes!)}`
      : `${formatBytes(transfer.bytesTransferred)} transferred · Total size not available`
    : '';
  const message = failed ? 'Sync needs retry. Please request synchronization again.'
    : running ? 'Sync in progress. Attendance and files will appear as they are saved.'
      : complete ? 'Sync finished. Files not yet available from Teams will be checked again automatically.'
        : 'Sync queued. Waiting for processing to start.';
  const value = complete ? 100 : percent;
  const explained = failed && Boolean(job.issues?.length || (!job.issues && job.last_error));
  return <div className={`space-y-3 px-4 py-4 text-sm ${failed ? 'bg-red-50 text-red-800' : complete ? 'bg-emerald-50' : 'bg-primary-50/60'}`}>
    {explained
      // Plain wording per failure category; the stored diagnostic sits under Technical details.
      ? <div role="alert"><SessionSyncIssues job={job} filesReady={progress?.filesReady} totalFiles={progress?.totalFiles} /></div>
      : <p role={failed ? 'alert' : 'status'}>{message}</p>}
    {(!failed || value !== undefined) && <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="font-semibold">{stage}{transfer ? ` · Session ${transfer.sessionNumber}` : ''}</span>
      {value !== undefined && <span className="font-semibold tabular-nums">{value}%{transferring ? phase === 'downloading' ? ' downloaded' : ' uploaded' : ''}</span>}
    </div>}
    {(!failed || value !== undefined) && <div role="progressbar" aria-label={stage}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}
      aria-valuetext={[stage, size, stale ? 'No recent progress update' : ''].filter(Boolean).join('. ')}
      className="h-2.5 overflow-hidden rounded-full bg-primary-100">
      <div className={`h-full rounded-full ${failed ? 'bg-red-500' : complete ? 'bg-emerald-500' : 'bg-primary-600'} ${value === undefined && !stale ? 'motion-safe:animate-pulse' : ''}`}
        style={{ width: value === undefined ? '33%' : `${value}%` }} />
    </div>}
    <div className="flex flex-wrap justify-between gap-2 text-xs text-foreground-600">
      {size && <span>{size}</span>}
      {progress && progress.totalFiles > 0 && !explained && <span>{progress.filesReady} of {progress.totalFiles} discovered files saved</span>}
      {running && <span>Updates automatically every 5 seconds</span>}
    </div>
    {stale && <p className="text-xs text-amber-800">No new transfer update for over a minute. These are the last reported values; completion is not confirmed.</p>}
  </div>;
}
