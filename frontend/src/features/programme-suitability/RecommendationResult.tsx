import { btnSecondary } from '@/pages/users/components/ui';
import { alternativeProgramme, recommendedProgramme } from './recommendation';
import { completion, selectionConfirmed } from './useSuitability';
import type { AssessmentState, ProgrammeId } from './types';

type ResultProps = { state: AssessmentState } & (
  { mode?: 'summary'; onSelect?: never } |
  { mode: 'confirmation'; onSelect: (id: ProgrammeId) => void }
);

export function RecommendationResult({ state, mode = 'summary', onSelect }: ResultProps) {
  const recommended = recommendedProgramme(state);
  const alternative = alternativeProgramme(state);
  const selected = state.configuration?.programmes.find((p) => p.id === state.selectedProgramme);
  const confirmedAlternative = selectionConfirmed(state) && recommended && selected && selected.id !== recommended.id ? selected : null;
  return <section aria-label="Assessment recommendation result" className="space-y-4">
    <p className="text-sm font-semibold text-primary-700">Assessment Completed</p>
    {recommended ? <div className="space-y-4 rounded-2xl border border-primary-300 bg-primary-50/60 p-5 md:p-6">
      <h3 className="text-lg font-semibold">Your Recommended Programme</h3>
      <h4 className="text-2xl font-bold text-primary-800">{recommended.title} — Level {recommended.level}</h4>
      <p className="text-sm text-foreground-600">Based on your answers and current job responsibilities.</p>
      {confirmedAlternative && <div className="space-y-3 border-t border-primary-200 pt-4">
        <h5 className="font-semibold">Your Confirmed Programme Selection</h5>
        <p role="status" className="text-base font-semibold text-primary-800 md:text-lg">
          You chose {confirmedAlternative.title} Level {confirmedAlternative.level} instead of {recommended.title} Level {recommended.level}.
        </p>
        {state.alternativeSelectionReason && <div className="space-y-1">
          <h5 className="text-sm font-semibold">Your Reason</h5>
          <p className="whitespace-pre-wrap text-sm text-foreground-700 [overflow-wrap:anywhere]">{state.alternativeSelectionReason}</p>
        </div>}
      </div>}
    </div> : <p role="status" className="rounded-lg bg-background-100 p-3 text-sm">
      There is not enough responsibility evidence to recommend a programme. You can continue enrolment.
    </p>}
    {!confirmedAlternative && selected && selected.id !== recommended?.id && <p role="status" className="text-sm">
      {selectionConfirmed(state) ? 'Selected programme: ' : 'Saved preference: '}
      <strong>{selected.title} Level {selected.level}</strong>.
    </p>}
    {mode === 'confirmation' && completion(state) === 'SUBMITTED' && !selectionConfirmed(state) && alternative && <div className="space-y-2">
      <p className="text-sm">Interested in {alternative.title} Level {alternative.level} instead?</p>
      <button type="button" disabled={recommended?.level !== 6 || !onSelect}
        className={`${btnSecondary} disabled:cursor-not-allowed disabled:opacity-50`}
        onClick={() => { if (recommended?.level === 6) onSelect?.(alternative.id); }}>
        {recommended?.level === 6 ? `Choose ${alternative.title} L${alternative.level}` : `${alternative.title} L${alternative.level} — Not Available`}
      </button>
    </div>}
  </section>;
}
