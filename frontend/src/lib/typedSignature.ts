// ============================================================================
// Typed signatures
//
// A signature is the account holder's own name, set in a script face — not a
// hand-drawn squiggle. One person's signature is therefore identical every
// time they sign, and is legible in the document.
//
// The mark is still stored as a PNG data URL, exactly as the drawn signatures
// were. That matters: every existing signature column, PDF signature block and
// `startsWith('data:image/')` guard keeps working untouched, and signatures
// captured before this change still render.
//
// The same font is embedded in generated PDFs (see signatureFontData.ts), so a
// signature looks the same on screen and on paper.
// ============================================================================

import { SIGNATURE_FONT_BASE64 } from './signatureFontData';

/** Family name registered by the @font-face rule in index.css. */
export const SIGNATURE_FONT_FAMILY = 'Dancing Script';

/** jsPDF's internal alias for the embedded font (see registerSignatureFont). */
export const SIGNATURE_PDF_FONT = 'DancingScript';

/** Rendered at this height, then trimmed — keeps strokes crisp on retina. */
const RENDER_PX = 96;
const PADDING_X = 12;
const PADDING_Y = 8;

/**
 * Wait until the signature font is actually usable.
 *
 * Canvas silently falls back to a default face if the font has not loaded,
 * which would store a signature in the wrong typeface. Resolves either way —
 * a failed load must not block someone from signing.
 */
export async function ensureSignatureFont(): Promise<void> {
  const fonts = (globalThis as { document?: Document }).document?.fonts;
  if (!fonts) return;
  try {
    await fonts.load(`400 ${RENDER_PX}px "${SIGNATURE_FONT_FAMILY}"`);
    await fonts.ready;
  } catch {
    // Fall through: better a signature in the fallback face than none at all.
  }
}

/**
 * Render a name as a signature image (PNG data URL).
 *
 * Returns an empty string for a blank name, so callers can treat "no signature"
 * the same way they always have.
 */
export function renderTypedSignature(name: string): string {
  const text = (name || '').trim();
  if (!text) return '';

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';

  const font = `400 ${RENDER_PX}px "${SIGNATURE_FONT_FAMILY}", cursive`;

  // Measure first, then size the canvas to fit — a fixed canvas would clip long
  // names and leave short ones adrift in whitespace.
  ctx.font = font;
  const metrics = ctx.measureText(text);
  const ascent = metrics.actualBoundingBoxAscent || RENDER_PX * 0.8;
  const descent = metrics.actualBoundingBoxDescent || RENDER_PX * 0.3;

  canvas.width = Math.ceil(metrics.width) + PADDING_X * 2;
  canvas.height = Math.ceil(ascent + descent) + PADDING_Y * 2;

  // Resizing the canvas resets the context, so re-apply the font.
  ctx.font = font;
  ctx.textBaseline = 'alphabetic';
  // Near-black ink rather than pure black, which reads as printed text.
  ctx.fillStyle = '#0f172a';
  ctx.fillText(text, PADDING_X, PADDING_Y + ascent);

  return canvas.toDataURL('image/png');
}

/** Render a signature, waiting for the font first. Use this in UI code. */
export async function createTypedSignature(name: string): Promise<string> {
  await ensureSignatureFont();
  return renderTypedSignature(name);
}

/**
 * Register the signature font with a jsPDF document.
 *
 * Idempotent per document. Call before using SIGNATURE_PDF_FONT, e.g. to set a
 * signatory's name in script directly on the page rather than as an image.
 */
export function registerSignatureFont(doc: {
  addFileToVFS: (file: string, data: string) => void;
  addFont: (file: string, name: string, style: string) => void;
  getFontList: () => Record<string, string[]>;
}): void {
  try {
    if (doc.getFontList()[SIGNATURE_PDF_FONT]) return;
    doc.addFileToVFS('DancingScript.ttf', SIGNATURE_FONT_BASE64);
    doc.addFont('DancingScript.ttf', SIGNATURE_PDF_FONT, 'normal');
  } catch {
    // A font that fails to register just means the caller falls back to
    // Helvetica — not a reason to lose the document.
  }
}
