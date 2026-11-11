import { useEffect, useState } from 'react';
import { Modal } from '@/pages/users/components/Modal';
import { btnSecondary } from '@/pages/users/components/ui';
import type { PreviewResponse } from './types';
import { previewPdf } from './previewPdf';

export function DocumentPreview({ value, onClose, onReady, onContinue }: { value: PreviewResponse; onClose: () => void; onReady?: (value: PreviewResponse) => void; onContinue?: () => void }) {
  const [url, setUrl] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let active = true; let objectUrl = '';
    setUrl(''); setError('');
    const pdf = value.pdfUrl ? fetch(value.pdfUrl, { credentials: 'include', cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error('The saved issued PDF is unavailable. Reload Documents and retry.');
      const blob = await response.blob();
      if (!(await blob.slice(0, 5).text()).startsWith('%PDF-')) throw new Error('The saved document is not a valid PDF.');
      return blob;
    }) : previewPdf(value);
    pdf.then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); onReady?.(value);
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [value, onReady]);
  return <Modal title={`PDF Preview — ${value.template.content.title}`} size="max-w-6xl" onClose={onClose} footer={<div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
    {url && <a className={btnSecondary} href={url} download={`${value.template.content.title.replace(/[^a-z0-9]+/gi, '-')}-preview.pdf`}>Download preview PDF</a>}
    {url && <a className={btnSecondary} href={url} target="_blank" rel="noopener noreferrer">Open in new tab</a>}
    {url && onContinue && <button className={btnSecondary} onClick={onContinue}>Continue to signature</button>}
    <button className={btnSecondary} onClick={onClose}>Close preview</button>
  </div>}>
    <p className={`mb-3 rounded-lg border p-3 text-sm ${value.sample ? 'border-amber-200 bg-amber-50' : 'border-primary-200 bg-primary-50'}`}>{value.notice}</p>
    {value.missingFields.length > 0 && <details className="mb-3 rounded-lg border border-amber-200 p-3 text-sm"><summary>{value.missingFields.length} unavailable or blank fields — shown blank in the document</summary><ul className="mt-2 list-inside list-disc">{value.missingFields.map((f) => <li key={f.field}>{f.label}</li>)}</ul></details>}
    {!!value.bindingWarnings?.length && <div role="alert" className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm"><p>Check learner dates before issuing this document.</p><ul className="mt-2 list-inside list-disc">{value.bindingWarnings.map(warning=><li key={`${warning.field}:${warning.code}`}>{warning.field}: {warning.message}</li>)}</ul></div>}
    {error && <p role="alert" className="text-red-700">Could not render preview: {error}</p>}
    {!url && !error && <p role="status">Loading document…</p>}
    {url && <iframe title="Document PDF preview" className="h-[70vh] w-full rounded-lg border" src={url} onError={() => setError('Your browser could not display the PDF. Use Open in new tab to review it.')} />}
  </Modal>;
}
