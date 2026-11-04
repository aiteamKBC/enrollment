import { useMemo, useState } from 'react';
import type { ModulesConfiguration, ProgrammeModuleSelection } from '../../modules/configuration';
import { moduleSelectionComplete } from '../../modules/configuration';
import { ProgrammeModulesView } from '../../modules/ProgrammeModulesCard';
import { ModuleMasterDetail } from '../ModuleMasterDetail';
import { resolveCatalogueRadar } from './runtime';
import type { RadarCatalogue } from './types';

export function CataloguePreview({ catalogue, configuration, version }: {
  catalogue: RadarCatalogue; configuration: ModulesConfiguration; version: number | null;
}) {
  const [selection, select] = useState<ProgrammeModuleSelection>();
  const [selectedModule, selectModule] = useState<string | null>(null);
  const grouping = useMemo(() => resolveCatalogueRadar(catalogue, { configuration, configurationVersion: version,
    selectedProgramme: catalogue.programmeId, savedSelection: selection,
    selectionComplete: moduleSelectionComplete(configuration, selection, version) }), [catalogue, configuration, version, selection]);
  return <div role="region" aria-label="Skills Radar draft preview" className="space-y-4 rounded-xl border border-dashed border-primary-300 p-3">
    <p className="text-sm">Draft preview — {catalogue.records.length} KSBs. Test choices stay in this preview.</p>
    <div className="flex gap-4 text-xs">{(['Knowledge', 'Skill', 'Behaviour'] as const).map((kind) => <span key={kind}>{kind}: {catalogue.records.filter((r) => r.kind === kind).length}</span>)}</div>
    {catalogue.programmeId === 'PCP_L6' && <ProgrammeModulesView configuration={configuration} programmeId="PCP_L6" selection={selection} onChange={select} version={version} />}
    {grouping.status !== 'ready' ? <p role="status" className="text-sm">{grouping.message}</p> : <ModuleMasterDetail modules={grouping.modules} assessments={{}}
      selectedModuleId={selectedModule} onSelect={selectModule} renderQuestion={(q) => <p key={q.id} className="p-3 text-sm [overflow-wrap:anywhere]">{q.codes[0]} {q.title}<span className="mt-1 block text-xs text-foreground-500">{q.kind}</span></p>} />}
  </div>;
}
