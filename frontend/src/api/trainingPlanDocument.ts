import { readLearnerJson, invalidateLearnerReads } from './learnerRead';
// ============================================================================
// Training Plan API client
//
// The tripartite document — signed by the apprentice, the employer AND the
// training provider. Lives in its own table
// (enrolment."Training_Plan_Documents"); see
// backend/learner_api/training_plan_document.py.
//
// As with the other two documents, the top-level fields are the LIVE derivation
// and `document.*` is the FROZEN snapshot taken at issue.
// ============================================================================
import type {
  TrainingPlanProgramme,
  TrainingPlanEmployment,
  TrainingPlanRow,
} from '@/lib/trainingPlanPdf';

const BASE = '/learner_api/training-plan-document';

export type TrainingPlanParty = 'apprentice' | 'employer' | 'provider';

export interface TrainingPlanPartyState {
  signed: boolean;
  name: string;
  position: string;
  signedAt: string | null;
}

export interface TrainingPlanDocument {
  id: string;
  status: 'active' | 'superseded' | string;
  fullySigned: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  programme: TrainingPlanProgramme;
  employment: TrainingPlanEmployment;
  learningPlan: TrainingPlanRow[];
  otjh: Record<string, number | null>;
  epa: Record<string, string>;
  contacts: Record<string, any>;
  signatures: {
    apprentice: TrainingPlanPartyState;
    employer: TrainingPlanPartyState;
    provider: TrainingPlanPartyState;
  };
  marks: { apprentice: string; employer: string; provider: string };
}

export interface TrainingPlanResponse {
  learner: { id: string; name: string; programmeStatus: string };
  programme: TrainingPlanProgramme;
  employment: TrainingPlanEmployment;
  learningPlan: TrainingPlanRow[];
  otjh: Record<string, number | null>;
  epa: Record<string, string>;
  contacts: Record<string, any>;
  meta: { datesFrom: string; moduleCount: number };
  /** Null until the provider issues it. */
  document: TrainingPlanDocument | null;
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

export async function fetchTrainingPlanDocument(
  learnerId: string | number,
): Promise<TrainingPlanResponse> {
  return readLearnerJson(`${BASE}/${learnerId}/`);
}

/** Issue the plan, freezing the current content onto a new row. */
export async function issueTrainingPlanDocument(
  learnerId: string | number,
): Promise<TrainingPlanDocument> {
  const data = await readJson<{ document: TrainingPlanDocument }>(
    await fetch(`${BASE}/${learnerId}/issue/`, { method: 'POST', credentials: 'include' }),
  );
  invalidateLearnerReads();
  return data.document;
}

/** Sign as one of the three parties. An empty signature withdraws that sign-off. */
export async function signTrainingPlanDocument(
  learnerId: string | number,
  party: TrainingPlanParty,
  name: string,
  signature: string,
  position = '',
): Promise<TrainingPlanDocument> {
  const data = await readJson<{ document: TrainingPlanDocument }>(
    await fetch(`${BASE}/${learnerId}/sign/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party, name, signature, position }),
    }),
  );
  return data.document;
}
