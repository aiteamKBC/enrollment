import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AppIcon } from './AppIcon';
import { SessionAccountCheck } from './SessionAccountCheck';
import { SessionRecordingPlayer } from './SessionRecordingPlayer';
import { SessionSyncStatus } from './SessionSyncStatus';
import { SessionSyncIssues } from './SessionSyncIssues';
import { recordingDay } from './recordingDay';
import { AttendanceCorrectionMenu, type AttendanceCorrectionAction } from './AttendanceCorrectionMenu';
import { formatSystemTimestamp } from '@/lib/format';
import {
  linkAttendanceAlias, loadSessionResult, requestSessionSync, sessionAttendanceUrl, sessionFileUrl, setRecordingVisibility,
  type SessionLearner, type SessionPerson, type SessionFile, type AttendanceIdentityLink,
  loadSessionAttendanceCorrections, saveSessionAttendanceCorrection, type SessionAttendanceCorrection,
} from '@/api/sessionResults';
import { useSavedSessionData } from '@/hooks/useSavedSessionData';

type RawStatus = 'present' | 'absent' | 'pending' | 'review';
type FinalOutcome = 'present' | 'absent' | 'pending' | 'review' | 'absent_excused' | 'made_up';
const rawLabels: Record<RawStatus, string> = {
  present: 'Present', absent: 'Absent', pending: 'Awaiting report', review: 'Identity needs review',
};
const outcomeLabels: Record<FinalOutcome, string> = {
  present: 'Present', absent: 'Absent, unrecovered', pending: 'Awaiting report', review: 'Identity needs review',
  absent_excused: 'Absent, excused — catch-up required', made_up: 'Made up — catch-up completed',
};
const rawStatusOf = (person: SessionPerson): RawStatus => person.rawStatus
  || (person.status === 'recovered' || person.status === 'excused' ? 'absent' : person.status);
const finalOutcomeOf = (person: SessionPerson): FinalOutcome => person.finalOutcome
  || (person.status === 'recovered' ? 'made_up' : person.status === 'excused' ? 'absent_excused' : person.status);
