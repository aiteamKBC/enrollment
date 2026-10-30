/**
 * How to show an authored document/slide-deck link inline.
 *
 * Authored resource URLs are free text or an uploaded file's path, and each kind
 * needs a different viewer:
 *
 * - A deck or PDF WE host is rendered in-house, page by page (see
 *   @/api/slideDeck) — the same result on a laptop as in production, the file
 *   never leaves our servers, and it does not depend on whether the reader's
 *   browser is set to preview PDFs or download them.
 * - Google Slides/Docs links have native embeds, and a PDF the browser draws
 *   itself, so both work from any origin.
 * - Anything else falls to Microsoft's Office Online viewer, which downloads the
 *   file from Microsoft's own servers. That only works for a URL that is
 *   absolute AND publicly reachable: a relative upload path or a localhost
 *   origin both make it render its "An error occurred / we can't open this for
 *   you" page instead.
 *
 * So: resolve what can actually be shown, and say when nothing can be, rather
 * than handing the learner a broken frame.
 */

export type DocEmbed =
  /** Renders on its own in an iframe — a Google embed, or a PDF the browser draws. */
  | { mode: 'native'; src: string }
  /** One of our own uploaded decks: render it with SlideDeckViewer. */
  | { mode: 'deck'; src: string }
  /** Handed to the Office Online viewer; the URL is absolute and publicly reachable. */
  | { mode: 'office'; src: string }
  /** Not previewable inline — open/download it instead. `reason` says why. */
  | { mode: 'unavailable'; reason: string };

const PDF_RE = /\.pdf$/i;
/** Formats the in-house renderer reads: OOXML decks (not legacy .ppt) and PDFs. */
const DECK_RE = /\.(pptx|ppsx|pptm|ppsm|pdf)$/i;
const DECK_BY_NAME_RE = /\.(pptx|ppsx|pptm|ppsm)$/i;
/**
 * PowerPoint 97-2003. The in-house renderer cannot read it (python-pptx opens
 * OOXML only, and the backend refuses it outright — see
 * curriculum_api.pptx_slides), so it depends entirely on the Office Online
 * viewer: fine on a public deployment, impossible on one Microsoft cannot
 * reach. Named here only to explain that difference to whoever uploaded it.
 */
const LEGACY_DECK_RE = /\.(ppt|pps)$/i;

/** Hostnames the public internet cannot resolve or route to. */
function isPubliclyReachable(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host === '::1') return false;
  // Dot-less names (bare machine names, docker service names) and private TLDs.
  if (!host.includes('.') || /\.(local|localhost|internal|test|invalid|home\.arpa)$/.test(host)) return false;
  const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (ipv4) {
    const [a, b] = [Number(ipv4[1]), Number(ipv4[2])];
    if (a === 127 || a === 10 || a === 0) return false;
    if (a === 192 && b === 168) return false;
    if (a === 172 && b >= 16 && b <= 31) return false;
    if (a === 169 && b === 254) return false;
  }
  return true;
}

/** Absolute form of an authored URL (relative upload paths resolve against the
 * page origin), or null when it isn't a URL we can use at all. */
export function absoluteDocUrl(url: string, origin = window.location.origin): string | null {
  const trimmed = url.trim();
  if (!trimmed) return null;
  try {
    const resolved = new URL(trimmed, origin);
    return resolved.protocol === 'http:' || resolved.protocol === 'https:' ? resolved.href : null;
  } catch {
    return null;
  }
}

