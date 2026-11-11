import { request } from './api';
import { previewPdf } from './previewPdf';
import type { IssuedTemplate } from './types';

/** The existing generators render the actual issued snapshot, not builder data. */
export async function persistOriginalPdf(learnerId: string | number, document: Record<string, any> & { template?: IssuedTemplate }) {
  const template = document.template;
  if (!template?.issueId || template.originalPdfVersion !== 1 || Object.values(document.signatures || {}).some((party: any) => party?.signed)) return;
  const blob = await previewPdf({ template, data: document as any, missingFields: [], previewToken: '', sample: false, notice: '' });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const chunks: string[] = [];
  for (let index = 0; index < bytes.length; index += 32768) chunks.push(String.fromCharCode(...bytes.subarray(index, index + 32768)));
  await request(`/enrolment_api/document-templates/learners/${learnerId}/issued/${template.issueId}/original-pdf/`, 'POST', {
    pdf: btoa(chunks.join('')), snapshot: document,
  });
}

/** Download the issued original bytes while unsigned; signed copies keep their
 * existing renderer, including their actual new signature appearances. */
export async function downloadOriginalPdf(learnerId: string, issuedDocument: Record<string, any>): Promise<boolean> {
  const template = issuedDocument.template as IssuedTemplate | undefined;
  if (!template?.issueId || ![1,2].includes(template.originalPdfVersion || 0) || template.originalPdfVersion === 1 && Object.values(issuedDocument.signatures || {}).some((party: any) => party?.signed)) return false;
  try {
    const response = await fetch(`/enrolment_api/document-templates/learners/${learnerId}/issued/${template.issueId}/${template.originalPdfVersion === 2 ? 'pdf' : 'original-pdf'}/`, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) { if (template.originalPdfVersion === 2) throw new Error('The exact stored document PDF is unavailable. Please retry.'); return false; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = `${template.content.documentKey}-${template.originalPdfVersion === 2 ? 'issued' : 'R1-original'}.pdf`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    return true;
  } catch (error) { if (template.originalPdfVersion === 2) throw error; return false; }
}

/** Published issued snapshots open directly behind the existing authenticated GET. */
export function storedPdfAction(value: unknown, filename?: string): boolean {
  const data = value as { pdfUrl?: string } | null;
  if (!data?.pdfUrl) return false;
  if (filename) { const link=document.createElement('a');link.href=data.pdfUrl;link.download=filename;link.click(); }
  else window.open(data.pdfUrl, '_blank', 'noopener,noreferrer');
  return true;
}
