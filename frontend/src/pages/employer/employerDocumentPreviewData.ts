import { fetchEmployerEnrolmentReview, fetchEmployerReviewInstance } from '@/api/employerPortal';
import { fetchMigratedReviewForParty, type LearnerReviewDefinition } from '@/api/learnerCalendar';
import type { Agreement } from '@/api/apprenticeshipAgreement';
import type { TrainingPlanDocument } from '@/api/trainingPlanDocument';
import type { WrittenAgreementDocument } from '@/api/writtenAgreement';
import type { SigningTarget } from './useEmployerSigning';

export type DocumentPreview =
  | { kind: 'pdf'; blob: Blob; filename: string }
  | { kind: 'review'; definition: LearnerReviewDefinition; unavailableReason: string };

type StoredDocument =
  | { kind: 'agreement'; document: Agreement }
  | { kind: 'training-plan'; document: TrainingPlanDocument }
  | { kind: 'written-agreement'; document: WrittenAgreementDocument }
  | { kind: 'document'; url: string };

async function checkedResponse(url: string, signal: AbortSignal, credentials: RequestCredentials = 'include') {
  const response = await fetch(url, { credentials, signal });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || error.error || 'Could not load the document. Please try again.');
  }
  return response;
}

async function readPdf(url: string, filename: string, signal: AbortSignal, credentials?: RequestCredentials): Promise<DocumentPreview> {
  const response = await checkedResponse(url, signal, credentials);
  if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('application/pdf')) {
    throw new Error('The server did not return a PDF. Please try again.');
  }
  return {
    kind: 'pdf', blob: await response.blob(),
    filename: response.headers.get('Content-Disposition')?.match(/filename="([A-Za-z0-9_.-]+)"/)?.[1] || filename,
  };
}

/** Reads only: opening a preview never signs, submits, issues or downloads a file. */
export async function loadEmployerDocumentPreview(employerId: string, target: SigningTarget, signal: AbortSignal): Promise<DocumentPreview> {
  const { item, kind, learnerId } = target;
  const base = `/learner_api/employer-portal/${employerId}/learner/${kind}/${learnerId}`;
  const filename = `${item.label.replace(/[^a-z0-9 _-]/gi, '').trim() || 'Document'}.pdf`;
  if (item.kind === 'review') {
    const eventKey = encodeURIComponent(item.eventKey);
    if (item.migratedForm && item.completed) {
      // Keep the immutable signed artefact; never substitute a regenerated draft.
      return readPdf(`/learner_api/migrated-reviews/${eventKey}/party-pdf`, filename, signal);
    }
    if (item.migratedForm || item.reviewInstanceId) {
      const definition = item.migratedForm
        ? await fetchMigratedReviewForParty(item.eventKey, signal)
        : await fetchEmployerReviewInstance(employerId, kind, learnerId, item.eventKey) as LearnerReviewDefinition;
      if (!definition) throw new Error('The review could not be loaded. Please try again.');
      if (!item.migratedForm && definition.pdf?.available) {
        return readPdf(`${base}/events/${eventKey}/review/pdf/`, filename, signal);
      }
      return {
        kind: 'review', definition,
        unavailableReason: definition.pdf?.reason || (item.migratedForm
          ? 'The final PDF is available after the review is completed and all required parties have signed.'
          : 'A downloadable PDF is not available for this review yet.'),
      };
    }
    const review = await fetchEmployerEnrolmentReview(employerId, kind, learnerId, item.eventKey);
    const [{ buildReviewPdf, reviewDocumentFilename }, { REVIEW_QUESTION_LABELS }] = await Promise.all([
      import('@/pages/learner/onboarding/reviews/reviewDocument'),
      import('@/pages/learner/onboarding/reviews/questions'),
    ]);
    return { kind: 'pdf', blob: buildReviewPdf(review, REVIEW_QUESTION_LABELS).output('blob'), filename: reviewDocumentFilename(review) };
  }
  const response = await checkedResponse(`${base}/documents/${item.kind}/${encodeURIComponent(item.id)}/`, signal);
  const stored = await response.json() as StoredDocument;
  if (stored.kind !== item.kind) throw new Error('The document type did not match. Please reload the list.');
  if (stored.kind === 'document') return readPdf(stored.url, filename, signal, 'omit');
  if (stored.kind === 'agreement') {
    const { renderAgreementPdf } = await import('@/lib/apprenticeshipAgreementPdf');
    return { kind: 'pdf', blob: renderAgreementPdf(stored.document).output('blob'), filename };
  }
  if (stored.kind === 'training-plan') {
    const { renderTrainingPlanPdf } = await import('@/lib/trainingPlanPdf');
    return { kind: 'pdf', blob: renderTrainingPlanPdf(stored.document).output('blob'), filename };
  }
  const { renderWrittenAgreementPdf } = await import('@/lib/writtenAgreementPdf');
  return { kind: 'pdf', blob: renderWrittenAgreementPdf(stored.document).output('blob'), filename };
}
