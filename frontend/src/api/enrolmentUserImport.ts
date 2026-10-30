import type { UserListRow } from '@/pages/users/types';

const BASE = '/learner_api/enrolment-users';

export const LEARNER_IMPORT_MAX_BYTES = 5 * 1024 * 1024;
export const LEARNER_IMPORT_MAX_ROWS = 500;

export interface LearnerImportResult {
  count: number;
  imported: number;
  results: UserListRow[];
  errors: { row: number; field?: string; message: string }[];
  preview: { row: number; name: string; email: string; programme: string; cohort: string; group: string }[];
}

export class LearnerImportError extends Error {
  readonly result?: LearnerImportResult;

  constructor(message: string, result?: LearnerImportResult) {
    super(message);
    this.name = 'LearnerImportError';
    this.result = result;
  }
}

async function readImportResponse(response: Response): Promise<LearnerImportResult> {
  let data: LearnerImportResult & { error?: string };
  try {
    data = await response.json();
  } catch {
    throw new LearnerImportError(`The server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) {
    const result = Array.isArray(data?.errors) && Array.isArray(data?.preview) ? data : undefined;
    throw new LearnerImportError(data?.error || `Request failed (${response.status}).`, result);
  }
  return data;
}

/** Validate first; only an explicit dryRun=false request creates learners. */
export async function importEnrolmentUsers(file: File, dryRun: boolean): Promise<LearnerImportResult> {
  const body = new FormData();
  body.append('file', file);
  body.append('dryRun', String(dryRun));
  let response: Response;
  try {
    response = await fetch(`${BASE}/import/`, {
      method: 'POST',
      credentials: 'include',
      // The browser must supply Content-Type with its multipart boundary.
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body,
    });
  } catch {
    throw new LearnerImportError(dryRun
      ? 'Could not reach the server. Please try validating the file again.'
      : 'Could not confirm whether the import completed. Refresh the directory before trying again.');
  }
  return readImportResponse(response);
}

export async function fetchEnrolmentUserTemplate(): Promise<Blob> {
  const response = await fetch(`${BASE}/import-template/`, {
    credentials: 'include',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  if (!response.ok) {
    let message = `Could not download the template (${response.status}).`;
    try {
      const data: { error?: string } = await response.json();
      message = data.error || message;
    } catch { /* Keep the HTTP error when the server returns HTML. */ }
    throw new Error(message);
  }
  return response.blob();
}
