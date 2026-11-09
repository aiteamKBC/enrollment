import type { PreviewResponse } from './types';

/** Dispatch to the existing document generators, without issuing or signing. */
export async function previewPdf(value: PreviewResponse): Promise<Blob> {
  if (value.pdfUnavailable) throw new Error(value.pdfUnavailable);
  if (value.pdfBase64) return new Blob([Uint8Array.from(atob(value.pdfBase64), ch => ch.charCodeAt(0))], {type:'application/pdf'});
  if (value.pdfUrl) {
    const response = await fetch(value.pdfUrl, {credentials:'include',cache:'no-store'});
    if (!response.ok) throw new Error('The saved document PDF is unavailable. Please retry.');
    return response.blob();
  }
  const data = value.data;
  switch (value.template.content.documentKey) {
    case 'apprenticeship-agreement': return (await import('@/lib/apprenticeshipAgreementPdf')).renderAgreementPdf(data as any).output('blob');
    case 'ilr': return (await import('@/lib/ilrDocumentPdf')).renderIlrPdf(data as any).output('blob');
    case 'training-plan': return (await import('@/lib/trainingPlanPdf')).renderTrainingPlanPdf(data as any).output('blob');
    case 'written-agreement': return (await import('@/lib/writtenAgreementPdf')).renderWrittenAgreementPdf(data as any).output('blob');
    default: {
      const [{ buildReviewPdf }, { REVIEW_QUESTION_LABELS }] = await Promise.all([
        import('@/pages/learner/onboarding/reviews/reviewDocument'), import('@/pages/learner/onboarding/reviews/questions'),
      ]);
      return buildReviewPdf(data as any, REVIEW_QUESTION_LABELS).output('blob');
    }
  }
}
