export type Pathway = 'marketing' | 'project-controls';
export type ProgrammeId = 'ME_L4' | 'MM_L6' | 'APM_L4' | 'PCP_L6';
export type Responses = Record<string, string[]>;
export interface Programme {
  id: ProgrammeId; title: string; level: number; pathway: Pathway; description: string;
  summary: string; responsibilities: string[]; active: boolean; order: number; catalogueId: string | null;
}
export interface Answer {
  id: string; text: string; active: boolean; order: number; affinities: Partial<Record<ProgrammeId, number>>;
}
export interface Question {
  id: string; slug: string; title: string; description: string; pathway: Pathway;
  weight: number; order: number; active: boolean; required: boolean; multiple: boolean;
  critical: boolean; criticalProgramme: ProgrammeId | null; answers: Answer[];
}
export interface Configuration {
  schemaVersion: 1; enabled: boolean; introduction: string; programmes: Programme[]; questions: Question[];
  rules: { minimumScore: number; minimumDifference: number; minimumCoverage: number;
    minimumCriticalIndicators: number; criticalStrength: number; mixedQuestionThreshold: number; mixedAffinityThreshold: number };
}
export interface PublicQuestion extends Omit<Question, 'weight' | 'active' | 'critical' | 'criticalProgramme' | 'answers'> {
  answers: Pick<Answer, 'id' | 'text' | 'order'>[];
}
export interface PublicConfiguration {
  enabled: boolean; introduction: string; programmes: Programme[]; questions: PublicQuestion[];
}
export interface Evidence {
  questionId: string; title: string; weight: number; answered: boolean; points: Record<string, number>;
  selectedAnswers: { id: string; text: string }[];
}
export interface Result {
  recommendationPolicy?: string; recommendationFlags?: string[];
  admissionsReviewOutcome?: string; admissionsFlags?: string[];
  outcome: string; recommendedProgramme: ProgrammeId | null; scores: Record<ProgrammeId, number>;
  coverage: number; evidenceCoverage: number; availableWeight: number; criticalIndicators: Record<string, string[]>;
  reasons: string[]; missingRequired: string[]; strongestEvidence: Evidence[]; breakdown?: Evidence[];
}
export interface Review {
  learnerRequest?: { reason: string; at: string };
  recommendedProgramme?: ProgrammeId | null; reason?: string; actor?: string; at?: string; notificationStatus?: string;
}
export interface AssessmentState {
  alternativeSelectionReason?: string | null;
  selectionStatus?: 'NOT_STARTED' | 'IN_PROGRESS' | 'SUBMITTED_PENDING_CONFIRMATION' | 'SELECTION_CONFIRMED';
  selectionConfirmedAt?: string | null;
  programmeSelectionSource?: string; allowedSelections?: ProgrammeId[];
  recommendedProgramme?: ProgrammeId | null; recommendationStatus?: string;
  assessmentStatus?: 'NOT_STARTED' | 'IN_PROGRESS' | 'SUBMITTED'; admissionsReviewStatus?: string;
  required?: boolean; completionStatus?: 'NOT_STARTED' | 'IN_PROGRESS' | 'SUBMITTED';
  available: boolean; configuration: PublicConfiguration | null; configurationVersion: number | null;
  pathway: Pathway | ''; responses: Responses; result: Result | null; revision: number;
  selectedProgramme: ProgrammeId | ''; originalPreference?: ProgrammeId | ''; reviewStatus: string;
  review?: Review; confirmedAt?: string | null; history?: unknown[];
}

export interface AssessmentChanges {
  alternativeSelectionReason?: string;
  pathway?: Pathway; responses?: Responses; selectedProgramme?: ProgrammeId | ''; confirm?: boolean;
  action?: 'draft' | 'submit' | 'select' | 'continue' | 'request_review'; reason?: string;
}

export interface SuitabilityCompletion {
  selectedProgramme?: ProgrammeId | '';
  required: boolean;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'SUBMITTED';
  selectionConfirmed?: boolean;
}
