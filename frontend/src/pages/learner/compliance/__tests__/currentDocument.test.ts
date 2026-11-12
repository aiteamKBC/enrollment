import { beforeEach, expect, it, vi } from 'vitest';
import { currentDocument } from '../currentDocument';
const api = vi.hoisted(() => ({ revisionHistory: vi.fn(), readRevision: vi.fn() }));
vi.mock('@/features/compliance-documents/revisionApi', () => api);
beforeEach(() => vi.clearAllMocks());

it('uses signed V3 content and marks while keeping unsigned V1 unchanged and scoped to its source', async () => {
  const original = { id: 'root', signatures: { apprentice: { signed: false } }, programme: { name: 'V1' } };
  api.revisionHistory.mockResolvedValue({ results: [
    { id: 'v1', sourceId: 'root', number: 1, state: 'original' },
    { id: 'v2', sourceId: 'root', number: 2, state: 'finalized' },
    { id: 'v3', sourceId: 'root', number: 3, state: 'finalized' },
    { id: 'draft', sourceId: 'root', number: 4, state: 'draft' },
    { id: 'foreign', sourceId: 'another-root', number: 9, state: 'finalized' },
  ] });
  const detail = { revision: { id: 'v3' }, preview: { data: { id: 'signing-source', programme: { name: 'V3' }, signatures: { apprentice: { signed: true } } } } };
  api.readRevision.mockResolvedValue(detail);
  const result = await currentDocument('learner', 'training-plan', original);
  expect(api.readRevision).toHaveBeenCalledExactlyOnceWith('learner', 'v3');
  expect(result.document).toMatchObject({ id: 'root', programme: { name: 'V3' }, signatures: { apprentice: { signed: true } } });
  expect(result.revision).toBe(detail);
  expect(original.programme.name).toBe('V1'); expect(original.signatures.apprentice.signed).toBe(false);
});

it('keeps an original current until a successor is actually finalized', async () => {
  const original = { id: 'root' };
  api.revisionHistory.mockResolvedValue({ results: [{ sourceId: 'root', number: 2, state: 'draft' }] });
  expect(await currentDocument('learner', 'ilr', original)).toEqual({ document: original, revision: null });
  expect(api.readRevision).not.toHaveBeenCalled();
});
