import { readLearnerJson } from './learnerRead';
// ============================================================================
// Extended ILR API client
// Talks to the Django backend at /enrolment_api (proxied to :8000 by Vite in dev).
//
// The questionnaire is stored as one jsonb document, so this client posts the
// wizard's IlrForm as-is and the server derives the signature/completion flags.
// ============================================================================
import type { EnrolmentBoard, IlrForm, WizardDraft } from '@/pages/users/types';
import { primeKsbProfile, type KsbProfileResponse } from './curriculum';
import { createCachedResource } from './cachedRequest';
import type { LearnerKind } from './learnerDetail';

export type { LearnerKind } from './learnerDetail';

const BASE = '/enrolment_api/extended-ilr';
const BOOTSTRAP_BASE = '/enrolment_api/wizard-bootstrap';

/** Every wizard step except the ILR, which travels in `answers`. */
export type WizardDraftRest = Omit<WizardDraft, 'ilr'>;

export interface ExtendedIlrMeta {
  learnerKind: LearnerKind;
  learnerId: number;
  learnerName: string;
  learnerSigned: boolean;
  learnerSignedDate: string;
  providerSigned: boolean;
  providerSignedDate: string;
  completed: boolean;
  updatedAt: string;
}

export interface ExtendedIlrResponse {
  /** null when the learner has no saved ILR yet — the form opens blank. */
  answers: IlrForm | null;
  /** The wizard's other steps; null/{} when nothing has been saved yet. */
  draft: Partial<WizardDraftRest> | null;
  meta: ExtendedIlrMeta;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, { ...init, headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  let res: Response;
  try {
    // credentials: 'include' sends the session cookie — the enrolment API now
    // requires an authenticated user (see enrolment_api/auth.py).
    res = await fetch(url, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...init });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

/** Board + ILR + KSB profile in one response — what the wizard needs to open. */
export interface WizardBootstrapResponse {
  board: EnrolmentBoard;
  ilr: ExtendedIlrResponse;
  /**
   * The learner's programme profile, primed into the curriculum cache below.
   * Null when the learner has no programme, and absent from responses served by
   * a backend older than this field — hence optional.
   */
  ksbProfile?: KsbProfileResponse | null;
}

const bootstrapResource = createCachedResource<WizardBootstrapResponse>(
  'wizard-bootstrap',
  (key) => request<WizardBootstrapResponse>(`${BOOTSTRAP_BASE}/${key}/`),
);

const ilrResource = createCachedResource<ExtendedIlrResponse>(
  'extended-ilr',
  (key) => request<ExtendedIlrResponse>(`${BASE}/${key}/`),
);

/**
 * Everything the wizard needs to open, in one round-trip and once per learner.
 *
 * The board and the ILR used to be two requests fired from two components, each
 * doubled by StrictMode — four for one page. Both halves are primed into their
 * own caches too, so anything that later asks for just the ILR is already served.
 */
export async function fetchWizardBootstrap(
  kind: LearnerKind,
  learnerId: string,
  options: { force?: boolean } = {},
): Promise<WizardBootstrapResponse> {
  const key = `${kind}/${learnerId}`;
  const data = await bootstrapResource.read(key, options);
  ilrResource.prime(key, data.ilr);
  // Keyed on the programme, not the learner — that is how the curriculum cache
  // holds it (one profile serves every learner on a programme). This is what
  // lets the wizard's seeding resolve without a request of its own.
  const programme = data.board?.programme?.name;
  if (programme && data.ksbProfile) primeKsbProfile(programme, data.ksbProfile);
  return data;
}

export function fetchExtendedIlr(
  kind: LearnerKind,
  learnerId: string,
  options: { force?: boolean } = {},
): Promise<ExtendedIlrResponse> {
  return ilrResource.read(`${kind}/${learnerId}`, options);
}

/**
 * The saved answers already in memory, or undefined.
 *
 * Lets the wizard hydrate on its first frame when the bootstrap (or a previous
 * visit) has them, rather than showing "Loading your answers…" for one paint
 * before identical state arrives from a microtask.
 */
export function peekExtendedIlr(
  kind: LearnerKind,
  learnerId: string,
): ExtendedIlrResponse | undefined {
  return ilrResource.peek(`${kind}/${learnerId}`);
}

/**
 * Forget this learner's cached wizard payloads.
 *
 * Anything writing learner data that the board or ILR reflects must call this,
 * or the next read serves a stale copy for the rest of the TTL.
 */
export function invalidateWizardCache(kind: LearnerKind, learnerId: string): void {
  const key = `${kind}/${learnerId}`;
  bootstrapResource.invalidate(key);
  ilrResource.invalidate(key);
}

/**
 * Forget a learner's wizard payloads when only their id is known.
 *
 * The board endpoints are addressed by id alone (ids are unique across both
 * learner tables) while these caches are keyed by kind too, so a write that
 * doesn't know the kind has to clear both — cheap, and far better than a
 * missed invalidation serving a stale board.
 */
export function invalidateWizardCacheById(learnerId: string): void {
  invalidateWizardCache('apprenticeship', learnerId);
  invalidateWizardCache('commercial', learnerId);
}

/**
 * Persist the wizard. `draft` carries every non-ILR step; omit it and any stored
 * draft is left as-is rather than cleared.
 */
export async function saveExtendedIlr(
  kind: LearnerKind,
  learnerId: string,
  answers: IlrForm,
  draft?: WizardDraftRest
): Promise<ExtendedIlrResponse> {
  const key = `${kind}/${learnerId}`;
  const saved = await request<ExtendedIlrResponse>(`${BASE}/${key}/`, {
    method: 'PUT',
    body: JSON.stringify(draft ? { answers, draft } : { answers }),
  });
  // The server echoes the stored row, so the cache is updated from the response
  // rather than dropped — a save leaves the next read served from memory instead
  // of costing a refetch of what we just sent.
  ilrResource.prime(key, saved);
  // The bootstrap's copy of the ILR is now behind, and its board may be too
  // (the wizard patches learner fields on finish), so it is dropped outright.
  bootstrapResource.invalidate(key);
  return saved;
}

// ── Eligibility evidence ────────────────────────────────────────────────────
// Proof of identification/residency for the ILR's Eligibility section. Files go
// to Azure through quarantine -> scan -> approved, and each one's blob path is
// recorded on the learner's Extended_ILR row (see ilr_eligibility_evidence.py).

export interface IlrEvidenceFile {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes: number | null;
  uploadedAt: string | null;
}

export interface IlrEvidenceListing {
  results: IlrEvidenceFile[];
  /** True once the ILR is signed by both parties: files can no longer change. */
  locked: boolean;
}

const evidenceBase = (kind: LearnerKind, learnerId: string) => `${BASE}/${kind}/${learnerId}/eligibility-evidence`;

export function fetchIlrEvidence(kind: LearnerKind, learnerId: string): Promise<IlrEvidenceListing> {
  return request<IlrEvidenceListing>(`${evidenceBase(kind, learnerId)}/`, { method: 'GET' });
}

/** Multipart, so no JSON Content-Type: the browser sets the boundary. */
export async function uploadIlrEvidence(kind: LearnerKind, learnerId: string, file: File): Promise<IlrEvidenceFile> {
  const body = new FormData();
  body.append('file', file, file.name);
  let res: Response;
  try {
    res = await fetch(`${evidenceBase(kind, learnerId)}/`, { method: 'POST', credentials: 'include', body });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Upload failed (${res.status})`);
  return data as IlrEvidenceFile;
}

export async function deleteIlrEvidence(kind: LearnerKind, learnerId: string, fileId: string): Promise<void> {
  await request<{ deleted: boolean }>(`${evidenceBase(kind, learnerId)}/${fileId}/`, { method: 'DELETE' });
}

/** A short-lived SAS URL for one stored file. */
export async function getIlrEvidenceUrl(kind: LearnerKind, learnerId: string, fileId: string): Promise<string> {
  const data = await request<{ url: string }>(`${evidenceBase(kind, learnerId)}/${fileId}/download/`, { method: 'GET' });
  return data.url;
}

// ── Employer Details prefill ────────────────────────────────────────────────
// From the learner's assigned employer and that employer's organisation (see
// ilr_employer_details.py). Every field is '' when the record has nothing.

export type IlrEmployerDetails = IlrForm['employer'];

export function fetchIlrEmployerDetails(kind: LearnerKind, learnerId: string): Promise<IlrEmployerDetails> {
  return request<IlrEmployerDetails>(`${BASE}/${kind}/${learnerId}/employer-details/`, { method: 'GET' });
}
