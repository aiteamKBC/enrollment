const BASE = '/progress_reviews_api';

export interface ProgressReviewActiveLearner {
  learnerId: number;
  fullName: string;
  email: string | null;
  programme: string | null;
  coach: string | null;
  cohort: string | null;
  group: string | null;
  startDate: string | null;
  endDate: string | null;
}

export interface ProgressReviewPeriod {
  review_number: number | null;
  review_date: string;
  review_period_start: string;
  review_period_end: string;
  action_period_start: string;
  action_period_end: string;
}

/** The progress_review_pack JSON, exactly as the spec defines it — every
 * section is either real, database-backed data or the literal string
 * "Not available"; nothing here is a guess. */
export interface ProgressReviewPack {
  learner: Record<string, unknown>;
  review: Record<string, unknown>;
  attendance: Record<string, unknown>;
  progress: Record<string, unknown>;
  otj: Record<string, unknown>;
  lms_modules: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  assignments: Array<Record<string, unknown>>;
  workplace_activities: Array<Record<string, unknown>>;
  ksbs: {
    knowledge_evidenced: Array<Record<string, unknown>>;
    skills_evidenced: Array<Record<string, unknown>>;
    behaviours_evidenced: Array<Record<string, unknown>>;
    priority_next: Array<Record<string, unknown>>;
  };
  epa: Record<string, unknown>;
  actions: Array<Record<string, unknown>>;
  manager_questions: string[];
  source_warnings: string[];
}

export interface ProgressReviewGenerateResult {
  reviewId: string;
  learnerId: number;
  reviewNumber: number | null;
  reviewDate: string;
  reviewPeriodStart: string;
  reviewPeriodEnd: string;
  generationStatus: 'draft' | 'completed' | 'failed';
  /** How this version was made. */
  revisionSource?: 'generated' | 'edited' | 'uploaded';
  sourceWarnings: string[];
  downloadUrl: string;
}

export interface ProgressReviewLatestRun {
  exists: boolean;
  reviewId?: string;
  generationStatus?: 'draft' | 'pending' | 'running' | 'completed' | 'failed';
  generatedAt?: string | null;
  /** How the newest version was made. */
  revisionSource?: 'generated' | 'edited' | 'uploaded';
}

export interface ProgressReviewBulkResult {
  results: Array<
    | ProgressReviewGenerateResult
    | { learnerId: number; generationStatus: 'failed'; error: string }
  >;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { cache: 'no-store', ...init });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  return parse<T>(res);
}

export async function fetchActiveLearners(): Promise<ProgressReviewActiveLearner[]> {
  const data = await request<{ results: ProgressReviewActiveLearner[] }>('/learners/active/');
  return data.results;
}

export async function fetchReviewPeriods(learnerId: number | string): Promise<ProgressReviewPeriod[]> {
  const data = await request<{ results: ProgressReviewPeriod[] }>(`/${learnerId}/periods/`);
  return data.results;
}

export async function fetchReviewPack(learnerId: number | string, reviewDate?: string): Promise<ProgressReviewPack> {
  const qs = reviewDate ? `?review_date=${encodeURIComponent(reviewDate)}` : '';
  return request<ProgressReviewPack>(`/${learnerId}/pack/${qs}`);
}

/** Cheap existence check for one exact review — never a general "does this
 * learner have any deck", so a review card can never show another review's
 * generated state. Backs both the card button label and the modal's initial
 * state (the owner can opt into loading their unsaved draft). */
export async function fetchLatestRun(
  learnerId: number | string,
  reviewDate: string,
  options: { includeDraft?: boolean } = {},
): Promise<ProgressReviewLatestRun> {
  const draft = options.includeDraft ? '&include_draft=1' : '';
  return request<ProgressReviewLatestRun>(`/${learnerId}/runs/latest/?review_date=${encodeURIComponent(reviewDate)}${draft}`);
}

export async function generateProgressReview(
  learnerId: number | string,
  reviewDate?: string,
): Promise<ProgressReviewGenerateResult> {
  return request<ProgressReviewGenerateResult>(`/${learnerId}/generate/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(reviewDate ? { review_date: reviewDate } : {}),
  });
}

export async function bulkGenerateProgressReviews(
  opts: { learnerIds?: Array<number | string>; reviewDate?: string } = {},
): Promise<ProgressReviewBulkResult> {
  const body: Record<string, unknown> = {};
  if (opts.learnerIds?.length) body.learner_ids = opts.learnerIds;
  if (opts.reviewDate) body.review_date = opts.reviewDate;
  return request<ProgressReviewBulkResult>('/bulk-generate/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchProgressReviewDownloadUrl(reviewId: string): Promise<string> {
  const data = await request<{ url: string }>(`/${reviewId}/download/`);
  return data.url;
}

/** Monthly Coaching Meeting decks: same pack shape and download endpoint as a
 * Progress Review, scoped to the month up to `meetingDate`. */
export async function fetchMcmPack(learnerId: number | string, meetingDate: string): Promise<ProgressReviewPack> {
  return request<ProgressReviewPack>(`/${learnerId}/mcm/pack/?meeting_date=${encodeURIComponent(meetingDate)}`);
}

export async function fetchMcmLatestRun(
  learnerId: number | string,
  meetingDate: string,
  options: { includeDraft?: boolean } = {},
): Promise<ProgressReviewLatestRun> {
  const draft = options.includeDraft ? '&include_draft=1' : '';
  return request<ProgressReviewLatestRun>(`/${learnerId}/mcm/runs/latest/?meeting_date=${encodeURIComponent(meetingDate)}${draft}`);
}

export async function generateMcm(learnerId: number | string, meetingDate: string): Promise<ProgressReviewGenerateResult> {
  return request<ProgressReviewGenerateResult>(`/${learnerId}/mcm/generate/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meeting_date: meetingDate }),
  });
}

