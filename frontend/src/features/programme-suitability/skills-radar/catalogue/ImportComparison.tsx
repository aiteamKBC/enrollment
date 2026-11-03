import { useState } from 'react';
import type { CatalogueRecord, ChangeKind, Comparison } from './types';

const LABELS: Record<ChangeKind, string> = { unchanged: 'Unchanged', added: 'Added', removed: 'Removed', descriptions: 'Descriptions', categories: 'Categories', mappings: 'Mappings' };
const mapping = (r: CatalogueRecord | null) => r?.owners ? Object.entries(r.owners).map(([path, key]) => `${path}: ${key}`).join(' · ') : r?.moduleKey || '—';

export function ImportComparison({ value }: { value: Comparison }) {
  const [filter, setFilter] = useState<ChangeKind | 'all'>('all');
  const rows = value.changes.filter((r) => filter === 'all' || r.changes.includes(filter));
  return <div className="space-y-3">
    <p className="font-medium">Current KSBs: {value.currentTotal} · Imported total: {value.newTotal}</p>
    <div role="group" aria-label="Filter CSV changes" className="flex flex-wrap gap-2">
      <button type="button" aria-pressed={filter === 'all'} className="rounded border px-2 py-1 text-xs" onClick={() => setFilter('all')}>All changes</button>
      {(Object.keys(LABELS) as ChangeKind[]).map((key) => <button type="button" key={key} aria-pressed={filter === key}
        className={`rounded border px-2 py-1 text-xs ${filter === key ? 'border-primary-400 bg-primary-50' : ''}`} onClick={() => setFilter(key)}>{LABELS[key]}: {value[key]}</button>)}
    </div>
    {value.largeRemoval && <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">Warning: at least 25% of the published catalogue is missing. Check that this is a complete CSV, not a partial export.</p>}
    {value.removedCodes.length > 0 && <p className="text-sm text-red-800">Removed codes ({value.removedCodes.length}): {value.removedCodes.join(', ')}</p>}
    {value.categoryCodes.length > 0 && <p role="alert" className="text-sm text-amber-800">Category changes need review: {value.categoryCodes.join(', ')}. A category change keeps the same question identity.</p>}
    <div className="max-h-[32rem] overflow-auto rounded-lg border">
      <table className="w-full text-left text-xs"><thead className="sticky top-0 bg-background-100"><tr><th className="p-2">KSB / change</th><th className="p-2">Published</th><th className="p-2">Imported</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.code} className="border-t align-top"><th className="p-2">{row.code}<span className="block font-normal">{row.changes.map((key) => LABELS[key]).join(', ')}</span></th>
          {[row.before, row.after].map((record, i) => <td key={i} className="min-w-48 whitespace-pre-wrap p-2 [overflow-wrap:anywhere]">{record ? <><strong>{record.kind}</strong><p>{record.description}</p><p className="mt-2 text-primary-800">{mapping(record)}</p></> : '—'}</td>)}
        </tr>)}</tbody></table>
      {!rows.length && <p className="p-3 text-xs">No changes in this category.</p>}
    </div>
  </div>;
}

export function CurrentMapping({ catalogue }: { catalogue: { records: CatalogueRecord[] } }) {
  return <div className="max-h-96 overflow-auto rounded border"><table className="w-full text-left text-xs"><thead><tr><th className="p-2">Code</th><th>Category / full description</th><th>Module / original owners</th></tr></thead><tbody>
    {catalogue.records.map((r) => <tr key={r.code} className="border-t align-top"><th className="p-2">{r.code}</th><td className="whitespace-pre-wrap p-2"><strong>{r.kind}</strong><p>{r.description}</p></td><td className="p-2">{mapping(r)}</td></tr>)}
  </tbody></table></div>;
}
