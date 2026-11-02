import { useEffect, useRef, useState } from 'react';
import type { LearnerKind } from '@/api/extendedIlr';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { saveAssessment } from './api';
import { AssessmentQuestions } from './AssessmentQuestions';
import { RecommendationResult } from './RecommendationResult';
import { completion, selectionConfirmed } from './useSuitability';
import { SubmittedAnswers } from './SubmittedAnswers';
import { alternativeProgramme, recommendedProgramme } from './recommendation';
import type { AssessmentChanges, AssessmentState, Pathway, ProgrammeId } from './types';

export function AssessmentDialog({ initial, kind, learnerId, onClose, onConfirmed, onSubmitted, onSaved, loading = false, loadError = '', onRetry }: {
  initial: AssessmentState | null; kind: LearnerKind; learnerId: string; onClose: () => void;
  onConfirmed: (state: AssessmentState) => void; onSubmitted?: (state: AssessmentState) => void;
  onSaved?: (state: AssessmentState) => void; loading?: boolean; loadError?: string; onRetry?: () => Promise<AssessmentState | null> | void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [state, setState] = useState(initial);
  const [stage, setStage] = useState<'pathway' | 'questions' | 'result' | 'answers' | 'reason'>(initial?.pathway ? (completion(initial) === 'SUBMITTED' ? 'result' : 'questions') : 'pathway');
  const [alternativeId, setAlternativeId] = useState<ProgrammeId | null>(null);
  const [selectionReason, setSelectionReason] = useState('');
  const [reasonError, setReasonError] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState<string[]>([]);
  const inFlight = useRef(false);
  const retry = useRef<(() => void) | null>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (!state && initial) {
      setState(initial);
      setStage(initial.pathway ? (completion(initial) === 'SUBMITTED' ? 'result' : 'questions') : 'pathway');
    }
  }, [initial, state]);
  const config = state?.configuration;
  const questions = config?.questions.filter((q) => q.pathway === state?.pathway) ?? [];
  const canConfirm = completion(state) === 'SUBMITTED' && !selectionConfirmed(state);
  const recommended = state ? recommendedProgramme(state) : undefined;
  const alternative = config?.programmes.find((p) => p.id === alternativeId);

  const persist = async (changes: AssessmentChanges, done?: (saved: AssessmentState) => void) => {
    if (inFlight.current || !state) return null;
    inFlight.current = true;
    setBusy(true); setError('');
    const sending = changes.responses || changes.pathway ? { ...changes, selectedProgramme: '' as const } : changes;
    const candidate = { ...state, ...sending, ...(changes.responses || changes.pathway ? { result: null } : {}) };
    if (!changes.action || changes.action === 'draft') setState(candidate);
    retry.current = () => { void persist(changes, done); };
    try {
      const saved = await saveAssessment(kind, learnerId, state, sending);
      setState(saved);
      onSaved?.(saved);
      retry.current = null;
      done?.(saved);
      return saved;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the assessment.');
      if (e instanceof Error && 'status' in e && e.status === 409) retry.current = () => {
        void Promise.resolve(onRetry?.()).then((fresh) => { if (fresh) { setState(fresh); if (completion(fresh) === 'SUBMITTED') setStage('result'); setError(''); retry.current = null; } });
      };
      return null;
    }
    finally { inFlight.current = false; setBusy(false); }
  };
  const submit = () => {
    if (!state) return;
    const unanswered = questions.filter((q) => q.required && !(q.id in state.responses)).map((q) => q.id);
    setMissing(unanswered);
    if (unanswered.length) { setError('Answer each required question or choose none apply.'); return; }
    void persist({ action: 'submit', confirm: true, selectedProgramme: '' }, (saved) => {
      if (completion(saved) !== 'SUBMITTED') throw new Error('The server has not confirmed your assessment submission. Please retry.');
      setStage('result'); onSubmitted?.(saved);
    });
  };
  const confirmProgramme = (selectedProgramme?: ProgrammeId, alternativeSelectionReason?: string) => {
    if (!canConfirm) return;
    void persist(selectedProgramme ? { action: 'select', selectedProgramme, alternativeSelectionReason } : { action: 'continue' }, (saved) => {
      if (!selectionConfirmed(saved)) throw new Error('The server has not confirmed your programme selection. Please retry.');
      setStage('result');
      onConfirmed(saved);
    });
  };
  const requestAlternative = (id: ProgrammeId) => {
    if (!canConfirm || !state || recommended?.level !== 6 || alternativeProgramme(state)?.id !== id) return;
    setAlternativeId(id); setSelectionReason(''); setReasonError(''); setError(''); retry.current = null;
    setStage('reason');
  };
  const cancelReason = () => {
    if (busy) return;
    setStage('result'); setAlternativeId(null); setSelectionReason(''); setReasonError(''); setError(''); retry.current = null;
  };
  return <dialog ref={dialog} aria-labelledby="suitability-dialog-title" onCancel={(e) => { e.preventDefault(); if (!busy) { if (stage === 'reason') cancelReason(); else onClose(); } }}
    className={`m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] ${stage === 'reason' ? 'max-w-2xl' : stage === 'result' ? 'max-w-3xl' : 'max-w-5xl'} overflow-y-auto rounded-2xl border border-foreground-200 bg-white p-5 text-foreground-800 shadow-xl backdrop:bg-black/40 md:p-7`}>
    <div className="mb-4 flex items-start justify-between gap-3">
      <div><h2 id="suitability-dialog-title" className="text-xl font-semibold">{stage === 'reason' ? 'Reason for Choosing an Alternative Programme' : 'CV & Programme Suitability'}</h2>
        {stage !== 'reason' && <p className="mt-1 text-sm text-foreground-500">{stage === 'pathway' ? '1. Choose a pathway' : stage === 'questions' ? '2. Your actual responsibilities' : '3. Your assessment result'}</p>}</div>
      {stage !== 'reason' && <button type="button" className={btnSecondary} disabled={busy} onClick={onClose} aria-label="Close assessment">Close</button>}
    </div>
    {config && (stage === 'pathway' || stage === 'questions') && <p className="mb-5 text-sm">{config.introduction}</p>}
    {(error || loadError) && <div role="alert" className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
      <p>{error || loadError}</p><button type="button" className={`${btnSecondary} mt-2`} disabled={busy || loading}
        onClick={() => { if (retry.current) retry.current(); else onRetry?.(); }}>Retry</button>
    </div>}
    {!config && <p role="status">{loading ? 'Loading assessment…' : loadError ? 'The assessment could not be loaded.' : 'No published assessment is available.'}</p>}
    {config && state && <fieldset disabled={busy} className="min-w-0 space-y-4 disabled:opacity-70">
      {stage === 'pathway' && <div className="grid gap-3 sm:grid-cols-2">{(['marketing', 'project-controls'] as Pathway[]).map((pathway) =>
        <button key={pathway} type="button" className={`${btnSecondary} min-h-24 justify-center`}
          onClick={() => { setStage('questions'); void persist({ pathway, ...(pathway !== state.pathway ? { responses: {}, selectedProgramme: '' } : {}) }); }}>
          {pathway === 'marketing' ? 'Marketing' : 'Project Controls'}
        </button>)}</div>}
      {stage === 'questions' && <>
        <AssessmentQuestions questions={questions} responses={state.responses} missing={missing}
          onChange={(responses) => { void persist({ responses }); }} />
        <p className="text-xs text-foreground-500">Each answer is saved to your assessment as you select it. Selecting none apply is valid and contributes no programme points.</p>
        <div className="flex flex-wrap justify-between gap-3">
          <button type="button" className={btnSecondary} onClick={() => setStage('pathway')}>Back to pathways</button>
          <button type="button" className={btnPrimary} onClick={submit}>Submit Assessment</button>
        </div>
      </>}
      {stage === 'result' && state.result && <>
        {canConfirm ? <RecommendationResult mode="confirmation" state={state} onSelect={requestAlternative} /> : <RecommendationResult state={state} />}
        <div className="flex flex-wrap justify-between gap-3">
          <button type="button" className={btnSecondary} onClick={() => setStage('answers')}>Show Answers</button>
          {canConfirm && <button type="button" className={btnPrimary} onClick={() => confirmProgramme()}>Continue to Next Step</button>}
        </div>
      </>}
      {stage === 'answers' && <>
        <SubmittedAnswers state={state} />
        <button type="button" className={btnSecondary} onClick={() => setStage('result')}>Back to Result</button>
      </>}
      {stage === 'reason' && canConfirm && recommended && alternative && <>
        <p className="text-sm">Why would you prefer {alternative.title} Level {alternative.level} instead of the recommended {recommended.title} Level {recommended.level}?</p>
        <label className="block space-y-2 text-sm">
          <span>Please explain your reason for choosing this programme.</span>
          <textarea required autoFocus rows={5} maxLength={1000} value={selectionReason} aria-invalid={Boolean(reasonError)}
            aria-describedby={reasonError ? 'alternative-reason-error' : undefined} className={inputClass}
            placeholder="Tell us why you feel this programme is more suitable for your goals and current role..."
            onChange={(e) => { setSelectionReason(e.target.value); setReasonError(''); }} />
        </label>
        {reasonError && <p id="alternative-reason-error" role="alert" className="text-sm text-red-700">{reasonError}</p>}
        <div className="flex flex-wrap justify-between gap-3">
          <button type="button" className={btnSecondary} onClick={cancelReason}>Cancel</button>
          <button type="button" className={btnPrimary} onClick={() => {
            if (!selectionReason.trim() || selectionReason.length > 1000) { setReasonError('Please explain your reason for choosing this programme (maximum 1000 characters).'); return; }
            confirmProgramme(alternative.id, selectionReason);
          }}>Submit Reason &amp; Continue</button>
        </div>
      </>}
    </fieldset>}
    <p role="status" aria-live="polite" className="mt-3 text-xs text-foreground-500">{busy ? 'Saving your assessment…' : stage === 'result' ? 'Your assessment result is saved.' : 'Your saved CV and job information is kept separately.'}</p>
  </dialog>;
}
