import { afterEach, expect, it, vi } from 'vitest';
import { previewPlrPdf, importPlrPdf } from '../plrPdfPreview';
import { fetchExtendedIlr } from '../extendedIlr';

afterEach(() => vi.unstubAllGlobals());
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
it('uses only the preview endpoint with credentials and CSRF, never an enrolment write endpoint', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ csrfToken: 'synthetic-token' }))
    .mockResolvedValueOnce(json({ previewOnly: true, records: [] }));
  vi.stubGlobal('fetch', fetch);
  const file = new File(['%PDF-synthetic'], 'synthetic.pdf', { type: 'application/pdf' });
  await previewPlrPdf('apprenticeship', '41', file, '', new AbortController().signal);
  expect(fetch).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetch.mock.calls) {
    expect(url).toBe('/enrolment_api/wizard/apprenticeship/41/plr-pdf-preview/');
    expect(options.credentials).toBe('include');
  }
  const post = fetch.mock.calls[1][1];
  expect(post.method).toBe('POST'); expect(post.headers['X-CSRFToken']).toBe('synthetic-token');
  expect(post.body.get('file')).toBe(file); expect(post.body.get('enteredUln')).toBe('');
});
it('surfaces safe server validation errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ csrfToken: 'synthetic' }))
    .mockResolvedValueOnce(json({ error: 'The PDF ULN does not match the selected student.' }, 409)));
  await expect(previewPlrPdf('apprenticeship', '41', new File(['synthetic'], 'synthetic.pdf'), '', new AbortController().signal)).rejects.toThrow('ULN does not match');
});

it('resends only the original file and entered ULN to the import endpoint', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ csrfToken: 'synthetic-token' }))
    .mockResolvedValueOnce(json({ imported: true, plr: { uln: '0123456789', records: [] }, added: 0, skipped: 3, message: '0 imported, 3 skipped' }));
  vi.stubGlobal('fetch', fetch);
  const file = new File(['%PDF-synthetic'], 'synthetic.pdf', { type: 'application/pdf' });
  const result = await importPlrPdf('apprenticeship', '41', file, '', new AbortController().signal);
  expect(fetch.mock.calls[1][0]).toBe('/enrolment_api/wizard/apprenticeship/41/plr-pdf-import/');
  const options = fetch.mock.calls[1][1];
  expect(options.credentials).toBe('include'); expect(options.headers['X-CSRFToken']).toBe('synthetic-token');
  expect(options.body.get('file')).toBe(file);
  expect([...options.body.keys()]).toEqual(['file', 'enteredUln']);
  expect(result.skipped).toBe(3);
});

it('invalidates cached enrolment data so reopening loads the imported records', async () => {
  const plr = { uln: '0123456789', records: [{ id: 'imported', subject: 'Synthetic award', recordType: 'Imported', credits: null }] };
  const fetch = vi.fn().mockResolvedValueOnce(json({ answers: null, draft: { plr: { uln: '', records: [] } }, meta: {} }))
    .mockResolvedValueOnce(json({ csrfToken: 'synthetic' }))
    .mockResolvedValueOnce(json({ imported: true, plr, added: 1, skipped: 0, message: 'Imported' }))
    .mockResolvedValueOnce(json({ answers: null, draft: { plr }, meta: {} }));
  vi.stubGlobal('fetch', fetch);
  await fetchExtendedIlr('apprenticeship', '59', { force: true });
  await importPlrPdf('apprenticeship', '59', new File(['synthetic'], 'synthetic.pdf'), '', new AbortController().signal);
  const loaded = await fetchExtendedIlr('apprenticeship', '59');
  expect(fetch).toHaveBeenCalledTimes(4);
  expect(loaded.draft?.plr).toEqual(plr);
});
