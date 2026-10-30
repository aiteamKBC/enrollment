import { useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { sanitizeCurriculumHtml } from '@/lib/curriculumHtml';
import { clearReadingHighlights, highlightReadingSelection, ReadingToolsContext, readStoredJson, type ReadingPreferences } from './readingAccessibility';
import { ReadingSurface } from './ReadingSurface';

export function ReadingAttachmentText({ sourceHtml, storageKey, preferences }: {
  sourceHtml: string; storageKey: string; preferences: ReadingPreferences;
}) {
  const tools = useContext(ReadingToolsContext);
  const register = tools?.register;
  const onDirty = tools?.onDirty;
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [html, setHtml] = useState(() => {
    const saved = readStoredJson(storageKey, { sourceHtml: '', html: '' });
    return sanitizeCurriculumHtml(saved.sourceHtml === sourceHtml ? saved.html : sourceHtml);
  });
  const innerHtml = useMemo(() => ({ __html: html }), [html]);
  useEffect(() => {
    register?.(id, {
      highlight: () => {
        if (!highlightReadingSelection(root.current)) return false;
        setHtml(root.current!.innerHTML);
        onDirty?.();
        return true;
      },
      clear: () => {
        if (!root.current) return;
        clearReadingHighlights(root.current);
        setHtml(root.current.innerHTML);
        onDirty?.();
      },
      save: () => window.localStorage.setItem(storageKey, JSON.stringify({ sourceHtml, html })),
    });
  }, [register, id, sourceHtml, html, storageKey, onDirty]);
  useEffect(() => () => register?.(id, null), [register, id]);

  return <ReadingSurface preferences={preferences} label="Reading attachment text" className="max-h-[72vh] overflow-auto rounded-xl border border-background-300 p-6 shadow-sm">
    <div ref={root} data-reading-text className="curriculum-html" dangerouslySetInnerHTML={innerHtml} />
  </ReadingSurface>;
}
