import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { autoLoginBksbAccount } from '../bksb';

const ok = (data: unknown) => ({
  ok: true,
  status: 200,
  text: async () => JSON.stringify(data),
}) as Response;

describe('BKSB Auto-login CSRF handling', () => {
  beforeEach(() => {
    document.cookie = 'enrollment_csrftoken=enrollment-token';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ url: 'https://login.example.test/one-time' })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = 'enrollment_csrftoken=; Max-Age=0';
    document.cookie = 'csrftoken=; Max-Age=0';
  });

  it('uses the standalone enrollment service cookie name', async () => {
    await autoLoginBksbAccount('apprenticeship', '1601');

    expect(fetch).toHaveBeenCalledWith(
      '/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/',
      expect.objectContaining({
        credentials: 'include',
        method: 'POST',
        headers: expect.objectContaining({
          'X-CSRFToken': 'enrollment-token',
          'X-Requested-With': 'XMLHttpRequest',
        }),
      }),
    );
  });

  it('retains compatibility with the default Django cookie name', async () => {
    document.cookie = 'enrollment_csrftoken=; Max-Age=0';
    document.cookie = 'csrftoken=legacy-token';

    await autoLoginBksbAccount('apprenticeship', '1601');

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ headers: expect.objectContaining({ 'X-CSRFToken': 'legacy-token' }) }),
    );
  });

  it('passes Maths through the subject-specific dashboard provisioning path', async () => {
    await autoLoginBksbAccount('apprenticeship', '1604', 'maths');
    expect(fetch).toHaveBeenCalledWith(
      '/enrolment_api/wizard/apprenticeship/1604/initial-assessments/bksb-account/?subject=maths',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
