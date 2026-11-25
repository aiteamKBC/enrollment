import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, Download, Minus, Pencil, Search } from 'lucide-react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import type { CurriculumModule, Ksb } from './catalogApi';
import { catalogueFocus, catalogueInput } from './catalogControls';
import { CatalogKsbPicker } from './CatalogKsbPicker';
import { discardMessage } from './catalogNavigation';
import { isEditableParent } from './CatalogParents';
import { downloadKsbCsv, makeKsbMappingCsv } from './ksbCsv';
import styles from './CatalogKsbMapping.module.css';

const filters = [['all', 'All KSBs'], ['assigned', 'Assigned KSBs'], ['unassigned', 'Unassigned KSBs']] as const;
const action = `${btnSecondary} ${catalogueFocus} disabled:opacity-50`;
const surface = 'min-w-0 rounded-xl border border-background-200 bg-background-50';

type AssignmentActions = {
  busy: boolean;
  error: string;
  onSave: (module: CurriculumModule, ids: string[]) => void;
  onCancel: () => void;
  onReload?: () => void;
  onDirtyChange: (dirty: boolean) => void;
};

function AssignmentEditor({ modules, ksbs, busy, error, onSave, onCancel, onReload, onDirtyChange }: AssignmentActions & {
  modules: CurriculumModule[];
  ksbs: Ksb[];
}) {
  // Capture the revision and links shown on selection, even if detail refreshes while editing.
  const [module, setModule] = useState(() => modules[0]);
  const [ids, setIds] = useState(() => module?.ksb_ids ?? []);
  const moduleId = useId();
  const moduleControl = useRef<HTMLSelectElement>(null);
  const errorControl = useRef<HTMLParagraphElement>(null);
  const original = new Set(module?.ksb_ids);
  const dirty = ids.length !== original.size || ids.some(id => !original.has(id));

  useEffect(() => { moduleControl.current?.focus(); }, []);
  useEffect(() => { if (error) errorControl.current?.focus(); }, [error]);
  useLayoutEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);

  return <form aria-label="Edit assignments" className={`${surface} space-y-3 p-4 sm:p-5`} onSubmit={event => {
    event.preventDefault();
    if (module && dirty && !busy && !onReload) onSave(module, ids);
  }}>
    <h3 className="font-semibold">Edit assignments</h3>
    <p className="text-sm text-foreground-600">Saving creates a module revision. Scheduled deliveries keep their saved coverage.</p>
    {error && <p ref={errorControl} role="alert" tabIndex={-1}
      className={`whitespace-pre-line rounded-lg bg-red-50 p-3 text-sm text-red-800 ${catalogueFocus}`}>{error}</p>}
    <fieldset disabled={busy || Boolean(onReload)} className="min-w-0 space-y-4">
      <div className="max-w-md space-y-1">
        <label htmlFor={moduleId} className="block text-sm font-medium">Module</label>
        <select id={moduleId} ref={moduleControl} value={module?.id ?? ''} className={catalogueInput} onChange={event => {
          const next = modules.find(row => row.id === event.currentTarget.value);
          if (!next || next.id === module?.id || (dirty && !window.confirm(discardMessage))) return;
          setModule(next);
          setIds(next.ksb_ids);
        }}>
          {modules.map(row => <option key={row.id} value={row.id}>
            {row.name}{row.current_revision ? ` · v${row.current_revision.number}` : ''}
          </option>)}
        </select>
      </div>
      {module && <CatalogKsbPicker key={module.id} rows={ksbs} initialIds={module.ksb_ids} selectedIds={ids} onChange={setIds} />}
    </fieldset>
    <div className="flex flex-wrap items-center justify-end gap-2">
      {dirty && <span className="mr-auto text-sm text-foreground-600">Unsaved changes</span>}
      <button type="button" className={action} disabled={busy} onClick={onCancel}>Cancel</button>
      {onReload ? <button type="button" className={action} onClick={onReload}>Reload latest</button>
        : <button type="submit" className={`${btnPrimary} ${catalogueFocus}`} disabled={busy || !dirty || !module}>
          {busy ? 'Saving…' : 'Save assignments'}
        </button>}
    </div>
  </form>;
}

