import { useId, useMemo, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import type { Ksb } from './catalogApi';
import { catalogueFocus, catalogueInput } from './catalogControls';

export function CatalogKsbPicker({ rows, initialIds, selectedIds, onChange }: {
  rows: Ksb[];
  initialIds: string[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const [search, setSearch] = useState('');
  const searchInput = useRef<HTMLInputElement>(null);
  const listId = useId();
  const available = useMemo(() => {
    const initial = new Set(initialIds);
    return rows.filter(ksb => !ksb.archived_at || initial.has(ksb.id));
  }, [rows, initialIds]);
  const selected = new Set(selectedIds);
  const query = search.trim().toLowerCase();
  const matching = available.filter(ksb => `${ksb.code} ${ksb.description}`.toLowerCase().includes(query));
  const chips = available.filter(ksb => selected.has(ksb.id));

  function remove(id: string) {
    onChange(selectedIds.filter(value => value !== id));
    searchInput.current?.focus();
  }

  if (!available.length) return <p className="text-sm text-foreground-600">
    The programme has no available KSBs. Add KSBs in the KSB Library before linking them to a module.
  </p>;

  return <div className="space-y-3">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <label className="block w-full max-w-sm space-y-1 text-sm font-medium">Search KSBs
        <span className="relative block">
          <Search size={16} className="pointer-events-none absolute left-3 top-3 text-foreground-600" aria-hidden />
          <input ref={searchInput} className={`${catalogueInput} pl-9`} type="search" value={search}
            aria-controls={listId} onChange={event => setSearch(event.currentTarget.value)} />
        </span>
      </label>
      <span role="status" className="pb-2 text-sm font-medium text-foreground-700">{selectedIds.length} selected</span>
    </div>
    {chips.length > 0 && <ul aria-label="Selected KSBs" className="flex flex-wrap gap-2">
      {chips.map(ksb => <li key={ksb.id} className="min-w-0">
        <button type="button" aria-label={`Remove ${ksb.code} from selection`}
          className={`inline-flex min-h-9 max-w-full items-center gap-1 rounded-lg bg-primary-100 px-2.5 text-xs font-semibold text-primary-800 hover:bg-primary-200 ${catalogueFocus}`}
          onClick={() => remove(ksb.id)}>
          <span className="min-w-0 break-words">{ksb.code}</span><X size={13} className="shrink-0" aria-hidden />
        </button>
      </li>)}
    </ul>}
    <div id={listId} className="grid max-h-72 gap-2 overflow-y-auto rounded-lg p-1 sm:grid-cols-2" role="group" aria-label="Module KSB selection">
      {matching.map(ksb => <label key={ksb.id}
        className={`flex min-h-11 cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm focus-within:ring-2 focus-within:ring-primary-500 ${selected.has(ksb.id) ? 'border-primary-300 bg-primary-50' : 'border-background-200 hover:bg-background-100'}`}>
        <input type="checkbox" className="mt-0.5 size-4 shrink-0 accent-primary-600" name="ksbs" value={ksb.id}
          checked={selected.has(ksb.id)} onChange={event => onChange(event.currentTarget.checked
            ? [...selectedIds, ksb.id] : selectedIds.filter(id => id !== ksb.id))} />
        <span className="min-w-0 break-words"><strong translate="no">{ksb.code}</strong>{ksb.archived_at && ' (archived)'} — {ksb.description}</span>
      </label>)}
    </div>
    {!matching.length && <p role="status" className="text-sm text-foreground-600">No KSBs match this search.</p>}
  </div>;
}
