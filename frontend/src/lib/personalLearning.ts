/** A separate, account-owned course context. Never masquerades as a learner ID. */
export type PersonalLearningMode = 'study' | 'preview' | 'all';
export interface PersonalLearningContext {
  id: string;
  accountId: number;
  moduleId: string;
  mode: PersonalLearningMode;
  returnTo: string;
}
const KEY = 'personal-learning-context';
const ID = /^pl\.(\d+)\.(study|preview|all)\.([A-Za-z0-9_-]+)$/;
let personalAccountId: number | null = null;

export function syncPersonalLearningAccount(account: { id: number; role: string } | null): void {
  personalAccountId = account?.role === 'admin' ? account.id : null;
  const saved = readPersonalLearning();
  if (saved && saved.accountId !== personalAccountId) clearPersonalLearning();
}

export function ownPersonalLearning(pathname?: string): PersonalLearningContext | null {
  const context = activePersonalLearning(pathname);
  return context && context.accountId === personalAccountId ? context : null;
}

export function parsePersonalLearning(id: unknown): Omit<PersonalLearningContext, 'returnTo'> | null {
  const match = ID.exec(String(id ?? ''));
  if (!match || !Number.isSafeInteger(Number(match[1])) || Number(match[1]) <= 0) return null;
  return { id: match[0], accountId: Number(match[1]), mode: match[2] as PersonalLearningMode, moduleId: match[3] };
}

export function personalLearningId(accountId: number, moduleId: string, mode: PersonalLearningMode): string {
  const id = `pl.${accountId}.${mode}.${moduleId}`;
  if (!parsePersonalLearning(id)) throw new Error('This course cannot be opened. Reload and try again.');
  return id;
}

export function rememberPersonalLearning(id: string, returnTo?: string): void {
  const context = parsePersonalLearning(id);
  if (!context) return;
  const previous = readPersonalLearning();
  const destination = returnTo?.startsWith('/curriculum/') || returnTo === '/my-courses'
    ? returnTo : previous?.returnTo || '/my-courses';
  sessionStorage.setItem(KEY, JSON.stringify({ ...context, returnTo: destination }));
}

export function readPersonalLearning(): PersonalLearningContext | null {
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    const context = parsePersonalLearning(stored?.id);
    if (!context) return null;
    const returnTo = typeof stored.returnTo === 'string' && (stored.returnTo.startsWith('/curriculum/') || stored.returnTo === '/my-courses')
      ? stored.returnTo : '/my-courses';
    return { ...context, returnTo };
  } catch { return null; }
}

export function activePersonalLearning(pathname = window.location.pathname): PersonalLearningContext | null {
  if (!pathname.startsWith('/learner/')) return null;
  const explicit = pathname.match(/\/(?:commercial|apprenticeship)\/([^/]+)/);
  let decoded = '';
  try { decoded = explicit ? decodeURIComponent(explicit[1]) : ''; } catch { return null; }
  if (explicit && !parsePersonalLearning(decoded)) return null;
  const parsed = explicit && parsePersonalLearning(decoded);
  const stored = readPersonalLearning();
  return parsed ? { ...parsed, returnTo: stored?.returnTo || '/my-courses' } : stored;
}

export function clearPersonalLearning(): void {
  try { sessionStorage.removeItem(KEY); } catch { /* A blocked store must not prevent sign-out. */ }
}

function requestIdentity(url: URL, init?: globalThis.RequestInit): string | null {
  const values = [...url.pathname.split('/').map(decodeURIComponent), ...url.searchParams.values()];
  if (typeof init?.body === 'string') {
    try {
      const body = JSON.parse(init.body);
      values.push(body?.learnerId);
    } catch { /* The receiving endpoint reports invalid JSON. */ }
  }
  return values.find(value => parsePersonalLearning(value)) || null;
}

/** Explicit adapter used by learner clients, never a global fetch override.
 * Unrelated requests retain their original transport and credentials. */
export function personalLearningUrl(input: string, init?: globalThis.RequestInit): string {
  const url = new URL(input, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/learner_api/')) return input;
  if (url.pathname.startsWith('/learner_api/personal-learning/')) return input;
  const id = requestIdentity(url, init);
  if (!id) return input;
  return `/learner_api/personal-learning/${encodeURIComponent(id)}/request/?path=${encodeURIComponent(url.pathname + url.search)}`;
}

export function learningFetch(input: string, init?: globalThis.RequestInit): Promise<Response> {
  const url = personalLearningUrl(input, init);
  if (url === input) return fetch(input, init);
  return fetch(url, { ...init, credentials: 'include', headers: {
    ...Object.fromEntries(new Headers(init?.headers).entries()), 'X-Requested-With': 'XMLHttpRequest',
  } });
}

export async function personalLearningJson<T>(path: string, init?: globalThis.RequestInit): Promise<T> {
  const response = await fetch(`/learner_api/personal-learning/${path}`, {
    ...init, credentials: 'include', headers: {
      'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers,
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Personal learning is unavailable. Please try again.');
  return data as T;
}
