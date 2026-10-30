import { archiveTimingDraft, timingSessionExpired } from '@/lib/timingRecovery';
import { useManualTimeConfirmation } from '@/hooks/useManualTimeConfirmation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { observeRewardInterval, flushRewardObservations, clearRewardObservations } from '@/lib/rewardTracking';
import { createPortal } from 'react-dom';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { QuizQuestionImage } from '@/components/feature/QuizQuestionImage';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { EmptyState } from '@/pages/users/components/ui';
import {
  completeQuizReading, fetchQuiz, generateQuizReading, submitQuizAttempt,
  type Quiz, type QuizQuestion, type QuizAnswerValue, type QuizAttemptResult, type QuizReading,
} from '@/api/quizzes';
import {
  fetchLearnerDetail,
  type LearnerDetail,
  type LearnerKsbItem,
  type LearnerKind,
  type LearnerQuizAttempt,
  type LearnerQuizQuestionResult,
} from '@/api/learnerDetail';
import { completedComponentIds } from '@/utils/learnerJourney';
import { PreviousLearningNotice } from '@/pages/learner/my-learning/PreviousLearningNotice';
import { placeActivity } from '@/pages/learner/video-watch/weekPreview';
import { ActivitySidebar } from '@/pages/learner/video-watch/ActivitySidebar';
import { componentRoute } from '@/pages/learner/video-watch/componentRoute';
import { ReflectionWindow } from '@/components/feature/ReflectionWindow';
import { formatClock } from "@/components/feature/ReflectionWindow.helpers";
import { ReflectionChoicePopup } from '@/components/feature/ReflectionChoicePopup';
import { rememberLearner } from '@/hooks/useMyLearner';
import { useLearnerWorkspaceAccess } from '@/hooks/useLearnerWorkspaceAccess';
import { ReadOnlyLearnerNotice } from '@/components/feature/ReadOnlyLearnerNotice';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { startTimeTracking, checkpointTimeTracking, type TimeTrackingSession } from '@/api/timeTracking';
import { ComponentAccessNotice } from '@/components/feature/ComponentAccessNotice';
import { useComponentAccessWindow } from '@/hooks/useComponentAccessWindow';
import { CompletionTimeDialog } from '@/components/feature/CompletionTimeDialog';
import { RetakeTimeDialog, type RetakeTimeChoice } from '@/components/feature/RetakeTimeDialog';
import { CompletionValidationError, type WorkingRuleReason } from '@/lib/completionValidation';

const learnerNav = roleNavMap.learner;

type Phase = 'intro' | 'quiz' | 'reflect' | 'results';

function latestQuizAttempt(attempts: LearnerQuizAttempt[], quizId: string | undefined, componentId?: string | null) {
  return attempts
    .filter((attempt) => String(attempt.quizId) === String(quizId)
      || (Boolean(componentId) && attempt.componentId === componentId))
    .sort((left, right) => {
      const submittedDifference = Date.parse(right.submittedAt || '') - Date.parse(left.submittedAt || '');
      if (Number.isFinite(submittedDifference) && submittedDifference !== 0) return submittedDifference;
      return Number(right.attempt || 0) - Number(left.attempt || 0);
    })[0] || null;
}

function answerText(question: QuizQuestion, ids: number | number[] | null | undefined) {
  if (ids == null) return null;
  const answerIds = Array.isArray(ids) ? ids : [ids];
  const labels = answerIds
    .map((answerId) => question.answers.find((answer) => answer.id === answerId))
    .map((answer) => answer?.text || answer?.left || answer?.label)
    .filter((label): label is string => Boolean(label));
  if (!answerIds.length) return null;
  return labels.length === answerIds.length ? labels.join(question.type === 'ordering' ? ' -> ' : ', ') : 'Answer text unavailable for this historical attempt';
}

function historicalCorrectAnswer(question: QuizQuestion, stored: LearnerQuizQuestionResult) {
  if ('correctText' in stored) return stored.correctText;
  const byId = answerText(question, stored.correctAnswerId);
  if (byId) return byId;
  if (question.type === 'fill_gap' || question.type === 'keywords') {
    const labels = question.answers.map((answer) => answer.text).filter((label): label is string => Boolean(label));
    return labels.length ? labels.join(' / ') : null;
  }
  if (question.type === 'matching' || question.type === 'image_matching') {
    const pairs = question.answers.map((answer) => answer.text).filter((label): label is string => Boolean(label));
    return pairs.length ? pairs.join('; ') : null;
  }
  return null;
}

function durationSeconds(value: string | null | undefined) {
  const parts = String(value || '').split(':').map(Number);
  if (!parts.length || parts.some(part => !Number.isFinite(part) || part < 0)) return 0;
  if (parts.length === 3) return (parts[0] * 3600) + (parts[1] * 60) + parts[2];
  if (parts.length === 2) return (parts[0] * 60) + parts[1];
  return parts[0];
}

/** Rebuild the full result view from the slim attempt stored on the learner. */
function resultFromStoredAttempt(quiz: Quiz, attempt: LearnerQuizAttempt): QuizAttemptResult {
  const breakdown = (attempt.questions || []).map((stored) => {
    const question = String(attempt.quizId) === String(quiz.id)
      ? quiz.questions.find(q => String(q.id) === String(stored.questionId)) : undefined;
    return {
      questionId: stored.questionId,
      questionText: stored.questionText ?? question?.text ?? 'Question no longer available',
      imageUrl: stored.imageUrl ?? question?.imageUrl,
      type: (stored.type ?? question?.type ?? 'single_choice') as QuizQuestion['type'],
      points: stored.possible ?? question?.points ?? 0,
      earned: Number(stored.earned || 0),
      possible: stored.possible ?? question?.points ?? 0,
      correct: Boolean(stored.correct),
      chosenAnswer: 'chosenText' in stored ? stored.chosenText ?? null : question ? answerText(question, stored.chosenAnswerId) : null,
      correctAnswer: 'correctText' in stored ? stored.correctText ?? null : question ? historicalCorrectAnswer(question, stored) ?? null : null,
    };
  });
  const earned = breakdown.reduce((total, question) => total + question.earned, 0);
  const possible = breakdown.reduce((total, question) => total + question.possible, 0);
  const achievedScore = attempt.achievedScore ?? breakdown.filter((question) => question.correct).length;
  const totalScore = attempt.totalScore ?? breakdown.length;
  return {
    attempt: {
      kind: 'quiz',
      attempt: attempt.attempt || 1,
      grade: attempt.grade,
      achievedScore,
      totalScore,
      passed: attempt.passed,
      quizId: attempt.quizId,
      questions: attempt.questions || [],
      startedAt: attempt.startedAt,
      submittedAt: attempt.submittedAt,
      timeTaken: attempt.timeTaken || '00:00',
      timeTrackingSource: attempt.timeTrackingSource || 'stored',
      claimedSeconds: attempt.claimedSeconds || 0,
      serverSessionSeconds: attempt.verifiedSeconds || 0,
      verifiedSeconds: attempt.verifiedSeconds || 0,
    },
    breakdown,
    earned,
    possible,
    grade: attempt.grade,
    achievedScore,
    totalScore,
    passed: attempt.passed,
    timeTaken: attempt.timeTaken || '00:00',
    quizName: quiz.title,
  };
}

