import JSZip, { type JSZipObject } from 'jszip';

export const MAX_BYTES = 50 * 1024 * 1024;
export const escape = (value: string) => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);

export async function readArchiveResponse(response: Response): Promise<ArrayBuffer> {
  const tooLarge = () => new Error('This archive is too large for an inline preview. Download the original to review it.');
  if (Number(response.headers.get('content-length')) > MAX_BYTES) { await response.body?.cancel(); throw tooLarge(); }
  const reader = response.body?.getReader();
  if (!reader) return response.arrayBuffer();
  const chunks: Uint8Array[] = []; let size = 0;
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    size += value.length;
    if (size > MAX_BYTES) { await reader.cancel(); throw tooLarge(); }
    chunks.push(value);
  }
  const buffer = new ArrayBuffer(size); const bytes = new Uint8Array(buffer); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return buffer;
}

export async function zipDocumentKind(buffer: ArrayBuffer): Promise<'docx' | 'spreadsheet' | 'odt' | 'archive'> {
  const zip = await JSZip.loadAsync(buffer);
  if (zip.file('word/document.xml')) return 'docx';
  if (zip.file('xl/workbook.xml')) return 'spreadsheet';
  const mime = await zip.file('mimetype')?.async('string');
  if (mime === 'application/vnd.oasis.opendocument.text') return 'odt';
  if (mime === 'application/vnd.oasis.opendocument.spreadsheet') return 'spreadsheet';
  return 'archive';
}

export async function readEntry(entry: JSZipObject): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const chunks: Uint8Array[] = []; let length = 0;
    // JSZip 3.10 exposes this browser stream; its bundled typings omit it.
    const stream = (entry as JSZipObject & { internalStream(type: 'uint8array'): JSZip.JSZipStreamHelper<Uint8Array> }).internalStream('uint8array');
    stream.on('data', chunk => {
      length += chunk.length;
      if (length > MAX_BYTES) { stream.pause(); reject(new Error('This file is too large for an inline preview. Download the original to review it.')); return; }
      chunks.push(chunk);
    }).on('error', reject).on('end', () => {
      const result = new Uint8Array(length); let offset = 0;
      for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
      resolve(result);
    }).resume();
  });
}

export async function openDocumentHtml(buffer: ArrayBuffer): Promise<string> {
  const zip = await JSZip.loadAsync(buffer);
  const entry = zip.file('content.xml');
  if (!entry) throw new Error('The OpenDocument content is missing.');
  const xml = new DOMParser().parseFromString(new TextDecoder().decode(await readEntry(entry)), 'application/xml');
  if (xml.querySelector('parsererror')) throw new Error('The OpenDocument content could not be read.');
  const body = xml.getElementsByTagNameNS('*', 'body')[0];
  if (!body) throw new Error('The OpenDocument body is missing.');
  const render = async (node: Node): Promise<string> => {
    if (node.nodeType === Node.TEXT_NODE) return escape(node.textContent || '');
    if (!(node instanceof Element)) return '';
    if (node.localName === 'image') {
      const href = node.getAttribute('xlink:href') || '';
      const image = zip.file(href); const type = /\.png$/i.test(href) ? 'png' : /\.jpe?g$/i.test(href) ? 'jpeg' : null;
      if (!image || !type) return '<p>[Image: see the original document]</p>';
      const bytes = await readEntry(image);
      let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
      return `<img alt="Document image" src="data:image/${type};base64,${btoa(binary)}">`;
    }
    if (node.localName === 'line-break') return '<br>';
    if (node.localName === 'tab') return ' &nbsp; ';
    if (node.localName === 's') return '&nbsp;'.repeat(Math.min(Number(node.getAttribute('text:c')) || 1, 100));
    const tags: Record<string, string> = { p: 'p', h: 'h2', list: 'ul', 'list-item': 'li', table: 'table', 'table-row': 'tr', 'table-cell': 'td' };
    const tag = tags[node.localName];
    const children = (await Promise.all([...node.childNodes].map(render))).join('');
    return tag ? `<${tag}>${children}</${tag}>` : children;
  };
  return render(body);
}

