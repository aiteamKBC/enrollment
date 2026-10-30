import { useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { sanitizeCurriculumHtml } from '@/lib/curriculumHtml';
import { narrationFromPdf } from './readingNarration';
import { clearReadingHighlights, highlightReadingSelection, ReadingToolsContext, readStoredJson, type ReadingPreferences } from './readingAccessibility';
import { ReadingSurface } from './ReadingSurface';
import { extractScannedPage } from './readingOcr';

type DocumentText = { sourceHtml: string; html: string };

function paragraphs(texts: string[]): string {
  return texts.map(text => {
    const paragraph = document.createElement('p');
    paragraph.dir = 'auto';
    paragraph.textContent = text;
    return paragraph.outerHTML;
  }).join('');
}

export function PdfReadingText({ pdf, pageNumber, active, storageKey, preferences, ocrText }: {
  pdf: PDFDocumentProxy; pageNumber: number; active: boolean; storageKey: string; preferences: ReadingPreferences;
  ocrText: Map<number, string>;
}) {
  const tools = useContext(ReadingToolsContext);
  const register = tools?.register;
  const onDirty = tools?.onDirty;
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [documents, setDocuments] = useState<Record<number, DocumentText>>(() => readStoredJson(storageKey, {}));
  const [loaded, setLoaded] = useState<{ page: number; sourceHtml: string } | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [extracting, setExtracting] = useState(false);
  const [ocrError, setOcrError] = useState('');
  const ocrRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoaded(null);
    setError('');
    setOcrError('');
    setExtracting(false);
    void pdf.getPage(pageNumber).then(page => page.getTextContent()).then(content => {
      if (cancelled) return;
      // Text is escaped before markup is constructed. PDF strings are untrusted.
      const blocks = narrationFromPdf(content.items);
      const sourceHtml = paragraphs(blocks.length ? blocks.map(block => block.text) : (ocrText.get(pageNumber) || '').split(/\n\s*\n/).filter(Boolean));
      setLoaded({ page: pageNumber, sourceHtml });
    }).catch(() => {
      if (!cancelled) setError('Could not extract text from this PDF page. Try again or use Original PDF.');
    });
    return () => { cancelled = true; ocrRequest.current?.abort(); };
  }, [pdf, pageNumber, active, retry, ocrText]);

  async function extractText() {
    if (ocrRequest.current && !ocrRequest.current.signal.aborted) return;
    const controller = new AbortController();
    ocrRequest.current = controller;
    setExtracting(true);
    setOcrError('');
    try {
      const text = await extractScannedPage(pdf, pageNumber, controller.signal);
      if (controller.signal.aborted) return;
      if (!text.trim()) {
        setOcrError('No text could be recognised on this page. Use Original PDF to view it.');
        return;
      }
      ocrText.set(pageNumber, text);
      setLoaded({ page: pageNumber, sourceHtml: paragraphs(text.split(/\n\s*\n/).filter(Boolean)) });
    } catch (failure) {
      if (!controller.signal.aborted) setOcrError(failure instanceof Error ? failure.message : 'Could not extract this page. Please try again.');
    } finally {
      if (!controller.signal.aborted) setExtracting(false);
      if (ocrRequest.current === controller) ocrRequest.current = null;
    }
  }

  const sourceHtml = loaded?.page === pageNumber ? loaded.sourceHtml : '';
  const saved = documents[pageNumber];
  const html = useMemo(() => sanitizeCurriculumHtml(
    saved?.sourceHtml === sourceHtml ? saved.html : sourceHtml,
  ), [saved, sourceHtml]);
  const innerHtml = useMemo(() => ({ __html: html }), [html]);

  useEffect(() => {
    register?.(id, {
      highlight: () => {
        if (!active || !highlightReadingSelection(root.current)) return false;
        const highlightedHtml = root.current!.innerHTML;
        setDocuments(current => ({ ...current, [pageNumber]: { sourceHtml, html: highlightedHtml } }));
        onDirty?.();
        return true;
      },
      clear: () => {
        const cleared = Object.fromEntries(Object.entries(documents).map(([page, doc]) => [page, { ...doc, html: doc.sourceHtml }]));
        if (root.current) clearReadingHighlights(root.current);
        setDocuments(cleared);
        onDirty?.();
      },
      save: () => window.localStorage.setItem(storageKey, JSON.stringify(documents)),
    });
  }, [register, id, active, pageNumber, sourceHtml, documents, storageKey, onDirty]);
  useEffect(() => () => register?.(id, null), [register, id]);

  if (!active) return null;
  if (error) return <div role="alert" className="p-5 text-sm text-red-700">{error}
    <button type="button" className="ml-2 underline" onClick={() => setRetry(value => value + 1)}>Retry text view</button>
  </div>;
  if (loaded?.page !== pageNumber) return <p role="status" className="p-5 text-sm">Loading page text…</p>;
  if (!sourceHtml) return <div className="p-5 text-sm">
    <p role="status">No readable text was found on this page. Extract text from this scanned page to use the reading tools.</p>
    <button type="button" disabled={extracting} onClick={() => void extractText()}
      className="mt-3 rounded-lg border border-violet-300 px-3 py-2 font-semibold text-violet-800 disabled:opacity-50">
      {extracting ? 'Extracting page text…' : 'Extract page text'}
    </button>
    {extracting && <p role="status" className="mt-2">This may take a few moments.</p>}
    {ocrError && <p role="alert" className="mt-2 text-red-700">{ocrError}</p>}
  </div>;
  return <ReadingSurface preferences={preferences} label={`PDF text, page ${pageNumber}`} className="max-h-[72vh] overflow-auto p-5">
    {ocrText.has(pageNumber) && <p className="mb-3 text-xs">Extracted from a scanned page. Check Original PDF if a word looks incorrect.</p>}
    <div ref={root} data-reading-text data-pdf-reading-text className="curriculum-html" dangerouslySetInnerHTML={innerHtml} />
  </ReadingSurface>;
}
