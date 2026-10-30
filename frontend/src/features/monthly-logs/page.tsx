import { useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { useLearnerWorkspaceAccess } from '@/hooks/useLearnerWorkspaceAccess';
import { useResolvedLearner } from '@/hooks/useMyLearner';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { PageContainer } from '@/components/ui/PageContainer';
import { EmptyState } from '@/components/ui/EmptyState';
import { AppIcon } from '@/components/feature/AppIcon';
import { roleNavMap } from '@/mocks/navigation';
import { LearnerInformation, MonthlyHours, ActivityLog } from '@/features/old-otjh/ReportSections';
import { JournalDownloads } from '@/features/old-otjh/JournalDownloads';
import { SignatureCapture } from '@/features/old-otjh/SignatureCapture';
import { RecordBadge } from '@/features/old-otjh/RecordDesign';
import { MonthReportSkeleton } from '@/features/old-otjh/RecordSkeletons';
import { displayDate, monthLabel, monthStatus, previousMonthSignature } from '@/features/old-otjh/report';
import { coachViewAs } from '@/lib/coachViewAs';
import type { SignatureCaptureMethod } from '@/features/old-otjh/api';
import design from '@/features/old-otjh/design.module.css';
import journal from '@/features/old-otjh/journal.module.css';
import reportStyles from '@/features/old-otjh/report.module.css';
import { completeLogMonth, getLogContent, getLogMonth, getLogSummary, signLogMonth, unlockLogMonth, type LogSummary, type LogPerspective, type LogDetail } from './api';
import type { ActivityContent } from '@/features/old-otjh/api';
import styles from './monthlyLogs.module.css';
import { MonthList, MonthIndexSkeleton } from './MonthList';
import { CoachLearnerLogs } from '@/features/coach/monthly-logs/components/CoachLearnerLogs';
import { CoachMonthlyLogLearners } from '@/features/coach/monthly-logs/components/CoachMonthlyLogLearners';

export default function MonthlyLogsPage() {
  const { auth } = useAuth();
  const { learnerId, kind, id: routeId, month } = useParams<{ learnerId?: string; kind?: string; id?: string; month?: string }>();
  const { pathname } = useLocation();
  const student = auth.account?.role === 'learner';
  const perspective: LogPerspective = student || pathname.startsWith('/learner/') ? 'learner' : 'coach';
  const selected = useResolvedLearner(student ? undefined : kind, student ? undefined : routeId);
  const id = student ? String(auth.account?.subjectId ?? '') : perspective === 'learner' ? selected.id : learnerId;
  const base = perspective === 'learner' ? student ? '/learner/monthly-logs' : `/learner/monthly-logs/${selected.kind}/${id}` : `/coach/monthly-logs/${id}`;
  const overview = student ? '/workspace/learner/dashboard' : `/workspace/learner/${selected.kind}/${id}/dashboard`;
  const nav = roleNavMap[perspective];
  const coachOverview = perspective === 'coach' && !month;
  return <WorkspaceShell role={perspective} roleLabel={nav.label} navItems={nav.items}
    workspaceLabel={nav.workspaceLabel} pageTitle="Monthly Logs" pageSubtitle="Your monthly learning record, activities and signatures"
    showBackButton backFallbackHref={month ? base : perspective === 'learner' ? overview : '/coach/monthly-logs'} hidePageChrome={coachOverview}>
    <PageContainer className={`${design.scope} ${design.page} ${styles.theme} ${month ? journal.canvas : ''} ${coachOverview ? styles.coachCanvas : ''}`}>
      {coachOverview ? <CoachMonthlyLogLearners selectedLearnerId={id} renderSelected={learner => {
        const learnerId = String(learner.id);
        return <CoachLearnerLogs key={`coach-${learnerId}`} id={learnerId} />;
      }} /> : id && perspective === 'coach' && month ? <CoachLearnerLogs key={`coach-${id}-${month}`} id={id} month={month} /> : id ? <LearnerLogs key={`${perspective}-${id}`} id={id} month={month} base={base} perspective={perspective} /> : perspective === 'learner'
        ? <EmptyState title="Your learner account is unavailable" /> : <EmptyState title="Choose a learner to view monthly logs" />}
    </PageContainer>
  </WorkspaceShell>;
}

function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  return <EmptyState variant="error" title="Unable to load monthly logs" description={error.message}
    action={<button className={journal.secondaryButton} onClick={retry}>Try again</button>} />;
}

function currentMonthKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function FutureMonthState({ month, base }: { month: string; base: string }) {
  return <div className={`${journal.card} ${styles.futureMonth}`}>
    <EmptyState title={`${monthLabel(month)} has not started yet`} icon="ri-calendar-schedule-line"
      description="This monthly log will become available when the month begins. Your scheduled lecture is still available from Attendance."
      action={<>
        <Link className={styles.futurePrimaryAction} to="/learner/attendance"><AppIcon className="ri-calendar-check-line" />Back to Attendance</Link>
        <Link className={styles.futureSecondaryAction} to={base}><AppIcon className="ri-history-line" />View available months</Link>
      </>} />
  </div>;
}

export function LearnerLogs({ id, month, base, perspective, workflow, embedded = false }: {
  id: string; month?: string; base: string; perspective: LogPerspective; contractKind?: string;
  workflow?: string; embedded?: boolean;
}) {
  const { auth } = useAuth();
  const { search } = useLocation();
  const workflowKey = workflow ?? new URLSearchParams(search).get('workflow') ?? undefined;
  const mcmMonth = workflowKey === 'mcm' ? month : undefined;
  const query = useQuery({ queryKey: ['monthly-logs', auth.account?.id, perspective, perspective === 'coach' ? coachViewAs()?.email : null, id, 'summary', mcmMonth, workflowKey],
    queryFn: ({ signal }) => mcmMonth ? getLogSummary(id, signal, perspective, mcmMonth, workflowKey) : getLogSummary(id, signal, perspective), refetchInterval: 7000 });
  if (query.isPending) return <MonthIndexSkeleton perspective={perspective} />;
  if (query.error && !query.data) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return null;
  if (!Array.isArray(query.data.months)) return <ErrorState error={new Error('The monthly logs response was incomplete.')} retry={() => void query.refetch()} />;
  const summary = { ...query.data, months: [...query.data.months].sort((a, b) => a.month.localeCompare(b.month)) };
  return <>
    {query.error && <p role="alert">Updates are temporarily unavailable. <button onClick={() => void query.refetch()}>Try again</button></p>}
    {month ? <MonthlyLog key={`${id}-${month}`} id={id} month={month} summary={summary} base={base} perspective={perspective} workflow={workflowKey} embedded={embedded} />
      : <MonthList summary={summary} base={base} perspective={perspective} />}
  </>;
}

export type MonthlyLogReader = {
  cacheScope: string;
  month: (month: string, signal?: AbortSignal) => Promise<LogDetail>;
  content: (month: string, rowId: number) => Promise<ActivityContent>;
};

