import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ReviewAttendanceEditor } from './ReviewAttendanceEditor';
import { AppIcon } from '@/components/feature/AppIcon';
import { cn } from '@/lib/cn';
import {
  coachMeetingArtifactContentUrl,
  fetchCoachMeetingArtifacts,
  updateCoachMeetingSummary,
  type CoachMeetingArtifact,
  type CoachMeetingAttendanceRecord,
  type CoachMeetingAttendanceSummary,
  type CoachMeetingAttendanceTrackerRow,
  type CoachMeetingSummary,
  type CoachMeetingSummaryPayload,
} from './calendarEvents';

interface CoachMeetingArtifactEvent {
  id: string;
  eventKey?: string;
  title?: string;
  source?: string;
  status?: string;
  meetingLink?: string;
  graphWebLink?: string;
}

type ArtifactState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; artifacts: CoachMeetingArtifact[]; attendance: CoachMeetingAttendanceSummary | null; meetingSummary: CoachMeetingSummary | null; errors: string[]; storedOnly?: boolean; syncedAt?: string; refreshing?: boolean; refreshError?: string }
  | { status: 'error'; message: string };

type PreviewSelection = {
  artifact: CoachMeetingArtifact;
  artifactId: string;
  type: string;
  url: string;
};

type TranscriptPreviewState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; text: string }
  | { status: 'error'; message: string };

