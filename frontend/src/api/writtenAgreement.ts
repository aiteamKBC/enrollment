import { persistOriginalPdf } from '@/features/compliance-documents/persistOriginalPdf';
import { pendingSignaturesChanged } from '@/features/pending-signatures/events';
import { readLearnerJson } from './learnerRead';
// ============================================================================
// Written Agreement API client
//
// The commercial agreement between employer and provider, signed by the
// learner, the employer and the provider. Lives in its own table
// (enrolment."Written_Agreements"); see backend/learner_api/written_agreement.py.
//
// Top-level fields are the LIVE derivation; `document.*` is the FROZEN snapshot
// taken at issue.
// ============================================================================
import type {
  WrittenAgreementParticulars,
  WrittenAgreementActivity,
} from '@/lib/writtenAgreementPdf';

const BASE = '/learner_api/written-agreement';

export type WrittenAgreementParty = 'learner' | 'employer' | 'provider';

export interface WrittenAgreementPartyState {
  signed: boolean;
  name: string;
  position: string;
  signedAt: string | null;
}

export interface WrittenAgreementDocument {
  template?: import('@/features/compliance-documents/types').IssuedTemplate;
  id: string;
  status: 'active' | 'superseded' | string;
  fullySigned: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  particulars: WrittenAgreementParticulars;
  delivery: { activities?: WrittenAgreementActivity[]; totalOtjHours?: number | null; offTheJobNote?: string; englishMathsNote?: string };
  epa: Record<string, string>;
  costs: { items?: { item: string; price: number | null }[]; total?: number | null; fundingBandMaximum?: number | null; balanceDue?: number | null };
  contacts: Record<string, any>;
  signatures: {
    learner: WrittenAgreementPartyState;
    employer: WrittenAgreementPartyState;
    provider: WrittenAgreementPartyState;
  };
  marks: { learner: string; employer: string; provider: string };
}

export interface WrittenAgreementResponse {
  learner: { id: string; name: string; programmeStatus: string };
  particulars: WrittenAgreementParticulars;
  delivery: WrittenAgreementDocument['delivery'];
  epa: Record<string, string>;
  costs: WrittenAgreementDocument['costs'];
  contacts: Record<string, any>;
  meta: { datesFrom: string; moduleCount: number; activityCount: number };
  /** Null until the provider issues it. */
  document: WrittenAgreementDocument | null;
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchWrittenAgreement(
  learnerId: string | number,
): Promise<WrittenAgreementResponse> {
  return readLearnerJson(`${BASE}/${learnerId}/`);
}

/** Issue the agreement, freezing the current content onto a new row. */
export async function issueWrittenAgreement(
  learnerId: string | number,
): Promise<WrittenAgreementDocument> {
  const data = await readJson<{ document: WrittenAgreementDocument; issuedDocumentSnapshot?: WrittenAgreementDocument }>(
    await fetch(`${BASE}/${learnerId}/issue/`, { method: 'POST', credentials: 'include', headers: { 'X-Document-Issue-Mode': 'initial' } }),
  );
  await persistOriginalPdf(learnerId, data.issuedDocumentSnapshot || data.document);
  pendingSignaturesChanged();
  return data.document;
}

/** Sign as one of the three parties. An empty signature withdraws that sign-off. */
export async function signWrittenAgreement(
  learnerId: string | number,
  party: WrittenAgreementParty,
  name: string,
  signature: string,
  position = '',
): Promise<WrittenAgreementDocument> {
  const data = await readJson<{ document: WrittenAgreementDocument }>(
    await fetch(`${BASE}/${learnerId}/sign/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party, name, signature, position }),
    }),
  );
  pendingSignaturesChanged();
  return data.document;
}
