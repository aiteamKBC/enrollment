import { Fragment, useId } from 'react';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import { btnSecondary } from '@/pages/users/components/ui';
import { completion, selectionConfirmed } from '../useSuitability';
import type { ProgrammeId } from '../types';
import { ordered, isModuleGroup, selectableModules, selectedModuleIds, mandatoryIds, pathwaySelectionError, selectionFingerprint, moduleSelectionComplete, type ProgrammeModule, type ProgrammeModuleSelection, type ModulesConfiguration } from './configuration';
import { useModuleSelectionSave } from './useModuleSelectionSave';
import { useModulesConfiguration } from './useModulesConfiguration';

function ModuleTable({ modules, selected = [], disabled = true, limitReached = false, onToggle }: {
  modules: ProgrammeModule[]; selected?: string[]; disabled?: boolean; limitReached?: boolean; onToggle?: (id: string) => void;
}) {
  const tableId = useId();
  // Keep group row headers scoped to their own children, not later root modules.
  const sections: ProgrammeModule[][] = [];
  for (const item of ordered(modules).filter((module) => module.active)) {
    const previous = sections[sections.length - 1];
    if (!previous || isModuleGroup(item) || isModuleGroup(previous[0])) sections.push([item]);
    else previous.push(item);
  }
  const rows = (items: ProgrammeModule[], nested = false) => ordered(items).filter((item) => item.active).map((item) => <Fragment key={item.id}>
    {isModuleGroup(item) ? <tr className="border-t border-foreground-200 bg-primary-50/50">
      <th id={`${tableId}-${item.id}`} scope="rowgroup" colSpan={2} className="py-3 pl-5 pr-3 font-semibold text-foreground-800">
        {item.title}{item.description && <span className="mt-1 block text-xs font-normal text-foreground-600">{item.description}</span>}
      </th>
    </tr> :
    <tr className="border-t border-foreground-100 align-top">
      <td className={`py-3 pr-3 ${nested ? 'pl-5' : ''}`}>
        <label className={`flex items-start gap-2 ${nested ? 'ml-3 border-l-2 border-primary-200 pl-3' : ''}`}>
          {item.selectable && onToggle && <input type="checkbox" className="mt-1 shrink-0 accent-primary-500" disabled={disabled || item.isMandatory || (limitReached && !selected.includes(item.id))}
            aria-label={item.title} checked={selected.includes(item.id)} onChange={() => onToggle(item.id)} />}
          <span className="min-w-0 [overflow-wrap:anywhere]">{item.title}
            {item.isMandatory && <span className="ml-2 inline-block rounded bg-primary-100 px-1.5 py-0.5 text-xs font-medium text-primary-800">Mandatory</span>}
            {item.description && <span className="mt-1 block text-xs font-normal text-foreground-600">{item.description}</span>}
            {item.credits !== undefined && <span className="mt-1 block text-xs font-normal text-foreground-500">{item.credits} {item.credits === 1 ? 'credit' : 'credits'}</span>}
          </span>
        </label>
      </td>
      <td className="py-3 text-right text-foreground-600">{item.sessions == null ? '—' : `${item.sessions} Sessions`}</td>
    </tr>}
    {item.items && rows(item.items, true)}
  </Fragment>);
  return <table className="w-full text-left text-sm">
    <thead><tr><th scope="col" className="pb-2 pr-3 font-medium">Module</th><th scope="col" className="pb-2 text-right font-medium">Sessions</th></tr></thead>
    {sections.map((section, index) => <tbody key={section[0].id}
      aria-labelledby={isModuleGroup(section[0]) ? `${tableId}-${section[0].id}` : undefined}
      className={index > 0 && isModuleGroup(sections[index - 1][0])
        ? '[&>tr:first-child>td]:border-t-2 [&>tr:first-child>td]:border-primary-200 [&>tr:first-child>td]:pt-5' : undefined}>
      {rows(section)}
    </tbody>)}
  </table>;
}

