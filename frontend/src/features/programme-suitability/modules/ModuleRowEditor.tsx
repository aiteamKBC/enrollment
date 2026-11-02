import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import { ordered, isModuleGroup, type ProgrammeModule } from './configuration';
import { newModule, moveOrdered, asModuleGroup, moduleGroups, assignModuleParent } from './editorOps';

type ModuleHierarchy = { groups: ProgrammeModule[]; parentId: string; assignParent: (moduleId: string, parentId: string) => void };

export function ModuleRowsEditor({ items, onChange, selectable = false, hierarchy }: {
  items: ProgrammeModule[]; onChange: (items: ProgrammeModule[]) => void; selectable?: boolean;
  hierarchy?: ModuleHierarchy;
}) {
  const tree = hierarchy ?? { groups: moduleGroups(items), parentId: '',
    assignParent: (moduleId: string, parentId: string) => onChange(assignModuleParent(items, moduleId, parentId)) };
  return <div className="space-y-3">
    {ordered(items).map((module, index) => {
      const group = isModuleGroup(module);
      const replace = (value: ProgrammeModule) => onChange(items.map((item) => item.id === module.id ? value : item));
      const patch = (changes: Partial<ProgrammeModule>) => {
        const value = { ...module, ...changes };
        replace(group ? asModuleGroup(value, value.items ?? []) : value);
      };
      return <details key={module.id} open className="rounded-xl border border-foreground-200 p-3">
        <summary className="cursor-pointer text-sm font-semibold">{module.title || 'Unnamed module'}{group ? ' (Module Group)' : ''}{!module.active ? ' (Inactive)' : ''}</summary>
        <fieldset aria-label={`Edit ${group ? 'group' : 'module'} ${module.title}`} className="mt-3 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-sm sm:col-span-2">{group ? 'Group name' : 'Module name'}<input className={inputClass} value={module.title} onChange={(e) => patch({ title: e.target.value })} /></label>
            {!group && <><label className="text-sm">Sessions<input type="number" min={0} step={1} className={inputClass} value={module.sessions ?? ''}
              onChange={(e) => patch({ sessions: e.target.value === '' ? null : e.target.valueAsNumber })} /></label>
            <label className="text-sm">Credits<input type="number" min={0} step="any" className={inputClass} value={module.credits ?? ''}
              onChange={(e) => patch({ credits: e.target.value === '' ? undefined : e.target.valueAsNumber })} /></label></>}
            <label className="text-sm">Display order<input type="number" min={0} step={1} className={inputClass} value={module.order} onChange={(e) => patch({ order: e.target.valueAsNumber })} /></label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={module.active} onChange={(e) => patch({ active: e.target.checked })} />Active {group ? 'group' : 'module'}</label>
            {!group && selectable && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!module.selectable} onChange={(e) => patch({ selectable: e.target.checked, ...(!e.target.checked ? { isMandatory: false } : {}) })} />Learner-selectable</label>}
            {!group && selectable && module.selectable && <label className="text-sm">Mandatory Module<select aria-label="Mandatory Module" className={inputClass} value={module.isMandatory ? 'yes' : 'no'} onChange={(e) => patch({ isMandatory: e.target.value === 'yes' })}>
              <option value="no">No</option><option value="yes">Yes</option></select></label>}
            {!group && selectable && tree.groups.length > 0 && <label className="text-sm">Parent group<select className={inputClass} value={tree.parentId} onChange={(e) => tree.assignParent(module.id, e.target.value)}>
              <option value="">None (pathway level)</option>
              {tree.groups.map((parent) => <option key={parent.id} value={parent.id}>{parent.title}{parent.active ? '' : ' (Inactive)'}</option>)}
            </select></label>}
          </div>
          <label className="block text-sm">Description<textarea rows={2} className={inputClass} value={module.description} onChange={(e) => patch({ description: e.target.value })} /></label>
          <p className="text-xs text-foreground-500">Stable ID: {module.id}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={btnSecondary} disabled={index === 0} onClick={() => onChange(moveOrdered(items, index, -1))}>Move {group ? 'group' : 'module'} up</button>
            <button type="button" className={btnSecondary} disabled={index === items.length - 1} onClick={() => onChange(moveOrdered(items, index, 1))}>Move {group ? 'group' : 'module'} down</button>
            <button type="button" className={btnSecondary} onClick={() => patch({ active: !module.active })}>{module.active ? 'Deactivate' : 'Reactivate'} {group ? 'group' : 'module'}</button>
            <button type="button" className={btnSecondary} onClick={() => replace(asModuleGroup(module, [...(module.items ?? []), newModule(module.items?.length ?? 0)]))}>{group ? 'Add child module' : 'Add submodule'}</button>
          </div>
          {module.items && <div className="border-l-2 border-primary-200 pl-3"><ModuleRowsEditor items={module.items} selectable={selectable} hierarchy={{ ...tree, parentId: module.id }} onChange={(children) => patch({ items: children })} /></div>}
        </fieldset>
      </details>;
    })}
    <button type="button" className={btnSecondary} onClick={() => onChange([...items, newModule(items.length)])}>Add Module</button>
  </div>;
}
