import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountSettingsApiError, fetchAccountSettings, saveAccountSettings } from '../accountSettings';

const settings = {
  learnerId: 20, learnerKind: 'apprenticeship', csrfToken: 'csrf',
  profile: { title: 'Mr', fullName: 'Synthetic Learner', pronouns: '', postNominals: '', dateOfBirth: '1995-04-03' },
  contact: { emailAddress: 'learner@example.invalid', mobileNumber: '07700 900123', landlineNumber: '' },
  address: { addressLine1: '1 Test Street', addressLine2: '', addressLine3: 'London', addressLine4: '', residentialPostcode: 'SW1A 1AA', country: 'United Kingdom' },
  editable: { profile: ['title', 'pronouns'], contact: ['mobileNumber'], address: ['addressLine1'] },
};

afterEach(() => vi.unstubAllGlobals());

function stubResponse(status: number, contentType: string, body: string) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status, headers: { 'Content-Type': contentType } })));
}

describe('account-settings API', () => {
  it('loads the owner-scoped JSON contract with cookies and no caching', async () => {
    stubResponse(200, 'application/json', JSON.stringify(settings));
    await expect(fetchAccountSettings(20)).resolves.toMatchObject(settings);
    expect(vi.mocked(fetch).mock.calls[0]).toEqual(['/learner_api/account-settings/20/', {
      credentials: 'include', cache: 'no-store', headers: { 'X-Requested-With': 'XMLHttpRequest' },
    }]);
  });

  it('never surfaces a Django HTML 404 or parser exception', async () => {
    stubResponse(404, 'text/html; charset=utf-8', '<!DOCTYPE html><title>Page not found</title>');
    await expect(fetchAccountSettings(20)).rejects.toMatchObject({ status: 404, message: 'Your account settings are currently unavailable. Please try again later.' });
  });

  it.each([
    [401, 'Your session has expired. Sign in again to view your account settings.'],
    [403, 'You do not have permission to view or change these account settings.'],
    [500, 'Your account settings could not be loaded or saved right now. Please try again.'],
  ])('handles status %s without exposing backend content', async (status, message) => {
    stubResponse(status, 'text/html', '<!DOCTYPE html>backend stack trace');
    await expect(fetchAccountSettings(20)).rejects.toMatchObject({ status, message });
  });

  it('rejects HTML success and malformed JSON without exposing response text', async () => {
    stubResponse(200, 'text/html', '<!DOCTYPE html>Vite fallback');
    await expect(fetchAccountSettings(20)).rejects.toBeInstanceOf(AccountSettingsApiError);
    stubResponse(200, 'application/json', '{bad json');
    await expect(fetchAccountSettings(20)).rejects.toThrow('Your account settings could not be loaded. Please try again.');
  });

  it('rejects a mismatched learner and sanitises a network failure', async () => {
    stubResponse(200, 'application/json', JSON.stringify(settings));
    await expect(fetchAccountSettings(21)).rejects.toBeInstanceOf(AccountSettingsApiError);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Internal network path')));
    await expect(fetchAccountSettings(20)).rejects.toThrow('Could not connect to account settings. Check your connection and try again.');
  });

  it('sends only the authorised edit to the existing endpoint with CSRF', async () => {
    stubResponse(200, 'application/json', JSON.stringify(settings));
    await saveAccountSettings(20, 'contact', { mobileNumber: '07700 900123' }, 'csrf');
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('/learner_api/account-settings/20/');
    expect(options).toMatchObject({ method: 'PUT', credentials: 'include', headers: { 'X-CSRFToken': 'csrf' } });
    expect(JSON.parse(options?.body as string)).toEqual({ section: 'contact', values: { mobileNumber: '07700 900123' } });
  });
});
