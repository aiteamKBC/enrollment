import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiLogin, AuthError } from '../auth';

describe('authentication API', () => {
  afterEach(() => vi.restoreAllMocks());

  it('identifies the configured local Django port when the proxy is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('connect refused')));

    await expect(apiLogin('learner@example.test', 'not-a-real-password'))
      .rejects.toMatchObject({
        name: 'AuthError',
        status: 0,
        message: 'Could not reach the server. Is the backend running on port 8001?',
      } satisfies Partial<AuthError>);
  });
});
