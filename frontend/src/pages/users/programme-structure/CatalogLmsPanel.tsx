import { useEffect, useMemo, useState } from 'react';
import { Database, Link2, RefreshCw, X } from 'lucide-react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import { catalogRequest, type KsbMappingWeight, type Programme } from './catalogApi';
import { catalogueFocus, catalogueInput } from './catalogControls';

type Metrics = {
  query_count: number; data_query_count: number; rows_returned: number;
  payload_bytes: number; payload_kb: number; execution_ms: number;
};
type ImportSnapshot = {
  programme: { id: string; name: string; code: string; level: number; import_status: string };
  cohorts: { id: string; name: string; start_date: string; training_end_date: string; import_status: string }[];
  groups: { id: string; name: string; import_status: string }[];
  modules: { id: string; name: string; sessions: number; weekly_topics: unknown[]; ksb_ids: string[];
    ksb_weights: Record<string, KsbMappingWeight>; import_status: string }[];
  ksbs: { id: string; code: string; description: string; import_status: string }[];
  counts: Record<'programmes' | 'cohorts' | 'groups' | 'modules' | 'weekly_topics' | 'ksbs', number>;
};
type ImportPreview = {
  snapshot: ImportSnapshot; preview_fingerprint: string; can_import: boolean; link_schema_ready: boolean;
  conflicts: string[]; missing_fields: string[]; metrics: Metrics; performance_warnings: string[];
};
type Change = {
  key: string; state: 'new' | 'modified' | 'missing_source' | 'conflict';
  entity_type: string; source_id: string;
  fields: { field: string; before: unknown; after: unknown; impact?: 'hard_readiness' | 'source_audit_only' }[];
};
type UpdatePreview = {
  preview_fingerprint: string; has_changes: boolean; changes: Change[];
  snapshot?: { modules: Pick<ImportSnapshot['modules'][number], 'id' | 'ksb_weights'>[];
    ksbs: Pick<ImportSnapshot['ksbs'][number], 'id' | 'code'>[] };
  metrics: Metrics; performance_warnings: string[];
};

const surface = 'rounded-xl border border-background-200 bg-background-50';
const focus = catalogueFocus;
const secondary = `${btnSecondary} ${focus}`;
const initialSource = {
  programme_id: 'PROG-ME-L4', cohort_start_date: '2027-01-01',
  standard_ref: 'ST0596', standard_version: '1.0',
};

function MetricSummary({ metrics }: { metrics: Metrics }) {
  return <dl className="grid grid-cols-2 gap-3 rounded-lg bg-background-100 p-3 text-sm sm:grid-cols-4">
    <div><dt className="text-foreground-600">SQL queries</dt><dd className="font-semibold tabular-nums">{metrics.query_count} ({metrics.data_query_count} data)</dd></div>
    <div><dt className="text-foreground-600">Rows returned</dt><dd className="font-semibold tabular-nums">{metrics.rows_returned}</dd></div>
    <div><dt className="text-foreground-600">Payload</dt><dd className="font-semibold tabular-nums">{metrics.payload_kb} KB</dd></div>
    <div><dt className="text-foreground-600">Read time</dt><dd className="font-semibold tabular-nums">{metrics.execution_ms} ms</dd></div>
  </dl>;
}

function ErrorMessage({ children }: { children: string }) {
  return <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{children}</p>;
}

