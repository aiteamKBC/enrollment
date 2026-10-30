import { readLearnerJson, invalidateLearnerReads } from './learnerRead';
import type { LearnerKind } from '@/api/learnerDetail';
import { formatSystemTimestamp } from '@/lib/format';

const BASE = '/learner_api/reviews';

/**
 * Panels across all three reviews. Which ones apply is decided server-side —
 * mirrors review_form.SECTIONS_BY_REVIEW, and `sections` on the response says
 * what to render.
 */
export type ReviewSection =
  // Eligibility Review & FS Discussion
  | 'ilr'
  | 'extendedIlr'
  | 'functionalSkills'
  | 'fsJobRoleDiscussion'
  | 'programmeStatus'
  // RPL And Experience
  | 'priorLearning'
  | 'rplExperience'
  | 'plr'
  | 'skillsRadar'
  // Workplace Health & Safety Declaration
  | 'healthSafetyVetting'
  // ULN Privacy Notice & Learner Acknowledgement
  | 'ulnPrivacyNotice'
  | 'learnerAcknowledgement'
  // shared
  | 'comments';

export interface ReviewLearnerInformation {
  name: string;
  programmeName: string;
  programmeStartDate: string;
  plannedEndDate: string;
  programmeStatus: string;
  employer: string;
  manager: string;
  mentor: string;
}

/** One Functional Skills assessment row (initial or diagnostic). */
export interface FsAssessment {
  subject: string;
  level: string;
  date: string;
  outcome: string;
}

/** A per-subject Functional Skills result. */
export interface FsResult {
  score: string;
  assessmentDate: string;
}

/** One Prior Learning entry, added through the "New Item" modal. */
export interface PriorLearningItem {
  description: string;
  impact: string;
  durationReduced: string;
  costReduced: string;
  offTheJobTimeReduced: string;
}

export interface ReviewFormAnswers {
  ilr?: Record<string, string>;
  extendedIlr?: Record<string, string>;
  functionalSkills?: {
    initialAssessments?: FsAssessment[];
    diagnosticAssessments?: FsAssessment[];
    exemptions?: Record<string, string>;
    results?: Record<string, FsResult>;
  };
  fsJobRoleDiscussion?: Record<string, string>;
  programmeStatus?: { status?: string };
  // --- RPL And Experience ---
  priorLearning?: { items?: PriorLearningItem[] };
  rplExperience?: Record<string, string>;
  plr?: {
    uln?: string;
    reportedAttainment?: string;
    /** Calculated per-subject attainment: English / Maths / ICT. */
    subjectLevels?: Record<string, string>;
  };
  skillsRadar?: { notes?: string };
  // --- Workplace Health & Safety Declaration ---
  healthSafetyVetting?: Record<string, string>;
  // --- ULN Privacy Notice & Learner Acknowledgement ---
  ulnPrivacyNotice?: { noticeRead?: string };
  learnerAcknowledgement?: { acknowledged?: string };
  // --- shared ---
  comments?: { text?: string };
}

export interface ReviewSignature {
  /** PNG data URL, or '' when unsigned. */
  signature: string;
  name: string;
  signedAt: string | null;
  signed: boolean;
}

export interface ReviewSignatures {
  learner: ReviewSignature;
  admin: ReviewSignature;
  /**
   * The learner's employer, on reviews that attest to employment facts (Health &
   * Safety, Eligibility). `required` is false on reviews the employer has no part
   * in — the RPL review — where this block is omitted from the document entirely.
   */
  employer?: ReviewSignature & { required?: boolean };
  /** False until the form is completed — an unfinished review cannot be signed. */
  signable: boolean;
  /**
   * The learner's signature captured during enrolment. Offered as the default so
   * they reuse it rather than drawing a new one. Empty object when they have none,
   * and omitted entirely from the documents list (which never renders it).
   */
}

/** One KSB with the learner's Skills Radar self-assessment of it. */
export interface SkillsRadarItem {
  ksbId: string;
  theme: string;
  kind: string;
  codes: string[];
  title: string;
  /** '' when this KSB was never answered. */
  level: string;
  score: number | null;
  note: string;
}

export interface SkillsRadarSummary {
  standardId: string;
  standardLabel: string;
  items: SkillsRadarItem[];
  answered: number;
  total: number;
}

/** A qualification already on the learner's Personal Learner Record. */
export interface PlrRecord {
  placeOfStudy: string;
  qualificationType: string;
  subject: string;
  level: string;
  awardDate: string | null;
  credits: number | null;
  grade: string;
  recordType: string;
}

