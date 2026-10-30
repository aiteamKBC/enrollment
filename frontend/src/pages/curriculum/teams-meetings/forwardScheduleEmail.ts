import { coachFetch } from '@/lib/coachFetch';

export interface ForwardScheduleEmailStatus {
  total: number;
  accepted: number;
  queued: number;
  failed: number;
  uncertain: number;
  status: 'complete' | 'pending';
}

export async function forwardScheduleEmail(liveSessionId: string, recipients: string[]): Promise<ForwardScheduleEmailStatus> {
  const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
  const response = await coachFetch(`${base}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/forward-email/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recipients }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The schedule could not be forwarded.');
  if (!['total', 'accepted', 'queued', 'failed', 'uncertain'].every(key => Number.isInteger(result[key]) && result[key] >= 0)
    || result.total !== result.accepted + result.queued + result.failed + result.uncertain
    || !['complete', 'pending'].includes(result.status)) {
    throw new Error('Forwarding status could not be read.');
  }
  return result as ForwardScheduleEmailStatus;
}
