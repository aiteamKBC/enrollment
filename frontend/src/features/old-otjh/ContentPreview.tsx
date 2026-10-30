import { lazy, Suspense, useEffect, useState } from 'react';
import DOMPurify from 'dompurify';
import { btnSecondary } from '@/pages/users/components/ui';
import type { Activity } from './api';
import { contentUrl } from './report';
import type { ActivityContent } from './api';
import { previewUrl } from './previewUrl';
const ArchivePreview = lazy(() => import('./ArchivePreview').then(module => ({ default: module.ArchivePreview })));

export function HtmlPreview({ html, title }: { html: string; title: string }) {
  const safe = DOMPurify.sanitize(html, { FORBID_TAGS: ['form', 'iframe', 'object', 'embed'], FORBID_ATTR: ['srcset'] });
  const document = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'"><style>body{font:16px/1.6 system-ui,sans-serif;margin:24px;color:#182d48;overflow-wrap:anywhere}img,table{max-width:100%}pre{white-space:pre-wrap}h1{font-size:22px;color:#5626b0}li{padding:12px 0;border-bottom:1px solid #ebe5f7}small{color:#5c6574}p{margin:8px 0}</style></head><body>${safe}</body></html>`;
  return <iframe title={title} srcDoc={document} sandbox="" className="h-[65vh] min-h-[260px] w-full rounded-xl border border-foreground-200 bg-white" />;
}

