import { beforeEach, describe, expect, it, vi } from 'vitest';
import { payloadFor } from './fixtures';
const api=vi.hoisted(()=>({request:vi.fn(),previewPdf:vi.fn()}));
vi.mock('../api',()=>({request:api.request}));
vi.mock('../previewPdf',()=>({previewPdf:api.previewPdf}));
import { persistOriginalPdf, downloadOriginalPdf } from '../persistOriginalPdf';
const bytes=new TextEncoder().encode('%PDF-1.4\nsynthetic original\n%%EOF');
const fetchMock=vi.fn();
function issued(){const value=payloadFor('training-plan');value.template.issueId='issued-uuid';value.template.originalPdfVersion=1;return value.data;}
beforeEach(()=>{
 vi.clearAllMocks();vi.stubGlobal('fetch',fetchMock);
 api.previewPdf.mockResolvedValue({arrayBuffer:async()=>bytes.buffer});api.request.mockResolvedValue({stored:true});
 vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:issued-pdf');vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{});
 vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
});
describe('immutable first-issued PDF artifacts',()=>{
 it('renders the issued payload with the existing generator and saves its exact original bytes',async()=>{
  const value=issued();await persistOriginalPdf('20',value);
  expect(api.previewPdf).toHaveBeenCalledWith(expect.objectContaining({template:value.template,data:value,sample:false}));
  const [path,method,body]=api.request.mock.calls[0];expect(path).toBe('/enrolment_api/document-templates/learners/20/issued/issued-uuid/original-pdf/');expect(method).toBe('POST');
  expect(atob(body.pdf)).toBe(new TextDecoder().decode(bytes));expect(body.snapshot).toBe(value);
 });
 it('downloads the stored original rather than regenerating different PDF bytes',async()=>{
  const blob=new Blob([bytes],{type:'application/pdf'});fetchMock.mockResolvedValue({ok:true,blob:async()=>blob});
  expect(await downloadOriginalPdf('20',issued())).toBe(true);
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/issued/issued-uuid/original-pdf/'),expect.objectContaining({credentials:'include'}));
  expect(URL.createObjectURL).toHaveBeenCalledWith(blob);expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();expect(api.previewPdf).not.toHaveBeenCalled();
 });
 it('leaves historical PDFs and actual signed downloads on their established path',async()=>{
  const historical=issued();delete historical.template.originalPdfVersion;
  await persistOriginalPdf('20',historical);expect(await downloadOriginalPdf('20',historical)).toBe(false);
  const signed=issued();signed.signatures.apprentice.signed=true;
  expect(await downloadOriginalPdf('20',signed)).toBe(false);expect(fetchMock).not.toHaveBeenCalled();expect(api.request).not.toHaveBeenCalled();
 });
 it('reports failed artifact persistence, while missing cached downloads can use the normal snapshot renderer',async()=>{
  api.request.mockRejectedValueOnce(new Error('Retry the Issue action'));
  await expect(persistOriginalPdf('20',issued())).rejects.toThrow('Retry the Issue action');
  fetchMock.mockResolvedValue({ok:false});expect(await downloadOriginalPdf('20',issued())).toBe(false);
 });
 it('keeps server-owned originals out of client generation and downloads their exact signed stored artifact',async()=>{
  const value=issued();value.template.originalPdfVersion=2;value.template.presentationTheme='kbc-dfe-purple-v3';value.signatures.provider.signed=true;
  await persistOriginalPdf('20',value);expect(api.previewPdf).not.toHaveBeenCalled();expect(api.request).not.toHaveBeenCalled();
  fetchMock.mockResolvedValue({ok:true,blob:async()=>new Blob([bytes],{type:'application/pdf'})});
  expect(await downloadOriginalPdf('20',value)).toBe(true);
  expect(fetchMock).toHaveBeenCalledWith('/enrolment_api/document-templates/learners/20/issued/issued-uuid/pdf/',expect.objectContaining({credentials:'include'}));
 });
 it('does not regenerate a branded issued PDF if its exact server artifact is missing',async()=>{
  const value=issued();value.template.originalPdfVersion=2;
  fetchMock.mockResolvedValue({ok:false});
  await expect(downloadOriginalPdf('20',value)).rejects.toThrow('exact stored document PDF');
  expect(api.previewPdf).not.toHaveBeenCalled();
 });
});