function artifactLabel(type: string) {
  if (type === 'transcript') return 'Transcript';
  if (type === 'recording') return 'Recording';
  return type.replace(/[-_]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function artifactIcon(type: string) {
  if (type === 'transcript') return 'ri-file-text-line';
  if (type === 'recording') return 'ri-video-download-line';
  return 'ri-attachment-2';
}

function artifactDate(value?: string) {
  if (!value) return 'Microsoft Teams artifact';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Microsoft Teams artifact';
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function sourceLabel(value?: string) {
  if (value === 'mcr') return 'MCM';
  if (value === 'progress-review') return 'PR';
  if (value === 'catch-up') return 'Catch-up';
  if (value === 'student-support') return 'Support';
  return value ? value.replace(/[-_]+/g, ' ') : 'Coach meeting';
}

function artifactKey(artifact: CoachMeetingArtifact) {
  const type = artifact.artifact_type || 'artifact';
  return `${type}-${artifact.graph_artifact_id || artifact.id}`;
}

function combinedTranscriptArtifact(artifacts: CoachMeetingArtifact[]): CoachMeetingArtifact | null {
  if (!artifacts.length) return null;
  const latest = artifacts[artifacts.length - 1];
  return {
    ...latest,
    id: 'combined',
    artifact_type: 'transcript',
    graph_artifact_id: 'combined',
    content_correlation_id: undefined,
    metadata: {
      ...(latest.metadata || {}),
      combined: true,
      segmentCount: artifacts.length,
      sourceArtifactIds: artifacts.map(artifact => artifact.graph_artifact_id || artifact.id),
    },
  };
}

function durationLabel(seconds?: number) {
  const totalSeconds = Math.max(0, Math.round(Number(seconds || 0)));
  if (!totalSeconds) return '0 min';
  if (totalSeconds < 60) return '<1 min';
  const minutes = Math.round(totalSeconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining ? `${hours}h ${remaining}m` : `${hours}h`;
}

function roleLabel(role?: string) {
  const normalized = (role || '').toLowerCase();
  if (normalized === 'coach') return 'Coach';
  if (normalized === 'learner') return 'Learner';
  if (normalized === 'employer') return 'Employer';
  if (normalized === 'presenter') return 'Presenter';
  return role ? role.replace(/[-_]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase()) : 'Attendee';
}

function trackerStatusClass(status?: string) {
  if (status === 'attended') return 'bg-emerald-50 text-emerald-700';
  if (status === 'absent') return 'bg-red-50 text-red-700';
  if (status === 'pending') return 'bg-amber-50 text-amber-700';
  if (status === 'extra') return 'bg-blue-50 text-blue-700';
  return 'bg-background-100 text-foreground-600';
}

function trackerStatusLabel(status?: string) {
  if (status === 'attended') return 'Attended';
  if (status === 'absent') return 'Absent';
  if (status === 'pending') return 'Pending';
  if (status === 'extra') return 'Extra';
  return status ? status.replace(/[-_]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase()) : 'Unknown';
}

function actualRecordsAsTracker(records: CoachMeetingAttendanceRecord[]): CoachMeetingAttendanceTrackerRow[] {
  return records.map((record, index) => ({
    id: record.id || `actual-${index}`,
    role: record.role || 'attendee',
    name: record.displayName || record.email || 'Unknown attendee',
    displayName: record.displayName,
    email: record.email,
    expected: false,
    required: false,
    attended: record.attended,
    status: record.attended ? 'extra' : 'absent',
    totalAttendanceSeconds: record.totalAttendanceSeconds || 0,
    actualDisplayName: record.displayName,
    actualRecordIds: record.id ? [record.id] : [],
    reportIds: record.reportId ? [record.reportId] : [],
  }));
}

function emptySummaryPayload(source?: string): CoachMeetingSummaryPayload {
  return {
    title: source === 'progress-review' ? 'Progress Review Recap' : 'Monthly Coaching Recap',
    overview: '',
    keyPoints: [],
    actions: [],
    nextSteps: [],
    support: [],
  };
}

function normalizeSummaryPayload(summary: CoachMeetingSummaryPayload | undefined, source?: string): CoachMeetingSummaryPayload {
  const fallback = emptySummaryPayload(source);
  return {
    title: summary?.title || fallback.title,
    overview: summary?.overview || '',
    keyPoints: Array.isArray(summary?.keyPoints) ? summary!.keyPoints : [],
    actions: Array.isArray(summary?.actions) ? summary!.actions : [],
    nextSteps: Array.isArray(summary?.nextSteps) ? summary!.nextSteps : [],
    support: Array.isArray(summary?.support) ? summary!.support : [],
  };
}

function summaryDate(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function SummaryListEditor({
  label,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
}) {
  const rows = values.length ? values : [''];
  return (
    <div>
      <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-foreground-500">{label}</p>
      <div className="space-y-2">
        {rows.map((value, index) => (
          <div key={`${label}-${index}`} className="flex gap-2">
            <input
              value={value}
              onChange={(event) => {
                const next = [...rows];
                next[index] = event.target.value;
                onChange(next.filter((item, itemIndex) => item.trim() || itemIndex === next.length - 1));
              }}
              placeholder={placeholder}
              className="min-w-0 flex-1 rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-medium text-foreground-800 outline-none focus:border-primary-400"
            />
            <button
              type="button"
              onClick={() => onChange(rows.filter((_, itemIndex) => itemIndex !== index))}
              className="h-9 w-9 rounded-lg border border-background-300 bg-white text-foreground-500 hover:bg-background-100"
              aria-label={`Remove ${label} item`}
            >
              <AppIcon className="ri-close-line"></AppIcon>
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange([...rows, ''])}
          className="inline-flex items-center gap-1 rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1.5 text-[12px] font-bold text-primary-700"
        >
          <AppIcon className="ri-add-line"></AppIcon>
          Add
        </button>
      </div>
    </div>
  );
}

function MeetingSummaryCard({
  event,
  meetingSummary,
  canEdit,
  onSave,
}: {
  event: CoachMeetingArtifactEvent;
  meetingSummary: CoachMeetingSummary | null;
  canEdit: boolean;
  onSave?: (summary: CoachMeetingSummaryPayload) => Promise<CoachMeetingSummary | null>;
}) {
  const summary = useMemo(
    () => normalizeSummaryPayload(meetingSummary?.summary, event.source),
    [meetingSummary?.summary, event.source],
  );
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CoachMeetingSummaryPayload>(summary);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setDraft(summary);
    setEditing(false);
    setError('');
  }, [meetingSummary?.generatedAt, meetingSummary?.editedAt, meetingSummary?.status, summary]);

  if (!meetingSummary && !canEdit) return null;
  if (!meetingSummary) {
    return (
      <div className="mt-3 rounded-xl border border-background-200 bg-background-50 p-3 text-[12px] text-foreground-500">
        Meeting recap will appear here after the transcript has been processed.
      </div>
    );
  }

  const edited = meetingSummary.status === 'edited';
  const failed = meetingSummary.status === 'failed';
  const statusLabel = edited ? 'Coach edited' : failed ? 'Generation failed' : 'AI generated';
  const timestamp = summaryDate(meetingSummary.editedAt || meetingSummary.generatedAt);
  const save = async () => {
    if (!onSave) return;
    setSaving(true);
    setError('');
    try {
      await onSave(draft);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save meeting recap.');
    } finally {
      setSaving(false);
    }
  };

  const visibleSummary = editing ? draft : summary;

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-primary-100 bg-white shadow-sm">
      <div className="border-b border-primary-100 bg-primary-50/70 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-primary-700">
              <AppIcon className="ri-sparkling-2-line"></AppIcon>
              Meeting recap
            </p>
            {editing ? (
              <input
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                className="mt-1 w-full rounded-lg border border-primary-200 bg-white px-3 py-2 text-sm font-bold text-foreground-950 outline-none focus:border-primary-400"
              />
            ) : (
              <h4 className="mt-1 text-sm font-bold text-foreground-950">{visibleSummary.title}</h4>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn(
              'rounded-full px-2.5 py-1 text-[11px] font-bold',
              edited
                ? 'bg-emerald-50 text-emerald-700'
                : failed
                  ? 'bg-red-50 text-red-700'
                  : 'bg-white text-primary-700',
            )}>
              {statusLabel}
            </span>
            {timestamp ? <span className="text-[11px] font-semibold text-foreground-500">{timestamp}</span> : null}
            {canEdit && !editing ? (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="inline-flex items-center gap-1 rounded-lg border border-primary-200 bg-white px-2.5 py-1.5 text-[12px] font-bold text-primary-700 hover:bg-primary-50"
              >
                <AppIcon className="ri-edit-line"></AppIcon>
                Edit
              </button>
            ) : null}
          </div>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {failed && !editing ? (
          <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] leading-5 text-red-700">
            Meeting recap generation failed. No AI recap is available yet.
          </div>
        ) : null}
        {!failed || editing ? (
          <>
        <div className="rounded-lg border border-background-200 bg-background-50 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-foreground-500">
            <AppIcon className="ri-file-list-3-line text-primary-500"></AppIcon>
            Overview
          </p>
          {editing ? (
            <textarea
              value={draft.overview}
              onChange={(event) => setDraft({ ...draft, overview: event.target.value })}
              rows={3}
              className="w-full resize-none rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] leading-5 text-foreground-800 outline-none focus:border-primary-400"
            />
          ) : (
            <p className="text-[13px] leading-6 text-foreground-700">{visibleSummary.overview || 'No overview is available yet.'}</p>
          )}
        </div>

        {editing ? (
          <div className="grid gap-4">
            <SummaryListEditor label="Key points" values={draft.keyPoints} placeholder="Add a key discussion point" onChange={(keyPoints) => setDraft({ ...draft, keyPoints })} />
            <SummaryListEditor label="Next steps" values={draft.nextSteps} placeholder="Add a next step" onChange={(nextSteps) => setDraft({ ...draft, nextSteps })} />
            <SummaryListEditor label="Support" values={draft.support} placeholder="Add support note" onChange={(support) => setDraft({ ...draft, support })} />
            <div>
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-foreground-500">Agreed actions</p>
              <div className="space-y-2">
                {(draft.actions.length ? draft.actions : [{ title: '', owner: 'Learner', dueDate: '', status: 'To do' }]).map((action, index, rows) => (
                  <div key={`action-${index}`} className="grid gap-2 rounded-lg border border-background-200 bg-background-50 p-2 md:grid-cols-[1fr_120px_110px_90px_auto]">
                    <input value={action.title} onChange={(event) => {
                      const next = [...rows];
                      next[index] = { ...action, title: event.target.value };
                      setDraft({ ...draft, actions: next });
                    }} placeholder="Action" className="rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-medium outline-none focus:border-primary-400" />
                    <input value={action.owner || ''} onChange={(event) => {
                      const next = [...rows];
                      next[index] = { ...action, owner: event.target.value };
                      setDraft({ ...draft, actions: next });
                    }} placeholder="Owner" className="rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-medium outline-none focus:border-primary-400" />
                    <input value={action.dueDate || ''} onChange={(event) => {
                      const next = [...rows];
                      next[index] = { ...action, dueDate: event.target.value };
                      setDraft({ ...draft, actions: next });
                    }} placeholder="Due" className="rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-medium outline-none focus:border-primary-400" />
                    <input value={action.status || ''} onChange={(event) => {
                      const next = [...rows];
                      next[index] = { ...action, status: event.target.value };
                      setDraft({ ...draft, actions: next });
                    }} placeholder="Status" className="rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-medium outline-none focus:border-primary-400" />
                    <button type="button" onClick={() => setDraft({ ...draft, actions: rows.filter((_, itemIndex) => itemIndex !== index) })} className="h-9 w-9 rounded-lg border border-background-300 bg-white text-foreground-500 hover:bg-background-100" aria-label="Remove action">
                      <AppIcon className="ri-close-line"></AppIcon>
                    </button>
                  </div>
                ))}
                <button type="button" onClick={() => setDraft({ ...draft, actions: [...draft.actions, { title: '', owner: 'Learner', dueDate: '', status: 'To do' }] })} className="inline-flex items-center gap-1 rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1.5 text-[12px] font-bold text-primary-700">
                  <AppIcon className="ri-add-line"></AppIcon>
                  Add action
                </button>
              </div>
            </div>
          </div>
        ) : (
          <>
            {visibleSummary.keyPoints.length > 0 ? (
              <div>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-foreground-500">Key discussion points</p>
                <div className="grid gap-2 md:grid-cols-2">
                  {visibleSummary.keyPoints.map((point, index) => (
                    <div key={`${point}-${index}`} className="flex gap-2 rounded-lg border border-background-200 bg-background-50 p-2.5">
                      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[11px] font-bold text-primary-700">{index + 1}</span>
                      <p className="text-[12px] leading-5 text-foreground-700">{point}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {visibleSummary.actions.length > 0 ? (
              <div>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-foreground-500">Agreed actions</p>
                <div className="overflow-hidden rounded-lg border border-background-200">
                  {visibleSummary.actions.map((action, index) => (
                    <div key={`${action.title}-${index}`} className="grid gap-2 border-b border-background-100 bg-white p-3 last:border-b-0 md:grid-cols-[1fr_auto_auto]">
                      <p className="text-[12px] font-semibold leading-5 text-foreground-800">{action.title}</p>
                      <span className="rounded-full bg-primary-50 px-2 py-1 text-[11px] font-bold text-primary-700">{action.owner || 'Learner'}</span>
                      <span className="rounded-full bg-background-100 px-2 py-1 text-[11px] font-bold text-foreground-600">{action.dueDate || action.status || 'To do'}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {visibleSummary.nextSteps.length > 0 ? (
              <div>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-foreground-500">Next steps</p>
                <div className="space-y-2">
                  {visibleSummary.nextSteps.map((step, index) => (
                    <div key={`${step}-${index}`} className="flex gap-2">
                      <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-emerald-500"></span>
                      <p className="text-[12px] leading-5 text-foreground-700">{step}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {visibleSummary.support.length > 0 ? (
              <div className="rounded-lg border border-teal-100 bg-teal-50 p-3">
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-teal-700">Support discussed</p>
                <div className="space-y-1.5">
                  {visibleSummary.support.map((item, index) => <p key={`${item}-${index}`} className="text-[12px] leading-5 text-teal-800">{item}</p>)}
                </div>
              </div>
            ) : null}
          </>
        )}

        {error ? <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold text-red-700">{error}</p> : null}
        {editing ? (
          <div className="flex flex-wrap justify-end gap-2 border-t border-background-100 pt-3">
            <button type="button" onClick={() => { setDraft(summary); setEditing(false); }} className="rounded-lg border border-background-300 bg-white px-3 py-2 text-[12px] font-bold text-foreground-700 hover:bg-background-100">Cancel</button>
            <button type="button" onClick={save} disabled={saving} className="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-3 py-2 text-[12px] font-bold text-white shadow-sm hover:bg-primary-700 disabled:opacity-60">
              <AppIcon className={saving ? 'ri-loader-4-line animate-spin' : 'ri-save-3-line'}></AppIcon>
              Save recap
            </button>
          </div>
        ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function transcriptPreviewText(raw: string) {
  const lines = raw.replace(/\r/g, '').split('\n');
  const visible: string[] = [];
  let skippingNote = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      skippingNote = false;
      if (visible.length && visible[visible.length - 1] !== '') visible.push('');
      continue;
    }
    if (skippingNote) continue;
    if (/^WEBVTT/i.test(trimmed)) continue;
    if (/^(Kind|Language):/i.test(trimmed)) continue;
    if (/^\d+$/.test(trimmed)) continue;
    if (trimmed.includes('-->')) continue;
    if (/^NOTE\b/i.test(trimmed)) {
      skippingNote = true;
      continue;
    }

    visible.push(trimmed.replace(/<[^>]+>/g, ''));
  }

  return visible.join('\n').replace(/\n{3,}/g, '\n\n').trim() || raw.trim() || 'Transcript content is empty.';
}

function AttendanceTracker({ attendance, controls }: { attendance: CoachMeetingAttendanceSummary | null; controls?: (row: CoachMeetingAttendanceTrackerRow) => ReactNode }) {
  const records = attendance?.records || [];
  const hasReport = Boolean(attendance?.reportCount);
  const trackerRows = attendance?.tracker?.length ? attendance.tracker : actualRecordsAsTracker(records);
  const expectedRows = trackerRows.filter(row => row.expected);
  const attendedExpected = attendance?.expectedAttendedCount ?? expectedRows.filter(row => row.status === 'attended').length;
  const expectedCount = attendance?.expectedCount ?? expectedRows.length;
  const absentExpected = attendance?.expectedAbsentCount ?? expectedRows.filter(row => row.status === 'absent').length;
  const pendingExpected = attendance?.expectedPendingCount ?? expectedRows.filter(row => row.status === 'pending').length;
  const extraCount = attendance?.extraCount ?? trackerRows.filter(row => !row.expected && row.status === 'extra').length;
  const totalDuration = trackerRows.reduce(
    (sum, row) => sum + (row.attended ? Math.max(0, Number(row.totalAttendanceSeconds || 0)) : 0),
    0,
  );

  return (
    <div className="mt-3 rounded-lg border border-background-200 bg-background-50 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="flex items-center gap-1.5 text-[12px] font-bold text-foreground-900">
            <AppIcon className="ri-user-follow-line text-emerald-600"></AppIcon>
            Attendance tracking
          </p>
          <p className="mt-0.5 text-[12px] text-foreground-500">
            Expected people are matched against Microsoft Teams attendance reports.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[11px] font-bold">
          {expectedCount ? (
            <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">{attendedExpected}/{expectedCount} expected attended</span>
          ) : (
            <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">{records.filter(record => record.attended).length} actual attended</span>
          )}
          {hasReport ? (
            <span className={cn('rounded-full px-2 py-1', absentExpected > 0 ? 'bg-red-50 text-red-700' : 'bg-background-100 text-foreground-600')}>
              {absentExpected} absent
            </span>
          ) : (
            <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">{pendingExpected || expectedCount || 0} pending</span>
          )}
          {extraCount > 0 ? (
            <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-700">{extraCount} extra</span>
          ) : null}
          <span className="rounded-full bg-background-100 px-2 py-1 text-foreground-600">{attendance?.reportCount || 0} reports</span>
          <span className="rounded-full bg-primary-50 px-2 py-1 text-primary-700">{durationLabel(totalDuration)} total</span>
        </div>
      </div>

      {!hasReport ? (
        <p className="mt-2 rounded-md border border-background-200 bg-white px-3 py-2 text-[12px] text-foreground-500">
          The expected list is ready. Teams attendance will be matched here after the meeting ends and the report is returned.
        </p>
      ) : null}

      {attendance?.linkWarning && <p role="alert" className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-900">{attendance.linkWarning}</p>}

      {hasReport && records.length === 0 ? (
        <p className="mt-2 rounded-md border border-background-200 bg-white px-3 py-2 text-[12px] text-foreground-500">
          Teams returned an attendance report, but no attendee rows were included.
        </p>
      ) : null}

      {trackerRows.length > 0 ? (
        <div className="mt-2 overflow-hidden rounded-md border border-background-200 bg-white">
          {trackerRows.slice(0, 12).map((row, index) => (
            <div key={`${row.expected ? 'expected' : 'actual'}-${row.id}-${index}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-b border-background-100 px-3 py-2 last:border-b-0 sm:grid-cols-[1fr_auto_auto_auto]">
              <div className="col-span-full min-w-0 sm:col-span-1">
                <p className="truncate text-[12px] font-semibold text-foreground-800">{row.displayName || row.name || row.email || 'Unknown attendee'}</p>
                {row.email ? <p className="truncate text-[11px] text-foreground-400">{row.email}</p> : null}
                {row.coachVerification && <p className="break-words text-[11px] text-emerald-700">Linked Teams account: {row.linkedEmail || row.actualDisplayName}</p>}
              </div>
              <span className="rounded-full bg-background-100 px-2 py-1 text-[11px] font-bold text-foreground-600">
                {row.expected ? roleLabel(row.role) : `Extra ${roleLabel(row.role)}`}
              </span>
              <span className={cn(
                'rounded-full px-2 py-1 text-[11px] font-bold',
                trackerStatusClass(row.status),
              )}>
                {row.coachVerification ? 'Present · Coach verified' : trackerStatusLabel(row.status)}
              </span>
              <span className="text-[11px] font-semibold text-foreground-500">{durationLabel(row.totalAttendanceSeconds)}</span>
              {controls?.(row)}
            </div>
          ))}
          {trackerRows.length > 12 ? (
            <p className="px-3 py-2 text-[11px] font-semibold text-foreground-400">
              +{trackerRows.length - 12} more attendance rows
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TranscriptPreview({ preview }: { preview: TranscriptPreviewState }) {
  if (preview.status === 'loading') {
    return (
      <div className="flex min-h-32 items-center justify-center gap-2 rounded-lg bg-white text-[12px] font-semibold text-primary-700">
        <AppIcon className="ri-loader-4-line animate-spin"></AppIcon>
        Loading transcript preview
      </div>
    );
  }
  if (preview.status === 'error') {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
        {preview.message}
      </div>
    );
  }
  if (preview.status === 'ready') {
    return (
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-[12px] leading-5 text-foreground-700">
        {preview.text}
      </pre>
    );
  }
  return null;
}

export function CoachMeetingArtifactsPanel({
  event,
  className,
  onEventStatusChange,
  fetchArtifacts = fetchCoachMeetingArtifacts,
  contentUrl = coachMeetingArtifactContentUrl,
  showAttendance = true,
  showMeetingSummary = true,
  visibleArtifactTypes = ['transcript', 'recording'],
  canEditSummary = showAttendance,
  saveSummary = updateCoachMeetingSummary,
  refreshOnLoad = false,
  canCheck = true,
  showCheckAction = canCheck,
  checkLabel = 'Check Teams',
  hasTeamsMeeting = false,
  allowContentAccess = true,
  onArtifactsLoaded,
  storedOnly = false,
  preserveResourcesOnRefreshError = false,
  attendanceLinkKey,
}: {
  event: CoachMeetingArtifactEvent;
  className?: string;
  onEventStatusChange?: (status: string) => void;
  fetchArtifacts?: typeof fetchCoachMeetingArtifacts;
  contentUrl?: typeof coachMeetingArtifactContentUrl;
  showAttendance?: boolean;
  showMeetingSummary?: boolean;
  visibleArtifactTypes?: string[];
  canEditSummary?: boolean;
  saveSummary?: typeof updateCoachMeetingSummary;
  refreshOnLoad?: boolean;
  canCheck?: boolean;
  showCheckAction?: boolean;
  checkLabel?: string;
  hasTeamsMeeting?: boolean;
  allowContentAccess?: boolean;
  onArtifactsLoaded?: (result: Awaited<ReturnType<typeof fetchCoachMeetingArtifacts>>) => void;
  storedOnly?: boolean;
  /** Imported checks only; callers must remount on source/booking/access changes. */
  preserveResourcesOnRefreshError?: boolean;
  /** Explicit Coach-only entry point; never enabled by Learner/Employer reuse. */
  attendanceLinkKey?: string;
}) {
  const eventKey = event.eventKey || '';
  const hasTeamsLink = hasTeamsMeeting || Boolean(event.meetingLink || event.graphWebLink);
  const supportsMeetingSummary = event.source === 'mcr' || event.source === 'progress-review';
  const [state, setState] = useState<ArtifactState>({ status: 'idle' });
  const [preview, setPreview] = useState<PreviewSelection | null>(null);
  const [playbackError, setPlaybackError] = useState(false);
  const [transcriptPreview, setTranscriptPreview] = useState<TranscriptPreviewState>({ status: 'idle' });

  const loadArtifacts = useCallback((signal?: AbortSignal, refresh = false) => {
    setState(current => preserveResourcesOnRefreshError && refresh && current.status === 'ready'
      ? { ...current, refreshing: true, refreshError: undefined } : { status: 'loading' });
    return fetchArtifacts(eventKey, signal, { refresh: storedOnly ? false : refresh })
      .then(result => {
        onArtifactsLoaded?.(result);
        if (result.event?.status) onEventStatusChange?.(result.event.status);
        setState({
          status: 'ready',
          artifacts: result.artifacts || [],
          attendance: result.attendance || null,
          meetingSummary: result.meetingSummary || null,
          errors: result.errors || [],
          storedOnly: result.resourceMode === 'stored',
          syncedAt: result.storage?.syncedAt,
        });
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        const message = error instanceof Error ? error.message : 'Unable to load Teams artifacts.';
        const failure = error as { status?: number; code?: string } | null;
        const invalidContext = [401, 403, 404].includes(failure?.status || 0)
          || (failure?.status === 409 && !['MEETING_END_NOT_CONFIRMED', 'SUMMARY_IN_PROGRESS', 'SUMMARY_SUPERSEDED'].includes(failure.code || ''));
        if (invalidContext) setPreview(null);
        setState(current => preserveResourcesOnRefreshError && refresh && !invalidContext && current.status === 'ready'
          ? { ...current, refreshing: false, refreshError: message }
          : { status: 'error', message });
      });
  }, [eventKey, fetchArtifacts, onArtifactsLoaded, onEventStatusChange, storedOnly, preserveResourcesOnRefreshError]);

  useEffect(() => {
    setPreview(null);
    setPlaybackError(false);
    if (!eventKey || !hasTeamsLink || event.source === 'live-session') {
      setState({ status: 'idle' });
      return;
    }
    const controller = new AbortController();
    loadArtifacts(controller.signal, refreshOnLoad);
    return () => controller.abort();
  }, [event.source, eventKey, hasTeamsLink, loadArtifacts, refreshOnLoad]);

  useEffect(() => {
    if (!allowContentAccess) setPreview(null);
  }, [allowContentAccess]);

  useEffect(() => {
    if (!preview || preview.type !== 'transcript') {
      setTranscriptPreview({ status: 'idle' });
      return;
    }

    const controller = new AbortController();
    setTranscriptPreview({ status: 'loading' });
    fetch(preview.url, { credentials: 'include', signal: controller.signal })
      .then(async response => {
        const text = await response.text();
        if (!response.ok) {
          throw new Error(text || `Transcript preview failed with ${response.status}`);
        }
        setTranscriptPreview({ status: 'ready', text: transcriptPreviewText(text) });
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setTranscriptPreview({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to preview transcript.',
        });
      });

    return () => controller.abort();
  }, [preview]);

  const grouped = useMemo(() => {
    if (state.status !== 'ready') return { transcripts: [], recordings: [] };
    const transcriptSegments = state.artifacts.filter(
      artifact => artifact.artifact_type === 'transcript' && visibleArtifactTypes.includes('transcript'),
    );
    const combinedTranscript = combinedTranscriptArtifact(transcriptSegments);
    return {
      transcripts: combinedTranscript ? [combinedTranscript] : [],
      recordings: state.artifacts.filter(artifact => artifact.artifact_type === 'recording' && visibleArtifactTypes.includes('recording')),
    };
  }, [state, visibleArtifactTypes]);

  if (event.source === 'live-session' || !eventKey || !hasTeamsLink) return null;

  const artifacts = state.status === 'ready' ? state.artifacts : [];
  const attendance = state.status === 'ready' ? state.attendance : null;
  const meetingSummary = state.status === 'ready' ? state.meetingSummary : null;
  const visibleArtifacts = artifacts.filter(artifact => visibleArtifactTypes.includes(artifact.artifact_type));
  const hasArtifacts = visibleArtifacts.length > 0;
  const isLoading = state.status === 'loading' || (state.status === 'ready' && state.refreshing);
  const savedResources = storedOnly || (state.status === 'ready' && state.storedOnly);

  return (
    <div className={cn('rounded-lg border border-background-200 bg-white p-3', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-foreground-500">
            <AppIcon className="ri-folder-video-line text-primary-500"></AppIcon>
            {showAttendance ? 'Recording, Transcript & Attendance' : visibleArtifactTypes.length === 1 && visibleArtifactTypes[0] === 'recording' ? 'Meeting Recording' : 'Meeting Artifacts'}
          </p>
          <p className="mt-1 text-[12px] leading-5 text-foreground-500">
            {savedResources
              ? 'Saved meeting resources. Your coach can check Teams for updates.'
              : `${sourceLabel(event.source)} artifacts from Microsoft Teams.`}
          </p>
          {savedResources && state.status === 'ready' && state.syncedAt ? (
            <p className="mt-1 text-[12px] leading-5 text-foreground-500">Last saved: {artifactDate(state.syncedAt)}. Newer resources may not be included.</p>
          ) : null}
        </div>
        {showCheckAction && !savedResources ? <button
          type="button"
          onClick={() => {
            if (!canCheck || isLoading) return;
            setPreview(null);
            void loadArtifacts(undefined, true);
          }}
          disabled={!canCheck || isLoading}
          className="inline-flex items-center gap-1.5 rounded-md border border-primary-200 bg-primary-50 px-3 py-1.5 text-[12px] font-semibold text-primary-700 transition hover:border-primary-300 hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <AppIcon className={isLoading ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'}></AppIcon>
          {isLoading ? 'Loading' : checkLabel}
        </button> : null}
      </div>

      {state.status === 'error' || (state.status === 'ready' && state.refreshError) ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
          {state.status === 'error' ? state.message : state.refreshError}
        </div>
      ) : null}

      {state.status === 'ready' && showAttendance ? attendanceLinkKey
        ? <ReviewAttendanceEditor key={attendanceLinkKey} eventKey={attendanceLinkKey} attendance={attendance}>
          {(value, controls) => <AttendanceTracker attendance={value} controls={controls} />}
        </ReviewAttendanceEditor>
        : <AttendanceTracker attendance={attendance} /> : null}

      {state.status === 'ready' && supportsMeetingSummary && showMeetingSummary && !savedResources ? (
        <MeetingSummaryCard
          event={event}
          meetingSummary={meetingSummary}
          canEdit={Boolean(canEditSummary && eventKey && saveSummary)}
          onSave={canEditSummary && saveSummary ? async (summary) => {
            const result = await saveSummary(eventKey, summary);
            setState((current) => current.status === 'ready'
              ? { ...current, meetingSummary: result.meetingSummary || null }
              : current);
            return result.meetingSummary || null;
          } : undefined}
        />
      ) : null}

      {state.status === 'ready' && !hasArtifacts ? (
        <div className="mt-3 rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[12px] leading-5 text-foreground-500">
          {preserveResourcesOnRefreshError
            ? 'No saved meeting resources are available for this review yet. Check the resource status above or retry.'
            : savedResources
            ? 'No saved meeting recording is available. Your coach can check Teams for updates.'
            : 'No meeting recording has been returned by Teams yet. It usually appears after the meeting ends and Teams finishes processing.'}
        </div>
      ) : null}

      {hasArtifacts ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {[...grouped.transcripts, ...grouped.recordings].map(artifact => {
            const type = artifact.artifact_type || 'artifact';
            const artifactId = artifact.graph_artifact_id || artifact.id;
            const previewUrl = contentUrl(eventKey, type, artifactId, { preview: true });
            const downloadUrl = contentUrl(eventKey, type, artifactId);
            const selected = preview?.type === type && preview.artifactId === artifactId;
            const contentAvailable = allowContentAccess &&
              (artifact.content_available !== false || (type === 'recording' && artifact.graph_playback_candidate === true));
            const downloadAvailable = allowContentAccess && artifact.content_available !== false;
            return (
              <div key={artifactKey(artifact)} className="rounded-lg border border-background-200 bg-background-50 px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
                    <AppIcon className={artifactIcon(type)}></AppIcon>
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12px] font-bold text-foreground-900">{artifactLabel(type)}</p>
                    <p className="mt-0.5 truncate text-[12px] text-foreground-500">
                      {artifactDate(artifact.end_datetime || artifact.created_datetime)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {contentAvailable ? <button
                        type="button"
                        onClick={() => { setPlaybackError(false); setPreview({ artifact, artifactId, type, url: previewUrl }); }}
                        className={cn(
                          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[12px] font-semibold transition',
                          selected
                            ? 'border-primary-400 bg-primary-50 text-primary-800'
                            : 'border-primary-200 bg-white text-primary-700 hover:border-primary-300 hover:bg-primary-50',
                        )}
                      >
                        <AppIcon className={type === 'recording' ? 'ri-play-circle-line' : 'ri-eye-line'}></AppIcon>
                        {type === 'recording' ? 'Play' : 'Preview'}
                      </button> : null}
                      {downloadAvailable ? <a
                        href={downloadUrl}
                        className="inline-flex items-center gap-1 rounded-md border border-background-300 bg-white px-2 py-1 text-[12px] font-semibold text-foreground-700 transition hover:border-background-400 hover:bg-background-100"
                      >
                        <AppIcon className="ri-download-2-line"></AppIcon>
                        Download
                      </a> : null}
                    </div>
                    {artifact.graph_playback_candidate && !artifact.content_available ? (
                      <p className="mt-2 text-[12px] text-foreground-500">Play from Teams. Video availability is confirmed when playback starts.</p>
                    ) : artifact.content_available === false ? (
                      <p className="mt-2 text-[12px] text-foreground-500">A saved copy of this recording is not available yet.</p>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {preview ? (
        <div className="mt-3 rounded-lg border border-primary-100 bg-primary-50/40 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[12px] font-bold text-primary-900">
                {artifactLabel(preview.type)} preview
              </p>
              <p className="truncate text-[11px] text-primary-700">
                {artifactDate(preview.artifact.end_datetime || preview.artifact.created_datetime)}
              </p>
            </div>
            <button
              type="button"
              onClick={() => { setPreview(null); setPlaybackError(false); }}
              className="inline-flex h-8 items-center gap-1 rounded-md border border-primary-200 bg-white px-2 text-[12px] font-semibold text-primary-700 hover:bg-primary-50"
            >
              <AppIcon className="ri-close-line"></AppIcon>
              Close
            </button>
          </div>
          {preview.type === 'recording' ? (
            <video
              key={preview.url}
              src={preview.url}
              controls
              preload="metadata"
              onError={() => setPlaybackError(true)}
              className="max-h-[420px] w-full rounded-lg bg-black"
            >
              <track kind="captions" />
            </video>
          ) : preview.type === 'transcript' ? (
            <TranscriptPreview preview={transcriptPreview} />
          ) : (
            <div className="rounded-lg border border-background-200 bg-white px-3 py-2 text-[12px] text-foreground-500">
              Inline preview is not available for this artifact type.
            </div>
          )}
          {preview.type === 'recording' && playbackError ? (
            <p role="alert" className="mt-2 text-[12px] text-amber-800">
              {preview.artifact.graph_playback_candidate && !preview.artifact.content_available
                ? 'Teams could not play this recording. It may still be processing or access may have expired.'
                : 'This recording could not be played. Please try again later.'}
            </p>
          ) : null}
        </div>
      ) : null}

      {state.status === 'ready' && state.errors.length > 0 ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
          {state.errors.join(' ')}
        </div>
      ) : null}
    </div>
  );
}
