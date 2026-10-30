import DOMPurify from 'dompurify';

/**
 * The one sanitiser for HTML a curriculum author wrote -- a reading's body, a
 * video's lesson content, a component description -- wherever it is put into
 * a live DOM: the learner runner, "Preview as learner", and the Module
 * Builder's design surface.
 *
 * It keeps what authors actually use (headings, lists, tables, links to
 * uploaded files, <video>/<audio>, YouTube-style <iframe> embeds, inline
 * styles) and removes what could act: scripts, event handlers, javascript:
 * and data: links, <style> blocks that would restyle the whole LMS, forms,
 * <object>/<embed>, and any iframe that is not https or same-site.
 *
 * Display only: nothing here is ever written back to the stored value.
 */

export interface CurriculumHtmlOptions {
  /** Learner views: a link must not navigate away from the open activity. */
  openLinksInNewTab?: boolean;
}

// https://… or a same-site path (/curriculum_api/…), never protocol-relative.
const SAFE_IFRAME_SRC = /^(?:https:\/\/|\/(?!\/))/i;

const CONFIG = {
  ADD_TAGS: ['iframe'],
  ADD_ATTR: ['target', 'allow', 'allowfullscreen', 'frameborder', 'scrolling', 'loading', 'referrerpolicy'],
  FORBID_TAGS: ['style', 'form', 'object', 'embed', 'base', 'meta', 'link'],
  FORBID_ATTR: ['srcdoc', 'formaction'],
};

let purifier: ReturnType<typeof DOMPurify> | null = null;
// Read by the hook during one synchronous sanitize call.
let newTabLinks = false;

function curriculumPurifier() {
  if (purifier) return purifier;
  // Its own instance, so these hooks never touch the other DOMPurify callers.
  const instance = DOMPurify(window);
  instance.addHook('afterSanitizeAttributes', node => {
    const element = node as Element;
    const tag = element.tagName?.toLowerCase();
    if (tag === 'iframe') {
      const src = (element.getAttribute('src') || '').trim();
      if (!SAFE_IFRAME_SRC.test(src)) element.removeAttribute('src');
      return;
    }
    if (tag !== 'a') return;
    const href = (element.getAttribute('href') || '').trim();
    const target = (element.getAttribute('target') || '').trim().toLowerCase();
    if (target && target !== '_self') element.setAttribute('target', '_blank');
    else if (newTabLinks && href && !href.startsWith('#')) element.setAttribute('target', '_blank');
    if (element.getAttribute('target') === '_blank') element.setAttribute('rel', 'noopener noreferrer');
  });
  purifier = instance;
  return instance;
}

export function sanitizeCurriculumHtml(html: string, options: CurriculumHtmlOptions = {}): string {
  if (!html) return '';
  newTabLinks = Boolean(options.openLinksInNewTab);
  try {
    return curriculumPurifier().sanitize(html, CONFIG) as string;
  } finally {
    newTabLinks = false;
  }
}

/** Does this authored value carry markup, or is it plain text? */
export function looksLikeHtml(value: string | null | undefined): boolean {
  return /<[a-z][a-z0-9-]*(?:\s[^>]*)?\/?>/i.test(value || '');
}

/**
 * Older reading editors stored their markup escaped (`&lt;p&gt;…`), wrapped
 * only in the editor's own <div>/<br>/<p>. That legacy shape is decoded, with
 * the same line-break handling the activity player has always applied.
 *
 * A document that already carries real markup is never decoded: there an
 * escaped `&lt;user role&gt;` is text the author meant to show (a code
 * sample, a "<US$100K" threshold), and decoding it would turn it into an
 * unknown tag that renders as nothing.
 */
export function normalizeAuthoredHtml(value: string): string {
  if (!/&lt;\/?[a-z][a-z0-9]*(&gt;|\s)/i.test(value)) return value;
  if (/<(?!\/?(?:br|div|p)\b)\/?[a-z]/i.test(value)) return value;
  const textarea = document.createElement('textarea');
  textarea.innerHTML = value.replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/div>\s*<div>/gi, '\n').replace(/<\/?div>/gi, '');
  return textarea.value;
}