/** Shared by real learner rendering and a fully local administration preview. */
export function ProgrammeModulesView({ configuration, programmeId, selection, onChange, readOnly = false, version }: {
  configuration: ModulesConfiguration; programmeId: ProgrammeId; selection?: ProgrammeModuleSelection;
  onChange?: (next: ProgrammeModuleSelection) => void; readOnly?: boolean; version?: number | null;
}) {
  const programme = configuration.programmes.find((p) => p.id === programmeId);
  if (!programme) return null;
  const pcp = programmeId === 'PCP_L6';
  const paths = ordered(programme.pathways).filter((p) => p.active);
  const columns = paths.length === 1 ? '' : paths.length === 2 || paths.length === 4 ? 'lg:grid-cols-2' : 'lg:grid-cols-3';
  return <section aria-label="Programme Modules" className={`mt-6 rounded-xl border border-primary-200 bg-background-50 p-4 md:p-5 ${pcp ? 'max-w-7xl' : 'max-w-3xl'}`}>
    <h3 className="text-lg font-semibold">Programme Modules</h3>
    <p className="mt-1 text-sm text-foreground-600">These are the modules for your selected programme.</p>
    <p className="mb-5 mt-2 text-sm font-semibold text-primary-700">{programme.title}</p>
    {!pcp ? <ModuleTable modules={programme.modules} /> : <div role="radiogroup" aria-label="Project Controls Professional pathway" className={`grid gap-4 ${columns}`}>
      {paths.map((pathway) => {
        const active = selection?.programme === 'PCP_L6' && selection.pathway === pathway.id;
        const selected = selectedModuleIds(selection, pathway);
        const mandatory = mandatoryIds(pathway);
        const optionalCount = selected.filter((id) => !mandatory.includes(id)).length;
        const selectionError = active && !readOnly ? pathwaySelectionError(pathway, selected) : '';
        const credits = selectableModules(pathway.modules).filter((m) => selected.includes(m.id)).reduce((total, m) => total + (m.credits ?? 0), 0);
        const toggleOptional = (id: string) => {
          if (mandatory.includes(id) || (!selected.includes(id) && pathway.requiredOptionalModulesCount != null && optionalCount >= pathway.requiredOptionalModulesCount)) return;
          onChange?.({ programme: 'PCP_L6', pathway: pathway.id, configurationVersion: version,
            modulesByPathway: { ...(selection?.modulesByPathway ?? {}),
              [pathway.id]: selected.includes(id) ? selected.filter((key) => key !== id) : [...selected, id] } });
        };
        return <section key={pathway.id} aria-label={pathway.title}
          className={`min-w-0 rounded-xl border p-4 ${active ? 'border-primary-400 bg-primary-50/50' : 'border-foreground-200 bg-background-50'}`}>
          <label className="flex items-start gap-2 text-base font-semibold">
            <input type="radio" name="pcp-module-pathway" className="mt-1 shrink-0 accent-primary-500" checked={active}
              disabled={readOnly || !onChange} onChange={() => onChange?.({ programme: 'PCP_L6', pathway: pathway.id, configurationVersion: version,
                modulesByPathway: { ...(selection?.programme === 'PCP_L6' ? selection.modulesByPathway ?? {} : {}),
                  [pathway.id]: [...new Set([...(selection?.modulesByPathway[pathway.id] ?? []), ...mandatory])] } })} />
            {pathway.title}
          </label>
          {pathway.description && <p className="mt-2 rounded-lg bg-primary-50/50 p-3 text-sm leading-relaxed text-foreground-600">{pathway.description}</p>}
          {pathway.subtitle && <p className="mt-2 text-xs text-foreground-600">{pathway.subtitle}</p>}
          {pathway.recognition && <p className="mt-2 rounded bg-primary-50 p-2 text-xs text-primary-700">{pathway.recognition}</p>}
          {pathway.targetCredits !== null && <p className="mt-2 text-xs text-foreground-600">Target credits: {pathway.targetCredits}</p>}
          {pathway.requiredOptionalModulesCount != null && <p className="mt-2 text-xs text-foreground-600">Select exactly {pathway.requiredOptionalModulesCount} optional modules.</p>}
          {active && <p className="mt-2 text-xs font-medium text-primary-700">Selected credits: {credits}</p>}
          {selectionError && <p role="status" className="mt-2 text-xs text-amber-800">{selectionError}</p>}
          <div className="mt-4"><ModuleTable modules={pathway.modules} selected={selected} disabled={!active || readOnly || !onChange || !pathway.allowModuleSelection}
            limitReached={pathway.requiredOptionalModulesCount != null && optionalCount >= pathway.requiredOptionalModulesCount}
            onToggle={toggleOptional} /></div>
        </section>;
      })}
      {paths.length === 0 && <p className="text-sm text-foreground-600">No pathways are currently available.</p>}
    </div>}
  </section>;
}

