import { beforeEach, describe, expect, it, vi } from 'vitest';
import { contentFor } from './fixtures';

let api: typeof import('../api');
const fetchMock = vi.fn();
const result = (value: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(value), json: async () => value });
beforeEach(async () => {
  vi.resetModules(); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); api = await import('../api');
});
describe('secure builder API', () => {
  it('discards the exact learner/document/source draft with CSRF and a stale-write version', async () => {
    const { discardRevision } = await import('../revisionApi');
    fetchMock.mockResolvedValueOnce(result({ csrfToken:'csrf' })).mockResolvedValueOnce(result({ discarded:true }));
    await discardRevision('20', { id:'draft-id',documentKey:'ilr',sourceId:'issued-ilr',editVersion:4 } as import('../revisionApi').DocumentRevision);
    expect(fetchMock.mock.calls[1][0]).toBe('/enrolment_api/document-revisions/learners/20/ilr/revisions/draft-id/');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method:'DELETE',credentials:'include',headers:{ 'X-CSRFToken':'csrf' } });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ sourceId:'issued-ilr',editVersion:4 });
  });
  it('reads with the existing cookie session and without caching learner/template data', async () => {
    fetchMock.mockResolvedValue(result({ results: [] })); await api.listTemplates();
    expect(fetchMock).toHaveBeenCalledWith('/enrolment_api/document-templates/', expect.objectContaining({ credentials: 'include', cache: 'no-store' }));
  });
  it('gets existing CSRF protection before saving a draft', async () => {
    fetchMock.mockResolvedValueOnce(result({ csrfToken: 'synthetic-csrf' })).mockResolvedValueOnce(result({ revision: 1 }));
    await api.saveTemplate(contentFor('ilr'), 'published-version', 0);
    expect(fetchMock.mock.calls[0][0]).toBe('/learner_api/enrollment-csrf/');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'PUT', headers: { 'X-CSRFToken': 'synthetic-csrf' } });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ baseVersion: 'published-version', revision: 0 });
  });
  it('refreshes an expired CSRF token once and retries the same unchanged draft', async () => {
    fetchMock.mockResolvedValueOnce(result({ csrfToken: 'old' })).mockResolvedValueOnce(result({ error: 'CSRF token expired' }, 403))
      .mockResolvedValueOnce(result({ csrfToken: 'new' })).mockResolvedValueOnce(result({ revision: 1 }));
    await api.saveTemplate(contentFor('ilr'), 'published-version', 0);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[1][1].body).toBe(fetchMock.mock.calls[3][1].body);
    expect(fetchMock.mock.calls[3][1].headers['X-CSRFToken']).toBe('new');
  });
  it('surfaces stale draft errors and does not overwrite them with an automatic retry', async () => {
    fetchMock.mockResolvedValueOnce(result({ csrfToken: 'csrf' })).mockResolvedValueOnce(result({ error: 'Reload the updated draft.' }, 409));
    await expect(api.saveTemplate(contentFor('ilr'), 'old-version', 0)).rejects.toThrow('Reload the updated draft.');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('keeps structured storage diagnostics on an unsuccessful Edit request', async () => {
    const failure = { error: 'Document storage has not been installed.', setupRequired: true, setup: { code: 'missing_tables', missingTables: ['Learner_Document_Revisions'] } };
    fetchMock.mockResolvedValueOnce(result({ csrfToken: 'csrf' })).mockResolvedValueOnce(result(failure, 503));
    await expect(api.request('/enrolment_api/document-revisions/learners/20/ilr/drafts/', 'POST', { sourceId: 'signed-ilr' })).rejects.toMatchObject({ message: failure.error, status: 503, details: failure });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('preview uses sample data unless an existing learner is explicitly chosen', async () => {
    fetchMock.mockResolvedValueOnce(result({ csrfToken: 'csrf' })).mockResolvedValue(result({}));
    await api.previewTemplate('ilr', 1);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ revision: 1 });
    await api.previewTemplate('ilr', 1, 456);
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ revision: 1, learnerId: 456 });
    expect(fetchMock.mock.calls.every(([url]) => !/\/issue\/|\/sign\//.test(url))).toBe(true);
  });
});
