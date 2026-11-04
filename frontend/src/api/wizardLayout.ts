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
import { prefetchAssessmentSecurity } from '@/features/programme-suitability/api';

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

const layoutResource = createCachedResource<WizardLayoutResponse>('wizard-layout', (key) => send<WizardLayoutResponse>(
  key === 'current' ? `${BASE}/` : `${BASE}/${key}/`,
));

export function fetchWizardLayout(options: { force?: boolean; kind?: LearnerKind; learnerId?: string } = {}): Promise<WizardLayoutResponse> {
  const key = options.kind && options.learnerId ? `${options.kind}/${encodeURIComponent(options.learnerId)}` : 'current';
  return layoutResource.read(key, options);
}

/** The layout already in memory, if any — lets the wizard render it on its first frame. */
export function peekWizardLayout(options: { kind?: LearnerKind; learnerId?: string } = {}): WizardLayoutResponse | undefined {
  const key = options.kind && options.learnerId ? `${options.kind}/${encodeURIComponent(options.learnerId)}` : 'current';
  return layoutResource.peek(key);
}

/**
 * Publish a new layout. `baseVersion` is the version the editor started from:
 * the server refuses (409) when someone else published in between, rather than
 * silently overwriting their changes.
 */
export interface PublishWizardLayoutOptions {
  scope?: 'default' | 'targeted';
  name?: string;
  parentVersion?: number | null;
  assignTo?: { learnerKind: LearnerKind; learnerIds: string[]; reason?: string };
}

export async function publishWizardLayout(layout: WizardLayout, baseVersion: number | null, options: PublishWizardLayoutOptions = {}): Promise<WizardLayoutResponse> {
  const security = layout.skillsRadarCatalogues || options.scope === 'targeted'
    ? { 'X-CSRFToken': (await prefetchAssessmentSecurity()).csrfToken }
    : {};
  const saved = await send<WizardLayoutResponse>(`${BASE}/publish/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...security },
    body: JSON.stringify({ layout, baseVersion, ...options }),
  });
  if ((options.scope ?? 'default') === 'default') layoutResource.invalidate('current');
  return saved;
}

export interface WizardLayoutVersion {
  version: number;
  scope: 'default' | 'targeted';
  name: string;
  parentVersion: number | null;
  createdAt: string;
  createdBy: string;
}

export async function fetchWizardVersions(): Promise<WizardLayoutVersion[]> {
  const data = await send<{ results: WizardLayoutVersion[] }>(`${BASE}/versions/`);
  return data.results;
}

export async function assignWizardVersion(input: {
  learnerKind: LearnerKind;
  learnerIds: string[];
  version: number | null;
  reason?: string;
}): Promise<{ assigned: number; version: number | null; cleared: boolean }> {
  const security = { 'X-CSRFToken': (await prefetchAssessmentSecurity()).csrfToken };
  return send<{ assigned: number; version: number | null; cleared: boolean }>(`${BASE}/assignments/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...security },
    body: JSON.stringify({ ...input, clear: input.version === null }),
  });
}

/** Changes global field visibility without publishing a layout version. */
export async function setWizardFieldVisibility(version: number, fieldKey: string, hidden: boolean): Promise<void> {
  const security = { 'X-CSRFToken': (await prefetchAssessmentSecurity()).csrfToken };
  await send(`${BASE}/visibility/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...security },
    body: JSON.stringify({ version, fieldKey, hidden }),
  });
  layoutResource.invalidate();
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
