import { useMemo } from 'react';
import { looksLikeHtml, normalizeAuthoredHtml, sanitizeCurriculumHtml } from '@/lib/curriculumHtml';

/**
 * Text a curriculum author wrote, shown to a learner. HTML is sanitised and
 * formatted with the shared `curriculum-html` styles; plain text keeps its
 * line breaks exactly as before. Links open in a new tab so the activity, and
 * its timer, stay open.
 */
export function AuthoredContent({ value, className = '' }: { value: string; className?: string }) {
  // Memoised object: React 19 re-applies innerHTML on a new identity, which
  // would restart an embedded <video>/<iframe> on every parent re-render.
  const inner = useMemo(() => (looksLikeHtml(value)
    ? { __html: sanitizeCurriculumHtml(normalizeAuthoredHtml(value), { openLinksInNewTab: true }) }
    : null), [value]);
  if (inner) return <div className={`curriculum-html ${className}`} dangerouslySetInnerHTML={inner} />;
  return <p className={`whitespace-pre-line ${className}`}>{value}</p>;
}
