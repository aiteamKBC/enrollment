type ReviewCategoryEvent = {
  source?: string | null;
  importedReviewType?: string | null;
  reviewTypeCode?: string | null;
};

/** Display classification only. Keep source/event keys intact for bookings and forms. */
export function reviewCategory(event: ReviewCategoryEvent): string {
  if (event.importedReviewType != null) {
    const type = event.importedReviewType.trim().toLowerCase();
    if (['monthly coaching meeting', 'monthly coaching', 'mcm'].includes(type)) return 'mcr';
    if (['progress review', 'progress review (+ skills radar)'].includes(type)) return 'progress-review';
    return 'review';
  }
  const code = event.reviewTypeCode?.trim().toLowerCase();
  if (code) return code === 'mcm' ? 'mcr' : code === 'progress_review' ? 'progress-review' : 'review';
  return event.source || '';
}
