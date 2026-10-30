import { normalizeAuthoredHtml, sanitizeCurriculumHtml } from '@/lib/curriculumHtml';

const FILE_EXTENSION = /\.(pdf|docx?|pptx?|xlsx?|txt|csv|rtf|epub|zip|png|jpe?g|webp)$/i;

/** Expose attachments, not ordinary navigation links in the reading. */
export function readingFiles(
  resourceUrl: string | null | undefined,
  fileName: string | null | undefined,
  html: string,
  // Every file the author attached, in their order. The first is normally the
  // same file as `resourceUrl`; `add` dedupes by resolved href, so passing the
  // whole list rather than its tail keeps the order the author set without
  // offering the first document twice.
  attachments: readonly { url: string; fileName?: string | null }[] = [],
) {
  const files: { url: string; fileName?: string; label: string }[] = [];
  const seen = new Set<string>();
  const add = (url: string, name?: string, label?: string) => {
    try {
      const parsed = new URL(url, window.location.href);
      if (!['http:', 'https:'].includes(parsed.protocol) || seen.has(parsed.href)) return;
      seen.add(parsed.href);
      files.push({ url, fileName: name, label: label || name || 'Original file' });
    } catch { /* Malformed links are not downloadable attachments. */ }
  };
  if (resourceUrl) add(resourceUrl, fileName || undefined);
  for (const attachment of attachments) add(attachment.url, attachment.fileName || undefined);
  const document = new DOMParser().parseFromString(sanitizeCurriculumHtml(normalizeAuthoredHtml(html)), 'text/html');
  for (const anchor of document.querySelectorAll('a[href]')) {
    const url = anchor.getAttribute('href') || '';
    if (!url || url.startsWith('#')) continue;
    try {
      const parsed = new URL(url, window.location.href);
      const label = anchor.textContent?.trim() || '';
      if (FILE_EXTENSION.test(parsed.pathname) || anchor.hasAttribute('download') ||
          /\bPDF\b/i.test(label) || (parsed.hostname === 'drive.google.com' && /\/file\/d\//.test(parsed.pathname))) {
        add(url, anchor.getAttribute('download') || undefined, label);
      }
    } catch { /* Ignore malformed content links. */ }
  }
  return files;
}

/** Export source text without learner highlights or surrounding controls. */
export function readingText(html: string): string {
  const document = new DOMParser().parseFromString(sanitizeCurriculumHtml(normalizeAuthoredHtml(html)), 'text/html');
  document.querySelectorAll('script,style,iframe,video,audio,[hidden],[aria-hidden="true"]').forEach(node => node.remove());
  document.querySelectorAll('br').forEach(node => node.replaceWith('\n'));
  document.querySelectorAll('p,div,h1,h2,h3,h4,h5,h6,li,tr,blockquote,section').forEach(node => node.append('\n\n'));
  document.querySelectorAll('td,th').forEach(node => node.append('  '));
  document.querySelectorAll('li').forEach(node => node.prepend('• '));
  return (document.body.textContent || '').replace(/[^\S\n]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** Canvas preserves browser font fallback and Arabic shaping, like assignment PDFs. */
export async function downloadReadingPdf(title: string, html: string) {
  const text = readingText(html);
  if (!text) throw new Error('There is no reading text to download.');
  const { jsPDF } = await import('jspdf');
  await document.fonts?.ready;
  const pdf = new jsPDF({ unit: 'mm', format: 'a4', compress: true });
  pdf.setProperties({ title });
  const canvas = document.createElement('canvas');
  canvas.width = 1240;
  canvas.height = 1754;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Your browser could not prepare the reading PDF. Please try again.');
  let y = 90;
  let pages = 0;
  const begin = () => {
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    y = 90;
  };
  const flush = () => {
    context.direction = 'ltr';
    context.textAlign = 'right';
    context.font = '18px Arial';
    context.fillStyle = '#64748b';
    context.fillText(`Page ${pages + 1}`, 1150, 1700);
    if (pages++) pdf.addPage();
    pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, 210, 297, undefined, 'FAST');
  };
  const drawText = (value: string, heading = false) => {
    const size = heading ? 32 : 24;
    const font = `${heading ? 'bold ' : ''}${size}px Arial`;
    const draw = (line: string) => {
      if (y > 1620) { flush(); begin(); }
      context.font = font;
      context.fillStyle = '#172554';
      const rtl = /^[\s\d\p{P}]*[\u0590-\u08ff]/u.test(line);
      context.direction = rtl ? 'rtl' : 'ltr';
      context.textAlign = rtl ? 'right' : 'left';
      context.fillText(line, rtl ? 1150 : 90, y);
      y += size * 1.5;
    };
    for (const paragraph of value.split('\n')) {
      let line = '';
      for (const word of paragraph.split(/\s+/)) {
        context.font = font;
        const candidate = line ? `${line} ${word}` : word;
        if (context.measureText(candidate).width <= 1060) { line = candidate; continue; }
        if (line) draw(line);
        line = '';
        for (const char of word) {
          context.font = font;
          if (context.measureText(line + char).width > 1060) { draw(line); line = ''; }
          line += char;
        }
      }
      draw(line);
    }
    y += 20;
  };
  begin();
  drawText(title, true);
  drawText(text);
  flush();
  pdf.save(`${title.replace(/[<>:"/\\|?*]/g, '-').trim().slice(0, 120) || 'reading'}.pdf`);
}