const escapeHtml = (text: unknown) => String(text ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const questionHtml = (text: string) => DOMPurify.sanitize(text, {
  ALLOWED_TAGS: ['p', 'br', 'strong', 'b', 'i', 'em', 'sub', 'sup', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'img'],
  ALLOWED_ATTR: ['src', 'alt', 'colspan', 'rowspan'],
});
const CORRECT_ICON = '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/></svg>';

function quizOptions(value: unknown): { option_text: string; is_selected?: boolean }[] | null {
  let options = value;
  if (typeof options === 'string') {
    try { options = JSON.parse(options); } catch { return null; }
  }
  if (options == null) return [];
  if (!Array.isArray(options) || options.some(option => !option || typeof option !== 'object' || typeof option.option_text !== 'string')) return null;
  return options;
}

export function QuizPreview({ quiz, title }: { quiz: NonNullable<ActivityContent['parts'][number]['quiz']>; title: string }) {
  const attempt = quiz.attempt;
  const questions = (attempt?.quiz_body.questions ?? []).map(question => ({ ...question, answer_options: quizOptions(question.answer_options) }));
  const definitionQuestions = (quiz.definition?.questions ?? []).map(question => ({ ...question, answer_options: quizOptions(question.answer_options) }));
  if ([...questions, ...definitionQuestions].some(question => question.answer_options === null)) {
    return <p role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900">The quiz answer options could not be read. Close this activity and try again.</p>;
  }
  if ((!attempt || !questions.length || quiz.answers_available === false) && definitionQuestions.length) {
    const definition = quiz.definition;
    const html = `<h1>${escapeHtml(title)}</h1><p>Original quiz questions</p>${questionHtml(definition.description || '')}<ol>${definitionQuestions.map(question =>
      `<li><div>${questionHtml(question.question_text)}</div>${question.answer_options.length ? `<ul>${question.answer_options.map(option => `<li>${escapeHtml(option.option_text)}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ol>`;
    return <div className="space-y-3"><p role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900">Learner answers are unavailable. This view shows the original quiz questions only.</p>
      <HtmlPreview html={html} title={`Original quiz questions: ${title}`} /></div>;
  }
  if (!attempt || !questions.length) return <p role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900">The quiz result is recorded, but its questions and answers are unavailable. The source record needs to be restored before signing.</p>;
  const html = `<h1>${escapeHtml(title)}</h1><p>Saved quiz attempt · ${escapeHtml(attempt.status)} · ${escapeHtml(attempt.score ?? '—')} / ${escapeHtml(attempt.maximum_score ?? '—')}</p><p>Attempt ${escapeHtml(attempt.attempt_number)} · ${questions.length} questions</p><ol>${questions.map(question =>
    `<li><div>${questionHtml(question.question_text)}</div>
      <p><strong>Your answer:</strong> ${question.learner_selected_answers.map(escapeHtml).join(', ') || '—'}</p>
      <p>${question.is_correct ? `${CORRECT_ICON} Correct` : `Incorrect · Correct answer: ${question.correct_answers.map(escapeHtml).join(', ') || '—'}`}</p>
      ${question.answer_options?.length ? `<details><summary>Answer options</summary><ul>${question.answer_options.map(option => `<li>${escapeHtml(option.option_text)}${option.is_selected ? ' (selected)' : ''}</li>`).join('')}</ul></details>` : ''}</li>`).join('')}</ol>`;
  return <HtmlPreview html={html} title={`Saved quiz attempt: ${title}`} />;
}

function VideoPreview({ url, title }: { url: string; title: string }) {
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  return <div className="space-y-3">
    <video key={`${url}:${retry}`} title={title} src={url} controls playsInline preload="metadata"
      className="aspect-video max-h-[65vh] w-full rounded-xl bg-black object-contain"
      onError={() => setFailed(true)} onLoadedMetadata={() => setFailed(false)} />
    {failed && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm">
      <span>The video could not be loaded. Try again or open it in a new tab.</span>
      <button className={btnSecondary} onClick={() => { setFailed(false); setRetry(value => value + 1); }}>Retry video</button>
    </div>}
  </div>;
}

export function SourcePreview({ url: value, html, title, contentType }: { url: string | null; html: string | null; title: string; contentType?: string | null }) {
  const localMaterial = /^\/learner_api\/monthly-logs\/\d+\/\d{4}-\d{2}\/activities\/\d+\/materials\/\d+\/$/.test(value ?? '');
  const url = contentUrl(localMaterial ? new URL(value!, window.location.origin).href : value);
  if (!url) return html ? <HtmlPreview html={html} title={title} /> : <p className="p-4 text-sm text-foreground-500">No embedded content is available for this activity.</p>;
  const path = new URL(url).pathname;
  const embed = previewUrl(url);
  const source = new URL(url);
  const video = (contentType ?? '').toLowerCase().split(';')[0].trim().startsWith('video/') || /\.(mp4|webm|mov|m4v)$/i.test(path);
  const pdf = contentType === 'application/pdf' || /\.pdf$/i.test(path);
  // Office starts its document renderer with a form POST inside the frame.
  const office = new URL(embed).origin === 'https://view.officeapps.live.com';
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-foreground-500"><span>{video ? 'Video recording' : 'If the source does not allow embedded viewing, open it in a new tab.'}</span>
      <a className="font-medium text-primary-700 hover:underline" href={url} target="_blank" rel="noreferrer">Open in new tab</a></div>
    {/\.(png|jpe?g|webp|gif)$/i.test(path) ? <img src={url} alt={title} className="mx-auto max-h-[65vh] max-w-full rounded-xl object-contain" />
      : video ? <VideoPreview key={url} url={url} title={title} />
      : /\.(mp3|wav|m4a|ogg|aac)$/i.test(path) ? <audio title={title} src={url} controls preload="metadata" className="w-full" />
      : <iframe title={title} src={embed} className="h-[65vh] min-h-[260px] w-full rounded-xl border border-foreground-200 bg-white"
        sandbox={pdf ? undefined : source.origin === window.location.origin ? 'allow-scripts allow-presentation' : `allow-scripts allow-same-origin allow-presentation allow-popups${office ? ' allow-forms' : ''}`}
        allow="autoplay; fullscreen; picture-in-picture; encrypted-media" allowFullScreen />}
  </div>;
}

type Loaded = { kind: 'archive'; buffer: ArrayBuffer } | { kind: 'blob'; url: string; mime: string } | { kind: 'office'; url: string } | { kind: 'html'; html: string } | { kind: 'text'; text: string } | { kind: 'error'; message: string };
export function ProtectedFilePreview({ file }: { file: Activity['documents'][number] }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setLoaded(null);
    const load = async () => {
      try {
        const url = new URL(file.url, window.location.origin);
        const trustedPath = /^\/audit_api\/old-otjh\/documents\/\d+\/$/.test(url.pathname)
          || /^\/audit_api\/old-otjh\/material-documents\/\d+\/\d+\/$/.test(url.pathname)
          || /^\/audit_api\/old-otjh\/source-documents\/\d+\/\d+\/(file|report|note)\/$/.test(url.pathname);
        if (url.origin !== window.location.origin || !trustedPath) throw new Error('This document link is unavailable.');
        const name = file.display_name.toLowerCase();
        const office = /\.(doc|ppt|pptx|xls|xlsx)$/i.test(name)
          || /^(application\/(msword|vnd\.ms-(excel|powerpoint)|vnd\.openxmlformats-officedocument\.(presentationml\.presentation|spreadsheetml\.sheet)))$/i.test(file.content_type || '');
        if (office) {
          url.searchParams.set('preview', 'office');
          const response = await fetch(url.href, { credentials: 'include', cache: 'no-store', signal: controller.signal });
          const data = await response.json();
          const viewer = new URL(data.url);
          if (!response.ok || viewer.origin !== 'https://view.officeapps.live.com' || viewer.pathname !== '/op/embed.aspx') throw new Error('The Office document preview could not be loaded.');
          if (!controller.signal.aborted) setLoaded({ kind: 'office', url: viewer.href });
          return;
        }
        const response = await fetch(url.href, { credentials: 'include', cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('The document could not be loaded. Please try again.');
        let mime = file.content_type?.toLowerCase() || response.headers.get('content-type')?.split(';')[0] || '';
        if (!mime || mime === 'application/octet-stream') {
          const extensions: Record<string, string> = { pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', mp4: 'video/mp4', webm: 'video/webm', mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4', ogg: 'audio/ogg' };
          mime = extensions[name.split('.').pop() || ''] || mime;
        }
        if (name.endsWith('.zip') || /application\/(x-zip-compressed|zip)/.test(mime)) {
          const buffer = await (await import('./archiveDocument')).readArchiveResponse(response);
          if (!controller.signal.aborted) setLoaded({ kind: 'archive', buffer });
        } else if (name.endsWith('.odt') || mime === 'application/vnd.oasis.opendocument.text') {
          const html = await (await import('./archiveDocument')).openDocumentHtml(await response.arrayBuffer());
          if (!controller.signal.aborted) setLoaded({ kind: 'html', html });
        } else if (name.endsWith('.ods') || mime === 'application/vnd.oasis.opendocument.spreadsheet') {
          const xlsx = await import('xlsx'); const book = xlsx.read(await response.arrayBuffer(), { type: 'array' });
          const html = book.SheetNames.map(name => `<h2>${escapeHtml(name)}</h2>${xlsx.utils.sheet_to_html(book.Sheets[name])}`).join('');
          if (!controller.signal.aborted) setLoaded({ kind: 'html', html });
        } else if (name.endsWith('.docx') || mime === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') {
          const mammoth = await import('mammoth/mammoth.browser');
          const result = await mammoth.convertToHtml({ arrayBuffer: await response.arrayBuffer() });
          if (!controller.signal.aborted) setLoaded({ kind: 'html', html: result.value });
        } else if (mime === 'text/plain' || name.endsWith('.txt')) {
          const text = await response.text();
          if (!controller.signal.aborted) setLoaded({ kind: 'text', text });
        } else if (mime === 'text/html' || /\.html?$/.test(name)) {
          const html = await response.text();
          if (!controller.signal.aborted) setLoaded({ kind: 'html', html });
        } else {
          const blob = await response.blob();
          if (!mime || mime === 'application/octet-stream') {
            const prefix = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
            if (new TextDecoder().decode(prefix).startsWith('%PDF-')) mime = 'application/pdf';
            else if (prefix[0] === 80 && prefix[1] === 75 && prefix[2] === 3 && prefix[3] === 4) {
              const buffer = await blob.arrayBuffer();
              const decoder = await import('./archiveDocument');
              const kind = await decoder.zipDocumentKind(buffer);
              let html: string | undefined;
              if (kind === 'docx') html = (await (await import('mammoth/mammoth.browser')).convertToHtml({ arrayBuffer: buffer })).value;
              else if (kind === 'odt') html = await decoder.openDocumentHtml(buffer);
              else if (kind === 'spreadsheet') {
                const xlsx = await import('xlsx'); const book = xlsx.read(buffer, { type: 'array' });
                html = book.SheetNames.map(name => `<h2>${escapeHtml(name)}</h2>${xlsx.utils.sheet_to_html(book.Sheets[name])}`).join('');
              }
              if (!controller.signal.aborted) setLoaded(html === undefined ? { kind: 'archive', buffer } : { kind: 'html', html });
              return;
            }
          }
          const supported = mime === 'application/pdf' || /^(image\/(png|jpeg|gif|webp)|video\/(mp4|webm)|audio\/(mpeg|mp4|wav|ogg))$/.test(mime);
          if (!supported) throw new Error('An inline preview is not available for this file format. You can download the original file.');
          if (controller.signal.aborted) return;
          objectUrl = URL.createObjectURL(new Blob([blob], { type: mime }));
          setLoaded({ kind: 'blob', url: objectUrl, mime });
        }
      } catch (error) {
        if (!controller.signal.aborted) setLoaded({ kind: 'error', message: error instanceof Error ? error.message : 'Could not load the document.' });
      }
    };
    void load();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file.url, file.display_name, file.content_type, retry]);
  if (!loaded) return <p role="status" className="p-5 text-sm text-foreground-500">Loading document…</p>;
  if (loaded.kind === 'error') return <div role="alert" className="space-y-3 p-4 text-sm"><p>{loaded.message}</p><button className={btnSecondary} onClick={() => setRetry(value => value + 1)}>Try again</button></div>;
  if (loaded.kind === 'archive') return <Suspense fallback={<p role="status">Loading archive…</p>}><ArchivePreview buffer={loaded.buffer} title={file.display_name} /></Suspense>;
  if (loaded.kind === 'html') return <HtmlPreview html={loaded.html} title={file.display_name} />;
  if (loaded.kind === 'text') return <HtmlPreview html={`<pre>${escapeHtml(loaded.text)}</pre>`} title={file.display_name} />;
  if (loaded.kind === 'office') return <SourcePreview url={loaded.url} html={null} title={file.display_name} />;
  if (loaded.mime.startsWith('image/')) return <img src={loaded.url} alt={file.display_name} className="mx-auto max-h-[65vh] max-w-full rounded-xl object-contain" />;
  if (loaded.mime.startsWith('audio/')) return <audio src={loaded.url} title={file.display_name} controls className="w-full" />;
  if (loaded.mime.startsWith('video/')) return <video src={loaded.url} title={file.display_name} controls className="max-h-[65vh] w-full" />;
  return <iframe src={loaded.url} title={file.display_name} className="h-[65vh] min-h-[260px] w-full rounded-xl border border-foreground-200 bg-white" />;
}
