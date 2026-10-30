import type { CurriculumRecordKind } from '@/lib/curriculumApi';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';

/** A record as stored, and the token that describes exactly that version. */
export interface CurriculumRecordSnapshot<TRecord> {
  /** '' when the record cannot be fingerprinted; the save then goes unguarded. */
  revision: string;
  record: TRecord | null;
}

/**
 * Read the record a drawer edits together with its revision token.
 *
 * The drawer fills its fields from this rather than from the cached overview
 * row it was opened with, so the token it saves with always describes the
 * values it showed. Rejects on any failure: a token without its record is the
 * stale pairing this exists to prevent.
 */
export async function fetchCurriculumRecordSnapshot<TRecord>(
  kind: CurriculumRecordKind,
  id: string,
  signal?: AbortSignal,
): Promise<CurriculumRecordSnapshot<TRecord>> {
  const query = new URLSearchParams({ kind, id });
  const response = await fetch(`${API_BASE_URL}/curriculum/record-snapshot/?${query.toString()}`, {
    signal,
    cache: 'no-store',
    headers: { 'Cache-Control': 'no-cache' },
  });
  if (!response.ok) throw new Error(`Could not read the saved ${kind} (${response.status}).`);
  const payload = await response.json() as { revision?: string; record?: TRecord | null };
  return { revision: String(payload.revision || ''), record: payload.record ?? null };
}
