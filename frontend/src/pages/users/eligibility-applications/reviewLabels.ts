import type { ReviewStatus } from '@/api/eligibilityApplications';

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  approved: 'Approved — awaiting invitation', rejected: 'Rejected', more_evidence_required: 'More evidence required',
};
