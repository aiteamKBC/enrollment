/** Carry enrolment identity in the URL so refresh/direct entry retains it. */
export function enrolmentReviewPath(learnerId: string, eventKey: string) {
  return `/users/${encodeURIComponent(learnerId)}/reviews/${encodeURIComponent(eventKey)}?admin=1`;
}

export function reviewReturnPath(learnerId?: string) {
  return learnerId ? `/users/${encodeURIComponent(learnerId)}?tab=documents` : '/learner/onboarding/reviews';
}