/** Publishes the owner's generated draft without replacing its stored file. */
export async function publishProgressReview(reviewId: string): Promise<ProgressReviewGenerateResult> {
  return request<ProgressReviewGenerateResult>(`/${reviewId}/publish/`, { method: 'POST' });
}

/** The generated deck rendered as a PDF by the server, for the in-page viewer. */
export async function fetchProgressReviewPreview(reviewId: string): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/${reviewId}/preview/`, { cache: 'no-store' });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  if (!res.ok) await parse(res);
  return res.blob();
}

/** The fields of a generated deck its owner can correct (see backend edits.py).
 * A value a slide shows as "Not available" is null. */
export interface DeckEvidenceItem {
  /** null for an item added in the editor. */
  ref: string | null;
  /** Editor-only key for an added item; the server ignores it. */
  client_key?: string;
  evidence_title: string | null;
  evidence_summary: string | null;
  evidence_date: string | null;
  ksb_mappings: string[];
  image_ref: string | null;
}

export interface DeckEditableFields {
  kind: 'mcm' | 'progress_review';
  /** How the version being edited was made. */
  revisionSource: 'generated' | 'edited' | 'uploaded';
  learner: Record<'full_name' | 'programme' | 'employer' | 'manager_name' | 'coach', string | null>;
  attendance: { attendance_percentage: number | null; engagement_notes: string | null };
  progress: { current_programme_progress_percentage: number | null; target_progress_percentage: number | null; next_module: string | null };
  otj: { completed_otj_hours: number | null; required_otj_hours_to_date: number | null; risk_status: string | null };
  evidence: DeckEvidenceItem[];
  /** How many evidence items (in order) get a photo frame on the slides. */
  evidence_photo_slots: number;
  priority_ksbs: Array<{ code: string | null; description: string | null; how_to_evidence: string | null }>;
  actions: Array<{ title: string | null; detail: string | null; owner: string | null; due_by: string | null }>;
  epa?: { current_readiness: number | null };
  manager_questions?: string[];
}

export async function fetchDeckEditableFields(reviewId: string): Promise<DeckEditableFields> {
  return request<DeckEditableFields>(`/${reviewId}/edit/`);
}

/** Renders a corrected version of the deck; the version it revises is kept. */
export async function saveDeckEdits(reviewId: string, edits: Omit<DeckEditableFields, 'kind' | 'revisionSource' | 'evidence_photo_slots'>): Promise<ProgressReviewGenerateResult> {
  return request<ProgressReviewGenerateResult>(`/${reviewId}/edit/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edits }),
  });
}

/** Stores a replacement photo; returns the image_ref to put on an evidence item. */
export async function uploadDeckImage(reviewId: string, file: File): Promise<string> {
  const body = new FormData();
  body.append('image', file);
  const data = await request<{ imageRef: string }>(`/${reviewId}/edit/images/`, { method: 'POST', body });
  return data.imageRef;
}

/** Where the editor shows one of the deck's photos from. */
export function deckImageUrl(reviewId: string, imageRef: string): string {
  return `${BASE}/${reviewId}/edit/images/?ref=${encodeURIComponent(imageRef)}`;
}

/** Makes a .pptx edited in PowerPoint the deck's newest version. */
export async function uploadEditedDeck(reviewId: string, file: File): Promise<ProgressReviewGenerateResult> {
  const body = new FormData();
  body.append('file', file);
  return request<ProgressReviewGenerateResult>(`/${reviewId}/edit/upload/`, { method: 'POST', body });
}

/** Uses the owner's own .pptx instead of a generated deck (no deck needed first). */
export async function uploadOwnDeck(
  kind: 'mcm' | 'progress_review', learnerId: number | string, meetingDate: string, file: File,
): Promise<ProgressReviewGenerateResult> {
  const body = new FormData();
  body.append('file', file);
  body.append(kind === 'mcm' ? 'meeting_date' : 'review_date', meetingDate);
  return request<ProgressReviewGenerateResult>(`/${learnerId}/${kind === 'mcm' ? 'mcm/' : ''}upload/`, { method: 'POST', body });
}
