import { type ReactNode, useState } from 'react';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { fetchDefaults, previewAssessment } from './api';
import { AssessmentQuestions } from './AssessmentQuestions';
import { ProgrammeComparison } from './ProgrammeComparison';
import type { Answer, Configuration, Pathway, Question, Responses, Result } from './types';

const tabs = ['Marketing Assessment', 'Project Controls Assessment', 'Programme Modules', 'Preview & Test'];
const scoringRowClass = 'grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,3fr)]';
const newId = () => crypto.randomUUID();

function TextField({ label, value, onChange, multiline = false }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean }) {
  return <label className="block text-sm">{label}{multiline
    ? <textarea className={inputClass} rows={3} value={value} onChange={(e) => onChange(e.target.value)} />
    : <input className={inputClass} value={value} onChange={(e) => onChange(e.target.value)} />}</label>;
}
function Numeric({ label, value, onChange, max = 1000, min = 0, step = 0.01 }: { label: string; value: number; onChange: (value: number) => void; max?: number; min?: number; step?: number }) {
  return <label className="block text-sm">{label}<input className={inputClass} type="number" min={min} max={max} step={step} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.value === '' ? 0 : e.target.valueAsNumber)} /></label>;
}
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{label}</label>;
}

export function SuitabilityAdmin({ value, onChange, modulesEditor }: { value?: Configuration; onChange: (configuration: Configuration) => void; modulesEditor?: ReactNode }) {
  const [tab, setTab] = useState(tabs[0]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const patch = (changes: Partial<Configuration>) => { if (value) onChange({ ...value, ...changes }); };
  return <section aria-label="Programme Suitability Assessment configuration" className="m-5 space-y-4 rounded-xl border border-primary-200 bg-background-50 p-4">
    <h3 className="font-semibold">Programme Suitability Assessment</h3>
    <p className="text-sm">Changes are drafts until you use the wizard’s <strong>Publish changes</strong> button. Each learner keeps the configuration version they started with.</p>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    <div role="tablist" aria-label="Suitability configuration" className="flex flex-wrap gap-2">{tabs.map((name, i) =>
      <button key={name} type="button" role="tab" id={`suitability-tab-${i}`} aria-controls="suitability-tab-panel" aria-selected={tab === name}
        className={tab === name ? btnPrimary : btnSecondary} onClick={() => setTab(name)}>{name}</button>)}</div>
    <div role="tabpanel" id="suitability-tab-panel" aria-labelledby={`suitability-tab-${tabs.indexOf(tab)}`} className="space-y-4">
    <div hidden={tab !== 'Programme Modules'}>{modulesEditor}</div>
    {tab !== 'Programme Modules' && (!value ? <button type="button" className={btnPrimary} disabled={busy} onClick={() => {
      setBusy(true); setError(''); void fetchDefaults().then((data) => onChange(data.configuration))
        .catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
    }}>{busy ? 'Loading…' : 'Add the initial 18-question assessment to this draft'}</button> : <>
        {(tab === 'Marketing Assessment' || tab === 'Project Controls Assessment') && <QuestionEditor key={tab} configuration={value}
          pathway={tab === 'Marketing Assessment' ? 'marketing' : 'project-controls'} onChange={(questions) => patch({ questions })} />}
        {tab === 'Preview & Test' && <Preview configuration={value} />}
    </>)}
    </div>
    {value && <details className="space-y-4 rounded-lg border p-3">
      <summary className="cursor-pointer font-semibold">Advanced configuration</summary>
      <p className="text-sm text-foreground-500">Assessment activation, learner introduction, programme associations and background review rules. Changes use the existing draft and Publish changes workflow.</p>
      <fieldset className="space-y-3 rounded-lg border p-3"><legend className="px-1 font-semibold">General Settings</legend>
          <Toggle label="Enable assessment after publishing" checked={value.enabled} onChange={(enabled) => patch({ enabled })} />
          <TextField label="Learner introduction" multiline value={value.introduction} onChange={(introduction) => patch({ introduction })} />
          <details className="space-y-3 rounded-lg border p-3"><summary className="cursor-pointer font-semibold">Advanced programme associations</summary>
            <p className="text-xs text-foreground-500">Optional existing catalogue references are used when resolving a confirmed programme to an enrolment assignment. Saved programme guidance and names are retained.</p>
            {value.programmes.map((programme) => <TextField key={programme.id} label={`${programme.title}: existing catalogue ID (optional reference)`}
              value={programme.catalogueId ?? ''} onChange={(catalogueId) => patch({ programmes: value.programmes.map((item) => item.id === programme.id ? { ...item, catalogueId: catalogueId || null } : item) })} />)}
          </details>
          <p className="text-xs text-foreground-500">Initial strengths and thresholds are provisional heuristics. Test them with admissions-reviewed examples before relying on recommendations.</p>
      </fieldset>
      <fieldset className="space-y-3 rounded-lg border p-3"><legend className="px-1 font-semibold">Scoring Rules</legend>
          <p className="text-sm">Publishing applies general weighted scoring without critical indicator checks to new assessment snapshots. Started assessments retain their saved rules. Preview tests the policy that will be published.</p>
          <p className="text-sm">These thresholds flag evidence for admissions review. The learner recommendation uses the higher weighted programme index; a positive tie recommends Level 4.</p>
          <fieldset className="space-y-3 rounded-lg border p-3"><legend className="px-1 font-semibold">Match and coverage</legend>
          <p className="text-xs text-foreground-500">Flag a low match, a small gap between programme indices or limited answered question weight. Choosing none apply counts as answered.</p>
          {([
            ['minimumScore', 'Minimum match index (0–100)', 100, 0.01], ['minimumDifference', 'Minimum score difference (percentage points)', 100, 0.01],
            ['minimumCoverage', 'Minimum answered-question coverage (%)', 100, 0.01],
          ] as const).map(([key, label, max, step]) => <Numeric key={key} label={label} value={value.rules[key]} max={max} step={step}
            onChange={(number) => patch({ rules: { ...value.rules, [key]: number } })} />)}
          </fieldset>
          <details className="space-y-3 rounded-lg border p-3"><summary className="cursor-pointer font-semibold">Advanced Scoring Settings</summary>
          <p className="text-xs text-foreground-500">Mixed evidence flags questions with enough strength for both programmes.</p>
          {([
            ['mixedQuestionThreshold', 'Review when mixed evidence reaches this share of question weight (%)', 100, 0.01],
            ['mixedAffinityThreshold', 'Per-programme strength defining mixed evidence', 1, 0.01],
          ] as const).map(([key, label, max, step]) => <Numeric key={key} label={label} value={value.rules[key]} max={max} step={step}
            onChange={(number) => patch({ rules: { ...value.rules, [key]: number } })} />)}
          </details>
          <p className="text-sm">Points = question weight × answer affinity, capped at the question weight separately for each programme. All active question weights form the denominator, including unanswered and none-apply questions.</p>
      </fieldset>
    </details>}
  </section>;
}

function QuestionEditor({ configuration, pathway, onChange }: { configuration: Configuration; pathway: Pathway; onChange: (questions: Question[]) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const questions = configuration.questions.filter((q) => q.active && q.pathway === pathway).sort((a, b) => a.order - b.order);
  const programmes = configuration.programmes.filter((p) => p.pathway === pathway);
  const update = (qid: string, changes: Partial<Question>) => onChange(configuration.questions.map((q) => q.id === qid ? { ...q, ...changes } : q));
  const reorder = (index: number, delta: number) => {
    const moved = [...questions]; [moved[index], moved[index + delta]] = [moved[index + delta], moved[index]];
    const orders = Object.fromEntries(moved.map((q, i) => [q.id, i]));
    onChange(configuration.questions.map((q) => q.active && q.pathway === pathway ? { ...q, order: orders[q.id] } : q));
  };
  return <div className="space-y-4">{questions.map((q, qi) => {
    const updateAnswer = (aid: string, changes: Partial<Answer>) => update(q.id, { answers: q.answers.map((a) => a.id === aid ? { ...a, ...changes } : a) });
    const answers = q.answers.filter((a) => a.active).sort((a, b) => a.order - b.order);
    return <section key={q.id} aria-label={`Question ${qi + 1}: ${q.title}`} className="rounded-xl border p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1"><h4 className="font-semibold">{qi + 1}. {q.title}</h4>
          <p className="line-clamp-2 text-sm text-foreground-500">{q.description}</p>
        </div>
        <button type="button" className={btnSecondary} aria-expanded={editing === q.id} aria-controls={`question-editor-${q.id}`} onClick={() => setEditing(editing === q.id ? null : q.id)}>{editing === q.id ? 'Close editor' : 'Edit'}</button>
        <button type="button" className={btnSecondary} disabled={qi === 0} onClick={() => reorder(qi, -1)}>Move question up</button>
        <button type="button" className={btnSecondary} disabled={qi === questions.length - 1} onClick={() => reorder(qi, 1)}>Move question down</button>
        <button type="button" className={btnSecondary} onClick={() => { update(q.id, { active: false }); setEditing(null); }}>Remove question</button>
      </div>
      {editing === q.id && <div id={`question-editor-${q.id}`} className="mt-4 space-y-3">
        <div className={scoringRowClass}>
          <div className="min-w-0 lg:col-start-1">
            <TextField label="Question title" value={q.title} onChange={(title) => update(q.id, { title })} />
          </div>
          <div className="min-w-0 space-y-3 lg:col-start-1">
            <TextField label="Question text / description" multiline value={q.description} onChange={(description) => update(q.id, { description })} />
            <Toggle label="Required question" checked={q.required} onChange={(required) => update(q.id, { required })} />
          </div>
          <div className="min-w-0 lg:col-start-2 lg:row-start-2">
            <Numeric label="Question weight" value={q.weight} min={0.01} onChange={(weight) => update(q.id, { weight })} />
          </div>
        </div>
        {answers.map((a, ai) => <fieldset key={a.id} className="space-y-3 rounded-lg border p-3"><legend className="px-1 text-sm">Answer {ai + 1}</legend>
          <div className={scoringRowClass}>
          <div className="min-w-0 space-y-3">
          <TextField label="Answer text" multiline value={a.text} onChange={(text) => updateAnswer(a.id, { text })} />
          <div className="flex flex-wrap gap-2">{([-1, 1] as const).map((delta) => <button key={delta} type="button" className={btnSecondary}
            disabled={delta < 0 ? ai === 0 : ai === answers.length - 1} onClick={() => {
              const moved = [...answers]; [moved[ai], moved[ai + delta]] = [moved[ai + delta], moved[ai]];
              const orders = Object.fromEntries(moved.map((item, order) => [item.id, order]));
              update(q.id, { answers: q.answers.map((item) => item.active ? { ...item, order: orders[item.id] } : item) });
            }}>Move answer {delta < 0 ? 'up' : 'down'}</button>)}
            <button type="button" className={btnSecondary} onClick={() => updateAnswer(a.id, { active: false })}>Remove answer</button>
          </div>
          </div>
          <div className="min-w-0 space-y-3">
            {programmes.map((p) => <Numeric key={p.id} label={`${p.title} affinity (0–1)`} value={a.affinities[p.id] ?? 0} max={1}
              onChange={(strength) => updateAnswer(a.id, { affinities: { ...a.affinities, [p.id]: strength } })} />)}
          </div>
          </div>
        </fieldset>)}
        <button type="button" className={btnSecondary} onClick={() => update(q.id, { answers: [...q.answers, { id: newId(), text: 'New answer', active: true, order: Math.max(-1, ...q.answers.map((item) => item.order)) + 1, affinities: {} }] })}>Add answer</button>
        <details className="space-y-3 rounded-lg border p-3"><summary className="cursor-pointer font-semibold">Advanced Settings</summary>
          <p className="text-xs text-foreground-500">Stable ID: {q.id} / Slug: {q.slug}</p>
          <Toggle label="Allow multiple selected answers" checked={q.multiple} onChange={(multiple) => update(q.id, { multiple })} />
          {answers.map((a, ai) => <p key={a.id} className="text-xs text-foreground-500 [overflow-wrap:anywhere]">Answer {ai + 1} stable ID: {a.id}</p>)}
        </details>
      </div>}
    </section>;
  })}
    <button type="button" className={btnPrimary} onClick={() => {
      const id = newId(); setEditing(id);
      onChange([...configuration.questions, { id, slug: `question-${id}`, title: 'New question', description: 'Which responsibilities do you perform?',
        pathway, weight: 10, order: Math.max(-1, ...configuration.questions.filter((q) => q.pathway === pathway).map((q) => q.order)) + 1, active: true, required: true, multiple: true, critical: false, criticalProgramme: null,
        answers: ['A', 'B', 'C', 'D'].map((letter, index) => ({ id: `${id}_${letter}`, text: `Answer ${letter}`, order: index, active: true,
          affinities: { [programmes.find((p) => p.level === (index < 2 ? 4 : 6))!.id]: [0.4, 0.8, 0.6, 1][index] } })) }]);
    }}>Add question</button>
    <p className="text-xs text-foreground-500">Removing an item excludes it from this draft and new assessments after publishing. Published versions and earlier assessments retain their original content.</p>
  </div>;
}

function Preview({ configuration }: { configuration: Configuration }) {
  const [pathway, setPathway] = useState<Pathway>('marketing');
  const [responses, setResponses] = useState<Responses>({});
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const questions = configuration.questions.filter((q) => q.active && q.pathway === pathway).sort((a, b) => a.order - b.order)
    .map((q) => ({ ...q, answers: q.answers.filter((a) => a.active).sort((a, b) => a.order - b.order) }));
  return <div className="space-y-4">
    <p className="text-sm">Tests the current draft on the backend using the existing assessment and result components. This does not create or update a learner application. Saved preferences, admissions reviews, cohort schedules and confirmed learner selections require an actual learner record and are not reproduced here. Use Programme Modules, then Preview Changes to test module and pathway choices.</p>
    <label className="block text-sm">Preview pathway<select className={inputClass} value={pathway} onChange={(e) => {
      setPathway(e.target.value as Pathway); setResponses({}); setResult(null);
    }}><option value="marketing">Marketing</option><option value="project-controls">Project Controls</option></select></label>
    <AssessmentQuestions questions={questions} responses={responses} onChange={(next) => { setResponses(next); setResult(null); }} />
    <button type="button" className={btnPrimary} disabled={busy} onClick={() => {
      setBusy(true); setError(''); setResult(null); void previewAssessment(configuration, pathway, responses).then(setResult)
        .catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
    }}>{busy ? 'Calculating…' : 'Calculate preview'}</button>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {result && <>
      <ProgrammeComparison programmes={configuration.programmes.filter((p) => p.active && p.pathway === pathway)} result={result} />
      <p className="text-sm">Outcome: {result.outcome} · Coverage: {result.coverage}%</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="text-left font-semibold">Per-question capped points</caption>
        <thead><tr><th scope="col">Question</th><th scope="col">Weight</th>{Object.keys(result.scores).map((id) => <th scope="col" key={id}>{id}</th>)}</tr></thead>
        <tbody>{result.breakdown?.map((row) => <tr key={row.questionId}><th scope="row" className="py-2 font-normal">{row.title}</th><td>{row.weight}</td>{Object.keys(result.scores).map((id) => <td key={id}>{row.points[id]}</td>)}</tr>)}</tbody>
      </table></div>
      <p className="text-xs text-foreground-500">Machine-readable reasons: {result.reasons.filter((reason) => reason !== 'MISSING_CRITICAL_LEVEL_6_EVIDENCE').join(', ')}</p>
    </>}
  </div>;
}