export function CatalogKsbMapping({ programmeCode, modules, ksbs, allKsbs, editable, assignments, onEdit }: {
  programmeCode: string;
  modules: CurriculumModule[];
  ksbs: Ksb[];
  allKsbs: Ksb[];
  editable: boolean;
  assignments?: AssignmentActions;
  onEdit: () => void;
}) {
  const [filter, setFilter] = useState<typeof filters[number][0]>('all');
  const [search, setSearch] = useState('');
  const moduleLinks = useMemo(() => modules.map(module => new Set(module.ksb_ids)), [modules]);
  const assigned = useMemo(() => new Set(modules.flatMap(module => module.ksb_ids)), [modules]);
  const query = search.trim().toLowerCase();
  const matching = ksbs.filter(ksb => `${ksb.code} ${ksb.description}`.toLowerCase().includes(query) &&
    (filter === 'all' || assigned.has(ksb.id) === (filter === 'assigned')));
  const assignedCount = ksbs.filter(ksb => assigned.has(ksb.id)).length;
  const editableModules = editable ? modules.filter(isEditableParent) : [];
  const hasCoverage = modules.length > 0 && ksbs.length > 0;
  const heading = 'sticky top-0 bg-background-100 text-xs font-semibold text-foreground-700';
  const cell = 'border-b border-r border-background-200 px-3 py-2.5';

  return <div className="min-w-0 space-y-4">
    <section className={`${surface} space-y-4 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">KSB mapping</h3>
          <p className="mt-1 text-sm text-foreground-600">Coverage across the latest module template revisions.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={action} disabled={!hasCoverage || !matching.length}
            onClick={() => downloadKsbCsv(programmeCode, 'mapping', makeKsbMappingCsv(matching, modules))}>
            <Download size={16} aria-hidden /> Export CSV
          </button>
          <button type="button" className={`${btnPrimary} ${catalogueFocus}`} onClick={onEdit}
            disabled={Boolean(assignments) || !editableModules.length || !ksbs.length}>
            <Pencil size={16} aria-hidden /> Edit assignments
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="block w-full max-w-md space-y-1 text-sm font-medium">Search KSB code or description
          <span className="relative block">
            <Search size={16} aria-hidden className="pointer-events-none absolute left-3 top-3 text-foreground-600" />
            <input type="search" className={`${catalogueInput} pl-9`} value={search} onChange={event => setSearch(event.currentTarget.value)} />
          </span>
        </label>
        <div role="group" aria-label="Filter KSB coverage" className="flex flex-wrap gap-1">
          {filters.map(([id, label]) => <button key={id} type="button" onClick={() => setFilter(id)} aria-pressed={filter === id}
            className={`min-h-10 rounded-lg border px-3 text-sm font-medium ${catalogueFocus} ${filter === id
              ? 'border-primary-200 bg-primary-50 text-primary-800' : 'border-transparent text-foreground-700 hover:bg-background-100'}`}>{label}</button>)}
        </div>
      </div>
    </section>
    {assignments && <AssignmentEditor {...assignments} modules={editableModules} ksbs={allKsbs} />}
    <section className={`${surface} overflow-hidden`} aria-label="Module coverage">
      {!hasCoverage || !matching.length ? <p role="status" className="p-4 text-sm text-foreground-600">
        {!ksbs.length ? 'Add KSBs in the KSB Library to start mapping coverage.'
          : !modules.length ? 'Add a module template to start mapping coverage.' : 'No KSBs match your search or filter.'}
      </p> : <div role="region" aria-label="Scrollable KSB mapping matrix" tabIndex={0}
        className={`${styles.scroller} max-h-[65vh] w-full min-w-0 overflow-auto ${catalogueFocus}`}>
        <table className={`${styles.matrix} w-full table-fixed border-separate border-spacing-0 text-left text-sm`} aria-label="KSB mapping matrix"
          style={{ minWidth: 328 + modules.length * 210 }}>
          <colgroup><col className="w-[4.5rem]" /><col className="w-40 sm:w-64" /><col span={modules.length} /></colgroup>
          <thead>
            <tr>
              <th scope="col" className={`${heading} ${cell} left-0 z-30`}>KSB code</th>
              <th scope="col" className={`${heading} ${cell} z-20 sm:left-[4.5rem] sm:z-30`}>KSB description</th>
              {modules.map(module => <th key={module.id} scope="col" className={`${heading} ${cell} z-20 text-center`}>
                <span className="block break-words text-sm text-foreground-900">{module.name}</span>
                <span className="block font-normal">{module.current_revision ? `v${module.current_revision.number}` : 'No revision'}</span>
                <span className="block font-normal">{module.lectures == null ? 'Sessions not set'
                  : `${module.lectures} ${module.lectures === 1 ? 'session' : 'sessions'}`}</span>
                {module.archived_at && <span className="block font-normal">Archived</span>}
              </th>)}
            </tr>
          </thead>
          <tbody>
            {matching.map(ksb => <tr key={ksb.id}>
              <th scope="row" className={`${cell} sticky left-0 z-10 break-words bg-background-50 font-semibold text-primary-800`}>
                {ksb.code}
              </th>
              <td className={`${cell} break-words bg-background-50 sm:sticky sm:left-[4.5rem] sm:z-10`}>
                {ksb.description}{ksb.archived_at && <span className="ml-1 text-xs text-foreground-600">(Archived)</span>}
              </td>
              {moduleLinks.map((ids, index) => {
                const covered = ids.has(ksb.id);
                const module = modules[index];
                const weight = module.ksb_weights?.[ksb.id];
                return <td key={modules[index].id} className={`${cell} text-center ${covered ? styles.assigned : 'text-foreground-600'}`}>
                  {covered && weight ? <div className="space-y-1 text-xs" aria-label={`${module.name} ${ksb.code} mapping weight`}>
                    <strong className="block text-sm tabular-nums">{weight.total_weight}</strong>
                    <span className="block">Total Mapping Weight · {weight.mapping_count} mappings</span>
                    <span className="block tabular-nums">Hard {weight.hard_count} ({weight.hard_weight}) · Soft {weight.soft_count} ({weight.soft_weight})</span>
                    {weight.possible_count > 0 && <span className="block tabular-nums">Possible {weight.possible_count} ({weight.possible_weight})</span>}
                    <details className="text-left"><summary className={`cursor-pointer ${catalogueFocus}`}>Classification</summary>
                      <span className="block tabular-nums">Main {weight.main_count} ({weight.main_weight})</span>
                      <span className="block tabular-nums">Secondary {weight.secondary_count} ({weight.secondary_weight})</span>
                    </details>
                  </div> : covered ? <>
                    <Check size={18} className="inline-block" aria-hidden />
                    <span className="sr-only">Assigned</span>
                    {module.source_system === 'lms' && <span className="block text-xs">Weight not recorded</span>}
                  </> : <><Minus size={15} className="inline-block" aria-hidden /><span className="sr-only">Unassigned</span></>}
                </td>;
              })}
            </tr>)}
          </tbody>
        </table>
      </div>}
      <dl aria-label="KSB coverage totals" className="grid grid-cols-3 gap-3 px-3 py-3 text-sm sm:px-4">
        {([['Total KSBs', ksbs.length], ['Assigned KSBs', assignedCount], ['Unassigned KSBs', ksbs.length - assignedCount]] as const).map(([label, count]) =>
          <div key={label}><dt className="text-xs text-foreground-600">{label}</dt><dd className="mt-0.5 font-semibold tabular-nums">{count}</dd></div>)}
      </dl>
    </section>
  </div>;
}
