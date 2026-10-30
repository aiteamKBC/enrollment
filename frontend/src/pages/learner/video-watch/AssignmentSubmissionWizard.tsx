import type { ComponentProps } from 'react';
import { useManualTimeConfirmation } from '@/hooks/useManualTimeConfirmation';
import { presentTopic, type AssignmentResourceLink, type AssignmentTopic } from '@/lib/assignmentTopics';
import { TopicAssignment } from './TopicAssignment';
import { AssignmentGuidance, briefLabelClass } from './AssignmentBriefSections';
import { useEffect, useMemo, useRef, useState, type ReactNode, type SetStateAction } from 'react';
import DOMPurify from 'dompurify';
import type { ComponentKsbMapping, LearnerKind } from '@/api/learnerDetail';
import {
  loadLearningReflectionSubmission,
  saveLearningReflectionSubmission,
  type LearningReflectionSubmissionInput,
  type HistoricalAssignmentContent,
} from '@/api/reflectionSubmission';
import {
  getEvidenceDownloadUrl,
  type EvidenceRecord,
  type EvidenceTrainingPlanDetails,
} from '@/api/evidence';
import { AssignmentEvidence } from '@/components/feature/AssignmentEvidence';
import { AssignmentAttemptHistory } from '@/components/feature/AssignmentAttemptHistory';
import type { SubmissionAttempt } from '@/api/assignmentAttempts';
import { AppIcon } from '@/components/feature/AppIcon';
import { checkMonthlyAssignment, emptyMonthlyAssignment, MONTHLY_STEPS, type MonthlyAssignment, type AssignmentQualityCheck } from '@/api/monthlyAssignment';
import { AssignmentAiCheckContext } from './AssignmentAiCheckContext';
import { useAssignmentStepCheck } from './useAssignmentStepCheck';
import { MonthlyAnswerField, MonthlyAssignmentSteps } from './MonthlyAssignmentSteps';
import { AssignmentAttachment } from '../monthly-submission/AssignmentAttachment';
import { readingFiles } from './readingDownloads';
import { assignmentTimeHours } from "./AssignmentTimeEntries.helpers";
import { HistoricalAssignmentCards } from './HistoricalAssignmentCards';
import { useLearningStatements } from '@/hooks/useLearningStatements';
import { parsePersonalLearning } from '@/lib/personalLearning';
import styles from './assignmentWizard.module.css';

export type AssignmentAnswers = {
  assignmentAnswer: string;
  whatYouLearned: string;
  businessImpact: string;
  assignmentTopicId?: string;
};

export type AssignmentCompletion = AssignmentAnswers & {
  timeTakenSeconds: number;
  timeEntrySource: 'timer' | 'input';
};

const EMPTY_ANSWERS: AssignmentAnswers = {
  assignmentAnswer: '',
  whatYouLearned: '',
  businessImpact: '',
};

