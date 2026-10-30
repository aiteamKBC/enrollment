import { createCachedResource } from './cachedRequest';

// Keep binary payloads bounded and in memory. Each PDF.js instance receives
// its own response body because its worker can transfer/detach the buffer.
const files = createCachedResource('pdf-previews', async (url: string) => {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { Accept: 'application/pdf,*/*' },
  });
  const bytes = [204, 205, 304].includes(response.status) ? null : await response.arrayBuffer();
  return { bytes, status: response.status, headers: response.headers };
}, 0, 8);

/** Share concurrent mounts without extending authorization/file freshness. */
export async function fetchPdfResource(url: string): Promise<Response> {
  const file = await files.read(url);
  return new Response(file.bytes?.slice(0) ?? null, { status: file.status, headers: file.headers });
}

const attachments = createCachedResource('attachment-previews', async (url: string) => {
  const response = await fetch(url, { credentials: 'same-origin' });
  const bytes = [204, 205, 304].includes(response.status) ? null : await response.arrayBuffer();
  return { bytes, status: response.status, headers: response.headers };
}, 0, 8);

export async function fetchAttachmentResource(url: string): Promise<Response> {
  const file = await attachments.read(url);
  return new Response(file.bytes?.slice(0) ?? null, { status: file.status, headers: file.headers });
}
