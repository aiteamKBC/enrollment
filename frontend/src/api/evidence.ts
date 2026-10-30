import { learningFetch } from '@/lib/personalLearning';
import { readLearnerJson, invalidateLearnerReads } from './learnerRead';
import type { LearnerKind } from '@/api/learnerDetail';

const BASE = '/learner_api/evidence';

/** Training-plan context an evidence file was uploaded against — snapshotted at
 * upload time so the row stays traceable even if the curriculum is later
 * restructured (module/week/component ids can be reused or removed). */
export interface EvidenceTrainingPlanDetails {
  moduleId?: string | null;
  moduleTitle?: string | null;
  weekId?: string | null;
  weekTitle?: string | null;
  componentId?: string | null;
  componentTitle?: string | null;
  componentType?: string | null;
  evidenceDescription?: string | null;
  ksbCodes?: string[];
  otjhHours?: number | null;
}

export interface EvidenceRecord {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  status: 'pending' | 'approved' | 'rejected' | string;
  scanResult: string | null;
  sectionRef: string;
  uploadedAt: string | null;
  trainingPlanDetails: EvidenceTrainingPlanDetails | null;
  /** False once the activity has been submitted for marking — the file is part
   *  of what was handed in, and the server refuses to delete it. Offer Remove
   *  only when this is true, or the learner gets a 409 from an action the page
   *  appeared to allow. Older responses omit it; treat a missing value as
   *  deletable so the server stays the one that decides. */
  canDelete?: boolean;
  /** The coach's decision on the activity this file belongs to, or '' when the
   *  work has not been handed in. Distinct from `status`, which is the
   *  malware-scan verdict from the upload pipeline — a file can be stored and
   *  clean while no coach has ever seen it. Use this, not `status`, for
   *  anything the learner reads as validation. */
  markingStatus?: string;
}

export interface UploadEvidenceResult {
  id: string;
  status: string;
  scanResult: string;
  filename: string;
  sectionRef: string;
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Request failed with ${res.status}`;
    throw new Error(message);
  }
  return data as T;
}

/** Upload a file as workplace evidence. Lands in quarantine, is promoted to the
 * approved container, and (on approval) recorded in "Learner"."Evidence". */
export async function uploadEvidence(
  kind: LearnerKind,
  id: string,
  file: File,
  sectionRef: string,
  trainingPlanDetails?: EvidenceTrainingPlanDetails,
): Promise<UploadEvidenceResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('section_ref', sectionRef);
  if (trainingPlanDetails) form.append('training_plan_details', JSON.stringify(trainingPlanDetails));
  let res: Response;
  try {
    // NOTE: do not set Content-Type — the browser sets the multipart boundary.
    res = await learningFetch(`${BASE}/${kind}/${id}/upload/`, { method: 'POST', body: form });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const data = await parse<UploadEvidenceResult>(res);
  invalidateLearnerReads();
  return data;
}

export async function fetchEvidence(
  kind: LearnerKind,
  id: string,
  opts: { sectionRef?: string; status?: string; signal?: AbortSignal } = {},
): Promise<EvidenceRecord[]> {
  const params = new URLSearchParams();
  if (opts.sectionRef) params.set('section_ref', opts.sectionRef);
  if (opts.status) params.set('status', opts.status);
  const qs = params.toString() ? `?${params.toString()}` : '';
  const data = await readLearnerJson<{ results: EvidenceRecord[] }>(`${BASE}/${kind}/${id}/${qs}`, { signal: opts.signal });
  return data.results;
}

/** Get a short-lived SAS download URL for an approved evidence file. */
export async function getEvidenceDownloadUrl(kind: LearnerKind, id: string, fileId: string): Promise<string> {
  const data = await readLearnerJson<{ url: string }>(`${BASE}/${kind}/${id}/${fileId}/download/`);
  return data.url;
}

/** Remove one of the learner's own evidence files.
 *
 * Reuploading is this followed by an ordinary upload — there is no separate
 * replace endpoint. The server refuses (409) once the file has been submitted
 * for marking. */
export async function deleteEvidence(kind: LearnerKind, id: string, fileId: string): Promise<void> {
  let res: Response;
  try {
    res = await learningFetch(`${BASE}/${kind}/${id}/${fileId}/`, { method: 'DELETE' });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  await parse<{ deleted: boolean }>(res);
  invalidateLearnerReads();
}
