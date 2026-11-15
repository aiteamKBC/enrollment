import { useState } from 'react';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { saveReview } from './api';
import { selectionConfirmed } from './useSuitability';
import { AssessmentDialog } from './AssessmentDialog';
import { SubmittedAnswers } from './SubmittedAnswers';
import { RecommendationResult } from './RecommendationResult';
import type { AssessmentState, ProgrammeId } from './types';

/** The saved learner result is shared; authorised admissions controls stay separate. */
export function CvSuitabilityPanel() {
  const { userId, isCommercial, readOnly, suitability } = useWizard();
  const kind = isCommercial ? 'commercial' : 'apprenticeship';
  if (!suitability?.enabled) return null;
  const { state, error, loading } = suitability;
  const programmeName = (id: string) => {
    const programme = state?.configuration?.programmes.find((p) => p.id === id);
    return programme ? `${programme.title} Level ${programme.level}` : id;
  };
  return <section aria-label="Programme suitability assessment" className="mt-6 space-y-4 rounded-xl border border-primary-200 p-4">
    <h3 className="font-semibold">Programme Suitability Assessment</h3>
    <p className="text-sm">Complete a short assessment to help identify which apprenticeship programme best matches your current job responsibilities.</p>
    {loading && !state && <p role="status" className="text-sm">Loading your assessment…</p>}
    {error && <div role="alert" className="text-sm text-red-700"><p>{error}</p>
      <button type="button" className={btnSecondary} disabled={loading} onClick={() => { void suitability.refresh(); }}>Retry</button>
    </div>}
    {!loading && !error && state && (!state.available || !state.configuration?.enabled) &&
      <p role="status" className="text-sm">The assessment is not currently available. Please contact the enrolment team if you expected to complete it.</p>}
    {suitability.status === 'SUBMITTED' && state?.result && <RecommendationResult state={state} />}
    {readOnly && state && suitability.status !== 'SUBMITTED' && <p role="status" className="text-sm">
      {suitability.status === 'IN_PROGRESS' ? 'Assessment in progress' : 'Assessment not started'}
    </p>}
    {readOnly && suitability.status === 'SUBMITTED' && state?.result && <button type="button" className={btnPrimary} onClick={suitability.start}>Preview Assessment</button>}
    {!readOnly && (!state || (state.available && state.configuration?.enabled)) && <>
      <button type="button" className={btnPrimary} onClick={suitability.start}>
        {suitability.status === 'SUBMITTED' ? 'Preview Assessment' : state?.revision ? 'Resume Assessment' : 'Start Assessment'}
      </button>
      {!selectionConfirmed(state) && <p className="text-xs text-foreground-500">CV upload and experience text are optional for this assessment. Assessment progress saves separately.</p>}
    </>}
    {readOnly && state && <section aria-label="Admissions assessment details" className="space-y-4 border-t pt-4">
      <p className="text-sm">Review status: {state.reviewStatus.replaceAll('_', ' ')}</p>
      {state.pathway && <details className="text-sm"><summary className="cursor-pointer font-semibold">Saved assessment answers</summary>
        <div className="mt-3"><SubmittedAnswers state={state} /></div>
      </details>}
      {state.review?.reason && <p className="rounded-lg bg-primary-50 p-3 text-sm">Admissions review: {state.review.reason}
        {state.review.recommendedProgramme ? ` Suggested programme: ${programmeName(state.review.recommendedProgramme)}. Your preference has been preserved.` : ''}</p>}
      {state.review?.learnerRequest && <p className="rounded-lg bg-background-100 p-3 text-sm">Learner review request: {state.review.learnerRequest.reason}</p>}
      {state.result && <AdmissionsReview state={state} onSave={async (status, programme, reason) => {
        const saved = await saveReview(kind, userId, state.revision, status, programme, reason); suitability.accept(saved);
      }} />}
    </section>}
    {suitability.open && <AssessmentDialog key={userId} readOnly={readOnly} initial={state} kind={kind} learnerId={userId}
      loading={loading} loadError={error} onRetry={suitability.refresh} onSaved={suitability.accept}
      onClose={suitability.close} onSubmitted={suitability.submitted} onConfirmed={suitability.continued} />}
  </section>;
}

function AdmissionsReview({ state, onSave }: { state: AssessmentState;
  onSave: (status: string, programme: ProgrammeId | '', reason: string) => Promise<void> }) {
  const [status, setStatus] = useState(state.reviewStatus);
  const [programme, setProgramme] = useState<ProgrammeId | ''>(state.review?.recommendedProgramme ?? '');
  const [reason, setReason] = useState(state.review?.reason ?? '');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  return <fieldset className="space-y-3 border-t pt-4" disabled={busy}>
    <legend className="font-semibold">Admissions review</legend>
    <label className="block text-sm">Review status<select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
      {['PRELIMINARY_RECOMMENDATION', 'PENDING_ADMISSIONS_REVIEW', 'FURTHER_INFORMATION_REQUIRED', 'REVIEW_COMPLETED'].map((s) => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}
    </select></label>
    <label className="block text-sm">Manual programme recommendation<select className={inputClass} value={programme} onChange={(e) => setProgramme(e.target.value as ProgrammeId | '')}>
      <option value="">Keep automated recommendation</option>{state.configuration!.programmes.filter((p) => p.pathway === state.pathway && p.active).map((p) => <option key={p.id} value={p.id}>{p.title} Level {p.level}</option>)}
    </select></label>
    <label className="block text-sm">Reason for review / override<textarea className={inputClass} rows={3} maxLength={4000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
    <button type="button" className={btnSecondary} onClick={() => {
      if (!reason.trim()) { setMessage('A review reason is required.'); return; }
      setBusy(true); void onSave(status, programme, reason).then(() => setMessage('Review saved. It is visible to the learner in their assessment.'))
        .catch((e: Error) => setMessage(e.message)).finally(() => setBusy(false));
    }}>Save admissions review</button>
    {message && <p role="status" className="text-sm">{message}</p>}
    <p className="text-xs text-foreground-500">The learner can continue enrolment while review is pending. Reviews remain visible in their assessment; email delivery depends on the existing mail service.</p>
  </fieldset>;
}
