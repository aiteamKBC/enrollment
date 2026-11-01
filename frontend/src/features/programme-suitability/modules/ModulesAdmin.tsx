import { useEffect, useState } from 'react';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import type { ProgrammeId } from '../types';
import { configurationErrors, type ModulesConfiguration, type ProgrammeModuleSelection, type ProgrammeModules } from './configuration';
import { getModuleDefaults, getModulesDraft, saveModulesDraft } from './api';
import { ModuleRowsEditor } from './ModuleRowEditor';
import { PathwayEditor } from './PathwayEditor';
import { ProgrammeModulesView } from './ProgrammeModulesCard';
import { applyPcpDefaults } from './upgrade';

export function ModulesAdmin({ value, version, onChange }: {
  value?: ModulesConfiguration; version: number | null; onChange: (config: ModulesConfiguration) => void;
}) {
  const [seed, setSeed] = useState<ModulesConfiguration | null>(null);
  const [draft, setDraft] = useState<{ configuration: ModulesConfiguration; baseVersion: number | null } | null>(null);
  const [selected, select] = useState<ProgrammeId>('ME_L4');
  const [preview, showPreview] = useState(false);
  const [testSelection, testSelect] = useState<ProgrammeModuleSelection>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const needsDefaults = value === undefined || value.schemaVersion === 1;
  useEffect(() => {
    let active = true;
    if (needsDefaults) getModuleDefaults().then((data) => { if (active) setSeed(data.configuration); }).catch((e: Error) => { if (active) setError(e.message); });
    getModulesDraft().then((data) => { if (active && data.configuration) setDraft({ configuration: data.configuration, baseVersion: data.baseVersion }); })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [needsDefaults]);
  const config = value ?? seed;
  const programme = config?.programmes.find((p) => p.id === selected);
  const errors = config ? configurationErrors(config) : [];
  const patch = (changes: Partial<ProgrammeModules>) => {
    if (config) onChange({ ...config, programmes: config.programmes.map((p) => p.id === selected ? { ...p, ...changes } : p) });
    setMessage('');
  };
  return <section aria-label="Programme Modules configuration" className="m-5 space-y-4 rounded-xl border border-primary-200 bg-background-50 p-4">
    <h3 className="text-lg font-semibold">Programme Modules</h3>
    <p className="text-sm text-foreground-600">Edit modules and PCP pathways here. Save Draft stores your modules edits privately. Use the wizard’s Publish changes button to make them available to new module enrolments. Existing module enrolments keep their saved version.</p>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {!config && !error && <p role="status">Loading modules configuration…</p>}
    {draft && <div className="rounded-lg border border-primary-200 p-3 text-sm">
      <p>Saved modules draft available.{draft.baseVersion !== version ? ' It was saved against an older wizard version; review it before publishing.' : ''}</p>
      <button type="button" className={`${btnSecondary} mt-2`} onClick={() => { onChange(structuredClone(draft.configuration)); setMessage('Saved draft restored for review.'); }}>Load saved modules draft</button>
    </div>}
    {config && programme && <>
      {config.schemaVersion === 1 && seed && <div className="rounded-lg border border-primary-200 p-3 text-sm">
        <p>This publication keeps the earlier PCP selection rules. Apply the new defaults to this draft, review the settings, then publish.</p>
        <button type="button" className={`${btnSecondary} mt-2`} onClick={() => onChange(applyPcpDefaults(config, seed))}>Apply PCP mandatory-module defaults to draft</button>
      </div>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-60 text-sm">Programme<select aria-label="Programme" className={inputClass} value={selected} onChange={(e) => { select(e.target.value as ProgrammeId); testSelect(undefined); }}>
          {config.programmes.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select></label>
        <button type="button" className={btnSecondary} onClick={() => showPreview((open) => !open)}>{preview ? 'Close modules preview' : 'Preview Changes'}</button>
        <button type="button" className={btnPrimary} disabled={busy || errors.length > 0} onClick={() => {
          setBusy(true); setError(''); void saveModulesDraft(config, version).then((data) => {
            setDraft(data); setMessage('Modules draft saved. Published modules have not changed.');
          }).catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
        }}>{busy ? 'Saving draft…' : 'Save Draft'}</button>
      </div>
      {errors.length > 0 && <ul role="alert" className="list-inside list-disc text-sm text-red-700">{errors.map((item) => <li key={item}>{item}</li>)}</ul>}
      {message && <p role="status" className="text-sm text-primary-700">{message}</p>}
      {preview && <div aria-label="Modules draft preview" role="region" className="rounded-xl border border-dashed border-primary-300 p-3">
        <p className="text-sm text-foreground-600">Draft preview. Pathway and module test choices stay in this preview.</p>
        <ProgrammeModulesView configuration={config} programmeId={selected} selection={testSelection} onChange={testSelect} version={version} />
      </div>}
      {selected === 'PCP_L6'
        ? <PathwayEditor pathways={programme.pathways} onChange={(pathways) => patch({ pathways })} />
        : <ModuleRowsEditor items={programme.modules} onChange={(modules) => patch({ modules })} />}
    </>}
  </section>;
}
