import { useEffect, useState } from 'react';
import JSZip, { type JSZipObject } from 'jszip';
import { HtmlPreview } from './ContentPreview';

import { MAX_BYTES, escape, readEntry, openDocumentHtml } from './archiveDocument';

type Preview = { kind: 'html'; html: string } | { kind: 'file'; url: string; mime: string } | { kind: 'error'; message: string };

export function ArchivePreview({ buffer, title }: { buffer: ArrayBuffer; title: string }) {
  const [entries, setEntries] = useState<JSZipObject[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  useEffect(() => {
    let active = true; setEntries([]); setError(''); setSelected('');
    void (async () => {
      if (buffer.byteLength > MAX_BYTES) throw new Error('This archive is too large for an inline preview. Download the original to review it.');
      const zip = await JSZip.loadAsync(buffer);
      const files = Object.values(zip.files).filter(file => !file.dir && !file.name.startsWith('__MACOSX/'));
      if (files.length > 256) throw new Error('This archive contains too many files for an inline preview. Download the original to review it.');
      if (!files.length) throw new Error('This archive contains no files.');
      if (active) { setEntries(files); setSelected(files[0].name); }
    })().catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'The archive could not be read.'); });
    return () => { active = false; };
  }, [buffer]);
  useEffect(() => {
    const entry = entries.find(file => file.name === selected);
    let active = true, blobUrl: string | undefined; setPreview(null);
    if (entry) void (async () => {
      const bytes = await readEntry(entry); const data = new ArrayBuffer(bytes.byteLength); new Uint8Array(data).set(bytes);
      const ext = entry.name.split('.').pop()?.toLowerCase();
      let html: string | undefined;
      if (ext === 'docx') html = (await (await import('mammoth/mammoth.browser')).convertToHtml({ arrayBuffer: data })).value;
      else if (ext === 'odt') html = await openDocumentHtml(data);
      else if (['ods', 'xls', 'xlsx', 'csv'].includes(ext || '')) {
        const xlsx = await import('xlsx'); const book = xlsx.read(data, { type: 'array' });
        html = book.SheetNames.map(name => `<h2>${escape(name)}</h2>${xlsx.utils.sheet_to_html(book.Sheets[name])}`).join('');
      } else if (['txt', 'md', 'json', 'html', 'htm'].includes(ext || '')) {
        const text = new TextDecoder().decode(bytes); html = ['html', 'htm'].includes(ext || '') ? text : `<pre>${escape(text)}</pre>`;
      }
      if (!active) return;
      if (html !== undefined) { setPreview({ kind: 'html', html }); return; }
      const mime = ({ pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', mp4: 'video/mp4', webm: 'video/webm', mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4', ogg: 'audio/ogg' } as Record<string, string>)[ext || ''] || 'application/octet-stream';
      blobUrl = URL.createObjectURL(new Blob([data], { type: mime })); setPreview({ kind: 'file', url: blobUrl, mime });
    })().catch(cause => { if (active) setPreview({ kind: 'error', message: cause instanceof Error ? cause.message : 'The file could not be read.' }); });
    return () => { active = false; if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [entries, selected]);
  if (error) return <p role="alert">{error}</p>;
  if (!entries.length) return <p role="status">Loading archive…</p>;
  return <div className="space-y-4"><label className="block text-sm font-medium">Files in {title}
    <select aria-label="Archive file" className="mt-2 w-full rounded-xl border border-foreground-200 p-3" value={selected} onChange={event => setSelected(event.target.value)}>{entries.map(entry => <option key={entry.name} value={entry.name}>{entry.name}</option>)}</select></label>
    {!preview ? <p role="status">Loading file…</p> : preview.kind === 'error' ? <p role="alert">{preview.message}</p> : preview.kind === 'html' ? <HtmlPreview html={preview.html} title={selected} /> : <>
      <a href={preview.url} download={selected.split('/').pop()} className="text-sm text-primary-700 underline">Download selected file</a>
      {preview.mime === 'application/pdf' ? <iframe src={preview.url} title={selected} className="h-[65vh] w-full rounded-xl border" />
        : preview.mime.startsWith('image/') ? <img src={preview.url} alt={selected} className="mx-auto max-h-[65vh] max-w-full" />
          : preview.mime.startsWith('video/') ? <video src={preview.url} title={selected} controls className="max-h-[65vh] w-full" />
            : preview.mime.startsWith('audio/') ? <audio src={preview.url} title={selected} controls className="w-full" />
              : <p>Download this file to review its original format.</p>}</>}
  </div>;
}
