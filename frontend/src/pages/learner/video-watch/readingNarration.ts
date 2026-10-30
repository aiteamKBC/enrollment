import { createContext, useCallback, useEffect, useRef, useState } from 'react';

export type NarrationBlock = { text: string; pauseMs: number };
export type PdfPageReader = () => Promise<NarrationBlock[]>;

// Only previews inside a Reading activity join its main read-aloud button.
// A collapsed linked preview has no reader until the learner opens it.
export const ReadingPdfNarrationContext = createContext<((id: string, reader: PdfPageReader | null) => void) | null>(null);

export class ReadingNarrationError extends Error {}

// Decorative rules are layout, not spoken content. Keep ordinary hyphens,
// negative numbers and punctuation inside meaningful sentences.
export function cleanNarrationText(text: string): string {
  return text.replace(/(?:[_=~*—–-][ \t]*){3,}/gu, ' ')
    .replace(/[\u200B-\u200D\uFEFF]/gu, '').replace(/\s+/gu, ' ').trim();
}

export function narrationFromHtml(root: Element): NarrationBlock[] {
  const blocks: NarrationBlock[] = [];
  let text = '';
  const flush = (pauseMs = 650) => {
    const cleaned = cleanNarrationText(text);
    if (/[\p{L}\p{N}]/u.test(cleaned)) blocks.push({ text: cleaned, pauseMs });
    text = '';
  };
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) { text += node.textContent || ''; return; }
    if (!(node instanceof Element)) return;
    if (node.matches('script,style,button,svg,canvas,iframe,nav,[hidden],[aria-hidden="true"]')) return;
    const heading = /^H[1-6]$/.test(node.tagName);
    const block = heading || /^(P|DIV|SECTION|ARTICLE|LI|UL|OL|TR|BLOCKQUOTE|PRE)$/.test(node.tagName);
    if (node.tagName === 'BR' || node.tagName === 'HR') { flush(); return; }
    if (block) flush();
    if (node.tagName === 'PRE') {
      (node.textContent || '').split(/\n\s*\n/u).forEach(paragraph => { text = paragraph; flush(); });
    } else {
      node.childNodes.forEach(visit);
    }
    if (/^(TD|TH)$/.test(node.tagName)) text += '. ';
    if (block) flush(heading ? 900 : 650);
  };
  visit(root);
  flush();
  return blocks;
}

type PdfTextItem = { str: string; hasEOL?: boolean; transform: number[]; height: number };

export function narrationFromPdf(items: readonly (PdfTextItem | { type: string })[]): NarrationBlock[] {
  const blocks: NarrationBlock[] = [];
  let paragraph = '';
  let lastLineY: number | undefined;
  let lastHeight = 0;
  let lineEnded = false;
  const flush = () => {
    const text = cleanNarrationText(paragraph);
    if (/[\p{L}\p{N}]/u.test(text)) blocks.push({ text, pauseMs: 650 });
    paragraph = '';
  };
  for (const item of items) {
    if (!('str' in item)) continue;
    const y = item.transform[5];
    const height = Math.abs(item.height || item.transform[3]);
    // PDF text has visual lines, not semantic paragraphs. Treat larger line
    // gaps and font-size changes as boundaries; keep wrapped lines together.
    if (lastLineY !== undefined && Math.abs(y - lastLineY) > Math.max(height, lastHeight) * 1.6
        || lineEnded && lastHeight > 0 && Math.abs(height - lastHeight) > lastHeight * 0.25) flush();
    if (item.str.trim() && !cleanNarrationText(item.str)) flush();
    else paragraph += `${item.str} `;
    lastLineY = y;
    lastHeight = height;
    lineEnded = Boolean(item.hasEOL);
  }
  flush();
  return blocks;
}

function speechChunks(blocks: NarrationBlock[]): NarrationBlock[] {
  return blocks.flatMap(block => {
    const cleaned = cleanNarrationText(block.text);
    if (!/[\p{L}\p{N}]/u.test(cleaned)) return [];
    // Bound utterance length so long paragraphs remain stoppable and avoid
    // browser speech engines truncating a large, single utterance.
    const chunks: NarrationBlock[] = [];
    for (const sentence of cleaned.split(/(?<=[.!?…])\s+/u)) {
      let chunk = '';
      for (const word of sentence.split(' ')) {
        if (chunk && chunk.length + word.length > 260) {
          chunks.push({ text: chunk, pauseMs: 80 });
          chunk = '';
        }
        chunk += (chunk ? ' ' : '') + word;
      }
      if (chunk) chunks.push({ text: chunk, pauseMs: 180 });
    }
    if (chunks.length) chunks[chunks.length - 1].pauseMs = block.pauseMs;
    return chunks;
  });
}

let stopActiveNarration: (() => void) | null = null;

export function useReadingNarration(sourceKey: string) {
  const [speaking, setSpeaking] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const utterance = useRef<SpeechSynthesisUtterance | null>(null);
  const ownStop = useRef<(() => void) | null>(null);
  const stop = useCallback(() => {
    generation.current += 1;
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    if (utterance.current) {
      utterance.current.onend = null;
      utterance.current.onerror = null;
      utterance.current = null;
    }
    if (stopActiveNarration === ownStop.current) {
      stopActiveNarration = null;
      window.speechSynthesis?.cancel();
    }
    setSpeaking(false);
  }, []);
  ownStop.current = stop;

  useEffect(() => { setSpeechError(null); return stop; }, [sourceKey, stop]);

  const read = useCallback(async (getBlocks: () => NarrationBlock[] | Promise<NarrationBlock[]>) => {
    stopActiveNarration?.();
    stop();
    setSpeechError(null);
    if (!window.speechSynthesis || typeof SpeechSynthesisUtterance === 'undefined') {
      setSpeechError('Read aloud is not available in this browser.');
      return;
    }
    stopActiveNarration = stop;
    const request = generation.current;
    setSpeaking(true);
    try {
      const chunks = speechChunks(await getBlocks());
      if (request !== generation.current) return;
      if (!chunks.length) {
        stop();
        setSpeechError('No readable text was found in this content.');
        return;
      }
      const speak = (index: number) => {
        if (request !== generation.current) return;
        if (index >= chunks.length) { stop(); return; }
        const current = new SpeechSynthesisUtterance(chunks[index].text);
        utterance.current = current;
        current.rate = 0.95;
        current.onend = () => {
          if (request !== generation.current) return;
          if (index + 1 === chunks.length) { stop(); return; }
          timer.current = setTimeout(() => {
            try { speak(index + 1); } catch { stop(); setSpeechError('Could not read this content aloud. Please try again.'); }
          }, chunks[index].pauseMs);
        };
        current.onerror = () => {
          if (request !== generation.current) return;
          stop();
          setSpeechError('Could not read this content aloud. Please try again.');
        };
        window.speechSynthesis.speak(current);
      };
      speak(0);
    } catch (error) {
      if (request !== generation.current) return;
      stop();
      setSpeechError(error instanceof ReadingNarrationError ? error.message : 'Could not read this content aloud. Please try again.');
    }
  }, [stop]);

  return { speaking, speechError, read, stop };
}
