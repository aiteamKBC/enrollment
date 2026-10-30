export interface ReviewHandover {
  transferred: string[];
  unchanged: string[];
  historical: string[];
  uninitialized: string[];
  blocked: { reviewId?: string; code: string }[];
}

export function reviewHandoverMessage(result?: ReviewHandover): string | null {
  if (!result?.blocked.length) return null;
  const reasons = new Set(result.blocked.map(item => item.code));
  const detail = reasons.has('shared_database_verification_required')
    ? 'Review ownership could not be transferred until the database configuration is verified.'
    : reasons.has('booking_handover_required')
      ? 'Existing meeting bookings need a separate handover. Their links, organizers and Review ownership were retained.'
      : 'Some Reviews need a manual handover because their ownership, lifecycle or signed evidence could not be safely transferred.';
  return `Coach assignment saved. ${result.transferred.length} Review(s) transferred. ${detail}`;
}