export function MonthlyLog({ id, month, summary, base, perspective, workflow, embedded = false, observer = false, reader, onMonthChange, loadedDetail }: {
  id: string; month: string; summary: LogSummary; loadedDetail?: LogDetail; base: string; perspective: LogPerspective;
  workflow?: string; embedded?: boolean; observer?: boolean; reader?: MonthlyLogReader;
  onMonthChange?: (month?: string) => void;
}) {
  const { auth } = useAuth();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const sourceRef = params.get('source') || undefined;
  const workflowKey = workflow ?? params.get('workflow') ?? undefined;
  const mcmWorkflow = workflowKey === 'mcm';
  const navigate = useNavigate();
  const client = useQueryClient();
  const student = auth.account?.role === 'learner';
  // The learner's own coach acts here like an administrator does.
  const { canProgress: ownCoachOrAdmin } = useLearnerWorkspaceAccess(perspective === 'learner' ? id : null);
  const canActAsStudent = !observer && (student || (perspective === 'learner' && (auth.account?.role === 'admin' || ownCoachOrAdmin)));
  const futureMonth = month > currentMonthKey() && !mcmWorkflow;
  const key = ['monthly-logs', auth.account?.id, perspective, perspective === 'coach' ? coachViewAs()?.email : null, id, month, ...(reader ? [reader.cacheScope] : [])];
  const loadMonth = (selected: string, signal?: AbortSignal) => reader ? reader.month(selected, signal)
    : mcmWorkflow ? getLogMonth(id, selected, signal, perspective, false, workflowKey) : getLogMonth(id, selected, signal, perspective);
  const selectMonth = (selected: string) => onMonthChange ? onMonthChange(selected) : navigate(`${base}/${selected}`);
  const query = useQuery({ queryKey: [...key, workflowKey], queryFn: ({ signal }) => loadMonth(month, signal), refetchInterval: loadedDetail ? false : 7000, enabled: !futureMonth && !loadedDetail, initialData: loadedDetail });
  const draftDigest = useRef<string | null>(null);
  const [captureVersion, setCaptureVersion] = useState(0);
  const [message, setMessage] = useState('');
  const signing = useMutation({ mutationFn: ({ blob, capture }: { blob: Blob; capture: SignatureCaptureMethod }) =>
    signLogMonth(id, month, draftDigest.current || query.data!.snapshot_digest, blob, capture, summary.csrf_token, perspective),
    onSuccess: data => { client.setQueryData(key, data); draftDigest.current = null; setCaptureVersion(v => v + 1);
      setMessage('Your signature has been saved for this month.'); void client.invalidateQueries({ queryKey: ['monthly-logs'] });
    },
    onError: () => { void client.invalidateQueries({ queryKey: ['monthly-logs'] }); } });
  const completion = useMutation({ mutationFn: () => completeLogMonth(id, month, summary.csrf_token, perspective), onSuccess: data => {
    client.setQueryData(key, data);
    setMessage('This month has been reviewed, signed and completed.');
    void client.invalidateQueries({ queryKey: ['monthly-logs'] });
  } });
  const unlocking = useMutation({ mutationFn: () => unlockLogMonth(id, month, summary.csrf_token, 'learner'), onSuccess: data => {
    client.setQueryData(key, data);
    setMessage('This monthly log has been unlocked. Existing signatures were kept.');
    void client.invalidateQueries({ queryKey: ['monthly-logs'] });
  } });
  if (futureMonth) return <FutureMonthState month={month} base={base} />;
  if (query.isPending && !loadedDetail) return <MonthReportSkeleton />;
  if (query.error && !query.data && !loadedDetail) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  if (!query.data && !loadedDetail) return null;
  const data = loadedDetail ?? query.data!;
  // Training-plan values come only from the canonical monthly-log response;
  // this view must not overwrite them from a second frontend contract.
  // Sort a presentation copy so the saved report and signing digest stay intact.
  const displayData = { ...data, rows: [...data.rows].sort((left, right) =>
    Number(!left.activity_date) - Number(!right.activity_date)
    || (left.activity_date || '').localeCompare(right.activity_date || '')
    || Number(!left.activity_time) - Number(!right.activity_time)
    || (left.activity_time || '').localeCompare(right.activity_time || '')
    || left.id - right.id) };
  // The MCM is the learner's signing surface for this linked month. Keep the
  // log available for review first, but do not allow a separate log signature
  // to diverge from the MCM signature that will be mirrored here.
  const awaitingMcmSignature = mcmWorkflow && perspective === 'learner' && !data.student_signature;
  const readOnly = observer || embedded || !!data.is_open || summary.read_only || (perspective === 'learner' && !canActAsStudent) || awaitingMcmSignature;
  const index = summary.months.findIndex(m => m.month === month);
  const previous = summary.months[index - 1], next = summary.months[index + 1];
  const signatureRows = embedded
    ? [{ role: 'Learner', signature: data.student_signature, own: false }]
    : [{ role: 'Learner', signature: data.student_signature, own: canActAsStudent }, { role: 'Coach', signature: data.coach_signature, own: !canActAsStudent && perspective === 'coach' }];
  return <div className={`${design.reportPage} ${journal.page}`}>
    {message && <p role="status" className={styles.savedMessage}>{message}</p>}
    {query.error && <p role="alert">Updates are temporarily unavailable. <button onClick={() => void query.refetch()}>Try again</button></p>}
    {!embedded && <nav className={`${journal.card} ${journal.filters}`} aria-label="Monthly report navigation">
      <div className={journal.filterField}><span className={journal.label}>Learner</span><div className={journal.learnerField}><AppIcon className="ri-user-line" />{summary.learner?.name}</div></div>
      <div className={journal.filterField}><label htmlFor="monthly-log-month" className={journal.label}>Report month</label><div className={journal.monthControl}>
        <select id="monthly-log-month" className={journal.monthSelect} value={month} disabled={signing.isPending} onChange={e => selectMonth(e.target.value)}>
          {summary.months.map(item => <option key={item.month} value={item.month}>{monthLabel(item.month)} · {item.is_open ? 'In progress' : monthStatus(item)}</option>)}</select>
        <button className={journal.secondaryButton} disabled={!previous || signing.isPending} onClick={() => selectMonth(previous.month)} aria-label="Previous month"><AppIcon className="ri-arrow-left-s-line" /></button>
        <button className={journal.secondaryButton} disabled={!next || signing.isPending} onClick={() => selectMonth(next.month)} aria-label="Next month"><AppIcon className="ri-arrow-right-s-line" /></button>
        {onMonthChange ? <button type="button" className={journal.secondaryButton} onClick={() => onMonthChange()}><AppIcon className="ri-layout-grid-line" />All months</button>
          : <Link className={journal.secondaryButton} to={base}><AppIcon className="ri-layout-grid-line" />All months</Link>}
      </div></div>
    </nav>}
    <LearnerInformation summary={summary} data={displayData} actions={!embedded ? <JournalDownloads summary={summary} month={month} disabled={signing.isPending || (data.source === 'lms' && !(data.student_signature && data.coach_signature))} loadMonth={loadMonth} /> : undefined} />
    {displayData.target_warning && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Target hours: Unavailable. {displayData.target_warning}</p>}
    <MonthlyHours data={displayData} />
    <ActivityLog key={sourceRef ?? 'all'} data={displayData} initialSourceRef={sourceRef}
      contentScope={reader?.cacheScope ?? `monthly-logs:${perspective}:${id}`} loadContent={rowId => reader ? reader.content(month, rowId) : mcmWorkflow ? getLogContent(id, month, rowId, perspective, workflowKey) : getLogContent(id, month, rowId, perspective)} />
    <section className={`${journal.card} ${journal.signoff}`} aria-label={embedded ? 'Learner monthly sign-off' : 'Monthly sign-off'}>
      <div className={journal.sectionHeading}><div><h2 className="font-heading">{embedded ? 'Learner sign-off' : 'Report sign-off'}</h2><p>{data.is_open ? 'This month is still updating. Signatures become available after month-end.' : embedded ? 'The learner signature is captured on the Monthly Coaching Meeting.' : 'Your learner and coach signatures for this month’s record.'}</p></div></div>
      <div className={journal.signoffBody}><div className={reportStyles.reportTableWrap}><table className={reportStyles.signTable} aria-label="Report sign-off">
        <thead><tr>{['Role', 'Signature', 'Print name', 'Date', 'Status'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{signatureRows.map(item => <tr key={item.role}>
          <td data-label="Role">{item.role}{item.own && <span className={journal.ownLabel}>{canActAsStudent && !student ? '(admin on behalf)' : '(you)'}</span>}</td>
          <td className={reportStyles.signatureCell} data-label="Signature">{item.signature ? <img src={item.signature.url} alt={`${item.role} signature`} className={`${design.signatureImage} ${journal.signatureImage}`} />
            : item.own && !readOnly ? <SignatureCapture key={captureVersion} name={auth.account?.displayName || ''} busy={signing.isPending} dialogRole={canActAsStudent ? 'learner' : 'coach'}
              saveError={signing.error?.message} importSignature={canActAsStudent && !student ? undefined : previousMonthSignature(summary.months, month, student ? 'learner' : 'coach')}
              confirmationText="I have reviewed this month's activities and confirm this is my signature."
              onDraftStart={() => { draftDigest.current ??= data.snapshot_digest; }} onDraftReset={() => { draftDigest.current = null; signing.reset(); }}
              onSave={(blob, capture) => signing.mutate({ blob, capture })} /> : <span>{data.is_open ? 'Available after month-end' : 'Awaiting signature'}</span>}</td>
          <td data-label="Print name">{item.signature?.signer_name || '—'}</td><td data-label="Date">{displayDate(item.signature?.signed_at)}</td>
          <td data-label="Status"><RecordBadge tone={item.signature ? 'positive' : 'pending'}>{item.signature ? 'Signed' : data.is_open ? 'Month in progress' : 'Awaiting signature'}</RecordBadge></td>
        </tr>)}</tbody>
      </table></div></div>
      <div className={`${journal.signoffFooter} space-y-3`}>
        {data.locked && !observer && auth.account?.role === 'admin' && perspective === 'learner' && <button className={journal.secondaryButton} disabled={unlocking.isPending} onClick={() => unlocking.mutate()}><AppIcon className="ri-lock-unlock-line" />{unlocking.isPending ? 'Unlockingâ€¦' : 'Unlock monthly log'}</button>}
        {data.locked && <p className={journal.signingNote}><AppIcon className="ri-lock-line" /> This record is locked after both signatures were saved.</p>}
        {canActAsStudent && !readOnly && data.source === 'legacy' && data.can_complete && <button className={journal.primaryButton} disabled={completion.isPending} onClick={() => completion.mutate()}>Complete month</button>}
        <p className={journal.signingNote}>{data.is_open ? 'Activities recorded this month appear here automatically. Signing opens after the month ends.' : mcmWorkflow ? 'This learner log is linked to the Monthly Coaching Meeting. The learner signs the MCM once and the signature appears here automatically.' : readOnly ? 'You are viewing this learner’s record. Each person signs from their own account.' : 'Each person signs from their own account. Saved signatures are retained.'}</p>
        {signing.error && <p role="alert" className="text-red-700">{signing.error.message}</p>}
        {completion.error && <p role="alert" className="text-red-700">{completion.error.message}</p>}
        {unlocking.error && <p role="alert" className="text-red-700">{unlocking.error.message}</p>}
      </div>
    </section>
  </div>;
}
