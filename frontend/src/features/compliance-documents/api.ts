import type { DocumentKey, DocumentTemplate, DraftSaved, PreviewResponse, TemplateDetail, TemplateIndex, TemplateVersion } from './types';

const BASE = '/enrolment_api/document-templates';
let csrf: string | null = null;
async function token() {
  if (!csrf) {
    const response = await fetch('/learner_api/enrollment-csrf/', { credentials: 'include' });
    if (!response.ok) throw new Error('Could not initialise the secure document request.');
    csrf = (await response.json()).csrfToken;
  }
  return csrf!;
}
export async function request<T>(url: string, method = 'GET', data?: unknown, retry = true): Promise<T> {
  const write = method !== 'GET';
  const response = await fetch(url, { method, credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(write ? { 'X-CSRFToken': await token() } : {}) },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  const text = await response.text(); let value;
  try { value = JSON.parse(text); } catch { value = null; }
  if (response.status === 403 && /csrf|origin checking/i.test(text) && write && retry) {
    csrf = null; return request<T>(url, method, data, false);
  }
  if (!response.ok) throw Object.assign(new Error(value?.error || `Document template request failed (${response.status}).`), { status: response.status, details: value });
  return value;
}
export const listTemplates = () => request<TemplateIndex>(`${BASE}/`);
export const readTemplate = (id: string) => request<TemplateDetail>(`${BASE}/${encodeURIComponent(id)}/`);
export const readFields = (key: DocumentKey) => request<{ results: import('./types').FieldDefinition[] }>(`${BASE}/fields/${key}/`);
export const saveTemplate = (content: DocumentTemplate, baseVersion: string, revision: number) =>
  request<DraftSaved>(`${BASE}/${encodeURIComponent(content.templateId)}/`, 'PUT', { content, baseVersion, revision });
export const deleteDraft = (id: string, baseVersion: string, revision: number) => request(`${BASE}/${encodeURIComponent(id)}/`, 'DELETE', { baseVersion, revision });
export const previewTemplate = (id: string, revision: number, learnerId?: number, version?: string) =>
  request<PreviewResponse>(`${BASE}/${encodeURIComponent(id)}/preview/`, 'POST', { revision, ...(learnerId ? { learnerId } : {}), ...(version ? { version } : {}) });
export const publishTemplate = (id: string, revision: number, previewToken: string, reviewConfirmed: boolean, approvalNote: string) =>
  request<{ published: TemplateVersion }>(`${BASE}/${encodeURIComponent(id)}/publish/`, 'POST', { revision, previewToken, reviewConfirmed, approvalNote });
export const templateHistory = (id: string) => request<{ results: TemplateVersion[] }>(`${BASE}/${encodeURIComponent(id)}/history/`);
