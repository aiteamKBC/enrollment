import { useLearnerReviewInstance, type LearnerReviewInstanceState } from './LearnerReviewInstanceForm.helpers';
export type { LearnerReviewInstanceState } from './LearnerReviewInstanceForm.helpers';
import { renderImportedSection } from './imported/renderImportedSection';
import { ImportedValue } from './imported/ImportedReviewSection';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { ReviewFormRenderer } from '@/components/reviews/ReviewFormRenderer';
import { computeMissingRequiredFieldsForRole, computeVisibleRequiredFields, computeVisibleWritableFieldIds, computeWritableFieldIds } from "@/components/reviews/ReviewFormRenderer.helpers";
import { ReviewSignatures } from '@/components/reviews/ReviewSignatures';
import { ReviewPdfDownload } from '@/components/reviews/ReviewPdfDownload';
import { ReviewProgressPanel } from '@/components/reviews/ReviewProgressPanel';
import { fetchLearnerEventReviewInstance, fetchMigratedReviewForParty, type LearnerReviewDefinition, type MonthlyLogSignatureSync } from '@/api/learnerCalendar';
import { MonthlyLogSignatureStatus } from './MonthlyLogSignatureStatus';
import { MigratedReviewHistory } from '@/components/reviews/MigratedReviewHistory';
import { downloadMigratedRevisionForParty } from '@/api/learnerCalendar';
import { prepareLearnerPersonalSupport } from '@/api/learnerPersonalSupport';
import type { LearnerKind } from '@/api/learnerDetail';
import { flattenReviewFields } from '@/api/reviewInstances';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { LearnerLogs } from '@/features/monthly-logs/page';
import monthlyLogDesign from '@/features/old-otjh/design.module.css';
import monthlyLogJournal from '@/features/old-otjh/journal.module.css';
import monthlyLogStyles from '@/features/monthly-logs/monthlyLogs.module.css';

function savedLearnerSignatureKey(name: string): string {
  return `learner-review-signature:${name.trim().toLowerCase() || 'learner'}`;
}

function readSavedLearnerSignature(name: string): string {
  if (typeof window === 'undefined') return '';
  try {
    const value = window.localStorage.getItem(savedLearnerSignatureKey(name)) || '';
    return value.startsWith('data:image/') ? value : '';
  } catch {
    return '';
  }
}

function writeSavedLearnerSignature(name: string, signature: string): void {
  if (typeof window === 'undefined' || !signature.startsWith('data:image/')) return;
  try {
    window.localStorage.setItem(savedLearnerSignatureKey(name), signature);
  } catch {
    // The review signature itself is saved server-side. Reuse is best-effort.
  }
}

function seedAnswersFromDefinition(definition: LearnerReviewDefinition): Record<string, unknown> {
  const saved: Record<string, unknown> = {};
  for (const field of flattenReviewFields(definition.sections)) {
    if (field.answer !== undefined && field.answer !== null) saved[field.id] = field.answer;
  }
  return saved;
}

