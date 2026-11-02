import { useState } from 'react';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { saveReview } from './api';
import { selectionConfirmed } from './useSuitability';
import { AssessmentDialog } from './AssessmentDialog';
import { ProgrammeComparison } from './ProgrammeComparison';
import { RecommendationResult } from './RecommendationResult';
import type { AssessmentState, ProgrammeId } from './types';

/** Review lives beside the existing CV uploads/job fields; no separate dashboard. */
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
    {error && <div role="alert" className="text-sm text-red-700"><p>{error}</p>
      <button type="button" className={btnSecondary} disabled={loading} onClick={() => { void suitability.refresh(); }}>Retry</button>
    </div>}
    {!readOnly && suitability.status === 'SUBMITTED' && state?.result && <RecommendationResult state={state} />}
    {readOnly && state?.result && state.pathway && <>
      <p className="text-sm">{state.reviewStatus.replaceAll('_', ' ')}</p>
      <ProgrammeComparison programmes={state.configuration!.programmes.filter((p) => p.pathway === state.pathway && p.active)}
        result={state.result} selected={state.selectedProgramme} />
      <p className="text-sm">Learner preference: {programmeName(state.selectedProgramme) || 'Not confirmed'}{state.originalPreference ? ` / Original preference: ${programmeName(state.originalPreference)}` : ''}</p>
      {selectionConfirmed(state) && state.selectedProgramme !== state.result.recommendedProgramme && state.alternativeSelectionReason && <div className="space-y-1 text-sm">
        <h4 className="font-semibold">Learner-provided alternative programme reason</h4>
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{state.alternativeSelectionReason}</p>
      </div>}
      {readOnly && <div className="text-sm"><h4 className="font-semibold">Critical Level 6 evidence</h4>
        {Object.entries(state.result.criticalIndicators).filter(([id]) => state.configuration!.programmes.some((p) => p.id === id && p.level === 6)).map(([id, questionIds]) =>
          <p key={id}>{programmeName(id)}: {questionIds.length} distinct indicators{questionIds.length ? ` / ${questionIds.map((qid) => state.configuration!.questions.find((q) => q.id === qid)?.title ?? qid).join('; ')}` : ' / no qualifying indicators'}</p>)}
      </div>}
      <details className="text-sm"><summary className="cursor-pointer font-semibold">Saved assessment answers</summary>
        {state.configuration!.questions.filter((q) => q.pathway === state.pathway).map((q) => <div className="mt-3" key={q.id}>
          <strong>{q.title}</strong><p>{q.id in state.responses ? state.responses[q.id].length
            ? q.answers.filter((a) => state.responses[q.id].includes(a.id)).map((a) => a.text).join(' ')
            : 'None apply' : 'Not answered'}</p></div>)}
      </details>
      {state.review?.reason && <p className="rounded-lg bg-primary-50 p-3 text-sm">Admissions review: {state.review.reason}
        {state.review.recommendedProgramme ? ` Suggested programme: ${programmeName(state.review.recommendedProgramme)}. Your preference has been preserved.` : ''}</p>}
      {state.review?.learnerRequest && <p className="rounded-lg bg-background-100 p-3 text-sm">Learner review request: {state.review.learnerRequest.reason}</p>}
    </>}
    {!readOnly && <>
      <button type="button" className={btnPrimary} onClick={suitability.start}>
        {suitability.status === 'SUBMITTED' ? 'Preview Assessment' : state?.revision ? 'Resume Assessment' : 'Start Assessment'}
      </button>
      {!selectionConfirmed(state) && <p className="text-xs text-foreground-500">CV upload and experience text are optional for this assessment. Assessment progress saves separately.</p>}
    </>}
    {readOnly && state?.result && <AdmissionsReview state={state} onSave={async (status, programme, reason) => {
      const saved = await saveReview(kind, userId, state.revision, status, programme, reason); suitability.accept(saved);
    }} />}
    {suitability.open && <AssessmentDialog key={userId} initial={state} kind={kind} learnerId={userId}
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
