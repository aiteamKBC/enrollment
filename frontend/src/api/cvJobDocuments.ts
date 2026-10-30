// ============================================================================
// CV/Job Description documents API client
// The CV, qualification transcript and GCSE evidence, stored in Azure through
// quarantine -> scan -> approved, each with its blob path recorded on the
// learner's enrolment."Wizard_Cv_Job" row (see cv_job_documents.py).
// ============================================================================
import type { LearnerKind } from './extendedIlr';

export type CvDocKind = 'cv' | 'transcript' | 'gcse-english' | 'gcse-maths';

export interface CvDocument {
  id: string;
  docKind: CvDocKind;
  filename: string;
  contentType: string;
  sizeBytes: number | null;
  uploadedAt: string | null;
}

const base = (kind: LearnerKind, learnerId: string) => `/enrolment_api/wizard/${kind}/${learnerId}/cv-documents`;

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

export async function fetchCvDocuments(kind: LearnerKind, learnerId: string): Promise<CvDocument[]> {
  const data = await send<{ results: CvDocument[] }>(`${base(kind, learnerId)}/`);
  return data.results;
}

/** Multipart, so no Content-Type header: the browser sets the boundary. */
export function uploadCvDocument(kind: LearnerKind, learnerId: string, docKind: CvDocKind, file: File): Promise<CvDocument> {
  const body = new FormData();
  body.append('doc_kind', docKind);
  body.append('file', file, file.name);
  return send<CvDocument>(`${base(kind, learnerId)}/`, { method: 'POST', body });
}

export async function deleteCvDocument(kind: LearnerKind, learnerId: string, fileId: string): Promise<void> {
  await send<{ deleted: boolean }>(`${base(kind, learnerId)}/${fileId}/`, { method: 'DELETE' });
}

/** A short-lived SAS URL for one stored file. */
export async function getCvDocumentUrl(kind: LearnerKind, learnerId: string, fileId: string): Promise<string> {
  const data = await send<{ url: string }>(`${base(kind, learnerId)}/${fileId}/download/`);
  return data.url;
}
