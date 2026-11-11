import { createCachedResource } from '@/api/cachedRequest';

export interface PendingSignature {
  id: string; documentKey: string; documentId: string; revisionId: string | null;
  revisionNumber: number; title: string; category: 'Compliance' | 'Review';
  templateVersion: number | null; issuedAt: string | null;
  status: 'pending'; requiresLearnerSignature: true; href: string;
}
export interface PendingSignatures { count: number; results: PendingSignature[] }

export async function fetchPendingSignatures(): Promise<PendingSignatures> {
  const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch('/enrolment_api/pending-signatures/', { credentials: 'include', cache: 'no-store', signal: controller.signal });
    let body;
    try { body = await response.json(); } catch { throw new Error('Could not load signature requests. Please retry.'); }
    if (!response.ok) throw Object.assign(new Error(body?.error || 'Could not load signature requests.'), { status: response.status });
    if (!Array.isArray(body.results) || !Number.isInteger(body.count) || body.count !== body.results.length) throw new Error('Could not load signature requests. Please retry.');
    return body;
  } catch (error) {
    if (controller.signal.aborted) throw new Error('Loading signature requests is taking longer than expected. Please retry.');
    if (error instanceof TypeError) throw new Error('Could not reach the server. Please check your connection and retry.');
    throw error;
  } finally { window.clearTimeout(timer); }
}

// The cache key includes the authenticated account AND learner. The backend
// derives ownership from its session; it never trusts either client identifier.
export const pendingSignatureResource = createCachedResource<PendingSignatures>('learner-pending-signatures', fetchPendingSignatures, 10000);
