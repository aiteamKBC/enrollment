import type { AssessmentState } from './types';

/** Render the assessment's saved configuration snapshot, never the current editor version. */
export function SubmittedAnswers({ state }: { state: AssessmentState }) {
  return <section aria-label="Submitted assessment answers" className="space-y-5">
    <h3 className="text-lg font-semibold">Submitted Answers</h3>
    <p className="text-sm">Pathway: {state.pathway === 'marketing' ? 'Marketing' : 'Project Controls'}</p>
    {state.configuration?.questions.filter((q) => q.pathway === state.pathway).map((q) => <fieldset key={q.id}
      disabled className="space-y-3 rounded-xl border border-foreground-200 p-4">
      <legend className="px-1 font-semibold">{q.title}</legend>
      <p className="text-sm text-foreground-600">{q.description}</p>
      {q.answers.map((a) => <label key={a.id} className="flex items-start gap-3 text-sm">
        <input type="checkbox" disabled checked={state.responses[q.id]?.includes(a.id) ?? false} readOnly className="mt-1 accent-primary-600" />
        <span>{a.text}</span>
      </label>)}
      <label className="flex items-start gap-3 text-sm">
        <input type="checkbox" disabled checked={q.id in state.responses && state.responses[q.id].length === 0} readOnly className="mt-1 accent-primary-600" />
        <span>None of these responsibilities applies to me.</span>
      </label>
    </fieldset>)}
  </section>;
}
