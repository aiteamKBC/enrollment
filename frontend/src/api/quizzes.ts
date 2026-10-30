import { learningFetch } from '@/lib/personalLearning';
import { readLearnerJson } from './learnerRead';
// ============================================================================
// Quiz-taking API client.
// Talks to the Django backend at /learner_api/quizzes (proxied to :8000 by Vite in dev).
// ============================================================================

import { invalidateLearnerDetailCache } from '@/api/learnerDetail';
import { CompletionValidationError, type WorkingRuleReason } from '@/lib/completionValidation';

const BASE = '/learner_api/quizzes';

export type QuestionType =
  | 'single_choice' | 'multiple_choice' | 'true_false' | 'fill_gap'
  | 'matching' | 'image_matching' | 'ordering' | 'keywords';

export interface QuizAnswerOption {
  id: number;
  text?: string;
  left?: string;
  leftKey?: string;
  label?: string;
  imageUrl?: string;
}

export interface QuizQuestion {
  id: number;
  text: string;
  imageUrl?: string;
  type: QuestionType;
  points: number;
  sortOrder: number;
  explanation: string | null;
  answers: QuizAnswerOption[];
  rightOptions?: string[];
  answerCount?: number;
}

export interface Quiz {
  id: number;
  unavailableReason?: string | null;
  title: string;
  module: string | null;
  programme: string | null;
  weekId: string | null;
  duration: number | null;
  timeUnit: string | null;
  passingGrade: number | null;
  randomizeQuestions: boolean;
  randomizeAnswers: boolean;
  questions: QuizQuestion[];
}

/** Answer value shape depends on the question type:
 *  single_choice/true_false -> answer id (number)
 *  multiple_choice          -> answer ids (number[])
 *  fill_gap                 -> free text (string)
 *  matching/image_matching  -> { [leftKey]: rightOptionText }
 *  ordering                 -> answer ids in the learner's chosen order (number[])
 *  keywords                 -> free-text words (string[])
 */
export type QuizAnswerValue = number | number[] | string | string[] | Record<string, string>;

export interface QuizSubmission {
  retakeTimeChoice?: 'keep' | 'new';
  answers: Record<string, QuizAnswerValue>;
  timeTakenSeconds: number;
  startedAt: string;
  trackingToken: string;
  module?: string | null;
  week?: string | null;
  ksbs?: string[];           // KSB codes the learner marked as fulfilled
  feedback?: string;         // general feedback about the quiz
  reportedTime?: string;     // self-reported time-to-complete (planned time or free text)
  skipReflection?: boolean;  // explicit learner choice, audited separately from an empty answer
  /** The working instant the learner declared after their Submit click was
   *  refused. Omitted on a first, already-valid attempt. The rest of the
   *  submission is re-sent unchanged, so the attempt grades identically. */
  declaredCompletedAt?: string | null;
}

export interface QuizQuestionResult {
  questionId: number;
  questionText: string;
  imageUrl?: string;
  type: QuestionType;
  points: number;
  earned: number;
  possible: number;
  correct: boolean;
  chosenAnswer: string | null;
  correctAnswer: string | null;
}

// The slim stored attempt (also echoed in the submit response's `attempt`).
export interface QuizAttempt {
  kind: 'quiz';
  attempt: number;            // 1-based attempt number for this quiz
  grade: number;              // 0-1 decimal, e.g. 0.9
  achievedScore: number;      // questions correct
  totalScore: number;         // questions total
  passed: boolean;
  quizId: number;
  ksbs?: string[];
  feedback?: string;
  reportedTime?: string;
  reflectionSkipped?: boolean;
  questions: unknown[];       // slim id-referenced questions (not read by the results screen)
  startedAt: string;
  submittedAt: string;
  timeTaken: string;          // "MM:SS", e.g. "00:26"
  timeTrackingSource: string;
  claimedSeconds: number;
  serverSessionSeconds: number;
  verifiedSeconds: number;
}

