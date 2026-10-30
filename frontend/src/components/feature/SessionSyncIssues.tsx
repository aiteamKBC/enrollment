import type { SessionSyncIssue, SessionSyncIssueCategory, SessionSyncJob } from '@/api/sessionResults';
import { formatSystemTimestamp } from '@/lib/format';

/** Staff wording for each backend category (backend/curriculum_api/session_sync_issues.py). */
interface SyncIssueCopy { title: string; summary: string; reasons: string[]; actions: string[] }

const SYNC_BUTTON = 'Sync attendance & files';
const CONTACT = 'If the problem continues, contact your LMS administrator.';

function fileLabel(type: SessionSyncIssue['fileType'], plural = false) {
  const label = type === 'attendance' ? 'attendance report' : type || 'file';
  return plural ? `${label}s` : label;
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** "Week 3: Title", or the title alone when it already names the week. */
function weekLabel(week: SessionSyncIssue['week']) {
  if (!week) return '';
  const title = week.title.trim();
  if (/^week\s*\d/i.test(title)) return title;
  const number = week.number !== null && week.number !== undefined ? `Week ${week.number}` : '';
  return [number, title].filter(Boolean).join(': ');
}

/** "Item title (Session 3, Week 3: Title)" for each distinct session in a group, in order. */
function sessionsLabel(group: SessionSyncIssue[]) {
  const sessions = new Map<number, SessionSyncIssue>();
  for (const issue of group) {
    if (issue.sessionNumber !== null && !sessions.has(issue.sessionNumber)) sessions.set(issue.sessionNumber, issue);
  }
  const entries = [...sessions].sort(([a], [b]) => a - b).map(([number, issue]) => {
    const week = weekLabel(issue.week);
    const title = issue.sessionTitle?.trim();
    if (title) return `${title} (Session ${number}${week ? `, ${week}` : ''})`;
    return week ? `Session ${number} (${week})` : `Session ${number}`;
  });
  return entries.join('; ');
}

function describeSyncIssue(category: SessionSyncIssueCategory, type: SessionSyncIssue['fileType'], stage?: string | null): SyncIssueCopy {
  const file = fileLabel(type);
  const media = type === 'recording' || type === 'transcript';
  switch (category) {
    case 'not_found':
      return {
        title: `${capital(file)} could not be saved`,
        summary: media
          ? `The LMS found this ${file} in Microsoft Teams but could not download it to LMS storage. Microsoft said it could not find the file.`
          : `Microsoft Teams said it could not find the ${file} the LMS asked for.`,
        reasons: media ? [
          `The ${file} may have been deleted, moved, or expired in OneDrive or SharePoint.`,
          `The meeting organiser or ${file} reference may have changed.`,
          `Microsoft Teams may not have finished processing the ${file}.`,
          `Microsoft Graph may be unable to reach the ${file} using the meeting information the LMS holds.`,
        ] : [
          `Teams may not have produced the ${file} yet.`,
          'The meeting organiser or meeting link may have changed.',
        ],
        actions: media ? [
          `Open the ${file} in Microsoft Teams or Stream and check that it is available${type === 'recording' ? ' and plays' : ''}.`,
          `If it is available, press ${SYNC_BUTTON} to try again.`,
          CONTACT,
        ] : [`Check the meeting in Microsoft Teams, then press ${SYNC_BUTTON} to try again.`, CONTACT],
      };
    case 'access_denied':
      return {
        title: `Microsoft denied access to the ${file}`,
        summary: `Microsoft Teams refused the LMS permission to read this ${file}.`,
        reasons: [
          "The meeting organiser's account may have changed or been restricted.",
          "The LMS's Microsoft permissions or an access policy may not cover this meeting.",
        ],
        actions: [
          'Ask your LMS administrator to check the Microsoft permissions for this meeting and its organiser.',
          'Syncing again will not help until access is fixed.',
        ],
      };
    case 'authentication':
      return {
        title: 'The LMS could not sign in to Microsoft',
        summary: `Microsoft did not accept the LMS's sign-in when it asked for this ${file}.`,
        reasons: ["The LMS's connection to Microsoft may have expired or been changed."],
        actions: ['Contact your LMS administrator. The Microsoft connection needs to be renewed before syncing can work.'],
      };
    case 'rate_limited':
      return {
        title: 'Microsoft is limiting requests',
        summary: `Microsoft asked the LMS to slow down, so this ${file} was not downloaded this time.`,
        reasons: [],
        actions: [`Wait a while, then press ${SYNC_BUTTON} to try again.`],
      };
    case 'service_unavailable':
      return {
        title: 'Microsoft Teams had a temporary problem',
        summary: `Microsoft returned a server error while the LMS was fetching this ${file}.`,
        reasons: [],
        actions: [`Try again later with ${SYNC_BUTTON}.`, CONTACT],
      };
    case 'network':
      return {
        title: `The ${file} download did not finish`,
        summary: `The connection to Microsoft dropped or timed out before this ${file} finished downloading.`,
        reasons: [],
        actions: [`Press ${SYNC_BUTTON} to try again.`, CONTACT],
      };
    case 'storage':
      return {
        title: `${capital(file)} was not saved to LMS storage`,
        summary: stage === 'uploading' || stage === 'finalizing'
          ? `The ${file} was retrieved from Microsoft Teams, but the LMS could not save it to its own storage.`
          : `The LMS could not read or write its own file storage while handling this ${file}.`,
        reasons: [],
        actions: [`Press ${SYNC_BUTTON} to try again.`, 'If it fails again, contact your LMS administrator: LMS file storage may need attention.'],
      };
    case 'link_repair':
      return {
        title: 'Saved recordings were not linked to the module',
        summary: "Files were saved, but the LMS could not attach them to this module's live-session items.",
        reasons: [],
        actions: [`Press ${SYNC_BUTTON} to try again.`, CONTACT],
      };
    default:
      return {
        title: type ? `${capital(file)} could not be synced` : 'Part of the sync did not finish',
        summary: 'Something unexpected stopped part of this sync.',
        reasons: [],
        actions: [`Press ${SYNC_BUTTON} to try again.`, `${CONTACT} Share the technical details below with them.`],
      };
  }
}

/** Issues to show, falling back to the raw stored error for servers that send none. */
function jobIssues(job: SessionSyncJob): SessionSyncIssue[] {
  if (job.issues) return job.issues;
  return job.last_error ? [{ category: 'unknown', fileType: null, artifactId: null, sessionNumber: null,
    httpStatus: null, stage: null, errorType: null, occurredAt: job.finished_at ?? null, detail: job.last_error }] : [];
}

function countPhrase(issues: SessionSyncIssue[]) {
  const files = new Map<string, SessionSyncIssue['fileType']>();
  for (const issue of issues) if (issue.artifactId) files.set(issue.artifactId, issue.fileType);
  const counts = new Map<string, number>();
  for (const type of files.values()) counts.set(fileLabel(type), (counts.get(fileLabel(type)) || 0) + 1);
  const parts = [...counts].map(([label, count]) => `${count} ${count === 1 ? label : `${label}s`}`);
  if (!parts.length) return { failed: 0, text: '' };
  const total = files.size;
  return { failed: total, text: `${parts.join(' and ')} ${total === 1 ? 'requires' : 'require'} attention.` };
}

const STAGES: Record<string, string> = {
  preparing: 'Preparing', downloading: 'Downloading from Microsoft', uploading: 'Saving to LMS storage',
  finalizing: 'Finishing the save', discovering: 'Checking Teams for files', linking: 'Linking files to the module',
};

function TechnicalDetails({ issues }: { issues: SessionSyncIssue[] }) {
  return <details className="mt-2 text-xs">
    <summary className="cursor-pointer font-semibold">Technical details</summary>
    <ul className="mt-2 space-y-2">{issues.map((issue, index) => <li key={`${issue.artifactId || issue.detail}-${index}`} className="rounded-md bg-white/70 p-2 text-foreground-700">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {issue.artifactId && <><dt className="font-semibold">File ID</dt><dd className="break-all">{issue.artifactId}</dd></>}
        {issue.sessionNumber !== null && <><dt className="font-semibold">Session</dt><dd>{issue.sessionNumber}{issue.sessionTitle ? ` · ${issue.sessionTitle}` : ''}</dd></>}
        {issue.week && <><dt className="font-semibold">Week</dt><dd>{weekLabel(issue.week)}</dd></>}
        {issue.httpStatus !== null && <><dt className="font-semibold">HTTP status</dt><dd>{issue.httpStatus}</dd></>}
        {issue.stage && <><dt className="font-semibold">Stage</dt><dd>{STAGES[issue.stage] || issue.stage}</dd></>}
        <dt className="font-semibold">Category</dt><dd>{issue.category}</dd>
        {issue.errorType && <><dt className="font-semibold">Error type</dt><dd>{issue.errorType}</dd></>}
        {issue.occurredAt && <><dt className="font-semibold">Recorded</dt><dd>{formatSystemTimestamp(issue.occurredAt)} (UK time)</dd></>}
        {issue.detail && <><dt className="font-semibold">Logged message</dt><dd className="break-words">{issue.detail}</dd></>}
      </dl>
    </li>)}</ul>
  </details>;
}

/** Plain-language account of a failed sync: what was saved, what needs attention and what to do. */
export function SessionSyncIssues({ job, filesReady, totalFiles }: { job: SessionSyncJob; filesReady?: number; totalFiles?: number }) {
  const issues = jobIssues(job);
  const { failed, text } = countPhrase(issues);
  const counted = totalFiles !== undefined && totalFiles > 0;
  const ready = filesReady ?? 0;
  const heading = counted && ready > 0 && ready < totalFiles! ? 'Sync partly completed'
    : counted && failed >= totalFiles! ? 'Sync could not save any files' : 'Sync needs attention';
  const groups = new Map<string, SessionSyncIssue[]>();
  for (const issue of issues) {
    const key = `${issue.category}:${issue.fileType ?? ''}`;
    groups.set(key, [...(groups.get(key) || []), issue]);
  }
  return <div className="space-y-3">
    <div>
      <p className="font-semibold">{heading}</p>
      <p>
        {counted ? `Sync status: ${ready} of ${totalFiles} files saved.` : ''}{text ? ` ${text}` : ''}
        {counted && ready > 0 ? ' Files already saved are kept and are not downloaded again.' : ''}
      </p>
    </div>
    {[...groups.values()].map(group => {
      const first = group[0];
      const copy = describeSyncIssue(first.category, first.fileType, first.stage);
      const sessions = sessionsLabel(group);
      return <section key={`${first.category}:${first.fileType}`} className="rounded-lg border border-red-200 bg-white/60 p-3" aria-label={copy.title}>
        <p className="font-semibold">{copy.title}{sessions ? ` · ${sessions}` : ''}</p>
        <p className="mt-1">{copy.summary}</p>
        {copy.reasons.length > 0 && <><p className="mt-2 font-semibold">Possible reasons</p>
          <ul className="ml-5 list-disc">{copy.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></>}
        <p className="mt-2 font-semibold">What you can do</p>
        <ul className="ml-5 list-disc">{copy.actions.map(action => <li key={action}>{action}</li>)}</ul>
        <TechnicalDetails issues={group} />
      </section>;
    })}
  </div>;
}
