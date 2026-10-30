export interface LearnerEntry {
  classification: 'existing' | 'new' | 'not_applicable';
  enabled?: boolean;
  required: boolean;
  canAccess: boolean;
  completedMonths?: number;
  totalMonths?: number;
  document?: { title: string; version: string; through: string };
}

/** No shared read cache: only a fresh, authenticated server response opens the gate. */
export async function fetchLearnerEntry(signal?: AbortSignal): Promise<LearnerEntry> {
  const response = await fetch('/login_api/learner-entry/', {
    credentials: 'include', cache: 'no-store', signal,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error || 'We could not check your learning access. Please try again.');
  if (!body || !['existing', 'new'].includes(body.classification)
      || typeof body.canAccess !== 'boolean' || typeof body.required !== 'boolean'
      || body.canAccess === body.required) {
    throw new Error('We could not verify your learner profile. Please try again.');
  }
  return body;
}
