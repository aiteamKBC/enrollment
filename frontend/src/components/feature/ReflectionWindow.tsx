import { formatClock, parseClockSeconds, formatRecordedClock } from './ReflectionWindow.helpers';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentKsbMapping, LearnerKind, LearnerKsbItem } from '@/api/learnerDetail';
import { uploadEvidence } from '@/api/evidence';
import { proofreadLearningReflection, transcribeVoiceReflection } from '@/api/reflectionVoice';
import {
  loadLearningReflectionSubmission,
  type LearningReflectionSubmissionInput,
  type StoredLearningReflectionSubmission,
} from '@/api/reflectionSubmission';
import { useReflectionAutosave } from './useReflectionAutosave';
import { useManualTimeConfirmation } from '@/hooks/useManualTimeConfirmation';

/** The saved reflection always stores hours; show it in the field's unit. */
function savedActualTime(hours: string, inMinutes: boolean): string {
  if (!inMinutes || !hours.trim()) return hours;
  const value = Number(hours);
  return Number.isFinite(value) && value >= 0 ? String(Number((value * 60).toFixed(2))) : '';
}

function formatKsbWeight(weight: number): string {
  const rounded = Math.round((Number(weight) || 0) * 10) / 10;
  return `${rounded}%`;
}

const TABS = [
  { id: 'learning', label: 'Learning' },
  { id: 'ksbs', label: 'KSBs' },
  { id: 'apply', label: 'Apply' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'benefit', label: 'Benefit' },
  { id: 'otjh', label: 'OTJH' },
  { id: 'review', label: 'Review' },
] as const;

type TabId = (typeof TABS)[number]['id'];

const KSB_GROUPS = [
  { key: 'K', label: 'Knowledge' },
  { key: 'S', label: 'Skills' },
  { key: 'B', label: 'Behaviours' },
] as const;

type KsbGroupKey = (typeof KSB_GROUPS)[number]['key'];

const BENEFITS = [
  'Improved productivity',
  'Better customer service',
  'Better marketing performance',
  'Better project planning or control',
  'Improved communication',
  'Better data use',
  'Reduced errors',
  'Better compliance',
  'Cost saving',
  'Improved quality',
  'Improved teamwork',
  'Stronger innovation',
];

interface ReflectionSubmission {
  ksbs: string[];
  feedback: string;
  reportedTime: string;
  confidenceBefore?: Record<string, number>;
  confidenceAfter?: Record<string, number>;
  ksbExplanations?: Record<string, string>;
  applicationType?: string;
  applicationText?: string;
  evidenceFiles?: string[];
  coachVisibilityConfirmed?: boolean;
  selectedBenefits?: string[];
  benefitExplanation?: string;
  completedDuringPaidHours?: string;
  dateCompleted?: string;
  signedDeclaration?: boolean;
  timeTakenSeconds?: number;
  timeEntrySource?: 'input';
}

export function ReflectionWindow(props: Parameters<typeof ReflectionWindowForm>[0]) {
  return <ReflectionWindowForm key={`${props.learnerKind}:${props.learnerId}:${props.noun}:${props.evidenceSectionRef}`} {...props} />;
}