export default function QuizTakePage() {
  const { kind, id, quizId } = useParams<{ kind: string; id: string; quizId: string }>();
  // Keep sidebar self-view pointing at this learner after the quiz.
  useEffect(() => { rememberLearner(kind, id); }, [kind, id]);
  const [searchParams] = useSearchParams();
  const [detail, setDetail] = useState<LearnerDetail | null>(null);
  const navigate = useNavigate();
  // A staff viewer can reach this URL directly (pasted, or from history) even
  // though the plan rows no longer link here. Sitting the quiz would file an
  // attempt in the learner's name, so they get the read-only panel instead.
  const { canProgress } = useLearnerWorkspaceAccess(id);
  // Scoped to this quiz so the correction dialog can name the learner's own
  // closures. Nothing here gates taking the quiz -- `open` is always true.
  const componentAccess = useComponentAccessWindow(null, quizId);
  const canUseComponent = canProgress && componentAccess.open;
  const moduleTitle = searchParams.get('module');
  const weekTitle = searchParams.get('week');

  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [learnerKsbs, setLearnerKsbs] = useState<LearnerKsbItem[]>([]);
  const [learnerName, setLearnerName] = useState('Learner');
  const [programmeName, setProgrammeName] = useState('Programme not set');
  const [reflectionRequired, setReflectionRequired] = useState(true);
  const [reflectionQuestion, setReflectionQuestion] = useState<string | null>(null);
  const [reflectionChoiceOpen, setReflectionChoiceOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const [phase, setPhase] = useState<Phase>('intro');
  const [current, setCurrent] = useState(0);
  const [answers, setAnswers] = useState<Record<string, QuizAnswerValue>>({});
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [expiredTiming, setExpiredTiming] = useState(false);
  const [recoveringTiming, setRecoveringTiming] = useState(false);
  const [timingNotice, setTimingNotice] = useState('');
  const recoveryInFlight = useRef(false);
  const [timingGeneration, setTimingGeneration] = useState(0);
  useEffect(() => { setExpiredTiming(false); setTimingNotice(''); }, [kind, id, quizId]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Open only after the server refused the Submit click's instant.
  const [correction, setCorrection] = useState<{ reason: WorkingRuleReason | ''; holidayName: string } | null>(null);
  const [correctionError, setCorrectionError] = useState('');
  const lastSubmissionRef = useRef<{
    reflection: { ksbs: string[]; feedback: string; reportedTime: string; timeTakenSeconds?: number };
    skipReflection: boolean;
  } | null>(null);
  const [result, setResult] = useState<QuizAttemptResult | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completionInFlightRef = useRef(false);
  const trackingSessionRef = useRef<TimeTrackingSession | null>(null);
  const trackingPromiseRef = useRef<Promise<TimeTrackingSession> | null>(null);
  const elapsedRef = useRef(elapsedSeconds);
  elapsedRef.current = elapsedSeconds;
  const [timingSyncError, setTimingSyncError] = useState<string | null>(null);

  useEffect(() => {
    if (!quizId || !kind || !id || (phase !== 'quiz' && phase !== 'reflect')) return;
    let cancelled = false;
    let busy = false;
    const sync = async () => {
      const session = trackingSessionRef.current;
      if (!session?.persistent || busy || completionInFlightRef.current) return;
      busy = true;
      try {
        const restored = await checkpointTimeTracking(session, 'quiz', quizId, kind, id, elapsedRef.current);
        if (cancelled || trackingSessionRef.current?.sessionId !== session.sessionId) return;
        trackingSessionRef.current = restored;
        if (restored.rewardTracking) await flushRewardObservations(restored, 'quiz', quizId);
        if (!cancelled) setTimingSyncError(null);
      } catch (error) {
        if (!cancelled) setTimingSyncError(`Your time could not sync. Keep this page open; your answers are kept. ${error instanceof Error ? error.message : 'Retry when connected.'}`);
      }
      finally { busy = false; }
    };
    const interval = setInterval(() => void sync(), 60000);
    window.addEventListener('online', sync);
    return () => { cancelled = true; clearInterval(interval); window.removeEventListener('online', sync); };
  }, [quizId, kind, id, phase]);

  useEffect(() => {
    if (!quizId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setPhase('intro');
    setReflectionChoiceOpen(false);
    setAnswers({});
    setResult(null);
    fetchQuiz(Number(quizId), id)
      .then((data) => { if (!cancelled) setQuiz(data); })
      .catch((e) => { if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Could not load quiz'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [kind, id, quizId, loadAttempt]);

  // Load the learner's KSBs (from their Active_users row) for the reflection window.
  useEffect(() => {
    if (kind !== 'commercial' && kind !== 'apprenticeship') return;
    if (!id) return;
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    fetchLearnerDetail(kind as LearnerKind, id, { quizView: true })
      .then((d) => {
        if (!cancelled) {
          // Kept whole: the sidebar is built from the learner's plan, not just
          // from the KSBs this page used to take out of it.
          setDetail(d);
          setLearnerKsbs(d.ksbs || []);
          setLearnerName(d.name || 'Learner');
          setProgrammeName(d.programme || 'Programme not set');
          const quizComponent = (d.components || []).find(component =>
            String(component.quizMeta?.quizId ?? '') === String(quizId),
          );
          setReflectionRequired(quizComponent?.reflectionRequired !== false);
          setReflectionQuestion(quizComponent?.reflectionQuestion || null);
        }
      })
      .catch(error => { if (!cancelled) setDetailError(error instanceof Error ? error.message : 'Could not load your quiz requirements.'); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [kind, id, quizId, loadAttempt]);

  useEffect(() => {
    if (phase !== 'quiz' || reflectionChoiceOpen || !componentAccess.open) return;
    let last = performance.now();
    timerRef.current = setInterval(() => {
      const now = performance.now();
      if (document.visibilityState === 'visible') {
        setElapsedSeconds((s) => s + 1);
        try { observeRewardInterval(trackingSessionRef.current,Math.max(last,now-1000),now); }
        catch { setSubmitError('Your measured-time draft could not be saved. Keep this page open and retry.'); }
      }
      last = now;
    }, 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [phase, reflectionChoiceOpen, componentAccess.open, timingGeneration]);

  useEffect(() => {
    if (phase !== 'quiz') return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Browsers intentionally show their own message, but setting returnValue
      // is still required by some engines to trigger the confirmation dialog.
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeLeaving);
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving);
  }, [phase]);

  const totalPoints = useMemo(() => (quiz ? quiz.questions.reduce((n, q) => n + q.points, 0) : 0), [quiz]);
  const latestAttempt = useMemo(
    () => {
      const components = (detail?.components || []).filter(component =>
        String(component.quizMeta?.quizId ?? '') === String(quizId)
        && (!moduleTitle || component.module === moduleTitle)
        && (!weekTitle || component.week === weekTitle),
      );
      return latestQuizAttempt(detail?.quizAttempts || [], quizId,
        components.length === 1 ? components[0].componentId : null);
    },
    [detail?.quizAttempts, detail?.components, quizId, moduleTitle, weekTitle],
  );

  // Where this quiz sits in the plan, for the list beside it. A quiz reached
  // from somewhere other than the plan (or one no longer in it) simply has no
  // list — the page still works, which is how it behaved before it had one.
  const completedIds = useMemo(() => completedComponentIds(detail), [detail]);
  const placement = useMemo(
    () => placeActivity(detail, { quizId }, completedIds),
    [detail, quizId, completedIds],
  );
  // Hidden while the quiz is actually being sat: a timed attempt is not the
  // moment to offer a way out of it, and leaving mid-attempt loses the answers.
  const showSidebar = Boolean(placement) && (phase === 'intro' || phase === 'results');

  const [retakeTimeOpen, setRetakeTimeOpen] = useState(false);
  const retakeTimeChoice = useRef<RetakeTimeChoice | undefined>(undefined);
  const startQuiz = () => {
    if (latestAttempt || result) { setRetakeTimeOpen(true); return; }
    retakeTimeChoice.current = undefined;
    beginQuiz();
  };
  const beginQuiz = () => {
    if (!quiz || quiz.unavailableReason || !kind || !id || !canUseComponent || detailLoading || detailError) return;
    setSubmitError(null);
    setReflectionChoiceOpen(false);
    trackingSessionRef.current = null;
    const pending = startTimeTracking(
      'quiz', quiz.id, kind as LearnerKind, id, 'active_quiz',
    );
    trackingPromiseRef.current = pending;
    pending
      .then((session) => { trackingSessionRef.current = session; })
      .catch((error) => setSubmitError(error instanceof Error ? error.message : 'Could not start quiz timing'));
    setElapsedSeconds(0);
    setCurrent(0);
    setAnswers({});
    setResult(null);
    setPhase('quiz');
  };

  const restartTiming = async () => {
    if (recoveryInFlight.current || !quiz || !kind || !id || !canUseComponent) return;
    recoveryInFlight.current = true; setRecoveringTiming(true);
    try {
      archiveTimingDraft(`learner_quiz_timer:v1:${kind}:${id}:${quiz.id}`, trackingSessionRef.current,
        elapsedSeconds, { answers, reflection: lastSubmissionRef.current });
      if (!trackingSessionRef.current) throw new Error('Keep this page open and contact support to restore your time.');
      const session = await checkpointTimeTracking(trackingSessionRef.current, 'quiz', String(quiz.id), kind, id, elapsedSeconds);
      trackingSessionRef.current = session; trackingPromiseRef.current = Promise.resolve(session);
      setElapsedSeconds(value => Math.max(value, session.savedSeconds || 0)); setTimingGeneration(value => value + 1); setExpiredTiming(false); setSubmitError(null);
      setTimingSyncError(null);
      setTimingNotice('Your time has been restored. Your answers are kept. You can finish the quiz.');
    } catch (error) { setSubmitError(error instanceof Error ? error.message : 'Could not restore your time. Your answers are kept.'); }
    finally { recoveryInFlight.current = false; setRecoveringTiming(false); }
  };

  const viewLatestResult = () => {
    if (!quiz || !latestAttempt) return;
    setResult(resultFromStoredAttempt(quiz, latestAttempt));
    setPhase('results');
  };

  const setAnswer = (questionId: number, value: QuizAnswerValue) => {
    setAnswers((prev) => ({ ...prev, [String(questionId)]: value }));
  };

  const goNext = () => setCurrent((c) => Math.min(c + 1, (quiz?.questions.length || 1) - 1));
  const goPrev = () => setCurrent((c) => Math.max(c - 1, 0));

  // A configured reflection is optional. The attempt is persisted only after
  // the learner chooses a path and completes it.
  const handleFinishQuiz = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    setSubmitError(null);
    if (!reflectionRequired) {
      void finalizeSubmit({ ksbs: [], feedback: '', reportedTime: '' });
      return;
    }
    setReflectionChoiceOpen(true);
  };

  // The quiz's time limit as hours, for presetting "Actual time spent (minutes)".
  // `timeUnit` is free text defaulting to "minutes", so it is read the same way
  // the quiz runner's own countdown reads it: seconds when it says so, minutes
  // otherwise. Passing the raw number would preset a 30-minute quiz as 30 hours.
  const quizPlannedHours = quiz?.duration
    ? (quiz.duration * (quiz.timeUnit === 'seconds' ? 1 : 60)) / 3600
    : undefined;

  const finalizeSubmit = async (
    reflection: { ksbs: string[]; feedback: string; reportedTime: string; timeTakenSeconds?: number },
    options: { skipReflection?: boolean; declaredCompletedAt?: string } = {},
  ) => {
    if (!quiz || !kind || !id || completionInFlightRef.current || !canUseComponent) return;
    completionInFlightRef.current = true;
    setSubmitting(true);
    setSubmitError(null);
    // Remembered so a correction re-posts the SAME submission. The answers and
    // the frozen timer go back unchanged, so the server grades the attempt to
    // exactly the score it would have given the refused click -- the learner
    // never retakes anything and nothing they typed is lost.
    lastSubmissionRef.current = { reflection, skipReflection: options.skipReflection === true };
    try {
      let tracking = trackingSessionRef.current || await trackingPromiseRef.current;
      if (!tracking) throw new Error('Quiz timing did not start. Reopen the quiz and try again.');
      if (tracking.persistent) {
        tracking = await checkpointTimeTracking(tracking, 'quiz', String(quiz.id), kind, id, elapsedSeconds)
          .catch(error => { setExpiredTiming(true); throw error; });
        trackingSessionRef.current = tracking;
      }
      if (tracking.rewardTracking) await flushRewardObservations(tracking,'quiz',String(quiz.id), Boolean(tracking.persistent));
      const res = await submitQuizAttempt(quiz.id, kind as 'commercial' | 'apprenticeship', id, {
        answers,
        timeTakenSeconds: reflection.timeTakenSeconds ?? elapsedSeconds,
        startedAt: tracking.startedAt,
        trackingToken: tracking.trackingToken,
        module: moduleTitle,
        week: weekTitle,
        ksbs: reflection.ksbs,
        feedback: reflection.feedback,
        reportedTime: reflection.reportedTime,
        skipReflection: options.skipReflection === true,
        declaredCompletedAt: options.declaredCompletedAt,
        retakeTimeChoice: retakeTimeChoice.current,
      });
      setReflectionChoiceOpen(false);
      setResult(res);
      clearRewardObservations(tracking);
      setPhase('results');
      setCorrection(null);
      setCorrectionError('');
    } catch (e) {
      // A working-rules refusal wrote nothing: no attempt, no progress, no
      // OTJH. The dialog collects a working instant and the same submission
      // goes back. A refusal OF the declared instant keeps the dialog open.
      if (e instanceof CompletionValidationError) {
        if (options.declaredCompletedAt) setCorrectionError(e.message);
        else { setCorrection({ reason: e.reason, holidayName: e.holidayName }); setCorrectionError(''); }
        setSubmitError(null);
        return;
      }
      if (timingSessionExpired(e)) setExpiredTiming(true);
      setSubmitError(e instanceof Error ? e.message : 'Could not submit quiz');
    } finally {
      completionInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const submitDeclared = (declaredAt: string) => {
    const last = lastSubmissionRef.current;
    if (!last) return;
    void finalizeSubmit(last.reflection, {
      skipReflection: last.skipReflection,
      declaredCompletedAt: declaredAt,
    });
  };

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle={quiz?.title || 'Quiz'}
      pageSubtitle={[moduleTitle, weekTitle].filter(Boolean).join(' · ')}
      userName="Learner"
      userRole="Learner"
    >
      <div className={`p-3 md:p-6 ${phase === 'reflect' ? 'max-w-none' : showSidebar ? 'max-w-7xl' : 'max-w-5xl'} mx-auto`}>
        <div className={showSidebar ? 'grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-6 items-start' : ''}>
        <div className="min-w-0">
        {expiredTiming && <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4">
          <p>Your time needs to sync before you finish. Your previous time and answers are kept.</p>
          <button type="button" disabled={recoveringTiming} onClick={restartTiming} className="mt-2 font-semibold underline">{recoveringTiming ? 'Restoring...' : 'Restore saved time'}</button>
        </div>}
        {timingSyncError && <p role="status">{timingSyncError}</p>}
        {timingNotice && <p role="status">{timingNotice}</p>}
        {loading || detailLoading ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-5"><RowsSkeleton rows={4} avatar={false} /></div>
        ) : loadError || detailError || !quiz ? (
          <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6"><EmptyState text={loadError || detailError || 'Quiz not found.'} /><button type="button" onClick={() => setLoadAttempt(value => value + 1)} className="mt-3 text-sm font-semibold underline">Retry quiz</button></div>
        ) : !canProgress ? (
          <ReadOnlyLearnerNotice what="sit their own quizzes" onBack={() => navigate(-1)} />
        ) : !componentAccess.open ? (
          <ComponentAccessNotice onBack={() => navigate(-1)} />
        ) : phase === 'intro' ? (
          <>
          <PreviousLearningNotice learning={placement?.weekComponents.find(component => String(component.quizMeta?.quizId) === String(quizId))?.previousLearning} kind={kind} learnerId={id} />
          <IntroScreen
            quiz={quiz}
            totalPoints={totalPoints}
            latestAttempt={latestAttempt}
            onStart={startQuiz}
            onViewResult={viewLatestResult}
            onBack={() => navigate(-1)}
          />
          </>
        ) : phase === 'quiz' ? (
          <QuizScreen
            quiz={quiz}
            current={current}
            answers={answers}
            elapsedSeconds={elapsedSeconds}
            submitting={submitting}
            submitError={submitError}
            onAnswer={setAnswer}
            onNext={goNext}
            onPrev={goPrev}
            onJump={setCurrent}
            onSubmit={handleFinishQuiz}
          />
        ) : phase === 'reflect' ? (
          <ReflectionWindow
            noun="quiz"
            plannedTimeLabel={quiz.duration ? `${quiz.duration} ${quiz.timeUnit || 'min'}` : ''}
            plannedHours={quizPlannedHours}
            actualTimeUnit="minutes"
            learnerKsbs={learnerKsbs}
            autoKsbs={detail?.components?.find(component => String(component.quizMeta?.quizId ?? '') === String(quizId)
              && (!moduleTitle || component.module === moduleTitle)
              && (!weekTitle || component.week === weekTitle))?.ksbMappings ?? []}
            elapsedSeconds={elapsedSeconds}
            submitting={submitting}
            submitError={submitError}
            onSubmit={finalizeSubmit}
            activityTitle={quiz.title}
            weekLabel={weekTitle || ''}
            moduleLabel={moduleTitle || ''}
            learnerName={learnerName}
            programmeName={programmeName}
            learnerKind={kind as LearnerKind}
            learnerId={id}
            evidenceSectionRef={`quiz-${quiz.id}`}
            reflectionQuestion={reflectionQuestion}
            onClose={() => setPhase('quiz')}
          />
        ) : (
          result && (
            <ResultsScreen
              quiz={quiz}
              result={result}
              kind={kind as 'commercial' | 'apprenticeship'}
              learnerId={id || ''}
              onBack={() => navigate(-1)}
              onRetake={startQuiz}
            />
          )
        )}
        </div>

        {showSidebar && placement && (
          <ActivitySidebar
            weekComponents={placement.weekComponents}
            weekTitle={placement.weekTitle}
            moduleTitle={placement.moduleTitle}
            weeks={placement.weeks}
            completedIds={completedIds}
            kind={kind}
            id={id}
            currentQuizId={Number(quizId)}
            routeFor={(component, week) => componentRoute(
              kind, id, component, placement.moduleTitle, week,
            )}
            accessOpen={componentAccess.open}
          />
        )}
        {retakeTimeOpen && <RetakeTimeDialog onClose={() => setRetakeTimeOpen(false)} onChoose={selection => {
          retakeTimeChoice.current = selection; setRetakeTimeOpen(false); beginQuiz();
        }} />}
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
        {reflectionChoiceOpen && (
          <ReflectionChoicePopup
            noun="quiz"
            submitting={submitting}
            error={submitError}
            onCancel={() => setReflectionChoiceOpen(false)}
            onRecoverTiming={expiredTiming ? restartTiming : undefined}
            recoveringTiming={recoveringTiming}
            onAddReflection={() => { setReflectionChoiceOpen(false); setPhase('reflect'); }}
            onFinishWithoutReflection={() => void finalizeSubmit(
              { ksbs: [], feedback: '', reportedTime: '' },
              { skipReflection: true },
            )}
          />
        )}
        </div>
      </div>
    </WorkspaceShell>
  );
}

/* ═══════════════════════════════════════════════════════
   INTRO
   ═══════════════════════════════════════════════════════ */
function IntroScreen({ quiz, totalPoints, latestAttempt, onStart, onViewResult, onBack }: {
  quiz: Quiz;
  totalPoints: number;
  latestAttempt: LearnerQuizAttempt | null;
  onStart: () => void;
  onViewResult: () => void;
  onBack: () => void;
}) {
  return (
    <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6 md:p-8 card-premium">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-12 h-12 rounded-xl bg-amber-50 flex items-center justify-center shrink-0">
          <AppIcon className="ri-questionnaire-line text-amber-600 text-xl" />
        </div>
        <div>
          <h1 className="text-lg font-heading font-bold text-foreground-900">{quiz.title}</h1>
          <p className="text-sm text-foreground-400">{quiz.questions.length} questions · {totalPoints} points</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <StatTile icon="ri-time-line" label="Time limit" value={quiz.duration ? `${quiz.duration} ${quiz.timeUnit || 'min'}` : 'No limit'} />
        <StatTile icon="ri-checkbox-circle-line" label="Passing grade" value={quiz.passingGrade != null ? `${quiz.passingGrade}%` : '—'} />
        <StatTile icon="ri-question-line" label="Questions" value={String(quiz.questions.length)} />
        <StatTile icon="ri-medal-line" label="Total points" value={String(totalPoints)} />
      </div>

      {latestAttempt ? (
        <div className="grid gap-3 sm:grid-cols-2 mb-6">
          <div className={`rounded-xl border p-4 ${latestAttempt.passed ? 'border-emerald-200 bg-emerald-50/60' : 'border-red-200 bg-red-50/60'}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Latest result</p>
                <p className={`mt-1 text-lg font-heading font-bold ${latestAttempt.passed ? 'text-emerald-800' : 'text-red-800'}`}>
                  {Math.round(latestAttempt.grade * 100)}% · {latestAttempt.passed ? 'Passed' : 'Not passed'}
                </p>
                <p className="mt-1 text-xs text-foreground-500">
                  Attempt {latestAttempt.attempt || 1}
                  {latestAttempt.achievedScore != null && latestAttempt.totalScore != null
                    ? ` · ${latestAttempt.achievedScore}/${latestAttempt.totalScore} correct`
                    : ''}
                  {latestAttempt.timeTaken ? ` · ${latestAttempt.timeTaken}` : ''}
                </p>
              </div>
              <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${latestAttempt.passed ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                <AppIcon className={latestAttempt.passed ? 'ri-trophy-line' : 'ri-close-circle-line'} />
              </span>
            </div>
            <button type="button" onClick={onViewResult} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-foreground-200 bg-background-50 px-4 py-2.5 text-sm font-semibold text-foreground-800 transition-colors hover:bg-background-100">
              <AppIcon className="ri-file-list-3-line" /> View Result Details
            </button>
          </div>

          <div className="rounded-xl border border-primary-200 bg-primary-50/60 p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-primary-700">Another attempt</p>
            <p className="mt-1 text-base font-heading font-bold text-foreground-900">Ready to try again?</p>
            <p className="mt-1 text-xs leading-5 text-foreground-500">Your previous result will stay saved when you start a new attempt.</p>
            <button type="button" onClick={onStart} disabled={quiz.questions.length === 0 || Boolean(quiz.unavailableReason)} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50">
              <AppIcon className="ri-refresh-line" /> Retake Quiz
            </button>
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <button onClick={onBack} className="px-4 py-2.5 rounded-xl text-sm font-medium text-foreground-600 hover:bg-background-100 transition-colors cursor-pointer">
          Back
        </button>
        {!latestAttempt && <button
          onClick={onStart}
          disabled={quiz.questions.length === 0 || Boolean(quiz.unavailableReason)}
          className="flex-1 px-4 py-2.5 rounded-xl text-sm font-semibold bg-primary-600 text-white hover:bg-primary-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
        >
          <AppIcon className="ri-play-fill" /> Start Quiz
        </button>}
      </div>
      {quiz.unavailableReason && <p role="alert" className="text-sm text-red-700 mt-3">{quiz.unavailableReason}</p>}
      {quiz.questions.length === 0 && (
        <p className="text-xs text-foreground-400 mt-3">This quiz has no questions yet.</p>
      )}
    </div>
  );
}

function StatTile({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="coach-metric-card">
      <AppIcon className={`${icon} text-primary-500 text-base`} />
      <p className="text-sm font-semibold text-foreground-900 mt-1">{value}</p>
      <p className="text-[11px] text-foreground-400">{label}</p>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   QUIZ — timer + navigator + active question
   ═══════════════════════════════════════════════════════ */
function QuizScreen({
  quiz, current, answers, elapsedSeconds, submitting, submitError, onAnswer, onNext, onPrev, onJump, onSubmit,
}: {
  quiz: Quiz;
  current: number;
  answers: Record<string, QuizAnswerValue>;
  elapsedSeconds: number;
  submitting: boolean;
  submitError: string | null;
  onAnswer: (questionId: number, value: QuizAnswerValue) => void;
  onNext: () => void;
  onPrev: () => void;
  onJump: (i: number) => void;
  onSubmit: () => void;
}) {
  const question = quiz.questions[current];
  const isLast = current === quiz.questions.length - 1;
  const answeredCount = quiz.questions.filter((q) => isAnswered(answers[String(q.id)])).length;

  const timeLimitSeconds = quiz.duration ? quiz.duration * (quiz.timeUnit === 'seconds' ? 1 : 60) : null;
  const timeRemaining = timeLimitSeconds != null ? Math.max(0, timeLimitSeconds - elapsedSeconds) : null;
  const overTime = timeLimitSeconds != null && elapsedSeconds >= timeLimitSeconds;

  useEffect(() => {
    if (overTime) onSubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overTime]);

  return (
    <div className="space-y-4">
      <div role="note" className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <AppIcon className="ri-error-warning-line mt-0.5 shrink-0" />
        <span><strong>Do not refresh or close this page.</strong> Your current answers will be deleted and the quiz will restart.</span>
      </div>

      {/* Timer + progress bar */}
      <div className="flex items-center justify-between bg-background-50 rounded-xl border border-foreground-200/60 px-4 py-3">
        <span className="text-sm font-semibold text-foreground-700">{answeredCount}/{quiz.questions.length} answered</span>
        <span className={`text-sm font-mono font-bold inline-flex items-center gap-1.5 ${timeRemaining != null && timeRemaining < 60 ? 'text-red-600' : 'text-foreground-700'}`}>
          <AppIcon className="ri-timer-line" />
          {timeRemaining != null ? formatClock(timeRemaining) : formatClock(elapsedSeconds)}
        </span>
      </div>

      {/* Question navigator */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {quiz.questions.map((q, i) => (
          <button
            key={q.id}
            onClick={() => onJump(i)}
            aria-current={i === current ? 'step' : undefined}
            className={`w-8 h-8 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
              i === current
                ? 'bg-primary-600 text-white'
                : isAnswered(answers[String(q.id)])
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-background-100 text-foreground-500'
            }`}
          >
            {i + 1}
          </button>
        ))}
      </div>

      {/* Active question */}
      <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-5 md:p-6 card-premium">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Question {current + 1} of {quiz.questions.length}</span>
          <span className="text-[11px] text-foreground-400">· {question.points} {question.points === 1 ? 'point' : 'points'}</span>
        </div>
        <p className="text-base font-semibold text-foreground-900 mb-5">{question.text}</p>
        <QuizQuestionImage imageUrl={question.imageUrl} />

        <QuestionInput question={question} value={answers[String(question.id)]} onChange={(v) => onAnswer(question.id, v)} />
      </div>

      {submitError && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">{submitError}</div>
      )}

      {/* Nav buttons */}
      <div className="flex items-center gap-3">
        <button
          onClick={onPrev}
          disabled={current === 0}
          className="px-4 py-2.5 rounded-xl text-sm font-medium text-foreground-600 hover:bg-background-100 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Previous
        </button>
        {isLast ? (
          <button
            onClick={onSubmit}
            className="flex-1 px-4 py-2.5 rounded-xl text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-700 transition-colors cursor-pointer inline-flex items-center justify-center gap-2"
          >
            <AppIcon className="ri-check-line" /> Finish Quiz
          </button>
        ) : (
          <button
            onClick={onNext}
            className="flex-1 px-4 py-2.5 rounded-xl text-sm font-semibold bg-primary-600 text-white hover:bg-primary-700 transition-colors cursor-pointer"
          >
            Next
          </button>
        )}
      </div>
    </div>
  );
}

function isAnswered(value: QuizAnswerValue | undefined): boolean {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  if (typeof value === 'string') return value.trim().length > 0;
  return true;
}

/* ═══════════════════════════════════════════════════════
   QUESTION INPUT — dispatches by type
   ═══════════════════════════════════════════════════════ */
export function QuestionInput({ question, value, onChange }: {
  question: QuizQuestion;
  value: QuizAnswerValue | undefined;
  onChange: (v: QuizAnswerValue) => void;
}) {
  switch (question.type) {
    case 'single_choice':
    case 'true_false':
      return <SingleChoiceInput question={question} value={value as number | undefined} onChange={onChange} />;
    case 'multiple_choice':
      return <MultipleChoiceInput question={question} value={value as number[] | undefined} onChange={onChange} />;
    case 'fill_gap':
      return <FillGapInput value={value as string | undefined} onChange={onChange} />;
    case 'keywords':
      return <KeywordsInput answerCount={question.answerCount || 1} value={value as string[] | undefined} onChange={onChange} />;
    case 'ordering':
      return <OrderingInput question={question} value={value as number[] | undefined} onChange={onChange} />;
    case 'matching':
    case 'image_matching':
      return <MatchingInputRich question={question} value={value as Record<string, string> | undefined} onChange={onChange} />;
    default:
      return <p className="text-sm text-foreground-400 italic">This question type isn't supported yet — skip and continue.</p>;
  }
}

function SingleChoiceInput({ question, value, onChange }: {
  question: QuizQuestion; value: number | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  return (
    <div className="space-y-2">
      {question.answers.map((a, i) => (
        <button
          key={a.id}
          onClick={() => onChange(a.id)}
          className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-colors cursor-pointer ${
            value === a.id ? 'border-primary-400 bg-primary-50' : 'border-foreground-200 hover:bg-background-100'
          }`}
        >
          <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
            value === a.id ? 'bg-primary-600 text-white' : 'bg-background-100 text-foreground-500'
          }`}>
            {String.fromCharCode(65 + i)}
          </span>
          <span className="text-sm text-foreground-800">{a.text}</span>
        </button>
      ))}
    </div>
  );
}

function MultipleChoiceInput({ question, value, onChange }: {
  question: QuizQuestion; value: number[] | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  const selected = value || [];
  const toggle = (id: number) => {
    onChange(selected.includes(id) ? selected.filter((v) => v !== id) : [...selected, id]);
  };
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-foreground-400 mb-1">Select all that apply</p>
      {question.answers.map((a) => (
        <button
          key={a.id}
          onClick={() => toggle(a.id)}
          className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-colors cursor-pointer ${
            selected.includes(a.id) ? 'border-primary-400 bg-primary-50' : 'border-foreground-200 hover:bg-background-100'
          }`}
        >
          <span className={`w-5 h-5 rounded-md flex items-center justify-center shrink-0 border-2 ${
            selected.includes(a.id) ? 'bg-primary-600 border-primary-600' : 'border-foreground-300'
          }`}>
            {selected.includes(a.id) && <AppIcon className="ri-check-line text-white text-xs" />}
          </span>
          <span className="text-sm text-foreground-800">{a.text}</span>
        </button>
      ))}
    </div>
  );
}

function FillGapInput({ value, onChange }: { value: string | undefined; onChange: (v: QuizAnswerValue) => void }) {
  return (
    <input
      type="text"
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Type your answer…"
      className="w-full h-11 px-4 text-sm bg-background-50 border border-foreground-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary-300 focus:border-primary-300 text-foreground-800"
    />
  );
}

function KeywordsInput({ answerCount, value, onChange }: {
  answerCount: number; value: string[] | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  const words = value && value.length === answerCount ? value : Array.from({ length: answerCount }, (_, i) => (value || [])[i] || '');
  const setWord = (i: number, w: string) => {
    const next = [...words];
    next[i] = w;
    onChange(next);
  };
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-foreground-400 mb-1">Give {answerCount} {answerCount === 1 ? 'answer' : 'answers'}</p>
      {words.map((w, i) => (
        <input
          key={i}
          type="text"
          value={w}
          onChange={(e) => setWord(i, e.target.value)}
          placeholder={`Answer ${i + 1}…`}
          className="w-full h-10 px-4 text-sm bg-background-50 border border-foreground-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary-300 focus:border-primary-300 text-foreground-800"
        />
      ))}
    </div>
  );
}

function OrderingInput({ question, value, onChange }: {
  question: QuizQuestion; value: number[] | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  const order = value && value.length === question.answers.length ? value : question.answers.map((a) => a.id);
  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= order.length) return;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  const byId = new Map(question.answers.map((a) => [a.id, a.text]));
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-foreground-400 mb-1">Arrange in the correct order</p>
      {order.map((id, i) => (
        <div key={id} className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-foreground-200 bg-background-50">
          <span className="w-6 h-6 rounded-full bg-background-100 text-foreground-500 text-xs font-bold flex items-center justify-center shrink-0">{i + 1}</span>
          <span className="flex-1 text-sm text-foreground-800">{byId.get(id)}</span>
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={() => move(i, -1)} disabled={i === 0} className="w-7 h-7 rounded-lg flex items-center justify-center hover:bg-background-100 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
              <AppIcon className="ri-arrow-up-line text-foreground-500 text-sm" />
            </button>
            <button onClick={() => move(i, 1)} disabled={i === order.length - 1} className="w-7 h-7 rounded-lg flex items-center justify-center hover:bg-background-100 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
              <AppIcon className="ri-arrow-down-line text-foreground-500 text-sm" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function MatchingInput({ question, value, onChange }: {
  question: QuizQuestion; value: Record<string, string> | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  const pairs = value || {};
  const setMatch = (left: string, right: string) => {
    onChange({ ...pairs, [left]: right });
  };
  const rightOptions = question.rightOptions || [];
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-foreground-400 mb-1">Match each item on the left with the correct option</p>
      {question.answers.map((a) => (
        <div key={a.id} className="flex items-center gap-3">
          <span className="flex-1 text-sm text-foreground-800 px-3 py-2.5 rounded-lg bg-background-100">{a.left}</span>
          <AppIcon className="ri-arrow-right-line text-foreground-300 shrink-0" />
          <select
            value={pairs[a.left || ''] || ''}
            onChange={(e) => setMatch(a.left || '', e.target.value)}
            className="flex-1 h-10 px-3 text-sm bg-background-50 border border-foreground-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-300 text-foreground-800"
          >
            <option value="">Select…</option>
            {rightOptions.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      ))}
    </div>
  );
}

function MatchingInputRich({ question, value, onChange }: {
  question: QuizQuestion; value: Record<string, string> | undefined; onChange: (v: QuizAnswerValue) => void;
}) {
  const pairs = value || {};
  const setMatch = (key: string, right: string) => {
    onChange({ ...pairs, [key]: right });
  };
  const rightOptions = question.rightOptions || [];

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-foreground-400 mb-1">Match each item on the left with the correct option</p>
      {question.answers.map((answer, index) => {
        const answerKey = answer.leftKey || answer.left || String(answer.id);
        const label = answer.label || answer.left || `Item ${index + 1}`;
        const hasImage = Boolean(answer.imageUrl);

        return (
          <div key={answer.id} className="flex items-center gap-3">
            <span className={`flex-1 rounded-lg bg-background-100 ${hasImage ? 'px-3 py-3' : 'px-3 py-2.5'} text-sm text-foreground-800`}>
              {hasImage ? (
                <span className="flex items-center gap-3">
                  <img src={answer.imageUrl} alt={label} className="h-32 w-32 rounded-lg object-contain border border-foreground-200/60 bg-white shrink-0" />
                  <span className="min-w-0">
                    <span className="block text-[11px] font-bold uppercase tracking-wide text-foreground-400">Image {String.fromCharCode(65 + index)}</span>
                    <span className="block break-words">{label}</span>
                  </span>
                </span>
              ) : (
                answer.left
              )}
            </span>
            <AppIcon className="ri-arrow-right-line text-foreground-300 shrink-0" />
            <select
              value={pairs[answerKey] || ''}
              onChange={(e) => setMatch(answerKey, e.target.value)}
              className="flex-1 h-10 px-3 text-sm bg-background-50 border border-foreground-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-300 text-foreground-800"
            >
              <option value="">Select...</option>
              {rightOptions.map((rightOption) => (
                <option key={rightOption} value={rightOption}>{rightOption}</option>
              ))}
            </select>
          </div>
        );
      })}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   RESULTS
   ═══════════════════════════════════════════════════════ */
function ResultsScreen({ quiz, result, kind, learnerId, onBack, onRetake }: {
  quiz: Quiz;
  result: QuizAttemptResult;
  kind: 'commercial' | 'apprenticeship';
  learnerId: string;
  onBack: () => void;
  onRetake: () => void;
}) {
  const { attempt } = result;
  return (
    <div className="space-y-4">
      <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-6 md:p-8 card-premium text-center">
        <div className={`w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 ${attempt.passed ? 'bg-emerald-100' : 'bg-red-100'}`}>
          <AppIcon className={`${attempt.passed ? 'ri-trophy-line text-emerald-600' : 'ri-close-circle-line text-red-600'} text-2xl`} />
        </div>
        <h1 className="text-lg font-heading font-bold text-foreground-900 mb-1">{attempt.passed ? 'Quiz Passed!' : 'Quiz Not Passed'}</h1>
        <p className="text-sm text-foreground-400 mb-6">{quiz.title}</p>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <StatTile icon="ri-percent-line" label="Grade" value={`${Math.round((result.grade ?? attempt.grade) * 100)}%`} />
          <StatTile icon="ri-check-double-line" label="Score" value={`${result.achievedScore ?? attempt.achievedScore}/${result.totalScore ?? attempt.totalScore}`} />
          <StatTile icon="ri-medal-line" label="Points" value={`${result.earned}/${result.possible}`} />
          <StatTile icon="ri-timer-line" label="Time taken" value={attempt.timeTaken} />
        </div>

        <div className="flex flex-col justify-center gap-3 sm:flex-row">
          <button
            onClick={onBack}
            className="px-6 py-2.5 rounded-xl text-sm font-semibold border border-foreground-200 bg-background-50 text-foreground-700 hover:bg-background-100 transition-colors cursor-pointer"
          >
            Back to Training Plan
          </button>
          <button
            onClick={onRetake}
            disabled={Boolean(quiz.unavailableReason)}
            className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-sm font-semibold bg-primary-600 text-white hover:bg-primary-700 transition-colors cursor-pointer"
          >
            <AppIcon className="ri-refresh-line" /> Retake Quiz
          </button>
        </div>
        {quiz.unavailableReason && <p role="alert" className="mt-3 text-sm text-red-700">{quiz.unavailableReason}</p>}
      </div>

      <QuizReadingPanel
        quizId={Number(attempt.quizId)}
        attemptNumber={attempt.attempt}
        kind={kind}
        learnerId={learnerId}
        quizTimeSeconds={attempt.verifiedSeconds || durationSeconds(attempt.timeTaken)}
      />

      {/* Per-question breakdown */}
      <div className="bg-background-50 rounded-2xl border border-foreground-200/60 p-5 md:p-6 card-premium">
        <h2 className="text-sm font-heading font-bold text-foreground-900 mb-3">Question Breakdown</h2>
        <div className="space-y-2">
          {result.breakdown.map((q, i) => (
            <div
              key={q.questionId}
              className={`rounded-xl border p-4 ${q.correct ? 'border-emerald-200 bg-emerald-50/40' : 'border-red-200 bg-red-50/40'}`}
            >
              <div className="flex items-start gap-2 mb-2">
                <span className={`shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold mt-0.5 ${
                  q.correct ? 'bg-emerald-500 text-white' : 'bg-red-500 text-white'
                }`}>
                  <AppIcon className={q.correct ? 'ri-check-line' : 'ri-close-line'} />
                </span>
                <p className="text-sm font-semibold text-foreground-900 flex-1">
                  <span className="text-foreground-400 font-normal">Q{i + 1}.</span> {q.questionText}
                </p>
                <span className="shrink-0 text-[11px] text-foreground-400">{q.earned}/{q.possible} pt</span>
              </div>
              <div className="pl-7 space-y-1">
                <QuizQuestionImage imageUrl={q.imageUrl} />
                <p className="text-xs text-foreground-600">
                  <span className="font-medium text-foreground-500">Your answer: </span>
                  {q.chosenAnswer || <span className="italic text-foreground-400">No answer</span>}
                </p>
                {!q.correct && q.correctAnswer && (
                  <p className="text-xs text-emerald-700">
                    <span className="font-medium">Correct answer: </span>{q.correctAnswer}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function QuizReadingPanel({ quizId, attemptNumber, kind, learnerId, quizTimeSeconds }: {
  quizId: number;
  attemptNumber: number;
  kind: 'commercial' | 'apprenticeship';
  learnerId: string;
  quizTimeSeconds: number;
}) {
  const [reading, setReading] = useState<QuizReading | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const { confirmTime, timeConfirmation } = useManualTimeConfirmation();
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [confirmationReadingSeconds, setConfirmationReadingSeconds] = useState(0);
  const [showTimeConfirmation, setShowTimeConfirmation] = useState(false);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const [timeEntryMode, setTimeEntryMode] = useState<'timer' | 'manual'>('timer');
  const [manualMinutes, setManualMinutes] = useState('');
  const trackingRef = useRef<TimeTrackingSession | null>(null);

  const cleanParagraph = (value: string) => value
    .replace(/^#{1,6}\s*/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  const sectionParagraphs = (section: QuizReading['material']['sections'][number]) => {
    if (section.paragraphs?.length) return section.paragraphs.map(cleanParagraph).filter(Boolean);
    return String(section.body || '')
      .split(/\n{2,}|(?=#{1,6}\s)/g)
      .map(cleanParagraph)
      .filter(Boolean);
  };

  const loadReading = () => {
    setLoading(true);
    setError(null);
    generateQuizReading(quizId, kind, learnerId, attemptNumber)
      .then(setReading)
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Could not generate your reading.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    generateQuizReading(quizId, kind, learnerId, attemptNumber)
      .then((value) => { if (!cancelled) setReading(value); })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : 'Could not generate your reading.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [attemptNumber, kind, learnerId, quizId]);

  useEffect(() => {
    if (!open || reading?.completed || showTimeConfirmation) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') setElapsedSeconds((seconds) => seconds + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [open, reading?.completed, showTimeConfirmation]);

  const openReading = async () => {
    if (!reading) return;
    if (reading.completed || trackingRef.current) {
      setOpen(true);
      return;
    }
    setError(null);
    try {
      trackingRef.current = await startTimeTracking(
        'component', `quiz-reading:${quizId}:${attemptNumber}`, kind, learnerId, 'visible_page',
      );
      setElapsedSeconds(0);
      setTimeEntryMode('timer');
      setManualMinutes('');
      setShowTimeConfirmation(false);
      setOpen(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start the reading timer.');
    }
  };

  const openTimeConfirmation = () => {
    setConfirmationReadingSeconds(elapsedSeconds);
    setTimeEntryMode('timer');
    setManualMinutes('');
    setConfirmationError(null);
    setShowTimeConfirmation(true);
  };

  const finishReading = async () => {
    const tracking = trackingRef.current;
    if (!tracking || finishing) return;
    const enteredMinutes = Number(manualMinutes);
    if (timeEntryMode === 'manual' && (!manualMinutes.trim() || !Number.isFinite(enteredMinutes) || enteredMinutes <= 0)) {
      setConfirmationError('Enter a valid reading time greater than 0 minutes.');
      return;
    }
    const claimedSeconds = timeEntryMode === 'manual'
      ? Math.round(enteredMinutes * 60)
      : elapsedSeconds;
    setFinishing(true);
    setConfirmationError(null);
    try {
      if (timeEntryMode === 'manual' && !await confirmTime(claimedSeconds)) {
        requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[aria-label="Reading time (minutes)"]')?.focus());
        return;
      }
      if (tracking.persistent) {
        trackingRef.current = await checkpointTimeTracking(tracking, 'component', `quiz-reading:${quizId}:${attemptNumber}`,
          kind, learnerId, elapsedSeconds);
      }
      const completed = await completeQuizReading(
        quizId, kind, learnerId, attemptNumber, tracking.trackingToken, claimedSeconds, timeEntryMode,
      );
      setReading(completed);
      trackingRef.current = null;
      setShowTimeConfirmation(false);
      setOpen(false);
    } catch (cause) {
      setConfirmationError(cause instanceof Error ? cause.message : 'Could not save your reading time.');
    } finally {
      setFinishing(false);
    }
  };

  const enteredMinutes = Number(manualMinutes);
  const selectedReadingSeconds = timeEntryMode === 'manual' && Number.isFinite(enteredMinutes) && enteredMinutes > 0
    ? Math.round(enteredMinutes * 60)
    : timeEntryMode === 'timer' ? confirmationReadingSeconds : 0;
  const totalTimeSeconds = Math.max(0, quizTimeSeconds) + selectedReadingSeconds;

  return (
    <section className="overflow-hidden rounded-2xl border border-primary-200 bg-background-50 card-premium">
      {timeConfirmation}
      <div className="flex flex-col gap-4 bg-gradient-to-r from-primary-50 to-violet-50 p-5 sm:flex-row sm:items-center sm:justify-between md:p-6">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary-600 text-white shadow-sm">
            <AppIcon className="ri-sparkling-2-line text-lg" />
          </span>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-primary-700">AI personalised reading</p>
            <h2 className="mt-1 text-base font-heading font-bold text-foreground-950">
              {reading?.material.title || (loading ? 'Analysing your quiz answers…' : 'Revision reading')}
            </h2>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-foreground-600">
              {reading?.material.summary || 'We are turning the areas that need more practice into a focused reading for you.'}
            </p>
            {reading?.completed && (
              <p className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
                <AppIcon className="ri-checkbox-circle-line" /> Completed · {reading.timeTaken || '00:00'} added to actual time
              </p>
            )}
          </div>
        </div>
        <div className="shrink-0">
          {loading ? (
            <span className="inline-flex items-center gap-2 rounded-xl bg-background-50 px-4 py-2.5 text-sm font-semibold text-primary-700 shadow-sm">
              <AppIcon className="ri-loader-4-line animate-spin" /> Generating reading
            </span>
          ) : reading ? (
            <button type="button" onClick={() => void openReading()} className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700">
              <AppIcon className={reading.completed ? 'ri-book-open-line' : 'ri-timer-line'} />
              {reading.completed ? 'Read Again' : 'Open Reading & Start Time'}
            </button>
          ) : (
            <button type="button" onClick={loadReading} className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700">
              <AppIcon className="ri-refresh-line" /> Retry Generation
            </button>
          )}
        </div>
      </div>

      {error && <p className="border-t border-red-100 bg-red-50 px-5 py-3 text-xs font-semibold text-red-700">{error}</p>}

      {open && reading && (
        <div className="border-t border-primary-100 p-5 md:p-7">
          {!reading.completed && (
            <div className="mb-5 flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <span className="inline-flex items-center gap-2 text-sm font-semibold text-amber-900">
                <AppIcon className="ri-time-line" /> Active reading time
              </span>
              <span className="font-mono text-base font-bold tabular-nums text-amber-900">{formatClock(quizTimeSeconds + elapsedSeconds)}</span>
            </div>
          )}
          <article className="mx-auto max-w-4xl space-y-6">
            {reading.material.sections.map((section, index) => (
              <section key={`${section.heading}-${index}`}>
                <h3 className="text-base font-heading font-bold text-foreground-950">{section.heading}</h3>
                <div className="mt-2 space-y-3">
                  {sectionParagraphs(section).map((paragraph, paragraphIndex) => (
                    <p key={paragraphIndex} className="text-sm leading-7 text-foreground-700">{paragraph}</p>
                  ))}
                </div>
              </section>
            ))}
            {!!reading.material.keyTakeaways.length && (
              <section className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
                <h3 className="text-sm font-heading font-bold text-emerald-900">Key takeaways</h3>
                <ul className="mt-3 space-y-2">
                  {reading.material.keyTakeaways.map((takeaway, index) => (
                    <li key={index} className="flex items-start gap-2 text-sm leading-6 text-emerald-900">
                      <AppIcon className="ri-check-line mt-1 shrink-0" /> {takeaway}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </article>
          <div className={`${showTimeConfirmation ? 'hidden' : 'flex'} mt-7 flex-col justify-end gap-3 border-t border-background-200 pt-5 sm:flex-row`}>
            <button type="button" onClick={() => setOpen(false)} className="rounded-xl border border-foreground-200 px-5 py-2.5 text-sm font-semibold text-foreground-700 hover:bg-background-100">
              {reading.completed ? 'Close' : 'Continue Later'}
            </button>
            {!reading.completed && (
              <button type="button" onClick={openTimeConfirmation} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-700">
                <AppIcon className="ri-checkbox-circle-line" /> Finish Reading & Add Actual Time
              </button>
            )}
          </div>
        </div>
      )}

      {showTimeConfirmation && reading && !reading.completed && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto bg-slate-950/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="reading-confirmation-title">
          <div className="w-full max-w-md rounded-2xl border border-background-200 bg-background-50 p-6 shadow-2xl sm:p-7">
            <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-primary-50 text-primary-700 shadow-sm">
              <AppIcon className="ri-clipboard-line text-2xl" />
            </div>
            <h2 id="reading-confirmation-title" className="mt-5 text-center text-2xl font-heading font-bold leading-tight text-foreground-950">Confirm reading completion?</h2>
            <p className="mt-2 text-center text-sm text-foreground-500">{reading.material.title}</p>

            <div className="mt-6 rounded-xl border border-foreground-200 bg-background-50 p-4">
              <div className="flex items-center justify-between gap-4 border-b border-foreground-200 pb-3">
                <div>
                  <p className="text-[11px] font-bold uppercase tracking-wider text-foreground-500">AI Assessted learning</p>
                  <p className="mt-1 text-xs text-foreground-400">Quiz {formatClock(quizTimeSeconds)} + Reading {formatClock(selectedReadingSeconds)}</p>
                </div>
                <span className="shrink-0 font-mono text-lg font-bold tabular-nums text-foreground-900">{formatClock(totalTimeSeconds)}</span>
              </div>

              <div className="mt-3 space-y-2">
                <button type="button" onClick={() => { setTimeEntryMode('timer'); setConfirmationError(null); }} aria-pressed={timeEntryMode === 'timer'} className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-sm font-semibold ${timeEntryMode === 'timer' ? 'border-primary-300 bg-primary-50 text-primary-950' : 'border-foreground-200 text-foreground-600'}`}>
                  <span className="inline-flex items-center gap-2"><AppIcon className="ri-timer-line" /> Timer</span>
                  <span className="font-mono tabular-nums">{formatClock(quizTimeSeconds + confirmationReadingSeconds)}</span>
                </button>
                <button type="button" onClick={() => { setTimeEntryMode('manual'); setConfirmationError(null); }} aria-pressed={timeEntryMode === 'manual'} className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-sm font-semibold ${timeEntryMode === 'manual' ? 'border-primary-300 bg-primary-50 text-primary-950' : 'border-foreground-200 text-foreground-500'}`}>
                  <span className="inline-flex items-center gap-2"><AppIcon className="ri-edit-line" /> Input</span>
                  <span>{timeEntryMode === 'manual' && manualMinutes ? `${manualMinutes} min` : '--:--'}</span>
                </button>
                {timeEntryMode === 'manual' && (
                  <label className="block pt-1 text-xs font-semibold text-foreground-700">
                    Reading time (minutes)
                    <input autoFocus aria-label="Reading time (minutes)" type="number" min="0.1" step="0.1" inputMode="decimal" value={manualMinutes} onChange={event => setManualMinutes(event.target.value)} placeholder="e.g. 30" className="mt-1.5 w-full rounded-xl border border-foreground-200 bg-white px-3 py-2.5 text-sm font-normal text-foreground-900 outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-200" />
                  </label>
                )}
              </div>
              {confirmationError && <p role="alert" className="mt-3 text-xs font-semibold text-red-700">{confirmationError}</p>}
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <button type="button" onClick={() => setShowTimeConfirmation(false)} disabled={finishing} className="rounded-xl border border-foreground-200 px-4 py-3 text-sm font-semibold text-foreground-700 hover:bg-background-100 disabled:opacity-60">Cancel</button>
              <button type="button" onClick={() => void finishReading()} disabled={finishing} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-60">
                <AppIcon className={finishing ? 'ri-loader-4-line animate-spin' : 'ri-check-line'} /> {finishing ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </section>
  );
}