export interface ReviewFormResponse {
  eventKey: string;
  reviewType: string;
  reviewLabel: string;
  scheduledDate: string | null;
  scheduledTime: string | null;
  reviewedBy: string;
  learnerInformation: ReviewLearnerInformation;
  answers: ReviewFormAnswers;
  /** Only the sections this review actually renders. */
  sectionStatus: Partial<Record<ReviewSection, boolean>>;
  sections: ReviewSection[];
  programmeStatusOptions: string[];
  priorAttainmentOptions?: string[];
  priorAttainmentSubjectLevels?: string[];
  /** PLR panel only (RPL review). */
  uln?: string;
  plrRecords?: PlrRecord[];
  /** Skills Radar panel only (RPL review): the learner's own self-assessment. */
  skillsRadar?: SkillsRadarSummary;
  signatures: ReviewSignatures;
  completed: boolean;
  completedAt: string | null;
  startedAt: string | null;
  meetingLink: string;
  status: string;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, init);
  let res: Response;
  try {
    res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
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
  invalidateLearnerReads();
  return data as T;
}

/** One started or finished review, as listed under Review documents. */
export interface ReviewDocument {
  eventKey: string;
  reviewType: string;
  label: string;
  scheduledDate: string | null;
  reviewedBy: string;
  completed: boolean;
  completedAt: string | null;
  startedAt: string | null;
  sectionsDone: number;
  sectionsTotal: number;
  signatures: ReviewSignatures;
}

export interface ReviewDocumentsResponse {
  programme: string;
  documents: ReviewDocument[];
}

/** Reviews this learner has started or finished. */
export function fetchReviewDocuments(kind: LearnerKind, id: string): Promise<ReviewDocumentsResponse> {
  return call<ReviewDocumentsResponse>(`${BASE}/${kind}/${id}/`);
}

/** The event key contains colons, which must survive the URL path. */
const path = (kind: LearnerKind, id: string, eventKey: string) =>
  `${BASE}/${kind}/${id}/${eventKey.split('/').map(encodeURIComponent).join('/')}/`;

export function fetchReviewForm(kind: LearnerKind, id: string, eventKey: string): Promise<ReviewFormResponse> {
  return call<ReviewFormResponse>(path(kind, id, eventKey));
}

/**
 * Sign a completed review. Pass an empty `signature` to withdraw a signature.
 * `party` is 'learner' (their reviews page) or 'admin' (the learner's board).
 */
export function signReviewForm(
  kind: LearnerKind,
  id: string,
  eventKey: string,
  body: { party: 'learner' | 'admin'; name: string; signature: string },
): Promise<ReviewFormResponse> {
  return call<ReviewFormResponse>(`${path(kind, id, eventKey)}sign/`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * Save one or more sections. Sections are merged server-side, so posting a single
 * panel never blanks the others.
 */
export function saveReviewForm(
  kind: LearnerKind,
  id: string,
  eventKey: string,
  body: {
    answers?: Partial<ReviewFormAnswers>;
    sectionStatus?: Partial<Record<ReviewSection, boolean>>;
    reviewedBy?: string;
    finish?: boolean;
  },
): Promise<ReviewFormResponse> {
  return call<ReviewFormResponse>(path(kind, id, eventKey), {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

// ── the four onboarding reviews, issued by the enrolment team ────────────────

export type OnboardingReviewType = 'eligibility-review' | 'workspace' | 'training-plan' | 'uln-privacy';

/**
 * One onboarding review and how far it has got. The enrolment team issues each
 * one from the learner's board (no booking, no meeting); until then only
 * `type`, `label` and `issued: false` are present.
 */
export interface OnboardingReviewSlot {
  type: OnboardingReviewType;
  label: string;
  issued: boolean;
  eventKey?: string;
  issuedAt?: string | null;
  started?: boolean;
  completed?: boolean;
  completedAt?: string | null;
  sectionsDone?: number;
  sectionsTotal?: number;
  reviewedBy?: string;
  signatures?: ReviewSignatures;
}

export interface OnboardingReviewSlotsResponse {
  programme: string;
  reviews: OnboardingReviewSlot[];
  allIssued: boolean;
  allCompleted: boolean;
}

/** The learner's four onboarding reviews (none for a commercial learner). */
export function fetchOnboardingReviewSlots(kind: LearnerKind, id: string): Promise<OnboardingReviewSlotsResponse> {
  return call<OnboardingReviewSlotsResponse>(`${BASE}/${kind}/${id}/onboarding/`);
}

/** Issue one onboarding review to the learner. Staff only; each is issued once. */
export function issueOnboardingReview(kind: LearnerKind, id: string, type: OnboardingReviewType): Promise<OnboardingReviewSlotsResponse> {
  return call<OnboardingReviewSlotsResponse>(`${BASE}/${kind}/${id}/issue/`, {
    method: 'POST',
    body: JSON.stringify({ type }),
  });
}

/** When a review was issued or completed, in the business time zone: "15 Oct 2026, 14:32". */
export function issuedLabel(iso?: string | null): string {
  return iso ? formatSystemTimestamp(iso, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : '';
}
