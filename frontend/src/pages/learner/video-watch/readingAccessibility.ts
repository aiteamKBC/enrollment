import { createContext } from 'react';
import type { CSSProperties } from 'react';

export type ReadingColourMode = 'paper' | 'cream' | 'monochrome' | 'dark';
export type ReadingFont = 'default' | 'dyslexia' | 'sans';
export interface ReadingPreferences {
  colourMode: ReadingColourMode;
  font: ReadingFont;
  fontSize: number;
  lineHeight: number;
  letterSpacing: number;
  ruler: boolean;
}
export const DEFAULT_READING_PREFERENCES: ReadingPreferences = {
  colourMode: 'paper', font: 'default', fontSize: 16, lineHeight: 1.7, letterSpacing: 0, ruler: false,
};

export function readingStorageKey(componentId: string, suffix: string): string {
  return `kbc-reading:${componentId || 'unknown'}:${suffix}`;
}

export function readStoredJson<T>(key: string, fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return value ? { ...fallback, ...JSON.parse(value) } : fallback;
  } catch {
    return fallback;
  }
}

export function readingSurfaceStyle(preferences: ReadingPreferences): CSSProperties {
  const colours = {
    paper: { backgroundColor: '#ffffff', color: '#1f2937' },
    cream: { backgroundColor: '#fff7dc', color: '#292524' },
    monochrome: { backgroundColor: '#ffffff', color: '#000000', filter: 'grayscale(1)' },
    dark: { backgroundColor: '#111827', color: '#f9fafb' },
  };
  return {
    ...colours[preferences.colourMode],
    fontFamily: preferences.font === 'dyslexia' ? '"OpenDyslexic", "Comic Sans MS", "Trebuchet MS", sans-serif'
      : preferences.font === 'sans' ? 'Arial, Helvetica, sans-serif' : 'inherit',
    fontSize: `${preferences.fontSize}px`, lineHeight: preferences.lineHeight,
    letterSpacing: `${preferences.letterSpacing}em`,
  };
}

export interface ReadingAnnotations {
  highlight: () => boolean;
  clear: () => void;
  /** Throw if storage fails, so the main toolbar cannot report false success. */
  save: () => void;
}
export const ReadingToolsContext = createContext<{
  preferences: ReadingPreferences;
  register: (id: string, actions: ReadingAnnotations | null) => void;
  onDirty: () => void;
} | null>(null);

/** Mark individual text nodes, preserving paragraphs, links and table structure. */
export function highlightReadingSelection(root: HTMLElement | null): boolean {
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (!root || !range || range.collapsed || !root.contains(range.commonAncestorContainer)) return false;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const selected: { node: Text; start: number; end: number }[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!range.intersectsNode(node) || node.parentElement?.closest('mark[data-learner-highlight],script,style,button')) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.length;
    if (node.textContent?.slice(start, end).trim()) selected.push({ node, start, end });
  }
  for (const { node, start, end } of selected) {
    const part = document.createRange();
    part.setStart(node, start);
    part.setEnd(node, end);
    const mark = document.createElement('mark');
    mark.dataset.learnerHighlight = 'true';
    mark.style.backgroundColor = '#fde047';
    mark.style.color = '#111827';
    part.surroundContents(mark);
  }
  selection?.removeAllRanges();
  return selected.length > 0;
}

export function clearReadingHighlights(root: HTMLElement): void {
  root.querySelectorAll('mark[data-learner-highlight]').forEach(mark => mark.replaceWith(...Array.from(mark.childNodes)));
  root.normalize();
}
