import type { PDFDocumentProxy } from 'pdfjs-dist';

// Pixels come from the PDF already opened through the normal file access path.
// No document URL, learner identity, or API credential is sent to the OCR API.
export async function extractScannedPage(pdf: PDFDocumentProxy, pageNumber: number, signal: AbortSignal): Promise<string> {
  const page = await pdf.getPage(pageNumber);
  const initial = page.getViewport({ scale: 1 });
  const scale = Math.min(2.5, 2200 / Math.max(initial.width, initial.height));
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Could not prepare this page for text extraction.');
  signal.throwIfAborted();
  const render = page.render({ canvas, canvasContext: context, viewport, background: '#ffffff' });
  const cancel = () => render.cancel();
  signal.addEventListener('abort', cancel, { once: true });
  try {
    await render.promise;
    signal.throwIfAborted();
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      value => value ? resolve(value) : reject(new Error('Could not prepare this page.')), 'image/jpeg', 0.85,
    ));
    if (blob.size > 2 * 1024 * 1024) throw new Error('This page is too large to extract. Use Original PDF.');
    const csrfResponse = await fetch('/learner_api/reading/ocr/csrf/', { credentials: 'include', signal });
    const csrf = await csrfResponse.json();
    if (!csrfResponse.ok || !csrf.csrfToken) throw new Error('Please sign in again to extract page text.');
    const body = new FormData();
    body.append('page', blob, 'page.jpg');
    const response = await fetch('/learner_api/reading/ocr/', {
      method: 'POST', credentials: 'include', headers: { 'X-CSRFToken': csrf.csrfToken }, body, signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || typeof result.text !== 'string') throw new Error(result.error || 'Could not extract this page. Please try again.');
    return result.text;
  } finally {
    signal.removeEventListener('abort', cancel);
    canvas.width = canvas.height = 0;
  }
}
