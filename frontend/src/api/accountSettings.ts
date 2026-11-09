export type AccountSettingsSection = 'profile' | 'contact' | 'address';

export interface LearnerAccountSettings {
  learnerId: number;
  learnerKind: 'apprenticeship' | 'commercial';
  profile: {
    title: string;
    fullName: string;
    pronouns: string;
    postNominals: string;
    dateOfBirth: string;
  };
  contact: {
    emailAddress: string;
    mobileNumber: string;
    landlineNumber: string;
  };
  address: {
    addressLine1: string;
    addressLine2: string;
    addressLine3: string;
    addressLine4: string;
    residentialPostcode: string;
    country: string;
  };
  editable: Record<AccountSettingsSection, string[]>;
  csrfToken: string;
}

export class AccountSettingsApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'AccountSettingsApiError';
    this.status = status;
  }
}

function errorMessage(status: number, data: unknown): string {
  if (status === 401) return 'Your session has expired. Sign in again to view your account settings.';
  if (status === 403) return 'You do not have permission to view or change these account settings.';
  if (status === 404) return 'Your account settings are currently unavailable. Please try again later.';
  if (status === 400 && data && typeof data === 'object' && 'error' in data) {
    const message = data.error;
    if (typeof message === 'string' && message.length <= 120 && !/[<>\r\n]/.test(message)
      && /^(Enter |Choose |Keep this to |That (?:profile|contact|address) field cannot be changed here\.)/.test(message)) {
      return message;
    }
  }
  if (status === 400) return 'Check the details you entered and try again.';
  return 'Your account settings could not be loaded or saved right now. Please try again.';
}

function isSettings(value: unknown): value is LearnerAccountSettings {
  if (!value || typeof value !== 'object') return false;
  const data = value as Partial<LearnerAccountSettings>;
  const hasStrings = (part: unknown, keys: string[]) => Boolean(part && typeof part === 'object'
    && keys.every((key) => typeof (part as Record<string, unknown>)[key] === 'string'));
  return typeof data.learnerId === 'number'
    && (data.learnerKind === 'apprenticeship' || data.learnerKind === 'commercial')
    && hasStrings(data.profile, ['title', 'fullName', 'pronouns', 'postNominals', 'dateOfBirth'])
    && hasStrings(data.contact, ['emailAddress', 'mobileNumber', 'landlineNumber'])
    && hasStrings(data.address, ['addressLine1', 'addressLine2', 'addressLine3', 'addressLine4', 'residentialPostcode', 'country'])
    && Boolean(data.editable && ['profile', 'contact', 'address'].every((section) => Array.isArray(data.editable?.[section as AccountSettingsSection])))
    && typeof data.csrfToken === 'string';
}

async function responseJson<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') || '';
  let data: unknown = null;
  if (/\bapplication\/(?:[\w.-]+\+)?json\b/i.test(contentType)) {
    try { data = await response.json(); } catch { /* Never show raw parser errors. */ }
  }
  if (!response.ok) throw new AccountSettingsApiError(response.status, errorMessage(response.status, data));
  if (!data) throw new AccountSettingsApiError(response.status, 'Your account settings could not be loaded. Please try again.');
  return data as T;
}

async function settingsRequest(url: string, options: RequestInit, learnerId: number): Promise<LearnerAccountSettings> {
  let response: Response;
  try { response = await fetch(url, options); }
  catch { throw new AccountSettingsApiError(0, 'Could not connect to account settings. Check your connection and try again.'); }
  const data = await responseJson<unknown>(response);
  if (!isSettings(data) || data.learnerId !== learnerId) {
    throw new AccountSettingsApiError(response.status, 'Your account settings could not be loaded. Please try again.');
  }
  return data;
}

export async function fetchAccountSettings(learnerId: number): Promise<LearnerAccountSettings> {
  return settingsRequest(`/learner_api/account-settings/${learnerId}/`, {
    credentials: 'include',
    cache: 'no-store',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  }, learnerId);
}

export async function saveAccountSettings(
  learnerId: number,
  section: AccountSettingsSection,
  values: Record<string, string>,
  csrfToken: string,
): Promise<LearnerAccountSettings> {
  return settingsRequest(`/learner_api/account-settings/${learnerId}/`, {
    method: 'PUT',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken,
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: JSON.stringify({ section, values }),
  }, learnerId);
}

export async function fetchProfilePhoto(kind: string, learnerId: number): Promise<Blob | null> {
  const response = await fetch(`/learner_api/profile-photo/${encodeURIComponent(kind)}/${learnerId}/`, {
    credentials: 'include',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  if (response.status === 204 || response.status === 503) return null;
  if (!response.ok) throw new Error('Your profile photo could not be loaded.');
  return response.blob();
}

export async function uploadProfilePhoto(
  kind: string,
  learnerId: number,
  file: File,
  csrfToken: string,
): Promise<Blob> {
  const body = new FormData();
  body.append('photo', file);
  const response = await fetch(`/learner_api/profile-photo/${encodeURIComponent(kind)}/${learnerId}/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
    body,
  });
  if (!response.ok) {
    await responseJson<unknown>(response);
  }
  return response.blob();
}
