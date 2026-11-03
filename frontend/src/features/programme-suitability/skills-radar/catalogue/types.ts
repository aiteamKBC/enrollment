import type { ProgrammeId } from '../../types';
import type { ModulesConfiguration } from '../../modules/configuration';
import type { PcpRadar } from '../allocation';

export type RadarPathway = 'operational' | 'strategic' | 'chartered';
export interface CatalogueRecord {
  id: string; code: string; kind: 'Knowledge' | 'Skill' | 'Behaviour'; description: string;
  moduleKey?: string; owners?: Record<RadarPathway, string>;
}
export interface RadarCatalogue { programmeId: ProgrammeId; version: string; publishedAt: string | null; records: CatalogueRecord[] }
export type RadarCatalogues = Record<ProgrammeId, RadarCatalogue>;
export interface ImportApproval { againstVersion: string; completeSnapshot: true; removedCodes: string[]; categoryCodes: string[] }
export interface CatalogueDraft { skillsRadarCatalogues: RadarCatalogues; skillsRadarImportApprovals: Partial<Record<ProgrammeId, ImportApproval>> }
export type ChangeKind = 'unchanged' | 'added' | 'removed' | 'descriptions' | 'categories' | 'mappings';
export interface Comparison extends Record<ChangeKind, number> {
  currentTotal: number; newTotal: number; removedCodes: string[]; categoryCodes: string[]; largeRemoval: boolean;
  changes: { code: string; changes: ChangeKind[]; before: CatalogueRecord | null; after: CatalogueRecord | null }[];
}
export interface CatalogueIndex { catalogues: RadarCatalogues; baseVersion: number | null; modules: ModulesConfiguration }
export interface ImportPreview { catalogue: RadarCatalogue; comparison: Comparison; baseVersion: number | null }
export interface LearnerCatalogue { programmeId: ProgrammeId | null; catalogue: RadarCatalogue | null; token: string; pinned: boolean }
export interface ManagedRadar { enabled: boolean; loading: boolean; error: string; data: LearnerCatalogue | null; grouping: PcpRadar | null }