const recoveryLabel = (person: SessionPerson) => {
  if (rawStatusOf(person) !== 'absent') return '—';
  if (person.recoveryStatus === 'completed' || person.catchupCompleted) return 'Catch-up completed';
  if (person.recoveryStatus === 'catchup_booked') return 'Catch-up booked';
  if (person.recoveryStatus === 'requested') return 'Recovery requested';
  return 'Not requested';
};
export function SessionResults({ seriesId, sessionNumber, moduleId, learner, attendanceType, preview = false, recordingDate, recordingsOnly = false, onRecordingWatchTimeChange, onRecordingPlayingChange }: {
  seriesId: string; sessionNumber: number; learner?: SessionLearner; attendanceType?: string | null; preview?: boolean;
  moduleId?: string;
  recordingDate?: string | null;
  recordingsOnly?: boolean;
  onRecordingWatchTimeChange?: (seconds: number) => void;
  onRecordingPlayingChange?: (playing: boolean) => void;
}) {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [noticeKind, setNoticeKind] = useState<'sync' | 'identity'>('sync');
  const [syncing, setSyncing] = useState(false);
  const [visibilityBusy, setVisibilityBusy] = useState('');
  const [tab, setTab] = useState<'recordings' | 'attendance' | 'transcripts'>('recordings');
  const [query, setQuery] = useState('');
  const [attendanceFilter, setAttendanceFilter] = useState<'all' | 'present' | 'absent' | 'manual'>('all');
  const [page, setPage] = useState(0);
  const [unmatchedOpen, setUnmatchedOpen] = useState(false);
  const [aliasChoices, setAliasChoices] = useState<Record<string, string>>({});
  const [aliasBusy, setAliasBusy] = useState('');
  const [editingLink, setEditingLink] = useState<AttendanceIdentityLink>();
  const [correctionLearner, setCorrectionLearner] = useState('');
  const [recordingWatchSeconds, setRecordingWatchSeconds] = useState<Record<string, number>>({});
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [attendanceBusy, setAttendanceBusy] = useState<number | null>(null);
  const [corrections, setCorrections] = useState<SessionAttendanceCorrection[]>([]);
  const [correctionError, setCorrectionError] = useState('');
  const [correctionsEditable, setCorrectionsEditable] = useState(false);
  const [correctionReload, setCorrectionReload] = useState(0);
  const kind = learner?.kind, learnerId = learner?.id;
  const attendanceContext = useRef('');
  attendanceContext.current = JSON.stringify([seriesId, sessionNumber, moduleId, kind, learnerId, preview]);
  const load = useCallback((signal: AbortSignal) =>
    loadSessionResult(seriesId, sessionNumber, kind && learnerId ? { kind, id: learnerId } : undefined, signal, moduleId),
  [seriesId, sessionNumber, moduleId, kind, learnerId]);
  const saved = useSavedSessionData(load, true, data => ['queued', 'running'].includes(data?.job?.state || ''));
  const session = saved.data?.sessions[0];
  useEffect(() => {
    const controller = new AbortController();
    setCorrections([]); setCorrectionsEditable(false); setCorrectionError('');
    if (!moduleId || kind || preview || tab !== 'attendance') return;
    loadSessionAttendanceCorrections(seriesId, sessionNumber, moduleId, controller.signal).then(result => {
      if (!controller.signal.aborted) { setCorrections(result.corrections || []); setCorrectionsEditable(result.editable); }
    }).catch(reason => {
      if (!controller.signal.aborted) setCorrectionError(reason instanceof Error ? reason.message : 'Attendance corrections unavailable.');
    });
    return () => controller.abort();
  }, [moduleId, seriesId, sessionNumber, kind, learnerId, preview, tab, saved.data, correctionReload]);
  const correctAttendance = async (correction: SessionAttendanceCorrection, action: AttendanceCorrectionAction) => {
    if (!moduleId || learner || preview || attendanceBusy !== null) return;
    const context = attendanceContext.current;
    setAttendanceBusy(correction.learnerProfileId); setCorrectionError('');
    try {
      const result = await saveSessionAttendanceCorrection(seriesId, sessionNumber, moduleId, correction, action);
      if (context !== attendanceContext.current) return;
      setCorrections(current => current.map(row => row.learnerProfileId === result.learnerProfileId ? { ...row, ...result } : row));
      saved.refresh();
    } catch (reason) {
      if (context === attendanceContext.current) setCorrectionError(reason instanceof Error ? reason.message : 'Attendance correction could not be saved.');
    } finally { setAttendanceBusy(null); }
  };
  const recordedSessionsLearner = session?.attendanceMode !== undefined
    ? session.attendanceMode === 'lazy' : (attendanceType || '').trim().toLowerCase() === 'recorded';
  const loading = saved.loading;
  const scheduledStart = Date.parse(session?.startsAt || '');
  const upcoming = Number.isFinite(scheduledStart) && scheduledStart > currentTime;
  useEffect(() => {
    if (!upcoming) return;
    const timer = window.setTimeout(() => setCurrentTime(Date.now()), Math.min(scheduledStart - currentTime, 60_000));
    return () => window.clearTimeout(timer);
  }, [upcoming, scheduledStart, currentTime]);
  useEffect(() => {
    setError(''); setPage(0); setNotice(''); setTab('recordings');
    setUnmatchedOpen(false); setAliasChoices({}); setAliasBusy('');
    setEditingLink(undefined); setCorrectionLearner('');
  }, [seriesId, sessionNumber, moduleId, kind, learnerId]);
  const files = session?.artifacts?.filter(file => ((!learner && !preview) || !file.hiddenFromLearners)
    && (!recordingDate || recordingDay(file.createdAt) === recordingDate)) || [];
  const recordings = files.filter(file => file.type === 'recording');
  const transcripts = files.filter(file => file.type === 'transcript');
  const playableTranscripts = session?.artifacts?.filter(file => file.type === 'transcript'
    && ((!learner && !preview) || !file.hiddenFromLearners)) || [];
  const savedSessionReady = !upcoming && session?.reportReady && session.archiveReady !== false
    && recordings.length > 0 && files.every(file => file.state === 'ready');
  const recordingWatchTotal = recordings.reduce((total, file) => total + (recordingWatchSeconds[file.id] || 0), 0);
  useEffect(() => { onRecordingWatchTimeChange?.(recordingWatchTotal); }, [onRecordingWatchTimeChange, recordingWatchTotal]);
  const matchesSearch = (person: SessionPerson) => `${person.name} ${person.email}`.toLowerCase().includes(query.trim().toLowerCase());
  const correctionFor = (person: SessionPerson) => corrections.find(row => row.email?.trim().toLowerCase() === person.email.trim().toLowerCase());
  const people = (session?.attendance || []).filter(person => {
    const correction = correctionFor(person);
    return matchesSearch(person) && (attendanceFilter === 'all'
      || (attendanceFilter === 'manual' ? Boolean(correction?.manualStatus)
        : (correction?.manualStatus || rawStatusOf(person)) === attendanceFilter));
  });
  const unmatchedPeople = (session?.unmatchedAttendance || []).filter(matchesSearch);
  const visiblePage = Math.min(page, Math.max(0, Math.ceil(people.length / 25) - 1));
  const own = learner ? session?.attendance?.[0] : undefined;
  const ownRawStatus = own ? rawStatusOf(own) : undefined;
  const ownFinalOutcome = own ? finalOutcomeOf(own) : undefined;
  const liveAttendanceSatisfied = !upcoming && session?.reportReady
    && ownRawStatus === 'present' && ownFinalOutcome === 'present' && own?.attendance === 1;
  const sync = async () => {
    setSyncing(true); setError(''); setNotice(''); setNoticeKind('sync');
    try { setNotice((await requestSessionSync(seriesId)).message); saved.refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not request synchronization.'); }
    finally { setSyncing(false); }
  };
  const toggleVisibility = async (file: SessionFile) => {
    setVisibilityBusy(file.id); setError('');
    try { await setRecordingVisibility(seriesId, file.id, !file.hiddenFromLearners); saved.refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update visibility.'); }
    finally { setVisibilityBusy(''); }
  };
  const identityKey = (person: SessionPerson) => person.email || (person.sourceRecordIds || []).join('|');
  const selectedLearnerId = (person: SessionPerson) => {
    const key = identityKey(person);
    return Object.prototype.hasOwnProperty.call(aliasChoices, key)
      ? aliasChoices[key]
      : String(person.suggestedLearnerProfileId || '');
  };
  const linkAlias = async (person: SessionPerson) => {
    const key = identityKey(person);
    const learnerProfileId = Number(selectedLearnerId(person) || 0);
    if (!learnerProfileId) return;
    setAliasBusy(key); setError(''); setNotice(''); setNoticeKind('identity');
    try {
      const linked = await linkAttendanceAlias(seriesId, sessionNumber, person.email, learnerProfileId, person.sourceRecordIds || [], moduleId);
      setNotice(`${linked.aliasEmail || person.name} is now matched to ${linked.learnerName}. ${linked.scope === 'session' ? 'This review applies to this session only. Future appearances of this duplicate name need review.' : 'This match applies across this module.'}`);
      if (linked.recalculation === 'failed') setError('The identity link was saved, but attendance reporting could not be refreshed. Retry the link to recalculate reporting.');
      if (linked.recalculation === 'pending') setError('The identity link was saved. This view uses the saved Teams evidence, but no attendance reporting rows were available to update.');
      saved.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not link the reported email.');
    } finally { setAliasBusy(''); }
  };
  const correctLink = async (action: 'change' | 'unlink') => {
    if (!editingLink || (action === 'change' && !correctionLearner)) return;
    setAliasBusy('correction'); setError(''); setNotice(''); setNoticeKind('identity');
    try {
      const result = await linkAttendanceAlias(seriesId, sessionNumber, editingLink.aliasEmail,
        action === 'change' ? Number(correctionLearner) : 0, editingLink.sourceRecordIds, moduleId,
        { action, link: editingLink });
      setNotice(action === 'unlink' ? 'Identity unlinked. Attendance is now based on the remaining Teams evidence.' : `Identity is now matched to ${result.learnerName}.`);
      if (result.recalculation === 'failed') setError('The correction was saved, but attendance reporting could not be refreshed. Contact an administrator to retry reporting.');
      if (result.recalculation === 'pending') setError('The correction was saved. This view uses the saved Teams evidence, but no attendance reporting rows were available to update.');
      setEditingLink(undefined); saved.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not correct the identity link.');
    } finally { setAliasBusy(''); }
  };
  return <section className="overflow-hidden rounded-2xl border border-primary-100 bg-white shadow-sm" aria-label={recordingsOnly ? 'Recorded event' : `Session ${sessionNumber} results`}>
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-primary-100 bg-primary-50/50 p-5">
      <div><p className="text-xs font-bold uppercase tracking-wider text-primary-600">{recordingsOnly ? 'Recorded event' : `Session ${sessionNumber}`}</p>
        <h2 className="mt-1 text-lg font-bold">{recordingsOnly ? 'Event recordings' : 'Recording & session results'}</h2>
        {!recordingsOnly && session?.title && <p className="mt-1 font-semibold">{session.title}</p>}
        {recordingsOnly && recordingDate && <p className="mt-1 text-xs text-foreground-500">Recorded on {recordingDate} (UK date)</p>}
        {!recordingsOnly && session?.startsAt && <p className="mt-1 text-xs text-foreground-500">Scheduled: {formatSystemTimestamp(session.startsAt, { dateStyle: 'medium', timeStyle: 'short' })} (UK time)</p>}
        {!recordingsOnly && session?.actualStartsAt && <p className="mt-1 text-xs text-foreground-500">Recorded meeting: {formatSystemTimestamp(session.actualStartsAt, { dateStyle: 'medium', timeStyle: 'short' })} (UK time)</p>}
        <p className="mt-1 text-xs text-foreground-500">{session?.syncedAt ? `Last saved ${formatSystemTimestamp(session.syncedAt)}` : upcoming ? 'Results will be available after this session.' : 'No saved results for this session yet.'}</p></div>
      <div className="flex flex-wrap gap-2">
        {!learner && !preview && <button type="button" disabled={loading || syncing || !session || session.archiveReady === false} onClick={() => void sync()} className="rounded-lg bg-primary-600 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{syncing ? 'Requesting…' : 'Sync attendance & files'}</button>}
        <button type="button" disabled={loading || saved.refreshing} aria-busy={saved.refreshing} onClick={saved.refresh} className="rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-60">{saved.refreshing && !loading ? 'Refreshing…' : 'Refresh saved results'}</button>
      </div>
    </header>
    {upcoming && !recordingsOnly && <p role="status" className="m-4 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900">This session has not taken place yet. Attendance and any recordings or transcripts will appear here after the session ends and Teams makes them available.</p>}
    {(error || saved.error) && <p role="alert" className="m-4 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error || saved.error}</p>}
    {notice && (noticeKind === 'identity' || !saved.data?.job) && <p role="status" className="m-4 rounded-lg bg-primary-50 p-3 text-sm">{notice}</p>}
    {!learner && !preview && saved.data?.job && (saved.data.job.state === 'failed' && savedSessionReady
      ? <div className="m-4 rounded-lg border border-background-200 bg-background-50 p-4 text-sm text-foreground-700">
        <p>The recording and attendance for this session are saved.</p>
        <details className="mt-2"><summary className="cursor-pointer font-semibold">View background sync notice</summary>
          <p className="mt-2">The last sync for this meeting series reported an issue.{saved.data.job.last_error || saved.data.job.issues?.length ? '' : ' Some results need another sync.'}</p>
          <div className="mt-2"><SessionSyncIssues job={saved.data.job} filesReady={saved.data.job.progress?.filesReady} totalFiles={saved.data.job.progress?.totalFiles} /></div>
        </details>
      </div>
      : <><p className="px-4 pt-3 text-xs font-semibold text-foreground-600">Sync status for the full meeting series</p><SessionSyncStatus job={saved.data.job} /></>)}
    {session?.archiveReady === false && <p role="status" className="m-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{learner || preview
      ? 'Saved recordings and transcripts are not ready for playback yet. Please contact your tutor.'
      : 'Recording storage needs setup. Saved attendance is available below; synchronization and playback are unavailable until setup is complete.'}</p>}
    {loading ? <p role="status" className="p-5 text-sm">Loading saved session…</p> : !session ? !error && !saved.error && <p className="p-5 text-sm">This session has not been linked yet.</p> : <>
      {learner && !preview && !recordingsOnly && recordedSessionsLearner && !upcoming && !liveAttendanceSatisfied
        && ['recorded', 'awaiting_recording'].includes(session.learningOutcome || '') && <div className="m-4 rounded-xl border bg-background-50 p-4 text-sm" role="status">
          <strong>{session.learningOutcome === 'recorded' ? 'Completed by recording' : 'Awaiting recording'}</strong>
          <p>{session.learningOutcome === 'recorded'
            ? 'This session counts once towards your attendance through recorded learning.'
            : 'Complete the session recordings to receive attendance credit. This session is excluded from your attendance percentage while you wait.'}</p>
        </div>}
      {learner && !preview && !recordingsOnly && (!recordedSessionsLearner || liveAttendanceSatisfied) && <div className="m-4 rounded-xl border bg-background-50 p-4 text-sm">
        <strong>Your original attendance: {ownRawStatus ? rawLabels[ownRawStatus] : upcoming ? 'Not available yet' : session.reportReady ? 'Identity needs review' : 'Awaiting report'}</strong>
        {own && <p className="mt-1 text-foreground-500">{Math.floor(own.seconds / 60)}m {own.seconds % 60}s verified in Teams. Presence requires more than 3 minutes.</p>}
        {recordedSessionsLearner && liveAttendanceSatisfied && <p className="mt-1 font-semibold text-primary-700">Your live attendance counts for this session. Watching the recording is optional.</p>}
        {ownFinalOutcome && ownRawStatus && ownFinalOutcome !== ownRawStatus && <p className="mt-1 font-semibold text-primary-700">Effective outcome: {outcomeLabels[ownFinalOutcome]}</p>}
        {own?.attendance === 0 && !own.catchupCompleted && <Link className="mt-2 inline-block font-semibold text-primary-700 underline" to="/learner/attendance">{own.absenceReported ? 'View recovery plan' : 'Open attendance and report absence'}</Link>}
      </div>}
      <div className="flex gap-2 border-b px-4" role="tablist" aria-label="Session results">
        {(['recordings', 'attendance', 'transcripts'] as const).filter(value => !recordingsOnly && ((!learner && !preview) || value !== 'attendance') || recordingsOnly && value === 'recordings').map(value =>
          <button key={value} role="tab" type="button" aria-selected={tab === value} onClick={() => setTab(value)}
            className={`border-b-2 px-4 py-3 text-sm font-semibold capitalize ${tab === value ? 'border-primary-600 text-primary-700' : 'border-transparent text-foreground-500'}`}>{value}</button>)}
      </div>
      <div className="p-5" role="tabpanel">
        {tab === 'recordings' && <div className="space-y-5">{!recordings.length && <p className="text-sm text-foreground-500">{upcoming ? 'Recordings will appear here if this session is recorded.' : 'No recording is saved for this session. Check the session date; other sessions may have recordings. New saved results appear automatically.'}</p>}
          {recordings.map((file, index) => <div key={file.id}><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-bold">Recording {index + 1}{file.hiddenFromLearners ? ' · Hidden from learners' : ''}</h3>
            {!learner && !preview && <button type="button" disabled={Boolean(visibilityBusy)} onClick={() => void toggleVisibility(file)} className="rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50">{visibilityBusy === file.id ? 'Updating…' : file.hiddenFromLearners ? 'Show to learners' : 'Hide from learners'}</button>}</div>
            {file.state === 'ready' ? <SessionRecordingPlayer seriesId={seriesId} file={file} transcripts={playableTranscripts} learner={learner} label={`Session recording ${index + 1}`} trackWatch={Boolean(learner) && !preview}
              onPlayingChange={onRecordingPlayingChange}
              onWatchTimeChange={seconds => setRecordingWatchSeconds(current => current[file.id] === seconds ? current : { ...current, [file.id]: seconds })} />
              : <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm">{session.archiveReady === false ? 'Teams has a recording for this session. A saved playback copy is not available yet.' : file.state === 'failed' ? 'Saving this recording needs retry. An administrator can request synchronization.' : 'Recording found. Saving a copy for playback…'}</p>}</div>)}
        </div>}
        {tab === 'transcripts' && <div className="space-y-4">{!transcripts.length && <p className="text-sm text-foreground-500">{upcoming ? 'Transcripts will appear here if this session is transcribed.' : 'No transcript has been received yet.'}</p>}
          {transcripts.map((file, index) => <div key={file.id} className="rounded-xl border p-4"><div className="mb-3 flex justify-between gap-3"><h3 className="font-semibold">Transcript {index + 1}</h3>
            {file.state === 'ready' && <a className="text-sm font-semibold text-primary-700 underline" href={sessionFileUrl(seriesId, file, learner, true)}>Download TXT</a>}</div>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap font-sans text-sm leading-7">{file.state === 'ready' ? file.text || 'The transcript contains no spoken text.' : session.archiveReady === false ? 'Teams has a transcript for this session. A saved text copy is not available yet.' : 'Transcript is being saved. Please check again later.'}</pre></div>)}
        </div>}
        {tab === 'attendance' && !learner && !preview && <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3"><input aria-label="Search attendance" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder="Search name or email" className="rounded-lg border px-3 py-2 text-sm" />
            <label className="flex items-center gap-2 text-sm">Attendance
              <select aria-label="Filter attendance" value={attendanceFilter} onChange={event => { setAttendanceFilter(event.target.value as typeof attendanceFilter); setPage(0); }} className="rounded-lg border bg-white px-3 py-2">
                <option value="all">All</option><option value="present">Present</option><option value="absent">Absent</option><option value="manual">Manually adjusted</option>
              </select>
            </label>
            <div className="flex flex-wrap gap-2"><a href={sessionAttendanceUrl(session, 'csv', moduleId)} className="rounded-lg border px-4 py-2 text-sm font-semibold"><AppIcon className="ri-download-line mr-2" />Export attendance CSV</a>
              <a href={sessionAttendanceUrl(session, 'pdf', moduleId)} className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white"><AppIcon className="ri-download-line mr-2" />Export attendance PDF</a></div></div>
          {!session.reportReady && <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm">{upcoming ? 'Attendance will be available after this session ends and Teams provides a report.' : 'Awaiting a completed Teams report. Pending learners are not marked absent.'}</p>}
          {!!session.unmatchedAttendance?.length && <div className="rounded-xl border border-amber-200 bg-amber-50/60">
            <button type="button" aria-expanded={unmatchedOpen} onClick={() => setUnmatchedOpen(value => !value)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left font-semibold text-amber-950">
              <span><AppIcon className="ri-user-search-line mr-2" />Review unmatched participants ({unmatchedPeople.length})</span>
              <AppIcon className={unmatchedOpen ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'} />
            </button>
            {unmatchedOpen && <div className="space-y-3 border-t border-amber-200 p-4">
              <p className="text-sm text-amber-900">These Teams identities are not assigned to a learner in this module. Link only an identity you have verified.</p>
              <p className="text-sm text-amber-900">A guest's full name is matched automatically when it identifies one learner in this module without a conflict. Your reviewed choices are remembered; similar or duplicate names need review.</p>
              {!unmatchedPeople.length && <p>No unmatched participants match your search.</p>}
              {unmatchedPeople.map(person => { const key = identityKey(person); return <div key={key} className="grid gap-3 rounded-lg border bg-white p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-center">
                <div><p className="font-semibold">{person.name}</p><p className="text-xs text-foreground-500">{person.email || 'Unverified Teams identity'}</p><p className="mt-1 text-xs">{Math.floor(person.seconds / 60)}m {person.seconds % 60}s in Teams</p>
                  {person.identityAmbiguous && <p className="mt-2 text-xs font-semibold text-amber-900">This name identifies more than one participant or learner. Review the correct person before linking.</p>}</div>
                <label className="text-xs font-semibold text-foreground-600">Match to module learner
                  <select aria-label={`Match ${person.email || person.name} to learner`} value={selectedLearnerId(person)}
                    onChange={event => setAliasChoices(value => ({ ...value, [key]: event.target.value }))}
                    className="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm font-normal">
                    <option value="">Choose learner…</option>
                    {(session.attendanceCandidates || []).map(candidate => <option key={candidate.learnerProfileId} value={candidate.learnerProfileId}>{candidate.name} · {candidate.email}</option>)}
                  </select>
                  {person.suggestedLearnerProfileId && !Object.prototype.hasOwnProperty.call(aliasChoices, key) && <span className="mt-1 block font-normal text-primary-700">Suggested from the Teams name — confirm before linking.</span>}
                </label>
                <button type="button" disabled={Boolean(aliasBusy) || !selectedLearnerId(person)}
                  onClick={() => void linkAlias(person)} className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
                  {aliasBusy === key ? 'Linking…' : 'Link identity'}
                </button>
              </div>})}
            </div>}
          </div>}
          {!!session.linkedIdentities?.length && <details className="rounded-xl border border-primary-200 bg-primary-50/40 p-4">
            <summary className="cursor-pointer font-semibold">Linked identities ({session.linkedIdentities.length})</summary>
            <div className="mt-3 space-y-3">{session.linkedIdentities.map(link => <div key={`${link.identityKind}:${link.identityValue}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white p-3">
              <div><p className="font-semibold">{link.name}</p><p className="text-sm">Linked to {link.learnerName}</p>
                {link.automatic && <p className="text-xs text-primary-700">Automatically matched by exact full name. You can correct or unlink this match.</p>}
                <p className="text-xs text-foreground-500">{link.scope === 'module' ? 'Current and future sessions in this module' : 'This session only'}</p></div>
              <button type="button" disabled={Boolean(aliasBusy)} onClick={() => { setEditingLink(link); setCorrectionLearner(String(link.learnerProfileId)); }}
                className="rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50" aria-label={`Correct link for ${link.name}`}>Change or unlink</button>
            </div>)}</div>
          </details>}
          {editingLink && <div role="region" aria-label="Correct identity link" className="space-y-3 rounded-xl border border-primary-200 bg-white p-4">
            <p className="font-semibold">Correct {editingLink.name}</p>
            <p className="text-sm">Currently linked to {editingLink.learnerName}. {editingLink.scope === 'module'
              ? 'Changing or unlinking updates this identity across the module. Both learners’ attendance will be recalculated from Teams evidence.'
              : 'This correction affects this session only.'}</p>
            <label className="block text-sm font-semibold">Correct learner
              <select value={correctionLearner} onChange={event => setCorrectionLearner(event.target.value)} disabled={Boolean(aliasBusy)} className="mt-1 w-full rounded-lg border bg-white px-3 py-2 font-normal">
                <option value="">Choose learner…</option>
                {(session.attendanceCandidates || []).map(candidate => <option key={candidate.learnerProfileId} value={candidate.learnerProfileId}>{candidate.name} · {candidate.email}</option>)}
              </select>
            </label>
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={Boolean(aliasBusy) || !correctionLearner} onClick={() => void correctLink('change')} className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Save corrected link</button>
              <button type="button" disabled={Boolean(aliasBusy)} onClick={() => void correctLink('unlink')} className="rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-700 disabled:opacity-50">Confirm unlink</button>
              <button type="button" disabled={Boolean(aliasBusy)} onClick={() => setEditingLink(undefined)} className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
            </div>
          </div>}
          {session.identityCheck && <SessionAccountCheck check={session.identityCheck} />}
          {correctionError && <p role="alert">{correctionError} <button type="button" onClick={() => setCorrectionReload(value => value + 1)}>Reload corrections</button></p>}
          <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-foreground-500"><th className="p-3">Learner</th><th>Duration</th><th>Attendance</th><th>Recovery</th>{moduleId && !learner && !preview && <th>Actions</th>}</tr></thead><tbody>
            {!people.length && <tr><td colSpan={moduleId ? 5 : 4} className="p-4 text-center">No learners match these filters.</td></tr>}
            {people.slice(visiblePage * 25, visiblePage * 25 + 25).map((person, index) => {
              const correction = correctionFor(person);
              return <tr key={person.email || index} className="border-b"><td className="p-3"><p className="font-semibold">{person.name}</p><p className="text-xs text-foreground-500">{person.email || 'Unmatched identity'}</p></td><td>{Math.floor(person.seconds / 60)}m {person.seconds % 60}s</td><td>{correction?.manualStatus ? rawLabels[correction.manualStatus] : rawLabels[rawStatusOf(person)]}</td><td>{recoveryLabel(person)}</td>
                {moduleId && !learner && !preview && <td>{correction && <AttendanceCorrectionMenu learnerName={person.name}
                  manualStatus={correction.manualStatus} disabled={!correctionsEditable || attendanceBusy !== null}
                  saving={attendanceBusy === correction.learnerProfileId} onAction={action => void correctAttendance(correction, action)} />}</td>}
              </tr>;
            })}
          </tbody></table></div>
          {people.length > 25 && <div className="flex items-center gap-4 text-sm"><button disabled={!visiblePage} onClick={() => setPage(visiblePage - 1)}>Previous</button><span>Page {visiblePage + 1} of {Math.ceil(people.length / 25)}</span><button disabled={(visiblePage + 1) * 25 >= people.length} onClick={() => setPage(visiblePage + 1)}>Next</button></div>}
        </div>}
      </div>
    </>}
  </section>;
}
