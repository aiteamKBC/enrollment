import { useEffect, useState } from 'react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import type { WizardLayout } from '@/pages/users/wizard/layout/types';
import type { ProgrammeId } from '../../types';
import { csvUrl, getCatalogues, getCatalogueDraft, saveCatalogueDraft, uploadCatalogue } from './api';
import type { CatalogueDraft, CatalogueIndex, ImportPreview } from './types';
import { CataloguePreview } from './CataloguePreview';
import { CurrentMapping, ImportComparison } from './ImportComparison';

const PROGRAMMES: ProgrammeId[] = ['PCP_L6', 'MM_L6', 'ME_L4', 'APM_L4'];
const TITLES = { PCP_L6: 'Project Controls Professional L6', MM_L6: 'Marketing Manager L6', ME_L4: 'Marketing Executive L4', APM_L4: 'Associate Project Manager L4' };

export function CatalogueAdmin({ layout, version, onChange }: { layout: WizardLayout; version: number | null; onChange: (draft: CatalogueDraft) => void }) {
  const [index, setIndex] = useState<CatalogueIndex | null>(null);
  const [saved, setSaved] = useState<{ configuration: CatalogueDraft | null; baseVersion: number | null } | null>(null);
  const [selected, select] = useState<ProgrammeId>('PCP_L6');
  const [pending, setPending] = useState<ImportPreview | null>(null);
  const [current, showCurrent] = useState<ProgrammeId | null>(null);
  const [preview, showPreview] = useState(false);
  const [snapshot, confirmSnapshot] = useState(false);
  const [removals, confirmRemovals] = useState(false);
  const [categories, confirmCategories] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [rowErrors, setRowErrors] = useState<{ row?: number; code?: string; message: string }[]>([]);
  const [message, setMessage] = useState('');
  const [reload, retry] = useState(0);
  useEffect(() => {
    let active = true;
    setPending(null); showPreview(false); setError('');
    getCatalogues().then((value) => { if (active) setIndex(value); }).catch((e: Error) => { if (active) setError(e.message); });
    getCatalogueDraft().then((value) => { if (active) setSaved(value); }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [version, reload]);
  const catalogue = (layout.skillsRadarCatalogues || index?.catalogues)?.[selected];
  const valid = snapshot && (!pending?.comparison.removed || removals) && (!pending?.comparison.categories || categories);
  const upload = async (pid: ProgrammeId, file?: File) => {
    if (!file) return;
    select(pid); setPending(null); setError(''); setRowErrors([]); setMessage(''); showPreview(false);
    confirmSnapshot(false); confirmRemovals(false); confirmCategories(false); setBusy(true);
    try {
      const result = await uploadCatalogue(pid, file);
      if (result.baseVersion !== version) throw new Error('The wizard was published elsewhere. Reload the builder before importing.');
      setPending(result);
    } catch (e) { setError(e instanceof Error ? e.message : 'CSV import failed.'); setRowErrors((e as { errors?: typeof rowErrors }).errors || []); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!pending || !index || !valid) return;
    const pid = pending.catalogue.programmeId;
    const unchanged = pending.catalogue.version === index.catalogues[pid].version;
    if (unchanged && (!layout.skillsRadarCatalogues || layout.skillsRadarCatalogues[pid].version === index.catalogues[pid].version)) {
      setPending(null); setMessage('The CSV matches the published catalogue exactly. No records or mappings were changed.'); return;
    }
    setBusy(true); setError('');
    const configuration: CatalogueDraft = { skillsRadarCatalogues: { ...(layout.skillsRadarCatalogues || index.catalogues), [pid]: pending.catalogue },
      skillsRadarImportApprovals: { ...layout.skillsRadarImportApprovals, [pid]: { againstVersion: index.catalogues[pid].version, completeSnapshot: true,
        removedCodes: pending.comparison.removedCodes, categoryCodes: pending.comparison.categoryCodes } } };
    if (unchanged) delete configuration.skillsRadarImportApprovals[pid];
    try {
      const result = await saveCatalogueDraft(configuration, version);
      setSaved(result); onChange(result.configuration); select(pid); setPending(null); showPreview(true);
      setMessage('Skills Radar draft saved. Preview it below, then use Publish changes at the top of the wizard. Learners still see the published version.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save draft.'); }
    finally { setBusy(false); }
  };
  return <section aria-label="Skills Radar catalogue configuration" className="m-5 min-w-0 space-y-4 rounded-xl border border-primary-200 bg-background-50 p-4">
    <h3 className="text-lg font-semibold">Skills Radar catalogues &amp; module mapping</h3>
    <p className="text-sm text-foreground-600">Upload a complete UTF-8 CSV for one programme. Review all differences, save a private draft and preview it before using the wizard’s Publish changes button. Assessments already started keep their saved version and answers.</p>
    <p className="text-xs text-foreground-600">Download exports include a programme metadata row. Keep it for exact reimport, including Excel-safe text escaping. Hand-written CSVs can start with the required header. Maximum file size: 1 MB.</p>
    {error && <div role="alert" className="space-y-2 text-sm text-red-700"><p>{error}</p>{rowErrors.length > 0 && <ul className="max-h-64 overflow-auto list-inside list-disc">{rowErrors.map((e, i) => <li key={i}>Row {e.row ?? '?'}{e.code ? ` (${e.code})` : ''}: {e.message}</li>)}</ul>}<button type="button" className={btnSecondary} onClick={() => retry((n) => n + 1)}>Reload catalogue data</button></div>}
    {message && <p role="status" className="text-sm text-primary-800">{message}</p>}
    {!index && !error && <p role="status">Loading published Skills Radar catalogues…</p>}
    {index && <>
      {saved?.configuration && <div className="rounded border p-3 text-sm"><p>Saved Skills Radar draft available.{saved.baseVersion !== version ? ' This draft is stale. Reimport against the latest publication to review current changes.' : ''}</p>
        <button type="button" className={`${btnSecondary} mt-2`} disabled={busy || saved.baseVersion !== version} onClick={() => { if (saved.configuration) { onChange(structuredClone(saved.configuration)); setPending(null); setMessage('Saved Skills Radar draft restored for review.'); showPreview(true); } }}>Load saved Skills Radar draft</button></div>}
      <div className="grid gap-3 md:grid-cols-2">{PROGRAMMES.map((pid) => {
        const published = index.catalogues[pid];
        return <article key={pid} aria-label={TITLES[pid]} className={`space-y-2 rounded-xl border p-3 ${selected === pid ? 'border-primary-400 bg-primary-50/40' : 'border-foreground-200'}`}>
          <h4 className="font-semibold">{TITLES[pid]}</h4>
          <p className="text-sm">{published.records.length} published KSBs</p>
          <p className="break-all text-xs">Mapping version: {published.version.slice(0, 12)}<br />Last published: {published.publishedAt ? new Date(published.publishedAt).toLocaleString('en-GB') : 'Initial catalogue'}</p>
          <div className="flex flex-wrap gap-2"><a className={btnSecondary} href={csvUrl(pid)} download>Download Current Mapping CSV</a>
            <label className={`${btnSecondary} ${busy ? 'opacity-50' : 'cursor-pointer'}`}>Upload CSV<input aria-label={`Upload CSV for ${TITLES[pid]}`} type="file" accept=".csv,text/csv" className="sr-only" disabled={busy} onChange={(e) => { void upload(pid, e.target.files?.[0]); e.target.value = ''; }} /></label>
            <button type="button" className={btnSecondary} disabled={busy} onClick={() => { select(pid); showCurrent((old) => old === pid ? null : pid); }}>View Current Mapping</button>
            <a className={btnSecondary} href={csvUrl(pid, true)} download>Blank CSV template</a>
            <button type="button" className={btnSecondary} disabled={busy} onClick={() => { select(pid); showPreview(true); }}>Preview mapping</button>
          </div>
        </article>;
      })}</div>
      {current && <div role="region" aria-label="Current published KSB mapping"><h4 className="mb-2 font-semibold">{TITLES[current]} — current published mapping</h4><CurrentMapping catalogue={index.catalogues[current]} /></div>}
      {pending && <div role="region" aria-label="CSV import comparison" className="space-y-4 rounded-xl border p-3">
        <h4 className="font-semibold">{TITLES[pending.catalogue.programmeId]} — complete snapshot comparison</h4><ImportComparison key={pending.catalogue.version} value={pending.comparison} />
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={snapshot} onChange={(e) => confirmSnapshot(e.target.checked)} />I confirm this CSV is the complete desired catalogue for this programme, not a partial update.</label>
        {pending.comparison.removed > 0 && <label className="flex items-start gap-2 text-sm text-red-800"><input type="checkbox" checked={removals} onChange={(e) => confirmRemovals(e.target.checked)} />I confirm removal of all listed codes from future assessments: {pending.comparison.removedCodes.join(', ')}. Historical answers remain available.</label>}
        {pending.comparison.categories > 0 && <label className="flex items-start gap-2 text-sm text-amber-800"><input type="checkbox" checked={categories} onChange={(e) => confirmCategories(e.target.checked)} />I have reviewed the old and new categories for: {pending.comparison.categoryCodes.join(', ')}.</label>}
        <div className="flex gap-2"><button type="button" className={btnPrimary} disabled={busy || !valid} onClick={() => void save()}>{busy ? 'Saving draft…' : 'Save Skills Radar Draft'}</button>
          <button type="button" className={btnSecondary} disabled={busy} onClick={() => { setPending(null); setError(''); }}>Cancel import</button></div>
      </div>}
      {preview && catalogue && <><h4 className="font-semibold">{TITLES[selected]} — mapping preview</h4><CataloguePreview key={selected} catalogue={catalogue} configuration={layout.programmeModules || index.modules} version={version} /></>}
    </>}
  </section>;
}