export function LearnerReviewInstanceForm({
  definition: incomingDefinition, onSign, onDownload, signatoryName = 'Learner',
  viewerRole = 'participant', onSaveAnswers, mcmMonthlyLog, monthlyLogSync, onRetryMonthlyLogSignature,
}: {
  definition: LearnerReviewDefinition;
  onSign?: (signature: string, options?: { applyMonthlyLogSignature?: boolean }) => Promise<void>;
  onDownload?: () => Promise<void>;
  signatoryName?: string;
  /** Which respondent role this rendering represents -- 'participant' (the
   *  Learner) unless the caller is the Employer's own surface. */
  viewerRole?: 'participant' | 'employer';
  /** Persists a role-scoped subset of answers and returns the refreshed
   *  definition. Omitted (every existing caller until it opts in), the form
   *  stays fully read-only exactly as it always has. */
  onSaveAnswers?: (answers: Record<string, unknown>, answerVersion?: string) => Promise<LearnerReviewDefinition>;
  /** The target-month log is reviewed inside the MCM signing modal, never inline. */
  mcmMonthlyLog?: { id: string; month: string; contractKind: LearnerKind };
  monthlyLogSync?: MonthlyLogSignatureSync;
  onRetryMonthlyLogSignature?: () => Promise<void>;
}) {
  const [savedPersonalSupport, setSavedPersonalSupport] = useState<LearnerReviewDefinition | null>(null);
  useEffect(() => setSavedPersonalSupport(null), [incomingDefinition]);
  const definition = savedPersonalSupport || incomingDefinition;
  const personalSupportForm = Boolean(definition.migratedForm && definition.template.reviewTypeCode === 'aptem_psp');
  const [openSectionId, setOpenSectionId] = useState('');
  const [signing, setSigning] = useState(false);
  const [signatureError, setSignatureError] = useState('');
  const [signatureOpen, setSignatureOpen] = useState(!mcmMonthlyLog);
  const [applyMonthlyLogSignature, setApplyMonthlyLogSignature] = useState(true);
  const [savedSignature, setSavedSignature] = useState('');
  const [drawingSignature, setDrawingSignature] = useState(false);
  const signingInFlight = useRef(false);
  const signatureSection = useRef<HTMLDivElement>(null);
  const [answers, setAnswers] = useState<Record<string, unknown>>(() => seedAnswersFromDefinition(definition));
  const [savingAnswers, setSavingAnswers] = useState(false);
  const [saveAnswersError, setSaveAnswersError] = useState('');
  const answerVersion = useRef(definition.answerVersion);
  const savingAnswersRef = useRef(false);

  function focusSignatureSection() {
    signatureSection.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    signatureSection.current?.focus({ preventScroll: true });
  }

  function showSignatures() {
    if (canSign && mcmMonthlyLog) {
      setSignatureOpen(true);
      return;
    }
    setSignatureOpen(true);
    focusSignatureSection();
  }

  async function saveSignature(signature: string) {
    if (!onSign || signingInFlight.current) return;
    signingInFlight.current = true;
    setSigning(true);
    setSignatureError('');
    try {
      await onSign(signature, mcmMonthlyLog ? { applyMonthlyLogSignature } : undefined);
      writeSavedLearnerSignature(signatoryName, signature);
      setSavedSignature(signature);
      setDrawingSignature(false);
    } catch (reason) {
      setSignatureError(reason instanceof Error ? reason.message : 'Could not save your signature. Please try again.');
    } finally {
      signingInFlight.current = false;
      setSigning(false);
    }
  }

  useEffect(() => {
    setOpenSectionId(definition.sections.find((section) => section.enabled)?.id || '');
  }, [definition]);

  useEffect(() => {
    setSavedSignature(definition.savedSignature || readSavedLearnerSignature(signatoryName));
    setDrawingSignature(false);
    setSignatureError('');
  }, [definition.savedSignature, definition.reviewRevision, signatoryName]);

  useEffect(() => {
    setApplyMonthlyLogSignature(true);
  }, [definition.instance?.id, mcmMonthlyLog?.month]);

  // The renderer draws from `answers`, not from the field rows, so the saved
  // answers are seeded the same way ReviewInstanceModal seeds them. Kept as
  // state (not a pure derivation) so a writable field's own edits show
  // immediately, then reseeded whenever a fresh definition arrives (a new
  // occurrence opened, or this component's own save below returns one).
  useEffect(() => {
    setAnswers(seedAnswersFromDefinition(definition));
    answerVersion.current = definition.answerVersion;
  }, [definition]);

  const requiredSignatures = Object.values(definition.signatures).filter(state => state.required);
  const signatureFreePersonalSupport = personalSupportForm && requiredSignatures.length === 0;
  const submitted = ['awaiting-signature', 'completed'].includes(definition.instance?.status || '');
  // Exactly the fields the Curriculum template opted this viewer into
  // answering -- every other field stays read-only regardless of onSaveAnswers.
  const writableFieldIds = useMemo(
    () => computeWritableFieldIds(definition.sections, viewerRole),
    [definition, viewerRole],
  );
  // Writing follows the same lifecycle rule the backend enforces: once the
  // review reaches the signature step, its answers are frozen for everyone.
  const canWriteAnswers = Boolean(onSaveAnswers) && !submitted && (!definition.migratedForm || !definition.readOnly);
  const visibleRespondentFieldIds = useMemo(
    () => computeVisibleWritableFieldIds(definition.sections, answers, viewerRole),
    [definition.sections, answers, viewerRole],
  );
  const respondentRequiredFieldIds = useMemo(() => {
    const writable = visibleRespondentFieldIds;
    return new Set(
      [...computeVisibleRequiredFields(definition.sections, answers)].filter((fieldId) => writable.has(fieldId)),
    );
  }, [definition.sections, answers, visibleRespondentFieldIds]);
  const respondentMissingFieldIds = useMemo(
    () => computeMissingRequiredFieldsForRole(definition.sections, answers, viewerRole),
    [definition.sections, answers, viewerRole],
  );
  const respondentLabel = viewerRole === 'employer' ? 'Employer' : 'Learner';
  const firstRespondentSectionId = useMemo(
    () => definition.sections.find(
      (section) => computeVisibleWritableFieldIds([section], answers, viewerRole).size > 0,
    )?.id || '',
    [definition.sections, answers, viewerRole],
  );

  const openRespondentQuestions = () => {
    if (!firstRespondentSectionId) return;
    setOpenSectionId(firstRespondentSectionId);
    window.requestAnimationFrame(() => {
      document.getElementById(`review-section-${firstRespondentSectionId}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    });
  };

  const handleAnswerChange = useCallback((fieldId: string, value: unknown) => {
    if (!canWriteAnswers || (definition.migratedForm && savingAnswersRef.current) || !writableFieldIds.has(fieldId)) return;
    setAnswers(current => ({ ...current, [fieldId]: value }));
    setSaveAnswersError('');
  }, [canWriteAnswers, definition.migratedForm, writableFieldIds]);

  const saveAnswers = async () => {
    if (!onSaveAnswers || !canWriteAnswers || savingAnswersRef.current) return;
    savingAnswersRef.current = true;
    setSavingAnswers(true);
    setSaveAnswersError('');
    try {
      const payload: Record<string, unknown> = {};
      for (const fieldId of definition.migratedForm ? visibleRespondentFieldIds : writableFieldIds) {
        payload[fieldId] = definition.migratedForm ? answers[fieldId] ?? null : answers[fieldId];
      }
      const updated = definition.migratedForm
        ? await onSaveAnswers(payload, answerVersion.current)
        : await onSaveAnswers(payload);
      answerVersion.current = updated.answerVersion;
      setAnswers(seedAnswersFromDefinition(updated));
      if (personalSupportForm) setSavedPersonalSupport(updated);
    } catch (reason) {
      setSaveAnswersError(reason instanceof Error ? reason.message : 'Could not save your answers. Please try again.');
    } finally {
      savingAnswersRef.current = false;
      setSavingAnswers(false);
    }
  };
  const allSigned = requiredSignatures.length > 0 && requiredSignatures.every(state => state.signed);
  const viewerSigned = Boolean(definition.signatures[viewerRole]?.signed);
  const canSign = Boolean(onSign && submitted && definition.signatures[viewerRole]?.required && !viewerSigned);
  const signatureTitle = allSigned ? 'All signatures saved' : canSign ? 'Ready for your signature'
    : viewerSigned ? 'Your signature is saved' : submitted ? 'Awaiting required signatures' : 'Your coach is preparing this review';
  const signatureHelp = allSigned ? 'All required parties have signed this review.'
    : canSign ? "Read your coach's notes and the agreed next steps, then sign below."
    : viewerSigned ? 'You do not need to sign again. This review is waiting for the remaining required signatures.'
    : submitted ? 'The review is ready for the required parties to sign.'
    : 'The coach must complete this review before you can sign it.';

  return (
    <section className="space-y-3" data-testid="learner-review-instance-form">
      <div className="rounded-2xl border border-primary-100 bg-primary-50/70 px-4 py-3.5">
        <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-primary-600">Review</p>
        {/* The Review Template's own name is the title. The Review Type only
            classifies/routes the occurrence and is never displayed here. */}
        <h2 className="mt-0.5 text-sm font-bold text-foreground-900" data-testid="learner-review-instance-title">
          {definition.template.name}{!personalSupportForm && <> #{definition.instance?.occurrenceNumber || definition.occurrenceNumber}</>}
        </h2>
        <p className="mt-1 text-xs text-primary-800">
          <AppIcon className="ri-information-line mr-1.5" />
          {personalSupportForm ? signatureFreePersonalSupport ? 'Save your answers to complete the plan once all required questions are answered.' : 'Saving all required answers sends the plan for the selected signatures.' : "Review your coach's notes and the agreed next steps."}
        </p>
      </div>

      {personalSupportForm && definition.instance?.status === 'completed' && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 font-semibold text-emerald-800">Personal Support Plan completed</p>}

      {requiredSignatures.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-violet-200 bg-violet-50 px-4 py-4">
        <div className="min-w-0 flex-1 basis-64">
          <h3 className="text-sm font-bold text-violet-950">{signatureTitle}</h3>
          <p className="mt-1 text-sm text-violet-900">{signatureHelp}</p>
        </div>
        <button type="button" onClick={showSignatures} className="rounded-xl border border-violet-200 bg-white px-4 py-2.5 text-sm font-semibold text-violet-800 hover:bg-violet-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600">
          {canSign ? 'Go to signature' : 'View signatures'}
        </button>
      </div>}

      {/* The same frozen figures the coach calculated and the signed PDF
          renders. Read-only here: a learner never calculates, and nothing on
          this page recalculates against their current progress. */}
      {(definition.template.reviewTypeCode === 'progress_review'
        || (definition.migratedForm && definition.template.reviewTypeCode === 'aptem_progress_review')) ? (
        <ReviewProgressPanel
          snapshot={definition.progressSnapshot}
          ragHistory={definition.ragHistory}
          canCalculate={false}
          calculating={false}
          onCalculate={() => undefined}
        />
      ) : null}

      {canWriteAnswers && (definition.migratedForm ? visibleRespondentFieldIds : writableFieldIds).size > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-primary-200 bg-primary-50/70 px-4 py-3">
          <p className="text-xs text-primary-900">
            <AppIcon className="ri-edit-2-line mr-1.5" />
            Your coach has opened some fields here for you to answer directly.
          </p>
          <button
            type="button"
            onClick={() => void saveAnswers()}
            disabled={savingAnswers}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-4 text-xs font-bold text-white transition hover:bg-primary-700 disabled:opacity-60"
          >
            {savingAnswers && <AppIcon className="ri-loader-4-line animate-spin"></AppIcon>}
            Save my answers
          </button>
        </div>
      )}
      {visibleRespondentFieldIds.size > 0 && (
        <div data-testid="respondent-question-summary" className="rounded-2xl border border-primary-200 bg-primary-50/70 px-4 py-3.5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-primary-700">{respondentLabel} questions</p>
              <p className="mt-1 text-sm font-semibold text-primary-950">
                {visibleRespondentFieldIds.size} question{visibleRespondentFieldIds.size === 1 ? '' : 's'} for you
              </p>
              {respondentRequiredFieldIds.size > 0 ? (
                <p className="mt-1 text-xs text-primary-800">
                  {respondentRequiredFieldIds.size - respondentMissingFieldIds.size} of {respondentRequiredFieldIds.size} required questions completed
                </p>
              ) : (
                <p className="mt-1 text-xs text-primary-800">Your responses are optional.</p>
              )}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {respondentMissingFieldIds.size > 0 ? (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-800">
                  <AppIcon className="ri-error-warning-line"></AppIcon>
                  {respondentMissingFieldIds.size} required remaining
                </span>
              ) : respondentRequiredFieldIds.size > 0 ? (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-800">
                  <AppIcon className="ri-checkbox-circle-line"></AppIcon>All required answered
                </span>
              ) : null}
              <button
                type="button"
                onClick={openRespondentQuestions}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-primary-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-700"
              >
                <AppIcon className="ri-arrow-down-line"></AppIcon>Open my questions
              </button>
            </div>
          </div>
        </div>
      )}
      {saveAnswersError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{saveAnswersError}</p>}

      <ReviewFormRenderer
        sections={definition.sections}
        renderSectionContent={definition.source === 'aptem' && viewerRole === 'participant'
          ? (section, renderFields) => renderImportedSection(section, answers, renderFields) : undefined}
        renderFieldInput={definition.source === 'aptem' && viewerRole === 'participant'
          ? (field, context) => context.readOnly ? <ImportedValue value={context.value} label={field.title} fieldType={field.fieldType} /> : undefined
          : undefined}
        answers={answers}
        onAnswerChange={handleAnswerChange}
        readOnly
        fieldReadOnly={field => !canWriteAnswers || (Boolean(definition.migratedForm) && savingAnswers) || !writableFieldIds.has(field.id)}
        respondentRole={viewerRole}
        showSavedAnswerStatus
        openSectionId={openSectionId}
        onOpenSectionChange={setOpenSectionId}
      />

      <div ref={signatureSection} tabIndex={-1} aria-label={signatureFreePersonalSupport ? 'Review documents' : 'Signature step'} className="scroll-mt-6 space-y-3 rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">
        {definition.migratedForm && definition.instance && <MigratedReviewHistory entries={definition.reopenHistory} signatureFree={signatureFreePersonalSupport}
          onDownload={number => downloadMigratedRevisionForParty(definition.instance!.id, number)} />}
        {!signatureFreePersonalSupport && <ReviewSignatures signatures={definition.signatures} />}
        {viewerRole === 'participant' && monthlyLogSync && <MonthlyLogSignatureStatus sync={monthlyLogSync} onRetry={onRetryMonthlyLogSignature}
          previousReviewVersion={Boolean(definition.reviewRevision && !definition.signatures.participant?.signed)} />}
        {(definition.source !== 'aptem' || definition.instance?.status === 'completed') && <ReviewPdfDownload
          label={personalSupportForm ? 'Download PDF' : undefined}
          availability={definition.migratedForm
            ? { available: definition.instance?.status === 'completed', reason: 'Available after completion.' }
            : definition.pdf}
          onDownload={onDownload}
        />}
      {canSign && signatureOpen ? mcmMonthlyLog ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-3 sm:p-6" role="presentation">
          <div className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-violet-200 bg-background-50 shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="mcm-sign-dialog-title">
            <header className="flex items-start justify-between gap-4 border-b border-violet-100 bg-violet-50 px-5 py-4 sm:px-6">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-violet-700">Review &amp; sign</p>
                <h2 id="mcm-sign-dialog-title" className="mt-1 text-lg font-bold text-violet-950">Review the monthly log before signing</h2>
                <p className="mt-1 text-sm text-violet-900">Sign the MCM below. You can choose whether the same learner signature is also saved on this month’s log.</p>
              </div>
              <button type="button" disabled={signing} onClick={() => setSignatureOpen(false)} className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm font-semibold text-violet-800 hover:bg-violet-100 disabled:opacity-50" aria-label="Close review and sign dialog">Close</button>
            </header>
            <div className="min-h-0 overflow-y-auto p-3 sm:p-5">
              {mcmMonthlyLog ? (
                <section className="mb-5 space-y-3 rounded-2xl border border-violet-200 bg-violet-50/40 p-3 sm:p-4" aria-label="Monthly learning log">
                  <div className="flex flex-wrap items-start justify-between gap-3 px-1">
                    <div><p className="text-sm font-bold text-violet-950">Monthly learning log</p><p className="mt-1 text-sm text-violet-900">Review the activities for {new Date(`${mcmMonthlyLog.month}-01T00:00:00`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })} before signing.</p></div>
                    <span className="rounded-full border border-violet-200 bg-white px-3 py-1.5 text-xs font-bold text-violet-800">Read only</span>
                  </div>
                  <div className={`${monthlyLogDesign.scope} ${monthlyLogDesign.page} ${monthlyLogStyles.theme} ${monthlyLogJournal.canvas}`}>
                    <LearnerLogs id={mcmMonthlyLog.id} month={mcmMonthlyLog.month} base="" perspective="learner" contractKind={mcmMonthlyLog.contractKind} workflow="mcm" embedded />
                  </div>
                  <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-violet-200 bg-white px-4 py-3 text-sm text-violet-950">
                    <input type="checkbox" className="mt-0.5 h-4 w-4 accent-violet-700" checked={applyMonthlyLogSignature} onChange={event => setApplyMonthlyLogSignature(event.target.checked)} disabled={signing} />
                    <span><span className="block font-bold">Apply my signature to this Monthly Log as well</span><span className="mt-0.5 block text-xs text-violet-800">If unchecked, only the MCM form will be signed. You can sign the Monthly Log later from its normal page.</span></span>
                  </label>
                </section>
              ) : null}
              <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4" aria-busy={signing}>
                <p className="mb-3 text-sm font-bold text-violet-950">Your signature is required for the MCM</p>
                {signatureError && <p role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{signatureError} You can try signing again.</p>}
                {signing && <p role="status" className="mb-3 text-sm text-violet-900">Saving your signature…</p>}
                {savedSignature && !drawingSignature ? (
                  <div className="max-w-md rounded-xl border border-foreground-200 bg-white p-3">
                    <p className="text-[12px] text-foreground-700">Saved signature for</p>
                    <p className="mt-1 text-[13px] font-medium text-foreground-900">{signatoryName}</p>
                    <img src={savedSignature} alt="Your saved signature" className="mt-3 max-h-24 w-full rounded-lg border border-foreground-100 bg-white object-contain p-3" />
                    <p className="mt-3 text-[11px] text-foreground-500">Use this saved signature for this review, or draw a new one.</p>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <button type="button" disabled={signing} onClick={() => { void saveSignature(savedSignature); }} className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-violet-800 disabled:opacity-50">Use saved signature</button>
                      <button type="button" disabled={signing} onClick={() => setDrawingSignature(true)} className="rounded-xl border border-violet-200 bg-white px-4 py-2.5 text-sm font-semibold text-violet-800 hover:bg-violet-100 disabled:opacity-50">Draw new signature</button>
                      <button type="button" disabled={signing} onClick={() => setSignatureOpen(false)} className="rounded-xl border border-background-200 bg-white px-4 py-2.5 text-sm font-semibold text-foreground-600 hover:bg-background-100 disabled:opacity-50">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <fieldset disabled={signing} className="min-w-0 border-0 p-0">
                    <SignaturePad signatoryName={signatoryName} onCommit={saveSignature} onCancel={() => { setSignatureOpen(false); setDrawingSignature(false); }} />
                  </fieldset>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4" aria-busy={signing}>
          <p className="mb-3 text-sm font-bold text-violet-950">Your signature is required</p>
          {signatureError && <p role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{signatureError} You can try signing again.</p>}
          {signing && <p role="status" className="mb-3 text-sm text-violet-900">Saving your signature…</p>}
          {savedSignature && !drawingSignature ? (
            <div className="max-w-md rounded-xl border border-foreground-200 bg-white p-3">
              <p className="text-[12px] text-foreground-700">Saved signature for</p>
              <p className="mt-1 text-[13px] font-medium text-foreground-900">{signatoryName}</p>
              <img src={savedSignature} alt="Your saved signature" className="mt-3 max-h-24 w-full rounded-lg border border-foreground-100 bg-white object-contain p-3" />
              <p className="mt-3 text-[11px] text-foreground-500">Use this saved signature for this review, or draw a new one.</p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button type="button" disabled={signing} onClick={() => { void saveSignature(savedSignature); }} className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-violet-800 disabled:opacity-50">Use saved signature</button>
                <button type="button" disabled={signing} onClick={() => setDrawingSignature(true)} className="rounded-xl border border-violet-200 bg-white px-4 py-2.5 text-sm font-semibold text-violet-800 hover:bg-violet-100 disabled:opacity-50">Draw new signature</button>
                <button type="button" disabled={signing} onClick={() => setSignatureOpen(false)} className="rounded-xl border border-background-200 bg-white px-4 py-2.5 text-sm font-semibold text-foreground-600 hover:bg-background-100 disabled:opacity-50">Cancel</button>
              </div>
            </div>
          ) : (
            <fieldset disabled={signing} className="min-w-0 border-0 p-0">
              <SignaturePad signatoryName={signatoryName} onCommit={saveSignature} onCancel={() => { setSignatureOpen(false); setDrawingSignature(false); }} />
            </fieldset>
          )}
        </div>
      ) : null}
      {canSign && !signatureOpen && <button type="button" onClick={showSignatures} className="rounded-xl bg-violet-700 px-4 py-3 text-sm font-semibold text-white hover:bg-violet-800">Review &amp; sign</button>}
      </div>
    </section>
  );
}
