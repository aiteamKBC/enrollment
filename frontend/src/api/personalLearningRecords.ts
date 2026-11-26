import type { PlrRecord, PlrState } from '@/pages/users/types';
import { invalidateWizardCache, type LearnerKind } from './extendedIlr';

export interface PlrResponse extends PlrState {
  canManage?: boolean;
  csrfToken?: string;
  deletedRefs?: string[];
  message?: string;
  added?: number;
  skipped?: number;
}
export class PlrRequestError extends Error {
  code?: string;
  constructor(message: string, code?: string) { super(message); this.code = code; }
}
const base = (kind: LearnerKind, id: string) => `/enrolment_api/wizard/${kind}/${encodeURIComponent(id)}/plr/`;
async function send(kind: LearnerKind, id: string, init?: RequestInit): Promise<PlrResponse> {
  const response = await fetch(base(kind, id), { credentials: 'include', ...init });
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('PLR service is unavailable. Please try again.');
  const data = await response.json();
  if (!response.ok) throw new PlrRequestError(data.error || 'The PLR operation failed.', data.code);
  if (init?.method) invalidateWizardCache(kind, id);
  return data;
}
export const fetchPlr = (kind: LearnerKind, id: string) => send(kind, id);
export function writePlr(kind: LearnerKind, id: string, csrfToken: string, method: 'PUT' | 'PATCH' | 'DELETE', data: { uln?: string; revision?: number; id?: string; record?: PlrRecord }) {
  return send(kind, id, { method, headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken }, body: JSON.stringify(data) });
}
export function importPlrPdf(kind: LearnerKind, id: string, csrfToken: string, file: File, enteredUln: string, identityReviewed = false) {
  const body = new FormData();
  body.append('file', file); body.append('enteredUln', enteredUln); body.append('identityReviewed', String(identityReviewed));
  return send(kind, id, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body });
}
