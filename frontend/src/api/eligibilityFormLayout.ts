// ============================================================================
// Eligibility form layout API client (staff)
//
// The layout the "Edit eligibility form" builder publishes
// (enrolment."Eligibility_Form_Layouts"), with who published it and when, and
// its publish. The public form reads the layout in force through
// fetchEligibilityForm (apprenticeshipApplication.ts) instead, which carries no
// names. See enrolment_api/eligibility_layout.py.
// ============================================================================
import type { EligibilityLayout, EligibilityLayoutResponse } from '@/pages/apprenticeship-application/layout/types';

const BASE = '/enrolment_api/eligibility-form-layout';

async function send<T>(url: string, init?: Parameters<typeof fetch>[1]): Promise<T> {
  let res: Response;
  try {
    // credentials: 'include' sends the session cookie the enrolment API requires.
    res = await fetch(url, { credentials: 'include', ...init, headers: { 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const message = data && typeof data === 'object' && 'error' in data ? String((data as { error: unknown }).error) : '';
    throw Object.assign(new Error(message || `Request failed (${res.status})`), { status: res.status });
  }
  return data as T;
}

export function fetchEligibilityLayout(): Promise<EligibilityLayoutResponse> {
  return send<EligibilityLayoutResponse>(`${BASE}/`);
}

/**
 * Publish a new layout. `baseVersion` is the version the editor started from:
 * the server refuses (409) when someone else published in between, rather than
 * silently overwriting their changes.
 */
export function publishEligibilityLayout(layout: EligibilityLayout, baseVersion: number | null): Promise<EligibilityLayoutResponse> {
  return send<EligibilityLayoutResponse>(`${BASE}/publish/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layout, baseVersion }),
  });
}