export interface QuizAttemptResult {
  attempt: QuizAttempt;
  breakdown: QuizQuestionResult[];   // full-text, for the results screen
  earned: number;
  possible: number;
  grade: number;              // 0-1 decimal
  achievedScore: number;
  totalScore: number;
  passed: boolean;
  timeTaken: string;
  quizName: string;
}

export interface QuizReadingMaterial {
  title: string;
  summary: string;
  sections: Array<{ heading: string; body?: string; paragraphs?: string[] }>;
  keyTakeaways: string[];
}

export interface QuizReading {
  id: number;
  quizId: number;
  attempt: number;
  material: QuizReadingMaterial;
  completed: boolean;
  startedAt: string | null;
  completedAt: string | null;
  timeTaken: string | null;
  verifiedSeconds: number | null;
}

// POST generation is idempotent on the backend, but React StrictMode mounts
// effects twice in development. Share the in-flight call so one page visit
// never starts two expensive AI generations for the same attempt.
const pendingReadingGenerations = new Map<string, Promise<QuizReading>>();

async function request<T>(url: string, init?: globalThis.RequestInit): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, { ...init, headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  let res: Response;
  try {
    res = await learningFetch(url, { headers: { 'Content-Type': 'application/json' }, ...init });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const message = (data && data.error) || `Request failed (${res.status})`;
    // 409 + validation is the working-rules refusal: the attempt was neither
    // graded nor stored, so the caller opens the completion date/time dialog
    // and re-posts. Any other 409 (a reused timing session) stays an error.
    if (res.status === 409 && data && data.validation) {
      throw new CompletionValidationError(
        message,
        (data.validation.reason || '') as WorkingRuleReason | '',
        data.validation.holidayName || '',
      );
    }
    throw new Error(message);
  }
  return data as T;
}

export function fetchQuiz(quizId: number, learnerId?: string): Promise<Quiz> {
  return request<Quiz>(`${BASE}/${quizId}/${learnerId?.startsWith('pl.') ? `?learnerId=${encodeURIComponent(learnerId)}` : ''}`);
}

export function submitQuizAttempt(
  quizId: number,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  submission: QuizSubmission,
): Promise<QuizAttemptResult> {
  return request<QuizAttemptResult>(`${BASE}/${quizId}/submit/?kind=${kind}&learnerId=${learnerId}`, {
    method: 'POST',
    body: JSON.stringify(submission),
  }).then((result) => {
    invalidateLearnerDetailCache(kind, learnerId, { quizId });
    return result;
  });
}

function quizReadingUrl(quizId: number, kind: 'commercial' | 'apprenticeship', learnerId: string, attempt: number) {
  return `${BASE}/${quizId}/reading/?kind=${kind}&learnerId=${encodeURIComponent(learnerId)}&attempt=${attempt}`;
}

export function generateQuizReading(
  quizId: number,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  attempt: number,
): Promise<QuizReading> {
  const url = quizReadingUrl(quizId, kind, learnerId, attempt);
  const existing = pendingReadingGenerations.get(url);
  if (existing) return existing;
  const pending = request<QuizReading>(url, {
    method: 'POST',
    body: JSON.stringify({ action: 'generate' }),
  }).finally(() => {
    if (pendingReadingGenerations.get(url) === pending) pendingReadingGenerations.delete(url);
  });
  pendingReadingGenerations.set(url, pending);
  return pending;
}

export function completeQuizReading(
  quizId: number,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  attempt: number,
  trackingToken: string,
  timeTakenSeconds: number,
  timeEntryMode: 'timer' | 'manual',
): Promise<QuizReading> {
  return request<QuizReading>(quizReadingUrl(quizId, kind, learnerId, attempt), {
    method: 'POST',
    body: JSON.stringify({ action: 'complete', trackingToken, timeTakenSeconds, timeEntryMode }),
  }).then((reading) => {
    invalidateLearnerDetailCache(kind, learnerId, { quizId });
    return reading;
  });
}
