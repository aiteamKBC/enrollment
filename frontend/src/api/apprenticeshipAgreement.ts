import { readLearnerJson } from './learnerRead';
// ============================================================================
// Apprenticeship Agreement API client
//
// The statutory agreement between an apprentice and their employer, stored in
// its own table (enrolment."Apprenticeship_Agreements") rather than the generic
// documents index — see backend/learner_api/apprenticeship_agreement.py.
//
// Two things to keep straight:
//   * `particulars` on the response is the LIVE derivation — what a newly
//     issued agreement would say.
//   * `agreement.particulars` is the FROZEN snapshot taken at issue — what the
//     parties actually signed. Editing the learning plan afterwards changes the
//     first and never the second.
// ============================================================================
const BASE = '/learner_api/apprenticeship-agreement';

export type AgreementParty = 'apprentice' | 'employer';

export interface AgreementPlanModule {
  moduleId: string;
  moduleTitle: string;
  groupName: string;
  hours: number;
}

export interface AgreementParticulars {
  apprenticeName: string;
  employerName: string;
  employerAddress: string;
  standard: string;
  startDate: string;
  endDate: string;
  practicalStartDate: string;
  practicalEndDate: string;
  durationWeeks: number | null;
  plannedOtjHours: number | null;
}

export interface AgreementPartyState {
  signed: boolean;
  name: string;
  signedAt: string | null;
}

export interface Agreement {
  id: string;
  status: 'active' | 'superseded' | string;
  fullySigned: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  /** The frozen particulars — what was signed. */
  particulars: AgreementParticulars;
  planModules: AgreementPlanModule[];
  signatures: { apprentice: AgreementPartyState; employer: AgreementPartyState };
  /** The signature images, for embedding in the rendered PDF. */
  marks: { apprentice: string; employer: string };
  document: { path: string; sizeBytes: number | null; stored: boolean };
}

export interface AgreementResponse {
  learner: {
    id: string;
    name: string;
    programmeStatus: string;
    group: string;
    cohort: string;
  };
  /** What a freshly issued agreement would state, from live data. */
  particulars: AgreementParticulars;
  planModules: AgreementPlanModule[];
  meta: { datesFrom: string; moduleCount: number };
  /** Null until one has been issued. */
  agreement: Agreement | null;
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchAgreement(learnerId: string | number): Promise<AgreementResponse> {
  return readLearnerJson(`${BASE}/${learnerId}/`);
}

/**
 * Issue the agreement, freezing the current particulars onto a new row. Any
 * existing agreement is superseded, and the new one starts unsigned — a
 * signature only ever attests to the particulars it was given against.
 */
export async function issueAgreement(learnerId: string | number): Promise<Agreement> {
  const data = await readJson<{ agreement: Agreement }>(
    await fetch(`${BASE}/${learnerId}/issue/`, { method: 'POST', credentials: 'include' }),
  );
  return data.agreement;
}

/** Sign as one party. An empty signature withdraws that party's sign-off. */
export async function signAgreement(
  learnerId: string | number,
  party: AgreementParty,
  name: string,
  signature: string,
): Promise<Agreement> {
  const data = await readJson<{ agreement: Agreement }>(
    await fetch(`${BASE}/${learnerId}/sign/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party, name, signature }),
    }),
  );
  return data.agreement;
}

/** DD/MM/YYYY, the format the statutory form uses. */
export function fmtAgreementDate(iso?: string | null): string {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}
