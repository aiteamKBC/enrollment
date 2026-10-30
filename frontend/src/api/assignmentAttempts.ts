export interface SubmissionAttempt {
  number: number;
  status: string;
  submittedAt: string | null;
  reviewedAt: string | null;
  reviewedBy: string;
  coachFeedback: string;
  answer: string;
  actualTimeHours?: string | number | null;
}
