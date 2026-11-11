import { Blob as NodeBlob } from 'node:buffer';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { keys, payloadFor } from './fixtures';
import type { DocumentKey } from '../types';
const api = vi.hoisted(() => ({ request: vi.fn(), previewPdf: vi.fn(), changed: vi.fn() }));
vi.mock('../api', () => ({ request: api.request }));
vi.mock('../previewPdf', () => ({ previewPdf: api.previewPdf }));
vi.mock('@/features/pending-signatures/events', () => ({ pendingSignaturesChanged: api.changed }));
import { useAdminDocumentPreview } from '../useAdminDocumentPreview';

function Row({ doc = 'training-plan', source = 'issued-1', learner = '20' }: { doc?: DocumentKey; source?: string; learner?: string }) {
  const preview = useAdminDocumentPreview(learner);
  return <>{preview.button(doc, source || undefined)}{preview.dialog}
    <button onClick={() => { void preview.open(doc, source, () => undefined); }}>Sign as provider</button>
    <button onClick={() => { void preview.sign(doc, source, 'Admin', 'data:image/png;base64,test').catch(e => window.alert(e.message)); }}>Capture signature</button>
  </>;
}
beforeEach(() => {
  vi.clearAllMocks();
  api.request.mockImplementation(async (path: string) => {
    const key = path.match(/documents\/([^/]+)/)?.[1] as DocumentKey || 'training-plan';
    const value = payloadFor(key); value.sample = false; value.notice = path.includes('pre-issue') ? 'PRE-ISSUE PREVIEW — unsigned' : 'Issued original R1 · Source issued-1 · Template V1';
    return value;
  });
  api.previewPdf.mockResolvedValue(new NodeBlob(['%PDF-1.4\npage 1\n%%EOF'], { type: 'application/pdf' }));
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:read-only-pdf');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(window, 'alert').mockImplementation(() => {});
});

describe('admin document-row preview', () => {
  it.each(keys)('previews issued %s read-only, keeping issue/edit/sign actions out of the request', async doc => {
    render(<Row doc={doc} />);
    const trigger = screen.getByRole('button', { name: `Preview ${doc.replaceAll('-', ' ')}` });
    trigger.focus(); fireEvent.click(trigger);
    const pdf = await screen.findByTitle('Document PDF preview');
    expect(pdf).toHaveAttribute('src', 'blob:read-only-pdf');
    expect(screen.getByText('Issued original R1 · Source issued-1 · Template V1')).toBeVisible();
    expect(api.request).toHaveBeenCalledExactlyOnceWith(`/enrolment_api/document-templates/learners/20/documents/${doc}/preview/?sourceId=issued-1`);
    expect(api.previewPdf).toHaveBeenCalledWith(expect.objectContaining({ sample: false, data: expect.objectContaining({ template: expect.anything() }) }));
    expect(screen.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('link', { name: 'Download preview PDF' })).toHaveAttribute('download');
    expect(screen.queryByRole('button', { name: 'Continue to signature' })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTitle('Document PDF preview')).not.toBeInTheDocument());
    expect(trigger).toHaveFocus(); expect(api.changed).not.toHaveBeenCalled();
  });

  it.each(keys)('previews %s before issue using only the read-only endpoint', async doc => {
    render(<Row doc={doc} source="" />);
    fireEvent.click(screen.getByRole('button', { name: `Preview ${doc.replaceAll('-', ' ')}` }));
    await screen.findByTitle('Document PDF preview');
    expect(screen.getByText('PRE-ISSUE PREVIEW — unsigned')).toBeVisible();
    expect(api.request).toHaveBeenCalledWith(expect.stringContaining('/preview/?phase=pre-issue'));
    expect(api.changed).not.toHaveBeenCalled();
  });

  it('loads persisted original bytes when supplied, without regenerating them', async () => {
    const value = payloadFor('training-plan'); value.pdfUrl = '/private/learner/20/original.pdf';
    api.request.mockResolvedValue(value);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new NodeBlob(['%PDF-1.4\noriginal\n%%EOF']) })));
    render(<Row />); fireEvent.click(screen.getByRole('button', { name: 'Preview training plan' }));
    await screen.findByTitle('Document PDF preview');
    expect(fetch).toHaveBeenCalledWith(value.pdfUrl, { credentials: 'include', cache: 'no-store' });
    expect(api.previewPdf).not.toHaveBeenCalled();
  });

  it('only allows signing after the PDF is available and explicitly continued, using the reviewed identity', async () => {
    render(<Row />);
    fireEvent.click(screen.getByRole('button', { name: 'Capture signature' }));
    await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('Preview')));
    expect(api.request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Sign as provider' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to signature' }));
    expect(api.changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Capture signature' }));
    await waitFor(() => expect(api.changed).toHaveBeenCalledOnce());
    expect(api.request).toHaveBeenLastCalledWith('/enrolment_api/document-templates/learners/20/documents/training-plan/sign-reviewed/', 'POST', {
      sourceId: 'issued-1', reviewToken: 'synthetic-preview-token', party: 'provider', name: 'Admin', signature: 'data:image/png;base64,test',
    });
  });

  it('shows unavailable/corrupt PDF errors and never enables signing or substitutes a generated PDF', async () => {
    const value = payloadFor('training-plan'); value.pdfUrl = '/private/pdf'; api.request.mockResolvedValue(value);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new NodeBlob(['not a PDF']) })));
    render(<Row />); fireEvent.click(screen.getByRole('button', { name: 'Sign as provider' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('not a valid PDF');
    expect(screen.queryByRole('button', { name: 'Continue to signature' })).not.toBeInTheDocument();
    expect(api.previewPdf).not.toHaveBeenCalled(); expect(api.changed).not.toHaveBeenCalled();
  });

  it('reports missing source/stale-sign errors and keeps signing notifications unchanged', async () => {
    api.request.mockRejectedValueOnce(new Error('The issued document was replaced'));
    render(<Row />); fireEvent.click(screen.getByRole('button', { name: 'Preview training plan' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('replaced');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sign as provider' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to signature' }));
    api.request.mockRejectedValueOnce(new Error('The document changed after preview'));
    fireEvent.click(screen.getByRole('button', { name: 'Capture signature' }));
    await waitFor(() => expect(window.alert).toHaveBeenCalledWith('The document changed after preview'));
    expect(api.changed).not.toHaveBeenCalled();
  });
});
