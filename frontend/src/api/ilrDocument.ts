import { readLearnerJson } from './learnerRead';
// ============================================================================
// Individual Learner Record API client
//
// The ILR lives in its own table (enrolment."ILR_Documents") and is signed by
// the learner and the provider only — an employer has no part in it and never
// sees it. See backend/learner_api/ilr_document.py.
//
// As with the Apprenticeship Agreement:
//   * `learnerDetails` / `answers` at the top level are the LIVE derivation.
//   * `document.learnerDetails` / `document.answers` are the FROZEN snapshot
//     taken at issue — what was actually signed.
// ============================================================================
import type { IlrLearnerDetails } from '@/lib/ilrDocumentPdf';

const BASE = '/learner_api/ilr-document';

export type IlrParty = 'learner' | 'provider';

export interface IlrPartyState {
  signed: boolean;
  name: string;
  signedAt: string | null;
}

export interface IlrDocument {
  id: string;
  status: 'active' | 'superseded' | string;
  fullySigned: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  learnerDetails: IlrLearnerDetails;
  answers: Record<string, unknown>;
  signatures: { learner: IlrPartyState; provider: IlrPartyState };
  marks: { learner: string; provider: string };
}

export interface IlrResponse {
  learner: { id: string; name: string; programmeStatus: string };
  /** What a freshly issued ILR would state, from live data. */
  learnerDetails: IlrLearnerDetails;
  answers: Record<string, unknown>;
  /** Null until one has been issued. */
  document: IlrDocument | null;
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchIlrDocument(learnerId: string | number): Promise<IlrResponse> {
  return readLearnerJson(`${BASE}/${learnerId}/`);
}

/** Issue the ILR, freezing the current details and answers onto a new row. */
export async function issueIlrDocument(learnerId: string | number): Promise<IlrDocument> {
  const data = await readJson<{ document: IlrDocument }>(
    await fetch(`${BASE}/${learnerId}/issue/`, { method: 'POST', credentials: 'include' }),
  );
  return data.document;
}

/** Sign as one party. An empty signature withdraws that party's sign-off. */
export async function signIlrDocument(
  learnerId: string | number,
  party: IlrParty,
  name: string,
  signature: string,
): Promise<IlrDocument> {
  const data = await readJson<{ document: IlrDocument }>(
    await fetch(`${BASE}/${learnerId}/sign/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party, name, signature }),
    }),
  );
  return data.document;
}
