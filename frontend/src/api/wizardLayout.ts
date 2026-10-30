// ============================================================================
// Enrolment wizard layout API client
//
// The layout the wizard builder publishes (enrolment."Wizard_Form_Layouts"),
// read by every wizard — staff and learner — and the files learners upload to
// the builder's custom "File upload" fields. See enrolment_api/wizard_layout.py.
// ============================================================================
import type { LearnerKind } from './extendedIlr';
import { createCachedResource } from './cachedRequest';
import type { CustomUploadRef, WizardLayout, WizardLayoutResponse } from '@/pages/users/wizard/layout/types';

const BASE = '/enrolment_api/wizard-layout';

async function send<T>(url: string, init?: Parameters<typeof fetch>[1]): Promise<T> {
  let res: Response;
  try {
    // credentials: 'include' sends the session cookie the enrolment API requires.
    res = await fetch(url, { credentials: 'include', ...init });
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

// One layout for everybody, so one cache key.
const layoutResource = createCachedResource<WizardLayoutResponse>('wizard-layout', () => send<WizardLayoutResponse>(`${BASE}/`));

export function fetchWizardLayout(options: { force?: boolean } = {}): Promise<WizardLayoutResponse> {
  return layoutResource.read('current', options);
}

/** The layout already in memory, if any — lets the wizard render it on its first frame. */
export function peekWizardLayout(): WizardLayoutResponse | undefined {
  return layoutResource.peek('current');
}

/**
 * Publish a new layout. `baseVersion` is the version the editor started from:
 * the server refuses (409) when someone else published in between, rather than
 * silently overwriting their changes.
 */
export async function publishWizardLayout(layout: WizardLayout, baseVersion: number | null): Promise<WizardLayoutResponse> {
  const saved = await send<WizardLayoutResponse>(`${BASE}/publish/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layout, baseVersion }),
  });
  layoutResource.prime('current', saved);
  return saved;
}

// ── custom upload fields ────────────────────────────────────────────────────

export interface CustomUpload extends CustomUploadRef {
  contentType: string;
  sizeBytes: number | null;
  uploadedAt: string | null;
}

const uploadBase = (kind: LearnerKind, learnerId: string, fieldKey: string) =>
  `/enrolment_api/wizard/${kind}/${learnerId}/custom-uploads/${encodeURIComponent(fieldKey)}`;

export async function fetchCustomUploads(kind: LearnerKind, learnerId: string, fieldKey: string): Promise<CustomUpload[]> {
  const data = await send<{ results: CustomUpload[] }>(`${uploadBase(kind, learnerId, fieldKey)}/`);
  return data.results;
}

/** Multipart, so no Content-Type header: the browser sets the boundary. */
export function uploadCustomFile(kind: LearnerKind, learnerId: string, fieldKey: string, file: File): Promise<CustomUpload> {
  const body = new FormData();
  body.append('file', file, file.name);
  return send<CustomUpload>(`${uploadBase(kind, learnerId, fieldKey)}/`, { method: 'POST', body });
}

export async function deleteCustomUpload(kind: LearnerKind, learnerId: string, fieldKey: string, fileId: string): Promise<void> {
  await send<{ deleted: boolean }>(`${uploadBase(kind, learnerId, fieldKey)}/${fileId}/`, { method: 'DELETE' });
}

/** A short-lived SAS URL for one stored file. */
export async function getCustomUploadUrl(kind: LearnerKind, learnerId: string, fieldKey: string, fileId: string): Promise<string> {
  const data = await send<{ url: string }>(`${uploadBase(kind, learnerId, fieldKey)}/${fileId}/download/`);
  return data.url;
}
