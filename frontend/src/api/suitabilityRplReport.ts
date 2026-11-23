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
  parentReportId?: string | null;
  amendmentReason?: string;
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
  cvEvidenceVersion: { documentId: string; container: string; blobName: string; sha256: string; extractorVersion: string };
  sourceSnapshot: SuitabilityRplEvidence;
  sourceVersions: Record<string, unknown>;
  sourceSnapshotHash: string;
  trainingHours: { originalHours: number | null; reductionHours: number; remainingHours: number | null };
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
  contextError?: string | null;
  history: Array<{ id: string; reportReference: string; programmeName: string; status: string; reportVersion: number; amendmentReason: string; parentReportId: string | null; finalisedAt: string | null }>;
  aiEnabled: boolean;
}

export interface SuitabilityRplSavePayload {
  revision: number;
  newVersion?: boolean;
  amendmentReason?: string;
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

export type RplAnalysisOutcome =
  | 'no-relevant-prior-learning'
  | 'potential-whole-module-reduction'
  | 'relevant-partial-no-exemption'
  | 'insufficient-evidence';

export interface RplAnalysisModuleRecommendation {
  moduleId: string;
  moduleName: string;
  plannedHours: number | null;
  wholeModuleReductionRecommended: boolean;
  evidenceSufficiency: 'sufficient' | 'partial' | 'insufficient' | 'needs-review';
  supportingEvidence: Array<{ evidenceId: string; detail: string }>;
  authoritativeKsbMappings: Array<{ code: string; description: string; source: string }>;
  missingEvidence: string[];
  explanation: string;
}

export interface RplAnalysisRecommendation {
  overallOutcome: RplAnalysisOutcome;
  reasoning: string;
  modules: RplAnalysisModuleRecommendation[];
  evidenceGaps: string[];
  warnings: string[];
}

export interface RplAnalysisMetadata {
  provider: string;
  model: string;
  generatedAt: string;
  sourceSnapshotHash: string;
  cvEvidenceVersion: { documentId: string; container: string; blobName: string; sha256: string; extractorVersion: string };
  usage?: Record<string, unknown> | null;
  finishReason?: string | null;
}

export interface SuitabilityRplSignatureStatus {
  issueId: string;
  reportId: string;
  reportVersion: number;
  reportRevision: number;
  pdfSha256: string;
  requiredRoles: Array<'admin' | 'learner' | 'employer'>;
  signatures: Record<'admin' | 'learner' | 'employer', {
    signed: boolean;
    name: string;
    signedAt: string | null;
    method: string;
  }>;
  fullySigned: boolean;
  pdfUrl: string;
  signingEnabled: boolean;
  signingStatus: string;
}

export interface SuitabilityRplIssuedDocument {
  issueId: string;
  title: string;
  issuedAt: string | null;
  reportId: string;
  reportVersion: number;
  reportRevision: number;
  pdfSha256: string;
  pdfUrl: string;
  requiredRoles: Array<'admin' | 'learner' | 'employer'>;
  signatures: SuitabilityRplSignatureStatus['signatures'];
  fullySigned: boolean;
  signingEnabled: boolean;
  signingStatus: string;
}

const base = (kind: LearnerKind, learnerId: string) =>
  `/enrolment_api/programme-suitability/${kind}/${learnerId}/suitability-rpl/`;

export class SuitabilityRplRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'SuitabilityRplRequestError';
    this.status = status;
  }
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: { error?: string } | null = null;
  try { data = JSON.parse(text) as { error?: string }; } catch { data = null; }
  if (!response.ok) throw new SuitabilityRplRequestError(data?.error || `The report request failed (${response.status}).`, response.status);
  if (!data || typeof data !== 'object') throw new Error('The report service returned an invalid response.');
  return data as T;
}

async function readPdf(response: Response): Promise<Blob> {
  if (!response.ok) {
    const text = await response.text();
    let message = `The report PDF request failed (${response.status}).`;
    try { message = (JSON.parse(text) as { error?: string }).error || message; } catch { /* use status message */ }
    throw new SuitabilityRplRequestError(message, response.status);
  }
  const blob = await response.blob();
  if (!blob.type.includes('pdf')) throw new Error('The report service returned an invalid PDF.');
  return blob;
}

export async function fetchSuitabilityRplReport(kind: LearnerKind, learnerId: string, signal?: AbortSignal) {
  return readJson<SuitabilityRplState>(await fetch(base(kind, learnerId), {
    credentials: 'include', cache: 'no-store', signal,
  }));
}

export async function saveSuitabilityRplReport(kind: LearnerKind, learnerId: string, payload: SuitabilityRplSavePayload) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ report: SuitabilityRplReport } & Pick<SuitabilityRplState, 'history' | 'aiEnabled'>>(await fetch(base(kind, learnerId), {
    method: 'PUT', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(payload),
  }));
}

export async function previewSuitabilityRplReport(
  kind: LearnerKind,
  learnerId: string,
  payload: SuitabilityRplSavePayload,
) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readPdf(await fetch(`${base(kind, learnerId)}preview-pdf/`, {
    method: 'POST', credentials: 'include', cache: 'no-store',
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

export async function analyseSuitabilityRplEvidence(kind: LearnerKind, learnerId: string) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ recommendation: RplAnalysisRecommendation; metadata: RplAnalysisMetadata }>(await fetch(`${base(kind, learnerId)}ai/rpl-analysis/`, {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: '{}',
  }));
}

export async function issueSuitabilityRplReport(
  kind: LearnerKind,
  learnerId: string,
  payload: { reportId: string; reportVersion: number; reportRevision: number },
) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ issued: boolean; issueId: string; sha256: string; alreadyIssued: boolean; signingEnabled: boolean }>(await fetch(`${base(kind, learnerId)}issue/`, {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(payload),
  }));
}

export async function fetchSuitabilityRplSignatures(
  kind: LearnerKind,
  learnerId: string,
  issueId: string,
) {
  return readJson<{ status: SuitabilityRplSignatureStatus }>(await fetch(
    `${base(kind, learnerId)}issued/${issueId}/signatures/`,
    { credentials: 'include', cache: 'no-store' },
  ));
}

export async function fetchSuitabilityRplIssuedDocuments(kind: LearnerKind, learnerId: string) {
  return readJson<{ documents: SuitabilityRplIssuedDocument[] }>(await fetch(
    `${base(kind, learnerId)}issued/`,
    { credentials: 'include', cache: 'no-store' },
  ));
}

export async function signSuitabilityRplReport(
  kind: LearnerKind,
  learnerId: string,
  issueId: string,
  payload: { role: 'admin' | 'learner' | 'employer'; name: string; signature: string; consent: true; consentText: string },
) {
  const { csrfToken } = await prefetchAssessmentSecurity();
  return readJson<{ status: SuitabilityRplSignatureStatus }>(await fetch(
    `${base(kind, learnerId)}issued/${issueId}/signatures/`,
    {
      method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload),
    },
  ));
}
