// ============================================================================
// PLR certificate/evidence API client
// Files for each Personal Learning Record entry, stored in Azure through
// quarantine -> scan -> approved, each with its blob path recorded on the
// entry's enrolment."Wizard_Plr_Records" row (see plr_evidence.py).
// ============================================================================
import type { LearnerKind } from './extendedIlr';

export interface PlrEvidenceFile {
  id: string;
  /** The PLR entry's id (PlrRecord.id). */
  recordRef: string;
  filename: string;
  contentType: string;
  sizeBytes: number | null;
  uploadedAt: string | null;
}

const base = (kind: LearnerKind, learnerId: string) => `/enrolment_api/wizard/${kind}/${learnerId}/plr-evidence`;

async function send<T>(url: string, init?: Parameters<typeof fetch>[1]): Promise<T> {
  let res: Response;
  try {
    // credentials: 'include' sends the session cookie the enrolment API requires.
    res = await fetch(url, { credentials: 'include', ...init });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchPlrEvidence(kind: LearnerKind, learnerId: string): Promise<PlrEvidenceFile[]> {
  const data = await send<{ results: PlrEvidenceFile[] }>(`${base(kind, learnerId)}/`);
  return data.results;
}

/** Multipart, so no Content-Type header: the browser sets the boundary. */
export function uploadPlrEvidence(kind: LearnerKind, learnerId: string, recordRef: string, file: File): Promise<PlrEvidenceFile> {
  const body = new FormData();
  body.append('record_ref', recordRef);
  body.append('file', file, file.name);
  return send<PlrEvidenceFile>(`${base(kind, learnerId)}/`, { method: 'POST', body });
}

export async function deletePlrEvidence(kind: LearnerKind, learnerId: string, fileId: string): Promise<void> {
  await send<{ deleted: boolean }>(`${base(kind, learnerId)}/${fileId}/`, { method: 'DELETE' });
}

/** A short-lived SAS URL for one stored file. */
export async function getPlrEvidenceUrl(kind: LearnerKind, learnerId: string, fileId: string): Promise<string> {
  const data = await send<{ url: string }>(`${base(kind, learnerId)}/${fileId}/download/`);
  return data.url;
}
