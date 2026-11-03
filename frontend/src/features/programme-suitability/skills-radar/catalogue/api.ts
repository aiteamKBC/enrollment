import type { LearnerKind } from '@/api/extendedIlr';
import { prefetchAssessmentSecurity, request } from '../../api';
import type { ProgrammeId } from '../../types';
import type { CatalogueDraft, CatalogueIndex, ImportPreview, LearnerCatalogue } from './types';

const BASE = '/enrolment_api/skills-radar';
export const getCatalogues = () => request<CatalogueIndex>(`${BASE}/catalogues/`);
export const getCatalogueDraft = () => request<{ configuration: CatalogueDraft | null; baseVersion: number | null }>(`${BASE}/draft/`);
export const saveCatalogueDraft = (configuration: CatalogueDraft, baseVersion: number | null) =>
  request<{ configuration: CatalogueDraft; baseVersion: number | null }>(`${BASE}/draft/`, { configuration, baseVersion });
export const getLearnerCatalogue = (kind: LearnerKind, id: string) => request<LearnerCatalogue>(`${BASE}/${kind}/${id}/`);
export const csvUrl = (pid: ProgrammeId, template = false) => `${BASE}/catalogues/${pid}/${template ? '?template=1' : ''}`;
export async function uploadCatalogue(pid: ProgrammeId, file: File): Promise<ImportPreview> {
  const body = new FormData(); body.append('file', file, file.name);
  const response = await fetch(`${BASE}/catalogues/${pid}/`, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRFToken': (await prefetchAssessmentSecurity()).csrfToken }, body });
  const text = await response.text();
  let result;
  try { result = JSON.parse(text); } catch { result = null; }
  if (!response.ok) throw Object.assign(new Error(result?.error || `CSV upload failed (${response.status}).`), { errors: result?.errors ?? [] });
  if (!result?.catalogue || !result?.comparison) throw new Error('Invalid CSV preview response. Please retry.');
  return result;
}
