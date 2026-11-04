import { useId, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import type { Ksb, KsbAssessment } from '@/pages/users/types';
import type { RadarModule } from './allocation';

export function ModuleMasterDetail({ modules, assessments, selectedModuleId, onSelect, renderQuestion }: {
  modules: (RadarModule & { sessions?: number | null })[]; assessments: Record<string, KsbAssessment>; selectedModuleId: string | null;
  onSelect: (moduleId: string) => void; renderQuestion: (ksb: Ksb) => ReactNode;
}) {
  const panelId = useId();
  const active = modules.find((module) => module.id === selectedModuleId) ?? modules[0];
  const answered = (module: RadarModule) => module.questions.filter((ksb) => assessments[ksb.id]?.level).length;
  if (!active) return null;
  return <div dir="ltr" style={{ direction: 'ltr' }} aria-label="Selected module skills assessments"
    className="grid w-full min-w-0 grid-cols-1 items-start gap-4 text-left lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]">
    <nav aria-label="Selected modules" className="col-start-1 row-start-1 min-w-0 space-y-3">
      {modules.map((module) => {
        const selected = module.id === active.id;
        return <button key={module.id} type="button" onClick={() => onSelect(module.id)}
          aria-label={module.title} aria-pressed={selected} aria-controls={panelId} aria-describedby={`${panelId}-${module.id}-progress`}
          className={`flex w-full items-center justify-between gap-3 rounded-xl border p-4 text-left transition-colors ${selected
            ? 'border-primary-400 bg-primary-50 text-primary-900 shadow-sm' : 'border-foreground-200 bg-background-50 text-foreground-800 hover:bg-primary-50/50'}`}>
          <span className="min-w-0 flex-1 text-left [overflow-wrap:anywhere]"><span className="block text-sm font-semibold">{module.title}</span>
            {module.sessions != null && <span className="mt-1 block text-xs text-foreground-600">{module.sessions} Sessions</span>}
            <span id={`${panelId}-${module.id}-progress`} className="mt-1 block text-xs text-foreground-600">{answered(module)} of {module.questions.length} answered</span></span>
          <ChevronRight aria-hidden="true" className={`h-4 w-4 shrink-0 ${selected ? 'text-primary-600' : 'text-foreground-400'}`} />
        </button>;
      })}
    </nav>
    <section id={panelId} aria-label={`${active.title} KSB questions`}
      className="col-start-1 row-start-2 min-w-0 overflow-hidden rounded-xl border border-foreground-200 bg-background-50 lg:col-start-2 lg:row-start-1">
      <header className="border-b border-foreground-200 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h3 className="min-w-0 text-base font-semibold text-foreground-900 [overflow-wrap:anywhere]">{active.title}</h3>
          <span className="flex gap-3 text-xs text-foreground-600">{active.sessions != null && <span>{active.sessions} Sessions</span>}<span>{active.questions.length} KSBs</span></span>
        </div>
        <p className="mt-1 text-xs text-foreground-600">{answered(active)} of {active.questions.length} answered</p>
      </header>
      <div className="divide-y divide-foreground-100">{active.questions.map(renderQuestion)}</div>
    </section>
  </div>;
}
