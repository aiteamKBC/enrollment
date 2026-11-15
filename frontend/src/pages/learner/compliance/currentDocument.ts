import { readRevision, revisionHistory, type RevisionDetail } from '@/features/compliance-documents/revisionApi';
import type { DocumentKey } from '@/features/compliance-documents/types';

/** Same active root, latest issued content and its own signature evidence. */
export async function currentDocument<T extends { id: string }>(learnerId: string, key: DocumentKey, original: T | null): Promise<{ document: T | null; revision: RevisionDetail | null }> {
  if (!original?.id) return { document: original, revision: null };
  const history = await revisionHistory(learnerId, key);
  const latest = history.results.filter(row => row.sourceId === String(original.id) && row.state === 'finalized')
    .sort((a, b) => b.number - a.number)[0];
  if (!latest) return { document: original, revision: null };
  const revision = await readRevision(learnerId, latest.id);
  return { document: { ...original, ...revision.preview.data, id: original.id } as T, revision };
}
