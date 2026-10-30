import { useEffect, useId, useMemo, useState, type MouseEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight, Award, BookOpen, CheckCircle2, ChevronLeft, ChevronRight, GraduationCap, Layers3, Loader2, Map as MapIcon, Search } from 'lucide-react';
import type { LearnerDetail } from '@/api/learnerDetail';
import type { LearnerMetrics } from '@/api/learnerMetrics';
import { type StudentActivityItem, type StudentActivityResponse, type SubjectAttemptResult } from '@/api/studentActivity';
import { fetchLearnerCertificateTemplate, issueLearnerModuleCertificate, type CertificateTemplateSummary } from '@/api/learnerCertificates';
import { formatHoursMinutes, type JourneyComponent } from '@/utils/learnerJourney';
import { DeferredStudentMaterial as StudentMaterial } from './DeferredStudentMaterial';
import styles from './SubjectWorkspace.module.css';
import { useSubjectMetadata } from './useSubjectLearningData';
import { LearningCatalogue } from './LearningCatalogue';
import { DEFAULT_MODULE_COVER } from './moduleCover';
import { LearningMapHero, SubjectTimeline } from './SubjectTimeline';
import { HolidayNoteHint } from '@/components/feature/HolidayNoteHint';
import { weekHolidayNotes } from '@/pages/learner/training-plan-timeline/model';
import { nativeHref, certificateEligible, learningDate, learningDeadlines, learningPlanSelection, learningHref, nextLearningWeek, continuingLearningWeek, currentLearningWeek, recommendedLearningSubject, resolveLearningSubject, subjectMapWeeks, subjectOpeningActivity, subjectLearningStatus } from './subjectLearning';
import type { LearningSchedule } from '@/api/learnerOverview';
import type { PlanModule, PlanModuleProgress, PlanSession } from '@/api/trainingPlanDashboard';
import { buildUnifiedLearningSummary, type SubjectEntry, type Subject, type CoverMetadata } from './learningSummary';
import { PreviousLearningNotice } from './PreviousLearningNotice';
import { SubjectActivityWorkspace } from './SubjectActivityWorkspace';
import { resolveSubjectWeek } from './subjectLearning';
export type { SubjectEntry, Subject, CoverMetadata, UnifiedLearningSummary } from './learningSummary';


function formatRecordedHours(hours: number) {
  const seconds = Math.max(0, Math.round(hours * 3600));
  return [Math.floor(seconds / 3600) ? `${Math.floor(seconds / 3600)}h` : '',
    Math.floor(seconds % 3600 / 60) ? `${Math.floor(seconds % 3600 / 60)}m` : '',
    seconds % 60 ? `${seconds % 60}s` : ''].filter(Boolean).join(' ') || '0m';
}



function Progress({ done, total, label = 'Subject progress', showFormula = false, compact = false, aggregate, transferred }: {
  done: number; total: number; label?: string; showFormula?: boolean; compact?: boolean; aggregate?: number; transferred?: number;
}) {
  const percent = aggregate ?? (total ? Math.round(done / total * 10000) / 100 : 0);
  return <div className="space-y-2"><div className="flex items-center justify-between gap-2 text-xs"><span className={compact ? 'text-slate-500' : 'text-foreground-500'}>{done} of {total} completed</span><strong className={`shrink-0 tabular-nums ${compact && percent === 100 ? 'text-emerald-700' : 'text-primary-700'}`}>{percent}%</strong></div>
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} className={`h-1.5 overflow-hidden rounded-full ${compact ? 'bg-slate-100' : 'bg-foreground-100'}`}><div className={`h-full rounded-full motion-safe:transition-[width] ${compact ? percent === 100 ? 'bg-emerald-500' : 'bg-gradient-to-r from-primary-500 to-violet-400' : 'bg-primary-500'}`} style={{ width: `${percent}%` }} /></div>
    {transferred != null ? <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900"><strong>Progress merged.</strong> Completed components from both systems count once. Old LMS progress before transfer: {transferred}% (historical).{!compact && <> The current percentage uses this module's component total, so it may differ. Your recorded hours are preserved.</>}</div>
      : showFormula && <p className="text-xs text-foreground-500">Progress = completed activities ÷ total activities × 100{total > 0 ? ` = ${done} ÷ ${total} × 100 = ${percent}%` : '. No activities yet'}. Quizzes count as complete after passing.</p>}
  </div>;
}

function HoursProgress({ completed, planned, label }: {
  completed: number | null; planned: number | null; label: string;
}) {
  const available = completed != null && Number.isFinite(completed) && planned != null && Number.isFinite(planned) && planned > 0;
  const percent = available ? Math.min(100, Math.round(completed / planned * 10000) / 100) : 0;
  const completedLabel = completed != null && Number.isFinite(completed) ? formatRecordedHours(completed) : 'Unavailable';
  const plannedLabel = planned != null && Number.isFinite(planned) ? formatRecordedHours(planned) : 'Unavailable';
  return <div className={styles.hoursProgress}>
    <div className="flex items-center justify-between gap-2 text-xs"><span className="text-slate-500">Hours progress</span><strong className="shrink-0 tabular-nums text-emerald-700">{available ? `${percent}%` : '—'}</strong></div>
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={available ? percent : undefined}
      aria-valuetext={available ? `${completedLabel} completed of ${plannedLabel} planned (${percent}%)` : 'Hours progress unavailable'}
      className={styles.hoursProgressTrack} data-available={available || undefined}>
      <span style={{ width: `${percent}%` }} />
    </div>
    <div className={styles.hoursProgressLabel}><span>Completed {completedLabel}</span><span>Planned {plannedLabel}</span></div>
  </div>;
}

