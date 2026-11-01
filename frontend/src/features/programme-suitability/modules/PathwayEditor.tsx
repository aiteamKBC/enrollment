import { useState } from 'react';
import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import { ordered, type ModulePathway } from './configuration';
import { ModuleRowsEditor } from './ModuleRowEditor';
import { moveOrdered } from './editorOps';

export function PathwayEditor({ pathways, onChange }: { pathways: ModulePathway[]; onChange: (paths: ModulePathway[]) => void }) {
  const [selected, select] = useState(pathways[0]?.id ?? '');
  const paths = ordered(pathways);
  const path = paths.find((p) => p.id === selected) ?? paths[0];
  const patch = (changes: Partial<ModulePathway>) => onChange(pathways.map((p) => p.id === path.id ? { ...p, ...changes } : p));
  const index = paths.findIndex((p) => p.id === path?.id);
  return <div className="space-y-4">
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-60 text-sm">PCP pathway<select aria-label="PCP pathway" className={inputClass} value={path?.id ?? ''} onChange={(e) => select(e.target.value)}>
        {paths.map((p) => <option key={p.id} value={p.id}>{p.title}{p.active ? '' : ' (Inactive)'}</option>)}
      </select></label>
      <button type="button" className={btnSecondary} onClick={() => {
        const id = `pathway-${crypto.randomUUID()}`;
        onChange([...pathways, { id, title: 'New pathway', subtitle: '', description: '', recognition: '', targetCredits: 0, requiredOptionalModulesCount: 0,
          order: pathways.length, active: true, allowModuleSelection: true, modules: [] }]); select(id);
      }}>Add Pathway</button>
    </div>
    {path && <fieldset aria-label={`Edit pathway ${path.title}`} className="space-y-4 rounded-xl border border-primary-200 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">Pathway name<input className={inputClass} value={path.title} onChange={(e) => patch({ title: e.target.value })} /></label>
        <label className="text-sm">Subtitle<input className={inputClass} value={path.subtitle} onChange={(e) => patch({ subtitle: e.target.value })} /></label>
        <label className="text-sm">Pathway Note<textarea aria-label="Pathway Note" rows={4} className={inputClass} value={path.description} onChange={(e) => patch({ description: e.target.value })} /></label>
        <label className="text-sm">Recognition / accreditation information<textarea className={inputClass} value={path.recognition} onChange={(e) => patch({ recognition: e.target.value })} /></label>
        <label className="text-sm">Target credits<input type="number" min={0} step="any" className={inputClass} value={path.targetCredits ?? ''} onChange={(e) => patch({ targetCredits: e.target.value === '' ? null : e.target.valueAsNumber })} /></label>
        <label className="text-sm">Required Optional Modules Count<input type="number" min={0} step={1} className={inputClass} value={path.requiredOptionalModulesCount ?? ''}
          onChange={(e) => patch({ requiredOptionalModulesCount: e.target.value === '' ? null : e.target.valueAsNumber })} /></label>
        <label className="text-sm">Pathway order<input type="number" min={0} step={1} className={inputClass} value={path.order} onChange={(e) => patch({ order: e.target.valueAsNumber })} /></label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={path.active} onChange={(e) => patch({ active: e.target.checked })} />Active pathway</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={path.allowModuleSelection} onChange={(e) => patch({ allowModuleSelection: e.target.checked })} />Allow module selection</label>
      </div>
      <p className="text-xs text-foreground-500">Stable ID: {path.id}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={btnSecondary} disabled={index === 0} onClick={() => onChange(moveOrdered(pathways, index, -1))}>Move pathway up</button>
        <button type="button" className={btnSecondary} disabled={index === paths.length - 1} onClick={() => onChange(moveOrdered(pathways, index, 1))}>Move pathway down</button>
        <button type="button" className={btnSecondary} onClick={() => patch({ active: !path.active })}>{path.active ? 'Deactivate pathway' : 'Reactivate pathway'}</button>
      </div>
      <ModuleRowsEditor items={path.modules} selectable onChange={(modules) => patch({ modules })} />
    </fieldset>}
  </div>;
}