function ReflectionWindowForm({
  learnerKsbs,
  plannedTimeLabel,
  plannedHours: plannedHoursProp,
  actualTimeUnit = 'hours',
  selectedTimeSeconds,
  selectedTimeSource,
  noun = 'quiz',
  submitting,
  submitError,
  onSubmit,
  autoKsbs,
  activityTitle = '',
  weekLabel = '',
  moduleLabel = '',
  learnerName = 'Learner',
  programmeName = 'Programme not set',
  learnerKind,
  learnerId,
  evidenceSectionRef,
  reflectionQuestion,
  onClose,
}: {
  learnerKsbs: LearnerKsbItem[];
  elapsedSeconds: number;
  plannedTimeLabel: string;
  /**
   * The planned time as a number of **hours**, used to preset "Actual time
   * spent (hours)".
   *
   * Separate from `plannedTimeLabel` because that is only ever a display
   * string, and its unit depends on which fallback produced it — a component's
   * authored OTJH renders as "1h 42m", a real video length as "01:42:25" (HH:MM:SS),
   * an authored duration as "45 min". Reading a number back out of that text
   * cannot tell hours from minutes, so a 102-minute video was preset as 102
   * hours and submitted as such. Callers now convert to hours once, here.
   */
  plannedHours?: number;
  /**
   * Unit of the "Actual time spent" field. Quizzes use minutes because their
   * planned time is shown in minutes; a bare "14" typed beside "60 minutes"
   * was otherwise read as 14 hours. Minutes are submitted with an explicit
   * unit ("14 minutes") so every OTJ parser reads them the same way, and the
   * reflection record still receives hours.
   */
  actualTimeUnit?: 'hours' | 'minutes';
  /** Timer/Input duration already selected on the activity completion screen. */
  selectedTimeSeconds?: number;
  selectedTimeSource?: 'timer' | 'input';
  noun?: string;
  submitting: boolean;
  submitError: string | null;
  onSubmit: (result: ReflectionSubmission) => void | Promise<void>;
  autoKsbs?: ComponentKsbMapping[];
  activityTitle?: string;
  weekLabel?: string;
  moduleLabel?: string;
  learnerName?: string;
  programmeName?: string;
  learnerKind?: LearnerKind;
  learnerId?: string;
  evidenceSectionRef?: string;
  reflectionQuestion?: string | null;
  onClose?: () => void;
}) {
  const applyQuestion = reflectionQuestion?.trim()
    || 'How can this learning be applied in your workplace?';
  // Two decimals, and no trailing ".00" — the field takes a plain hours number,
  // so "1.71" and "2" both read correctly while 102 minutes can no longer
  // arrive as 102 hours. Falls back to blank rather than to a parsed label: an
  // empty field is an honest "unknown", a wrong number is not.
  const inMinutes = actualTimeUnit === 'minutes';
  const plannedHours =
    plannedHoursProp != null && Number.isFinite(plannedHoursProp) && plannedHoursProp > 0
      ? String(Number((inMinutes ? plannedHoursProp * 60 : plannedHoursProp).toFixed(2)))
      : '';
  const [tab, setTab] = useState<TabId>('learning');
  const [reflection, setReflection] = useState('');
  const [selectedKsbs, setSelectedKsbs] = useState<string[]>([]);
  const [ksbExplanations, setKsbExplanations] = useState<Record<string, string>>({});
  const [confidenceBefore, setConfidenceBefore] = useState<Record<string, number>>({});
  const [confidenceAfter, setConfidenceAfter] = useState<Record<string, number>>({});
  const [applicationType, setApplicationType] = useState('');
  const [applicationText, setApplicationText] = useState('');
  const [evidenceFiles, setEvidenceFiles] = useState<File[]>([]);
  const [storedEvidenceFileNames, setStoredEvidenceFileNames] = useState<string[]>([]);
  const [coachVisibilityConfirmed, setCoachVisibilityConfirmed] = useState(false);
  const [selectedBenefits, setSelectedBenefits] = useState<string[]>([]);
  const [benefitExplanation, setBenefitExplanation] = useState('');
  const [actualTime, setActualTime] = useState(plannedHours);
  const [dateCompleted, setDateCompleted] = useState(new Date().toISOString().split('T')[0]);
  const [paidHours, setPaidHours] = useState('yes');
  const [otjhConfirmed, setOtjhConfirmed] = useState(false);
  const [signedDeclaration, setSignedDeclaration] = useState(false);
  const [aiChecked, setAiChecked] = useState(false);
  const [editedClock, setEditedClock] = useState<string | null>(null);
  const [timeEdited, setTimeEdited] = useState(false);
  const [recording, setRecording] = useState(false);
  const [voiceProcessing, setVoiceProcessing] = useState(false);
  const [voiceError, setVoiceError] = useState('');
  const [proofreading, setProofreading] = useState(false);
  const [evidenceUploading, setEvidenceUploading] = useState(false);
  const [evidenceUploadError, setEvidenceUploadError] = useState('');
  const [reflectionSaving, setReflectionSaving] = useState(false);
  const [reflectionSaveError, setReflectionSaveError] = useState('');
  const [submissionStatus, setSubmissionStatus] = useState('');
  // Accepted reflections are historical, read-only records. A new activity
  // selection must not replace their saved duration when they are reopened.
  const initialSelectedSeconds = submissionStatus !== 'accepted'
    && selectedTimeSeconds != null && Number.isFinite(selectedTimeSeconds)
    ? Math.max(0, Math.floor(selectedTimeSeconds)) : null;
  const usesClock = initialSelectedSeconds != null;
  const clockValue = editedClock ?? (usesClock ? formatClock(initialSelectedSeconds) : '');
  const selectedSeconds = usesClock && /^\d+:[0-5]\d:[0-5]\d$/.test(clockValue)
    ? parseClockSeconds(clockValue) : null;
  const actualTimeHours = () => {
    if (usesClock) return selectedSeconds == null ? '' : String(selectedSeconds / 3600);
    const value = actualTime.trim();
    return inMinutes && value ? String(Number((Number(value) / 60).toFixed(4))) : value;
  };
  const [coachFeedback, setCoachFeedback] = useState<string | null>(null);
  const [loadingExisting, setLoadingExisting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordingTimeoutRef = useRef<number | null>(null);
  const submissionInFlightRef = useRef(false);
  const { confirmTime, timeConfirmation } = useManualTimeConfirmation();
  const timeInputRef = useRef<HTMLInputElement>(null);

  const draftKey = learnerKind && learnerId && evidenceSectionRef
    ? `reflection-draft:${learnerKind}:${learnerId}:${noun}:${evidenceSectionRef}` : null;
  const [loadedDraftKey, setLoadedDraftKey] = useState<string | null>(null);
  const [serverLoaded, setServerLoaded] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [localRecoveryAvailable, setLocalRecoveryAvailable] = useState(true);

  const hasAuthoredKsbs = Array.isArray(autoKsbs) && autoKsbs.length > 0;
  // Pick relevant codes, not one from every K/S/B category. Authored mappings
  // constrain the choices; otherwise offer the programme's KSBs.
  const selectingFromProgramme = !hasAuthoredKsbs;
  const availableKsbs = useMemo(() => hasAuthoredKsbs ? autoKsbs! : learnerKsbs.map(item => ({
    code: item.code, description: item.description, weight: 0, classification: 'possible',
  })), [autoKsbs, hasAuthoredKsbs, learnerKsbs]);
  const selectionRequired = availableKsbs.length > 0;

  const [ksbGroup, setKsbGroup] = useState<KsbGroupKey>('K');
  const programmeKsbGroups = useMemo(() => {
    const groups: Record<KsbGroupKey, typeof availableKsbs> = { K: [], S: [], B: [] };
    for (const item of availableKsbs) {
      const kind = String(item.code || '').trim().charAt(0).toUpperCase();
      // Anything that is not an S or B code is listed with Knowledge so it can
      // still be selected.
      groups[kind === 'S' || kind === 'B' ? kind : 'K'].push(item);
    }
    return groups;
  }, [availableKsbs]);
  const activeKsbGroup = programmeKsbGroups[ksbGroup].length
    ? ksbGroup
    : KSB_GROUPS.find(group => programmeKsbGroups[group.key].length)?.key ?? ksbGroup;

  const mappedKsbs = useMemo(() => {
    return availableKsbs.filter(item => selectedKsbs.includes(item.code));
  }, [availableKsbs, selectedKsbs]);

  const mappedKsbWeightSummary = useMemo(() => mappedKsbs.reduce(
    (summary, item) => {
      const weight = Number(item.weight) || 0;
      const kind = String(item.code || '').trim().charAt(0).toUpperCase();
      summary.total += weight;
      if (kind === 'K' || kind === 'S' || kind === 'B') summary[kind] += weight;
      return summary;
    },
    { total: 0, K: 0, S: 0, B: 0 },
  ), [mappedKsbs]);

  const ksbCodes = mappedKsbs.map(item => item.code);
  const ksbWeights = Object.fromEntries(
    mappedKsbs.map(item => [item.code, Number(item.weight) || 0]),
  );
  const wordCount = reflection.trim().split(/\s+/).filter(Boolean).length;
  const learningReady = wordCount >= 100;
  const ksbReady = ksbCodes.length === 0
    ? !selectionRequired
    : ksbCodes.every(code =>
      Boolean(ksbExplanations[code]?.trim())
      && confidenceBefore[code] !== undefined
      && confidenceAfter[code] !== undefined
    );
  const applicationReady = Boolean(applicationType && applicationText.trim());
  const allEvidenceFileNames = [
    ...storedEvidenceFileNames,
    ...evidenceFiles.map(file => file.name),
  ];
  const evidenceReady = allEvidenceFileNames.length === 0 || coachVisibilityConfirmed;
  const benefitReady = selectedBenefits.length > 0 && Boolean(benefitExplanation.trim());
  const validTime = usesClock ? selectedSeconds != null
    : actualTime.trim() !== '' && Number.isFinite(Number(actualTime)) && Number(actualTime) >= 0;
  const otjhReady = Boolean(validTime && dateCompleted && paidHours && otjhConfirmed);
  const canSubmit = learningReady && ksbReady && applicationReady && evidenceReady
    && benefitReady && otjhReady && signedDeclaration;

  const readiness: Record<TabId, boolean> = {
    learning: learningReady,
    ksbs: ksbReady,
    apply: applicationReady,
    evidence: evidenceReady,
    benefit: benefitReady,
    otjh: otjhReady,
    review: canSubmit,
  };

  const checklist = [
    { label: 'Reflection (≥ 100 words)', complete: learningReady },
    {
      label: selectionRequired
        ? 'Selected KSBs explained and rated (any K, S or B)'
        : ksbCodes.length ? 'Mapped KSBs explained and rated' : 'No mapped KSBs to rate',
      complete: ksbReady,
    },
    { label: 'Workplace application or support request', complete: applicationReady },
    { label: 'Consent confirmed (if evidence uploaded)', complete: evidenceReady },
    { label: 'Employer benefit selected and explained', complete: benefitReady },
    { label: 'OTJH confirmed', complete: otjhReady },
    { label: 'Signed declaration', complete: signedDeclaration },
  ];

  const qualityChecks = [
    learningReady,
    ksbReady,
    applicationReady,
    benefitReady,
    otjhReady,
    signedDeclaration,
  ];
  const qualityScore = Math.round(
    (qualityChecks.filter(Boolean).length / qualityChecks.length) * 100,
  );
  const qualitySuggestions = [
    ...(wordCount === 0
      ? ['No learning reflection has been provided']
      : !learningReady
        ? [`Increase the reflection to at least 100 words (currently ${wordCount})`]
        : []),
    ...(!ksbReady
      ? [selectionRequired && ksbCodes.length === 0
        ? 'Select at least one relevant KSB; you do not need one from each category'
        : 'Explain and rate each selected KSB']
      : []),
    ...(!applicationReady ? ['Describe workplace application or the support you need'] : []),
    ...(!benefitReady ? ['Select and explain an employer or business benefit'] : []),
    ...(!otjhReady ? ['Complete and confirm the OTJH record'] : []),
    ...(allEvidenceFileNames.length > 0 && !coachVisibilityConfirmed
      ? ['Confirm permission and consent for the uploaded evidence']
      : []),
    ...(!signedDeclaration ? ['Confirm the learner declaration and signature'] : []),
  ];
  const qualitySummary = qualityScore === 100
    ? 'Your reflection is complete and clearly demonstrates your learning, workplace application and employer benefit.'
    : wordCount === 0
      ? 'This reflection is currently empty and requires your input to demonstrate your learning and its application.'
      : 'Your reflection has a good start, but some areas still need attention before it is ready for tutor review.';

  const activeIndex = TABS.findIndex(item => item.id === tab);
  const completedSections = TABS.filter(item => readiness[item.id]).length;
  const progress = Math.round((completedSections / TABS.length) * 100);
  const submissionLocked = submissionStatus === 'accepted';
  const draftPayload: LearningReflectionSubmissionInput | null = learnerKind && learnerId && evidenceSectionRef ? {
    submissionMode: 'draft', learnerKind, learnerId, learnerName, programmeName,
    activityType: noun, activityId: evidenceSectionRef, activityTitle,
    moduleTitle: moduleLabel, weekTitle: weekLabel, plannedOtjh: plannedTimeLabel,
    learningReflection: reflection, ksbCodes, ksbWeights, ksbExplanations,
    confidenceBefore, confidenceAfter, applicationType, applicationText,
    evidenceFiles: allEvidenceFileNames, evidenceConsentConfirmed: coachVisibilityConfirmed,
    selectedBenefits, benefitExplanation, actualTimeHours: actualTimeHours(), reflectionTimeEdited: timeEdited,
    completedDuringPaidHours: paidHours, dateCompleted, otjhConfirmed, signedDeclaration, qualityScore,
  } : null;
  const draftSerialized = JSON.stringify(draftPayload);
  const editableDraft = !submissionLocked && submissionStatus !== 'submitted_for_tutor_review';
  const autosave = useReflectionAutosave(draftPayload,
    Boolean(draftKey && loadedDraftKey === draftKey && serverLoaded && !loadingExisting && editableDraft));

  useEffect(() => {
    if (!learnerKind || !learnerId || !evidenceSectionRef) return;

    let cancelled = false;
    setLoadingExisting(true);
    setServerLoaded(false);
    setReflectionSaveError('');

    let localDraft: (Partial<StoredLearningReflectionSubmission> & { tab?: TabId; synced?: boolean }) | null = null;
    try {
      const raw = draftKey ? localStorage.getItem(draftKey) : null;
      localDraft = raw ? JSON.parse(raw) : null;
    } catch { /* Server loading still works when browser storage is unavailable. */ }

    const restore = (saved: Partial<StoredLearningReflectionSubmission> & { tab?: TabId }) => {
      if (saved.tab && TABS.some(item => item.id === saved.tab)) setTab(saved.tab);
      setSubmissionStatus(saved.status || '');
      setCoachFeedback(saved.coachFeedback || null);
      setReflection(saved.learningReflection || '');
      setSelectedKsbs(Array.isArray(saved.ksbCodes) ? saved.ksbCodes : []);
      setKsbExplanations(saved.ksbExplanations || {});
      setConfidenceBefore(saved.confidenceBefore || {});
      setConfidenceAfter(saved.confidenceAfter || {});
      setApplicationType(saved.applicationType || '');
      setApplicationText(saved.applicationText || '');
      setStoredEvidenceFileNames(Array.isArray(saved.evidenceFiles) ? saved.evidenceFiles : []);
      setCoachVisibilityConfirmed(Boolean(saved.evidenceConsentConfirmed));
      setSelectedBenefits(Array.isArray(saved.selectedBenefits) ? saved.selectedBenefits : []);
      setBenefitExplanation(saved.benefitExplanation || '');
      setActualTime(savedActualTime(saved.actualTimeHours || '', inMinutes) || plannedHours);
      setTimeEdited(Boolean(saved.reflectionTimeEdited));
      setEditedClock(saved.reflectionTimeEdited && saved.actualTimeHours?.trim() && Number.isFinite(Number(saved.actualTimeHours))
        ? formatClock(Math.round(Number(saved.actualTimeHours) * 3600)) : null);
      setPaidHours(saved.completedDuringPaidHours || 'yes');
      setDateCompleted(saved.dateCompleted || new Date().toISOString().split('T')[0]);
      setOtjhConfirmed(Boolean(saved.otjhConfirmed));
      setSignedDeclaration(Boolean(saved.signedDeclaration));
      setAiChecked((saved.qualityScore || 0) > 0);
    };

    void loadLearningReflectionSubmission({
      learnerKind,
      learnerId,
      activityType: noun,
      activityId: evidenceSectionRef,
    })
      .then(saved => {
        if (cancelled) return;
        setLoadedDraftKey(draftKey);
        setServerLoaded(true);
        if (localDraft && (!saved || !['accepted', 'submitted_for_tutor_review'].includes(saved.status))) {
          // A successfully saved browser copy must not overwrite a newer server
          // draft edited on another device. Keep its last-open section only.
          restore(saved && localDraft.synced ? { ...saved, tab: localDraft.tab }
            : { ...saved, ...localDraft, status: saved?.status || 'draft' });
        } else if (saved) {
          restore(saved);
          if (draftKey && ['accepted', 'submitted_for_tutor_review'].includes(saved.status)) {
            try { localStorage.removeItem(draftKey); } catch { /* The server record remains authoritative. */ }
          }
        }
      })
      .catch(error => {
        if (!cancelled) {
          if (localDraft) restore(localDraft);
          setLoadedDraftKey(draftKey);
          setReflectionSaveError(
            error instanceof Error ? error.message : 'Could not load the saved reflection.',
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingExisting(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [evidenceSectionRef, learnerId, learnerKind, noun, plannedHours, inMinutes, draftKey, loadAttempt]);

  // Persist each edit locally so leaving the activity does not lose progress.
  // Files themselves are uploaded by the evidence flow; only uploaded names are stored.
  useEffect(() => {
    if (!draftKey || loadedDraftKey !== draftKey || loadingExisting || submissionStatus === 'accepted'
      || submissionStatus === 'submitted_for_tutor_review') return;
    try {
      localStorage.setItem(draftKey, JSON.stringify({ ...JSON.parse(draftSerialized), tab, synced: autosave.saved }));
      setLocalRecoveryAvailable(true);
    } catch { setLocalRecoveryAvailable(false); }
  }, [draftKey, loadedDraftKey, loadingExisting, submissionStatus, tab, draftSerialized, autosave.saved]);

  useEffect(() => {
    if (!editableDraft || !draftKey || loadedDraftKey !== draftKey || autosave.saved) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [editableDraft, draftKey, loadedDraftKey, autosave.saved]);

  const handleNext = () => {
    const nextTab = TABS[activeIndex + 1];
    if (nextTab) setTab(nextTab.id);
  };

  const handleSubmit = async (mode: 'draft' | 'submit' = 'submit') => {
    if (submissionInFlightRef.current) return;
    if (submissionLocked) {
      setReflectionSaveError('This reflection has already been accepted and can no longer be changed.');
      return;
    }
    if (mode === 'submit' && !canSubmit) {
      const firstIncomplete = TABS.find(item => !readiness[item.id]);
      if (firstIncomplete && firstIncomplete.id !== 'review') setTab(firstIncomplete.id);
      return;
    }

    if (!draftPayload || !serverLoaded) {
      setReflectionSaveError('This activity is missing the learner details required to save the submission.');
      return;
    }

    const result: ReflectionSubmission = {
      ksbs: ksbCodes,
      feedback: reflection.trim(),
      reportedTime: selectedSeconds != null
        ? `${selectedSeconds / 60} minutes`
        : inMinutes && actualTime.trim() ? `${actualTime.trim()} minutes` : actualTime.trim(),
      ...(selectedSeconds != null ? { timeTakenSeconds: selectedSeconds }
        : timeEdited && validTime ? { timeTakenSeconds: Math.round(Number(actualTimeHours()) * 3600) } : {}),
      ...(timeEdited ? { timeEntrySource: 'input' as const } : {}),
      confidenceBefore,
      confidenceAfter,
      ksbExplanations,
      applicationType,
      applicationText: applicationText.trim(),
      evidenceFiles: allEvidenceFileNames,
      coachVisibilityConfirmed,
      selectedBenefits,
      benefitExplanation: benefitExplanation.trim(),
      completedDuringPaidHours: paidHours,
      dateCompleted,
      signedDeclaration,
    };

    submissionInFlightRef.current = true;
    if (mode === 'submit' && (timeEdited || selectedTimeSource === 'input' || !usesClock)
      && !await confirmTime(selectedSeconds ?? Math.round(Number(actualTimeHours()) * 3600))) {
      submissionInFlightRef.current = false;
      setTab('otjh');
      requestAnimationFrame(() => timeInputRef.current?.focus());
      return;
    }
    if (mode === 'submit') autosave.pause();
    setReflectionSaving(true);
    setReflectionSaveError('');
    try {
      const saved = await autosave.save({ ...draftPayload, submissionMode: mode,
        ...(mode === 'submit' ? {
          learningReflection: reflection.trim(), applicationText: applicationText.trim(), benefitExplanation: benefitExplanation.trim(),
        } : {}),
      });
      if (mode === 'submit') {
        setSubmissionStatus(saved.status || 'submitted_for_tutor_review');
        try { if (draftKey) localStorage.removeItem(draftKey); } catch { /* Storage unavailable. */ }
        await onSubmit(result);
      }
    } catch (error) {
      // Draft failures are displayed by autosave and cleared by its next
      // successful retry. Completion failures also need to remain visible.
      if (mode === 'submit') {
        setReflectionSaveError(error instanceof Error ? error.message : 'Could not save the reflection for tutor review.');
        autosave.resume();
      }
    } finally {
      submissionInFlightRef.current = false;
      setReflectionSaving(false);
    }
  };

  const proofreadReflection = async () => {
    if (!reflection.trim()) return;
    setProofreading(true);
    setVoiceError('');
    try {
      const result = await proofreadLearningReflection(reflection, {
        activityTitle,
        moduleLabel,
        weekLabel,
      });
      setReflection(result.text);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : 'The reflection could not be proofread.');
    } finally {
      setProofreading(false);
    }
  };

  const releaseMicrophone = useCallback(() => {
    microphoneStreamRef.current?.getTracks().forEach(track => track.stop());
    microphoneStreamRef.current = null;
    if (recordingTimeoutRef.current !== null) {
      window.clearTimeout(recordingTimeoutRef.current);
      recordingTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => () => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.onstop = null;
      mediaRecorderRef.current.stop();
    }
    releaseMicrophone();
  }, [releaseMicrophone]);

  const processVoiceRecording = async (audio: Blob) => {
    if (!audio.size) {
      setVoiceError('No audio was captured. Please try again.');
      return;
    }
    setVoiceProcessing(true);
    setVoiceError('');
    try {
      const result = await transcribeVoiceReflection(audio, {
        activityTitle,
        moduleLabel,
        weekLabel,
      });
      setReflection(previous => [previous.trim(), result.text.trim()].filter(Boolean).join('\n\n'));
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : 'The recording could not be processed.');
    } finally {
      setVoiceProcessing(false);
    }
  };

  const startRecording = async () => {
    setVoiceError('');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Voice recording is not supported by this browser.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      const preferredTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
      const mimeType = preferredTypes.find(type => MediaRecorder.isTypeSupported(type));
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      microphoneStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      recorder.ondataavailable = event => {
        if (event.data.size) audioChunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setVoiceError('The microphone recording failed. Please try again.');
        setRecording(false);
        releaseMicrophone();
      };
      recorder.onstop = () => {
        const audio = new Blob(audioChunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        audioChunksRef.current = [];
        setRecording(false);
        releaseMicrophone();
        void processVoiceRecording(audio);
      };
      recorder.start(1000);
      setRecording(true);
      recordingTimeoutRef.current = window.setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, 5 * 60 * 1000);
    } catch (error) {
      releaseMicrophone();
      setRecording(false);
      if (error instanceof DOMException && error.name === 'NotAllowedError') {
        setVoiceError('Microphone access was blocked. Allow microphone access and try again.');
      } else {
        setVoiceError('The microphone could not be started. Please try again.');
      }
    }
  };

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === 'recording') recorder.stop();
  };

  const handleEvidenceUpload = async (files: File[]) => {
    setEvidenceUploadError('');
    if (!learnerKind || !learnerId || !evidenceSectionRef) {
      setEvidenceUploadError('This activity is missing the learner details required for evidence upload.');
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    setEvidenceUploading(true);
    try {
      for (const file of files) {
        if (file.size > 50 * 1024 * 1024) {
          throw new Error(`${file.name} exceeds the 50 MB size limit.`);
        }
        await uploadEvidence(learnerKind, learnerId, file, evidenceSectionRef, {
          moduleTitle: moduleLabel || null,
          weekTitle: weekLabel || null,
          componentId: evidenceSectionRef,
          componentTitle: activityTitle || null,
          componentType: noun,
        });
        setEvidenceFiles(previous => [...previous, file]);
      }
    } catch (error) {
      setEvidenceUploadError(error instanceof Error ? error.message : 'Evidence upload failed.');
    } finally {
      setEvidenceUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const timeInput = () => (
    <div>
      <Field label={usesClock ? 'Selected activity time' : `Actual time spent (${inMinutes ? 'minutes' : 'hours'})`}>
        <input
          ref={timeInputRef}
          type={usesClock ? 'text' : 'number'}
          min={usesClock ? undefined : '0'}
          step={usesClock ? undefined : inMinutes ? '1' : '0.25'}
          value={usesClock ? clockValue : actualTime}
          onChange={event => {
            setTimeEdited(true);
            if (usesClock) setEditedClock(event.target.value);
            else setActualTime(event.target.value);
          }}
          aria-invalid={!validTime}
          placeholder={usesClock ? 'HH:MM:SS' : inMinutes ? 'e.g. 30' : 'e.g. 2'}
          className="h-11 w-full rounded-xl border border-foreground-200 bg-white px-3 font-mono text-sm focus:border-primary-400 focus:outline-none"
        />
      </Field>
      {!submissionLocked && <p className="mt-1 text-xs text-foreground-500">
        {usesClock && !validTime ? 'Enter hours, minutes and seconds, for example 00:30:00.'
          : 'You can increase or reduce this time. Your final value will be saved.'}
      </p>}
    </div>
  );

  return (
    <div className="w-full bg-[#f7fbff] rounded-2xl border border-[#bcc6d1] shadow-xl overflow-hidden">
      {timeConfirmation}
      <div className="p-5 md:px-8 md:pt-7 md:pb-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-[#607086]">
              {[weekLabel, moduleLabel, noun].filter(Boolean).join(' · ')}
            </p>
            <h1 className="mt-1 text-xl font-heading font-bold text-[#142033]">
              My Learning Evidence and Reflection
            </h1>
            <p className="mt-0.5 text-sm text-[#607086]">
              {activityTitle || `Complete your ${noun} reflection`}
            </p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close reflection"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[#607086] transition-colors hover:bg-[#e7eef5] hover:text-[#142033]"
            >
              <AppIcon className="ri-close-line text-xl" />
            </button>
          )}
        </div>

        <div className="mt-5 grid grid-cols-2 gap-4 rounded-xl border border-[#d4e0eb] bg-[#eef6fd] px-4 py-4 md:grid-cols-4 md:px-5">
          <MetaItem label="Learner" value={learnerName} />
          <MetaItem label="Programme" value={programmeName} />
          <MetaItem label="Planned OTJH" value={plannedTimeLabel || 'Not set'} />
          <MetaItem
            label="Selected KSBs"
            value={ksbCodes.length ? ksbCodes.join(', ') : 'None'}
          />
        </div>

        <div className="mt-7 flex items-center gap-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-[#cbd3dc]">
            <div className="h-full rounded-full bg-[#102d52] transition-all" style={{ width: `${progress}%` }} />
          </div>
          <span className="text-sm text-[#607086]">{progress}%</span>
        </div>

        <div className="mt-6 overflow-x-auto">
          <div className="grid min-w-[680px] grid-cols-7 rounded-xl bg-[#e7eef5]">
            {TABS.map(item => (
              <button
                key={item.id}
                onClick={() => setTab(item.id)}
                className={`rounded-xl px-3 py-2.5 text-sm transition-colors ${
                  tab === item.id
                    ? 'bg-[#f7fbff] font-medium text-[#142033] shadow-sm ring-2 ring-inset ring-[#3191cf]'
                    : readiness[item.id]
                      ? 'text-emerald-700'
                      : 'text-[#607086] hover:text-[#142033]'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loadingExisting && (
        <div className="border-t border-[#d7e0e8] bg-white px-5 py-3 text-sm text-[#607086] md:px-8">
          <AppIcon className="ri-loader-4-line mr-2 animate-spin" />
          Loading your saved reflection...
        </div>
      )}

      {submissionLocked && (
        <div className="border-t border-emerald-200 bg-emerald-50 px-5 py-4 md:px-8">
          <div className="flex items-start gap-3">
            <AppIcon className="ri-lock-2-line mt-0.5 text-lg text-emerald-700" />
            <div>
              <p className="text-sm font-semibold text-emerald-900">
                Accepted by your coach — this submission is now locked.
              </p>
              <p className="mt-1 text-xs leading-relaxed text-emerald-800">
                You can review the submitted information, but you cannot edit it, upload more evidence or submit it again.
              </p>
              {coachFeedback && (
                <p className="mt-2 rounded-lg border border-emerald-200 bg-white/70 px-3 py-2 text-sm text-emerald-900">
                  <strong>Coach feedback:</strong> {coachFeedback}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      <fieldset
        disabled={submissionLocked || loadingExisting || reflectionSaving || submitting}
        className="min-w-0 border-t border-[#d7e0e8] px-5 py-5 disabled:cursor-not-allowed md:px-8 md:py-6"
      >
        {tab === 'learning' && (
          <section>
            <div className="mb-5 max-w-sm">{timeInput()}</div>
            <h2 className="text-base font-semibold text-[#142033]">What have you learnt from this activity?</h2>
            <p className="mt-1 text-sm text-[#607086]">
              Explain the key ideas, methods, models, tools, concepts or techniques you have understood. Aim for at least 100 words.
            </p>
            <textarea
              value={reflection}
              onChange={event => setReflection(event.target.value)}
              rows={8}
              placeholder="Type or use the microphone..."
              className="mt-4 w-full resize-none rounded-xl border border-[#d3dee8] bg-[#f8fcff] px-4 py-3 text-base text-[#142033] shadow-sm focus:border-[#3191cf] focus:outline-none focus:ring-2 focus:ring-[#3191cf]/20"
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                onClick={recording ? stopRecording : startRecording}
                disabled={voiceProcessing}
                className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium shadow-sm ${
                  recording
                    ? 'border-red-300 bg-red-50 text-red-700'
                    : 'border-[#d3dee8] bg-[#f8fcff] text-[#24354a] disabled:cursor-not-allowed disabled:opacity-60'
                }`}
              >
                <AppIcon className={`${voiceProcessing ? 'ri-loader-4-line animate-spin' : recording ? 'ri-stop-circle-line' : 'ri-mic-line'} text-lg`} />
                {voiceProcessing ? 'Processing recording...' : recording ? 'Stop recording' : 'Record my answer'}
              </button>
              <button
                onClick={proofreadReflection}
                disabled={!reflection.trim() || proofreading}
                className="inline-flex items-center gap-2 rounded-xl border border-[#d3dee8] bg-[#f8fcff] px-4 py-2.5 text-sm font-medium text-[#7a8797] shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                <AppIcon className={`${proofreading ? 'ri-loader-4-line animate-spin' : 'ri-sparkling-line'} text-lg`} />
                {proofreading ? 'Improving wording...' : 'Proofread & improve my wording'}
              </button>
              <p className={`ml-auto text-sm ${learningReady ? 'text-emerald-600' : 'text-[#607086]'}`}>
                {wordCount} words
              </p>
            </div>
            <p className="mt-3 text-xs text-[#607086]">
              AI can help improve your wording, but the final answer must be based on your real learning and workplace experience.
            </p>
            {recording && (
              <p className="mt-2 text-xs font-medium text-red-600">
                <AppIcon className="ri-record-circle-line mr-1 animate-pulse" />
                Recording in English... select Stop recording when you finish.
              </p>
            )}
            {voiceError && (
              <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {voiceError}
              </p>
            )}
          </section>
        )}

        {tab === 'ksbs' && (
          <section>
            <h2 className="text-sm font-semibold text-foreground-900">KSB confidence check</h2>
            <p className="mt-1 text-xs text-foreground-500">
              {selectionRequired
                ? `Select at least one relevant ${hasAuthoredKsbs ? 'mapped' : 'programme'} KSB this ${noun} developed. You do not need to select from every K, S and B category. Explain and rate only the KSBs you select.`
                : `Explain each mapped KSB, then rate your confidence before and after this ${noun}.`}
            </p>

            {mappedKsbs.length > 0 && !selectingFromProgramme && (
              <div className="mt-4 overflow-hidden rounded-2xl border border-primary-200 bg-gradient-to-r from-primary-50 via-white to-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary-500">Curriculum mapping</p>
                    <div className="mt-1 flex items-baseline gap-2">
                      <span className="text-2xl font-bold text-primary-800">{formatKsbWeight(mappedKsbWeightSummary.total)}</span>
                      <span className="text-xs font-medium text-foreground-500">selected mapped weight</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      { key: 'K' as const, label: 'Knowledge', className: 'bg-violet-50 text-violet-700 ring-violet-100' },
                      { key: 'S' as const, label: 'Skills', className: 'bg-sky-50 text-sky-700 ring-sky-100' },
                      { key: 'B' as const, label: 'Behaviours', className: 'bg-emerald-50 text-emerald-700 ring-emerald-100' },
                    ].map(group => (
                      <div key={group.key} className={`min-w-[88px] rounded-xl px-3 py-2 ring-1 ${group.className}`}>
                        <p className="text-[10px] font-semibold">{group.label}</p>
                        <p className="mt-0.5 text-base font-bold">{formatKsbWeight(mappedKsbWeightSummary[group.key])}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {selectionRequired && (
              <div className="mt-4 min-w-0 rounded-xl border border-foreground-200 bg-white p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-foreground-700">{hasAuthoredKsbs ? 'Select the mapped KSBs this activity developed' : 'Select the programme KSBs this activity developed'}</p>
                  <span className="shrink-0 text-xs text-foreground-500">{ksbCodes.length} selected</span>
                </div>
                <div role="tablist" aria-label="KSB type" className="mb-3 inline-flex gap-1 rounded-lg bg-background-100 p-1">
                  {KSB_GROUPS.map(group => {
                    const count = programmeKsbGroups[group.key].length;
                    const selectedCount = programmeKsbGroups[group.key]
                      .filter(item => selectedKsbs.includes(item.code)).length;
                    const active = activeKsbGroup === group.key;
                    return (
                      <button
                        key={group.key}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        disabled={count === 0}
                        title={group.label}
                        onClick={() => setKsbGroup(group.key)}
                        className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                          active ? 'bg-white text-primary-700 shadow-sm' : 'text-foreground-600 hover:text-foreground-900'
                        }`}
                      >
                        {group.key}
                        <span className="ml-1 font-normal text-foreground-500">
                          {selectedCount > 0 ? `${selectedCount}/${count}` : count}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div role="tabpanel" aria-label={KSB_GROUPS.find(group => group.key === activeKsbGroup)?.label} className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
                  {programmeKsbGroups[activeKsbGroup].map(item => {
                    const selected = selectedKsbs.includes(item.code);
                    return (
                      <label
                        key={item.code}
                        className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-colors ${
                          selected
                            ? 'border-primary-500 bg-primary-50'
                            : 'border-foreground-200 hover:border-primary-300'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => setSelectedKsbs(previous =>
                            previous.includes(item.code)
                              ? previous.filter(code => code !== item.code)
                              : [...previous, item.code]
                          )}
                          className="mt-0.5 shrink-0 accent-primary-600"
                        />
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
                          selected ? 'bg-primary-100 text-primary-700' : 'bg-background-100 text-foreground-600'
                        }`}>{item.code}</span>
                        <span className="min-w-0 break-words text-xs leading-relaxed text-foreground-600">{item.description}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="mt-4 space-y-3">
              {mappedKsbs.map(item => {
                const hasExplanation = Boolean(ksbExplanations[item.code]?.trim());
                const hasRatings = confidenceBefore[item.code] !== undefined && confidenceAfter[item.code] !== undefined;
                const requirementsComplete = hasExplanation && hasRatings;

                return (
                <div key={item.code} className={`overflow-hidden rounded-2xl border bg-white shadow-sm transition ${
                  requirementsComplete ? 'border-emerald-200' : 'border-foreground-200'
                }`}>
                  <div className={`h-1 ${requirementsComplete ? 'bg-emerald-400' : 'bg-primary-400'}`} />
                  <div className="p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex min-w-0 items-start gap-2">
                        <span className="shrink-0 rounded-full bg-primary-100 px-2 py-1 text-xs font-semibold text-primary-700">{item.code}</span>
                        <p className="text-xs leading-relaxed text-foreground-600">{item.description}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {!selectingFromProgramme && (
                          <span className="rounded-lg bg-primary-50 px-2.5 py-1.5 text-[10px] font-bold text-primary-700 ring-1 ring-primary-100">
                            {formatKsbWeight(item.weight)} weight
                          </span>
                        )}
                        <span className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[10px] font-semibold ${
                          requirementsComplete
                            ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100'
                            : 'bg-amber-50 text-amber-700 ring-1 ring-amber-100'
                        }`}>
                          <AppIcon className={requirementsComplete ? 'ri-check-line' : 'ri-time-line'} />
                          {requirementsComplete ? 'Evidence complete' : 'Needs input'}
                        </span>
                      </div>
                    </div>
                  <textarea
                    value={ksbExplanations[item.code] || ''}
                    onChange={event => setKsbExplanations(previous => ({ ...previous, [item.code]: event.target.value }))}
                    rows={2}
                    placeholder={`Why is ${item.code} relevant to this activity? What did you do?`}
                    className="mt-3 w-full resize-none rounded-xl border border-foreground-200 px-3 py-2 text-sm focus:border-primary-400 focus:outline-none"
                  />
                  <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <ConfidenceRange
                      label="Confidence before"
                      value={confidenceBefore[item.code]}
                      onChange={value => setConfidenceBefore(previous => ({ ...previous, [item.code]: value }))}
                    />
                    <ConfidenceRange
                      label="Confidence after"
                      value={confidenceAfter[item.code]}
                      onChange={value => setConfidenceAfter(previous => ({ ...previous, [item.code]: value }))}
                    />
                  </div>
                </div>
                </div>
                );
              })}
              {mappedKsbs.length === 0 && (
                <p className="rounded-xl bg-background-100 p-4 text-sm text-foreground-500">
                  {selectionRequired
                    ? 'Select at least one KSB above to explain and rate it.'
                    : selectingFromProgramme
                      ? 'No KSBs are mapped to this activity and your programme has no KSBs to choose from.'
                      : 'No KSBs are mapped to this activity.'}
                </p>
              )}
            </div>
          </section>
        )}

        {tab === 'apply' && (
          <section>
            <h2 className="text-sm font-semibold text-foreground-900">{applyQuestion}</h2>
            <div className="mt-3 space-y-2">
              {[
                ['already', 'I have already applied this learning'],
                ['plan', 'I plan to apply this learning'],
                ['unsure', "I'm not sure how to apply — I need support"],
              ].map(([value, label]) => (
                <label key={value} className="flex cursor-pointer items-center gap-2 rounded-xl border border-foreground-200 bg-white px-3 py-2.5 text-sm text-foreground-700">
                  <input
                    type="radio"
                    name="application"
                    checked={applicationType === value}
                    onChange={() => {
                      setApplicationType(value);
                      setApplicationText('');
                    }}
                    className="accent-primary-600"
                  />
                  {label}
                </label>
              ))}
            </div>
            {applicationType && (
              <>
                <textarea
                  value={applicationText}
                  onChange={event => setApplicationText(event.target.value)}
                  rows={6}
                  placeholder={applicationType === 'unsure'
                    ? 'Describe what support you need from your coach or employer.'
                    : 'Describe how you have applied or plan to apply this learning.'}
                  className="mt-3 w-full resize-none rounded-xl border border-foreground-200 bg-white px-4 py-3 text-sm focus:border-primary-400 focus:outline-none"
                />
                {applicationType === 'unsure' && (
                  <p className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-800">
                    <AppIcon className="ri-alert-line mr-1" />
                    Submitting this will alert your coach to discuss the support you need in your next meeting.
                  </p>
                )}
              </>
            )}
          </section>
        )}

        {tab === 'evidence' && (
          <section>
            <h2 className="text-sm font-semibold text-foreground-900">Upload evidence of workplace application (optional but recommended)</h2>
            <p className="mt-1 text-xs text-foreground-500">
              Word, PDF, PowerPoint, Excel, screenshot, photo, email, meeting notes, report, work product, link, audio or video.
            </p>
            <p className="mt-1 text-xs text-foreground-500">
              Uploaded files also appear in your Evidence Library under this activity, with their upload date.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="application/pdf,image/png,image/jpeg,video/mp4"
              className="hidden"
              onChange={event => void handleEvidenceUpload(Array.from(event.target.files || []))}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={evidenceUploading}
              className="mt-4 inline-flex items-center gap-2 rounded-xl border border-foreground-200 bg-white px-4 py-2.5 text-sm font-medium text-foreground-700 shadow-sm disabled:cursor-wait disabled:opacity-60"
            >
              <AppIcon className={evidenceUploading ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />
              {evidenceUploading ? 'Uploading to Azure...' : 'Add evidence file'}
            </button>
            {evidenceUploadError && (
              <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                {evidenceUploadError}
              </p>
            )}
            {allEvidenceFileNames.length > 0 && (
              <div className="mt-3 space-y-2">
                {allEvidenceFileNames.map((fileName, index) => (
                  <div key={`${fileName}-${index}`} className="flex items-center justify-between rounded-lg bg-background-100 px-3 py-2 text-xs text-foreground-700">
                    <span className="truncate"><AppIcon className="ri-file-line mr-2" />{fileName}</span>
                    <span className="ml-3 inline-flex shrink-0 items-center gap-1 font-medium text-emerald-700">
                      <AppIcon className="ri-checkbox-circle-line" /> Uploaded
                    </span>
                  </div>
                ))}
              </div>
            )}
            <label className="mt-4 flex cursor-pointer items-start gap-2 rounded-xl border border-foreground-200 bg-white p-4">
              <input
                type="checkbox"
                checked={coachVisibilityConfirmed}
                onChange={event => setCoachVisibilityConfirmed(event.target.checked)}
                className="mt-0.5 accent-primary-600"
              />
              <span className="text-sm leading-relaxed text-foreground-700">
                I confirm I have permission to upload this evidence and have removed/hidden any confidential, personal or commercially sensitive information where necessary.
              </span>
            </label>
          </section>
        )}

        {tab === 'benefit' && (
          <section>
            <h2 className="text-sm font-semibold text-foreground-900">How could your employer or business benefit from this learning?</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              {BENEFITS.map(benefit => (
                <button
                  key={benefit}
                  onClick={() => setSelectedBenefits(previous =>
                    previous.includes(benefit)
                      ? previous.filter(item => item !== benefit)
                      : [...previous, benefit]
                  )}
                  className={`rounded-full border px-3 py-1.5 text-xs ${
                    selectedBenefits.includes(benefit)
                      ? 'border-primary-600 bg-primary-600 text-white'
                      : 'border-[#d4e0eb] bg-[#eef6fd] text-foreground-600'
                  }`}
                >
                  {benefit}
                </button>
              ))}
            </div>
            <textarea
              value={benefitExplanation}
              onChange={event => setBenefitExplanation(event.target.value)}
              rows={4}
              placeholder="Explain how this could improve your team, employer or business outcomes."
              className="mt-4 w-full resize-none rounded-xl border border-foreground-200 bg-white px-4 py-3 text-sm focus:border-primary-400 focus:outline-none"
            />
          </section>
        )}

        {tab === 'otjh' && (
          <section>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Planned time">
                <div className="flex h-11 items-center rounded-xl border border-foreground-200 bg-background-100 px-3 text-sm text-foreground-500">
                  {plannedTimeLabel || 'Not set'}
                </div>
              </Field>
              {timeInput()}
              <Field label="Date completed">
                <input
                  type="date"
                  value={dateCompleted}
                  onChange={event => setDateCompleted(event.target.value)}
                  className="h-11 w-full rounded-xl border border-foreground-200 bg-white px-3 text-sm focus:border-primary-400 focus:outline-none"
                />
              </Field>
            </div>
            <fieldset className="mt-5">
              <legend className="text-sm font-medium text-foreground-800">Completed during paid working hours?</legend>
              <div className="mt-2 flex gap-5">
                {[['yes', 'Yes'], ['no', 'No'], ['partly', 'Partly']].map(([value, label]) => (
                  <label key={value} className="flex cursor-pointer items-center gap-2 text-sm text-foreground-700">
                    <input type="radio" name="paidHours" checked={paidHours === value} onChange={() => setPaidHours(value)} className="accent-primary-600" />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="mt-5 rounded-xl border border-foreground-200 bg-primary-50/30 p-4 text-xs leading-relaxed text-foreground-600">
              I confirm that the time recorded is accurate. I confirm that this activity supported development of my apprenticeship KSBs. I confirm it was completed during my normal paid working hours, unless I have explained an agreed exception.
            </div>
            <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm text-foreground-700">
              <input type="checkbox" checked={otjhConfirmed} onChange={event => setOtjhConfirmed(event.target.checked)} className="accent-primary-600" />
              I confirm this OTJH record is accurate.
            </label>
          </section>
        )}

        {tab === 'review' && (
          <section className="space-y-4">
            <div className="max-w-sm">{timeInput()}</div>
            <div className="rounded-xl border border-foreground-200 bg-white p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium text-foreground-800">AI quality check</span>
                <button
                  onClick={() => setAiChecked(true)}
                  className="inline-flex items-center gap-2 rounded-lg border border-foreground-200 px-3 py-2 text-xs font-medium text-foreground-700 shadow-sm"
                >
                  <AppIcon className="ri-sparkling-line" /> {aiChecked ? 'Run again' : 'Run check'}
                </button>
              </div>
              {aiChecked && (
                <div className="mt-3 border-t border-foreground-100 pt-3">
                  <div className="flex items-center gap-3">
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-[#cbd3dc]">
                      <div
                        className={`h-full rounded-full transition-all ${
                          qualityScore === 100 ? 'bg-emerald-500' : 'bg-[#102d52]'
                        }`}
                        style={{ width: `${qualityScore}%` }}
                      />
                    </div>
                    <strong className="min-w-12 text-right text-sm font-medium text-foreground-700">
                      {qualityScore}/100
                    </strong>
                  </div>
                  <p className="mt-3 text-sm leading-relaxed text-foreground-600">{qualitySummary}</p>
                  {qualitySuggestions.length > 0 && (
                    <div className="mt-2">
                      <p className="text-sm font-medium text-foreground-800">Suggestions</p>
                      <ul className="mt-1 list-disc pl-5 text-xs leading-relaxed text-foreground-700">
                        {qualitySuggestions.map(suggestion => (
                          <li key={suggestion}>{suggestion}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="rounded-xl border border-primary-200 bg-primary-50/30 p-4">
              <h2 className="text-sm font-semibold text-foreground-900"><AppIcon className="ri-shield-check-line mr-2 text-primary-600" />Learner declaration &amp; signature</h2>
              <p className="mt-2 text-xs leading-relaxed text-foreground-500">
                Your stored signature is reused on every submission and saved with the document dated and time-stamped for legal and compliance reasons.
              </p>
              <div className="mt-3 rounded-xl border border-foreground-200 bg-white px-4 py-2.5 text-sm italic text-foreground-700">{learnerName}</div>
              <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-foreground-700">
                <input type="checkbox" checked={signedDeclaration} onChange={event => setSignedDeclaration(event.target.checked)} className="accent-primary-600" />
                I confirm this evidence is accurate and electronically sign it.
              </label>
              <p className="mt-2 text-xs text-foreground-500">
                Will be stored as: <strong>{learnerName}</strong> · {new Date().toLocaleDateString('en-GB')} · {new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
              </p>
            </div>
            <div className="rounded-xl border border-foreground-200 bg-white p-4">
              <h2 className="mb-2 text-xs font-semibold text-foreground-900">Submission checklist</h2>
              <div className="space-y-1.5">
                {checklist.map(item => (
                  <p key={item.label} className={`flex items-center gap-2 text-xs ${item.complete ? 'text-emerald-700' : 'text-foreground-500'}`}>
                    <AppIcon className={item.complete ? 'ri-checkbox-circle-line' : 'ri-alert-line'} /> {item.label}
                  </p>
                ))}
              </div>
            </div>
          </section>
        )}

        {(reflectionSaveError || autosave.error || submitError) && (
          <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {reflectionSaveError || autosave.error || submitError}
          </p>
        )}
        {!localRecoveryAvailable && !autosave.saved && editableDraft && (
          <p role="alert" className="mt-3 text-sm text-amber-800">Browser recovery is unavailable. Keep this page open until your draft is saved to the server.</p>
        )}
        {!serverLoaded && loadedDraftKey === draftKey && draftKey && (
          <button type="button" className="mt-3 text-sm underline" onClick={() => setLoadAttempt(value => value + 1)}>
            Retry loading saved reflection
          </button>
        )}
      </fieldset>

      <div className="flex flex-col gap-4 border-t border-[#bfc9d3] bg-[#f7fbff] px-5 py-4 sm:flex-row sm:items-center sm:justify-between md:px-8 md:py-5">
        <p className="max-w-xl text-sm leading-relaxed text-[#607086]">
          {submissionLocked ? (
            <>Marking status: <strong className="text-emerald-700">Accepted</strong> — this submission is read-only.</>
          ) : (
            <>Status on submit: <strong>Submitted for tutor review</strong> — KSBs &amp; OTJH stay pending until validated.</>
          )}
        </p>
        <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
          {editableDraft && draftKey && <span role="status" className="text-xs text-foreground-500">
            {autosave.saving ? 'Saving draft...' : autosave.error ? 'Draft not saved to server — retry Save draft.'
              : autosave.saved ? 'All changes saved' : serverLoaded ? 'Changes waiting to save' : 'Server draft not loaded'}
          </span>}
          {submissionLocked ? (
            <span className="inline-flex items-center gap-2 rounded-xl bg-emerald-100 px-5 py-2.5 text-sm font-semibold text-emerald-800">
              <AppIcon className="ri-lock-2-line" /> Accepted &amp; locked
            </span>
          ) : (
            <>
              <button
                onClick={() => void handleSubmit('draft')}
                disabled={loadingExisting || !serverLoaded || reflectionSaving || submitting || evidenceUploading}
                className="rounded-xl border border-[#cbd5df] bg-[#f8fcff] px-5 py-2.5 text-sm font-medium text-[#142033] shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                {autosave.saved ? 'Draft saved' : 'Save draft'}
              </button>
              {tab === 'review' ? (
                <button
                  onClick={() => void handleSubmit()}
                  disabled={submitting || reflectionSaving || loadingExisting || !serverLoaded || !canSubmit}
                  className="inline-flex items-center gap-2 rounded-xl bg-[#102d52] px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-[#91a0b3]"
                >
                  {submitting || reflectionSaving
                    ? <><AppIcon className="ri-loader-4-line animate-spin" /> Saving...</>
                    : <><AppIcon className="ri-edit-line" /> Submit for tutor review</>}
                </button>
              ) : (
                <button
                  onClick={handleNext}
                  disabled={loadingExisting}
                  className="rounded-xl bg-[#102d52] px-6 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ConfidenceRange({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="mb-1 flex items-center justify-between text-xs text-foreground-600">
        {label} <strong>{value ?? 1}/5</strong>
      </span>
      <input
        type="range"
        min="1"
        max="5"
        value={value ?? 1}
        onChange={event => onChange(Number(event.target.value))}
        className="w-full accent-primary-700"
      />
    </label>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-foreground-700">{label}</span>
      {children}
    </label>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wider text-[#607086]">{label}</p>
      <p className="mt-0.5 text-sm font-medium leading-snug text-[#142033]">{value}</p>
    </div>
  );
}
