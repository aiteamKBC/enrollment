import type { PublicQuestion, Responses } from './types';

export function AssessmentQuestions({ questions, responses, onChange, missing = [] }: {
  questions: PublicQuestion[]; responses: Responses; onChange: (responses: Responses) => void; missing?: string[];
}) {
  return <div className="space-y-5">{questions.map((q) => <fieldset key={q.id}
    aria-invalid={missing.includes(q.id) || undefined} className="rounded-xl border border-foreground-200 p-4">
    <legend className="px-1 font-semibold">{q.title}{q.required ? ' *' : ''}</legend>
    <p className="mb-3 text-sm text-foreground-600">{q.description}</p>
    <p className="mb-3 text-xs text-foreground-500">{q.multiple ? 'Select all that apply to responsibilities you actually perform.' : 'Select the responsibility you actually perform.'}</p>
    <div className="space-y-3">{q.answers.map((a) => <label key={a.id} className="flex cursor-pointer items-start gap-3 text-sm">
      <input type={q.multiple ? 'checkbox' : 'radio'} name={`suitability-${q.id}`} className="mt-1 accent-primary-600"
        checked={responses[q.id]?.includes(a.id) ?? false} onChange={(e) => onChange({ ...responses,
          [q.id]: e.target.checked ? (q.multiple ? [...(responses[q.id] ?? []), a.id] : [a.id]) : (responses[q.id] ?? []).filter((id) => id !== a.id) })} />
      <span>{a.text}</span>
    </label>)}
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type={q.multiple ? 'checkbox' : 'radio'} name={`suitability-${q.id}`} className="mt-1 accent-primary-600"
        checked={q.id in responses && responses[q.id].length === 0}
        onChange={(e) => { const next = { ...responses }; if (e.target.checked) next[q.id] = []; else delete next[q.id]; onChange(next); }} />
      <span>None of these responsibilities applies to me.</span>
    </label></div>
    {missing.includes(q.id) && <p role="alert" className="mt-2 text-sm text-red-700">Select an answer or choose none apply.</p>}
  </fieldset>)}</div>;
}
