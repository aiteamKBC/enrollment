import type { LearnerCalendarEvent, LearnerReviewDefinition } from './learnerCalendar';

/** Only unfinished Aptem PSPs need a working copy; imported history stays intact. */
export function needsPersonalSupportForm(event: LearnerCalendarEvent | null): boolean {
  if (!event || event.migratedForm || !event.eventKey.startsWith('imported-review:')) return false;
  const type = event.importedReviewType || event.importedReview?.type || event.reviewTypeName;
  const normalize = (value?: string) => (value || '').trim().toLowerCase().replaceAll(' ', '-');
  const editable = ['not-scheduled', 'scheduled', 'in-progress'];
  return type?.trim().toLowerCase() === 'personal support plan'
    && editable.includes(normalize(event.status))
    && editable.includes(normalize(event.sourceStatus || event.importedReview?.status || event.status))
    && !event.reviewCompletedAt && !event.importedReview?.completedDate;
}

export async function prepareLearnerPersonalSupport(eventKey: string, signal?: AbortSignal): Promise<LearnerReviewDefinition> {
  const csrfResponse = await fetch('/learner_api/migrated-reviews/csrf', { credentials: 'include', signal });
  const csrf = await csrfResponse.json() as { csrfToken?: string };
  if (!csrfResponse.ok || !csrf.csrfToken) throw new Error('Unable to verify this review request. Please try again.');
  const response = await fetch(`/learner_api/migrated-reviews/${encodeURIComponent(eventKey)}/prepare-psp`, {
    method: 'POST', credentials: 'include', signal,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrfToken },
    body: '{}',
  });
  const data = await response.json() as LearnerReviewDefinition & { detail?: string };
  if (!response.ok) throw new Error(data.detail || `Could not load the Personal Support Plan (${response.status}).`);
  return data;
}