function londonDate(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/London',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

export function AssignmentSubmissionForm({
  kind,
  learnerId,
  learnerName,
  programmeName,
  componentId,
  title,
  moduleTitle,
  weekTitle,
  plannedOtjh,
  initialMonth,
  questionHtml,
  questionText,
  questionFileUrl,
  questionFileName,
  questionFiles,
  instructionsHtml,
  resourceLinks,
  ksbMappings,
  evidenceFiles,
  evidenceDetails,
  timeSeconds,
  timeSource = 'timer',
  timeControl,
  submittingProgress,
  onEvidenceChanged,
  onRestoreTime,
  onSubmitProgress,
  historicalReadOnly = false,
  resolveEvidenceUrl,
  renderEvidencePreview,
  historicalContent,
  assignmentTopicId, elapsedSeconds, onRestoreElapsed, onManualDraftSaved, onShowInstructions, onTopicSubmitted,
}: {
  kind: LearnerKind;
  learnerId: string;
  learnerName: string;
  programmeName: string;
  componentId: string;
  title: string;
  moduleTitle: string;
  weekTitle: string;
  plannedOtjh: number | null;
  initialMonth?: string | null;
  questionHtml?: string | null;
  questionText?: string | null;
  questionFileUrl?: string | null;
  questionFileName?: string | null;
  questionFiles?: readonly { url: string; fileName?: string | null }[];
  /** Authored instructions, shown under their own heading beside the question. */
  instructionsHtml?: string | null;
  /** Web resources listed under the instructions. */
  resourceLinks?: readonly AssignmentResourceLink[];
  ksbMappings: ComponentKsbMapping[];
  evidenceFiles: EvidenceRecord[];
  evidenceDetails: EvidenceTrainingPlanDetails;
  timeSeconds: number | null;
  timeSource?: 'timer' | 'input';
  timeControl: ReactNode;
  submittingProgress: boolean;
  onEvidenceChanged: (files: EvidenceRecord[]) => void;
  onRestoreTime: (seconds: number, source: 'timer' | 'input') => void;
  onSubmitProgress: (answers: AssignmentCompletion) => Promise<void>;
  historicalReadOnly?: boolean;
  resolveEvidenceUrl?: (file: EvidenceRecord) => Promise<string>;
  renderEvidencePreview?: (file: EvidenceRecord, url: string) => ReactNode;
  historicalContent?: HistoricalAssignmentContent;
  assignmentTopicId?: string;
  elapsedSeconds?: number;
  onRestoreElapsed?: (seconds: number) => void;
  onManualDraftSaved?: () => void;
  onShowInstructions?: () => void;
  onTopicSubmitted?: () => void;
}) {
  const questionAttachments = useMemo(() => readingFiles(questionFileUrl, questionFileName, questionHtml || '', questionFiles),
    [questionFileUrl, questionFileName, questionHtml, questionFiles]);
  const personalStudy = parsePersonalLearning(learnerId)?.mode === 'study';
  const steps = MONTHLY_STEPS.map((label, index) => personalStudy && index === 7 ? 'Presentation' : label);
  // Imported cards retain their original storage order; display them in wizard order.
  const orderedHistoricalContent = historicalContent ? { ...historicalContent, cards: [
    ...historicalContent.cards.slice(0, 6), ...historicalContent.cards.slice(6, 8).reverse(),
    ...historicalContent.cards.slice(8),
  ] } : undefined;
  const [step, setStep] = useState(0);
  const wizardRef = useRef<HTMLElement>(null);
  const [qualityTarget, setQualityTarget] = useState<string | null>(null);
  useEffect(() => {
    if (!qualityTarget) return;
    const target = wizardRef.current?.querySelector<HTMLElement>(`[data-quality-target="${qualityTarget}"]`);
    if (target) {
      target.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      target.focus({ preventScroll: true });
    }
    setQualityTarget(null);
  }, [step, qualityTarget]);
  const [savedTopicQuestion, setSavedTopicQuestion] = useState<string | null>(null);
  const [answers, setAnswers] = useState<AssignmentAnswers>(EMPTY_ANSWERS);
  // The Training Plan month seeds a new assignment; a saved draft always wins.
  const [defaultMonth] = useState(() => initialMonth && /^\d{4}-(0[1-9]|1[0-2])$/.test(initialMonth) ? initialMonth : londonDate().slice(0, 7));
  const [monthly, setMonthly] = useState<MonthlyAssignment>(() => emptyMonthlyAssignment(ksbMappings.map(m => m.code), defaultMonth));
  const detailedSeconds = monthly.timeEntries === undefined ? null : Math.round(assignmentTimeHours(monthly.timeEntries) * 3600);
  const claimedSeconds = detailedSeconds ?? timeSeconds;
  const [checks, setChecks] = useState<AssignmentQualityCheck[]>([]);
  const [checking, setChecking] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [imported, setImported] = useState(false);
  const [savedTimeSeconds, setSavedTimeSeconds] = useState<number | null>(null);
  const [recoveredDraft, setRecoveredDraft] = useState(false);
  const [status, setStatus] = useState('');
  const [submissionAttempts, setSubmissionAttempts] = useState<SubmissionAttempt[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [evidencePreview, setEvidencePreview] = useState<{ file: EvidenceRecord; url: string } | null>(null);
  const [openingEvidenceId, setOpeningEvidenceId] = useState<string | null>(null);
  const loadedRef = useRef(false);
  const saveVersionRef = useRef(0);
  const submittingRef = useRef(false);
  const { confirmTime, timeConfirmation } = useManualTimeConfirmation();
  const saveQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const latestDraftRef = useRef<LearningReflectionSubmissionInput | null>(null);
  const lockedRef = useRef(false);
  const mountedRef = useRef(true);
  const onRestoreTimeRef = useRef(onRestoreTime);
  const ksbMappingsRef = useRef(ksbMappings);
  ksbMappingsRef.current = ksbMappings;
  const restoreElapsedRef = useRef(onRestoreElapsed);
  restoreElapsedRef.current = onRestoreElapsed;
  const recoveryKey = `monthly-assignment-draft:${kind}:${learnerId}:${componentId}${assignmentTopicId ? `:topic:${assignmentTopicId}` : ''}`;

  useEffect(() => { onRestoreTimeRef.current = onRestoreTime; }, [onRestoreTime]);
  useEffect(() => {
    if (detailedSeconds !== null) onRestoreTimeRef.current(detailedSeconds, 'input');
  }, [detailedSeconds]);

  const locked = historicalReadOnly || imported || status === 'submitted_for_tutor_review' || status === 'accepted' || Boolean(assignmentTopicId && status === 'partial');
  const readOnly = locked || loadFailed || submittingProgress;
  const learningConfirmed = monthly.plannedReviewed === true && monthly.newKnowledge === true
    && monthly.newSkills === true && monthly.sharingConsent === true;
  const learningGeneration = useLearningStatements(
    answers.assignmentAnswer, learnerId, kind, componentId, !readOnly && !loading && step === 0,
    { whatYouLearned: answers.whatYouLearned, understood: monthly.understood, gainedSkills: monthly.gainedSkills },
    result => {
      if (!Object.keys(result).length || submittingRef.current) return;
      setChecks([]);
      setAnswers(current => ({ ...current, ...(result.whatYouLearned !== undefined ? { whatYouLearned: result.whatYouLearned } : {}) }));
      setMonthly(current => ({ ...current,
        ...(result.understood !== undefined ? { understood: result.understood } : {}),
        ...(result.gainedSkills !== undefined ? { gainedSkills: result.gainedSkills } : {}),
        presentationReviewed: false, presentationToken: '',
      }));
    },
  );
  lockedRef.current = locked;
  const evidenceNames = [...new Set([...evidenceFiles.map(file => file.filename), ...(monthly.uploadedPresentation ? [monthly.uploadedPresentation.name] : [])])];
  const cleanQuestionHtml = useMemo(
    () => {
      // Older briefs can contain rich text in the plain-text API field.
      const html = locked && savedTopicQuestion !== null ? savedTopicQuestion : questionHtml || (/<[a-zA-Z][^>]*>/.test(questionText || '') ? questionText : '');
      return html ? DOMPurify.sanitize(html) : '';
    },
    [questionHtml, questionText, locked, savedTopicQuestion],
  );
  // Stable object identity: React 19 re-applies innerHTML whenever it changes,
  // which would recreate an embedded <video> on every parent timer tick.
  const questionInnerHtml = useMemo(
    () => ({ __html: cleanQuestionHtml }),
    [cleanQuestionHtml],
  );

  const payload = (mode: 'draft' | 'submit'): LearningReflectionSubmissionInput => ({
    learnerKind: kind,
    ...(assignmentTopicId ? { assignmentTopicId } : {}),
    ...(elapsedSeconds !== undefined ? { assignmentElapsedSeconds: elapsedSeconds } : {}),
    learnerId,
    learnerName,
    programmeName,
    activityType: 'assignment',
    activityId: componentId,
    activityTitle: title,
    moduleTitle,
    weekTitle,
    plannedOtjh: plannedOtjh == null ? '' : String(plannedOtjh),
    learningReflection: answers.whatYouLearned || answers.assignmentAnswer,
    ksbCodes: monthly.claims.map(claim => claim.code),
    ksbWeights: Object.fromEntries(ksbMappings.map(mapping => [mapping.code, Number(mapping.weight) || 0])),
    ksbExplanations: Object.fromEntries(monthly.claims.map(claim => [claim.code, claim.explanation])),
    confidenceBefore: {},
    confidenceAfter: {},
    applicationType: 'assignment_form',
    applicationText: answers.businessImpact,
    evidenceFiles: evidenceNames,
    evidenceConsentConfirmed: monthly.sharingConsent,
    selectedBenefits: [],
    benefitExplanation: answers.businessImpact,
    actualTimeHours: claimedSeconds && claimedSeconds > 0 ? String(claimedSeconds / 3600) : '',
    completedDuringPaidHours: monthly.paidHours ? 'yes' : 'no',
    dateCompleted: mode === 'submit' ? londonDate() : '',
    otjhConfirmed: mode === 'submit',
    signedDeclaration: mode === 'submit',
    qualityScore: 0,
    submissionMode: mode,
    assignmentAnswer: answers.assignmentAnswer,
    whatYouLearned: answers.whatYouLearned,
    businessImpact: answers.businessImpact,
    monthlyAssignment: { ...monthly, step },
    assignmentTimeSource: detailedSeconds !== null ? 'input' : timeSource,
  });
  latestDraftRef.current = payload('draft');
  const qualitySnapshot = JSON.stringify({ ...payload('draft'), assignmentElapsedSeconds: undefined, monthlyAssignment: { ...monthly, step: 0 } });
  const qualitySnapshotRef = useRef(qualitySnapshot);
  qualitySnapshotRef.current = qualitySnapshot;
  useEffect(() => { setChecks([]); }, [qualitySnapshot]);
  const stepCheck = useAssignmentStepCheck(JSON.stringify({ ...payload('draft'), assignmentElapsedSeconds: undefined }), step, !locked && !loading && !loadFailed);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Flush the current snapshot on in-app navigation as well as debounced
      // typing. Queue it after earlier writes so it cannot be overwritten.
      if (loadedRef.current && !lockedRef.current && !submittingRef.current && latestDraftRef.current) {
        const snapshot = latestDraftRef.current;
        saveQueueRef.current = saveQueueRef.current.catch(() => undefined).then(() => saveLearningReflectionSubmission(snapshot)).catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    let active = true;
    loadedRef.current = false;
    setLoadFailed(false);
    setLoading(true);
    setChecks([]);
    loadLearningReflectionSubmission({
      learnerKind: kind,
      learnerId,
      activityType: 'assignment',
      activityId: componentId,
      ...(assignmentTopicId ? { assignmentTopicId } : {}),
    })
      .then(submission => {
        if (!active) return;
        loadedRef.current = true;
        // A tab-local recovery copy protects the debounce and a temporary
        // network failure. It never supersedes a submitted/server-newer record.
        try {
          const local = JSON.parse(sessionStorage.getItem(recoveryKey) || 'null');
          const serverLocked = submission?.status === 'submitted_for_tutor_review' || submission?.status === 'accepted' || Boolean(assignmentTopicId && submission?.status === 'partial') || ['imported_legacy', 'classified_legacy'].includes(submission?.submissionOrigin || '');
          if (local?.payload?.activityId === componentId && !serverLocked && Number(local.at) > (Date.parse(submission?.submittedAt || '') || 0)) {
            submission = { ...submission, ...local.payload, status: submission?.status || 'draft' };
            setRecoveredDraft(true);
          } else if (serverLocked) sessionStorage.removeItem(recoveryKey);
        } catch { /* Storage is optional; the server draft is authoritative. */ }
        if (!submission) return;
        if (submission.assignmentQuestion !== undefined) setSavedTopicQuestion(submission.assignmentQuestion);
        if (submission.assignmentElapsedSeconds !== undefined) restoreElapsedRef.current?.(submission.assignmentElapsedSeconds);
        setAnswers({
          assignmentAnswer: submission.assignmentAnswer || '',
          whatYouLearned: submission.whatYouLearned || submission.learningReflection || '',
          businessImpact: submission.businessImpact || submission.benefitExplanation || '',
        });
        setStatus(submission.status || '');
        setSubmissionAttempts(submission.submissionAttempts || []);
        setImported(['imported_legacy', 'classified_legacy'].includes(submission.submissionOrigin || ''));
        if (submission.monthlyAssignment) {
          const restored = { ...emptyMonthlyAssignment(ksbMappingsRef.current.map(m => m.code), defaultMonth), ...submission.monthlyAssignment, timeEntries: submission.monthlyAssignment.timeEntries };
          setMonthly(restored);
          setStep(Math.max(0, Math.min(7, Number(restored.step) || 0)));
        } else setMonthly(current => ({ ...current, timeEntries: undefined }));
        const storedHours = Number(submission.actualTimeHours);
        if (Number.isFinite(storedHours) && storedHours > 0) {
          const seconds = Math.round(storedHours * 3600);
          setSavedTimeSeconds(seconds);
          onRestoreTimeRef.current(seconds, submission.assignmentTimeSource || 'input');
        }
      })
      .catch(error => {
        if (active) {
          setLoadFailed(true);
          setSaveError(error instanceof Error ? error.message : 'Could not load the saved assignment.');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [kind, learnerId, componentId, defaultMonth, recoveryKey, assignmentTopicId]);

  const saveDraft = async (_showSaved = true): Promise<boolean> => {
    if (locked || !loadedRef.current || submittingRef.current) return false;
    const snapshot = payload('draft');
    const version = ++saveVersionRef.current;
    setSavingDraft(true);
    setSaveError('');
    try {
      // Queue writes, not just responses: old drafts must never arrive last.
      const request = saveQueueRef.current.catch(() => undefined).then(() => saveLearningReflectionSubmission(snapshot));
      saveQueueRef.current = request;
      const result = await request;
      if (version !== saveVersionRef.current || !mountedRef.current) return true;
      setStatus(result.status || 'draft');
      if (result.submissionAttempts) setSubmissionAttempts(result.submissionAttempts);
      setDraftSaved(true);
      setRecoveredDraft(false);
      try {
        const local = JSON.parse(sessionStorage.getItem(recoveryKey) || 'null');
        if (JSON.stringify(local?.payload) === JSON.stringify(snapshot)) sessionStorage.removeItem(recoveryKey);
      } catch { /* Server save succeeded even when browser storage is disabled. */ }
      return true;
    } catch (error) {
      if (version === saveVersionRef.current) {
        setSaveError(error instanceof Error ? error.message : 'Could not save the assignment draft.');
      }
      return false;
    } finally {
      if (version === saveVersionRef.current) setSavingDraft(false);
    }
  };

  useEffect(() => {
    if (!loadedRef.current || locked || submittingRef.current) return;
    setDraftSaved(false);
    try { sessionStorage.setItem(recoveryKey, JSON.stringify({ at: Date.now(), payload: latestDraftRef.current })); } catch { /* Draft still saves to the server. */ }
    const timeout = window.setTimeout(() => { void saveDraft(false); }, 900);
    return () => window.clearTimeout(timeout);
    // The identifying fields are stable for the lifetime of this wizard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answers, monthly, step, evidenceFiles, Math.floor((timeSeconds || 0) / 30), Math.floor((elapsedSeconds || 0) / 30), locked]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!locked && loadedRef.current && (!draftSaved || savingDraft)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [locked, draftSaved, savingDraft]);

  const runChecks = async (): Promise<boolean> => {
    const checkedSnapshot = qualitySnapshotRef.current;
    setChecking(true); setChecks([]); setSaveError('');
    try {
      const result = await checkMonthlyAssignment(payload('draft'));
      if (!mountedRef.current || checkedSnapshot !== qualitySnapshotRef.current) return false;
      if (!Array.isArray(result) || result.length !== 13 || result.some(check => !check || typeof check.passed !== 'boolean')) {
        throw new Error('Could not verify all submission requirements. Please run the checks again.');
      }
      setChecks(result);
      return result.every(check => check.passed);
    } catch (e) { setSaveError(e instanceof Error ? e.message : 'Could not check your submission.'); return false; }
    finally { setChecking(false); }
  };

  const setAnswer = (key: keyof AssignmentAnswers, value: string) => {
    setChecks([]);
    setAnswers(current => ({ ...current, [key]: value }));
    setMonthly(current => ({ ...current, presentationReviewed: false, presentationToken: '' }));
  };

  const changeMonthly = (update: SetStateAction<MonthlyAssignment>) => {
    setChecks([]);
    setMonthly(current => {
    const next = typeof update === 'function' ? update(current) : update;
    const content = (value: MonthlyAssignment) => JSON.stringify(Object.fromEntries(Object.entries(value).filter(([key]) => !['step', 'meetingKey', 'presentationReviewed', 'presentationToken'].includes(key))));
    return content(next) !== content(current) ? { ...next, presentationReviewed: false, presentationToken: '' } : next;
    });
  };

  const goNext = async () => {
    if (!locked && (!stepCheck.ready || savingDraft || submittingProgress)) return;
    const from = step;
    const snapshot = JSON.stringify({ ...payload('draft'), assignmentElapsedSeconds: undefined });
    if (locked) { setStep(current => Math.min(7, current + 1)); return; }
    if (await saveDraft() && JSON.stringify({ ...latestDraftRef.current, assignmentElapsedSeconds: undefined }) === snapshot) {
      setStep(current => current === from ? Math.min(7, current + 1) : current);
    }
  };

  const submit = async () => {
    if (readOnly || submittingRef.current || checking || submittingProgress) return;
    if (!learningConfirmed) {
      setStep(2);
      setSaveError('Confirm all four learning declarations in Step 3 before submitting your assignment.');
      return;
    }
    if (!claimedSeconds || claimedSeconds <= 0) {
      setSaveError('Enter the time spent on this assignment before submitting.');
      return;
    }
    submittingRef.current = true;
    if ((detailedSeconds !== null || timeSource === 'input') && !await confirmTime(claimedSeconds)) {
      submittingRef.current = false;
      setStep(2);
      return;
    }
    // Invalidate any autosave already in flight so it cannot overwrite the
    // final submitted status with an older draft response.
    saveVersionRef.current += 1;
    setSavingDraft(true);
    setSaveError('');
    try {
      await saveQueueRef.current.catch(() => undefined);
      // The completion endpoint saves progress and submits this saved draft
      // atomically. Never use a second POST that could fail after completion.
      await saveLearningReflectionSubmission(payload('draft'));
      if (!await runChecks()) {
        setStep(7);
        setSaveError('Your draft is saved. Complete the outstanding checks before submitting.');
        return;
      }
      setSavedTimeSeconds(claimedSeconds);
      await onSubmitProgress({ ...answers, ...(assignmentTopicId ? { assignmentTopicId } : {}),
        timeTakenSeconds: claimedSeconds, timeEntrySource: detailedSeconds !== null ? 'input' : timeSource });
      setStatus('submitted_for_tutor_review');
      try {
        const saved = await loadLearningReflectionSubmission({ learnerKind: kind, learnerId, activityType: 'assignment', activityId: componentId, ...(assignmentTopicId ? { assignmentTopicId } : {}) });
        if (mountedRef.current && saved?.submissionAttempts) setSubmissionAttempts(saved.submissionAttempts);
      } catch {
        setSaveError('Your assignment was submitted. Reload to refresh the submission history.');
      }
      try { sessionStorage.removeItem(recoveryKey); } catch { /* Optional recovery copy. */ }
      if (onTopicSubmitted) onTopicSubmitted();
      else setPreviewOpen(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Could not submit the assignment.');
    } finally {
      submittingRef.current = false;
      setSavingDraft(false);
    }
  };

  const openEvidence = async (file: EvidenceRecord) => {
    if (file.status !== 'approved') return;
    setOpeningEvidenceId(file.id);
    setSaveError('');
    try {
      const url = resolveEvidenceUrl ? await resolveEvidenceUrl(file) : await getEvidenceDownloadUrl(kind, learnerId, file.id);
      setEvidencePreview({ file, url });
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Could not open the evidence file.');
    } finally {
      setOpeningEvidenceId(null);
    }
  };

  const closeSubmissionPreview = () => {
    setEvidencePreview(null);
    setPreviewOpen(false);
  };

  if (loading) {
    return (
      <div className="rounded-2xl border border-background-200 bg-white p-8 text-center text-sm text-foreground-500">
        <AppIcon className="ri-loader-4-line mr-2 animate-spin" />Loading your saved assignment…
      </div>
    );
  }

  return (
    <AssignmentAiCheckContext.Provider value={{ learnerId, learnerKind: kind, enabled: !readOnly }}>
      {timeConfirmation}
      <AssignmentAttemptHistory attempts={submissionAttempts} />
      <section ref={wizardRef} className={`${styles.assignmentWizard} overflow-hidden rounded-2xl border border-slate-200 bg-white font-sans shadow-sm`}>
        <div className="border-b border-slate-200 bg-sky-50/50 px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-600">Monthly submission</p>
              <h2 className="mt-1 font-heading text-lg font-bold text-foreground-950">{title}</h2>
            </div>
            <div className="flex items-center gap-2">
              {!locked && <button type="button" disabled={savingDraft || loadFailed || submittingProgress} onClick={() => void saveDraft().then(saved => { if (saved) onManualDraftSaved?.(); })} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold shadow-sm">Save draft</button>}
              {(savingDraft || draftSaved) && !locked && !saveError && (
                <span className="text-[11px] font-semibold text-foreground-500">
                  <AppIcon className={savingDraft ? 'ri-loader-4-line mr-1 animate-spin' : 'ri-check-line mr-1 text-emerald-600'} />
                  {savingDraft ? 'Saving…' : 'Draft saved'}
                </span>
              )}
              {locked && (
                <span className="rounded-full bg-emerald-100 px-3 py-1.5 text-[11px] font-bold text-emerald-700">
                  <AppIcon className="ri-checkbox-circle-line mr-1" />{imported ? 'Historical submission' : 'Submitted'}
                </span>
              )}
              {locked && (
                <button
                  type="button"
                  onClick={() => setPreviewOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-primary-200 bg-white px-3 py-2 text-xs font-bold text-primary-700 hover:bg-primary-50"
                >
                  <AppIcon className="ri-eye-line" />Preview
                </button>
              )}
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-700">
            <label>Submission month <input aria-label="Submission month" type="month" value={monthly.month} disabled={readOnly || savingDraft} onChange={e => { if (e.target.value) changeMonthly(m => ({ ...m, month: e.target.value, meetingKey: '', presentationToken: '' })); }} className="ml-2 rounded-lg border border-slate-200 px-2 py-1" /></label>
            <span>Step {step + (assignmentTopicId ? 2 : 1)} of {assignmentTopicId ? 9 : 8} — {steps[step]}</span>
            <span>{imported ? 'Historical record — new submission checks do not apply' : checks.length ? `${checks.filter(c => c.passed).length}/13 checks passed at last check` : 'Quality checks not run yet'}</span>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-slate-900 transition-all" style={{ width: `${(step + 1) / 8 * 100}%` }} /></div>
          <div className="mt-4 flex flex-wrap gap-2">
            {/* Always available: the saved draft or submission is unchanged by reading the instructions again. */}
            {onShowInstructions && <button type="button" onClick={onShowInstructions} className="rounded-lg border px-3 py-2 text-xs font-bold">1 Instructions</button>}
            {steps.map((label, index) => (
              <button
                key={label}
                type="button"
                onClick={() => setStep(index)}
                className={`rounded-lg border px-3 py-2 text-left text-xs shadow-sm transition-colors ${step === index ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700 hover:bg-sky-50'}`}
              >
                <span className="flex items-center gap-2 text-[11px] font-bold">
                  <span className={`grid h-6 w-6 place-items-center rounded-full ${step === index ? 'bg-white/15 text-white' : 'bg-background-200 text-foreground-500'}`}>{index + (assignmentTopicId ? 2 : 1)}</span>
                  <span>{label}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="p-5 sm:p-6">
          {recoveredDraft && <p role="status" className="mb-4 rounded-xl bg-blue-50 p-3 text-sm text-blue-900">Recovered your most recent unsaved changes from this tab. Use Save draft to confirm they are stored on the server.</p>}
          {locked && (
            <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
              {imported ? (historicalContent ? 'Original assignment content is organised into the eight sections below. Extracts retain their source; tutor observations are labelled separately. This remains a historical submission.' : 'Historical assignment. Original files and assessment reports are preserved in Preview. Sections not captured in the original record are left blank; new submission requirements do not apply.') : 'This assignment has been submitted for tutor review. Your saved answers and evidence remain available in Preview.'}
            </div>
          )}

          {/* The question stays in view on every step; learners can fold it away. */}
          {!historicalContent && <details open className="mb-5 rounded-xl border border-background-200 bg-white p-4">
            <summary className={`cursor-pointer ${briefLabelClass}`}>Assignment brief & instructions</summary>
            <div className="mt-3 space-y-4">
              <section aria-label="Assignment question">
                <p className={briefLabelClass}>Assignment question</p>
                <div className="mt-2 rounded-xl border border-background-200 bg-background-50 p-4 text-sm leading-6 text-foreground-800">
                  {cleanQuestionHtml ? (
                    <div className="max-w-none [&_p]:mb-3 [&_ul]:list-disc [&_ul]:pl-5" dangerouslySetInnerHTML={questionInnerHtml} />
                  ) : (
                    <p className="whitespace-pre-line">{questionText || (questionAttachments.length ? 'Preview the attached file for your assignment question.' : 'Your tutor has not added the assignment question yet.')}</p>
                  )}
                </div>
              </section>
              <AssignmentGuidance instructions={instructionsHtml} links={resourceLinks} />
            </div>
          </details>}

          {historicalContent ? <HistoricalAssignmentCards content={orderedHistoricalContent!} step={step} files={evidenceFiles} onPreviewFile={file => { setPreviewOpen(true); void openEvidence(file); }} /> : step === 0 && (
            <div className="space-y-5">
              {questionAttachments.map(file => <AssignmentAttachment key={file.url} url={file.url} fileName={file.fileName} title={file.label || title} defaultExpanded pdfTools />)}
              <MonthlyAnswerField qualityTarget="answer" title={title} label="Your answer (at least 120 words; one point per line)" value={answers.assignmentAnswer} onChange={value => setAnswer('assignmentAnswer', value)} disabled={readOnly || submittingRef.current} rows={10} minimumWords={120} onePointPerLine generation={{ enabled: learningGeneration.canGenerate, busy: learningGeneration.generating, onGenerate: () => { if (!(answers.whatYouLearned || monthly.understood || monthly.gainedSkills) || window.confirm('Replace the three learning statements with new drafts from your answer?')) void learningGeneration.generate(); } }} />
              <p className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm leading-6 text-blue-900">Write at least 120 words, then click Generate learning statements to draft the three fields below. Review and edit the generated text before submitting.</p>
              {learningGeneration.status && <div
                className={`mt-4 flex items-start gap-3 rounded-xl border p-4 sm:p-5 ${learningGeneration.phase === 'error' ? 'border-amber-300 bg-amber-50 text-amber-950' : learningGeneration.phase === 'success' ? 'border-emerald-300 bg-emerald-50 text-emerald-950' : 'border-blue-300 bg-blue-50 text-blue-950'}`}
                role={learningGeneration.phase === 'error' ? 'alert' : 'status'}
                aria-live="polite"
              >
                <AppIcon className={`mt-0.5 shrink-0 text-xl ${learningGeneration.phase === 'loading' ? 'ri-loader-4-line animate-spin' : learningGeneration.phase === 'success' ? 'ri-checkbox-circle-line' : 'ri-error-warning-line'}`} />
                <div className="min-w-0">
                  <p className="text-sm font-bold sm:text-base">{learningGeneration.phase === 'loading' ? 'Preparing your learning statements' : learningGeneration.phase === 'success' ? 'Your learning statements have been updated' : 'Learning statements could not be generated'}</p>
                  <p className="mt-1 text-sm leading-relaxed">{learningGeneration.status}</p>
                </div>
              </div>}
            </div>
          )}

          {!historicalContent && <div className="mt-5">
            {historicalReadOnly && step === 7 ? <p className="text-sm text-slate-600">This historical submission keeps its original assessment status. New-form completeness checks do not apply.</p> : <MonthlyAssignmentSteps question={questionHtml || questionText || ''} activityId={componentId} step={step} data={monthly} onChange={changeMonthly} answers={answers} onAnswer={setAnswer}
              kind={kind} learnerId={learnerId} title={title} plannedOtjh={plannedOtjh} mappings={ksbMappings}
              evidenceFiles={evidenceFiles} timeControl={timeControl} disabled={readOnly || submittingRef.current} historical={historicalReadOnly}
              evidenceUploader={historicalReadOnly ? <button type="button" className="text-sm font-semibold text-primary-700" onClick={() => setPreviewOpen(true)}>View original files and assessment reports in Preview</button> : <AssignmentEvidence kind={kind} learnerId={learnerId} componentId={componentId} trainingPlanDetails={evidenceDetails} onUploaded={onEvidenceChanged} readOnly={readOnly} />}
              payload={() => payload('draft')} checks={checks} checking={checking} onCheck={runChecks} onSave={saveDraft}
              revisingRejected={status === 'rejected' || (status === 'draft' && submissionAttempts.at(-1)?.status === 'rejected')}
              onNavigateToCheck={(nextStep, target) => { setQualityTarget(target); setStep(nextStep); }}
            />}
          </div>}

          {saveError && (
            <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">{saveError}</p>
          )}
          {loadFailed && <button type="button" onClick={() => window.location.reload()} className="mt-3 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold">Reload saved submission</button>}

          {!locked && step < 7 && !stepCheck.ready && <div id="assignment-step-required" role="status" aria-live="polite" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            {stepCheck.pending ? <p>Checking this step's requirements...</p> : stepCheck.error ? <><p>{stepCheck.error}</p><button type="button" className="mt-2 underline" onClick={stepCheck.retry}>Retry step check</button></> : <><p className="font-semibold">Complete the following before selecting Next:</p><ul className="mt-2 list-disc space-y-1 pl-5">{stepCheck.missing.map(check => <li key={check.key}>{check.label}</li>)}</ul></>}
          </div>}
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-background-200 pt-4">
            <button
              type="button"
              onClick={() => setStep(current => Math.max(0, current - 1))}
              disabled={step === 0}
              className="inline-flex items-center gap-1.5 rounded-xl border border-background-300 bg-white px-4 py-2.5 text-sm font-semibold text-foreground-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <AppIcon className="ri-arrow-left-line" />Back
            </button>
            <div className="flex min-w-0 flex-1 flex-col items-end gap-3 sm:flex-row sm:items-center sm:justify-end">
              <div className="shrink-0">
                {step < 7 ? (
                  <button
                    type="button"
                    onClick={() => void goNext()}
                    disabled={loadFailed || savingDraft || submittingProgress || !stepCheck.ready}
                    aria-describedby={!locked && !stepCheck.ready ? 'assignment-step-required' : undefined}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Next<AppIcon className="ri-arrow-right-line" />
                  </button>
                ) : locked ? (
                  <button type="button" onClick={() => setPreviewOpen(true)} className="inline-flex items-center gap-1.5 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-bold text-white">
                    <AppIcon className="ri-eye-line" />Preview submission
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void submit()}
                    aria-describedby={!learningConfirmed ? 'learning-confirmation-required' : undefined}
                    disabled={loadFailed || checking || savingDraft || submittingProgress || !learningConfirmed || checks.length !== 13 || checks.some(check => !check.passed)}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-bold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <AppIcon className={savingDraft || submittingProgress ? 'ri-loader-4-line animate-spin' : 'ri-send-plane-fill'} />
                    {savingDraft || submittingProgress ? 'Submitting…' : 'Submit assignment'}
                  </button>
                )}
              </div>
            </div>
            {!locked && step === 7 && !learningConfirmed && <p id="learning-confirmation-required" className="w-full text-sm text-amber-800">Confirm all four learning declarations in Step 3 before submitting your assignment.</p>}
          </div>
        </div>
      </section>

      {previewOpen && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={closeSubmissionPreview}>
          <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl" onClick={event => event.stopPropagation()}>
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-background-200 bg-white px-5 py-4">
              <div className="flex min-w-0 items-center gap-3">
                {evidencePreview && (
                  <button
                    type="button"
                    onClick={() => setEvidencePreview(null)}
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-background-300 text-foreground-600 hover:bg-background-100"
                    aria-label="Back to submission preview"
                  >
                    <AppIcon className="ri-arrow-left-line" />
                  </button>
                )}
                <div className="min-w-0">
                  <p className="text-[10px] font-black uppercase tracking-wider text-primary-600">{evidencePreview ? 'Evidence preview' : 'Submission preview'}</p>
                  <h2 className="mt-1 truncate font-heading text-lg font-bold text-foreground-950">{evidencePreview ? evidencePreview.file.filename : title}</h2>
                </div>
              </div>
              <button type="button" onClick={closeSubmissionPreview} className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-background-100 text-foreground-600 hover:bg-background-200">
                <AppIcon className="ri-close-line" />
              </button>
            </div>
            {evidencePreview ? (
              <div className="min-h-0 flex-1 overflow-auto bg-background-950/95 p-4">
                {(() => {
                  if (renderEvidencePreview) return renderEvidencePreview(evidencePreview.file, evidencePreview.url);
                  const fileName = evidencePreview.file.filename.toLowerCase();
                  const contentType = (evidencePreview.file.contentType || '').toLowerCase();
                  const isImage = contentType.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|svg)$/.test(fileName);
                  const isVideo = contentType.startsWith('video/') || /\.(mp4|webm|ogg|mov|m4v)$/.test(fileName);
                  return isImage ? (
                    <img src={evidencePreview.url} alt={evidencePreview.file.filename} className="mx-auto max-h-[72vh] max-w-full rounded-lg object-contain" />
                  ) : isVideo ? (
                    <video src={evidencePreview.url} controls className="mx-auto max-h-[72vh] max-w-full rounded-lg bg-black" />
                  ) : (
                    <iframe title={evidencePreview.file.filename} src={evidencePreview.url} referrerPolicy="no-referrer" sandbox={evidencePreview.file.id.startsWith('link:') ? 'allow-scripts allow-forms' : undefined} className="h-[72vh] w-full rounded-lg bg-white" />
                  );
                })()}
              </div>
            ) : (
              <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5 sm:p-6">
                {historicalContent ? <HistoricalAssignmentCards content={orderedHistoricalContent!} files={evidenceFiles} onPreviewFile={file => void openEvidence(file)} /> : <>{[
                  ['1. Assignment answer', answers.assignmentAnswer],
                  ['2. What You Learned / Achieved KSBs', answers.whatYouLearned],
                  ['3. Business Impact', answers.businessImpact],
                  ['I understood', monthly.understood],
                  ['I gained skills in', monthly.gainedSkills],
                  ['Full-month LMS reflection', monthly.lmsReflection],
                  ['Additional activities', monthly.extraActivities],
                  ['Integrated understanding', monthly.integratedReflection],
                  ['Career impact', monthly.careerImpact],
                  ['Job impact', monthly.jobImpact],
                  ['Employer impact', monthly.employerImpact],
                  ['Action plan', monthly.actionPlan],
                  ['EPA preparedness', monthly.epaPreparedness],
                ].map(([label, value]) => (
                  <section key={label} className="rounded-xl border border-background-200 bg-background-50 p-4">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-primary-700">{label}</h3>
                    <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground-800">{value || (imported ? 'Not captured as a separate field in the original submission. See the original assignment file below.' : 'Not answered yet.')}</p>
                  </section>
                ))}
                <section className="rounded-xl border border-background-200 p-4 text-sm">
                  <h3 className="font-bold">Submission details</h3>
                  <p>Month: {monthly.month} · Time: {savedTimeSeconds ? `${(savedTimeSeconds / 3600).toFixed(2)} hours` : imported ? 'See original source record' : '0 hours'}</p>
                  <p>Coaching reference: {monthly.meetingKey || 'Not linked'}</p>
                  {monthly.timeEntries?.map((entry, index) => <p key={index} className="mt-2"><strong>{entry.topic}</strong>: {entry.hours} hours · {entry.date}</p>)}
                  {monthly.claims.map(c => <p key={c.code} className="mt-2 whitespace-pre-wrap"><strong>{c.code}</strong>: {c.explanation}</p>)}
                </section>
                {monthly.slides.map((slide, index) => <section key={index} className="rounded-xl border border-background-200 p-4"><h3 className="font-bold">Slide {index + 1}: {slide.title}</h3><p className="mt-3 whitespace-pre-wrap text-sm">{slide.body}</p></section>)}
                {monthly.evidence.map(entry => <section key={entry.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-background-200 p-4 text-sm"><div><strong>{entry.name}</strong><p>Supports answer points: {entry.points}</p></div><button type="button" className="font-semibold text-primary-700" onClick={() => {
                  if (entry.url && /^https?:\/\//i.test(entry.url)) {
                    setEvidencePreview({ file: { id: entry.id, filename: entry.name, contentType: '', sizeBytes: 0, status: 'approved', scanResult: null, sectionRef: componentId, uploadedAt: null, trainingPlanDetails: null }, url: entry.url });
                  } else void openEvidence(evidenceFiles.find(f => f.id === entry.id) || { id: entry.id, filename: entry.name, contentType: '', sizeBytes: 0, status: 'approved', scanResult: null, sectionRef: componentId, uploadedAt: null, trainingPlanDetails: null });
                }}>Preview evidence</button></section>)}
                <section className="rounded-xl border border-background-200 bg-background-50 p-4">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-primary-700">Evidence</h3>
                  {evidenceFiles.length === 0 ? (
                    <p className="mt-3 text-sm text-foreground-500">No evidence files attached.</p>
                  ) : (
                    <ul className="mt-3 space-y-2">
                      {evidenceFiles.map(file => (
                        <li key={file.id} className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2">
                          <span className="min-w-0 truncate text-sm font-semibold text-foreground-800"><AppIcon className="ri-attachment-2 mr-2 text-primary-600" />{file.filename}</span>
                          <button type="button" onClick={() => void openEvidence(file)} disabled={file.status !== 'approved' || openingEvidenceId === file.id} className="shrink-0 text-xs font-bold text-primary-700 disabled:text-foreground-300">
                            {openingEvidenceId === file.id ? 'Opening\u2026' : file.status === 'approved' ? 'Preview' : file.status}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                </>}
                {saveError && (
                  <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">{saveError}</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </AssignmentAiCheckContext.Provider>
  );
}

export function AssignmentSubmissionWizard(props: ComponentProps<typeof AssignmentSubmissionForm> & { topics?: AssignmentTopic[] }) {
  if (props.topics?.length && !props.historicalReadOnly && !parsePersonalLearning(props.learnerId)) {
    return <TopicAssignment key={`${props.kind}:${props.learnerId}:${props.componentId}`} {...props} topics={props.topics} />;
  }
  const only = props.topics?.length === 1 ? presentTopic(props.topics[0]) : null;
  return <AssignmentSubmissionForm {...props} instructionsHtml={props.instructionsHtml ?? only?.instructions}
    resourceLinks={props.resourceLinks ?? only?.links} />;
}
