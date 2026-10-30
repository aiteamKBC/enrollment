import { createCachedResource } from './cachedRequest';
import { readLearnerJson, subscribeLearnerReadInvalidation } from './learnerRead';
import type { LearnerKind } from './learnerDetail';
import type { LearnerCalendarResponse } from './learnerCalendar';

const sessionsResource = createCachedResource<LearnerCalendarResponse>('learner-review-sessions', key => {
  const [kind, id, content] = key.split(':');
  return readLearnerJson(`/learner_api/review-sessions/${kind}/${id}/?content=${content}`);
});
subscribeLearnerReadInvalidation(() => sessionsResource.invalidate());

export function fetchLearnerReviewSessions(kind: LearnerKind, id: string, summary: boolean) {
  // Summary and full history must never share a cached payload. Revalidation
  // shares in-flight reads while observing bookings and remote review writes.
  return sessionsResource.read(`${kind}:${id}:${summary ? 'summary' : 'full'}`, { revalidate: true });
}
