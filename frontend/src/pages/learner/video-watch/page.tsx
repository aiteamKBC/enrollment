import { fileProbe } from './page.helpers';
import { archiveTimingDraft, timingSessionExpired } from '@/lib/timingRecovery';
import { useManualTimeConfirmation } from '@/hooks/useManualTimeConfirmation';
import { ManualTimeSummary } from '@/components/feature/ManualTimeConfirmation';
import { startActivityClock } from '@/lib/activityClock';
import { fetchAttachmentResource, fetchPdfResource } from '@/api/pdfResource';
import { observeRewardInterval, flushRewardObservations, clearRewardObservations } from '@/lib/rewardTracking';
import { SessionResults } from '@/components/feature/SessionResults';
import { narrationFromHtml, narrationFromPdf, ReadingNarrationError, ReadingPdfNarrationContext, useReadingNarration, type PdfPageReader } from './readingNarration';
import { parsePersonalLearning } from '@/lib/personalLearning';
import { PreviousLearningNotice } from '@/pages/learner/my-learning/PreviousLearningNotice';
import { useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { Mic } from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import DOMPurify from 'dompurify';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { EmptyState } from '@/pages/users/components/ui';
import { fetchLearnerDetail, type LearnerDetail, type LearnerKind, type LearnerKsbItem } from '@/api/learnerDetail';
import { markingVerdict } from '@/lib/markingVerdict';
import { submitVideoProgress } from '@/api/videos';
import { submitComponentProgress } from '@/api/components';
import { startTimeTracking, resumeTimeTracking, checkpointTimeTracking, type TimeTrackingSession, type TrackingCountingMode } from '@/api/timeTracking';
import { EvidenceFilesButton, EvidencePreviewModal, type EvidencePreview } from '@/components/feature/EvidenceFilesButton';
import { AssignmentEvidence } from '@/components/feature/AssignmentEvidence';
import {
  buildLearnerJourney, componentTypeMeta, componentContentKind, componentNoun, hasComponentContent, isOpenableComponent, gradePercent, formatHoursMinutes,
  componentCriteria, componentRequiresEvidence, completedComponentIds, isComponentComplete, isMonthlyAssignment,
  type JourneyComponent,
} from '@/utils/learnerJourney';
import { fetchEvidence, getEvidenceDownloadUrl, deleteEvidence, type EvidenceRecord } from '@/api/evidence';
import { ReflectionWindow } from '@/components/feature/ReflectionWindow';
import { formatClock, formatRecordedClock } from "@/components/feature/ReflectionWindow.helpers";
import { ReflectionChoicePopup } from '@/components/feature/ReflectionChoicePopup';
import { VideoPlayer, parseVideoUrl } from '@/components/feature/VideoPlayer';
import { rememberLearner } from '@/hooks/useMyLearner';
import { useLearnerWorkspaceAccess } from '@/hooks/useLearnerWorkspaceAccess';
import { formatSystemTimestamp, systemTimeZoneName } from '@/lib/format';
import {
  activityTimerStorageKey,
  canResumeActivityTimer,
  clearActivityTimer,
  readActivityTimer,
  saveActivityTimerElapsed,
  saveActivityTimerSession,
} from '@/lib/activityTimer';
import { ReadOnlyLearnerNotice } from '@/components/feature/ReadOnlyLearnerNotice';
import { ComponentAccessNotice } from '@/components/feature/ComponentAccessNotice';
import { useComponentAccessWindow } from '@/hooks/useComponentAccessWindow';
import { CompletionTimeDialog } from '@/components/feature/CompletionTimeDialog';
import { CompletionValidationError, type WorkingRuleReason } from '@/lib/completionValidation';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { ActivitySidebar } from './ActivitySidebar';
import { AudioYouTubeAlternative } from './AudioYouTubeAlternative';
import { isNavigableComponent, placeActivity, weekDisplayLabel, type ActivityPlacement } from './weekPreview';
import { componentRoute } from './componentRoute';
import { ActivityElapsedTimer } from './ActivityElapsedTimer';
import manualTimeStyles from './ActivityManualTimeInput.module.css';
import layoutStyles from './ComponentActivityLayout.module.css';
import { AssignmentSubmissionWizard, type AssignmentCompletion } from './AssignmentSubmissionWizard';
import { AssignmentBriefPanel } from './AssignmentBriefSections';
import { useSavedAssignmentAccess } from './useSavedAssignmentAccess';
import { absoluteDocUrl, resolveDocEmbed } from '@/lib/docEmbed';
import { normalizeAuthoredHtml, sanitizeCurriculumHtml } from '@/lib/curriculumHtml';
import { AuthoredContent } from '@/components/feature/AuthoredContent';
import { downloadReadingPdf, readingFiles } from './readingDownloads';
import { SlideDeckViewer } from '@/components/feature/SlideDeckViewer';
import { ReadingSurface } from './ReadingSurface';
import { ReadingAttachmentText } from './ReadingAttachmentText';
import { PdfReadingText } from './PdfReadingText';
import { clearReadingHighlights, highlightReadingSelection, ReadingToolsContext, DEFAULT_READING_PREFERENCES, readingStorageKey, readStoredJson, type ReadingAnnotations, type ReadingPreferences, type ReadingColourMode, type ReadingFont } from './readingAccessibility';
import {
  loadTeamsMeetingArtifacts,
  teamsMeetingArtifactContentUrl,
  type TeamsMeetingArtifactsResult,
} from '@/pages/curriculum/module-builder/moduleAuthoringData';

const learnerNav = roleNavMap.learner;

type Phase = 'consume' | 'time-entry' | 'reflection-choice' | 'reflect' | 'confirm';
type TimeSource = 'timer' | 'input';

interface DoneRecord { activityKey: string; componentId: string; timeTaken: string | null }

interface FoundContext extends ActivityPlacement {
  component: JourneyComponent;
}

interface TimedCompletion {
  submittedAt?: string | null;
  timeTaken?: string | null;
  passed?: boolean | null;
}

/** The latest successful completion time for one sidebar activity. */
function completionTimeFor(component: JourneyComponent, detail: LearnerDetail | null): string | null {
  let records: TimedCompletion[] = [];
  if (component.isQuiz) {
    records = (component.quizAttempts || []).filter((attempt) => attempt.passed);
  } else if (component.componentId && componentContentKind(component.type) === 'video') {
    records = (detail?.videoProgress || []).filter(
      (entry) => entry.componentId === component.componentId && entry.passed !== false,
    );
  } else if (component.componentId) {
    records = (detail?.componentProgress || []).filter(
      (entry) => entry.componentId === component.componentId && entry.passed !== false,
    );
  }

  const latest = records.reduce<TimedCompletion | null>((current, record) => {
    if (!current) return record;
    return String(record.submittedAt || '') >= String(current.submittedAt || '') ? record : current;
  }, null);
  return formatRecordedClock(latest?.timeTaken);
}

function ActivityTimeSpentInput({ onChange, initialSeconds = null }: { onChange: (seconds: number | null) => void; initialSeconds?: number | null }) {
  const partsFromSeconds = (seconds: number | null) => {
    if (seconds == null || seconds <= 0) return { hours: '', minutes: '' };
    const [hours, minutes] = formatClock(seconds).split(':');
    return { hours, minutes };
  };
  const [parts, setParts] = useState(() => partsFromSeconds(initialSeconds));

  useEffect(() => {
    setParts(current => {
      const currentSeconds = current.hours || current.minutes
        ? (Number(current.hours) || 0) * 3600 + (Number(current.minutes) || 0) * 60
        : null;
      // Keep in-progress typing, but reflect time edited in the other control.
      return currentSeconds === initialSeconds ? current : partsFromSeconds(initialSeconds);
    });
  }, [initialSeconds]);

  const totalSeconds = (next: typeof parts): number | null => {
    if (!next.hours && !next.minutes) return null;
    return (Number(next.hours) || 0) * 3600 + (Number(next.minutes) || 0) * 60;
  };

  const updatePart = (part: keyof typeof parts, rawValue: string) => {
    const value = rawValue.replace(/\D/g, '').slice(0, part === 'hours' ? 3 : 2);
    const next = { ...parts, [part]: value };
    setParts(next);
    onChange(totalSeconds(next));
  };

  const normalise = () => {
    const total = totalSeconds(parts);
    if (total == null) return;
    const [hours, minutes] = formatClock(total).split(':');
    setParts({ hours, minutes });
    onChange(total);
  };

  const fields: { key: keyof typeof parts; label: string; ariaLabel: string }[] = [
    { key: 'hours', label: 'Hours', ariaLabel: 'Hours spent' },
    { key: 'minutes', label: 'Minutes', ariaLabel: 'Minutes spent' },
  ];

  return (
    <div
      className={manualTimeStyles.card}
      title="Enter time spent in hours and minutes"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) normalise();
      }}
    >
      <div className={manualTimeStyles.heading}>
        <span className={manualTimeStyles.icon}><AppIcon className="ri-edit-2-line" /></span>
        <span>Manual time entry</span>
        <span className={manualTimeStyles.badge}>Editable</span>
      </div>
      <div className={manualTimeStyles.fields}>
        {fields.map((field) => (
          <label key={field.key} className={manualTimeStyles.field}>
            <input
              type="text"
              inputMode="numeric"
              value={parts[field.key]}
              onChange={(event) => updatePart(field.key, event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur();
                if (event.key === 'Escape') {
                  event.preventDefault();
                  const empty = { hours: '', minutes: '' };
                  setParts(empty);
                  onChange(null);
                }
              }}
              placeholder="00"
              aria-label={field.ariaLabel}
              className={manualTimeStyles.input}
            />
            <span className={manualTimeStyles.label}>{field.label}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

/** Find the target component + its week/module context inside the built journey. */
function locate(detail: LearnerDetail | null, componentId: string, completedIds: Set<string>): FoundContext | null {
  const placement = placeActivity(detail, { componentId }, completedIds);
  const component = placement?.weekComponents.find((c) => c.componentId === componentId);
  return placement && component ? { ...placement, component } : null;
}

function nextActivityRoute(detail: LearnerDetail | null, currentComponentId: string | undefined, kind: string | undefined, id: string | undefined): { href: string; label: string } | null {
  if (!detail || !currentComponentId || !kind || !id) return null;
  const journey = buildLearnerJourney(detail);
  const ordered = journey.flatMap((module) => (
    module.weeks.flatMap((week, index) => (
      week.components.map((component) => ({ module: module.module, week: week.week, weekLabel: weekDisplayLabel(module.weeks, index), component }))
    ))
  ));
  const currentIndex = ordered.findIndex((item) => item.component.componentId === currentComponentId);
  if (currentIndex < 0) return null;
  const next = ordered
    .slice(currentIndex + 1)
    .find((item) => hasComponentContent(item.component) && isNavigableComponent(item.component));
  if (!next) return null;
  const meta = componentTypeMeta(next.component.title);
  return {
    href: componentRoute(kind, id, next.component, next.module, next.week),
    label: `${meta.detail || meta.label} · ${next.weekLabel} · ${next.module}`,
  };
}

export default function ComponentViewPage() {
  const { kind, id, componentId } = useParams<{ kind: string; id: string; componentId: string }>();
  const timerStorageKey = activityTimerStorageKey(kind, id, componentId);
  const [readyContent, setReadyContent] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  useEffect(() => { rememberLearner(kind, id); }, [kind, id]);
  // Reachable by URL even now the plan rows are inert for a staff viewer.
  // Completing the component here would be recorded as the learner's own work.
  const { canProgress } = useLearnerWorkspaceAccess(id);
  // Scoped to this component's cohort, so the dialog only ever refuses this
  // learner's own college closures.
  const componentAccess = useComponentAccessWindow(componentId);
  const canUseComponent = canProgress && componentAccess.open;

  const [detail, setDetail] = useState<LearnerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadRevision, setLoadRevision] = useState(0);

  const [phase, setPhase] = useState<Phase>('consume');
  // Real playback state (video only), driven by the player.
  const [realDuration, setRealDuration] = useState<number | null>(null);
  const [playerPlaying, setPlayerPlaying] = useState(false);
  const [unsupported, setUnsupported] = useState(false); // no player progress events → wall-clock
  const [wallElapsed, setWallElapsed] = useState(
    () => readActivityTimer(timerStorageKey)?.elapsedSeconds ?? 0,
  );
  const elapsedRef = useRef(wallElapsed);
  elapsedRef.current = wallElapsed;
  const [manualTimeSeconds, setManualTimeSeconds] = useState<number | null>(null);
  const { confirmTime, timeConfirmation } = useManualTimeConfirmation();
  const confirmedManualTime = useRef<number | null>(null);
  useEffect(() => { confirmedManualTime.current = null; }, [manualTimeSeconds, componentId, kind, id]);
  const [recordingWatchSeconds, setRecordingWatchSeconds] = useState(0);
  const [timeSource, setTimeSource] = useState<TimeSource>('timer');
  // Opened only by a server refusal of the Finish click; null while learning.
  const [correction, setCorrection] = useState<{ reason: WorkingRuleReason | ''; holidayName: string } | null>(null);
  const [correctionError, setCorrectionError] = useState('');
  // The reflection the learner already gave, replayed with their declared
  // instant so Final Submit does not ask for it again.
  const lastReflectionRef = useRef<{ ksbs: string[]; feedback: string; reportedTime: string; assignmentTopicId?: string; timeTakenSeconds?: number; timeEntrySource?: 'timer' | 'input' }>({
    ksbs: [], feedback: '', reportedTime: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [expiredTiming, setExpiredTiming] = useState(false);
  const [recoveringTiming, setRecoveringTiming] = useState(false);
  const [timingNotice, setTimingNotice] = useState('');
  const recoveryInFlight = useRef(false);
  useEffect(() => { setExpiredTiming(false); setTimingNotice(''); }, [timerStorageKey]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [record, setRecord] = useState<DoneRecord | null>(null);
  const [repeating, setRepeating] = useState(false);
  const [keepPreviousTime, setKeepPreviousTime] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // Bumped by the uploader so the criteria panel re-checks after an upload.
  const [evidenceVersion, setEvidenceVersion] = useState(0);
  const [evidenceFiles, setEvidenceFiles] = useState<EvidenceRecord[]>([]);
  const [pendingEvidenceFileName, setPendingEvidenceFileName] = useState<string | null>(null);
  const [confirmingEvidenceRemoval, setConfirmingEvidenceRemoval] = useState(false);
  const [removingEvidence, setRemovingEvidence] = useState(false);
  const [evidencePreview, setEvidencePreview] = useState<EvidencePreview | null>(null);
  const evidenceInputId = useId();
  const timerRef = useRef<ReturnType<typeof startActivityClock> | null>(null);
  const timerRemainder = useRef({ milliseconds: 0 });
  const completionInFlightRef = useRef(false);
  const [trackingGeneration, setTrackingGeneration] = useState(0);
  const trackingSessionRef = useRef<TimeTrackingSession | null>(null);
  const trackingPromiseRef = useRef<Promise<TimeTrackingSession> | null>(null);
  const [timingSyncError, setTimingSyncError] = useState<string | null>(null);
  const currentTimingKeyRef = useRef(timerStorageKey);
  currentTimingKeyRef.current = timerStorageKey;

  // React Router can reuse this page while only the component id changes.
  // Restore the independently saved counter for the newly selected activity.
  useEffect(() => {
    setPhase('consume');
    setRecord(null);
    setRepeating(false);
    setRefreshError(null);
    setSubmitError(null);
    setPlayerPlaying(false);
    setUnsupported(false);
    setRealDuration(null);
    setWallElapsed(readActivityTimer(timerStorageKey)?.elapsedSeconds ?? 0);
    timerRemainder.current.milliseconds = 0;
    setManualTimeSeconds(null);
    setRecordingWatchSeconds(0);
    setTimeSource('timer');
    setCorrection(null);
    setCorrectionError('');
    setPendingEvidenceFileName(null);
    setEvidenceFiles([]);
    setEvidencePreview(null);
    // A pending "Remove this file?" must not survive onto the next activity.
    setConfirmingEvidenceRemoval(false);
  }, [timerStorageKey]);

  useEffect(() => {
    if ((kind !== 'commercial' && kind !== 'apprenticeship') || !id) {
      setLoadError('Unknown learner.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchLearnerDetail(kind as LearnerKind, id, { componentId })
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Could not load component'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [kind, id, componentId, loadRevision]);

  // Keep completion state derived from the same progress records used by the
  // learner journey. This is also needed by the sidebar's done counts.
  const completedIds = useMemo(() => {
    const ids = completedComponentIds(detail);
    // A successful save stays visible even if the subsequent refresh fails.
    if (record?.activityKey === timerStorageKey) ids.add(record.componentId);
    return ids;
  }, [detail, record, timerStorageKey]);
  const ctx = useMemo(
    () => (componentId ? locate(detail, componentId, completedIds) : null),
    [detail, componentId, completedIds],
  );
  const component = ctx?.component ?? null;
  const recordedEvent = Boolean(component?.recordingSeriesId && component.recordingSessionNumber && component.recordingDate);
  const contentReadyKey = `${timerStorageKey}:${component?.resourceUrl || ''}`;
  const onContentReady = useCallback(() => setReadyContent(contentReadyKey), [contentReadyKey]);
  useEffect(() => () => setReadyContent(null), [contentReadyKey]);
  // Where this activity stands with the coach. Only meaningful for an activity
  // the author sent for validation: everything else is finished when the
  // learner completes it. Without this the page said "Ready to complete" on
  // work that had already been submitted and rejected.
  const marking = componentId ? detail?.componentMarkingStatus?.[componentId] : undefined;
  // Driven by whether the work was actually submitted, not by the component's
  // validation flag. A coach can review anything a learner hands in, and most
  // reflection components carry no flag — gating on it hid a rejection with
  // 692 characters of feedback behind a "Ready to complete" banner.
  const verdict = markingVerdict(marking?.status);
  const meta = component ? componentTypeMeta(component.title) : null;
  const learnerKsbs: LearnerKsbItem[] = detail?.ksbs ?? [];

  const contentKind = componentContentKind(component?.type);
  const waitsForPreview = contentKind === 'reading' || (contentKind === 'slides' && Boolean(component?.resourceUrl));
  const isVideo = contentKind === 'video';
  const isAudio = contentKind === 'audio';
  // Live sessions and assignments use learner-entered time instead of exposing
  // a page timer. The signed session still runs invisibly so the server can cap
  // and verify the submitted duration.
  const isLiveSession = (component?.type || '').trim().toLowerCase().replace(/-/g, '_') === 'live_session';
  const hasActivityPanel = isVideo || isLiveSession;
  // The monthly assignment uses the eight-step submission. Once an author flags
  // one, the plan's other assignments are completed like any other activity.
  const isAssignment = isMonthlyAssignment(component, detail?.components);
  const isPlainAssignment = !isAssignment && componentRequiresEvidence(component?.type);
  const completed = !!component && isComponentComplete(component, completedIds);
  const recordingAttempt = isAssignment || !completed || repeating;
  const nextActivity = useMemo(() => nextActivityRoute(detail, componentId, kind, id), [detail, componentId, kind, id]);
  const completedTime = record?.activityKey === timerStorageKey
    ? formatRecordedClock(record.timeTaken)
    : component ? completionTimeFor(component, detail) : null;
  const noun = componentNoun(component?.type);
  const contentOpenable = component ? isOpenableComponent(component) || (isAssignment && !!component.componentId) : false;
  const savedAssignment = useSavedAssignmentAccess(kind, id, componentId, isAssignment && !contentOpenable && canUseComponent);
  const openable = contentOpenable || savedAssignment.status === 'available';

  // Approved evidence uploaded for this component. Owned here (not inside the
  // uploader) because the completion gate depends on it.
  const [evidenceCount, setEvidenceCount] = useState(0);
  const needsEvidence = componentRequiresEvidence(component?.type);
  const usesManualTimeOnly = isLiveSession || needsEvidence;
  const manualTimeMissing = usesManualTimeOnly && (manualTimeSeconds == null || manualTimeSeconds <= 0);

  useEffect(() => {
    if (usesManualTimeOnly) setTimeSource('input');
  }, [usesManualTimeOnly]);

  useEffect(() => {
    // Only assignments collect evidence — nothing to look up elsewhere.
    if (!needsEvidence || !kind || !id || !componentId) return;
    let cancelled = false;
    fetchEvidence(kind as LearnerKind, id, { sectionRef: componentId })
      .then((rows) => {
        if (!cancelled) {
          setEvidenceFiles(rows);
          setEvidenceCount(rows.filter((r) => r.status === 'approved').length);
        }
      })
      .catch(() => { /* the criteria panel just shows 0; the server re-checks on submit */ });
    return () => { cancelled = true; };
  }, [needsEvidence, kind, id, componentId, evidenceVersion]);

  const criteria = component ? componentCriteria(component, evidenceCount) : null;
  const finishDisabled = (!!criteria && !criteria.met) || (!isLiveSession && manualTimeMissing);

  const moduleTitle = ctx?.moduleTitle ?? searchParams.get('module') ?? '';
  const weekTitle = ctx?.weekTitle ?? searchParams.get('week') ?? '';
  const weekLabel = ctx?.weekLabel ?? weekTitle;
  const backHref = kind && id ? `/workspace/learner/${kind}/${id}/dashboard` : '/workspace/learner/dashboard';


  // A quiz component has nowhere to show its questions — the quiz page owns
  // that. Reaching this page for one (a direct link, a bookmark, the sidebar
  // before its quiz was linked) would otherwise show the generic "complete this
  // activity" card instead of the quiz.
  const linkedQuizId = component?.isQuiz ? component.quizMeta?.quizId ?? null : null;
  useEffect(() => {
    if (linkedQuizId == null || !kind || !id) return;
    navigate(
      `/learner/quiz/${kind}/${id}/${linkedQuizId}`
      + `?module=${encodeURIComponent(moduleTitle)}&week=${encodeURIComponent(weekTitle)}`,
      { replace: true },
    );
  }, [linkedQuizId, kind, id, moduleTitle, weekTitle, navigate]);

  const parsed = useMemo(() => (isVideo && component?.videoUrl ? parseVideoUrl(component.videoUrl) : null), [isVideo, component?.videoUrl]);
  const pageTitle = decodeInlineText(meta?.detail || meta?.label || 'Activity');
  const activityEvidenceContext: EvidenceContext | null = component
    && componentId
    && id
    && (kind === 'commercial' || kind === 'apprenticeship')
    ? {
        kind,
        learnerId: id,
        componentId,
        onUploaded: (files) => {
          setEvidenceFiles(files);
          setEvidenceVersion((version) => version + 1);
          // Also fires when a file is removed, and the optimistic label set at
          // file-choice time would otherwise keep naming a file that is gone.
          if (files.length === 0) setPendingEvidenceFileName(null);
        },
        trainingPlanDetails: {
          moduleId: component.moduleId ?? null,
          moduleTitle: moduleTitle || null,
          weekId: component.weekId ?? null,
          weekTitle: weekTitle || null,
          componentId,
          componentTitle: pageTitle,
          componentType: component.type || null,
        },
      }
    : null;
  const visibleEvidenceFile = evidenceFiles[0] ?? null;
  const evidenceFileLabel = pendingEvidenceFileName || visibleEvidenceFile?.filename || null;
  const evidenceFileStatus = visibleEvidenceFile?.status || (pendingEvidenceFileName ? 'pending' : null);
  // Deleting the stored file and letting the control fall back to its upload
  // state *is* the reupload path — there is no separate replace call.
  const removeEvidenceFile = async (fileId: string) => {
    setSubmitError(null);
    setRemovingEvidence(true);
    try {
      await deleteEvidence(kind as LearnerKind, id || '', fileId);
      setPendingEvidenceFileName(null);
      setConfirmingEvidenceRemoval(false);
      // Bumping the version re-runs the fetch effect above, which is what keeps
      // `evidenceCount` — and so the criteria gate and the Finish button — in
      // step. Setting the file list here directly would leave that count stale.
      setEvidenceVersion((version) => version + 1);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Could not remove the evidence file.');
    } finally {
      setRemovingEvidence(false);
    }
  };

  const openEvidenceFile = async (file: EvidenceRecord) => {
    try {
      const url = await getEvidenceDownloadUrl(kind as LearnerKind, id || '', file.id);
      setEvidencePreview({ file, url });
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Could not open the evidence file.');
    }
  };

  // Non-video content has no player progress → run the wall-clock.
  useEffect(() => { if (component && !isVideo) setUnsupported(true); }, [component, isVideo]);

  // Seeking changes the playhead/duration metadata, but cannot add watched time.
  const elapsedSeconds = wallElapsed;
  const submittedTimeSeconds = usesManualTimeOnly
    ? manualTimeSeconds ?? 0
    : timeSource === 'input' && manualTimeSeconds != null
      ? manualTimeSeconds
      : elapsedSeconds;
  // Planned time in the reflection window: always the component's
  // authored expected_otjh (its OTJ hours) when set, so "the planned time"
  // means the same thing for every component type in the training plan.
  // Falls back to the real video length / authored duration only when no
  // expected_otjh was set for this component.
  // The planned time as a number of hours, from whichever source is authoritative
  // for this component. Every branch converts to hours here so the reflection
  // window is never handed a value whose unit depends on where it came from:
  // expected_otjh is already hours, a real video length is seconds, and an
  // authored duration is minutes.
  const plannedHours =
    component?.expectedOtjh != null && component.expectedOtjh > 0
      ? component.expectedOtjh
      : isVideo && realDuration !== null
        ? realDuration / 3600
        : component?.durationMinutes
          ? component.durationMinutes / 60
          : null;

  // Shown to the learner. Rendered from the hours above rather than from each
  // source's own units, so "Planned time" reads as hours-and-minutes ("1h 42m")
  // everywhere instead of switching to an HH:MM:SS clock for videos — which is what
  // made a 1h42m video look like 102 hours.
  const plannedTimeLabel = plannedHours != null ? formatHoursMinutes(plannedHours) : '';

  const trackingMode: TrackingCountingMode = isVideo || isAudio
    ? 'active_playback'
    : 'visible_page';

  // The server signs this learner/activity start for verification evidence,
  // separately from the Timer/Input duration chosen at completion.
  useEffect(() => {
    if (phase !== 'consume' || !recordingAttempt || !openable || !componentId || !kind || !id || !canUseComponent) return;
    const learnerKind = kind as LearnerKind;
    const activityKind = isVideo ? 'video' : 'component';
    let cancelled = false;
    trackingSessionRef.current = null;

    const savedTimer = readActivityTimer(timerStorageKey);
    // Audio already counted playback before its session was labelled correctly.
    // Keep a still-valid older session and its saved seconds when resuming.
    if (canResumeActivityTimer(savedTimer, trackingMode)
        || (isAudio && canResumeActivityTimer(savedTimer, 'visible_page'))) {
      const savedSession = savedTimer!.session!;
      if (savedSession.rewardTracking || savedSession.persistent) {
        const resumed = savedSession.persistent
          ? checkpointTimeTracking(savedSession, activityKind, componentId, learnerKind, id, savedTimer!.elapsedSeconds)
          : resumeTimeTracking(savedSession, activityKind, componentId).catch(error => {
            if (!timingSessionExpired(error)) throw error;
            return checkpointTimeTracking(savedSession, activityKind, componentId, learnerKind, id, savedTimer!.elapsedSeconds);
          });
        trackingPromiseRef.current = resumed;
        resumed.then(session => { if (!cancelled) {
          trackingSessionRef.current = session;
          saveActivityTimerSession(timerStorageKey, session);
          const seconds = Math.max(elapsedRef.current, savedTimer!.elapsedSeconds, session.savedSeconds || 0);
          elapsedRef.current = seconds; setWallElapsed(seconds); saveActivityTimerElapsed(timerStorageKey, seconds);
          setExpiredTiming(false); setTimingSyncError(null);
        } })
          .catch(error => { if (!cancelled) { setExpiredTiming(true); setSubmitError(error instanceof Error ? error.message : 'Study timing could not synchronize. Your draft is kept.'); } });
      } else {
        trackingSessionRef.current = savedSession;
        trackingPromiseRef.current = Promise.resolve(savedSession);
      }
      setWallElapsed(savedTimer!.elapsedSeconds);
      return () => { cancelled = true; };
    }

    // Ask the server to validate and recover older drafts before using them.
    if (savedTimer?.session) {
      // Recover expired legacy drafts under their original signature and ID.
      const pending = checkpointTimeTracking(savedTimer.session, activityKind, componentId, learnerKind, id, savedTimer.elapsedSeconds);
      trackingPromiseRef.current = pending;
      pending.then(session => { if (!cancelled) {
        trackingSessionRef.current = session; saveActivityTimerSession(timerStorageKey, session);
        const seconds = Math.max(elapsedRef.current, savedTimer.elapsedSeconds, session.savedSeconds || 0);
        elapsedRef.current = seconds; setWallElapsed(seconds); saveActivityTimerElapsed(timerStorageKey, seconds);
        setExpiredTiming(false); setSubmitError(null);
      } }).catch(error => { if (!cancelled) { setExpiredTiming(true); setSubmitError(error instanceof Error ? error.message : 'Your time could not sync. Your draft is kept.'); } });
      return () => { cancelled = true; };
    }

    const pending = startTimeTracking(activityKind, componentId, learnerKind, id, trackingMode);
    trackingPromiseRef.current = pending;
    pending
      .then((session) => {
        if (!cancelled) {
          trackingSessionRef.current = session;
          saveActivityTimerSession(timerStorageKey, session);
        }
      })
      .catch((error) => {
        if (!cancelled) setSubmitError(error instanceof Error ? error.message : 'Could not start activity timing');
      });
    return () => { cancelled = true; };
  }, [phase, recordingAttempt, openable, componentId, kind, id, canUseComponent, isVideo, isAudio, trackingMode, timerStorageKey, trackingGeneration]);

  const restartTiming = async () => {
    if (recoveryInFlight.current || !componentId || !kind || !id || !canUseComponent) return;
    recoveryInFlight.current = true; setRecoveringTiming(true);
    try {
      const saved = readActivityTimer(timerStorageKey);
      archiveTimingDraft(timerStorageKey, trackingSessionRef.current || saved?.session || null,
        Math.max(elapsedRef.current, saved?.elapsedSeconds || 0), { reflection: lastReflectionRef.current, manualTimeSeconds, timeSource });
      const previous = trackingSessionRef.current || saved?.session;
      if (!previous) throw new Error('Your timing draft is unavailable. Keep this page open and contact support.');
      const session = await checkpointTimeTracking(previous, isVideo ? 'video' : 'component', componentId, kind, id,
        Math.max(elapsedRef.current, saved?.elapsedSeconds || 0));
      if (currentTimingKeyRef.current !== timerStorageKey) return;
      const seconds = Math.max(elapsedRef.current, saved?.elapsedSeconds || 0, session.savedSeconds || 0);
      localStorage.setItem(timerStorageKey, JSON.stringify({ session, elapsedSeconds: seconds }));
      trackingSessionRef.current = session; trackingPromiseRef.current = Promise.resolve(session);
      elapsedRef.current = seconds; setWallElapsed(seconds);
      setExpiredTiming(false); setSubmitError(null);
      setTimingSyncError(null);
      setTimingNotice('Your time has been restored. Your reflection and entered time are kept. You can finish the activity.');
    } catch (error) { setSubmitError(error instanceof Error ? error.message : 'Could not restore your timing session. Your draft is kept.'); }
    finally { recoveryInFlight.current = false; setRecoveringTiming(false); }
  };

  useEffect(() => {
    if (!recordingAttempt || !componentId || !kind || !id || !canUseComponent) return;
    let cancelled = false;
    let busy = false;
    const sync = async () => {
      const session = trackingSessionRef.current || readActivityTimer(timerStorageKey)?.session;
      if (!session?.persistent || busy || completionInFlightRef.current) return;
      busy = true;
      try {
        const restored = await checkpointTimeTracking(session, isVideo ? 'video' : 'component', componentId, kind, id, elapsedRef.current);
        if (cancelled || (trackingSessionRef.current && trackingSessionRef.current.sessionId !== session.sessionId)) return;
        trackingSessionRef.current = restored;
        trackingPromiseRef.current = Promise.resolve(restored);
        saveActivityTimerSession(timerStorageKey, restored);
        setExpiredTiming(false);
        if (restored.rewardTracking) await flushRewardObservations(restored, isVideo ? 'video' : 'component', componentId);
        if (!cancelled) setTimingSyncError(null);
      } catch (error) {
        if (!cancelled) setTimingSyncError(`Your time could not sync. Your draft is kept on this device. ${error instanceof Error ? error.message : 'Retry when connected.'}`);
      }
      finally { busy = false; }
    };
    const interval = setInterval(() => void sync(), 60000);
    window.addEventListener('online', sync);
    return () => { cancelled = true; clearInterval(interval); window.removeEventListener('online', sync); };
  }, [recordingAttempt, componentId, kind, id, canUseComponent, isVideo, timerStorageKey]);

  // Ordinary page content counts only while visible. Media counts while it is
  // genuinely playing, even when the learner switches tab or opens another
  // application. Hidden tabs throttle intervals, so active audio/video uses
  // wall-clock deltas rather than assuming each callback is exactly one second.
  useEffect(() => {
    const activeMedia = isVideo || isAudio;
    if (phase !== 'consume' || !recordingAttempt || !canUseComponent || expiredTiming) return;
    // Downloading/parsing a document is not study time; wait for its first page.
    if (waitsForPreview && (loading || loadError || !openable || readyContent !== contentReadyKey)) return;
    if (activeMedia ? !playerPlaying : (!unsupported && !playerPlaying)) return;
    const clock = startActivityClock({
      onInterval: (start,end) => {
        try { observeRewardInterval(trackingSessionRef.current,start,end); }
        catch { setSubmitError('Your measured-time draft could not be saved. Keep this page open and retry saving.'); }
      },
      countInBackground: activeMedia,
      pauseOnUnload: contentKind === 'reading',
      remainder: timerRemainder.current,
      onElapsed: (increment) => {
        const next = elapsedRef.current + increment;
        elapsedRef.current = next;
        // Persist synchronously: React may ignore a state update during unmount.
        saveActivityTimerElapsed(timerStorageKey, next);
        setWallElapsed(next);
      },
    });
    timerRef.current = clock;
    return () => clock.stop();
  }, [phase, recordingAttempt, canUseComponent, unsupported, playerPlaying, isVideo, isAudio, timerStorageKey, contentKind, loading, loadError, openable, waitsForPreview, readyContent, contentReadyKey, expiredTiming]);

  const finishConsuming = () => {
    if (!recordingAttempt) return;
    timerRef.current?.stop();
    setKeepPreviousTime(false);
    if (isLiveSession && manualTimeMissing) {
      setPhase('time-entry');
      return;
    }
    if (component?.reflectionRequired === false) {
      setPhase('confirm');
      return;
    }
    setPhase('reflection-choice');
  };

  const confirmCompletion = () => {
    if (!component) return;
    if (keepPreviousTime && completedTime) {
      clearActivityTimer(timerStorageKey);
      setWallElapsed(0);
      timerRemainder.current.milliseconds = 0;
      setManualTimeSeconds(null);
      setTimeSource(usesManualTimeOnly ? 'input' : 'timer');
      setRepeating(false);
      setPhase('consume');
      return;
    }
    if (manualTimeMissing) return;
    if (timeSource === 'input') confirmedManualTime.current = submittedTimeSeconds;
    if (phase === 'time-entry' && component.reflectionRequired !== false) {
      setPhase('reflection-choice');
      return;
    }
    void finalizeSubmit({
      ksbs: (component.ksbMappings || []).map(mapping => mapping.code),
      feedback: '',
      reportedTime: `${submittedTimeSeconds / 60} minutes`,
    }, { manualTimeConfirmed: true });
  };

  const finalizeSubmit = async (
    reflection: { ksbs: string[]; feedback: string; reportedTime: string; assignmentTopicId?: string; timeTakenSeconds?: number; timeEntrySource?: 'timer' | 'input' },
    options: { rethrow?: boolean; skipReflection?: boolean; declaredCompletedAt?: string; manualTimeConfirmed?: boolean } = {},
  ) => {
    if (!component || !componentId || !kind || !id || completionInFlightRef.current || !canUseComponent) return;
    // Working-time rules stay on the server. The learner can press Finish anytime;
    // the server judges the instant and refuses with a 409 that has written
    // nothing, which opens the correction dialog below.
    const declared = options.declaredCompletedAt ?? null;
    const finalTimeSeconds = reflection.timeTakenSeconds ?? submittedTimeSeconds;
    const finalTimeSource = reflection.timeEntrySource ?? timeSource;
    lastReflectionRef.current = reflection;
    completionInFlightRef.current = true;
    setSubmitting(true);
    setSubmitError(null);
    setRefreshError(null);
    try {
      if (finalTimeSource === 'input' && !options.manualTimeConfirmed && confirmedManualTime.current !== finalTimeSeconds
        && !await confirmTime(finalTimeSeconds)) {
        setPhase('consume');
        requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[aria-label="Hours spent"]')?.focus());
        return;
      }
      if (finalTimeSource === 'input') confirmedManualTime.current = finalTimeSeconds;
      let tracking = trackingSessionRef.current || await trackingPromiseRef.current;
      if (!tracking) throw new Error('Activity timing did not start. Reopen the activity and try again.');
      if (tracking.persistent || !canResumeActivityTimer({ session: tracking, elapsedSeconds: elapsedRef.current }, tracking.countingMode)) {
        tracking = await checkpointTimeTracking(tracking, isVideo ? 'video' : 'component', componentId, kind, id, elapsedRef.current)
          .catch(error => { setExpiredTiming(true); throw error; });
        trackingSessionRef.current = tracking;
        saveActivityTimerSession(timerStorageKey, tracking);
      }
      if (tracking.rewardTracking && finalTimeSource === 'timer') await flushRewardObservations(tracking,isVideo?'video':'component',componentId, Boolean(tracking.persistent));
      if (isVideo) {
        const res = await submitVideoProgress(componentId, kind as 'commercial' | 'apprenticeship', id, {
          week: weekTitle || null, module: moduleTitle || null,
          startedAt: tracking.startedAt, timeTakenSeconds: finalTimeSeconds, trackingToken: tracking.trackingToken,
          timeEntrySource: finalTimeSource,
          declaredCompletedAt: declared,
          videoTitle: meta?.detail || meta?.label || 'Video',
          ksbs: reflection.ksbs, feedback: reflection.feedback, reportedTime: reflection.reportedTime,
          skipReflection: options.skipReflection === true,
        });
        setRecord({ activityKey: timerStorageKey, componentId, timeTaken: res.record.timeTaken });
      } else {
        const res = await submitComponentProgress(componentId, kind as 'commercial' | 'apprenticeship', id, {
          week: weekTitle || null, module: moduleTitle || null,
          startedAt: tracking.startedAt, timeTakenSeconds: finalTimeSeconds, trackingToken: tracking.trackingToken,
          timeEntrySource: finalTimeSource,
          declaredCompletedAt: declared,
          componentTitle: pageTitle, componentType: component.type || undefined,
          plannedOtjh: plannedTimeLabel,
          ...(reflection.assignmentTopicId ? { assignmentTopicId: reflection.assignmentTopicId } : {}),
          ksbs: reflection.ksbs, feedback: reflection.feedback, reportedTime: reflection.reportedTime,
          skipReflection: options.skipReflection === true,
        });
        setRecord({ activityKey: timerStorageKey, componentId, timeTaken: res.record.timeTaken });
      }
      clearRewardObservations(tracking);
      clearActivityTimer(timerStorageKey);
      setWallElapsed(0);
      timerRemainder.current.milliseconds = 0;
      setManualTimeSeconds(null);
      if (reflection.assignmentTopicId) setTrackingGeneration(value => value + 1);
      setTimeSource(usesManualTimeOnly ? 'input' : 'timer');
      setRepeating(false);
      // A refresh failure after a committed completion must not invite the
      // learner to submit the same timing session again.
      const refreshed = await fetchLearnerDetail(kind as LearnerKind, id, { componentId }).catch(() => {
        setRefreshError('Your completion was saved. Refresh the page to reload the activity list.');
        return detail;
      });
      setDetail(refreshed);
      setPhase('consume');
      // The completion is committed, so any correction dialog has served its
      // purpose and must not linger over the finished activity.
      setCorrection(null);
      setCorrectionError('');
    } catch (e) {
      if (timingSessionExpired(e)) setExpiredTiming(true);
      if (e instanceof CompletionValidationError) {
        // Nothing was written. Ask for a working instant instead of reporting
        // a failure; a refusal of an instant the learner already chose is shown
        // inside the dialog so it stays open.
        if (declared) {
          setCorrectionError(e.message);
        } else {
          setCorrection({ reason: e.reason, holidayName: e.holidayName });
          setCorrectionError('');
        }
        setSubmitError(null);
        // The assignment wizard awaits this call: it must not treat a refusal
        // as a submission, so it still sees the throw while the dialog opens.
        if (options.rethrow) throw e;
        return;
      }
      setSubmitError(e instanceof Error ? e.message : 'Could not save progress');
      if (options.rethrow) throw e;
    } finally {
      completionInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const submitDeclared = (declaredAt: string) => {
    void finalizeSubmit(lastReflectionRef.current, { declaredCompletedAt: declaredAt, manualTimeConfirmed: true });
  };

  const activityHeading = component && (
    <div className={isVideo ? layoutStyles.videoHeading : 'min-w-0'}>
      <span className={isVideo
        ? layoutStyles.videoEyebrow
        : `text-[10px] font-semibold uppercase tracking-wider inline-flex items-center gap-1 ${meta?.color || 'text-foreground-500'}`}>
        <AppIcon className={meta?.icon || 'ri-checkbox-circle-line'} /> {meta?.label || 'Activity'}
      </span>
      <h1 className={isVideo ? layoutStyles.videoTitle : 'mt-1 text-xl md:text-2xl font-heading font-bold text-foreground-900 leading-tight'}>{pageTitle}</h1>
      <div className={isVideo ? layoutStyles.videoMeta : 'mt-2 flex flex-wrap items-center gap-3 text-[13px] text-foreground-500'}>
        {isVideo && realDuration !== null ? (
          <span className="inline-flex items-center gap-1"><AppIcon className="ri-time-line" />{formatClock(realDuration)}</span>
        ) : component.durationMinutes != null && (
          <span className="inline-flex items-center gap-1"><AppIcon className="ri-time-line" />{component.durationMinutes} min</span>
        )}
        {component.expectedOtjh != null && component.expectedOtjh > 0 && (
          <span className="inline-flex items-center gap-1"><AppIcon className="ri-timer-line" />{component.expectedOtjh}h OTJ</span>
        )}
        {weekLabel && <span className="inline-flex items-center gap-1"><AppIcon className="ri-calendar-line" />{weekLabel}</span>}
      </div>
    </div>
  );

  return (
    <WorkspaceShell
      role="learner" roleLabel={learnerNav.label} navItems={learnerNav.items} workspaceLabel={learnerNav.workspaceLabel}
      pageTitle={pageTitle}
      pageSubtitle={[moduleTitle, weekLabel].filter(Boolean).join(' · ')}
      userName="Learner" userRole="Learner"
      hideBreadcrumbs
    >
      <div className="mx-auto page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        <button
          onClick={() => navigate(backHref)}
          className="mb-5 inline-flex items-center gap-2 rounded-xl border border-background-300 bg-white px-4 py-2.5 text-[13px] font-semibold text-foreground-700 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary-200 hover:text-primary-700 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 cursor-pointer"
        >
          <span className="grid h-6 w-6 place-items-center rounded-lg bg-primary-50 text-primary-700">
            <AppIcon className="ri-arrow-left-line text-sm" />
          </span>
          Back to training plan
        </button>


        {expiredTiming && <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4">
          <p>Your time needs to sync before you finish. Your previous time and answers are kept.</p>
          <button type="button" disabled={recoveringTiming} onClick={restartTiming} className="mt-2 font-semibold underline">{recoveringTiming ? 'Restoring...' : 'Restore saved time'}</button>
        </div>}
        {timingSyncError && <p role="status" className="rounded-xl border border-amber-300 p-3">{timingSyncError}</p>}
        {timingNotice && <p role="status" className="rounded-xl border p-3">{timingNotice}</p>}
        {loading ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-5"><RowsSkeleton rows={4} avatar={false} /></div>
        ) : loadError ? (
          <div role="alert" className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6">
            <EmptyState text={loadError} />
            <button type="button" onClick={() => setLoadRevision(value => value + 1)}
              className="mt-4 rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white">
              Try again
            </button>
          </div>
        ) : !component ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6"><EmptyState text="Component not found in this learner's plan." /></div>
        ) : !canProgress ? (
          <ReadOnlyLearnerNotice what="complete their own training-plan activities" onBack={() => navigate(backHref)} />
        ) : !componentAccess.open ? (
          <ComponentAccessNotice onBack={() => navigate(backHref)} />
        ) : !openable && savedAssignment.status === 'checking' ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-5"><RowsSkeleton rows={4} avatar={false} /></div>
        ) : !openable && savedAssignment.status === 'error' ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6">
            <p role="alert" className="text-sm text-red-700">Could not load your saved assignment.</p>
            <button type="button" onClick={savedAssignment.retry} className="mt-3 text-sm underline">Retry saved assignment</button>
          </div>
        ) : !openable ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6"><EmptyState text="This component can't be completed here yet." /></div>
        ) : isVideo && !parsed && !recordedEvent ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6"><EmptyState text="This video has no playable URL yet." /></div>
        ) : phase === 'reflect' ? (
          <div className="space-y-4">
            <ReflectionWindow
              noun={noun}
              plannedTimeLabel={plannedTimeLabel}
              plannedHours={plannedHours ?? undefined}
              learnerKsbs={learnerKsbs}
              autoKsbs={component.ksbMappings ?? []}
              elapsedSeconds={elapsedSeconds}
              selectedTimeSeconds={submittedTimeSeconds}
              selectedTimeSource={timeSource}
              submitting={submitting}
              submitError={submitError}
              onSubmit={result => finalizeSubmit(result, { manualTimeConfirmed: true })}
              activityTitle={pageTitle}
              weekLabel={weekTitle}
              moduleLabel={moduleTitle}
              learnerName={detail?.name || 'Learner'}
              programmeName={detail?.programme || 'Programme not set'}
              learnerKind={kind as LearnerKind}
              learnerId={id}
              evidenceSectionRef={componentId}
              reflectionQuestion={component.reflectionQuestion}
              onClose={() => setPhase('consume')}
            />
          </div>
        ) : (
          /* ── consume phase: content + details + sidebar ── */
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 items-start">
            <div className="min-w-0">
              <PreviousLearningNotice learning={component.previousLearning} kind={kind} learnerId={id} />
              {isVideo && activityHeading}
              {!isAssignment && !recordedEvent && (
                <ComponentContent key={timerStorageKey} component={{ ...component, liveSessionUrl: parsePersonalLearning(id) ? null : component.teamsLiveSessionId && component.teamsSessionNumber
                    ? `/learner_api/session-results/${kind}/${id}/${encodeURIComponent(component.teamsLiveSessionId)}/sessions/${component.teamsSessionNumber}/join/`
                    : component.liveSessionUrl }} contentKind={contentKind} parsed={parsed} title={pageTitle}
                  onDuration={(d) => setRealDuration((prev) => prev ?? d)}
                  onProgress={() => undefined}
                  onPlayingChange={setPlayerPlaying}
                  onContentReady={onContentReady}
                  onEnded={() => setPlayerPlaying(false)}
                  onUnsupported={() => setUnsupported(true)}
                />
              )}
              {recordedEvent && component.recordingSeriesId && component.recordingSessionNumber && (
                <SessionResults
                  seriesId={component.recordingSeriesId}
                  sessionNumber={component.recordingSessionNumber}
                  recordingDate={component.recordingDate}
                  recordingsOnly
                  learner={{ kind: kind as LearnerKind, id: id || '' }}
                  preview={Boolean(parsePersonalLearning(id))}
                  onRecordingPlayingChange={setPlayerPlaying}
                />
              )}
              {isPlainAssignment && <AssignmentBriefPanel component={component} />}
              {(component.type || '').trim().toLowerCase().replace(/-/g, '_') === 'live_session' && component.teamsLiveSessionId && (
                component.teamsSessionNumber ? <SessionResults
                  seriesId={component.teamsLiveSessionId}
                  sessionNumber={component.teamsSessionNumber}
                  recordingDate={component.recordingDate || component.sessionDate}
                  learner={{ kind: kind as LearnerKind, id: id || '' }}
                  attendanceType={detail?.attendanceType}
                  onRecordingWatchTimeChange={setRecordingWatchSeconds}
                  preview={Boolean(parsePersonalLearning(id))}
                /> : <p className="mt-4 rounded-xl border bg-amber-50 p-4 text-sm">This live session needs its saved session number before results can be shown. Please contact your tutor.</p>
              )}

              {/* Activity controls stay below the player; video details are above it. */}
              <div className={isVideo
                ? `mt-4 ${layoutStyles.videoArea}`
                : isLiveSession
                  ? `mt-4 ${layoutStyles.liveArea}`
                  : 'mt-4 flex items-start justify-between gap-4 flex-wrap'}>
                {!isVideo && activityHeading}

                {!isAssignment && recordingAttempt && <div className={isVideo
                  ? layoutStyles.videoActions
                  : isLiveSession
                    ? `${layoutStyles.liveActions} ${layoutStyles.liveActionsWithTimer}`
                    : 'ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-3'}>
                  {(!usesManualTimeOnly || isLiveSession) && (
                    <ActivityElapsedTimer time={formatClock(isLiveSession ? recordingWatchSeconds : elapsedSeconds)} />
                  )}
                  <div className={hasActivityPanel ? layoutStyles.videoTools : 'contents'}>
                  <ActivityTimeSpentInput
                    key={timerStorageKey}
                    initialSeconds={isLiveSession ? manualTimeSeconds : undefined}
                    onChange={(seconds) => {
                      setManualTimeSeconds(seconds);
                      setTimeSource(seconds == null && !usesManualTimeOnly ? 'timer' : 'input');
                    }}
                  />
                  {activityEvidenceContext && canUseComponent && (
                    <div className={layoutStyles.evidenceTool}>
                      <AssignmentEvidence
                        kind={activityEvidenceContext.kind}
                        learnerId={activityEvidenceContext.learnerId}
                        componentId={activityEvidenceContext.componentId}
                        trainingPlanDetails={activityEvidenceContext.trainingPlanDetails}
                        onUploaded={activityEvidenceContext.onUploaded}
                        onFileSelected={setPendingEvidenceFileName}
                        inputId={evidenceInputId}
                        showPanel={false}
                      />
                      {evidenceFileLabel ? (
                      <span className="inline-flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            if (visibleEvidenceFile?.status === 'approved') void openEvidenceFile(visibleEvidenceFile);
                          }}
                          disabled={visibleEvidenceFile?.status !== 'approved'}
                          className="inline-flex max-w-[220px] items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800 transition-colors enabled:cursor-pointer enabled:hover:border-emerald-300 enabled:hover:bg-emerald-100 disabled:cursor-default"
                          title={evidenceFileLabel}
                        >
                          <AppIcon className={evidenceFileStatus === 'approved' ? 'ri-file-check-line' : 'ri-file-line'} />
                          <span className="truncate">{evidenceFileLabel}</span>
                          {evidenceFileStatus === 'pending' && (
                            <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-700">Scanning</span>
                          )}
                        </button>
                        {/* Wrong file uploaded? Remove it here and the control
                            reverts to "Upload evidence" for the right one.
                            Only for a real stored row — an optimistic label
                            from a still-uploading file has no id to delete.

                            Hidden once the activity has been handed in:
                            `canDelete` is the server's own answer, and offering
                            Remove without it produced a 409 Conflict on a button
                            the page had just invited the learner to press. */}
                        {visibleEvidenceFile && visibleEvidenceFile.canDelete !== false && (
                          removingEvidence ? (
                            <span className="text-[11px] font-semibold text-foreground-400">Removing…</span>
                          ) : confirmingEvidenceRemoval ? (
                            <>
                              <button
                                type="button"
                                onClick={() => void removeEvidenceFile(visibleEvidenceFile.id)}
                                className="rounded-lg bg-red-600 px-2.5 py-2 text-[11px] font-semibold text-white transition-colors hover:bg-red-700 cursor-pointer"
                              >
                                Remove
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmingEvidenceRemoval(false)}
                                className="rounded-lg border border-background-300 px-2.5 py-2 text-[11px] font-semibold text-foreground-600 transition-colors hover:bg-background-100 cursor-pointer"
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmingEvidenceRemoval(true)}
                              className="rounded-lg border border-background-300 bg-white px-2.5 py-2 text-foreground-400 transition-colors hover:border-red-200 hover:text-red-600 cursor-pointer"
                              title="Remove this file and upload a different one"
                            >
                              <AppIcon className="ri-delete-bin-line" />
                            </button>
                          )
                        )}
                      </span>
                    ) : (
                      <label
                        htmlFor={evidenceInputId}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl border border-primary-200 bg-white px-4 py-2 text-sm font-semibold text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-50"
                      >
                        <AppIcon className="ri-upload-2-line" />
                        Upload evidence
                      </label>
                      )}
                    </div>
                  )}
                  </div>
                  <div className={hasActivityPanel ? layoutStyles.videoFooter : 'flex min-w-0 max-w-full items-center gap-3'}>
                    <button
                      onClick={finishConsuming}
                      disabled={finishDisabled}
                      title={
                        criteria && !criteria.met
                          ? 'Complete the criteria below before finishing.'
                          : manualTimeMissing && !isLiveSession
                            ? 'Enter the time spent before finishing.'
                            : undefined
                      }
                      className={`inline-flex shrink-0 items-center gap-1.5 text-sm font-semibold px-4 py-2 rounded-xl transition-colors ${
                        finishDisabled
                          ? 'bg-background-200 text-foreground-400 cursor-not-allowed'
                          : isVideo
                            ? 'bg-primary-600 text-white hover:bg-primary-700 cursor-pointer'
                            : 'bg-emerald-600 text-white hover:bg-emerald-700 cursor-pointer'
                      }`}
                    >
                      <AppIcon className={finishDisabled ? 'ri-lock-line' : 'ri-check-line'} />
                      Finish
                    </button>
                  </div>
                </div>}
              </div>

              {completed && !isAssignment && (
                <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                  <p role="status" className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
                    <AppIcon className="ri-checkbox-circle-fill" /> Completed
                    {completedTime && <span className="font-mono">· {completedTime}</span>}
                  </p>
                  <p className="mt-1 text-xs text-emerald-800">Your completion for {weekLabel} is saved.</p>
                  {refreshError && <p role="alert" className="mt-2 text-xs text-amber-900">{refreshError}</p>}
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    {nextActivity && <button type="button" onClick={() => navigate(nextActivity.href)} className="rounded-lg bg-emerald-700 px-3 py-2 text-left text-xs font-semibold text-white hover:bg-emerald-800">
                      Next activity: {nextActivity.label}
                    </button>}
                    {!repeating && <button type="button" onClick={() => setRepeating(true)} className="text-xs font-semibold text-emerald-800 underline">Record another attempt</button>}
                  </div>
                </div>
              )}

              {component.description && (
                <div className="mt-4 rounded-xl border border-background-300 bg-white p-4">
                  <h2 className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400 mb-2">Description</h2>
                  <AuthoredContent value={component.description} className="text-sm text-foreground-700 leading-relaxed" />
                </div>
              )}

              {/* Where this stands with the coach. Shown above the completion
                  criteria and instead of them: once the work is with a coach,
                  "Ready to complete" is not the useful thing to say — and on a
                  rejected submission it was actively wrong. */}
              {verdict && (
                <div className={`mt-4 rounded-xl border p-4 ${verdict.panel}`}>
                  <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-foreground-500">
                    <AppIcon className={`${verdict.icon} ${verdict.panelIcon}`} />
                    {verdict.label}
                  </h2>
                  <p className="text-sm leading-relaxed text-foreground-700">{verdict.detail}</p>
                  {marking?.feedback && (
                    <div className="mt-3 rounded-lg border border-foreground-200/70 bg-white/70 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-foreground-400">
                        Coach feedback
                      </p>
                      <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6 text-foreground-700">
                        {marking.feedback}
                      </p>
                      {marking.reviewedBy && (
                        <p className="mt-2 text-[11px] text-foreground-400">
                          {marking.reviewedBy}
                          {marking.reviewedAt
                            ? ` · ${new Date(marking.reviewedAt).toLocaleDateString('en-GB')}`
                            : ''}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {isAssignment && activityEvidenceContext && kind && id && componentId && (
                <div className="mt-4">
                  <AssignmentSubmissionWizard
                    key={`${kind}:${id}:${componentId}`}
                    kind={kind as LearnerKind}
                    learnerId={id}
                    learnerName={detail?.name || 'Learner'}
                    programmeName={detail?.programme || 'Programme not set'}
                    componentId={componentId}
                    title={pageTitle}
                    moduleTitle={moduleTitle}
                    weekTitle={weekTitle}
                    plannedOtjh={component.expectedOtjh ?? null}
                    initialMonth={searchParams.get('month')}
                    topics={component.assignmentTopics}
                    questionHtml={component.assignmentBriefHtml}
                    questionText={component.assignmentBrief}
                    questionFileUrl={component.resourceUrl}
                    questionFileName={component.fileName}
                    questionFiles={component.files}
                    ksbMappings={component.ksbMappings || []}
                    evidenceFiles={evidenceFiles}
                    evidenceDetails={activityEvidenceContext.trainingPlanDetails}
                    timeSeconds={submittedTimeSeconds}
                    timeSource={timeSource}
                    timeControl={(
                      <div className="space-y-3">
                        {timeSource === 'input'
                          ? <p className="text-sm">Confirmed time: <span className="font-mono font-bold">{formatClock(submittedTimeSeconds)}</span></p>
                          : <ActivityElapsedTimer time={formatClock(submittedTimeSeconds)} />}
                        <ActivityTimeSpentInput
                          initialSeconds={manualTimeSeconds}
                          onChange={(seconds) => {
                            setManualTimeSeconds(seconds);
                            setTimeSource(seconds == null ? 'timer' : 'input');
                          }}
                        />
                        <p className="text-xs text-slate-500">The timer runs normally. Only enter a time above if you need to correct it.</p>
                      </div>
                    )}
                    submittingProgress={submitting}
                    onEvidenceChanged={activityEvidenceContext.onUploaded}
                    onRestoreTime={(seconds, source) => {
                      if (source === 'input') { setManualTimeSeconds(seconds); setTimeSource('input'); }
                      else setWallElapsed(current => Math.max(current, seconds));
                    }}
                    onSubmitProgress={async (answers: AssignmentCompletion) => {
                      await finalizeSubmit({
                        assignmentTopicId: answers.assignmentTopicId,
                        ksbs: (component.ksbMappings || []).map(mapping => mapping.code),
                        feedback: `${answers.whatYouLearned}\n\nBusiness impact:\n${answers.businessImpact}`,
                        reportedTime: `${answers.timeTakenSeconds / 60} minutes`,
                        timeTakenSeconds: answers.timeTakenSeconds,
                        timeEntrySource: answers.timeEntrySource,
                      }, { rethrow: true, manualTimeConfirmed: true });
                    }}
                  />
                </div>
              )}

              {!verdict && criteria?.gated && !isAssignment && (
                <div className={`mt-4 rounded-xl border p-4 ${
                  criteria.met ? 'border-emerald-200 bg-emerald-50/60' : 'border-amber-200 bg-amber-50/60'
                }`}>
                  <h2 className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500 mb-2 flex items-center gap-1.5">
                    <AppIcon className={criteria.met ? 'ri-checkbox-circle-line text-emerald-600' : 'ri-information-line text-amber-600'} />
                    {criteria.met ? 'Ready to complete' : 'Before you can complete this'}
                  </h2>
                  <ul className="space-y-1.5">
                    {criteria.evidenceRequired && (
                      <CriterionRow
                        met={criteria.evidenceMet}
                        label={
                          criteria.evidenceMet
                            ? `${evidenceCount} evidence file${evidenceCount === 1 ? '' : 's'} uploaded`
                            : 'Upload at least one evidence file'
                        }
                      />
                    )}
                  </ul>
                </div>
              )}

            </div>

            {/* The list beside the activity — shared with the quiz page, which
                is another way into the same week. */}
            <ActivitySidebar
              weekComponents={ctx?.weekComponents ?? []}
              weekTitle={weekTitle}
              moduleTitle={moduleTitle}
              weeks={ctx?.weeks ?? []}
              completedIds={completedIds}
              kind={kind}
              id={id}
              currentComponentId={componentId}
              completionTimeFor={(c) => completionTimeFor(c, detail)}
              routeFor={(c, week) => componentRoute(kind, id, c, moduleTitle, week)}
              accessOpen={componentAccess.open}
            />
          </div>
        )}
        {timeConfirmation}
        {(phase === 'confirm' || phase === 'time-entry') && (
          <CompletionConfirmPopup
            title={pageTitle}
            noun={noun}
            previousTimeLabel={completed ? completedTime : null}
            keepPreviousTime={keepPreviousTime}
            onKeepPreviousTimeChange={setKeepPreviousTime}
            timerLabel={formatClock(elapsedSeconds)}
            inputLabel={manualTimeSeconds == null ? null : formatClock(manualTimeSeconds)}
            inputSeconds={manualTimeSeconds}
            onInputChange={(seconds) => {
              setManualTimeSeconds(seconds);
              setTimeSource('input');
            }}
            selectedSource={timeSource}
            manualTimeOnly={usesManualTimeOnly}
            continueToReflection={phase === 'time-entry' && component?.reflectionRequired !== false && !keepPreviousTime}
            timeEntryHint={isLiveSession ? 'Enter the time you spent on this session to continue.' : undefined}
            evidenceFileName={evidenceFileLabel}
            submitting={submitting}
            error={submitError}
            onSelectSource={setTimeSource}
            onCancel={() => setPhase('consume')}
            onConfirm={confirmCompletion}
            onRecoverTiming={expiredTiming ? restartTiming : undefined}
            recoveringTiming={recoveringTiming}
          />
        )}
        {phase === 'reflection-choice' && (
          <ReflectionChoicePopup
            noun={noun}
            submitting={submitting}
            error={submitError}
            onCancel={() => setPhase('consume')}
            onAddReflection={() => setPhase('reflect')}
            onRecoverTiming={expiredTiming ? restartTiming : undefined}
            recoveringTiming={recoveringTiming}
            onFinishWithoutReflection={() => void finalizeSubmit({
              ksbs: (component?.ksbMappings || []).map(mapping => mapping.code),
              feedback: '',
              reportedTime: `${submittedTimeSeconds / 60} minutes`,
            }, { skipReflection: true })}
          />
        )}
        {evidencePreview && (
          <EvidencePreviewModal preview={evidencePreview} onClose={() => setEvidencePreview(null)} />
        )}
        {correction && (
          <CompletionTimeDialog
            reason={correction.reason}
            holidayName={correction.holidayName}
            holidays={componentAccess.holidays ?? []}
            submitting={submitting}
            serverError={correctionError}
            onSubmit={submitDeclared}
            onClose={() => { setCorrection(null); setCorrectionError(''); }}
          />
        )}
      </div>
    </WorkspaceShell>
  );
}

function CompletionConfirmPopup({
  title,
  noun,
  timerLabel,
  previousTimeLabel,
  keepPreviousTime,
  onKeepPreviousTimeChange,
  inputLabel,
  inputSeconds,
  onInputChange,
  selectedSource,
  manualTimeOnly,
  continueToReflection = false,
  timeEntryHint,
  evidenceFileName,
  submitting,
  error,
  onSelectSource,
  onCancel,
  onConfirm,
  onRecoverTiming,
  recoveringTiming = false,
}: {
  title: string; noun: string; timerLabel: string; inputLabel: string | null; selectedSource: TimeSource; manualTimeOnly: boolean; evidenceFileName?: string | null;
  previousTimeLabel?: string | null; keepPreviousTime: boolean; onKeepPreviousTimeChange: (keep: boolean) => void;
  submitting: boolean; error: string | null;
  inputSeconds: number | null; onInputChange: (seconds: number | null) => void;
  continueToReflection?: boolean; timeEntryHint?: string;
  onSelectSource: (source: TimeSource) => void; onCancel: () => void; onConfirm: () => void;
  onRecoverTiming?: () => void; recoveringTiming?: boolean;
}) {
  const titleId = useId();
  const timeReviewId = useId();
  const [reviewingManualTime, setReviewingManualTime] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  useEffect(() => {
    const panel = panelRef.current;
    (panel?.querySelector<HTMLInputElement>('input') || panel?.querySelector<HTMLButtonElement>('button'))?.focus();
  }, [reviewingManualTime]);
  const selectedTimeLabel = keepPreviousTime && previousTimeLabel ? previousTimeLabel : selectedSource === 'input'
    ? inputLabel || '--:--:--'
    : timerLabel;
  const confirmSelectedTime = () => {
    if (!reviewingManualTime && !keepPreviousTime && selectedSource === 'input' && inputSeconds != null && inputSeconds > 0) {
      setReviewingManualTime(true);
      return;
    }
    onConfirm();
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-foreground-950/35 px-4 py-6 backdrop-blur-[2px]">
      <div ref={panelRef} role={reviewingManualTime ? 'alertdialog' : 'dialog'} aria-modal="true" aria-labelledby={titleId}
        aria-describedby={reviewingManualTime ? timeReviewId : undefined}
        onKeyDownCapture={(event) => {
          if (event.key === 'Escape' && !submitting) {
            event.preventDefault();
            event.stopPropagation();
            if (reviewingManualTime) setReviewingManualTime(false);
            else onCancel();
          }
          if (event.key === 'Tab') {
            const controls = panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)');
            const first = controls?.[0], last = controls?.[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}
        className="max-h-full w-full max-w-md overflow-y-auto rounded-2xl border border-background-300 bg-white p-6 text-center shadow-2xl">
        <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-full bg-primary-100">
          <AppIcon className="ri-question-line text-2xl text-primary-700" />
        </div>
        <h1 id={titleId} className="text-lg font-heading font-bold text-foreground-900">
          {reviewingManualTime ? 'Check your time entry' : continueToReflection ? 'Enter time spent' : `Confirm ${noun} completion?`}
        </h1>
        <p className="mt-1 text-sm text-foreground-500">{title}</p>
        {!reviewingManualTime && timeEntryHint && <p className="mt-3 text-sm font-medium text-foreground-700">{timeEntryHint}</p>}

        {reviewingManualTime ? (
          <div id={timeReviewId} className="mt-5">
            <ManualTimeSummary seconds={inputSeconds ?? 0} />
          </div>
        ) : <div className="mt-5 space-y-3 rounded-xl border border-background-300 bg-background-50 p-4 text-left">
          <div className="flex items-center justify-between gap-3 border-b border-background-300 pb-3">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Save time</span>
            <span className="font-mono text-base font-bold tabular-nums text-foreground-900">{selectedTimeLabel}</span>
          </div>
          {evidenceFileName && (
            <div className="flex items-center justify-between gap-3 border-b border-background-300 pb-3">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Evidence</span>
              <span className="min-w-0 truncate text-right text-sm font-semibold text-emerald-700" title={evidenceFileName}>
                <AppIcon className="ri-attachment-2 mr-1" />
                {evidenceFileName}
              </span>
            </div>
          )}
          {previousTimeLabel && (
            <>
              <p className="text-xs text-foreground-500">Choose which attempt time to keep. Times will not be added together.</p>
              <button
                type="button"
                aria-pressed={keepPreviousTime}
                onClick={() => onKeepPreviousTimeChange(true)}
                disabled={submitting}
                className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left ${keepPreviousTime ? 'border-primary-300 bg-primary-50 text-primary-800' : 'border-background-300 bg-white text-foreground-700'}`}
              >
                <span className="text-sm font-semibold">Previous attempt</span>
                <span className="font-mono text-sm font-bold tabular-nums">{previousTimeLabel}</span>
              </button>
              <button
                type="button"
                aria-pressed={!keepPreviousTime}
                onClick={() => onKeepPreviousTimeChange(false)}
                disabled={submitting}
                className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left ${!keepPreviousTime ? 'border-primary-300 bg-primary-50 text-primary-800' : 'border-background-300 bg-white text-foreground-700'}`}
              >
                <span className="text-sm font-semibold">Current attempt</span>
                <span className="font-mono text-sm font-bold tabular-nums">{selectedSource === 'input' ? inputLabel || '--:--:--' : timerLabel}</span>
              </button>
            </>
          )}
          <fieldset disabled={submitting || keepPreviousTime} className="space-y-3 disabled:opacity-50">
          {!manualTimeOnly && (
            <button
              type="button"
              onClick={() => onSelectSource('timer')}
              disabled={submitting}
              className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                selectedSource === 'timer'
                  ? 'border-primary-300 bg-primary-50 text-primary-800'
                  : 'border-background-300 bg-white text-foreground-700 hover:bg-background-50'
              } disabled:cursor-not-allowed disabled:opacity-60`}
            >
              <span className="inline-flex items-center gap-2 text-sm font-semibold">
                <AppIcon className="ri-timer-line" />
                Timer
              </span>
              <span className="font-mono text-sm font-bold tabular-nums">{timerLabel}</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => onSelectSource('input')}
            disabled={submitting}
            className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
              selectedSource === 'input'
                ? 'border-primary-300 bg-primary-50 text-primary-800'
                : 'border-background-300 bg-white text-foreground-700 hover:bg-background-50'
            } disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white`}
          >
            <span className="inline-flex items-center gap-2 text-sm font-semibold">
              <AppIcon className="ri-edit-2-line" />
              Input
            </span>
            <span className="font-mono text-sm font-bold tabular-nums">{inputLabel || '--:--:--'}</span>
          </button>
          {selectedSource === 'input' && (
            <fieldset disabled={submitting}>
              <ActivityTimeSpentInput initialSeconds={inputSeconds} onChange={onInputChange} />
            </fieldset>
          )}
          </fieldset>
        </div>}

        {error && (
          <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-left text-xs font-semibold text-red-700">
            {error}
          </p>
        )}
        {error && onRecoverTiming && <button type="button" onClick={onRecoverTiming} disabled={submitting || recoveringTiming}
          className="mt-3 text-sm font-semibold text-primary-700 underline">
          {recoveringTiming ? 'Restoring...' : 'Restore saved time'}
        </button>}

        <div className="mt-5 grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={() => reviewingManualTime ? setReviewingManualTime(false) : onCancel()}
            disabled={submitting}
            className="inline-flex items-center justify-center rounded-xl border border-background-300 bg-white px-4 py-3 text-sm font-semibold text-foreground-700 shadow-sm transition-colors hover:bg-background-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {reviewingManualTime ? 'Edit time' : 'Cancel'}
          </button>
          <button
            type="button"
            onClick={confirmSelectedTime}
            disabled={submitting || (!keepPreviousTime && (manualTimeOnly || selectedSource === 'input') && (inputSeconds == null || inputSeconds <= 0))}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <AppIcon className={submitting ? 'ri-loader-4-line animate-spin' : 'ri-check-line'} />
            {submitting ? 'Saving...' : reviewingManualTime ? 'Confirm time' : continueToReflection ? 'Continue' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   LINK CLASSIFICATION — authored URLs are free text (an
   external listening page, a direct file, a Google Slides
   link, …). Detect what we can actually embed/play inline
   vs. what only supports a "open in new tab" fallback.
   ═══════════════════════════════════════════════════════ */
const AUDIO_FILE_RE = /\.(mp3|wav|ogg|m4a|aac|flac)(\?.*)?$/i;
const IMAGE_FILE_RE = /\.(png|jpe?g|gif|webp|bmp|svg)(\?.*)?$/i;
const VIDEO_FILE_RE = /\.(mp4|webm|ogg|mov|m4v)(\?.*)?$/i;
const PDF_FILE_RE = /\.pdf(\?.*)?$/i;
const WORD_FILE_RE = /\.docx(\?.*)?$/i;
const EXCEL_FILE_RE = /\.(xlsx|xls|csv)(\?.*)?$/i;
// Not .rtf: printed as text it is a wall of control codes. It goes to the
// Office preview with the other word-processor formats (see docEmbed).
const TEXT_FILE_RE = /\.(txt|md)(\?.*)?$/i;

function googleDriveFileId(url: string): string | null {
  const match =
    url.match(/drive\.google\.com\/file\/d\/([\w-]{10,})/) ||
    url.match(/drive\.google\.com\/open\?id=([\w-]{10,})/) ||
    url.match(/drive\.google\.com\/uc\?[^#]*id=([\w-]{10,})/);
  return match?.[1] ?? null;
}

function proxiedMaterialUrl(url: string): string {
  const driveId = googleDriveFileId(url);
  return driveId ? `/learner_api/media/google-drive/${driveId}/` : url;
}

function displayableMediaSource(url: string, fileName?: string | null): { kind: 'image' | 'video'; src: string } | null {
  const probe = `${fileName || ''} ${url}`;
  const src = proxiedMaterialUrl(url);
  if (IMAGE_FILE_RE.test(probe)) return { kind: 'image', src };
  if (VIDEO_FILE_RE.test(probe)) return { kind: 'video', src };
  return null;
}

function directAudioSource(url: string, fileName?: string | null): string | null {
  const probe = `${fileName || ''} ${url}`;
  if (AUDIO_FILE_RE.test(probe) || googleDriveFileId(url)) return proxiedMaterialUrl(url);
  return null;
}

function fileLabelFrom(url: string, fileName?: string | null): string {
  if (fileName) return decodeInlineText(fileName);
  try {
    const parsed = new URL(url, window.location.origin);
    return decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || 'Attached file');
  } catch {
    return 'Attached file';
  }
}

type AttachmentPreviewState =
  | { status: 'loading' }
  | { status: 'ready'; kind: 'html'; html: string }
  | { status: 'ready'; kind: 'text'; text: string }
  | { status: 'error'; message: string };

/** What to tell the learner when a file request fails. The server's own
 * message ("Learning activities open on 1 October…", "This file is empty…")
 * says what to do; a bare status code does not. */
async function fileRequestError(response: Response): Promise<string> {
  try {
    if ((response.headers.get('Content-Type') || '').includes('application/json')) {
      const body = await response.json() as { error?: unknown };
      if (typeof body.error === 'string' && body.error.trim()) return body.error;
    }
  } catch {
    // Fall through to the status-based messages.
  }
  if (response.status === 404) return 'This file could not be found on the server. Please let your coach or the curriculum team know.';
  if (response.status === 401) return 'Your session has ended. Sign in again to open this file.';
  if (response.status === 403) return 'You do not have access to this file yet.';
  return `File request failed (${response.status}). Please try again.`;
}

function ReadingAccessibilityToolbar({
  preferences,
  onChange,
  onHighlight,
  onClearHighlights,
  onSave,
  onRead,
  speaking,
  saved,
}: {
  preferences: ReadingPreferences;
  onChange: (next: ReadingPreferences) => void;
  onHighlight?: () => void;
  onClearHighlights?: () => void;
  onSave: () => void;
  onRead?: () => void;
  speaking: boolean;
  saved: boolean;
}) {
  const update = <K extends keyof ReadingPreferences>(key: K, value: ReadingPreferences[K]) => {
    onChange({ ...preferences, [key]: value });
  };
  return (
    <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50/70 p-3" aria-label="Reading accessibility tools">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 inline-flex items-center gap-1.5 text-xs font-black text-blue-900">
          <AppIcon className="ri-accessibility-line text-base" />
          Reading tools
        </span>
        <button type="button" onClick={() => update('fontSize', Math.max(12, preferences.fontSize - 2))} className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-bold text-blue-800" aria-label="Decrease text size">A−</button>
        <button type="button" onClick={() => update('fontSize', Math.min(30, preferences.fontSize + 2))} className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-bold text-blue-800" aria-label="Increase text size">A+</button>
        <select value={preferences.font} onChange={(event) => update('font', event.target.value as ReadingFont)} className="h-8 rounded-lg border border-blue-200 bg-white px-2 text-xs font-bold text-blue-900" aria-label="Reading font">
          <option value="default">Default font</option>
          <option value="dyslexia">Dyslexia-friendly</option>
          <option value="sans">Clear sans serif</option>
        </select>
        <select value={preferences.colourMode} onChange={(event) => update('colourMode', event.target.value as ReadingColourMode)} className="h-8 rounded-lg border border-blue-200 bg-white px-2 text-xs font-bold text-blue-900" aria-label="Reading colour mode">
          <option value="paper">White paper</option>
          <option value="cream">Warm cream</option>
          <option value="monochrome">Black & white</option>
          <option value="dark">Dark mode</option>
        </select>
        <button type="button" onClick={() => update('lineHeight', preferences.lineHeight >= 2.1 ? 1.5 : Number((preferences.lineHeight + 0.2).toFixed(1)))} className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-bold text-blue-800" title="Cycle line spacing">
          <AppIcon className="ri-line-height" /> Spacing
        </button>
        <button type="button" onClick={() => update('letterSpacing', preferences.letterSpacing >= 0.1 ? 0 : Number((preferences.letterSpacing + 0.05).toFixed(2)))} className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-bold text-blue-800" title="Cycle letter spacing">
          Letter gap
        </button>
        <button type="button" onClick={() => update('ruler', !preferences.ruler)} aria-pressed={preferences.ruler} className={`rounded-lg border px-2.5 py-1.5 text-xs font-bold ${preferences.ruler ? 'border-blue-600 bg-blue-600 text-white' : 'border-blue-200 bg-white text-blue-800'}`}>
          <AppIcon className="ri-focus-3-line" /> Reading ruler
        </button>
        {onRead && (
          <button type="button" onClick={onRead} aria-pressed={speaking} className={`rounded-lg border px-2.5 py-1.5 text-xs font-bold ${speaking ? 'border-violet-600 bg-violet-600 text-white' : 'border-blue-200 bg-white text-blue-800'}`}>
            <AppIcon className={speaking ? 'ri-stop-circle-line' : 'ri-volume-up-line'} /> {speaking ? 'Stop reading' : 'Read aloud'}
          </button>
        )}
        {onHighlight && <button type="button" onMouseDown={event => event.preventDefault()} onClick={onHighlight} className="rounded-lg border border-amber-300 bg-amber-100 px-2.5 py-1.5 text-xs font-bold text-amber-900"><AppIcon className="ri-mark-pen-line" /> Highlight selection</button>}
        {onClearHighlights && <button type="button" onClick={onClearHighlights} className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-bold text-blue-800">Clear highlights</button>}
        <button type="button" onClick={onSave} className="ml-auto rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-700">
          <AppIcon className="ri-save-line" /> {saved ? 'Saved' : 'Save changes'}
        </button>
      </div>
      <p className="mt-2 text-[10px] font-semibold text-blue-700">Preferences and highlights are kept on this device. For PDF fonts, spacing and text selection, choose Text reading view. Original PDF keeps its own marker. Focus a reading area and use the arrow keys to move the ruler.</p>
    </div>
  );
}

function AccessibleReadingMaterial({
  component,
  title,
  onReady,
}: {
  component: JourneyComponent;
  title: string;
  onReady?: () => void;
}) {
  const componentId = component.componentId || title;
  const sourceHtml = normalizeAuthoredHtml(component.contentHtml || '');
  const files = useMemo(() => readingFiles(component.resourceUrl, component.fileName, sourceHtml, component.files), [component.resourceUrl, component.fileName, sourceHtml, component.files]);
  const [downloadingPdf, setDownloadingPdf] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const savedDocument = readStoredJson(readingStorageKey(componentId, 'document'), { sourceHtml: '', html: '' });
  // The authored HTML and a saved copy with the learner's <mark>s go through
  // the same curriculum sanitiser: scripts, event handlers and unsafe links
  // never reach the page, while embeds, tables and file links stay.
  const [html, setHtml] = useState(() => sanitizeCurriculumHtml(
    savedDocument.sourceHtml === sourceHtml && savedDocument.html ? savedDocument.html : sourceHtml,
    { openLinksInNewTab: true },
  ));
  // React 19 re-applies innerHTML whenever this object's identity changes, so a
  // fresh literal on every render (the parent re-renders each second for the
  // activity timer) would tear down and recreate any embedded <video>/<iframe>.
  const innerHtml = useMemo(() => ({ __html: html }), [html]);
  const [preferences, setPreferences] = useState<ReadingPreferences>(() => readStoredJson(readingStorageKey(componentId, 'preferences'), DEFAULT_READING_PREFERENCES));
  const [saved, setSaved] = useState(true);
  const [saveError, setSaveError] = useState('');
  const [highlightNotice, setHighlightNotice] = useState('');
  const annotations = useRef(new Map<string, ReadingAnnotations>());
  const registerAnnotations = useCallback((id: string, actions: ReadingAnnotations | null) => {
    if (actions) annotations.current.set(id, actions);
    else annotations.current.delete(id);
  }, []);
  const onAnnotationsDirty = useCallback(() => setSaved(false), []);
  const readingTools = useMemo(() => ({ preferences, register: registerAnnotations, onDirty: onAnnotationsDirty }), [preferences, registerAnnotations, onAnnotationsDirty]);
  const narration = useReadingNarration(`${componentId}:${component.resourceUrl || ''}:${sourceHtml}`);
  const { speaking, stop: stopNarration } = narration;
  const pdfReaders = useRef(new Map<string, PdfPageReader>());
  const registerPdfReader = useCallback((id: string, reader: PdfPageReader | null) => {
    // A changed/closed PDF page invalidates any queued text from that page.
    stopNarration();
    if (reader) pdfReaders.current.set(id, reader);
    else pdfReaders.current.delete(id);
  }, [stopNarration]);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const readingBodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!component.resourceUrl && sourceHtml) onReady?.();
  }, [component.resourceUrl, sourceHtml, onReady]);


  const highlightSelection = () => {
    setHighlightNotice('');
    if (highlightReadingSelection(contentRef.current)) {
      setHtml(contentRef.current!.innerHTML);
      setSaved(false);
      return;
    }
    if (Array.from(annotations.current.values()).some(actions => actions.highlight())) return;
    setHighlightNotice('Select some reading text first. For a PDF, use Text reading view or the Original PDF marker.');
  };

  const clearHighlights = () => {
    const root = contentRef.current;
    if (root) {
      clearReadingHighlights(root);
      setHtml(root.innerHTML);
    }
    annotations.current.forEach(actions => actions.clear());
    setHighlightNotice('');
    setSaved(false);
  };

  const save = () => {
    // Saved apart, so a reading too large for the browser's storage still keeps
    // the learner's display preferences -- and the learner is told which part
    // was not kept instead of the button silently staying on "Save changes".
    try {
      window.localStorage.setItem(readingStorageKey(componentId, 'preferences'), JSON.stringify(preferences));
    } catch {
      setSaved(false);
      setSaveError('Your browser would not let this page save on this device (storage may be full or blocked in private browsing).');
      return;
    }
    try {
      window.localStorage.setItem(readingStorageKey(componentId, 'document'), JSON.stringify({ sourceHtml, html }));
      annotations.current.forEach(actions => actions.save());
      setSaved(true);
      setSaveError('');
    } catch {
      setSaved(false);
      setSaveError('Your reading preferences were saved, but some highlights could not be saved on this device. Storage may be full or blocked. Keep this reading open and try saving again.');
    }
  };

  const readAloud = () => {
    if (speaking) {
      narration.stop();
      return;
    }
    void narration.read(async () => {
      const text = Array.from(readingBodyRef.current?.querySelectorAll('[data-reading-text]:not([data-pdf-reading-text])') || [])
        .flatMap(narrationFromHtml);
      const pages = await Promise.all(Array.from(pdfReaders.current.values(), readPage => readPage()));
      return [...text, ...pages.flat()];
    });
  };

  const downloadText = async () => {
    if (downloadingPdf) return;
    setDownloadingPdf(true);
    setDownloadError('');
    try {
      await downloadReadingPdf(title, sourceHtml);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : 'Could not download the reading. Please try again.');
    } finally {
      setDownloadingPdf(false);
    }
  };

  const downloadNotes = () => {
    const documentHtml = `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title><style>body{max-width:850px;margin:40px auto;padding:0 24px;font-family:Arial,sans-serif;font-size:${preferences.fontSize}px;line-height:${preferences.lineHeight};letter-spacing:${preferences.letterSpacing}em}mark{background:#fde047;color:#111827}</style></head><body><h1>${title}</h1>${html}</body></html>`;
    const blob = new Blob([documentHtml], { type: 'text/html;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `${title.replace(/[^a-z0-9-_]+/gi, '-').replace(/^-|-$/g, '') || 'reading'}-highlights.html`;
    anchor.click();
    URL.revokeObjectURL(href);
  };

  return (
    <>
      <ReadingAccessibilityToolbar
        preferences={preferences}
        onChange={(next) => { setPreferences(next); setSaved(false); }}
        onHighlight={highlightSelection}
        onClearHighlights={clearHighlights}
        onSave={save}
        onRead={readAloud}
        speaking={speaking}
        saved={saved}
      />
      {(files.length > 0 || component.contentHtml) && (
        <div className="mb-3 flex flex-wrap justify-end gap-2" aria-label="Reading downloads">
          {files.map(file => <DownloadFileButton key={file.url} url={file.url} fileName={file.fileName} label={files.length === 1 ? 'Download original file' : `Download: ${file.label}`} />)}
          {component.contentHtml && <>
            <button type="button" onClick={downloadText} disabled={downloadingPdf} className="inline-flex items-center gap-1.5 rounded-lg border border-background-300 bg-white px-3 py-1.5 text-xs font-bold text-foreground-700 hover:bg-background-50 disabled:cursor-wait disabled:opacity-60"><AppIcon className="ri-file-pdf-2-line" />{downloadingPdf ? 'Preparing PDF…' : 'Download reading as PDF'}</button>
            <button type="button" onClick={downloadNotes} className="inline-flex items-center gap-1.5 rounded-lg border border-background-300 bg-white px-3 py-1.5 text-xs font-bold text-foreground-700 hover:bg-background-50"><AppIcon className="ri-download-2-line" />Download highlighted reading</button>
          </>}
        </div>
      )}
      {downloadError && <p role="alert" className="mb-3 text-sm text-red-700">{downloadError}</p>}
      {saveError && <p role="alert" className="mb-3 text-sm text-red-700">{saveError}</p>}
      {highlightNotice && <p role="status" className="mb-3 text-sm text-amber-800">{highlightNotice}</p>}
      {narration.speechError && <p role="alert" className="mb-3 text-sm text-red-700">{narration.speechError}</p>}
      <ReadingPdfNarrationContext.Provider value={registerPdfReader}>
      <ReadingToolsContext.Provider value={readingTools}>
      <div ref={readingBodyRef}>
      {component.contentHtml && (
        <ReadingSurface
          preferences={preferences}
          label="Reading text"
          className="relative overflow-hidden rounded-xl border border-background-200 p-5"
        >
          <div
            ref={contentRef}
            data-reading-text
            className="curriculum-html relative z-0 max-w-none [&_h1]:font-heading [&_h2]:font-heading [&_h3]:font-heading"
            dangerouslySetInnerHTML={innerHtml}
          />
        </ReadingSurface>
      )}
      {component.resourceUrl ? (
        <div className={component.contentHtml ? 'mt-4 border-t border-background-200 pt-4' : ''}>
          <InlineAttachmentPreview
            key={component.resourceUrl}
            onReady={onReady}
            url={component.resourceUrl}
            title={title}
            fileName={component.fileName}
            readingPreferences={preferences}
            annotationKey={componentId}
            allowAnnotatedDownload
          />
        </div>
      ) : !component.contentHtml ? (
        <p className="text-sm text-foreground-500">No reading content was set. You can still record your reflection below.</p>
      ) : null}
      <ExtraComponentFiles component={component} title={title} primaryUrl={component.resourceUrl} />
      {/* `files` also carries the documents linked from inside the reading text,
          which no author ever attached and so `ExtraComponentFiles` never sees.
          Those get a collapsed preview instead of a card -- and the attachment
          filter keeps a file that is in both lists from being offered twice. */}
      {files.filter(file => file.url !== component.resourceUrl
        && !(component.files || []).some(attached => attached.url === file.url)
        && /\.(pdf|pptx?|ppsx?|pptm|ppsm)$/i.test(fileProbe(file.url, file.fileName))).map(file => (
        <LinkedReadingPreview key={file.url} url={file.url} title={file.label} fileName={file.fileName} />
      ))}
      </div>
      </ReadingToolsContext.Provider>
      </ReadingPdfNarrationContext.Provider>
    </>
  );
}

/**
 * The attachments the block above did not already show.
 *
 * One of a component's files is normally on screen already — inline for a
 * reading or a deck, in the player for a podcast — and `primaryUrl` says which.
 * Everything else the author attached is rendered here, each keeping the
 * position it has in the authored list so the numbers match the #1, #2 order
 * the Module Builder shows.
 *
 * Filtering by URL rather than dropping `files[0]` matters when the primary is
 * not the first file: a reading whose text is the body, or a podcast pointing
 * at an external episode page, would otherwise silently swallow one attachment.
 */
function ExtraComponentFiles({ component, title, primaryUrl = '', downloadAllowed = true, audio = false }: {
  component: JourneyComponent;
  title: string;
  /** The file already rendered above, if any. */
  primaryUrl?: string | null;
  downloadAllowed?: boolean;
  /** A podcast's extra files are recordings, so give them players, not cards. */
  audio?: boolean;
}) {
  const extras = (component.files || [])
    .map((file, index) => ({ file, position: index + 1 }))
    .filter(({ file }) => file.url !== (primaryUrl || ''));
  if (!extras.length) return null;
  return (
    <section className="mt-4 border-t border-background-200 pt-4" aria-label="More files">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-foreground-400">
        {extras.length} more file{extras.length === 1 ? '' : 's'}
      </p>
      <div className="space-y-4">
        {extras.map(({ file, position }) => (
          <div key={`${file.url}:${position}`}>
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="min-w-0 truncate text-xs font-bold text-foreground-700">
                <span className="tabular-nums text-foreground-400">{position}.</span> {file.fileName || 'Attached file'}
              </p>
              {downloadAllowed && <DownloadFileButton url={file.url} fileName={file.fileName} />}
            </div>
            {audio
              ? <audio controls preload="metadata" className="w-full" src={proxiedMaterialUrl(file.url)}>Your browser does not support audio playback.</audio>
              : <InlineAttachmentPreview url={file.url} title={file.fileName || title} fileName={file.fileName} allowAnnotatedDownload={downloadAllowed} />}
          </div>
        ))}
      </div>
    </section>
  );
}

/** A document linked from the reading text: previewed on request, not up front. */
function LinkedReadingPreview({ url, title, fileName }: { url: string; title: string; fileName?: string }) {
  const [open, setOpen] = useState(false);
  return <div className="mt-4 rounded-xl border border-background-300 bg-white p-3">
    <button type="button" onClick={() => setOpen(value => !value)} aria-expanded={open}
      className="flex w-full items-center gap-2 text-left text-sm font-semibold text-primary-700">
      <AppIcon className={open ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'} />
      {open ? 'Hide preview' : 'Preview file'}: {title}
    </button>
    {open && <div className="mt-3"><InlineAttachmentPreview url={url} title={title} fileName={fileName} allowAnnotatedDownload /></div>}
  </div>;
}

function InlineMediaPreview({ url, title, fileName, onReady }: { url: string; title: string; fileName?: string | null; onReady?: () => void }) {
  const media = displayableMediaSource(url, fileName);
  if (!media) return null;
  if (media.kind === 'image') {
    return (
      <div className="overflow-hidden rounded-xl border border-background-300 bg-background-950/95 p-3">
        <img src={media.src} alt={title} onLoad={onReady} className="mx-auto max-h-[70vh] max-w-full rounded-lg object-contain" />
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-background-300 bg-black">
      <video src={media.src} onLoadedData={onReady} controls preload="metadata" className="mx-auto max-h-[70vh] w-full bg-black" />
    </div>
  );
}

function decodeInlineText(value: string): string {
  return value
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

/**
 * Download the file itself.
 *
 * Reading attachments are always downloadable for learners with access.
 * Slide decks still respect the author's `downloadAllowed` setting.
 *
 * `download` names the saved file rather than leaving the learner with the
 * upload's timestamped name; it works because these are served same-origin.
 */
function DownloadFileButton({ url, fileName, label = 'Download' }: {
  url: string;
  fileName?: string | null;
  label?: string;
}) {
  const href = proxiedMaterialUrl(url);
  // Browsers ignore `download` on another site's link and navigate instead,
  // which took the learner off the activity (and its timer). Open those apart.
  const absolute = absoluteDocUrl(href);
  const external = absolute !== null && new URL(absolute).origin !== window.location.origin;
  return (
    <a
      href={href}
      download={fileLabelFrom(url, fileName)}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-primary-200 bg-primary-50 px-3.5 py-2 text-xs font-bold text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-100"
    >
      <AppIcon className="ri-download-2-line" />
      {label}
    </a>
  );
}

/**
 * The file itself, under whatever preview was shown.
 *
 * `previewed` says whether the reader is already looking at the content: this
 * card sits under the in-house deck viewer as well as standing in for the
 * previews we cannot draw, and telling somebody a preview is unavailable while
 * they read one is simply untrue.
 */
function AttachedFileCard({ url, fileName, previewed = false }: {
  url: string;
  fileName?: string | null;
  previewed?: boolean;
}) {
  const href = proxiedMaterialUrl(url);
  return (
    <div className="rounded-xl border border-background-300 bg-background-50 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-100 text-blue-700">
            <AppIcon className="ri-file-excel-2-line text-lg" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold text-foreground-900">{fileLabelFrom(url, fileName)}</p>
            <p className="text-xs text-foreground-500">
              {previewed
                ? 'Save a copy to read it outside the platform.'
                : 'Preview is not available for this file type.'}
            </p>
          </div>
        </div>
        {/* Saved, not opened in a tab: the browser has no viewer for these
            types either, so opening one only moved the download a click away
            (and, for a deck, downloaded it under the upload's timestamped
            name). `download` names the file as the author uploaded it. */}
        <a
          href={href}
          download={fileLabelFrom(url, fileName)}
          className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-primary-600 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-primary-700"
        >
          <AppIcon className="ri-download-2-line" />
          Download file
        </a>
      </div>
    </div>
  );
}

export function InlineAttachmentPreview({ url, title, fileName, readingPreferences, annotationKey, allowAnnotatedDownload = false, pdfTools = true, onReady }: {
  onReady?: () => void;
  url: string;
  title: string;
  fileName?: string | null;
  readingPreferences?: ReadingPreferences;
  annotationKey?: string;
  allowAnnotatedDownload?: boolean;
  /** Fit PDF pages to the available width and show the zoom percentage. */
  pdfTools?: boolean;
}) {
  const readingTools = useContext(ReadingToolsContext);
  readingPreferences = readingPreferences || readingTools?.preferences;
  const media = displayableMediaSource(url, fileName);
  const previewUrl = proxiedMaterialUrl(url);
  const probe = fileProbe(url, fileName);
  const isPdf = PDF_FILE_RE.test(probe);
  const isWord = WORD_FILE_RE.test(probe);
  const isExcel = EXCEL_FILE_RE.test(probe);
  const isText = TEXT_FILE_RE.test(probe);
  const canParseInline = isWord || isExcel || isText;
  const [preview, setPreview] = useState<AttachmentPreviewState | null>(canParseInline ? { status: 'loading' } : null);

  useEffect(() => {
    if (!canParseInline) {
      setPreview(null);
      return;
    }

    let cancelled = false;
    setPreview({ status: 'loading' });

    async function loadPreview() {
      try {
        const response = await fetchAttachmentResource(previewUrl);
        if (!response.ok) throw new Error(await fileRequestError(response));

        if (isWord) {
          const arrayBuffer = await response.arrayBuffer();
          const mammoth = await import('mammoth/mammoth.browser');
          const result = await mammoth.convertToHtml({ arrayBuffer });
          if (!cancelled) {
            setPreview({ status: 'ready', kind: 'html', html: DOMPurify.sanitize(result.value || '<p>No preview content found.</p>') });
          }
          return;
        }

        if (isExcel) {
          const arrayBuffer = await response.arrayBuffer();
          const XLSX = await import('xlsx');
          const workbook = XLSX.read(arrayBuffer, { type: 'array' });
          const sheetName = workbook.SheetNames[0];
          const sheet = sheetName ? workbook.Sheets[sheetName] : null;
          const html = sheet
            ? XLSX.utils.sheet_to_html(sheet, { id: 'learner-inline-sheet-preview' })
            : '<p>No worksheet found.</p>';
          if (!cancelled) setPreview({ status: 'ready', kind: 'html', html: DOMPurify.sanitize(html) });
          return;
        }

        const text = await response.text();
        if (!cancelled) setPreview({ status: 'ready', kind: 'text', text });
      } catch (error) {
        if (!cancelled) {
          setPreview({
            status: 'error',
            message: error instanceof Error ? error.message : 'Could not preview this file.',
          });
        }
      }
    }

    void loadPreview();

    return () => {
      cancelled = true;
    };
  }, [canParseInline, isExcel, isText, isWord, previewUrl]);

  // Stable identity so the parent's per-second timer re-render does not re-apply
  // innerHTML (React 19 compares this object by reference).
  const previewHtml = preview?.status === 'ready' && preview.kind === 'html' ? preview.html : null;
  const previewInnerHtml = useMemo(() => (previewHtml == null ? undefined : { __html: previewHtml }), [previewHtml]);

  useEffect(() => {
    if (preview?.status === 'ready') onReady?.();
  }, [preview, onReady]);

  if (media) return <InlineMediaPreview url={url} title={title} fileName={fileName} onReady={onReady} />;

  if (isPdf) {
    return <PdfCanvasPreview key={previewUrl} url={previewUrl} title={title} fileName={fileName} readingPreferences={readingPreferences} annotationKey={annotationKey} allowAnnotatedDownload={allowAnnotatedDownload} fitWidth={pdfTools} onReady={onReady} />;
  }

  if (preview?.status === 'loading') {
    return (
      <div className="grid min-h-[280px] place-items-center rounded-xl border border-background-300 bg-white text-sm font-semibold text-foreground-500 shadow-sm">
        <span className="inline-flex items-center gap-2"><AppIcon className="ri-loader-4-line animate-spin" />Loading file preview...</span>
      </div>
    );
  }

  if (preview?.status === 'ready' && preview.kind === 'html') {
    if (readingPreferences) return <ReadingAttachmentText key={`${previewUrl}:${preview.html}`} sourceHtml={preview.html}
      storageKey={readingStorageKey(`${annotationKey || url}:${url}`, 'file-document')} preferences={readingPreferences} />;
    return (
      <div className="max-h-[72vh] overflow-auto rounded-xl border border-background-300 bg-white p-6 shadow-sm">
        <div
          data-reading-text
          className="learner-file-preview max-w-none text-sm leading-relaxed text-foreground-800 [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-bold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-xl [&_h2]:font-bold [&_h3]:mb-2 [&_h3]:mt-3 [&_h3]:text-lg [&_h3]:font-semibold [&_p]:mb-3 [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-background-300 [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-background-300 [&_th]:bg-background-100 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left"
          dangerouslySetInnerHTML={previewInnerHtml}
        />
      </div>
    );
  }

  if (preview?.status === 'ready' && preview.kind === 'text') {
    if (readingPreferences) {
      const sourceHtml = `<pre>${preview.text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>`;
      return <ReadingAttachmentText key={`${previewUrl}:${preview.text}`} sourceHtml={sourceHtml}
        storageKey={readingStorageKey(`${annotationKey || url}:${url}`, 'file-document')} preferences={readingPreferences} />;
    }
    return (
      <pre data-reading-text className="max-h-[72vh] overflow-auto whitespace-pre-wrap rounded-xl border border-background-300 bg-white p-6 text-sm leading-relaxed text-foreground-800 shadow-sm">
        {preview.text}
      </pre>
    );
  }

  if (preview?.status === 'error') {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-bold">Could not show this file inline.</p>
        <p className="mt-1 text-xs">{preview.message}</p>
      </div>
    );
  }

  return (
    <>
      <DocumentEmbed url={url} title={title} fileName={fileName} onReady={onReady} />
      <div className="mt-3">
        {/* DocumentEmbed above draws the deck itself where it can, so the card
            below offers the file rather than apologising for a missing
            preview. */}
        <AttachedFileCard url={url} fileName={fileName} previewed />
      </div>
    </>
  );
}

type PdfHighlight = { x: number; y: number; width: number; height: number };

function PdfCanvasPreview({ url, title, fileName, readingPreferences, annotationKey, allowAnnotatedDownload, fitWidth = false, onReady }: {
  onReady?: () => void;
  url: string;
  title: string;
  fileName?: string | null;
  readingPreferences?: ReadingPreferences;
  annotationKey?: string;
  allowAnnotatedDownload: boolean;
  fitWidth?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.25);
  const [status, setStatus] = useState<'loading' | 'ready' | 'browser' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [markerActive, setMarkerActive] = useState(false);
  const [draftHighlight, setDraftHighlight] = useState<PdfHighlight | null>(null);
  const highlightStorageKey = readingStorageKey(annotationKey || url, 'pdf-highlights');
  const [highlights, setHighlights] = useState<Record<number, PdfHighlight[]>>(() => readStoredJson(highlightStorageKey, {}));
  const [annotationsSaved, setAnnotationsSaved] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const narration = useReadingNarration(`${url}:${pageNumber}`);
  const { speaking } = narration;
  const registerPdfReader = useContext(ReadingPdfNarrationContext);
  const readingTools = useContext(ReadingToolsContext);
  const registerAnnotations = readingTools?.register;
  const onAnnotationsDirty = readingTools?.onDirty;
  const pdfReaderId = useId();
  const [textView, setTextView] = useState(false);
  const ocrDocument = useMemo(() => ({ pdf, pages: new Map<number, string>() }), [pdf]);
  const ocrText = ocrDocument.pages;
  const preferences = readingPreferences || DEFAULT_READING_PREFERENCES;

  const persistHighlights = useCallback(() => {
    window.localStorage.setItem(highlightStorageKey, JSON.stringify(highlights));
    setAnnotationsSaved(true);
    setExportError(null);
  }, [highlightStorageKey, highlights]);
  useEffect(() => {
    if (!annotationsSaved) onAnnotationsDirty?.();
  }, [annotationsSaved, highlights, onAnnotationsDirty]);
  useEffect(() => {
    registerAnnotations?.(pdfReaderId, {
      highlight: () => false,
      clear: () => { setHighlights({}); setAnnotationsSaved(false); },
      save: persistHighlights,
    });
  }, [registerAnnotations, pdfReaderId, persistHighlights]);
  useEffect(() => () => registerAnnotations?.(pdfReaderId, null), [registerAnnotations, pdfReaderId]);

  const currentPageNarration = useCallback(async () => {
    if (status !== 'ready' || !pdf) {
      throw new ReadingNarrationError(status === 'loading'
        ? 'The PDF is still loading. Please wait, then try Read aloud again.'
        : 'The open PDF could not be read. Please reload its preview and try again.');
    }
    const page = await pdf.getPage(pageNumber);
    const content = await page.getTextContent();
    const blocks = narrationFromPdf(content.items);
    if (!blocks.length && ocrText.has(pageNumber)) {
      return [{ text: ocrText.get(pageNumber)!, pauseMs: 650 }];
    }
    if (!blocks.length) {
      throw new ReadingNarrationError('No readable text was found on the open PDF page. Open Text reading view and choose Extract page text, then try Read aloud again.');
    }
    return blocks;
  }, [pageNumber, pdf, status, ocrText]);

  useEffect(() => {
    registerPdfReader?.(pdfReaderId, currentPageNarration);
  }, [registerPdfReader, pdfReaderId, currentPageNarration]);

  // Keep a preview's place in the narration order when its page changes.
  // Remove it only when the preview closes or the activity unmounts.
  useEffect(() => () => registerPdfReader?.(pdfReaderId, null), [registerPdfReader, pdfReaderId]);

  useEffect(() => {
    let cancelled = false;
    let loadedPdf: PDFDocumentProxy | null = null;

    async function loadPdf() {
      let requestFailed = false;
      try {
        setStatus('loading');
        setError(null);
        const response = await fetchPdfResource(url).catch(error => { requestFailed = true; throw error; });
        if (!response.ok) throw new Error(await fileRequestError(response));
        const buffer = await response.arrayBuffer();
        if (!buffer.byteLength) throw new Error('The PDF response contains no file data. Please try opening the original file.');
        if (cancelled) return;
        const { GlobalWorkerOptions, getDocument } = await import('pdfjs-dist');
        GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
        const document = await getDocument({ data: new Uint8Array(buffer) }).promise;
        loadedPdf = document;
        if (!cancelled) {
          setPdf(document);
          setPageCount(document.numPages);
          setPageNumber(1);
          setStatus('ready');
        }
      } catch (loadError) {
        if (!cancelled) {
          const absolute = absoluteDocUrl(url);
          // Keep our reading/annotation tools for files that allow CORS.
          // A cross-origin network failure can still be viewed by the browser.
          if (requestFailed && absolute && new URL(absolute).origin !== window.location.origin) {
            setStatus('browser');
            return;
          }
          setStatus('error');
          setError(loadError instanceof Error ? loadError.message : 'Could not load PDF preview.');
        }
      }
    }

    void loadPdf();

    return () => {
      cancelled = true;
      void loadedPdf?.cleanup();
    };
  }, [url]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return;

    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null;

    async function renderPage() {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const page = await pdf.getPage(pageNumber);
      if (cancelled) return;
      // Fit-width pages can be wider than their PDF point size on a desktop.
      const viewport = page.getViewport({ scale: fitWidth ? scale * 2 : scale });
      const context = canvas.getContext('2d');
      if (!context) return;

      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.style.width = '100%';
      canvas.style.height = 'auto';
      context.clearRect(0, 0, canvas.width, canvas.height);

      renderTask = page.render({ canvas, canvasContext: context, viewport });
      await renderTask.promise.catch((renderError: unknown) => {
        if (!cancelled && !(renderError instanceof Error && renderError.name === 'RenderingCancelledException')) {
          throw renderError;
        }
      });
      if (!cancelled) onReady?.();
    }

    void renderPage().catch((renderError: unknown) => {
      if (!cancelled) {
        setStatus('error');
        setError(renderError instanceof Error ? renderError.message : 'Could not render PDF page.');
      }
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pageNumber, pdf, scale, fitWidth, onReady]);


  const pointFromPointer = (event: React.PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      y: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
    };
  };

  const updateDraftHighlight = (start: { x: number; y: number }, end: { x: number; y: number }): PdfHighlight => ({
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  });

  const saveHighlights = () => {
    try {
      persistHighlights();
    } catch {
      setAnnotationsSaved(false);
      setExportError('Could not save PDF marks on this device. Storage may be full or blocked. Please try again.');
    }
  };

  const readCurrentPage = async () => {
    if (!pdf) return;
    if (speaking) {
      narration.stop();
      return;
    }
    await narration.read(currentPageNarration);
  };

  const downloadAnnotatedPdf = async () => {
    if (!pdf || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      const { jsPDF } = await import('jspdf');
      let output: InstanceType<typeof jsPDF> | null = null;
      for (let pageIndex = 1; pageIndex <= pdf.numPages; pageIndex += 1) {
        const page = await pdf.getPage(pageIndex);
        const viewport = page.getViewport({ scale: 1.6 });
        const exportCanvas = document.createElement('canvas');
        exportCanvas.width = Math.floor(viewport.width);
        exportCanvas.height = Math.floor(viewport.height);
        const context = exportCanvas.getContext('2d');
        if (!context) throw new Error('Could not prepare the highlighted PDF. Please try again.');
        await page.render({ canvas: exportCanvas, canvasContext: context, viewport }).promise;
        context.save();
        context.globalAlpha = 0.38;
        context.fillStyle = '#fde047';
        (highlights[pageIndex] || []).forEach((mark) => {
          context.fillRect(mark.x * exportCanvas.width, mark.y * exportCanvas.height, mark.width * exportCanvas.width, mark.height * exportCanvas.height);
        });
        context.restore();
        const orientation: 'landscape' | 'portrait' = exportCanvas.width > exportCanvas.height ? 'landscape' : 'portrait';
        if (!output) {
          output = new jsPDF({ orientation, unit: 'px', format: [exportCanvas.width, exportCanvas.height], hotfixes: ['px_scaling'] });
        } else {
          output.addPage([exportCanvas.width, exportCanvas.height], orientation);
        }
        output.addImage(exportCanvas.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, exportCanvas.width, exportCanvas.height);
      }
      output?.save(`${fileLabelFrom(url, fileName).replace(/\.pdf$/i, '') || title}-highlighted.pdf`);
    } catch (exportFailure) {
      setExportError(exportFailure instanceof Error ? exportFailure.message : 'Could not download the highlighted PDF. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  if (status === 'browser') {
    return <iframe src={url} title={title} onLoad={onReady} className="h-[72vh] min-h-[400px] w-full rounded-xl border border-background-300 bg-white" />;
  }

  if (status === 'loading') {
    return (
      <div className="grid min-h-[420px] place-items-center rounded-xl border border-background-300 bg-white text-sm font-semibold text-foreground-500 shadow-sm">
        <span className="inline-flex items-center gap-2"><AppIcon className="ri-loader-4-line animate-spin" />Loading PDF preview...</span>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-bold">Could not show this PDF inline.</p>
        <p className="mt-1 text-xs">{error || fileLabelFrom(url, fileName)}</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-background-300 bg-background-100 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-background-300 bg-white px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-bold text-foreground-900">{fileLabelFrom(url, fileName) || title}</p>
          <p className="text-xs text-foreground-500">Page {pageNumber} of {pageCount || 1}</p>
        </div>
        <div className={fitWidth ? 'flex flex-wrap items-center gap-2' : 'flex items-center gap-2'}>
          {readingPreferences && <div className="inline-flex gap-1" role="group" aria-label="PDF reading view">
            <button type="button" aria-pressed={!textView} onClick={() => setTextView(false)} className="h-9 rounded-lg border border-background-300 bg-white px-3 text-xs font-bold text-foreground-700">Original PDF</button>
            <button type="button" aria-pressed={textView} onClick={() => setTextView(true)} className="h-9 rounded-lg border border-blue-300 bg-blue-50 px-3 text-xs font-bold text-blue-800">Text reading view</button>
          </div>}
          <button
            type="button"
            onClick={() => setMarkerActive((value) => !value)}
            disabled={textView}
            aria-pressed={markerActive}
            className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-40 ${markerActive ? 'border-amber-400 bg-amber-300 text-amber-950' : 'border-background-300 bg-white text-foreground-700 hover:bg-background-50'}`}
          >
            <AppIcon className="ri-mark-pen-line" /> Marker
          </button>
          <button type="button" onClick={() => void readCurrentPage()} className={`group relative grid h-9 w-9 place-items-center rounded-lg border ${speaking ? 'border-violet-500 bg-violet-600 text-white' : 'border-background-300 bg-white text-foreground-700 hover:bg-background-50'}`} aria-label={speaking ? 'Stop reading page' : 'Read page aloud'} aria-pressed={speaking}>
            <Mic size={18} aria-hidden="true" />
            <span role="tooltip" className="pointer-events-none absolute top-full left-1/2 z-30 mt-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-900 px-3 py-2 text-xs font-medium text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
              {speaking ? 'Stop narration' : 'Narration: read this page aloud'}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setScale((value) => Math.max(0.75, value - 0.25))}
            className="grid h-9 w-9 place-items-center rounded-lg border border-background-300 bg-white text-foreground-700 hover:bg-background-50 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Zoom out"
            disabled={textView || (fitWidth && scale <= 0.75)}
          >
            <AppIcon className="ri-subtract-line" />
          </button>
          <button
            type="button"
            onClick={() => setScale((value) => Math.min(2.5, value + 0.25))}
            className="grid h-9 w-9 place-items-center rounded-lg border border-background-300 bg-white text-foreground-700 hover:bg-background-50 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Zoom in"
            disabled={textView || (fitWidth && scale >= 2.5)}
          >
            <AppIcon className="ri-add-line" />
          </button>
          {fitWidth && <>
            <output aria-label="PDF zoom level" className="text-xs font-semibold tabular-nums">{Math.round(scale / 1.25 * 100)}%</output>
            <button type="button" disabled={textView} onClick={() => setScale(1.25)} className="h-9 rounded-lg border border-background-300 bg-white px-3 text-xs font-bold text-foreground-700 disabled:cursor-not-allowed disabled:opacity-40">Fit width</button>
          </>}
          <button
            type="button"
            onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
            disabled={pageNumber <= 1}
            className="grid h-9 w-9 place-items-center rounded-lg border border-background-300 bg-white text-foreground-700 hover:bg-background-50 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Previous page"
          >
            <AppIcon className="ri-arrow-left-s-line" />
          </button>
          <button
            type="button"
            onClick={() => setPageNumber((value) => Math.min(pageCount || 1, value + 1))}
            disabled={pageNumber >= (pageCount || 1)}
            className="grid h-9 w-9 place-items-center rounded-lg border border-background-300 bg-white text-foreground-700 hover:bg-background-50 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Next page"
          >
            <AppIcon className="ri-arrow-right-s-line" />
          </button>
          {!textView && <button type="button" onClick={saveHighlights} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 text-xs font-bold text-emerald-700">
            <AppIcon className="ri-save-line" /> {annotationsSaved ? 'Saved' : 'Save marks'}
          </button>}
          {allowAnnotatedDownload && !textView && (
            <button type="button" onClick={() => void downloadAnnotatedPdf()} disabled={exporting} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-xs font-bold text-white disabled:opacity-50">
              <AppIcon className={exporting ? 'ri-loader-4-line animate-spin' : 'ri-download-2-line'} /> {exporting ? 'Preparing…' : 'Download highlighted PDF'}
            </button>
          )}
        </div>
      </div>
      {textView && <p className="border-b border-background-200 bg-white px-4 py-2 text-xs text-foreground-600">Use the reading tools above to adjust and highlight this page. Save changes keeps text highlights on this device. Original PDF preserves the page layout, images and marker annotations.</p>}
      {exportError && <p role="alert" className="p-3 text-sm text-red-700">{exportError}</p>}
      {narration.speechError && <p role="alert" className="p-3 text-sm text-red-700">{narration.speechError}</p>}
      {readingPreferences && pdf && <PdfReadingText pdf={pdf} pageNumber={pageNumber} active={textView} ocrText={ocrText}
        storageKey={readingStorageKey(`${annotationKey || url}:${url}`, 'pdf-text-highlights')} preferences={preferences} />}
      <div hidden={textView}>
      <ReadingSurface
        preferences={preferences}
        label="Original PDF page"
        className="relative max-h-[72vh] overflow-auto p-4"
      >
        <div className={fitWidth ? 'relative mx-auto' : 'relative mx-auto w-fit max-w-full'} style={fitWidth ? { width: `${scale / 1.25 * 100}%` } : undefined}>
          <canvas
            ref={canvasRef}
            className="block max-w-full rounded-lg bg-white shadow-sm"
            style={{ filter: preferences.colourMode === 'monochrome' ? 'grayscale(1) contrast(1.15)' : preferences.colourMode === 'dark' ? 'grayscale(1) invert(1)' : preferences.colourMode === 'cream' ? 'sepia(0.25)' : undefined }}
          />
          <svg
            className={`absolute inset-0 h-full w-full rounded-lg ${markerActive ? 'cursor-crosshair touch-none' : 'pointer-events-none'}`}
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
            aria-label="PDF highlight layer"
            onPointerDown={(event) => {
              if (!markerActive) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              dragStartRef.current = pointFromPointer(event);
              setDraftHighlight({ ...dragStartRef.current, width: 0, height: 0 });
            }}
            onPointerMove={(event) => {
              if (!markerActive || !dragStartRef.current) return;
              setDraftHighlight(updateDraftHighlight(dragStartRef.current, pointFromPointer(event)));
            }}
            onPointerUp={(event) => {
              const start = dragStartRef.current;
              if (!markerActive || !start) return;
              const mark = updateDraftHighlight(start, pointFromPointer(event));
              if (mark.width > 0.003 && mark.height > 0.003) {
                setHighlights((current) => ({ ...current, [pageNumber]: [...(current[pageNumber] || []), mark] }));
                setAnnotationsSaved(false);
              }
              dragStartRef.current = null;
              setDraftHighlight(null);
            }}
          >
            {(highlights[pageNumber] || []).map((mark, index) => (
              <rect key={index} x={mark.x * 1000} y={mark.y * 1000} width={mark.width * 1000} height={mark.height * 1000} fill="#fde047" fillOpacity="0.4" />
            ))}
            {draftHighlight && <rect x={draftHighlight.x * 1000} y={draftHighlight.y * 1000} width={draftHighlight.width * 1000} height={draftHighlight.height * 1000} fill="#fde047" fillOpacity="0.4" stroke="#ca8a04" strokeWidth="2" />}
          </svg>
        </div>
        {(highlights[pageNumber] || []).length > 0 && (
          <button type="button" onClick={() => { setHighlights((current) => ({ ...current, [pageNumber]: [] })); setAnnotationsSaved(false); }} className="mx-auto mt-3 block text-xs font-bold text-red-600 hover:underline">Clear marks on this page</button>
        )}
      </ReadingSurface>
      </div>
    </div>
  );
}

/** A slide deck / document shown inline where that is possible, and an honest
 * "open it instead" card where it is not — an uploaded .pptx can only be
 * previewed by Microsoft's Office viewer, which cannot reach a file that isn't
 * published on the public internet. See @/lib/docEmbed. */
function DocumentEmbed({ url, title, fileName, onReady }: { url: string; title: string; fileName?: string | null; onReady?: () => void }) {
  const embed = resolveDocEmbed(url, undefined, fileName);
  // Say why there is no preview. Returning null left a learner looking at a
  // header, a download card, and nothing in between — with no way to tell a
  // file that cannot be shown from one that failed to load. The file itself is
  // offered by the card below this, so the note only has to explain.
  const unavailable = (reason: string) => (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
      <p className="flex items-center gap-2 text-sm font-bold text-amber-900">
        <AppIcon className="ri-information-line" />
        This file can&apos;t be shown here
      </p>
      <p className="mt-1 text-xs leading-5 text-amber-800">{reason}</p>
    </div>
  );
  if (embed.mode === 'unavailable') return unavailable(embed.reason);
  if (embed.mode === 'deck') return <SlideDeckViewer src={embed.src} title={title} fallback={unavailable} onReady={onReady} />;
  return (
    // A 4:3 box on a wide card is taller than the screen, which puts the top of
    // the document above the fold and the controls far below it. The ratio still
    // drives the shape; the window caps how big it gets.
    <div
      className="rounded-xl overflow-hidden border border-background-300"
      style={{ aspectRatio: '4 / 3', maxHeight: 'calc(100vh - 14rem)' }}
    >
      <iframe title={title} src={embed.src} onLoad={onReady} className="w-full h-full" />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   CONTENT RENDERER — one presentation per content kind.
   ═══════════════════════════════════════════════════════ */
interface EvidenceContext {
  kind: LearnerKind;
  learnerId: string;
  componentId: string;
  onUploaded: (files: EvidenceRecord[]) => void;
  trainingPlanDetails: {
    moduleId: string | null; moduleTitle: string | null;
    weekId: string | null; weekTitle: string | null;
    componentId: string; componentTitle: string; componentType: string | null;
  };
}

/** Content for the component, plus the evidence uploader when one is required.
 * The uploader is appended outside the per-kind renderers so a gated video or
 * reading gets it too — not just the activity/assignment fallback. */
function ComponentContent(props: Parameters<typeof ComponentBody>[0]) {
  return <ComponentBody {...props} />;
}

export function ComponentBody({ component, contentKind, parsed, title, onDuration, onProgress, onPlayingChange, onEnded, onUnsupported, onContentReady, preview = false }: {
  onContentReady?: () => void;
  preview?: boolean;
  component: JourneyComponent;
  contentKind: ReturnType<typeof componentContentKind>;
  parsed: ReturnType<typeof parseVideoUrl> | null;
  title: string;
  onDuration: (d: number) => void;
  onProgress: (t: number) => void;
  onPlayingChange: (playing: boolean) => void;
  onEnded: () => void;
  onUnsupported: () => void;
}) {
  const sessionEnd = component.sessionDateTimeUtc && component.durationMinutes
    ? Date.parse(component.sessionDateTimeUtc) + component.durationMinutes * 60000 : NaN;
  const [clock, setClock] = useState(Date.now);
  useEffect(() => {
    if (!Number.isFinite(sessionEnd) || preview) return;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      clearTimeout(timer);
      const now = Date.now(); setClock(now);
      if (sessionEnd > now) timer = setTimeout(tick, Math.min(60000, sessionEnd - now));
    };
    tick(); window.addEventListener('focus', tick);
    return () => { clearTimeout(timer); window.removeEventListener('focus', tick); };
  }, [sessionEnd, preview]);
  const primaryAudioRef = useRef<HTMLAudioElement>(null);
  if (contentKind === 'video' && parsed) {
    // The video's "Component content" from the Module Builder, shown under the
    // player. Its own field: title, description and video URL are unchanged.
    const lessonContent = component.lessonContentHtml?.trim() ? component.lessonContentHtml : '';
    return (
      <>
        <div className="rounded-2xl overflow-hidden bg-black shadow-sm ring-1 ring-background-300">
          <div className="relative w-full mx-auto" style={{ aspectRatio: '16 / 9', maxHeight: 'calc(100vh - 14rem)' }}>
            <VideoPlayer parsed={parsed} title={title} onDuration={onDuration} onProgress={onProgress} onPlayingChange={onPlayingChange} onEnded={onEnded} onUnsupported={onUnsupported} />
          </div>
        </div>
        {lessonContent && (
          <section aria-label="Lesson content" className="mt-4 rounded-2xl border border-background-300 bg-white p-5 text-sm leading-relaxed text-foreground-800">
            <AuthoredContent value={lessonContent} />
          </section>
        )}
      </>
    );
  }

  if (contentKind === 'audio') {
    const audioSource = component.audioUrl ? directAudioSource(component.audioUrl, component.fileName) : null;
    return (
      <div className="rounded-2xl border border-background-300 bg-gradient-to-br from-violet-50 to-background-50 p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="w-11 h-11 rounded-xl bg-violet-100 text-violet-600 flex items-center justify-center"><AppIcon className="ri-headphone-line text-xl" /></span>
          <div><p className="text-sm font-semibold text-foreground-900">{title}</p><p className="text-xs text-foreground-400">Listen, then finish and reflect below.</p></div>
        </div>
        {audioSource ? (
          <audio
            controls
            preload="metadata"
            className="w-full"
            ref={primaryAudioRef}
            src={audioSource}
            onPlaying={() => onPlayingChange(true)}
            onWaiting={() => onPlayingChange(false)}
            onSeeking={() => onPlayingChange(false)}
            onEmptied={() => onPlayingChange(false)}
            onError={() => onPlayingChange(false)}
            onPause={() => onPlayingChange(false)}
            onEnded={() => { onPlayingChange(false); onEnded(); }}
          >Your browser does not support audio playback.</audio>
        ) : component.audioUrl ? (
          <>
            {/* Not a direct media file (e.g. a podcast listening page) — fetch
                and display the page itself in the LMS rather than only linking
                out. Some sites block embedding (X-Frame-Options), so the "open
                in a new tab" link below is always shown, not just a fallback. */}
            <div
              className="rounded-xl overflow-hidden border border-background-300 bg-white"
              style={{ aspectRatio: '16 / 9', maxHeight: 'calc(100vh - 16rem)' }}
            >
              <iframe title={title} src={component.audioUrl} className="w-full h-full" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
            </div>
            <p className="text-[11px] text-foreground-400 mt-2">If the player above stays blank, this site doesn&apos;t allow embedding — use the link below instead.</p>
          </>
        ) : (
          <p className="text-sm text-foreground-500">No audio was set for this podcast. You can still record your reflection below.</p>
        )}
        {component.audioUrl && (
          <a href={proxiedMaterialUrl(component.audioUrl)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-violet-600 hover:text-violet-700"><AppIcon className="ri-external-link-line" />Open in a new tab</a>
        )}
        {audioSource && <AudioYouTubeAlternative url={component.alternativeYouTubeUrl} onLeave={() => {
          primaryAudioRef.current?.pause();
          onPlayingChange(false);
        }} />}
        <ExtraComponentFiles component={component} title={title} primaryUrl={component.audioUrl} audio />
      </div>
    );
  }

  if (contentKind === 'reading') {
    return (
      <div className="rounded-2xl border border-background-300 bg-white p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="w-11 h-11 rounded-xl bg-blue-100 text-blue-600 flex items-center justify-center"><AppIcon className="ri-book-open-line text-xl" /></span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground-900">{title}</p>
            <p className="text-xs text-foreground-400">Read the material, then finish and reflect below.</p>
          </div>
        </div>
        <AccessibleReadingMaterial key={`${component.componentId}:${component.resourceUrl || ''}:${component.contentHtml || ''}`} component={component} title={title} onReady={onContentReady} />
        {component.audioUrl && (
          <div className="mt-4 pt-4 border-t border-background-200">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400 mb-2">Audio version</p>
            <audio controls preload="metadata" className="w-full" src={proxiedMaterialUrl(component.audioUrl)} />
          </div>
        )}
      </div>
    );
  }

  if (contentKind === 'slides') {
    return (
      <div className="rounded-2xl border border-background-300 bg-white p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="w-11 h-11 rounded-xl bg-orange-100 text-orange-600 flex items-center justify-center"><AppIcon className="ri-slideshow-line text-xl" /></span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground-900">{title}</p>
            <p className="text-xs text-foreground-400">Review the slide deck, then finish and reflect below.</p>
          </div>
          {/* The author said this deck may be taken away; the inline viewer is
              not the only way to read it. */}
          {component.downloadAllowed && component.resourceUrl && (
            <DownloadFileButton
              url={component.resourceUrl}
              fileName={component.fileName}
              label="Download deck"
            />
          )}
        </div>
        {component.resourceUrl ? (
          <InlineAttachmentPreview url={component.resourceUrl} title={title} fileName={component.fileName} onReady={onContentReady} />
        ) : (
          <p className="text-sm text-foreground-500">{component.fileName ? <>Slide deck: <span className="font-semibold text-foreground-700">{component.fileName}</span>. </> : ''}Review your slide deck for this week, then record your reflection below.</p>
        )}
        <ExtraComponentFiles component={component} title={title} primaryUrl={component.resourceUrl} downloadAllowed={Boolean(component.downloadAllowed)} />
      </div>
    );
  }

  if ((component.type || '').trim().toLowerCase().replace(/-/g, '_') === 'live_session') {
    const parsedStart = component.sessionDateTimeUtc ? new Date(component.sessionDateTimeUtc) : null;
    const validStart = parsedStart && !Number.isNaN(parsedStart.getTime()) ? parsedStart : null;
    const ended = Boolean(validStart && component.durationMinutes && clock >= validStart.getTime() + component.durationMinutes * 60000);
    const dateLabel = component.sessionDate
      ? new Date(`${component.sessionDate}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
      : (validStart
        ? formatSystemTimestamp(validStart, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
        : 'Date to be confirmed');
    const timeLabel = component.sessionTime
      || (validStart
        ? `${formatSystemTimestamp(validStart, { hour: '2-digit', minute: '2-digit', hour12: false })} ${systemTimeZoneName(validStart)}`
        : 'Time to be confirmed');

    return (
      <div className="overflow-hidden rounded-2xl border border-primary-200 bg-white shadow-sm">
        <div className="bg-primary-500 p-6 text-white">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-start gap-4">
              <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-white/15 ring-1 ring-white/20">
                <AppIcon className="ri-microsoft-teams-line text-2xl" />
              </span>
              <div className="min-w-0">
                <p className="text-[10px] font-black uppercase tracking-[0.16em] text-white/70">Microsoft Teams live session</p>
                <p className="mt-1 truncate text-base font-heading font-black">{title}</p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-semibold text-white/80">
                  <span><AppIcon className="ri-calendar-line mr-1" />{dateLabel}</span>
                  <span><AppIcon className="ri-time-line mr-1" />{timeLabel}</span>
                  {component.durationMinutes ? <span><AppIcon className="ri-timer-line mr-1" />{component.durationMinutes} min</span> : null}
                </div>
              </div>
            </div>

            {component.liveSessionUrl && !ended && !preview ? (
              <a href={component.liveSessionUrl} target="_blank" rel="noreferrer" className="meeting-join-action inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl px-5 text-[12px] font-black shadow-lg transition-transform hover:-translate-y-0.5">
                <AppIcon className="ri-microsoft-teams-line text-base" />
                Join live session
                <AppIcon className="ri-external-link-line text-xs" />
              </a>
            ) : (
              <button type="button" disabled className="inline-flex min-h-11 shrink-0 cursor-not-allowed items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 text-[11px] font-bold text-white/80">
                <AppIcon className="ri-calendar-todo-line text-base" />
                {preview ? 'Join is disabled in preview' : ended ? 'Session ended' : 'Meeting link not scheduled'}
              </button>
            )}
          </div>
        </div>

        <div className="grid gap-4 p-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <div>
            <p className="text-[10px] font-black uppercase tracking-wider text-foreground-400">Before the session</p>
            <p className="mt-1 text-[12px] leading-5 text-foreground-600">
              {component.reflectionPrompt || 'Join on time and complete your reflection after the live session.'}
            </p>
          </div>
          {!component.liveSessionUrl && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[10px] font-semibold text-amber-800">
              Contact your tutor if you expect a meeting link here.
            </p>
          )}
        </div>
      </div>
    );
  }

  if (contentKind === 'reflection') {
    return (
      <div className="rounded-2xl border border-background-300 bg-gradient-to-br from-purple-50 to-background-50 p-6">
        <div className="flex items-center gap-3 mb-3">
          <span className="w-11 h-11 rounded-xl bg-purple-100 text-purple-600 flex items-center justify-center"><AppIcon className="ri-brain-line text-xl" /></span>
          <div><p className="text-sm font-semibold text-foreground-900">{title}</p><p className="text-xs text-foreground-400">Read the prompt, then capture your reflection below.</p></div>
        </div>
        {component.reflectionPrompt && (
          <div className="rounded-xl bg-white border border-purple-100 p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-purple-500 mb-1">Reflection prompt</p>
            <p className="text-sm text-foreground-700 leading-relaxed whitespace-pre-line">{component.reflectionPrompt}</p>
          </div>
        )}
      </div>
    );
  }

  /* resource / activity / evidence / live session / recording */
  return (
    <div className="rounded-2xl border border-background-300 bg-white p-6">
      <div className="flex items-center gap-3 mb-3">
        <span className="w-11 h-11 rounded-xl bg-emerald-100 text-emerald-600 flex items-center justify-center"><AppIcon className="ri-task-line text-xl" /></span>
        <div><p className="text-sm font-semibold text-foreground-900">{title}</p><p className="text-xs text-foreground-400">Complete this activity, then finish and reflect below.</p></div>
      </div>
      {component.reflectionPrompt && (
        <div className="rounded-xl bg-background-50 border border-background-200 p-4 mb-3">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400 mb-1">What to do</p>
          <p className="text-sm text-foreground-700 leading-relaxed whitespace-pre-line">{component.reflectionPrompt}</p>
        </div>
      )}
      {component.resourceUrl && (
        <div className="space-y-3">
          <InlineAttachmentPreview url={component.resourceUrl} title={title} fileName={component.fileName} />
        </div>
      )}
    </div>
  );
}

/* One line of the completion-criteria checklist. */
function CriterionRow({ met, label, hint, showHint }: { met: boolean; label: string; hint?: string; showHint?: boolean }) {
  return (
    <li className="flex items-start gap-2">
      <AppIcon className={`mt-0.5 text-sm shrink-0 ${met ? 'ri-checkbox-circle-fill text-emerald-600' : 'ri-close-circle-line text-amber-600'}`} />
      <span className="min-w-0">
        <span className={`block text-[13px] font-medium ${met ? 'text-foreground-600' : 'text-foreground-800'}`}>{label}</span>
        {hint && showHint && <span className="block text-[11px] text-foreground-400 mt-0.5">{hint}</span>}
      </span>
    </li>
  );
}
