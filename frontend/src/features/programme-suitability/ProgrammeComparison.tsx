import type { Programme, ProgrammeId, Result } from './types';

const explanations: Record<string, string> = {
  NO_RESPONSIBILITY_EVIDENCE: 'More information about your actual responsibilities is needed.',
  LOW_ASSESSMENT_COVERAGE: 'Too few questions have been answered for a clear recommendation.',
  BELOW_MINIMUM_MATCH: 'The evidence does not reach the configured recommendation threshold.',
  CLOSE_PROGRAMME_SCORES: 'The two programmes have similar matching evidence.',
  CONTRADICTORY_RESPONSIBILITIES: 'The answers indicate mixed responsibilities across both levels.',
  MISSING_CRITICAL_LEVEL_6_EVIDENCE: 'More distinct evidence of Level 6 responsibilities is needed.',
};

export function ProgrammeComparison({ programmes, result, selected, onSelect }: {
  programmes: Programme[]; result: Result; selected?: ProgrammeId | ''; onSelect?: (id: ProgrammeId) => void;
}) {
  const recommended = programmes.find((p) => p.id === result.recommendedProgramme);
  return <section className="space-y-4" aria-label="Programme comparison">
    <p className="rounded-xl bg-primary-50 p-3 text-sm">{recommended
      ? <>Preliminary recommendation: <strong>{recommended.title} Level {recommended.level}</strong>.</>
      : <strong>Further admissions review is needed to make a recommendation.</strong>}
      {' '}This is guidance about role responsibilities, not a final eligibility or admissions decision.</p>
    {!recommended && <ul className="list-disc pl-5 text-sm">{result.reasons.map((reason) =>
      <li key={reason}>{explanations[reason] ?? reason.replaceAll('_', ' ')}</li>)}</ul>}
    <div className="grid gap-4 md:grid-cols-2">{[...programmes].sort((a, b) => a.order - b.order).map((p) => <article key={p.id}
      className={`rounded-xl border p-4 ${recommended?.id === p.id ? 'border-primary-500 bg-primary-50' : 'border-foreground-200'}`}>
      <h4 className="font-semibold">{p.title} — Level {p.level}</h4>
      <p className="mt-1 text-sm text-foreground-500">{p.description}</p>
      <p className="mt-2 text-sm">{p.summary}</p>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">{p.responsibilities.map((bullet, i) => <li key={i}>{bullet}</li>)}</ul>
      <p className="mt-3 text-sm">Responsibility matching index: <strong>{result.scores[p.id]}</strong> / 100</p>
      <p className="text-xs text-foreground-500">An internal matching index, not a probability of acceptance.</p>
      {onSelect && <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm font-semibold">
        <input type="radio" name="suitability-programme" checked={selected === p.id} onChange={() => onSelect(p.id)} />Choose {p.title}
      </label>}
    </article>)}</div>
    {result.strongestEvidence.length > 0 && <div><h4 className="font-semibold">What contributed most</h4>
      <ul className="list-disc space-y-1 pl-5 text-sm">{result.strongestEvidence.map((e) => <li key={e.questionId}>
        <strong>{e.title}:</strong> {e.selectedAnswers.map((a) => a.text).join(' ')}
      </li>)}</ul></div>}
    {selected && programmes.find((p) => p.id === selected)?.level === 4 && recommended?.level === 6 &&
      <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Your answers show stronger evidence for {recommended.title} Level 6.
        You may wish to discuss it with admissions. You can keep your Level 4 preference.</p>}
  </section>;
}