const SUBJECT_CARD_TONES = ['purple', 'navy', 'green', 'gold', 'blue', 'rose'] as const;
type SubjectCardTone = typeof SUBJECT_CARD_TONES[number];

export function Cover({ title, url, large = false, fallbackUrl }: { title: string; url?: string; large?: boolean; fallbackUrl?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const imageUrl = url && !failed ? url : fallbackUrl;
  return <div className={`${styles.cover} ${large ? styles.coverLarge : ''}`}>
    {imageUrl ? <img src={imageUrl} alt={`${title} cover`} loading="lazy" decoding="async" onError={() => setFailed(true)} className={`h-full w-full object-cover motion-safe:transition-transform motion-safe:duration-500 motion-safe:group-hover:scale-105 ${imageUrl === fallbackUrl ? styles.defaultCoverImage : ''}`} /> : <div className={`${styles.coverFallback} flex h-full ${large ? 'items-center justify-center' : 'items-end p-4'}`}>
      <div aria-hidden="true" className={styles.coverOrbit} />
      <span className={`${styles.coverSymbol} relative flex items-center justify-center rounded-2xl border border-white/90 bg-white/85 text-primary-600 shadow-sm ${large ? 'h-16 w-16' : 'h-11 w-11'}`}><BookOpen className={large ? 'h-8 w-8' : 'h-5 w-5'} strokeWidth={1.7} aria-hidden="true" /></span>
    </div>}
  </div>;
}

function SubjectCertificateAction({
  subject,
  template,
  csrfToken,
  kind,
  learnerId,
}: {
  subject: Subject;
  template: CertificateTemplateSummary | null;
  csrfToken: string;
  kind?: string;
  learnerId?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [cachedHref, setCachedHref] = useState('');
  const ready = !!kind && !!learnerId && certificateEligible(subject, template);
  const cacheKey = ready && template && kind && learnerId
    ? `learner-certificate:${kind}:${learnerId}:${subject.id}:v${template.version}`
    : '';

  useEffect(() => {
    setCachedHref('');
    if (!ready || !kind || !learnerId || !cacheKey) return;
    try {
      const stored = window.localStorage.getItem(cacheKey);
      if (stored) {
        setCachedHref(stored);
        return;
      }
    } catch {
      // Local storage can be unavailable in private or locked-down contexts.
    }
    // Existing certificates are resolved by the idempotent issue endpoint on
    // click. Avoid one status request (and an eligibility rebuild) per card.
  }, [ready, kind, learnerId, subject.id, cacheKey]);

  if (!ready) return null;

  const issue = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (cachedHref) {
      window.open(cachedHref, '_blank', 'noopener,noreferrer');
      return;
    }
    const certificateWindow = window.open('about:blank', '_blank');
    if (certificateWindow) {
      certificateWindow.document.title = 'Preparing certificate';
      certificateWindow.document.body.innerHTML = '<main style="min-height:100vh;display:grid;place-items:center;font-family:Inter,system-ui,sans-serif;color:#0f172a;background:#f8fafc"><section style="width:min(92vw,760px);padding:32px;border:1px solid #e2e8f0;border-radius:16px;background:white;box-shadow:0 24px 70px rgba(15,23,42,.16);text-align:center"><h1 style="margin:0 0 8px;font-size:24px">Preparing certificate</h1><p style="margin:0;color:#475569">Your certificate is being generated...</p></section></main>';
    }
    setBusy(true);
    setMessage('');
    try {
      const result = await issueLearnerModuleCertificate(kind, learnerId, subject.id, csrfToken);
      const href = result.certificate?.verificationUrl;
      if (href) {
        setCachedHref(href);
        if (cacheKey) {
          try {
            window.localStorage.setItem(cacheKey, href);
          } catch {
            // Non-critical cache.
          }
        }
      }
      if (href && certificateWindow) certificateWindow.location.replace(href);
      else if (href) window.location.assign(href);
      else {
        if (certificateWindow) certificateWindow.close();
        setMessage('Certificate issued, but the verification link was not returned.');
      }
    } catch (error) {
      if (certificateWindow) certificateWindow.close();
      setMessage(error instanceof Error ? error.message : 'Could not issue certificate.');
    } finally {
      setBusy(false);
    }
  };

  return <div className={styles.certificateAction}>
    <button type="button" onClick={issue} disabled={busy} className={styles.certificateButton}>
      {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Award size={14} aria-hidden="true" />}
      {busy ? 'Issuing certificate' : cachedHref ? 'View certificate' : 'Get certificate'}
    </button>
    {message && <p className={styles.certificateError}>{message}</p>}
  </div>;
}

export function SubjectCard({ subject, cover, tone = 'purple', onOpen, template, csrfToken, kind, learnerId, planModule, moduleProgress }: {
  subject: Subject;
  cover?: string;
  tone?: SubjectCardTone;
  onOpen: () => void;
  template: CertificateTemplateSummary | null;
  csrfToken: string;
  kind?: string;
  learnerId?: string;
  planModule?: Omit<PlanModule, 'description' | 'coach_name'> & Partial<Pick<PlanModule, 'description' | 'coach_name'>>;
  moduleProgress?: Pick<PlanModuleProgress, 'hours'> & Partial<Omit<PlanModuleProgress, 'hours'>>;
}) {
  const total = subject.card?.activityCount ?? subject.activities.length;
  const completed = subject.card?.completedActivityCount ?? subject.activities.filter((activity) => activity.completed).length;
  const isComplete = total > 0 && completed === total;
  const certificateReady = certificateEligible(subject, template);
  const status = subjectLearningStatus(subject);
  const next = nextLearningWeek(subject)?.activities.find(entry => !entry.completed);
  const moduleName = planModule?.title || subject.title;
  const tutorName = planModule?.tutor_name?.trim() || 'To be assigned';
  const startDate = planModule?.start_date ? learningDate(planModule.start_date) : 'Not scheduled';
  const endDate = planModule?.end_date ? learningDate(planModule.end_date) : 'Not scheduled';
  const sessionCount = planModule?.educational_session_count;
  const completedHours = subject.source === 'legacy' || subject.replacementCourseRef || subject.transferredProgress != null
    ? subject.acceptedHours ?? null
    : moduleProgress?.hours.actual ?? subject.acceptedHours ?? null;
  const plannedHours = planModule?.total_otjh ?? null;
  const origin = subject.importedCourseRef || subject.replacementCourseRef ? 'legacy' : subject.source;
  return <article className={`group ${styles.card} ${styles.subjectTheme} ${styles.moduleCard}`} data-tone={tone} data-source={origin}>
    <button type="button" onClick={onOpen} className={styles.cardButton}>
      <div className="relative w-full"><Cover title={subject.title} url={cover} fallbackUrl={DEFAULT_MODULE_COVER} /></div>
      <div className={styles.cardBody}>
        <div className={styles.cardMetaRow}>
          <span className={styles.sourceBadge} data-source={origin}>
            {origin === 'legacy' ? <BookOpen size={13} aria-hidden="true" /> : <GraduationCap size={13} aria-hidden="true" />}
            {subject.source === 'legacy' ? 'Old LMS · Previous learning'
              : subject.replacementCourseRef ? 'Old LMS · Updated content'
              : subject.importedCourseRef ? 'Old LMS · Current learning plan' : 'New LMS · Current learning plan'}
          </span>
          {subject.source === 'legacy' && <span className={styles.learningModeBadge} data-source="legacy">Review only</span>}
          {subject.transferPending && <span className={styles.learningModeBadge}>Transfer pending</span>}
          {subject.demoProgress && <span className={styles.learningModeBadge}>Demo progress</span>}
          <span className={styles.status} data-state={isComplete ? 'complete' : status === 'In progress' ? 'started' : 'new'}>
            <span aria-hidden="true" className={styles.statusDot} />{status}
          </span>
          <p className={styles.cardEyebrow}><Layers3 size={13} aria-hidden="true" />{total} {subject.recordedHistory ? 'recorded activities' : total === 1 ? 'activity' : 'activities'}</p>
          {certificateReady && <span className={styles.certificateBadge}><Award size={13} aria-hidden="true" />Ready</span>}
        </div>
        <h3 className={styles.cardTitle}>{subject.title}</h3>
        <p className="mb-2 text-xs text-foreground-500 break-words">Cohort: <span className="font-semibold">{planModule?.cohort_name?.trim() || 'Not available'}</span></p>
        <dl className={styles.moduleDetails} aria-label={`${moduleName} module details`}>
          <div><dt>Tutor</dt><dd>{tutorName}</dd></div>
          <div><dt>Start date</dt><dd>{startDate}</dd></div>
          <div><dt>End date</dt><dd>{endDate}</dd></div>
          <div><dt>Sessions</dt><dd>{sessionCount == null ? 'Not set' : `${sessionCount} ${sessionCount === 1 ? 'session' : 'sessions'}`}</dd></div>
        </dl>
        <p className={styles.cardDescription}>Explore your learning materials and activities.</p>
        <div className={styles.cardProgress}><Progress done={completed} total={total} compact aggregate={subject.aggregateProgress ?? subject.card?.progress} transferred={subject.transferredProgress} label={`${subject.title} ${subject.source === 'legacy' ? 'previous learning' : 'current plan'} activities progress`} />
          <HoursProgress completed={completedHours} planned={plannedHours} label={`${subject.title} hours progress`} />
          {subject.recordedHistory && <p className="mt-2 text-xs text-foreground-500">Recorded activities only · Full catalogue: {subject.catalogueCount ?? 0}</p>}
        </div>
        <span className={styles.nextActivity}>{isComplete ? <CheckCircle2 size={20} /> : <BookOpen size={20} />}<span><small>{isComplete ? 'Well done' : 'Next up'}</small><strong>{isComplete ? subject.recordedHistory ? 'All recorded activities complete' : 'All activities complete' : subject.card?.nextTitle || next?.title || 'Explore this subject'}</strong></span><ChevronRight size={16} /></span>
        <span className={styles.cardAction}>Open subject<ArrowRight size={16} aria-hidden="true" /></span>
      </div>
    </button>
    <SubjectCertificateAction subject={subject} template={template} csrfToken={csrfToken} kind={kind} learnerId={learnerId} />
  </article>;
}

export function SubjectCardsSkeleton() {
  return <div role="status" className={styles.workspace} aria-label="Loading subjects">
    <div className={styles.loadingHeader}><div><span className={styles.loadingLabel}>Loading subjects</span><p className={styles.loadingHint}>Getting your activities and progress ready…</p></div><BookOpen size={22} aria-hidden="true" /></div>
    <div className={`${styles.stats} ${styles.skeletonStats}`} aria-hidden="true">{[0, 1, 2, 3].map(key => <div key={key} className={styles.stat}><span className={`${styles.skeletonLine} ${styles.mediumLine}`} /><span className={`${styles.skeletonLine} ${styles.shortLine}`} /></div>)}</div>
    <div className={styles.catalogueLayout} aria-hidden="true"><div className={styles.catalogueMain}><div className={styles.skeletonToolbar}><span className={`${styles.skeletonLine} ${styles.mediumLine}`} /></div><div className={styles.grid}>{[0, 1, 2, 3].map(key => <div key={key} className={`${styles.card} ${styles.skeleton}`}>
      <div className={styles.cover}><span className={styles.skeletonIcon} /><span className={styles.skeletonBadge} /></div>
      <div className={styles.cardBody}><span className={`${styles.skeletonLine} ${styles.shortLine}`} /><div className={styles.cardTitle}><span className={styles.skeletonLine} /><span className={`${styles.skeletonLine} ${styles.mediumLine}`} /></div>
        <div className={styles.cardProgress}><span className={`${styles.skeletonLine} ${styles.mediumLine}`} /><span className={styles.skeletonTrack} /></div><span className={styles.skeletonNext} /><span className={styles.skeletonAction} /></div>
    </div>)}</div></div><div className={styles.catalogueAside}>{[0, 1].map(key => <div key={key} className={`${styles.asidePanel} ${styles.skeletonAside}`}><span className={`${styles.skeletonLine} ${styles.mediumLine}`} /><span className={styles.skeletonNext} /><span className={styles.skeletonLine} /><span className={`${styles.skeletonLine} ${styles.shortLine}`} /></div>)}</div></div>
  </div>;
}


function ActivityRow({ entry, kind, learnerId, onProgress, defaultOpen = false, readOnly = false, direct = false }: { defaultOpen?: boolean; readOnly?: boolean; direct?: boolean; entry: SubjectEntry; kind?: string; learnerId?: string; onProgress?: (result: SubjectAttemptResult) => void }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (defaultOpen) setOpen(true); }, [defaultOpen]);
  const contentId = useId();
  const href = nativeHref(entry, kind, learnerId);
  const legacy = entry.legacy;
  const canOpenMaterial = legacy?.can_open_material !== false;
  const canReviewQuiz = legacy?.catalogue_kind === 'quiz' && entry.completed;
  const canOpen = canOpenMaterial || legacy?.catalogue_kind === 'quiz';
  const sourceHref = legacy?.source_material_activity_id && kind && learnerId
    ? `${learningHref('catalogue', kind, learnerId, `legacy:${legacy.group_id}`)}&activity=${encodeURIComponent(legacy.source_material_activity_id)}` : null;
  const score = entry.bestScorePercent ?? legacy?.best_score_percent ?? (legacy?.quiz_score != null && legacy.quiz_maximum_score ? legacy.quiz_score / legacy.quiz_maximum_score * 100 : null);
  return <div role="group" aria-label={`${entry.title} activity`} className="border-t border-foreground-100 first:border-t-0"><div className="grid grid-cols-[2rem_minmax(0,1fr)] items-start gap-3 p-4 sm:flex sm:flex-wrap sm:items-center">
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${entry.completed ? 'bg-emerald-50 text-emerald-600' : 'bg-background-100 text-foreground-400'}`}>{entry.completed ? <CheckCircle2 size={18} /> : <BookOpen size={16} />}</span>
    <div className="min-w-0 flex-1"><h5 className="text-sm font-semibold text-foreground-900">
      {legacy && canOpen && kind && learnerId && !direct ? <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-controls={contentId} className="text-left text-primary-700 underline-offset-4 hover:underline focus-visible:underline">{entry.title}</button>
        : href ? <Link to={href} className="text-primary-700 underline-offset-4 hover:underline focus-visible:underline">{entry.title}</Link> : entry.title}
    </h5><p className="mt-1 text-xs text-foreground-500">{[entry.category, entry.schedule.date, score != null ? `Best score ${Math.round(score)}%` : ''].filter(Boolean).join(' · ')}</p>
      {legacy?.section_title && <p className="mt-1 text-xs text-foreground-500">Lecture: {legacy.section_title}</p>}
    </div>
    <div className="col-start-2 flex flex-wrap items-center gap-2 sm:ml-auto"><span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${entry.completed ? 'bg-emerald-50 text-emerald-700' : 'bg-background-100 text-foreground-600'}`}>{entry.completed ? 'Complete' : 'Not complete'}</span>
    {legacy && canOpen && kind && learnerId && !direct && <button onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-controls={contentId} className="rounded-lg border border-foreground-200 px-3 py-2 text-xs font-semibold text-primary-700">{canReviewQuiz ? open ? 'Close quiz' : 'View quiz' : open ? 'Close activity' : 'Open activity'}</button>}
    {!legacy && href && <Link to={href} className="rounded-lg border border-foreground-200 px-3 py-2 text-xs font-semibold text-primary-700">Open activity</Link>}
    {sourceHref && <Link to={sourceHref} className="rounded-lg border border-foreground-200 px-3 py-2 text-xs font-semibold text-primary-700">View source material</Link>}
    {((legacy && !canOpen && !sourceHref) || (!legacy && !href)) && <span className="text-xs text-foreground-500">{entry.native?.retired ? 'No longer in this module' : 'Content not available yet'}</span>}</div>
  </div>
    <PreviousLearningNotice learning={entry.native?.previousLearning} kind={kind} learnerId={learnerId} />
    {legacy && <p className="px-4 pb-3 text-[11px] text-foreground-400">OTJH: {legacy.hours_mapped ? formatHoursMinutes(legacy.actual) : 'Unavailable'} · Planned: {legacy.planned_hours_mapped ? formatHoursMinutes(legacy.planned) : 'Unavailable'}</p>}
    <div id={contentId}>{(direct || open) && legacy && canOpen && kind && learnerId && <div role="region" aria-label={`${entry.title} content`} className="p-3 pt-0"><StudentMaterial kind={kind} learnerId={learnerId} groupId={legacy.group_id} activityId={legacy.source_activity_id} completed={entry.completed} readOnly={readOnly || canReviewQuiz} reviewQuiz={canReviewQuiz} catalogueQuiz={legacy.catalogue_kind === 'quiz'} onProgress={readOnly || canReviewQuiz ? undefined : onProgress} /></div>}</div>
  </div>;
}

