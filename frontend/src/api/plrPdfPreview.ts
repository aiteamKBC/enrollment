import { invalidateWizardCache, type LearnerKind } from './extendedIlr';
import type { PlrState } from '@/pages/users/types';

export interface PlrPdfImportResult { imported: true; plr: PlrState; added: number; skipped: number; message: string }

export interface PlrPdfPreviewRecord {
  placeOfStudy: string; qualificationType: string; subject: string; level: string;
  awardDate: string; credits: string; grade: string; recordType: 'Imported';
  sourceSection: string; awardingBody: string; referenceNumber: string;
  startDate: string; endDate: string; completionStatus: string; outcomeStatus: string; language: string;
}
export interface PlrPdfPreviewResult {
  previewOnly: true; uln: string; learnerName: string; dateOfBirth: string; identityVerified: boolean;
  records: PlrPdfPreviewRecord[]; counts: { learningEvents: number; achievements: number }; warnings: string[];
}

async function readJson(response: Response) {
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The PDF preview service is unavailable. Please retry.');
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The PDF preview service returned an unreadable response. Please retry.'); }
  if (!response.ok) throw new Error(data.error || 'The PDF could not be previewed. Please retry.');
  return data;
}

/** Multipart preview only. Never invokes any enrolment write or import endpoint. */
export async function previewPlrPdf(kind: LearnerKind, learnerId: string, file: File, enteredUln: string, signal: AbortSignal): Promise<PlrPdfPreviewResult> {
  const url = `/enrolment_api/wizard/${kind}/${encodeURIComponent(learnerId)}/plr-pdf-preview/`;
  const security = await readJson(await fetch(url, { credentials: 'include', signal }));
  if (typeof security.csrfToken !== 'string' || !security.csrfToken) throw new Error('The PDF preview security check failed. Please retry.');
  const form = new FormData();
  form.append('file', file); form.append('enteredUln', enteredUln);
  const result = await readJson(await fetch(url, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRFToken': security.csrfToken }, body: form, signal }));
  if (result.previewOnly !== true || !Array.isArray(result.records)) throw new Error('An invalid PDF preview was returned. Nothing has been saved.');
  return result;
}

/** Confirmation resends the original file; client preview records are never sent. */
export async function importPlrPdf(kind: LearnerKind, learnerId: string, file: File, enteredUln: string, signal: AbortSignal): Promise<PlrPdfImportResult> {
  const base = `/enrolment_api/wizard/${kind}/${encodeURIComponent(learnerId)}`;
  const security = await readJson(await fetch(`${base}/plr-pdf-preview/`, { credentials: 'include', signal }));
  if (typeof security.csrfToken !== 'string' || !security.csrfToken) throw new Error('The PDF import security check failed.');
  const body = new FormData(); body.append('file', file); body.append('enteredUln', enteredUln);
  const result = await readJson(await fetch(`${base}/plr-pdf-import/`, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRFToken': security.csrfToken }, body, signal }));
  if (result.imported !== true || !result.plr || !Array.isArray(result.plr.records)) throw new Error('The import result could not be confirmed. Reload PLR before retrying.');
  invalidateWizardCache(kind, learnerId);
  return result;
}