function State({ value }: { value: string }) {
  const warning = value === 'conflict' || value === 'missing_source';
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${warning
    ? 'bg-amber-100 text-amber-900' : value === 'new' || value === 'modified'
      ? 'bg-primary-100 text-primary-900' : 'bg-background-200 text-foreground-700'}`}>{value.replaceAll('_', ' ')}</span>;
}

function changeValue(value: unknown) {
  if (value == null) return 'Not recorded';
  if (typeof value === 'object' && 'total_weight' in value) {
    const weight = value as KsbMappingWeight;
    return `Effective Hard weight ${weight.hard_weight ?? 'Not recorded'}; Total Mapping Weight ${weight.total_weight}; ${weight.mapping_count} mappings; ` +
      `hard ${weight.hard_count ?? 'Not recorded'} (${weight.hard_weight ?? 'Not recorded'}), soft ${weight.soft_count} (${weight.soft_weight}), ` +
      `possible ${weight.possible_count} (${weight.possible_weight}); ` +
      `main ${weight.main_count} (${weight.main_weight}), secondary ${weight.secondary_count} (${weight.secondary_weight})` +
      (weight.possible_classification_count == null ? '' :
        `, possible classification ${weight.possible_classification_count} (${weight.possible_classification_weight})`);
  }
  return JSON.stringify(value);
}

export function CatalogLmsPanel({ programme, onImported, onUpdated }: {
  programme?: Programme; onImported: (id: string) => void; onUpdated: () => void;
}) {
  const linked = programme?.source_system === 'lms';
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<ImportPreview>();
  const [updates, setUpdates] = useState<UpdatePreview>();
  const [selected, setSelected] = useState<string[]>([]);
  const [archiveSelected, setArchiveSelected] = useState<string[]>([]);
  const [pricing, setPricing] = useState({ total_cost: '', epa_cost: '', funding_band_maximum: '', epao: '' });
  const [moduleCounts, setModuleCounts] = useState<Record<string, { reviews: string; books: string }>>({});
  const [source, setSource] = useState(initialSource);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    if (!open) {
      setError(''); setPreview(undefined); setUpdates(undefined); setSelected([]); setArchiveSelected([]); setAcknowledged(false);
      setModuleCounts({});
    }
  }, [open]);

  const applicable = useMemo(() => updates?.changes.filter(change => change.state === 'new' || change.state === 'modified') ?? [], [updates]);
  const archivable = useMemo(() => updates?.changes.filter(change => change.state === 'missing_source' && change.entity_type !== 'programme') ?? [], [updates]);

  async function run<T>(operation: () => Promise<T>, complete: (value: T) => void) {
    if (busy) return;
    setBusy(true); setError('');
    try { complete(await operation()); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'The LMS operation could not be completed. Try again.'); }
    finally { setBusy(false); }
  }

  function previewImport() {
    void run(() => catalogRequest<ImportPreview>('lms/preview/', { method: 'POST', data: source }), value => {
      setPreview(value); setAcknowledged(false);
      setModuleCounts(current => Object.fromEntries(value.snapshot.modules.map(module => [
        module.id, current[module.id] ?? { reviews: '', books: '' },
      ])));
    });
  }

  function approveImport() {
    if (!preview) return;
    void run(() => catalogRequest<{ programme_id: string }>('lms/import/', {
      method: 'POST', data: {
        ...source, preview_fingerprint: preview.preview_fingerprint, pricing,
        module_counts: Object.fromEntries(preview.snapshot.modules.map(module => [
          module.id, { reviews: Number(moduleCounts[module.id].reviews), books: Number(moduleCounts[module.id].books) },
        ])),
        acknowledge_performance_warnings: acknowledged,
      },
    }), value => { setOpen(false); onImported(value.programme_id); });
  }

  const validCount = (value: string | undefined) => value !== undefined && /^\d{1,5}$/.test(value) && Number(value) <= 10000;
  const countsReady = Boolean(preview?.snapshot.modules.every(module =>
    validCount(moduleCounts[module.id]?.reviews) && validCount(moduleCounts[module.id]?.books)));

  function checkForUpdates() {
    if (!programme) return;
    void run(() => catalogRequest<UpdatePreview>(`programmes/${programme.id}/lms/check/`, { method: 'POST', data: {} }), value => {
      setUpdates(value);
      setSelected([]);
      setArchiveSelected([]);
    });
  }

  function applyChanges() {
    if (!programme || !updates || (!selected.length && !archiveSelected.length)) return;
    void run(() => catalogRequest(`programmes/${programme.id}/lms/apply/`, {
      method: 'POST', data: {
        preview_fingerprint: updates.preview_fingerprint,
        selected_keys: selected, archive_keys: archiveSelected, programme_revision: programme.revision,
      },
    }), () => { setOpen(false); onUpdated(); });
  }

  return <div className="min-w-0">
    <button type="button" className={secondary} aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {linked ? <Link2 size={16} aria-hidden /> : <Database size={16} aria-hidden />}
      {linked ? 'LMS link & updates' : 'Import from LMS'}
    </button>
    {open && <section className={`${surface} mt-3 space-y-4 p-4 sm:p-5`} aria-labelledby="lms-panel-title">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="lms-panel-title" className="text-lg font-semibold">{linked ? 'Linked LMS source' : 'Import from LMS'}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-foreground-700">
            LMS access is read-only. Data is fetched only when you request a preview or check, and Enrolment changes require a separate approval.
          </p>
        </div>
        <button type="button" className={`shrink-0 ${secondary}`} aria-label="Close LMS panel" onClick={() => setOpen(false)}><X size={16} aria-hidden /></button>
      </div>

      {!linked && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="space-y-1 text-sm font-medium">Programme ID<input className={catalogueInput} maxLength={200} value={source.programme_id} onChange={event => setSource(value => ({ ...value, programme_id: event.target.value }))} /></label>
        <label className="space-y-1 text-sm font-medium">Cohorts starting on or after<input className={catalogueInput} type="date" value={source.cohort_start_date} onChange={event => setSource(value => ({ ...value, cohort_start_date: event.target.value }))} /></label>
        <label className="space-y-1 text-sm font-medium">Standard<input className={catalogueInput} maxLength={200} value={source.standard_ref} onChange={event => setSource(value => ({ ...value, standard_ref: event.target.value }))} /></label>
        <label className="space-y-1 text-sm font-medium">Version<input className={catalogueInput} maxLength={200} value={source.standard_version} onChange={event => setSource(value => ({ ...value, standard_version: event.target.value }))} /></label>
      </div>}

      {error && <ErrorMessage>{error}</ErrorMessage>}
      {!linked && !preview && <button type="button" className={`${btnPrimary} ${focus}`} disabled={busy} onClick={previewImport}>
        {busy ? 'Reading selected LMS records…' : 'Preview Import'}
      </button>}
      {linked && !updates && <button type="button" className={`${btnPrimary} ${focus}`} disabled={busy} onClick={checkForUpdates}>
        <RefreshCw size={16} className={busy ? 'animate-spin' : ''} aria-hidden />{busy ? 'Checking linked records…' : 'Check for Updates'}
      </button>}

      {preview && <div className="space-y-4">
        <MetricSummary metrics={preview.metrics} />
        {(preview.performance_warnings.length > 0 || preview.conflicts.length > 0 || preview.missing_fields.length > 0) && <div className="space-y-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-950">
          {[...preview.performance_warnings, ...preview.conflicts, ...preview.missing_fields].map(message => <p key={message}>{message}</p>)}
        </div>}
        <div className="overflow-x-auto rounded-lg border border-background-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-background-100 text-foreground-700"><tr><th className="px-3 py-2">Record type</th><th className="px-3 py-2">Records</th><th className="px-3 py-2">Review</th></tr></thead>
            <tbody className="divide-y divide-background-200">
              <tr><td className="px-3 py-2 font-medium">Programme</td><td className="px-3 py-2 break-words">{preview.snapshot.programme.name}</td><td className="px-3 py-2"><State value={preview.snapshot.programme.import_status} /></td></tr>
              <tr><td className="px-3 py-2 font-medium">Cohorts</td><td className="px-3 py-2">{preview.snapshot.counts.cohorts}</td><td className="px-3 py-2">{preview.snapshot.cohorts.map(row => <span className="me-2 inline-flex" key={row.id}><State value={row.import_status} /></span>)}</td></tr>
              <tr><td className="px-3 py-2 font-medium">Groups</td><td className="px-3 py-2">{preview.snapshot.counts.groups}</td><td className="px-3 py-2">{preview.snapshot.groups.map(row => <span className="me-2 inline-flex" key={row.id}><State value={row.import_status} /></span>)}</td></tr>
              <tr><td className="px-3 py-2 font-medium">Modules / topics</td><td className="px-3 py-2">{preview.snapshot.counts.modules} / {preview.snapshot.counts.weekly_topics}</td><td className="px-3 py-2">{preview.snapshot.modules.map(row => <span className="me-2 inline-flex" key={row.id}><State value={row.import_status} /></span>)}</td></tr>
              <tr><td className="px-3 py-2 font-medium">KSB definitions</td><td className="px-3 py-2">{preview.snapshot.counts.ksbs}</td><td className="px-3 py-2">Mapped in PostgreSQL</td></tr>
            </tbody>
          </table>
        </div>
        <details className="rounded-lg border border-background-200 bg-background-50 p-3 text-sm">
          <summary className={`cursor-pointer font-semibold ${focus}`}>Review module KSB mapping weights</summary>
          <p className="mt-2 text-foreground-600">Source totals are mapping weights, not percentages. Hard weights alone contribute to readiness; Soft and Possible remain for source auditing.</p>
          {preview.snapshot.modules.map(module => <section key={module.id} className="mt-3 border-t border-background-200 pt-3">
            <h4 className="font-semibold">{module.name}</h4>
            <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {Object.entries(module.ksb_weights ?? {}).map(([ksbId, weight]) => <li key={ksbId} className="rounded-md bg-background-100 p-2">
                <strong>{preview.snapshot.ksbs.find(row => row.id === ksbId)?.code ?? ksbId}</strong> ·
                Total Mapping Weight {weight.total_weight} · {weight.mapping_count} mappings
                <span className="block text-xs text-foreground-600">Hard {weight.hard_count} ({weight.hard_weight}) · Soft {weight.soft_count} ({weight.soft_weight}) · Possible {weight.possible_count} ({weight.possible_weight})</span>
                <span className="block text-xs text-foreground-600">Main {weight.main_count} ({weight.main_weight}) · Secondary {weight.secondary_count} ({weight.secondary_weight}){weight.possible_classification_count == null ? '' : ` · Possible classification ${weight.possible_classification_count} (${weight.possible_classification_weight})`}</span>
              </li>)}
            </ul>
          </section>)}
        </details>
        <fieldset className="space-y-3">
          <legend className="font-semibold">Enrolment-owned programme fields</legend>
          <p className="text-sm text-foreground-700">These values are never read from or overwritten by LMS.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm font-medium">Total per apprentice (£)<input className={catalogueInput} inputMode="numeric" pattern="[0-9]*" required value={pricing.total_cost} onChange={event => setPricing(value => ({ ...value, total_cost: event.target.value.replace(/\D/g, '') }))} /></label>
            <label className="space-y-1 text-sm font-medium">EPA cost (£)<input className={catalogueInput} inputMode="numeric" pattern="[0-9]*" required value={pricing.epa_cost} onChange={event => setPricing(value => ({ ...value, epa_cost: event.target.value.replace(/\D/g, '') }))} /></label>
            <label className="space-y-1 text-sm font-medium">Funding band maximum (£)<input className={catalogueInput} inputMode="numeric" pattern="[0-9]*" value={pricing.funding_band_maximum} onChange={event => setPricing(value => ({ ...value, funding_band_maximum: event.target.value.replace(/\D/g, '') }))} /></label>
            <label className="space-y-1 text-sm font-medium">EPA organisation<input className={catalogueInput} maxLength={200} value={pricing.epao} onChange={event => setPricing(value => ({ ...value, epao: event.target.value }))} /></label>
          </div>
        </fieldset>
        <fieldset className="space-y-3">
          <legend className="font-semibold">Enrolment-owned module counts</legend>
          <p className="text-sm text-foreground-700">Enter both counts for each module. Use 0 when none are planned; a blank count is still missing.</p>
          {preview.snapshot.modules.map(module => <div key={module.id} className="rounded-lg border border-background-200 bg-background-50 p-3">
            <p className="font-medium">{module.name}</p>
            <p className="text-xs text-foreground-600">{module.sessions} sessions</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(['reviews', 'books'] as const).map(field => <label key={field} className="space-y-1 text-sm font-medium">
                {field === 'reviews' ? 'Progress Reviews' : 'Books'}
                <input className={catalogueInput} type="number" inputMode="numeric" min="0" max="10000" step="1" required
                  value={moduleCounts[module.id]?.[field] ?? ''} onChange={event => setModuleCounts(current => ({
                    ...current, [module.id]: { ...current[module.id], [field]: event.target.value },
                  }))} />
              </label>)}
            </div>
          </div>)}
        </fieldset>
        {preview.performance_warnings.length > 0 && <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 accent-primary-600" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />I reviewed the LMS query and payload warnings.</label>}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={`${btnPrimary} ${focus}`} disabled={busy || !preview.can_import || !pricing.total_cost || !pricing.epa_cost || !countsReady || (preview.performance_warnings.length > 0 && !acknowledged)} onClick={approveImport}>{busy ? 'Revalidating and importing…' : 'Approve & Import'}</button>
          <button type="button" className={secondary} disabled={busy} onClick={previewImport}>Refresh preview</button>
        </div>
      </div>}

      {updates && <div className="space-y-4">
        <MetricSummary metrics={updates.metrics} />
        {updates.performance_warnings.length > 0 && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950">{updates.performance_warnings.map(item => <p key={item}>{item}</p>)}</div>}
        {!updates.has_changes ? <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">No Changes Found. Linked LMS records match the last approved state.</p> : <fieldset className="space-y-2">
          <legend className="font-semibold">Review Changes</legend>
          {updates.changes.map(change => {
            const selectable = change.state === 'new' || change.state === 'modified';
            const canArchive = change.state === 'missing_source' && change.entity_type !== 'programme';
            const checked = selectable ? selected.includes(change.key) : archiveSelected.includes(change.key);
            const newModule = change.state === 'new' && change.entity_type === 'module_template'
              ? updates.snapshot?.modules.find(module => module.id === change.source_id) : undefined;
            return <label key={change.key} className="flex min-w-0 items-start gap-3 rounded-lg border border-background-200 p-3 text-sm">
              <input type="checkbox" className="mt-1 accent-primary-600" disabled={!selectable && !canArchive} checked={checked} aria-label={canArchive ? `Archive missing ${change.entity_type} ${change.source_id}` : undefined}
                onChange={event => selectable
                  ? setSelected(current => event.target.checked ? [...current, change.key] : current.filter(key => key !== change.key))
                  : setArchiveSelected(current => event.target.checked ? [...current, change.key] : current.filter(key => key !== change.key))} />
              <span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2"><strong className="break-all">{change.entity_type}: {change.source_id}</strong><State value={change.state} /></span>
                {change.fields.length > 0 && <ul className="mt-2 space-y-1 text-foreground-700">{change.fields.map(field => <li className="break-words" key={field.field}><strong>{field.field.replaceAll('_', ' ')}:</strong> {changeValue(field.before)} → {changeValue(field.after)}</li>)}</ul>}
                {change.fields.some(field => field.impact === 'source_audit_only') &&
                  <span className="mt-1 block text-foreground-600">Source audit only: Soft/Possible changes do not change Hard readiness or repin deliveries.</span>}
                {newModule && <div className="mt-2 rounded-lg bg-background-100 p-2">
                  <strong>New module Hard weights ({Object.keys(newModule.ksb_weights).length} KSBs)</strong>
                  <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-foreground-700">{Object.entries(newModule.ksb_weights).map(([ksbId, weight]) =>
                    <li key={ksbId}>{updates.snapshot?.ksbs.find(ksb => ksb.id === ksbId)?.code ?? ksbId}: Hard {weight.hard_weight} ({weight.hard_count} mappings); Soft {weight.soft_weight}; Possible {weight.possible_weight}</li>)}</ul>
                </div>}
                {canArchive && <span className="mt-1 block text-amber-900">Optional explicit archive. The record is never deleted automatically.</span>}
                {!selectable && !canArchive && <span className="mt-1 block text-amber-900">Manual review required. This record will not be deleted or overwritten.</span>}
              </span>
            </label>;
          })}
        </fieldset>}
        <div className="flex flex-wrap gap-2">
          {(applicable.length > 0 || archivable.length > 0) && <button type="button" className={`${btnPrimary} ${focus}`} disabled={busy || (!selected.length && !archiveSelected.length)} onClick={applyChanges}>{busy ? 'Revalidating and applying…' : 'Approve & Apply Changes'}</button>}
          <button type="button" className={secondary} disabled={busy} onClick={checkForUpdates}>Check again</button>
          <button type="button" className={secondary} disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </div>}
    </section>}
  </div>;
}
