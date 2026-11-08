import type { LearnerKind } from '@/api/learnerDetail';
import { prefetchAssessmentSecurity } from '@/features/programme-suitability/api';

export interface SuitabilityRplSkill {
  id: string;
  code: string;
  kind: string;
  description: string;
  level: string;
  score: number | null;
  moduleKey: string;
}

export interface SuitabilityRplSkillSummary {
  label: string;
  score: number | null;
  answered: number;
  total: number;
}

export interface SuitabilityRplTrainingModule {
  moduleId: string;
  title: string;
  plannedHours: number | null;
  ksbCodes: string[];
}

export interface SuitabilityRplEvidence {
  learner: {
    id: string;
    name: string;
    reference: string;
    employer: string;
    jobTitle: string;
    responsibilities: string[];
  };
  programme: { id: string; name: string; level: string; startDate: string };
  programmeUnderstanding: Array<{ question: string; answer: string }>;
  programmeSuitability: {
    status: string;
    pathway?: string;
    selectedProgramme?: string;
    recommendedProgramme?: string;
    selectedProgrammeSpecification?: {
      id?: string;
      title?: string;
      summary?: string;
      description?: string;
      pathway?: string;
      level?: number;
      responsibilities?: string[];
    };
    outcome?: string;
    responses?: Array<{
      questionId: string;
      group?: string;
      question: string;
      answers: string[];
      selectedOptions?: Array<{ id: string; label: string }>;
      saved?: boolean;
    }>;
  };
  skillsRadar: { status: string; scaleMaximum: number; catalogueVersion: string; cataloguePinned?: boolean; summary?: SuitabilityRplSkillSummary[]; items: SuitabilityRplSkill[] };
  evidence: {
    cv: { status: string; extractionStatus?: string; experience: string; documents: Array<{ id: string; filename: string }> };
    qualifications: { status: string; items: Array<{ label: string; value: string; source?: string }>; documents: Array<{ id: string; filename: string }> };
    plr: { status: 'not_available'; message: string };
  };
  trainingPlan: {
    available: boolean;
    source: 'authoritative_training_plan';
    plannedHours: number | null;
    modules: SuitabilityRplTrainingModule[];
    message: string;
  };
  limitations: string[];
  capturedAt: string;
}

export interface SuitabilityRplReduction {
  id?: string;
  moduleId: string;
  moduleTitle?: string;
  plannedHours?: number;
  reducedHours: number | '';
  ksbCodes: string[];
  evidence: string;
  assessorComments: string;
}

export interface SuitabilityRplReport {
  id: string;
  status: 'draft' | 'finalised';
  reportReference: string;
  reportVersion: number;
  revision: number;
  learnerKind: LearnerKind;
  learnerId: string;
  contextKey: string;
  programmeId: string;
  programmeName: string;
  enrolmentReference: string;
  practicalStartDate: string;
  relevantPriorLearning: boolean | null;
  reductionJustified: boolean | null;
  assessorEvidenceReview: string;
  noReductionReason: string;
  suitabilityOutcome: 'not_assessed' | 'suitable' | 'further_assessment';
  finalSummary: string;
  suitabilityJustification: string;
  suitabilityAiMetadata?: Record<string, unknown>;
  suitabilityReviewed: boolean;
  suitabilityReviewedAt: string | null;
  suitabilityReviewedBy: string;
  rplJustification: string;
  rplAiMetadata?: Record<string, unknown>;
  rplReviewed: boolean;
  rplReviewedAt: string | null;
  rplReviewedBy: string;
  cvExtractionAcknowledged: boolean;
  cvExtractionAcknowledgedBy: string | null;
  cvExtractionAcknowledgedAt: string | null;
  sourceSnapshot: SuitabilityRplEvidence;
  sourceVersions: Record<string, unknown>;
  sourceSnapshotHash: string;
  reductions: SuitabilityRplReduction[];
  createdAt: string;
  updatedAt: string;
  finalisedAt: string | null;
}

export interface SuitabilityRplState {
  report: SuitabilityRplReport | null;
  evidence: SuitabilityRplEvidence;
  sourceVersions: Record<string, unknown>;
  context: { key: string; programmeId: string; programmeName: string; enrolmentReference: string; practicalStartDate: string };
  history: Array<{ id: string; reportReference: string; programmeName: string; status: string; finalisedAt: string | null }>;
  aiEnabled: boolean;
}

export interface SuitabilityRplSavePayload {
  revision: number;
  newVersion?: boolean;
  relevantPriorLearning: boolean | null;
  reductionJustified: boolean | null;
  assessorEvidenceReview: string;
  noReductionReason: string;
  suitabilityOutcome: SuitabilityRplReport['suitabilityOutcome'];
  finalSummary: string;
  suitabilityJustification: string;
  suitabilityReviewed: boolean;
  rplJustification: string;
  rplReviewed: boolean;
  cvExtractionAcknowledged?: boolean;
  cvExtractionAcknowledgementReason?: string;
  reductions: SuitabilityRplReduction[];
  finalise: boolean;
}

const base = (kind: LearnerKind, learnerId: string) =>
  `/enrolment_api/programme-suitability/${kind}/${learnerId}/suitability-rpl/`;

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: { error?: string } | null = null;
  try { data = JSON.parse(text) as { error?: string }; } catch { data = null; }
  if (!response.ok) throw new Error(data?.error || `The report request failed (${response.status}).`);
  if (!data || typeof data !== 'object') throw new Error('The report service returned an invalid response.');
  return data as T;
}

export async function fetchSuitabilityRplReport(kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return readJson<SuitabilityRplState>(await fetch(base(kind, learnerId), {
    credentials: 'include', cache: 'no-store', signal,
  }));
}

export async function saveSuitabilityRplReport(kind: LearnerKind, learnerId: string, payload: SuitabilityRplSavePayload) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ report: SuitabilityRplReport; aiEnabled: boolean }>(await fetch(base(kind, learnerId), {
    method: 'PUT', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(payload),
  }));
}

export async function generateSuitabilityRplJustification(
  kind: LearnerKind,
  learnerId: string,
  type: 'suitability' | 'rpl',
) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ report: SuitabilityRplReport }>(await fetch(`${base(kind, learnerId)}ai/${type}/`, {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: '{}',
  }));
}