function SavedModules({ configuration, programmeId, version, previewOnly = false }: { configuration: ModulesConfiguration; programmeId: ProgrammeId; version: number | null; previewOnly?: boolean }) {
  const { readOnly, hydrated } = useWizard();
  const { selection, status, error, update, retry } = useModuleSelectionSave();
  const path = configuration.programmes.find((p) => p.id === programmeId)?.pathways.find((p) => p.id === selection?.pathway);
  const reviewed: ProgrammeModuleSelection | undefined = selection && path ? { ...selection, configurationVersion: version,
    modulesByPathway: { ...selection.modulesByPathway, [path.id]: selectedModuleIds(selection, path) } } : undefined;
  const canSaveReview = reviewed && moduleSelectionComplete(configuration, reviewed, version) && selectionFingerprint(reviewed) !== selectionFingerprint(selection);
  return <>
    {selection && selection.configurationVersion !== version && !(version === 0 && selection.configurationVersion === undefined) &&
      <p role="status" className="mt-4 text-sm text-amber-800">Published module settings have changed. Review your choices and save them before continuing.</p>}
    <ProgrammeModulesView configuration={configuration} programmeId={programmeId} version={version} selection={selection}
      onChange={previewOnly ? undefined : programmeId === 'PCP_L6' ? update : undefined} readOnly={previewOnly || readOnly || !hydrated} />
    {!readOnly && canSaveReview && <button type="button" className={`${btnSecondary} mt-3`} disabled={status === 'saving' || !hydrated}
      onClick={() => update(reviewed)}>Save reviewed module selection</button>}
    {!previewOnly && !readOnly && programmeId === 'PCP_L6' && <p role="status" aria-live="polite" className="mt-3 text-xs text-foreground-600">
      {status === 'saving' ? 'Saving…' : status === 'saved' ? 'All changes saved.' : status === 'error' ? 'Unable to save changes. Retry.' : 'Choose a pathway, then choose your modules. Your choices save automatically.'}
    </p>}
    {status === 'error' && <div role="alert" className="mt-3 text-sm text-red-700"><p>{error}</p>
      <button type="button" className={`${btnSecondary} mt-2`} onClick={retry}>Retry saving module choices</button>
    </div>}
  </>;
}

export function ProgrammeModulesCard() {
  const { suitability, userId, isCommercial, modules } = useWizard();
  const state = suitability?.state;
  const confirmed = selectionConfirmed(state ?? null);
  const resolvedProgramme = state?.selectedProgramme || modules?.data?.selectedProgramme;
  const preview = Boolean(resolvedProgramme && completion(state) === 'SUBMITTED');
  const fallback = useModulesConfiguration(isCommercial ? 'commercial' : 'apprenticeship', userId, (confirmed || preview) && !modules);
  const { data, error, retry } = modules ?? fallback;
  if (!resolvedProgramme) return null;
  if (!confirmed && !preview) return <p role="status" className="mt-6 text-sm text-foreground-600">Confirm your programme selection to load its modules.</p>;
  if (error) return <div role="alert" className="mt-6 text-sm text-red-700">{error}<button type="button" className={`${btnSecondary} ml-3`} onClick={retry}>Retry loading modules</button></div>;
  if (!data) return <p role="status" className="mt-6 text-sm">Loading programme modules…</p>;
  return <SavedModules key={userId} configuration={data.configuration} programmeId={resolvedProgramme} version={data.configurationVersion} previewOnly={!confirmed} />;
}
