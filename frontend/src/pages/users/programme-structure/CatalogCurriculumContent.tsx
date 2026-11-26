import type { Topic, WeightedKsb } from './catalogApi';
import { catalogueFocus } from './catalogControls';

export function CurriculumContent({ topics, ksbs, embedded = false }: {
  topics: Topic[];
  ksbs: WeightedKsb[];
  embedded?: boolean;
}) {
  const panel = embedded ? 'border-t border-background-200 pt-3'
    : 'rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5';
  return <div className="grid items-start gap-5 lg:grid-cols-2">
    <section className={`min-w-0 space-y-3 ${panel}`}>
      <h3 className="font-semibold">Weekly topics</h3>
      {topics.length ? topics.map(topic => <details key={topic.id ?? topic.week} className="border-t border-background-200 pt-2">
        <summary className={`cursor-pointer text-sm font-semibold ${catalogueFocus}`}>Week {topic.week}</summary>
        <p className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground-700">{topic.content}</p>
      </details>) : <p className="text-sm text-foreground-600">No weekly topics recorded.</p>}
    </section>
    <section className={`min-w-0 space-y-3 ${panel}`}>
      <h3 className="font-semibold">Knowledge, skills and behaviours</h3>
      {ksbs.length ? <ul className="space-y-2">{ksbs.map(ksb => <li key={ksb.id} className="break-words text-sm">
        <strong>{ksb.code}</strong> — {ksb.description}
        {ksb.mapping_weight && <span className="mt-1 block text-xs text-foreground-600">
          Total Mapping Weight: <strong>{ksb.mapping_weight.total_weight}</strong> ·
          Hard {ksb.mapping_weight.hard_count} ({ksb.mapping_weight.hard_weight}) ·
          Soft {ksb.mapping_weight.soft_count} ({ksb.mapping_weight.soft_weight}) ·
          Possible {ksb.mapping_weight.possible_count} ({ksb.mapping_weight.possible_weight})
        </span>}
      </li>)}</ul> : <p className="text-sm text-foreground-600">No KSBs linked.</p>}
    </section>
  </div>;
}
