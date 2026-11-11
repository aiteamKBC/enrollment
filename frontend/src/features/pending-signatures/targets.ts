export function signatureTargetId(key: string, sourceId?: string | number, revisionId?: string): string | undefined {
  if (revisionId) return `signature-revision-${revisionId}`;
  return sourceId == null ? undefined : `signature-${key}-${encodeURIComponent(String(sourceId))}`;
}
