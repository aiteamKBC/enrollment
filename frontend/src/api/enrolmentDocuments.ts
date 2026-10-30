// ============================================================================
// Enrolment documents API client
// Generated compliance paperwork stored in Azure (container: enrolment-docs),
// indexed in enrolment."Enrolment_Documents". Talks to /enrolment_api.
// ============================================================================
import type { LearnerKind } from './extendedIlr';

const BASE = '/enrolment_api';

/** Codes mirror DOC_TYPES in backend/enrolment_api/documents.py. */
export type EnrolmentDocType =
  | 'extended-ilr'
  | 'training-plan'
  | 'commitment-statement'
  | 'apprenticeship-agreement'
  | 'contract-of-services'
  | 'initial-assessment'
  | 'learning-agreement'
  | 'privacy-notice';

export interface EnrolmentDocument {
  id: string;
  docType: EnrolmentDocType;
  docLabel: string;
  filename: string;
  path: string;
  sizeBytes: number | null;
  /** True only once every party this doc type needs has signed. */
  signed: boolean;
  generatedAt: string | null;
  /** Per-party sign-off, so a part-signed document reads correctly. */
  learner?: { name: string; signedAt: string | null; signed: boolean };
  employer?: { name: string; signedAt: string | null; signed: boolean };
}

/**
 * Which parties each document type is signed by. Mirrors SIGNING_PARTIES in
 * backend/enrolment_api/documents.py; types not listed are employer-only.
 */
export const DOC_SIGNING_PARTIES: Record<string, ReadonlyArray<'learner' | 'employer'>> = {
  'apprenticeship-agreement': ['learner', 'employer'],
};

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchEnrolmentDocuments(kind: LearnerKind, learnerId: string, docType?: EnrolmentDocType): Promise<EnrolmentDocument[]> {
  const qs = docType ? `?doc_type=${encodeURIComponent(docType)}` : '';
  let res: Response;
  try {
    res = await fetch(`${BASE}/documents/${kind}/${learnerId}/${qs}`, { credentials: 'include' });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const data = await readJson<{ results: EnrolmentDocument[] }>(res);
  return data.results;
}

/**
 * Store a generated PDF. Sent as multipart (not JSON) so the file streams to
 * Azure without being base64-inflated on the way through.
 */
export async function uploadEnrolmentDocument(
  kind: LearnerKind,
  learnerId: string,
  docType: EnrolmentDocType,
  file: Blob,
  filename: string,
  opts?: { signed?: boolean; learnerName?: string }
): Promise<EnrolmentDocument> {
  const body = new FormData();
  body.append('file', file, filename);
  body.append('doc_type', docType);
  if (opts?.signed) body.append('signed', 'true');
  if (opts?.learnerName) body.append('learner_name', opts.learnerName);

  let res: Response;
  try {
    // No Content-Type header: the browser must set the multipart boundary.
    res = await fetch(`${BASE}/documents/${kind}/${learnerId}/`, { method: 'POST', credentials: 'include', body });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  return readJson<EnrolmentDocument>(res);
}

/** Exchange a stored document for a short-lived SAS URL and open it. */
export async function getEnrolmentDocumentUrl(kind: LearnerKind, learnerId: string, docId: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/documents/${kind}/${learnerId}/${docId}/download/`, { credentials: 'include' });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const data = await readJson<{ url: string }>(res);
  return data.url;
}
