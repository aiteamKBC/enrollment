import { useEffect, useRef, useState } from 'react';
import styles from './monthlySubmission.module.css';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

export function AssignmentPdfPreview({ url, filename, label = 'Assignment report PDF preview', scrollableZoom = false }: { url: string; filename: string; label?: string; scrollableZoom?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let task: ReturnType<typeof import('pdfjs-dist')['getDocument']> | undefined;
    setPdf(null); setPage(1); setBusy(true); setError('');
    void (async () => {
      const { getDocument, GlobalWorkerOptions } = await import('pdfjs-dist');
      if (!active) return;
      GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      // Reports use blob URLs. Read their bytes directly instead of routing
      // them through PDF.js's HTTP/network transport.
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`PDF request failed (${response.status}).`);
      const data = new Uint8Array(await response.arrayBuffer());
      if (!active) return;
      task = getDocument({ data });
      const loaded = await task.promise;
      if (active) setPdf(loaded);
    })().catch(() => { if (active) { setError('Preview could not be loaded. You can still download the PDF.'); setBusy(false); } });
    return () => { active = false; controller.abort(); if (task) void task.destroy(); };
  }, [url, retry]);
  useEffect(() => {
    if (!pdf) return;
    let active = true;
    let render: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined;
    setBusy(true); setError('');
    void (async () => {
      const sheet = await pdf.getPage(page);
      if (!active || !canvas.current) return;
      const viewport = sheet.getViewport({ scale: 2 });
      const target = canvas.current;
      target.width = Math.ceil(viewport.width); target.height = Math.ceil(viewport.height);
      render = sheet.render({ canvas: target, canvasContext: target.getContext('2d')!, viewport });
      await render.promise;
      if (active) setBusy(false);
    })().catch(() => { if (active) { setBusy(false); setError('This page could not be previewed. You can still download the PDF.'); } });
    return () => { active = false; render?.cancel(); };
  }, [pdf, page]);
  return <section className={styles.pdfViewer} aria-label={label}>
    <p className="mb-3 break-words text-sm font-semibold">{filename}</p>
    {pdf && <div className={styles.pdfToolbar}>
      <button type="button" disabled={page === 1 || busy} onClick={() => setPage(value => value - 1)}>Previous page</button>
      <span>Page {page} of {pdf.numPages}</span>
      <button type="button" disabled={page === pdf.numPages || busy} onClick={() => setPage(value => value + 1)}>Next page</button>
      <div className={styles.pdfZoom} aria-label="Report zoom">
        <button type="button" aria-label="Zoom out report" disabled={zoom <= 50} onClick={() => setZoom(value => value - 25)}>-</button>
        <output aria-label="Report zoom level">{zoom}%</output>
        <button type="button" aria-label="Zoom in report" disabled={zoom >= (scrollableZoom ? 400 : 150)} onClick={() => setZoom(value => value + 25)}>+</button>
        <button type="button" onClick={() => setZoom(100)}>Reset zoom</button>
      </div>
    </div>}
    {busy && <p role="status">Loading PDF preview…</p>}
    {error && <div role="alert"><p>{error}</p><button type="button" className="mt-2 text-sm font-semibold text-blue-700" onClick={() => setRetry(value => value + 1)}>Retry preview</button></div>}
    <div className={`${styles.pdfStage}${scrollableZoom ? ` ${styles.pdfScrollable}` : ''}`}><canvas ref={canvas} aria-label={`Report page ${page}`} style={{ display: busy || error ? 'none' : 'block', width: `${zoom}%`, maxWidth: `${794 * zoom / 100}px`, height: 'auto' }} /></div>
  </section>;
}