export function StudentActivityPanel({ data: incomingData, loading, error, onRetry, kind, learnerId, real: incomingReal = null, onProgress, metrics, view = 'catalogue', schedule, scheduleLoading = false, upcomingSessions, moduleProgress, resolvedMetadata, catalogueSubjects }: {
  kind?: string; learnerId?: string; real?: LearnerDetail | null; data: StudentActivityResponse | null;
  loading: boolean; error: string | null; onRetry: () => void; onProgress?: (changes?: { id: string; total: number; completed: number }[]) => void;
  metrics?: LearnerMetrics | null;
  view?: 'catalogue' | 'map'; schedule?: LearningSchedule | null; scheduleLoading?: boolean; upcomingSessions?: PlanSession[];
  moduleProgress?: Record<string, PlanModuleProgress>; resolvedMetadata?: CoverMetadata; catalogueSubjects?: Subject[];
}) {
  const [search, setSearch] = useState('');
  const location = useLocation();
  const navigate = useNavigate();
  const routeParams = new URLSearchParams(location.search);
  const requestedActivity = routeParams.get('activity');
  const selected = routeParams.get('subject') || routeParams.get('module');
  const selectedWeek = routeParams.get('week');
  const continueCurrentWeek = selectedWeek === 'current';
  const select = (subject?: string, week?: string) => {
    const params = new URLSearchParams(location.search);
    params.delete('module'); params.delete('week'); params.delete('subject'); params.delete('activity');
    if (subject) params.set('subject', subject);
    if (week) params.set('week', week);
    navigate({ pathname: location.pathname, search: params.toString() });
  };
  const identity = `${kind}:${learnerId}`;
  const [certificateConfig, setCertificateConfig] = useState<{ template: CertificateTemplateSummary | null; csrfToken: string }>({ template: null, csrfToken: '' });
  const { metadata: fetchedMetadata, error: imageError, retry: retryMetadata } = useSubjectMetadata(incomingData, incomingReal, kind, learnerId, !error && !resolvedMetadata, true);
  const incomingMetadata = resolvedMetadata ?? fetchedMetadata;
  const ready = !loading && !error && (!learnerId || !!incomingMetadata);
  const [snapshot, setSnapshot] = useState<{
    identity: string; data: StudentActivityResponse | null; real: LearnerDetail | null; metadata?: CoverMetadata | null;
  } | null>(null);
  useEffect(() => {
    if (ready) setSnapshot({ identity, data: incomingData, real: incomingReal, metadata: incomingMetadata });
  }, [ready, identity, incomingData, incomingReal, incomingMetadata]);
  // Publish a complete set of cards and totals together. During a refresh keep
  // the last complete set, including an open activity and its saved progress.
  // A previous learner's snapshot is never eligible for reuse.
  const displayed = ready ? { data: incomingData, real: incomingReal, metadata: incomingMetadata }
    : snapshot?.identity === identity ? snapshot : null;
  const data = displayed?.data || null;
  const real = displayed?.real || null;
  const metadata = displayed?.metadata;
  const recordedOtjh = metrics !== undefined ? metrics?.otjh.actual ?? null : data?.audit_lms_actual ?? data?.recorded_otjh_total ?? data?.actual_total ?? null;
  const plannedOtjh = metrics !== undefined ? metrics?.otjh.planned ?? null : data?.audit_tp_planned ?? data?.planned_total ?? null;
  const hasProgrammeOtjh = data?.audit_lms_actual != null || data?.audit_tp_planned != null;
  const [savedProgress, setSavedProgress] = useState<{ identity: string; activities: Record<string, SubjectAttemptResult> }>({ identity, activities: {} });
  useEffect(() => {
    setCertificateConfig({ template: null, csrfToken: '' });
    if (view !== 'catalogue' || selected || requestedActivity || !kind || !learnerId) return;
    let cancelled = false;
    fetchLearnerCertificateTemplate(kind, learnerId)
      .then((result) => { if (!cancelled) setCertificateConfig({ template: result.template, csrfToken: result.csrfToken || '' }); })
      .catch(() => { if (!cancelled) setCertificateConfig({ template: null, csrfToken: '' }); });
    return () => { cancelled = true; };
  }, [view, kind, learnerId, selected, requestedActivity]);
  const updatedData = useMemo(() => {
    if (!data || savedProgress.identity !== identity) return data;
    return { ...data, activities: data.activities.map((item) => {
      const saved = savedProgress.activities[item.activity_id];
      if (!saved) return item;
      const scores = [item.best_score_percent, saved.score_percent].filter((score): score is number => score != null);
      return { ...item, completed: item.completed || saved.completed, best_score_percent: scores.length ? Math.max(...scores) : null };
    }) };
  }, [data, identity, savedProgress]);
  const recordProgress = (activityId: string, result: SubjectAttemptResult) => {
    setSavedProgress((previous) => {
      const activities = previous.identity === identity ? previous.activities : {};
      const existing = activities[activityId];
      const scores = [existing?.score_percent, result.score_percent].filter((score): score is number => score != null);
      return { identity, activities: { ...activities, [activityId]: { ...result, completed: !!existing?.completed || result.completed, score_percent: scores.length ? Math.max(...scores) : null } } };
    });
    const projected = updatedData ? { ...updatedData, activities: updatedData.activities.map(item => item.activity_id === activityId
      ? { ...item, completed: item.completed || result.completed } : item) } : null;
    const changed = buildUnifiedLearningSummary(projected, real, metadata);
    onProgress?.(changed.subjects.map(subject => ({ id: subject.id, total: subject.activities.length,
      completed: subject.activities.filter(item => item.completed).length })));
  };
  const summary = useMemo(() => buildUnifiedLearningSummary(updatedData, real, metadata), [updatedData, real, metadata]);
  const subjects = useMemo(() => catalogueSubjects
    ? [...catalogueSubjects.filter(card => !summary.subjects.some(subject => subject.id === card.id || subject.replacementCourseRef === card.id)), ...summary.subjects]
      .sort((a, b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id))
    : summary.subjects, [catalogueSubjects, summary.subjects]);
  // Canonical evidence IDs identify progress records, not source materials.
  // Refuse ambiguous matches rather than opening a different course's material.
  const activityMatches = requestedActivity ? subjects.flatMap(subject => [...subject.activities, ...(subject.archivedActivities || [])]
    .filter(entry => requestedActivity.startsWith('record:')
      ? entry.legacy?.record_ids?.includes(requestedActivity.slice(7))
        || /^record:[^:]+:[^:]+$/.test(entry.id) && entry.id.slice(entry.id.lastIndexOf(':') + 1) === requestedActivity.slice(7)
      : entry.id === requestedActivity || String(entry.legacy?.source_activity_id) === requestedActivity)
    .map(entry => ({ subject, entry }))) : [];
  const activityTarget = activityMatches.length === 1 ? activityMatches[0] : undefined;
  const deadlines = useMemo(() => learningDeadlines(subjects), [subjects]);
  // Assign colours from the full ID-sorted set, before display filters or sorts,
  // so finding, completing or renaming a subject does not change its colour.
  const subjectTones = useMemo(() => new Map([...subjects]
    .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
    .map((subject, index) => [subject.id, SUBJECT_CARD_TONES[index % SUBJECT_CARD_TONES.length]] as const)), [subjects]);
  const planModulesBySubject = useMemo(() => {
    const modules = new Map<string, PlanModule>();
    for (const module of schedule?.modules || []) {
      const subject = resolveLearningSubject(subjects, `current:${module.id}`, metadata, schedule);
      if (subject && !modules.has(subject.id)) modules.set(subject.id, module);
    }
    return modules;
  }, [subjects, metadata, schedule]);
  const covers = { ...data?.covers, ...metadata?.covers };
  const term = search.trim().toLocaleLowerCase();
  const planSelection = learningPlanSelection(subjects, real, metadata, schedule);
  const planLabel = planSelection.status === 'current' ? 'Current module' : planSelection.status === 'next' ? 'Next module' : planSelection.status === 'previous' ? 'Previous module' : 'Dates pending';
  const currentSubject = recommendedLearningSubject(subjects, real, metadata, schedule);
  const defaultSubject = planSelection.status !== 'undated' ? planSelection.entries[0]?.subject : undefined;
  const continuedSubject = continueCurrentWeek ? defaultSubject || subjects.find(subject => currentLearningWeek(subject)) : undefined;
  const active = activityTarget?.subject || resolveLearningSubject(subjects, selected, metadata, schedule)
    || (!selected && !scheduleLoading ? (view === 'map' ? defaultSubject : continuedSubject) : undefined);
  const activePlan = planSelection.entries.find(entry => entry.subject.id === active?.id)?.module;
  const noScheduledModule = scheduleLoading ? 'Finding your current module…'
    : !subjects.length ? 'Your weeks will appear when a module is assigned.'
    : planSelection.status === 'unavailable' ? 'Training plan dates are unavailable. Choose a module below to explore your learning.'
    : planSelection.status === 'undated' ? 'Your training plan needs module dates before a current module can be identified. You can still choose a module below.'
    : 'No scheduled module is available in your learning map. Choose a module below to explore your learning.';
  const weeks = active ? subjectMapWeeks(active, real, metadata, schedule) : [];
  // The curriculum team's holiday hints for the module on screen. Only weeks
  // they published one for are in here, so a lookup that misses shows nothing.
  const holidayNotes = weekHolidayNotes(activePlan?.curriculumSlots
    || (active ? planModulesBySubject.get(active.id)?.curriculumSlots : undefined));
  const activeWeek = activityTarget ? weeks.find(week => week.activities.some(entry => entry.id === activityTarget.entry.id))
    : continueCurrentWeek && active ? continuingLearningWeek(active)
    : selectedWeek ? resolveSubjectWeek(weeks, selectedWeek) : undefined;
  // The header and course cards describe the same complete catalogue. Programme
  // metrics count ledger records, which can omit activities not yet started.
  const total = summary.activityCount;
  const done = summary.completedActivityCount;
  const percent = summary.percent;
  if (!displayed && (error || imageError)) return <div role="alert" className="rounded-2xl border bg-white p-6"><p className="font-semibold">Could not load your subjects</p><p className="mt-2 text-sm text-foreground-500">{error || imageError}</p><button onClick={error ? onRetry : retryMetadata} className="mt-4 rounded-lg border px-4 py-2 text-sm font-semibold">Try again</button></div>;
  if (!displayed) return <SubjectCardsSkeleton />;
  if (requestedActivity && !activityTarget) return <div role="alert" className={styles.empty}><p>{activityMatches.length > 1 ? 'This evidence matches more than one learning activity. Choose the correct module to review it.' : 'This evidence activity is not available in My Learning.'}</p><button onClick={() => select()}>View modules</button></div>;
  // Catalogue links open the existing player directly. Imported-only courses
  // and weeks awaiting content keep their material browser available.
  const openingActivity = !requestedActivity && active && !active.replacementCourseRef && view === 'catalogue' && (!selectedWeek || activeWeek)
    ? subjectOpeningActivity(active, activeWeek) : undefined;
  const openingHref = openingActivity ? nativeHref(openingActivity, kind, learnerId) : null;
  if (openingHref) return <Navigate to={openingHref} replace />;
  return <section className={`${styles.workspace} space-y-5`} aria-label="Your subjects" aria-busy={!ready && !error && !imageError}>
    {view === 'map' && <LearningMapHero subject={active} weeks={weeks} kind={kind} learnerId={learnerId} planModule={activePlan} planLabel={activePlan ? planLabel : 'Selected module'} />}
    {view === 'catalogue' && data?.learner_name && <p className="text-sm text-foreground-500">{data.learner_name}</p>}
    {active && view === 'catalogue' && <header className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-widest text-primary-600">My learning</p><h2 className="mt-1 text-2xl font-bold text-foreground-900">{active.title}</h2></div>
      <label className={`${styles.search} w-full min-w-0 sm:max-w-xs`}><Search size={17} aria-hidden="true" /><input aria-label="Search modules or activities" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search subjects or activities" /></label>
    </header>}
    {imageError && <p role="alert" className="text-sm text-amber-800">{imageError} <button onClick={retryMetadata} className="font-semibold underline">Try again</button></p>}
    {error && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">{error} <button onClick={onRetry} className="font-semibold underline">Try again</button></p>}
    {data?.source_status === 'historical' && data.verification_status !== 'verified' && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">Showing saved previous learning. The latest results could not be verified. <button onClick={onRetry} className="font-semibold underline">Try again</button></p>}
    {selected && !active && <p role="alert" className={styles.empty}>This module is not available in your learning plan. <button onClick={() => select()}>Show your modules</button></p>}
    {view === 'map' && <div className={styles.mapToolbar}>
      <label className={styles.selectLabel}>Module<select aria-label="Filter by module" value={active?.id || ''} onChange={event => { setSearch(''); select(event.target.value); }}><option value="" disabled>{scheduleLoading ? 'Finding your current module…' : 'Choose a module'}</option>{subjects.map(subject => <option key={subject.id} value={subject.id}>{subject.title}{planSelection.entries.some(entry => entry.subject.id === subject.id) ? ` · ${planLabel}` : ''}</option>)}</select></label>
      <label className={styles.search}><Search size={18} aria-hidden="true" /><input aria-label="Search modules or activities" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search weeks or activities…" /></label>
      <Link to={learningHref('catalogue', kind, learnerId)} className={styles.textLink}>My Learning<ArrowRight size={17} aria-hidden="true" /></Link>
    </div>}
    {view === 'catalogue' && active && (!selectedWeek || activeWeek || activityTarget) ? <>
      <div className="flex flex-wrap items-center justify-between gap-3"><button onClick={() => select()} className={styles.textLink}><ChevronLeft size={17} />All subjects</button><Link to={learningHref('map', kind, learnerId, active.id)} className={styles.textLink}><MapIcon size={17} />View learning map<ArrowRight size={16} /></Link></div>
      <SubjectActivityWorkspace key={`${kind}:${learnerId}:${active.id}`} subject={active} weeks={weeks} requestedWeek={activeWeek?.id}
        requestedActivity={activityTarget?.entry.id} search={search} kind={kind} learnerId={learnerId}
        progress={<>{active.demoProgress && <p className="mb-2 text-sm">Demo progress for a transfer rehearsal. No study hours or completed activities are claimed.</p>}<Progress done={active.activities.filter(entry => entry.completed).length} total={active.activities.length} showFormula={!active.demoProgress} aggregate={active.aggregateProgress} transferred={active.transferredProgress} /></>}
        renderWeekProgress={week => <><HolidayNoteHint note={week.weekId ? holidayNotes.get(week.weekId) : undefined} className="mb-2" /><Progress done={week.activities.filter(entry => entry.completed).length} total={week.activities.length} label="Week progress" /></>}
        renderActivity={entry => <ActivityRow entry={entry} kind={kind} learnerId={learnerId} direct
          onProgress={result => { if (entry.legacy) recordProgress(entry.legacy.activity_id, result); }} />} />
    </> : activeWeek ? <>
      <button onClick={() => select(active!.id)} className={styles.textLink}><ChevronLeft size={17} />{view === 'map' ? 'Back to timeline' : 'Back to subject'}</button>
      <section className={styles.weekMaterial} aria-label={`${activeWeek.label} materials`}><header><p className={styles.eyebrow}>{activeWeek.label}</p><h3>{activeWeek.title}</h3><HolidayNoteHint note={activeWeek.weekId ? holidayNotes.get(activeWeek.weekId) : undefined} className="mb-2" /><Progress done={activeWeek.activities.filter(a => a.completed).length} total={activeWeek.activities.length} label="Week progress" /></header>
        {activeWeek.activities.filter(entry => !term || active!.title.toLowerCase().includes(term) || entry.title.toLowerCase().includes(term)).map(entry => <ActivityRow key={entry.id} defaultOpen={entry.id === activityTarget?.entry.id} entry={entry} kind={kind} learnerId={learnerId} onProgress={result => { if (entry.legacy) recordProgress(entry.legacy.activity_id, result); }} />)}
        {!activeWeek.activities.length && <p className="p-6 text-sm text-foreground-500">Learning materials will appear here when they are added to this week.</p>}
        {activeWeek.activities.length > 0 && !activeWeek.activities.some(entry => !term || active!.title.toLowerCase().includes(term) || entry.title.toLowerCase().includes(term)) && <p className="p-6 text-sm text-foreground-500">No activities match your search.</p>}
      </section>
    </> : selectedWeek && active ? <div className={styles.empty}><p>{continueCurrentWeek ? 'Learning materials will appear here when they are added to this module.' : 'This week is no longer available in this module.'}</p><button onClick={() => select(active.id)}>View module</button></div>
      : view === 'map' ? (active ? <SubjectTimeline subject={active} weeks={weeks} search={search} holidayNotes={holidayNotes} onOpen={week => { setSearch(''); select(active.id, week); }} /> : !selected && <div className={styles.empty}>{noScheduledModule}</div>)
      : !active ? <>
      <LearningCatalogue summary={summary} search={search} onSearch={setSearch} current={currentSubject} total={total} done={done} percent={percent} kind={kind} learnerId={learnerId}
        deadlines={deadlines}
        moduleStartDate={subject => planModulesBySubject.get(subject.id)?.start_date}
        onContinue={subject => { setSearch(''); select(subject.id, 'current'); }}
        covers={covers} upcomingSessions={upcomingSessions}
        renderCard={subject => {
          // Navigation aliases must not replace historical course hours with
          // the duplicate Builder copy's planned hours or progress.
          const replacesImport = subject.source === 'legacy' && Object.values(metadata?.imported_courses || {}).includes(subject.id);
          const planModule = replacesImport ? undefined : planModulesBySubject.get(subject.id);
          return <SubjectCard key={subject.id} subject={subject} cover={covers[subject.id]} tone={subjectTones.get(subject.id)} onOpen={() => select(subject.id)} template={certificateConfig.template} csrfToken={certificateConfig.csrfToken} kind={kind} learnerId={learnerId} planModule={planModule} moduleProgress={planModule ? moduleProgress?.[planModule.id] : undefined} />;
        }} />
      {(data || metrics) && <><div className="flex flex-wrap gap-6 rounded-xl bg-background-100 px-4 py-3 text-xs"><div><span className="block text-foreground-500">Recorded OTJH</span><strong>{recordedOtjh == null ? 'Unavailable' : formatHoursMinutes(recordedOtjh)}</strong></div><div><span className="block text-foreground-500">Planned OTJH</span><strong>{plannedOtjh == null ? 'Unavailable' : formatHoursMinutes(plannedOtjh)}</strong></div></div>{hasProgrammeOtjh && <p className="text-[12px] text-foreground-500">Programme totals include accepted historical hours and new recorded learning. Planned hours come from your training plan.</p>}</>}
    </> : null}
    {active?.archivedActivities?.length ? <details open={!!activityTarget?.entry.legacy} className="rounded-xl border bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-primary-700">Previous learning record</summary>
      <p className="py-3 text-xs text-foreground-500">Original results and hours are retained here. Matching completions count once in the updated content above.</p>
      {active.archivedActivities.filter(entry => !term || entry.title.toLocaleLowerCase().includes(term)).map(entry =>
        <ActivityRow key={entry.id} entry={entry} kind={kind} learnerId={learnerId} readOnly defaultOpen={entry.id === activityTarget?.entry.id} />)}
    </details> : null}
  </section>;
}