/** Google Slides "/edit" or "/present" link -> its embeddable "/embed" form, else null. */
export function googleSlidesEmbedUrl(url: string): string | null {
  const m = url.match(/docs\.google\.com\/presentation\/d\/([^/?#]+)/);
  return m ? `https://docs.google.com/presentation/d/${m[1]}/embed` : null;
}

/** Google Docs "/edit" link -> its embeddable "/preview" form, else null. */
export function googleDocsEmbedUrl(url: string): string | null {
  const m = url.match(/docs\.google\.com\/document\/d\/([^/?#]+)/);
  return m ? `https://docs.google.com/document/d/${m[1]}/preview` : null;
}

/** Google Sheets link -> its read-only "/preview" form, else null. */
export function googleSheetsEmbedUrl(url: string): string | null {
  const m = url.match(/docs\.google\.com\/spreadsheets\/d\/([^/?#]+)/);
  return m ? `https://docs.google.com/spreadsheets/d/${m[1]}/preview` : null;
}

/**
 * Google Drive file link ("/file/d/<id>/view", "open?id=", "uc?id=") -> Drive's
 * own "/preview" embed, else null. Handing a Drive page to the Office viewer is
 * what showed learners "File not found": Office downloads the URL and gets
 * Drive's HTML page, not the document.
 */
export function googleDriveEmbedUrl(url: string): string | null {
  const m = url.match(/drive\.google\.com\/file\/d\/([\w-]{10,})/)
    || url.match(/drive\.google\.com\/(?:open|uc)\?[^#]*\bid=([\w-]{10,})/);
  return m ? `https://drive.google.com/file/d/${m[1]}/preview` : null;
}

/** SharePoint / OneDrive: every link needs a Microsoft sign-in to read. */
const MICROSOFT_DRIVE_HOST_RE = /(^|\.)(sharepoint\.com|onedrive\.live\.com|1drv\.ms)$/i;

/** Uploads the backend's `?preview=1` page hands to Office with a temporary link
 * (see curriculum_api.views.OFFICE_PREVIEW_EXTENSIONS). */
const OFFICE_PREVIEW_RE = /\.(docx?|docm|dotx?|rtf|odt|xlsx?|xlsm|ods|ppt|pps|odp)$/i;

/** The extension-bearing name to judge a file by: a protected endpoint
 * ("/learner_api/media/legacy-attachment/42/") has none in its path. */
function typeProbe(pathname: string, fileName?: string | null): string {
  const named = fileName?.trim().split(/[?#]/)[0];
  return named && /\.[a-z0-9]{1,8}$/i.test(named) ? named : pathname;
}

/** What, if anything, can be shown inline for this authored resource URL.
 * `fileName` is the authored file name, when the URL itself carries no extension. */
export function resolveDocEmbed(url: string, origin = window.location.origin, fileName?: string | null): DocEmbed {
  const absolute = absoluteDocUrl(url, origin);
  if (!absolute) return { mode: 'unavailable', reason: 'This link is not a web address we can open.' };

  const google = googleSlidesEmbedUrl(absolute) || googleDocsEmbedUrl(absolute)
    || googleSheetsEmbedUrl(absolute) || googleDriveEmbedUrl(absolute);
  if (google) return { mode: 'native', src: google };

  const { pathname, hostname, origin: fileOrigin } = new URL(absolute);
  const sameOrigin = fileOrigin === new URL(origin).origin;
  const probe = typeProbe(pathname, fileName);
  // A document we host ourselves: the backend renders it to page images, so it
  // shows the same way wherever this runs, is never handed to a third party,
  // and never depends on the browser's own PDF handling.
  // A name-only match is a deck, never a PDF: the slide renderer reads an
  // extension-less legacy link as PowerPoint (curriculum_presentation_slides).
  if (sameOrigin && (DECK_RE.test(pathname) || DECK_BY_NAME_RE.test(probe))) {
    return { mode: 'deck', src: absolute };
  }
  // This authenticated route supplies Office with a temporary read URL. Office
  // cannot fetch a learner's session-protected upload URL directly.
  if (sameOrigin && pathname.startsWith('/curriculum_api/curriculum/uploads/') && OFFICE_PREVIEW_RE.test(pathname)) {
    const preview = new URL(absolute);
    preview.searchParams.set('preview', '1');
    return { mode: 'native', src: preview.href };
  }
  // A PDF somewhere else: the browser's own viewer is all we have, and it
  // works from any origin unlike the Office viewer.
  if (PDF_RE.test(probe)) return { mode: 'native', src: absolute };

  if (MICROSOFT_DRIVE_HOST_RE.test(hostname)) {
    return {
      mode: 'unavailable',
      reason: 'This document is stored in SharePoint/OneDrive, which needs a Microsoft sign-in to open. Use “Open” below to view it in a new tab.',
    };
  }
  // Anything else on this site sits behind the learner's session, which
  // Microsoft's servers do not have: the Office viewer can only ever answer
  // "File not found" for it. Offer the file instead.
  if (sameOrigin && isPubliclyReachable(hostname)) {
    return {
      mode: 'unavailable',
      reason: 'This file type cannot be previewed here. Use the download or open button below to view it.',
    };
  }

  if (!isPubliclyReachable(hostname)) {
    return {
      mode: 'unavailable',
      // A legacy deck says so plainly: on a public deployment the Office viewer
      // would open it, so "we cannot reach this server" is the whole truth
      // here, but it leaves the author with nothing to act on. Re-saving as
      // .pptx is what makes it readable everywhere, including in-house.
      reason: LEGACY_DECK_RE.test(pathname)
        ? 'This deck is in the older PowerPoint 97-2003 format, which can only be previewed by Microsoft’s Office Online viewer — and that cannot reach this server. Re-saving it as .pptx (or as a PDF) would let it render here directly.'
        : 'Inline preview uses Microsoft’s Office Online viewer, which has to download the file from the internet — it cannot reach this server.',
    };
  }
  // Everything else — Office documents, and any other public link an author
  // pasted — goes to the Office viewer, which handles what it recognises and
  // shows its own message for what it does not.
  return { mode: 'office', src: `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(absolute)}` };
}
